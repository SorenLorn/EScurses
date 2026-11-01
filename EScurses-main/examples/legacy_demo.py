"""旧版 API 示例：README 里的老写法必须原样可运行。

运行::

    python examples/legacy_demo.py

这个文件证明"旧项目平滑迁移"：函数名、参数顺序、1-based 坐标、
``exitGame()`` 会结束进程——全部保持不变。

注意：旧写法每帧调用 ``clear()``，会让差异渲染退化成整屏重绘。
新代码请不要每帧 clear（见 examples/hello_game.py）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import escurses


def main() -> int:
    if not escurses.is_interactive():
        print("此示例需要交互式终端，请在真实终端里运行。")
        return 1

    escurses.initGame()
    escurses.cursorEcho(False)

    width, height = escurses.getTerminalSize()
    x, y = width // 2, height // 2

    try:
        while True:
            escurses.frameStart()

            key = escurses.getch()
            if key in ("q", "Q"):
                break
            # 方向键在新版会返回原始序列，这里只演示最朴素的按键
            if key == "a":
                x = max(1, x - 1)
            elif key == "d":
                x = min(width, x + 1)
            elif key == "w":
                y = max(1, y - 1)
            elif key == "s":
                y = min(height, y + 1)

            escurses.clear()
            escurses.drawChar(1, 1, "旧 API 演示  WASD 移动  Q 退出")
            escurses.drawChar(x, y, "@")
            escurses.refresh()
            escurses.sleepForFrame(30)
    except KeyboardInterrupt:
        pass
    finally:
        escurses.exitGame(exit_process=False)

    print("已恢复正常终端状态。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
