# Weather Signal Candidates 底表设计

> 目的:回答 **"这个城市/方向/模型本身有没有可交易 alpha"**——不是"我们成交到的样本表现"。
> 建立**机会粒度(opportunity-grain)候选事实表 `fact_signal_candidates`**:
> 每个 `(condition_id, side, event_date)` 机会一行,装下"我们看见过的全部机会",
> 并把三层数据对齐到同一行:**全机会宇宙(snapshot)→ 我们决定下的 paper 单(intended)→ 真实成交(fact_trades / live)**。
>
> **核心要解决的(用户明确点名)**:回测/未实盘的 paper 下单(我们*认为*会交易的)
> 与刚洗出来的真实成交表 `fact_trades` 之间的关联。这条链是本表存在的主要理由。
>
> **范围边界**:本表 grain 是**机会**,不是 fill,也**不下沉到 snapshot 时序粒度**(那 62 万行只在研究"日内入场时点"时按需读 JSON,不物化)。
> 已成交绩效仍归 [`fact_trades`](WEATHER_FACT_TRADES_DESIGN.md),本表不重复造已成交绩效口径,而是引用它。
>
> 状态:**设计待评审**,未实现。
> 关联:[WEATHER_FACT_TRADES_DESIGN.md](WEATHER_FACT_TRADES_DESIGN.md) §8(本表的占位)、
> [WEATHER_ANALYSIS_CONTRACT.md](WEATHER_ANALYSIS_CONTRACT.md)(口径)、
> [WEATHER_DATA_PIPELINE.md](WEATHER_DATA_PIPELINE.md)(管道)

---

## §0 背景:fact_trades 答不了的问题

`fact_trades` 是 **fill 粒度**,只装实际成交了的交易,带 survivorship bias——它能回答
"我们成交过的交易里谁赚钱、是否依赖 BUY_NO/某模型/某执行策略",但答不了:

1. **没下单的候选信号**——有 edge 但策略没决定下单
2. **下单但没成交的机会**——paper 决定了,live 没 fill(missed fill)
3. **被过滤掉的城市/窗口**——`eligible_for_paper_order=false` 的机会集
4. **全天候所有 bracket 的机会集**——每城市每天的完整可交易空间
5. **intended vs actual 的差**——我们*想*按 `entry_price` 进,live 实际 `fill_price` 是多少(滑点),想下的单到底成没成

这些都是**机会粒度**的问题。本表把"全机会宇宙"物化成一张表,并标注每个机会
"是否被 paper 下单 / 是否 live 成交 / 结算中没中",从而能算**真实 alpha**(全机会集),
而不只是"我们交易到的样本"。

---

## §1 设计决策(待评审 — 标 ★ 的是我替你做的判断,请重点确认)

| 决策 | 选择 | 依据 |
|---|---|---|
| 颗粒度 | **机会粒度** = 每 `(condition_id, side, event_date)` 一行 | 对题("城市 alpha"),不是时序;Polymarket 每 bracket 独立 condition_id,故已隐含 bracket |
| 不下沉 snapshot 时序 | 62 万行的 30 分钟切片**不物化**;日内入场时点研究按需读 JSON | 用户明确:没必要落到 snapshot 维度;低频问题不值得维护 |
| 机会宇宙来源 | `paper_snapshots/*.json` 全部 record(含 `eligible=false`) | 这是唯一的全机会集来源;DB signals 表只有产生了 plan/order 的信号,不全 |
| ★ "代表值"取哪个 snapshot | **决策窗 snapshot**:有 paper 单时用其 `snapshot_ts_utc` 对应的那条;否则取该机会**首次 eligible** 的 snapshot;**并行保留全天聚合统计**(edge_max/mean、best_entry、n_snapshots) | 避免被某一时点的 spread 噪声误导;聚合列兜底日内变化 |
| intended 来源 | `paper_orders.jsonl`(1873 条),`order_id = condition_id\|side` | 这是策略**实际决定**下的 paper 单,不是 snapshot 重建,口径最硬 |
| actual 来源 | `fact_trades` WHERE `trade_class='live_real'` | 真实成交唯一源,引用而非重算 |
| ★ 关联键 | `(condition_id, side, event_date)`;缺 condition_id 时 fallback `(city, event_date, bracket, side)` | condition_id 在三层数据里都有且最稳;fallback 处理早期缺失 |
| 结算来源 | DB `settlements` 表(与 fact_trades 同源) | 口径统一,`final_yes` 单点 |
| 中没中口径 | `bracket_hit = int(final_yes==1.0)`(side-independent);`win_by_count` 才看 side | 与 fact_trades §最近修复对齐,不重新发明 |
| 存储 | 双写 `weather.db.fact_signal_candidates` + `fact_signal_candidates.parquet` | 与 fact_trades 一致:DB 给 API,Parquet 给离线 |
| 刷新 | **全量重建**,挂在 `run_stack.sh` 里 fact_trades **之后** | snapshot 文件不可变 + 表小,全量重建几秒;不搞增量/回填 ceremony |

---

## §2 数据来源与对齐路径

grain = 一个机会 `(condition_id, side, event_date)`。三层对齐:

```
机会宇宙 (universe)            ← paper_snapshots/*.json 全 record，按 (condition_id, side, event_date) 去重
  ├─ intended (paper 决定)     ← paper_orders.jsonl     ON (condition_id, side)  → paper_ordered=1, 取 entry_price/shares/snapshot_ts
  ├─ actual (live 成交)        ← fact_trades            ON (condition_id, side, target_date) AND trade_class='live_real'
  │                                                       → live_filled=1, 取 fill_price/fill_qty/fill_id/pnl_usd_at_fill
  └─ 结算                       ← settlements (DB)        ON (condition_id, bracket, target_date)  → final_yes
```

**去重(universe → 机会粒度)**:同一机会在当天多个 snapshot 里出现多次。
按 §1 的"代表值"规则塌缩成一行,同时计算聚合列。

**三个布尔标志把链路标清楚**(这是本表的核心产出):

| 标志 | 含义 | 来源 join |
|---|---|---|
| `seen` | 机会出现在 snapshot(恒为 1,定义如此) | snapshot |
| `eligible` | 是生产交易池机会 | snapshot `eligible_for_paper_order` |
| `paper_ordered` | 策略决定 paper 下单 | join paper_orders 命中 |
| `live_filled` | live 真实成交 | join fact_trades(live_real)命中 |

由此派生关键分析视图:
- **missed_fill** = `paper_ordered=1 AND live_filled=0`(想下没成的)
- **counterfactual_win** = `paper_ordered=0 AND 结算中了`(漏掉的赢家)
- **filtered_out** = `eligible=0`(被过滤的机会,看它们若交易会怎样)

---

## §3 表结构(候选列,评审时可增删)

```sql
CREATE TABLE IF NOT EXISTS fact_signal_candidates (
  -- grain / 关联键
  candidate_id        TEXT PRIMARY KEY,   -- f"{condition_id}|{side}|{event_date}"
  condition_id        TEXT,
  market_id           TEXT,
  side                TEXT,               -- BUY_YES / BUY_NO
  event_date          TEXT,               -- = target_date
  bracket             TEXT,

  -- 维度
  city                TEXT,
  city_pool           TEXT,               -- t1_trading / t2_research
  icao                TEXT,
  unit                TEXT,
  forecast_source     TEXT,               -- e.g. open_meteo_live_ecmwf
  model_version       TEXT,               -- ecmwf / gfs
  time_bucket         TEXT,               -- 决策窗 bucket（t24/t12/t6...）
  window              TEXT,

  -- 信号（决策窗代表值）
  model_p_yes         REAL,
  market_yes_price    REAL,
  edge                REAL,
  abs_edge            REAL,
  hours_to_settle     REAL,
  decision_snapshot_ts_utc TEXT,          -- 代表值取自哪个 snapshot

  -- 盘口可成交性（决策窗代表值）
  entry_price         REAL,               -- 候选入场价（snapshot 口径）
  yes_spread          REAL,
  no_spread           REAL,
  yes_depth_ask_5c    REAL,
  no_depth_ask_5c     REAL,

  -- 全天聚合（防被单点噪声误导）
  first_seen_ts_utc   TEXT,
  last_seen_ts_utc    TEXT,
  n_snapshots         INTEGER,
  edge_max            REAL,
  edge_mean           REAL,
  best_entry_price    REAL,               -- 当天该机会最优可得入场价

  -- 链路标志
  seen                INTEGER,            -- 恒 1
  eligible            INTEGER,            -- eligible_for_paper_order
  paper_ordered       INTEGER,
  live_filled         INTEGER,

  -- intended（paper 决定）
  paper_order_id      TEXT,
  paper_entry_price   REAL,
  paper_shares        REAL,
  paper_snapshot_ts_utc TEXT,

  -- actual（live 成交，引用 fact_trades）
  fill_id             TEXT,               -- → fact_trades.fill_id
  live_fill_price     REAL,
  live_fill_qty       REAL,
  live_pnl_usd        REAL,               -- = fact_trades.pnl_usd_at_fill

  -- intended vs actual
  slippage_vs_paper   REAL,               -- live_fill_price - paper_entry_price

  -- 结算 / 中没中
  settlement_status   TEXT,
  final_yes           REAL,
  bracket_hit         INTEGER,            -- int(final_yes==1.0)  side-independent
  win_by_count        INTEGER,            -- 该 side 是否赢

  -- 反事实绩效（机会本身的 alpha，不依赖是否成交）
  counterfactual_pnl_best   REAL,         -- 用 best_entry_price 持有到结算的 PnL
  counterfactual_pnl_paper  REAL,         -- 用 paper_entry_price 的 PnL

  -- build 元数据
  fact_built_at_utc   TEXT
)
```

**反事实 PnL 口径**:沿用 fact_trades 已验证的公式（contract 已对账）
- BUY_YES: `(final_yes - entry) × shares`
- BUY_NO:  `((1 - final_yes) - entry) × shares`
未结算的机会 `final_yes IS NULL` → 反事实 PnL 留空（不估值,本表不做 Phase 1.5）。

---

## §4 这张表能回答的问题（验收标准）

1. **城市真实 alpha**:`GROUP BY city`,看全机会集（不只成交的）的 `counterfactual_pnl_best` / win_rate
2. **方向依赖**:`GROUP BY side`,验证"BUY_NO 远强于 BUY_YES"是否在全机会集成立,还是只是成交样本偏差
3. **模型/forecast_source alpha**:`GROUP BY model_version / forecast_source`
4. **成交率与漏单**:`paper_ordered` 中 `live_filled` 的比例（fill rate）、`missed_fill` 集中在哪些城市/价位/spread
5. **滑点**:`AVG(slippage_vs_paper)`——我们想进的价 vs 真实成交价差多少
6. **漏掉的赢家**:`counterfactual_win`——没下单但结算中了的机会,本可赚多少
7. **过滤是否正确**:`filtered_out` 机会若交易的反事实绩效,反推过滤规则好坏

---

## §5 刷新与管道接入

**不搞增量**。snapshot 文件写出即不可变,表又小（几万行），每次全量重建:

```
sync_weather_remote.sh        # 拉 N100 最新 snapshot / paper_orders / settlement 镜像
run_stack.sh
  → build_weather_fact_trades.py            # 先 fact_trades
  → build_weather_signal_candidates.py      # 再本表（读 snapshot + paper_orders + settlements + fact_trades）
  → metrics-refresh
```

builder 失败时**致命退出**（与 fact_trades 一致,不静默兜底）。
输出双写 `runtime/weather.db` 表 + `runtime/.../fact_signal_candidates.parquet`。

---

## §6 边界（明确不做）

- **不下沉 snapshot 时序**:日内入场时点 / edge 演化研究继续按需读 JSON,不进本表。
- **不重算已成交绩效**:`live_pnl_usd` 直接引用 `fact_trades.pnl_usd_at_fill`,不重写公式。
- **不做未结算估值**:`final_yes IS NULL` 的反事实 PnL 留空;Phase 1.5 估值只在 fact_trades 做。
- **不收 live 之外的 actual**:`live_filled` 只认 `trade_class='live_real'`;paper/snapshot_replay 的"成交"是假设成交,不算真实 actual。

---

## §7 待评审/待定的开放问题

1. ★ **代表值规则**(§1)——你接受"决策窗优先 + 聚合兜底",还是想直接用"edge 最大那刻"或纯聚合?
2. **early-period 缺 condition_id** 的机会占比多大?fallback 键够不够?(需跑一遍数据确认)
3. **paper_orders 与 snapshot universe 对不齐**的情况(paper 下了但 snapshot 里找不到对应 record)——报错还是单列标记?
4. **counterfactual entry 用哪个价**:`best_entry_price`(乐观)还是决策窗 `entry_price`(贴近真实决策)?我倾向两个都存(§3 已两列),分析时自己选。
