# Weather Strategy — Quant System Design

**Status:** Core architecture implemented (DB + ingest + API + frontend MVP). See `docs/WEATHER_DASHBOARD_DATA_MODEL_AUDIT.md` for current gaps vs implementation.
**Date:** 2026-05-15 (moved/retitled 2026-05-17)
**Scope:** 量化血缘链架构 / 策略身份与配置管理 / Run Registry / experiment tracking DB / API / 前端结构。这是天气策略量化系统的核心架构设计文档，不只是 dashboard。

Early live rollout history and backfill governance: [WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md](WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md)

**Table of Contents:**
- §0 Goals
- §1 Background — 量化系统设计沉淀（数据契约、策略身份、Run Registry、指标层次、异常值防御、通用 + Polymarket 难点）
- §2 Database Schema（SQLite, 容量分析, 韧性, DDL, 索引, trigger）
- §3 Strategy / Model Management（YAML config, 命名 + tag 规约, model/universe SOP, paper-live 配对, supersede）
- §4 Frontend Structure（IA, 双语显示, 5 个页面, 共享组件, 数据流）
- §5 API Surface（汇总）
- §6 Implementation Plan Pointer（PR 拆解）

---

## 0. Goals

1. **Compare** — 任意 N 个策略 run（不同参数 / 不同时间窗 / paper vs live）并排对比 PnL、ROI 与风险指标，支持按维度切片。
2. **Live** — 实时持仓、当日 PnL、按 city / bracket 看暴露（直接读 Polymarket 账户 API）。
3. **History** — 单条 trade 时间序列、累计 PnL 曲线、按日 / 城市 / edge 桶 / model 的归因。

策略当前处于探索期，参数维度会持续扩张（止损、maker policy、新 sizing），系统必须对**未来新增字段**鲁棒。

---

## 1. Background — 量化系统设计沉淀

> 这一节是设计的"为什么"。读完它你会理解后面 schema 为什么这么拆、为什么有些字段看起来冗余但其实是不变量。

### 1.1 核心数据契约：单向血缘链

#### 架构图

```
┌────────────────────────────────────────────────────────────────────────────┐
│                       STRATEGY CONFIG REGISTRY                             │
│   config_id = hash(params)                                                 │
│   repro_key  = hash(config + code_version + universe + data_snapshot)      │
│   { pool, price_range, sizing, stop_loss, maker_policy, ... }              │
└────────────────────────────┬───────────────────────────────────────────────┘
                             │  (referenced by plan, never copied)
                             ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                       DATA PIPELINE  (append-only, immutable)              │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  [1] MarketData       point-in-time 行情快照                               │
│      (snapshot)       owner: market_data_fetcher                           │
│         │             store: snapshots/*.json                              │
│         │             key:   snapshot_ts_utc                               │
│         ▼                                                                  │
│  [2] Signal           "在城市 C 对 bracket B 应该买 YES，edge = X"          │
│      (model output)   owner: weather_edge_model                            │
│         │             store: signals.jsonl                                 │
│         │             refs:  snapshot_file + model_version                 │
│         │             key:   signal_id = hash(snapshot+city+bracket+model) │
│         ▼                                                                  │
│  [3] TradePlan        "结合 config，这笔信号要不要下？下多少？目标价多少？" │
│      (planner)        owner: weather_trade_planner                         │
│         │             store: plans table                                   │
│         │             refs:  signal_id + config_id                         │
│         │             out:   desired_shares, target_price, sizing_decision │
│         │             SKIP:  capital_cap / edge_threshold 未满足时不出 plan│
│         ▼                                                                  │
│  [4] Order            提交到 paper 引擎或 live 市场                         │
│      (executor)       owner: weather_order_executor                        │
│         │             store: orders table                                  │
│         │             mode:  snapshot_replay | paper | live                │
│         │             refs:  plan_id                                       │
│         │             INV:   placed_at_utc > signal.snapshot_ts_utc        │
│         ▼                                                                  │
│  [5] Fill             实际成交（可 partial / cancel / expire）              │
│      (market)         owner: paper_engine 或 polymarket_clob               │
│         │             store: fills table                                   │
│         │             status: filled / partial / cancelled / expired       │
│         │             INV:   Σ filled_shares ≤ order.shares                │
│         ▼                                                                  │
│  [6] Position         按 (city × bracket × side) 聚合的净仓                 │
│      (position keeper)owner: position_keeper                               │
│         │             derived view: 不独立写库，由 fills 算出               │
│         ▼                                                                  │
│  [7] Settlement       事件结算 → 实现 PnL                                  │
│      (oracle/poly)    owner: settlement_service                            │
│                       store: settlements table                             │
│                       status: settled / missing_event / missing_bracket    │
│                       out:    final_yes, won, pnl_usd                      │
│                                                                            │
└────────────────────────────┬───────────────────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                       RUN REGISTRY & METRICS                               │
│   run = (config_id × execution_mode × time_window × universe)              │
│   run.metrics JSON: PnL / ROI / settled_ratio / expectancy /               │
│                    PnL concentration / by_bucket / by_city / ...           │
└────────────────────────────┬───────────────────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                       DASHBOARD  (FastAPI + React)                         │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐             │
│   │   Compare    │  │     Live     │  │       History        │             │
│   │ 多 run 对比  │  │  实时持仓    │  │  累计 PnL / 归因切片  │             │
│   └──────────────┘  └──────────────┘  └──────────────────────┘             │
└────────────────────────────────────────────────────────────────────────────┘
```

#### 分层职责表

| # | 层 | 一句话职责 | Owner | 存储 | 关键字段 | 关键不变量 |
|---|---|---|---|---|---|---|
| 1 | **MarketData** | 提供 point-in-time 行情快照 | `market_data_fetcher` | `snapshots/*.json` | `snapshot_ts_utc`, `market_price`, `orderbook` | 文件一旦写不改；`ts_utc` 单调 |
| 2 | **Signal** | model 把快照转成「应该交易」的信号 | `weather_edge_model` | `signals.jsonl` | `signal_id`, `city`, `bracket`, `side`, `model_p_yes`, `edge`, `model_version` | append-only；引用 snapshot 不复制 |
| 3 | **TradePlan** | 应用 strategy_config 决定下不下、下多少 | `weather_trade_planner` | `plans` 表 | `plan_id`, `signal_id`, `config_id`, `desired_shares`, `target_price`, `skip_reason?` | 必引用 `config_id`；skip 也要记录 |
| 4 | **Order** | 把 plan 真实提交（paper / live） | `weather_order_executor` | `orders` 表 | `order_id`, `plan_id`, `mode`, `placed_at_utc`, `price`, `shares` | `placed_at_utc > signal.snapshot_ts_utc`（防 look-ahead） |
| 5 | **Fill** | 记录实际成交（含 partial / cancel） | `paper_engine` / `polymarket_clob` | `fills` 表 | `fill_id`, `order_id`, `filled_shares`, `filled_price`, `status`, `fees_usd` | `Σ filled ≤ order.shares` |
| 6 | **Position** | 按 (city, bracket, side) 算净仓 | `position_keeper` | derived view（由 fills 聚合） | `open_shares`, `avg_cost`, `unrealized_pnl` | 不持久化，永远从 fills 重算 |
| 7 | **Settlement** | 事件落地 → 输赢 → 实现 PnL | `settlement_service` | `settlements` 表 | `target_date`, `bracket`, `final_yes`, `settled_at_utc`, `status` | settle 后不再改；可缺失 |

#### 设计细节解释

**为什么 Position 是 derived view 不入库？**
持仓本质是 fills 的累加视图。如果独立存储，会有「fills 改了但 position 没更新」的不一致风险。所有持仓查询永远从 fills 实时聚合算出（数据量不大，性能足够）。

**TradePlan 为什么要记 "skip"？**
当 capital cap / edge threshold / city tier 让某个 signal **不下单**时，也要记录一条 `plan with skip_reason`——否则回看时只看到「下了单的 plan」，永远不知道「为什么没下」，分析有盲区（例如：capital 用完了导致漏掉好机会）。

**为什么 Order 有 mode 字段而 Signal / Plan 没有？**
Signal 和 Plan 是「策略意图」，和执行模式无关。**同一个 plan 可以同时产生 paper order 和 live order**（做 paper-live 并行验证就需要这个）。Mode 是执行层的属性。

**为什么 Settlement 单独一层而不是 Fill 的字段？**
- 时间错位：fill 立即发生，settlement 几小时到几天后才来
- 来源不同：fill 是订单成交，settlement 是事件结算（不同数据源）
- 可缺失：`missing_event` 是 settlement 状态，不是 fill 状态
- 合并存会让 `pnl_usd` 字段在 fill 表里大部分时间是 NULL，schema 难看

#### 铁律

1. 每一层 **immutable / append-only**——写过不改，重放是回到那个时刻产生新下游
2. 每一层都引用上游 ID（trade ⇒ plan ⇒ signal ⇒ snapshot）
3. **双时间戳**：`event_time`（业务时间，用于 point-in-time）+ `ingest_time`（入库时间，用于运维查问题）
4. 行情数据**引用**不嵌入（指向 snapshot_file，不复制行）
5. 金额一律 **Decimal**（SQLite 用 TEXT 存），不用 float
6. 时间一律 **UTC** 入库，渲染时再转

当前 signal JSONL + ledger CSV 已基本满足这些不变量，血缘链是好底子。

### 1.2 策略身份：四元组而不是单一 ID

| 维度 | 含义 | 例 |
|---|---|---|
| **config** | 参数集合 | `pool, price_range, sizing, stop_loss, maker, ...` |
| **code_version** | 执行代码的 git SHA | `a3f9b2c` |
| **universe** | 数据宇宙：哪些 city / model / 日期 | `universe_v2`（冻结的城市清单） |
| **data_snapshot** | 用了哪一批 snapshot 数据 | `snapshots_2026-05-06_to_2026-05-13` |
| **execution_host** | live 实际发单机器/环境 | `local_pm_agent`, `n100_pm_agent` |

**`repro_key = hash(canonical(config + code_version + universe + data_snapshot + execution_host))`**

- 同一 `repro_key` 跑两次，PnL 必须 bit-by-bit 一致——否则系统有隐藏非确定性，必须排查
- 任何一项变化即为新策略，不要混并算总 PnL
- 对 live 来说，`execution_host` 是策略身份的一部分。本机和 N100 可能有不同 env/proxy/package/scheduler 状态，不能默认视作同一个 run。

**`config_id = hash(canonical(config))`**——给"同样参数不同时间窗 / paper vs live"对比用的子 ID。

> **当前最大缺口**：没有显式记录 `code_version`。改了 planner 代码后旧 ledger 和新 ledger 摆一起，看不出差异是参数变化还是 bug fix。

### 1.3 Run Registry：一个 config 可以有 N 个 run

```
strategy_config (config_id)  ──┐
                               │
                               ├─ run #1: snapshot_replay, 05-06→05-13
                               ├─ run #2: paper_ledger,    05-08→05-13
                               ├─ run #3: live,            05-10→05-13
                               └─ run #4: replay 同窗口（修了 bug 后重跑）
```

**关键设计点：**
- **同 config 多 run** 是常态，不要合并
- **重跑历史** 是新 `run_id`，可以打 tag `supersedes: <old_run_id>`
- **修 bug 重跑**必须打 tag，并标 `code_version` 变化
- 每个 run 有 **lifecycle state**：`explore` / `paper` / `live` / `retired` —— UI 默认隐藏 retired
- **事故/脏历史也要入库但标记质量**：例如 `run_quality=invalid_process_wrong_universe`，不要删除，也不要混入 active candidate。

### 1.4 指标层次（PnL/ROI 只是冰山一角）

```
Per-Trade        : entry_price, edge, pnl, won, worst_loss
Per-Day          : daily_pnl, hit_rate, n_trades, cum_drawdown
Per-Run          : 见 1.5 完整指标集
Per-Config       : 跨 run 稳定性（paper vs live 漂移、不同时间窗的方差）
Per-Portfolio    : 多策略合并、capital utilization、策略间相关性
```

### 1.5 一个 run 必须算的指标集（不只是 PnL/ROI）

| 类别 | 指标 | 用途 |
|---|---|---|
| **规模** | `trades`, `trades_settled`, **`settled_ratio`** | 数据完整性 |
| **核心** | `cost`, `pnl_settled`, `roi_settled` | 主指标 |
| **胜率** | `win_rate`, `avg_win`, `avg_lose`, `expectancy` | sizing 不恒定时必看 |
| **极值** | `worst_loss`, `best_win`, `max_drawdown`, `max_dd_pct` | 风险尾部 |
| **★ 异常值/彩票防御** | `median_trade_pnl`, `top_1_pnl_share`, `top_5_pnl_share`, `top_10pct_pnl_share`, `pnl_trimmed_1pct`, `roi_trimmed_1pct` | 区分 "edge" 和 "luck" |
| **统计可靠性** | `n_trades_per_bucket`, `min_sample_warning` (<30 标灰) | 小样本警告 |
| **稳定性** | `rolling_weekly_pnl`, `weekly_pnl_std` | 看是否每周都赚 vs 一次高潮 |
| **分组归因** | `by_price_bucket`, `by_city`, `by_side`, `by_edge_bucket`, `by_model` | 切片分析 |
| **成本** | `fees_paid`, `pnl_net_of_fees`, `roi_net_of_fees` | 真实可落袋 |

#### 两个最容易被忘的

- **`settled_ratio` < 0.95 的 run 都先不信**——大量未结算时 ROI 是虚的
- **`top_5_pnl_share` > 0.3 的 run 是 luck 不是 edge**——例如 $0.006 进场中奖的彩票单，直接被这个指标抓出来

#### Expectancy 何时有用

$$\text{expectancy} = P(\text{win}) \times \overline{\text{win}} - P(\text{lose}) \times \overline{\text{lose}}$$

仅当 sizing **不是恒定金额**时（fixed shares / tiered / 加止损后）才和 ROI 携带不同信息；fixed amount 下两者严格等价（`expectancy = ROI × avg_cost_per_trade`，每笔成本相同所以是同一个数）。

#### Outlier / 彩票效应

Prediction market 经典陷阱，业内叫 **lottery ticket effect** / **PnL concentration**。

机理：fixed amount sizing 下，$0.006 买进 → `$3.90 / $0.006 = 650 股`。如果中了 → `+$646`，单单贡献了相当于其他 423 单合并的 PnL。**总 PnL 里可能 80% 来自这一单**——这种"赚到的钱"完全是 luck，下次再 replay 没这单 PnL 直接归零。

业内标准四件套：

1. **PnL Concentration**：top_1 / top_5 / top_10% 的 PnL 占比，> 30% 警告
2. **Median vs Mean Trade PnL**：差距大 = 分布偏斜
3. **Trimmed Metrics**：去掉头尾 1% 后的 PnL / ROI，反映"可重复"部分
4. **Lottery Zone 标记**：`<10c` bucket 自动打 `lottery_zone: true`，UI 警告"勿外推"

### 1.6 业内通用难点 + 必须落到设计

| 难点 | 处理 |
|---|---|
| **参数笛卡尔积爆炸** | Run registry 不预生成，tag 系统按需对比 |
| **Paper ↔ Live 漂移** | 同 `config_id` 必须配对存储，UI 自动 diff |
| **Look-ahead bias** | 强制不变量：`order.placed_at_utc > signal.snapshot_ts_utc`，违反则 reject 入库 |
| **重放偏差（无 slippage）** | 在 replay 里建模 fill 概率 + spread 损耗（先记录，下一步建模） |
| **存活者偏差** | universe 一旦冻结不能删，只能 soft-deactivate |
| **多重检验（过拟合）** | 强制区分 `in_sample` / `out_of_sample` / `live`，UI 标注 |
| **样本量太小** | 任何分组小于 N（默认 30）显示警告色，禁止从中下结论 |
| **手续费未建模** | 入库时计算 `fees_paid` 列，所有 PnL 报表默认显示 net of fees |
| **时间稳定性** | 每个 run 自动产出 rolling weekly PnL，UI 给曲线 |
| **执行非确定性** | 用 `repro_key` 校验，bit-level 一致 |
| **运营注释** | runs 表带 `tags JSON` + `notes TEXT`，自由打标 |

### 1.7 Polymarket / 天气策略特有

| 难点 | 处理 |
|---|---|
| **Settlement 异步且可缺失** | `settlement_status` 一等指标，PnL 永远配 `settled_ratio` |
| **Edge 自身漂移** | 区分 `entry_edge`（下单瞬间）vs `peak_edge`（区间最大） |
| **Bracket / city 强相关** | universe 显式声明对冲关系，归因避免重复计 |
| **Price bucket 边界** | bucket 用 left-closed-right-open + 显式定义；`<10c` 自动标 `lottery_zone` |
| **Maker vs taker 不同博弈** | maker policy 是 config 一等字段，maker 单要记录 queue position / unfilled risk |
| **Model version 漂移** | `model_version` 进 universe，混了就是不同 run |

### 1.8 几个让系统"可信"的工程不变量

1. **Append-only 强制**：所有核心表禁止 UPDATE / DELETE（除了 tag / state 这类元数据）
2. **Look-ahead 守门**：入库时校验时间戳单调性，违反 reject
3. **Decimal 一致**：金额字段全部 TEXT 存 Decimal，应用层 cast，禁用 float 算 PnL
4. **Repro-on-write**：每次写 run 时 stamp `repro_key`，定期 re-run 抽检是否一致

---

## 2. Database Schema

主库选型 **SQLite**（WAL 模式 + `synchronous=FULL`），单文件部署在 N100 上，配合分层备份。所有金额用 Decimal-as-text，所有时间 UTC ISO-8601。

### 2.1 容量分析

基于现有 paper 流量（~300 signals/day，~150 orders/day across modes，每周 replay 4 configs × 30 天回看）估算：

| 表 | 增速 | 1 年 | 5 年 |
|---|---|---|---|
| `signals` | ~300/天 | 110K 行 | 550K 行 |
| `plans` | ~300/天（含 skip） | 110K 行 | 550K 行 |
| `orders` | ~150/天 + 周 replay ~12-36K | 1-2M 行 | 5-10M 行 |
| `fills` | ~1.5× orders | 1.5-3M 行 | 7-15M 行 |
| `settlements` | ~50/天 | 18K 行 | 90K 行 |
| `runs` | ~20/周 | 1K 行 | 5K 行 |

**总量：1 年 ~200-500 MB；5 年 ~1-2 GB。** SQLite 舒适区上限约 100 GB，离红线 50-100 倍。

**关键性能保险：**

- `runs.metrics` 是 JSON 快照，**Compare 页面查的是预聚合指标，不查裸明细**
- 大表查询永远带 `run_id` 或时间窗约束，索引下百万行 < 5ms
- `pragma mmap_size = 268435456`（256 MB）让热点页内存映射
- 月度 cron `VACUUM` 防碎片

**何时考虑迁移 Postgres：** 多进程并发写、10+ 并发用户、单表 > 100M 行——你们在可预见的 5-10 年都不会触发。真要换，SQLAlchemy 抽象层让迁移成本约 1-2 天。

### 2.2 数据韧性（Resilience）

**三层防御：**

1. **本地硬化：** WAL 模式 + `synchronous=FULL` 抗一般 crash 和断电单事务丢失
2. **DB 可重建：** SQLite 是 raw 文件（snapshots / signals.jsonl / ledger CSV）的**派生视图**。`scripts/ops/rebuild_db_from_raw.py` idempotent 重建，DB 坏了 5 分钟回来
3. **异地备份：** raw 文件每日 rsync 到本地 + rclone 到 B2/R2；DB 每小时 `sqlite3 .backup` + rsync

**工程契约（铁律）：**

- **Raw 文件是唯一真理**：任何 UI 数字最终可追溯到某个 CSV/JSONL/snapshot 行
- **SQLite 是派生视图**：可删可重建，不可"只在 DB 里修一行"
- **入库脚本 idempotent**：用 `ingestion_log(source_path, row_hash)` 做幂等键
- **`rebuild_db_from_raw.py` 输出 = 增量入库输出**：定期 bit-by-bit 对账

### 2.3 ER 概览

```
            ┌──────────────┐    ┌──────────────────┐
            │  universes   │    │  code_versions   │
            └──────┬───────┘    └────────┬─────────┘
                   │                     │
                   │   ┌─────────────────┘
                   ▼   ▼
┌──────────────────┐   ┌─────────┐
│ strategy_config  │◄──│  runs   │  state, metrics JSON, repro_key
└──────────────────┘   └────┬────┘
                            │
   ┌────────────────────────┼────────────────────────┐
   ▼                        ▼                         ▼
┌──────────┐          ┌─────────┐              ┌──────────┐
│ signals  │◄─────────│  plans  │─────────────►│  orders  │
└────┬─────┘          └─────────┘              └─────┬────┘
     │                                               │
     ▼                                               ▼
[snapshots/                                     ┌────────┐
 *.json]                                        │ fills  │
                                                └────┬───┘
                                                     │
                                                     ▼ (target_date, bracket)
                                                ┌─────────────┐
                                                │settlements  │
                                                └─────────────┘

  ┌──────────────────┐    ┌────────────────┐
  │ ingestion_log    │    │ schema_version │
  └──────────────────┘    └────────────────┘
```

### 2.4 表定义（DDL）

```sql
-- Enable foreign key enforcement (SQLite default = OFF)
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = FULL;
PRAGMA wal_autocheckpoint = 1000;
PRAGMA temp_store = MEMORY;
PRAGMA mmap_size = 268435456;

-- ============================================================
-- 元数据 / 维度表
-- ============================================================

CREATE TABLE schema_version (
    version          INTEGER PRIMARY KEY,
    applied_at_utc   TEXT NOT NULL,
    description      TEXT
);

CREATE TABLE universes (
    universe_id      TEXT PRIMARY KEY,
    name             TEXT NOT NULL UNIQUE,
    description      TEXT,
    cities           TEXT NOT NULL,         -- JSON array
    models           TEXT NOT NULL,         -- JSON array
    created_at_utc   TEXT NOT NULL,
    frozen_at_utc    TEXT,                  -- once set, all fields immutable
    deprecated_at_utc TEXT                  -- soft-deprecate; new runs disallowed but old preserved
);

CREATE TABLE code_versions (
    code_version     TEXT PRIMARY KEY,      -- git SHA
    branch           TEXT,
    commit_subject   TEXT,
    commit_at_utc    TEXT,
    deployed_at_utc  TEXT,
    notes            TEXT
);

CREATE TABLE strategy_config (
    config_id        TEXT PRIMARY KEY,      -- hash(canonical(params))
    name             TEXT NOT NULL,
    params           TEXT NOT NULL,         -- JSON: {pool, price_range, sizing, stop_loss, maker_policy, edge_threshold, ...}
    created_at_utc   TEXT NOT NULL,
    notes            TEXT
);

-- Generated columns for frequently filtered JSON fields
ALTER TABLE strategy_config ADD COLUMN pool TEXT
    GENERATED ALWAYS AS (json_extract(params, '$.pool')) VIRTUAL;
ALTER TABLE strategy_config ADD COLUMN sizing_mode TEXT
    GENERATED ALWAYS AS (json_extract(params, '$.sizing.mode')) VIRTUAL;
ALTER TABLE strategy_config ADD COLUMN price_min TEXT
    GENERATED ALWAYS AS (json_extract(params, '$.price_range.min')) VIRTUAL;
ALTER TABLE strategy_config ADD COLUMN price_max TEXT
    GENERATED ALWAYS AS (json_extract(params, '$.price_range.max')) VIRTUAL;

-- ============================================================
-- 实验注册表（核心）
-- ============================================================

CREATE TABLE runs (
    run_id           TEXT PRIMARY KEY,
    config_id        TEXT NOT NULL REFERENCES strategy_config(config_id),
    universe_id      TEXT NOT NULL REFERENCES universes(universe_id),
    code_version     TEXT NOT NULL REFERENCES code_versions(code_version),
    execution_mode   TEXT NOT NULL CHECK (execution_mode IN ('snapshot_replay', 'paper', 'live')),
    date_range_start TEXT NOT NULL,         -- UTC date 'YYYY-MM-DD'
    date_range_end   TEXT,                  -- NULL = open run
    started_at_utc   TEXT NOT NULL,
    ended_at_utc     TEXT,
    state            TEXT NOT NULL DEFAULT 'explore'
                     CHECK (state IN ('explore', 'paper', 'live', 'retired')),
    repro_key        TEXT NOT NULL,         -- hash(config + code + universe + data_snapshot); NOT unique
    parent_run_id    TEXT REFERENCES runs(run_id),
    tags             TEXT,                  -- JSON array
    notes            TEXT,
    metrics          TEXT,                  -- JSON; see Section 1.5
    metrics_at_utc   TEXT
);

-- ============================================================
-- 血缘链：signal → plan → order → fill
-- ============================================================

CREATE TABLE signals (
    signal_id        TEXT PRIMARY KEY,
    snapshot_ts_utc  TEXT NOT NULL,         -- point-in-time anchor
    snapshot_file    TEXT NOT NULL,         -- reference to raw file
    target_date      TEXT NOT NULL,
    city             TEXT NOT NULL,
    bracket          TEXT NOT NULL,
    side             TEXT NOT NULL CHECK (side IN ('YES', 'NO')),
    model_version    TEXT NOT NULL,
    model_p_yes      TEXT NOT NULL,         -- Decimal as text
    market_price     TEXT NOT NULL,
    edge             TEXT NOT NULL,
    abs_edge         TEXT NOT NULL,
    obs_source       TEXT,
    ingested_at_utc  TEXT NOT NULL
);

CREATE TABLE plans (
    plan_id          TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES runs(run_id),
    signal_id        TEXT NOT NULL REFERENCES signals(signal_id),
    config_id        TEXT NOT NULL REFERENCES strategy_config(config_id),  -- denormalized
    created_at_utc   TEXT NOT NULL,
    desired_shares   TEXT,                  -- NULL when skipped
    target_price     TEXT,
    sizing_decision  TEXT,                  -- JSON: {tier_mult, capital_remaining, ...}
    city_pool        TEXT CHECK (city_pool IN ('t1_trading', 't2_research')),
    entry_price_min  TEXT,
    entry_price_max  TEXT,
    execution_policy TEXT,
    skip_reason      TEXT                   -- NULL = plan led to order; non-NULL = no order
);

CREATE TABLE orders (
    order_id           TEXT PRIMARY KEY,
    run_id             TEXT NOT NULL REFERENCES runs(run_id),
    plan_id            TEXT NOT NULL REFERENCES plans(plan_id),
    execution_mode     TEXT NOT NULL CHECK (execution_mode IN ('snapshot_replay', 'paper', 'live')),
    side               TEXT NOT NULL CHECK (side IN ('BUY_YES', 'BUY_NO')),
    entry_price        TEXT NOT NULL,
    shares             TEXT NOT NULL,
    cost_usd           TEXT NOT NULL,
    maker_or_taker     TEXT CHECK (maker_or_taker IN ('maker', 'taker', NULL)),
    placed_at_utc      TEXT NOT NULL,       -- INVARIANT: > signals.snapshot_ts_utc (app-level check)
    external_order_id  TEXT                 -- Polymarket order ID for live
);

CREATE TABLE fills (
    fill_id            TEXT PRIMARY KEY,
    order_id           TEXT NOT NULL REFERENCES orders(order_id),
    filled_shares      TEXT NOT NULL,
    filled_price       TEXT NOT NULL,
    fees_usd           TEXT NOT NULL DEFAULT '0',
    status             TEXT NOT NULL
                       CHECK (status IN ('filled', 'partial', 'cancelled', 'expired')),
    filled_at_utc      TEXT NOT NULL,
    external_fill_id   TEXT
);

CREATE TABLE settlements (
    settlement_id      TEXT PRIMARY KEY,    -- hash(target_date + bracket)
    target_date        TEXT NOT NULL,
    bracket            TEXT NOT NULL,
    final_yes          INTEGER CHECK (final_yes IN (0, 1, NULL)),
    status             TEXT NOT NULL
                       CHECK (status IN ('settled', 'missing_event', 'missing_bracket')),
    settled_at_utc     TEXT,
    source             TEXT,
    ingested_at_utc    TEXT NOT NULL,
    UNIQUE (target_date, bracket)
);

-- ============================================================
-- 入库幂等键
-- ============================================================

CREATE TABLE ingestion_log (
    ingest_id        TEXT PRIMARY KEY,
    source_path      TEXT NOT NULL,
    source_row_hash  TEXT NOT NULL,
    target_table     TEXT NOT NULL,
    target_id        TEXT NOT NULL,
    ingested_at_utc  TEXT NOT NULL,
    UNIQUE (source_path, source_row_hash, target_table)
);
```

### 2.5 索引

```sql
-- Compare 页面主路径
CREATE INDEX idx_orders_run ON orders(run_id);
CREATE INDEX idx_orders_run_mode ON orders(run_id, execution_mode);
CREATE INDEX idx_plans_run ON plans(run_id);

-- 血缘 join
CREATE INDEX idx_plans_signal ON plans(signal_id);
CREATE INDEX idx_plans_config ON plans(config_id);
CREATE INDEX idx_fills_order ON fills(order_id);

-- 时间窗查询
CREATE INDEX idx_signals_target_date_city ON signals(target_date, city);
CREATE INDEX idx_signals_snapshot_ts ON signals(snapshot_ts_utc);
CREATE INDEX idx_orders_placed_at ON orders(placed_at_utc);
CREATE INDEX idx_fills_filled_at ON fills(filled_at_utc);

-- settlement 关联
CREATE INDEX idx_settlements_lookup ON settlements(target_date, bracket);

-- runs 查询
CREATE INDEX idx_runs_config_mode ON runs(config_id, execution_mode);
CREATE INDEX idx_runs_state ON runs(state);
CREATE INDEX idx_runs_repro_key ON runs(repro_key);
CREATE INDEX idx_runs_started ON runs(started_at_utc);

-- JSON 字段加速
CREATE INDEX idx_config_pool_sizing ON strategy_config(pool, sizing_mode);
```

### 2.6 Append-only 强制（Triggers）

白名单可变字段：`runs.metrics`、`runs.tags`、`runs.notes`、`runs.state`、`runs.ended_at_utc`、`runs.parent_run_id`、`runs.metrics_at_utc`。其余表禁止 UPDATE/DELETE：

```sql
-- 例：signals 全锁
CREATE TRIGGER signals_no_update BEFORE UPDATE ON signals
BEGIN SELECT RAISE(ABORT, 'signals is append-only'); END;
CREATE TRIGGER signals_no_delete BEFORE DELETE ON signals
BEGIN SELECT RAISE(ABORT, 'signals is append-only'); END;

-- plans / orders / fills / settlements / strategy_config / code_versions / ingestion_log 类似
-- universes 在 frozen_at_utc 设置后禁止改：
CREATE TRIGGER universes_no_update_when_frozen BEFORE UPDATE ON universes
WHEN OLD.frozen_at_utc IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'universe is frozen'); END;
```

`rebuild_db_from_raw.py` 时通过 `PRAGMA defer_foreign_keys = ON` + 临时禁用 triggers 来允许全量重灌。

### 2.7 关键设计决策（FAQ）

**(1) `runs.metrics` 用 JSON 而不是独立 metrics 表**
指标种类持续演化（PnL concentration、trimmed、by_bucket……），不希望每加一个就 ALTER TABLE。单 run 的 metrics 总量 < 10 KB，JSON 完全够。需按某指标排序时，generated column 抽出来加索引。

**(2) Position 不建表**
持仓 = fills 按 (city, bracket, side) 聚合减 settled。运行时 SQL 算，不持久化避免一致性风险。

**(3) `code_version` 在 runs 而不在 config**
config 是参数，code_version 是「执行参数的代码」。同 config × 不同 code_version 是不同 run（bug fix 前后必须可区分）。

**(4) `plans.skip_reason` 存"没下单的原因"**
"为什么没下单"和"下了什么"同等重要。capital cap 漏掉好机会是核心分析场景。

**(5) `orders.execution_mode` 冗余存（vs 从 run 查）**
denormalize 换查询速度。Compare 页面频繁按 mode 过滤，免一次 join。

**(6) `repro_key` 不 unique**
故意允许重复——同 repro_key 跑两次结果应一致，这是**重放校验数据**，不是去重 key。UI 上 surface "相同 repro_key 的 N 个 run，metrics diff = 0 ?"

**(7) `settlements` 不和 fills 用 FK**
settlement 通过 (target_date, bracket) 间接关联。settlement 可能晚到/缺失，硬 FK 会让 fill 入库阻塞。

**(8) 金额全部 Decimal-as-text**
`TEXT` 存 `"1653.60"`，应用层 `Decimal()` cast。永不 float。SQLite 没有 native Decimal，TEXT 是业内事实标准。

### 2.8 JSON 扩展策略

新增策略参数（例：止损、maker policy）= 写到 `strategy_config.params` 即可，**零 schema 变更**。

```python
# 加止损
params = {
    "pool": "T1",
    "price_range": {"min": 0.25, "max": 0.75},
    "sizing": {"mode": "fixed_amount", "amount_usd": 3.90},
    "stop_loss": {"mode": "fixed_pct", "pct": 0.20},   # ← 新增
    "edge_threshold": 0.10,
}
```

UI 自动适配：diff 多个 run 时，JSON 展开自动显示 `stop_loss` 字段差异；旧 run 没这字段则显示 N/A。

**何时把 JSON 字段"升级"为列：**
- 该字段被 90%+ 查询使用
- 出现在多个索引中
- 需要 CHECK 约束

升级方法：`ALTER TABLE ... ADD COLUMN ... GENERATED ALWAYS AS (json_extract(...))` 加索引，**应用代码不改**。

### 2.9 入库幂等

每行 raw 数据入库前算 `row_hash = sha256(canonical(row))`，写入 `ingestion_log` 同时插主表。`UNIQUE(source_path, row_hash, target_table)` 保证重复入库 silently noop。

`rebuild_db_from_raw.py`：

```python
def rebuild():
    db.execute("DELETE FROM ingestion_log")
    db.execute("DELETE FROM fills; DELETE FROM orders; ...")  # 按 FK 倒序
    for csv_path in find_raw_files():
        for row in iter_rows(csv_path):
            row_hash = hash_row(row)
            ingest_idempotent(csv_path, row_hash, row)
    verify_against_metrics()  # 对账：重建后 metrics 是否和原 runs.metrics 一致
```

---

## 3. Strategy / Model Management

回答的是「人怎么操作 config / run / universe」的运营问题。Schema 已定，本节定**工作流和规约**。

### 3.1 核心原则：Config as Code

业内主流（Hydra / Kedro / MLflow / dbt）共识：**策略配置必须是版本化的代码资产**，不在 UI 上直接编辑、不放数据库当 source of truth。原因：

- 改动可 review、可 diff、可回滚
- 和代码版本一起进 git，repro_key 自动锚定
- AI 当**起草器**而不是**操作员**——降低无 audit trail 的风险

### 3.2 工作流概览

```
┌─ 用户/AI ────────────────────────────────────────────────────┐
│  自然语言："加个 20% 止损的版本"                              │
│       ↓                                                       │
│  AI 起草 YAML diff（不直接跑）                                │
│       ↓                                                       │
│  人 review + git commit                                       │
└──────┬───────────────────────────────────────────────────────┘
       │
       │  weather config register configs/strategies/X.yaml
       ▼
┌─ 系统 ──────────────────────────────────────────────────────┐
│  1. JSON Schema 校验（fail-fast）                            │
│  2. canonicalize → config_id = sha256(canonical_json)        │
│  3. INSERT INTO strategy_config（已存在则 noop）              │
│  4. 输出 config_id                                           │
└──────┬───────────────────────────────────────────────────────┘
       │
       │  weather run create --config-id X --mode replay --start ... --end ...
       ▼
┌─ Run ───────────────────────────────────────────────────────┐
│  生成 run_id，stamp repro_key，跑实验，写 metrics             │
└─────────────────────────────────────────────────────────────┘
```

**两类 schema 别混：**
- `schema_version` 表：**DB schema** 的 migration 版本号
- `configs/schemas/*.json`：**YAML config** 的 JSON Schema 校验文件

### 3.3 Config YAML 格式

文件位置：`configs/strategies/<name>.yaml`，进 git。

```yaml
# configs/strategies/t1_25-75c_fixed_amount_baseline.yaml
name: t1_25-75c_fixed_amount_baseline
description: T1 池子，25-75c 进场，固定 $3.90 每单，无止损。当前 exploration baseline。

universe: universe_v2

params:
  pool: t1_trading
  price_range:
    min: "0.25"            # left-closed
    max: "0.75"            # right-open
  sizing:
    mode: fixed_amount
    amount_usd: "3.90"
  edge_threshold: "0.10"
  stop_loss: null          # 显式 null 比省略好
  maker_policy: null

tags:
  - phase:explore
  - baseline
```

**JSON Schema 关键约束（`configs/schemas/strategy.schema.json`）：**

- `params.price_range.min < params.price_range.max`
- `sizing.mode ∈ {fixed_amount, fixed_shares, tiered, custom}`
- 所有金额必须是 Decimal 字符串（正则 `^-?\d+(\.\d+)?$`）
- `pool ∈ {t1_trading, t2_research, all}`
- `tags` 至少含一个 `phase:*` 标签
- `universe` 必须引用已存在的 universe_id

### 3.4 命名 + Tag 规约

#### `config.name` 文件命名
**格式**：`<pool>_<price_band>_<sizing>[_<extras>]`

例：
- `t1_25-75c_fixed_amount_baseline`
- `t1_25-75c_fixed_amount_stop20`
- `all_full-range_fixed_shares_v2`

#### Tag 命名空间

| 前缀 | 用途 | 例 |
|---|---|---|
| `phase:` | 生命周期阶段 | `phase:explore`, `phase:paper`, `phase:live` |
| `tactic:` | 战术意图 | `tactic:lottery_avoidance`, `tactic:high_edge_only` |
| `bug:` | 修过的 bug 引用 | `bug:planner_v3_fix` |
| `cohort:` | 实验队列 | `cohort:2026-05-stop-loss-ablation` |
| `note:` | 自由备注 | `note:weekend_only` |

UI 上按前缀分组显示。

#### Run 名（自动生成）
`<config.name>__<mode>__<date_range>`
例：`t1_25-75c_fixed_amount_baseline__paper__2026-05-08_to_open`

### 3.5 Model 版本管理

**核心决策：`model_version` 是 universe 的一部分，不是独立维度。**

理由：model_version 决定了"signals 长什么样"——换 model 等于换数据源，混在一起做 PnL 加总没意义。

**Universe YAML：**

```yaml
# configs/universes/universe_v3.yaml
universe_id: universe_v3
name: universe_v3
description: 加入 chicago 后的版本，model_version 升 1.4
cities:
  - nyc
  - sfo
  - chi    # 新加
models:
  - weather_edge_v1.4   # 升级
frozen_at_utc: null     # 还在 explore
```

**Model 升级 SOP：**

1. 老 universe 不删，标 `deprecated_at_utc`
2. 新 universe 用新 `universe_id`
3. 所有新 run 用新 universe；老 run 数据保留，UI 标 "deprecated universe"
4. UI 默认隐藏 deprecated universe 的 run，可一键展开

**漂移检测（自动化）：** 入库时如果 signal 的 `model_version` 不在 run 关联的 universe 的 models 列表中 → reject + 告警。防止混入"野生"模型输出。

### 3.6 Universe 演进 SOP

| 场景 | 处理 |
|---|---|
| 加新城市 | 新 universe（**不要改老的**） |
| 删城市 | **禁止** —— 只能在新 universe 不包含它 |
| 改 model | 新 universe（同上） |
| Freeze | `frozen_at_utc` 一旦写不可改；建议进入 paper 阶段时 freeze |
| Deprecate | 标 `deprecated_at_utc`；新 run 拒绝使用，老 run 保留 |

**为什么这么死板：** 防存活者偏差。"老的不动，新的换新身份" 是量化系统铁律。

### 3.7 Paper-Live 配对

**自动配对规则：** 同 `config_id` 下，存在 `execution_mode=paper` 和 `execution_mode=live` 的 run，UI 自动配对显示。

**Drift 计算：**

```
drift = {
    "roi_diff_pct":      live.roi_settled  - paper.roi_settled,
    "win_rate_diff":     live.win_rate     - paper.win_rate,
    "expectancy_diff":   live.expectancy   - paper.expectancy,
    "fill_rate_diff":    live.fill_rate    - paper.fill_rate,
}
```

**Drift 警告阈值（默认）：**
- `|roi_diff_pct| > 5%` → 黄色
- `|roi_diff_pct| > 15%` → 红色（停 live 检查 paper 模型）

Live 跑久了和 paper 漂移 = 滑点 / 手续费 / queue position 没建模好。这是 paper 模型需要修的反馈信号。

### 3.8 重跑 / Supersede 工作流

**触发场景：**
1. 修了 planner bug 想重新算历史 PnL
2. 老 replay 数据格式过时想 refresh
3. 加了新指标想回填

**SOP：**

1. **绝不删除老 run** —— 保留作为审计
2. 新 run `parent_run_id = 老 run_id`
3. 新 run tags 加 `supersedes:<old_run_id>` 和 `bug:<short_id>` 或 `metric_refresh`
4. UI 默认显示新 run，老 run 折叠在 "Superseded" 节，可一键展开

**Repro 校验（自动）：**
- 如果新 run 和老 run 的 `repro_key` 相同但 metrics 差异 > 阈值 → CI 告警
- 系统有隐藏非确定性必须排查（未控制的随机种子、并发顺序）

### 3.9 工程文件布局（增量）

```
configs/
├── schemas/
│   ├── strategy.schema.json       # JSON Schema for strategy YAML
│   └── universe.schema.json
├── strategies/
│   ├── t1_25-75c_fixed_amount_baseline.yaml
│   ├── t1_25-75c_fixed_amount_stop20.yaml
│   └── ...
└── universes/
    ├── universe_v2.yaml
    └── universe_v3.yaml

scripts/ops/
├── weather_config_register.py     # YAML → strategy_config 表
├── weather_universe_register.py   # YAML → universes 表
├── weather_run_create.py          # 创建 run（含 repro_key 计算）
├── weather_run_supersede.py       # 标记 supersede 关系
└── ingest_to_db.py                # raw 文件 → 血缘表（含幂等）
```

### 3.10 Bootstrap：从当前状态迁移

你们目前还没有正式 config 文件（自然语言 → AI → CLI flags）。一次性迁移步骤：

1. **盘点现有 CLI 用法**：`grep -r 'weather_trade_planner' scripts/ | sort -u`，抽出每种参数组合
2. **AI 协助生成 YAML 草稿**：每个组合一个文件，进 `configs/strategies/`
3. **`weather_config_register --dry-run`**：校验所有 YAML，列出会注册的 config_id
4. **批量注册**：`for f in configs/strategies/*.yaml; do weather_config_register "$f"; done`
5. **回填历史 runs**：`weather_run_backfill --from-csv runtime/.../ledger.csv` —— 把已有 ledger CSV 关联到最匹配的 config_id（暴露当时确切 params 不一定能完美回填，可标 `tag: bootstrap_inferred`）

迁移完成后 raw CSV 仍然是真理，DB 是派生层。

### 3.11 不在本节范围

- **Strategy lifecycle 状态机**（`explore → paper → live → retired` 的自动晋升规则）：schema 已留 `runs.state` 字段，未来加状态机直接用。当前阶段手动改 state，不做自动化。

## 4. Frontend Structure

### 4.1 技术栈

| 层 | 选型 |
|---|---|
| 前端框架 | Vite + React 18 + TypeScript |
| 样式 | Tailwind CSS + shadcn/ui |
| 图表 | Recharts |
| Server state | TanStack Query v5 |
| Routing | React Router v6 |
| Backend | FastAPI + SQLAlchemy / SQLModel + Pydantic v2 |
| 部署 | N100 单进程，FastAPI 同时 serve API 和 React build |
| 开发流 | 本地 `vite dev` → SSH tunnel 到 N100 FastAPI |

### 4.2 信息架构

```
┌─ Top Nav ────────────────────────────────────────────────────┐
│  Runs  │  Live  │  Configs  │  Universes  │  Settings        │
└──────────────────────────────────────────────────────────────┘
```

| 页面 | 路由 | 用途 |
|---|---|---|
| Runs | `/runs`, `/runs/compare?ids=` | run 列表 + 多选对比 |
| Run Detail | `/runs/:run_id` | 单 run 深挖（PnL 曲线、归因、trade 明细） |
| Live | `/live` | 当前实时持仓 + 今日 PnL |
| Configs | `/configs`, `/configs/:config_id` | strategy_config 列表 + 详情 + 草稿/编辑 |
| Universes | `/universes`, `/universes/:universe_id` | universe 列表 + 详情 + 草稿/编辑 |
| Settings | `/settings` | 本地偏好（暂极简） |

`/` 默认 redirect 到 `/runs`。

### 4.3 双语显示约定（核心规约）

**所有数据 / 配置字段** UI 上必须并列显示**英文字段名（小字 mono 字体） + 中文说明**。理由：

- 英文字段名 = 数据库列 = API 字段，便于排查 / 沟通 / debug
- 中文说明 = 阅读时不卡壳
- 量化系统跨语境（log、SQL、UI）使用同一套词汇，杜绝歧义

**渲染样式（举例）：**

```
┌────────────────────────────────────────────────┐
│ trades        单数                  424        │
│ settled_ratio 已结算占比            96.7%      │
│ pnl_usd       PnL（扣费后）         +$597.64   │
│ roi_settled   已结算 ROI            36.1%      │
│ expectancy    期望收益              +$1.41     │
│ top_5_share   前 5 单 PnL 占比      0.45 🟡    │
│ max_dd        最大回撤              -$45.20    │
└────────────────────────────────────────────────┘
```

**字段标签字典**统一维护在前端 `src/labels/metrics.ts`、`src/labels/config.ts`：

```typescript
export const METRIC_LABELS = {
  trades: { en: 'trades', zh: '单数' },
  trades_settled: { en: 'trades_settled', zh: '已结算单数' },
  settled_ratio: { en: 'settled_ratio', zh: '已结算占比' },
  cost_usd: { en: 'cost_usd', zh: '成本' },
  pnl_usd_settled: { en: 'pnl_usd_settled', zh: '已结算 PnL' },
  roi_settled: { en: 'roi_settled', zh: '已结算 ROI' },
  win_rate: { en: 'win_rate', zh: '胜率' },
  avg_win_usd: { en: 'avg_win_usd', zh: '平均盈利' },
  avg_lose_usd: { en: 'avg_lose_usd', zh: '平均亏损' },
  expectancy_usd: { en: 'expectancy_usd', zh: '期望收益' },
  worst_loss_usd: { en: 'worst_loss_usd', zh: '最差单笔' },
  max_drawdown_usd: { en: 'max_drawdown_usd', zh: '最大回撤' },
  max_drawdown_pct: { en: 'max_drawdown_pct', zh: '最大回撤 %' },
  median_trade_pnl_usd: { en: 'median_trade_pnl_usd', zh: '单笔中位 PnL' },
  top_1_pnl_share: { en: 'top_1_pnl_share', zh: '前 1 单 PnL 占比' },
  top_5_pnl_share: { en: 'top_5_pnl_share', zh: '前 5 单 PnL 占比' },
  top_10pct_pnl_share: { en: 'top_10pct_pnl_share', zh: '前 10% PnL 占比' },
  pnl_trimmed_1pct: { en: 'pnl_trimmed_1pct', zh: '截尾 1% 后 PnL' },
  roi_trimmed_1pct: { en: 'roi_trimmed_1pct', zh: '截尾 1% 后 ROI' },
  fees_paid: { en: 'fees_paid', zh: '手续费' },
  pnl_net_of_fees: { en: 'pnl_net_of_fees', zh: '扣费后 PnL' },
  // ... 持续扩展
};
```

**`<MetricCell field="top_5_pnl_share" value={0.45} />`** 组件自动渲染双语 + 警告色（参见 4.11）。

### 4.4 Runs 页面 + Compare 视图

#### Runs List

```
┌─ Filters ────────────────────────────────────────────────┐
│ State: [all]  Mode: [all]  Config: [...]  Universe: [v2] │
│ Tags: [phase:explore × ...]                              │
│ Date: [2026-05-01 → today]                               │
│ ☐ show_retired   ☐ show_superseded                       │
└──────────────────────────────────────────────────────────┘

┌─ Run List ─────────────────────────────────────────────────┐
│ □ │ name                              │ mode    │ dates    │ ROI │
│───┼───────────────────────────────────┼─────────┼──────────┼─────│
│ ☑ │ t1_25-75c_fa_baseline             │ replay  │ 05-06→13 │ 36.1│
│ ☑ │ t1_25-75c_fa_baseline             │ paper   │ 05-08→   │ 53.1│
│ ☑ │ t1_25-75c_fa_stop20               │ replay  │ 05-06→13 │ 32.3│
└────────────────────────────────────────────────────────────┘

         [Compare Selected (3)]   [+ Create Run]
```

#### Compare 视图（核心 KPI）

```
┌─ Compare: 3 runs ──────────────────────────────────────────────┐
│                                                                 │
│ ┌─ Metrics Matrix ─────────────────────────────────────────┐   │
│ │                                  │ Run A    │ Run B    │ Run C │
│ │ trades 单数                      │ 424      │ 290      │ 410   │
│ │ settled_ratio 已结算占比         │ 96.7%    │ 100%     │ 96.5% │
│ │ pnl_net_of_fees 扣费后 PnL       │ +$597.64 │ +$600.95 │ +$520 │
│ │ roi_settled ROI                  │ 36.1%    │ 53.1% 🟢 │ 32.3% │
│ │ pnl_trimmed_1pct 截尾后 PnL      │ +$120 ⚠  │ +$580    │ +$500 │
│ │ top_5_pnl_share 前5单PnL占比     │ 0.45 🟡  │ 0.12     │ 0.18  │
│ │ expectancy_usd 期望收益          │ +$1.41   │ +$2.07   │ +$1.27│
│ │ max_drawdown_usd 最大回撤        │ -$45.20  │ -$23.00  │ -$28  │
│ │ worst_loss_usd 最差单笔          │ -$3.90   │ -$3.90   │ -$3.90│
│ └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│ ┌─ Config Diff (only-diff mode) ───────────────────────────┐   │
│ │ pool, price_range, sizing：全部相同 ▼ 展开                │   │
│ │ ▼ stop_loss:                                             │   │
│ │     Run A: null                                          │   │
│ │     Run B: null                                          │   │
│ │     Run C: { mode: "fixed_pct", pct: "0.20" }            │   │
│ └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│ ┌─ Cumulative PnL（overlay）────────────────────────────────┐   │
│ │ [Recharts line chart, 3 series]                          │   │
│ └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│ ┌─ By Price Bucket ─────────────────────────────────────────┐   │
│ │ bucket   │ Run A          │ Run B          │ Run C        │   │
│ │ <10c 🎰  │ +$580 (n=18)   │ +$0   (n=0)    │ +$480 (n=15) │   │
│ │ 10-25c   │ +$12  (n=82)   │ +$0   (n=0)    │ +$10  (n=70) │   │
│ │ 25-75c   │ +$5   (n=280)  │ +$601 (n=290)  │ +$30  (n=300)│   │
│ │ 75c+     │ +$0.14(n=44)   │ +$0   (n=0)    │ +$0   (n=25) │   │
│ └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│ ┌─ Tabs: By City | By Side | By Edge Bucket | By Model ────┐   │
│ └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

#### 关键徽章 / 警告（自动）

| 条件 | 视觉处理 |
|---|---|
| `top_5_pnl_share > 0.3` | 🟡 黄色徽章 + tooltip"PnL 集中度过高，可能是 luck 不是 edge" |
| `settled_ratio < 0.95` | ⚠ 灰色文字 + tooltip"数据未完整" |
| `n_trades < 30`（任一分组） | 灰色 + tooltip"样本不足，不建议外推" |
| `<10c` 价格桶 | 🎰 lottery_zone 标记 |
| `state = retired` | 透明度降低 |

**默认排序：** Compare 视图按 `pnl_trimmed_1pct` 排序，避免被异常单（彩票）误导。

### 4.5 Run Detail 页面

```
┌─ Run: t1_25-75c_fa_baseline__replay__2026-05-06_to_2026-05-13 ─┐
│ config_id: 0x4b8a...   code_version: a3f9b2c   state: explore   │
│ repro_key: 0xc1de...   universe: universe_v2                    │
│ tags: phase:explore, baseline                                   │
│                                                                 │
│ ┌─ Cumulative PnL ─────────────────────────────────────────┐   │
│ │ [line chart with daily annotations]                      │   │
│ └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│ ┌─ Metric Cards (双语) ─────────────────────────────────────┐  │
│ │  trades 单数 │ settled_ratio │ pnl │ roi │ expectancy ... │  │
│ └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│ ┌─ Breakdown Tabs ─────────────────────────────────────────┐   │
│ │ [By Day] [By Price Bucket] [By City] [By Edge] [By Side] │   │
│ │ [By Model] [Trades]                                       │   │
│ └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│ ┌─ Trade List (paginated, 50/page) ────────────────────────┐   │
│ │ filter / sort / export CSV                                │   │
│ └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 4.6 Live 页面

```
┌─ Live Trading ──────────────────────────────────────────────────┐
│ wallet_balance: $1,234.56     last_update: 12s ago    [⟳]       │
│                                                                  │
│ ┌─ Today's PnL ────────────────┐ ┌─ Exposure ──────────────────┐│
│ │ realized:   +$23.40 (n=12)   │ │ by_city:    [pie]           ││
│ │ unrealized: -$5.20  (n=34)   │ │ by_bracket: [pie]           ││
│ │ net:        +$18.20          │ │ total_at_risk: $54.80       ││
│ └──────────────────────────────┘ └─────────────────────────────┘│
│                                                                  │
│ ┌─ Open Positions (34) ─────────────────────────────────────┐   │
│ │ target_date │ city │ bracket │ side │ shares │ cost │ uPnL│   │
│ │ 2026-05-17  │ nyc  │ 65-70F  │ YES  │ 12     │$5.40 │+$1.2│   │
│ └───────────────────────────────────────────────────────────┘   │
│                                                                  │
│ ┌─ Settled Today (12) ──────────────────────────────────────┐   │
│ │ target_date │ city │ bracket │ side │ pnl                 │   │
│ └───────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

**数据来源：**
- `wallet_balance` + open positions：FastAPI 转发 Polymarket API（缓存 5s）
- Today's settled / unrealized：SQLite + 当前价
- 自动刷新：`refetchInterval: 10_000`

### 4.7 Configs 页面（含管理界面）

#### List View

```
┌─ Configs ─────────────────────────────────────────────────────┐
│ Search [...]   Filter: [pool] [sizing] [tag]    [+ New Draft] │
│                                                                │
│ name                       │pool│sizing  │runs│last_used      │
│ t1_25-75c_fa_baseline      │T1  │fa $3.9 │ 3  │2026-05-15     │
│ t1_25-75c_fa_stop20        │T1  │fa $3.9 │ 2  │2026-05-13     │
└────────────────────────────────────────────────────────────────┘
```

#### Detail View（`/configs/:config_id`）

```
┌─ Config: t1_25-75c_fa_baseline ──────────────────────────────┐
│ config_id: 0x4b8a...                                         │
│ git: configs/strategies/t1_25-75c_fa_baseline.yaml           │
│ created_at: 2026-05-10                                       │
│                                                              │
│ ┌─ Params（双语）──────────┐ ┌─ YAML 原文 ──────────────┐    │
│ │ pool 池子: t1_trading    │ │ name: t1_25-75c_fa_...   │    │
│ │ price_range 价格区间:    │ │ universe: universe_v2    │    │
│ │   0.25 - 0.75            │ │ params: ...              │    │
│ │ sizing 仓位: fa $3.9     │ │                          │    │
│ │ edge_threshold: 0.10     │ │                          │    │
│ └──────────────────────────┘ └──────────────────────────┘   │
│                                                              │
│ [Clone as Draft]  [View Git History]                         │
│                                                              │
│ ┌─ Runs (5) ───────────────────────────────────────────┐    │
│ │ replay  05-06→13   ROI 36.1%  top5 0.45 🟡           │    │
│ │ paper   05-08→     ROI 53.1%  ✓                      │    │
│ └──────────────────────────────────────────────────────┘    │
│                                                              │
│ ┌─ Paper vs Live Drift ────────────────────────────────┐    │
│ │ (无 live run，配对未启用)                              │    │
│ └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

#### Draft / Edit Form

`+ New Draft` 或 `Clone as Draft` 打开表单：

```
┌─ Draft New Config ─────────────────────────────────────────┐
│ name 名称:        [_________________________________]      │
│ description 说明: [_________________________________]      │
│ universe:         [universe_v2 ▾]                          │
│                                                            │
│ ─── params 参数 ────────────────────────────────────────── │
│ pool 池子:        ○ t1_trading  ○ t2_research  ○ all      │
│ price_range 价格区间: [0.25] - [0.75]                       │
│ sizing 仓位模式:  ● fixed_amount  ○ fixed_shares  ○ tiered│
│   amount_usd:     [3.90]                                   │
│ edge_threshold:   [0.10]                                   │
│ stop_loss 止损:   ☐ Enable                                 │
│ maker_policy:     ☐ Enable                                 │
│                                                            │
│ tags:             [phase:explore] [+ Add Tag]              │
│                                                            │
│ ─── 实时校验 ─────────────────────────────────────────── │
│ ✓ Schema 通过                                              │
│ ✓ config_id (预计算): 0x9c2f...                            │
│ ⚠ 无类似 params 的 live run（这是新策略）                  │
│                                                            │
│ [Preview YAML]  [💾 Save to Git]  [✅ Save + Register]     │
└────────────────────────────────────────────────────────────┘
```

#### 两段式保存（核心设计）

| 按钮 | 后端动作 | 何时用 |
|---|---|---|
| **💾 Save to Git** | 写 YAML 文件 + `git add` + `git commit -m "draft: <name>"` | 草稿、暂不入 DB |
| **✅ Save + Register** | 上面 + `weather_config_register` 入 `strategy_config` 表 | 立刻可用作 run |

**铁律：YAML 始终是真理。** UI 是「辅助生成 git commit 的工具」。

**避免双向编辑冲突：** UI 不直接改已 register 的 YAML——只能 **Clone**（另存为新文件 = 新 `config_id`）。修一个 config 就 Clone 出新版本，旧的 deprecate。这维护 config 的不可变性。

### 4.8 Universes 页面

```
┌─ Universes ────────────────────────────────────────────────┐
│ name          │cities │models       │status      │runs    │
│ universe_v2   │ 8     │ 1.3         │frozen ✓    │ 12     │
│ universe_v3   │ 9     │ 1.4         │active      │ 3      │
│ universe_v1   │ 6     │ 1.2         │deprecated ⊘│ 5      │
└────────────────────────────────────────────────────────────┘
```

详情页有 `[Freeze]` / `[Deprecate]` 按钮（写 `frozen_at_utc` / `deprecated_at_utc`，是 trigger 白名单字段，可改）。

YAML draft / edit 模式同 Config。

### 4.9 Run Creation 页面

Runs 页面 `[+ Create Run]` → 表单：

```
┌─ Create Run ─────────────────────────────────────────┐
│ config:    [t1_25-75c_fa_baseline ▾]                 │
│ mode:      ○ snapshot_replay  ○ paper  ○ live        │
│ universe:  [universe_v2 ▾] (config 自动)             │
│ start:     [2026-05-06]  end: [2026-05-13]           │
│ tags:      [phase:explore]                           │
│                                                      │
│ ─── 将执行 ────────────────────────────────────────  │
│ weather_run_create --config-id 0x4b8a                │
│   --mode replay --start 2026-05-06                   │
│   --end 2026-05-13 --tags phase:explore              │
│                                                      │
│ [▶ Start]  [Copy CLI Command]                        │
└──────────────────────────────────────────────────────┘
```

**[▶ Start]** POST `/api/runs`，后端 fork subprocess 运行脚本，立即返回 `run_id`。UI 跳转 `/runs/:run_id`，用户手动刷新看进度（**不流式日志**——简化实现，subprocess 状态写到 `runs.state` 和 `runs.notes` 字段，UI 显示即可）。

### 4.10 数据流 + Refresh 策略

```
React Component
    │   useQuery(['runs', filters])
    ▼
TanStack Query Cache  ─── stale-while-revalidate
    │   GET /api/runs?config_id=...
    ▼
FastAPI Endpoint
    │
    ▼
SQLAlchemy → SQLite
```

| 数据 | staleTime | refetchInterval | 备注 |
|---|---|---|---|
| Run list | 60s | manual | 不太变 |
| Run detail | 5min | manual | metrics 不常 refresh |
| Live wallet/positions | 5s | 10s | 自动刷 |
| Compare result | ∞ | manual | run immutable 后结果不变 |
| Today's settled | 30s | 30s | 间歇结算 |

### 4.11 共享组件

| 组件 | 用途 |
|---|---|
| `<MetricCell field="..." value={...} />` | 自动双语 + 警告色 + tooltip |
| `<ConfigDiffView configs={[...]} />` | 递归 JSON diff，相同字段折叠 |
| `<RunPicker selected={...} onChange={...} />` | 多选 checkbox |
| `<DateRangePicker />` | 常用预设（今天/本周/本月/自定义） |
| `<TagFilter tags={...} />` | 按命名空间分组 |
| `<DecimalDisplay value="1234.56" currency="USD" />` | 统一金额渲染 |
| `<FieldLabel field="..." />` | 双语字段标签（mono 英文 + 中文） |

### 4.12 状态管理

| 状态类型 | 工具 |
|---|---|
| Server state | TanStack Query |
| Filters / selected run_ids | React 组件 state（暂不上 URL，保留默认） |
| 临时 UI（modal、hover） | `useState` |
| 用户偏好 | `localStorage` + custom hook |

### 4.13 暂不做（明确范围）

- **Auth**：Tailscale 内网访问，单用户，无登录
- **流式日志（SSE）**：Run 创建后跳转详情页，手动刷新看状态
- **主题切换 UI**：跟随系统偏好（prefers-color-scheme）
- **Mobile responsive**：desktop-first，平板可看
- **i18n**：纯中文 UI（字段名英文 + 中文标签双语显示）

### 4.14 URL State（filter / comparison 选择同步到 URL）

Runs 页面的过滤条件（state, mode, config_id, tags, date_range）和 Compare 视图选中的 run_ids 同步到 URL query string，刷新页面保留选择、可分享链接。

实现方式：`React Router` `useSearchParams` + 自定义 hook `useUrlState`，在 `URLSearchParams` 和 React state 之间双向同步。

适用页面：`/runs`、`/runs/compare`、`/configs`、`/universes`。

## 5. API Surface（汇总）

所有 endpoint 都在 `/api/*` 下，FastAPI 自动生成 OpenAPI doc（`/api/docs`）。

### 5.1 Runs

| Method | Path | 用途 |
|---|---|---|
| `GET` | `/api/runs` | 列出 + 过滤（query：state, mode, config_id, universe_id, tags, date_range） |
| `GET` | `/api/runs/:run_id` | run 详情（含 metrics JSON、关联 config + universe） |
| `POST` | `/api/runs` | 创建 run（body：config_id, mode, date_range, tags）→ 后端 fork subprocess |
| `GET` | `/api/runs/:run_id/trades` | trade 明细（分页 + 筛选 + 排序） |
| `GET` | `/api/runs/:run_id/breakdown/:dimension` | 按维度归因（dimension: by_day, by_price_bucket, by_city, by_edge, by_side, by_model） |
| `POST` | `/api/runs/compare` | body：`run_ids: []` → 返回 metrics matrix + config diff + 各维度 breakdown |
| `PATCH` | `/api/runs/:run_id/state` | 修改 state（白名单字段） |
| `PATCH` | `/api/runs/:run_id/tags` | 修改 tags |

### 5.2 Configs

| Method | Path | 用途 |
|---|---|---|
| `GET` | `/api/configs` | 列出 + 过滤 |
| `GET` | `/api/configs/:config_id` | 详情 + 关联 runs |
| `GET` | `/api/configs/:config_id/yaml` | 返回原始 YAML |
| `POST` | `/api/configs/validate` | 实时 schema 校验（draft 表单用） |
| `POST` | `/api/configs/draft` | 写 YAML + git commit（不 register） |
| `POST` | `/api/configs/register` | 读 YAML → 入 `strategy_config` 表 |

### 5.3 Universes

| Method | Path | 用途 |
|---|---|---|
| `GET` | `/api/universes` | 列出 |
| `GET` | `/api/universes/:universe_id` | 详情 |
| `POST` | `/api/universes/validate` | schema 校验 |
| `POST` | `/api/universes/draft` | 写 YAML + git commit |
| `POST` | `/api/universes/register` | 读 YAML → 入 `universes` 表 |
| `POST` | `/api/universes/:universe_id/freeze` | 写 `frozen_at_utc` |
| `POST` | `/api/universes/:universe_id/deprecate` | 写 `deprecated_at_utc` |

### 5.4 Live

| Method | Path | 用途 |
|---|---|---|
| `GET` | `/api/live/wallet` | Polymarket 钱包余额（FastAPI 转发，5s 缓存） |
| `GET` | `/api/live/positions` | 当前开放仓位（转发 + 缓存） |
| `GET` | `/api/live/pnl/today` | 今日 PnL（SQLite + 当前价计算 unrealized） |
| `GET` | `/api/live/exposure` | 按 city / bracket 暴露聚合 |

### 5.5 Code Versions（辅助）

| Method | Path | 用途 |
|---|---|---|
| `GET` | `/api/code_versions` | 列出已注册的 code version |
| `POST` | `/api/code_versions` | 注册当前 git HEAD（由部署脚本调用） |

### 5.6 一致性约定

- 所有 timestamp 字段：UTC ISO-8601 字符串
- 所有金额字段：Decimal-as-text 字符串
- 错误响应：`{"detail": "...", "code": "..."}` (Pydantic 默认 422)
- 鉴权：暂无（Tailscale 内网）

## 6. Implementation Plan Pointer

本设计文档覆盖了 dashboard + 实验追踪系统的**完整设计**。下一步是用 **superpowers:writing-plans** skill 把 Section 2-5 拆解为可独立执行的 PR 序列，建议拆分：

| PR # | 范围 | 估算 |
|---|---|---|
| PR1 | DB schema + migrations + idempotent ingest 脚本 + rebuild_db_from_raw | ~3 天 |
| PR2 | strategy config YAML format + register CLI + schema validation | ~1 天 |
| PR3 | universe YAML + register CLI + freeze/deprecate | ~0.5 天 |
| PR4 | run_create CLI + repro_key 计算 + metrics 计算（含 Section 1.5 全套指标） | ~3 天 |
| PR5 | FastAPI backend skeleton + read endpoints | ~2 天 |
| PR6 | FastAPI write endpoints + Polymarket API 转发 + 缓存 | ~1.5 天 |
| PR7 | React app 骨架 + Runs + Run Detail 页面 + 双语字段系统 | ~3 天 |
| PR8 | Compare 视图 + 各维度 breakdown + 警告徽章 | ~2 天 |
| PR9 | Live 页面 | ~1.5 天 |
| PR10 | Configs 页面 + Draft / Edit 表单 + 两段式保存 | ~2 天 |
| PR11 | Universes 页面 + Run Creation 表单 | ~1 天 |
| PR12 | Backup / rebuild 流程 + cron + B2 远端备份 | ~1 天 |
| PR13 | Bootstrap：盘点现有 CLI 用法，迁移到 YAML configs，回填 historical runs | ~1.5 天 |

**总计约 23 个工作日（4-5 周单人 full-time，或 8-10 周 50%）。**

Frontend 视觉设计：PR7 之后用 **frontend-design** skill 走一遍设计语言、Tailwind 主题、shadcn 定制。
