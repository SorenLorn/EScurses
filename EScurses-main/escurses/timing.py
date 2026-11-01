"""帧率控制与帧时间统计。

旧实现的两个致命错误（审计 FPS-01/02/03）在这里被彻底替换：

- 旧：``sleep(frame_time - elapsed - 0.01)`` 每帧少睡 10ms，再用
  ``while perf_counter() - start < 0.01`` 把时间"补到帧开始后 10ms"，
  结果是帧率与参数几乎无关，并烧掉一个 CPU 核。
- 新：用 **deadline 累加器**（``deadline += period``）消除累积漂移，
  等待量 = ``deadline - now``，纯 ``time.sleep``，不忙等。

计时一律使用 ``time.perf_counter()``（单调时钟），不受系统时间调整影响。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .config import MAX_FPS, MIN_FPS
from .exceptions import InvalidConfigError

__all__ = ["FrameStats", "Clock"]


@dataclass(frozen=True)
class FrameStats:
    """帧统计快照。"""

    frames: int = 0
    last_dt: float = 0.0
    avg_dt: float = 0.0
    max_dt: float = 0.0
    overruns: int = 0

    @property
    def fps(self) -> float:
        """按平均帧长换算的实际 FPS（未跑满一帧时为 0）。"""
        return 1.0 / self.avg_dt if self.avg_dt > 0 else 0.0


class Clock:
    """帧同步时钟。

    Args:
        fps: 目标帧率，必须落在 ``0.1..1000``。
        spin: 末尾自旋等待的秒数上限。默认 ``0``（纯 ``sleep``，不忙等）。
            某些平台的 ``sleep`` 精度不足时，可以设置 ``0.001`` 左右换取稳定性。

    Example:
        >>> clock = Clock(60)
        >>> _ = clock.start()          # 帧开始
        >>> ...                        # 绘制、逻辑
        >>> _ = clock.sleep()          # 睡到下一帧
    """

    def __init__(self, fps: float = 60.0, *, spin: float = 0.0) -> None:
        self._fps = self._check_fps(fps)
        self.spin = max(0.0, float(spin))
        self._next_deadline: Optional[float] = None
        self._last_start: Optional[float] = None
        self._frames = 0
        self._total_dt = 0.0
        self._last_dt = 0.0
        self._max_dt = 0.0
        self._overruns = 0

    # ------------------------------------------------------------------ 配置

    @staticmethod
    def _check_fps(fps: float) -> float:
        if isinstance(fps, bool) or not isinstance(fps, (int, float)):
            raise InvalidConfigError("fps 必须是数字，收到 {!r}".format(fps))
        value = float(fps)
        if not (MIN_FPS <= value <= MAX_FPS):
            raise InvalidConfigError(
                "fps 必须在 {}..{} 之间，收到 {!r}".format(MIN_FPS, MAX_FPS, fps)
            )
        return value

    @property
    def fps(self) -> float:
        """目标帧率。"""
        return self._fps

    @fps.setter
    def fps(self, value: float) -> None:
        new_value = self._check_fps(value)
        if new_value != self._fps:
            self._fps = new_value
            # 帧长变了，重新对齐，避免用旧 deadline 追赶。
            self._next_deadline = None

    @property
    def period(self) -> float:
        """目标帧间隔（秒）。"""
        return 1.0 / self._fps

    # ------------------------------------------------------------------ 帧

    def start(self) -> float:
        """标记帧开始，返回当前单调时间。"""
        now = time.perf_counter()
        period = self.period
        if self._next_deadline is None or self._last_start is None:
            self._next_deadline = now + period
        else:
            self._next_deadline += period
            if self._next_deadline < now - period:
                # 已经落后超过一帧（比如窗口被拖动、断点调试）。
                # 不做"追帧爆发"，直接从现在重新对齐。
                self._next_deadline = now
        self._last_start = now
        return now

    def sleep(self) -> float:
        """睡到下一个帧边界，返回本帧实际耗时（秒）。

        若本帧耗时已经超过目标帧间隔，则不再等待，并记一次 ``overrun``。
        """
        now = time.perf_counter()
        if self._last_start is None:
            # 没有显式 start()，视为"从此刻开始一帧"。
            self.start()
            now = time.perf_counter()

        deadline = self._next_deadline
        if deadline is not None:
            remaining = deadline - now
            if remaining > 0:
                self._wait(remaining)

        dt = time.perf_counter() - self._last_start
        self._last_dt = dt
        self._total_dt += dt
        self._frames += 1
        if dt > self._max_dt:
            self._max_dt = dt
        if dt > self.period * 1.05:
            self._overruns += 1
        return dt

    def _wait(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self.spin <= 0 or seconds <= self.spin:
            if self.spin > 0:
                end = time.perf_counter() + seconds
                while time.perf_counter() < end:
                    pass
            else:
                time.sleep(seconds)
            return
        time.sleep(seconds - self.spin)
        end = time.perf_counter() + self.spin
        while time.perf_counter() < end:
            pass

    # ------------------------------------------------------------------ 统计

    @property
    def dt(self) -> float:
        """上一帧的实际耗时（秒）。"""
        return self._last_dt

    @property
    def stats(self) -> FrameStats:
        """帧统计快照。"""
        avg = self._total_dt / self._frames if self._frames else 0.0
        return FrameStats(
            frames=self._frames,
            last_dt=self._last_dt,
            avg_dt=avg,
            max_dt=self._max_dt,
            overruns=self._overruns,
        )

    def reset(self) -> None:
        """重置计时与统计（不改变目标帧率）。"""
        self._next_deadline = None
        self._last_start = None
        self._frames = 0
        self._total_dt = 0.0
        self._last_dt = 0.0
        self._max_dt = 0.0
        self._overruns = 0

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "Clock(fps={}, frames={})".format(self._fps, self._frames)
