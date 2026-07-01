# Regime-Routed NO Score Component Audit V1

## Conclusion

The current score is a defensible conservative mechanism score, but its rank signal is weak. It is not yet a calibrated probability model.

Verdict: `keep_live_score_fixed_quality_gate; research_shadow_calibrated_score_v2`.

Most important finding: keep the fixed `score / ask >= 1.0` quality gate, but treat score improvement as a shadow/research task. Do not change live scoring from this audit alone.

## Coverage

- Frozen/live-like replay: `2026-05-20`..`2026-06-28`, rows `165`.
- Historical best-ask diagnostic: `2026-05-20`..`2026-06-26`, rows `283`.
- Forward split: `>= 2026-06-21`.

## Deployed Score Threshold Sweep

| window | variant | threshold | cap_mode | rows | dates | cities | win_rate | avg_ask | avg_weight | roi | roi_ci_low | roi_ci_high | daily_negative_100pct | rank_auc_score_ratio |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | deployed_score | 0.800 | daily_cap_weight_1 | 47 | 33 | 20 | +46.8% | 0.353 | 0.455 | +25.5% | -11.2% | +62.1% | 13 | +54.7% |
| all | deployed_score | 1.000 | daily_cap_weight_1 | 42 | 31 | 22 | +47.6% | 0.335 | 0.477 | +29.7% | -11.9% | +69.9% | 13 | +54.7% |
| all | deployed_score | 1.200 | daily_cap_weight_1 | 29 | 26 | 17 | +48.3% | 0.322 | 0.544 | +51.2% | -11.1% | +122.3% | 12 | +54.7% |
| forward_2026-06-21_plus | deployed_score | 0.800 | daily_cap_weight_1 | 6 | 4 | 5 | +50.0% | 0.373 | 0.445 | +36.9% | -45.0% | +89.4% | 1 | +58.3% |
| forward_2026-06-21_plus | deployed_score | 1.000 | daily_cap_weight_1 | 5 | 4 | 5 | +60.0% | 0.342 | 0.432 | +69.5% | -45.0% | +203.6% | 1 | +58.3% |
| forward_2026-06-21_plus | deployed_score | 1.200 | daily_cap_weight_1 | 3 | 3 | 3 | +66.7% | 0.317 | 0.451 | +89.8% | -100.0% | +376.2% | 1 | +58.3% |

## Ablation / Variant Check

| window | variant | threshold | cap_mode | rows | dates | cities | win_rate | avg_ask | avg_weight | roi | roi_ci_low | roi_ci_high | daily_negative_100pct | rank_auc_score_ratio |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | deployed_score | 1.000 | daily_cap_weight_1 | 42 | 31 | 22 | +47.6% | 0.335 | 0.477 | +29.7% | -11.9% | +69.9% | 13 | +54.7% |
| all | all_expr_city_source_bias | 1.000 | daily_cap_weight_1 | 32 | 24 | 16 | +46.9% | 0.329 | 0.480 | +48.2% | -13.3% | +118.3% | 12 | +55.3% |
| all | no_price_mult | 1.000 | daily_cap_weight_1 | 44 | 32 | 21 | +45.5% | 0.346 | 0.483 | +20.4% | -15.7% | +58.3% | 13 | +57.1% |
| all | no_peak_mult | 1.000 | daily_cap_weight_1 | 37 | 32 | 16 | +45.9% | 0.351 | 0.552 | +17.3% | -25.6% | +61.6% | 16 | +48.3% |
| all | no_freshness_mult | 1.000 | daily_cap_weight_1 | 42 | 31 | 22 | +47.6% | 0.335 | 0.477 | +29.7% | -11.9% | +69.9% | 13 | +54.7% |
| all | no_momentum_mult | 1.000 | daily_cap_weight_1 | 40 | 32 | 21 | +45.0% | 0.334 | 0.525 | +25.2% | -17.6% | +72.5% | 16 | +52.7% |
| all | no_weather_mult | 1.000 | daily_cap_weight_1 | 43 | 32 | 22 | +46.5% | 0.334 | 0.507 | +30.0% | -8.3% | +70.1% | 13 | +54.9% |

## Score Ratio Bins

| window | score_ratio_bin | rows | dates | win_rate | avg_ask | avg_weight | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| all | <0.5 | 49 | 25 | +40.8% | 0.533 | 0.182 | -23.8% |
| all | 0.5-0.8 | 25 | 20 | +56.0% | 0.474 | 0.298 | +15.8% |
| all | 0.8-1.0 | 15 | 12 | +46.7% | 0.424 | 0.388 | +7.7% |
| all | 1.0-1.2 | 17 | 14 | +52.9% | 0.366 | 0.398 | +43.5% |
| all | 1.2-1.5 | 26 | 19 | +46.2% | 0.411 | 0.564 | +11.6% |
| all | >=1.5 | 31 | 16 | +45.2% | 0.284 | 0.610 | +86.7% |
| forward_2026-06-21_plus | <0.5 | 5 | 4 | +40.0% | 0.522 | 0.149 | -10.9% |
| forward_2026-06-21_plus | 0.5-0.8 | 3 | 3 | +33.3% | 0.530 | 0.338 | -39.6% |
| forward_2026-06-21_plus | 0.8-1.0 | 1 | 1 | +0.0% | 0.530 | 0.514 | -100.0% |
| forward_2026-06-21_plus | 1.0-1.2 | 2 | 2 | +50.0% | 0.380 | 0.403 | +35.6% |
| forward_2026-06-21_plus | 1.2-1.5 | 2 | 2 | +100.0% | 0.330 | 0.426 | +200.8% |
| forward_2026-06-21_plus | >=1.5 | 1 | 1 | +0.0% | 0.290 | 0.499 | -100.0% |

## Route Slices

| component_value | rows | dates | cities | win_rate | avg_ask | avg_weight | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| capped_d2_no | 37 | 22 | 15 | +54.1% | 0.540 | 0.233 | +10.1% |
| cheap_stale_tail_current_no | 8 | 8 | 7 | +37.5% | 0.268 | 0.151 | +114.3% |
| false_fade_reheat_current_no | 28 | 17 | 21 | +60.7% | 0.480 | 0.282 | +70.3% |
| fresh_runway_current_no | 90 | 32 | 26 | +40.0% | 0.383 | 0.499 | +28.4% |

## Interpretation

- The score/ask ratio has useful but thin ranking signal: frozen AUC is only about 55%, so this should be treated as a conservative heuristic, not a probability estimate.
- Loosening the live threshold to `0.8` adds weaker rows and does not improve the forward slice.
- Tightening to `1.2` looks better in-sample but leaves only 3 forward rows, so it is not a deployable improvement.
- Removing the price multiplier admits more rows but does not provide a clean forward improvement; price is probably doing useful risk control, even if the current formula is rough.
- Applying city/source bias to all expression groups improves the full-window point estimate but fails the forward slice here. It should be shadow telemetry only, not a live scoring change.
- Weather haircuts are plausible first-principles features, but their independent contribution is not proven here; keep them as small haircuts, not hard gates.
- The next clean upgrade is not more thresholds. It is a calibrated `P(NO wins | route, ask, peak clock, freshness, trend, weather, city/source bias)` shadow model compared against this heuristic score.

## Files

- Summary JSON: `docs/analysis/2026-07/generated/regime_routed_no_score_component_audit_v1/summary.json`
- Variant summary: `docs/analysis/2026-07/generated/regime_routed_no_score_component_audit_v1/score_variant_gate_summary.csv`
- Score bins: `docs/analysis/2026-07/generated/regime_routed_no_score_component_audit_v1/score_ratio_bins.csv`
- Component summary: `docs/analysis/2026-07/generated/regime_routed_no_score_component_audit_v1/component_slice_summary.csv`
- Component rows: `docs/analysis/2026-07/generated/regime_routed_no_score_component_audit_v1/score_component_rows.csv`
