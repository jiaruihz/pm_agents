# Current-YES fade reheat features v1

Created: 2026-06-18T16:20:54+00:00

## Target

Research-only.  The denominator is current-YES rows where the market has already seen a running max and the observed temperature has faded.  The label is whether that current bracket eventually held (`label_yes_wins=1`) or later reheated and broke (`label_yes_wins=0`).

## Data self-check

- `fact_trades` max built at: `2026-06-17T17:09:13.232107+00:00`
- `fact_signal_candidates`: `{'rows': 31499, 'eligible': 10961, 'paper_ordered': 4274, 'live_filled': 348}`
- CLOB order/fill join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`

## Sample funnel

| slice | rows | city_days | dates | win_rate | reheat_rate | remaining_vs_all |
|---|---:|---:|---:|---:|---:|---:|
| all_current_yes | 3239 | 831 | 27 | 67.5% | 32.5% | 100.0% |
| all_current_yes_train | 1527 | 392 | 13 | 66.1% | 33.9% | 47.1% |
| all_current_yes_holdout | 1712 | 439 | 14 | 68.8% | 31.2% | 52.9% |
| fade_decline_ge_0_5c | 1038 | 560 | 26 | 90.4% | 9.6% | 32.0% |
| fade_plus_d1_visible | 857 | 494 | 26 | 88.3% | 11.7% | 26.5% |
| fade_plus_d1_train | 396 | 229 | 13 | 86.6% | 13.4% | 12.2% |
| fade_plus_d1_holdout | 461 | 265 | 13 | 89.8% | 10.2% | 14.2% |
| fade_plus_d1_with_reheat_features | 731 | 417 | 26 | 86.6% | 13.4% | 22.6% |
| fade_live_like_h13_15_ask_ge_0_55 | 445 | 292 | 25 | 91.2% | 8.8% | 13.7% |

Plain English: all current-YES replay rows are 3239.  Requiring an actual fade (`decline_c >= 0.5`) leaves 1038, so the sample shrinks by 68.0%.  Requiring the higher NO expression to be visible leaves 857, so the trainable fade sample shrinks by 73.5% versus all current-YES rows.  Train/holdout are 396/461.

The important subtlety is the negative class.  `fade_plus_d1_visible` still has 857 rows, but only about 100 later reheat failures.  Holdout has 461 rows and about 47 failures.  That is enough for feature research and guard design, but thin for a standalone high-confidence live probability model.

## Feature slices

| feature | bucket | rows | win_rate | reheat_rate | avg_yes_ask |
|---|---:|---:|---:|---:|---:|
| forecast_peak_delta_gfs | peak_future>1h | 72 | 50.0% | 50.0% | 0.621 |
| forecast_peak_delta_gfs | peak_future0-1h | 73 | 72.6% | 27.4% | 0.766 |
| forecast_peak_delta_gfs | peak_now_or_past0-1h | 120 | 82.5% | 17.5% | 0.840 |
| forecast_peak_delta_gfs | peak_past1-2h | 154 | 94.8% | 5.2% | 0.914 |
| forecast_peak_delta_gfs | peak_past2h+ | 312 | 95.8% | 4.2% | 0.948 |
| temp_trend_1h_f | rise>2F | 31 | 61.3% | 38.7% | 0.686 |
| temp_trend_1h_f | rise0.5-2F | 74 | 70.3% | 29.7% | 0.799 |
| temp_trend_1h_f | flat | 263 | 90.5% | 9.5% | 0.883 |
| temp_trend_1h_f | fall0.5-2F | 308 | 89.6% | 10.4% | 0.890 |
| minutes_since_running_max | <15m | 73 | 64.4% | 35.6% | 0.707 |
| minutes_since_running_max | 60m+ | 646 | 89.0% | 11.0% | 0.890 |
| forecast_gap_to_running_gfs | 2+ | 73 | 69.9% | 30.1% | 0.678 |
| forecast_gap_to_running_gfs | below<-1 | 137 | 91.2% | 8.8% | 0.912 |

## Holdout model check

| model | rows | brier | log_loss | auc | selected p80 rows | selected p80 win_rate | selected p80 roi |
|---|---:|---:|---:|---:|---:|---:|---:|
| existing_base_probability | 407 | 0.0670 | 0.2415 | 0.851418439716312 | 39 | 92.3% | +13.3% |
| weather_forecast_only_no_city_price | 407 | 0.1132 | 0.3615 | 0.7910165484633569 | 43 | 86.0% | +5.9% |
| base_plus_weather_forecast | 407 | 0.0905 | 0.2972 | 0.8492316784869975 | 44 | 90.9% | +10.5% |
| city_market_plus_weather_forecast | 407 | 0.0922 | 0.2981 | 0.8573286052009457 | 42 | 90.5% | +11.0% |

## Readout

- This is enough data to research the direction, but not enough to trust a high-confidence specialist live model by itself.  The live-like high-ask fade subset is only hundreds of rows, not thousands.
- Weather/forecast-only features are meaningful if they improve Brier/AUC versus a dumb base rate, but the current artifact should be treated as reheat-risk evidence, not as a direct replacement for the base current-YES model.
- The next useful version should train the base current-YES probability and the reheat-risk correction jointly, with live decision logs carrying both numbers.

## Artifacts

- `sample_funnel.csv`
- `feature_bins.csv`
- `model_metrics.csv`
- `live_like_rule_summary.csv`
- `holdout_scored.csv`
