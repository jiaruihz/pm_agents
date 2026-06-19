# PM Agent — 项目上下文（Codex）

> 工作风格见全局 `~/.codex/AGENTS.md`（做完再交 / 缺数据自己补 / 先修根因 / 信息够了就动手 / 输出给人看）。
> 本文件与 `CLAUDE.md` 应保持核心规范一致（两份共享主体，改一处同步另一处）。
> 历史 WSL 宿主命令约定（`wsl -d Ubuntu-24.04 -- ...`）已退场，需要时查 git 历史；当前机器是 Mac。

## 0. 当前主线

天气温度策略。**README 描述的是旧 PMM/ARB 框架，已不是活跃主线，别被它误导。**

## 1. 系统主轴：一条量化血缘，所有工作都挂上去

本项目不是一堆独立脚本，是一个有完整血缘的量化系统：

```text
signal candidate → plan → order → fill → settlement
```

canonical 事实表：`fact_signal_candidates`（机会粒度）、`fact_trades`（成交粒度），
由 `weather_dashboard/legacy_migration/*` 从 N100 镜像重建到 `runtime/weather.db`。

这条血缘（含 `order → fill → live/shadow 对比 → PnL → strategy_config 参数 → 看板`）是**基础设施，与具体策略无关**：
换策略方向只动上层信号/特征，不重做这条链。暂时不用的策略/底表是 dormant（保留备用），不是 dead，不归档不删。

**做任何新分析 / 脚本 / 特征 / 看板前，先定位它在血缘哪一层：**
- 读数据 → 从 canonical 表读，不绕过去自算 fill / PnL / 漏单 / 滑点。
- 产新信号 / 特征 → 挂进 `fact_signal_candidates` 机会粒度，不另建并行的一次性表。
- 不确定结构往哪挂 → 先读 `WEATHER_STRATEGY_QUANT_DESIGN.md`，别先写脚本。

完整设计 [WEATHER_STRATEGY_QUANT_DESIGN.md](docs/WEATHER_STRATEGY_QUANT_DESIGN.md) ·
字段契约 [WEATHER_SYSTEM_CONTRACT.md](docs/WEATHER_SYSTEM_CONTRACT.md)

## 2. 机器与角色（摘要，细节见 [WEATHER_REPO_BOUNDARY.md](docs/WEATHER_REPO_BOUNDARY.md)）

- **本机 = Mac** `/Users/deepsleep/projects/pm_agents`：分析 / 看板 / 回测 / 脚本开发 / N100 部署 staging。默认 `zsh`/Darwin，**不要套 `wsl`**。
- **N100** `jiarui@192.168.0.200`，两个 repo 各管一块：
  - `weather-predict`（systemd timer）：snapshot / orderbook / pm_history / GFS / 观测 cache —— 信号原料与行情。
  - `pm_agent`（foreground loop）：读信号 → plan → CLOB 下单 → live JSONL / live_cycle —— 实盘执行。
- 本机只读 N100 镜像做分析，**不作生产采集 / 下单来源**。N100 访问：`ssh jiarui@192.168.0.200 '<command>'`。
- 判断生产是否断流先看 N100 doctor（`ssh ... 'cd ~/projects/weather-predict && scripts/ops/doctor_restart.sh'`），不要用本机镜像新旧直接判断。

数据流、镜像目录逐条映射、备份 → [WEATHER_DATA_PIPELINE.md](docs/WEATHER_DATA_PIPELINE.md) ·
[WEATHER_DATA_CANONICAL_SOURCES.md](docs/WEATHER_DATA_CANONICAL_SOURCES.md) · [OPS_RUNBOOK.md](docs/OPS_RUNBOOK.md)

## 3. 硬边界与工程姿态

**硬边界（不可逆操作，必须守）**：涉及 N100 live / 私钥 / 余额 / 真实 CLOB 下单 / 删数据 / 远端部署，
保留显式确认、暂停开关、notional 上限、可追溯日志。生产行为变更（city_pools / paper_policy /
execution_policy / live_cycle）走 [weather-strategy-deploy] 的 **git-first** 流程，**不许 `scp`/`rsync` 直推**
——直推绕过版本审计，事故无法回溯。

**工程姿态（个人研究项目，默认直接推进）**：少写与当前目标无关的防御性兜底；**显式失败优于静默 fallback**
（数据缺失 / 字段不一致 / 盘口不可用时报错暴露原因，不偷偷换旧字段旧数据继续跑）；兼容逻辑要标注删除时机。
**但"少兜底" ≠ 绕过资金安全或不可逆操作的硬边界。**

## 4. 研究防跑偏

用户提出的往往是一个很具体的失败模式 / 交易形态。先把问题收敛成一句明确的 **target metric / target slice**，
再跑数据写脚本：先复述目标指标 → 先锁分母（事前持仓形态 vs 事后结算形态，不混用）→ 结论先给交易动作
（保留 / 过滤 / 降 size / shadow / 不改 live）再给证据。**不要擅自把"某个坏场景怎么优化"扩大成"整个分支砍不砍"。**
发现口径漂移立刻暂停纠正，不继续堆结果。

## 5. 分析必走的 skill + 硬口径（细则见 [WEATHER_ANALYSIS_CONTRACT.md](docs/WEATHER_ANALYSIS_CONTRACT.md)）

weather 分析请求先 invoke 对应 skill，别直接写一次性 pandas 脚本：

| 触发 | skill |
|---|---|
| 绩效 / PnL / ROI / 胜率 / 切片 / 对比 / 回测结果 | `weather-strategy-performance` |
| 单日血缘 / 逐笔复盘 / 为什么下这单 | `weather-strategy-lineage` |
| 持仓 / 敞口 / 未结算 / 风险 | `weather-strategy-exposure` |
| 余额 / 钱包 / USDC / CLOB 对账 / fill 对不上 | `weather-live-account-reconcile` |
| 部署 / 上线策略 / 城市池 / 参数 / T1/T2 | `weather-strategy-deploy` |
| 补全 / 重建底表 / 同步 / 数据陈旧 / 重新结算 | `weather-fact-rebuild` |

发布任何 `live_real` PnL / ROI / 曲线前的硬 gate（踩过坑换来的，不是形式）：
- 先跑 5 行 SQL 自检（数据新鲜度 / trade_class 分布 / 结算 / 机会覆盖 / 订单成交）。
- `python3 scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py` 必须 `gate_pass=true`，否则停下先修数据链。
- 现金流 / 余额用 `fill_date_bj`，**不用 `order_date_bj`**（后者受回填污染，只作下单归属诊断）。
- `submitted_notional` / `posted_notional` / `actual_fill_cost` / `open_cost` / `realized_pnl` 分开报；open cost 不是亏损。
- 已结算才报 `pnl_usd_at_fill`；未结算只报 MTM 并附 `val_snapshot_ts_utc`，估值旧就明说旧。
- pm_history near-binary `0.9995/0.0005` 必须归一化为 `1/0`（旧报告需重算）。
- 不读 `runtime/_legacy/*.db` 等退役库；不绕过 `fact_trades` 自算 fill PnL、不绕过 `fact_signal_candidates` 自算成交质量。

## 6. 高频命令

```bash
# 看板（建库+ingest+API+FE；已有库加 --no-rebuild；仅看状态 --status）
scripts/weather_dashboard/run_stack.sh [--no-rebuild|--status|--api-only|--fe-only]
#   入口 http://localhost:5173/weather/runs · /weather/live · http://localhost:8000/docs

# 同步 N100 镜像（分析"最新/今天"前先同步；--dry-run 预演）
scripts/ops/sync_weather_remote.sh [--dry-run]
```

读 `runtime/weather.db`（WAL，只读）：不要用无界交互式 sqlite，不在只读连接里跑 checkpoint/WAL 修复 PRAGMA。
```bash
sqlite3 -batch -cmd ".timeout 1000" runtime/weather.db "SELECT COUNT(*) FROM fact_signal_candidates;"
```
```python
conn = sqlite3.connect("file:runtime/weather.db?mode=ro", uri=True, timeout=1.0)
conn.execute("PRAGMA query_only=ON"); conn.execute("PRAGMA busy_timeout=1000")
```

## 7. 文档入口

完整索引与权威性分级：[WEATHER_DOCS_INDEX.md](docs/WEATHER_DOCS_INDEX.md)（新增 / 归档文档只维护那里）。
最高频：[STRATEGY_ENTRYPOINT](docs/WEATHER_STRATEGY_ENTRYPOINT.md)（实盘接手）·
[STRATEGY_REGISTRY](docs/WEATHER_STRATEGY_REGISTRY.md)（试过哪些策略/状态/血缘归属）·
[CITY_POOL_DECISIONS](docs/WEATHER_CITY_POOL_DECISIONS.md) · [ANALYSIS_CONTRACT](docs/WEATHER_ANALYSIS_CONTRACT.md) ·
[DATA_CANONICAL_SOURCES](docs/WEATHER_DATA_CANONICAL_SOURCES.md) · [REPO_BOUNDARY](docs/WEATHER_REPO_BOUNDARY.md) ·
[EDGE_ENGINE_CURRENT_STATE](docs/WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md)。

> 早期那组"盈利模式"**数字**（5 月 BUY_NO 胜率 / Warsaw / ECMWF / LA）是 near-binary 修复前口径，**已作废**——
> 但这是数字作废，不是方向被否（BUY_NO 等是 unconfirmed，不是 disproven）。各策略当前状态/灵感/血缘归属见
> [STRATEGY_REGISTRY](docs/WEATHER_STRATEGY_REGISTRY.md)，口径背景见
> [LIVE_RUN_HISTORY §1.1](docs/WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md)。
