# Regime-Routed NO Calibrated Score V2

## Conclusion

A simple L2 logistic `P(NO wins)` layer does not beat the current fixed-quality heuristic on the frozen forward slice. It is useful as shadow telemetry, not a live replacement.

Verdict: `shadow_only_do_not_replace_live_score`.

## Coverage

- Frozen/live-like replay: `2026-05-20`..`2026-06-28`, rows `165`.
- Historical best-ask diagnostic: `2026-05-20`..`2026-06-26`, rows `283`.
- Raw frozen rows before label/ask filtering: `165` rows through `2026-06-28`; dropped rows `0`.
- Missing replay payoff labels are backfilled from canonical `settlement_outcomes` when the routed bracket has a settled pm_history row.
- Data refresh: N100 sync completed; local fact rebuild completed but frontend restart exited non-cleanly because port 5174 stayed occupied; CLOB fill coverage gate `gate_pass=True`.
- Train/forward split: train `< 2026-06-21`, forward `>= 2026-06-21`.
- Evidence is frozen/live-like candidate replay, not live_real PnL.

## Feature Availability

- `city_source_bias`, hot-underforecast/cold-overforecast rates, `city_source_bias_regime`, route, ask, peak clock, freshness, trend, wind, humidity, cloud/moisture regime are present on the frozen rows.
- `city_source_bias_mae` and `city_source_bias_p90` are all-missing on the current replay denominator, so they are dropped at training time and recorded in `model_quality.csv`.

## Frozen Forward Model Quality

| scope | model | rows | dates | label_rate | prob_mean | auc | brier | log_loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| expanding_walk_forward | logit_market_route | 88 | 21 | +50.0% | +46.3% | +50.0% | 0.256 | 0.707 |
| expanding_walk_forward | logit_mechanism_plus_heuristic_score | 88 | 21 | +50.0% | +47.0% | +55.4% | 0.263 | 0.732 |
| expanding_walk_forward | logit_mechanism_weather | 88 | 21 | +50.0% | +46.3% | +54.0% | 0.268 | 0.740 |
| expanding_walk_forward | logit_mechanism_weather_citybias | 88 | 21 | +50.0% | +47.0% | +56.0% | 0.262 | 0.732 |
| forward_2026-06-21_plus | logit_market_route | 16 | 8 | +43.8% | +47.9% | +57.1% | 0.235 | 0.662 |
| forward_2026-06-21_plus | logit_mechanism_plus_heuristic_score | 16 | 8 | +43.8% | +47.2% | +63.5% | 0.230 | 0.648 |
| forward_2026-06-21_plus | logit_mechanism_weather | 16 | 8 | +43.8% | +44.2% | +68.3% | 0.226 | 0.643 |
| forward_2026-06-21_plus | logit_mechanism_weather_citybias | 16 | 8 | +43.8% | +47.1% | +65.1% | 0.229 | 0.646 |

## Frozen Forward Trading Expressions

| scope | model | trade_variant | trades | dates | cities | win_rate | avg_ask | avg_cost | roi | roi_ci_low | roi_ci_high | daily_negative_100pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| expanding_walk_forward | logit_market_route | current_heuristic_score_over_ask_ge_1_size_score | 25 | 17 | 16 | +52.0% | 0.306 | +0.469 | +93.1% | +18.8% | +176.7% | 5 |
| expanding_walk_forward | logit_market_route | model_p_ge_ask_plus_05_size_probability | 20 | 14 | 17 | +45.0% | 0.247 | +0.439 | +99.1% | +11.8% | +187.3% | 6 |
| expanding_walk_forward | logit_market_route | model_p_ge_ask_size_probability | 23 | 16 | 19 | +52.2% | 0.268 | +0.440 | +105.2% | +31.4% | +179.9% | 6 |
| expanding_walk_forward | logit_mechanism_plus_heuristic_score | current_heuristic_score_over_ask_ge_1_size_score | 25 | 17 | 16 | +52.0% | 0.306 | +0.469 | +93.1% | +18.8% | +176.7% | 5 |
| expanding_walk_forward | logit_mechanism_plus_heuristic_score | model_p_ge_ask_plus_05_size_probability | 15 | 14 | 11 | +46.7% | 0.280 | +0.572 | +91.9% | -13.2% | +197.9% | 7 |
| expanding_walk_forward | logit_mechanism_plus_heuristic_score | model_p_ge_ask_size_probability | 20 | 15 | 15 | +50.0% | 0.277 | +0.505 | +98.6% | +8.6% | +188.1% | 6 |
| expanding_walk_forward | logit_mechanism_weather | current_heuristic_score_over_ask_ge_1_size_score | 25 | 17 | 16 | +52.0% | 0.306 | +0.469 | +93.1% | +18.8% | +176.7% | 5 |
| expanding_walk_forward | logit_mechanism_weather | model_p_ge_ask_plus_05_size_probability | 19 | 14 | 14 | +47.4% | 0.276 | +0.499 | +87.9% | -7.9% | +173.4% | 7 |
| expanding_walk_forward | logit_mechanism_weather | model_p_ge_ask_size_probability | 22 | 15 | 16 | +45.5% | 0.284 | +0.480 | +79.1% | -10.4% | +160.8% | 7 |
| expanding_walk_forward | logit_mechanism_weather_citybias | current_heuristic_score_over_ask_ge_1_size_score | 25 | 17 | 16 | +52.0% | 0.306 | +0.469 | +93.1% | +18.8% | +176.7% | 5 |
| expanding_walk_forward | logit_mechanism_weather_citybias | model_p_ge_ask_plus_05_size_probability | 18 | 14 | 13 | +50.0% | 0.284 | +0.538 | +100.7% | +11.0% | +186.3% | 6 |
| expanding_walk_forward | logit_mechanism_weather_citybias | model_p_ge_ask_size_probability | 21 | 16 | 16 | +52.4% | 0.287 | +0.507 | +102.3% | +21.6% | +179.2% | 6 |
| forward_2026-06-21_plus | logit_market_route | current_heuristic_score_over_ask_ge_1_size_score | 5 | 4 | 5 | +60.0% | 0.342 | +0.432 | +69.5% | -45.0% | +203.6% | 1 |
| forward_2026-06-21_plus | logit_market_route | model_p_ge_ask_plus_05_size_probability | 5 | 5 | 5 | +40.0% | 0.284 | +0.459 | +42.4% | -100.0% | +229.1% | 3 |
| forward_2026-06-21_plus | logit_market_route | model_p_ge_ask_size_probability | 6 | 6 | 6 | +50.0% | 0.332 | +0.479 | +48.9% | -64.5% | +181.6% | 3 |
| forward_2026-06-21_plus | logit_mechanism_plus_heuristic_score | current_heuristic_score_over_ask_ge_1_size_score | 5 | 4 | 5 | +60.0% | 0.342 | +0.432 | +69.5% | -45.0% | +203.6% | 1 |
| forward_2026-06-21_plus | logit_mechanism_plus_heuristic_score | model_p_ge_ask_plus_05_size_probability | 4 | 4 | 4 | +25.0% | 0.253 | +0.535 | +39.5% | -100.0% | +264.8% | 3 |
| forward_2026-06-21_plus | logit_mechanism_plus_heuristic_score | model_p_ge_ask_size_probability | 5 | 5 | 5 | +40.0% | 0.284 | +0.514 | +56.9% | -100.0% | +247.0% | 3 |
| forward_2026-06-21_plus | logit_mechanism_weather | current_heuristic_score_over_ask_ge_1_size_score | 5 | 4 | 5 | +60.0% | 0.342 | +0.432 | +69.5% | -45.0% | +203.6% | 1 |
| forward_2026-06-21_plus | logit_mechanism_weather | model_p_ge_ask_plus_05_size_probability | 4 | 4 | 4 | +25.0% | 0.253 | +0.476 | +53.8% | -100.0% | +278.8% | 3 |
| forward_2026-06-21_plus | logit_mechanism_weather | model_p_ge_ask_size_probability | 4 | 4 | 4 | +25.0% | 0.253 | +0.476 | +53.8% | -100.0% | +278.8% | 3 |
| forward_2026-06-21_plus | logit_mechanism_weather_citybias | current_heuristic_score_over_ask_ge_1_size_score | 5 | 4 | 5 | +60.0% | 0.342 | +0.432 | +69.5% | -45.0% | +203.6% | 1 |
| forward_2026-06-21_plus | logit_mechanism_weather_citybias | model_p_ge_ask_plus_05_size_probability | 4 | 4 | 4 | +25.0% | 0.253 | +0.540 | +44.4% | -100.0% | +267.7% | 3 |
| forward_2026-06-21_plus | logit_mechanism_weather_citybias | model_p_ge_ask_size_probability | 5 | 5 | 5 | +40.0% | 0.284 | +0.521 | +61.5% | -100.0% | +249.5% | 3 |

## Frozen Forward Live-Sized Gate Variants

These variants use the model only as an entry gate, while keeping the current deployed score as sizing.

| scope | model | trade_variant | trades | dates | cities | win_rate | avg_ask | avg_cost | roi | roi_ci_low | roi_ci_high | daily_negative_100pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| expanding_walk_forward | logit_market_route | current_heuristic_score_over_ask_ge_1_size_score | 25 | 17 | 16 | +52.0% | 0.306 | +0.469 | +93.1% | +18.8% | +176.7% | 5 |
| expanding_walk_forward | logit_market_route | model_p_ge_ask_plus_05_size_current_score | 21 | 14 | 18 | +38.1% | 0.249 | +0.358 | +99.5% | -6.1% | +208.7% | 6 |
| expanding_walk_forward | logit_market_route | model_p_ge_ask_size_current_score | 29 | 16 | 21 | +44.8% | 0.317 | +0.341 | +94.8% | +14.4% | +178.2% | 5 |
| expanding_walk_forward | logit_mechanism_plus_heuristic_score | current_heuristic_score_over_ask_ge_1_size_score | 25 | 17 | 16 | +52.0% | 0.306 | +0.469 | +93.1% | +18.8% | +176.7% | 5 |
| expanding_walk_forward | logit_mechanism_plus_heuristic_score | model_p_ge_ask_plus_05_size_current_score | 25 | 14 | 14 | +52.0% | 0.356 | +0.384 | +89.4% | +0.3% | +178.8% | 4 |
| expanding_walk_forward | logit_mechanism_plus_heuristic_score | model_p_ge_ask_size_current_score | 29 | 15 | 17 | +48.3% | 0.344 | +0.380 | +78.0% | -2.6% | +162.7% | 4 |
| expanding_walk_forward | logit_mechanism_weather | current_heuristic_score_over_ask_ge_1_size_score | 25 | 17 | 16 | +52.0% | 0.306 | +0.469 | +93.1% | +18.8% | +176.7% | 5 |
| expanding_walk_forward | logit_mechanism_weather | model_p_ge_ask_plus_05_size_current_score | 25 | 14 | 16 | +52.0% | 0.339 | +0.368 | +99.2% | +4.6% | +194.8% | 4 |
| expanding_walk_forward | logit_mechanism_weather | model_p_ge_ask_size_current_score | 33 | 15 | 19 | +51.5% | 0.373 | +0.352 | +76.9% | +0.1% | +154.3% | 3 |
| expanding_walk_forward | logit_mechanism_weather_citybias | current_heuristic_score_over_ask_ge_1_size_score | 25 | 17 | 16 | +52.0% | 0.306 | +0.469 | +93.1% | +18.8% | +176.7% | 5 |
| expanding_walk_forward | logit_mechanism_weather_citybias | model_p_ge_ask_plus_05_size_current_score | 27 | 14 | 16 | +48.1% | 0.344 | +0.376 | +78.9% | -10.4% | +167.2% | 4 |
| expanding_walk_forward | logit_mechanism_weather_citybias | model_p_ge_ask_size_current_score | 30 | 16 | 19 | +50.0% | 0.340 | +0.369 | +82.5% | +5.6% | +163.8% | 4 |
| forward_2026-06-21_plus | logit_market_route | current_heuristic_score_over_ask_ge_1_size_score | 5 | 4 | 5 | +60.0% | 0.342 | +0.432 | +69.5% | -45.0% | +203.6% | 1 |
| forward_2026-06-21_plus | logit_market_route | model_p_ge_ask_plus_05_size_current_score | 5 | 5 | 5 | +40.0% | 0.284 | +0.271 | +37.1% | -100.0% | +254.3% | 3 |
| forward_2026-06-21_plus | logit_market_route | model_p_ge_ask_size_current_score | 7 | 6 | 6 | +42.9% | 0.357 | +0.274 | +28.7% | -74.6% | +204.2% | 3 |
| forward_2026-06-21_plus | logit_mechanism_plus_heuristic_score | current_heuristic_score_over_ask_ge_1_size_score | 5 | 4 | 5 | +60.0% | 0.342 | +0.432 | +69.5% | -45.0% | +203.6% | 1 |
| forward_2026-06-21_plus | logit_mechanism_plus_heuristic_score | model_p_ge_ask_plus_05_size_current_score | 5 | 4 | 4 | +20.0% | 0.308 | +0.324 | -22.5% | -100.0% | +51.3% | 3 |
| forward_2026-06-21_plus | logit_mechanism_plus_heuristic_score | model_p_ge_ask_size_current_score | 6 | 5 | 5 | +33.3% | 0.325 | +0.311 | -0.6% | -100.0% | +78.6% | 3 |
| forward_2026-06-21_plus | logit_mechanism_weather | current_heuristic_score_over_ask_ge_1_size_score | 5 | 4 | 5 | +60.0% | 0.342 | +0.432 | +69.5% | -45.0% | +203.6% | 1 |
| forward_2026-06-21_plus | logit_mechanism_weather | model_p_ge_ask_plus_05_size_current_score | 4 | 4 | 4 | +25.0% | 0.253 | +0.277 | +13.4% | -100.0% | +297.2% | 3 |
| forward_2026-06-21_plus | logit_mechanism_weather | model_p_ge_ask_size_current_score | 5 | 4 | 5 | +40.0% | 0.326 | +0.267 | +21.6% | -100.0% | +297.2% | 2 |
| forward_2026-06-21_plus | logit_mechanism_weather_citybias | current_heuristic_score_over_ask_ge_1_size_score | 5 | 4 | 5 | +60.0% | 0.342 | +0.432 | +69.5% | -45.0% | +203.6% | 1 |
| forward_2026-06-21_plus | logit_mechanism_weather_citybias | model_p_ge_ask_plus_05_size_current_score | 5 | 4 | 4 | +20.0% | 0.308 | +0.324 | -22.5% | -100.0% | +51.3% | 3 |
| forward_2026-06-21_plus | logit_mechanism_weather_citybias | model_p_ge_ask_size_current_score | 6 | 5 | 5 | +33.3% | 0.325 | +0.311 | -0.6% | -100.0% | +78.6% | 3 |

## Best Forward Logistic p >= ask Variants

| scope | model | trade_variant | trades | dates | cities | win_rate | avg_ask | avg_cost | roi | roi_ci_low | roi_ci_high | daily_negative_100pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| forward_2026-06-21_plus | logit_mechanism_weather_citybias | model_p_ge_ask_size_probability | 5 | 5 | 5 | +40.0% | 0.284 | +0.521 | +61.5% | -100.0% | +249.5% | 3 |
| forward_2026-06-21_plus | logit_mechanism_plus_heuristic_score | model_p_ge_ask_size_probability | 5 | 5 | 5 | +40.0% | 0.284 | +0.514 | +56.9% | -100.0% | +247.0% | 3 |
| forward_2026-06-21_plus | logit_mechanism_weather | model_p_ge_ask_size_probability | 4 | 4 | 4 | +25.0% | 0.253 | +0.476 | +53.8% | -100.0% | +278.8% | 3 |
| forward_2026-06-21_plus | logit_market_route | model_p_ge_ask_size_probability | 6 | 6 | 6 | +50.0% | 0.332 | +0.479 | +48.9% | -64.5% | +181.6% | 3 |

## Interpretation

- Natural EV gating is `p_no >= ask`. On this small forward slice it usually selects more/other rows, but does not produce a robust improvement over the current score/ask gate.
- Adding city/source bias and heuristic score features does not rescue forward performance. The forward set is too thin to justify replacing the live score.
- Expanding walk-forward is the right next evidence layer because it prevents using future days to set probabilities. Results are mixed and should remain shadow-only.
- The useful artifact is the per-row `p_no_*` telemetry. It can be logged beside `row_risk_soft_v1` and reviewed after more live/frozen days accumulate.

## Files

- Summary JSON: `docs/analysis/2026-07/generated/regime_routed_no_calibrated_score_v2/summary.json`
- Model quality: `docs/analysis/2026-07/generated/regime_routed_no_calibrated_score_v2/model_quality.csv`
- Trade summary: `docs/analysis/2026-07/generated/regime_routed_no_calibrated_score_v2/trade_summary.csv`
- Walk-forward summary: `docs/analysis/2026-07/generated/regime_routed_no_calibrated_score_v2/walk_forward_trade_summary.csv`
- Daily summary: `docs/analysis/2026-07/generated/regime_routed_no_calibrated_score_v2/daily_summary.csv`
- Scored rows: `docs/analysis/2026-07/generated/regime_routed_no_calibrated_score_v2/scored_rows.csv`
