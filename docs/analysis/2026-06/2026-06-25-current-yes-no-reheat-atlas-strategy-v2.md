# Current-YES No-Reheat Atlas Strategy v2

Status: research-only
Generated: 2026-06-24T18:06:40+00:00

## 一句话结论

Atlas regime features improve the physical model versus v1-style raw physics but still do not beat market enough for trading: holdout market+atlas AUC 0.728 vs market 0.749; `market_atlas edge>=2%` holdout ROI +4.1%, CI [-3.4%, +10.5%], forward ROI +9.0% on 147 rows.

## 数据范围

- Atlas rows: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`
- Atlas date range: `2026-05-19`..`2026-06-23`
- Tradable current-YES rows: 2786 / dates 36 / cities 36
- Train: `<= 2026-05-31`; holdout: `2026-06-01`..`2026-06-20`; forward/settled check: `2026-06-21`..`2026-06-23`
- DB fact refresh: `2026-06-24T18:03:01.617832+00:00`

Regime labels are PIT features, not hard gates. This report uses quote-ask opportunity rows from the atlas, not live fills.

Leakage guard: realized columns such as `remaining_heat_native`, `forecast_error_native`, final max, payoffs, and ROI are not model features. They are used only as labels/diagnostics after scoring.

## Model Metrics

| period | model | rows | break | pred break | AUC | Brier | logloss |
|---|---|---:|---:|---:|---:|---:|---:|
| train | market_implied_break | 935 | +28.6% | +25.2% | 0.780 | 0.165 | 0.497 |
| train | atlas_regime_physical | 935 | +28.6% | +28.6% | 0.807 | 0.153 | 0.479 |
| train | market_plus_atlas | 935 | +28.6% | +28.6% | 0.835 | 0.142 | 0.444 |
| holdout | market_implied_break | 1632 | +28.6% | +26.3% | 0.749 | 0.173 | 0.519 |
| holdout | atlas_regime_physical | 1632 | +28.6% | +29.8% | 0.658 | 0.205 | 0.631 |
| holdout | market_plus_atlas | 1632 | +28.6% | +29.9% | 0.728 | 0.184 | 0.560 |
| forward | market_implied_break | 219 | +27.9% | +25.0% | 0.757 | 0.176 | 0.512 |
| forward | atlas_regime_physical | 219 | +27.9% | +14.2% | 0.712 | 0.192 | 0.637 |
| forward | market_plus_atlas | 219 | +27.9% | +16.3% | 0.803 | 0.176 | 0.533 |

## EV Rules

| period | rule | rows | dates | win | avg ask | avg edge | ROI | CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| forward | atlas_edge_ge_0.02 | 148 | 3 | +73.0% | 0.696 | +21.4% | +4.8% | [-0.1%, +11.6%] |
| forward | atlas_edge_ge_0.02_ask_50_70 | 33 | 3 | +54.5% | 0.601 | +27.9% | -9.3% | [-12.0%, -6.6%] |
| forward | atlas_edge_ge_0.05 | 122 | 3 | +70.5% | 0.659 | +25.1% | +7.0% | [+0.2%, +11.5%] |
| forward | market_atlas_edge_ge_0.00 | 163 | 3 | +79.8% | 0.745 | +14.7% | +7.1% | [+3.8%, +10.2%] |
| forward | market_atlas_edge_ge_0.02 | 147 | 3 | +79.6% | 0.730 | +16.1% | +9.0% | [+6.6%, +11.8%] |
| forward | market_atlas_edge_ge_0.02_ask_50_70 | 28 | 3 | +60.7% | 0.602 | +24.0% | +0.8% | [-12.0%, +40.6%] |
| forward | market_atlas_edge_ge_0.05 | 119 | 3 | +75.6% | 0.689 | +19.1% | +9.8% | [+7.1%, +13.1%] |
| holdout | atlas_edge_ge_0.02 | 690 | 20 | +69.4% | 0.663 | +16.5% | +4.7% | [-3.2%, +12.1%] |
| holdout | atlas_edge_ge_0.02_ask_50_70 | 176 | 20 | +64.2% | 0.596 | +18.6% | +7.7% | [-5.9%, +22.4%] |
| holdout | atlas_edge_ge_0.05 | 567 | 20 | +67.0% | 0.635 | +19.3% | +5.6% | [-3.6%, +14.1%] |
| holdout | market_atlas_edge_ge_0.00 | 746 | 20 | +78.2% | 0.751 | +9.1% | +4.1% | [-2.4%, +9.6%] |
| holdout | market_atlas_edge_ge_0.02 | 607 | 20 | +76.1% | 0.731 | +10.9% | +4.1% | [-3.4%, +10.5%] |
| holdout | market_atlas_edge_ge_0.02_ask_50_70 | 117 | 20 | +66.7% | 0.604 | +15.3% | +10.4% | [-5.4%, +25.3%] |
| holdout | market_atlas_edge_ge_0.05 | 429 | 20 | +72.3% | 0.686 | +14.0% | +5.4% | [-4.8%, +14.5%] |
| train | atlas_edge_ge_0.02 | 346 | 13 | +73.1% | 0.654 | +14.3% | +11.7% | [+5.5%, +17.9%] |
| train | atlas_edge_ge_0.02_ask_50_70 | 108 | 12 | +67.6% | 0.586 | +16.1% | +15.4% | [+3.0%, +27.9%] |
| train | atlas_edge_ge_0.05 | 286 | 13 | +71.3% | 0.629 | +16.6% | +13.4% | [+5.3%, +21.7%] |
| train | market_atlas_edge_ge_0.00 | 389 | 13 | +86.6% | 0.779 | +7.0% | +11.2% | [+6.7%, +15.3%] |
| train | market_atlas_edge_ge_0.02 | 292 | 13 | +84.6% | 0.750 | +8.9% | +12.7% | [+7.3%, +17.6%] |
| train | market_atlas_edge_ge_0.02_ask_50_70 | 51 | 12 | +76.5% | 0.583 | +14.7% | +31.1% | [+7.8%, +52.1%] |
| train | market_atlas_edge_ge_0.05 | 190 | 13 | +85.3% | 0.713 | +11.9% | +19.6% | [+12.0%, +27.2%] |

## Regime Diagnostics

| period | kind | regime | rows | break | avg ask | ROI all | p_atlas | p_market_atlas |
|---|---|---|---:|---:|---:|---:|---:|---:|
| forward | day_regime | day_space_unknown | 99 | +35.4% | 0.745 | -13.2% | +25.6% | +26.5% |
| forward | day_regime | day_open_runway | 39 | +15.4% | 0.704 | +20.3% | +6.0% | +11.1% |
| forward | day_regime | day_marginal_runway | 30 | +26.7% | 0.764 | -4.0% | +5.3% | +7.9% |
| forward | day_regime | day_forecast_busted | 27 | +22.2% | 0.769 | +1.2% | +2.4% | +4.5% |
| forward | day_regime | day_forecast_capped | 24 | +25.0% | 0.808 | -7.1% | +5.1% | +6.1% |
| forward | intraday_state | active_warming | 89 | +38.2% | 0.714 | -13.5% | +17.0% | +19.9% |
| forward | intraday_state | fresh_high | 49 | +26.5% | 0.752 | -2.4% | +11.6% | +12.5% |
| forward | moisture_cloud_regime | mixed_moisture | 99 | +22.2% | 0.743 | +4.6% | +12.4% | +14.2% |
| forward | moisture_cloud_regime | dry_heat_inertia | 49 | +46.9% | 0.752 | -29.4% | +18.2% | +20.4% |
| forward | moisture_cloud_regime | humid_convective_risk | 30 | +23.3% | 0.746 | +2.7% | +15.6% | +18.5% |
| forward | moisture_cloud_regime | humid_overcast_suppression | 22 | +0.0% | 0.803 | +24.5% | +5.1% | +7.1% |
| forward | running_max_state | fresh_running_high | 118 | +32.2% | 0.740 | -8.3% | +15.2% | +17.0% |
| forward | running_max_state | running_max_clock_unknown | 42 | +28.6% | 0.757 | -5.6% | +16.3% | +17.9% |
| forward | running_max_state | mature_fade | 28 | +10.7% | 0.799 | +11.8% | +8.5% | +10.0% |
| holdout | day_regime | day_forecast_capped | 556 | +23.4% | 0.746 | +2.8% | +28.6% | +28.0% |
| holdout | day_regime | day_forecast_busted | 544 | +22.8% | 0.768 | +0.5% | +24.1% | +24.7% |
| holdout | day_regime | day_marginal_runway | 335 | +40.9% | 0.700 | -15.6% | +40.7% | +40.3% |
| holdout | day_regime | day_open_runway | 197 | +38.1% | 0.689 | -10.1% | +30.5% | +32.4% |
| holdout | intraday_state | active_warming | 570 | +33.3% | 0.702 | -5.0% | +35.0% | +34.3% |
| holdout | intraday_state | fresh_high | 425 | +26.8% | 0.738 | -0.8% | +23.0% | +23.6% |

## Verdict

significance=FAIL / baseline=FAIL / forward=FAIL / conclusion=inconclusive

Regime atlas is useful as a shared feature layer and improves interpretability, but the current current-YES no-reheat expression is not live-ready. Market pricing remains the stronger baseline, and EV-selected rows do not pass holdout/forward gates. Keep it as shadow/research telemetry and use the scored rows to diagnose which regimes market under/overprices.

## Outputs

- scored rows: `docs/analysis/2026-06/generated/current_yes_no_reheat_atlas_strategy_v2/atlas_strategy_v2_scored_rows.csv`
- model metrics: `docs/analysis/2026-06/generated/current_yes_no_reheat_atlas_strategy_v2/atlas_strategy_v2_model_metrics.csv`
- EV rules: `docs/analysis/2026-06/generated/current_yes_no_reheat_atlas_strategy_v2/atlas_strategy_v2_ev_rules.csv`
- regime summary: `docs/analysis/2026-06/generated/current_yes_no_reheat_atlas_strategy_v2/atlas_strategy_v2_regime_summary.csv`
- json: `docs/analysis/2026-06/2026-06-25-current-yes-no-reheat-atlas-strategy-v2.json`
