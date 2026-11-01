"""帧率控制测试。

重点是"真的按目标帧率走"和"不忙等"——这两点是审计中 FPS-01/02 的回归测试。
"""

from __future__ import annotations

import time
import unittest

from escurses.exceptions import InvalidConfigError
from escurses.timing import Clock


class TestValidation(unittest.TestCase):
    def test_zero_fps_rejected(self) -> None:
        with self.assertRaises(InvalidConfigError):
            Clock(0)

    def test_negative_fps_rejected(self) -> None:
        with self.assertRaises(InvalidConfigError):
            Clock(-30)

    def test_non_numeric_fps_rejected(self) -> None:
        with self.assertRaises(InvalidConfigError):
            Clock("60")  # type: ignore[arg-type]

    def test_too_large_fps_rejected(self) -> None:
        with self.assertRaises(InvalidConfigError):
            Clock(5000)

    def test_bool_rejected(self) -> None:
        with self.assertRaises(InvalidConfigError):
            Clock(True)  # type: ignore[arg-type]

    def test_period(self) -> None:
        self.assertAlmostEqual(Clock(50).period, 0.02, places=6)


class TestFpsAccuracy(unittest.TestCase):
    def _run_frames(self, fps: float, frames: int) -> float:
        clock = Clock(fps)
        started = time.perf_counter()
        for _ in range(frames):
            clock.start()
            clock.sleep()
        return time.perf_counter() - started

    def test_20_fps_is_not_capped_at_100(self) -> None:
        """审计 FPS-01 回归：旧实现 60fps 目标实际跑 100fps。"""
        elapsed = self._run_frames(20, 5)
        # 4 个间隔 × 50ms = 200ms
        self.assertGreaterEqual(elapsed, 0.18, "帧率过快，说明没有真正限速")
        self.assertLess(elapsed, 0.45, "帧率过慢，说明睡眠过量")

    def test_60_fps(self) -> None:
        elapsed = self._run_frames(60, 6)
        expected = 5 * (1 / 60)
        self.assertGreaterEqual(elapsed, expected * 0.85)
        self.assertLess(elapsed, expected * 2.2)

    def test_high_fps_still_respects_target(self) -> None:
        """120fps 目标不能被钉死在 100fps（旧实现的上限）。"""
        elapsed = self._run_frames(120, 6)
        # 6 个 8.33ms 周期 ≈ 50ms；若被限速到 100fps 则是 60ms。
        self.assertLess(elapsed, 6 * (1 / 100) * 0.95)


class TestNoBusyWait(unittest.TestCase):
    def test_cpu_time_is_much_lower_than_wall_time(self) -> None:
        """忙等会吃掉整个 CPU 核；正常睡眠的 CPU 时间应远小于墙钟时间。"""
        clock = Clock(50)
        wall_start = time.perf_counter()
        cpu_start = time.process_time()
        for _ in range(6):
            clock.start()
            clock.sleep()
        wall = time.perf_counter() - wall_start
        cpu = time.process_time() - cpu_start
        self.assertGreater(wall, 0.08)
        self.assertLess(cpu, wall * 0.5, "CPU 占用过高，疑似忙等待")


class TestOverrun(unittest.TestCase):
    def test_slow_frame_is_counted(self) -> None:
        clock = Clock(100)  # 10ms/帧
        clock.start()
        time.sleep(0.02)  # 故意超时
        dt = clock.sleep()
        self.assertGreater(dt, 0.015)
        self.assertEqual(clock.stats.overruns, 1)

    def test_no_unbounded_catch_up_after_overrun(self) -> None:
        """超时后不允许"补帧爆发"：不能在几毫秒内连续刷完一堆积压帧。

        允许落后一帧立即执行（不为已经过去的时间额外补睡），
        但随后必须重新对齐到目标周期，而不是一路狂飙。
        """
        clock = Clock(50)  # 20ms/帧
        clock.start()
        time.sleep(0.05)  # 严重超时，落后约 1.5 个周期
        clock.sleep()

        started = time.perf_counter()
        for _ in range(4):
            clock.start()
            clock.sleep()
        elapsed = time.perf_counter() - started

        self.assertGreater(elapsed, 0.03, "帧率失控：积压帧被瞬间刷完")
        self.assertLess(elapsed, 0.15, "补偿过度：为了追帧反而拖慢了")


class TestStats(unittest.TestCase):
    def test_frame_count(self) -> None:
        clock = Clock(200)
        for _ in range(5):
            clock.start()
            clock.sleep()
        self.assertEqual(clock.stats.frames, 5)

    def test_dt_is_recorded(self) -> None:
        clock = Clock(200)
        clock.start()
        clock.sleep()
        self.assertGreater(clock.dt, 0)
        self.assertEqual(clock.stats.last_dt, clock.dt)

    def test_average_and_max(self) -> None:
        clock = Clock(200)
        for _ in range(3):
            clock.start()
            clock.sleep()
        stats = clock.stats
        self.assertGreater(stats.avg_dt, 0)
        self.assertGreaterEqual(stats.max_dt, stats.avg_dt)
        self.assertGreater(stats.fps, 0)

    def test_reset(self) -> None:
        clock = Clock(200)
        clock.start()
        clock.sleep()
        clock.reset()
        self.assertEqual(clock.stats.frames, 0)
        self.assertEqual(clock.dt, 0)

    def test_zero_state(self) -> None:
        self.assertEqual(Clock(60).stats.fps, 0)
        self.assertEqual(Clock(60).stats.frames, 0)


class TestApiContract(unittest.TestCase):
    def test_sleep_without_start_is_safe(self) -> None:
        clock = Clock(1000)
        self.assertGreaterEqual(clock.sleep(), 0)

    def test_start_returns_timestamp(self) -> None:
        self.assertGreater(Clock(60).start(), 0)

    def test_fps_change_resets_schedule(self) -> None:
        clock = Clock(10)
        clock.start()
        clock.fps = 100
        self.assertEqual(clock.fps, 100)
        self.assertAlmostEqual(clock.period, 0.01, places=6)

    def test_fps_setter_validates(self) -> None:
        clock = Clock(60)
        with self.assertRaises(InvalidConfigError):
            clock.fps = 0


if __name__ == "__main__":
    unittest.main()
