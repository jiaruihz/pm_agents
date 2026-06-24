# Current-YES Future-Break Hazard V3.1

Status: research-only
Generated: 2026-06-23T14:48:08+00:00

Target metric: `future_break_hazard_v31` keeps the V3 survive/break label but models only residual edge after market-ask calibration.

## 数据快照

- 数据源: `docs/analysis/2026-06/generated/current_yes_future_break_hazard_v3/factory/reheat_feature_rows.csv` + `runtime/weather.db` self-check.
- Feature target-date range: `2026-05-19`..`2026-06-20`.
- Row grain: one peak current-YES state = city + target_date + orderbook snapshot + current running-max bracket.
- Records: source rows 109415, current-YES rows 8907, peak rows 5361.
- Residual model: market logit calibrator + weather/plateau Ridge residual; alpha=100.0 (min_inner_valid_brier).
- DB fact_trades self-check: `{'rows': 4400, 'min_target_date': '2026-05-06', 'max_target_date': '2026-06-11', 'missing_bracket_rows': 0, 'unsettled_rows': 150}`.

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

| model | rows | actual survive | mean p | AUC survive | Brier | Logloss | accuracy | balanced acc | avg edge |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| market_price_as_probability | 3306 | +41.7% | +43.9% | 0.947 | 0.091 | 0.291 | +87.0% | +87.0% | +0.0% |
| market_logit_calibrated | 3306 | +41.7% | +42.0% | 0.947 | 0.091 | 0.289 | +86.9% | +86.5% | -1.9% |
| weather_only_direct_v3_shape | 3306 | +41.7% | +42.8% | 0.907 | 0.122 | 0.388 | +82.5% | +82.0% | -1.1% |
| market_plus_weather_direct_v3_shape | 3306 | +41.7% | +42.6% | 0.946 | 0.091 | 0.295 | +87.1% | +86.9% | -1.3% |
| v31_market_cal_plus_weather_residual | 3306 | +41.7% | +42.4% | 0.948 | 0.089 | 0.304 | +87.4% | +87.1% | -1.5% |

## Trading Backtest

| rule | orders | dates | cities | avg ask | win | ROI | CI | forward rows | forward ROI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| market_tradable_all | 1116 | 20 | 36 | 0.745 | +71.0% | -4.8% | [-10.3%, +0.5%] | 138 | +5.6% |
| v3_live_like_edge_ge_02 | 318 | 20 | 36 | 0.810 | +81.1% | +0.2% | [-7.5%, +7.9%] | 40 | +4.6% |
| v31_resid_edge_ge_02 | 205 | 20 | 34 | 0.855 | +83.9% | -1.9% | [-10.9%, +6.1%] | 27 | +8.3% |
| v31_resid_edge_ge_05 | 62 | 20 | 21 | 0.842 | +90.3% | +7.3% | [-3.9%, +15.1%] | 5 | +21.7% |
| v31_live_like_edge_ge_02 | 141 | 20 | 31 | 0.894 | +87.9% | -1.6% | [-9.8%, +5.7%] | 17 | +4.0% |
| v31_strict_stalled_edge_ge_02 | 71 | 19 | 22 | 0.895 | +85.9% | -4.1% | [-15.1%, +5.9%] | 12 | +0.3% |
| v31_late_no_warming_edge_ge_02 | 78 | 18 | 25 | 0.905 | +91.0% | +0.6% | [-6.9%, +7.1%] | 11 | -0.9% |
| v31_peak_not_ahead_edge_ge_02 | 69 | 19 | 24 | 0.871 | +82.6% | -5.1% | [-17.7%, +7.6%] | 9 | +12.4% |
| v31_strict_combo_edge_ge_02 | 18 | 13 | 10 | 0.870 | +83.3% | -4.2% | [-23.9%, +14.4%] | 4 | +6.7% |
| train_selected_grid_h12_p0.60_edge0.06_stalled0_nowarm0 | 41 | 19 | 15 | 0.832 | +90.2% | +8.4% | [-2.4%, +16.4%] | 4 | +25.8% |
| train_selected_grid_h12_p0.60_edge0.04_stalled0_nowarm0 | 102 | 20 | 29 | 0.852 | +84.3% | -1.0% | [-11.9%, +8.2%] | 11 | +10.3% |
| train_selected_grid_h12_p0.55_edge0.02_stalled1_nowarm1 | 65 | 19 | 24 | 0.886 | +87.7% | -1.1% | [-14.2%, +10.1%] | 9 | -3.3% |
| train_selected_grid_h12_p0.60_edge0.02_stalled1_nowarm1 | 65 | 19 | 24 | 0.886 | +87.7% | -1.1% | [-14.2%, +10.1%] | 9 | -3.3% |

## Verdict

significance=FAIL / baseline=FAIL / forward=PASS / conclusion=inconclusive

在 2026-06-01..2026-06-20 holdout，V3.1 residual live-like edge>=0.02 规则 ROI 为 -1.6%，95% 日期 bootstrap CI [-9.8%, +5.7%]；forward-tail 自 2026-06-18 起 17 rows，ROI +4.0%。未同时通过显著性、基准、前瞻三门，不能上 live。

## 8-Ring Coverage

- Covered: descriptive slices, date bootstrap, signal discrimination, probability calibration, market-price baseline, target-date block correlation.
- Not covered enough for live: real forward shadow fills, maker/taker execution, capacity beyond top ask size, post-2026-06-20 complete settled feature rows.

## Outputs

- scored rows: `docs/analysis/2026-06/generated/current_yes_future_break_hazard_v31/future_break_hazard_v31_scored_rows.csv`
- model metrics: `docs/analysis/2026-06/generated/current_yes_future_break_hazard_v31/future_break_hazard_v31_model_metrics.csv`
- rule comparison: `docs/analysis/2026-06/generated/current_yes_future_break_hazard_v31/future_break_hazard_v31_rule_comparison.csv`
- json: `docs/analysis/2026-06/2026-06-23-current-yes-future-break-hazard-v31.json`
