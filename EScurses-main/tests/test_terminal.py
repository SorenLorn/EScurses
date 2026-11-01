"""终端生命周期与能力探测测试。"""

from __future__ import annotations

import unittest

from escurses.exceptions import TerminalError
from escurses.terminal import (
    SEQ_ALT_SCREEN_OFF,
    SEQ_ALT_SCREEN_ON,
    SEQ_CURSOR_HIDE,
    SEQ_CURSOR_SHOW,
    SEQ_RESET_SGR,
    SEQ_WRAP_OFF,
    SEQ_WRAP_ON,
    ColorSupport,
    Terminal,
    detect_color_support,
)

from .helpers import ExplodingStream, FakeStream


class TestSetupAndRestore(unittest.TestCase):
    def test_setup_enters_alt_screen_and_hides_cursor(self) -> None:
        stream = FakeStream(tty=True)
        terminal = Terminal(stream, force_ansi=True)
        terminal.setup()
        output = stream.getvalue()
        self.assertIn(SEQ_ALT_SCREEN_ON, output)
        self.assertIn(SEQ_CURSOR_HIDE, output)
        self.assertIn(SEQ_WRAP_OFF, output)

    def test_restore_undoes_everything(self) -> None:
        stream = FakeStream(tty=True)
        terminal = Terminal(stream, force_ansi=True)
        terminal.setup()
        stream.take()
        terminal.restore()
        output = stream.getvalue()
        self.assertIn(SEQ_CURSOR_SHOW, output)
        self.assertIn(SEQ_WRAP_ON, output)
        self.assertIn(SEQ_ALT_SCREEN_OFF, output)
        self.assertIn(SEQ_RESET_SGR, output)

    def test_restore_is_idempotent(self) -> None:
        stream = FakeStream(tty=True)
        terminal = Terminal(stream, force_ansi=True)
        terminal.setup()
        terminal.restore()
        stream.take()
        terminal.restore()
        self.assertEqual(stream.getvalue(), "")

    def test_setup_is_idempotent(self) -> None:
        stream = FakeStream(tty=True)
        terminal = Terminal(stream, force_ansi=True)
        terminal.setup()
        stream.take()
        terminal.setup()
        self.assertEqual(stream.getvalue(), "")

    def test_alt_screen_can_be_disabled(self) -> None:
        stream = FakeStream(tty=True)
        terminal = Terminal(stream, force_ansi=True)
        terminal.setup(alt_screen=False)
        output = stream.getvalue()
        self.assertNotIn(SEQ_ALT_SCREEN_ON, output)
        stream.take()
        terminal.restore()
        self.assertNotIn(SEQ_ALT_SCREEN_OFF, stream.getvalue())

    def test_hide_cursor_can_be_disabled(self) -> None:
        stream = FakeStream(tty=True)
        terminal = Terminal(stream, force_ansi=True)
        terminal.setup(hide_cursor=False)
        self.assertNotIn(SEQ_CURSOR_HIDE, stream.getvalue())

    def test_restore_after_failed_setup(self) -> None:
        """初始化中途失败：必须抛 TerminalError，且不留下"半激活"状态。"""
        stream = ExplodingStream()
        terminal = Terminal(stream, force_ansi=True)
        with self.assertRaises(TerminalError):
            terminal.setup()
        self.assertFalse(terminal._active)
        # 再次 restore 不应有任何输出（已经回滚过）
        stream.write_count = 0
        terminal.restore()
        self.assertEqual(stream.getvalue(), "")


class TestNonTTY(unittest.TestCase):
    def test_no_escape_sequences_when_not_a_tty(self) -> None:
        stream = FakeStream(tty=False)
        terminal = Terminal(stream)
        self.assertFalse(terminal.ansi)
        self.assertFalse(terminal.is_tty)
        terminal.setup()
        self.assertEqual(stream.getvalue(), "")

    def test_restore_on_non_tty_is_silent(self) -> None:
        stream = FakeStream(tty=False)
        terminal = Terminal(stream)
        terminal.setup()
        terminal.restore()
        self.assertEqual(stream.getvalue(), "")

    def test_force_ansi_overrides_detection(self) -> None:
        terminal = Terminal(FakeStream(tty=False), force_ansi=True)
        self.assertTrue(terminal.ansi)


class TestSize(unittest.TestCase):
    def test_size_never_raises(self) -> None:
        terminal = Terminal(FakeStream(tty=True), force_ansi=True)
        width, height = terminal.size()
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)

    def test_size_has_fallback(self) -> None:
        class BrokenStream(FakeStream):
            def fileno(self) -> int:
                raise ValueError("closed")

        terminal = Terminal(BrokenStream(tty=True), force_ansi=True)
        self.assertEqual(len(terminal.size()), 2)


class TestCursorControl(unittest.TestCase):
    def test_hide_show_are_idempotent(self) -> None:
        stream = FakeStream(tty=True)
        terminal = Terminal(stream, force_ansi=True)
        terminal.hide_cursor()
        terminal.hide_cursor()
        terminal.show_cursor()
        terminal.show_cursor()
        self.assertEqual(stream.getvalue(), SEQ_CURSOR_HIDE + SEQ_CURSOR_SHOW)

    def test_clear_writes_clear_sequence(self) -> None:
        stream = FakeStream(tty=True)
        terminal = Terminal(stream, force_ansi=True)
        terminal.clear()
        self.assertIn("\x1b[2J", stream.getvalue())


class TestColorDetection(unittest.TestCase):
    def test_no_color_env_disables(self) -> None:
        terminal = Terminal(FakeStream(tty=True), force_ansi=True)
        self.assertEqual(
            detect_color_support(terminal._stream, {"NO_COLOR": "1"}),
            ColorSupport.NONE,
        )

    def test_clicolor_zero_disables(self) -> None:
        stream = FakeStream(tty=True)
        self.assertEqual(
            detect_color_support(stream, {"CLICOLOR": "0"}), ColorSupport.NONE
        )

    def test_term_dumb_disables(self) -> None:
        stream = FakeStream(tty=True)
        self.assertEqual(
            detect_color_support(stream, {"TERM": "dumb"}), ColorSupport.NONE
        )

    def test_non_tty_disables(self) -> None:
        stream = FakeStream(tty=False)
        self.assertEqual(
            detect_color_support(stream, {"TERM": "xterm-256color"}), ColorSupport.NONE
        )

    def test_truecolor(self) -> None:
        stream = FakeStream(tty=True)
        self.assertEqual(
            detect_color_support(stream, {"TERM": "xterm", "COLORTERM": "truecolor"}),
            ColorSupport.TRUECOLOR,
        )

    def test_256_colors(self) -> None:
        stream = FakeStream(tty=True)
        self.assertEqual(
            detect_color_support(stream, {"TERM": "xterm-256color"}),
            ColorSupport.XTERM256,
        )

    def test_basic_colors(self) -> None:
        stream = FakeStream(tty=True)
        self.assertEqual(
            detect_color_support(stream, {"TERM": "xterm"}), ColorSupport.BASIC
        )


if __name__ == "__main__":
    unittest.main()
