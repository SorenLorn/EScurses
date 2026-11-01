"""EScurses —— 轻量级跨平台终端游戏库。

两种使用方式：

**现代 API（推荐）**::

    from escurses import Game, Key

    with Game(fps=30) as game:
        game.draw_text(1, 1, "按 Q 退出")
        game.present()
        while True:
            game.frame_start()
            for event in game.poll_keys():
                if event.key is Key.CHAR and event.char == "q":
                    game.quit()
            game.present()
            game.sleep_for_frame()
            if not game.is_running:
                break

**旧版 API（保持兼容）**::

    import escurses

    escurses.initGame()
    escurses.drawChar(5, 3, "P")
    escurses.refresh()
    escurses.exitGame()

旧函数名、参数顺序与含义全部保留，内部实现改为调用 :class:`Game`。
行为上仅有 6 处明确改进，详见 README 的"从旧版本迁移"。
"""

from __future__ import annotations

import sys
from typing import Any, Optional, Tuple

from .buffer import BLANK, CONTINUATION, Buffer, Cell, DoubleBuffer
from .config import Config, get_default_config, set_default_config
from .exceptions import (
    EscursesError,
    GameStateError,
    InputError,
    InvalidCharError,
    InvalidConfigError,
    OutOfBoundsError,
    TerminalError,
)
from .game import Game
from .input import Input, Key, KeyEvent
from .renderer import RenderStats, Renderer
from .screen import Screen
from .terminal import ColorSupport, Terminal, detect_color_support
from .timing import Clock, FrameStats
from .unicode_utils import cluster_width, codepoint_width, text_width

__version__ = "0.2.0"

__all__ = [
    # 现代 API
    "Game",
    "Config",
    "Screen",
    "Buffer",
    "DoubleBuffer",
    "Cell",
    "Renderer",
    "RenderStats",
    "Terminal",
    "ColorSupport",
    "Clock",
    "FrameStats",
    "Input",
    "Key",
    "KeyEvent",
    "BLANK",
    "CONTINUATION",
    # 工具
    "text_width",
    "cluster_width",
    "codepoint_width",
    "detect_color_support",
    "is_interactive",
    "configure",
    "get_config",
    "reset_config",
    # 异常
    "EscursesError",
    "InvalidConfigError",
    "InvalidCharError",
    "OutOfBoundsError",
    "TerminalError",
    "InputError",
    "GameStateError",
    # 旧 API（兼容）
    "initGame",
    "exitGame",
    "clear",
    "drawChar",
    "refresh",
    "getch",
    "getTerminalSize",
    "cursorEcho",
    "frameStart",
    "sleepForFrame",
    "linuxTerminalCheck",
    # 旧 API 的 snake_case 别名
    "init_game",
    "exit_game",
    "draw_char",
    "refresh_screen",
    "get_terminal_size",
]

# ---------------------------------------------------------------------- 全局状态

_session: Optional[Game] = None
_legacy_clock: Optional[Clock] = None
_probe_terminal: Optional[Terminal] = None


def _probe() -> Terminal:
    """用于纯查询（尺寸、TTY 判断）的终端对象，不进入任何游戏状态。"""
    global _probe_terminal
    if _probe_terminal is None:
        _probe_terminal = Terminal()
    return _probe_terminal


def _require_session() -> Game:
    if _session is None:
        raise GameStateError(
            "尚未初始化游戏，请先调用 initGame()（现代 API 请用 with Game() as game）"
        )
    return _session


# ---------------------------------------------------------------------- 配置


def configure(**kwargs: Any) -> Config:
    """修改全局默认配置，返回新配置。

    影响之后创建的会话（含 ``initGame()``）。若当前已有活动会话，
    ``strict`` 会立即生效，其余项需要重新 ``initGame()``。

    Example:
        >>> configure(strict=True)   # doctest: +SKIP
    """
    new_config = get_default_config().merged(**kwargs)
    set_default_config(new_config)
    if _session is not None and "strict" in kwargs and kwargs["strict"] is not None:
        _session.strict = bool(kwargs["strict"])
    return new_config


def get_config() -> Config:
    """返回当前全局默认配置。"""
    return get_default_config()


def reset_config() -> Config:
    """恢复出厂配置（主要供测试使用）。"""
    return set_default_config(Config())


def is_interactive() -> bool:
    """stdin 和 stdout 是否都连接着终端。

    两者都为真时，字符模式、备用屏幕、光标控制才有意义。
    """
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):  # pragma: no cover
        return False


# ---------------------------------------------------------------------- 旧 API


def initGame(**kwargs: Any) -> Game:
    """初始化游戏（旧 API）。

    与旧版的区别：会同时保存终端状态、进入备用屏幕、隐藏光标、进入字符模式。
    这些都是可以关闭的：``initGame(alt_screen=False, hide_cursor=False)``。

    Returns:
        底层的 :class:`Game` 对象（旧版返回 ``None``，此处返回值可忽略）。
    """
    global _session
    if _session is not None and _session.is_running:
        return _session  # 重复初始化是无害的空操作
    if _session is not None:
        _session.close()
    _session = Game(**kwargs)
    _session.start()
    return _session


def exitGame(exit_process: bool = True) -> None:
    """退出游戏并恢复终端（旧 API）。

    Args:
        exit_process: 是否在当前进程上抛 ``SystemExit(0)``。
            旧版行为就是"调用后进程结束"，因此默认 ``True`` 以保持兼容；
            在库/测试/交互式环境中请传 ``False``。

    Note:
        与旧版不同，**不再清屏**——清屏会抹掉玩家看到的最后一帧。
    """
    global _session
    session, _session = _session, None
    if session is not None:
        session.close()
    if exit_process:
        raise SystemExit(0)


def clear() -> None:
    """清空屏幕内容（旧 API）。下一次 ``refresh()`` 才会真正输出。"""
    _require_session().clear()


def drawChar(x: int, y: int, char: str) -> bool:
    """在 ``(x, y)`` 画一个字符（旧 API，1-based）。

    Returns:
        是否真的写入了内容。越界时返回 ``False``（严格模式抛异常），
        不会像旧版那样静默写到错误位置。
    """
    return _require_session().draw_char(x, y, char)


def refresh() -> None:
    """把缓冲区内容刷新到终端（旧 API）。"""
    _require_session().present()


def getch(nodelay: bool = True) -> Optional[str]:
    """读取一个按键（旧 API）。

    - 可打印键返回字符本身（``"q"``）。
    - 方向键返回原始序列（POSIX ``"\\\\x1b[A"``，Windows ``"\\\\xe0H"``）。
    - 无按键返回 ``None``。
    """
    return _require_session().getch(nodelay)


def getTerminalSize() -> Tuple[int, int]:
    """返回 ``(列数, 行数)``（旧 API）。

    与旧版不同：即使 stdout 被重定向也不会抛 ``OSError``，会退化为
    ``shutil.get_terminal_size()`` 或 ``(80, 24)``。
    """
    return _probe().size()


def cursorEcho(echo: bool = False) -> None:
    """显示/隐藏光标（旧 API）。

    Args:
        echo: ``False`` 隐藏（旧版默认），``True`` 显示。
    """
    terminal = _session.terminal if _session is not None else _probe()
    if echo:
        terminal.show_cursor()
    else:
        terminal.hide_cursor()


def frameStart() -> None:
    """标记帧开始（旧 API），配合 :func:`sleepForFrame` 使用。"""
    clock = _session.clock if _session is not None else _legacy_clock_obj()
    clock.start()


def sleepForFrame(fps: float) -> None:
    """睡到本帧结束（旧 API）。

    与旧版不同：真正按 ``fps`` 控制帧率，不再固定少睡 10 ms，
    也不再忙等。

    Raises:
        InvalidConfigError: ``fps`` 不在 ``0.1..1000`` 范围内。
    """
    clock = _session.clock if _session is not None else _legacy_clock_obj()
    if clock.fps != fps:
        clock.fps = fps
    clock.sleep()


def linuxTerminalCheck() -> bool:
    """是否为交互式终端（旧 API，名字保留）。

    实际上它对所有平台都有效；新代码请用 :func:`is_interactive`。
    """
    return is_interactive()


def _legacy_clock_obj() -> Clock:
    global _legacy_clock
    if _legacy_clock is None:
        _legacy_clock = Clock(get_default_config().fps)
    return _legacy_clock


# ---------------------------------------------------------------------- 别名

init_game = initGame
exit_game = exitGame
draw_char = drawChar
refresh_screen = refresh
get_terminal_size = getTerminalSize
