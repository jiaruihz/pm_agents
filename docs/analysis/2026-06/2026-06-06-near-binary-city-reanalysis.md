# 2026-06-06 near-binary settlement 勘误后城市重算

## 结论先行

- 这次重算后，旧报告里 `missing_bracket=725/734/28` 的口径已经过时；本轮 `fact_trades.missing_bracket_rows=0`。
- DB 与 raw CLOB fills 对齐：`db_live_real_distinct_fills=855`，`raw_clob_distinct_fills=855`，差异 `0/0`。
- 城市问题仍然成立，但更精确：不是所有城市坏，而是新增城市和 BUY_YES 侧在 live 兑现上污染了组合；城市评价必须升到 `city×side×strategy_instance`。
- 立即动作不变：`NYC BUY_YES` 继续 ban；`Guangzhou/Jeddah/Manila/Ankara` 不应留在 normal live；`Istanbul` 是 recent drawdown，不够证据整城永久删除，但不能加仓。

## 数据快照

| 字段 | 值 |
| --- | --- |
| 数据源 | `runtime/weather.db` + fact_trades / fact_signal_candidates |
| 生成时间 | 2026-06-06T11:58:22+08:00 |
| DB mtime | 2026-06-06T11:54:46+08:00 |
| fact_trades | 4808 rows, target_date 2026-05-06..2026-06-06 |
| fact_signal_candidates | 21845 rows, event_date 2026-05-05..2026-06-06 |
| missing_bracket | 0 |
| unsettled/null | 321 |
| 本轮刷新 | 已跑 `sync_weather_remote.sh` + `run_stack.sh` rebuild；API 启动因 8000 占用失败，但 DB/fact 表已完成 |

## 强制 SQL 自检

```json
{
  "freshness": [
    {
      "max_fact_built_at_utc": "2026-06-06T03:54:29.726938+00:00"
    }
  ],
  "trade_class_distribution": [
    {
      "trade_class": "live_real",
      "rows": 855
    },
    {
      "trade_class": "live_simulated",
      "rows": 1032
    },
    {
      "trade_class": "paper",
      "rows": 2285
    },
    {
      "trade_class": "snapshot_replay",
      "rows": 636
    }
  ],
  "settlement_status_distribution": [
    {
      "settlement_status": null,
      "rows": 321
    },
    {
      "settlement_status": "settled",
      "rows": 4487
    }
  ],
  "signal_candidate_coverage": [
    {
      "rows": 21845,
      "eligible": 6974,
      "paper_ordered": 2621,
      "live_filled": 464
    }
  ],
  "clob_order_fill_join": [
    {
      "status": "error",
      "orders": 151,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 964,
      "with_fill": 855
    }
  ]
}
```

## Account Reconcile

现金流口径使用 `fill_date_bj`，窗口 `2026-05-31`..`2026-06-06`。`cash_cost_usd` 是已成交买入花钱，不是亏损；`realized_pnl_usd` 只统计 settled。

| instance | date | fills | cash_cost | settled_pnl | open_cost | mtm_mid | val_ts |
| --- | --- | --- | --- | --- | --- | --- | --- |
| mid_price_core_v1_25_75 | 2026-06-02 | 38 | 137.92 | -0.85 | 0.00 | 0.00 |  |
| mid_price_core_v2_25_75 | 2026-05-31 | 43 | 113.98 | -54.71 | 0.00 | 0.00 |  |
| mid_price_core_v1_25_75 | 2026-06-05 | 36 | 111.34 | 0.00 | 111.34 | -0.64 | 2026-06-06T03:30:53Z |
| mid_price_core_v1_25_75 | 2026-06-04 | 32 | 107.60 | -29.35 | 28.86 | 0.00 |  |
| mid_price_core_v1_25_75 | 2026-05-31 | 34 | 103.86 | -41.49 | 0.00 | 0.00 |  |
| mid_price_core_v1_25_75 | 2026-06-01 | 32 | 102.74 | 23.62 | 0.00 | 0.00 |  |
| mid_price_core_v2_25_75 | 2026-06-01 | 39 | 98.65 | 7.33 | 0.00 | 0.00 |  |
| mid_price_core_v1_25_75 | 2026-06-03 | 24 | 82.54 | 12.72 | 0.00 | 0.00 |  |
| mid_price_core_v1_side_band | 2026-06-01 | 21 | 59.82 | 37.69 | 0.00 | 0.00 |  |
| mid_price_core_v2_25_75 | 2026-06-02 | 19 | 58.19 | -10.26 | 0.00 | 0.00 |  |
| mid_price_core_v2_25_75 | 2026-06-04 | 17 | 50.74 | -40.65 | 2.85 | 0.00 |  |
| mid_price_core_v2_25_75 | 2026-06-05 | 14 | 44.82 | 1.75 | 41.57 | -1.57 | 2026-06-06T03:30:53Z |
| mid_price_core_v1_side_band | 2026-06-04 | 12 | 38.14 | -20.85 | 9.36 | 0.00 |  |
| mid_price_core_v2_25_75 | 2026-06-03 | 12 | 34.90 | 28.25 | 0.00 | 0.00 |  |
| mid_price_core_v1_25_75 | 2026-06-06 | 12 | 29.31 | 0.00 | 29.31 | 1.85 | 2026-06-06T03:30:53Z |
| mid_price_core_v1_side_band | 2026-06-05 | 8 | 24.66 | 0.00 | 24.66 | -2.38 | 2026-06-06T03:30:53Z |
| mid_price_core_v1_side_band | 2026-06-02 | 7 | 20.71 | 4.19 | 0.00 | 0.00 |  |
| mid_price_core_v1_side_band | 2026-06-06 | 3 | 14.89 | 0.00 | 14.89 | 0.73 | 2026-06-06T03:30:53Z |
| mid_price_core_v1_side_band | 2026-05-31 | 4 | 12.78 | 3.19 | 0.00 | 0.00 |  |
| mid_price_core_v1_side_band | 2026-06-03 | 6 | 12.62 | 1.05 | 0.00 | 0.00 |  |

Raw live order files 同窗口：submitted `1809.00`，posted `1794.96`；raw CLOB fill cost `1160.65`。

## 最近一周 live_real 亏损切片

按 `target_date` 归因，窗口 `2026-05-31`..`2026-06-06`，只看已结算 live_real。

### 按城市

| city | fills | days | cost | pnl | ROI | win |
| --- | --- | --- | --- | --- | --- | --- |
| NYC | 35 | 5 | 92.27 | -41.98 | -45.5% | 28.6% |
| Amsterdam | 14 | 3 | 46.35 | -38.16 | -82.3% | 14.3% |
| Istanbul | 9 | 3 | 29.33 | -22.09 | -75.3% | 11.1% |
| BuenosAires | 15 | 5 | 62.92 | -21.60 | -34.3% | 40.0% |
| Guangzhou | 11 | 5 | 48.21 | -14.25 | -29.6% | 45.5% |
| Jeddah | 9 | 5 | 28.61 | -13.65 | -47.7% | 33.3% |
| Manila | 10 | 4 | 25.38 | -12.98 | -51.1% | 30.0% |
| Ankara | 18 | 5 | 53.95 | -11.19 | -20.7% | 38.9% |
| Madrid | 10 | 5 | 31.79 | -8.24 | -25.9% | 50.0% |
| Tokyo | 10 | 4 | 37.70 | -3.32 | -8.8% | 60.0% |
| Singapore | 1 | 1 | 3.20 | -3.20 | -100.0% | 0.0% |
| Seattle | 3 | 2 | 9.15 | -2.48 | -27.1% | 66.7% |
| Lucknow | 8 | 1 | 20.03 | 2.29 | 11.4% | 62.5% |
| Munich | 12 | 5 | 39.16 | 2.87 | 7.3% | 41.7% |
| London | 26 | 5 | 89.32 | 3.58 | 4.0% | 50.0% |

### 按方向

| side | fills | days | cost | pnl | ROI | win |
| --- | --- | --- | --- | --- | --- | --- |
| BUY_YES | 138 | 5 | 331.53 | -88.42 | -26.7% | 26.1% |
| BUY_NO | 209 | 5 | 726.82 | 4.07 | 0.6% | 62.7% |

### 按策略

| strategy_id | fills | days | cost | pnl | ROI | win |
| --- | --- | --- | --- | --- | --- | --- |
| live_weather_edge_v1_986d901ccc58 | 134 | 5 | 365.96 | -68.43 | -18.7% | 40.3% |
| live_weather_edge_v1_4ef9b3ec3e2e | 163 | 5 | 551.31 | -46.62 | -8.5% | 50.3% |
| live_weather_edge_v1_4b07f7abc42f | 28 | 4 | 90.22 | -7.85 | -8.7% | 57.1% |
| live_weather_edge_v1_c13ccf0c3181 | 2 | 1 | 6.38 | 5.43 | 85.2% | 100.0% |
| live_weather_edge_v1_91f019941593 | 20 | 4 | 44.48 | 33.12 | 74.4% | 65.0% |

## 重点城市处置表

| city | side | live pnl/ROI | recent pnl/ROI | opp cf | brier_delta | action |
| --- | --- | --- | --- | --- | --- | --- |
| Ankara | BUY_NO | -13.05 / -13.0% | -7.00 / -14.1% | -13.58 | -0.08 | 从 normal live 移出，进 T2/shadow |
| Ankara | BUY_YES | -5.63 / -100.0% | -4.18 / -100.0% | -12.84 | -0.03 | 从 normal live 移出，进 T2/shadow |
| Guangzhou | BUY_NO | -9.91 / -11.0% | -14.25 / -29.6% | -15.05 | -0.07 | 从 normal live 移出，进 T2/shadow |
| Istanbul | BUY_NO | -18.42 / -35.0% | -15.40 / -100.0% | -8.20 | -0.12 | 不要加仓；先 low-size/shadow 或 side-only 复核 |
| Istanbul | BUY_YES | 23.32 / 97.5% | -6.68 / -48.0% | 11.10 | 0.01 | 可观察，不作为整城永久删除 |
| Jeddah | BUY_NO | -8.53 / -15.0% | -13.59 / -47.6% | -29.55 | -0.07 | 从 normal live 移出，进 T2/shadow |
| Jeddah | BUY_YES | -0.06 / -100.0% | -0.06 / -100.0% | 4.65 | 0.03 | 从 normal live 移出，进 T2/shadow |
| Manila | BUY_NO | -9.45 / -26.7% | -6.09 / -32.9% | -7.20 | -0.04 | 从 normal live 移出，进 T2/shadow |
| Manila | BUY_YES | -16.89 / -100.0% | -6.89 / -100.0% | -10.35 | -0.07 | 从 normal live 移出，进 T2/shadow |
| NYC | BUY_NO | -14.53 / -11.2% | -5.98 / -12.9% | 32.46 | -0.03 | 按 city×side gate 复核 |
| NYC | BUY_YES | -49.35 / -71.3% | -36.00 / -78.1% | 0.96 | -0.01 | 已执行先 ban YES；保留 NO |

## 全机会 city×side 反事实最差项

| city | side | n | days | cf_pnl | win | brier_delta | live_cov |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Jeddah | BUY_NO | 6 | 5 | -12.25 | 50.0% | -0.08 | 100.0% |
| NYC | BUY_YES | 7 | 4 | -11.80 | 14.3% | -0.06 | 57.1% |
| Moscow | BUY_YES | 4 | 4 | -10.80 | 0.0% | -0.09 | 75.0% |
| Amsterdam | BUY_YES | 3 | 2 | -9.60 | 0.0% | -0.07 | 66.7% |
| Ankara | BUY_YES | 3 | 3 | -9.00 | 0.0% | -0.04 | 66.7% |
| Manila | BUY_NO | 6 | 4 | -8.85 | 50.0% | -0.06 | 100.0% |
| Ankara | BUY_NO | 9 | 5 | -8.80 | 55.6% | -0.13 | 88.9% |
| Guangzhou | BUY_NO | 7 | 5 | -7.90 | 57.1% | -0.06 | 100.0% |
| Istanbul | BUY_NO | 3 | 3 | -7.45 | 33.3% | -0.18 | 66.7% |
| BuenosAires | BUY_YES | 2 | 2 | -7.35 | 0.0% | -0.06 | 50.0% |
| Singapore | BUY_NO | 1 | 1 | -6.75 | 0.0% | -0.09 | 100.0% |
| Manila | BUY_YES | 2 | 2 | -5.60 | 0.0% | -0.08 | 100.0% |
| Miami | BUY_YES | 5 | 4 | -5.45 | 20.0% | -0.13 | 60.0% |
| Amsterdam | BUY_NO | 4 | 3 | -5.35 | 50.0% | -0.12 | 50.0% |
| Tokyo | BUY_NO | 4 | 4 | -5.30 | 50.0% | -0.15 | 100.0% |
| Miami | BUY_NO | 7 | 4 | -5.10 | 57.1% | -0.09 | 42.9% |
| Lucknow | BUY_YES | 1 | 1 | -3.85 | 0.0% | -0.07 | 0.0% |
| Karachi | BUY_NO | 10 | 5 | -3.60 | 60.0% | 0.00 | 90.0% |

## Promotion 前后

| cohort | promote | paper prior | live after | opp after |
| --- | --- | --- | --- | --- |
| new_t1_2026_05_26 | 2026-05-26 | 185 fills / 230.48 / 23.1% | 192 fills / -12.98 / -2.0% | 154 opp / -64.29 / brier -0.07 |
| new_t1_2026_05_27 | 2026-05-27 | 91 fills / 187.26 / 39.6% | 104 fills / -90.71 / -24.8% | 122 opp / -19.43 / brier -0.03 |

## 新城市选择体系

1. `ROI 高` 只能作为候选，不再作为晋升条件；晋升必须同时看 opportunity `counterfactual_pnl`、`brier_delta`、live 小仓样本外兑现。
2. 第一粒度是 `city×side`；城市整体正但某一侧 brier/cf/live 均负，就只禁该侧，不整城处理。
3. 新城市进 normal live 前先过 shadow/low-size 7-14 天；promotion 后看 `drop_best_day_pnl` 和 recent live，不再只看历史总 ROI。
4. `decision_window_missing` 高的城市先补数据，不给交易结论；paper_ordered 不是 live 意图，不能拿 paper fill 当 live 可成交性。
