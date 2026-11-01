"""游戏主控制器。

把终端、输入、屏幕、渲染、时钟粘在一起，并保证生命周期可靠：

- ``with Game() as game:`` 无论正常结束、抛异常还是 ``Ctrl+C``，都会还原终端。
- ``close()`` 幂等，重复调用不会产生额外输出或状态损坏。
- 初始化中途失败时，已经生效的终端修改会被回滚。

本模块**不调用** ``sys.exit()``。进程是否退出由调用方决定
（旧 API 的 ``exitGame()`` 保留了 ``SystemExit`` 语义，见 ``__init__.py``）。
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, TextIO, Tuple

from .config import Config, build_config, get_default_config
from .exceptions import GameStateError
from .input import Input, KeyEvent, create_backend
from .renderer import RenderStats, Renderer
from .screen import Screen
from .terminal import ColorSupport, Terminal
from .timing import Clock, FrameStats

__all__ = ["Game"]


class Game:
    """一个游戏会话。

    Args:
        width: 屏幕列数；``None`` 表示使用终端当前宽度。
        height: 屏幕行数；``None`` 表示使用终端当前高度。
        fps: 目标帧率，默认取 :class:`~escurses.config.Config`。
        strict: 严格模式。``None`` 表示沿用配置；``True`` 时越界绘制抛异常。
        alt_screen: 是否使用备用屏幕。``None`` 表示沿用配置（默认 ``True``）。
        hide_cursor: 是否隐藏光标。
        clear_on_exit: 退出时是否清屏（仅 ``alt_screen=False`` 时有意义）。
        esc_timeout: 单独 ``ESC`` 的判定窗口（秒）。
        stream: 输出流，默认 ``sys.stdout``。测试可注入。
        input_stream: 输入流，默认 ``sys.stdin``。
        config: 基础配置对象，默认取全局默认配置。
        force_ansi: 强制开/关 ANSI 输出，``None`` 表示自动判断。

    Example:
        >>> with Game() as game:                      # doctest: +SKIP
        ...     game.draw_text(1, 1, "Hello")
        ...     game.present()
        ...     game.wait_for_key()
    """

    def __init__(
        self,
        *,
        width: Optional[int] = None,
        height: Optional[int] = None,
        fps: Optional[float] = None,
        strict: Optional[bool] = None,
        alt_screen: Optional[bool] = None,
        hide_cursor: Optional[bool] = None,
        clear_on_exit: Optional[bool] = None,
        esc_timeout: Optional[float] = None,
        stream: Optional[TextIO] = None,
        input_stream: Optional[TextIO] = None,
        config: Optional[Config] = None,
        force_ansi: Optional[bool] = None,
        backend: Any = None,
    ) -> None:
        base = config or get_default_config()
        self.config = build_config(
            base,
            fps=fps,
            strict=strict,
            alt_screen=alt_screen,
            hide_cursor=hide_cursor,
            clear_on_exit=clear_on_exit,
            esc_timeout=esc_timeout,
        )
        self.terminal = Terminal(stream, force_ansi=force_ansi)
        self.input = Input(
            backend if backend is not None else create_backend(input_stream),
            esc_timeout=self.config.esc_timeout,
        )
        self.clock = Clock(self.config.fps)

        self._requested_size = (width, height)
        self._screen: Optional[Screen] = None
        self._renderer: Optional[Renderer] = None
        self._started = False
        self._running = False

        self.on_resize: Optional[Callable[[int, int], None]] = None
        """窗口尺寸变化时的回调，签名 ``callback(width, height)``。"""

    # ------------------------------------------------------------------ 生命周期

    def start(self) -> "Game":
        """进入游戏状态。重复调用无副作用。

        Raises:
            TerminalError: 终端初始化失败（已做的修改会被回滚）。
            InputError: 输入初始化失败（终端已还原）。
        """
        if self._started:
            return self
        screen = self._ensure_screen()
        self.terminal.setup(
            alt_screen=self.config.alt_screen,
            hide_cursor=self.config.hide_cursor,
            disable_autowrap=True,
            clear_on_enter=True,
        )
        try:
            self.input.open()
        except Exception:
            self.terminal.restore()
            raise
        self._started = True
        self._running = True
        self._sync_size()
        self._renderer = Renderer(self.terminal, screen)
        self.clock.reset()
        return self

    def close(self) -> None:
        """还原终端与输入。幂等，且"尽力而为"：一个步骤失败不影响其余步骤。"""
        if not self._started:
            self._running = False
            return
        try:
            self.input.close()
        finally:
            try:
                self.terminal.restore()
            finally:
                self._started = False
                self._running = False

    def __enter__(self) -> "Game":
        return self.start()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.close()
        return False

    def quit(self) -> None:
        """请求主循环结束（不会立即恢复终端，由 ``close()`` 负责）。"""
        self._running = False

    @property
    def is_running(self) -> bool:
        """是否处于运行状态。"""
        return self._running and self._started

    # ------------------------------------------------------------------ 组件

    @property
    def screen(self) -> Screen:
        """屏幕缓冲区。未 ``start()`` 时也可用（用于预渲染）。"""
        return self._ensure_screen()

    @property
    def renderer(self) -> Renderer:
        """渲染器。未 ``start()`` 时抛 :class:`GameStateError`。"""
        if self._renderer is None:
            raise GameStateError("渲染器尚未创建，请先调用 start()")
        return self._renderer

    @property
    def size(self) -> Tuple[int, int]:
        """``(width, height)``。"""
        return self.screen.size

    @property
    def color_support(self) -> ColorSupport:
        """终端颜色能力（Iteration A 不输出颜色，仅上报）。"""
        return self.terminal.color_support

    @property
    def strict(self) -> bool:
        """严格模式开关。"""
        return self.screen.strict

    @strict.setter
    def strict(self, value: bool) -> None:
        self.screen.strict = bool(value)

    @property
    def fps(self) -> float:
        """目标帧率。"""
        return self.clock.fps

    @fps.setter
    def fps(self, value: float) -> None:
        self.clock.fps = value

    @property
    def dt(self) -> float:
        """上一帧实际耗时（秒）。"""
        return self.clock.dt

    @property
    def frame_stats(self) -> FrameStats:
        """帧统计快照。"""
        return self.clock.stats

    # ------------------------------------------------------------------ 绘制

    def draw_char(self, x: int, y: int, char: str) -> bool:
        """在 ``(x, y)`` 写入一个字符（1-based）。

        与旧版 ``drawChar`` 的差异：多字符会被依次写入，而不是挤进一格。
        """
        return self.screen.set(x, y, char)

    def draw_text(self, x: int, y: int, text: str) -> int:
        """从 ``(x, y)`` 开始写入一段文本，返回写入的列数。"""
        return self.screen.text(x, y, text)

    def fill(self, x1: int, y1: int, x2: int, y2: int, char: str = " ") -> int:
        """用 ``char`` 填充矩形区域。"""
        return self.screen.fill(x1, y1, x2, y2, char)

    def clear(self) -> None:
        """清空屏幕内容（下一次 ``present()`` 才真正输出）。"""
        self.screen.clear()

    def invalidate(self) -> None:
        """强制下一帧整屏重绘。"""
        self.screen.invalidate()

    # ------------------------------------------------------------------ 渲染

    def present(self, *, force: bool = False) -> RenderStats:
        """提交当前帧。"""
        if not self._started:
            raise GameStateError("请在 start() 之后调用 present()")
        self._sync_size()
        return self.renderer.present(force=force)

    def refresh(self) -> RenderStats:
        """``present()`` 的别名，对应旧版 ``refresh()``。"""
        return self.present()

    # ------------------------------------------------------------------ 输入

    def poll_keys(self) -> List[KeyEvent]:
        """取出当前所有待处理按键事件（非阻塞）。"""
        if not self._started:
            return []
        return self.input.poll()

    def read_key(self, timeout: Optional[float] = None) -> Optional[KeyEvent]:
        """读取一个按键事件，超时返回 ``None``。"""
        if not self._started:
            raise GameStateError("请在 start() 之后读取按键")
        return self.input.read_key(timeout)

    def getch(self, nodelay: bool = True) -> Optional[str]:
        """兼容旧版 ``getch``：返回字符串而不是事件对象。"""
        if not self._started:
            raise GameStateError("请在 start() 之后读取按键")
        return self.input.getch(nodelay)

    def wait_for_key(
        self,
        prompt: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Optional[KeyEvent]:
        """在屏幕底部显示提示并等待按键（常用于"按任意键结束"）。"""
        if prompt:
            screen = self.screen
            self.draw_text(1, screen.height, prompt)
            self.present()
        return self.read_key(timeout)

    # ------------------------------------------------------------------ 时钟

    def frame_start(self) -> float:
        """标记帧开始，对应旧版 ``frameStart()``。"""
        return self.clock.start()

    def sleep_for_frame(self) -> float:
        """睡到下一帧边界，对应旧版 ``sleepForFrame()``。"""
        return self.clock.sleep()

    def run(
        self,
        step: Callable[["Game"], None],
        *,
        fps: Optional[float] = None,
        max_frames: Optional[int] = None,
    ) -> int:
        """跑主循环。

        Args:
            step: 每帧调用的回调，签名 ``step(game)``。在其中绘制与读键。
            fps: 覆盖目标帧率。
            max_frames: 最多跑多少帧（测试与演示用）。

        Returns:
            实际执行的帧数。
        """
        if fps is not None:
            self.fps = fps
        if not self._started:
            self.start()

        frames = 0
        while self._running:
            self.clock.start()
            step(self)
            self.present()
            self.clock.sleep()
            frames += 1
            if max_frames is not None and frames >= max_frames:
                break
        return frames

    # ------------------------------------------------------------------ 内部

    def _ensure_screen(self) -> Screen:
        if self._screen is None:
            width, height = self._target_size()
            self._screen = Screen(width, height, strict=self.config.strict)
        return self._screen

    def _target_size(self) -> Tuple[int, int]:
        requested_width, requested_height = self._requested_size
        if requested_width and requested_height:
            return max(1, int(requested_width)), max(1, int(requested_height))
        terminal_width, terminal_height = self.terminal.size()
        return (
            max(1, int(requested_width or terminal_width)),
            max(1, int(requested_height or terminal_height)),
        )

    def _sync_size(self) -> bool:
        """检测终端尺寸变化并重建缓冲区。"""
        if self._screen is None:
            return False
        width, height = self._target_size()
        if (width, height) == self._screen.size:
            return False
        self._screen.resize(width, height)
        self._screen.invalidate()
        if self._renderer is not None:
            self._renderer.invalidate()
        if self.on_resize is not None:
            self.on_resize(width, height)
        return True

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "Game(size={}x{}, fps={}, started={})".format(
            *self.size, self.fps, self._started
        )
