# EScurses Iteration A 重构报告

> 干了什么：把 102 行的单文件 `escurses.py` 拆成一个包，补上双缓冲、差异渲染、正经点的输入层和帧率控制。
> 只做了 Iteration A（基础架构）。颜色、图层、场景、事件总线、动画、鼠标、示例游戏都没碰。
> `CODE_AUDIT.md` 里记了 70 个缺陷，`REFACTOR_PLAN.md` 是当时的计划，这篇写做完之后的样子。

---

## 1. 结论摘要

| 项目 | 结果 |
|---|---|
| 公开 API 兼容 | 11 个旧函数都在，签名和坐标语义没动。有 6 处行为改进，第 7 节列了 |
| 自动化测试 | 264 个用例全过，1.6 s，没引入第三方依赖 |
| 渲染输出量（80×24 整屏） | 16,137 → 2,062 字节（−87.2%），光标定位 1,896 → 25 次 |
| 静止帧输出 | 0 字节。旧实现每帧都要重发 16 KB |
| 帧率精度 | 目标 30/60/120/240，实测 29.98 / 59.95 / 119.95 / 239.75。旧实现是 42.28 / 99.97 / 99.95 / 99.96 |
| 等待期间 CPU | 0%。旧实现 26% / 94% / 96% |
| 中文对齐 | 宽字符占两格，后面的字不再错位 |
| 终端恢复 | 异常、`Ctrl+C`、初始化失败都能还原。测试里断言了还原序列 |
| 打包 | `pip wheel .` 能出包，产物 13 个模块加 `py.typed` |
| 没验证的 | Linux / macOS 没实机跑过；真实终端长什么样没肉眼看过（沙箱里没有 TTY） |

---

## 2. 测试环境

先把机器交代清楚，不然后面那些数字没法看。

| 项 | 值 |
|---|---|
| OS | Windows 11 10.0.26200 |
| Python | 3.13.14（MSC v.1944 64-bit） |
| CPU 噪声 | 单机、不是独占。计时类测试取中位数，或者连着跑几次 |
| 终端 | 审计和测试时 stdout 不是 TTY，所以终端相关的测试全部用注入的伪流 |

所有"实测"数字都能用仓库里的脚本跑出来：

```bash
python -m unittest discover -s tests -t .
python benchmarks/bench_render.py
python benchmarks/bench_fps.py
```

---

## 3. 重构前是什么样

`escurses.py`，102 行，一个文件。4 个模块级全局变量
（`_screen/_screenWidth/_screenHeight/_frameStartTime`），11 个函数，0 个类，0 个测试。

审计挑出 70 项缺陷，21 项 P0。这些不是代码风格问题，是四类结构性错误：

1. **没有帧的概念。** `drawChar` 写的数组就是 `refresh` 读的那个数组。双缓冲、差异渲染都没地方放。
2. **没有人管资源。** `initGame` 只分配内存，不保存终端状态；`getch` 每次调用临时开关 termios；
   `exitGame` 直接 `sys.exit()`。三件事凑一块，出异常终端必坏。
3. **拿字符串长度当显示宽度。** 缓冲区一行 `width-1` 格、宽字符只占 1 格、越界靠 Python 负索引静默写入。
   三条叠起来，任何中文界面都会错位。
4. **计时错了两处。** `sleep(t - 0.01)` 加上 `while ... < 0.01`，帧率跟参数几乎没关系，上限还被锁在 100 FPS。

第 1 条决定了这次只能做结构重构。打补丁没用。

---

## 4. 最终项目结构

```text
EScurses/
├── escurses/                 13 个模块，3,043 行
│   ├── __init__.py           公开 API、__all__、旧 API 兼容层
│   ├── config.py             Config：strict / alt_screen / fps / esc_timeout
│   ├── exceptions.py         7 个异常类型
│   ├── unicode_utils.py      显示宽度 + grapheme 切分（lru_cache）
│   ├── buffer.py             Cell / Buffer / DoubleBuffer
│   ├── screen.py             坐标系、裁剪、宽字符不变量
│   ├── renderer.py           差异渲染 + 渲染统计
│   ├── terminal.py           状态保存/还原、备用屏幕、能力探测
│   ├── input.py              统一输入接口 + ESC/CSI/SS3 解析器
│   ├── _win_input.py         msvcrt 后端（仅 nt 导入）
│   ├── _posix_input.py       termios 后端（仅 posix 导入）
│   ├── timing.py             Clock：deadline 调度
│   └── game.py               Game：生命周期 + 主循环
├── tests/                    11 个测试模块 + helpers，2,380 行，264 个用例
├── benchmarks/               新旧实现对比（含旧实现原样副本）
├── examples/                 hello_game.py（现代）/ legacy_demo.py（旧 API）
├── audit/                    审计期探针脚本（保留）
├── pyproject.toml            PEP 621
├── CHANGELOG.md / README.md / README-zh.md
├── CODE_AUDIT.md / REFACTOR_PLAN.md / REFACTOR_REPORT.md
```

**跟任务书建议的结构差 3 处，都有理由**：

| 差异 | 为什么 |
|---|---|
| 多了 `_win_input.py` / `_posix_input.py` | 任务要求"Windows 专用模块不得影响 Linux/macOS 导入"。把 `msvcrt` / `termios` 塞进按平台惰性导入的私有模块，是唯一能让"导入期零平台副作用"被测试**断言**的写法，见 `tests/test_input.py::TestPlatformIsolation`。 |
| 没有 `color.py` | 这次不实现颜色。颜色能力探测放进 `terminal.py` 了，不想再造一个只有几个常量的空模块。 |
| 没有 `events.py` / `scene.py` / `layer.py` | 属于 Iteration B。这次只留了 `Game.on_resize` 一个回调钩子。 |

### 依赖方向

严格单向，人工核对过。

```
__init__ → game → {terminal, input, renderer, screen, timing, config}
renderer → screen, terminal
screen   → buffer → unicode_utils
input    → _win_input | _posix_input
所有     → exceptions
```

`buffer` / `screen` 不 import `terminal`；`renderer` 不 import `input`；没有任何模块 import `game`。

---

## 5. 模块职责

| 模块 | 职责 | 明确不做的事 |
|---|---|---|
| `config.py` | 配置项的**唯一**校验入口（`Config.validate`），全局默认加会话级覆盖 | 不读环境变量，不管终端 |
| `exceptions.py` | 异常体系；`OutOfBoundsError` 继承 `IndexError` | 不做错误处理策略 |
| `unicode_utils.py` | `codepoint_width`（`lru_cache`）、`cluster_width`、`text_width`、grapheme 切分 | 不碰缓冲区，不知道终端 |
| `buffer.py` | `Cell`（不可变）、`Buffer`（二维格子）、`DoubleBuffer`（front/back 加 commit/invalidate） | 不做坐标转换（1-based 归 screen） |
| `screen.py` | 1-based 坐标、裁剪 / strict、宽字符占位不变量、`text()` 按宽度排版 | 不写终端，不知道帧 |
| `renderer.py` | 比 front/back，生成并写出转义序列，统计字节和定位次数 | 不读键盘，不管时钟 |
| `terminal.py` | stdout 侧状态机：备用屏幕、光标、自动换行、Windows VT、颜色能力探测、尺寸 | 不管 stdin（termios 在 input 后端） |
| `input.py` | 统一 `Key` / `KeyEvent`；ESC/CSI/SS3/Windows 前缀解析；`poll`/`read_key`/`getch` | 不做按键重复，不做 KEY_UP（检测不可靠） |
| `_win_input.py` / `_posix_input.py` | 只负责把可用字符读出来 | 不做语义解析 |
| `timing.py` | deadline 调度、`dt`、平均/最大帧长、超时计数 | 不做渲染 |
| `game.py` | 组装上面这些；生命周期；主循环；旧 API 入口 | 不调用 `sys.exit()` |

---

## 6. 实现细节

### 6.1 单元格模型与宽字符不变量

```python
@dataclass(frozen=True)
class Cell:
    char: str = " "   # 一个 grapheme cluster
    width: int = 1    # 占用的终端列数：1 或 2
    cont: bool = False  # True = 左侧宽字符的右半格，渲染时跳过

CONTINUATION = Cell(char="", width=0, cont=True)
```

`Screen._place()` 维护三条不变量，都是 O(1)：

1. 写之前先拆链。目标格是右半格就抹掉左半；目标是宽字符左半就抹掉右半。
2. 宽字符写进去的同时，把右邻标成 `CONTINUATION`。
3. 宽字符放不进最后一列就裁掉，不写半个字。

顺带有个副作用挺有用：宽字符左右两格永远一起变。所以差异区间的起点和终点各往外扩一格，
就不会切出半个字。

`_place` 里还做了个复用优化——内容完全相同时保留原来的 `Cell` 对象。`Cell` 不可变，
`list.__eq__` 会走 CPython 的 `is` 快路径。实测重画同样内容之后，空转帧从 0.56 ms 掉到 0.048 ms。

### 6.2 双缓冲：copy-sync，不是指针交换

教科书上的 double buffer 是画完交换指针、清空 back。这么干会破坏旧库的持久场景语义——
`drawChar` 画上去的字不会自己消失。照搬的话，所有旧代码的静态画面每帧闪一下。所以这里：

```python
class DoubleBuffer:
    back: Buffer    # 持久场景：绘制目标，帧结束后不清空
    front: Buffer   # 终端上现在真实显示的内容
    valid: bool     # False = 不知道终端上是什么，必须整屏重绘
```

- `present()`：`diff(back, front)`，输出，`commit()` 把 back 逐行复制进 front。
- 刷新失败就不提交，`invalidate()`，下一帧整屏重绘。不会出现"以为已经画上去了"。
- `clear()`：清 back，作废 front，触发下一次整屏重绘。对应审计 LIFE-10，清屏后不刷新那个经典 bug。
- 复制是行级 `list(row)`。Cell 不可变，不用深拷贝，1920 格大概 40 μs。

### 6.3 差异渲染

```
对每一行：
  if back_row == front_row: 跳过            # C 级 list 比较，命中身份快路径
  扫出变化区间；间隔 ≤ 2 列时合并            # 少一次光标定位
  区间端点向外扩，避免切断宽字符
定位：光标正好在位则跳过；同一行且间隔 ≤ 3 列用 CUF(\x1b[nC)，否则 CUP(\x1b[y;xH)
输出：跳过 cont 格，直接顺序写 cluster（宽字符自动前进 2 列）
```

首帧、`invalidate()`、尺寸变化走另一条路：`\x1b[H\x1b[2J` 清屏，然后只对**非空行**发一条 CUP 加整行内容。
既清残影，又不用把整屏空格写一遍。

会话期间发 `\x1b[?7l` 关掉自动换行，退出时恢复。这样写最后一列不会触发滚屏，
缓冲区也能老老实实等于终端宽度，不用再少一格。

**非 TTY 降级**：`Terminal.ansi == False` 时一个转义序列都不输出，只按行写纯文本。
不支持颜色的终端不会收到颜色转义，重定向到文件也不会有乱码。

### 6.4 输入

拆三层，平台差异压在最下面：

```
后端（读原始字符） → 解析器（纯函数，产出 KeyEvent） → Input（poll / read_key / getch）
```

- **POSIX**：会话级 `cbreak`（保留 `ISIG`，`Ctrl+C` 随时能救场）加 `os.read()`。
  丢键就是靠 `os.read` 绕开 `TextIOWrapper` 的用户态缓冲修掉的。
- **Windows**：`kbhit()` 加 `getwch()`。读到 `\x00` / `\xe0` 前缀就立刻去取扫描码，合成一个事件，
  方向键到这才真正能用。
- **解析器**统一吃 `ESC[A`、`ESCOP`、`ESC[5~`、`ESC[<...M`（鼠标→`UNKNOWN`）和 Windows 的 `\xe0H`。
  纯逻辑，不接终端也能测。
- 孤立 `ESC` 有歧义，用 `esc_timeout`（默认 30 ms）判，不阻塞主循环。

### 6.5 计时

```python
def start(self):
    now = perf_counter()
    self._next_deadline += self.period          # deadline 累加，消除漂移
    if self._next_deadline < now - self.period:
        self._next_deadline = now               # 落后超过一帧：重新对齐，不追帧爆发

def sleep(self):
    remaining = self._next_deadline - perf_counter()
    if remaining > 0:
        time.sleep(remaining)                   # spin 默认 0，纯睡眠
```

旧实现错在哪：

| | 旧 | 新 |
|---|---|---|
| 睡眠量 | `frame_time - elapsed - 0.01`（每帧少睡 10 ms） | `deadline - now` |
| 忙等 | `while perf_counter()-start < 0.01: pass`（补齐到帧开始后 10 ms，逻辑本来就是错的，还烧 CPU） | 无（`spin=0`；需要时可以配到 1 ms 以内） |
| 超时帧 | 没处理 | 计数、不追帧、自动重新对齐 |
| 参数校验 | 没有（`fps=0` 直接 `ZeroDivisionError`） | `0.1..1000`，否则 `InvalidConfigError` |

### 6.6 终端生命周期

`Terminal` 用逐项标志位记"我改过什么"（`_alt_screen` / `_cursor_hidden` / `_wrap_disabled` / `_vt_enabled`）。
`restore()` 逆序逐项还原，然后清零标志，重复调用没副作用。

```python
with Game() as game:      # setup()：保存状态 → 备用屏幕 → 关自动换行 → 隐藏光标 → 清屏
    ...                   # 任何异常
# __exit__ → input.close() → terminal.restore()（try/finally 保证两者都会执行）
```

初始化中途挂了：`setup()` 的 `try/except` 会把已经生效的项立刻 `restore()` 掉，再抛 `TerminalError`。

---

## 7. 旧 API 兼容

`escurses.py` 删了（同名包和单文件不能共存）。11 个函数全部从 `escurses/__init__.py` 重新导出。
`import escurses; escurses.drawChar(5, 3, "P")` 一行都不用改。

| 旧函数 | 状态 | 说明 |
|---|---|---|
| `initGame()` | 兼容，有增强 | 新增可选关键字参数；返回 `Game`（旧的是 `None`，忽略就行） |
| `exitGame()` | 兼容 | 新增 `exit_process=True` 参数；**不再清屏** |
| `clear()` | 兼容 | 改成"清内容加标记重绘"，下一帧才真正输出 |
| `drawChar(x, y, char)` | 兼容 | 越界改成裁剪；多字符改成顺序写入 |
| `refresh()` | 兼容 | 内部换成差异渲染 |
| `getch(nodelay=True)` | 兼容 | 方向键返回原始序列 |
| `getTerminalSize()` | 兼容 | 不再抛 `OSError` |
| `cursorEcho(echo=False)` | 兼容 | — |
| `frameStart()` | 兼容 | — |
| `sleepForFrame(fps)` | 兼容 | 真正限速了；非法 fps 抛 `InvalidConfigError` |
| `linuxTerminalCheck()` | 兼容 | 新增 `is_interactive()`，这个名字才是对的 |

snake_case 别名也有：`init_game` / `exit_game` / `draw_char` / `get_terminal_size`。

**6 处行为变化**（README 的「迁移说明」里写全了）：可写宽度多一列、越界改裁剪、
多字符顺序写入、`exitGame` 不清屏、`initGame` 会隐藏光标并进备用屏幕、`getch` 返回原始序列。

唯一不兼容的地方：直接摸 `escurses._screen` 这类内部变量。README 一直不建议这么干。

---

## 8. 新 API 长什么样

```python
from escurses import Game, Key, text_width

with Game(fps=60, strict=False) as game:
    game.draw_text(1, 1, "你好，世界")          # 按显示宽度排版
    game.draw_char(5, 3, "P", strict=False)     # 单字符
    stats = game.present()                       # 只输出变化

    for event in game.poll_keys():
        if event.key is Key.UP:
            ...
        elif event.key is Key.CHAR and event.char == "q":
            game.quit()

    game.on_resize = lambda w, h: print(w, h)
    print(stats.bytes_written, game.frame_stats.fps, game.dt)
```

```python
# 严格模式：开发期把画错暴露成异常
game = Game(strict=True)
game.draw_char(0, 0, "X")     # OutOfBoundsError（IndexError 子类）
```

---

## 9. 测试覆盖

264 个用例全过（`python -m unittest discover -s tests -t .`，1.6 s）。

| 测试模块 | 用例 | 覆盖内容 |
|---|---|---|
| `test_unicode.py` | 28 | 英文/中文/全角/半角/组合字符/零宽；grapheme 切分；ZWJ、旗帜、肤色；截断不切宽字符 |
| `test_buffer.py` | 22 | 读写、越界尺寸、resize 保内容、复制、清空、双缓冲 commit/invalidate |
| `test_screen.py` | 42 | 1-based 坐标、边界/负坐标/超大坐标、strict、裁剪、多字符、控制字符、宽字符拆链、resize |
| `test_renderer.py` | 28 | 首帧整屏、静止帧 0 字节、单字/连续/多行变化、区间合并、宽字符不写右半、invalidate、写入失败不提交、纯文本模式 |
| `test_render_fidelity.py` | 12 | **虚拟终端回放**：把输出喂给 ANSI 解释器，逐格比对缓冲区（含 30 帧随机修改、resize、清屏重绘） |
| `test_terminal.py` | 21 | 备用屏幕开关、还原顺序、幂等、非 TTY 降级、初始化失败回滚、颜色能力探测 7 种组合 |
| `test_input.py` | 31 | 无输入、可打印键、中文键、方向键（POSIX/Windows/SS3）、功能键、ESC 超时、半截序列、鼠标→UNKNOWN、平台隔离（子进程验证） |
| `test_timing.py` | 21 | fps 校验、20/60/120 FPS 精度、CPU 时间 ≪ 墙钟（证明没有忙等）、超时帧、不追帧爆发、统计 |
| `test_game.py` | 31 | 生命周期、上下文管理器、异常恢复、重复 start/close、绘图守卫、alt_screen 开关、resize 回调、主循环、wait_for_key |
| `test_compat.py` | 26 | README 旧写法原样跑通、`exitGame` 的 `SystemExit`、`configure(strict)`、`__all__` 完整性、`import *` |
| `test_examples.py` | 2 | 两个示例脚本在假终端下端到端跑通，并正常还原终端 |

另外做过一次模糊测试，没进常规套件：40 个随机种子 × 25 帧，随机改中文/ASCII、随机清屏、
随机尺寸（5×2 到 30×10），每帧拿虚拟终端比对，0 处不一致。

没做的检查：`mypy` 和 `ruff` 都没装也没跑（本机没有，也不想为这个引开发依赖）。
类型注解补全了，但"能过 strict mypy"这件事没验证，不声称。

---

## 10. 性能数据

### 10.1 渲染输出量（80×24，15 次取中位数）

| 场景 | 旧-字节 | 新-字节 | 降幅 | 旧-定位 | 新-定位 | 旧/新耗时 |
|---|---|---|---|---|---|---|
| 整屏首帧（100% 内容） | 16,137 | 2,062 | 87.2% | 1,896 | 25 | 0.51 / 0.26 ms |
| 改变 1% 格子 | 16,137 | 162 | 99.0% | 1,896 | 19 | 0.49 / 0.33 ms |
| 改变 5% 格子 | 16,137 | 573 | 96.4% | 1,896 | 96 | 0.53 / 0.47 ms |
| 画面完全不变 | 16,137 | 0 | 100% | 1,896 | 0 | 0.50 / 0.02 ms |

中文那组只测了新实现，旧实现在这个场景本身就错位，没法公平比：
整屏 2,686 字节 / 25 次定位 / 0.28 ms；单字变化 9 字节 / 1 次定位。

### 10.2 帧率控制（每个目标跑 0.4 s 左右）

| 目标 FPS | 旧-实际 | 新-实际 | 旧-CPU | 新-CPU |
|---|---|---|---|---|
| 30 | 42.28 | 29.98 | 0% | 0% |
| 60 | 99.97 | 59.95 | 26% | 0% |
| 120 | 99.95 | 119.95 | 94% | 0% |
| 240 | 99.96 | 239.75 | 96% | 0% |

### 10.3 这些数字怎么读

- **字节数是确定的**：只统计写出去的 UTF-8 字节，跟终端无关。这也是这次最硬的收益。
  `bench_render.py` 用的是旧实现的原始副本（`benchmarks/_legacy_escurses.py`，跟基线提交逐字节相同），
  跑同样的内容做对比。
- **耗时只算 Python 侧拼字符串和写出去的时间**，输出目标是内存 `StringIO`，不含真实终端解析和渲染的成本。
  所以"0.33 ms 对 0.49 ms"不能读成"快 32%"，只能说 Python 侧开销差不多。
  收益在**输出量**：终端要解析的字节少了 87%~100%。
- **CPU 占比**是 `process_time / perf_counter`，单核跑满算 100%。旧实现 60 FPS 时那 26%，
  就是 `while ...: pass` 在空转。
- **帧率**在 Windows 上受睡眠精度影响（1~15 ms 左右），实测偏差 ≤ 0.2%，换台机器可能抖 ±2%。
  表里的数都是 3 次取中位数。
- 120×40 / 200×60 的字节量按比例能外推，但我**没实测**，所以不写具体数字。

---

## 11. 跨平台

| 平台 | 状态 | 依据 |
|---|---|---|
| Windows 11 | 实机跑过 | 264 个测试、两个基准脚本、wheel 构建全部在本机执行 |
| Linux | **没实机跑过** | POSIX 后端按 termios 规范写的；解析器部分有 31 个输入测试覆盖（含 `\x1b[A` 序列）；但 `cbreak` / `os.read` 这条路径本身没在真实 Linux tty 上走过 |
| macOS | **没实机跑过** | 同上，走同一套 POSIX 后端 |

为这个做的三件事：

1. **平台隔离是可测的。** `tests/test_input.py::TestPlatformIsolation` 用子进程断言
   "导入 `escurses` 不会顺带加载另一个平台的后端模块"（Windows 上验证 `escurses._posix_input`
   没被导入，反过来也一样）。
2. **解析器和后端分开。** 方向键、功能键、ESC 超时、半截序列、Windows 前缀加起来 31 个用例，
   完全不需要真实终端，哪台机器都能跑。
3. **异常路径有覆盖。** `InputError` / `TerminalError` 的降级路径单独写了用例，读不到键盘也不会崩。

**所以不声称"支持 Linux/macOS"。** 文档里写的是"应当在 Linux 上可用，但维护者没实机验证过"。

同样没验证的还有真实终端里的视觉效果——颜色能力、光标闪烁、备用屏幕切换好不好看。
这次用虚拟终端回放顶替了肉眼检查。它能证明"发出去的序列会把屏幕画成缓冲区的内容"，
证明不了"某个具体终端模拟器会照标准解析这些序列"。

---

## 12. 审计项处置

### 已修（都有对应测试）

`INP-01~06, 08, 10` · `LIFE-01~10` · `DRAW-01~09` · `REND-01~07` ·
`FPS-01~07` · `UNI-01~05` · `CLR-02` · `ARCH-01~06` · `ENG-01, 02(测试部分), 07` · `DOC-01~05`

### 不在本次范围

| 项 | 原因 |
|---|---|
| `CLR-01`（颜色 API）、`CLR-03`（降级链）、`CLR-04`（SGR 缓存） | 任务书把完整颜色系统划到 Iteration B。这次只做了能力探测，并保证**绝不输出** SGR |
| `INP-07`（按键重复抑制） | 要拿时间窗口做去抖，属于输入层策略，建议跟事件系统一起做 |
| `INP-09`（`nodelay` 命名） | 保留旧签名是硬要求；再塞个 `blocking=` 会有两个含义重叠的参数，留到 Iteration B 一起调 |
| `ENG-02`（CI）、`ENG-06`（Ruff 配置） | 仓库还没有 GitHub Actions 和工具链配置，属于工程化阶段 |
| `ENG-03`（LICENSE） | 得维护者确认授权方式，我决定不了 |
| `ENG-05`（mypy strict） | 类型注解加了，但工具没装没跑，不声称通过 |

---

## 13. 已知限制

1. **颜色没做。** `Game.color_support` 能报能力，但没有 `fg=` / `bg=` 参数。
2. **不提供 `KEY_UP`。** 两个平台的字符流都没法可靠检测抬起，`supports_key_up` 恒为 `False`。
3. **歧义宽度字符（EAW=A）按 1 列算**，比如 `±`、`→`、制表符号。终端要是配了 `ambiguous-width=2` 就对不上。
4. **复杂 Emoji**（ZWJ 家庭、带修饰的旗帜）按"簇内最大码位宽度"估，通常算 2 列。
   有些终端渲染得更宽，这边感知不到。
5. **不滚屏。** 默认画面能塞进终端窗口，超出的裁掉。
6. **单线程。** 一个进程一个 `Game` 会话，`Game` 里面没有锁。多线程同时画不支持。
7. **瓶颈在 Python 侧。** 每帧还是要 O(行数) 把所有行比一遍，5% 变化时 0.47 ms 左右（80×24）。
   掉帧先查这里，别先怀疑终端。
8. **`escurses._screen` 这些旧内部变量没了**，直接访问会 `AttributeError`。

---

## 14. 还没做的 / Iteration B 建议

按投入产出比排：

1. **颜色系统**（`CLR-01/03/04`）：`Color` 类型加 16/256/RGB、降级链、**SGR 状态机**（只在样式变化时发序列）。
   注意 `Cell` 要加样式字段，差异比较的键也得跟着扩——这是 Iteration B 里唯一要动 `buffer.py` 的改动。
2. **事件总线**（`events.py`）：把 `poll_keys` 升级成统一事件流
   （`KEY_DOWN` / `WINDOW_RESIZE` / `SCENE_CHANGE` / `GAME_QUIT`）。
   `on_resize` 这次已经做成回调钩子了，迁起来不费劲。
3. **图层和场景**（`layer.py` / `scene.py`）：`Layer` 各持一个 `Buffer`，
   `LayerStack` 按 z-order 合成到 back buffer。合成完还是只 diff 一次，现有性能模型不会破。
4. **性能优化（可选）**：给 `Screen` 加脏行位图，`set()` 时打标记，让 `_build_diff` 只比脏行。
   5% 变化那个 0.47 ms 大概能降到 0.1 ms 量级。代价是多几个状态同步点（clear / resize / commit），
   建议先攒到真实掉帧数据再动。
5. **鼠标**：解析器已经把 SGR 鼠标序列归到 `Key.UNKNOWN` 了，补个 `MouseEvent` 分支就行。
6. **工程化**：CI 三平台矩阵（这也是唯一能真把 Linux/macOS 验证补上的手段）、Ruff、mypy strict、LICENSE。
7. **示例游戏**：贪吃蛇 / 地牢 / 太空射击。依赖图层和场景，排在图层后面比较合适。

---

## 15. 提交记录

```
e54fb06 chore: 保存重构前原始状态（审计基线）
f637f68 feat(core): 建立包结构，重构配置/异常/Unicode 宽度/缓冲区/屏幕
268617f feat(terminal, input): 终端生命周期与跨平台输入重构
b503fee feat(render, timing): 双缓冲差异渲染与帧率控制
817628c feat(game): Game 主控制器与旧 API 兼容层
```

基线提交里是完整的旧实现，`benchmarks/_legacy_escurses.py` 就是从那儿导出来的。
测试是所有模块就位之后统一补的，所以中间这几个提交本身不带测试。
按提交逐个 checkout 只能保证"模块能 import"，不等于"当时测试是过的"。
要完整验证，请在最终提交上跑 `python -m unittest discover -s tests -t .`。
