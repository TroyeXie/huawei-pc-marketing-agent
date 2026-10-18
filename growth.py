"""Transparent store workflow, built from actual inputs and accepted facts.

No customer psychology, prices or eligibility are inferred by a model. Quotes
are operator-input calculations, never verification of a live retail offer.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_CENT = Decimal("0.01")
_TIER_NAMES = ("标准方案", "门店优惠", "优惠组合", "完整权益")
_QUOTE_NOTE = "按填报的SKU、价格与权益条件示算；来源、资格和叠加规则待下单前复核。未填权益不代表无权益。"


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _url(value: Any) -> str:
    try:
        parsed = urlsplit(_text(value, 2000))
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            return ""
        # Preserve retail product/SKU query IDs, remove credential-like fields.
        sensitive = {"api_key", "apikey", "client_id", "clientid", "authorization", "access_token", "token", "key", "secret"}
        query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.casefold() not in sensitive]
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
    except ValueError:
        return ""


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(_text(value, 40)[:10])
    except ValueError:
        return None


def _number(value: Any, *, minimum: Decimal = Decimal("0")) -> Decimal | None:
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and minimum <= result <= Decimal("100000000") else None


def _money(value: Decimal) -> str:
    return format(value.quantize(_CENT, rounding=ROUND_HALF_UP), ".2f")


def build_quote(inputs: dict[str, Any] | None, *, today: date | None = None) -> dict[str, Any]:
    """Calculate four explicit combinations with Decimal and auditable order.

    percent.amount is the percentage deducted (5 means 5% off). The caller may
    set tier_benefit_ids to four lists; otherwise benefits are added in input
    order, one for tier 2, two for tier 3, and all for tier 4. This sequence is
    a calculation choice only, and is shown to the operator.
    """
    data = inputs if isinstance(inputs, dict) else {}
    today = today or date.today()
    base = _number(data.get("base_price"), minimum=Decimal("0.01"))
    sku, source_url = _text(data.get("sku"), 200), _url(data.get("source_url"))
    verified = _date(data.get("verified_at"))
    missing = []
    if base is None:
        missing.append("完整SKU的基础金额")
    if not sku:
        missing.append("完整SKU与系统/内存/存储/屏幕版本")
    if not source_url:
        missing.append("基础金额的HTTPS来源链接")
    if verified is None or verified > today:
        missing.append("不晚于今天的价格核对日期")
    raw_benefits = data.get("benefits") if isinstance(data.get("benefits"), list) else []
    benefits, duplicate_ids = {}, set()
    for index, raw in enumerate(raw_benefits[:8]):
        if not isinstance(raw, dict):
            continue
        ident = _text(raw.get("id") or f"benefit-{index + 1}", 80)
        if ident in benefits:
            duplicate_ids.add(ident)
        benefits[ident] = dict(raw, id=ident)
    ids = list(benefits)
    requested = data.get("tier_benefit_ids")
    if not (isinstance(requested, list) and len(requested) == 4
            and all(isinstance(v, list) for v in requested)):
        requested = [[], ids[:1], ids[:2], ids]
    tiers = []
    for index, requested_ids in enumerate(requested):
        tier = {"name": _TIER_NAMES[index], "price": None, "currency": "CNY", "status": "missing_input",
                "benefits": [], "conditions": [], "expression": "", "steps": [], "missing": [],
                "source_url": source_url, "verified_at": verified.isoformat() if verified else "", "sku": sku}
        selected_ids = [_text(v, 80) for v in requested_ids]
        if missing:
            tier["missing"] = list(missing)
            tiers.append(tier)
            continue
        if index and not selected_ids:
            tier["missing"] = ["该档方案的权益金额、适用资格及来源"]
            tiers.append(tier)
            continue
        issues = []
        if len(set(selected_ids)) != len(selected_ids):
            issues.append("同一权益不能重复扣减")
        if any(ident in duplicate_ids for ident in selected_ids):
            issues.append("权益ID重复，无法确定金额")
        selected = []
        for ident in selected_ids:
            benefit = benefits.get(ident)
            if benefit is None:
                issues.append("方案引用了不存在的权益")
                continue
            label = _text(benefit.get("label") or ident, 100)
            amount = _number(benefit.get("amount"), minimum=Decimal("0.01"))
            kind = _text(benefit.get("kind"))
            if kind not in {"fixed", "percent"} or amount is None or (kind == "percent" and amount >= 100):
                issues.append(label + "：请填写有效的减免金额或减免百分比（5表示减5%）")
            benefit_url = _url(benefit.get("source_url"))
            checked = _date(benefit.get("verified_at"))
            condition = _text(benefit.get("conditions"))
            if not benefit_url or checked is None or checked > today or not condition:
                issues.append(label + "：缺少权益自己的HTTPS来源、核对日期或适用条件")
            if benefit.get("eligible") is False:
                issues.append(label + "：已标记不符合资格")
            end_input = benefit.get("valid_until") or benefit.get("expires_at")
            start, end = _date(benefit.get("valid_from")), _date(end_input)
            if benefit.get("valid_from") and start is None or end_input and end is None:
                issues.append(label + "：有效期格式不正确")
            if start and start > today or end and end < today or start and end and start > end:
                issues.append(label + "：当前日期不在填写的有效期内")
            tier["benefits"].append(label)
            tier["conditions"].append(label + "：" + (condition or "待填写"))
            selected.append((benefit, amount, kind, benefit_url, checked))
        if len(selected) > 1:
            if any(item[0].get("stackable") is not True for item in selected):
                issues.append("组合中每项权益均须明确确认允许叠加")
            groups = [_text(item[0].get("exclusive_group")) for item in selected if item[0].get("exclusive_group")]
            if len(groups) != len(set(groups)):
                issues.append("同一互斥组的权益不能叠加")
        if issues:
            tier.update(status="conditions_unconfirmed", missing=list(dict.fromkeys(issues)))
            tiers.append(tier)
            continue
        payable = base
        expression = _money(base)
        for benefit, amount, kind, benefit_url, checked in selected:
            label = _text(benefit.get("label") or benefit["id"], 100)
            minimum_spend = _number(benefit.get("minimum_spend", "0"))
            cap = _number(benefit.get("cap")) if benefit.get("cap") not in (None, "") else None
            if minimum_spend is None or payable < minimum_spend:
                issues.append(label + "：按当前扣减顺序未满足门槛或门槛格式无效")
                break
            if benefit.get("cap") not in (None, "") and cap is None:
                issues.append(label + "：封顶金额格式无效")
                break
            discount = amount if kind == "fixed" else (payable * amount / Decimal("100")).quantize(_CENT, rounding=ROUND_HALF_UP)
            if cap is not None:
                discount = min(discount, cap)
            if discount > payable:
                issues.append(label + "：减免超过当前应付金额，请核对金额和扣减顺序")
                break
            before = payable
            payable = (payable - discount).quantize(_CENT, rounding=ROUND_HALF_UP)
            tier["steps"].append({"benefit": label, "before": _money(before), "discount": _money(discount),
                                  "after": _money(payable), "source_url": benefit_url,
                                  "verified_at": checked.isoformat(), "kind": kind, "amount": str(amount)})
            expression += f" − {_money(discount)}（{label}）"
        if issues:
            tier.update(status="conditions_unconfirmed", missing=issues, steps=[])
        else:
            tier.update(status="calculated_pending_verification", price=_money(payable),
                        expression=expression + " = " + _money(payable),
                        input_source="操作者填报；系统已检查字段与算术，未独立复核来源内容")
            if selected:
                tier["conditions"].append("按以上列出顺序逐项计算；百分比减免在该步骤当前应付金额上计算并四舍五入到分。")
        tiers.append(tier)
    calculated = sum(tier["price"] is not None for tier in tiers)
    all_missing = list(dict.fromkeys(missing + [item for tier in tiers for item in tier["missing"]]))
    scope = "已填报且可计算的四档方案中最低示算金额"
    candidates = [(index, tier) for index, tier in enumerate(tiers) if tier["price"] is not None]
    best_option = None
    if candidates:
        best_index, best = min(candidates, key=lambda item: Decimal(item[1]["price"]))
        best_option = {"name": best["name"], "price": best["price"], "tier_index": best_index,
                       "expression": best["expression"], "scope": scope,
                       "tied_names": [tier["name"] for _, tier in candidates if tier["price"] == best["price"]],
                       "status": "calculated_pending_verification"}
    return {"status": "calculated_pending_verification" if calculated == 4 else ("partial" if calculated else "missing_input"),
            "tiers": tiers, "missing": all_missing, "disclaimer": _QUOTE_NOTE, "as_of": today.isoformat(),
            "source_url": source_url, "sku": sku, "best_option": best_option, "optimization_scope": scope}


def _brief_evidence(brief: str, pattern: str) -> str:
    return next((part.strip()[:240] for part in re.split(r"[。；;\n]", brief) if re.search(pattern, part)), "")


def _insights(payload: dict[str, Any]) -> list[dict[str, str]]:
    context = payload.get("customer_context") if isinstance(payload.get("customer_context"), dict) else {}
    brief, audience, scene = (_text(payload.get(k), 3000) for k in ("brief", "audience", "scene"))
    occupation = _text(context.get("occupation"), 200)
    usage = "；".join(value for value in (audience, occupation, scene) if value)
    budget = _text(context.get("budget"), 300) or _brief_evidence(brief, r"预算|预算上限|价位|价格区间|不超过|元以内")
    stage = _text(context.get("purchase_stage"), 200) or _brief_evidence(brief, r"观望|比较|对比|犹豫|准备购买|近期购买|下单|换新|已购|购买阶段")
    concerns = _text(context.get("concerns"), 400) or _brief_evidence(brief, r"担心|顾虑|兼容|必须|必用|怕|不接受|障碍")
    devices = _text(context.get("devices"), 400) or _brief_evidence(brief, r"手机|平板|打印机|投影|显示器|外设|现有设备|常用软件")
    rows = [("使用任务", usage, "audience / customer_context.occupation / scene", "按已提供人群与场景安排体验，具体任务仍由顾客确认。"),
            ("预算与价值", budget, "customer_context.budget / brief", "按顾客原话核对预算边界；不把预算自动视为成交能力。"),
            ("决策阶段", stage, "customer_context.purchase_stage / brief", "按顾客明示阶段安排下一步，不推断其成交概率。"),
            ("顾虑与障碍", concerns, "customer_context.concerns / brief", "将明示顾虑转为现场核验项目，不推断未表达的焦虑。"),
            ("设备与体验环境", devices, "customer_context.devices / brief", "用顾客自己的软件版本、文件与外设现场核对。")]
    return [{"dimension": dim, "conclusion": evidence if evidence else "待确认：输入尚未提供这一维度的信息。",
             "evidence": "输入原文：“" + evidence + "”" if evidence else "无输入依据；未推断客户心理或需求。",
             "status": "provided" if evidence else "needs_confirmation", "source": source if evidence else "",
             "action": action} for dim, evidence, source, action in rows]


def _pick_fact(facts: list[dict[str, Any]], pattern: str) -> dict[str, Any] | None:
    return next((f for f in facts if re.search(pattern, str(f.get("text", "")) + str(f.get("category", "")))), None)


def _demo(payload: dict[str, Any], facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scene = _text(payload.get("scene"), 100)
    product = _text(payload.get("product"), 120)
    screen = _pick_fact(facts, r"屏幕|触控|OLED|分辨率")
    system = _pick_fact(facts, r"操作系统|Windows|HarmonyOS|Linux")
    interface = _pick_fact(facts, r"接口|USB|HDMI|无线|投屏")
    portability = _pick_fact(facts, r"重量|kg|\d+\s*g|电池|Wh")
    if any(key in scene for key in ("开学", "学生")):
        task = "打开顾客提供的一份课程文档或课件，按其习惯试读、切换窗口并确认字号"
    elif "创作" in scene:
        task = "在顾客常用且现场已可运行的软件中打开自备素材，核对显示、导入与保存路径"
    elif "企业" in scene:
        task = "用试点账号和一份脱敏业务文件核对登录、打开与保存，记录需IT进一步验证的环节"
    elif "家庭" in scene:
        task = "列出家庭常用软件和待迁移文件，先用一份无敏感内容的文件试开并核对保存位置"
    else:
        task = "打开顾客常用的一份文档，按其实际操作习惯试用，记录能完成和未完成的动作"
    def item(time: str, seconds: int, action: str, words: str, check: str, fact: dict | None = None) -> dict:
        return {"time": time, "seconds": seconds, "action": action, "words": words, "check": check,
                "fact_ids": [fact["id"]] if fact else [], "fact": fact.get("text", "") if fact else ""}
    return [
        item("00:00–00:20", 20, "复述需求，选定一项验收任务", f"今天围绕{scene}体验{product}。您最希望当场确认哪一件事？", "记录顾客确认的单项任务。"),
        item("00:20–00:50", 30, "核对样机型号与系统版本", "先核对样机标签、系统和具体配置，再测试您必须使用的软件版本。", "本轮原文摘录：" + system["quote"] if system else "现场记录系统和配置，与顾客必用软件核对。", system),
        item("00:50–01:30", 40, task, "请按自己的字号和操作习惯试一下，我会记录您的体验结果。", "规格原文摘录：" + screen["quote"] if screen else "记录文档显示与操作结果，注明应用版本和设置。", screen),
        item("01:30–02:10", 40, "用顾客实际外设或设备核对一次连接", "如果现场有您常用的设备，我们按具体接口和应用版本测试；没有设备的项目列为待确认。", "连接原文摘录：" + interface["quote"] if interface else "按实际接口测试；无设备时记录为未测试。", interface),
        item("02:10–02:40", 30, "确认携带感受与实际使用限制", "请拿起样机感受携带；我们把具体配置、资料中的重量和电池信息一起核对。", "原文摘录：" + portability["quote"] + "；容量不换算为实际续航。" if portability else "现场记录携带感受和需要进一步核对的规格。", portability),
        item("02:40–03:00", 20, "复述结果并约定下一步", "今天已确认的项目和仍待核对的项目分别是这些。您愿意接收哪一项资料，或约什么时间继续核对？", "记录通过、未通过、未测试结果与下一步，价格按当日来源确认。"),
    ]


def build_growth_plan(payload: dict[str, Any], facts: list[dict[str, Any]], strategy: dict[str, Any]) -> dict[str, Any]:
    scene, product = _text(payload.get("scene"), 100), _text(payload.get("product"), 120)
    quote = build_quote(payload.get("quote_inputs"))
    context = payload.get("customer_context") if isinstance(payload.get("customer_context"), dict) else {}
    local_focus = "；".join(label + "：“" + _text(context.get(key), limit) + "”" for key, label, limit in
                           (("concerns", "顾客明示顾虑", 200), ("devices", "顾客填写设备/软件", 200))
                           if _text(context.get(key), limit))
    content_strategy = "围绕" + scene + "，采用" + _text(strategy.get("angle"), 500) + "；渠道结构：" + _text(strategy.get("structure"), 300)
    if local_focus:
        content_strategy += "。门店现场优先核对" + local_focus
    return {"method": "客户洞察保留输入原文，报价列出计算条件，体验计划关联已提取事实。",
            "insights": _insights(payload),
            "content_strategy": content_strategy,
            "quote": quote, "demo_total_seconds": 180, "demo_script": _demo(payload, facts),
            "followups": [
                {"day": "第1天", "action": "回顾咨询，补齐未验证项", "message": f"您好，针对前次咨询的{scene}需求，{product}还有哪项任务需要进一步确认？我可以按您的意愿整理已确认与待确认清单。"},
                {"day": "第3天", "action": "提供与顾虑对应的证据并核对方案", "message": "前次待确认的软件、外设或预算问题，您希望先核对哪一项？确认具体版本和配置后，我再提供对应资料；价格与权益也会按当天原来源重新核对。"},
                {"day": "第7天", "action": "征求后续联系意愿，支持继续比较或结束跟进", "message": "这几天的比较是否有新问题？如果仍有需要，我们可约一次针对性的核对；若暂时不考虑，我就不继续打扰，您需要时再联系即可。"},
            ],
            "followup_note": "以下为待人工确认的跟进草稿和建议节奏，尚未安排或发送；联系时间及方式以顾客同意为准。"}


def growth_markdown(plan: dict[str, Any]) -> str:
    lines = ["", "## 五、客户洞察与门店执行", "", plan["method"], "", "### 五维客户洞察", ""]
    for insight in plan["insights"]:
        lines.extend([f"- **{insight['dimension']}**：{insight['conclusion']}", f"  - 依据：{insight['evidence']}"])
    lines.extend(["", "### 四档报价示算", "", plan["quote"]["disclaimer"], ""])
    for tier in plan["quote"]["tiers"]:
        lines.append(f"- **{tier['name']}**：" + ("¥" + tier["price"] + "；" + tier["expression"] if tier["price"] is not None else "待补充：" + "；".join(tier["missing"])))
        if tier["source_url"]:
            lines.append(f"  - SKU：{tier['sku']}；[填报来源]({tier['source_url']})；填报核对日期：{tier['verified_at']}")
        lines.extend("  - " + condition for condition in tier["conditions"])
    if plan["quote"].get("best_option"):
        best = plan["quote"]["best_option"]
        lines.extend(["", f"**{best['scope']}**：{best['name']}，¥{best['price']}。"])
    lines.extend(["", "### 3分钟门店演示脚本", "", "计划总时长180秒；按现场条件执行，未测试项目如实记录。", ""])
    for step in plan["demo_script"]:
        lines.extend([f"- **{step['time']}** {step['action']}", "  - 话术：" + step["words"], "  - 检查：" + step["check"]])
    lines.extend(["", "### 第1/3/7天跟进草稿", "", plan["followup_note"], ""])
    for followup in plan["followups"]:
        lines.extend([f"- **{followup['day']} · {followup['action']}**", "  - " + followup["message"]])
    return "\n".join(lines) + "\n"
