"""Regression checks for brief prohibitions and later affirmative requests."""
import unittest

from engine import check_text


class DenialScopeTests(unittest.TestCase):
    def test_parallel_prohibitions_do_not_become_claims(self):
        for text in (
            "不编造亲测、价格、补贴、销量或续航。",
            "不写亲测、价格、补贴、销量或软件全面兼容。",
            "不写价格、补贴或所有软件都兼容。",
            "不得虚构亲测、价格、补贴或续航。",
            "不虚构补贴或销量冠军。",
            "不伪造亲测好用和销量冠军。",
            "请勿写价格、补贴或软件全面兼容。",
            "避免使用全面兼容、赠送或包邮表述。",
        ):
            with self.subTest(text=text):
                self.assertEqual(check_text(text), [])

    def test_affirmative_request_after_turn_still_requires_evidence(self):
        for text, kind in (
            ("不要写价格但请加补贴。", "合规·动态权益"),
            ("不写价格，但请加补贴。", "合规·动态权益"),
            ("不编造亲测、价格、补贴，但请写包邮。", "合规·动态权益"),
            ("不写价格，不过请加赠送礼品。", "合规·动态权益"),
            ("不写软件全面兼容但所有软件都兼容。", "合规·全面兼容"),
            ("不保证所有软件兼容，但是这台机器支持所有应用完全兼容。", "合规·全面兼容"),
            ("不要虚构亲测然而请写我亲测好用。", "合规·虚构体验"),
            ("不编造价格但请写999元。", "事实·数字依据"),
            ("不写价格，但请加补贴；优惠以门店核对为准。", "合规·动态权益"),
            ("不写价格，但请加补贴，不过兼容性仍需确认。", "合规·动态权益"),
        ):
            with self.subTest(text=text):
                self.assertIn(kind, {item["type"] for item in check_text(text)})

    def test_regular_promises_and_existing_safe_limitations(self):
        for text in ("我亲测好用。", "有补贴。", "所有软件都兼容。", "赠送礼品。", "全网最低价。"):
            with self.subTest(text=text):
                self.assertTrue(check_text(text))
        for text in ("不保证所有软件兼容。", "接口存在不等于所有外设都兼容。", "不承诺一整天续航。"):
            with self.subTest(text=text):
                self.assertEqual(check_text(text), [])

if __name__ == "__main__":
    unittest.main()
