"""Behavioral checks for quote math and truthfulness of the store workflow."""
from datetime import date
import unittest
from unittest.mock import patch

from growth import build_growth_plan, build_quote, growth_markdown


TODAY = date(2026, 10, 18)


def offer():
    return {
        "base_price": "100.10", "sku": "测试SKU，仅用于算术测试", "source_url": "https://www.vmall.com/product?prdId=123&token=remove",
        "verified_at": "2026-10-18", "benefits": [
            {"id": "a", "label": "减额", "kind": "fixed", "amount": "0.10", "stackable": True,
             "conditions": "仅测试条件A", "source_url": "https://www.vmall.com/a", "verified_at": "2026-10-18"},
            {"id": "b", "label": "减百分比", "kind": "percent", "amount": "10", "stackable": True,
             "conditions": "仅测试条件B", "source_url": "https://www.vmall.com/b", "verified_at": "2026-10-18"},
            {"id": "c", "label": "尾项减额", "kind": "fixed", "amount": "1.01", "stackable": True,
             "conditions": "仅测试条件C", "source_url": "https://www.vmall.com/c", "verified_at": "2026-10-18"},
        ],
    }


class QuoteTests(unittest.TestCase):
    def test_missing_source_never_becomes_fake_zero_or_price(self):
        result = build_quote({}, today=TODAY)
        self.assertEqual(len(result["tiers"]), 4)
        self.assertTrue(all(t["price"] is None for t in result["tiers"]))
        self.assertTrue(result["missing"])
        data = offer()
        data.pop("source_url")
        self.assertTrue(all(t["price"] is None for t in build_quote(data, today=TODAY)["tiers"]))

    def test_decimal_order_and_percentage_mean_deduct_not_pay(self):
        result = build_quote(offer(), today=TODAY)
        self.assertEqual([t["price"] for t in result["tiers"]], ["100.10", "100.00", "90.00", "88.99"])
        self.assertEqual(result["tiers"][2]["steps"][1]["discount"], "10.00")
        self.assertIn("prdId=123", result["source_url"])
        self.assertNotIn("token", result["source_url"])
        self.assertEqual(result["status"], "calculated_pending_verification")
        self.assertEqual(result["best_option"]["price"], "88.99")
        self.assertEqual(result["best_option"]["name"], "完整权益")
        self.assertEqual(result["best_option"]["scope"], "已填报且可计算的四档方案中最低示算金额")

    def test_no_unconfirmed_stacking(self):
        data = offer()
        data["benefits"][0].pop("stackable")
        tiers = build_quote(data, today=TODAY)["tiers"]
        self.assertIsNotNone(tiers[1]["price"])
        self.assertIsNone(tiers[2]["price"])
        self.assertIn("叠加", " ".join(tiers[2]["missing"]))

    def test_exclusivity_and_duplicate_prevent_double_discount(self):
        data = offer()
        for benefit in data["benefits"][:2]:
            benefit["exclusive_group"] = "one-policy"
        self.assertIsNone(build_quote(data, today=TODAY)["tiers"][2]["price"])
        data["tier_benefit_ids"] = [[], ["a"], ["a", "a"], ["b", "c"]]
        self.assertIsNone(build_quote(data, today=TODAY)["tiers"][2]["price"])

    def test_expiry_false_eligibility_and_future_dates(self):
        data = offer()
        data["benefits"][0]["valid_until"] = "2026-10-17"
        self.assertIsNone(build_quote(data, today=TODAY)["tiers"][1]["price"])
        data = offer()
        data["benefits"][0]["eligible"] = False
        self.assertIsNone(build_quote(data, today=TODAY)["tiers"][1]["price"])
        data = offer()
        data["verified_at"] = "2026-10-19"
        self.assertTrue(all(t["price"] is None for t in build_quote(data, today=TODAY)["tiers"]))

    def test_threshold_cap_overdeduction_and_nan(self):
        data = offer()
        data["benefits"][1].update(amount="20", cap="3.21")
        self.assertEqual(build_quote(data, today=TODAY)["tiers"][2]["price"], "96.79")
        data["benefits"][1]["minimum_spend"] = "100.01"
        self.assertIsNone(build_quote(data, today=TODAY)["tiers"][2]["price"])
        data = offer()
        data["benefits"][0]["amount"] = "200"
        self.assertIsNone(build_quote(data, today=TODAY)["tiers"][1]["price"])
        data["base_price"] = "NaN"
        self.assertTrue(all(t["price"] is None for t in build_quote(data, today=TODAY)["tiers"]))


class WorkflowTests(unittest.TestCase):
    def test_structured_customer_context_and_quotes_never_reach_model_task(self):
        from engine import EngineError, MarketingEngine
        engine = MarketingEngine()
        engine.url, engine.key, engine.model = "https://example.invalid/chat/completions", "test-placeholder", "mocked"
        payload = {"product": "MateBook 14 酷睿 Ultra", "audience": "大学新生", "scene": "开学季", "platform": "门店海报", "brief": "公开产品介绍",
                   "customer_context": {"occupation": "local-only-value", "devices": "local-device", "budget": "12345"},
                   "quote_inputs": {"base_price": "9999"}}
        materials = {"product_name": payload["product"], "documents": [{"title": "测试原文", "text": "仅供本地边界测试的产品原文，不触发外部请求。", "source": {}}],
                     "images": [{"id": "mock-image", "product_name": payload["product"], "url": "/mock.png", "source": {}}]}
        captured = []
        def intercept(instruction, data, max_tokens=4500):
            captured.append(data)
            raise EngineError("本地模拟已在模型边界停止；没有网络请求")
        engine._chat = intercept
        with patch("public_evidence.build_public_evidence", return_value=materials):
            engine.generate(payload, materials)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["task"]["brief"], "公开产品介绍")
        self.assertNotIn("customer_context", captured[0]["task"])
        self.assertNotIn("quote_inputs", captured[0]["task"])
        self.assertNotIn("local-only-value", str(captured))
        self.assertNotIn("local-device", str(captured))

    def test_no_fabricated_customer_psychology(self):
        payload = {"product": "MateBook 14 酷睿 Ultra", "audience": "大学新生", "scene": "开学季",
                   "customer_context": {"budget": "不超过7000元", "concerns": "必须使用课程指定软件"}}
        facts = [{"id": "F1", "text": "屏幕14.2英寸", "quote": "屏幕尺寸14.2英寸", "category": "屏幕"}]
        plan = build_growth_plan(payload, facts, {"angle": "核对课程任务", "structure": "短段落"})
        self.assertEqual(len(plan["insights"]), 5)
        self.assertIn("不超过7000元", plan["insights"][1]["evidence"])
        self.assertEqual(plan["insights"][2]["status"], "needs_confirmation")
        self.assertEqual(plan["insights"][4]["status"], "needs_confirmation")
        self.assertIn("必须使用课程指定软件", plan["content_strategy"])
        self.assertEqual(sum(s["seconds"] for s in plan["demo_script"]), 180)
        self.assertEqual(plan["demo_script"][2]["fact_ids"], ["F1"])
        self.assertEqual([f["day"] for f in plan["followups"]], ["第1天", "第3天", "第7天"])
        self.assertEqual(len({f["message"] for f in plan["followups"]}), 3)
        self.assertTrue(all(t["price"] is None for t in plan["quote"]["tiers"]))
        markdown = growth_markdown(plan)
        self.assertIn("180秒", markdown)
        self.assertIn("尚未安排或发送", markdown)
        self.assertNotIn("95%", markdown)


if __name__ == "__main__":
    unittest.main()
