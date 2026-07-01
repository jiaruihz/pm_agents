# Regime-Routed NO Original + City Bias Soft V4B Replay

## Conclusion

This replay keeps the original regime-routed candidate set eligible and applies city/source forecast-bias only as a soft sizing multiplier. It compares that against the stricter V4 tail-shadow policy on the same denominator.

Verdict: `prefer_original_plus_city_bias_soft_over_tail_shadow_v4_for_shadow_candidate`，live_ready=`False`。

## Data Snapshot

- Generated at: `2026-07-01T04:28:50+00:00`
- Historical best-ask diagnostic: `2026-05-20`..`2026-06-26`
- Frozen/live-like replay: `2026-05-20`..`2026-06-28`
- Forward split: `>= 2026-06-21`
- `historical_best_ask_diagnostic` is not live-causal; use it only as a broad diagnostic.
- `frozen_live_like_route_price` is the relevant replay for the current runner shape.

## Frozen/Live-Like Same-Denominator Result

| window | policy | rows_seen | traded_rows | dates | cities | win_rate | avg_ask | avg_weight | pnl_usd | roi | roi_ci_low | roi_ci_high | daily_negative_100pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | original_executable_min5 | 163 | 77 | 31 | 27 | +46.8% | 0.345 | 0.539 | $+106.61 | +51.4% | +9.5% | +90.9% | 11 |
| all | original_plus_city_bias_soft_executable_min5 | 163 | 75 | 31 | 26 | +46.7% | 0.344 | 0.539 | $+104.64 | +51.7% | +8.9% | +92.5% | 11 |
| all | original_plus_city_bias_soft_no_min5 | 163 | 163 | 37 | 34 | +46.6% | 0.430 | 0.384 | $+102.66 | +32.8% | +3.7% | +62.3% | 5 |
| all | original_weighted_no_min5 | 163 | 163 | 37 | 34 | +46.6% | 0.430 | 0.407 | $+102.77 | +31.0% | +3.4% | +59.4% | 5 |
| all | v4_tail_shadow_city_bias_executable_min5 | 163 | 65 | 29 | 24 | +41.5% | 0.352 | 0.564 | $+71.54 | +39.0% | -4.3% | +82.7% | 11 |
| all | v4_tail_shadow_city_bias_no_min5 | 163 | 127 | 34 | 31 | +44.1% | 0.429 | 0.421 | $+68.06 | +25.5% | -7.5% | +57.0% | 5 |
| forward_2026-06-21_plus | original_executable_min5 | 14 | 5 | 4 | 5 | +60.0% | 0.342 | 0.463 | $+6.72 | +58.0% | -45.6% | +202.2% | 1 |
| forward_2026-06-21_plus | original_plus_city_bias_soft_executable_min5 | 14 | 5 | 4 | 5 | +60.0% | 0.342 | 0.432 | $+7.50 | +69.5% | -45.0% | +203.6% | 1 |
| forward_2026-06-21_plus | original_plus_city_bias_soft_no_min5 | 14 | 14 | 6 | 10 | +42.9% | 0.460 | 0.316 | $+2.52 | +11.4% | -33.7% | +47.9% | 1 |
| forward_2026-06-21_plus | original_weighted_no_min5 | 14 | 14 | 6 | 10 | +42.9% | 0.460 | 0.343 | $+1.46 | +6.1% | -35.4% | +47.2% | 1 |
| forward_2026-06-21_plus | v4_tail_shadow_city_bias_executable_min5 | 14 | 4 | 3 | 4 | +50.0% | 0.375 | 0.474 | $+2.54 | +26.8% | -100.0% | +112.8% | 1 |
| forward_2026-06-21_plus | v4_tail_shadow_city_bias_no_min5 | 14 | 9 | 4 | 7 | +33.3% | 0.464 | 0.385 | $-3.86 | -22.2% | -79.9% | +20.1% | 1 |
| train_to_2026_06_20 | original_executable_min5 | 149 | 72 | 27 | 25 | +45.8% | 0.345 | 0.544 | $+99.90 | +51.0% | +8.6% | +94.7% | 10 |
| train_to_2026_06_20 | original_plus_city_bias_soft_executable_min5 | 149 | 70 | 27 | 24 | +45.7% | 0.344 | 0.547 | $+97.14 | +50.7% | +7.3% | +95.0% | 10 |
| train_to_2026_06_20 | original_plus_city_bias_soft_no_min5 | 149 | 149 | 31 | 34 | +47.0% | 0.427 | 0.390 | $+100.14 | +34.5% | +3.0% | +66.1% | 4 |
| train_to_2026_06_20 | original_weighted_no_min5 | 149 | 149 | 31 | 34 | +47.0% | 0.427 | 0.413 | $+101.31 | +32.9% | +3.5% | +63.4% | 4 |
| train_to_2026_06_20 | v4_tail_shadow_city_bias_executable_min5 | 149 | 61 | 26 | 22 | +41.0% | 0.350 | 0.570 | $+69.00 | +39.7% | -6.4% | +84.5% | 10 |
| train_to_2026_06_20 | v4_tail_shadow_city_bias_no_min5 | 149 | 118 | 30 | 30 | +44.9% | 0.426 | 0.424 | $+71.92 | +28.8% | -5.2% | +62.3% | 4 |

## Historical Best-Ask Diagnostic

| window | policy | rows_seen | traded_rows | dates | cities | win_rate | avg_ask | avg_weight | pnl_usd | roi | roi_ci_low | roi_ci_high | daily_negative_100pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | original_executable_min5 | 283 | 76 | 32 | 26 | +48.7% | 0.338 | 0.540 | $+128.88 | +62.8% | +18.8% | +104.2% | 11 |
| all | original_plus_city_bias_soft_executable_min5 | 283 | 74 | 32 | 25 | +48.6% | 0.337 | 0.540 | $+126.91 | +63.5% | +18.1% | +106.5% | 11 |
| all | original_plus_city_bias_soft_no_min5 | 283 | 283 | 38 | 35 | +54.1% | 0.526 | 0.321 | $+112.34 | +24.7% | +4.2% | +45.4% | 1 |
| all | original_weighted_no_min5 | 283 | 283 | 38 | 35 | +54.1% | 0.526 | 0.343 | $+112.81 | +23.2% | +3.7% | +42.7% | 1 |
| all | v4_tail_shadow_city_bias_executable_min5 | 283 | 64 | 30 | 23 | +45.3% | 0.345 | 0.567 | $+98.10 | +54.1% | +8.1% | +96.9% | 10 |
| all | v4_tail_shadow_city_bias_no_min5 | 283 | 243 | 38 | 34 | +53.9% | 0.538 | 0.334 | $+82.62 | +20.4% | -1.1% | +42.3% | 2 |
| forward_2026-06-21_plus | original_executable_min5 | 31 | 5 | 4 | 5 | +60.0% | 0.314 | 0.456 | $+9.24 | +81.2% | -28.9% | +209.5% | 1 |
| forward_2026-06-21_plus | original_plus_city_bias_soft_executable_min5 | 31 | 5 | 4 | 5 | +60.0% | 0.314 | 0.424 | $+10.03 | +94.6% | -28.6% | +239.3% | 1 |
| forward_2026-06-21_plus | original_plus_city_bias_soft_no_min5 | 31 | 31 | 6 | 17 | +45.2% | 0.552 | 0.270 | $-0.43 | -1.0% | -18.1% | +13.0% | 1 |
| forward_2026-06-21_plus | original_weighted_no_min5 | 31 | 31 | 6 | 17 | +45.2% | 0.552 | 0.291 | $-1.37 | -3.0% | -20.7% | +12.8% | 1 |
| forward_2026-06-21_plus | v4_tail_shadow_city_bias_executable_min5 | 31 | 4 | 3 | 4 | +50.0% | 0.340 | 0.464 | $+5.06 | +54.6% | -100.0% | +112.8% | 1 |
| forward_2026-06-21_plus | v4_tail_shadow_city_bias_no_min5 | 31 | 25 | 6 | 16 | +44.0% | 0.568 | 0.289 | $-5.93 | -16.4% | -43.1% | -3.8% | 2 |
| train_to_2026_06_20 | original_executable_min5 | 252 | 71 | 28 | 24 | +47.9% | 0.340 | 0.546 | $+119.64 | +61.7% | +18.2% | +105.1% | 10 |
| train_to_2026_06_20 | original_plus_city_bias_soft_executable_min5 | 252 | 69 | 28 | 23 | +47.8% | 0.339 | 0.549 | $+116.88 | +61.7% | +17.1% | +106.3% | 10 |
| train_to_2026_06_20 | original_plus_city_bias_soft_no_min5 | 252 | 252 | 32 | 35 | +55.2% | 0.523 | 0.328 | $+112.77 | +27.3% | +5.3% | +49.3% | 0 |
| train_to_2026_06_20 | original_weighted_no_min5 | 252 | 252 | 32 | 35 | +55.2% | 0.523 | 0.350 | $+114.18 | +25.9% | +5.0% | +46.8% | 0 |
| train_to_2026_06_20 | v4_tail_shadow_city_bias_executable_min5 | 252 | 60 | 27 | 21 | +45.0% | 0.346 | 0.573 | $+93.03 | +54.1% | +7.6% | +100.8% | 9 |
| train_to_2026_06_20 | v4_tail_shadow_city_bias_no_min5 | 252 | 218 | 32 | 34 | +55.0% | 0.534 | 0.339 | $+88.55 | +24.0% | +1.1% | +47.2% | 0 |

## Frozen Forward Daily: Original + City Bias Soft Executable

| target_date | rows | cities | wins | cost_usd | pnl_usd | roi | route_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-21 | 2 | 2 | 1 | $+4.41 | $+2.14 | +48.5% | capped_d2_no:1,fresh_runway_current_no:1 | Beijing |
| 2026-06-23 | 1 | 1 | 1 | $+1.32 | $+4.97 | +376.2% | cheap_stale_tail_current_no:1 |  |
| 2026-06-25 | 1 | 1 | 1 | $+2.57 | $+2.90 | +112.8% | fresh_runway_current_no:1 |  |
| 2026-06-26 | 1 | 1 | 0 | $+2.50 | $-2.50 | -100.0% | fresh_runway_current_no:1 | Chongqing |

## Route Contribution: Original + City Bias Soft Executable

| window | router_route | traded_rows | dates | cities | win_rate | pnl_usd | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| all | capped_d2_no | 1 | 1 | 1 | +0.0% | $-1.46 | -100.0% |
| all | cheap_stale_tail_current_no | 2 | 2 | 2 | +100.0% | $+9.11 | +366.0% |
| all | false_fade_reheat_current_no | 8 | 7 | 8 | +75.0% | $+23.99 | +145.7% |
| all | fresh_runway_current_no | 64 | 29 | 24 | +42.2% | $+73.00 | +40.2% |
| forward_2026-06-21_plus | capped_d2_no | 1 | 1 | 1 | +0.0% | $-1.46 | -100.0% |
| forward_2026-06-21_plus | cheap_stale_tail_current_no | 1 | 1 | 1 | +100.0% | $+4.97 | +376.2% |
| forward_2026-06-21_plus | false_fade_reheat_current_no | 0 | 0 | 0 | NA | $+0.00 | NA |
| forward_2026-06-21_plus | fresh_runway_current_no | 3 | 3 | 3 | +66.7% | $+4.00 | +49.9% |
| train_to_2026_06_20 | capped_d2_no | 0 | 0 | 0 | NA | $+0.00 | NA |
| train_to_2026_06_20 | cheap_stale_tail_current_no | 1 | 1 | 1 | +100.0% | $+4.15 | +354.5% |
| train_to_2026_06_20 | false_fade_reheat_current_no | 8 | 7 | 8 | +75.0% | $+23.99 | +145.7% |
| train_to_2026_06_20 | fresh_runway_current_no | 61 | 26 | 22 | +41.0% | $+69.00 | +39.7% |

## City Bias Changed Rows

| target_date | city | router_route | router_ask | router_payoff | base_weight | city_bias_soft_weight | city_bias_soft_shares | city_bias_effect | city_source_bias_regime |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-20 | Singapore | capped_d2_no | 0.620 | 0.000 | 0.237 | 0.154 | 1.240 | downweight | hot_underforecast_clean |
| 2026-05-21 | Jeddah | capped_d2_no | 0.600 | 1.000 | 0.302 | 0.196 | 1.633 | downweight | hot_underforecast_clean |
| 2026-05-21 | Manila | capped_d2_no | 0.600 | 0.000 | 0.271 | 0.176 | 1.470 | downweight | hot_underforecast_clean |
| 2026-05-21 | Munich | capped_d2_no | 0.560 | 0.000 | 0.341 | 0.256 | 2.284 | downweight | hot_underforecast_noisy |
| 2026-05-22 | Manila | capped_d2_no | 0.580 | 0.000 | 0.289 | 0.188 | 1.620 | downweight | hot_underforecast_clean |
| 2026-05-22 | Munich | capped_d2_no | 0.550 | 0.000 | 0.351 | 0.263 | 2.393 | downweight | hot_underforecast_noisy |
| 2026-05-30 | Karachi | capped_d2_no | 0.560 | 1.000 | 0.314 | 0.204 | 1.821 | downweight | hot_underforecast_clean |
| 2026-05-30 | Shanghai | capped_d2_no | 0.560 | 0.000 | 0.307 | 0.200 | 1.782 | downweight | hot_underforecast_clean |
| 2026-05-31 | Ankara | capped_d2_no | 0.600 | 1.000 | 0.302 | 0.196 | 1.633 | downweight | hot_underforecast_clean |
| 2026-05-31 | Karachi | capped_d2_no | 0.550 | 0.000 | 0.351 | 0.228 | 2.074 | downweight | hot_underforecast_clean |
| 2026-06-01 | Wuhan | capped_d2_no | 0.600 | 1.000 | 0.271 | 0.244 | 2.035 | downweight | mild_or_mixed |
| 2026-06-02 | Munich | capped_d2_no | 0.510 | 1.000 | 0.391 | 0.293 | 2.872 | downweight | hot_underforecast_noisy |
| 2026-06-03 | BuenosAires | capped_d2_no | 0.580 | 1.000 | 0.283 | 0.212 | 1.828 | downweight | hot_underforecast_noisy |
| 2026-06-04 | Karachi | capped_d2_no | 0.580 | 1.000 | 0.321 | 0.209 | 1.800 | downweight | hot_underforecast_clean |
| 2026-06-05 | Karachi | capped_d2_no | 0.570 | 1.000 | 0.331 | 0.215 | 1.888 | downweight | hot_underforecast_clean |
| 2026-06-06 | Chongqing | capped_d2_no | 0.460 | 1.000 | 0.396 | 0.257 | 2.798 | downweight | hot_underforecast_clean |
| 2026-06-07 | Munich | capped_d2_no | 0.490 | 1.000 | 0.410 | 0.308 | 3.141 | downweight | hot_underforecast_noisy |
| 2026-06-08 | Dallas | capped_d2_no | 0.530 | 1.000 | 0.371 | 0.241 | 2.274 | downweight | hot_underforecast_clean |
| 2026-06-08 | Karachi | capped_d2_no | 0.560 | 1.000 | 0.314 | 0.204 | 1.821 | downweight | hot_underforecast_clean |
| 2026-06-09 | Karachi | capped_d2_no | 0.600 | 1.000 | 0.302 | 0.196 | 1.633 | downweight | hot_underforecast_clean |
| 2026-06-09 | Shanghai | capped_d2_no | 0.470 | 1.000 | 0.387 | 0.252 | 2.677 | downweight | hot_underforecast_clean |
| 2026-06-11 | Ankara | capped_d2_no | 0.410 | 0.000 | 0.450 | 0.293 | 3.567 | downweight | hot_underforecast_clean |
| 2026-06-12 | Ankara | capped_d2_no | 0.610 | 0.000 | 0.292 | 0.190 | 1.554 | downweight | hot_underforecast_clean |
| 2026-06-12 | Karachi | capped_d2_no | 0.550 | 0.000 | 0.323 | 0.210 | 1.908 | downweight | hot_underforecast_clean |
| 2026-06-13 | Karachi | capped_d2_no | 0.600 | 0.000 | 0.259 | 0.169 | 1.404 | downweight | hot_underforecast_clean |
| 2026-06-15 | Ankara | capped_d2_no | 0.310 | 1.000 | 0.450 | 0.293 | 4.718 | downweight | hot_underforecast_clean |
| 2026-06-15 | Karachi | capped_d2_no | 0.600 | 1.000 | 0.277 | 0.180 | 1.502 | downweight | hot_underforecast_clean |
| 2026-06-15 | Munich | capped_d2_no | 0.490 | 0.000 | 0.410 | 0.308 | 3.141 | downweight | hot_underforecast_noisy |
| 2026-06-17 | Dallas | capped_d2_no | 0.610 | 1.000 | 0.292 | 0.190 | 1.554 | downweight | hot_underforecast_clean |
| 2026-06-17 | Karachi | capped_d2_no | 0.530 | 0.000 | 0.341 | 0.222 | 2.092 | downweight | hot_underforecast_clean |
| 2026-06-19 | BuenosAires | capped_d2_no | 0.560 | 0.000 | 0.341 | 0.256 | 2.284 | downweight | hot_underforecast_noisy |
| 2026-06-19 | Helsinki | capped_d2_no | 0.510 | 1.000 | 0.391 | 0.352 | 3.446 | downweight | mild_or_mixed |
| 2026-06-19 | Karachi | capped_d2_no | 0.620 | 0.000 | 0.242 | 0.157 | 1.270 | downweight | hot_underforecast_clean |
| 2026-06-21 | Beijing | capped_d2_no | 0.290 | 0.000 | 0.450 | 0.293 | 5.043 | downweight | hot_underforecast_clean |
| 2026-06-21 | Singapore | capped_d2_no | 0.510 | 0.000 | 0.328 | 0.213 | 2.091 | downweight | hot_underforecast_clean |
| 2026-06-23 | Wellington | capped_d2_no | 0.620 | 1.000 | 0.282 | 0.183 | 1.477 | downweight | hot_underforecast_clean |
| 2026-06-28 | Singapore | capped_d2_no | 0.410 | NA | 0.378 | 0.246 | 2.996 | downweight | hot_underforecast_clean |

## Interpretation

- Keeping the original route set and applying city/source bias as soft sizing preserves almost all signal count.
- The strict V4 patch is cleaner for mechanism accounting, but it is too conservative as the main execution expression because it thins forward rows.
- City/source bias is a reasonable overlay because it is based on historical forecast-vs-station behavior and changes size rather than rewriting the trade label.
- Runner implication: tail/fade routes should be logged as diagnostic route tags, not forced to `shadow_only` in the main execution expression.
- This is still not a live-confirmed edge: the forward executable sample remains very small, so it should replace V4 only as the preferred shadow/main-candidate replay, not as an unqualified size-up approval.

## Files

- Summary JSON: `docs/analysis/2026-07/generated/regime_routed_no_original_city_bias_soft_v4b/summary.json`
- Policy summary: `docs/analysis/2026-07/generated/regime_routed_no_original_city_bias_soft_v4b/policy_summary.csv`
- Route summary: `docs/analysis/2026-07/generated/regime_routed_no_original_city_bias_soft_v4b/route_summary.csv`
- Daily summary: `docs/analysis/2026-07/generated/regime_routed_no_original_city_bias_soft_v4b/daily_summary.csv`
- Changed rows: `docs/analysis/2026-07/generated/regime_routed_no_original_city_bias_soft_v4b/city_bias_changed_rows.csv`
