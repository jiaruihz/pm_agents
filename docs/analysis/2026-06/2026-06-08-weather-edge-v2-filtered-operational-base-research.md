# Weather Edge V2 Filtered Operational-Base Research - 2026-06-08

## 数据快照

- 数据源: `runtime/weather.db` (`fact_trades`, `fact_signal_candidates`)
- generated_at_utc: `2026-06-07T17:12:25+00:00`
- DB mtime BJ: `2026-06-08T01:03:02+08:00`
- MAX fact_built_at_utc: `2026-06-07T17:02:43.560681+00:00`
- CLOB gate: `gate_pass=True`; db/cache diff `0/0`; missing_order_rows `0`; over_order_keys `0`; db_fill_cost_minus_fact_cost `0.0`
- live_real fill_id reconciliation: DB `1320`, raw CLOB `1320`
- fact_trades rows: `5334`; settled `5033`; unsettled/null `301` (`5.6%`); missing_bracket `0`

## 目标指标与分母

- target metric: `weather_edge_v2_filtered_operational_base` = 在新 operational base 上比较 raw/blend/side-band/basket 规则的 settled opportunity PnL、ROI、top5-removed ROI、missed/avoided attribution、walk-forward 稳定性。
- 分母: `fact_signal_candidates` 中 `settlement_status='settled'`、`decision_window_missing=0`、模型/市场/entry/final 字段齐全的机会行。
- 本报告中的策略 PnL 是 offline opportunity / shadow-notional PnL；不是钱包 cashflow，也不是直接用来解释账户余额的 live_real realized PnL。
- operational base: exclude `Ankara, BuenosAires, Jeddah, Karachi, Moscow, Munich`; `decision_hours_to_settle <= 28`。
- filtered rows: `1884` / `2212`; date range `2026-05-06 -> 2026-06-06`。

## 数据完整性自检

```json
{
  "fact_built_at": [
    {
      "max_fact_built_at_utc": "2026-06-07T17:02:43.560681+00:00"
    }
  ],
  "trade_class_distribution": [
    {
      "trade_class": "live_real",
      "rows": 1320
    },
    {
      "trade_class": "live_simulated",
      "rows": 1093
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
      "rows": 301
    },
    {
      "settlement_status": "settled",
      "rows": 5033
    }
  ],
  "signal_candidate_coverage": [
    {
      "rows": 23487,
      "eligible": 7642,
      "paper_ordered": 2835,
      "live_filled": 513
    }
  ],
  "clob_order_fill_status": [
    {
      "status": "error",
      "orders": 151,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 1546,
      "with_fill": 1320
    }
  ],
  "fact_rows": [
    {
      "fact_rows": 5334,
      "settled_rows": 5033,
      "missing_bracket_rows": 0
    }
  ],
  "db_mtime_bj": "2026-06-08T01:03:02+08:00"
}
```

## Current Live Actual Reference

这段只看 `fact_trades.trade_class='live_real'` 的已结算真实成交，并 join `fact_signal_candidates` 做 operational-base 过滤。它回答“现在符合 live 条件的真实成交实际表现”，不等同于 basket 反事实。

| scope | fills | date range | cost | pnl | ROI | top5 ROI | win_rate |
|---|---:|---|---:|---:|---:|---:|---:|
| overall | 828 | 2026-05-16 -> 2026-06-06 | $+2211.24 | $+61.83 | +2.80% | -0.63% | 51.3% |
| recent_from_2026_06_01 | 351 | 2026-06-01 -> 2026-06-06 | $+892.99 | $-77.38 | -8.67% | -15.59% | 42.7% |

| instance | fills | date range | cost | pnl | ROI | top5 ROI | win_rate |
|---|---:|---|---:|---:|---:|---:|---:|
| live_weather_edge_v1_4b07f7abc42f | 49 | 2026-06-01 -> 2026-06-06 | $+142.11 | $-15.73 | -11.07% | -28.06% | 49.0% |
| live_weather_edge_v1_4ef9b3ec3e2e | 533 | 2026-05-16 -> 2026-06-06 | $+1446.02 | $+144.70 | +10.01% | +5.53% | 56.7% |
| live_weather_edge_v1_91f019941593 | 43 | 2026-06-01 -> 2026-06-06 | $+84.15 | $-7.77 | -9.23% | -63.03% | 25.6% |
| live_weather_edge_v1_986d901ccc58 | 117 | 2026-05-29 -> 2026-06-06 | $+290.66 | $-76.28 | -26.24% | -38.20% | 35.9% |
| live_weather_edge_v1_c13ccf0c3181 | 45 | 2026-05-24 -> 2026-05-28 | $+119.77 | $-13.29 | -11.09% | -33.75% | 51.1% |
| live_weather_edge_v1_edb2b6f8df82 | 41 | 2026-05-27 -> 2026-05-30 | $+128.52 | $+30.19 | +23.49% | -9.47% | 56.1% |

## Basket Research Directions

新增方向都只做 shadow/offline 比较，baseline 固定为 current-like `legacy_side_band_raw`。`entry` 表示同 side-band 入场价 universe；`live_trigger` 表示只在 legacy raw 已触发 rows 上重组 basket。

| slice | rule | n | pnl | ROI | top5 ROI | missed | avoided | gates |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| full | legacy_side_band_raw | 393 | $+197.27 | +10.04% | +5.61% | $+0.00 | $+0.00 | 0/4 |
| full | combo_market_risk_entry | 162 | $+173.42 | +22.06% | +4.19% | $+761.76 | $+705.00 | 2/4 |
| full | combo_market_tail_entry | 53 | $+31.47 | +14.70% | -19.79% | $+1008.42 | $+845.00 | 1/4 |
| full | robust_single_best_entry | 44 | $+17.95 | +10.14% | -38.61% | $+1012.33 | $+840.00 | 2/4 |
| full | tail_balanced_combo_entry | 59 | $+34.44 | +12.90% | -25.13% | $+975.49 | $+820.00 | 2/4 |
| full | no_hit_guard_combo_entry | 56 | $+31.56 | +12.73% | -28.46% | $+985.29 | $+825.00 | 1/4 |
| full | compact_diversified_combo_entry | 48 | $+27.78 | +13.62% | -27.76% | $+998.18 | $+835.00 | 2/4 |
| full | live_trigger_tail_combo | 24 | $+127.09 | +67.96% | +5.35% | $+923.79 | $+810.00 | 2/4 |
| full | live_trigger_no_hit_guard_combo | 23 | $+119.09 | +66.53% | -0.06% | $+928.79 | $+810.00 | 1/4 |
| holdout_from_2026_05_26 | legacy_side_band_raw | 143 | $+85.82 | +12.00% | -0.38% | $+0.00 | $+0.00 | 0/4 |
| holdout_from_2026_05_26 | combo_market_risk_entry | 63 | $+147.95 | +43.64% | +2.07% | $+240.26 | $+275.00 | 4/4 |
| holdout_from_2026_05_26 | combo_market_tail_entry | 18 | $+7.35 | +9.93% | -91.68% | $+409.19 | $+340.00 | 1/4 |
| holdout_from_2026_05_26 | robust_single_best_entry | 16 | $-9.72 | -15.43% | -99.28% | $+421.73 | $+335.00 | 1/4 |
| holdout_from_2026_05_26 | tail_balanced_combo_entry | 20 | $-8.65 | -9.61% | -96.30% | $+409.19 | $+330.00 | 1/4 |
| holdout_from_2026_05_26 | no_hit_guard_combo_entry | 20 | $-8.65 | -9.61% | -96.30% | $+409.19 | $+330.00 | 1/4 |
| holdout_from_2026_05_26 | compact_diversified_combo_entry | 16 | $-9.72 | -15.43% | -99.28% | $+421.73 | $+335.00 | 1/4 |
| holdout_from_2026_05_26 | live_trigger_tail_combo | 9 | $+76.77 | +106.63% | -100.00% | $+362.84 | $+325.00 | 2/4 |
| holdout_from_2026_05_26 | live_trigger_no_hit_guard_combo | 9 | $+76.77 | +106.63% | -100.00% | $+362.84 | $+325.00 | 2/4 |
| recent_from_2026_06_01 | legacy_side_band_raw | 48 | $+14.87 | +6.20% | -22.39% | $+0.00 | $+0.00 | 0/4 |
| recent_from_2026_06_01 | combo_market_risk_entry | 25 | $+4.07 | +3.01% | -82.92% | $+78.81 | $+75.00 | 1/4 |
| recent_from_2026_06_01 | combo_market_tail_entry | 4 | $-3.78 | -31.51% | +0.00% | $+129.87 | $+115.00 | 2/4 |
| recent_from_2026_06_01 | robust_single_best_entry | 4 | $-3.78 | -31.51% | +0.00% | $+129.87 | $+115.00 | 2/4 |
| recent_from_2026_06_01 | tail_balanced_combo_entry | 4 | $-3.78 | -31.51% | +0.00% | $+129.87 | $+115.00 | 2/4 |
| recent_from_2026_06_01 | no_hit_guard_combo_entry | 4 | $-3.78 | -31.51% | +0.00% | $+129.87 | $+115.00 | 2/4 |
| recent_from_2026_06_01 | compact_diversified_combo_entry | 4 | $-3.78 | -31.51% | +0.00% | $+129.87 | $+115.00 | 2/4 |
| recent_from_2026_06_01 | live_trigger_tail_combo | 2 | $+22.10 | +138.10% | +0.00% | $+111.06 | $+110.00 | 3/4 |
| recent_from_2026_06_01 | live_trigger_no_hit_guard_combo | 2 | $+22.10 | +138.10% | +0.00% | $+111.06 | $+110.00 | 3/4 |
| live_filled_only | legacy_side_band_raw | 106 | $+81.47 | +15.37% | +0.31% | $+0.00 | $+0.00 | 0/4 |
| live_filled_only | combo_market_risk_entry | 43 | $+131.83 | +64.62% | +2.22% | $+204.99 | $+195.00 | 3/4 |
| live_filled_only | combo_market_tail_entry | 14 | $+27.89 | +66.40% | -23.65% | $+309.98 | $+235.00 | 2/4 |
| live_filled_only | robust_single_best_entry | 17 | $+3.89 | +5.89% | -65.22% | $+309.98 | $+220.00 | 1/4 |
| live_filled_only | tail_balanced_combo_entry | 17 | $+3.89 | +5.89% | -65.22% | $+309.98 | $+220.00 | 1/4 |
| live_filled_only | no_hit_guard_combo_entry | 17 | $+3.89 | +5.89% | -65.22% | $+309.98 | $+220.00 | 1/4 |
| live_filled_only | compact_diversified_combo_entry | 17 | $+3.89 | +5.89% | -65.22% | $+309.98 | $+220.00 | 1/4 |
| live_filled_only | live_trigger_tail_combo | 5 | $+9.99 | +28.55% | +0.00% | $+291.17 | $+220.00 | 2/4 |
| live_filled_only | live_trigger_no_hit_guard_combo | 5 | $+9.99 | +28.55% | +0.00% | $+291.17 | $+220.00 | 2/4 |

### 方向定义

- `combo_market_risk_entry`: 既有 market-normalized risk objective，作为 combo 旧方向对照。
- `combo_market_tail_entry`: 要求移除单个最佳温度结果后仍有正 EV，直接压制 top outcome 依赖。
- `robust_single_best_entry`: 每 city-day 最多一腿，用 market EV + tail floor 做保守单腿化。
- `tail_balanced_combo_entry`: 组合枚举，但要求 leave-best-out EV > 0、CVaR20 不吞掉过多成本。
- `no_hit_guard_combo_entry`: 限制 BUY_NO 数量，并禁止对 market top bracket 买 NO，针对 “买 NO 命中被买 bracket” 的坏场景。
- `compact_diversified_combo_entry`: 每 city-day 最多两腿，限制 YES/NO 结构，减少多腿尾部集中。
- `live_trigger_tail_combo` / `live_trigger_no_hit_guard_combo`: 不扩大触发机会，只重组当前 side-band raw 已会碰到的 rows。

## Baseline / Basket 对比

| slice | profile | rule | n | pnl | ROI | top5 ROI | missed | avoided |
|---|---|---|---:|---:|---:|---:|---:|---:|
| full | legacy_25_75_e10 | legacy_raw_independent | 781 | $+178.05 | +4.56% | +2.69% | $+0.00 | $+0.00 |
| full | legacy_25_75_e10 | legacy_blended_independent | 86 | $+90.89 | +21.14% | +4.84% | $+1482.16 | $+1395.00 |
| full | legacy_25_75_e10 | basket_pr2b_same_entry_band | 224 | $+46.38 | +3.98% | -5.50% | $+1134.70 | $+995.00 |
| full | legacy_25_75_e10 | combo_market_risk_same_entry_band | 141 | $+88.15 | +12.45% | -3.54% | $+1291.32 | $+1175.00 |
| full | legacy_25_75_e10 | combo_market_risk_legacy_raw_triggers | 133 | $+144.66 | +23.18% | +5.49% | $+1276.19 | $+1190.00 |
| full | legacy_side_band | legacy_raw_independent | 393 | $+197.27 | +10.04% | +5.61% | $+0.00 | $+0.00 |
| full | legacy_side_band | legacy_blended_independent | 65 | $+10.39 | +3.20% | -7.45% | $+906.88 | $+720.00 |
| full | legacy_side_band | basket_pr2b_same_entry_band | 245 | $+165.21 | +13.14% | +2.14% | $+568.91 | $+505.00 |
| full | legacy_side_band | combo_market_risk_same_entry_band | 162 | $+173.42 | +22.06% | +4.19% | $+761.76 | $+705.00 |
| full | legacy_side_band | combo_market_risk_legacy_raw_triggers | 49 | $+172.31 | +44.52% | +10.03% | $+810.52 | $+725.00 |
| pre_2026_06_01 | legacy_25_75_e10 | legacy_raw_independent | 694 | $+208.38 | +6.01% | +3.91% | $+0.00 | $+0.00 |
| pre_2026_06_01 | legacy_25_75_e10 | legacy_blended_independent | 77 | $+92.40 | +24.00% | +5.95% | $+1335.98 | $+1220.00 |
| pre_2026_06_01 | legacy_25_75_e10 | basket_pr2b_same_entry_band | 188 | $+111.62 | +11.35% | +0.28% | $+1021.83 | $+900.00 |
| pre_2026_06_01 | legacy_25_75_e10 | combo_market_risk_same_entry_band | 120 | $+132.40 | +22.07% | +3.58% | $+1154.59 | $+1045.00 |
| pre_2026_06_01 | legacy_25_75_e10 | combo_market_risk_legacy_raw_triggers | 114 | $+173.40 | +32.59% | +12.23% | $+1145.70 | $+1060.00 |
| pre_2026_06_01 | legacy_side_band | legacy_raw_independent | 345 | $+182.40 | +10.57% | +5.93% | $+0.00 | $+0.00 |
| pre_2026_06_01 | legacy_side_band | legacy_blended_independent | 60 | $+17.44 | +5.81% | -5.56% | $+784.96 | $+620.00 |
| pre_2026_06_01 | legacy_side_band | basket_pr2b_same_entry_band | 205 | $+173.02 | +16.43% | +4.31% | $+520.13 | $+465.00 |
| pre_2026_06_01 | legacy_side_band | combo_market_risk_same_entry_band | 137 | $+169.36 | +26.01% | +6.21% | $+682.94 | $+630.00 |
| pre_2026_06_01 | legacy_side_band | combo_market_risk_legacy_raw_triggers | 39 | $+184.71 | +60.17% | +20.52% | $+712.90 | $+650.00 |
| post_2026_06_01 | legacy_25_75_e10 | legacy_raw_independent | 87 | $-30.33 | -6.97% | -19.22% | $+0.00 | $+0.00 |
| post_2026_06_01 | legacy_25_75_e10 | legacy_blended_independent | 9 | $-1.50 | -3.34% | -100.00% | $+146.18 | $+175.00 |
| post_2026_06_01 | legacy_25_75_e10 | basket_pr2b_same_entry_band | 36 | $-65.24 | -36.04% | -68.21% | $+112.86 | $+95.00 |
| post_2026_06_01 | legacy_25_75_e10 | combo_market_risk_same_entry_band | 21 | $-44.25 | -40.97% | -100.87% | $+136.73 | $+130.00 |
| post_2026_06_01 | legacy_25_75_e10 | combo_market_risk_legacy_raw_triggers | 19 | $-28.74 | -31.24% | -98.84% | $+130.49 | $+130.00 |
| post_2026_06_01 | legacy_side_band | legacy_raw_independent | 48 | $+14.87 | +6.20% | -22.39% | $+0.00 | $+0.00 |
| post_2026_06_01 | legacy_side_band | legacy_blended_independent | 5 | $-7.05 | -28.22% | +0.00% | $+121.92 | $+100.00 |
| post_2026_06_01 | legacy_side_band | basket_pr2b_same_entry_band | 40 | $-7.80 | -3.82% | -57.98% | $+48.77 | $+40.00 |
| post_2026_06_01 | legacy_side_band | combo_market_risk_same_entry_band | 25 | $+4.07 | +3.01% | -82.92% | $+78.81 | $+75.00 |
| post_2026_06_01 | legacy_side_band | combo_market_risk_legacy_raw_triggers | 10 | $-12.40 | -15.51% | -100.00% | $+97.62 | $+75.00 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | legacy_raw_independent | 272 | $-54.33 | -4.00% | -9.47% | $+0.00 | $+0.00 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | legacy_blended_independent | 34 | $+43.30 | +25.47% | -15.30% | $+447.36 | $+545.00 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | basket_pr2b_same_entry_band | 86 | $-45.04 | -9.69% | -34.05% | $+370.57 | $+360.00 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | combo_market_risk_same_entry_band | 50 | $+9.57 | +3.61% | -41.30% | $+423.30 | $+460.00 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | combo_market_risk_legacy_raw_triggers | 47 | $+37.46 | +15.87% | -33.19% | $+417.46 | $+465.00 |
| holdout_from_2026_05_26 | legacy_side_band | legacy_raw_independent | 143 | $+85.82 | +12.00% | -0.38% | $+0.00 | $+0.00 |
| holdout_from_2026_05_26 | legacy_side_band | legacy_blended_independent | 21 | $-19.11 | -18.20% | -56.55% | $+389.93 | $+285.00 |
| holdout_from_2026_05_26 | legacy_side_band | basket_pr2b_same_entry_band | 102 | $+139.97 | +25.13% | +0.39% | $+152.00 | $+180.00 |
| holdout_from_2026_05_26 | legacy_side_band | combo_market_risk_same_entry_band | 63 | $+147.95 | +43.64% | +2.07% | $+240.26 | $+275.00 |
| holdout_from_2026_05_26 | legacy_side_band | combo_market_risk_legacy_raw_triggers | 25 | $+138.79 | +69.40% | +2.51% | $+274.07 | $+275.00 |
| recent_from_2026_06_01 | legacy_25_75_e10 | legacy_raw_independent | 87 | $-30.33 | -6.97% | -19.22% | $+0.00 | $+0.00 |
| recent_from_2026_06_01 | legacy_25_75_e10 | legacy_blended_independent | 9 | $-1.50 | -3.34% | -100.00% | $+146.18 | $+175.00 |
| recent_from_2026_06_01 | legacy_25_75_e10 | basket_pr2b_same_entry_band | 36 | $-65.24 | -36.04% | -68.21% | $+112.86 | $+95.00 |
| recent_from_2026_06_01 | legacy_25_75_e10 | combo_market_risk_same_entry_band | 21 | $-44.25 | -40.97% | -100.87% | $+136.73 | $+130.00 |
| recent_from_2026_06_01 | legacy_25_75_e10 | combo_market_risk_legacy_raw_triggers | 19 | $-28.74 | -31.24% | -98.84% | $+130.49 | $+130.00 |
| recent_from_2026_06_01 | legacy_side_band | legacy_raw_independent | 48 | $+14.87 | +6.20% | -22.39% | $+0.00 | $+0.00 |
| recent_from_2026_06_01 | legacy_side_band | legacy_blended_independent | 5 | $-7.05 | -28.22% | +0.00% | $+121.92 | $+100.00 |
| recent_from_2026_06_01 | legacy_side_band | basket_pr2b_same_entry_band | 40 | $-7.80 | -3.82% | -57.98% | $+48.77 | $+40.00 |
| recent_from_2026_06_01 | legacy_side_band | combo_market_risk_same_entry_band | 25 | $+4.07 | +3.01% | -82.92% | $+78.81 | $+75.00 |
| recent_from_2026_06_01 | legacy_side_band | combo_market_risk_legacy_raw_triggers | 10 | $-12.40 | -15.51% | -100.00% | $+97.62 | $+75.00 |
| live_filled_only | legacy_25_75_e10 | legacy_raw_independent | 206 | $+84.33 | +8.19% | +1.47% | $+0.00 | $+0.00 |
| live_filled_only | legacy_25_75_e10 | legacy_blended_independent | 32 | $+8.57 | +5.35% | -32.61% | $+410.77 | $+335.00 |
| live_filled_only | legacy_25_75_e10 | basket_pr2b_same_entry_band | 70 | $+72.69 | +20.46% | -8.33% | $+258.33 | $+250.00 |
| live_filled_only | legacy_25_75_e10 | combo_market_risk_same_entry_band | 39 | $+94.82 | +52.10% | -7.58% | $+317.11 | $+320.00 |
| live_filled_only | legacy_25_75_e10 | combo_market_risk_legacy_raw_triggers | 39 | $+94.82 | +52.10% | -7.58% | $+317.11 | $+320.00 |
| live_filled_only | legacy_side_band | legacy_raw_independent | 106 | $+81.47 | +15.37% | +0.31% | $+0.00 | $+0.00 |
| live_filled_only | legacy_side_band | legacy_blended_independent | 24 | $-21.71 | -18.09% | -52.64% | $+268.18 | $+165.00 |
| live_filled_only | legacy_side_band | basket_pr2b_same_entry_band | 73 | $+86.61 | +23.98% | -10.44% | $+152.90 | $+105.00 |
| live_filled_only | legacy_side_band | combo_market_risk_same_entry_band | 43 | $+131.83 | +64.62% | +2.22% | $+204.99 | $+195.00 |
| live_filled_only | legacy_side_band | combo_market_risk_legacy_raw_triggers | 15 | $+77.77 | +67.63% | -52.22% | $+223.80 | $+195.00 |

## Optimizer Slice

| slice | rule | n | pnl | ROI | top5 ROI | gates |
|---|---|---:|---:|---:|---:|---:|
| full | raw_single | 1719 | $+443.60 | +5.16% | -7.29% | 0/4 |
| full | blended_single | 1213 | $+721.09 | +11.89% | -4.78% | 0/4 |
| full | heuristic_pr2b | 841 | $+1035.92 | +25.03% | +1.46% | 4/4 |
| full | combo_market_risk | 345 | $+812.71 | +49.56% | -13.82% | 3/4 |
| full | combo_market_tail | 66 | $+35.38 | +10.62% | -35.79% | 2/4 |
| pre_2026_06_01 | raw_single | 1586 | $+467.81 | +5.90% | -7.60% | 0/4 |
| pre_2026_06_01 | blended_single | 1113 | $+738.11 | +13.26% | -4.91% | 0/4 |
| pre_2026_06_01 | heuristic_pr2b | 787 | $+1080.57 | +28.02% | +2.73% | 4/4 |
| pre_2026_06_01 | combo_market_risk | 315 | $+831.84 | +56.21% | -14.02% | 3/4 |
| pre_2026_06_01 | combo_market_tail | 61 | $+42.16 | +13.26% | -35.40% | 2/4 |
| post_2026_06_01 | raw_single | 133 | $-24.21 | -3.64% | -14.87% | 0/4 |
| post_2026_06_01 | blended_single | 100 | $-17.03 | -3.41% | -18.52% | 0/4 |
| post_2026_06_01 | heuristic_pr2b | 54 | $-44.65 | -15.83% | -54.11% | 0/4 |
| post_2026_06_01 | combo_market_risk | 30 | $-19.13 | -11.96% | -83.80% | 0/4 |
| post_2026_06_01 | combo_market_tail | 5 | $-6.78 | -45.21% | +0.00% | 3/4 |
| holdout_from_2026_05_26 | raw_single | 650 | $-297.93 | -9.17% | -20.54% | 0/4 |
| holdout_from_2026_05_26 | blended_single | 476 | $-206.70 | -8.69% | -24.26% | 0/4 |
| holdout_from_2026_05_26 | heuristic_pr2b | 357 | $+40.85 | +2.33% | -23.30% | 2/4 |
| holdout_from_2026_05_26 | combo_market_risk | 135 | $+175.19 | +25.95% | -43.75% | 2/4 |
| holdout_from_2026_05_26 | combo_market_tail | 16 | $+2.85 | +3.91% | -62.78% | 3/4 |
| recent_from_2026_06_01 | raw_single | 133 | $-24.21 | -3.64% | -14.87% | 0/4 |
| recent_from_2026_06_01 | blended_single | 100 | $-17.03 | -3.41% | -18.52% | 0/4 |
| recent_from_2026_06_01 | heuristic_pr2b | 54 | $-44.65 | -15.83% | -54.11% | 0/4 |
| recent_from_2026_06_01 | combo_market_risk | 30 | $-19.13 | -11.96% | -83.80% | 0/4 |
| recent_from_2026_06_01 | combo_market_tail | 5 | $-6.78 | -45.21% | +0.00% | 3/4 |
| live_filled_only | raw_single | 290 | $+84.86 | +5.85% | -0.01% | 0/4 |
| live_filled_only | blended_single | 217 | $+93.69 | +8.63% | +0.82% | 0/4 |
| live_filled_only | heuristic_pr2b | 94 | $+86.90 | +17.94% | -7.59% | 1/4 |
| live_filled_only | combo_market_risk | 51 | $+126.13 | +49.85% | -0.75% | 3/4 |
| live_filled_only | combo_market_tail | 5 | $+10.06 | +67.08% | +0.00% | 3/4 |

## Distribution Sanity

| slice | best logloss | market_norm logloss | blend_norm logloss | raw_norm logloss |
|---|---|---:|---:|---:|
| full | blend_norm | 0.8183 | 0.8183 | 1.2931 |
| pre_2026_06_01 | blend_norm | 0.8465 | 0.8444 | 1.3348 |
| post_2026_06_01 | market_norm | 0.5507 | 0.5713 | 0.8976 |
| holdout_from_2026_05_26 | market_norm | 0.7650 | 0.7905 | 1.4865 |
| recent_from_2026_06_01 | market_norm | 0.5507 | 0.5713 | 0.8976 |
| live_filled_only | market_norm | 0.5092 | 0.5123 | 0.9698 |

## Walk-Forward

| policy | folds | n | pnl | ROI | weighted top5 ROI | positive folds |
|---|---:|---:|---:|---:|---:|---:|
| selected_by_train_score | 5 | 349 | $+132.70 | +7.78% | -40.02% | 60.0% |
| always_blended_single | 5 | 651 | $-33.16 | -1.02% | -22.01% | 60.0% |
| always_heuristic_pr2b | 5 | 408 | $+95.50 | +4.76% | -36.14% | 60.0% |
| always_combo_risk | 5 | 197 | $+125.78 | +12.63% | -70.63% | 60.0% |
| always_combo_market_risk | 5 | 167 | $+267.87 | +32.23% | -67.26% | 60.0% |
| always_combo_market_tail | 5 | 22 | $-9.24 | -9.62% | -92.88% | 20.0% |

## 结论

- 交易动作: `shadow`, 不 canary。当前 operational-base live_real settled recent ROI `-8.67%`、top5 ROI `-15.59%`；这说明要看 current-live 分母，但也不能把它当作 basket 已验证。
- filtered base 的 offline opportunity 结果里，`legacy_side_band` post-2026-06-01 ROI `+6.20%`，`combo_market_risk_same_entry_band` post ROI `+3.01%`，walk-forward selected weighted top5 ROI `-40.02%`，旧 combo 仍不能直接替代。
- 新增 basket 方向里，`live_filled_only` 上 headline ROI 最高的是 `combo_market_tail_entry` = `+66.40%`，但仍要看 holdout/recent top5 ROI、missed/avoided 和 forward settled，不允许只按 live_filled headline 选 canary。
- v2 方向: raw/blend 只能先作为候选过滤或确认信号；city-day objective 继续 market-normalized，并且必须带 live-trigger constrained、leave-best-out、CVaR20、NO-hit guard、missed-vs-avoided 约束。
- shadow lineage 下一步: 对每个 city-day 输出 rule_id、operational-base pass/fail、distribution source、selected/rejected legs、EV、CVaR20、leave-best-out EV、worst-case payoff、missed/avoided attribution。
- canary gate 维持原条件: CLOB gate 和 fill_id reconciliation 通过、filtered basket 在 holdout/recent  beats side_band、top5-removed ROI 不劣化、walk-forward positive fold >=60%、再加一周 forward settled shadow 证据。
