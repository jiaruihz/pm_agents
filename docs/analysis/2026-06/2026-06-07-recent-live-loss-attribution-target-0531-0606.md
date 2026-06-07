# 最近一周 live_real 亏损归因

## 数据快照

| 字段 | 值 |
| --- | --- |
| 数据源 | `runtime/weather.db` 的 `fact_trades` / `fact_signal_candidates` |
| 生成时间 | 2026-06-07T15:06:28+08:00 |
| target_date 窗口 | 2026-05-31..2026-06-06 |
| fact_built_at | 2026-06-07T05:48:57.787507+00:00 |
| live_real rows in window | 653 |
| settled rows | 572 |
| open/null rows | 81 |
| missing_bracket rows | 0 |
| open valuation max ts | None |

## 强制自检

```text
max_fact_built_at: [('2026-06-07T05:48:57.787507+00:00',)]
trade_class_distribution: [('live_real', 1302), ('live_simulated', 1077), ('paper', 2285), ('snapshot_replay', 636)]
settlement_status_distribution: [(None, 388), ('settled', 4912)]
signal_candidate_coverage: [(23299, 7567, 2795, 503)]
clob_order_fill_join: [('error', 151, 0), ('submitted', 1524, 1302)]
```

## 一句话结论

按 `target_date=2026-05-31..2026-06-06`，最近一周已结算 live_real 是 `-225.14`，settled cost `1464.07`，ROI `-15.4%`；未结算 open cost `191.54`，按最新 mid 估值 MTM `0.00`。

亏损主因不是一个抽象的“天气模型整体坏了”，而是 **新增/扩池城市 + 特定 side + V2/YES 兑现差**。core9 在这个窗口仍然赚钱；新增两批城市合计贡献了主要 realized 亏损。

## 按 cohort

| cohort | settled fills | settled pnl | ROI | open cost | open MTM |
| --- | --- | --- | --- | --- | --- |
| new_t1_2026_05_26 | 148 | 396.06 | -109.32 / -27.6% | 54.26 | 0.00 |
| new_t1_2026_05_27 | 94 | 256.27 | -101.10 / -39.4% | 34.97 | 0.00 |
| core9 | 311 | 759.11 | -26.31 / -3.5% | 102.31 | 0.00 |
| other | 19 | 52.63 | 11.58 / 22.0% |  |  |

## 按城市：realized + open

| city | cohort | settled pnl/ROI | settled fills | open cost | open MTM | 判断 |
| --- | --- | --- | --- | --- | --- | --- |
| NYC | core9 | -71.51 / -48.9% | 63 | 19.99 |  | 主要亏损城市 |
| Amsterdam | new_t1_2026_05_27 | -35.20 / -72.7% | 16 | 10.00 |  | 主要扩池亏损 |
| Jeddah | new_t1_2026_05_26 | -29.46 / -57.5% | 22 | 4.99 |  | 主要扩池亏损 |
| Istanbul | new_t1_2026_05_26 | -27.48 / -65.2% | 15 | 5.00 |  | 主要扩池亏损 |
| BuenosAires | new_t1_2026_05_27 | -25.08 / -42.0% | 18 |  |  | 主要扩池亏损 |
| Munich | new_t1_2026_05_27 | -19.45 / -42.2% | 20 | 4.99 |  | 主要扩池亏损 |
| Warsaw | core9 | -18.03 / -21.0% | 38 | 15.00 |  | 主要亏损城市 |
| Ankara | new_t1_2026_05_26 | -16.04 / -24.6% | 28 | 9.92 |  | 主要扩池亏损 |
| Manila | new_t1_2026_05_27 | -13.87 / -26.7% | 19 | 9.99 |  | 主要扩池亏损 |
| Karachi | new_t1_2026_05_26 | -11.42 / -18.2% | 23 | 10.00 |  | 主要扩池亏损 |
| Moscow | new_t1_2026_05_26 | -10.83 / -14.5% | 26 | 9.68 |  | 主要扩池亏损 |
| London | core9 | -9.18 / -8.0% | 41 | 4.93 |  | 观察 |
| Singapore | new_t1_2026_05_27 | -7.95 / -53.0% | 9 | 4.99 |  | 观察 |
| Lucknow | new_t1_2026_05_26 | -7.77 / -21.6% | 15 | 4.69 |  | 观察 |
| Guangzhou | new_t1_2026_05_26 | -7.09 / -14.4% | 13 |  |  | 观察 |
| Chengdu | new_t1_2026_05_27 | 0.44 / 1.2% | 12 | 5.00 |  | 观察 |
| Seattle | new_t1_2026_05_26 | 0.76 / 5.1% | 6 | 9.99 |  | 观察 |
| Madrid | other | 11.58 / 22.0% | 19 |  |  | 观察 |
| Miami | core9 | 15.15 / 10.1% | 70 | 19.22 |  | core9 正贡献 |
| LA | core9 | 15.71 / 11.4% | 57 | 19.99 |  | core9 正贡献 |
| Tokyo | core9 | 16.58 / 30.7% | 19 |  |  | core9 正贡献 |
| Shanghai | core9 | 24.97 / 35.2% | 23 | 23.18 |  | core9 正贡献 |

## 按 side

| side | settled fills | settled pnl/ROI | win | avg fill |
| --- | --- | --- | --- | --- |
| BUY_YES | 250 | -165.87 / -32.5% | 20.4% | 0.31 |
| BUY_NO | 322 | -59.27 / -6.2% | 56.8% | 0.61 |

## 按 strategy_id

| strategy_id | fills | pnl/ROI | win | avg fill |
| --- | --- | --- | --- | --- |
| live_weather_edge_v1_4ef9b3ec3e2e | 337 | -163.91 / -18.2% | 41.5% | 0.50 |
| live_weather_edge_v1_986d901ccc58 | 128 | -93.59 / -29.7% | 32.8% | 0.46 |
| live_weather_edge_v1_4b07f7abc42f | 57 | -5.79 / -3.9% | 52.6% | 0.52 |
| live_weather_edge_v1_c13ccf0c3181 | 3 | 7.61 / 81.1% | 100.0% | 0.56 |
| live_weather_edge_v1_91f019941593 | 47 | 30.55 / 33.1% | 40.4% | 0.30 |

## 最差 city×side

| city | side | cohort | fills | pnl/ROI | win |
| --- | --- | --- | --- | --- | --- |
| NYC | BUY_YES | core9 | 40 | -58.60 / -78.0% | 5.0% |
| Amsterdam | BUY_YES | new_t1_2026_05_27 | 8 | -25.87 / -100.0% | 0.0% |
| Warsaw | BUY_YES | core9 | 27 | -25.16 / -53.1% | 14.8% |
| Jeddah | BUY_NO | new_t1_2026_05_26 | 13 | -19.46 / -47.2% | 46.2% |
| BuenosAires | BUY_YES | new_t1_2026_05_27 | 6 | -18.03 / -100.0% | 0.0% |
| Moscow | BUY_YES | new_t1_2026_05_26 | 12 | -17.89 / -100.0% | 0.0% |
| Istanbul | BUY_NO | new_t1_2026_05_26 | 8 | -17.06 / -69.6% | 12.5% |
| Lucknow | BUY_YES | new_t1_2026_05_26 | 6 | -13.46 / -100.0% | 0.0% |
| Manila | BUY_YES | new_t1_2026_05_27 | 7 | -12.96 / -100.0% | 0.0% |
| NYC | BUY_NO | core9 | 23 | -12.91 / -18.2% | 52.2% |
| Munich | BUY_YES | new_t1_2026_05_27 | 6 | -11.20 / -100.0% | 0.0% |
| Ankara | BUY_YES | new_t1_2026_05_26 | 8 | -10.47 / -100.0% | 0.0% |
| Istanbul | BUY_YES | new_t1_2026_05_26 | 7 | -10.43 / -59.0% | 14.3% |
| Jeddah | BUY_YES | new_t1_2026_05_26 | 9 | -10.00 / -100.0% | 0.0% |
| Amsterdam | BUY_NO | new_t1_2026_05_27 | 8 | -9.33 / -41.4% | 37.5% |
| Munich | BUY_NO | new_t1_2026_05_27 | 14 | -8.25 / -23.6% | 35.7% |
| Miami | BUY_NO | core9 | 28 | -8.03 / -11.2% | 53.6% |
| Singapore | BUY_NO | new_t1_2026_05_27 | 9 | -7.95 / -53.0% | 22.2% |

## 交易动作

1. 不要把最近一周亏损归因成“所有城市都不行”：core9 仍然正，扩池城市明显拖累。
2. 新增城市不要继续同权 live；先按 `city×side` 降到 shadow/low size，尤其是报告表里的负 `city×side`。
3. V2/YES 相关亏损需要继续单独复盘；短期不应用 V2 或新城市池做扩大。
4. 这不是钱包现金流报告；如果要解释 USDC 余额少了多少，要再跑 `weather_live_account_reconcile.py --date-field fill_date_bj`。

