"""Read-only ima shared-knowledge-base adapter.

Only the explicitly named competition knowledge base is queried.  This module
does not generate marketing copy, write to ima, serve files, or log credentials.
Callers must explicitly choose image results before exposing them to visitors.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError
import ipaddress
import json
import os
import re
import socket
import time
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

class IMAError(RuntimeError):
    """A readable error with no API credentials or internal knowledge-base ID."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _norm(value: Any) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", str(value)).casefold())


def _first(data: dict, *names: str, default: Any = "") -> Any:
    for name in names:
        value = data.get(name)
        if value is not None and value != "":
            return value
    return default


def _as_list(data: Any, *names: str) -> list:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for name in names:
        value = data.get(name)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [dict(v, **({"id": k} if not any(x in v for x in ("id", "kb_id")) else {}))
                    for k, v in value.items() if isinstance(v, dict)]
    return []


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1
        elif tag in {"p", "div", "br", "h1", "h2", "h3", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


class IMAClient:
    API_ORIGIN = "https://ima.qq.com"
    KB_NAME = "任德耀新建华为PC知识库"
    _READ_ENDPOINTS = frozenset({
        "wiki/v1/search_knowledge_base", "wiki/v1/get_knowledge_base",
        "wiki/v1/search_knowledge", "wiki/v1/get_knowledge_list",
        "wiki/v1/get_media_info", "note/v1/get_doc_content",
    })

    def __init__(self, cache_dir: str | Path | None = None, timeout: float = 20):
        self._client_id = os.environ.get("IMA_OPENAPI_CLIENTID", "").strip()
        self._api_key = os.environ.get("IMA_OPENAPI_APIKEY", "").strip()
        self._kb_id = ""
        self._account_label = "ima API 账户"
        self._discovery_meta: dict[str, Any] = {}
        self._known_ids: set[str] = set()
        self._folder_titles: dict[str, str] = {}
        self._folder_context: dict[str, str] = {}
        self.timeout = max(3, min(float(timeout), 45))
        self.cache_dir = Path(cache_dir or os.environ.get("IMA_CACHE_DIR")
                              or os.environ.get("ZHI_HUA_CACHE_DIR")
                              or "/tmp/zhi-hua-runtime").expanduser().resolve()
        # Runtime downloads must never be written beneath the application repo.
        repo_dir = Path(__file__).resolve().parent
        if self.cache_dir == repo_dir or repo_dir in self.cache_dir.parents:
            raise IMAError("ima 缓存目录必须位于应用仓库之外。")
        self._opener = build_opener(_NoRedirect)
        self._media_cache: dict[str, tuple[float, dict]] = {}
        self._directory_cache: tuple[float, list[dict]] | None = None
        self.max_download_bytes = 50 * 1024 * 1024
        self.max_images_per_doc = 3

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._api_key)

    def _redact(self, value: Any) -> str:
        text = str(value)
        for secret in (self._api_key, self._client_id, self._kb_id, *self._known_ids):
            if secret and len(secret) >= 6:
                text = text.replace(secret, "[已隐藏]")
        text = re.sub(r"https?://\S+", "[资料地址已隐藏]", text)
        text = re.sub(r"(?i)(authorization|api[_-]?key|token|secret)\s*[:=]\s*\S+",
                      r"\1=[已隐藏]", text)
        return text[:400]

    def _post(self, endpoint: str, body: dict) -> dict:
        if endpoint not in self._READ_ENDPOINTS:
            raise IMAError("该 ima 操作不在只读接口范围内。")
        if not self.configured:
            raise IMAError("尚未配置 ima 连接凭证，请在服务端配置 Client ID 与 API Key。")
        request = Request(
            f"{self.API_ORIGIN}/openapi/{endpoint}",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
            headers={"ima-openapi-clientid": self._client_id,
                     "ima-openapi-apikey": self._api_key,
                     "ima-openapi-ctx": "skill_version=1.1.10",
                     "Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise IMAError(f"ima 接口请求未成功（HTTP {response.status}）。")
                response_body = response.read(6 * 1024 * 1024)
        except HTTPError as error:
            raise IMAError(f"ima 接口请求未成功（HTTP {error.code}）。") from None
        except (URLError, TimeoutError, OSError):
            raise IMAError("ima 服务暂时无法连接，请稍后重试。") from None
        try:
            payload = json.loads(response_body)
        except (ValueError, UnicodeDecodeError):
            raise IMAError("ima 返回的响应格式无法读取。") from None
        if not isinstance(payload, dict):
            raise IMAError("ima 返回的响应结构不符合接口约定。")
        code = payload.get("code", payload.get("retcode", payload.get("ret_code")))
        if str(code) != "0":
            message = self._redact(payload.get("msg") or payload.get("message") or "服务未返回成功状态")
            raise IMAError(f"ima 请求未完成：{message}（代码 {self._redact(code)}）。")
        data = payload.get("data", {})
        if not isinstance(data, (dict, list)):
            raise IMAError("ima 业务数据格式无法读取。")
        return data

    def discover(self) -> dict:
        """Resolve only the explicitly authorized shared KB; never query all KBs."""
        if not self._kb_id:
            matches = []
            for query in (self.KB_NAME, "华为PC"):
                cursor = ""
                for _ in range(10):
                    data = self._post("wiki/v1/search_knowledge_base",
                                      {"query": query, "cursor": cursor, "limit": 20})
                    if isinstance(data, dict):
                        self._account_label = str(_first(data, "account_label", "account_name",
                                                        "nickname", default=self._account_label))
                    entries = _as_list(data, "info_list", "knowledge_base_list", "kb_list", "items", "list")
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        name = str(_first(entry, "kb_name", "name", "knowledge_base_name"))
                        kb_id = str(_first(entry, "kb_id", "id", "knowledge_base_id"))
                        base_type = str(entry.get("base_type", ""))
                        is_shared = not base_type or "共享" in base_type or "shared" in base_type.casefold()
                        if _norm(name) == _norm(self.KB_NAME) and kb_id and is_shared:
                            matches.append(kb_id)
                            self._discovery_meta = {
                                key: entry[key] for key in ("content_count", "base_type", "role_type")
                                if key in entry
                            }
                    if matches or not isinstance(data, dict) or data.get("is_end", True):
                        break
                    next_cursor = str(data.get("next_cursor", ""))
                    if not next_cursor or next_cursor == cursor:
                        break
                    cursor = next_cursor
                if matches:
                    break
            unique = set(matches)
            if not unique:
                raise IMAError(f"当前 ima 账户未找到指定共享知识库「{self.KB_NAME}」，请确认已加入该共享库。")
            if len(unique) > 1:
                raise IMAError("ima 中存在多个同名共享知识库，需先在服务端明确正确的知识库。")
            self._kb_id = unique.pop()
            self._known_ids.add(self._kb_id)
        return {"status": "connected", "account_label": self._redact(self._account_label),
                "kb_name": self.KB_NAME, **self._discovery_meta}

    def _normalize_item(self, raw: dict) -> dict:
        item = dict(raw)
        for nested in ("knowledge_info", "media_info", "folder_info"):
            if isinstance(raw.get(nested), dict):
                item.update(raw[nested])
        media_id = str(_first(item, "media_id", "id", "knowledge_id"))
        folder_id = str(_first(item, "folder_id", default=media_id))
        media_type = _first(item, "media_type", "type", default=None)
        is_folder = bool(item.get("is_folder") or raw.get("folder_info")
                         or str(media_type).casefold() in {"folder", "directory", "99"}
                         or "file_number" in item or "folder_number" in item
                         or (item.get("folder_id") and not media_id))
        if is_folder and not media_id:
            media_id = folder_id
        self._known_ids.update(x for x in (media_id, folder_id) if x)
        title = str(_first(item, "title", "name", "file_name", "folder_name"))
        if is_folder:
            self._folder_titles[folder_id or media_id] = title
            parent_context = self._folder_context.get(str(item.get("parent_folder_id", "")), "")
            if parent_context:
                self._folder_context[folder_id or media_id] = parent_context
        return {
            "title": title,
            "media_id": media_id, "folder_id": folder_id if is_folder else "",
            "parent_folder_id": str(item.get("parent_folder_id", "")),
            "media_type": media_type, "is_folder": is_folder,
            "highlight_content": unescape(re.sub(r"<[^>]+>", "", str(item.get("highlight_content", "")))),
            "model_context": (self._folder_context.get(str(item.get("parent_folder_id", "")), "")
                              or self._folder_titles.get(str(item.get("parent_folder_id", "")), "")),
            "source": {"provider": "ima", "knowledge_base": self.KB_NAME},
        }

    def _page(self, data: Any, retrieval_mode: str) -> dict:
        raw_items = _as_list(data, "knowledge_list", "info_list", "items", "list", "media_list")
        # Some versions return folders separately from document entries.
        if isinstance(data, dict) and isinstance(data.get("folder_list"), list):
            raw_items = raw_items + [dict(x, is_folder=True) for x in data["folder_list"] if isinstance(x, dict)]
        normalized = [self._normalize_item(x) for x in raw_items if isinstance(x, dict)]
        return {
            "items": normalized,
            "is_end": bool(data.get("is_end", True)) if isinstance(data, dict) else True,
            "next_cursor": str(data.get("next_cursor", "")) if isinstance(data, dict) else "",
            "retrieval_mode": retrieval_mode,
        }

    def list_folder(self, folder_id: str = "", cursor: str = "") -> dict:
        self.discover()
        body = {"knowledge_base_id": self._kb_id, "cursor": str(cursor), "limit": 50}
        if folder_id:
            if str(folder_id) not in self._known_ids:
                raise IMAError("目标文件夹未由指定共享知识库的检索结果确认。")
            body["folder_id"] = str(folder_id)
        return self._page(self._post("wiki/v1/get_knowledge_list", body), "directory")

    def _directory(self) -> list[dict]:
        if self._directory_cache and time.monotonic() - self._directory_cache[0] < 180:
            return self._directory_cache[1]
        folders = deque([""])
        seen_folders, seen_items, documents = set(), set(), []
        pages_read = 0
        while folders and len(seen_folders) < 16 and pages_read < 40:
            folder = folders.popleft()
            if folder in seen_folders:
                continue
            seen_folders.add(folder)
            cursor = ""
            for _ in range(10):
                page = self.list_folder(folder, cursor)
                pages_read += 1
                for item in page["items"]:
                    identity = item.get("media_id") or item.get("folder_id")
                    if not identity or identity in seen_items:
                        continue
                    seen_items.add(identity)
                    if item["is_folder"]:
                        folders.append(item["folder_id"] or item["media_id"])
                    else:
                        documents.append(item)
                if page["is_end"] or not page["next_cursor"] or page["next_cursor"] == cursor:
                    break
                cursor = page["next_cursor"]
                if pages_read >= 40:
                    break
        self._directory_cache = (time.monotonic(), documents)
        return documents

    def search(self, query: str) -> dict:
        self.discover()
        query = str(query).strip()[:160]
        items, seen, cursor = [], set(), ""
        if query:
            for _ in range(3):
                data = self._post("wiki/v1/search_knowledge",
                                  {"query": query, "knowledge_base_id": self._kb_id, "cursor": cursor})
                page = self._page(data, "search")
                for item in page["items"]:
                    if item["media_id"] and item["media_id"] not in seen:
                        seen.add(item["media_id"])
                        items.append(item)
                if page["is_end"] or not page["next_cursor"] or page["next_cursor"] == cursor:
                    break
                cursor = page["next_cursor"]
            if items:
                return {"items": items, "is_end": page["is_end"],
                        "next_cursor": page["next_cursor"], "retrieval_mode": "search"}
        # Directory reads remain confined to the selected shared KB.
        directory = self._directory()
        selected = [x for x in directory if not query or _norm(query) in _norm(x["title"])]
        return {"items": selected, "is_end": True, "next_cursor": "",
                "retrieval_mode": "directory_fallback"}

    def media_info(self, media_id: str) -> dict:
        self.discover()
        media_id = str(media_id)
        if not media_id or media_id not in self._known_ids:
            raise IMAError("目标资料未由指定共享知识库的检索结果确认。")
        cached = self._media_cache.get(media_id)
        if cached is None or time.monotonic() - cached[0] > 60:
            data = self._post("wiki/v1/get_media_info", {"media_id": media_id})
            self._media_cache[media_id] = (time.monotonic(), data)
        return self._media_cache[media_id][1]

    def _validate_download_url(self, url: str) -> None:
        try:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError
            if parsed.port not in (None, 443):
                raise ValueError
            if parsed.hostname.casefold() in {"localhost", "metadata.google.internal"}:
                raise ValueError
            host = parsed.hostname.casefold()
            if "." not in host or host.endswith((".local", ".internal", ".localhost")):
                raise ValueError
            try:
                addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
            except socket.gaierror:
                # The execution environment may resolve official COS hosts at
                # its HTTPS egress proxy rather than through local getaddrinfo.
                if not (host == "ima.qq.com" or host.endswith(".myqcloud.com")):
                    raise ValueError
                addresses = []
            if any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
                raise ValueError
            if any(secret and secret in url for secret in (self._api_key, self._client_id)):
                raise ValueError
        except (ValueError, OSError):
            raise IMAError("资料下载地址不符合公开 HTTPS 访问要求，未继续请求。") from None

    def _download(self, url: str, headers: dict | None = None) -> tuple[bytes, str]:
        # Only raise the transport security of an API-provided legacy URL.
        # Never send a request, with or without signed headers, over HTTP.
        if urlsplit(url).scheme == "http":
            url = "https:" + url[len("http:"):]
        safe_headers = {}
        for key, value in (headers or {}).items():
            key, value = str(key), str(value)
            if "\n" in key + value or "\r" in key + value:
                raise IMAError("资料下载请求头格式不合法。")
            if key.casefold().startswith("ima-openapi-") or any(
                    secret and secret in value for secret in (self._api_key, self._client_id)):
                raise IMAError("资料下载请求中包含不应外发的 ima 认证信息，已停止。")
            safe_headers[key] = value
        for _ in range(5):
            self._validate_download_url(url)
            try:
                response = self._opener.open(Request(url, headers=safe_headers), timeout=self.timeout)
            except HTTPError as error:
                if error.code not in (301, 302, 303, 307, 308):
                    raise IMAError(f"资料原文请求未成功（HTTP {error.code}），请使用 ima 客户端查看原文。") from None
                response = error
            except (URLError, TimeoutError, OSError):
                raise IMAError("资料原文暂时无法下载，请使用 ima 客户端查看原文。") from None
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location", "")
                response.close()
                if not location:
                    raise IMAError("资料下载地址发生无效跳转。")
                next_url = urljoin(url, location)
                if urlsplit(next_url).netloc != urlsplit(url).netloc:
                    safe_headers = {}  # Do not forward signed download headers across origins.
                url = next_url
                continue
            if response.status != 200:
                response.close()
                raise IMAError(f"资料原文请求未成功（HTTP {response.status}），请使用 ima 客户端查看原文。")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            chunks, size = [], 0
            try:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_download_bytes:
                        raise IMAError("单份资料超过本演示的 50 MB 读取上限，请选择较小的资料。")
                    chunks.append(chunk)
            except (URLError, TimeoutError, OSError):
                raise IMAError("资料下载中断，请稍后重试。") from None
            finally:
                response.close()
            if not size:
                raise IMAError("资料原文为空，未生成事实或图片结果。")
            return b"".join(chunks), content_type
        raise IMAError("资料下载跳转次数超限，未继续请求。")

    def _save_private(self, content: bytes, suffix: str) -> str:
        folder = self.cache_dir / "ima-selected"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = folder / f"{sha256(content).hexdigest()}{suffix}"
        if not path.exists():
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass  # A concurrent request cached this same content hash.
            else:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
        return str(path)

    @staticmethod
    def _image_suffix(content: bytes) -> str:
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if content.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return ".webp"
        return ""

    def _image_record(self, content: bytes, source: dict, page: int | None = None,
                      caption: str = "") -> dict | None:
        suffix = self._image_suffix(content)
        if not suffix or len(content) > 30 * 1024 * 1024:
            return None
        result = {"local_path": self._save_private(content, suffix), "source": dict(source),
                  "caption": caption[:240], "sha256": sha256(content).hexdigest()}
        if page is not None:
            result["source"]["page"] = page
        return result

    @staticmethod
    def _model_key(product: str) -> str:
        return _norm(re.sub(r"(?i)huawei|华为", "", product))

    @classmethod
    def _matches_model(cls, text: str, product: str) -> bool:
        """Require the requested model, with suffix guards for Pro / s / Go."""
        target = cls._model_key(product)
        if not target:
            return False
        candidate = cls._model_key(text)
        start = candidate.find(target)
        while start >= 0:
            tail = candidate[start + len(target):]
            suffixes = ("pro", "plus", "max", "go", "s")
            if not any(tail.startswith(s) and not target.endswith(s) for s in suffixes):
                # Match 14 separately from 14X or 14S and 16 separately from 16S.
                if not (target[-1:].isdigit() and tail[:1] in {"x", "s"}):
                    return True
            start = candidate.find(target, start + 1)
        return False

    def _pdf(self, content: bytes, source: dict, target: str = "") -> tuple[list, list]:
        try:
            import fitz
        except ImportError:
            fitz = None
        if fitz is not None:
            try:
                doc = fitz.open(stream=content, filetype="pdf")
            except Exception:
                raise IMAError("该 PDF 无法打开，请在 ima 中检查原文件。") from None
            pages, images = [], []
            title_matches = bool(target and self._matches_model(source["title"], target))
            try:
                for index in range(min(len(doc), 160)):
                    page = doc[index]
                    text = page.get_text("text")
                    pages.append({"page": index + 1, "text": text})
                    if len(images) >= self.max_images_per_doc:
                        continue
                    if target and not title_matches and not self._matches_model(text, target):
                        continue
                    for meta in page.get_images(full=True):
                        if meta[2] < 240 or meta[3] < 180:
                            continue
                        extracted = doc.extract_image(meta[0])
                        image = self._image_record(extracted["image"], source, index + 1, text[:240])
                        if image and not any(i["sha256"] == image["sha256"] for i in images):
                            images.append(image)
                        if len(images) >= self.max_images_per_doc:
                            break
            finally:
                doc.close()
            return pages, images
        try:
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(content))
            return ([{"page": i + 1, "text": page.extract_text() or ""}
                     for i, page in enumerate(reader.pages[:160])], [])
        except ImportError:
            raise IMAError("服务器尚未启用 PDF 文本解析组件，无法核验该 PDF 内容。") from None
        except Exception:
            raise IMAError("该 PDF 文本无法解析，请检查原文件。") from None

    def _office(self, content: bytes, source: dict, target: str = "") -> tuple[list, list]:
        try:
            archive = zipfile.ZipFile(BytesIO(content))
            if sum(x.file_size for x in archive.infolist()) > 180 * 1024 * 1024:
                raise IMAError("该 Office 文档解压后超过演示读取上限。")
        except zipfile.BadZipFile:
            raise IMAError("该 Office 文件无法解析，请提供 docx 或 pptx 格式。") from None
        pages, images = [], []
        ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
              "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
              "r": "http://schemas.openxmlformats.org/package/2006/relationships"}
        with archive:
            names = archive.namelist()
            slides = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                            key=lambda n: int(re.search(r"slide(\d+)\.xml", n).group(1)))
            if slides:
                for index, name in enumerate(slides[:160], 1):
                    root = ET.fromstring(archive.read(name))
                    text = "\n".join(x.text or "" for x in root.findall(".//a:t", ns))
                    pages.append({"page": index, "text": text})
                    if len(images) >= self.max_images_per_doc or (target and not (
                            self._matches_model(source["title"], target) or self._matches_model(text, target))):
                        continue
                    rel_name = "ppt/slides/_rels/" + Path(name).name + ".rels"
                    if rel_name not in names:
                        continue
                    relations = ET.fromstring(archive.read(rel_name))
                    for rel in relations:
                        if not rel.attrib.get("Type", "").endswith("/image") or rel.attrib.get("TargetMode") == "External":
                            continue
                        target_path = rel.attrib.get("Target", "")
                        image_name = "ppt/" + target_path.removeprefix("../")
                        if image_name in names:
                            image = self._image_record(archive.read(image_name), source, index, text[:240])
                            if image and not any(x["sha256"] == image["sha256"] for x in images):
                                images.append(image)
                        if len(images) >= self.max_images_per_doc:
                            break
            elif "word/document.xml" in names:
                root = ET.fromstring(archive.read("word/document.xml"))
                paragraphs = ["".join(x.text or "" for x in p.findall(".//w:t", ns))
                              for p in root.findall(".//w:p", ns)]
                pages = [{"page": None, "text": "\n".join(paragraphs)}]
                # DOCX cannot establish page numbers. Only extract when the file
                # itself is specifically about the requested complete model.
                if not target or self._matches_model(source["title"], target):
                    for name in names:
                        if name.startswith("word/media/"):
                            image = self._image_record(archive.read(name), source, caption=source["title"])
                            if image:
                                images.append(image)
                            if len(images) >= self.max_images_per_doc:
                                break
            else:
                raise IMAError("该 Office 文档类型暂不支持原文核验，请选择 PDF、Word 或 PPT 资料。")
        return pages, images

    def read_media(self, item: dict) -> dict:
        if item.get("is_folder"):
            raise IMAError("选中项目是文件夹，请先选择其中的资料。")
        media_id = str(item.get("media_id", ""))
        info = self.media_info(media_id)
        media_type = _first(info, "media_type", "type", default=item.get("media_type"))
        source = {"provider": "ima", "knowledge_base": self.KB_NAME,
                  "title": str(item.get("title") or "共享知识库资料"),
                  "source_id": "IMA-" + sha256(media_id.encode()).hexdigest()[:12],
                  "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        if item.get("model_context"):
            source["model_context"] = str(item["model_context"])
        pages, images, local_path = [], [], ""
        if str(media_type) == "11":
            note = info.get("notebook_ext_info") or {}
            note_id = str(note.get("notebook_id", ""))
            if not note_id:
                raise IMAError("该笔记未返回可读取的原文入口，请使用 ima 客户端查看。")
            self._known_ids.add(note_id)
            data = self._post("note/v1/get_doc_content", {"note_id": note_id, "target_content_format": 0})
            pages = [{"page": None, "text": str(data.get("content", ""))}]
        else:
            url_info = info.get("url_info") or {}
            url = str(url_info.get("url", ""))
            if not url:
                raise IMAError("ima 未提供该资料的可读取原文地址，请使用 ima 客户端查看原文。")
            content, mime = self._download(url, url_info.get("headers") or {})
            target = str(item.get("_target_product", ""))
            if str(media_type) == "9" or mime.startswith("image/"):
                image = self._image_record(content, source, caption=source["title"])
                if not image:
                    raise IMAError("素材未通过 PNG、JPEG 或 WebP 原图校验，未作为产品配图。")
                images, local_path = [image], image["local_path"]
            elif content.startswith(b"%PDF-"):
                pages, images = self._pdf(content, source, target)
            elif content.startswith(b"PK\x03\x04"):
                pages, images = self._office(content, source, target)
            elif str(media_type) in {"1", "3", "4"}:
                raise IMAError("该资料的原文类型与文件说明不一致，未提取事实或配图。")
            else:
                text = None
                for encoding in ("utf-8-sig", "gb18030"):
                    try:
                        text = content.decode(encoding)
                        break
                    except UnicodeDecodeError:
                        pass
                if text is None or "\x00" in text[:1000]:
                    raise IMAError("该资料不是可读取的文本格式，未生成事实结果。")
                if mime == "text/html" or re.match(r"\s*(?:<!doctype html|<html)", text, re.I):
                    parser = _HTMLText()
                    parser.feed(text)
                    text = unescape("".join(parser.parts))
                pages = [{"page": None, "text": text}]
        full_text = "\n\n".join((f"[第 {p['page']} 页]\n" if p.get("page") else "") + p["text"]
                                for p in pages)
        official_urls = []
        for candidate in re.findall(r"https://[^\s<>\]\)]+", full_text):
            candidate = candidate.rstrip("。，；、.\"'")
            host = (urlsplit(candidate).hostname or "").casefold()
            if host in {"consumer.huawei.com", "support.huawei.com", "www.vmall.com", "www.huawei.com"}:
                if candidate not in official_urls:
                    official_urls.append(candidate)
        if official_urls:
            source["official_urls"] = official_urls[:6]
        source_date = re.search(r"(?:抓取日期|更新日期|资料日期|核验日期)\s*[：:]\s*(\d{4}-\d{2}-\d{2})", full_text)
        if source_date:
            source["source_date"] = source_date.group(1)
        if not full_text.strip() and not images:
            raise IMAError("资料中未取得可核验文本或图片，未生成示例结果。")
        return {"title": source["title"], "source": source, "media_type": media_type,
                "text": full_text, "pages": pages, "images": images, "local_path": local_path}

    @classmethod
    def _fact_excerpt(cls, text: str, product: str, title_scoped: bool = False) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        indices = [i for i, line in enumerate(lines) if cls._matches_model(line, product)]
        if not indices and not title_scoped:
            return ""
        if title_scoped:
            return "\n".join(lines)[:14000]
        selected: set[int] = set()
        for index in indices[:12]:
            selected.update(range(max(0, index - 1), min(len(lines), index + 18)))
        return "\n".join(lines[i] for i in sorted(selected))[:14000]

    @staticmethod
    def _is_generated_material(title: str) -> bool:
        stem = Path(title).stem
        platform_drafts = {"门店长图", "短视频口播", "知乎回答", "直播讲解",
                           "朋友圈", "新闻稿", "微信公众号", "小红书"}
        return stem in platform_drafts or any(marker in title for marker in
            ("完整成稿", "文案模板", "营销模板", "话术模板", "沟通卡", "适用人群与限制卡"))

    def collect_product(self, product_name: str) -> dict:
        """Retrieve a small, attributable source set for an exact product model.

        Facts are original source excerpts, not generated or verified marketing
        statements.  Images are private candidates; the caller chooses assets.
        """
        product = str(product_name).strip()
        if not product or len(product) > 160:
            raise IMAError("请提供具体、完整的华为 PC 产品型号。")
        self.discover()
        page = self.search(product)
        candidates = [x for x in page["items"] if not x["is_folder"]]
        matched_folders = [x for x in page["items"] if x["is_folder"]
                           and self._matches_model(x["title"], product)]
        # Official image filenames often abbreviate the product.  Their exact
        # product folder supplies attributable context, rather than guessing
        # that a similarly named image is the same year or operating system.
        for folder in matched_folders[:5]:
            folder_id = folder["folder_id"] or folder["media_id"]
            self._folder_context[folder_id] = product
            children = self.list_folder(folder_id)
            candidates.extend(dict(x, model_context=product) for x in children["items"]
                              if not x["is_folder"])
            for child_folder in [x for x in children["items"] if x["is_folder"]][:3]:
                child_id = child_folder["folder_id"] or child_folder["media_id"]
                self._folder_context[child_id] = product
                nested = self.list_folder(child_id)
                candidates.extend(dict(x, model_context=product) for x in nested["items"]
                                  if not x["is_folder"])
        target_candidates = [x for x in candidates if (
            self._matches_model(x["title"], product)
            or self._matches_model(x.get("model_context", ""), product)
            or self._matches_model(x.get("highlight_content", ""), product))
            and not any(marker in x["title"].casefold() for marker in ("×", "竞品", "对比", " vs "))
            and not self._is_generated_material(x["title"])]
        if target_candidates:
            candidates = target_candidates
        if not candidates:
            brandless = re.sub(r"(?i)huawei|华为", "", product).strip()
            if brandless != product:
                page = self.search(brandless)
                candidates = [x for x in page["items"] if not x["is_folder"]]
        if not candidates:
            directory = self._directory()
            candidates = [x for x in directory if self._matches_model(x["title"], product)]
            if not candidates:
                # Generic competition sourcebooks may contain the model inside.
                candidates = [x for x in directory if any(term in x["title"] for term in
                              ("全板块", "全产品", "产品参数", "产品知识", "产品资料", "知识库", "汇总"))]
        if not candidates:
            raise IMAError(f"指定共享知识库中未检索到「{product}」的可用资料。")
        by_id = {}
        for item in candidates:
            if self._is_generated_material(item["title"]):
                continue
            previous = by_id.get(item["media_id"])
            if previous is None or item.get("model_context"):
                by_id[item["media_id"]] = item
        candidates = list(by_id.values())
        candidates.sort(key=lambda x: (
            {"产品事实.md": 0, "卖点与限制.md": 1, "01-型号身份卡.md": 2,
             "05-系统协同与兼容卡.md": 3}.get(x["title"], 4),
            not self._matches_model(x["title"], product),
            not self._matches_model(x.get("model_context", ""), product),
            not self._matches_model(x.get("highlight_content", ""), product),
            "价格" in x["title"], x["title"]))
        document_candidates = [x for x in candidates if str(x.get("media_type")) != "9"]
        image_candidates = [x for x in candidates if str(x.get("media_type")) == "9"]
        selected_candidates = document_candidates[:4] + image_candidates[:3]
        facts, documents, images, warnings = [], [], [], []
        seen_documents, seen_images = set(), set()
        for item in selected_candidates:
            if item["media_id"] in seen_documents:
                continue
            seen_documents.add(item["media_id"])
            try:
                record = self.read_media(dict(item, _target_product=product))
            except IMAError as error:
                warnings.append({"title": item["title"], "message": self._redact(error)})
                continue
            title_scoped = (self._matches_model(record["title"], product)
                            or self._matches_model(record["source"].get("model_context", ""), product))
            fact_count = 0
            for part in record["pages"]:
                excerpt = self._fact_excerpt(part["text"], product, title_scoped)
                if not excerpt:
                    continue
                source = dict(record["source"])
                if part.get("page") is not None:
                    source["page"] = part["page"]
                facts.append({"text": excerpt, "source": source, "product_name": product,
                              "status": "source_excerpt"})
                fact_count += 1
                if len(facts) >= 20:
                    break
            for image in record["images"]:
                if not title_scoped and not self._matches_model(image.get("caption", ""), product):
                    continue
                if image["sha256"] not in seen_images:
                    seen_images.add(image["sha256"])
                    images.append(dict(image, product_name=product, status="candidate_needs_selection"))
                if len(images) >= 6:
                    break
            if fact_count or record["images"]:
                documents.append({"title": record["title"], "source": record["source"],
                                  "fact_excerpt_count": fact_count})
            if len(documents) >= 4 and len(images) >= 3:
                break
        if not facts and not images:
            detail = warnings[0]["message"] if warnings else "原文中未确认与该完整型号对应的内容。"
            raise IMAError(f"未取得「{product}」的可核验事实或配图。{detail}")
        return {"product_name": product, "kb_name": self.KB_NAME,
                "status": "retrieved" if facts else "images_only",
                "retrieval_mode": page["retrieval_mode"], "facts": facts,
                "documents": documents, "images": images, "warnings": warnings}


__all__ = ["IMAClient", "IMAError"]
