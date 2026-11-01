"""屏幕缓冲区数据结构。

本模块**不知道终端存在**：只负责存格子、比格子、复制格子。

一个终端单元（:class:`Cell`) 的三种形态：

- 普通字符：``char="A"``, ``width=1``, ``cont=False``
- 宽字符左半：``char="中"``, ``width=2``, ``cont=False``
- 宽字符右半：``char=""``, ``width=0``, ``cont=True``（渲染时跳过，不写任何字符）

``Cell`` 是不可变的，因此 :meth:`DoubleBuffer.commit` 只需要复制行列表，
不需要深拷贝单元格对象。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .exceptions import InvalidConfigError

__all__ = ["Cell", "BLANK", "CONTINUATION", "Buffer", "DoubleBuffer"]


@dataclass(frozen=True)
class Cell:
    """一个终端单元格。"""

    char: str = " "
    width: int = 1
    cont: bool = False

    @property
    def is_blank(self) -> bool:
        """是否为空白格（宽字符右半也算空白，因为它本身不占内容）。"""
        return self.cont or self.char == " "


BLANK = Cell()
"""空白格单例。所有清空操作都复用同一个对象，减少内存与比较开销。"""

CONTINUATION = Cell(char="", width=0, cont=True)
"""宽字符右半格。"""


class Buffer:
    """二维单元格缓冲区，0-based 索引。

    坐标转换（1-based → 0-based）由 :class:`~escurses.screen.Screen` 负责，
    本类只接受合法的 0-based 下标，不做边界检查以外的任何语义处理。
    """

    __slots__ = ("width", "height", "_rows")

    def __init__(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise InvalidConfigError(
                "缓冲区尺寸必须为正，收到 {}x{}".format(width, height)
            )
        self.width = width
        self.height = height
        self._rows: List[List[Cell]] = [[BLANK] * width for _ in range(height)]

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "Buffer({}x{})".format(self.width, self.height)

    def row(self, y: int) -> List[Cell]:
        """返回第 ``y`` 行的单元格列表（0-based，可变）。"""
        return self._rows[y]

    def get(self, x: int, y: int) -> Cell:
        """返回 ``(x, y)`` 处的单元格（0-based，调用方保证不越界）。"""
        return self._rows[y][x]

    def set(self, x: int, y: int, cell: Cell) -> None:
        """写入单元格（0-based，调用方保证不越界）。"""
        self._rows[y][x] = cell

    def reset(self) -> None:
        """把所有格子恢复为空白。"""
        blank_row = [BLANK] * self.width
        for y in range(self.height):
            self._rows[y] = list(blank_row)

    def clear_row(self, y: int) -> None:
        """清空单行。"""
        self._rows[y] = [BLANK] * self.width

    def resize(self, width: int, height: int) -> None:
        """调整尺寸，保留左上角重叠区域的内容。

        缩小时被裁掉的内容直接丢弃；放大时新增区域为空白。
        """
        if width <= 0 or height <= 0:
            raise InvalidConfigError(
                "缓冲区尺寸必须为正，收到 {}x{}".format(width, height)
            )
        if width == self.width and height == self.height:
            return
        rows: List[List[Cell]] = []
        for y in range(height):
            if y < self.height:
                old = self._rows[y]
                if width <= self.width:
                    rows.append(old[:width])
                else:
                    rows.append(old + [BLANK] * (width - self.width))
            else:
                rows.append([BLANK] * width)
        self._rows = rows
        self.width = width
        self.height = height

    def copy_from(self, other: "Buffer") -> None:
        """把另一个同尺寸缓冲区的内容复制进来（行级复制，不复制单元格对象）。"""
        if other.width != self.width or other.height != self.height:
            raise InvalidConfigError("copy_from 要求尺寸一致")
        for y in range(self.height):
            self._rows[y] = list(other._rows[y])

    def same_as(self, other: "Buffer") -> bool:
        """内容是否完全一致（尺寸相同且每个单元格相等）。"""
        if other.width != self.width or other.height != self.height:
            return False
        return all(a == b for a, b in zip(self._rows, other._rows))

    def row_text(self, y: int, *, rstrip: bool = True) -> str:
        """把一行拼成字符串。

        宽字符右半格（``cont``）不输出任何字符——左侧的宽字符在终端里
        已经自动占了两列。
        """
        parts: List[str] = []
        for cell in self._rows[y]:
            if cell.cont:
                continue
            parts.append(cell.char)
        text = "".join(parts)
        return text.rstrip() if rstrip else text

    def row_is_blank(self, y: int) -> bool:
        """整行是否全为空白（含宽字符右半）。"""
        return all(cell.is_blank for cell in self._rows[y])

    def lines(self, *, rstrip: bool = True) -> List[str]:
        """所有行的文本表示。"""
        return [self.row_text(y, rstrip=rstrip) for y in range(self.height)]

class DoubleBuffer:
    """双缓冲：``back`` 是绘制目标，``front`` 是"终端上现在真实显示的内容"。

    设计要点（与教科书版 swap 不同，原因见 REFACTOR_PLAN 3.3）：

    - ``back`` 是**持久**的场景缓冲，帧结束后不会被自动清空，
      因此"画一次就一直显示"的旧库语义得以保留。
    - ``front`` 只用于差异比较，成功渲染后由 :meth:`commit` 从 ``back`` 复制。
    - ``valid`` 为 ``False`` 表示"不知道终端上是什么"，必须全量重绘。
    """

    __slots__ = ("back", "front", "valid")

    def __init__(self, width: int, height: int) -> None:
        self.back = Buffer(width, height)
        self.front = Buffer(width, height)
        self.valid = False

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "DoubleBuffer({}x{}, valid={})".format(
            self.back.width, self.back.height, self.valid
        )

    @property
    def width(self) -> int:
        return self.back.width

    @property
    def height(self) -> int:
        return self.back.height

    def commit(self) -> None:
        """渲染成功后调用：让 ``front`` 与 ``back`` 一致。"""
        self.front.copy_from(self.back)
        self.valid = True

    def invalidate(self) -> None:
        """作废 ``front``，下一帧强制全量重绘。"""
        self.valid = False

    def resize(self, width: int, height: int) -> None:
        """调整尺寸并作废 ``front``（尺寸变了，终端内容不可信）。"""
        if width == self.width and height == self.height:
            return
        self.back.resize(width, height)
        self.front.resize(width, height)
        self.front.reset()
        self.valid = False

    def clear(self) -> None:
        """清空场景内容并作废 ``front``。"""
        self.back.reset()
        self.invalidate()

    def reset(self) -> None:
        """把两个缓冲区都清空，并作废 ``front``。"""
        self.back.reset()
        self.front.reset()
        self.valid = False

    def pending_changes(self) -> Optional[int]:
        """返回发生变化的单元格数量；``front`` 无效时返回 ``None``。

        这是 O(行数) 的快速统计，仅供测试和调试使用，渲染路径不依赖它。
        """
        if not self.valid:
            return None
        changed = 0
        for y in range(self.height):
            back_row = self.back.row(y)
            front_row = self.front.row(y)
            if back_row == front_row:
                continue
            for a, b in zip(back_row, front_row):
                if a != b:
                    changed += 1
        return changed
