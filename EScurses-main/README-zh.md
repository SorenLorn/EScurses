# EScurses

一个**零依赖**的轻量级终端游戏库。画字符、读键盘、稳住帧率，Windows 和 Linux 都能用。

[English](./README.md) · [架构说明](./REFACTOR_REPORT.md) · [代码审计](./CODE_AUDIT.md)

```python
from escurses import Game, Key

with Game(fps=30) as game:
    game.draw_text(1, 1, "方向键移动，Q 退出")

    x, y = 10, 5
    while game.is_running:
        game.frame_start()

        for event in game.poll_keys():
            if event.key is Key.CHAR and event.char == "q":
                game.quit()
            elif event.key is Key.UP:
                y -= 1
            elif event.key is Key.DOWN:
                y += 1

        game.draw_char(x, y, "@")
        game.present()
        game.sleep_for_frame()
```

---

## 安装

```bash
pip install escurses
```

或者直接把 `escurses/` 目录复制到你的项目里 —— 没有依赖，不需要构建。

- Python **3.9+**
- Windows 10/11 控制台或 Windows Terminal
- Linux / 任意 ANSI 终端
- macOS：**维护者未测试**。理论上可用（走 POSIX 后端），但没有实际验证过

## 为什么用它

| | |
|---|---|
| **零依赖** | 只用标准库，不用编译、不用锁版本 |
| **双缓冲 + 差异渲染** | 只写发生变化的格子。画面不动时一帧输出 **0 字节** |
| **真正的帧率控制** | 基于 deadline，无漂移、不忙等。`fps=60` 就是 60 |
| **终端状态安全** | 上下文管理器 + `try/finally`，出异常也会还原终端 |
| **中文对齐** | 显示宽度来自 `unicodedata` 而不是 `len()`，中文不会串行 |

## 0.2.0 改了什么

这一版把内部实现整体重写（Iteration A）。公开 API 不变，行为可靠得多。

| 之前 | 现在 |
|---|---|
| 每个格子都发一条光标定位序列（80×24 约 16 KB/帧） | 只发变化区间；80×24 整屏约 2 KB，静止帧 0 字节 |
| `sleepForFrame(60)` 实际跑到约 100 FPS，还吃满一个核 | 误差约 1%，等待期间 CPU 占用 0% |
| 缓冲区比公开宽度少 1 列；负坐标静默写到错误位置 | 缓冲区与公开尺寸一致；越界默认裁剪，严格模式抛异常 |
| 方向键不可用（前缀字节泄漏成字符） | 方向键、Home/End/PgUp/PgDn、F1–F12 都能识别 |
| 中文只占一格，右边会被后一个字符覆盖 | 宽字符占两格，布局不再错位 |
| `exitGame()` 直接 `sys.exit()` 并清屏 | 只还原终端；是否清屏改成可选 |

## 现代 API

### 生命周期

```python
with Game(fps=60, alt_screen=True) as game:
    ...
```

`Game` 是上下文管理器。无论正常结束、抛异常还是 `Ctrl+C`，终端都会被还原：
光标恢复、退出备用屏幕、恢复自动换行、重置 SGR。

```python
game = Game(alt_screen=False)   # 不使用备用屏幕，游戏内容留在正常屏幕上
game.start()
try:
    ...
finally:
    game.close()                # 幂等
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `fps` | `60` | 目标帧率（0.1–1000） |
| `strict` | `False` | 越界绘制抛 `OutOfBoundsError`，而不是裁剪 |
| `alt_screen` | `True` | 使用备用屏幕 |
| `hide_cursor` | `True` | 游戏期间隐藏光标 |
| `width` / `height` | 终端尺寸 | 固定逻辑尺寸（测试用） |

### 绘制

坐标是 **1-based**：`(1, 1)` 是左上角。

```python
game.draw_char(5, 3, "P")          # 单个字符
game.draw_text(1, 1, "你好，世界")   # 一段文本，按显示宽度前进
game.fill(1, 10, 40, 12, "#")      # 填充矩形
game.clear()                       # 清空场景（下一次 present 才重绘）
```

越界坐标**默认裁剪**，绝不会写到别的格子上：

```python
game.draw_char(0, 0, "X")          # 忽略，返回 False
game.draw_char(999, 999, "X")      # 忽略，返回 False

game.strict = True
game.draw_char(0, 0, "X")          # 抛 OutOfBoundsError（IndexError 的子类）
```

### 渲染

```python
game.present()             # 只写变化的部分
game.present(force=True)   # 强制整屏重绘
game.refresh()             # present() 的别名，兼容旧代码
```

`present()` 返回 `RenderStats(bytes_written, cells, spans, cursor_moves, elapsed)`。

后台缓冲是**持久**的：每帧重画同一画面几乎不花代价，画一次也会一直留在屏幕上，
不需要每帧 `clear()`。

### 输入

```python
for event in game.poll_keys():        # 非阻塞，取出全部待处理事件
    print(event.key, event.char)

event = game.read_key(timeout=1.0)    # 带超时的阻塞读取
key = game.getch()                    # 旧式：返回 str 或 None
```

`Key` 枚举包含 `CHAR, ENTER, TAB, BACKSPACE, ESC, SPACE, CTRL_C, UP, DOWN, LEFT, RIGHT,
HOME, END, PAGE_UP, PAGE_DOWN, INSERT, DELETE, F1..F12, UNKNOWN`。

### 主循环

```python
game.frame_start()
# ... 更新逻辑 + 绘制 ...
game.present()
game.sleep_for_frame()
print(game.frame_stats.fps, game.dt)
```

也可以交给 `Game` 驱动：

```python
game.run(step_function, fps=60)   # 每帧调用 step(game)
```

### Unicode 宽度

```python
from escurses import text_width

text_width("abc")      # 3
text_width("中文")      # 4（不是 len() 的 2）
text_width("e\u0301")  # 1（组合符号跟着基字符）
```

## 旧版 API

原来的 11 个函数全部保留，函数名、参数顺序、1-based 坐标都不变，老代码不用改：

```python
import escurses

escurses.initGame()
escurses.cursorEcho(False)
try:
    while True:
        escurses.frameStart()
        escurses.clear()
        escurses.drawChar(5, 3, "P")
        escurses.refresh()
        escurses.sleepForFrame(30)
        if escurses.getch() == "q":
            break
finally:
    escurses.exitGame(exit_process=False)
```

### 迁移说明（6 处行为变化）

| # | 变化 | 原因 | 想保留旧行为怎么办 |
|---|---|---|---|
| 1 | 可写宽度等于终端宽度（以前是 `宽度 - 1`） | 少一列会让合法坐标崩溃 | 无需处理，那本来就是内部规避手段 |
| 2 | 越界绘制被裁剪，不再抛 `IndexError` / 静默写错位置 | 负索引会写到随机格子 | `escurses.configure(strict=True)` 恢复抛异常 |
| 3 | `drawChar(1, 1, "AB")` 会顺序写两格 | 以前两字符挤一格，整行右移 | 无需处理，旧结果本身就是错的 |
| 4 | `exitGame()` 不再清屏 | 清屏会抹掉最后一帧（分数/GameOver） | 默认仍抛 `SystemExit(0)`；库/测试里传 `exit_process=False` |
| 5 | `initGame()` 会隐藏光标并进入备用屏幕 | 原来的 `initGame` 几乎什么都没做 | `initGame(alt_screen=False, hide_cursor=False)` |
| 6 | `getch()` 对方向键返回原始序列（`"\x1b[A"` / `"\xe0H"`） | 以前根本不支持方向键 | 改用 `game.poll_keys()` 和 `Key` |

唯一不兼容的是直接访问内部变量（`escurses._screen` 等），README 一直不建议这么用。

## 已知限制

- **颜色系统尚未实现。** `detect_color_support()` 能报告终端能力
  （`NONE / BASIC / XTERM256 / TRUECOLOR`），渲染器也绝不输出 SGR 序列，
  但还没有 `fg=` / `bg=` 参数 —— 这是 Iteration B 的内容。
- **不提供 `KEY_UP`。** 字符流在两个平台上都无法可靠检测按键抬起，
  所以 `supports_key_up` 直接返回 `False`，不做假实现。
- **歧义宽度字符**（`±`、`→`、制表符等）按 1 列处理。把终端设成
  `ambiguous-width=2` 时会不一致。
- **复杂 Emoji**（ZWJ 家庭、带修饰的旗帜）按 2 列估算，有些终端会渲染得更宽，本库无法感知。
- **Linux 与 macOS 未实机运行。** POSIX 后端有完整的解析器测试，行为也按
  termios 规范实现，但维护者没有在真实 Linux tty 上跑过。
- **鼠标**上报目前解析为 `Key.UNKNOWN`，还没有鼠标事件（Iteration B）。

## 项目结构

```
escurses/
├── config.py         配置与 strict，单一校验入口
├── exceptions.py     统一异常体系
├── unicode_utils.py  显示宽度 + grapheme 切分（lru_cache）
├── buffer.py         Cell / Buffer / DoubleBuffer
├── screen.py         1-based 坐标、裁剪、宽字符不变量
├── renderer.py       差异渲染、ANSI 输出、渲染统计
├── terminal.py       状态保存/还原、备用屏幕、能力探测
├── input.py          统一输入接口 + ESC/CSI/SS3 解析
├── _win_input.py     msvcrt 后端（仅 Windows 导入）
├── _posix_input.py   termios 后端（仅 POSIX 导入）
├── timing.py         Clock：deadline 调度，无忙等
└── game.py           生命周期、主循环、旧 API
```

依赖方向严格单向：`game → {terminal, input, renderer, screen, timing}`，
`renderer → screen`，`screen → buffer → unicode_utils`。
`buffer`/`screen` 不 import `terminal`，`renderer` 不 import `input`。

实测数据见 [REFACTOR_REPORT.md](./REFACTOR_REPORT.md)，
缺陷清单见 [CODE_AUDIT.md](./CODE_AUDIT.md)。

## 开发

```bash
python -m unittest discover -s tests -t .   # 264 个测试，约 1.6 秒
python benchmarks/bench_render.py           # 新旧实现输出量对比
python benchmarks/bench_fps.py              # 新旧实现帧率/CPU 对比
python examples/hello_game.py               # 交互式示例
```

`-t .` 不能省略：`tests/` 是使用相对导入的包，省略后 `unittest` 会把各模块
当成顶层模块导入，所有 `from .helpers import ...` 都会失败。

## 许可证

仓库目前没有 LICENSE 文件。在维护者补充之前，默认保留所有权利 ——
如需分发请先开 issue 沟通。
