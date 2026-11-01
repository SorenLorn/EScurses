"""差异渲染测试。"""

from __future__ import annotations

import unittest

from escurses.renderer import Renderer
from escurses.screen import Screen
from escurses.terminal import SEQ_CLEAR, SEQ_HOME, Terminal

from .helpers import FakeStream


def make(width: int = 10, height: int = 4):
    stream = FakeStream(tty=True)
    terminal = Terminal(stream, force_ansi=True)
    screen = Screen(width, height)
    renderer = Renderer(terminal, screen)
    return stream, terminal, screen, renderer


class TestFirstFrame(unittest.TestCase):
    def test_first_frame_is_full(self) -> None:
        stream, _, screen, renderer = make()
        screen.text(1, 1, "Hello")
        stats = renderer.present()
        self.assertTrue(stats.full)
        self.assertIn(SEQ_CLEAR, stream.getvalue())
        self.assertIn("Hello", stream.getvalue())

    def test_first_frame_only_writes_non_blank_rows(self) -> None:
        _, _, screen, renderer = make(10, 6)
        screen.text(1, 3, "mid")
        stats = renderer.present()
        self.assertEqual(stats.rows, 1)

    def test_first_frame_has_cursor_positioning(self) -> None:
        stream, _, screen, renderer = make()
        screen.text(1, 2, "x")
        renderer.present()
        self.assertIn("\x1b[2;1H", stream.getvalue())


class TestIdleFrame(unittest.TestCase):
    def test_no_output_when_nothing_changed(self) -> None:
        stream, _, screen, renderer = make()
        screen.text(1, 1, "Hello")
        renderer.present()
        stream.take()
        stats = renderer.present()
        self.assertEqual(stats.bytes_written, 0)
        self.assertEqual(stats.cells, 0)
        self.assertEqual(stream.getvalue(), "")

    def test_many_idle_frames_stay_silent(self) -> None:
        stream, _, screen, renderer = make()
        screen.text(1, 1, "Hello")
        renderer.present()
        stream.take()
        for _ in range(10):
            renderer.present()
        self.assertEqual(stream.getvalue(), "")


class TestDiffOutput(unittest.TestCase):
    def setUp(self) -> None:
        self.stream, _, self.screen, self.renderer = make(20, 4)
        self.screen.text(1, 1, "AAAAABBBBB")
        self.renderer.present()
        self.stream.take()

    def test_single_char_change(self) -> None:
        self.screen.set(3, 1, "X")
        stats = self.renderer.present()
        self.assertFalse(stats.full)
        self.assertEqual(stats.cells, 1)
        self.assertEqual(self.stream.getvalue(), "\x1b[1;3HX")

    def test_unchanged_neighbours_are_not_rewritten(self) -> None:
        self.screen.set(3, 1, "X")
        self.renderer.present()
        output = self.stream.getvalue()
        self.assertNotIn("A", output)

    def test_consecutive_changes_merge_into_one_span(self) -> None:
        self.screen.text(2, 1, "xyz")
        stats = self.renderer.present()
        self.assertEqual(stats.spans, 1)
        self.assertEqual(stats.cells, 3)
        self.assertIn("xyz", self.stream.getvalue())

    def test_gap_of_one_is_merged(self) -> None:
        self.screen.set(1, 1, "X")
        self.screen.set(3, 1, "Y")
        stats = self.renderer.present()
        self.assertEqual(stats.spans, 1)

    def test_gap_of_two_is_merged(self) -> None:
        self.screen.set(1, 1, "X")
        self.screen.set(4, 1, "Y")
        stats = self.renderer.present()
        self.assertEqual(stats.spans, 1)

    def test_large_gap_is_not_merged(self) -> None:
        self.screen.set(1, 1, "X")
        self.screen.set(10, 1, "Y")
        stats = self.renderer.present()
        self.assertEqual(stats.spans, 2)

    def test_multiple_rows(self) -> None:
        self.screen.set(1, 1, "X")
        self.screen.set(1, 3, "Y")
        stats = self.renderer.present()
        self.assertEqual(stats.rows, 2)

    def test_clearing_a_char(self) -> None:
        self.screen.set(5, 1, " ")
        stats = self.renderer.present()
        self.assertEqual(stats.cells, 1)
        self.assertEqual(self.stream.getvalue(), "\x1b[1;5H ")


class TestWideCharRendering(unittest.TestCase):
    def setUp(self) -> None:
        self.stream, _, self.screen, self.renderer = make(20, 3)
        self.screen.text(1, 1, "中A")
        self.renderer.present()
        self.stream.take()

    def test_wide_char_replacement_writes_one_char(self) -> None:
        self.screen.set(1, 1, "文")
        stats = self.renderer.present()
        self.assertEqual(stats.cells, 1)
        self.assertEqual(self.stream.getvalue(), "\x1b[1;1H文")

    def test_continuation_cell_never_written(self) -> None:
        self.screen.set(1, 1, "文")
        self.renderer.present()
        # 右半格（第 2 列）不能被单独写入任何字符
        self.assertNotIn("\x1b[1;2H", self.stream.getvalue())

    def test_span_expands_left_to_cover_wide_char(self) -> None:
        """只改右半格时，输出必须从宽字符的左半格开始，否则会写半个字。"""
        self.screen.set(2, 1, "X")
        self.renderer.present()
        self.assertEqual(self.stream.getvalue(), "\x1b[1;1H X")

    def test_wide_char_row_roundtrip(self) -> None:
        self.screen.text(1, 2, "你好世界")
        self.renderer.present()
        output = self.stream.getvalue()
        self.assertIn("你好世界", output)


class TestInvalidateAndResize(unittest.TestCase):
    def test_invalidate_forces_full_redraw(self) -> None:
        stream, _, screen, renderer = make()
        screen.text(1, 1, "A")
        renderer.present()
        stream.take()
        renderer.invalidate()
        stats = renderer.present()
        self.assertTrue(stats.full)
        self.assertIn(SEQ_CLEAR, stream.getvalue())

    def test_clear_triggers_full_redraw(self) -> None:
        stream, _, screen, renderer = make()
        screen.text(1, 1, "AAA")
        renderer.present()
        screen.clear()
        stream.take()
        stats = renderer.present()
        self.assertTrue(stats.full)
        self.assertIn(SEQ_CLEAR, stream.getvalue())
        self.assertNotIn("AAA", stream.getvalue())

    def test_force_flag(self) -> None:
        stream, _, screen, renderer = make()
        screen.text(1, 1, "A")
        renderer.present()
        stream.take()
        stats = renderer.present(force=True)
        self.assertTrue(stats.full)
        self.assertIn(SEQ_HOME, stream.getvalue())

    def test_resize_marks_full_redraw(self) -> None:
        _, _, screen, renderer = make()
        screen.text(1, 1, "A")
        renderer.present()
        screen.resize(12, 5)
        self.assertTrue(screen.needs_full_redraw)
        stats = renderer.present()
        self.assertTrue(stats.full)


class TestWriteFailure(unittest.TestCase):
    def test_failure_does_not_commit_front(self) -> None:
        stream, _, screen, renderer = make()
        screen.text(1, 1, "A")
        renderer.present()
        screen.set(1, 1, "B")
        stream.fail_next()
        with self.assertRaises(OSError):
            renderer.present()
        # front 必须被作废，而不是"假装已经画上去了"
        self.assertTrue(screen.needs_full_redraw)

    def test_next_present_recovers_with_full_redraw(self) -> None:
        stream, _, screen, renderer = make()
        screen.text(1, 1, "A")
        renderer.present()
        screen.set(1, 1, "B")
        stream.fail_next()
        with self.assertRaises(OSError):
            renderer.present()
        stream.fail_next(False)
        stats = renderer.present()
        self.assertTrue(stats.full)
        self.assertIn("B", stream.getvalue())


class TestPlainMode(unittest.TestCase):
    def test_non_tty_output_has_no_escape_sequences(self) -> None:
        stream = FakeStream(tty=False)
        terminal = Terminal(stream, force_ansi=False)
        screen = Screen(8, 2)
        renderer = Renderer(terminal, screen)
        screen.text(1, 1, "Hi")
        renderer.present()
        output = stream.getvalue()
        self.assertNotIn("\x1b", output)
        self.assertIn("Hi", output)

    def test_plain_mode_skips_unchanged_frames(self) -> None:
        stream = FakeStream(tty=False)
        terminal = Terminal(stream, force_ansi=False)
        screen = Screen(8, 2)
        renderer = Renderer(terminal, screen)
        screen.text(1, 1, "Hi")
        renderer.present()
        stream.take()
        renderer.present()
        self.assertEqual(stream.getvalue(), "")

    def test_no_color_escape_ever_emitted(self) -> None:
        """Iteration A 不输出任何 SGR 颜色序列（Q2 的最低保证）。"""
        stream, _, screen, renderer = make()
        screen.text(1, 1, "Hello")
        renderer.present()
        screen.set(1, 1, "J")
        renderer.present()
        output = stream.getvalue()
        for prefix in ("\x1b[38", "\x1b[48", "\x1b[0;3", "\x1b[1m"):
            self.assertNotIn(prefix, output)


class TestRendererStats(unittest.TestCase):
    def test_stats_are_recorded(self) -> None:
        _, _, screen, renderer = make()
        screen.text(1, 1, "abc")
        stats = renderer.present()
        self.assertIs(renderer.last_stats, stats)
        self.assertGreater(stats.bytes_written, 0)
        self.assertGreaterEqual(stats.elapsed, 0)

    def test_bytes_match_payload(self) -> None:
        stream, _, screen, renderer = make()
        screen.text(1, 1, "abc")
        stats = renderer.present()
        self.assertEqual(stats.bytes_written, len(stream.getvalue().encode("utf-8")))


if __name__ == "__main__":
    unittest.main()
