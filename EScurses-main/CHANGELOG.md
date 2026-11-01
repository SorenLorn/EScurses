# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的格式，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.2.0] — 2026-11-01

Iteration A：底层基础架构重构。公开 API 保持不变，内部实现整体重写。

### 新增

- `Game` 类：上下文管理器 + 生命周期 + 主循环（`run()`）。
  异常、`Ctrl+C`、初始化失败都会还原终端。
- 双缓冲（`DoubleBuffer`）与差异渲染（`Renderer`）：只输出变化的单元格，
  画面不变时一帧输出 0 字节。
- `Screen`：1-based 坐标、统一裁剪、`strict` 模式、`text()` 按显示宽度排版。
- `Key` / `KeyEvent`：统一按键事件；方向键、Home/End/PgUp/PgDn、F1–F12。
- `Clock`：基于 deadline 的帧率控制，无漂移、不忙等，暴露 `frame_stats`。
- `Config` / `configure()`：`strict`、`alt_screen`、`fps` 等集中配置。
- 异常体系：`EscursesError` / `OutOfBoundsError` / `InvalidCharError` /
  `InvalidConfigError` / `TerminalError` / `InputError` / `GameStateError`。
- `unicode_utils`：基于 `unicodedata` + `lru_cache` 的显示宽度与 grapheme 切分。
- `detect_color_support()`：报告 `NONE / BASIC / XTERM256 / TRUECOLOR`。
- 备用屏幕支持（`?1049`），可用 `Game(alt_screen=False)` 关闭。
- 窗口尺寸变化检测与 `Game.on_resize` 回调。
- `pyproject.toml`（PEP 621）、`py.typed`（PEP 561）。

### 变更

- 缓冲区可写宽度等于终端宽度（以前少 1 列）。
- 越界绘制默认裁剪；`strict=True` 时抛 `OutOfBoundsError`（`IndexError` 子类）。
- `drawChar()` 传入多字符时按顺序写入，不再挤进一格。
- `exitGame()` 不再清屏；默认仍抛 `SystemExit(0)`，新增 `exit_process=False`。
- `initGame()` 现在会隐藏光标、进入备用屏幕、进入字符模式。
- `getTerminalSize()` 在 stdout 被重定向时不再抛 `OSError`，退化为 `(80, 24)`。
- `sleepForFrame(fps)` 真正按 `fps` 限速；`fps <= 0` 抛 `InvalidConfigError`。
- `getch()` 对方向键返回原始序列（POSIX `"\x1b[A"`，Windows `"\xe0H"`）。

### 修复

- `sleepForFrame(60)` 实际跑到约 100 FPS，并吃满一个 CPU 核（审计 FPS-01/02）。
- 每格一条光标定位序列导致 80×24 每帧输出约 16 KB（审计 REND-01）。
- 负坐标通过 Python 负索引静默写到错误单元格（审计 DRAW-01）。
- 缓冲区比公开尺寸少一列，合法坐标 `x=width` 抛 `IndexError`（审计 DRAW-02）。
- 中文/日文宽字符只占一格，被后续字符覆盖右半（审计 DRAW-03）。
- 控制字符（含 `\x1b`）被原样写入，可注入 ANSI 序列（审计 DRAW-05）。
- POSIX 阻塞读取实际是行缓冲；每次读键做 3 次 termios 调用；
  `select` 与 `TextIOWrapper` 缓冲混用导致丢键（审计 INP-01/02/03）。
- Windows 方向键前缀字节泄漏成独立按键（审计 INP-04）。
- 退出后光标、自动换行、SGR 状态可能残留（审计 LIFE-08）。

### 已知限制

- 颜色绘制尚未实现（只有能力探测）。
- 不提供 `KEY_UP`；`supports_key_up` 恒为 `False`。
- 歧义宽度字符按 1 列；复杂 Emoji 按 2 列估算。
- Linux / macOS 未实机验证。
