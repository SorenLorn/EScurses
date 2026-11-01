"""屏幕绘制、边界策略与宽字符测试。"""

from __future__ import annotations

import unittest

from escurses.buffer import BLANK, CONTINUATION
from escurses.exceptions import InvalidCharError, OutOfBoundsError
from escurses.screen import Screen


class TestCoordinateSystem(unittest.TestCase):
    def test_top_left_is_one_based(self) -> None:
        screen = Screen(5, 3)
        screen.set(1, 1, "A")
        self.assertEqual(screen.row_text(1), "A")
        self.assertEqual(screen.get(1, 1).char, "A")

    def test_bottom_right_is_writable(self) -> None:
        screen = Screen(5, 3)
        self.assertTrue(screen.set(5, 3, "Z"))
        self.assertEqual(screen.row_text(3), "    Z")

    def test_last_column_is_not_reserved(self) -> None:
        """审计 DRAW-02：缓冲区必须和公开尺寸一致，不能少一格。"""
        screen = Screen(80, 24)
        self.assertEqual(screen.width, 80)
        self.assertTrue(screen.set(80, 1, "A"))
        self.assertEqual(len(screen.buffer.back.row(0)), 80)

    def test_size_property(self) -> None:
        self.assertEqual(Screen(7, 4).size, (7, 4))


class TestClippingDefault(unittest.TestCase):
    def setUp(self) -> None:
        self.screen = Screen(5, 3)

    def test_negative_coordinates_do_not_crash(self) -> None:
        # 审计 DRAW-01：旧实现靠负索引静默写到错误位置
        self.assertFalse(self.screen.set(-1, -1, "Z"))
        self.assertFalse(self.screen.set(0, 0, "Z"))
        for y in range(3):
            self.assertEqual(self.screen.row_text(y + 1), "")

    def test_x_beyond_width(self) -> None:
        self.assertFalse(self.screen.set(6, 1, "A"))

    def test_y_beyond_height(self) -> None:
        self.assertFalse(self.screen.set(1, 4, "A"))

    def test_huge_coordinates(self) -> None:
        self.assertFalse(self.screen.set(10**6, 10**6, "A"))

    def test_text_clipped_at_right_edge(self) -> None:
        written = self.screen.text(4, 1, "ABCDE")
        self.assertEqual(written, 2)
        self.assertEqual(self.screen.row_text(1), "   AB")

    def test_text_starting_outside(self) -> None:
        self.assertEqual(self.screen.text(10, 1, "AB"), 0)
        self.assertEqual(self.screen.row_text(1), "")

    def test_text_partially_left_of_screen(self) -> None:
        # x = -1：A 落在第 -2 列、B 落在第 -1 列，都被裁掉；C 落在第 1 列
        written = self.screen.text(-1, 1, "ABC")
        self.assertEqual(written, 1)
        self.assertEqual(self.screen.row_text(1), "C")

    def test_get_out_of_range_returns_blank(self) -> None:
        self.assertIs(self.screen.get(0, 0), BLANK)
        self.assertIs(self.screen.get(99, 99), BLANK)


class TestStrictMode(unittest.TestCase):
    def setUp(self) -> None:
        self.screen = Screen(5, 3, strict=True)

    def test_negative_raises(self) -> None:
        with self.assertRaises(OutOfBoundsError):
            self.screen.set(0, 0, "A")

    def test_x_too_large_raises(self) -> None:
        with self.assertRaises(OutOfBoundsError):
            self.screen.set(6, 1, "A")

    def test_y_too_large_raises(self) -> None:
        with self.assertRaises(OutOfBoundsError):
            self.screen.set(1, 4, "A")

    def test_exception_is_index_error_subclass(self) -> None:
        """旧代码的 ``except IndexError`` 必须仍然有效。"""
        with self.assertRaises(IndexError):
            self.screen.set(0, 0, "A")

    def test_valid_coordinates_still_work(self) -> None:
        self.assertTrue(self.screen.set(5, 3, "A"))

    def test_strict_can_be_overridden_per_call(self) -> None:
        self.assertFalse(self.screen.set(0, 0, "A", strict=False))

    def test_control_char_raises(self) -> None:
        with self.assertRaises(InvalidCharError):
            self.screen.set(1, 1, "\n")

    def test_multi_char_raises_in_set(self) -> None:
        with self.assertRaises(InvalidCharError):
            self.screen.set(1, 1, "AB")


class TestCharacterValidation(unittest.TestCase):
    def test_non_string_rejected(self) -> None:
        screen = Screen(5, 3)
        with self.assertRaises(TypeError):
            screen.set(1, 1, 65)  # type: ignore[arg-type]

    def test_empty_string_is_noop(self) -> None:
        screen = Screen(5, 3)
        screen.set(1, 1, "A")
        self.assertFalse(screen.set(1, 1, ""))
        self.assertEqual(screen.row_text(1), "A")

    def test_multi_char_writes_sequentially(self) -> None:
        screen = Screen(5, 3)
        self.assertTrue(screen.set(1, 1, "AB"))
        self.assertEqual(screen.row_text(1), "AB")

    def test_control_char_is_skipped_by_default(self) -> None:
        screen = Screen(8, 2)
        screen.text(1, 1, "A\nB\tC\x1b[31m")
        # 控制字符被丢弃，可打印字符依次写入；不会注入转义序列
        self.assertEqual(screen.row_text(1), "ABC[31m")

    def test_float_coordinates_rejected(self) -> None:
        screen = Screen(5, 3)
        with self.assertRaises(TypeError):
            screen.set(1.5, 1, "A")  # type: ignore[arg-type]

    def test_bool_coordinates_rejected(self) -> None:
        screen = Screen(5, 3)
        with self.assertRaises(TypeError):
            screen.set(True, True, "A")  # type: ignore[arg-type]


class TestWideCharacters(unittest.TestCase):
    def setUp(self) -> None:
        self.screen = Screen(10, 3)

    def test_wide_char_occupies_two_cells(self) -> None:
        self.screen.set(1, 1, "中")
        self.assertEqual(self.screen.buffer.back.get(0, 0).width, 2)
        self.assertIs(self.screen.buffer.back.get(1, 0), CONTINUATION)

    def test_next_text_starts_after_wide_char(self) -> None:
        """审计 DRAW-03 的核心回归：中文后面不能错位。"""
        self.screen.text(1, 1, "中文AB")
        self.assertEqual(self.screen.row_text(1), "中文AB")
        self.assertEqual(self.screen.buffer.back.get(4, 0).char, "A")

    def test_wide_char_at_last_column_is_clipped(self) -> None:
        screen = Screen(4, 2)
        self.assertFalse(screen.set(4, 1, "中"))
        self.assertEqual(screen.row_text(1), "")

    def test_overwriting_right_half_clears_left_half(self) -> None:
        self.screen.set(1, 1, "中")
        self.screen.set(2, 1, "X")
        # 半个字无法显示，左半格必须被抹掉
        self.assertEqual(self.screen.row_text(1), " X")

    def test_overwriting_left_half_clears_right_half(self) -> None:
        self.screen.set(1, 1, "中")
        self.screen.set(1, 1, "X")
        self.assertEqual(self.screen.row_text(1), "X")
        # 原来的右半格必须变回普通空格，不能残留 CONTINUATION
        self.assertIs(self.screen.buffer.back.get(1, 0), BLANK)

    def test_wide_char_over_wide_char(self) -> None:
        self.screen.set(1, 1, "中")
        self.screen.set(1, 1, "文")
        self.assertEqual(self.screen.row_text(1), "文")

    def test_narrow_then_wide_at_same_spot(self) -> None:
        self.screen.set(1, 1, "A")
        self.screen.set(1, 1, "中")
        self.assertIs(self.screen.buffer.back.get(1, 0), CONTINUATION)

    def test_wide_char_overwriting_next_wide_char_start(self) -> None:
        screen = Screen(10, 1)
        screen.text(1, 1, "中中")
        screen.set(1, 1, "文")
        self.assertEqual(screen.row_text(1), "文中")
        # 第二个"中"的左半在被覆盖的右半格之外，保持完整
        self.assertEqual(screen.buffer.back.get(2, 0).char, "中")

    def test_text_with_wide_chars_width(self) -> None:
        written = self.screen.text(1, 1, "你好")
        self.assertEqual(written, 4)

    def test_combining_mark_cluster_is_one_cell(self) -> None:
        self.screen.text(1, 1, "e\u0301x")
        self.assertEqual(self.screen.buffer.back.get(0, 0).char, "e\u0301")
        self.assertEqual(self.screen.buffer.back.get(1, 0).char, "x")
        self.assertEqual(self.screen.row_text(1), "e\u0301x")


class TestFillAndClear(unittest.TestCase):
    def test_fill_rectangle(self) -> None:
        screen = Screen(5, 4)
        count = screen.fill(2, 2, 3, 3, "#")
        self.assertEqual(count, 4)
        self.assertEqual(screen.lines(), ["", " ##", " ##", ""])

    def test_fill_is_clipped(self) -> None:
        screen = Screen(3, 3)
        screen.fill(-5, -5, 99, 99, "x")
        self.assertEqual(screen.lines(), ["xxx", "xxx", "xxx"])

    def test_fill_normalizes_reversed_corners(self) -> None:
        screen = Screen(4, 2)
        screen.fill(3, 2, 2, 1, "x")
        self.assertEqual(screen.lines(), [" xx", " xx"])

    def test_clear(self) -> None:
        screen = Screen(4, 2)
        screen.text(1, 1, "AB")
        screen.clear()
        self.assertEqual(screen.lines(), ["", ""])
        self.assertTrue(screen.needs_full_redraw)


class TestResize(unittest.TestCase):
    def test_resize_preserves_overlap(self) -> None:
        screen = Screen(5, 3)
        screen.text(1, 1, "AB")
        self.assertTrue(screen.resize(8, 4))
        self.assertEqual(screen.size, (8, 4))
        self.assertEqual(screen.row_text(1), "AB")
        self.assertTrue(screen.needs_full_redraw)

    def test_resize_same_size(self) -> None:
        screen = Screen(5, 3)
        self.assertFalse(screen.resize(5, 3))


if __name__ == "__main__":
    unittest.main()
