# Current-YES Peak-YES Execution Timing v1

Status: research-only
Generated: 2026-06-25T13:40:53+00:00

## 一句话结论

Execution timing helps but does not solve peak YES: first market+components edge>=0 holdout has 419 rows ROI +1.8%, CI [-3.7%, +7.5%], forward ROI +3.1%. The broader first low-risk q30 rule has holdout ROI +0.5%. Waiting inside the same edge>=0 event raises ask by 0.210 on average and changes ROI from +26.8% to -4.5%.

## 数据范围

- scored rows: `docs/analysis/2026-06/generated/current_yes_peak_yes_mechanism_v4/peak_yes_mechanism_v4_scored_rows.csv`
- rows: 2786 / dates 36 / cities 36

This is opportunity replay at quote ask, not live fill PnL. Each first-signal rule buys the first eligible row per city/date/current bracket.

## First Signal Rules

| period | rule | rows | dates | win | avg ask | avg hour | ROI | CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| forward | first_edge_components_ge_2 | 47 | 3 | +61.7% | 0.560 | 13.0 | +10.3% | [-3.9%, +23.8%] |
| forward | first_edge_market_components_ge_0 | 54 | 3 | +70.4% | 0.682 | 13.0 | +3.1% | [-8.8%, +10.1%] |
| forward | first_edge_market_components_ge_2 | 40 | 3 | +67.5% | 0.643 | 12.7 | +5.0% | [-10.2%, +14.1%] |
| forward | first_low_market_components_q30 | 40 | 3 | +97.5% | 0.889 | 13.8 | +9.7% | [+2.5%, +14.0%] |
| holdout | first_edge_components_ge_2 | 362 | 20 | +60.8% | 0.584 | 13.4 | +4.0% | [-3.7%, +11.2%] |
| holdout | first_edge_market_components_ge_0 | 419 | 20 | +70.9% | 0.696 | 13.5 | +1.8% | [-3.7%, +7.5%] |
| holdout | first_edge_market_components_ge_2 | 359 | 20 | +69.1% | 0.671 | 13.4 | +3.0% | [-3.6%, +9.5%] |
| holdout | first_low_market_components_q30 | 333 | 20 | +89.8% | 0.893 | 14.5 | +0.5% | [-3.0%, +4.0%] |
| train | first_edge_components_ge_2 | 215 | 13 | +64.2% | 0.599 | 13.7 | +7.2% | [+0.7%, +14.8%] |
| train | first_edge_market_components_ge_0 | 238 | 13 | +75.2% | 0.712 | 13.7 | +5.6% | [+1.2%, +10.8%] |
| train | first_edge_market_components_ge_2 | 195 | 13 | +75.9% | 0.685 | 13.6 | +10.8% | [+7.1%, +15.1%] |
| train | first_low_market_components_q30 | 180 | 13 | +93.3% | 0.896 | 14.5 | +4.1% | [+0.4%, +7.7%] |

## Hour Bands

| period | rule | rows | dates | win | avg ask | avg hour | ROI | CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| holdout | all_hours | 1632 | 20 | +71.4% | 0.737 | 13.9 | -3.0% | [-8.4%, +2.2%] |
| holdout | hour_10-12 | 395 | 20 | +65.1% | 0.617 | 11.4 | +5.4% | [-4.4%, +15.6%] |
| holdout | hour_13-15 | 904 | 20 | +71.6% | 0.750 | 14.0 | -4.6% | [-9.5%, +0.8%] |
| holdout | hour_16-18 | 318 | 20 | +79.6% | 0.842 | 16.5 | -5.5% | [-13.6%, +2.0%] |
| holdout | hour_19-21 | 15 | 8 | +60.0% | 0.846 | 19.8 | -29.1% | [-73.3%, +6.2%] |
| forward | all_hours | 219 | 3 | +72.1% | 0.750 | 14.0 | -3.8% | [-16.3%, +8.7%] |
| forward | hour_10-12 | 53 | 3 | +66.0% | 0.641 | 11.4 | +3.0% | [-18.7%, +35.3%] |
| forward | hour_13-15 | 121 | 3 | +74.4% | 0.764 | 14.1 | -2.6% | [-10.8%, +4.3%] |
| forward | hour_16-18 | 42 | 3 | +71.4% | 0.833 | 16.6 | -14.2% | [-35.3%, +2.2%] |
| forward | hour_19-21 | 3 | 1 | +100.0% | 0.942 | 20.0 | +6.2% | [NA, NA] |

## Wait Cost

| period | rule | events | first hour | last hour | ask first | ask last | wait ask change | ROI first | ROI last |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| train | edge_market_components_ge_0 | 117 | 13.1 | 14.9 | 0.664 | 0.854 | 0.190 | +30.0% | +1.1% |
| holdout | edge_market_components_ge_0 | 203 | 12.9 | 14.9 | 0.641 | 0.851 | 0.210 | +26.8% | -4.5% |
| forward | edge_market_components_ge_0 | 27 | 12.4 | 14.4 | 0.594 | 0.825 | 0.231 | +30.9% | -5.7% |
| train | edge_components_ge_2 | 88 | 13.0 | 14.6 | 0.560 | 0.754 | 0.194 | +36.0% | +0.9% |
| holdout | edge_components_ge_2 | 165 | 12.8 | 14.6 | 0.562 | 0.741 | 0.179 | +29.4% | -1.9% |
| forward | edge_components_ge_2 | 23 | 12.5 | 14.4 | 0.523 | 0.710 | 0.187 | +41.3% | +4.2% |
| train | low_components_pbreak_le_25 | 137 | 13.6 | 15.4 | 0.723 | 0.892 | 0.169 | +23.2% | -0.2% |
| holdout | low_components_pbreak_le_25 | 226 | 13.4 | 15.3 | 0.688 | 0.878 | 0.189 | +20.2% | -5.7% |
| forward | low_components_pbreak_le_25 | 26 | 13.0 | 15.3 | 0.676 | 0.871 | 0.195 | +19.5% | -7.3% |
| train | low_market_components_pbreak_le_25 | 138 | 13.6 | 15.4 | 0.809 | 0.917 | 0.107 | +13.7% | +0.4% |
| holdout | low_market_components_pbreak_le_25 | 232 | 13.4 | 15.2 | 0.779 | 0.899 | 0.120 | +12.3% | -2.7% |
| forward | low_market_components_pbreak_le_25 | 31 | 13.4 | 15.4 | 0.787 | 0.906 | 0.119 | +14.7% | -0.3% |

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
