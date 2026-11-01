"""屏幕与绘制接口。

职责：
- 唯一持有 :class:`~escurses.buffer.DoubleBuffer`。
- 统一坐标合法性判断（全库只在这里出现 ``-1`` 偏移）。
- 维护宽字符占位不变量。

坐标系：**1-based**，左上角是 ``(1, 1)``，``x`` 是列、``y`` 是行。
这与旧版 API、README 以及 ANSI 光标定位保持一致。

越界策略（对应审计报告 Q1）：

- 默认（``strict=False``）：裁剪。超出右/下边界的部分被丢弃，负坐标被跳过，
  绝不写入错误位置，也不抛异常。
- 严格模式（``strict=True``）：抛 :class:`OutOfBoundsError`（``IndexError`` 子类，
  旧代码的 ``except IndexError`` 仍然有效）。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .buffer import BLANK, CONTINUATION, Cell, DoubleBuffer
from .exceptions import InvalidCharError, OutOfBoundsError
from .unicode_utils import cluster_width, graphemes, iter_graphemes

__all__ = ["Screen"]


class Screen:
    """屏幕缓冲区与绘制 API。

    Args:
        width: 列数（终端宽度）。
        height: 行数（终端高度）。
        strict: 严格模式初始值。

    Note:
        本类不向终端写任何东西。所有绘制只改内存，输出由
        :class:`~escurses.renderer.Renderer` 在 ``present()`` 时完成。
    """

    __slots__ = ("_buffer", "strict")

    def __init__(self, width: int, height: int, *, strict: bool = False) -> None:
        self._buffer = DoubleBuffer(width, height)
        self.strict = bool(strict)

    # ------------------------------------------------------------------ 几何

    @property
    def width(self) -> int:
        """可写列数。合法 ``x`` 范围是 ``1..width``。"""
        return self._buffer.width

    @property
    def height(self) -> int:
        """可写行数。合法 ``y`` 范围是 ``1..height``。"""
        return self._buffer.height

    @property
    def size(self) -> Tuple[int, int]:
        """``(width, height)``。"""
        return self._buffer.width, self._buffer.height

    @property
    def buffer(self) -> DoubleBuffer:
        """底层双缓冲（渲染器使用）。"""
        return self._buffer

    @property
    def needs_full_redraw(self) -> bool:
        """是否需要全量重绘（首次渲染、清屏后、尺寸变化后）。"""
        return not self._buffer.valid

    def resize(self, width: int, height: int) -> bool:
        """调整屏幕尺寸，保留左上角内容。

        Returns:
            ``True`` 表示尺寸确实发生了变化（调用方应重新同步终端）。
        """
        if width == self.width and height == self.height:
            return False
        self._buffer.resize(width, height)
        return True

    # ------------------------------------------------------------------ 清屏

    def clear(self) -> None:
        """清空整屏内容。

        只是把 ``back`` 置空并作废 ``front``；真正的清屏序列由下一次
        ``present()`` 发出，因此可以安全地在帧中间调用。
        """
        self._buffer.clear()

    def invalidate(self) -> None:
        """作废 ``front``，强制下一帧全量重绘（终端被外部写坏时用）。"""
        self._buffer.invalidate()

    # ------------------------------------------------------------------ 绘制

    def set(self, x: int, y: int, ch: str, *, strict: Optional[bool] = None) -> bool:
        """在 ``(x, y)`` 写入**单个** grapheme cluster。

        Args:
            x: 列号（1-based）。
            y: 行号（1-based）。
            ch: 单字符或单字素簇（如 ``"中"``、``"e\\u0301"``）。
            strict: 覆盖实例的严格模式。

        Returns:
            是否真的写入了内容（越界、空串、不可绘制字符返回 ``False``）。

        Raises:
            InvalidCharError: 严格模式下传入多字符或不可绘制字符。
            OutOfBoundsError: 严格模式下坐标越界。
            TypeError: ``ch`` 不是 ``str``。
        """
        strict_mode = self.strict if strict is None else bool(strict)
        if not isinstance(ch, str):
            raise TypeError("字符必须是 str，收到 {!r}".format(type(ch).__name__))
        if not ch:
            return False
        clusters = graphemes(ch)
        if not clusters:
            return False
        if len(clusters) > 1:
            if strict_mode:
                raise InvalidCharError(
                    "Screen.set 只接受单个字符，收到 {!r}（多字符请用 text()）".format(ch)
                )
            # 宽松模式下不丢数据：按顺序依次写入，等价于 text()。
            return self.text(x, y, ch, strict=False) > 0
        return self.text(x, y, clusters[0], strict=strict_mode) > 0

    def text(self, x: int, y: int, text: str, *, strict: Optional[bool] = None) -> int:
        """从 ``(x, y)`` 开始横向写入一段文本。

        文本按 grapheme cluster 逐个写入，每个簇前进"自己的显示宽度"列，
        因此 ``"你好"`` 会占用 4 列而不是 2 列。

        Args:
            x: 起始列号（1-based）。
            y: 行号（1-based）。
            text: 任意文本。
            strict: 覆盖实例的严格模式。

        Returns:
            实际写入的列数。

        Raises:
            OutOfBoundsError: 严格模式下 ``y`` 越界，或 ``x`` 完全在屏幕外。
            InvalidCharError: 严格模式下遇到不可绘制字符。
        """
        strict_mode = self.strict if strict is None else bool(strict)
        if not isinstance(text, str):
            raise TypeError("文本必须是 str，收到 {!r}".format(type(text).__name__))
        if not text:
            return 0

        row = self._resolve_row(y, strict_mode)
        if row is None:
            return 0

        if strict_mode and not (1 <= x <= self.width):
            raise OutOfBoundsError(x, y, self.width, self.height)

        written = 0
        cursor = x
        for cluster in iter_graphemes(text):
            width = cluster_width(cluster)
            if width == 0:
                # 控制字符 / 孤立组合标记：不可绘制。
                if strict_mode:
                    raise InvalidCharError(
                        "字符 {!r} 不可绘制（宽度为 0）".format(cluster)
                    )
                continue
            index = cursor - 1
            if index >= self.width:
                break  # 右侧越界：剩下的都放不下，直接结束
            cursor += width
            if index < 0:
                continue  # 左侧越界：跳过这个簇，但光标照常前进
            if not self._place(index, row, cluster, width):
                break  # 宽字符放不进最后一列
            written += width
        return written

    def fill(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        ch: str = " ",
        *,
        strict: Optional[bool] = None,
    ) -> int:
        """用 ``ch`` 填充矩形区域（闭区间，1-based，自动裁剪）。

        Returns:
            写入的单元格数量。
        """
        strict_mode = self.strict if strict is None else bool(strict)
        if strict_mode:
            if not (1 <= x1 <= self.width and 1 <= x2 <= self.width):
                raise OutOfBoundsError(x1 if not (1 <= x1 <= self.width) else x2, y1, self.width, self.height)
            if not (1 <= y1 <= self.height and 1 <= y2 <= self.height):
                raise OutOfBoundsError(x1, y1 if not (1 <= y1 <= self.height) else y2, self.width, self.height)

        left, right = (x1, x2) if x1 <= x2 else (x2, x1)
        top, bottom = (y1, y2) if y1 <= y2 else (y2, y1)
        left = max(1, left)
        top = max(1, top)
        right = min(self.width, right)
        bottom = min(self.height, bottom)
        if left > right or top > bottom:
            return 0

        written = 0
        for y in range(top, bottom + 1):
            for x in range(left, right + 1):
                if self.set(x, y, ch, strict=strict_mode):
                    written += 1
        return written

    def get(self, x: int, y: int, *, strict: Optional[bool] = None) -> Cell:
        """读取单元格；越界返回空白格（严格模式抛异常）。"""
        strict_mode = self.strict if strict is None else bool(strict)
        if not isinstance(x, int) or isinstance(x, bool):
            raise TypeError("x 必须是 int")
        if not isinstance(y, int) or isinstance(y, bool):
            raise TypeError("y 必须是 int")
        if 1 <= x <= self.width and 1 <= y <= self.height:
            return self._buffer.back.get(x - 1, y - 1)
        if strict_mode:
            raise OutOfBoundsError(x, y, self.width, self.height)
        return BLANK

    # ------------------------------------------------------------------ 只读视图

    def row_text(self, y: int, *, rstrip: bool = True) -> str:
        """第 ``y`` 行（1-based）的文本表示。"""
        if not 1 <= y <= self.height:
            raise OutOfBoundsError(1, y, self.width, self.height)
        return self._buffer.back.row_text(y - 1, rstrip=rstrip)

    def lines(self, *, rstrip: bool = True) -> List[str]:
        """整屏的文本表示（调试、测试、纯文本输出使用）。"""
        return self._buffer.back.lines(rstrip=rstrip)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "Screen({}x{}, strict={})".format(self.width, self.height, self.strict)

    # ------------------------------------------------------------------ 内部

    def _resolve_row(self, y: int, strict: bool) -> Optional[int]:
        """把 1-based 行号转成 0-based；越界返回 ``None`` 或抛异常。"""
        if not isinstance(y, int) or isinstance(y, bool):
            raise TypeError("y 必须是 int，收到 {!r}".format(type(y).__name__))
        if 1 <= y <= self.height:
            return y - 1
        if strict:
            raise OutOfBoundsError(1, y, self.width, self.height)
        return None

    def _detach(self, row: List[Cell], index: int) -> None:
        """把 ``index`` 位置从相邻的宽字符中解绑。

        宽字符占两格，被拆散时必须把另一半抹成空格，否则会留下半个字。
        """
        cell = row[index]
        if cell.cont:
            if index > 0:
                row[index - 1] = BLANK
            return
        if cell.width == 2 and index + 1 < self.width:
            row[index + 1] = BLANK

    def _place(self, index: int, row_index: int, cluster: str, width: int) -> bool:
        """把单个簇写入 0-based ``(index, row_index)``。

        Returns:
            ``False`` 表示宽字符放不进最后一列，调用方应立即停止。
        """
        row = self._buffer.back.row(row_index)
        if width == 2 and index + 1 >= self.width:
            return False
        new_cell = Cell(cluster, width, False)
        current = row[index]
        # 内容完全相同时保留原来的 Cell 对象。Cell 不可变，复用对象能让
        # 差异比较走 CPython 的身份快路径（list 比较先比 is 再比 ==），
        # 这对"每帧重画同样内容"的立即模式游戏是数量级的差别。
        if current == new_cell and not current.cont:
            if width == 1 or (index + 1 < self.width and row[index + 1].cont):
                return True
        self._detach(row, index)
        if width == 2:
            self._detach(row, index + 1)
            row[index] = new_cell
            row[index + 1] = CONTINUATION
        else:
            row[index] = new_cell
        return True
