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
> 状态:**已实现**(2026-05-29)。builder = `scripts/analysis/build_weather_signal_candidates.py`,
> 测试 = `tests/weather_dashboard/test_signal_candidates.py`(10 通过),已接入 `run_stack.sh`(1c 步),
> 双写 `runtime/weather.db.fact_signal_candidates` + `…/research/fact_signal_candidates.parquet`。
> 下方 §1 标 ★ 的设计判断均已按所选项落地。
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
| **决策窗(代表值)= builder 参数** | 按 `hours_to_settle` 区间选 snapshot,默认 `[22, 24]`(对齐当前生产 T-22~24h);取落在带内、最接近目标的那条;每行自描述 `decision_hours_to_settle` / `decision_snapshot_ts_utc` | **窗口以后会改**,做成参数而非写死;改窗口=换参数重跑(几秒),主表零时序冗余 |
| 决策窗缺失处理 | 该机会在带内**没有任何 snapshot** → 标 `decision_window_missing=1`,该行 decision 列留空,**不**偷偷拿带外快照顶替 | 显式失败优于静默兜底 |
| intended 来源 | `paper_orders.jsonl`(1873 条),`order_id = condition_id\|side` | 这是策略**实际决定**下的 paper 单,不是 snapshot 重建,口径最硬 |
| actual 来源 | `fact_trades` WHERE `trade_class='live_real'` | 真实成交唯一源,引用而非重算 |
| 关联键 | `(condition_id, side, event_date)`;**缺 condition_id 直接丢弃,不做 fallback** | condition_id 三层都有且最稳;早期缺失量小,用户确认可丢 |
| 反事实入场价(主口径) | **决策窗 `decision_entry_price`**(交易时真能看到的价);`best_entry_price` 仅作诊断上限,**不作主口径** | 交易时不可能预知全天最优价,主路径必须用决策当时的价 |
| 结算来源 | DB `settlements` 表(与 fact_trades 同源) | 口径统一,`final_yes` 单点 |
| 中没中口径 | `bracket_hit = int(final_yes==1.0)`(side-independent);`win_by_count` 才看 side | 与 fact_trades §最近修复对齐,不重新发明 |
| 存储 | 双写 `weather.db.fact_signal_candidates` + `fact_signal_candidates.parquet` | 与 fact_trades 一致:DB 给 API,Parquet 给离线 |
| 刷新 | **全量重建**,挂在 `run_stack.sh` 里 fact_trades **之后** | snapshot 文件不可变 + 表小,全量重建几秒;不搞增量/回填 ceremony |
| 窗口寻优扩展 | **暂不建**;需要"多窗口并排对比不重跑"时,再加伴生表 `fact_candidate_windows`(机会 × hours_to_settle 桶) | YAGNI;重跑已够便宜,纯增量扩展不影响主表 |

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
按 §1 的**决策窗参数**(默认 `hours_to_settle ∈ [22,24]`)选出代表那条 snapshot 塌缩成一行,
同时计算全天聚合列(诊断用)。带内无 snapshot 的机会标 `decision_window_missing=1`。

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
  time_bucket         TEXT,               -- 决策窗 snapshot 的 bucket 标签
  window              TEXT,

  -- 决策窗（builder 参数选出的代表 snapshot，自描述）
  decision_window_label    TEXT,          -- 用了哪个窗，如 "hts_22_24"
  decision_hours_to_settle REAL,          -- 该代表 snapshot 的实际 hours_to_settle
  decision_snapshot_ts_utc TEXT,          -- 代表值取自哪个 snapshot
  decision_window_missing  INTEGER,       -- 1=带内无 snapshot，decision 列留空

  -- 信号（决策窗代表值）
  model_p_yes         REAL,
  market_yes_price    REAL,
  edge                REAL,
  abs_edge            REAL,

  -- 盘口可成交性（决策窗代表值）
  decision_entry_price REAL,              -- ★ 主口径：决策时真能看到的入场价
  yes_spread          REAL,
  no_spread           REAL,
  yes_depth_ask_5c    REAL,
  no_depth_ask_5c     REAL,

  -- 全天聚合（诊断用，非主口径）
  first_seen_ts_utc   TEXT,
  last_seen_ts_utc    TEXT,
  n_snapshots         INTEGER,
  edge_max            REAL,
  edge_mean           REAL,
  best_entry_price    REAL,               -- 诊断：全天最优入场价（盈利上限，不可交易实现）

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
  counterfactual_pnl        REAL,         -- ★ 主口径：用 decision_entry_price 持有到结算的 PnL
  counterfactual_pnl_best   REAL,         -- 诊断：用 best_entry_price（盈利上限，不可交易实现）

  -- build 元数据
  fact_built_at_utc   TEXT
)
```

**反事实 PnL 口径**:沿用 fact_trades 已验证的公式（contract 已对账）
- BUY_YES: `(final_yes - entry) × shares`
- BUY_NO:  `((1 - final_yes) - entry) × shares`
- **主口径 `counterfactual_pnl` 用 `decision_entry_price`**(交易时真能看到的价);`shares` 用决策窗口径下的标准手数。
- `counterfactual_pnl_best` 仅作诊断对照(看离上限多远),**禁止当主绩效**。
- 未结算 `final_yes IS NULL` → 反事实 PnL 留空(不估值,本表不做 Phase 1.5)。

---

## §4 这张表能回答的问题（验收标准）

1. **城市真实 alpha**:`GROUP BY city`,看全机会集（不只成交的）的 `counterfactual_pnl` / win_rate
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
  → build_weather_signal_candidates.py \    # 再本表（读 snapshot + paper_orders + settlements + fact_trades）
        --decision-hts-min 22 --decision-hts-max 24    # 决策窗参数，默认对齐当前生产
  → metrics-refresh
```

**决策窗是参数,不是写死**。改窗口(以后做窗口寻优)= 换 `--decision-hts-*` 重跑,
全量重建几秒,不囤冗余。每行记 `decision_window_label` 自描述,不同窗口的结果不会混淆。

builder 失败时**致命退出**（与 fact_trades 一致,不静默兜底）。
输出双写 `runtime/weather.db` 表 + `runtime/.../fact_signal_candidates.parquet`。

---

## §6 边界（明确不做）

- **不下沉 snapshot 时序**:日内入场时点 / edge 演化研究继续按需读 JSON,不进本表。
- **不重算已成交绩效**:`live_pnl_usd` 直接引用 `fact_trades.pnl_usd_at_fill`,不重写公式。
- **不做未结算估值**:`final_yes IS NULL` 的反事实 PnL 留空;Phase 1.5 估值只在 fact_trades 做。
- **不收 live 之外的 actual**:`live_filled` 只认 `trade_class='live_real'`;paper/snapshot_replay 的"成交"是假设成交,不算真实 actual。

---

## §7 决策记录与剩余开放问题

**已拍板(用户确认):**
1. **代表值 = 决策窗**,做成 builder 参数(`hours_to_settle ∈ [22,24]` 默认,对齐当前生产 T-22~24h);窗口以后会改,故参数化而非写死。
2. **反事实主口径 = `decision_entry_price`**(决策时真能看到的价);`best_entry_price` 降级为诊断上限,不作主绩效——交易时不可能预知全天最优价。
3. **早期缺 condition_id 的机会直接丢弃**,不写 fallback(缺失量小)。
4. **存储不囤冗余**:主表机会粒度一行,改窗口靠重跑(几秒);多窗口并排对比的 `fact_candidate_windows` 伴生表 **YAGNI,暂不建**。

**剩余待定:**
1. **paper_orders 与 snapshot universe 对不齐**(paper 下了但 snapshot 里找不到对应 record)——我倾向 builder **报错暴露**(符合"显式失败"姿态),除非这种孤儿单很常见;需跑一遍数据看占比再定。
2. **决策窗带内多条 snapshot 时选哪条**:最接近目标 hours_to_settle 的、还是带内最晚(信息最全)的?默认取最接近目标,实现时确认。
