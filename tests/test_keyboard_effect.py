"""Hardware availability alone must not become an input experience comparison."""
import unittest
from engine import check_text, _clean_screen_claims


FACTS = [{"id": "F1", "quote": "采用全尺寸键盘，1.5 mm 键程，并支持独立数字键盘。"}]


class KeyboardEffectTests(unittest.TestCase):
    def test_keyboard_effect_requires_direct_evidence(self):
        for text in ("数字输入更顺手。", "数字输入可以更直接。", "数字键区更直接", "键盘录入更快。", "独立数字键盘提升输入效率。", "不从规格推导但键盘让数字输入更顺手。", "不从规格推导更顺手、更直接、更高效，但数字输入更顺手。"):
            with self.subTest(text=text):
                self.assertTrue(any(f["type"] == "事实·规格使用效果" for f in check_text(text, FACTS)))

    def test_invitation_and_direct_source_are_preserved(self):
        for text in ("请现场试试键盘是否更顺手。", "键盘是否更直接，建议带表格到店核对。", "不承诺数字输入更顺手。", "请现场试是否顺手。", "不从规格推导更顺手、更直接、更高效等使用体验。", "不从规格推导更顺手、更直接、更高效。"):
            with self.subTest(text=text):
                self.assertEqual(check_text(text, FACTS), [])
        text = "独立数字键盘让数字输入更顺手。"
        self.assertEqual(check_text(text, [{"quote": text}]), [])

    def test_saved_d16_sentence_removal_keeps_specs_and_invitation(self):
        article = {"title": "这台本有独立数字键盘", "lead": "键盘支持独立数字键盘，数字输入更顺手。", "sections": [{"heading": "数字键区更直接", "text": "采用全尺寸键盘，1.5 mm 键程，并支持独立数字键盘。做表格、录数据时，数字输入可以更直接。欢迎带表格到店体验。"}], "cta": "私信预约体验。", "tags": []}
        cleaned, _, checks = _clean_screen_claims(article, [], FACTS, "D16", "办公", "预约体验")
        self.assertNotIn("更顺手", str(cleaned))
        self.assertNotIn("更直接", str(cleaned))
        self.assertIn("1.5 mm", cleaned["sections"][0]["text"])
        self.assertIn("到店体验", cleaned["sections"][0]["text"])
        self.assertEqual(len(checks), 3)


if __name__ == "__main__":
    unittest.main()
