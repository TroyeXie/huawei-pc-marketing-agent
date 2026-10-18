"""Evidence-grounded, synchronous marketing generation for the hackathon.

Only Python's standard library is required. Documents and images must have been
retrieved by the ima adapter; this module never substitutes local samples for
live source material. No credentials are written to output or logs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


PLATFORM_RULES = {
    "公众号": {"min": 2, "max": 4, "preferred": 3, "structure": "标题→导语→任务分段→选型建议→行动引导", "tone": "清楚、可信、解释任务与选择理由", "cta": "带常用文件和外设到店体验，再按具体配置咨询", "length": "450—900字"},
    "小红书": {"min": 3, "max": 6, "preferred": 4, "structure": "口语标题→具体困扰→短段场景→选择提醒→话题标签", "tone": "口语、短句、有场景，不虚构亲测经历", "cta": "收藏购机清单，留言说明常用软件与使用需求", "length": "250—550字"},
    "门店朋友圈": {"min": 1, "max": 3, "preferred": 1, "structure": "一个场景→一个核心卖点→咨询或到店", "tone": "简洁、亲切、直达一个需求", "cta": "私信说明常用软件，预约到店体验", "length": "80—180字"},
    "门店海报": {"min": 1, "max": 1, "preferred": 1, "structure": "主标题→副标题→最多三个卖点→行动引导", "tone": "短而明确，突出一眼可读的信息", "cta": "到店体验，咨询具体配置", "length": "35—100字"},
}
PLATFORM_ALIASES = {"微信公众号": "公众号", "微信公众平台": "公众号", "wechat": "公众号", "xiaohongshu": "小红书", "red": "小红书", "朋友圈": "门店朋友圈", "微信朋友圈": "门店朋友圈", "销售朋友圈": "门店朋友圈", "moments": "门店朋友圈", "海报": "门店海报", "poster": "门店海报"}
SCENE_ANGLES = {
    "开学": "从课程、文档与携带任务进入，再核对常用软件和接口，避免只堆性能",
    "学生": "从学习任务和课程软件进入，解释阅读、携带及外设需求",
    "职场": "从会议投屏、移动办公与外设进入，再解释文档和差旅选择",
    "差旅": "从行李负担、会议连接与外设进入，给出具体核验动作",
    "创作": "从素材导入、显示与工作流进入，仅使用已证实接口和功能",
    "家庭": "从全家常用软件、文件迁移与易用任务进入",
    "企业": "从应用、账号、网络与外设试点进入，关注可核验部署条件",
    "门店": "只突出一个适合现场体验的卖点，给出到店理由",
    "新品": "解释已获证实的产品特点和适合任务，不编造代际变化",
    "节日": "从收礼者或家庭任务进入，提供有依据的选择建议",
}


class EngineError(RuntimeError):
    pass


def _norm(text: Any) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(r"[\s\u200b-\u200f\u2060\ufeff]+", "", value).lower()


def _quantity_norm(text: Any) -> str:
    value = _norm(text).replace("千克", "kg").replace("公斤", "kg").replace("克", "g").replace("毫米", "mm").replace("尼特", "nits").replace("nit", "nits").replace("nitss", "nits").replace("赫兹", "hz").replace("瓦时", "wh").replace("小时", "h").replace("×", "x").replace("＊", "x")
    if value.startswith("¥"): value = value[1:] + "元"
    value = value.replace("块钱", "元").replace("块", "元")
    return value


_QUANTITY = re.compile(r"\d+(?:\.\d+)?\s*(?:kg|千克|公斤|mm|毫米|cm|英寸|Wh|瓦时|GB|TB|Hz|赫兹|nits?|尼特|fps|帧|小时|PPI|ppi|万元|元|块钱|块|%|g|W)", re.I)
_RESOLUTION = re.compile(r"\d{3,5}\s*[×x*]\s*\d{3,5}", re.I)
_CPU = re.compile(r"(?:i[3579]-?\d{4,5}[a-z]{0,3}|Ultra\s*[3579]\s*\d{3}[a-z]|麒麟\s*[a-z]{0,2}\d{2,3}(?:\s*Plus)?)", re.I)
_CURRENCY = re.compile(r"[¥￥]\s*\d+(?:\.\d+)?")


def _quantities(text: str) -> set[str]:
    result = set()
    for pattern in (_QUANTITY, _RESOLUTION, _CPU, _CURRENCY):
        result.update(_quantity_norm(m.group()) for m in pattern.finditer(text))
    return result


def _source_url(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("url") or value.get("source_url") or value.get("link") or ""
    value = str(value or "")
    if not value.startswith(("https://", "http://")):
        return ""
    parsed = urllib.parse.urlsplit(value)
    if parsed.username or parsed.password:
        return ""
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    sensitive = {"api_key", "apikey", "client_id", "clientid", "authorization", "access_token", "token", "key", "secret"}
    pairs = [(k, v) for k, v in pairs if k.lower() not in sensitive]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(pairs), parsed.fragment))


def _product_key(name: Any) -> str:
    value = _norm(name).replace("huawei", "").replace("华为", "")
    if "matebook" not in value:
        return value
    if "fold" in value: return "fold"
    if "d16" in value: return "d16se" if "se" in value else "d16"
    if "d14" in value: return "d14se" if "se" in value else "d14"
    if "pros" in value: return "pros"
    if "gt" in value: return "gt"
    if "xpro" in value or "x系列" in value: return "xpro"
    if "14" in value:
        if "鸿蒙" in value or "harmony" in value: return "14_harmony"
        if any(x in value for x in ("ultra", "酷睿", "2024", "windows", "linux")): return "14_intel"
        return "14_unspecified"
    if "pro" in value: return "pro"
    return value


def _sentence_at(text: str, start: int, end: int) -> tuple[str, int, int]:
    separators = "。；;\n！？!?"
    left = max([text.rfind(s, 0, start) for s in separators] + [-1]) + 1
    rights = [text.find(s, end) for s in separators]
    right = min([x for x in rights if x >= 0] + [len(text)])
    return text[left:right], start - left, end - left


def _is_denial(text: str, start: int, end: int) -> bool:
    sentence, a, b = _sentence_at(text, start, end)
    prefix = sentence[max(0, a - 45):a]
    # A prior disclaimer must not neutralise a later affirmative promise.
    prefix = re.split(r"[，,](?:但|然而|不过|实际上)|(?:但是|然而|不过)", prefix)[-1]
    suffix = sentence[b:b + 35]
    if re.search(r"(?:并非|不是|并不是)\s*$", prefix):
        return True
    if re.search(r"(?:不|未|无法|不能|不会|尚未|不得|不应|请勿|不要)\s*$", prefix) and re.match(r"(?:保证|确保|承诺|宣称|表示|兼容|支持|编造|虚构|伪造)", sentence[a:]):
        return True
    denial = r"(?:不能|不得|不可|不应|请勿|不要)\s*(?:把|将|写|宣传|宣称|承诺|使用|表示|称为|标注|声称|理解|认为|保证|说|编造|虚构|伪造)|(?:不|无法)(?:承诺|保证|代表|等于|意味着|支持|证明|声称)|不(?:能|直接)(?:证明|推断)|(?:禁止|拒绝|避免)(?:使用|宣称|承诺|表述|写|声称|虚构)|(?:没有|缺少|尚无)(?:可靠)?(?:依据|证据)"
    return bool(re.search(denial, prefix) or re.search(r"(?:尚未|未获|未经).{0,10}(?:确认|证实|验证)|(?:未|不)(?:得到|能得到).{0,5}(?:确认|证实)", suffix))


def check_text(text: str, facts: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    """Return independent rule findings; negative limitation statements are safe.

    An empty list means these lexical/numeric rules found no issue, rather than
    claiming that semantic verification was performed.
    """
    text = str(text or "")
    facts = facts or []
    findings: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    patterns = [
        ("合规·绝对化", r"(?:全网|行业|全球|同价位|同级)(?:第一|第1|最低|最强|最好|最快|最轻|最薄|唯一)|(?:最强|最佳|顶级|零风险|100%安全|百分之百安全)|(?:永久|终身)(?:免费|有效|保修)|绝对(?:不会|保证|安全)", "移除无依据的绝对化宣传"),
        ("合规·全面兼容", r"(?:所有|任何|任意|全部).{0,8}(?:软件|应用|系统|外设|插件|驱动).{0,8}(?:兼容|支持|能用|畅玩|运行)|(?:全面|完全|百分之百|100%).{0,4}兼容", "改为按具体软件、版本和外设核验"),
        ("合规·虚构体验", r"(?:我|我们)(?:亲测|实测|入手了|用了|使用了|体验了)|亲测(?:好用|流畅|有效|续航)|用户(?:一致好评|都说好|公认)|好评率\s*\d", "删除没有实际体验或评价资料支持的表述"),
        ("合规·医疗功效", r"(?:治愈|治疗|预防|杜绝|根治).{0,6}(?:近视|眼病|干眼|视疲劳)", "删除产品资料未证明的医疗效果"),
        ("合规·虚假承诺", r"(?:保证|确保|承诺).{0,12}(?:不卡|不掉帧|不发热|不伤眼|永不|一定|全天|一整天)|(?:吊打|碾压|垃圾品牌|智商税)", "删除无依据的保证或贬低，保留任务与事实"),
    ]
    for kind, pattern, action in patterns:
        for match in re.finditer(pattern, text, re.I):
            if _is_denial(text, match.start(), match.end()):
                continue
            key = (kind, match.group())
            if key not in seen:
                findings.append({"type": kind, "status": "blocked", "claim": match.group(), "evidence": "本轮资料不足以支持该宣传表述", "action": action})
                seen.add(key)
    corpus = "\n".join(str(f.get("quote") or f.get("text") or "") for f in facts)
    for match in re.finditer(r"USB\s*[-－]?\s*A|Thunderbolt\s*[345]?|雷电\s*[345]?", text, re.I):
        if _is_denial(text, match.start(), match.end()):
            continue
        term = _norm(match.group()).replace("-", "")
        if term not in _norm(corpus).replace("-", ""):
            findings.append({"type": "事实·接口形态", "status": "blocked", "claim": match.group(), "evidence": "原文未明确该接口形态或协议；不能从USB3.2等规范名称推断USB-A或雷电", "action": "保留已证实的接口原名，删除无依据形态或协议"})
    source_quantities = _quantities(corpus)
    for match in list(_QUANTITY.finditer(text)) + list(_RESOLUTION.finditer(text)) + list(_CPU.finditer(text)) + list(_CURRENCY.finditer(text)):
        if _is_denial(text, match.start(), match.end()):
            continue
        quantity = _quantity_norm(match.group())
        if quantity not in source_quantities:
            key = ("事实·数字依据", quantity)
            if key not in seen:
                findings.append({"type": "事实·数字依据", "status": "blocked", "claim": match.group(), "evidence": "未在已核对的原文事实中找到这一数值或型号", "action": "删除该数值声明或补充对应型号的资料"})
                seen.add(key)
    for match in re.finditer(r"(?:赠送|立减|直降|补贴|优惠券|库存充足|限时特惠|包邮|免费换新|销量冠军|获奖)", text):
        if _is_denial(text, match.start(), match.end()):
            continue
        surrounding, _, _ = _sentence_at(text, match.start(), match.end())
        if re.search(r"(?:咨询|核对|确认|以.{0,8}为准|是否|不含|未提供|暂不|不写|不涉及|另行)", surrounding):
            continue
        if not any(match.group() in str(f.get("quote", "")) for f in facts):
            findings.append({"type": "合规·动态权益", "status": "blocked", "claim": match.group(), "evidence": "本轮缺少带配置、时间和条件的有效权益依据", "action": "删除该权益承诺"})
    return findings


def _json_from_content(content: Any) -> dict[str, Any]:
    if isinstance(content, list):
        content = "".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise EngineError("模型未返回可解析的JSON，未交付未经核验内容")
        try:
            value = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise EngineError("模型JSON不完整，请重新生成") from exc
    if not isinstance(value, dict):
        raise EngineError("模型响应格式不符合约定")
    return value


def _blank(status: str, message: str = "") -> dict[str, Any]:
    return {"status": status, "questions": [], "strategy": {}, "article": {"title": "", "lead": "", "sections": [], "cta": "", "tags": []}, "facts": [], "images": [], "checks": [], "trace": [], "mode": "live_llm", "message": message, "markdown": ""}


class MarketingEngine:
    def __init__(self) -> None:
        self.url = os.environ.get("MODEL_API_URL", "").strip()
        self.key = os.environ.get("MODEL_API_KEY", "").strip()
        self.model = os.environ.get("MODEL_NAME", "").strip()
        self.timeout = min(max(int(os.environ.get("MODEL_TIMEOUT_SECONDS", "55")), 5), 180)
        self.system_prompt = Path(__file__).with_name("prompts").joinpath("system_prompt.md").read_text(encoding="utf-8")

    def _chat(self, instruction: str, data: dict[str, Any], max_tokens: int = 4500) -> dict[str, Any]:
        body = {"model": self.model, "messages": [{"role": "system", "content": self.system_prompt + "\n\n## 本轮模块\n" + instruction}, {"role": "user", "content": json.dumps(data, ensure_ascii=False)}], "temperature": 0.25, "max_tokens": max_tokens, "response_format": {"type": "json_object"}}
        if "deepseek" in self.model.lower():
            thinking = os.environ.get("MODEL_THINKING", "disabled").strip().lower()
            body["thinking"] = {"type": thinking if thinking in {"enabled", "disabled"} else "disabled"}
        request = urllib.request.Request(self.url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.key}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(3_000_000)
            result = json.loads(raw)
        except urllib.error.HTTPError as exc:
            # Never expose provider response bodies: they can echo inputs/config.
            raise EngineError(f"大模型接口返回HTTP {exc.code}，本轮未完成生成") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise EngineError("大模型连接超时或网络不可用，请检查服务配置后重试") from exc
        except (ValueError, TypeError) as exc:
            raise EngineError("大模型接口响应无法解析") from exc
        choices = result.get("choices") or []
        if not choices:
            raise EngineError("大模型接口没有返回生成内容")
        if choices[0].get("finish_reason") == "length":
            raise EngineError("模型输出达到长度限制，请减少任务范围后重试")
        return _json_from_content((choices[0].get("message") or {}).get("content"))

    def generate(self, payload: dict[str, Any], materials: dict[str, Any]) -> dict[str, Any]:
        result = _blank("error")
        trace = result["trace"]
        current_step = "输入解析"

        def record(step: str, started: float, detail: str, status: str = "pass") -> None:
            trace.append({"step": step, "status": status, "detail": detail, "ms": round((time.monotonic() - started) * 1000)})

        try:
            started = time.monotonic()
            payload = {str(k): str(v or "").strip() for k, v in (payload or {}).items() if k in {"product", "audience", "scene", "platform", "goal", "brief"}}
            materials = materials or {}
            questions = []
            for key, question in (("product", "请指定华为PC的完整型号与版本。"), ("audience", "这篇内容主要面向哪类人群？"), ("scene", "请说明开学、职场、创作或门店等使用场景。"), ("platform", "请选择公众号、小红书、门店朋友圈或门店海报。")):
                if not payload.get(key): questions.append(question)
            product = payload.get("product", "")
            if _product_key(product) == "14_unspecified":
                questions.append("MateBook 14指鸿蒙版，还是酷睿Ultra（Windows/Linux）版？请先确认版本，避免型号与图片混用。")
            elif product and (product in {"轻薄本", "华为电脑", "华为PC", "笔记本", "MateBook"}):
                questions.append("请补充具体MateBook型号；如果尚未选型，请提供必用软件、主要任务和携带需求。")
            platform = PLATFORM_ALIASES.get(payload.get("platform", "").lower(), payload.get("platform", ""))
            if platform and platform not in PLATFORM_RULES:
                questions.append("当前可选渠道为公众号、小红书、门店朋友圈、门店海报，请选择其一。")
            material_product = str(materials.get("product_name") or product)
            if product and material_product and _product_key(product) != _product_key(material_product) and _product_key(material_product) != "14_unspecified":
                questions.append("本次检索资料的型号与任务型号不一致，请重新选择产品并检索。")
            if questions:
                result.update(status="needs_clarification", questions=list(dict.fromkeys(questions)), mode="clarification", message="请补充或确认以下信息后开始生成。")
                record(current_step, started, "发现关键输入缺失或型号需确认", "warning")
                return result
            payload["platform"] = platform
            rule = PLATFORM_RULES[platform]
            angle = next((v for k, v in SCENE_ANGLES.items() if k in payload["scene"]), "围绕目标人群的主要任务选择事实，解释具体使用价值与条件")
            if platform == "门店朋友圈": angle = "从本次场景选出一个核心卖点，给出清楚的咨询或到店理由"
            strategy = {"audience": payload["audience"], "scene": payload["scene"], "platform": platform, "goal": payload.get("goal") or "帮助目标人群理解产品并完成下一步咨询或体验", "angle": angle, "structure": rule["structure"], "tone": rule["tone"], "cta": rule["cta"], "image_count": rule["preferred"]}
            result["strategy"] = strategy
            record(current_step, started, "完整任务已解析；场景与渠道策略已分别确定")
            if not (self.url and self.key and self.model):
                result.update(status="error", mode="unconfigured", message="尚未配置大模型服务。请设置MODEL_API_URL、MODEL_API_KEY和MODEL_NAME；本系统不会把样例或模板作为AI运行结果。")
                return result
            if not self.url.startswith("https://") and not self.url.startswith(("http://127.0.0.1", "http://localhost")):
                raise EngineError("MODEL_API_URL应使用HTTPS完整chat/completions接口")

            current_step = "公开来源核对"
            started = time.monotonic()
            try:
                from public_evidence import build_public_evidence, PublicEvidenceError
            except ImportError as exc:
                raise EngineError("公开来源核对模块尚未就绪，已停止模型调用；不能将未确认公开的知识库资料发送给模型") from exc
            try:
                materials = build_public_evidence(product, materials)
            except PublicEvidenceError as exc:
                result.update(status="blocked", mode="evidence_gate", message=str(exc), markdown="")
                result["checks"].append({"type": "公开来源核对", "status": "blocked", "claim": "本次ima材料", "evidence": "公开事实映射或图片字节匹配未满足模型输入要求", "action": "已停止模型调用，请补充对应型号的公开证据"})
                record(current_step, started, "材料未通过公开来源匹配，未向模型发送知识库原文", "blocked")
                return result
            proof = materials.get("public_evidence") or {}
            result["public_evidence"] = proof
            result["checks"].append({"type": "公开来源核对", "status": "pass", "claim": "模型输入资料", "evidence": "仅使用本次ima原文中与已核对华为公开资料对应的必要摘录和通过字节匹配的图片元数据", "action": "库内整理稿和未确认公开的内容不进入模型上下文"})
            record(current_step, started, "已匹配公开证据并缩减模型输入；保留ima实际来源与获取时间")

            current_step = "接收ima资料"
            started = time.monotonic()
            documents = []
            total_chars = 0
            document_inputs = [d for d in materials.get("documents", []) if isinstance(d, dict) and (d.get("text") or d.get("content"))]
            if not document_inputs:
                # The ima adapter exposes source excerpts separately from its
                # lightweight document inventory. They are still unverified
                # original text, not ready-made factual claims or final copy.
                document_inputs = [d for d in materials.get("facts", []) if isinstance(d, dict) and d.get("text") and d.get("status", "source_excerpt") == "source_excerpt"]
            for raw in document_inputs:
                text = str(raw.get("text") or raw.get("content") or "").strip()
                if not text: continue
                # Bound request size while recording which source excerpt was used.
                remaining = 65000 - total_chars
                if remaining <= 0 or len(documents) >= 16: break
                text = text[:min(16000, remaining)]
                total_chars += len(text)
                source = raw.get("source") or {}
                source_dict = source if isinstance(source, dict) else {}
                title = str(raw.get("title") or source_dict.get("title") or "ima资料")
                locator = str(raw.get("locator") or source_dict.get("locator") or title)
                if source_dict.get("page") is not None: locator += " · 第" + str(source_dict["page"]) + "页"
                documents.append({"id": "D" + str(len(documents) + 1), "title": title, "text": text, "source_url": _source_url(raw.get("source_url") or source_dict.get("public_source_url") or source_dict.get("url") or source), "locator": locator, "retrieved_at": str(source_dict.get("retrieved_at") or raw.get("retrieved_at") or "")})
            if not documents:
                raise EngineError("本次ima资料没有可读取的产品原文，无法生成有依据的内容")
            image_candidates = []
            seen_images = set()
            for raw in materials.get("images", []):
                image_model = str(raw.get("model") or raw.get("product_name") or raw.get("product") or material_product)
                model_key = _product_key(image_model)
                if model_key not in {_product_key(material_product), "14_unspecified"}:
                    result["checks"].append({"type": "图片型号", "status": "warning", "claim": str(raw.get("title") or "配图"), "evidence": "图片型号与当前产品不一致", "action": "已排除该图片"})
                    continue
                source = raw.get("source") or {}
                source_dict = source if isinstance(source, dict) else {}
                url = str(raw.get("url") or raw.get("preview_url") or raw.get("image_url") or "")
                source_url = _source_url(raw.get("source_url") or source_dict.get("url") or source)
                if not url and re.search(r"\.(png|jpe?g|webp|gif)(?:\?|$)", source_url, re.I): url = source_url
                if url and not url.startswith(("https://", "http://", "/")): url = ""
                identity = str(raw.get("id") or raw.get("local_path") or url or raw.get("title"))
                if identity in seen_images: continue
                seen_images.add(identity)
                visual_review = str(raw.get("visual_review") or "")
                image_candidates.append({"id": "I" + str(len(image_candidates) + 1), "url": url, "title": str(raw.get("title") or raw.get("name") or source_dict.get("title") or raw.get("caption") or "产品资料图片"), "source_title": str(raw.get("source_title") or source_dict.get("title") or materials.get("kb_name") or "ima共享知识库"), "source_url": source_url, "position": "", "caption": "", "model": material_product, "status": "matched", "description": str(raw.get("description") or raw.get("caption") or ""), "visual_review": visual_review, "verification_basis": "ima产品目录、文件元数据及制作阶段图像预览核查" if visual_review else "ima产品目录与文件元数据；尚未执行视觉核查"})
            if len(image_candidates) < rule["min"]:
                result.update(status="needs_clarification", mode="clarification", questions=[f"{platform}需要{rule['min']}—{rule['max']}张同型号ima图片，本次只有{len(image_candidates)}张。请补充该型号资料，或选择适合当前图片数量的渠道。"], message="图片素材不足，尚未生成待发布文稿。")
                record(current_step, started, f"接收{len(documents)}份原文、{len(image_candidates)}张同型号图片；配图不足", "warning")
                return result
            image_count = min(rule["preferred"], len(image_candidates))
            strategy["image_count"] = image_count
            record(current_step, started, f"接收{len(documents)}份ima原文及{len(image_candidates)}张候选图片，来源随结果保留")

            current_step = "原文事实提取"
            started = time.monotonic()
            anomalies = []
            for doc in documents:
                if re.search(r"长度\s*(?:1[0-9](?:\.\d+)?)\s*mm[\s\S]{0,120}厚度\s*[123]\d{2}(?:\.\d+)?\s*mm", doc["text"], re.I):
                    anomalies.append(doc["title"] + "：长度与厚度标签存在疑似错位")
            if anomalies:
                result.update(status="needs_clarification", questions=["资料中的机身尺寸标签存在冲突，请核对原文或换用无冲突版本后生成。"], message="发现原文冲突，未将推测的尺寸当作已核实事实。")
                result["checks"].append({"type": "原文冲突", "status": "blocked", "claim": "机身长度与厚度", "evidence": "；".join(anomalies), "action": "停止生成并提示人工确认"})
                record(current_step, started, "检测到尺寸标签冲突", "blocked")
                return result
            extraction = self._chat(
                "你负责第一阶段：只从documents产品原文提取8—14条与任务有关的原子事实。返回JSON {facts:[{text,doc_id,quote,category}],conflicts:[{claim,reason,doc_ids}],notes:[]}。quote须为该doc_id内连续原文短片段，建议15—140字，保留支持关系及适用条件；不要引用软文案例的推荐句。text不能新增数字、关系、功能、用途保证或遗漏重要限定。没有价格时间/SKU/条件时不提取价格。若有明确事实冲突，列conflicts，不自行修复。不要生成软文。", {"task": payload, "documents": documents}, 5200)
            conflicts = extraction.get("conflicts") or []
            if conflicts:
                result.update(status="needs_clarification", questions=["检索原文存在冲突，请确认以下事实后继续：" + str(c.get("claim") or c.get("reason") or "产品配置") for c in conflicts[:3]], message="事实提取发现资料冲突，已停止生成。")
                result["checks"].extend({"type": "原文冲突", "status": "blocked", "claim": str(c.get("claim", "")), "evidence": str(c.get("reason", "")), "action": "请人工确认对应版本"} for c in conflicts[:6])
                record(current_step, started, "模型提取发现原文冲突", "blocked")
                return result
            docmap = {d["id"]: d for d in documents}
            facts = []
            removed_quotes = 0
            for item in extraction.get("facts") or []:
                if not isinstance(item, dict): continue
                doc = docmap.get(str(item.get("doc_id")))
                quote, fact_text = str(item.get("quote") or "").strip(), str(item.get("text") or "").strip()
                if not doc or not fact_text or len(_norm(quote)) < 8 or len(quote) > 450 or _norm(quote) not in _norm(doc["text"]):
                    removed_quotes += 1
                    continue
                if not _quantities(fact_text).issubset(_quantities(quote)):
                    removed_quotes += 1
                    continue
                if any(_norm(fact_text) == _norm(f["text"]) for f in facts): continue
                facts.append({"id": "F" + str(len(facts) + 1), "text": fact_text, "quote": quote, "source_title": doc["title"], "locator": doc["locator"], "source_url": doc["source_url"], "category": str(item.get("category") or "产品事实"), "retrieved_at": doc["retrieved_at"]})
            result["facts"] = facts
            if not facts:
                raise EngineError("没有事实通过原文引用与数字校验，已停止生成；请补充明确的产品资料")
            for fact in facts:
                result["checks"].append({"type": "原文引用", "status": "pass", "claim": fact["id"] + " · " + fact["text"], "evidence": fact["source_title"] + "：“" + fact["quote"] + "”", "action": "原文短引存在，数字与短引一致"})
            if removed_quotes:
                result["checks"].append({"type": "事实筛选", "status": "warning", "claim": f"{removed_quotes}条候选事实未通过原文引用或数字校验", "evidence": "逐条核对doc_id、quote和数字", "action": "已剔除，不进入生成上下文"})
            record(current_step, started, f"{len(facts)}条事实通过连续原文引用与数字校验；剔除{removed_quotes}条")

            input_issues = check_text(payload.get("brief", ""), facts)
            if any(not _is_denial(payload.get("brief", ""), match.start(), match.end()) for match in re.finditer(r"(?:编造|虚构|伪造|假装).{0,18}(?:价格|优惠|补贴|销量|评价|体验|用过|使用)", payload.get("brief", ""))):
                input_issues.append({"type": "用户要求", "status": "blocked", "claim": "要求编造产品或营销事实", "evidence": "用户brief包含虚构要求", "action": "拒绝加入，继续生成有依据内容"})
            for issue in input_issues:
                result["checks"].append({"type": "输入要求·" + issue["type"], "status": "warning", "claim": issue["claim"], "evidence": issue["evidence"], "action": "已拒绝该要求；正文不得采用"})

            current_step = "场景图文生成"
            started = time.monotonic()
            generation = self._chat(
                "你负责第二阶段：基于已经核对的facts原创生成当前任务图文，落实strategy并避免rejected_requests。只使用facts中的事实，不自行发掘文档，不把用户要求当产品依据。返回JSON {strategy:{angle,cta},article:{title,lead,sections:[{heading,text,fact_ids:[]}],cta,tags:[]},images:[{id,position,caption}]}。每个正文段落列其事实id，标题/导语也只能用这些事实。公众号3—5段，小红书3—5短段，朋友圈1段一个核心卖点，海报1—3段每段一个卖点。总字数优先服从task.brief；每段只用1—2项贴合任务的事实，不罗列所有CPU/无线/特色应用参数，整篇只突出2—4个关键卖点。除小红书外默认tags为空数组。图片选image_count张不同id，position说明具体位置，caption用15—35字简洁描述已知画面，不复述文件名，不推断看不见的功能。USB3.2 Gen1名称不能证明USB-A形态，不写原文没有的接口形态或雷电协议。触控硬件不直接证明每个应用支持缩放或批注，相关动作必须带应用支持条件或改为带常用文件到店核对的建议。内容不要含未证实价格、个人亲测或历史体验。", {"task": payload, "strategy": strategy, "length_guidance": rule["length"], "facts": facts, "images": image_candidates[:16], "image_count": image_count, "rejected_requests": input_issues}, 5000)
            article = generation.get("article") or {}
            article = {"title": str(article.get("title") or "").strip(), "lead": str(article.get("lead") or "").strip(), "sections": article.get("sections") or [], "cta": str(article.get("cta") or "").strip(), "tags": [str(t).lstrip("#") for t in article.get("tags", []) if str(t).strip()]}
            if platform != "小红书": article["tags"] = []
            valid_ids = {f["id"] for f in facts}
            sections = []
            for section in article["sections"]:
                if not isinstance(section, dict): continue
                ids = [str(x) for x in section.get("fact_ids", [])]
                if not str(section.get("text") or "").strip(): continue
                if any(x not in valid_ids for x in ids):
                    result["checks"].append({"type": "事实引用", "status": "warning", "claim": str(section.get("heading") or "正文段落"), "evidence": "段落引用了不存在的事实编号", "action": "已移除该段落"})
                    continue
                sections.append({"heading": str(section.get("heading") or ""), "text": str(section["text"]), "fact_ids": ids})
            max_sections = 1 if platform == "门店朋友圈" else (3 if platform == "门店海报" else 6)
            article["sections"] = sections[:max_sections]
            if not article["title"] or not article["sections"]:
                raise EngineError("生成稿缺少有效标题或正文，未交付空壳结果")
            used_images = []
            image_map = {i["id"]: i for i in image_candidates}
            for assignment in generation.get("images") or []:
                if not isinstance(assignment, dict): continue
                original = image_map.get(str(assignment.get("id")))
                if not original or any(i["id"] == original["id"] for i in used_images): continue
                selected = {k: v for k, v in original.items() if k != "description"}
                selected["position"] = str(assignment.get("position") or "正文相关段落后")
                selected["caption"] = str(assignment.get("caption") or original["title"])
                used_images.append(selected)
                if len(used_images) >= image_count: break
            if len(used_images) < rule["min"]:
                raise EngineError("生成的图片编排未达到该平台要求，请重新生成")
            result["article"], result["images"] = article, used_images
            adapted_strategy = generation.get("strategy") or {}
            if adapted_strategy.get("angle"): strategy["angle"] = str(adapted_strategy["angle"])
            strategy["image_count"] = len(used_images)
            record(current_step, started, f"完成{platform}结构、{len(article['sections'])}段内容与{len(used_images)}张图片编排")

            current_step = "独立语义核验"
            started = time.monotonic()
            paths = _content_paths(article, used_images)
            verification = self._chat(
                "你是独立核验模块，不负责润色。审查所有content_paths的产品声明是否被facts的原文quote充分支持，检查型号/数字/单位/功能/适用版本/限制/图片描述/营销合规。措辞建议与CTA无需产品证据；明确否定的限制不是违规。不要仅凭同数字判支持。返回JSON {reviewed_paths:[所有传入路径],checks:[{path,claim,fact_ids:[],status:'pass'或'unsupported',evidence}],issues:[{path,claim,reason}]}。每条无依据事实/遗漏关键限定/违规或图注推断都必须列issues，path必须是传入路径。已核对事实本身的text也必须与quote关系一致；如发现事实text误读quote，列issues到使用该事实的全部内容路径。不要返回改写后的稿件。", {"product": material_product, "task": payload, "facts": facts, "image_metadata": image_candidates, "article": article, "content_paths": paths}, 5000)
            reviewed = {str(p) for p in verification.get("reviewed_paths", [])}
            if set(paths) - reviewed:
                raise EngineError("语义核验未覆盖全部文稿与图注，未交付未经完整核验的结果")
            if not isinstance(verification.get("checks"), list) or not verification["checks"]:
                raise EngineError("语义核验没有提供逐项判断，未交付未经有效核验的结果")
            issues = [i for i in verification.get("issues", []) if isinstance(i, dict)]
            # A verifier may record an unsupported check but omit issues: do not ignore it.
            for entry in verification.get("checks", []):
                if isinstance(entry, dict) and str(entry.get("status", "")).lower() not in {"pass", "supported", "ok"}:
                    if not any(str(i.get("path")) == str(entry.get("path")) for i in issues):
                        issues.append({"path": entry.get("path"), "claim": entry.get("claim"), "reason": entry.get("evidence") or "独立核验未确认该声明"})
            bad_paths = set()
            for issue in issues:
                path = str(issue.get("path") or "")
                if path not in paths:
                    raise EngineError("语义核验发现无法定位的问题，已停止交付")
                bad_paths.add(path)
                result["checks"].append({"type": "语义核验", "status": "warning", "claim": str(issue.get("claim") or paths[path]), "evidence": str(issue.get("reason") or "本轮原文未支持该声明"), "action": "已移除对应段落或改为中性信息"})
            article, used_images = _remove_paths(article, used_images, bad_paths, material_product, payload["scene"], rule["cta"])
            for entry in verification.get("checks", []):
                if isinstance(entry, dict) and str(entry.get("status", "")).lower() in {"pass", "supported", "ok"} and str(entry.get("path")) not in bad_paths:
                    result["checks"].append({"type": "语义核验", "status": "pass", "claim": str(entry.get("claim") or "已审查内容"), "evidence": str(entry.get("evidence") or "与本轮引用事实一致"), "action": "保留"})
            record(current_step, started, f"覆盖{len(paths)}个内容位置；发现并处理{len(bad_paths)}处问题")

            current_step = "独立规则检查"
            started = time.monotonic()
            rule_paths = _content_paths(article, used_images)
            remove_paths = set()
            for path, text in rule_paths.items():
                findings = check_text(text, facts)
                if findings:
                    remove_paths.add(path)
                    for finding in findings:
                        result["checks"].append({**finding, "status": "warning", "action": "已移除对应内容，禁止进入发布稿"})
            article, used_images = _remove_paths(article, used_images, remove_paths, material_product, payload["scene"], rule["cta"])
            if not article["sections"]:
                result.update(status="blocked", article=article, images=used_images, message="正文未通过事实或合规核验，已阻止交付。请调整任务或补充产品资料后重试。")
                record(current_step, started, "所有正文段落均被移除", "blocked")
                return result
            remaining = [finding for text in _content_paths(article, used_images).values() for finding in check_text(text, facts)]
            if remaining:
                result["checks"].extend(remaining)
                result.update(status="blocked", article=article, images=used_images, message="文稿仍存在未通过核验的声明，已阻止导出。")
                record(current_step, started, "最终检查仍有未解决问题", "blocked")
                return result
            result["checks"].append({"type": "合规规则", "status": "pass", "claim": "最终发布稿", "evidence": "检查绝对化、全面兼容、虚构体验、动态权益、医疗功效及无依据数字", "action": "独立规则未发现残留问题；已完成前述语义核验"})
            visual_count = sum(bool(i.get("visual_review")) for i in used_images)
            result["checks"].append({"type": "图片与平台", "status": "pass", "claim": f"{len(used_images)}张{material_product}图片", "evidence": f"ima产品目录与文件元数据；{platform}要求{rule['min']}—{rule['max']}张；{visual_count}张另有图像预览核查说明", "action": "按已确认来源与图意编排；未核查画面的图片请预览后发布"})
            missing_preview = sum(not i["url"] for i in used_images)
            if missing_preview:
                result["checks"].append({"type": "图片预览", "status": "warning", "claim": f"{missing_preview}张图片暂无浏览器预览URL", "evidence": "资料引用可追溯，但适配器未提供可展示地址", "action": "请打开来源确认画面后发布"})
            result["article"], result["images"] = article, used_images
            result.update(status="success", mode="live_llm", message="已基于本轮ima资料完成原创生成、独立语义核验和规则检查。" + (" 部分无依据内容已自动移除，详见核验结果。" if bad_paths or remove_paths or input_issues else ""))
            record(current_step, started, "最终内容通过规则检查，来源与限制随稿保留")
            current_step = "导出"
            started = time.monotonic()
            result["markdown"] = _render_markdown(result)
            record(current_step, started, "已生成包含场景、依据、图文和核验结果的Markdown")
            return result
        except EngineError as exc:
            result.update(status="error", message=str(exc), markdown="")
            trace.append({"step": current_step, "status": "error", "detail": str(exc), "ms": 0})
            return result
        except Exception:
            result.update(status="error", message="本轮处理未完成，请检查输入材料或稍后重试；未交付未经核验的结果。", markdown="")
            trace.append({"step": current_step, "status": "error", "detail": "处理异常，未在公开输出中暴露接口或材料细节", "ms": 0})
            return result


def _content_paths(article: dict[str, Any], images: list[dict[str, Any]]) -> dict[str, str]:
    paths = {"title": str(article.get("title") or ""), "lead": str(article.get("lead") or ""), "cta": str(article.get("cta") or ""), "tags": " ".join(str(x) for x in article.get("tags", []))}
    for i, section in enumerate(article.get("sections", [])):
        paths[f"sections.{i}"] = str(section.get("heading") or "") + "\n" + str(section.get("text") or "")
    for i, image in enumerate(images):
        paths[f"images.{i}.caption"] = str(image.get("caption") or "")
    return {k: v for k, v in paths.items() if v.strip()}


def _remove_paths(article: dict[str, Any], images: list[dict[str, Any]], paths: set[str], product: str, scene: str, default_cta: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    article = dict(article)
    if "title" in paths: article["title"] = product + "｜" + scene + "选购参考"
    if "lead" in paths: article["lead"] = ""
    if "cta" in paths: article["cta"] = default_cta
    if "tags" in paths: article["tags"] = []
    article["sections"] = [s for i, s in enumerate(article.get("sections", [])) if f"sections.{i}" not in paths]
    for i, image in enumerate(images):
        if f"images.{i}.caption" in paths: image["caption"] = product + "资料配图；具体画面与版本请参见来源"
    return article, images


def _render_markdown(result: dict[str, Any]) -> str:
    strategy, article = result["strategy"], result["article"]
    lines = ["# " + article["title"], "", "## 一、场景判断", "", f"- 目标人群：{strategy['audience']}", f"- 使用场景：{strategy['scene']}", f"- 内容目标：{strategy['goal']}", f"- 发布平台：{strategy['platform']}", f"- 内容切入：{strategy['angle']}", "", "## 二、知识库依据", ""]
    used_ids = {fid for section in article["sections"] for fid in section.get("fact_ids", [])}
    for fact in result["facts"]:
        if fact["id"] not in used_ids: continue
        source = fact["source_title"]
        if fact["source_url"]: source = "[" + source + "](" + fact["source_url"] + ")"
        lines.extend([f"- **{fact['id']}** {fact['text']}（资料：{source}）", f"  - 原文依据：{fact['quote']}"])
    lines.extend(["", "## 三、图文软文", "", "### " + article["title"], "", article.get("lead", ""), ""])
    for section in article["sections"]:
        if section["heading"]: lines.extend(["#### " + section["heading"], ""])
        lines.extend([section["text"], "", "事实引用：" + "、".join(section.get("fact_ids", [])), ""])
    lines.extend([article.get("cta", ""), "", " ".join("#" + t for t in article.get("tags", [])), "", "### 配图方案", ""])
    for image in result["images"]:
        lines.extend([f"**{image['id']} · {image['title']}**", "", f"插入位置：{image['position']}", "", f"图片说明：{image['caption']}", "", f"型号：{image['model']}；来源资料：{image['source_title']}", ""])
        if image["url"]: lines.extend(["![" + image["caption"].replace("]", "") + "](" + image["url"] + ")", ""])
        if image["source_url"]: lines.extend(["[打开图片来源](" + image["source_url"] + ")", ""])
    lines.extend(["## 四、核验结果", "", result["message"], ""])
    for check in result["checks"]:
        lines.append(f"- [{check['status']}] {check['type']}：{check['claim']}。{check['action']}")
    return "\n".join(lines).strip() + "\n"


def generate(payload: dict[str, Any], materials: dict[str, Any]) -> dict[str, Any]:
    """Convenience entry point used by the web app."""
    return MarketingEngine().generate(payload, materials)
