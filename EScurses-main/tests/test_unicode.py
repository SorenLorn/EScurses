"""Unicode 宽度与 grapheme 切分测试。"""

from __future__ import annotations

import unittest

from escurses.unicode_utils import (
    cluster_width,
    codepoint_width,
    graphemes,
    is_control,
    text_width,
    truncate_to_width,
)


class TestCodepointWidth(unittest.TestCase):
    def test_ascii_is_single_width(self) -> None:
        for ch in "aZ0 #":
            self.assertEqual(codepoint_width(ch), 1, ch)

    def test_cjk_is_double_width(self) -> None:
        for ch in "中文汉字":
            self.assertEqual(codepoint_width(ch), 2, ch)

    def test_fullwidth_punctuation_is_double_width(self) -> None:
        for ch in "，。！？（）":
            self.assertEqual(codepoint_width(ch), 2, ch)

    def test_halfwidth_punctuation_is_single_width(self) -> None:
        for ch in ",.!?():":
            self.assertEqual(codepoint_width(ch), 1, ch)

    def test_halfwidth_kana_is_single_width(self) -> None:
        # U+FF76 半角片假名 Ka
        self.assertEqual(codepoint_width("\uff76"), 1)

    def test_combining_mark_is_zero_width(self) -> None:
        self.assertEqual(codepoint_width("\u0301"), 0)  # COMBINING ACUTE ACCENT

    def test_zwj_is_zero_width(self) -> None:
        self.assertEqual(codepoint_width("\u200d"), 0)

    def test_control_char_is_zero_width(self) -> None:
        self.assertEqual(codepoint_width("\n"), 0)

    def test_requires_single_character(self) -> None:
        with self.assertRaises(ValueError):
            codepoint_width("ab")


class TestTextWidth(unittest.TestCase):
    def test_ascii(self) -> None:
        self.assertEqual(text_width("Hello"), 5)

    def test_chinese(self) -> None:
        self.assertEqual(text_width("你好"), 4)

    def test_mixed(self) -> None:
        self.assertEqual(text_width("Hi你好"), 6)

    def test_empty(self) -> None:
        self.assertEqual(text_width(""), 0)

    def test_length_is_not_width(self) -> None:
        self.assertNotEqual(len("你好"), text_width("你好"))


class TestGraphemes(unittest.TestCase):
    def test_plain_ascii(self) -> None:
        self.assertEqual(graphemes("abc"), ["a", "b", "c"])

    def test_combining_mark_stays_with_base(self) -> None:
        text = "e\u0301"
        clusters = graphemes(text)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(cluster_width(clusters[0]), 1)

    def test_cjk_char_is_one_cluster(self) -> None:
        self.assertEqual(graphemes("中"), ["中"])

    def test_variation_selector_stays_with_base(self) -> None:
        clusters = graphemes("\u2764\ufe0f")  # ❤️
        self.assertEqual(len(clusters), 1)

    def test_zwj_sequence_is_one_cluster(self) -> None:
        family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
        clusters = graphemes(family)
        self.assertEqual(len(clusters), 1)
        # 按"最大码位宽度"估算，2 列；具体终端可能不同（已在文档声明限制）
        self.assertEqual(cluster_width(clusters[0]), 2)

    def test_flag_is_one_cluster(self) -> None:
        flag = "\U0001f1e8\U0001f1f3"  # 🇨🇳
        self.assertEqual(len(graphemes(flag)), 1)

    def test_skin_tone_is_one_cluster(self) -> None:
        clusters = graphemes("\U0001f44d\U0001f3fb")  # 👍🏻
        self.assertEqual(len(clusters), 1)


class TestTruncate(unittest.TestCase):
    def test_truncate_ascii(self) -> None:
        self.assertEqual(truncate_to_width("abcdef", 3), "abc")

    def test_truncate_never_splits_wide_char(self) -> None:
        self.assertEqual(truncate_to_width("中文字", 3), "中")

    def test_truncate_exact(self) -> None:
        self.assertEqual(truncate_to_width("中文", 4), "中文")

    def test_truncate_zero(self) -> None:
        self.assertEqual(truncate_to_width("abc", 0), "")

    def test_truncate_longer_than_text(self) -> None:
        self.assertEqual(truncate_to_width("ab", 10), "ab")


class TestControl(unittest.TestCase):
    def test_control_chars(self) -> None:
        for ch in ("\n", "\t", "\x1b", "\x07", "\x00"):
            self.assertTrue(is_control(ch), repr(ch))

    def test_printable_chars(self) -> None:
        for ch in ("a", "中", " "):
            self.assertFalse(is_control(ch), repr(ch))


if __name__ == "__main__":
    unittest.main()
