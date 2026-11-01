"""测试用的伪终端与假后端。

所有测试都不依赖真实终端：输出流、输入后端、终端尺寸都可以注入，
因此在 CI（甚至无 TTY 的管道里）也能完整运行。
"""

from __future__ import annotations

import io
import sys
import time
from typing import List, Optional


class FakeStream(io.StringIO):
    """可注入的输出流。

    Args:
        tty: ``isatty()`` 的返回值。``False`` 用于测试非 TTY 降级路径。
        fail_after: 写入多少次之后开始抛 ``OSError``（测试渲染失败）。
    """

    encoding = "utf-8"

    def __init__(self, tty: bool = True, fail_after: Optional[int] = None) -> None:
        super().__init__()
        self._tty = tty
        self._fail_after = fail_after
        self.write_count = 0

    def isatty(self) -> bool:
        return self._tty

    def fileno(self) -> int:
        # 故意抛异常：让 Terminal.size() 走 shutil 兜底而不是真实控制台。
        raise OSError("FakeStream 没有文件描述符")

    def write(self, text: str) -> int:
        if self._fail_after is not None and self.write_count >= self._fail_after:
            raise OSError("模拟写入失败")
        self.write_count += 1
        return super().write(text)

    def take(self) -> str:
        """取出当前内容并清空（便于逐帧断言）。"""
        value = self.getvalue()
        self.seek(0)
        self.truncate(0)
        return value

    def fail_next(self, enable: bool = True) -> None:
        """让下一次写入失败（测试渲染失败路径）。"""
        self._fail_after = 0 if enable else None
        self.write_count = 0


class FakeBackend:
    """可编排的输入后端。"""

    name = "fake"
    supports_key_up = False

    def __init__(self, chunks: Optional[List[str]] = None) -> None:
        self._chunks: List[str] = list(chunks or [])
        self.opened = False
        self.open_count = 0
        self.close_count = 0
        self.wait_calls = 0

    # -- 测试辅助 ---------------------------------------------------------
    def feed(self, text: str) -> None:
        self._chunks.append(text)

    # -- 后端接口 ---------------------------------------------------------
    def available(self) -> bool:
        return True

    def open(self) -> None:
        self.opened = True
        self.open_count += 1

    def close(self) -> None:
        self.opened = False
        self.close_count += 1

    def read_available(self) -> str:
        if not self._chunks:
            return ""
        data = "".join(self._chunks)
        self._chunks.clear()
        return data

    def wait(self, timeout: Optional[float]) -> bool:
        self.wait_calls += 1
        if self._chunks:
            return True
        if timeout:
            time.sleep(min(timeout, 0.001))
        return False


class ExplodingStream(FakeStream):
    """一写就炸的流，用于测试初始化失败回滚。"""

    def __init__(self) -> None:
        super().__init__(tty=True, fail_after=0)


def fresh_stdout() -> FakeStream:
    """返回一个 tty=True 的空流。"""
    return FakeStream(tty=True)


def module_loaded(name: str) -> bool:
    return name in sys.modules
