# EScurses 代码审计报告

> 审计对象：`E:\workbuddy-Demo\EScurses-main`（对应 https://github.com/KID194/EScurses）
> 审计日期：2026-11-01
> 审计范围：`escurses.py`(102 行)、`README.md`(11 行)、`README-zh.md`(56 行)、仓库工程化配置
> 本报告**只做诊断，未修改任何源码**。所有修改需你确认后进入第二阶段执行。

---

## 0. 摘要：三个必须先解决的致命结论

在进入细节前，先给结论。这三条会直接否决"只做优化、不动架构"的路线：

**① 帧率控制是坏的，不是"不够准"，是完全不生效。**
实测目标 60 FPS 实际跑 99.96 FPS；目标 30 FPS 实际 42.17 FPS；目标 120/240 FPS 被硬性钉死在 99.96 FPS。原因是 `sleepForFrame()` 每帧少睡 10ms，再用一个自旋循环把时间"补到帧开始后 10ms"——这个自旋补的是错的终点，同时烧掉约 33% 的单核 CPU。

**② 渲染是全量逐格重绘，且每格一条光标定位序列。**
实测 80×24 终端每帧输出 **16,137 字节**（8.5 字节/格），200×60 达 **111 KB/帧**。60 FPS 下等效流量 0.95 MB/s ~ 6.5 MB/s。画面完全静止时也一样。这与"终端游戏库"的定位严重不匹配，也是任务书第二阶段的核心收益点。

**③ 缓冲区宽度与公开尺寸差 1，且越界行为分裂成"抛异常"和"静默写错位置"两种。**
`getTerminalSize()` 返回 80，但每行只有 79 个格子：`drawChar(80,1,'A')` 抛 `IndexError`；`drawChar(-1,-1,'Z')` **不报错**，靠 Python 负索引静默写到 (22,77) 这样的随机位置。这类 bug 在游戏里表现为"随机位置冒出一个字符"，极难排查。

---

## 1. 审计方法与验证环境

### 1.1 环境

| 项 | 值 |
|---|---|
| OS | Windows 11 (10.0.26200) |
| Python | 3.13.14 (MSC v.1944 64-bit) |
| 终端 | 审计沙箱内 stdout 被重定向（非 TTY），VT 探测无法进行 |
| Linux / macOS | **本机不具备，未实测**（详见第 10 节） |

### 1.2 方法

1. 逐行阅读全部源码与文档。
2. 用 `monkeypatch os.get_terminal_size` 构造 80×24 假终端，直接探针 `initGame/drawChar/clear/sleepForFrame` 的边界行为（脚本见附录 A）。
3. 把 `sys.stdout` 换成 `StringIO` 统计 `refresh()` 的**输出字节数**与 Python 侧序列化耗时。
4. 用 `perf_counter` 实测 `frameStart()+sleepForFrame()` 的稳态帧率。
5. 离线原型对比三种输出策略的字节量，用于量化第二阶段收益（**这是原型测算，不是已实现功能**）。

### 1.3 诚实声明

- 所有标注"**实测**"的结论，均在本机 Python 3.13 / Windows 11 上真实运行得到，原始输出见附录 A。
- 标注"**静态分析**"的结论来自代码阅读与已知语言/终端语义，**未在目标平台实机复现**，尤其是 POSIX 输入相关条目。
- 本报告不含任何未验证的"已修复"声明。

---

## 2. 项目现状

### 2.1 资产清单

| 文件 | 行数 | 说明 |
|---|---|---|
| `escurses.py` | 102 | 全部实现，单文件单命名空间 |
| `README.md` | 11 | 实为"首页"，明确写"We don't have English README for now" |
| `README-zh.md` | 56 | 唯一有效文档，散文式，无 API 签名 |

**缺失**：`pyproject.toml` / `setup.py`、`LICENSE`、`.gitignore`、`.github/`、`tests/`、`examples/`、`CHANGELOG.md`、`py.typed`、版本号、类型注解、docstring。
另外：**当前目录不是 git 仓库**（`git status` 报 `not a git repository`），无法做分阶段提交，需先 `git init`。

### 2.2 公开 API 面（共 11 个函数、0 个类）

| API | 签名 | 实际职责 |
|---|---|---|
| `initGame()` | `()` | 仅分配 `_screen` 缓冲区 |
| `exitGame()` | `()` | 恢复光标 + 清屏 + **`sys.exit()`** |
| `clear()` | `()` | 发 `\033[2J` + 重置缓冲区 |
| `drawChar(x,y,char)` | `(int,int,str)` | 写单个格子，无校验 |
| `refresh()` | `()` | 全量逐格重绘 + flush |
| `getch(nodelay=True)` | `(bool)` | 跨平台读一个字符 |
| `getTerminalSize()` | `()` | `os.get_terminal_size()` 裸包装 |
| `cursorEcho(echo=False)` | `(bool)` | 光标显示/隐藏 |
| `frameStart()` | `()` | 记录帧起点 |
| `sleepForFrame(fps)` | `(int/float)` | 睡眠 + 自旋 |
| `linuxTerminalCheck()` | `()` | `os.isatty(sys.stdin.fileno())` |

模块级可变全局：`_screen`、`_screenWidth`、`_screenHeight`、`_frameStartTime`。

### 2.3 依赖

零第三方依赖（仅标准库 `sys/os/time/subprocess/termios/tty/select/msvcrt`）——这是项目最值得保留的优点，后续升级应继续守住"零依赖"底线。

---

## 3. 实测数据

### 3.1 渲染输出量（`refresh()` 单帧字节数）

| 终端尺寸 | 格子数 | 字节/帧 | 字节/格 | Python 侧序列化 | 60 FPS 等效流量 |
|---|---|---|---|---|---|
| 80×24 | 1,896 | **16,137** | 8.5 | 0.45 ms | ~0.95 MB/s |
| 120×40 | 4,760 | **42,209** | 8.9 | 1.09 ms | ~2.53 MB/s |
| 200×60 | 11,940 | **111,129** | 9.3 | 2.87 ms | ~6.67 MB/s |

> 注：0.45ms 只是 Python 侧拼串成本，真实终端还要解析同样多的转义序列。终端模拟器（Windows Terminal / iTerm2 / VTE）在 6 MB/s 的 CSI 洪流下会出现明显掉帧。

**离线原型对比**（80×24 满屏，仅字符串构造测算，非已实现代码）：

| 策略 | 字节/帧 | 相对当前 | 60 FPS 流量 |
|---|---|---|---|
| 逐格 CUP（当前实现） | 16,344 | 100% | 957 KB/s |
| 按行批量 CUP | 2,089 | **12.8%** | 122 KB/s |
| 差异渲染 + 行内游程（5% 变化） | 735 | **4.5%** | 43 KB/s |
| 差异渲染 + 行内游程（20% 变化） | 1,219 | **7.5%** | 71 KB/s |

结论：**仅"按行批量"就能砍掉 87% 的流量；加上差异渲染可到 95%**。这是第二阶段收益的量化依据。

### 3.2 帧率控制（`frameStart()` + `sleepForFrame(fps)`，n=40 稳态）

| 目标 FPS | 实测 FPS | 平均帧长 | 偏差 | 结论 |
|---|---|---|---|---|
| 30 | **42.17** | 23.71 ms | 应为 33.33 ms，**快 41%** | 不生效 |
| 60 | **99.96** | 10.00 ms | 应为 16.67 ms，**快 67%** | 不生效 |
| 120 | **99.96** | 10.00 ms | 被钉死在 100 | 硬上限 |
| 240 | **99.96** | 10.00 ms | 被钉死在 100 | 硬上限 |

四个目标值得到三个相同结果（10.00 ms），说明 `sleepForFrame` 的输出与 `fps` 参数几乎无关——它实际实现的是"把帧长固定为至少 10ms"。

### 3.3 边界与异常行为（实测原始结果）

```
screenWidth/Height: 80 24
row len: 79  rows: 24                      <- 每行比公开宽度少 1
drawChar (80, 1, 'A') -> IndexError        <- 公开尺寸内的合法坐标崩了
drawChar (0, 0, 'B')  -> ok (silent)       <- 负索引，静默写错位置
drawChar (-5,-5, 'C') -> ok (silent)
drawChar (81, 1, 'D') -> IndexError
wide: cell(1,1)=('中',1,1) cell(2,1)=('X',1,2)   <- 'X' 会盖住 '中' 的右半
empty cell: ''                             <- 空格子存空串，留下残影
multichar cell: 'AB'                       <- 两字符挤一格，整行右移
clear() before initGame: TypeError 'NoneType' object cannot be interpreted as an integer
sleepForFrame before frameStart: TypeError unsupported operand type(s) for -: 'float' and 'NoneType'
sleepForFrame(0): ZeroDivisionError division by zero
getTerminalSize (stdout redirected) -> OSError [WinError 6] 句柄无效
```

---

## 4. 缺陷清单

等级定义：

| 等级 | 含义 |
|---|---|
| **P0** | 致命：崩溃、静默产生错误画面、核心功能不可用、退出后污染终端 |
| **P1** | 严重：功能不符预期、跨平台行为不一致、可观测的体验缺陷 |
| **P2** | 性能 / 可维护性：影响上限但不阻塞正确性 |
| **P3** | 工程化 / 文档 |

### 4.1 键盘输入系统（10 项）

**INP-01 [P0] POSIX 阻塞模式实际是"行缓冲"，与 Windows 按键级语义冲突** — 静态分析
- 位置：`getch()` L70-77
- 现象：`elif not nodelay:` 分支只做了 `tcgetattr` 保存 + `tcsetattr` 还原，**中间没有 `tty.setcbreak(fd)`**，等于什么都没改。`sys.stdin.read(1)` 仍在 canonical 模式，必须按回车才返回。Windows 分支的 `msvcrt.getwch()` 是按键即返回。
- 影响：同一个公开 API 在两个平台是两种语义。README 里那句"无论阻塞还是无阻塞都建议先调 `linuxTerminalCheck()`"其实是在暗示这个坑，但没有点破。任何人在 Linux 上写 `while True: k=getch(False)` 都会困惑于"为什么按了没反应"。
- 修复：把 termios 的进入/还原提升到**会话级**（`initGame` 进入、`exitGame` 还原），`getch` 内部只做 `select` + `read`，不再每次开关。

**INP-02 [P0] POSIX 每次 `getch` 触发 3 次 termios 系统调用** — 静态分析
- 位置：L61-69、L71-77
- 现象：`tcgetattr` → `tty.setcbreak` → `tcsetattr(TCSADRAIN)`，每次轮询一遍。60 FPS 游戏 = **180 次/秒**的系统调用，且 `tcsetattr` 会**整体覆盖**用户进程原有的 termios（含 `ISIG`/`IXON`/`ICRNL` 等），不是"按位恢复差别"。
- 影响：多线程/多实例下互相踩踏；`TCSADRAIN` 在输出队列未排空时会阻塞，造成不可预测的卡顿。
- 修复：同 INP-01，改为会话级一次性设置；还原时用保存的完整 `termios` 结构，并且只在"确实被我们改过"时还原。

**INP-03 [P0] 混用 `select()` 与带缓冲的 `sys.stdin.read(1)`，导致丢键** — 静态分析（**最高风险项**）
- 位置：L64 `select.select([fd],...)` + L65 `sys.stdin.read(1)`
- 根因：`sys.stdin` 是 `TextIOWrapper`，其下是 `BufferedReader`。`read(1)` 在**解码层**只返回 1 个字符，但底层 `read()` 会一次性把内核缓冲里的可用字节全部吸进**用户态缓冲**。之后内核 fd 已空 → 下一帧 `select()` 返回 `False` → `getch()` 返回 `None`，而剩余字节被雪藏在 Python 缓冲里，直到下一次有**新**按键才被吐出。
- 现象：快速连按（尤其是 `\x1b[A` 方向键三连发）时，第 2、3 个字节"消失"，随后又莫名延迟出现；表现为方向键不灵、输入顺序错乱。
- 影响：这是终端游戏最不能接受的缺陷，且现象随机，用户只会觉得"这库有 bug"。
- 修复：`os.read(fd, 1)` 或 `sys.stdin.buffer.raw.read()`，彻底绕开 `TextIOWrapper`；配合内部 `_pending: deque[str]` 做预读缓存。

**INP-04 [P1] Windows 扩展键未被解析，方向键会"泄漏"成两个输入** — 静态分析
- 位置：L51-54
- 现象：`msvcrt.getwch()` 对方向键/F1-F12/Home/End/PgUp 首次返回 `'\x00'` 或 `'\xe0'` 前缀，第二次才返回扫描码。当前代码把 `'\xe0'` 当作普通字符 `return`，扫描码 `'H'` 在下一次轮询被当作独立按键返回。
- 影响：按一下 ↑，游戏收到两个事件（一个是垃圾字符）；**方向键在当前 API 下根本无法识别**——而任务书第六节的三个示例游戏全都依赖方向键。
- 修复：检测前缀字节并**立刻**读第二字节，映射到 `KEY_UP/KEY_DOWN/KEY_LEFT/KEY_RIGHT/KEY_HOME/KEY_END/KEY_PGUP/KEY_PGDN/KEY_DELETE/KEY_F1..F12`。

**INP-05 [P1] POSIX 方向键完全未解析** — 静态分析
- 现象：`\x1b[A` 被拆成 ESC / `[` / `A` 三个字符事件（叠加 INP-03 后 `[`/`A` 还可能丢失）。没有 ESC 序列状态机。
- 修复：输入层实现 CSI 解析：`ESC [ A/B/C/D/H/F/Z`、`ESC O A..D`（application cursor mode）、修饰键 `ESC [ 1 ; 5 A`、SGR 鼠标 `ESC [ < b ; x ; y M/m`。

**INP-06 [P1] Ctrl+C 语义未归一** — 静态分析
- Windows 上 Ctrl+C 可能被系统转成 `CTRL_C_EVENT`（`KeyboardInterrupt`），也可能读到 `'\x03'`（取决于 `ENABLE_PROCESSED_INPUT`）；POSIX 上是 `SIGINT`。两条路径在不同时机触发，都会绕过用户的 `try/finally`（因为 `getch` 内部每次都在改 termios）。
- 修复：输入层把 `'\x03'` 规范化为 `KEY_CTRL_C` 事件，同时 `core` 用 `try/except KeyboardInterrupt/finally` + `signal` 钩子做双保险恢复；提供开关让用户选择"Ctrl+C 退出"还是"Ctrl+C 作为游戏按键"。

**INP-07 [P1] 无按键重复抑制、无按下/抬起区分**
- 长按方向键的重复速率完全由 OS 决定（Windows 控制台自动重复速率、Linux 终端 `kbdrate`），跨平台不可控，且无法实现"长按加速"这类手感逻辑。
- 修复：事件层提供 `repeat` 标志与可配置的重复延迟/间隔；`KEY_UP` 仅在底层能可靠检测时提供（Windows 需 `ReadConsoleInput` 的 `KEY_EVENT.bKeyDown=False`，POSIX 无法可靠检测 → 明确标注"平台能力受限"，不做假实现）。

**INP-08 [P1] 输入路径缺少防御**
- `sys.stdin.fileno()` 在 `pythonw.exe`、部分 IDE、无 stdin 的守护进程里抛 `io.UnsupportedOperation`；`select` 在 Windows 上只支持 socket，若将来跨平台复用会炸；EOF（`read` 返回 `''`）未处理，会导致死循环。
- 修复：统一在 `InputBackend` 里做能力探测，失败时降级为"无输入后端"（返回空事件并可选告警一次），而不是崩溃。

**INP-09 [P2] 参数语义反直觉**
- `getch(nodelay=True)`：`nodelay` 为真表示**非阻塞**，双重否定。建议新增 `blocking: bool = False` 并保留 `nodelay` 作为兼容参数（两者同时给时 `blocking` 优先，冲突则 `ValueError`）。

**INP-10 [P3] `linuxTerminalCheck()` 命名与实现不符**
- 它在 Windows 上也返回 `True`（实测沙箱里 stdout 非 TTY 仍返回 `True`，因为它只查 **stdin**），名字却叫 linux；且不检查 stdout 是否可写。建议改为 `is_interactive()`（保留旧名别名）。

### 4.2 终端生命周期（10 项）

**LIFE-01 [P0] `initGame()` 名不副实** — 实测
- 只做了两件事：读终端尺寸、分配 `_screen`。**不保存终端状态、不进入 cbreak、不清屏、不隐藏光标、不切备用屏幕**。
- 影响：用户必须自己记得调 `cursorEcho(False)`，否则光标在游戏画面上闪；第二阶段一旦引入 raw 模式/备用屏幕/鼠标上报，这个"空壳 init"就没有任何位置去放置"进入/还原"逻辑。

**LIFE-02 [P0] 完全没有异常安全，没有上下文管理器** — 静态分析
- 全库没有一处 `try/finally`（`getch` 内部那两个不算资源管理，见 INP-01）。`exitGame()` 只有在"正常走到"时才执行。
- 影响：这是终端库最经典的事故。一旦第二阶段加入 raw 模式，`Ctrl+C` 或任何未捕获异常都会把用户的 shell 留在"无回显 + 光标隐藏 + 鼠标乱转"的状态，必须 `reset` 才能救回来。任务书第二节第 2 条要求的正是这个。

**LIFE-03 [P0] `exitGame()` 直接 `sys.exit()` —— 库函数杀死宿主进程** — 静态分析
- 位置：L96
- 影响：无法在 Jupyter / pytest / 大型应用 / 双屏工具里使用；`sys.exit()` 抛 `SystemExit`（继承 `BaseException`），用户的 `except Exception` 捕获不到；返回码恒 0，无法表达错误退出。
- 修复：`exitGame(exit_process: bool = True)` — 默认保持旧行为（兼容），`False` 时只做还原 + 标记退出；`Game` 上下文管理器内部**永不**调 `sys.exit`。

**LIFE-04 [P0] `exitGame()` 清屏，销毁最后一帧** — 静态分析
- 玩家看不到自己的分数 / "GAME OVER" 画面，游戏结束瞬间画面直接被刷掉。对一个游戏库来说这是体验级错误。
- 修复：默认 `clear_on_exit=False`；更彻底的方案是全程使用备用屏幕（`ESC[?1049h/l`），退出时主屏自动恢复原样，这是 vim/less 的标准做法。

**LIFE-05 [P1] 用 `subprocess.run("cls"/"clear", shell=True)` 清屏** — 静态分析
- 每次退出 fork 一个进程（Windows 上约 20-40ms，Unix 上约 5-10ms）；`clear` 在精简容器里可能不存在；`shell=True` 无必要且引入 shell 解析。
- 修复：`\033[2J\033[H`（配合 `\033[?7l` 关自动换行更安全）。

**LIFE-06 [P1] 重复 `initGame()` 无保护**
- 会重建缓冲区（旧画面内容丢失），且如果用户在 `initGame()` 之后又 `exitGame()` 再 `initGame()`，将来保存的终端状态会出现"嵌套/覆盖"。需要 `_initialized` 标志 + 幂等语义。

**LIFE-07 [P1] `clear()` 依赖未初始化的全局** — 实测
- 未 `initGame()` 直接 `clear()` → `TypeError: 'NoneType' object cannot be interpreted as an integer`。
- 另外 `\033[2J` 之后**没有 `\033[H` 归位**（部分终端会把光标留在原位，导致后续相对定位漂移），也没有 `flush()`，也没有重置 SGR。

**LIFE-08 [P1] 退出时不还原 SGR / 鼠标上报 / 自动换行 / 光标形状，不清空输入缓冲**
- 第二阶段加入这些能力后，任何一个漏还原都会污染用户的 shell；残留按键会"漏"到 shell 里变成乱码命令（如 `q` `:wq` 之类）。

**LIFE-09 [P2] 无窗口缩放处理**
- 缓冲区尺寸在 `initGame()` 时冻结。缩放后：变小 → 越界抛异常；变大 → 右侧/底部永久留白，且 `refresh()` 仍按旧尺寸重绘，产生"半屏冻结"。任务书第五节第 9 项要测这个。

**LIFE-10 [P2] `clear()` 与双缓冲缺少语义衔接**
- 差异渲染的前提是"前台缓冲 = 屏幕真实内容"。`clear()` 现在只重置了"当前缓冲"，将来必须同时把"前台缓冲"标记为未知，否则 diff 会认为"屏幕已经是空格"而不再输出，导致**清屏后画面不刷新**（这是双缓冲实现里最常见的 bug 之一）。需要显式的 `invalidate()` / `dirty_all` 标志。

### 4.3 绘制安全（9 项）

**DRAW-01 [P0] 无边界检查，负坐标静默写错位置** — 实测
```python
_screen[y-1][x-1] = (char, y, x)     # L46
```
- `drawChar(0,0,'B')` → `_screen[-1][-1]` → 写到**右下角**，不报错。
- `drawChar(-1,-1,'Z')` → `_screen[-2][-2]` → 写到 (22,77)，不报错。
- 影响：游戏里"边界判定写错一个数"就会在屏幕角落冒出幽灵字符，且完全没有报错线索。

**DRAW-02 [P0] 可写宽度与公开宽度差 1，且未文档化** — 实测
- L37：`for j in range(_screenWidth - 1)` → 每行 **79** 格（80 宽终端）。
- 但 `getTerminalSize()` 返回 80，`_screenWidth` 也是 80。用户按公开尺寸算出 `x=80` → `IndexError`。
- 推测作者意图：避免写最后一列触发终端自动换行/滚动。这个意图是**正确**的，但实现方式是"偷偷少一格"，既没注释也没文档。
- 修复：分配**满宽**缓冲区，输出时用 `\033[?7l`（DECAWM off）关闭自动换行；若终端不支持则对最后一列做跳过并只告警一次。这样 `width` 与实际可写范围一致，语义干净。

**DRAW-03 [P0] 宽字符不占位，中文/日文必然错位** — 实测
```
cell(1,1)=('中',1,1)   cell(2,1)=('X',1,2)
```
- `'中'` 只占 1 个格子，`'X'` 被画到 x=2 —— 而 x=2 是 `'中'` 的**右半格**。`refresh()` 逐格输出 `\033[1;2HX` 会直接把 `'中'` 的右半边覆盖掉，显示为半个字 + X。
- 影响：任何中文/日文/全角标点/Emoji 都会破坏整行布局，且越靠右错位越大。任务书第四节第 4 条要求的正是这个。

**DRAW-04 [P1] 无字符校验：空串、多字符、非字符串** — 实测
- `drawChar(1,1,'')` → 格子存空串 → `refresh()` 输出 `\033[y;xH` 后什么都不写 → **上一帧的字符残留在屏幕上**（残影）。
- `drawChar(1,1,'AB')` → 一格塞两字符 → `refresh()` 在该位置写 "AB" → 整行右移 1 格，后续全部错位。
- `None` / `bytes` / 整数 → 要么 `TypeError`，要么写入非法内容。
- 修复：强制 `char` 为 `str`，`grapheme` 归一化，长度≠1（且非组合序列）时截断或抛 `InvalidCharError`；空串等义为"写入空格"而不是"什么都不写"。

**DRAW-05 [P1] 控制字符未过滤 → 可注入 ANSI 序列** — 静态分析
- `'\n'` 换行破坏布局、`'\t'` 制表跳格、`'\x07'` 响铃、**`'\x1b'` 可注入任意转义序列**（改变颜色、清屏、甚至在某些终端触发 OSC 52 剪贴板写入）。
- 如果游戏把一个远端用户名/聊天内容直接 `drawChar` 上去，就是一个**终端注入**漏洞。
- 修复：默认把 C0/C1 控制字符替换为 `U+FFFD` 或安全占位，提供 `allow_control_chars=False` 开关（默认关）。

**DRAW-06 [P1] 组合字符与零宽字符同样错位**
- `'e' + U+0301`（两个码位、1 个显示格）、`U+200B`（0 宽）、ZWJ emoji 序列（宽度不定）——当前一个码位占一格，全部错位。
- 修复：按 grapheme cluster 写入，宽度由 cluster 首码位 + 组合标记决定。

**DRAW-07 [P1] 坐标类型未校验**
- `drawChar(1.5,2,'x')` → `TypeError`；`drawChar(True,True,'x')` → `True-1 = 0` → 负索引 → 静默错位（因为 `bool` 是 `int` 子类）。
- 修复：显式 `isinstance(x, int) and not isinstance(x, bool)`，越界按策略处理（裁剪 / 抛 `OutOfBoundsError` / 静默忽略，需你选，见第 11 节）。

**DRAW-08 [P2] 单元格冗余存储坐标**
- `(char, y, x)` 里 `y/x` 与列表下标重复，内存 3 倍，diff 比较时这两个字段永远相等（噪声）。
- 修复：`Cell` 用 `__slots__` 存 `(char, fg, bg, attr)`，坐标由下标推导。

**DRAW-09 [P2] 缺少批量绘制 API**
- 没有 `drawText(x,y,text)` / `drawRect` / `fill` / `hline` / `vline`。画一行字要用户自己 `for i,c in enumerate(s): drawChar(x+i,y,c)`，**而且这样写必然在中文上错位**（因为要用宽度而非索引递增）。这是把库的内部缺陷转嫁给用户。

### 4.4 渲染系统（7 项）

**REND-01 [P0] 全量逐格重绘 + 每格一条 CUP** — 实测
```python
sys.stdout.write(f'\033[{char[1]};{char[2]}H{char[0]}')   # L82
```
- 每格约 8.5 字节转义序列换 1 个字符，**信噪比 1:8.5**。实测数据见 3.1 节。
- 修复：二级优化 —— ①按行批量（行首一次 CUP，行内连续输出）；②双缓冲 diff（只发变化段，用 `CUF`/`CHA` 跳格）。

**REND-02 [P0] 无差异渲染，静止画面也全量刷** — 实测
- 空闲时（画面完全不变）依然 16 KB/帧 × 60 = 0.95 MB/s 的无效流量，CPU 与终端解析开销恒定。这就是任务书第二节第 2 条要解决的核心问题。
- 修复：`front`/`back` 双缓冲 + 脏行标记 + 行内游程合并；`invalidate()` 支持强制全刷。

**REND-03 [P1] 右下角自动换行导致滚动**
- 写满最后一行最后一列后，终端的"待换行"状态（wrap pending）会让下一次输出触发滚屏，整个画面向上滚一行 —— 表现为"画面整体抖动/标题栏丢失"。
- 修复：`\033[?7l` 关闭 autowrap，退出时恢复；或永不写最后一格（但那样又回到 DRAW-02 的问题）。

**REND-04 [P1] 无 flush 策略**
- 现在每帧末尾 `flush()` 一次是对的，但 `clear()`/`cursorEcho()` 不 flush，混合调用时输出顺序依赖缓冲实现。差异渲染后必须保证"整帧原子提交"，否则会看到撕裂。

**REND-05 [P1] 颜色未进入 diff 键（第二阶段设计约束）**
- 加入颜色后，diff 的比较键必须包含 `fg/bg/attr`，否则"同一字符换颜色"不会触发重绘。同时 SGR 必须**按变化发送**：一个 24-bit 前景色序列约 19 字节，每格都发会吃掉 diff 的全部收益。
- 需要在 Renderer 里维护 `current_sgr` 状态机，避免重复发送。

**REND-06 [P2] 字符串拼接方式低效**
- 逐格 `sys.stdout.write()` = 每格一次 Python 函数调用 + 编码。应改为 `list.append` + `''.join` + 单次 `write`。实测 Python 侧 0.45ms（80×24）/ 2.87ms（200×60），这部分可以压到接近 0。

**REND-07 [P2] 无 `refresh(full=True)` / `invalidate()`**
- 终端被外部程序写过（比如用户 `Ctrl+L`、系统弹窗）、或从备用屏幕切回时，必须能强制全刷。当前没有入口。

### 4.5 帧率控制（7 项）

**FPS-01 [P0] `sleepForFrame` 无法达成任何目标帧率** — 实测
```python
frameTime = 1 / fps
elapsedTime = time.perf_counter() - _frameStartTime
time.sleep(max(0, (frameTime - elapsedTime) - 0.01))
while time.perf_counter() - _frameStartTime < 0.01:
    pass
```
两个独立错误叠加：
1. `- 0.01` 让每一帧**少睡 10ms**；
2. 自旋条件是 `perf_counter() - start < 0.01`，即"补到帧开始后 10ms"，而不是"补到 `frameTime` 结束"。对 `fps ≥ 100` 且 `frameTime ≤ 10ms` 的情况，自旋把帧长**锁死在 10ms**（→ 100 FPS 天花板）；对 `fps < 100`，自旋立即不满足，直接返回提前 10ms。
- 实测：30→42.17，60→99.96，120→99.96，240→99.96（见 3.2）。

**FPS-02 [P0] 自旋烧 CPU** — 实测推断
- 60 FPS 时自旋约 3.3ms/帧 ≈ 20% 单核；`fps ≥ 100` 时自旋满 10ms ≈ **100% 单核**。
- 修复：`sleep(remaining - spin_budget)` + 极短 spin（默认 `spin_budget=0`，仅在需要 <1ms 精度时开启），并让预算可配。

**FPS-03 [P0] `fps` 无校验** — 实测
- `fps=0` → `ZeroDivisionError`；`fps<0` → 负帧时间 → `sleep(0)` + 自旋 → 忙循环，CPU 打满；`fps=None` → `TypeError`。
- 修复：`if not (0 < fps <= 1000): raise InvalidFPSError(...)`，并在文档写明上限来源（终端刷新率）。

**FPS-04 [P1] `_frameStartTime` 未初始化** — 实测
- `sleepForFrame(60)` 在 `frameStart()` 之前调用 → `TypeError: unsupported operand type(s) for -: 'float' and 'NoneType'`。
- 修复：`Clock` 内部自己记录，`sleep()` 首次调用等价于 `frame_start()`。

**FPS-05 [P1] 无漂移补偿、无 `dt`**
- 没有累加器（deadline += frame_time），误差逐帧累积；也不返回实际 `dt`，动画和物理只能"假设每帧固定"，一旦掉帧就整体变慢（而不是按真实时间推进）。

**FPS-06 [P1] 无帧统计**
- 没有 avg/max/p99 帧耗时、没有掉帧计数、没有 `get_fps()`。任务书第五节要求的"FPS 稳定性"基准无从测起。

**FPS-07 [P2] Windows `time.sleep` 精度**
- 历史上 Windows `Sleep()` 粒度 15.6ms（Python 3.11+ 有改善但依赖 `timeBeginPeriod`）。必须采用 `sleep + 短 spin` 混合，且 spin 预算要能关掉以省电。

### 4.6 Unicode / 宽度（5 项）

**UNI-01 [P0] 全库零宽度计算**
- 没有任何一处调用 `unicodedata.east_asian_width`。若用户按 `len()` 布局（README 也没提醒不能这么做），中文界面必然错位。任务书明确要求"不要直接用 `len()` 作为显示宽度"——现状是连一个替代品都没提供。

**UNI-02 [P1] 无 EAW 表**
- 中文/日文/韩文/全角标点（`，。！`）/Emoji 都是 2 宽；`east_asian_width` 的 `W`/`F` 两类必须映射为 2，`A`（歧义）需要按终端 locale 决定（通常当 1）。

**UNI-03 [P1] 无 grapheme cluster 处理**
- 组合音标、变体选择符（VS15/VS16）、肤色修饰符必须"附着"在前一个格子上，而不是各占一格。

**UNI-04 [P1] Emoji ZWJ 序列宽度不确定**
- 👨‍👩‍👧 在不同终端可能是 2 或 4 或 6 格。必须给出"保守策略"（按 2 格处理 + 超出部分填空格）并在文档里声明"不保证与所有终端一致"。

**UNI-05 [P2] 实现方案选型（需你决策）**

| 方案 | 优点 | 缺点 |
|---|---|---|
| A. 零依赖，运行时 `unicodedata.east_asian_width()` | 无依赖、覆盖全 UCD、与 Python 版本同步 | 每字符一次 C 调用，需 LRU 缓存；`A` 类歧义需自行决策 |
| B. 内置 EAW 区间表（~1 万行二进制数据） | 极快、结果稳定不随 Python 版本变 | 数据体积、需要随 Unicode 版本更新 |
| C. 依赖 `wcwidth` | 成熟、处理组合字符/zWj 更完备 | **引入第三方依赖**，与"零依赖"原则冲突 |

**建议：A 为主（带 LRU 缓存），B 作为可选冻结表，不用 C。** 这样守住零依赖，且 `unicodedata` 本身随 Python 更新。

### 4.7 颜色系统（4 项）

**CLR-01 [P0] 完全没有颜色/样式能力**
- `drawChar(x, y, char)` 只有 3 个参数，任务书期望的 `drawChar(5,3,"P",fg="green",bg="black")` 无从实现。终端游戏没有颜色基本等于没有表现力。

**CLR-02 [P1] 无能力探测**
- 需要检查：`TERM`（`dumb` 直接禁用）、`COLORTERM`（`truecolor`/`24bit`）、`NO_COLOR` 环境变量、`sys.stdout.isatty()`、Windows 的 `ENABLE_VIRTUAL_TERMINAL_PROCESSING` 控制台模式。
- 特别提醒：**Python 不会自动为 Windows 控制台开启 VT 处理**。当前库在旧 conhost / 重定向到文件时会输出裸 `←[2J` 之类的乱码。需要 `ctypes` 调 `GetConsoleMode`/`SetConsoleMode` 开启，退出时还原。

**CLR-03 [P1] 无降级链**
- 真彩 → 256 色（`38;5;n`，用 6×6×6 立方 + 灰阶做最近邻）→ 16 色（最近邻）→ 无色。没有这条链，`fg="#ff8800"` 在 `TERM=xterm` 上会直接输出终端看不懂的序列。

**CLR-04 [P1] 无 SGR 状态缓存**
- 见 REND-05。差异渲染下必须"仅在样式变化时发 SGR"，否则每格 19 字节的真彩序列会把 diff 的收益吃干。

### 4.8 架构（6 项）

**ARCH-01 [P0] 全模块级全局状态，天然单例**
- `_screen`/`_screenWidth`/`_screenHeight`/`_frameStartTime`。无法同时存在两个 Screen（菜单预览 + 游戏、双人分屏、单元测试并行）。也无法注入 mock 做测试——这直接导致 ENG-02（无法测）。

**ARCH-02 [P0] 根本没有"帧"的概念，双缓冲无从落地**
- `drawChar` 写的数组**就是** `refresh` 读的数组——写入与呈现共享同一个容器。要加双缓冲必须先把"写入目标"和"呈现源"分离成两个对象，这是结构性改动，不是加几行代码。

**ARCH-03 [P1] 单文件、零分层**
- 输入 / 渲染 / 生命周期 / 计时 / 全局状态耦合在同一命名空间。任务书第五节的包结构不是"为了好看"，而是为了让 `InputBackend`、`Renderer`、`Clock` 能各自被替换和单测。

**ARCH-04 [P1] 无异常类型体系**
- 用户只能捕获 `IndexError`/`TypeError`/`OSError`，无法区分"坐标越界"和"真的写错了代码"。

**ARCH-05 [P1] 无 `__all__`**
- `from escurses import *` 会把 `sys`、`os`、`time`、`subprocess`、`termios`、`tty`、`select`、`msvcrt` **全部导出**，污染用户命名空间（且 `termios` 在 Windows 上不存在，`import *` 直接失败于缺失名）。

**ARCH-06 [P2] 命名风格**
- `drawChar`/`getTerminalSize`/`sleepForFrame` 是 camelCase，与 PEP 8 冲突。保留旧名为别名，新增 `draw_char`/`get_terminal_size`/`sleep_for_frame`。

### 4.9 工程化（7 项）

| ID | 等级 | 问题 | 建议 |
|---|---|---|---|
| ENG-01 | **P0** | 无 `pyproject.toml`/`setup.py`，无法 `pip install`，只能"丢进目录"（README 自己就这么教） | PEP 621 `pyproject.toml` + `hatchling`，`pip install -e .` |
| ENG-02 | **P0** | 零测试、零 CI | pytest + GitHub Actions（win/mac/linux × py3.9-3.13） |
| ENG-03 | P1 | 无 `LICENSE` | MIT（与"求 Star"的开源意图一致，需你确认） |
| ENG-04 | P1 | 无 `.gitignore`、无版本号、无 `CHANGELOG` | 加 `.gitignore`、`__version__`、Keep-a-Changelog 格式 |
| ENG-05 | P1 | 零类型注解 | 全量 `from __future__ import annotations` + mypy `strict` |
| ENG-06 | P2 | 无 Ruff/格式化配置 | Ruff（lint+format 二合一，比 black+flake8 轻） |
| ENG-07 | P2 | 无 `py.typed` | 加 PEP 561 标记，让下游 IDE 拿到类型 |

> 补充：当前目录**不是 git 仓库**。任务书要求"每个阶段独立提交"，需要先 `git init` + 初始提交（原始状态），这样每一阶段的 diff 才可回溯、可回滚。

### 4.10 文档（5 项）

**DOC-01 [P1] `README.md` 是空壳**
- 只有 11 行，写着"We don't have English README for now :)"。PyPI/GitHub 首页看到的就是这个——等于没有项目介绍，对 Star 数是硬伤。

**DOC-02 [P1] `README-zh.md` 含事实性错误**
- 第 29 行："第一个元素是屏幕的**长度**，第二关元素是屏幕的**宽度**" —— 实际是 `(columns, lines)` = `(宽, 高)`，且"第二关"是错别字。
- 第 31 行："它负责初始化 `_maxScreenWidth` 和 `_maxScreenHeight`" —— 实际变量名是 `_screenWidth`/`_screenHeight`，且它**还负责分配缓冲区**。
- 第 41 行："贴心的帮你把 `x` 和 `y` 调换了一下" —— 实际上是 `_screen[y-1][x-1]` 的标准行主序，没有"调换"这回事，描述会误导读者。

**DOC-03 [P1] 关键约束全部未文档化**
- "最后一列不可写"（DRAW-02）、"必须自己调 `cursorEcho(False)`"、"`exitGame()` 会 `sys.exit()` 并清屏"、"阻塞输入在 Linux 上要按回车"——全部没写。用户只能靠踩坑。

**DOC-04 [P1] 无 API 参考、无示例、无架构说明、无兼容性矩阵。**

**DOC-05 [P2] 技术对比不准确**
- "你应该不知道 pycurses，那是个很老很老的库" —— 生态里通常说的是 `curses`（标准库）和 `pygame`；"pycurses"不是主流称呼。既然要重写 README，建议改成与 `curses` / `blessed` / `rich` / `textual` 的准确定位对比（我们的差异点是"零依赖 + 极简 + 专为字符游戏"）。

---

## 5. 风险矩阵

| 模块 | P0 | P1 | P2 | P3 | 合计 | 处置阶段 |
|---|---|---|---|---|---|---|
| 输入系统 | 3 | 5 | 1 | 1 | 10 | 阶段 1 |
| 终端生命周期 | 4 | 4 | 2 | 0 | 10 | 阶段 1 |
| 绘制安全 | 3 | 4 | 2 | 0 | 9 | 阶段 1 + 2 |
| 渲染系统 | 2 | 3 | 2 | 0 | 7 | 阶段 2 |
| 帧率控制 | 3 | 3 | 1 | 0 | 7 | 阶段 1 |
| Unicode | 1 | 3 | 1 | 0 | 5 | 阶段 2 |
| 颜色 | 1 | 3 | 0 | 0 | 4 | 阶段 2 |
| 架构 | 2 | 3 | 1 | 0 | 6 | 阶段 3 |
| 工程化 | 2 | 3 | 2 | 0 | 7 | 贯穿 |
| 文档 | 0 | 4 | 1 | 0 | 5 | 阶段 7 |
| **合计** | **21** | **35** | **13** | **1** | **70** |

**21 个 P0**。其中 8 个（INP-01/02/03、LIFE-01/02、REND-01/02、ARCH-02）属于"必须先改结构才能改对"的类型——这就是为什么我建议把"阶段 1 修复"和"阶段 3 模块化"合并成一次重构，而不是先打补丁再搬家（打补丁的代码在搬家时要重写一遍，等于做两遍）。

---

## 6. 兼容性策略（关键决策，需你确认）

### 6.1 能 100% 兼容的部分

`import escurses`，以及全部 11 个旧函数名与位置参数签名，都可以在包结构下原样保留：

```
escurses/__init__.py  里 re-export 旧名字 -> 内部转发到新 API
```

旧代码 `escurses.drawChar(5,3,'P')`、`escurses.refresh()` 无需改动即可运行。**这一层"兼容垫片"是零成本的，必须做。**

### 6.2 无法完全兼容、必须明确告知的 4 处

| # | 旧行为 | 新行为 | 为什么必须变 | 缓解 |
|---|---|---|---|---|
| 1 | `exitGame()` 直接 `sys.exit()` + 清屏 | 默认仍 `SystemExit`，但**不清屏**；提供 `exit_process=False` | 清屏销毁 GameOver 画面是 P0；`sys.exit` 在库内是反模式 | `exitGame(clear=False, exit_process=True)` 保持旧默认，"看起来一样" |
| 2 | 可写宽度 = 终端宽度 − 1 | 可写宽度 = 终端宽度 | 公开尺寸与可写范围差 1 是 P0（DRAW-02） | 依赖"最后一列不能用"的代码本来就在写 bug；且提供 `Screen.width` 为唯一真源 |
| 3 | `drawChar` 支持多字符/空串 | 抛 `InvalidCharError`（可配 `strict=False` 退回旧行为） | 静默错位是 P0（DRAW-04/05） | `Game(strict_chars=False)` 兼容模式 |
| 4 | 越界抛 `IndexError` / 静默写错位置 | 抛 `OutOfBoundsError`（`IndexError` 子类，旧 `except` 仍生效） | 负索引静默写入是 P0（DRAW-01） | 新异常继承 `IndexError`，向后兼容捕获 |

**已经确认无法实测的一点**：如果旧用户代码直接访问了 `escurses._screen`（内部变量），升级后会断。README 明确说"不建议直接用这两个变量"，风险可接受。

### 6.3 包目录与 `escurses.py` 的同名冲突

Python 不允许同目录同时存在 `escurses.py` 和 `escurses/`。方案：

- **采用**：删除 `escurses.py`，改为 `escurses/` 包，`__init__.py` 提供全部旧名字。`import escurses` 行为不变。
- 不采用：保留 `escurses.py` 做 shim + 把包改名（丑陋，且破坏"pip 安装后 import 名不变"的直觉）。

---

## 7. 目标架构（在任务书建议上做减法）

任务书给的 13 个模块里，有 3 个在"零依赖小框架"的定位下属于过度设计，建议合并：

| 任务书建议 | 我的建议 | 理由 |
|---|---|---|
| `events.py` + `input.py` | 保留两个：`input.py` 只产"原始按键/鼠标"，`events.py` 做归一化 + 队列 + 分发 | 职责确实不同，值得拆 |
| `layer.py` + `scene.py` | 保留：Layer 管"画面合成顺序"，Scene 管"生命周期与切换" | 两者正交，拆开更清晰 |
| `utils.py` | **降级为内部 `_util.py`，不导出** | 公开的"utils"是垃圾桶模块的前兆 |
| `exceptions.py` | 保留（很小，但值得） | 用户需要 `except escurses.EscursesError` |
| `timer.py` | 保留，改名 `clock.py`? 不 —— 保持 `timer.py`，内含 `Clock` + `FrameLimiter` | 与任务书一致 |
| 新增 `unicode_width.py` | **必须新增** | 宽度计算是 P0 能力，且被 screen/renderer 共用，独立成文件便于单测 |
| 新增 `_compat.py` | 建议新增（内部） | Windows VT 开启、TTY 探测、编码处理集中在这里 |
| `cell.py` | **不新增**，并入 `screen.py` | Cell 只有 4 个字段，独立文件不值当 |

最终结构：

```
escurses/
    __init__.py        # 公开 API 汇总、__all__、__version__、旧名兼容
    _compat.py         # 平台探测：TTY / VT 模式 / 编码 / 尺寸（内部）
    cell.py?           -> 无
    screen.py          # Cell / Screen(Buffer)：坐标、宽字符占位、裁剪、invalidate
    renderer.py        # 双缓冲 diff、SGR 状态机、行批量化、全刷、flush
    color.py           # Color(16/256/RGB) / Attr / 能力探测 / 降级 / SGR 编码
    unicode_width.py   # grapheme 切分 + EAW 宽度 + 缓存
    input.py           # InputBackend(Windows/POSIX) + ESC 序列解析 + 鼠标 SGR
    events.py          # Event / EventType / EventQueue / dispatch
    timer.py           # Clock（dt、帧统计、漂移补偿）
    layer.py           # Layer / LayerStack（z-order、脏标记传播）
    scene.py           # Scene 基类 / SceneManager（enter/exit/pause/resume）
    core.py            # Game（上下文管理器 + 主循环 + 异常恢复 + 信号处理）
    exceptions.py      # EscursesError 体系
```

依赖方向（严格单向，用 import-linter 或人工 review 保证）：

```
core -> scene -> layer -> screen -> cell/unicode_width
core -> input -> events
core -> renderer -> screen + color + _compat
core -> timer
所有 -> exceptions
```

**关键设计约束**（写进 ARCHITECTURE.md，防止后续腐化）：
1. `screen.py` 不 import `renderer.py`；缓冲区不知道终端存在。
2. `renderer.py` 不 import `input.py`；渲染不读键盘。
3. 任何模块不 import `core.py`（避免循环）。
4. 平台相关代码只允许出现在 `_compat.py` 和 `input.py` 的 backend 实现里。

---

## 8. 分阶段执行计划与验收标准

我把任务书的 7 个阶段重排为 **5 个可独立验收的迭代**（把"修复"和"模块化"合并，理由见第 5 节末尾）：

### 迭代 A：地基重构（合并原阶段 1 修复 + 阶段 2 双缓冲 + 阶段 3 模块化）
**交付**：包结构、`Screen`/`Renderer` 双缓冲 + 差异渲染、`Clock`、`InputBackend`（含方向键、ESC 序列）、`Game` 上下文管理器、异常体系、旧 API 兼容层。
**验收**（全部可测）：
- [ ] 11 个旧函数全部可用，旧示例代码不改一行能跑
- [ ] `with Game() as g:` 内抛异常后，终端完全恢复（光标/回显/SGR/autowrap），`stty -a` 与进入前一致
- [ ] 目标 60 FPS 实测 59.5–60.5；目标 30 FPS 实测 29.5–30.5
- [ ] 空闲帧（画面不变）输出 **< 50 字节**
- [ ] 80×24 全屏重绘输出 **< 3 KB**（对比当前 16.1 KB）
- [ ] 中文 `"你好世界"` 绘制后，后续 ASCII 从 x+8 开始且不错位
- [ ] `drawChar(-1,-1)` 抛 `OutOfBoundsError`（且是 `IndexError` 子类）
- [ ] Ctrl+C 后终端状态正确恢复
- [ ] Windows 方向键解码为 `KEY_UP/DOWN/LEFT/RIGHT`

### 迭代 B：颜色 + Unicode + 图层/场景/事件/动画/鼠标（原阶段 2 余项 + 阶段 4）
**验收**：
- [ ] `drawChar(5,3,"P",fg="green",bg="black")` 生效；`fg=(255,136,0)` 真彩生效
- [ ] `TERM=dumb` / `NO_COLOR=1` / 非 TTY 下自动降级且不输出裸转义
- [ ] Windows 控制台 VT 开启/还原正确
- [ ] SGR 只在样式变化时发送（抓包验证：同色连续 100 格只有 1 条 SGR）
- [ ] 中日文、全角标点、Emoji 宽度正确
- [ ] 5 层叠加顺序正确；场景切换 `enter/exit/pause/resume` 各触发一次
- [ ] 不支持鼠标的终端上启用鼠标 → 安全降级 + 一次性警告，不崩

### 迭代 C：示例 + 测试体系 + 性能基准（原阶段 5 + 6）
**验收**：
- [ ] 贪吃蛇 / ASCII 地牢 / 太空射击 三个示例可 `python examples/xxx.py` 直接运行
- [ ] 三个示例**只使用公开 API**（grep 不到 `\033` 和 `sys.stdout`）
- [ ] pytest 覆盖任务书列出的 15 个测试点，`pytest -q` 全绿
- [ ] 基准脚本输出前后对比表（全量 vs diff、不同分辨率、不同 FPS 的 CPU）

### 迭代 D：工程化 + 文档（原阶段 7）
- [ ] `pip install -e .` 成功；`import escurses; escurses.__version__`
- [ ] GitHub Actions 三平台矩阵绿
- [ ] mypy strict 通过、ruff 通过
- [ ] README（英文）/ README-zh / API.md / ARCHITECTURE.md / COMPATIBILITY.md / CHANGELOG.md

### 迭代 E（可选）：PyPI 发布
- 需要你提供 PyPI token；在此之前不做任何"已发布"声明。

**每个迭代结束时我会输出**：修改的文件清单 / 修复的缺陷 ID / 新增能力 / 通过的测试 / 未通过的测试 / 已知问题。不通过的不写"已修复"。

---

## 9. 测试与基准设计（先说清怎么测，避免"测了但没意义"）

### 9.1 可自动化（无需真终端）

用 fake backend 注入：`FakeInput`（预置按键序列）、`FakeOutput`（收集输出字符串）、`FakeClock`（手动推进时间）。这样 15 个测试点里有 11 个能进 CI：

| # | 任务书测试点 | 方法 | 可否进 CI |
|---|---|---|---|
| 1 | Windows 输入 | `FakeInput` 注入 `\xe0H` → 断言 `KEY_UP` | ✅ |
| 2 | Linux 输入 | 注入 `\x1b[A` → 断言 `KEY_UP` | ✅ |
| 3 | 阻塞输入 | 注入队列，断言阻塞到有键 | ✅ |
| 4 | 非阻塞输入 | 空队列 → 断言 `None` 且不阻塞 | ✅ |
| 5 | 方向键 | 4 方向 × 2 平台 | ✅ |
| 6 | 坐标越界 | 8 个边界组合 | ✅ |
| 7 | 中文字符绘制 | 断言 `Screen.get(3,1)` 为宽字符占位、`Screen.get(4,1)` 为占位符 | ✅ |
| 8 | RGB 颜色 | 断言输出含 `38;2;255;136;0` | ✅ |
| 9 | 窗口缩放 | 注入 resize 事件 → 断言缓冲区重建 + 全量重绘 | ✅ |
| 10 | 双缓冲 | 断言 back 修改不影响 front，`present()` 后交换 | ✅ |
| 11 | 差异渲染 | 只改 1 格 → 断言输出 < 40 字节 | ✅ |
| 12 | FPS 稳定性 | `FakeClock` 推进，断言 deadline 累加无漂移 | ✅ |
| 13 | 异常退出恢复 | `with Game(): raise` → 断言 restore 调用序列 | ✅ |
| 14 | 多图层叠加 | 3 层同位置绘制 → 断言最上层胜出 | ✅ |
| 15 | 场景切换 | 断言 `exit` 在 `enter` 之前被调用 | ✅ |

### 9.2 必须真终端（手工，我会给出可执行的检查脚本）

- 光标是否真的隐藏/恢复（`tput cnorm` 对照）
- 颜色在 Windows Terminal / conhost / xterm 的实际观感
- 鼠标 SGR 上报在不同终端的可用性
- 缩放时的视觉一致性
- 中文对齐的肉眼检查（附一张"宽度标尺"示例，用 `0123456789` 对齐中文字符串）

### 9.3 基准脚本（`benchmarks/bench.py`）

对比维度：
1. 全量重绘 vs 差异渲染（相同画面变更比例 0% / 1% / 5% / 20% / 100%）
2. 分辨率 80×24 / 120×40 / 200×60
3. FPS 30 / 60 / 120 下的 CPU 占用（`psutil` 作为**可选**开发依赖，不进运行时依赖）
4. 大量字符变化的输出效率（逐格 vs 行批量 vs 游程）

输出 CSV + Markdown 表，直接进 CHANGELOG 作为"提升依据"。

---

## 10. 未验证项与假设（必须说清）

| 项 | 状态 | 说明 |
|---|---|---|
| Linux 输入（termios/select/阻塞） | **未实测** | 本机无 Linux。INP-01/02/03 为静态分析，基于 Python `TextIOWrapper` 缓冲语义与 termios 规范。**迭代 A 前必须在 Linux 上复核**，否则可能修错方向 |
| macOS 输入 | **未实测** | 作者自己在 README 里也说不确定。macOS 用 termios（无 `select` 差异），但 Terminal.app vs iTerm2 的 ESC 序列有差异 |
| 真实终端视觉表现 | **未实测** | 审计沙箱 stdout 非 TTY，`\033[2J` 等无法观察实际效果 |
| Windows 控制台 VT 开启 | **未实测** | `GetConsoleMode` 在沙箱内失败（句柄无效），需在真实 Windows Terminal 里验证 |
| 鼠标 SGR 上报 | **未实测** | 依赖 `\033[?1006h`，各终端支持度不一 |
| `exitGame()` 在 `atexit` 场景的行为 | 未测 | `sys.exit` 在 `atexit` 里只打印异常，不退出 |
| 多线程安全性 | 未测 | 现状是全全局变量，明确**非线程安全**，需在文档声明 |

**我不会在文档里写"支持 macOS"，除非真的在 macOS 上跑过。**

---

## 11. 需要你决定的 5 个决策点

这些会影响后续所有代码，请先定：

**Q1. 越界绘制策略？**
- (a) 抛 `OutOfBoundsError`（严格，推荐用于开发期）
- (b) 静默裁剪（游戏里画超出屏幕的精灵很常见，裁剪更实用）
- (c) 全局 `strict` 开关，默认裁剪、调试期开严格 ← **我建议这个**

**Q2. 颜色能力探测不到时（`TERM=dumb` / 重定向到文件）？**
- (a) 直接不输出任何转义（纯文本，推荐）
- (b) 抛异常要求用户显式关闭
- (c) 照常输出（现状，会产出乱码文件）

**Q3. 备用屏幕（alt-screen）用不用？**
- 用：退出时 shell 画面完美恢复（vim 体验），但用户看不到最后画面（除非在退出前 `input()` 等一下）
- 不用：简单，但退出后游戏画面留在屏幕上
- **我建议**：默认用 alt-screen，`Game(alt_screen=False)` 可关

**Q4. Unicode 宽度实现？**
- (a) `unicodedata.east_asian_width` + LRU 缓存（零依赖，推荐）
- (b) 内置冻结 EAW 表（更快，但要维护数据）
- (c) 依赖 `wcwidth`（最省事，但破坏零依赖）

**Q5. 迭代 A 的范围？**
- 我建议 **A = 包结构 + 双缓冲 diff + 输入重写 + Clock + Game 上下文 + 兼容层**，一次做完。因为双缓冲必须依赖"Screen/Renderer 分离"，而输入重写必须依赖"会话级 termios"，这三件事共享同一次结构变动。分批做等于把同一段代码写两遍。
- 如果你希望更小步，可以拆成 A1（纯结构 + 兼容层，行为不变，全测试绿）→ A2（双缓冲）→ A3（输入+时钟）。**但 A1 会有一个"没有性能提升"的中间态**，需要你接受。

---

## 12. 建议的第一步动作（等你确认后执行）

1. `git init` + 初始提交当前原始状态（保证可回滚、可分阶段 diff）
2. 按 Q5 的选择建立骨架 + 兼容层
3. 先写测试再做功能（fake backend 先行），保证每步可验证
4. 每完成一个迭代，出一份 `docs/reports/ITERATION-X.md`（改了什么/测了什么/没通过什么）

**在收到你对 Q1–Q5 的答复之前，我不会修改任何源码。**

---

## 附录 A：审计探针脚本与原始输出

### A.1 可复现命令

脚本已归档在 `audit/` 目录（对原始源码零依赖，只读探针）：

```bash
# 边界与异常行为
python audit/probe.py

# 渲染字节量与序列化耗时
python audit/bench.py

# 帧率控制实测
python audit/fps.py

# 三种渲染策略的离线原型对比
python audit/sim.py
```

> 注意：`probe.py` 会 monkeypatch `os.get_terminal_size` 并直接读写 `escurses._screen`，
> 它是一份**诊断型**脚本，只在审计阶段使用，不属于正式测试套件。

### A.2 `bench.py` 原始输出

```
80x24:  cells=1896  bytes/frame=16137   bytes/cell=8.5  time=0.45ms
120x40: cells=4760  bytes/frame=42209   bytes/cell=8.9  time=1.09ms
200x60: cells=11940 bytes/frame=111129  bytes/cell=9.3  time=2.87ms
```

### A.3 `fps.py` 原始输出

```
target  30 FPS -> actual  42.17 FPS  (avg 23.71 ms/frame)
target  60 FPS -> actual  99.96 FPS  (avg 10.00 ms/frame)
target 120 FPS -> actual  99.96 FPS  (avg 10.00 ms/frame)
target 240 FPS -> actual  99.96 FPS  (avg 10.00 ms/frame)
```

### A.4 `sim.py` 原始输出

```
per-cell (current)     bytes/frame=  16344  ratio=1.000  @60fps=957.7 KB/s
row-batch full         bytes/frame=   2089  ratio=0.128  @60fps=122.4 KB/s
diff 5% row-batch      bytes/frame=    735  ratio=0.045  @60fps= 43.1 KB/s
diff 20% row-batch     bytes/frame=   1219  ratio=0.075  @60fps= 71.4 KB/s
```

### A.5 `probe.py` 原始输出

```
screenWidth/Height: 80 24
row len: 79 rows: 24
drawChar (80, 1, 'A') -> IndexError list assignment index out of range
drawChar (0, 0, 'B') -> ok (silent), cell = ('B', 0, 0)
drawChar (-5, -5, 'C') -> ok (silent), cell = ('C', -5, -5)
drawChar (81, 1, 'D') -> IndexError list assignment index out of range
wide: cell(1,1)= ('中', 1, 1)  cell(2,1)= ('X', 1, 2)
empty cell: ''
multichar cell: 'AB'
after drawChar(-1,-1): last cell = (' ', 24, 79)
clear() before initGame: TypeError 'NoneType' object cannot be interpreted as an integer
sleepForFrame before frameStart: TypeError unsupported operand type(s) for -: 'float' and 'NoneType'
sleepForFrame(0): ZeroDivisionError division by zero
```

> 注：`drawChar(-5,-5)` 打印的是 `_screen[-6][-6]` 本身，能看到坐标字段 `('C',-5,-5)` 被原样写进单元格——证明写入确实发生了，只是落在了错误的位置。
