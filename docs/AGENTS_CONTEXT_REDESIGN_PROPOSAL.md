# 项目常驻上下文重构提案（草稿，未应用）

Status: `design-draft`
Updated: 2026-06-19
Source of truth: 否（这是提案，认可后才改 AGENTS.md / CLAUDE.md）

本文档解决一个根因问题：常驻上下文（`AGENTS.md` / `CLAUDE.md`，各 ~430/500 行、154 行逐字重复、
64 处"禁止/必须"）把预算花在**重复的数据目录琐事 + 禁止清单**上，而真正该常驻的
**量化血缘系统设计**（`WEATHER_STRATEGY_QUANT_DESIGN.md`）从不在默认上下文里。
结果：agent 每个任务局部做得对，但不挂到整体血缘，缺整体性。

目标：把常驻层从"参考手册 + 禁止墙"改成"**系统主轴 + 判断原则 + 指针**"，每份砍到 ~150 行。

---

## Part A — 整合地图：现有常驻内容该去哪

| 现常驻内容 | 去向 | 为什么 | 关键节点? |
|---|---|---|---|
| 天气策略是当前主线（非 README 旧框架） | **留常驻**（1 行） | 防被 README 误导，必读 | 是 |
| 量化血缘 / fact 表 / 整体设计 | **留常驻（新增主轴段）** | 当前缺失，正是病根 | 是 |
| 4 角色拓扑 + 数据流图 | 常驻留**3 行摘要 + 指针** | 细节已在 `WEATHER_REPO_BOUNDARY.md` | 是（摘要） |
| 镜像目录逐条映射表（market_data/remote_pm_agent 全清单） | **移走** → `WEATHER_DATA_PIPELINE.md` / `WEATHER_DATA_CANONICAL_SOURCES.md` | 已在那两份里有权威版，常驻重复 | 否 |
| 安全边界（钱/N100/私钥/CLOB/删数据/部署） | **留常驻**，改写成"规则+为什么" | 不可逆操作硬边界 | 是 |
| 分析强制规约 + skill 路由表 | **留常驻**（表保留），细则指针化 | 强制 skill 调用是流程决策 | 是 |
| 5 行 SQL 自检 / fill 对账 gate / order_date_bj 禁用 / near-binary 勘误 | 常驻留**1 行硬规则 + 指针** | 口径权威版在 `WEATHER_ANALYSIS_CONTRACT.md` | 是（指针保留硬性） |
| Host-aware 命令（Mac zsh / 历史 WSL） | 常驻留 **Mac 段**，WSL 整段移历史 | 当前机器是 Mac，WSL 是历史包袱 | 否 |
| `run_stack.sh` 一键启动 + 入口 URL | 常驻留**命令块**（高频） | 高频操作入口 | 否 |
| `sync_weather_remote.sh` 同步入口 | 常驻留**命令** | 高频 | 否 |
| 备份策略全段 | **移走** → `OPS_RUNBOOK.md` | 低频运维 | 否 |
| 盈利查询方法 1/2（settle_t24_paper 用法） | **移走** → `weather-strategy-performance` skill / runbook | how-to，非常驻 | 否 |
| "已知的盈利模式"（BUY_NO>YES、Warsaw、ECMWF…） | **移走** → 对应 analysis living doc | 时间点快照，非不变量，且可能过时 | 否（且过时风险） |
| T2 天气补全入口与产物口径 | **移走** → `WEATHER_DATA_PIPELINE.md` | 低频流程细节 | 否 |
| weather.db WAL 只读可靠性约定（仅 AGENTS.md 有） | 常驻留**精简版** | 高频读库陷阱 | 否（实用） |
| 文档索引段 | 常驻留**指针**到 `WEATHER_DOCS_INDEX.md` | 索引已是唯一来源 | 否 |
| 工作风格（已写进全局 `~/.codex/AGENTS.md`） | **不在项目层重复**，只 1 行指针 | 全局已覆盖 | 否 |

净效果：~430/500 行 → ~150 行；64 处"禁止" → 约 12 条真不变量（带 why）+ 其余转判断/指针。

---

## Part B — 提议的新常驻骨架（AGENTS.md / CLAUDE.md 共用主体）

> 两份文件共享同一主体；唯一差异是开头一行（Codex 版指 CLAUDE.md，Claude 版指 AGENTS.md）
> 和工作风格指针（Codex 指 `~/.codex/AGENTS.md`）。其余逐字一致，靠这份提案保证同步。

```markdown
# PM Agent — 项目上下文

> 工作风格见全局 ~/.codex/AGENTS.md（做完再交 / 缺数据自己补 / 先修根因 / 输出给人看）。
> 另一份对应文件（CLAUDE.md ↔ AGENTS.md）应与本文件核心规范一致。

## 0. 当前主线
天气温度策略。**README 描述的是旧 PMM/ARB 框架，已不是活跃主线，别被它误导。**

## 1. 系统主轴：一条量化血缘，所有工作都挂上去
本项目不是一堆独立脚本，是一个有完整血缘的量化系统：
  signal candidate → plan → order → fill → settlement
canonical 事实表：`fact_signal_candidates`（机会粒度）、`fact_trades`（成交粒度），
由 `weather_dashboard/legacy_migration/*` 从 N100 镜像重建到 `runtime/weather.db`。

做任何新分析/脚本/特征/看板前，先定位它在血缘哪一层：
- 读数据 → 从 canonical 表读，不绕过去自算 fill/PnL/漏单。
- 产新信号/特征 → 挂进 fact_signal_candidates 机会粒度，不另建并行一次性表。
- 不确定结构往哪挂 → 先读 WEATHER_STRATEGY_QUANT_DESIGN.md，别先写脚本。

完整设计 WEATHER_STRATEGY_QUANT_DESIGN.md · 字段契约 WEATHER_SYSTEM_CONTRACT.md

## 2. 机器与角色（摘要，细节见 WEATHER_REPO_BOUNDARY.md）
- 本机 = Mac `/Users/deepsleep/projects/pm_agents`：分析/看板/回测/部署 staging。默认 zsh，别套 wsl。
- N100 `jiarui@192.168.0.200`：两个 repo — weather-predict（采集原料）+ pm_agent（实盘执行）。
- 本机只读镜像分析，不作生产采集/下单来源。
数据流与镜像目录映射 → WEATHER_DATA_PIPELINE.md / WEATHER_DATA_CANONICAL_SOURCES.md。

## 3. 硬边界（不可逆操作，必须守）
涉及 N100 live / 私钥 / 余额 / 真实 CLOB 下单 / 删数据 / 远端部署：
保留显式确认、暂停开关、notional 上限、可追溯日志。**少兜底 ≠ 绕过资金安全。**
生产行为变更（city_pools / paper_policy / execution_policy / live_cycle）走
`weather-strategy-deploy` 的 git-first 流程，**不许 scp/rsync 直推**——因为直推会绕过版本审计，事故无法回溯。

## 4. 分析必走的 skill + 硬口径（细则见 WEATHER_ANALYSIS_CONTRACT.md）
weather 分析请求先 invoke 对应 skill（表见下），别直接写一次性 pandas 脚本。

| 触发 | skill |
|---|---|
| 绩效/PnL/ROI/胜率/切片/对比 | weather-strategy-performance |
| 单日血缘/逐笔复盘 | weather-strategy-lineage |
| 持仓/敞口/未结算 | weather-strategy-exposure |
| 余额/钱包/CLOB 对账 | weather-live-account-reconcile |
| 部署/城市池/参数上线 | weather-strategy-deploy |
| 补全/重建底表/同步 | weather-fact-rebuild |

发布任何 live_real PnL/ROI/曲线前的硬 gate（这些是踩过坑换来的，不是形式）：
- 先跑 5 行 SQL 自检（数据新鲜度/类别/结算/覆盖/订单成交）。
- `weather_clob_fill_coverage_gate.py gate_pass=true`，否则停下先修数据链。
- 现金流/余额用 `fill_date_bj`，**不用 `order_date_bj`**（后者受回填污染）。
- submitted/posted/actual_fill_cost/open_cost/realized 分开报；open cost 不是亏损。
- 已结算才报 `pnl_usd_at_fill`，未结算只报 MTM 并附 `val_snapshot_ts_utc`。
- pm_history near-binary `0.9995/0.0005` 必须归一化为 `1/0`（旧报告需重算）。
- 不读 `runtime/_legacy/*.db` 等退役库。

## 5. 高频命令
看板：`scripts/weather_dashboard/run_stack.sh [--no-rebuild|--status|--api-only|--fe-only]`
  入口 5173/weather/runs · 5173/weather/live · 8000/docs
同步 N100 镜像：`scripts/ops/sync_weather_remote.sh [--dry-run]`（分析「最新」前先同步）
读 weather.db（WAL，只读）：`sqlite3 -batch -cmd ".timeout 1000" runtime/weather.db "..."`
  Python：`sqlite3.connect("file:...?mode=ro", uri=True, timeout=1.0)` + `PRAGMA query_only=ON`

## 6. 文档入口
完整索引与权威性分级：WEATHER_DOCS_INDEX.md（新增/归档文档只维护那里）。
最高频：STRATEGY_ENTRYPOINT（实盘接手）· CITY_POOL_DECISIONS · ANALYSIS_CONTRACT ·
DATA_CANONICAL_SOURCES · REPO_BOUNDARY · EDGE_ENGINE_CURRENT_STATE。
```

---

## Part C — 我承诺不动的关键节点（认可前请核对）

重构只搬运/改写措辞，**不改变任何决策或口径**。以下逐条保留（位置可能从常驻挪到指针，但硬性不变）：

1. 天气策略为主线、README 作废 —— 保留。
2. 量化血缘 canonical 表口径（fact_signal_candidates / fact_trades）—— 保留并升为主轴。
3. 4 角色拓扑与"本机不作生产源"—— 保留（摘要 + 指针）。
4. 所有资金/N100/不可逆操作硬边界 —— 逐条保留。
5. git-first 部署、禁止 scp/rsync 直推生产 —— 保留。
6. 强制 skill 路由表 —— 保留。
7. fill 对账 gate、5 行自检、order_date_bj 禁用、submitted/posted/actual 分拆、settled vs MTM、
   near-binary 归一化、不读 legacy DB —— 全部保留为硬规则（细则指向 ANALYSIS_CONTRACT）。
8. weather.db WAL 只读约定 —— 保留。
9. Host-aware：本机 Mac/zsh、N100 ssh 直连 —— 保留（WSL 历史段移到附录/历史文档）。

**被移走（非删除）的内容** 全部进已有权威文档（DATA_PIPELINE / CANONICAL_SOURCES / REPO_BOUNDARY /
ANALYSIS_CONTRACT / OPS_RUNBOOK / 对应 skill），不丢信息，只是不再在常驻层重复。
唯一**真正删除**的候选是「已知的盈利模式」那种过时时间点快照——但也会先迁进 analysis living doc 留档，不直接丢。

---

## Part D — 落地分片（每片一个 commit，可回滚）

1. **本提案**（当前）——你 review 整体形态与主轴段措辞。
2. 认可后：用 Part B 骨架替换 AGENTS.md / CLAUDE.md 主体，被移内容确认已在目标文档中（缺的先补过去）。
3. 禁止分诊单独一片：把残留"禁止"逐条标为"真不变量(留)"或"反射式(转判断/删)"。
4. 与并行线程（脚本 triage 第 2 遍、5 个归档候选）汇合收尾。

> 注意：AGENTS.md / CLAUDE.md 当前在本分支带着 codex 的待提交改动（51/59 行）。
> 应用第 2 片前先确认那些改动是否要先并入或单独提交，避免我的重写与其纠缠。
```
