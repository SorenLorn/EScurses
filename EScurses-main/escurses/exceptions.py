"""EScurses 异常体系。

设计原则：
- 所有异常都继承 :class:`EscursesError`，用户可以只捕获一个基类。
- 同时继承对应的内置异常，保证旧代码里 ``except IndexError`` 之类的写法仍然生效。
- 每个异常只表达一种含义，不做"万能错误"。
"""

from __future__ import annotations

__all__ = [
    "EscursesError",
    "InvalidConfigError",
    "InvalidCharError",
    "OutOfBoundsError",
    "TerminalError",
    "InputError",
    "GameStateError",
]


class EscursesError(Exception):
    """所有 EScurses 异常的基类。"""


class InvalidConfigError(EscursesError, ValueError):
    """配置项非法，例如 ``fps <= 0``、未知的宽度。"""


class InvalidCharError(EscursesError, ValueError):
    """传入的字符不可绘制，例如控制字符 ``"\\n"`` 或纯组合标记。"""


class OutOfBoundsError(EscursesError, IndexError):
    """坐标超出屏幕范围。

    继承 :class:`IndexError` 是为了兼容旧版本：旧实现在越界时抛的就是
    ``IndexError``，这样 ``except IndexError`` 的老代码不需要修改。
    """

    def __init__(self, x: int, y: int, width: int, height: int) -> None:
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        super().__init__(
            "坐标 ({}, {}) 超出屏幕范围：可写区域为 1..{} (x) 和 1..{} (y)".format(
                x, y, width, height
            )
        )


class TerminalError(EscursesError, OSError):
    """终端状态无法建立或恢复。"""


class InputError(EscursesError, OSError):
    """输入设备不可用（无 stdin、fd 被关闭等）。"""


class GameStateError(EscursesError, RuntimeError):
    """游戏生命周期调用顺序错误，例如未 ``start()`` 就绘制。"""
