"""差异渲染。

工作流程：

1. ``front``（终端上现在显示的内容）与 ``back``（本帧要显示的内容）逐行比较。
2. 只在 ``back`` 与 ``front`` 不同的区间输出字符。
3. 全部写出成功后，才把 ``back`` 复制进 ``front``（提交）。
   写出失败则不提交，下一帧会自动重试，不会出现"以为已经画上去"的假象。

输出量对比（80×24，实测见 REFACTOR_REPORT）：

- 旧实现：每格一条 ``\\x1b[y;xH`` + 一个字符，约 16 KB/帧。
- 本实现：首帧全量 + 之后只发变化区间，静止帧 0 字节。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Tuple

from .screen import Screen
from .terminal import (
    CSI,
    SEQ_CLEAR,
    SEQ_HOME,
    Terminal,
)

__all__ = ["RenderStats", "Renderer"]

# 同一行内两个变化区间之间允许跨过的"未变化列"数量；跨过后合并成一个区间，
# 省一次光标定位。取值过大会导致重复写出大量未变化内容，2 是经验值。
_MERGE_GAP = 2

# 同一行内用 CUF（相对右移）代替 CUP（绝对定位）的最大间隔。
_CUF_MAX_GAP = 3


@dataclass
class RenderStats:
    """单帧渲染统计。"""

    full: bool = False
    """是否整屏重绘。"""

    rows: int = 0
    """实际输出的行数。"""

    spans: int = 0
    """输出的连续片段数。"""

    cells: int = 0
    """输出的字符数（宽字符算 1 个字符、2 列）。"""

    cursor_moves: int = 0
    """光标定位次数（CUP + CUF）。"""

    bytes_written: int = 0
    """输出字节数（UTF-8）。"""

    elapsed: float = 0.0
    """本次 ``present()`` 耗时（秒）。"""


class Renderer:
    """把 :class:`~escurses.screen.Screen` 的内容差异输出到终端。"""

    def __init__(self, terminal: Terminal, screen: Screen) -> None:
        self.terminal = terminal
        self.screen = screen
        self.last_stats = RenderStats()

    # ------------------------------------------------------------------ 公共

    def invalidate(self) -> None:
        """作废 ``front``，下一帧强制整屏重绘。"""
        self.screen.invalidate()

    def present(self, *, force: bool = False) -> RenderStats:
        """渲染一帧。

        Args:
            force: 强制整屏重绘（忽略 ``front`` 的有效性）。

        Returns:
            本帧统计信息。

        Raises:
            OSError: 写入终端失败。此时 ``front`` **不会**被提交。
        """
        started = time.perf_counter()
        full = force or self.screen.needs_full_redraw

        if not self.terminal.ansi:
            payload, stats = self._build_plain(full)
        elif full:
            payload, stats = self._build_full()
        else:
            payload, stats = self._build_diff()

        stats.full = full
        stats.bytes_written = len(payload.encode("utf-8", "replace"))
        if payload:
            try:
                self.terminal.write(payload)
                self.terminal.flush()
            except (ValueError, OSError):
                # 输出失败：终端内容未知，作废 front，下一帧整屏重绘。
                self.screen.invalidate()
                raise
        self.screen.buffer.commit()
        stats.elapsed = time.perf_counter() - started
        self.last_stats = stats
        return stats

    # ------------------------------------------------------------------ 整屏

    def _build_full(self) -> Tuple[str, RenderStats]:
        """整屏重绘：先清屏，再只写有内容的行。"""
        stats = RenderStats(rows=0, spans=0, cells=0, cursor_moves=0)
        parts: List[str] = [SEQ_HOME, SEQ_CLEAR]
        back = self.screen.buffer.back
        for y in range(back.height):
            if back.row_is_blank(y):
                continue
            text = back.row_text(y, rstrip=True)
            if not text:
                continue
            parts.append(CSI + "{};1H".format(y + 1))
            parts.append(text)
            stats.rows += 1
            stats.spans += 1
            stats.cursor_moves += 1
            stats.cells += len(text)
        return "".join(parts), stats

    def _build_plain(self, full: bool) -> Tuple[str, RenderStats]:
        """非 TTY（重定向到文件/管道）时的纯文本输出。

        不输出任何转义序列；有变化时输出完整一屏，便于日志阅读。
        """
        stats = RenderStats()
        back = self.screen.buffer.back
        front = self.screen.buffer.front
        changed = full
        if not changed:
            for y in range(back.height):
                if back.row(y) != front.row(y):
                    changed = True
                    break
        if not changed:
            return "", stats
        lines = back.lines(rstrip=True)
        stats.rows = len(lines)
        stats.spans = 1
        stats.cells = sum(1 for line in lines for _ in line)
        return "\n".join(lines) + "\n", stats

    # ------------------------------------------------------------------ 差异

    def _build_diff(self) -> Tuple[str, RenderStats]:
        stats = RenderStats()
        parts: List[str] = []
        back = self.screen.buffer.back
        front = self.screen.buffer.front
        width = back.width

        cursor_row = -1
        cursor_col = -1  # 0-based，下一个字符会落在这一列

        for y in range(back.height):
            back_row = back.row(y)
            front_row = front.row(y)
            if back_row == front_row:
                continue
            spans = self._spans(back_row, front_row, width)
            if not spans:
                continue
            stats.rows += 1
            for start, end in spans:
                gap = start - cursor_col
                if gap == 0 and cursor_row == y:
                    pass  # 光标正好停在这里
                elif cursor_row == y and 0 < gap <= _CUF_MAX_GAP:
                    parts.append(CSI + "{}C".format(gap))
                    stats.cursor_moves += 1
                else:
                    parts.append(CSI + "{};{}H".format(y + 1, start + 1))
                    stats.cursor_moves += 1

                moved = 0
                chars = 0
                for x in range(start, end + 1):
                    cell = back_row[x]
                    if cell.cont:
                        # 宽字符右半：左侧字符已经占了两列，这里不能再写。
                        continue
                    parts.append(cell.char)
                    moved += cell.width
                    chars += 1
                stats.cells += chars
                stats.spans += 1
                cursor_row = y
                cursor_col = start + moved

        return "".join(parts), stats

    def _spans(
        self,
        back_row: List,
        front_row: List,
        width: int,
    ) -> List[Tuple[int, int]]:
        """找出需要重写的列区间 ``[start, end]``（0-based，闭区间）。

        相邻区间之间若只隔 1~2 个未变化列则合并，减少定位次数。
        区间端点会向外扩展，保证不会把宽字符切成两半。
        """
        spans: List[Tuple[int, int]] = []
        x = 0
        while x < width:
            if back_row[x] == front_row[x]:
                x += 1
                continue
            start = x
            end = x
            x += 1
            while x < width:
                if back_row[x] != front_row[x]:
                    end = x
                    x += 1
                    continue
                # 遇到相同格：向后看 1~2 格是否又有差异，有则合并。
                if x + 1 < width and back_row[x + 1] != front_row[x + 1]:
                    end = x + 1
                    x += 2
                    continue
                if x + 2 < width and back_row[x + 2] != front_row[x + 2]:
                    end = x + 2
                    x += 3
                    continue
                break
            spans.append((start, end))

        # 端点扩展：不能从宽字符的右半开始写，也不能把宽字符左半当结尾。
        adjusted: List[Tuple[int, int]] = []
        for start, end in spans:
            while start > 0 and back_row[start].cont:
                start -= 1
            if back_row[end].width == 2 and end + 1 < width:
                end += 1
            adjusted.append((start, end))
        return adjusted
