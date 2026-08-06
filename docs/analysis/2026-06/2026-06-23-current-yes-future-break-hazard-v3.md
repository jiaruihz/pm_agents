# Current-YES Future-Break Hazard V3

Status: `historical / superseded_for_decision_use`
Generated: 2026-06-22T16:17:27+00:00

> ⚠️ Historical result only. The positive three-date forward-tail ROI was too
> thin and the main rule CI crossed zero. This version also predates the
> canonical fixed-model, native-lattice and corrected physical-clock audits.
> Later wider reruns did not preserve a hazard residual over market/frozen
> core. Preserve the scored rows for lineage, but do not cite this as evidence
> that a future-break V4 is ready.

Target metric: `future_break_hazard_v3` predicts whether the current running-max YES bracket survives, and reports the equivalent future-break probability as `1 - p_survive`.

## 数据快照

- 数据源: `docs/analysis/2026-06/generated/current_yes_future_break_hazard_v3/factory/reheat_feature_rows.csv` + `runtime/weather.db` self-check.
- Feature layer generated at UTC: `2026-06-22T16:10:44+00:00`.
- Feature target-date range: `2026-05-19`..`2026-06-20`.
- Row grain: one peak current-YES state = city + target_date + orderbook snapshot + current running-max bracket.
- Records: source rows 109415, current-YES rows 8907, peak rows 5361.
- Unsettled/missing bracket: model feature rows use `settlement_outcomes`; DB fact_trades self-check `{'rows': 4400, 'min_target_date': '2026-05-06', 'max_target_date': '2026-06-11', 'missing_bracket_rows': 0, 'unsettled_rows': 150}`.

## Funnel

| step | rows | dates | cities |
|---|---:|---:|---:|
| source feature rows | 109415 | 33 | 36 |
| current YES rows | 8907 | 33 | 36 |
| peak-forming rows | 5361 | 33 | 36 |
| train peak rows | 2055 | 13 | 36 |
| holdout peak rows | 3306 | 20 | 36 |
| forward-tail rows | 428 | 3 | 35 |

## Model Accuracy

| model | rows | actual survive | mean p | AUC survive | AUC break | Brier | Logloss | accuracy | balanced acc | avg edge |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| market_price_as_probability | 3306 | +41.7% | +43.9% | 0.947 | 0.947 | 0.091 | 0.291 | +87.0% | +87.0% | +0.0% |
| weather_only_future_break_v3 | 3306 | +41.7% | +42.8% | 0.907 | 0.907 | 0.122 | 0.388 | +82.5% | +82.0% | -1.1% |
| base_current_yes_v9 | 3306 | +41.7% | +41.2% | 0.937 | 0.937 | 0.096 | 0.318 | +87.1% | +86.5% | -2.6% |
| market_plus_weather_future_break_v3 | 3306 | +41.7% | +42.6% | 0.946 | 0.946 | 0.091 | 0.295 | +87.1% | +86.9% | -1.3% |

## Trading Backtest

| rule | orders | dates | cities | avg ask | win | ROI | CI | forward rows | forward ROI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| market_tradable_all | 1116 | 20 | 36 | 0.745 | +71.0% | -4.8% | [-10.3%, +0.5%] | 138 | +5.6% |
| v3_edge_ge_02 | 456 | 20 | 36 | 0.774 | +77.2% | -0.3% | [-7.3%, +6.6%] | 61 | +12.0% |
| v3_edge_ge_05 | 309 | 20 | 35 | 0.747 | +77.0% | +3.2% | [-6.4%, +12.2%] | 46 | +18.1% |
| v3_live_like_edge_ge_02 | 318 | 20 | 36 | 0.810 | +81.1% | +0.2% | [-7.5%, +7.9%] | 40 | +4.6% |
| v3_stalled_edge_ge_02 | 158 | 20 | 33 | 0.806 | +79.7% | -1.0% | [-11.9%, +9.1%] | 19 | +6.2% |
| v3_downtrend_edge_ge_02 | 176 | 20 | 32 | 0.824 | +85.8% | +4.2% | [-3.8%, +11.7%] | 22 | +14.4% |
| weather_only_edge_ge_02 | 207 | 20 | 35 | 0.782 | +81.6% | +4.4% | [-2.5%, +11.1%] | 30 | +9.5% |
| base_v9_edge_ge_02 | 271 | 20 | 29 | 0.800 | +80.4% | +0.5% | [-9.5%, +9.6%] | 33 | +8.4% |
| train_selected_grid_h14_p0.75_edge0.10_stalled1 | 48 | 20 | 22 | 0.725 | +79.2% | +9.2% | [-8.4%, +23.5%] | 7 | -12.3% |
| train_selected_grid_h13_p0.75_edge0.10_stalled1 | 56 | 20 | 25 | 0.729 | +76.8% | +5.3% | [-11.0%, +19.2%] | 9 | -1.9% |
| train_selected_grid_h13_p0.50_edge0.10_stalled1 | 63 | 20 | 25 | 0.703 | +74.6% | +6.1% | [-12.9%, +22.0%] | 10 | +3.6% |
| train_selected_grid_h13_p0.55_edge0.10_stalled1 | 61 | 20 | 25 | 0.713 | +75.4% | +5.8% | [-13.2%, +22.3%] | 10 | +3.6% |

## Verdict

significance=FAIL / baseline=PASS / forward=PASS / conclusion=inconclusive

在 2026-06-01..2026-06-20 holdout，V3 live-like edge>=0.02 规则 ROI 为 +0.2%，95% 日期 bootstrap CI [-7.5%, +7.9%]；forward-tail 自 2026-06-18 起 40 rows，ROI +4.6%。未同时通过显著性、基准、前瞻三门，不能上 live。

## 8-Ring Coverage

- Covered: descriptive slices, date bootstrap, signal discrimination, probability calibration, time-aligned orderbook pricing, target-date block correlation, market-price baseline.
- Not covered enough for live: real forward shadow fills, maker/taker execution, capacity beyond top ask size, post-2026-06-20 settled feature rows.

## Outputs

- scored rows: `docs/analysis/2026-06/generated/current_yes_future_break_hazard_v3/future_break_hazard_v3_scored_rows.csv`
- model metrics: `docs/analysis/2026-06/generated/current_yes_future_break_hazard_v3/future_break_hazard_v3_model_metrics.csv`
- rule comparison: `docs/analysis/2026-06/generated/current_yes_future_break_hazard_v3/future_break_hazard_v3_rule_comparison.csv`
- json: `docs/analysis/2026-06/2026-06-23-current-yes-future-break-hazard-v3.json`
