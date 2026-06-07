# 最近一周 live_real 亏损归因

> 2026-06-06 口径勘误：本文生成于 near-binary settlement 修复前，报告中的 `missing_bracket=28/734`、settled realized PnL、open cost 和 target_date attribution 已被后续账户对账快照覆盖；钱包现金流请按 `fill_date_bj` 重算。见 [2026-06-06-live-account-reconcile-near-binary-fix.md](2026-06-06-live-account-reconcile-near-binary-fix.md)。

## 数据快照

| 字段 | 值 |
| --- | --- |
| 数据源 | `runtime/weather.db` 的 `fact_trades` / `fact_signal_candidates` |
| 生成时间 | 2026-06-06T09:24:11+08:00 |
| target_date 窗口 | 2026-05-31..2026-06-06 |
| fact_built_at | 2026-06-05T18:31:00.166369+00:00 |
| live_real rows in window | 418 |
| settled rows | 319 |
| open/null rows | 71 |
| missing_bracket rows | 28 |
| open valuation max ts | 2026-06-05T18:00:53Z |

## 强制自检

```text
max_fact_built_at: [('2026-06-05T18:31:00.166369+00:00',)]
trade_class_distribution: [('live_real', 844), ('live_simulated', 1021), ('paper', 2285), ('snapshot_replay', 636)]
settlement_status_distribution: [(None, 299), ('missing_bracket', 734), ('settled', 3753)]
signal_candidate_coverage: [(21797, 6963, 2570, 455)]
clob_order_fill_join: [('error', 151, 0), ('submitted', 953, 844)]
```

## 一句话结论

按 `target_date=2026-05-31..2026-06-06`，最近一周已结算 live_real 是 `-61.26`，settled cost `984.26`，ROI `-6.2%`；未结算 open cost `232.50`，按最新 mid 估值 MTM `-33.54`。

亏损主因不是一个抽象的“天气模型整体坏了”，而是 **新增/扩池城市 + 特定 side + V2/YES 兑现差**。core9 在这个窗口仍然赚钱；新增两批城市合计贡献了主要 realized 亏损。

## 按 cohort

| cohort | settled fills | settled pnl | ROI | open cost | open MTM |
| --- | --- | --- | --- | --- | --- |
| new_t1_2026_05_27 | 48 | 164.75 | -63.21 / -38.4% | 43.87 | -4.85 |
| new_t1_2026_05_26 | 87 | 284.58 | -52.64 / -18.5% | 59.02 | -5.01 |
| other | 10 | 31.79 | -8.24 / -25.9% | 5.00 | 0.00 |
| core9 | 174 | 503.14 | 62.83 / 12.5% | 124.62 | -23.69 |

## 按城市：realized + open

| city | cohort | settled pnl/ROI | settled fills | open cost | open MTM | 判断 |
| --- | --- | --- | --- | --- | --- | --- |
| NYC | core9 | -35.31 / -41.2% | 33 | 14.87 | -5.00 | 主要亏损城市 |
| Amsterdam | new_t1_2026_05_27 | -33.32 / -100.0% | 9 |  |  | 主要扩池亏损 |
| BuenosAires | new_t1_2026_05_27 | -29.90 / -64.6% | 11 | 4.42 | -2.89 | 主要扩池亏损 |
| Istanbul | new_t1_2026_05_26 | -22.09 / -75.3% | 9 | 7.13 | 0.25 | 主要扩池亏损 |
| Guangzhou | new_t1_2026_05_26 | -14.25 / -29.6% | 11 | 5.00 |  | 主要扩池亏损 |
| Jeddah | new_t1_2026_05_26 | -13.65 / -47.7% | 9 | 7.15 | 0.15 | 主要扩池亏损 |
| Manila | new_t1_2026_05_27 | -12.98 / -51.1% | 10 | 19.76 | 0.43 | 主要扩池亏损 |
| Ankara | new_t1_2026_05_26 | -11.19 / -20.7% | 18 | 8.30 | -0.41 | 主要扩池亏损 |
| Madrid | other | -8.24 / -25.9% | 10 | 5.00 |  | 观察 |
| Tokyo | core9 | -3.32 / -8.8% | 10 | 17.97 |  | 观察 |
| Singapore | new_t1_2026_05_27 | -3.20 / -100.0% | 1 | 4.22 |  | 观察 |
| Seattle | new_t1_2026_05_26 | -3.05 / -37.9% | 2 |  |  | 观察 |
| Moscow | new_t1_2026_05_26 | 4.66 / 8.5% | 17 | 13.79 | -5.11 | 未结算风险偏负 |
| Lucknow | new_t1_2026_05_26 | 2.29 / 11.4% | 8 | 2.87 | 0.03 | 观察 |
| Miami | core9 | 16.27 / 25.5% | 30 | 24.99 | -13.03 | core9 正贡献 |
| London | core9 | 11.46 / 14.1% | 23 | 11.36 | -8.07 | core9 正贡献 |
| Karachi | new_t1_2026_05_26 | 4.64 / 11.2% | 13 | 14.78 | 0.08 | 观察 |
| Munich | new_t1_2026_05_27 | 7.87 / 23.0% | 9 | 5.48 | -2.88 | 观察 |
| Warsaw | core9 | 6.54 / 8.7% | 24 | 14.64 | 0.34 | 观察 |
| Chengdu | new_t1_2026_05_27 | 8.31 / 37.1% | 8 | 9.99 | 0.50 | 观察 |
| Shanghai | core9 | 19.59 / 29.3% | 19 | 23.06 | -1.23 | core9 正贡献 |
| LA | core9 | 47.59 / 51.6% | 35 | 17.72 | 3.31 | core9 正贡献 |

## 按 side

| side | settled fills | settled pnl/ROI | win | avg fill |
| --- | --- | --- | --- | --- |
| BUY_YES | 123 | -63.67 / -21.2% | 27.6% | 0.31 |
| BUY_NO | 196 | 2.40 / 0.4% | 62.2% | 0.62 |

## 按 strategy_id

| strategy_id | fills | pnl/ROI | win | avg fill |
| --- | --- | --- | --- | --- |
| live_weather_edge_v1_986d901ccc58 | 117 | -55.76 / -17.1% | 41.0% | 0.45 |
| live_weather_edge_v1_4ef9b3ec3e2e | 152 | -36.21 / -7.0% | 50.7% | 0.54 |
| live_weather_edge_v1_4b07f7abc42f | 28 | -7.85 / -8.7% | 57.1% | 0.54 |
| live_weather_edge_v1_c13ccf0c3181 | 2 | 5.43 / 85.2% | 100.0% | 0.56 |
| live_weather_edge_v1_91f019941593 | 20 | 33.12 / 74.4% | 65.0% | 0.38 |

## 最差 city×side

| city | side | cohort | fills | pnl/ROI | win |
| --- | --- | --- | --- | --- | --- |
| NYC | BUY_YES | core9 | 21 | -36.00 / -78.1% | 4.8% |
| Amsterdam | BUY_YES | new_t1_2026_05_27 | 5 | -19.68 / -100.0% | 0.0% |
| BuenosAires | BUY_YES | new_t1_2026_05_27 | 4 | -18.83 / -100.0% | 0.0% |
| Istanbul | BUY_NO | new_t1_2026_05_26 | 5 | -15.40 / -100.0% | 0.0% |
| Guangzhou | BUY_NO | new_t1_2026_05_26 | 11 | -14.25 / -29.6% | 45.5% |
| Amsterdam | BUY_NO | new_t1_2026_05_27 | 4 | -13.64 / -100.0% | 0.0% |
| Jeddah | BUY_NO | new_t1_2026_05_26 | 8 | -13.59 / -47.6% | 37.5% |
| BuenosAires | BUY_NO | new_t1_2026_05_27 | 7 | -11.06 / -40.3% | 28.6% |
| Warsaw | BUY_YES | core9 | 13 | -8.45 / -28.9% | 23.1% |
| Ankara | BUY_NO | new_t1_2026_05_26 | 14 | -7.00 / -14.1% | 50.0% |
| Manila | BUY_YES | new_t1_2026_05_27 | 4 | -6.89 / -100.0% | 0.0% |
| Istanbul | BUY_YES | new_t1_2026_05_26 | 4 | -6.68 / -48.0% | 25.0% |
| Lucknow | BUY_YES | new_t1_2026_05_26 | 3 | -6.20 / -100.0% | 0.0% |
| Moscow | BUY_YES | new_t1_2026_05_26 | 5 | -6.13 / -100.0% | 0.0% |
| Manila | BUY_NO | new_t1_2026_05_27 | 6 | -6.09 / -32.9% | 50.0% |
| Madrid | BUY_NO | other | 8 | -4.25 / -16.1% | 50.0% |
| Ankara | BUY_YES | new_t1_2026_05_26 | 4 | -4.18 / -100.0% | 0.0% |
| Madrid | BUY_YES | other | 2 | -4.00 / -74.7% | 50.0% |

## 交易动作

1. 不要把最近一周亏损归因成“所有城市都不行”：core9 仍然正，扩池城市明显拖累。
2. 新增城市不要继续同权 live；先按 `city×side` 降到 shadow/low size，尤其是报告表里的负 `city×side`。
3. V2/YES 相关亏损需要继续单独复盘；短期不应用 V2 或新城市池做扩大。
4. 这不是钱包现金流报告；如果要解释 USDC 余额少了多少，要再跑 `weather_live_account_reconcile.py --date-field fill_date_bj`。
