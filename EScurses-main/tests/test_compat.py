"""旧版 API 兼容性测试。

这些用例复刻 README 里的旧用法，确保老项目不改一行代码也能跑。
"""

from __future__ import annotations

import time
import unittest

import escurses
from escurses.exceptions import GameStateError, OutOfBoundsError

from .helpers import FakeBackend, FakeStream


class CompatCase(unittest.TestCase):
    def setUp(self) -> None:
        escurses._session = None
        escurses._legacy_clock = None
        escurses.reset_config()
        self.stream = FakeStream(tty=True)
        self.backend = FakeBackend()

    def tearDown(self) -> None:
        if escurses._session is not None:
            escurses._session.close()
        escurses._session = None
        escurses._legacy_clock = None
        escurses.reset_config()

    def init(self, **kwargs):
        kwargs.setdefault("stream", self.stream)
        kwargs.setdefault("force_ansi", True)
        kwargs.setdefault("backend", self.backend)
        kwargs.setdefault("width", 20)
        kwargs.setdefault("height", 5)
        return escurses.initGame(**kwargs)


class TestLegacyFlow(CompatCase):
    def test_readme_style_flow(self) -> None:
        """README 里的完整旧写法必须原样可用。"""
        game = self.init()
        escurses.cursorEcho(False)
        escurses.frameStart()
        escurses.clear()
        escurses.drawChar(5, 3, "P")
        escurses.refresh()
        escurses.sleepForFrame(240)
        self.assertEqual(game.screen.get(5, 3).char, "P")
        escurses.exitGame(exit_process=False)

    def test_init_returns_game(self) -> None:
        game = self.init()
        self.assertIsInstance(game, escurses.Game)

    def test_init_is_idempotent(self) -> None:
        first = self.init()
        second = self.init()
        self.assertIs(first, second)

    def test_draw_and_refresh(self) -> None:
        game = self.init()
        escurses.drawChar(1, 1, "A")
        self.stream.take()
        escurses.refresh()
        self.assertIn("A", self.stream.getvalue())
        self.assertEqual(game.screen.row_text(1), "A")

    def test_clear(self) -> None:
        game = self.init()
        escurses.drawChar(1, 1, "A")
        escurses.clear()
        self.assertEqual(game.screen.lines(), [""] * 5)

    def test_exit_without_process_exit(self) -> None:
        self.init()
        escurses.exitGame(exit_process=False)
        self.assertIsNone(escurses._session)
        self.assertIn("\x1b[?1049l", self.stream.getvalue())

    def test_exit_raises_system_exit_by_default(self) -> None:
        """旧版 exitGame() 会结束进程，这个语义保留。"""
        self.init()
        with self.assertRaises(SystemExit):
            escurses.exitGame()

    def test_exit_without_init_is_safe(self) -> None:
        escurses.exitGame(exit_process=False)  # 不应抛异常

    def test_draw_before_init_raises_clear_error(self) -> None:
        with self.assertRaises(GameStateError):
            escurses.drawChar(1, 1, "A")


class TestLegacyInput(CompatCase):
    def test_getch_returns_char(self) -> None:
        self.init()
        self.backend.feed("q")
        self.assertEqual(escurses.getch(), "q")

    def test_getch_nonblocking_returns_none(self) -> None:
        self.init()
        self.assertIsNone(escurses.getch())

    def test_getch_arrow_returns_raw_sequence(self) -> None:
        self.init()
        self.backend.feed("\x1b[A")
        self.assertEqual(escurses.getch(), "\x1b[A")


class TestLegacyUtilities(CompatCase):
    def test_get_terminal_size(self) -> None:
        size = escurses.getTerminalSize()
        self.assertEqual(len(size), 2)
        self.assertGreater(size[0], 0)

    def test_linux_terminal_check_returns_bool(self) -> None:
        self.assertIsInstance(escurses.linuxTerminalCheck(), bool)

    def test_cursor_echo(self) -> None:
        self.init()
        self.stream.take()
        escurses.cursorEcho(True)
        self.assertIn("\x1b[?25h", self.stream.getvalue())

    def test_cursor_echo_without_session(self) -> None:
        # 没有会话时也不能崩
        escurses.cursorEcho(False)


class TestLegacyTiming(CompatCase):
    def test_sleep_for_frame_respects_fps(self) -> None:
        """审计 FPS-01 回归：旧实现 240fps 目标被钉死在 100fps（10ms/帧）。"""
        self.init()
        started = time.perf_counter()
        for _ in range(5):
            escurses.frameStart()
            escurses.sleepForFrame(240)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.045, "帧率上限仍是 100fps")

    def test_sleep_for_frame_rejects_invalid_fps(self) -> None:
        from escurses.exceptions import InvalidConfigError

        self.init()
        escurses.frameStart()
        with self.assertRaises(InvalidConfigError):
            escurses.sleepForFrame(0)

    def test_without_session_uses_fallback_clock(self) -> None:
        escurses.frameStart()
        escurses.sleepForFrame(1000)  # 不应崩


class TestConfiguration(CompatCase):
    def test_strict_via_configure(self) -> None:
        escurses.configure(strict=True)
        self.init()
        with self.assertRaises(OutOfBoundsError):
            escurses.drawChar(0, 0, "A")

    def test_default_is_clipping(self) -> None:
        self.init()
        self.assertFalse(escurses.drawChar(0, 0, "A"))

    def test_configure_applies_to_active_session(self) -> None:
        game = self.init()
        self.assertFalse(game.strict)
        escurses.configure(strict=True)
        self.assertTrue(game.strict)


class TestPublicSurface(CompatCase):
    def test_all_names_exist(self) -> None:
        for name in escurses.__all__:
            self.assertTrue(hasattr(escurses, name), name)

    def test_star_import(self) -> None:
        namespace = {}
        exec("from escurses import *", namespace)  # noqa: S102
        for name in ("Game", "Key", "drawChar", "initGame", "exitGame", "refresh"):
            self.assertIn(name, namespace)

    def test_snake_case_aliases(self) -> None:
        self.assertIs(escurses.init_game, escurses.initGame)
        self.assertIs(escurses.exit_game, escurses.exitGame)
        self.assertIs(escurses.draw_char, escurses.drawChar)
        self.assertIs(escurses.get_terminal_size, escurses.getTerminalSize)

    def test_version(self) -> None:
        self.assertRegex(escurses.__version__, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
