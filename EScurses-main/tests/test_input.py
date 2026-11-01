"""输入系统测试。

全部使用假后端，不依赖真实键盘；解析器是纯逻辑，因此可以在任何平台验证
Windows 与 POSIX 两种序列。
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from escurses.input import Input, Key, create_backend

from .helpers import FakeBackend

ROOT = Path(__file__).resolve().parent.parent


def make_input(chunks=None, esc_timeout: float = 0.0) -> Input:
    return Input(FakeBackend(chunks), esc_timeout=esc_timeout)


class TestNoInput(unittest.TestCase):
    def test_poll_without_data(self) -> None:
        source = make_input()
        source.open()
        self.assertEqual(source.poll(), [])

    def test_nonblocking_read_returns_none(self) -> None:
        source = make_input()
        source.open()
        self.assertIsNone(source.read_key(0))

    def test_getch_without_data(self) -> None:
        source = make_input()
        source.open()
        self.assertIsNone(source.getch())


class TestPrintableKeys(unittest.TestCase):
    def test_single_char(self) -> None:
        source = make_input(["q"])
        source.open()
        events = source.poll()
        self.assertEqual(len(events), 1)
        self.assertIs(events[0].key, Key.CHAR)
        self.assertEqual(events[0].char, "q")

    def test_multiple_chars_in_one_chunk(self) -> None:
        source = make_input(["abc"])
        source.open()
        self.assertEqual([e.char for e in source.poll()], ["a", "b", "c"])

    def test_chinese_character(self) -> None:
        source = make_input(["中"])
        source.open()
        self.assertEqual(source.poll()[0].char, "中")

    def test_space_tab_enter(self) -> None:
        source = make_input([" \t\r"])
        source.open()
        keys = [e.key for e in source.poll()]
        self.assertEqual(keys, [Key.SPACE, Key.TAB, Key.ENTER])

    def test_backspace(self) -> None:
        source = make_input(["\x7f"])
        source.open()
        self.assertIs(source.poll()[0].key, Key.BACKSPACE)

    def test_ctrl_c(self) -> None:
        source = make_input(["\x03"])
        source.open()
        self.assertIs(source.poll()[0].key, Key.CTRL_C)


class TestArrowKeys(unittest.TestCase):
    def test_posix_arrows(self) -> None:
        source = make_input(["\x1b[A\x1b[B\x1b[C\x1b[D"])
        source.open()
        keys = [e.key for e in source.poll()]
        self.assertEqual(keys, [Key.UP, Key.DOWN, Key.RIGHT, Key.LEFT])

    def test_windows_arrows(self) -> None:
        """审计 INP-04：\xe0 前缀必须与扫描码合并成一个事件。"""
        source = make_input(["\xe0H\xe0P\xe0K\xe0M"])
        source.open()
        keys = [e.key for e in source.poll()]
        self.assertEqual(keys, [Key.UP, Key.DOWN, Key.LEFT, Key.RIGHT])
        self.assertEqual(len(keys), 4)  # 不能多出 4 个垃圾字符事件

    def test_windows_prefix_alone_is_buffered(self) -> None:
        source = make_input(["\xe0"])
        source.open()
        self.assertEqual(source.poll(), [])
        source.backend.feed("H")
        self.assertIs(source.poll()[0].key, Key.UP)

    def test_ss3_arrows(self) -> None:
        source = make_input(["\x1bOA\x1bOD"])
        source.open()
        self.assertEqual([e.key for e in source.poll()], [Key.UP, Key.LEFT])

    def test_home_end_pageup_pagedown(self) -> None:
        source = make_input(["\x1b[H\x1b[F\x1b[5~\x1b[6~"])
        source.open()
        keys = [e.key for e in source.poll()]
        self.assertEqual(keys, [Key.HOME, Key.END, Key.PAGE_UP, Key.PAGE_DOWN])

    def test_function_keys(self) -> None:
        source = make_input(["\x1bOP\x1b[15~\x1b[24~"])
        source.open()
        keys = [e.key for e in source.poll()]
        self.assertEqual(keys, [Key.F1, Key.F5, Key.F12])


class TestEscapeHandling(unittest.TestCase):
    def test_escape_resolves_after_timeout(self) -> None:
        source = make_input(["\x1b"], esc_timeout=0.0)
        source.open()
        self.assertEqual(source.poll(), [])  # 第一次：还不能确定是 Esc
        events = source.poll()  # 第二次：超时已到
        self.assertEqual(len(events), 1)
        self.assertIs(events[0].key, Key.ESC)

    def test_incomplete_csi_is_buffered_not_lost(self) -> None:
        source = make_input(["\x1b["])
        source.open()
        self.assertEqual(source.poll(), [])
        source.backend.feed("A")
        self.assertIs(source.poll()[0].key, Key.UP)

    def test_escape_then_letter(self) -> None:
        source = make_input(["\x1bx"])
        source.open()
        keys = [e.key for e in source.poll()]
        self.assertEqual(keys, [Key.ESC, Key.CHAR])

    def test_mouse_sequence_is_unknown_not_crash(self) -> None:
        source = make_input(["\x1b[<0;10;5M"])
        source.open()
        events = source.poll()
        self.assertEqual(len(events), 1)
        self.assertIs(events[0].key, Key.UNKNOWN)


class TestGetchCompat(unittest.TestCase):
    def test_plain_char(self) -> None:
        source = make_input(["q"])
        source.open()
        self.assertEqual(source.getch(), "q")

    def test_arrow_returns_raw_sequence(self) -> None:
        source = make_input(["\x1b[A"])
        source.open()
        self.assertEqual(source.getch(), "\x1b[A")

    def test_windows_arrow_returns_raw_sequence(self) -> None:
        source = make_input(["\xe0H"])
        source.open()
        self.assertEqual(source.getch(), "\xe0H")

    def test_read_key_does_not_drop_queued_events(self) -> None:
        source = make_input(["ab"])
        source.open()
        first = source.read_key(0)
        second = source.read_key(0)
        self.assertEqual((first.char, second.char), ("a", "b"))


class TestLifecycle(unittest.TestCase):
    def test_open_close_counts(self) -> None:
        backend = FakeBackend()
        source = Input(backend)
        source.open()
        source.open()
        self.assertEqual(backend.open_count, 1)
        source.close()
        source.close()
        self.assertEqual(backend.close_count, 1)

    def test_close_clears_pending_input(self) -> None:
        source = make_input(["abc"])
        source.open()
        source.poll()
        source.close()
        source.open()
        self.assertEqual(source.poll(), [])

    def test_context_manager(self) -> None:
        backend = FakeBackend()
        with Input(backend) as source:
            self.assertTrue(backend.opened)
        self.assertFalse(backend.opened)

    def test_blocking_read_without_device_raises(self) -> None:
        """没有输入设备时，阻塞读必须报错而不是永久挂起。"""
        from escurses.exceptions import InputError
        from escurses.input import Input as InputClass, NullBackend

        source = InputClass(NullBackend())
        source.open()
        with self.assertRaises(InputError):
            source.read_key(None)

    def test_nonblocking_read_without_device_returns_none(self) -> None:
        from escurses.input import Input as InputClass, NullBackend

        source = InputClass(NullBackend())
        source.open()
        self.assertIsNone(source.read_key(0))

    def test_supports_key_up_is_false(self) -> None:
        """Iteration A 不提供假的 KEY_UP。"""
        self.assertFalse(make_input().supports_key_up)


class TestPlatformIsolation(unittest.TestCase):
    """平台模块必须惰性导入：本平台不加载另一个平台的模块。"""

    def _run(self, code: str) -> str:
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_import_does_not_load_foreign_backend(self) -> None:
        foreign = (
            "escurses._posix_input" if sys.platform.startswith("win") else "escurses._win_input"
        )
        code = (
            "import sys, escurses;"
            "print('{}' in sys.modules, 'escurses._win_input' in sys.modules,"
            " 'escurses._posix_input' in sys.modules)".format(foreign)
        )
        self.assertEqual(self._run(code), "False False False")

    def test_create_backend_never_raises(self) -> None:
        code = (
            "import escurses;"
            "from escurses.input import create_backend;"
            "b = create_backend();"
            "print(type(b).__name__ != '')"
        )
        self.assertEqual(self._run(code), "True")


if __name__ == "__main__":
    unittest.main()
