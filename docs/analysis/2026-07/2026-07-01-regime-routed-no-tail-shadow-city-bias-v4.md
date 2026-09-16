# Regime-Routed NO Tail-Shadow + City Bias V4 Replay

## Conclusion

This replay applies the live-runner patch: false-fade and cheap-stale current-NO are shadow-only; day_forecast_capped d2 NO keeps the same expression but receives city/source forecast-bias sizing.

Verdict: `shadow_candidate_cleaner_expression_not_live_confirmed`，live_ready=`False`。

## Data Snapshot

- Generated at: `2026-07-01T02:14:17+00:00`
- Historical best-ask diagnostic: `2026-05-20`..`2026-06-26`
- Frozen/live-like replay: `2026-05-20`..`2026-06-28`
- Forward split: `>= 2026-06-21`
- `historical_best_ask_diagnostic` uses the old best-ask selector and is not live-causal.
- `frozen_live_like_route_price` is the relevant replay for the current runner shape.

## Frozen/Live-Like Result

| window | policy | rows_seen | traded_rows | dates | cities | win_rate | avg_ask | avg_weight | pnl_usd | roi | roi_ci_low | roi_ci_high | daily_negative_100pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | pre_patch_weighted | 163 | 163 | 37 | 34 | +46.6% | 0.430 | 0.407 | $+102.77 | +31.0% | +3.4% | +59.4% | 5 |
| all | v4_executable_min5 | 163 | 65 | 29 | 24 | +41.5% | 0.352 | 0.564 | $+71.54 | +39.0% | -4.3% | +82.7% | 11 |
| all | v4_weighted_no_min5 | 163 | 127 | 34 | 31 | +44.1% | 0.429 | 0.421 | $+68.06 | +25.5% | -7.5% | +57.0% | 5 |
| forward_2026-06-21_plus | pre_patch_weighted | 14 | 14 | 6 | 10 | +42.9% | 0.460 | 0.343 | $+1.46 | +6.1% | -35.4% | +47.2% | 1 |
| forward_2026-06-21_plus | v4_executable_min5 | 14 | 4 | 3 | 4 | +50.0% | 0.375 | 0.474 | $+2.54 | +26.8% | -100.0% | +112.8% | 1 |
| forward_2026-06-21_plus | v4_weighted_no_min5 | 14 | 9 | 4 | 7 | +33.3% | 0.464 | 0.385 | $-3.86 | -22.2% | -79.9% | +20.1% | 1 |
| train_to_2026_06_20 | pre_patch_weighted | 149 | 149 | 31 | 34 | +47.0% | 0.427 | 0.413 | $+101.31 | +32.9% | +3.5% | +63.4% | 4 |
| train_to_2026_06_20 | v4_executable_min5 | 149 | 61 | 26 | 22 | +41.0% | 0.350 | 0.570 | $+69.00 | +39.7% | -6.4% | +84.5% | 10 |
| train_to_2026_06_20 | v4_weighted_no_min5 | 149 | 118 | 30 | 30 | +44.9% | 0.426 | 0.424 | $+71.92 | +28.8% | -5.2% | +62.3% | 4 |

## Historical Best-Ask Diagnostic

| window | policy | rows_seen | traded_rows | dates | cities | win_rate | avg_ask | avg_weight | pnl_usd | roi | roi_ci_low | roi_ci_high | daily_negative_100pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | pre_patch_weighted | 283 | 283 | 38 | 35 | +54.1% | 0.526 | 0.343 | $+112.81 | +23.2% | +3.7% | +42.7% | 1 |
| all | v4_executable_min5 | 283 | 64 | 30 | 23 | +45.3% | 0.345 | 0.567 | $+98.10 | +54.1% | +8.1% | +96.9% | 10 |
| all | v4_weighted_no_min5 | 283 | 243 | 38 | 34 | +53.9% | 0.538 | 0.334 | $+82.62 | +20.4% | -1.1% | +42.3% | 2 |
| forward_2026-06-21_plus | pre_patch_weighted | 31 | 31 | 6 | 17 | +45.2% | 0.552 | 0.291 | $-1.37 | -3.0% | -20.7% | +12.8% | 1 |
| forward_2026-06-21_plus | v4_executable_min5 | 31 | 4 | 3 | 4 | +50.0% | 0.340 | 0.464 | $+5.06 | +54.6% | -100.0% | +112.8% | 1 |
| forward_2026-06-21_plus | v4_weighted_no_min5 | 31 | 25 | 6 | 16 | +44.0% | 0.568 | 0.289 | $-5.93 | -16.4% | -43.1% | -3.8% | 2 |
| train_to_2026_06_20 | pre_patch_weighted | 252 | 252 | 32 | 35 | +55.2% | 0.523 | 0.350 | $+114.18 | +25.9% | +5.0% | +46.8% | 0 |
| train_to_2026_06_20 | v4_executable_min5 | 252 | 60 | 27 | 21 | +45.0% | 0.346 | 0.573 | $+93.03 | +54.1% | +7.6% | +100.8% | 9 |
| train_to_2026_06_20 | v4_weighted_no_min5 | 252 | 218 | 32 | 34 | +55.0% | 0.534 | 0.339 | $+88.55 | +24.0% | +1.1% | +47.2% | 0 |

## Frozen Forward Daily

| target_date | rows | cities | wins | cost_usd | pnl_usd | roi | route_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-21 | 2 | 2 | 1 | $+4.41 | $+2.14 | +48.5% | capped_d2_no:1,fresh_runway_current_no:1 | Beijing |
| 2026-06-25 | 1 | 1 | 1 | $+2.57 | $+2.90 | +112.8% | fresh_runway_current_no:1 |  |
| 2026-06-26 | 1 | 1 | 0 | $+2.50 | $-2.50 | -100.0% | fresh_runway_current_no:1 | Chongqing |

## V4 Route Contribution

| evidence_layer | window | router_route | traded_rows | dates | win_rate | pnl_usd | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| frozen_live_like_route_price | all | capped_d2_no | 1 | 1 | +0.0% | $-1.46 | -100.0% |
| frozen_live_like_route_price | all | cheap_stale_tail_current_no | 0 | 0 | NA | $+0.00 | NA |
| frozen_live_like_route_price | all | false_fade_reheat_current_no | 0 | 0 | NA | $+0.00 | NA |
| frozen_live_like_route_price | all | fresh_runway_current_no | 64 | 29 | +42.2% | $+73.00 | +40.2% |
| frozen_live_like_route_price | forward_2026-06-21_plus | capped_d2_no | 1 | 1 | +0.0% | $-1.46 | -100.0% |
| frozen_live_like_route_price | forward_2026-06-21_plus | cheap_stale_tail_current_no | 0 | 0 | NA | $+0.00 | NA |
| frozen_live_like_route_price | forward_2026-06-21_plus | false_fade_reheat_current_no | 0 | 0 | NA | $+0.00 | NA |
| frozen_live_like_route_price | forward_2026-06-21_plus | fresh_runway_current_no | 3 | 3 | +66.7% | $+4.00 | +49.9% |
| frozen_live_like_route_price | train_to_2026_06_20 | capped_d2_no | 0 | 0 | NA | $+0.00 | NA |
| frozen_live_like_route_price | train_to_2026_06_20 | cheap_stale_tail_current_no | 0 | 0 | NA | $+0.00 | NA |
| frozen_live_like_route_price | train_to_2026_06_20 | false_fade_reheat_current_no | 0 | 0 | NA | $+0.00 | NA |
| frozen_live_like_route_price | train_to_2026_06_20 | fresh_runway_current_no | 61 | 26 | +41.0% | $+69.00 | +39.7% |
| historical_best_ask_diagnostic | all | capped_d2_no | 1 | 1 | +0.0% | $-1.46 | -100.0% |
| historical_best_ask_diagnostic | all | cheap_stale_tail_current_no | 0 | 0 | NA | $+0.00 | NA |
| historical_best_ask_diagnostic | all | false_fade_reheat_current_no | 0 | 0 | NA | $+0.00 | NA |
| historical_best_ask_diagnostic | all | fresh_runway_current_no | 63 | 30 | +46.0% | $+99.56 | +55.4% |
| historical_best_ask_diagnostic | all | pullback_uncertain_current_high_yes | 0 | 0 | NA | $+0.00 | NA |
| historical_best_ask_diagnostic | forward_2026-06-21_plus | capped_d2_no | 1 | 1 | +0.0% | $-1.46 | -100.0% |
| historical_best_ask_diagnostic | forward_2026-06-21_plus | cheap_stale_tail_current_no | 0 | 0 | NA | $+0.00 | NA |
| historical_best_ask_diagnostic | forward_2026-06-21_plus | false_fade_reheat_current_no | 0 | 0 | NA | $+0.00 | NA |
| historical_best_ask_diagnostic | forward_2026-06-21_plus | fresh_runway_current_no | 3 | 3 | +66.7% | $+6.53 | +83.5% |
| historical_best_ask_diagnostic | forward_2026-06-21_plus | pullback_uncertain_current_high_yes | 0 | 0 | NA | $+0.00 | NA |
| historical_best_ask_diagnostic | train_to_2026_06_20 | capped_d2_no | 0 | 0 | NA | $+0.00 | NA |
| historical_best_ask_diagnostic | train_to_2026_06_20 | cheap_stale_tail_current_no | 0 | 0 | NA | $+0.00 | NA |
| historical_best_ask_diagnostic | train_to_2026_06_20 | false_fade_reheat_current_no | 0 | 0 | NA | $+0.00 | NA |
| historical_best_ask_diagnostic | train_to_2026_06_20 | fresh_runway_current_no | 60 | 27 | +45.0% | $+93.03 | +54.1% |
| historical_best_ask_diagnostic | train_to_2026_06_20 | pullback_uncertain_current_high_yes | 0 | 0 | NA | $+0.00 | NA |

## Changed Rows

| evidence_layer | target_date | city | router_route | router_ask | router_payoff | base_weight | patched_weight | patched_shares | patch_effect | city_source_bias_regime |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| frozen_live_like_route_price | 2026-05-20 | Jeddah | false_fade_reheat_current_no | 0.587 | 0.000 | 0.114 | 0.000 | 0.000 | shadow_only_tail_or_false_fade | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-05-20 | Shanghai | cheap_stale_tail_current_no | 0.220 | 1.000 | 0.234 | 0.000 | 0.000 | shadow_only_tail_or_false_fade | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-05-20 | Singapore | capped_d2_no | 0.620 | 0.000 | 0.237 | 0.154 | 1.240 | below_min5_after_sizing | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-05-21 | Guangzhou | false_fade_reheat_current_no | 0.650 | 1.000 | 0.227 | 0.000 | 0.000 | shadow_only_tail_or_false_fade | hot_underforecast_noisy |
| frozen_live_like_route_price | 2026-05-21 | Jeddah | capped_d2_no | 0.600 | 1.000 | 0.302 | 0.196 | 1.633 | below_min5_after_sizing | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-05-21 | Manila | capped_d2_no | 0.600 | 0.000 | 0.271 | 0.176 | 1.470 | below_min5_after_sizing | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-05-21 | Munich | capped_d2_no | 0.560 | 0.000 | 0.341 | 0.256 | 2.284 | below_min5_after_sizing | hot_underforecast_noisy |
| frozen_live_like_route_price | 2026-05-21 | TelAviv | false_fade_reheat_current_no | 0.580 | 0.000 | 0.200 | 0.000 | 0.000 | shadow_only_tail_or_false_fade | mild_or_mixed |
| frozen_live_like_route_price | 2026-05-21 | Warsaw | false_fade_reheat_current_no | 0.430 | 1.000 | 0.260 | 0.000 | 0.000 | shadow_only_tail_or_false_fade | balanced_tight |
| frozen_live_like_route_price | 2026-05-22 | Helsinki | fresh_runway_current_no | 0.310 | 0.000 | 0.160 | 0.160 | 2.581 | below_min5_after_sizing | mild_or_mixed |
| frozen_live_like_route_price | 2026-05-22 | Manila | capped_d2_no | 0.580 | 0.000 | 0.289 | 0.188 | 1.620 | below_min5_after_sizing | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-05-22 | Munich | capped_d2_no | 0.550 | 0.000 | 0.351 | 0.263 | 2.393 | below_min5_after_sizing | hot_underforecast_noisy |
| frozen_live_like_route_price | 2026-05-23 | Lucknow | fresh_runway_current_no | 0.500 | 0.000 | 0.456 | 0.456 | 4.557 | below_min5_after_sizing | cold_overforecast_clean |
| frozen_live_like_route_price | 2026-05-23 | Shanghai | fresh_runway_current_no | 0.550 | 1.000 | 0.449 | 0.449 | 4.084 | below_min5_after_sizing | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-05-25 | CapeTown | fresh_runway_current_no | 0.550 | 0.000 | 0.250 | 0.250 | 2.269 | below_min5_after_sizing | cold_overforecast_noisy |
| frozen_live_like_route_price | 2026-05-25 | TelAviv | fresh_runway_current_no | 0.230 | 0.000 | 0.160 | 0.160 | 3.478 | below_min5_after_sizing | mild_or_mixed |
| frozen_live_like_route_price | 2026-05-25 | Wuhan | false_fade_reheat_current_no | 0.470 | 0.000 | 0.194 | 0.000 | 0.000 | shadow_only_tail_or_false_fade | mild_or_mixed |
| frozen_live_like_route_price | 2026-05-26 | Chongqing | fresh_runway_current_no | 0.440 | 1.000 | 0.269 | 0.269 | 3.055 | below_min5_after_sizing | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-05-26 | Shanghai | fresh_runway_current_no | 0.550 | 1.000 | 0.389 | 0.389 | 3.540 | below_min5_after_sizing | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-05-27 | SaoPaulo | false_fade_reheat_current_no | 0.350 | 0.000 | 0.366 | 0.000 | 0.000 | shadow_only_tail_or_false_fade | mild_or_mixed |
| frozen_live_like_route_price | 2026-05-28 | Ankara | fresh_runway_current_no | 0.500 | 0.000 | 0.456 | 0.456 | 4.557 | below_min5_after_sizing | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-05-28 | Beijing | false_fade_reheat_current_no | 0.290 | 1.000 | 0.650 | 0.000 | 0.000 | shadow_only_tail_or_false_fade | cold_overforecast_noisy |
| frozen_live_like_route_price | 2026-05-28 | Chongqing | cheap_stale_tail_current_no | 0.290 | 0.000 | 0.058 | 0.000 | 0.000 | shadow_only_tail_or_false_fade | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-05-28 | Jeddah | fresh_runway_current_no | 0.393 | 1.000 | 0.320 | 0.320 | 4.071 | below_min5_after_sizing | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-05-28 | Manila | fresh_runway_current_no | 0.480 | 0.000 | 0.430 | 0.430 | 4.483 | below_min5_after_sizing | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-05-28 | NYC | false_fade_reheat_current_no | 0.490 | 0.000 | 0.139 | 0.000 | 0.000 | shadow_only_tail_or_false_fade | cold_overforecast_noisy |
| frozen_live_like_route_price | 2026-05-28 | Wellington | false_fade_reheat_current_no | 0.440 | 1.000 | 0.458 | 0.000 | 0.000 | shadow_only_tail_or_false_fade | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-05-29 | CapeTown | fresh_runway_current_no | 0.530 | 1.000 | 0.527 | 0.527 | 4.975 | below_min5_after_sizing | cold_overforecast_noisy |
| frozen_live_like_route_price | 2026-05-29 | SaoPaulo | fresh_runway_current_no | 0.550 | 0.000 | 0.235 | 0.235 | 2.133 | below_min5_after_sizing | mild_or_mixed |
| frozen_live_like_route_price | 2026-05-29 | Seattle | cheap_stale_tail_current_no | 0.250 | 1.000 | 0.066 | 0.000 | 0.000 | shadow_only_tail_or_false_fade | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-05-30 | Karachi | capped_d2_no | 0.560 | 1.000 | 0.314 | 0.204 | 1.821 | below_min5_after_sizing | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-05-30 | Shanghai | capped_d2_no | 0.560 | 0.000 | 0.307 | 0.200 | 1.782 | below_min5_after_sizing | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-05-30 | Tokyo | capped_d2_no | 0.420 | 1.000 | 0.405 | 0.405 | 4.821 | below_min5_after_sizing | balanced_tight |
| frozen_live_like_route_price | 2026-05-31 | Ankara | capped_d2_no | 0.600 | 1.000 | 0.302 | 0.196 | 1.633 | below_min5_after_sizing | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-05-31 | Busan | fresh_runway_current_no | 0.150 | 0.000 | 0.144 | 0.144 | 4.800 | below_min5_after_sizing | cold_overforecast_noisy |
| frozen_live_like_route_price | 2026-05-31 | Karachi | capped_d2_no | 0.550 | 0.000 | 0.351 | 0.228 | 2.074 | below_min5_after_sizing | hot_underforecast_clean |
| frozen_live_like_route_price | 2026-06-01 | TelAviv | cheap_stale_tail_current_no | 0.320 | 0.000 | 0.150 | 0.000 | 0.000 | shadow_only_tail_or_false_fade | mild_or_mixed |
| frozen_live_like_route_price | 2026-06-01 | Wuhan | capped_d2_no | 0.600 | 1.000 | 0.271 | 0.244 | 2.035 | below_min5_after_sizing | mild_or_mixed |
| frozen_live_like_route_price | 2026-06-02 | Amsterdam | cheap_stale_tail_current_no | 0.330 | 0.000 | 0.264 | 0.000 | 0.000 | shadow_only_tail_or_false_fade | balanced_tight |
| frozen_live_like_route_price | 2026-06-02 | Munich | capped_d2_no | 0.510 | 1.000 | 0.391 | 0.293 | 2.872 | below_min5_after_sizing | hot_underforecast_noisy |

## Interpretation

- The patch is not a magic improvement on all historical point estimates. It deliberately removes two ambiguous current-NO mechanisms from live accounting.
- The main benefit is risk cleanliness: frozen forward loses fewer dollars and has fewer active bad rows, but sample size also drops.
- City/source bias mostly affects capped d2 NO; BuenosAires/ECMWF-style hot-underforecast rows become smaller or fall below min-share execution.

## Files

- Summary JSON: `docs/analysis/2026-07/generated/regime_routed_no_tail_shadow_city_bias_v4/summary.json`
- Policy summary: `docs/analysis/2026-07/generated/regime_routed_no_tail_shadow_city_bias_v4/policy_summary.csv`
- Daily summary: `docs/analysis/2026-07/generated/regime_routed_no_tail_shadow_city_bias_v4/daily_summary.csv`
- Changed rows: `docs/analysis/2026-07/generated/regime_routed_no_tail_shadow_city_bias_v4/dropped_or_resized_rows.csv`
