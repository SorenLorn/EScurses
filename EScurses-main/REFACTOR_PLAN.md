# EScurses 重构计划（Iteration A：底层基础架构）

> 输入：`CODE_AUDIT.md`（70 项缺陷：21×P0 / 35×P1 / 13×P2 / 1×P3）
> 基线提交：`e54fb06 chore: 保存重构前原始状态（审计基线）`
> 本文件只描述**本次要做什么**；执行结果见 `REFACTOR_REPORT.md`。

---

## 1. 现状核对（修改前）

### 1.1 文件与 API 现状

| 文件 | 行数 | 状态 |
|---|---|---|
| `escurses.py` | 102 | 单文件，模块级全局状态，无类型注解 |
| `README.md` | 11 | 仅"首页"占位 |
| `README-zh.md` | 56 | 唯一文档，散文式 |
| `CODE_AUDIT.md` | 770 | 审计报告（本次输入） |
| `audit/*.py` | 4 | 审计探针脚本，只读诊断用 |

工作区**无未提交的用户修改**，`escurses.py` 的 mtime / 大小与审计时一致（md5 `f5f232c4…`，2800 字节）。原项目不是 git 仓库，已 `git init` 并提交基线，保证可分阶段回滚。

### 1.2 公开 API（11 个函数，重构后必须全部保留）

| 函数 | 原签名 | 原语义 |
|---|---|---|
| `initGame()` | `()` | 只分配缓冲区 |
| `exitGame()` | `()` | 恢复光标 + 清屏 + `sys.exit()` |
| `clear()` | `()` | 发 `\033[2J` 并重置缓冲区 |
| `drawChar(x, y, char)` | `(int,int,str)` | 写单格，**1-based**，无校验 |
| `refresh()` | `()` | 全量逐格重绘 + flush |
| `getch(nodelay=True)` | `(bool)` | 读一个字符，非阻塞默认 |
| `getTerminalSize()` | `()` | 裸包装 `os.get_terminal_size` |
| `cursorEcho(echo=False)` | `(bool)` | 光标显隐 |
| `frameStart()` | `()` | 记录帧起点 |
| `sleepForFrame(fps)` | `(num)` | 睡眠到帧末 |
| `linuxTerminalCheck()` | `()` | `isatty(stdin)` |

### 1.3 审计结论在当前代码中的复核

抽查 8 条 P0，全部在当前代码中复现（脚本 `audit/probe.py`、`audit/fps.py` 仍可运行）：

- 每行 `width-1` 格：`initGame` L37 `range(_screenWidth - 1)` ✅
- 负索引静默写入：`_screen[y-1][x-1]` 无校验 ✅
- `clear()` 未初始化即 `TypeError` ✅
- `sleepForFrame(60)` 实测 99.96 FPS ✅
- `refresh()` 每格一条 CUP ✅
- POSIX 阻塞分支缺少 `tty.setcbreak` ✅（L70-77）
- `exitGame()` 调 `sys.exit()` ✅（L96）
- 全局状态 4 个可变全局 ✅

### 1.4 受重构影响的调用方

仓库内**没有**示例 / 测试 / CI 引用这些 API，唯一的"调用方"是 README 的散文描述。因此兼容风险只来自仓库外的用户代码，按第 5 节策略兜住。

---

## 2. 目标结构

### 2.1 最终目录

```text
EScurses/
├── escurses/
│   ├── __init__.py         # 公开 API 汇总、__all__、__version__、旧函数兼容层
│   ├── config.py           # Config / 全局默认配置（strict 等）
│   ├── exceptions.py       # 异常体系
│   ├── unicode_utils.py    # 显示宽度、grapheme 切分（lru_cache）
│   ├── buffer.py           # Cell / Buffer / DoubleBuffer
│   ├── screen.py           # 坐标系、绘制 API、裁剪与 strict
│   ├── renderer.py         # 差异渲染、ANSI 输出、渲染统计
│   ├── terminal.py         # 终端状态保存/恢复、备用屏幕、能力探测
│   ├── input.py            # 统一输入接口 + ESC/CSI 解析器
│   ├── _win_input.py       # Windows 后端（仅在 nt 导入）
│   ├── _posix_input.py     # POSIX 后端（仅在 posix 导入）
│   ├── timing.py           # Clock：目标 FPS、帧时间、无忙等
│   └── game.py             # Game 生命周期、主循环、旧 API 入口
├── tests/                  # unittest，9 个测试模块
├── examples/               # 2 个可运行示例（现代 API / 旧 API）
├── benchmarks/             # 新旧实现对比基准 + 旧实现副本
├── audit/                  # 审计期探针（保留）
├── CODE_AUDIT.md
├── REFACTOR_PLAN.md
├── REFACTOR_REPORT.md
├── README.md / README-zh.md
└── pyproject.toml
```

### 2.2 相对建议结构的 3 处调整（及原因）

| 调整 | 原因 |
|---|---|
| 新增 `_win_input.py` / `_posix_input.py`（私有） | 任务要求"不允许 Windows 专用模块影响 Linux/macOS 导入"。把 `msvcrt` / `termios` 放进**按平台惰性导入**的私有模块，是唯一能让"导入期零平台副作用"可被测试断言的做法。`input.py` 只做统一接口 + 解析。 |
| 不建 `color.py` | Iteration A 不实现颜色系统。颜色**能力探测**放在 `terminal.py`（它本来就负责能力探测），暴露 `color_support()`。避免造一个空模块。 |
| 不建 `events.py` / `scene.py` / `layer.py` / `animation` | 明确属于 Iteration B。本次只保留 `Game.on_resize` 一个回调钩子，够用且不构成事件总线。 |

### 2.3 模块职责与依赖方向

```
__init__ ──> game ──> terminal, input, renderer, screen, timing, config
                 └─> screen ──> buffer, unicode_utils, exceptions
                     renderer ──> screen, terminal
                     input ──> _win_input | _posix_input
                     timing ──> (stdlib only)
```

**硬约束**（写进代码注释，靠 review 保证）：

1. `buffer.py` / `screen.py` 不 import `terminal` —— 缓冲区不知道终端存在。
2. `renderer.py` 不 import `input` —— 渲染不读键盘。
3. 任何模块不 import `game.py`（避免循环）。
4. 平台相关代码只允许出现在 `terminal.py`（Windows 控制台模式）与两个私有后端里。

---

## 3. 关键技术设计

### 3.1 坐标系：全库统一 1-based

左上角 = `(1, 1)`，与旧 API、README、ANSI CUP 一致。内部 `Buffer` 用 0-based 索引，**只在 `Screen._resolve()` 一处做转换**，其余代码不得出现 `-1` 偏移。

> 选择理由：任务书给出的期望 API 是 `drawChar(5, 3, "P", fg=...)`，即旧语义；若新 API 改 0-based，Iteration B 给 `drawChar` 加颜色参数时会出现"同一个函数两套坐标"的灾难。

### 3.2 单元格模型（宽字符正确性核心）

```python
@dataclass(frozen=True)
class Cell:
    char: str = " "   # 一个 grapheme cluster（可含组合标记）
    width: int = 1    # 该 cluster 占的终端列数：1 或 2
    cont: bool = False  # True = 本格是左侧宽字符的右半，不写入任何字符
```

宽字符写入的**不变量**（`Screen.set` 内的一次性维护，O(1)）：

1. 写 `(x,y)` 前，先"拆链"：若 `(x,y).cont` 则把 `(x-1,y)` 抹成空格；若 `(x,y).width == 2` 则把 `(x+1,y)` 抹成空格。
2. 写入 `(x,y)`；若 `width == 2`，同时把 `(x+1,y)` 标为 `cont`。
3. `width == 2` 但 `x` 是最后一列 → 整字裁剪（不写入），保证不会溢出。

由此得到渲染侧可依赖的引理：**宽字符的左右两格永远同时变化**，因此差异区间的起点/终点只需各向外扩一格即可避免"写半个字"。

### 3.3 双缓冲：copy-sync，而不是指针交换

关键设计判断：旧库的 `drawChar` 是**持久场景语义**（画上去的字符不会自动消失，只有 `clear()` 才清）。若每帧结束后交换并清空 back，旧代码的静态画面会全部闪烁消失 —— 这是不可接受的兼容破坏。

因此：

- `back` 是**持久**的场景缓冲，`drawChar` 只改它，不自动清空。
- `front` 是"终端上现在真实显示的内容"。
- `present()` 算 `diff(back, front)` → 输出 → **把 back 逐行复制进 front**（Cell 不可变，复制行列表即可，1920 格约 40μs）。
- `clear()` 清 back 并置 `needs_full_redraw`。
- `invalidate()` 只作废 front（终端被外部写坏 / 尺寸变化）。
- 刷新抛异常时**不提交**，front 保持旧值，下一帧自动重试。

### 3.4 差异渲染算法

```
for y in rows:
    if back_row == front_row: continue          # C 级 list 比较，零成本
    找首/尾差异下标 → 得到若干变化区间
    相邻区间间隔 ≤ 2 列时合并                      # 少发一次定位
    区间端点向外扩，避免切断宽字符
    --- 输出 ---
    区间起点：cursor 已在位则跳过；
             同一行且间隔 ≤ 3 列用 CUF(\033[nC)，否则 CUP(\033[y;xH)
    区间内：跳过 cont 格，直接顺序写 cluster（宽字符自动前进 2 列）
```

**首次渲染 / `invalidate()` 后**：走另一条路径 —— `\033[H\033[2J` 清屏，然后只对"非空行"发一条 CUP + 整行内容（去掉行尾空格）。这比逐格重绘少一个数量级，也保证残影被清掉。

**自动换行**：会话期间发 `\033[?7l` 关闭 DECAWM，退出时恢复。这样写最后一列不会触发滚屏（对应 REND-03），也不需要"少一格"的畸形缓冲区。

**非 TTY 降级**：`Terminal.ansi == False` 时完全不发转义序列，只按行输出纯文本（仅变化行），满足 Q2"纯文本模式不输出颜色转义"。

### 3.5 输入

- `InputBackend` 抽象：`open/close/read_available/wait/supports_key_up`。
- POSIX：会话级 `cbreak`（保留 ISIG，Ctrl+C 永远能救场）+ `os.read()` 绕过 `TextIOWrapper` 缓冲（修复 INP-03 丢键）。
- Windows：`kbhit()` + `getwch()`，识别 `\x00` / `\xe0` 前缀并**立即**读第二字节（修复 INP-04）。
- 解析器与后端解耦：`ESC`/CSI/SS3/Windows 前缀统一在一个纯函数解析器里，可用假后端完整单测。
- ESC 单独出现时用 `esc_timeout`（默认 30ms）判定，不阻塞主循环。
- `supports_key_up`：Windows 理论可探测（`ReadConsoleInput`），POSIX 不能 → **本次两者都返回 False 并在文档声明**，不实现假的 KEY_UP。

### 3.6 计时

`Clock` 维护 **deadline 累加器**（`next += period`）消除累积漂移：

```
start():  记录 last_start；首次或落后超过一帧则 resync（不做追帧爆发）
sleep():  remaining = next - now; remaining > 0 → time.sleep(remaining)
          spin 默认 0（纯 sleep，无忙等）
```

- `fps` 校验 `0 < fps <= 1000`，否则 `InvalidConfigError`。
- 暴露 `dt` / `fps_estimate` / `max_dt` / `overrun_frames`，够用即可，不做统计框架。

### 3.7 终端生命周期

`Terminal` 维护**逐项标志**（`_alt_on` / `_cursor_hidden` / `_wrap_off` / `_termios_saved` / `_vt_enabled`），`restore()` 按逆序逐项还原并把标志置 False → **幂等**。

初始化用 `try/except` 包裹：中途失败时对已生效的项立刻 `restore()`，再抛 `TerminalError`。

`Game` 是上下文管理器，`__exit__` 无条件 `close()`；`close()` 幂等；不调用 `sys.exit()`。

---

## 4. 兼容策略

### 4.1 保留（零成本）

`escurses.py` 删除，改建同名包，`__init__.py` 重新导出全部 11 个旧函数名，参数顺序与含义不变。旧代码 `import escurses; escurses.drawChar(5,3,'P')` 无需改动。

### 4.2 语义有变化（必须告知，写入 `MIGRATION` 段）

| # | 旧行为 | 新行为 | 理由 | 缓解 |
|---|---|---|---|---|
| 1 | 可写宽度 = 终端宽度 − 1 | = 终端宽度 | DRAW-02，P0 | 靠 `?7l` 关自动换行解决根因；旧代码本来就在写越界 |
| 2 | `drawChar(-1,-1)` 静默写错位置 | 默认裁剪；`strict=True` 抛 `OutOfBoundsError` | DRAW-01，P0 | `OutOfBoundsError` 继承 `IndexError`，旧 `except IndexError` 仍生效 |
| 3 | `drawChar` 接受多字符/空串 | 空串写入空格；多字符按 grapheme 序列**顺序**写入（等价 `drawText`） | DRAW-04，P0 | 旧写法 `drawChar(1,1,'AB')` 从"整行错位"变成"正确写两个格" |
| 4 | `exitGame()` 清屏 + `sys.exit()` | 恢复终端（含退出备用屏幕）+ `SystemExit(0)`，**不再清屏** | LIFE-03/04，P0 | 旧脚本"调完就结束"的行为不变；`Game` 的 `close()` 不退出进程 |
| 5 | 首帧前需手动 `cursorEcho(False)` | `initGame()` 自动隐藏光标 | LIFE-01 | 旧代码再调一次也无害（幂等） |
| 6 | `initGame()` 不进备用屏幕 | 进备用屏幕（可为 `initGame(alt_screen=False)`） | Q3 | 新增可选关键字参数，旧调用不受影响 |

**唯一无法兼容**：直接访问 `escurses._screen` 等内部变量。README 已声明不建议使用，风险可接受。

### 4.3 废弃

无。本次不删任何旧 API。

---

## 5. 实施顺序（7 阶段，每阶段可独立验证）

| 阶段 | 内容 | 验收 |
|---|---|---|
| P1 架构准备 | 读代码、`REFACTOR_PLAN.md`、git 基线 | 本文件 |
| P2 底层结构 | `exceptions` / `config` / `unicode_utils` / `buffer` / `screen` | `tests/test_unicode.py` `test_buffer.py` `test_screen.py` 全绿 |
| P3 终端与输入 | `terminal` / `input` / 后端 | `test_terminal.py` `test_input.py` 全绿；异常退出可恢复 |
| P4 渲染 | `renderer` 双缓冲 + diff | `test_renderer.py` 全绿；静止帧输出 < 50 字节 |
| P5 计时 | `timing` + `game` 主循环 | `test_timing.py` 全绿；60/30 FPS 实测达标 |
| P6 兼容与测试 | `__init__` 兼容层、`test_compat.py`、跨平台导入检查 | 全量 `unittest` 绿 |
| P7 性能与交付 | `benchmarks/`、README、`REFACTOR_REPORT.md` | 有真实对比数据 |

---

## 6. 风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| 差异渲染产生残影/错位 | **高** | 宽字符不变量 + 区间端点外扩 + `invalidate()` 兜底 + 专项测试（写入宽字符后立刻改右邻、整行覆盖、清屏后重绘） |
| 双缓冲破坏旧库"持久场景"语义 | **高** | 采用 copy-sync 而非交换，back 永不自动清空（见 3.3） |
| POSIX 输入修复无法在本机验证 | **高** | 本机仅 Windows。解析器与后端解耦，用假后端覆盖解析逻辑；termios 路径标注"未实机验证"，并在报告中明确 |
| 终端恢复不彻底污染 shell | 中 | 逐项标志 + 逆序还原 + 幂等 + `try/except` 兜底；单测断言还原序列 |
| 备用屏幕导致最后一帧消失 | 中 | 文档说明用 `wait_for_key()` 停留；`initGame(alt_screen=False)` 可关 |
| `?7l` 在个别终端不支持 | 低 | 关闭失败无副作用（未知模式序列被忽略）；保守起见仍按满宽写入 |
| 性能数字被误读为"必然提升" | 低 | 报告严格区分"实测 / 理论预期 / 未验证"，并写明测试环境 |

---

## 7. 本次明确不做

颜色输出、图层、场景管理、事件总线、动画、鼠标、KEY_UP、三个示例游戏、PyPI 发布、CI 配置。
（`pyproject.toml` 只做最小打包描述，不配 CI 矩阵。）
