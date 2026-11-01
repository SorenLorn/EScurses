"""配置项。

两种使用方式，语义完全一致：

- 实例级：``Game(strict=True, fps=30)``
- 全局级：``escurses.configure(strict=True)``，影响之后创建的会话（含旧 API）

所有校验集中在 :meth:`Config.validate`，不在各个模块里重复判断。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, Optional

from .exceptions import InvalidConfigError

__all__ = ["Config", "get_default_config", "set_default_config", "reset_default_config"]

MIN_FPS = 0.1
MAX_FPS = 1000.0


@dataclass(frozen=True)
class Config:
    """全局/会话配置。

    Attributes:
        strict: 严格模式。``True`` 时越界绘制抛 :class:`OutOfBoundsError`，
            非法字符抛 :class:`InvalidCharError`；``False``（默认）时越界自动裁剪、
            非法字符被静默跳过，保证游戏不会因为一次画错而崩溃。
        alt_screen: 是否使用备用屏幕。默认 ``True``，退出后终端内容原样恢复。
        hide_cursor: 是否在游戏期间隐藏光标。
        disable_autowrap: 是否关闭终端自动换行。关闭后可以向最后一列写入而不触发滚屏。
        clear_on_exit: 退出时是否清屏。仅在 ``alt_screen=False`` 时有意义。
        fps: 默认目标帧率。
        esc_timeout: 单独一个 ``ESC`` 的判定窗口（秒）。用于区分"按下 Esc"和
            "方向键序列的开头"。
    """

    strict: bool = False
    alt_screen: bool = True
    hide_cursor: bool = True
    disable_autowrap: bool = True
    clear_on_exit: bool = False
    fps: float = 60.0
    esc_timeout: float = 0.03

    def validate(self) -> "Config":
        """校验配置，返回自身以便链式调用。

        Raises:
            InvalidConfigError: 任一字段非法。
        """
        if not isinstance(self.strict, bool):
            raise InvalidConfigError("strict 必须是 bool")
        if not isinstance(self.alt_screen, bool):
            raise InvalidConfigError("alt_screen 必须是 bool")
        if not isinstance(self.fps, (int, float)) or isinstance(self.fps, bool):
            raise InvalidConfigError("fps 必须是数字，收到 {!r}".format(self.fps))
        if not (MIN_FPS <= float(self.fps) <= MAX_FPS):
            raise InvalidConfigError(
                "fps 必须在 {}..{} 之间，收到 {!r}".format(MIN_FPS, MAX_FPS, self.fps)
            )
        if not isinstance(self.esc_timeout, (int, float)) or self.esc_timeout < 0:
            raise InvalidConfigError("esc_timeout 必须是 >= 0 的数字")
        return self

    def merged(self, **overrides: Any) -> "Config":
        """返回应用了非 ``None`` 覆盖项的新配置。

        这样 ``Game(strict=None)`` 可以表达"沿用默认值"，而 ``strict=False``
        可以明确覆盖全局默认的 ``True``。
        """
        clean: Dict[str, Any] = {}
        for key, value in overrides.items():
            if value is None:
                continue
            if not hasattr(self, key):
                raise InvalidConfigError("未知配置项: {!r}".format(key))
            clean[key] = value
        if not clean:
            return self
        return replace(self, **clean).validate()


_DEFAULT_CONFIG = Config()


def get_default_config() -> Config:
    """返回全局默认配置（供旧 API 与新建会话使用）。"""
    return _DEFAULT_CONFIG


def set_default_config(config: Config) -> Config:
    """替换全局默认配置，返回旧配置。"""
    global _DEFAULT_CONFIG
    if not isinstance(config, Config):
        raise InvalidConfigError("set_default_config 需要 Config 实例")
    previous = _DEFAULT_CONFIG
    _DEFAULT_CONFIG = config.validate()
    return previous


def build_config(base: Optional[Config] = None, **overrides: Any) -> Config:
    """基于给定配置（默认取全局默认）生成新配置。"""
    return (base or _DEFAULT_CONFIG).merged(**overrides)


def reset_default_config() -> Config:
    """把全局默认配置恢复为出厂值（测试用）。"""
    global _DEFAULT_CONFIG
    _DEFAULT_CONFIG = Config()
    return _DEFAULT_CONFIG
