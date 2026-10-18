import unittest
from engine import _remove_paths


class SemanticSentenceRemovalTests(unittest.TestCase):
    def remove(self, text, claims, heading="键盘规格"):
        article = {"title": "D16", "sections": [{"heading": heading, "text": text, "fact_ids": ["F13"]}]}
        issues = [{"path": "sections.0", "claim": claim} for claim in claims]
        return _remove_paths(article, [], {"sections.0"}, "D16", "办公", "预约", issues)[0]

    def test_exact_d16_issue_preserves_fact_and_invitation(self):
        result = self.remove("支持独立数字键盘。数字录入时手指有独立区域可用。欢迎带表格到店体验。", ["数字录入时手指有独立区域可用。"])
        self.assertEqual(result["sections"][0]["text"], "支持独立数字键盘。欢迎带表格到店体验。")
        self.assertEqual(result["sections"][0]["fact_ids"], ["F13"])
        # Text copied from the earlier saved real D16 run; inject the known
        # unsupported clause to exercise removal without another model call.
        saved = "HUAWEI MateBook D 16 2024 采用全尺寸键盘，1.5 mm 键程，并支持独立数字键盘。做表格、录数据时，数字输入可以更直接。想感受键位布局，欢迎带常用表格文件到店体验。"
        result = self.remove(saved, ["数字输入可以更直接。"])
        self.assertIn("1.5 mm", result["sections"][0]["text"])
        self.assertIn("到店体验", result["sections"][0]["text"])
        self.assertNotIn("更直接", result["sections"][0]["text"])

    def test_multiple_issues_use_original_offsets(self):
        result = self.remove("有独立数字键盘。数字输入更顺手。全尺寸键盘。数字录入更直接。欢迎到店。", ["数字输入更顺手", "数字录入更直接"])
        self.assertEqual(result["sections"][0]["text"], "有独立数字键盘。全尺寸键盘。欢迎到店。")

    def test_imprecise_duplicate_unfinished_and_heading_still_remove_section(self):
        for text, claim, heading in [
            ("有键盘。数字输入更顺手。", "改写后的不精确描述", "规格"),
            ("数字输入更顺手。数字输入更顺手。", "数字输入更顺手", "规格"),
            ("有键盘。数字输入更顺手", "数字输入更顺手", "规格"),
            ("有键盘。数字输入更顺手。", "数字输入更顺手", "数字输入更顺手"),
        ]:
            with self.subTest(text=text, claim=claim):
                self.assertEqual(self.remove(text, [claim], heading)["sections"], [])
        self.assertEqual(self.remove("有键盘。数字输入更顺手。", ["数字输入更顺手", "无法精确定位的另一个问题"])["sections"], [])


if __name__ == "__main__":
    unittest.main()
