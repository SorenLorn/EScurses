"""Game 生命周期与主循环测试。"""

from __future__ import annotations

import unittest

from escurses.exceptions import GameStateError, OutOfBoundsError
from escurses.game import Game
from escurses.input import Key
from escurses.terminal import (
    SEQ_ALT_SCREEN_OFF,
    SEQ_ALT_SCREEN_ON,
    SEQ_CURSOR_SHOW,
    Terminal,
)

from .helpers import FakeBackend, FakeStream


def make_game(width: int = 10, height: int = 3, **kwargs):
    stream = FakeStream(tty=True)
    backend = kwargs.pop("backend", FakeBackend())
    game = Game(
        width=width,
        height=height,
        stream=stream,
        force_ansi=True,
        backend=backend,
        **kwargs
    )
    return game, stream, backend


class TestLifecycle(unittest.TestCase):
    def test_start_marks_running(self) -> None:
        game, _, _ = make_game()
        self.assertFalse(game.is_running)
        game.start()
        self.assertTrue(game.is_running)
        game.close()
        self.assertFalse(game.is_running)

    def test_start_is_idempotent(self) -> None:
        game, stream, _ = make_game()
        game.start()
        stream.take()
        game.start()
        self.assertEqual(stream.getvalue(), "")
        game.close()

    def test_close_is_idempotent(self) -> None:
        game, stream, _ = make_game()
        game.start()
        game.close()
        stream.take()
        game.close()
        self.assertEqual(stream.getvalue(), "")

    def test_context_manager(self) -> None:
        stream = FakeStream(tty=True)
        with Game(width=8, height=2, stream=stream, force_ansi=True, backend=FakeBackend()) as game:
            self.assertTrue(game.is_running)
        self.assertFalse(game.is_running)
        output = stream.getvalue()
        self.assertIn(SEQ_ALT_SCREEN_OFF, output)

    def test_exception_still_restores_terminal(self) -> None:
        stream = FakeStream(tty=True)
        with self.assertRaises(RuntimeError):
            with Game(width=8, height=2, stream=stream, force_ansi=True, backend=FakeBackend()) as game:
                game.draw_text(1, 1, "hi")
                game.present()
                raise RuntimeError("boom")
        output = stream.getvalue()
        self.assertIn(SEQ_CURSOR_SHOW, output)
        self.assertIn(SEQ_ALT_SCREEN_OFF, output)

    def test_keyboard_interrupt_restores_terminal(self) -> None:
        """Ctrl+C 抛的是 BaseException，__exit__ 同样必须执行。"""
        stream = FakeStream(tty=True)
        with self.assertRaises(KeyboardInterrupt):
            with Game(
                width=8, height=2, stream=stream, force_ansi=True, backend=FakeBackend()
            ) as game:
                game.draw_text(1, 1, "x")
                game.present()
                raise KeyboardInterrupt
        output = stream.getvalue()
        self.assertIn(SEQ_CURSOR_SHOW, output)
        self.assertIn(SEQ_ALT_SCREEN_OFF, output)
        self.assertIn("\x1b[?7h", output)

    def test_cursor_shown_before_leaving_alt_screen(self) -> None:
        stream = FakeStream(tty=True)
        with Game(width=8, height=2, stream=stream, force_ansi=True, backend=FakeBackend()):
            pass
        output = stream.getvalue()
        self.assertLess(output.rindex(SEQ_CURSOR_SHOW), output.rindex(SEQ_ALT_SCREEN_OFF))

    def test_input_is_closed_on_exit(self) -> None:
        backend = FakeBackend()
        game, _, _ = make_game(backend=backend)
        game.start()
        self.assertTrue(backend.opened)
        game.close()
        self.assertFalse(backend.opened)
        self.assertEqual(backend.close_count, 1)


class TestAltScreenOption(unittest.TestCase):
    def test_default_enables_alt_screen(self) -> None:
        game, stream, _ = make_game()
        game.start()
        self.assertIn(SEQ_ALT_SCREEN_ON, stream.getvalue())
        game.close()

    def test_can_be_disabled(self) -> None:
        game, stream, _ = make_game(alt_screen=False)
        game.start()
        self.assertNotIn(SEQ_ALT_SCREEN_ON, stream.getvalue())
        game.close()
        self.assertNotIn(SEQ_ALT_SCREEN_OFF, stream.getvalue())


class TestGuards(unittest.TestCase):
    def test_present_before_start_raises(self) -> None:
        game, _, _ = make_game()
        with self.assertRaises(GameStateError):
            game.present()

    def test_read_key_before_start_raises(self) -> None:
        game, _, _ = make_game()
        with self.assertRaises(GameStateError):
            game.read_key(0)

    def test_drawing_before_start_is_allowed(self) -> None:
        game, _, _ = make_game()
        game.draw_text(1, 1, "pre")
        self.assertEqual(game.screen.row_text(1), "pre")


class TestDrawing(unittest.TestCase):
    def setUp(self) -> None:
        self.game, self.stream, _ = make_game(10, 4)
        self.game.start()

    def tearDown(self) -> None:
        self.game.close()

    def test_draw_char(self) -> None:
        self.assertTrue(self.game.draw_char(1, 1, "A"))
        self.assertEqual(self.game.screen.row_text(1), "A")

    def test_draw_char_out_of_bounds_is_clipped(self) -> None:
        self.assertFalse(self.game.draw_char(0, 0, "A"))
        self.assertFalse(self.game.draw_char(11, 1, "A"))
        self.assertEqual(self.game.screen.lines(), ["", "", "", ""])

    def test_strict_mode_raises(self) -> None:
        self.game.strict = True
        with self.assertRaises(OutOfBoundsError):
            self.game.draw_char(0, 0, "A")

    def test_draw_text_chinese(self) -> None:
        self.game.draw_text(1, 2, "中文")
        self.assertEqual(self.game.screen.row_text(2), "中文")

    def test_fill(self) -> None:
        self.game.fill(1, 1, 3, 1, "x")
        self.assertEqual(self.game.screen.row_text(1), "xxx")

    def test_present_and_idle(self) -> None:
        self.game.draw_text(1, 1, "abc")
        self.game.present()
        self.stream.take()
        stats = self.game.present()
        self.assertEqual(stats.bytes_written, 0)

    def test_refresh_alias(self) -> None:
        self.game.draw_text(1, 1, "a")
        stats = self.game.refresh()
        self.assertGreater(stats.bytes_written, 0)


class TestInputIntegration(unittest.TestCase):
    def test_poll_keys(self) -> None:
        backend = FakeBackend(["ab"])
        game, _, _ = make_game(backend=backend)
        game.start()
        keys = [e.char for e in game.poll_keys()]
        self.assertEqual(keys, ["a", "b"])
        game.close()

    def test_wait_for_key(self) -> None:
        backend = FakeBackend(["\x1b[A"])
        game, _, _ = make_game(backend=backend)
        game.start()
        event = game.wait_for_key("press")
        self.assertIsNotNone(event)
        self.assertIs(event.key, Key.UP)
        game.close()

    def test_getch_returns_string(self) -> None:
        backend = FakeBackend(["q"])
        game, _, _ = make_game(backend=backend)
        game.start()
        self.assertEqual(game.getch(), "q")
        game.close()


class TestMainLoop(unittest.TestCase):
    def test_run_max_frames(self) -> None:
        game, _, _ = make_game()
        frames = game.run(lambda g: None, fps=200, max_frames=3)
        self.assertEqual(frames, 3)
        self.assertEqual(game.frame_stats.frames, 3)
        game.close()

    def test_quit_stops_loop(self) -> None:
        game, _, _ = make_game()
        counter = {"n": 0}

        def step(g: Game) -> None:
            counter["n"] += 1
            if counter["n"] == 2:
                g.quit()

        game.run(step, fps=200, max_frames=10)
        self.assertEqual(counter["n"], 2)
        game.close()

    def test_run_draws_and_presents(self) -> None:
        game, stream, _ = make_game()
        game.run(lambda g: g.draw_text(1, 1, "x"), fps=200, max_frames=1)
        self.assertIn("x", stream.getvalue())
        game.close()


class SizedTerminal(Terminal):
    """可手动改变尺寸的终端，用于测试 resize。"""

    def __init__(self, stream, size):
        super().__init__(stream, force_ansi=True)
        self._size = size

    def size(self):
        return self._size


class TestResize(unittest.TestCase):
    def test_size_change_rebuilds_screen(self) -> None:
        stream = FakeStream(tty=True)
        game = Game(stream=stream, backend=FakeBackend())
        game.terminal = SizedTerminal(stream, (10, 3))
        game.start()
        self.assertEqual(game.size, (10, 3))

        events = []
        game.on_resize = lambda w, h: events.append((w, h))
        game.terminal._size = (14, 5)
        game.draw_text(1, 1, "a")
        stats = game.present()

        self.assertEqual(game.size, (14, 5))
        self.assertEqual(events, [(14, 5)])
        self.assertTrue(stats.full)
        game.close()

    def test_same_size_does_not_invalidate(self) -> None:
        stream = FakeStream(tty=True)
        game = Game(stream=stream, backend=FakeBackend())
        game.terminal = SizedTerminal(stream, (10, 3))
        game.start()
        game.draw_text(1, 1, "a")
        game.present()
        stream.take()
        stats = game.present()
        self.assertFalse(stats.full)
        self.assertEqual(stats.bytes_written, 0)
        game.close()


class TestProperties(unittest.TestCase):
    def test_fps_and_dt(self) -> None:
        game, _, _ = make_game(fps=50)
        self.assertEqual(game.fps, 50)
        game.fps = 30
        self.assertEqual(game.fps, 30)
        self.assertEqual(game.dt, 0)

    def test_color_support_reported(self) -> None:
        game, _, _ = make_game()
        self.assertGreaterEqual(int(game.color_support), 0)

    def test_size(self) -> None:
        game, _, _ = make_game(12, 6)
        self.assertEqual(game.size, (12, 6))


if __name__ == "__main__":
    unittest.main()
