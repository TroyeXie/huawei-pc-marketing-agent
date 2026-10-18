"""Allow only ima excerpts and selected images already proved public.

The bundled catalog is a public reference allowlist, never a replacement data
source. Every returned section must also occur in the current ima result. This
module performs no network calls and never forwards arbitrary source metadata.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import unicodedata


class PublicEvidenceError(RuntimeError):
    """The current material cannot safely enter an external model request."""


_CATALOG_PATH = Path(__file__).parent / "data" / "public_evidence.json"
_KB_NAME = "任德耀新建华为PC知识库"
_SECTION = re.compile(r"^##[ \t]+([^\n]+)\n(.*?)(?=^#{1,2}[ \t]+|\Z)", re.M | re.S)
_SOURCE_ID = re.compile(r"IMA-[a-f0-9]{12,64}\Z")
_MEDIA_ROUTE = re.compile(r"/api/media/[A-Za-z0-9_-]{1,80}/[a-f0-9]{18,64}\Z")


def _norm(value: str) -> str:
    # Preserve every non-whitespace character, including numbers, qualifiers,
    # punctuation and negations. Case and semantic similarity are not matches.
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _product_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    value = re.sub(r"^(?:huawei|华为)\s*", "", value)
    return re.sub(r"\s+", "", value)


@lru_cache(maxsize=1)
def _catalog() -> dict:
    try:
        data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or not isinstance(data["products"], list):
            raise ValueError()
        for product in data["products"]:
            if not product.get("sections") or not product.get("product_name"):
                raise ValueError()
        return data
    except (OSError, ValueError, KeyError, TypeError):
        raise PublicEvidenceError("公开来源核对目录不可用，请检查部署资料。") from None


def supported_products() -> list[str]:
    """Canonical full model names with a completed public reference mapping."""
    return [entry["product_name"] for entry in _catalog()["products"]]


def _entry_for(product_name: str) -> dict:
    key = _product_key(product_name)
    for entry in _catalog()["products"]:
        if key in {_product_key(n) for n in [entry["product_name"], *entry.get("aliases", [])]}:
            return entry
    raise PublicEvidenceError("该完整型号尚未完成公开来源核对，暂不能将其 ima 原文交给外部模型。请先选择已核对型号。")


def _safe_time(value: object) -> str:
    if not isinstance(value, str) or len(value) > 40:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.isoformat() if parsed.tzinfo else ""
    except ValueError:
        return ""


def _source(source: dict, entry: dict, *, image: dict | None = None) -> dict:
    url = image["public_source_url"] if image else entry["public_spec_url"]
    clean = {
        "provider": "ima",
        "knowledge_base": _KB_NAME,
        "title": image["title"] if image else "产品事实.md",
        "model_context": entry["product_name"],
        "url": url,
        "public_source_url": url,
        "public_verified": True,
        "retrieved_at": _safe_time(source.get("retrieved_at")),
        "public_verified_at": entry["verified_at"],
    }
    source_id = source.get("source_id", "")
    if isinstance(source_id, str) and _SOURCE_ID.fullmatch(source_id):
        clean["source_id"] = source_id
    return clean


def _model_matches(value: object, entry: dict) -> bool:
    if not value:
        return True
    return _product_key(str(value)) in {
        _product_key(n) for n in [entry["product_name"], *entry.get("aliases", [])]
    }


def _raw_documents(materials: dict, entry: dict) -> tuple[list[dict], int]:
    result, seen, excluded = [], set(), 0
    for field in ("documents", "facts"):
        for item in materials.get(field, []):
            if not isinstance(item, dict):
                excluded += 1
                continue
            text = item.get("text") or item.get("content")
            if not isinstance(text, str) or not text.strip():
                continue
            digest = sha256(text.encode()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            source = item.get("source")
            if (not isinstance(source, dict) or source.get("provider") != "ima"
                    or source.get("knowledge_base") != _KB_NAME
                    or not _model_matches(item.get("product_name"), entry)
                    or not _model_matches(source.get("model_context"), entry)):
                excluded += 1
                continue
            result.append({"text": text, "source": source})
    return result, excluded


def _match_sections(documents: list[dict], entry: dict) -> tuple[list[dict], dict, int]:
    references = {_norm(s["heading"]): s for s in entry["sections"]}
    matches, source, excluded = {}, {}, 0
    for document in documents:
        for match in _SECTION.finditer(document["text"].replace("\r\n", "\n")):
            heading, body = match.group(1).strip(), match.group(2).strip()
            ref = references.get(_norm(heading))
            if ref is None:
                excluded += 1
                continue
            normalized = _norm(body)
            expected = _norm(ref["text"])
            # Some official-page imports append the site's exit dialog. Only
            # the explicitly verified public footer may be removed; arbitrary
            # trailing prose causes the entire section to be excluded.
            for footer in entry.get("ignored_public_footers", []):
                tail = _norm(footer)
                if normalized.endswith(tail):
                    normalized = normalized[:-len(tail)]
            if normalized != expected:
                excluded += 1
                continue
            matches[ref["heading"]] = ref
            if not source:
                source = document["source"]
    missing_required = set(entry.get("required_sections", [])) - set(matches)
    if missing_required or len(matches) < entry.get("minimum_sections", 1):
        raise PublicEvidenceError("本次 ima 原文与已核对的公开规格不充分匹配，已阻止模型调用。请重新检索或补充该型号的公开来源核对。")
    return [s for s in entry["sections"] if s["heading"] in matches], source, excluded


def _image_digest(item: dict) -> tuple[str, str]:
    value = item.get("local_path")
    if not isinstance(value, str) or not value:
        return "", ""
    try:
        supplied = Path(value).expanduser()
        if supplied.is_symlink():
            return "", ""
        path = supplied.resolve(strict=True)
        roots = {Path("/tmp/zhi-hua-runtime").resolve()}
        for name in ("IMA_CACHE_DIR", "ZHI_HUA_CACHE_DIR", "ZHIHUA_RUNTIME_DIR"):
            if os.environ.get(name):
                roots.add(Path(os.environ[name]).expanduser().resolve())
        if not any(path.is_relative_to(root) for root in roots):
            return "", ""
        if not path.is_file() or not 0 < path.stat().st_size <= 30 * 1024 * 1024:
            return "", ""
        digest = sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest(), str(path)
    except (OSError, RuntimeError, ValueError):
        return "", ""


def _match_images(materials: dict, entry: dict) -> tuple[list[dict], int]:
    references = {img["sha256"]: img for img in entry.get("images", [])}
    result, seen, excluded = [], set(), 0
    for item in materials.get("images", []):
        if not isinstance(item, dict):
            excluded += 1
            continue
        source = item.get("source")
        if (not isinstance(source, dict) or source.get("provider") != "ima"
                or source.get("knowledge_base") != _KB_NAME
                or not _model_matches(item.get("product_name") or item.get("model"), entry)
                or not _model_matches(source.get("model_context"), entry)):
            excluded += 1
            continue
        digest, path = _image_digest(item)
        ref = references.get(digest)
        if ref is None or digest in seen:
            excluded += 1
            continue
        seen.add(digest)
        route = item.get("url", "")
        if not isinstance(route, str) or not _MEDIA_ROUTE.fullmatch(route):
            route = ref["public_source_url"]
        clean = {
            "local_path": path,
            "sha256": digest,
            "title": ref["title"],
            "description": ref["description"],
            "caption": ref["description"],
            "visual_review": ref["visual_review"],
            "product_name": entry["product_name"],
            "model": entry["product_name"],
            "source": _source(source, entry, image=ref),
            "source_url": ref["public_source_url"],
            "public_source_url": ref["public_source_url"],
            "public_verified": True,
            "public_verified_at": entry["verified_at"],
            "url": route,
            "status": "public_source_verified",
        }
        for dimension in ("width", "height"):
            if isinstance(ref.get(dimension), int):
                clean[dimension] = ref[dimension]
        result.append(clean)
    return result, excluded


def build_public_evidence(product_name: str, ima_materials: dict) -> dict:
    """Return a fresh, bounded, public-only materials object or fail closed.

    ``documents[].text`` and ``facts[].text`` hold the same complete verified
    sections, with all version/measurement qualifiers. ``images`` contains only
    actual ima-downloaded bytes matching public asset hashes and human-reviewed
    product compositions. Unknown captions, filenames, URLs, warnings, account
    identifiers and raw document text are never copied into the result.
    """
    entry = _entry_for(product_name)
    if not isinstance(ima_materials, dict) or not ima_materials.get("product_name"):
        raise PublicEvidenceError("缺少本次 ima 检索结果的完整型号，无法核对公开来源。")
    if not _model_matches(ima_materials["product_name"], entry):
        raise PublicEvidenceError("本次 ima 资料型号与任务型号不一致，已阻止模型调用。")
    documents, excluded_documents = _raw_documents(ima_materials, entry)
    if not documents:
        raise PublicEvidenceError("未收到指定 ima 共享知识库的当前产品原文，公开参考不能替代实时检索。")
    sections, origin, excluded_sections = _match_sections(documents, entry)
    text = "# HUAWEI " + entry["product_name"] + " - 产品事实\n" + "\n".join(
        "## " + section["heading"] + "\n" + section["text"] for section in sections
    )
    source = _source(origin, entry)
    images, excluded_images = _match_images(ima_materials, entry)
    omitted = len(entry["sections"]) - len(sections)
    warnings = []
    if omitted:
        warnings.append(f"有 {omitted} 个公开参考章节未在本次 ima 原文中完整匹配，已排除。")
    if excluded_images:
        warnings.append(f"有 {excluded_images} 张候选图未通过公开字节与画面核对，已排除。")
    urls = list(dict.fromkeys([entry["public_spec_url"], *[i["public_source_url"] for i in images]]))
    verification = {
        "verified_at": entry["verified_at"],
        "runtime_checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_urls": urls,
        "matched_count": len(sections),
        "excluded_count": excluded_sections + excluded_documents,
        "unmatched_reference_count": omitted,
        "matched_image_count": len(images),
        "excluded_image_count": excluded_images,
        "policy": "仅当前 ima 原文中与已核对公开参考完整匹配的章节，以及与公开素材 SHA-256 相同且画面已核对的图片，可进入模型请求。",
    }
    return {
        "product_name": entry["product_name"],
        "kb_name": _KB_NAME,
        "status": "public_evidence_verified",
        "retrieval_mode": "ima_current_result_public_allowlist",
        "documents": [{"title": "产品事实.md", "text": text, "source": dict(source), "fact_excerpt_count": len(sections)}],
        "facts": [{"text": text, "source": dict(source), "product_name": entry["product_name"], "status": "source_excerpt"}],
        "images": images,
        "warnings": warnings,
        "public_evidence": verification,
    }
