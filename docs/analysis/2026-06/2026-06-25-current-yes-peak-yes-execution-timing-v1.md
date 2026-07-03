# Current-YES Peak-YES Execution Timing v1

Status: research-only
Generated: 2026-07-01T02:13:41+00:00

## 一句话结论

Execution timing helps but does not solve peak YES: first market+components edge>=0 holdout has 412 rows ROI +0.7%, CI [-4.5%, +6.6%], forward ROI -3.2%. The broader first low-risk q30 rule has holdout ROI +0.2%. Waiting inside the same edge>=0 event raises ask by 0.208 on average and changes ROI from +26.8% to -3.8%.

## 数据范围

- scored rows: `docs/analysis/2026-06/generated/current_yes_peak_yes_mechanism_v4/peak_yes_mechanism_v4_scored_rows.csv`
- rows: 2997 / dates 39 / cities 36

This is opportunity replay at quote ask, not live fill PnL. Each first-signal rule buys the first eligible row per city/date/current bracket.

## First Signal Rules

| period | rule | rows | dates | win | avg ask | avg hour | ROI | CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| forward | first_edge_components_ge_2 | 93 | 6 | +49.5% | 0.545 | 13.1 | -9.3% | [-27.5%, +9.3%] |
| forward | first_edge_market_components_ge_0 | 99 | 6 | +63.6% | 0.657 | 12.9 | -3.2% | [-15.5%, +6.9%] |
| forward | first_edge_market_components_ge_2 | 85 | 6 | +61.2% | 0.635 | 12.8 | -3.6% | [-14.9%, +7.6%] |
| forward | first_low_market_components_q30 | 74 | 6 | +91.9% | 0.889 | 13.9 | +3.3% | [-3.8%, +10.0%] |
| holdout | first_edge_components_ge_2 | 361 | 20 | +60.4% | 0.587 | 13.4 | +2.8% | [-5.2%, +10.1%] |
| holdout | first_edge_market_components_ge_0 | 412 | 20 | +70.4% | 0.699 | 13.5 | +0.7% | [-4.5%, +6.6%] |
| holdout | first_edge_market_components_ge_2 | 354 | 20 | +69.2% | 0.677 | 13.4 | +2.3% | [-3.3%, +8.2%] |
| holdout | first_low_market_components_q30 | 335 | 20 | +89.3% | 0.891 | 14.5 | +0.2% | [-3.4%, +3.9%] |
| train | first_edge_components_ge_2 | 217 | 13 | +64.5% | 0.603 | 13.7 | +7.0% | [-0.3%, +14.8%] |
| train | first_edge_market_components_ge_0 | 242 | 13 | +74.8% | 0.710 | 13.7 | +5.3% | [+0.9%, +10.3%] |
| train | first_edge_market_components_ge_2 | 203 | 13 | +74.9% | 0.684 | 13.5 | +9.4% | [+4.9%, +14.5%] |
| train | first_low_market_components_q30 | 176 | 13 | +93.8% | 0.893 | 14.4 | +4.9% | [+1.4%, +8.5%] |

## Hour Bands

| period | rule | rows | dates | win | avg ask | avg hour | ROI | CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| holdout | all_hours | 1632 | 20 | +71.4% | 0.737 | 13.9 | -3.0% | [-8.4%, +2.2%] |
| holdout | hour_10-12 | 395 | 20 | +65.1% | 0.617 | 11.4 | +5.4% | [-4.4%, +15.6%] |
| holdout | hour_13-15 | 904 | 20 | +71.6% | 0.750 | 14.0 | -4.6% | [-9.5%, +0.8%] |
| holdout | hour_16-18 | 318 | 20 | +79.6% | 0.842 | 16.5 | -5.5% | [-13.6%, +2.0%] |
| holdout | hour_19-21 | 15 | 8 | +60.0% | 0.846 | 19.8 | -29.1% | [-73.3%, +6.2%] |
| forward | all_hours | 430 | 6 | +67.2% | 0.736 | 13.9 | -8.7% | [-16.7%, -0.1%] |
| forward | hour_10-12 | 107 | 6 | +59.8% | 0.635 | 11.4 | -5.9% | [-21.6%, +13.1%] |
| forward | hour_13-15 | 235 | 6 | +68.5% | 0.752 | 14.0 | -8.9% | [-16.4%, -1.9%] |
| forward | hour_16-18 | 85 | 6 | +71.8% | 0.811 | 16.5 | -11.5% | [-24.4%, -2.2%] |
| forward | hour_19-21 | 3 | 1 | +100.0% | 0.942 | 20.0 | +6.2% | [NA, NA] |

## Wait Cost

| period | rule | events | first hour | last hour | ask first | ask last | wait ask change | ROI first | ROI last |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| train | edge_market_components_ge_0 | 120 | 13.1 | 14.9 | 0.668 | 0.856 | 0.188 | +28.5% | +0.3% |
| holdout | edge_market_components_ge_0 | 197 | 12.9 | 15.0 | 0.653 | 0.860 | 0.208 | +26.8% | -3.8% |
| forward | edge_market_components_ge_0 | 50 | 12.3 | 14.3 | 0.594 | 0.807 | 0.212 | +17.8% | -13.2% |
| train | edge_components_ge_2 | 93 | 13.0 | 14.7 | 0.582 | 0.775 | 0.193 | +36.8% | +2.6% |
| holdout | edge_components_ge_2 | 164 | 12.8 | 14.7 | 0.569 | 0.749 | 0.180 | +28.6% | -2.3% |
| forward | edge_components_ge_2 | 37 | 12.2 | 14.2 | 0.536 | 0.694 | 0.158 | +11.0% | -14.3% |
| train | low_components_pbreak_le_25 | 130 | 13.6 | 15.4 | 0.731 | 0.898 | 0.167 | +21.0% | -1.5% |
| holdout | low_components_pbreak_le_25 | 225 | 13.4 | 15.3 | 0.702 | 0.883 | 0.181 | +20.4% | -4.3% |
| forward | low_components_pbreak_le_25 | 48 | 12.8 | 15.0 | 0.653 | 0.852 | 0.198 | +5.2% | -19.3% |
| train | low_market_components_pbreak_le_25 | 139 | 13.6 | 15.4 | 0.808 | 0.917 | 0.109 | +13.0% | -0.4% |
| holdout | low_market_components_pbreak_le_25 | 231 | 13.5 | 15.2 | 0.780 | 0.900 | 0.120 | +12.2% | -2.8% |
| forward | low_market_components_pbreak_le_25 | 52 | 13.2 | 15.1 | 0.782 | 0.907 | 0.125 | +10.6% | -4.6% |

## Verdict

significance=FAIL / baseline=FAIL / forward=FAIL / conclusion=inconclusive

Earlier first-signal entry is economically better than repeated/waited confirmation in some rows, but the edge is still not statistically live-ready. This supports forward maker-first/timing telemetry rather than immediate taker live: log first signal, quote drift, maker fill chance, and whether later confirmation merely buys a more expensive version of the same payoff.

## Outputs

- hour_band_summary: `docs/analysis/2026-06/generated/current_yes_peak_yes_execution_timing_v1/peak_yes_timing_v1_hour_band_summary.csv`
- first_signal_rules: `docs/analysis/2026-06/generated/current_yes_peak_yes_execution_timing_v1/peak_yes_timing_v1_first_signal_rules.csv`
- wait_cost_summary: `docs/analysis/2026-06/generated/current_yes_peak_yes_execution_timing_v1/peak_yes_timing_v1_wait_cost_summary.csv`
- event_rows: `docs/analysis/2026-06/generated/current_yes_peak_yes_execution_timing_v1/peak_yes_timing_v1_event_rows.csv`
- json: `docs/analysis/2026-06/2026-06-25-current-yes-peak-yes-execution-timing-v1.json`
- markdown: `docs/analysis/2026-06/2026-06-25-current-yes-peak-yes-execution-timing-v1.md`
