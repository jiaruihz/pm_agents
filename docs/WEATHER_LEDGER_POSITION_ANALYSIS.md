# Weather Ledger Position Analysis Design

> **与相关文档的区别**
> - 本文档：信号集固定不变，只模拟不同**仓位 sizing 策略**（fixed shares / fixed dollar / price bucket / city tier 等），看同样的信号改变仓位管理后 PnL 怎么变
> - [`WEATHER_SHADOW_PORTFOLIO_TRACKING.md`](WEATHER_SHADOW_PORTFOLIO_TRACKING.md)：模拟不同**信号过滤规则**组合，关注哪套 filter 长期优于 baseline（信号层面的决策）
> - [`WEATHER_CITY_DAY_PORTFOLIO_OPTIMIZER_DESIGN.md`](WEATHER_CITY_DAY_PORTFOLIO_OPTIMIZER_DESIGN.md)：同城同日的 YES+NO 多腿**组合优化**，关注 bracket 之间的组合收益

## 1. Purpose

这个模块用于回答一个问题:

> 在已有 weather paper ledger 信号不变的前提下，不同仓位管理方式会怎样改变收益、回撤和风险暴露？

它不是新的信号生成器，也不参与生产下单。它只读取已生成的 ledger / settled trades，离线模拟不同 sizing 和 exposure control 规则，为后续实盘或 paper order 的仓位策略提供依据。

## 2. Scope

### In Scope

- 基于 ledger 重建每笔交易的原始风险暴露。
- 比较固定 shares、固定 dollar risk、按价格/edge/城市/side 调整仓位的结果。
- 计算日度、城市、side、model、price bucket、edge bucket 的收益与风险。
- 分析同一城市同一日期的相关 bucket 暴露。
- 输出可读报告和机器可读统计结果。

### Out Of Scope

- 不重新生成信号。
- 不修改 paper order 生产逻辑。
- 不做盘口滑点/成交概率建模，除非后续 ledger 里有足够成交深度字段。
- 不用当前小样本直接定最终仓位参数。

## 3. Primary Inputs

主输入优先使用 settled ledger trades:

```text
runtime/weather_edge_v1/market_data/research/t24_paper_ledger_trades.csv
```

关键字段:

| Field | Usage |
|---|---|
| `event_date` | 日度风险和结算分组 |
| `city` | 城市风险预算 |
| `side` | BUY_YES / BUY_NO sizing |
| `model` | GFS / ECMWF 分层 |
| `bracket` | 同城市同日相邻 bucket 暴露 |
| `entry_price` | 最大亏损、赔率、价格分桶 |
| `shares` | 原始 shares |
| `cost_usd` | 原始最大亏损 |
| `edge`, `abs_edge` | edge-based sizing |
| `settlement_status` | 只对 settled 计算 PnL |
| `won`, `final_yes`, `pnl_usd` | 复算模拟 PnL |
| `snapshot_file`, `snapshot_ts_utc` | 信号时间和运行审计 |

辅助输入:

```text
runtime/weather_edge_v1/market_data/research/t24_paper_snapshot_replay_trades.csv
```

Snapshot replay 可用于扩展样本，但仓位管理的主口径仍建议用 ledger。原因是仓位管理要尽量贴近当时真实捕获到的信号组合和同日风险堆叠。

## 4. Core Concepts

### 4.1 Original Exposure

当前 paper order 接近固定 shares:

```text
shares = 10
cost_usd = entry_price * shares
max_loss = cost_usd
max_profit = shares * (1 - entry_price)
```

这导致不同价格的风险不均衡:

- `<5c`: 成本很小，类似小彩票。
- `40c-70c`: 中等风险。
- `70c+`: 单笔亏损很大，容易吞掉多个 winner。

### 4.2 Simulated Position

每个仓位策略都只改变模拟 shares，不改变信号集合:

```text
sim_shares = sizing_policy(row, context)
sim_cost = sim_shares * entry_price
sim_pnl = sim_shares * (1 - entry_price) if won else -sim_cost
```

这里 `context` 可以包含:

- 同一天已分配风险
- 同一城市同一天已分配风险
- 同一 city/date/side 的累计信号数量
- 当前 bankroll 或模拟资金规模

### 4.3 Exposure Group

天气 bracket 之间高度相关。建议定义 exposure group:

```text
exposure_group = (event_date, city)
```

可选更细:

```text
exposure_group = (event_date, city, model)
exposure_group = (event_date, city, side)
```

仓位分析必须报告每个 exposure group 的:

- 总成本
- 总最大亏损
- 净 PnL
- 最大单笔占比
- bucket 数量
- 是否相邻 bucket 过度集中

## 5. Sizing Policies To Compare

第一版只做少量专业但不过度设计的策略。

### Policy A: Baseline Fixed Shares

当前基准:

```text
shares = original shares
```

用途: 作为所有模拟的比较基准。

### Policy B: Fixed Dollar Risk

每笔固定最大亏损:

```text
max_loss_per_trade = 2.00
shares = max_loss_per_trade / entry_price
```

需要限制:

```text
shares <= max_shares_per_trade
```

用途: 检验固定 shares 是否被高价票拖累。

### Policy C: Price Bucket Risk

按价格桶给不同风险预算:

| Price Bucket | Suggested Max Loss |
|---|---:|
| `<5c` | $0.50 |
| `5c-20c` | $0 or disabled |
| `20c-40c` | $2.00 |
| `40c-70c` | $2.00 |
| `70c+` | $0.75-$1.00 |

用途: 把当前报告里的价格结构直接转化为 sizing 规则。

### Policy D: City Risk Tier

城市分层调整 max loss:

| Tier | Cities | Multiplier |
|---|---|---:|
| Strong | Warsaw, LA, Shanghai, Madrid, Miami | 1.0 |
| Watch | NYC, Chicago, Tokyo | 0.5 |
| Weak | Austin, Beijing, Paris, London | 0 or 0.25 |

用途: 测试城市 alpha 是否足以支持风险预算差异。

### Policy E: Group Cap

限制同一城市同一天的总风险:

```text
max_loss_per_city_day = 5.00
```

如果超过 cap:

1. 按信号时间排序，先到先得；或
2. 按 `abs_edge` 排序，只保留 top K；或
3. 按 estimated EV 排序。

第一版建议只实现前两种，避免过早引入复杂 EV 模型。

### Policy F: Hybrid Conservative

组合规则:

```text
disable 5c-20c
cap 70c+ to $1 max_loss
base max_loss = $2
weak city multiplier = 0
watch city multiplier = 0.5
strong city multiplier = 1
city_day cap = $5
```

用途: 作为第一版推荐候选策略。

## 6. Metrics

### 6.1 Return Metrics

- Total PnL
- Total cost / max loss
- ROI
- Win rate
- Average PnL per trade
- Median PnL per trade
- Profit factor: gross win / gross loss

### 6.2 Risk Metrics

- Daily PnL
- Worst day
- Max drawdown on settled equity curve
- PnL volatility by day
- Largest single-trade loss
- Largest city-day loss
- Share of PnL from top 5 winners
- Share of loss from top 5 losers

### 6.3 Exposure Metrics

- Cost by date
- Cost by city
- Cost by side
- Cost by price bucket
- Cost by edge bucket
- City-day concentration
- Number of trades per city-day
- High-price `BUY_NO` exposure
- Low-price `BUY_YES` exposure

### 6.4 Robustness Metrics

- Performance excluding top winner
- Performance excluding Warsaw
- Performance excluding worst day
- Performance by rolling week
- Minimum sample count warnings

## 7. Output Artifacts

Recommended output directory:

```text
runtime/weather_edge_v1/market_data/research/position_analysis/
```

Outputs:

```text
position_policy_summary.json
position_policy_summary.csv
position_policy_by_day.csv
position_policy_by_city.csv
position_policy_by_price_bucket.csv
position_policy_by_exposure_group.csv
position_analysis_report_YYYY-MM-DD.md
```

The JSON should be the canonical machine-readable output. CSVs support quick spreadsheet inspection. Markdown is for decision review.

## 8. Suggested CLI

Keep the first implementation simple:

```bash
python3 scripts/analysis/weather_position_sizing_analysis.py \
  --trades runtime/weather_edge_v1/market_data/research/t24_paper_ledger_trades.csv \
  --out-dir runtime/weather_edge_v1/market_data/research/position_analysis
```

Daily recurring ledger review should use the reusable pm_agent entrypoint:

```bash
# One event date, fixed report template
python3 scripts/ops/weather_ledger_daily_analysis.py --event-date 2026-05-12

# All settled rows, useful for checking whether a pattern is stable
python3 scripts/ops/weather_ledger_daily_analysis.py --all-settled
```

Outputs:

```text
runtime/weather_edge_v1/market_data/research/daily_ledger_analysis/weather_ledger_analysis_<event_date>.md
runtime/weather_edge_v1/market_data/research/daily_ledger_analysis/weather_ledger_analysis_<event_date>.json
runtime/weather_edge_v1/market_data/research/daily_ledger_analysis/weather_ledger_analysis_all_settled.md
runtime/weather_edge_v1/market_data/research/daily_ledger_analysis/weather_ledger_analysis_all_settled.json
```

Rule: do not add new one-off scripts for daily ledger slicing. Extend `scripts/ops/weather_ledger_daily_analysis.py` when a new recurring dimension is needed.

Optional later flags:

```bash
--include-snapshot-replay
--min-settled-date 2026-05-08
--max-settled-date 2026-05-31
--bankroll 1000
--max-loss-per-trade 2
--max-loss-per-city-day 5
```

## 9. Implementation Shape

Suggested module layout:

```text
scripts/analysis/weather_position_sizing_analysis.py
```

Internal functions:

```text
load_trades(path) -> list[Trade]
settled_only(rows) -> list[Trade]
classify_price_bucket(row) -> str
classify_city_tier(row) -> str
simulate_policy(rows, policy) -> list[SimTrade]
summarize_policy(sim_rows) -> dict
summarize_groups(sim_rows, keys) -> list[dict]
write_outputs(results, out_dir)
```

No database required in v1. Use CSV in, CSV/JSON/Markdown out.

## 10. Data Sufficiency Rules

The module should print warnings when samples are too small:

| Dimension | Minimum For Directional Read | Preferred For Decision |
|---|---:|---:|
| Total settled trades | 200 | 500+ |
| Days | 7 | 20+ |
| City settled trades | 20 | 80+ |
| Side x price bucket | 20 | 50+ |
| City x side | 15 | 50+ |

Current state is enough to design and test the module, but not enough to lock final production sizing parameters.

## 11. Recommended First Version

V1 should compare exactly these policies:

1. `baseline_fixed_shares`
2. `fixed_2usd_loss`
3. `price_bucket_risk`
4. `city_tier_risk`
5. `group_cap_5usd_city_day`
6. `hybrid_conservative`

V1 report should answer:

- Which policy improves ROI without concentrating PnL into fewer trades?
- Which policy reduces worst day and largest single-trade loss?
- How much PnL is lost by disabling weak cities?
- How much risk is saved by capping high-price `BUY_NO`?
- Does fixed dollar risk beat fixed shares?

## 12. Decision Use

Use this module only as a decision support layer. A sizing policy should not be promoted unless:

- It improves both ledger and snapshot replay, or at least does not materially worsen one.
- It reduces worst-day loss or high-price `BUY_NO` tail loss.
- It does not rely on one city or one winner for most PnL.
- It has enough settled sample size for the target dimension.

The first production change should be conservative:

```text
disable 5c-20c
cap 70c+ BUY_NO
add city-day max loss
keep filtered signals in research logs
```

Then review again after 3-4 weeks of settled data.

## 13. Daily Ledger Analysis Template

每天复盘固定用同一格式，主口径用 ledger，snapshot replay 只做对照。

### 13.1 Header

```text
# Weather Paper Ledger Daily Analysis — YYYY-MM-DD

Data timestamp:
- N100 latest snapshot:
- Local mirror synced at:
- Source file:
  - ledger trades: runtime/weather_edge_v1/market_data/research/t24_paper_ledger_trades.csv
  - ledger summary: runtime/weather_edge_v1/market_data/research/t24_paper_ledger_summary.json
  - snapshot replay: runtime/weather_edge_v1/market_data/research/t24_paper_snapshot_replay_trades.csv

Scope:
- event_date:
- source: ledger
- include only settlement_status=settled for PnL
- missing_event / missing_bracket reported separately
```

### 13.2 Data Status

```text
Data Status
- snapshots today:
- latest snapshot:
- latest snapshot records:
  - t1_trading:
  - t2_research:
- ledger total orders:
- event_date orders:
- settled:
- missing_event:
- missing_bracket:
- pm_history files for event_date:
- notes:
```

### 13.3 Overall

```text
Overall
- orders:
- settled:
- missing:
- wins:
- win_rate:
- total_cost:
- settled_cost:
- pnl:
- roi:
- avg_abs_edge:
- avg_entry_price:
```

### 13.4 Pool Split

```text
By Pool
| pool | orders | settled | missing | cost | pnl | roi | win_rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| t1_trading | | | | | | | |
| t2_research | | | | | | | |
| unknown/backfill | | | | | | | |

Read:
- T1:
- T2:
- Difference:
```

### 13.5 City Ranking

```text
By City
| city | orders | settled | missing | cost | cost_pct | pnl | roi | win_rate | avg_edge | avg_entry |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|

Top contributors:
1.
2.
3.

Worst contributors:
1.
2.
3.

Read:
- Good cities:
- Bad cities:
- Missing data cities:
- Cities to watch:
```

### 13.6 Side Split

```text
By Side
| side | orders | settled | missing | cost | cost_pct | pnl | roi | win_rate | avg_edge | avg_entry |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BUY_YES | | | | | | | | | | |
| BUY_NO | | | | | | | | | | |

Read:
- BUY_YES:
- BUY_NO:
- Direction bias:
```

### 13.7 Model Split

```text
By Model
| model | orders | settled | missing | cost | cost_pct | pnl | roi | win_rate | avg_edge | avg_entry |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gfs | | | | | | | | | | |
| ecmwf | | | | | | | | | | |

Read:
- GFS:
- ECMWF:
- Model action:
```

### 13.8 Edge Buckets

Use these buckets unless there is a specific reason to change them:

```text
10-15%
15-25%
25-40%
40%+
```

```text
By Edge Bucket
| abs_edge | orders | settled | missing | cost | pnl | roi | win_rate | avg_entry |
|---|---:|---:|---:|---:|---:|---:|---:|---:|

By Side x Edge Bucket
| side_edge_bucket | orders | settled | missing | cost | pnl | roi | win_rate |
|---|---:|---:|---:|---:|---:|---:|---:|

Read:
- Best edge range:
- Weak edge range:
- Does higher edge actually help:
```

### 13.9 Price / Position Buckets

Use entry price buckets:

```text
<10c
10-25c
25-50c
50-75c
75c+
```

```text
By Entry Price Bucket
| entry_price | orders | settled | missing | cost | pnl | roi | win_rate | avg_edge |
|---|---:|---:|---:|---:|---:|---:|---:|---:|

Read:
- Expensive NO exposure:
- Cheap YES exposure:
- Position sizing issue:
```

### 13.10 Biggest Trades

```text
Top Losses
| city | side | bracket | cost | pnl | edge | entry | model | status |
|---|---|---|---:|---:|---:|---:|---|---|

Top Gains
| city | side | bracket | cost | pnl | edge | entry | model | status |
|---|---|---|---:|---:|---:|---:|---|---|

Read:
- Loss pattern:
- Gain pattern:
- Single-trade concentration:
```

### 13.11 Missing / Data Quality

```text
Missing / Data Quality
| city | side | bracket | status | cost | edge | reason |
|---|---|---|---|---:|---:|---|

Checks:
- missing_event means settlement not available yet.
- missing_bracket means event exists but bracket label did not match settlement brackets.
- For same-day/future event_date, missing_event is expected.
- For past event_date, missing_event needs investigation.
```

### 13.12 Snapshot Replay Comparison

```text
Snapshot Replay Comparison
| source | orders | settled | missing | cost | pnl | roi | win_rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| ledger | | | | | | | |
| snapshot_replay | | | | | | | |

Read:
- Replay adds/removes:
- Same conclusion as ledger:
- Different conclusion:
```

### 13.13 Decision Notes

```text
Decision Notes
- Keep:
- Reduce:
- Disable / investigate:
- Sizing change candidate:
- Data issue:
- Next check:
```

### 13.14 One-Paragraph Summary

End every daily report with one short paragraph:

```text
YYYY-MM-DD was [positive/negative/mixed]. The main driver was [city/model/side/price bucket].
The cleanest positive segment was [...], while the biggest drag was [...].
The next action is [...].
```
