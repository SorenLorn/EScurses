"""EScurses 基础示例：用现代 API 做一个可移动的字符。

运行::

    python examples/hello_game.py

按键：方向键移动，Q 退出。

这个示例刻意做成"每帧全量重绘"的立即模式，方便对照旧 API 的写法；
差异渲染会自动把它压缩成很小的输出量，不需要你手动优化。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import escurses
from escurses import Game, Key


def draw_border(game: Game, width: int, height: int) -> None:
    """画一圈 ASCII 边框（不用制表符，避免不同终端宽度差异）。"""
    game.draw_text(1, 1, "+" + "-" * (width - 2) + "+")
    for y in range(2, height):
        game.draw_char(1, y, "|")
        game.draw_char(width, y, "|")
    game.draw_text(1, height, "+" + "-" * (width - 2) + "+")


def main() -> int:
    if not escurses.is_interactive():
        print("此示例需要交互式终端，请在真实终端里运行。")
        return 1

    with Game(fps=60) as game:
        width, height = game.size
        if width < 20 or height < 8:
            print("终端太小（至少需要 20x8）")
            return 1

        game.on_resize = lambda w, h: game.clear()

        x, y = width // 2, height // 2
        steps = 0

        while game.is_running:
            game.frame_start()

            for event in game.poll_keys():
                if event.key is Key.CHAR and event.char in ("q", "Q"):
                    game.quit()
                elif event.key is Key.UP:
                    y = max(2, y - 1)
                elif event.key is Key.DOWN:
                    y = min(height - 1, y + 1)
                elif event.key is Key.LEFT:
                    x = max(2, x - 1)
                elif event.key is Key.RIGHT:
                    x = min(width - 1, x + 1)

            width, height = game.size
            game.clear()
            draw_border(game, width, height)

            title = " EScurses 演示 "
            game.draw_text(max(2, (width - escurses.text_width(title)) // 2), 1, title)
            game.draw_char(x, y, "@")

            stats = game.frame_stats
            game.draw_text(2, height, "FPS {:.1f}  帧数 {}  步骤 {}".format(
                stats.fps, stats.frames, steps))
            game.draw_text(2, height - 1, "方向键移动，Q 退出   ")

            # 中文宽度演示：整行按显示宽度居中，不会因为 len() 而错位
            demo = "中文宽度测试：全角ａｂｃ与半角abc"
            game.draw_text(
                max(2, (width - escurses.text_width(demo)) // 2), 3, demo
            )

            game.present()
            steps += 1
            game.sleep_for_frame()

        game.wait_for_key("按任意键结束…", timeout=3.0)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
