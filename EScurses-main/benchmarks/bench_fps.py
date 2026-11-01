"""帧率控制基准：旧实现 vs 新实现。

对比"目标 FPS → 实际 FPS"和"等待期间的 CPU 占用"。

- 旧实现：``frameStart()`` + ``sleepForFrame(fps)``
- 新实现：``Clock(fps).start()`` + ``clock.sleep()``

CPU 时间用 ``time.process_time()``（进程占用的 CPU 秒数）统计，
与墙钟时间的比值即可近似"占用几个核"。

用法::

    python benchmarks/bench_fps.py
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _legacy_escurses as legacy  # noqa: E402

from escurses.timing import Clock  # noqa: E402

DURATION = 0.4  # 每个目标帧率跑约 0.4 秒


def run_legacy(fps: float, frames: int) -> tuple:
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    for _ in range(frames):
        legacy.frameStart()
        legacy.sleepForFrame(fps)
    wall = time.perf_counter() - wall_start
    cpu = time.process_time() - cpu_start
    return wall, cpu


def run_new(fps: float, frames: int) -> tuple:
    clock = Clock(fps)
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    for _ in range(frames):
        clock.start()
        clock.sleep()
    wall = time.perf_counter() - wall_start
    cpu = time.process_time() - cpu_start
    return wall, cpu


def main() -> None:
    print("=" * 88)
    print("EScurses 帧率控制基准  |  Python {}  |  每个目标跑约 {:.1f}s".format(
        sys.version.split()[0], DURATION))
    print("说明：只测时钟本身（不做绘制），用于隔离帧率控制的精度与 CPU 占用")
    print("=" * 88)
    print("{:<8} {:>12} {:>12} {:>12} {:>12}".format(
        "目标 FPS", "旧-实际FPS", "新-实际FPS", "旧-CPU占比", "新-CPU占比"))
    print("-" * 88)

    for fps in (30, 60, 120, 240):
        frames = max(6, int(round(DURATION * fps)))
        # 每个配置取 3 次，取中位数，减少系统噪声
        old_runs = [run_legacy(fps, frames) for _ in range(3)]
        new_runs = [run_new(fps, frames) for _ in range(3)]
        old_wall = statistics.median(r[0] for r in old_runs)
        old_cpu = statistics.median(r[1] for r in old_runs)
        new_wall = statistics.median(r[0] for r in new_runs)
        new_cpu = statistics.median(r[1] for r in new_runs)
        print("{:<8} {:>12.2f} {:>12.2f} {:>11.0f}% {:>11.0f}%".format(
            fps,
            frames / old_wall,
            frames / new_wall,
            old_cpu / old_wall * 100,
            new_cpu / new_wall * 100,
        ))

    print("=" * 88)
    print("误差说明：Windows 的睡眠精度约 1~15ms，实际 FPS 会有 ±2% 抖动；")
    print("          CPU 占比为进程 CPU 时间 / 墙钟时间，单核满载约 100%。")


if __name__ == "__main__":
    main()
