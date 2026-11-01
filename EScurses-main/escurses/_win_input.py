"""Windows 输入后端（msvcrt）。

**只在 ``os.name == 'nt'`` 时被导入**，因此 Linux/macOS 上不会加载 ``msvcrt``。

关键点（对应审计报告 INP-04）：``msvcrt.getwch()`` 读取方向键等扩展键时，
第一次返回 ``\\x00`` 或 ``\\xe0`` 前缀，第二次才返回扫描码。本后端在读到前缀后
**立即**取第二个字符，避免扫描码泄漏成独立按键。
"""

from __future__ import annotations

import sys
import time
from typing import Any, Optional

from .exceptions import InputError

__all__ = ["WindowsBackend"]


class WindowsBackend:
    """基于 msvcrt 的控制台键盘输入。"""

    name = "windows"
    supports_key_up = False
    """读取字符流的模式下无法区分按下/抬起；如需 KEY_UP 需改用 ReadConsoleInput。"""

    def __init__(self, stream: Any = None) -> None:
        self._stream = sys.stdin if stream is None else stream
        try:
            import msvcrt
        except ImportError as exc:  # pragma: no cover - 仅在非 Windows 误用时触发
            raise InputError("当前平台不支持 msvcrt") from exc
        self._msvcrt = msvcrt
        self._opened = False

    # ------------------------------------------------------------------ 能力

    def available(self) -> bool:
        try:
            return bool(self._stream.isatty())
        except (AttributeError, ValueError, OSError):
            return False

    # ------------------------------------------------------------------ 生命周期

    def open(self) -> None:
        """Windows 控制台不需要切换模式（``getwch`` 不经过行缓冲、不回显）。"""
        if not self.available():
            raise InputError("stdin 不是控制台")
        self._opened = True

    def close(self) -> None:
        self._opened = False

    # ------------------------------------------------------------------ 读

    def read_available(self) -> str:
        """读取缓冲区中所有待处理按键（非阻塞）。"""
        if not self._opened:
            return ""
        chars = []
        msvcrt = self._msvcrt
        while msvcrt.kbhit():
            ch = msvcrt.getwch()
            chars.append(ch)
            if ch in ("\x00", "\xe0"):
                # 扩展键前缀：必须紧接着取扫描码，否则会变成两个事件。
                if msvcrt.kbhit():
                    chars.append(msvcrt.getwch())
        return "".join(chars)

    def wait(self, timeout: Optional[float]) -> bool:
        """轮询等待按键（带 sleep，不忙等）。"""
        if not self._opened:
            if timeout:
                time.sleep(timeout)
            return False
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self._msvcrt.kbhit():
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.005)
