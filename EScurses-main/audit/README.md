# audit/ — 审计期探针脚本（历史存档）

这四个脚本是 `CODE_AUDIT.md` 的证据来源，**针对重构前的 0.1 版单文件实现**编写，
用于测量旧代码的边界行为、输出量和帧率。

它们**不能**在 0.2.0 上运行：脚本直接读写旧实现的内部变量
（`escurses._screen`、`escurses._screenWidth`、`escurses.sleepForFrame` 的旧语义），
这些变量在新版本中已经不存在。

## 复现方式

```bash
git worktree add ../escurses-0.1 e54fb06     # 或 git checkout e54fb06
cd ../escurses-0.1 && python audit/probe.py
```

## 脚本清单

| 脚本 | 作用 | 结论去向 |
|---|---|---|
| `probe.py` | 边界、负坐标、宽字符、未初始化调用的实际行为 | CODE_AUDIT 3.3 节 |
| `bench.py` | `refresh()` 每帧输出字节数与序列化耗时 | CODE_AUDIT 3.1 节 |
| `fps.py` | `frameStart + sleepForFrame` 的稳态帧率 | CODE_AUDIT 3.2 节 |
| `sim.py` | 三种输出策略的离线字节量原型 | CODE_AUDIT 3.1 节（预期收益） |

新版本的对比基准在 `benchmarks/`，那才是可以长期运行的脚本
（它自带旧实现的逐字节副本 `_legacy_escurses.py`）。
