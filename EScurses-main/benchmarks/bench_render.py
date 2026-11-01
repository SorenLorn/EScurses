"""渲染性能基准：旧实现 vs 新实现。

对比维度：
1. 每帧绘制大量字符（整屏）
2. 每帧只改变少量字符（1% / 5%）
3. 连续多帧画面完全不变
4. 终端输出字节数
5. 光标定位次数
6. 每帧渲染耗时（Python 侧）

**重要说明**：这里的耗时只统计"构造并写出字符串"的 Python 开销，
输出目标是内存缓冲（``io.StringIO``），不是真实终端。
真实终端还要解析同样多的转义序列，因此实际差距只会更大，
但本脚本不会声称任何"真实终端下的加速比"。

用法::

    python benchmarks/bench_render.py
"""

from __future__ import annotations

import io
import os
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _legacy_escurses as legacy  # noqa: E402

from escurses.renderer import Renderer  # noqa: E402
from escurses.screen import Screen  # noqa: E402
from escurses.terminal import Terminal  # noqa: E402

WIDTH = 80
HEIGHT = 24
ITERATIONS = 15

_POSITION_RE = re.compile(r"\x1b\[[0-9;]*[HfC]")


class Sink(io.StringIO):
    """内存输出目标。"""

    encoding = "utf-8"

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        raise OSError("no fd")


def make_rows() -> list:
    """构造一屏内容（纯 ASCII，保证两种实现的内容完全一致）。"""
    rows = []
    for y in range(HEIGHT):
        rows.append("".join(chr(65 + (x * 3 + y * 7) % 26) for x in range(WIDTH - 1)))
    return rows


def apply_changes(rows: list, ratio: float) -> None:
    """按比例修改单元格内容（模拟动态画面）。"""
    total = WIDTH * HEIGHT
    count = int(total * ratio)
    for index in range(count):
        x = (index * 13) % (WIDTH - 1)
        y = (index * 7) % HEIGHT
        rows[y] = rows[y][:x] + "#" + rows[y][x + 1:]


# ----------------------------------------------------------------- 旧实现


def legacy_render(base_rows: list) -> tuple:
    """用旧实现渲染一帧，返回 (字节数, 定位次数, 耗时)。"""
    rows = list(base_rows)
    sink = Sink()
    old_stdout = sys.stdout
    sys.stdout = sink
    try:
        legacy.initGame()
        for y, row in enumerate(rows, start=1):
            for x, ch in enumerate(row, start=1):
                legacy.drawChar(x, y, ch)
        sink.seek(0)
        sink.truncate(0)
        import time

        started = time.perf_counter()
        legacy.refresh()
        elapsed = time.perf_counter() - started
    finally:
        sys.stdout = old_stdout
    payload = sink.getvalue()
    return len(payload.encode("utf-8")), len(_POSITION_RE.findall(payload)), elapsed


# ----------------------------------------------------------------- 新实现


def new_render_full(base_rows: list) -> tuple:
    rows = list(base_rows)
    sink = Sink()
    terminal = Terminal(sink, force_ansi=True)
    screen = Screen(WIDTH, HEIGHT)
    renderer = Renderer(terminal, screen)
    for y, row in enumerate(rows, start=1):
        screen.text(1, y, row)
    stats = renderer.present()
    payload = sink.getvalue()
    return stats.bytes_written, len(_POSITION_RE.findall(payload)), stats.elapsed


def new_render_diff(base_rows: list, ratio: float) -> tuple:
    """先渲染一屏，再改 ratio 比例的格子，只统计第二次（差异帧）。"""
    rows = list(base_rows)
    sink = Sink()
    terminal = Terminal(sink, force_ansi=True)
    screen = Screen(WIDTH, HEIGHT)
    renderer = Renderer(terminal, screen)
    for y, row in enumerate(rows, start=1):
        screen.text(1, y, row)
    renderer.present()

    apply_changes(rows, ratio)
    for y, row in enumerate(rows, start=1):
        screen.text(1, y, row)
    sink.seek(0)
    sink.truncate(0)
    stats = renderer.present()
    payload = sink.getvalue()
    return stats.bytes_written, len(_POSITION_RE.findall(payload)), stats.elapsed


def new_render_idle(base_rows: list) -> tuple:
    rows = list(base_rows)
    sink = Sink()
    terminal = Terminal(sink, force_ansi=True)
    screen = Screen(WIDTH, HEIGHT)
    renderer = Renderer(terminal, screen)
    for y, row in enumerate(rows, start=1):
        screen.text(1, y, row)
    renderer.present()
    sink.seek(0)
    sink.truncate(0)
    stats = renderer.present()
    payload = sink.getvalue()
    return stats.bytes_written, len(_POSITION_RE.findall(payload)), stats.elapsed


# ----------------------------------------------------------------- 执行


def measure(func, *args) -> tuple:
    """重复执行取中位数，降低噪声。"""
    results = [func(*args) for _ in range(ITERATIONS)]
    byte_counts = [r[0] for r in results]
    cursor_counts = [r[1] for r in results]
    times = [r[2] for r in results]
    return statistics.median(byte_counts), statistics.median(cursor_counts), statistics.median(times)


def main() -> None:
    # 旧实现依赖 os.get_terminal_size，这里固定成 80x24
    os.get_terminal_size = lambda *a, **k: os.terminal_size((WIDTH, HEIGHT))

    base_rows = make_rows()

    scenarios = [
        ("整屏首帧（100% 内容）", legacy_render, new_render_full, (base_rows,)),
        ("改变 1% 格子", legacy_render, new_render_diff, (base_rows, 0.01)),
        ("改变 5% 格子", legacy_render, new_render_diff, (base_rows, 0.05)),
        ("画面完全不变", legacy_render, new_render_idle, (base_rows,)),
    ]

    print("=" * 100)
    print("EScurses 渲染基准  |  终端 80x24  |  Python {}  |  {} 次取中位数".format(
        sys.version.split()[0], ITERATIONS))
    print("输出目标：内存 StringIO（非真实终端），因此耗时不包含终端解析成本")
    print("=" * 100)
    header = "{:<22} {:>12} {:>12} {:>9} {:>10} {:>10} {:>10}".format(
        "场景", "旧-字节", "新-字节", "字节降幅", "旧-定位", "新-定位", "旧/新耗时(ms)")
    print(header)
    print("-" * 100)

    for name, legacy_func, new_func, args in scenarios:
        old_bytes, old_cursor, old_time = measure(legacy_func, base_rows)
        # 新实现每次都要重建 Screen，避免状态污染
        new_bytes, new_cursor, new_time = measure(new_func, *args)
        reduction = (1 - new_bytes / old_bytes) * 100 if old_bytes else 0.0
        print("{:<22} {:>12,} {:>12,} {:>8.1f}% {:>10,} {:>10,} {:>6.2f}/{:.2f}".format(
            name, old_bytes, new_bytes, reduction, old_cursor, new_cursor,
            old_time * 1000, new_time * 1000))

    print("=" * 100)

    # 中文场景：旧实现会错位，仅统计新实现
    print()
    print("中文内容（仅新实现；旧实现在此场景会因宽字符不占位而错位，无法公平对比）")
    print("-" * 100)
    sink = Sink()
    terminal = Terminal(sink, force_ansi=True)
    screen = Screen(WIDTH, HEIGHT)
    renderer = Renderer(terminal, screen)
    for y in range(1, 25):
        screen.text(1, y, "第{:02d}行 ".format(y) + "中文测试" * 8)
    stats = renderer.present()
    print("首帧：{} 字节，{} 次光标定位，{:.2f} ms".format(
        stats.bytes_written, len(_POSITION_RE.findall(sink.getvalue())), stats.elapsed * 1000))
    sink.seek(0)
    sink.truncate(0)
    screen.set(1, 1, "改")
    stats = renderer.present()
    print("单字变化：{} 字节，{} 次定位，{:.3f} ms".format(
        stats.bytes_written, len(_POSITION_RE.findall(sink.getvalue())), stats.elapsed * 1000))


if __name__ == "__main__":
    main()
