"""POSIX 输入后端（termios + os.read）。

**只在 ``os.name == 'posix'`` 时被导入**，因此 Linux/macOS 之外的平台
不会因为缺少 ``termios`` 而导入失败。

两个关键点（对应审计报告 INP-01 / INP-02 / INP-03）：

1. 字符模式在 :meth:`open` 时**一次性**设置，退出时还原，
   不再每次读键都做 3 次 ``tcgetattr``/``tcsetattr``。
2. 读键用 ``os.read()`` 而不是 ``sys.stdin.read()``，
   绕开 ``TextIOWrapper`` 的用户态缓冲，避免 ``select`` 看得见 fd、
   却拿不到已被 Python 缓冲吞掉的字节。
"""

from __future__ import annotations

import codecs
import os
import select
import sys
import termios
from typing import Optional, TextIO

from .exceptions import InputError

__all__ = ["PosixBackend"]


class PosixBackend:
    """基于 termios 的字符输入。"""

    name = "posix"
    supports_key_up = False
    """终端无法可靠区分"按下"和"抬起"，因此不提供 KEY_UP 事件。"""

    def __init__(self, stream: Optional[TextIO] = None) -> None:
        self._stream = sys.stdin if stream is None else stream
        self._fd: Optional[int] = None
        self._saved_attrs = None
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            self._fd = self._stream.fileno()
        except (AttributeError, ValueError, OSError):
            self._fd = None

    # ------------------------------------------------------------------ 能力

    def available(self) -> bool:
        """stdin 是否是可用终端。"""
        if self._fd is None:
            return False
        try:
            return os.isatty(self._fd)
        except OSError:  # pragma: no cover
            return False

    # ------------------------------------------------------------------ 生命周期

    def open(self) -> None:
        """进入字符模式（cbreak），幂等。

        Raises:
            InputError: stdin 不可用或 termios 调用失败。
        """
        if self._saved_attrs is not None:
            return
        if self._fd is None:
            raise InputError("stdin 没有可用的文件描述符")
        try:
            saved = termios.tcgetattr(self._fd)
        except (termios.error, OSError) as exc:
            raise InputError("无法读取终端属性：{}".format(exc)) from exc

        mode = termios.tcgetattr(self._fd)
        # lflag：关闭行缓冲与回显，保留 ISIG 让 Ctrl+C 始终能救命。
        mode[3] &= ~(termios.ICANON | termios.ECHO)
        # iflag：关闭流控与回车转换，避免 Ctrl+S 冻住游戏、\r 被转成 \n。
        mode[0] &= ~(termios.IXON | termios.ICRNL | termios.INLCR | termios.IGNCR)
        # 非阻塞读：VMIN=0, VTIME=0。
        mode[6][termios.VMIN] = 0
        mode[6][termios.VTIME] = 0
        try:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, mode)
        except (termios.error, OSError) as exc:  # pragma: no cover
            raise InputError("无法进入字符模式：{}".format(exc)) from exc
        self._saved_attrs = saved

    def close(self) -> None:
        """还原终端属性，幂等。"""
        saved = self._saved_attrs
        self._saved_attrs = None
        if saved is None or self._fd is None:
            return
        try:  # pragma: no cover - 关闭失败不应影响退出流程
            termios.tcsetattr(self._fd, termios.TCSADRAIN, saved)
        except (termios.error, OSError):
            pass

    # ------------------------------------------------------------------ 读

    def read_available(self) -> str:
        """读取当前可用的全部字符；没有数据返回空串。"""
        if self._fd is None or self._saved_attrs is None:
            return ""
        try:
            data = os.read(self._fd, 4096)
        except (BlockingIOError, InterruptedError):
            return ""
        except OSError:  # pragma: no cover
            return ""
        if not data:
            return ""
        return self._decoder.decode(data)

    def wait(self, timeout: Optional[float]) -> bool:
        """最多等待 ``timeout`` 秒，返回是否有数据可读。"""
        if self._fd is None:
            if timeout:
                import time

                time.sleep(timeout)
            return False
        try:
            ready, _, _ = select.select([self._fd], [], [], timeout)
        except (OSError, ValueError):  # pragma: no cover
            return False
        return bool(ready)
