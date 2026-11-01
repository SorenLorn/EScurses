"""渲染保真度测试：把输出的转义序列回放到"虚拟终端"里，逐格比对。

这是没有真实终端时最有力的正确性验证：

- 差异渲染可能产生的残影、错位、半字覆盖，都会在虚拟终端里暴露成内容不一致。
- 断言方式是 ``虚拟终端屏幕内容 == Screen 缓冲区内容``，不依赖对序列文本的猜测。
"""

from __future__ import annotations

import re
import unittest

from escurses.renderer import Renderer
from escurses.screen import Screen
from escurses.terminal import Terminal

from .helpers import FakeStream

_CSI_RE = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z])")


class VirtualTerminal:
    """极简 ANSI 解释器，只实现渲染器实际会用到的序列。

    不支持滚屏：写入被自动换行关闭（``?7l``）时的行为，即"写满最后一列就停在原地"。
    """

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.grid = [[" "] * width for _ in range(height)]
        self.x = 0
        self.y = 0
        self.autowrap = True

    # ------------------------------------------------------------------ 输出
    def feed(self, text: str) -> None:
        index = 0
        while index < len(text):
            if text[index] == "\x1b":
                match = _CSI_RE.match(text, index)
                if match is None:
                    index += 1
                    continue
                self._csi(match.group(1), match.group(2))
                index = match.end()
                continue
            self._put(text[index])
            index += 1

    def _csi(self, params: str, final: str) -> None:
        if params.startswith("?"):
            if params == "?7":
                self.autowrap = final == "h"
            return
        numbers = [int(p) for p in params.split(";") if p.isdigit()]
        if final == "H" or final == "f":
            row = numbers[0] if len(numbers) > 0 and numbers[0] else 1
            col = numbers[1] if len(numbers) > 1 and numbers[1] else 1
            self.y = min(max(row - 1, 0), self.height - 1)
            self.x = min(max(col - 1, 0), self.width - 1)
        elif final == "C":
            step = numbers[0] if numbers else 1
            self.x = min(self.x + step, self.width - 1)
        elif final == "J":
            if numbers and numbers[0] == 2:
                self.grid = [[" "] * self.width for _ in range(self.height)]

    def _put(self, ch: str) -> None:
        from escurses.unicode_utils import codepoint_width

        width = codepoint_width(ch)
        if width == 0:
            # 组合标记：附着到前一格
            target = max(0, self.x - 1)
            row = self.grid[self.y]
            if row[target] is None:
                target = max(0, target - 1)
            row[target] = row[target] + ch
            return

        row = self.grid[self.y]
        if (
            row[self.x] is None
            and self.x > 0
            and row[self.x - 1] is not None
            and codepoint_width(row[self.x - 1]) == 2
        ):
            # 覆盖了宽字符的右半格：左半格必须被抹掉，否则会留下半个字。
            # 注意只在左半格"确实还是宽字符"时才抹，否则会误删刚写好的普通字符。
            row[self.x - 1] = " "
        row[self.x] = ch
        if width == 2 and self.x + 1 < self.width:
            row[self.x + 1] = None
        self.x += width
        if self.x >= self.width:
            if self.autowrap:
                self.x = 0
                self.y = min(self.y + 1, self.height - 1)
            else:
                self.x = self.width - 1

    # ------------------------------------------------------------------ 视图
    def lines(self) -> list:
        result = []
        for row in self.grid:
            result.append("".join(cell for cell in row if cell is not None).rstrip())
        return result


class TestFidelity(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = FakeStream(tty=True)
        self.terminal = Terminal(self.stream, force_ansi=True)
        self.terminal.setup(clear_on_enter=False)
        self.stream.take()

    def build(self, width: int = 20, height: int = 6):
        screen = Screen(width, height)
        renderer = Renderer(self.terminal, screen)
        virtual = VirtualTerminal(width, height)
        return screen, renderer, virtual

    def present_and_compare(self, screen, renderer, virtual) -> None:
        renderer.present()
        virtual.feed(self.stream.take())
        self.assertEqual(
            virtual.lines(),
            screen.lines(rstrip=True),
            "虚拟终端内容与缓冲区不一致（残影 / 错位 / 半字覆盖）",
        )

    def test_ascii_first_frame(self) -> None:
        screen, renderer, virtual = self.build()
        screen.text(1, 1, "Hello, EScurses")
        screen.text(3, 3, "second line")
        self.present_and_compare(screen, renderer, virtual)

    def test_scattered_changes(self) -> None:
        screen, renderer, virtual = self.build()
        screen.text(1, 1, "ABCDEFGHIJ")
        screen.text(1, 2, "KLMNOPQRST")
        self.present_and_compare(screen, renderer, virtual)

        screen.set(2, 1, "x")
        screen.set(9, 2, "y")
        self.present_and_compare(screen, renderer, virtual)

    def test_clearing_a_cell_leaves_no_ghost(self) -> None:
        screen, renderer, virtual = self.build()
        screen.text(1, 1, "GHOST")
        self.present_and_compare(screen, renderer, virtual)

        screen.set(1, 1, " ")
        screen.set(2, 1, " ")
        self.present_and_compare(screen, renderer, virtual)

    def test_chinese_text(self) -> None:
        screen, renderer, virtual = self.build()
        screen.text(1, 1, "你好，世界")
        screen.text(2, 3, "中文混排 abc")
        self.present_and_compare(screen, renderer, virtual)

    def test_chinese_replacement(self) -> None:
        screen, renderer, virtual = self.build()
        screen.text(1, 1, "中文测试")
        self.present_and_compare(screen, renderer, virtual)

        screen.set(1, 1, "汉")
        self.present_and_compare(screen, renderer, virtual)

    def test_wide_to_narrow(self) -> None:
        screen, renderer, virtual = self.build()
        screen.text(1, 1, "中A中B")
        self.present_and_compare(screen, renderer, virtual)

        screen.set(1, 1, "X")
        self.present_and_compare(screen, renderer, virtual)

    def test_narrow_to_wide(self) -> None:
        screen, renderer, virtual = self.build()
        screen.text(1, 1, "ABCDEF")
        self.present_and_compare(screen, renderer, virtual)

        screen.set(2, 1, "中")
        self.present_and_compare(screen, renderer, virtual)

    def test_clear_then_redraw(self) -> None:
        screen, renderer, virtual = self.build()
        screen.text(1, 1, "OLD CONTENT")
        self.present_and_compare(screen, renderer, virtual)

        screen.clear()
        screen.text(1, 1, "NEW")
        self.present_and_compare(screen, renderer, virtual)

    def test_full_width_row(self) -> None:
        screen, renderer, virtual = self.build(10, 3)
        screen.text(1, 1, "0123456789")
        screen.text(1, 2, "abcdefghij")
        self.present_and_compare(screen, renderer, virtual)

    def test_wide_char_at_right_edge(self) -> None:
        screen, renderer, virtual = self.build(9, 2)
        screen.text(1, 1, "1234567中")  # 中文占 8-9 列，刚好放下
        self.present_and_compare(screen, renderer, virtual)

    def test_many_frames_stay_consistent(self) -> None:
        """连续 30 帧随机修改，每帧都要与虚拟终端一致。"""
        import random

        random.seed(20261101)
        screen, renderer, virtual = self.build(24, 8)
        alphabet = "abcdefghij中文测试01"
        for frame in range(30):
            for _ in range(5):
                x = random.randint(1, 24)
                y = random.randint(1, 8)
                screen.set(x, y, random.choice(alphabet))
            self.present_and_compare(screen, renderer, virtual)

    def test_resize_keeps_consistency(self) -> None:
        screen, renderer, virtual = self.build(10, 3)
        screen.text(1, 1, "abc")
        self.present_and_compare(screen, renderer, virtual)

        screen.resize(14, 4)
        virtual = VirtualTerminal(14, 4)
        screen.text(10, 4, "tail")
        self.present_and_compare(screen, renderer, virtual)


if __name__ == "__main__":
    unittest.main()
