"""字符显示宽度与 grapheme 切分。

终端里"一个字符"占几列，取决于 East Asian Width，而不是 ``len()``。
本模块是全库**唯一**的宽度来源：绘制、缓冲区定位、光标移动都必须走这里。

已知限制（不用"完全支持 Unicode"这种说法）：

- East Asian Width ``A``（Ambiguous）一律按 1 列处理。日文终端在
  ``ambiguous-width=2`` 设置下会与本库不一致，本库不做运行时探测。
- Emoji ZWJ 序列（如 👨‍👩‍👧）按"最大码位宽度"估算（通常 2 列），
  部分终端会渲染成 2/4/6 列，本库无法感知，可能与终端实际显示不一致。
- 文本呈现（Text Presentation）选择符 VS15 之后的字形宽度按原码位宽度处理。
- 组合标记必须跟随基字符；孤立出现的组合标记宽度为 0，不可单独绘制。
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache
from typing import Iterator, List

__all__ = [
    "codepoint_width",
    "cluster_width",
    "text_width",
    "iter_graphemes",
    "graphemes",
    "is_control",
    "truncate_to_width",
]

_ZERO_WIDTH_CATEGORIES = frozenset({"Mn", "Me", "Cf"})
_UNPRINTABLE_CATEGORIES = frozenset({"Cc", "Cs", "Co", "Cn"})
_WIDE_EAW = frozenset({"W", "F"})

# 上限：一个 grapheme cluster 最多占 2 列。更宽的序列在终端里也无法可靠定位。
_MAX_CLUSTER_WIDTH = 2


@lru_cache(maxsize=8192)
def codepoint_width(ch: str) -> int:
    """返回单个码位的显示宽度：0 / 1 / 2。

    Args:
        ch: 单个字符（长度必须为 1）。

    Returns:
        0 表示零宽（组合标记、格式符、控制符），1 表示半角，2 表示全角。

    Raises:
        ValueError: ``ch`` 长度不为 1。
    """
    if len(ch) != 1:
        raise ValueError("codepoint_width 需要单个字符，收到 {!r}".format(ch))
    category = unicodedata.category(ch)
    if category in _ZERO_WIDTH_CATEGORIES:
        return 0
    if category in _UNPRINTABLE_CATEGORIES:
        # 控制字符不可绘制，宽度记为 0；是否允许写入由 Screen 决定。
        return 0
    if unicodedata.east_asian_width(ch) in _WIDE_EAW:
        return _MAX_CLUSTER_WIDTH
    return 1


def cluster_width(cluster: str) -> int:
    """返回一个 grapheme cluster 的显示宽度。

    取簇内各码位宽度的最大值：``"e\\u0301"`` → 1，``"中"`` → 2，
    纯组合标记 → 0。
    """
    width = 0
    for ch in cluster:
        current = codepoint_width(ch)
        if current > width:
            width = current
    return min(width, _MAX_CLUSTER_WIDTH)


def text_width(text: str) -> int:
    """返回整段文本的终端显示宽度（不按 ``len()`` 计算）。"""
    total = 0
    for cluster in iter_graphemes(text):
        total += cluster_width(cluster)
    return total


def is_control(ch: str) -> bool:
    """判断是否为不可安全绘制的控制字符。

    ``\\n`` / ``\\t`` / ``\\x1b`` 这类字符写进终端会改变光标位置甚至注入转义序列，
    必须由调用方拦截。
    """
    if not ch:
        return False
    return unicodedata.category(ch) in _UNPRINTABLE_CATEGORIES


def _is_extend(ch: str) -> bool:
    """是否需要附着到前一个字符（组合标记 / ZWJ / 变体选择符 / 肤色 / 标签）。"""
    if unicodedata.category(ch) in ("Mn", "Me", "Mc"):
        return True
    code = ord(ch)
    if code == 0x200D:  # ZERO WIDTH JOINER
        return True
    if 0xFE00 <= code <= 0xFE0F:  # 变体选择符
        return True
    if 0xE0100 <= code <= 0xE01EF:  # 变体选择符补充区
        return True
    if 0x1F3FB <= code <= 0x1F3FF:  # 肤色修饰符
        return True
    if 0xE0020 <= code <= 0xE007F:  # 标签字符（旗帜子序列）
        return True
    return False


def _is_regional_indicator(ch: str) -> bool:
    return 0x1F1E6 <= ord(ch) <= 0x1F1FF


def iter_graphemes(text: str) -> Iterator[str]:
    """把字符串切成 grapheme cluster 序列。

    这是"一个位置该放什么"的最小单位：``"e\\u0301"`` 是一个簇（1 列），
    ``"中"`` 是一个簇（2 列），``"👨‍👩‍👧"`` 是一个簇（按 2 列估算）。

    实现是**简化版** UAX #29，覆盖常见场景（组合标记、ZWJ、变体选择符、
    肤色修饰符、旗帜对），不做完整的 Unicode 字素簇分段。
    """
    cluster = ""
    index = 0
    length = len(text)
    while index < length:
        ch = text[index]
        if not cluster:
            cluster = ch
            index += 1
            # 旗帜由两个连续的 Regional Indicator 组成，视为一个簇。
            if (
                _is_regional_indicator(ch)
                and index < length
                and _is_regional_indicator(text[index])
            ):
                cluster += text[index]
                index += 1
            continue
        if cluster.endswith("\u200d") or _is_extend(ch):
            cluster += ch
            index += 1
            continue
        yield cluster
        cluster = ""
    if cluster:
        yield cluster


def graphemes(text: str) -> List[str]:
    """``iter_graphemes`` 的列表版本。"""
    return list(iter_graphemes(text))


def truncate_to_width(text: str, max_width: int) -> str:
    """按显示宽度截断文本，不会切断宽字符。

    Args:
        text: 源文本。
        max_width: 允许的最大显示宽度（列数）。

    Returns:
        截断后的文本，其 :func:`text_width` 一定 ``<= max_width``。
    """
    if max_width <= 0:
        return ""
    used = 0
    kept: List[str] = []
    for cluster in iter_graphemes(text):
        width = cluster_width(cluster)
        if used + width > max_width:
            break
        kept.append(cluster)
        used += width
    return "".join(kept)
