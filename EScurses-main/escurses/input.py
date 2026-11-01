"""跨平台键盘输入。

分三层，职责互不重叠：

1. **后端**（:mod:`escurses._win_input` / :mod:`escurses._posix_input`）：
   只负责"把当前可用的原始字符读出来"，不做任何语义解释。
2. **解析器**（本模块 ``_Parser`` 部分）：把原始字符流解析成 :class:`KeyEvent`。
   纯函数式，可脱离真实终端完整测试。
3. **接口**（:class:`Input`）：对外提供 ``poll()`` / ``read_key()``。

平台差异只在第 1 层，第 2、3 层完全一致 —— 这就是"统一输入接口"的落地方式。
"""

from __future__ import annotations

import os
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Deque, List, Optional, TextIO, Tuple

from .exceptions import InputError

__all__ = ["Key", "KeyEvent", "Input", "create_backend", "NullBackend"]


class Key(Enum):
    """按键枚举。"""

    CHAR = auto()
    """可打印字符（具体字符见 :attr:`KeyEvent.char`）。"""

    ENTER = auto()
    TAB = auto()
    BACKSPACE = auto()
    ESC = auto()
    SPACE = auto()
    CTRL_C = auto()

    UP = auto()
    DOWN = auto()
    LEFT = auto()
    RIGHT = auto()

    HOME = auto()
    END = auto()
    PAGE_UP = auto()
    PAGE_DOWN = auto()
    INSERT = auto()
    DELETE = auto()

    F1 = auto()
    F2 = auto()
    F3 = auto()
    F4 = auto()
    F5 = auto()
    F6 = auto()
    F7 = auto()
    F8 = auto()
    F9 = auto()
    F10 = auto()
    F11 = auto()
    F12 = auto()

    UNKNOWN = auto()
    """无法识别的序列（例如鼠标上报、未映射的功能键）。"""


@dataclass(frozen=True)
class KeyEvent:
    """一次按键事件。"""

    key: Key
    char: Optional[str] = None
    """``Key.CHAR`` / ``Key.SPACE`` 等有可打印内容时的原始字符。"""

    raw: str = ""
    """原始字符序列，便于调试或自行处理未映射按键。"""

    @property
    def is_printable(self) -> bool:
        """是否是可以直接显示在屏幕上的字符。"""
        return self.key is Key.CHAR or self.key is Key.SPACE

    def __str__(self) -> str:  # pragma: no cover - 调试用
        if self.char:
            return "KeyEvent({}, {!r})".format(self.key.name, self.char)
        return "KeyEvent({})".format(self.key.name)


# ---------------------------------------------------------------- 序列映射

_CSI_FINAL_KEYS = {
    "A": Key.UP,
    "B": Key.DOWN,
    "C": Key.RIGHT,
    "D": Key.LEFT,
    "H": Key.HOME,
    "F": Key.END,
    "Z": Key.TAB,  # Shift+Tab
}

# SS3 序列（ESC O + 一个字符）：VT100 风格的 F1-F4 与方向键
_SS3_KEYS = dict(_CSI_FINAL_KEYS)
_SS3_KEYS.update({"P": Key.F1, "Q": Key.F2, "R": Key.F3, "S": Key.F4})

_CSI_TILDE_KEYS = {    1: Key.HOME,
    2: Key.INSERT,
    3: Key.DELETE,
    4: Key.END,
    5: Key.PAGE_UP,
    6: Key.PAGE_DOWN,
    7: Key.HOME,
    8: Key.END,
    11: Key.F1,
    12: Key.F2,
    13: Key.F3,
    14: Key.F4,
    15: Key.F5,
    17: Key.F6,
    18: Key.F7,
    19: Key.F8,
    20: Key.F9,
    21: Key.F10,
    23: Key.F11,
    24: Key.F12,
}

# Windows 扩展键扫描码（前缀 \x00 或 \xe0 之后的那个字符）
_WIN_SCAN_KEYS = {
    "H": Key.UP,
    "P": Key.DOWN,
    "K": Key.LEFT,
    "M": Key.RIGHT,
    "G": Key.HOME,
    "O": Key.END,
    "I": Key.PAGE_UP,
    "Q": Key.PAGE_DOWN,
    "R": Key.INSERT,
    "S": Key.DELETE,
}

_WIN_SCAN_FKEYS = {
    ";": Key.F1,
    "<": Key.F2,
    "=": Key.F3,
    ">": Key.F4,
    "?": Key.F5,
    "@": Key.F6,
    "A": Key.F7,
    "B": Key.F8,
    "C": Key.F9,
    "D": Key.F10,
    "\x85": Key.F11,
    "\x86": Key.F12,
    "\x8b": Key.F11,
    "\x8c": Key.F12,
}

_MAX_SEQUENCE = 32
"""单条转义序列的最大长度，超过视为垃圾数据，避免缓冲区无限增长。"""


# ---------------------------------------------------------------- 后端


class NullBackend:
    """没有可用输入时的空后端：永远读不到东西，但不会崩、不会忙等。"""

    name = "null"
    supports_key_up = False

    def available(self) -> bool:
        return False

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def read_available(self) -> str:
        return ""

    def wait(self, timeout: Optional[float]) -> bool:
        if timeout:
            time.sleep(min(timeout, 0.05))
        return False


def create_backend(stream: Optional[TextIO] = None) -> Any:
    """按平台创建输入后端。

    平台模块是**惰性导入**的：Windows 上不会加载 ``termios``，
    POSIX 上不会加载 ``msvcrt``。任何异常都退化为 :class:`NullBackend`，
    保证"输入不可用"不会阻止游戏启动。
    """
    try:
        if os.name == "nt":
            from ._win_input import WindowsBackend

            backend = WindowsBackend(stream)
        elif os.name == "posix":
            from ._posix_input import PosixBackend

            backend = PosixBackend(stream)
        else:  # pragma: no cover - 其他平台
            return NullBackend()
    except Exception:
        return NullBackend()
    return backend if backend.available() else NullBackend()


# ---------------------------------------------------------------- 解析器


def _char_event(ch: str) -> KeyEvent:
    if ch in ("\r", "\n"):
        return KeyEvent(Key.ENTER, "\r" if ch == "\r" else "\n", ch)
    if ch == "\t":
        return KeyEvent(Key.TAB, "\t", ch)
    if ch in ("\x7f", "\x08"):
        return KeyEvent(Key.BACKSPACE, "\x7f", ch)
    if ch == "\x03":
        return KeyEvent(Key.CTRL_C, "\x03", ch)
    if ch == " ":
        return KeyEvent(Key.SPACE, " ", ch)
    if unicodedata.category(ch) == "Cc":
        return KeyEvent(Key.UNKNOWN, None, ch)
    return KeyEvent(Key.CHAR, ch, ch)


class Input:
    """统一的键盘输入接口。

    Args:
        backend: 自定义后端（测试注入假后端）；``None`` 表示按平台自动创建。
        esc_timeout: 单独 ``ESC`` 的判定窗口（秒）。方向键序列以 ``ESC`` 开头，
            必须等待后续字节才能区分"按下 Esc"和"方向键"。
    """

    def __init__(self, backend: Any = None, *, esc_timeout: float = 0.03) -> None:
        self.backend = backend if backend is not None else create_backend()
        self.esc_timeout = max(0.0, float(esc_timeout))
        self._buffer = ""
        self._events: Deque[KeyEvent] = deque()
        self._esc_since: Optional[float] = None
        self._opened = False

    # ------------------------------------------------------------------ 生命周期

    @property
    def supports_key_up(self) -> bool:
        """底层是否能可靠检测"按键抬起"。Iteration A 恒为 ``False``。"""
        return bool(getattr(self.backend, "supports_key_up", False))

    @property
    def is_interactive(self) -> bool:
        """输入是否来自真实终端。"""
        try:
            return bool(self.backend.available())
        except Exception:  # pragma: no cover
            return False

    def open(self) -> None:
        """进入字符模式，幂等。

        Raises:
            InputError: 后端无法打开。
        """
        if self._opened:
            return
        try:
            self.backend.open()
        except InputError:
            raise
        except Exception as exc:
            raise InputError("输入初始化失败：{}".format(exc)) from exc
        self._opened = True

    def close(self) -> None:
        """退出字符模式并丢弃未处理的输入，幂等。"""
        if not self._opened:
            return
        try:
            self.backend.close()
        except Exception:  # pragma: no cover - 关闭失败不应影响退出
            pass
        self._opened = False
        self._buffer = ""
        self._events.clear()
        self._esc_since = None

    def __enter__(self) -> "Input":
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.close()
        return False

    # ------------------------------------------------------------------ 读

    def poll(self) -> List[KeyEvent]:
        """取出当前所有待处理事件（非阻塞，不会丢弃已解析事件）。"""
        self._pump()
        events = list(self._events)
        self._events.clear()
        return events

    def read_key(self, timeout: Optional[float] = None) -> Optional[KeyEvent]:
        """读取一个按键。

        Args:
            timeout: ``None`` 表示一直等到有按键；``0`` 表示纯非阻塞。

        Returns:
            按键事件，超时返回 ``None``。
        """
        if not self._opened:
            self.open()

        if timeout is None and not self.is_interactive:
            # 没有输入设备时的"永久阻塞"等于挂死，必须明确报错而不是卡住。
            raise InputError("没有可用的输入设备（stdin 不是终端），无法阻塞读取")

        if self._events:
            return self._events.popleft()

        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        while True:
            self._pump()
            if self._events:
                return self._events.popleft()
            remaining: Optional[float]
            if deadline is None:
                remaining = None
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
            if self._buffer:
                # 手里有半条序列（例如孤立的 ESC），最多只再等一个 esc_timeout，
                # 否则无限等待会因为没有后续字节而永远卡住。
                remaining = (
                    self.esc_timeout
                    if remaining is None
                    else min(remaining, self.esc_timeout)
                )
            self.backend.wait(remaining)

    def _pump(self) -> None:
        try:
            chunk = self.backend.read_available()
        except Exception:  # pragma: no cover - 后端异常不应中断主循环
            chunk = ""
        if chunk:
            self._buffer += chunk
            self._esc_since = None
        self._parse()

    # ------------------------------------------------------------------ 解析

    def _parse(self) -> None:
        while self._buffer:
            first = self._buffer[0]
            if first == "\x1b":
                consumed, event = self._consume_escape()
            elif first in ("\x00", "\xe0"):
                consumed, event = self._consume_windows_prefix()
            else:
                consumed, event = 1, _char_event(first)
            if consumed == 0:
                return  # 序列不完整，等下一批数据
            self._buffer = self._buffer[consumed:]
            if event is not None:
                self._events.append(event)

    def _consume_escape(self) -> Tuple[int, Optional[KeyEvent]]:
        buf = self._buffer
        if len(buf) == 1:
            now = time.monotonic()
            if self._esc_since is None:
                self._esc_since = now
                return 0, None
            if now - self._esc_since < self.esc_timeout:
                return 0, None
            self._esc_since = None
            return 1, KeyEvent(Key.ESC, "\x1b", "\x1b")

        second = buf[1]
        if second == "[":
            index = 2
            while index < len(buf):
                if 0x40 <= ord(buf[index]) <= 0x7E:
                    break
                index += 1
            else:
                if len(buf) > _MAX_SEQUENCE:
                    self._esc_since = None
                    return 1, KeyEvent(Key.UNKNOWN, None, buf[0])
                return 0, None
            sequence = buf[: index + 1]
            params = buf[2:index]
            final = buf[index]
            self._esc_since = None
            return index + 1, self._csi_event(params, final, sequence)

        if second == "O":
            if len(buf) < 3:
                return 0, None
            final = buf[2]
            sequence = buf[:3]
            self._esc_since = None
            key = _SS3_KEYS.get(final, Key.UNKNOWN)
            return 3, KeyEvent(key, None, sequence)

        # ESC + 普通字符（Alt+字母 或误按）：只消费 ESC，后续字符照常解析。
        self._esc_since = None
        return 1, KeyEvent(Key.ESC, "\x1b", "\x1b")

    def _csi_event(self, params: str, final: str, sequence: str) -> KeyEvent:
        if final in ("M", "m"):
            # SGR 鼠标上报：Iteration A 不处理，原样交给上层。
            return KeyEvent(Key.UNKNOWN, None, sequence)
        if final == "~":
            head = params.split(";")[0]
            try:
                number = int(head)
            except ValueError:
                return KeyEvent(Key.UNKNOWN, None, sequence)
            key = _CSI_TILDE_KEYS.get(number)
            return KeyEvent(key or Key.UNKNOWN, None, sequence)
        key = _CSI_FINAL_KEYS.get(final)
        return KeyEvent(key or Key.UNKNOWN, None, sequence)

    def _consume_windows_prefix(self) -> Tuple[int, Optional[KeyEvent]]:
        if len(self._buffer) < 2:
            return 0, None
        scan = self._buffer[1]
        sequence = self._buffer[:2]
        key = _WIN_SCAN_KEYS.get(scan) or _WIN_SCAN_FKEYS.get(scan) or Key.UNKNOWN
        return 2, KeyEvent(key, None, sequence)

    # ------------------------------------------------------------------ 兼容

    def getch(self, nodelay: bool = True) -> Optional[str]:
        """旧版 ``getch`` 语义：返回原始字符串而不是事件对象。

        - 可打印键返回字符本身（``"q"``）。
        - 方向键等功能键返回**原始序列**（POSIX 为 ``"\\x1b[A"``，
          Windows 为 ``"\\xe0H"``），旧代码不需要处理枚举。
        - 无按键时返回 ``None``。
        """
        event = self.read_key(0.0 if nodelay else None)
        if event is None:
            return None
        if event.key is Key.CHAR:
            return event.char
        if event.key is Key.SPACE:
            return " "
        if event.key is Key.ENTER:
            return "\r"
        if event.key is Key.TAB:
            return "\t"
        if event.key is Key.BACKSPACE:
            return "\x7f"
        if event.key is Key.ESC:
            return "\x1b"
        if event.key is Key.CTRL_C:
            return "\x03"
        return event.raw or None
