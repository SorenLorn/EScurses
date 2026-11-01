"""终端输出侧的状态管理。

职责边界（刻意划清，避免职责重叠）：

- 本模块只管 **stdout 侧**：备用屏幕、光标、自动换行、Windows 控制台模式。
- **stdin 侧**的字符模式（termios）由 :mod:`escurses.input` 的后端负责，
  因为它和文件描述符绑定，和输出流无关。
- 两者都由 :class:`escurses.game.Game` 协调，``close()`` 里用 ``try/finally``
  保证一个失败不会导致另一个被跳过。

所有恢复操作都是**幂等**的：每一项修改都有一个标志位，还原后置 ``False``，
重复调用不会重复输出序列。
"""

from __future__ import annotations

import os
import shutil
import sys
from enum import IntEnum
from typing import Any, Optional, TextIO, Tuple

from .exceptions import TerminalError

__all__ = ["ColorSupport", "detect_color_support", "Terminal", "supports_ansi"]

# ANSI 序列
CSI = "\x1b["
SEQ_ALT_SCREEN_ON = CSI + "?1049h"
SEQ_ALT_SCREEN_OFF = CSI + "?1049l"
SEQ_CURSOR_HIDE = CSI + "?25l"
SEQ_CURSOR_SHOW = CSI + "?25h"
SEQ_WRAP_OFF = CSI + "?7l"
SEQ_WRAP_ON = CSI + "?7h"
SEQ_CLEAR = CSI + "2J"
SEQ_HOME = CSI + "H"
SEQ_RESET_SGR = CSI + "0m"

# Windows 控制台模式
_ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
_STD_OUTPUT_HANDLE = -11


class ColorSupport(IntEnum):
    """终端颜色能力。Iteration A 不输出颜色，这里只做能力探测与上报。"""

    NONE = 0
    """不支持颜色（``TERM=dumb``、``NO_COLOR``、非 TTY）。"""

    BASIC = 1
    """16 色（ANSI 基本色）。"""

    XTERM256 = 2
    """256 色。"""

    TRUECOLOR = 3
    """24-bit 真彩色。"""


def _stream_is_tty(stream: Optional[TextIO]) -> bool:
    if stream is None:
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def supports_ansi(stream: Optional[TextIO] = None) -> bool:
    """终端是否能安全接收 ANSI 转义序列。

    Windows 上的权威信号是"是不是控制台 TTY"；真正的 VT 处理开关由
    :meth:`Terminal.setup` 通过 ``SetConsoleMode`` 打开。若打开失败而终端
    其实不支持，输出会退化为可见的转义字符——这种情况在 Windows 10 之前
    的旧控制台才会出现，本库不做版本探测。
    """
    stream = sys.stdout if stream is None else stream
    return _stream_is_tty(stream)


def detect_color_support(
    stream: Optional[TextIO] = None,
    env: Optional[Any] = None,
) -> ColorSupport:
    """探测终端颜色能力。

    判定顺序（前者优先）：

    1. ``NO_COLOR`` 非空 → :attr:`ColorSupport.NONE`
    2. ``CLICOLOR=0`` → :attr:`ColorSupport.NONE`
    3. 非 TTY → :attr:`ColorSupport.NONE`
    4. ``TERM=dumb`` → :attr:`ColorSupport.NONE`
    5. ``COLORTERM=truecolor|24bit`` → :attr:`ColorSupport.TRUECOLOR`
    6. ``TERM`` 含 ``256color`` → :attr:`ColorSupport.XTERM256`
    7. 其余 → :attr:`ColorSupport.BASIC`

    Note:
        这里只做**探测**。Iteration A 的渲染器不输出任何 SGR 序列，
        因此"不支持颜色时不得输出颜色转义"在当前实现中天然成立。
    """
    environ = os.environ if env is None else env
    stream = sys.stdout if stream is None else stream

    if environ.get("NO_COLOR", ""):
        return ColorSupport.NONE
    if environ.get("CLICOLOR", "") == "0":
        return ColorSupport.NONE
    if not _stream_is_tty(stream):
        return ColorSupport.NONE

    term = (environ.get("TERM") or "").strip().lower()
    if term == "dumb":
        return ColorSupport.NONE

    colorterm = (environ.get("COLORTERM") or "").strip().lower()
    if colorterm in ("truecolor", "24bit"):
        return ColorSupport.TRUECOLOR
    if "256color" in term:
        return ColorSupport.XTERM256
    return ColorSupport.BASIC


# ---------------------------------------------------------------- Windows 控制台


def _get_stdout_handle() -> Optional[int]:
    import ctypes

    handle = ctypes.windll.kernel32.GetStdHandle(_STD_OUTPUT_HANDLE)
    if handle in (0, -1, None):
        return None
    return handle


class Terminal:
    """stdout 侧终端状态机。

    Args:
        stream: 输出流，默认 ``sys.stdout``。测试可注入伪流。
        force_ansi: 强制开启/关闭 ANSI 输出；``None`` 表示自动判断。

    Example:
        >>> term = Terminal()
        >>> with term.session():          # doctest: +SKIP
        ...     term.write("hello")
    """

    def __init__(
        self,
        stream: Optional[TextIO] = None,
        *,
        force_ansi: Optional[bool] = None,
    ) -> None:
        self._stream: TextIO = sys.stdout if stream is None else stream
        self._active = False
        self._alt_screen = False
        self._cursor_hidden = False
        self._wrap_disabled = False
        self._vt_enabled = False
        self._old_console_mode: Optional[int] = None
        self._console_handle: Optional[int] = None

        tty = _stream_is_tty(self._stream)
        if force_ansi is None:
            self.ansi = tty and supports_ansi(self._stream)
        else:
            self.ansi = bool(force_ansi)
        self.is_tty = tty
        self.color_support = detect_color_support(self._stream)

    # ------------------------------------------------------------------ 生命周期

    def setup(
        self,
        *,
        alt_screen: bool = True,
        hide_cursor: bool = True,
        disable_autowrap: bool = True,
        clear_on_enter: bool = True,
    ) -> None:
        """进入游戏终端状态。重复调用无副作用。

        Raises:
            TerminalError: 初始化过程中发生错误（已做的修改会被回滚）。
        """
        if self._active:
            return
        self._active = True
        try:
            if not self.ansi:
                return
            self._enable_vt()
            if alt_screen:
                self.write(SEQ_ALT_SCREEN_ON)
                self._alt_screen = True
            if disable_autowrap:
                self.write(SEQ_WRAP_OFF)
                self._wrap_disabled = True
            if hide_cursor:
                self.write(SEQ_CURSOR_HIDE)
                self._cursor_hidden = True
            if clear_on_enter:
                self.write(SEQ_HOME + SEQ_CLEAR)
            self.flush()
        except Exception as exc:
            self.restore()
            raise TerminalError("终端初始化失败：{}".format(exc)) from exc

    def restore(self) -> None:
        """还原终端状态。幂等，且不会因为某个步骤失败而中断其余步骤。"""
        if not self._active:
            return
        if self.ansi:
            # 顺序与 setup 相反；每一项单独兜底，保证"尽力恢复"。
            if self._cursor_hidden:
                self._safe_write(SEQ_CURSOR_SHOW)
                self._cursor_hidden = False
            if self._wrap_disabled:
                self._safe_write(SEQ_WRAP_ON)
                self._wrap_disabled = False
            self._safe_write(SEQ_RESET_SGR)
            if self._alt_screen:
                self._safe_write(SEQ_ALT_SCREEN_OFF)
                self._alt_screen = False
            self._safe_flush()
        self._restore_console_mode()
        self._active = False

    def __enter__(self) -> "Terminal":
        self.setup()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.restore()
        return False

    # ------------------------------------------------------------------ 输出

    def write(self, text: str) -> None:
        """写出文本（不做 flush）。"""
        if not text:
            return
        try:
            self._stream.write(text)
        except UnicodeEncodeError:
            self._write_replacing(text)

    def flush(self) -> None:
        """刷新输出缓冲。"""
        try:
            self._stream.flush()
        except (ValueError, OSError):  # pragma: no cover - 流已关闭
            pass

    def _safe_write(self, text: str) -> None:
        try:
            self.write(text)
        except (ValueError, OSError):  # pragma: no cover - 流已关闭
            pass

    def _safe_flush(self) -> None:
        self.flush()

    def _write_replacing(self, text: str) -> None:
        """编码失败时用替换字符重写，避免整帧崩掉。"""
        encoding = getattr(self._stream, "encoding", None) or "utf-8"
        buffer = getattr(self._stream, "buffer", None)
        if buffer is None:
            self._stream.write(text.encode(encoding, "replace").decode(encoding, "replace"))
            return
        try:
            self._stream.flush()
            buffer.write(text.encode(encoding, "replace"))
        except (ValueError, OSError):  # pragma: no cover
            pass

    # ------------------------------------------------------------------ 能力

    def hide_cursor(self) -> None:
        """隐藏光标（幂等）。"""
        if self.ansi and not self._cursor_hidden:
            self.write(SEQ_CURSOR_HIDE)
            self.flush()
            self._cursor_hidden = True

    def show_cursor(self) -> None:
        """显示光标（幂等）。"""
        if self.ansi and self._cursor_hidden:
            self.write(SEQ_CURSOR_SHOW)
            self.flush()
            self._cursor_hidden = False

    def clear(self) -> None:
        """清屏并把光标移到左上角。"""
        if self.ansi:
            self.write(SEQ_HOME + SEQ_CLEAR)
        self.flush()

    def size(self) -> Tuple[int, int]:
        """返回 ``(列数, 行数)``，任何情况下都不抛异常。"""
        try:
            fileno = self._stream.fileno()
            size = os.get_terminal_size(fileno)
            return max(1, size.columns), max(1, size.lines)
        except (AttributeError, ValueError, OSError):
            pass
        try:
            fallback = shutil.get_terminal_size()
            return max(1, fallback.columns), max(1, fallback.lines)
        except (ValueError, OSError):  # pragma: no cover
            return 80, 24

    # ------------------------------------------------------------------ Windows VT

    def _enable_vt(self) -> None:
        if os.name != "nt" or self._vt_enabled:
            return
        try:
            import ctypes
            from ctypes import wintypes

            handle = _get_stdout_handle()
            if handle is None:
                return
            mode = wintypes.DWORD()
            if not ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return
            self._console_handle = handle
            self._old_console_mode = int(mode.value)
            if not (mode.value & _ENABLE_VIRTUAL_TERMINAL_PROCESSING):
                ctypes.windll.kernel32.SetConsoleMode(
                    handle, mode.value | _ENABLE_VIRTUAL_TERMINAL_PROCESSING
                )
            self._vt_enabled = True
        except Exception:  # pragma: no cover - 非 Windows 或受限环境
            self._vt_enabled = False

    def _restore_console_mode(self) -> None:
        if not self._vt_enabled:
            return
        handle = self._console_handle
        old_mode = self._old_console_mode
        self._vt_enabled = False
        self._console_handle = None
        self._old_console_mode = None
        if handle is None or old_mode is None:
            return
        try:  # pragma: no cover - 非 Windows 或受限环境
            import ctypes

            ctypes.windll.kernel32.SetConsoleMode(handle, old_mode)
        except Exception:
            pass

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "Terminal(tty={}, ansi={}, color={})".format(
            self.is_tty, self.ansi, self.color_support.name
        )
