# Regime-Routed NO Temperature Context Overlay V1

Generated: `2026-06-26T13:21:42+00:00`

## Verdict

This is a same-denominator A/B overlay on the current regime-routed NO candidate set.  It keeps the existing `routed_capped_d2_no_relaxed70_best_ask` rows fixed and only tests whether shared temperature-context soft sizing improves the current regime-only `soft_balanced` policy.

Result: temperature context improves point-estimate ROI on this denominator, but the delta versus `soft_balanced` is not statistically significant and there is no new frozen-forward evidence.  This should remain shadow/telemetry, not live sizing.

Funnel: `271` selected rows, `35` dates, `35` cities, `2026-05-20`..`2026-06-23`.

## Policy Summary

| weight_policy | rows | dates | cities | hit_rate | cost_usd | pnl_usd | roi | exec_rows | exec_roi | avg_weight |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full_size | 271 | 35 | 35 | +51.7% | +1355.00 | +155.73 | +11.5% | 271 | +11.5% | +100.0% |
| soft_balanced | 271 | 35 | 35 | +51.7% | +469.32 | +123.21 | +26.3% | 77 | +69.9% | +34.6% |
| soft_wind_context | 271 | 35 | 35 | +51.7% | +453.50 | +117.24 | +25.9% | 75 | +73.0% | +33.5% |
| soft_temp_context_light | 271 | 35 | 35 | +51.7% | +428.56 | +128.62 | +30.0% | 67 | +71.0% | +31.6% |
| soft_temp_context_medium | 271 | 35 | 35 | +51.7% | +408.12 | +132.60 | +32.5% | 61 | +81.5% | +30.1% |

## Delta Bootstrap

| candidate | baseline | delta_roi | ci_low | ci_high |
| --- | --- | --- | --- | --- |
| soft_temp_context_light | soft_balanced | +3.8% | -0.00 | +0.08 |
| soft_temp_context_medium | soft_balanced | +6.2% | -0.00 | +0.12 |
| soft_wind_context | soft_balanced | -0.4% | -0.01 | +0.00 |

## Where It Helped

| context_feature | context_bucket | rows | dates | routes | avg_multiplier | baseline_roi | temp_roi | cost_delta_usd | pnl_delta_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cloud_warming_interaction | mixed_sky_warming | 46 | 27 | capped_d2_no,runway_current_no | +91.3% | +54.4% | +64.3% | -0.41 | +7.66 |
| moisture_cloud_interaction | mixed_moisture_cloud | 163 | 35 | capped_d2_no,runway_current_no | +87.3% | +9.0% | +12.5% | -22.79 | +7.04 |
| marine_thermal_state | coastal_direction_unknown_mixing | 29 | 23 | capped_d2_no,runway_current_no | +85.2% | -58.1% | -55.2% | -6.07 | +4.98 |
| route_leg | runway_current_no | 189 | 34 | runway_current_no | +89.5% | +31.0% | +34.4% | -23.30 | +4.96 |
| marine_thermal_state | inland_wind_mixing | 82 | 32 | capped_d2_no,runway_current_no | +89.0% | +30.5% | +35.0% | -8.92 | +3.26 |
| forecast_peak_clock_state | forecast_peak_passed_0_to_1h | 22 | 18 | capped_d2_no,runway_current_no | +65.1% | -28.8% | -28.8% | -10.85 | +3.13 |
| moisture_cloud_interaction | dry_heat_inertia | 43 | 23 | capped_d2_no,runway_current_no | +91.6% | +84.3% | +91.8% | -3.42 | +2.78 |
| cloud_warming_interaction | cloud_warming_unknown | 55 | 29 | capped_d2_no,runway_current_no | +90.7% | -3.8% | -1.0% | -4.61 | +2.66 |
| forecast_peak_clock_state | forecast_peak_2h_plus_ahead | 136 | 34 | capped_d2_no,runway_current_no | +89.8% | +33.7% | +36.4% | -12.38 | +2.54 |
| marine_thermal_state | offshore_or_parallel_warming_risk | 17 | 15 | capped_d2_no,runway_current_no | +88.4% | +96.7% | +109.2% | -1.57 | +2.39 |
| cloud_warming_interaction | cloud_limited_flat_or_cooling | 17 | 13 | capped_d2_no,runway_current_no | +70.8% | -24.7% | -21.3% | -6.70 | +2.28 |
| forecast_peak_clock_state | forecast_peak_0_to_2h_ahead | 96 | 34 | capped_d2_no,runway_current_no | +92.1% | +25.0% | +27.8% | -12.65 | +1.17 |

## Where It Hurt

| context_feature | context_bucket | rows | dates | routes | avg_multiplier | baseline_roi | temp_roi | cost_delta_usd | pnl_delta_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| marine_thermal_state | onshore_marine_cooling_risk | 51 | 26 | capped_d2_no,runway_current_no | +77.9% | +30.0% | +29.5% | -16.90 | -5.40 |
| moisture_cloud_interaction | humid_convective_risk | 37 | 21 | capped_d2_no,runway_current_no | +84.0% | +41.0% | +37.4% | -6.27 | -4.68 |
| cloud_warming_interaction | mixed_sky_flat_or_cooling | 26 | 14 | capped_d2_no,runway_current_no | +83.1% | +55.2% | +54.5% | -7.17 | -4.27 |
| forecast_peak_clock_state | forecast_peak_passed_2h_plus | 10 | 8 | capped_d2_no,runway_current_no | +58.4% | +102.2% | +98.4% | -2.92 | -3.14 |
| cloud_warming_interaction | warming_through_cloud | 12 | 11 | capped_d2_no,runway_current_no | +87.8% | +36.3% | +32.2% | -2.06 | -1.53 |
| cloud_warming_interaction | clear_solar_warming | 53 | 28 | capped_d2_no,runway_current_no | +93.8% | +42.1% | +40.1% | +1.25 | -1.31 |
| moisture_cloud_interaction | cloud_suppression | 19 | 15 | capped_d2_no,runway_current_no | +79.6% | +23.3% | +28.2% | -5.96 | -0.15 |
| cloud_warming_interaction | clear_but_not_warming | 62 | 30 | capped_d2_no,runway_current_no | +78.6% | +15.9% | +19.5% | -21.07 | -0.08 |
| marine_thermal_state | coastal_light_or_unclear_flow | 92 | 33 | capped_d2_no,runway_current_no | +89.2% | +35.0% | +36.8% | -7.31 | +0.18 |
| moisture_cloud_interaction | humid_cloud_suppression | 9 | 8 | runway_current_no | +73.5% | -22.7% | -23.7% | -2.32 | +0.42 |
| route_leg | capped_d2_no | 82 | 32 | capped_d2_no | +79.5% | +5.1% | +7.1% | -17.46 | +0.45 |
| forecast_peak_clock_state | forecast_peak_0_to_2h_ahead | 96 | 34 | capped_d2_no,runway_current_no | +92.1% | +25.0% | +27.8% | -12.65 | +1.17 |

## Route x Context Scenes

Helpful route/context buckets:

| route_leg | context_feature | context_bucket | rows | dates | avg_multiplier | baseline_roi | temp_roi | cost_delta_usd | pnl_delta_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| runway_current_no | moisture_cloud_interaction | mixed_moisture_cloud | 111 | 31 | +91.2% | +10.6% | +14.2% | -11.59 | +6.45 |
| runway_current_no | cloud_warming_interaction | mixed_sky_warming | 24 | 19 | +107.6% | +83.5% | +86.1% | +5.72 | +6.41 |
| runway_current_no | marine_thermal_state | coastal_direction_unknown_mixing | 27 | 22 | +85.6% | -59.5% | -56.6% | -5.74 | +4.79 |
| runway_current_no | marine_thermal_state | inland_wind_mixing | 51 | 26 | +95.5% | +37.6% | +41.7% | -1.31 | +3.89 |
| runway_current_no | forecast_peak_clock_state | forecast_peak_passed_0_to_1h | 21 | 18 | +63.6% | -34.8% | -38.0% | -10.82 | +3.16 |
| runway_current_no | moisture_cloud_interaction | dry_heat_inertia | 27 | 20 | +98.7% | +107.3% | +111.3% | +0.47 | +2.92 |
| runway_current_no | marine_thermal_state | offshore_or_parallel_warming_risk | 13 | 12 | +91.1% | +109.8% | +121.2% | -0.73 | +2.39 |
| runway_current_no | cloud_warming_interaction | cloud_limited_flat_or_cooling | 15 | 11 | +67.9% | -30.1% | -27.9% | -6.57 | +2.34 |
| runway_current_no | forecast_peak_clock_state | forecast_peak_2h_plus_ahead | 73 | 31 | +100.8% | +45.7% | +46.0% | +2.84 | +2.00 |
| runway_current_no | cloud_warming_interaction | cloud_warming_unknown | 36 | 23 | +94.3% | -2.8% | -0.1% | -1.06 | +1.96 |
| runway_current_no | forecast_peak_clock_state | forecast_peak_passed_1_to_2h | 7 | 7 | +55.0% | -87.0% | -87.0% | -1.96 | +1.70 |
| capped_d2_no | cloud_warming_interaction | mixed_sky_warming | 22 | 16 | +73.4% | -17.8% | -16.8% | -6.13 | +1.26 |
| runway_current_no | forecast_peak_clock_state | forecast_peak_0_to_2h_ahead | 79 | 31 | +93.0% | +25.8% | +28.6% | -10.55 | +1.18 |
| capped_d2_no | marine_thermal_state | coastal_light_or_unclear_flow | 27 | 18 | +79.3% | +3.1% | +8.7% | -5.03 | +0.97 |

Harmful route/context buckets:

| route_leg | context_feature | context_bucket | rows | dates | avg_multiplier | baseline_roi | temp_roi | cost_delta_usd | pnl_delta_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| runway_current_no | marine_thermal_state | onshore_marine_cooling_risk | 33 | 19 | +75.6% | +38.7% | +38.3% | -13.23 | -5.31 |
| runway_current_no | moisture_cloud_interaction | humid_convective_risk | 29 | 19 | +84.6% | +43.3% | +39.1% | -4.92 | -4.41 |
| runway_current_no | cloud_warming_interaction | mixed_sky_flat_or_cooling | 19 | 10 | +79.0% | +59.9% | +59.4% | -6.77 | -4.21 |
| runway_current_no | forecast_peak_clock_state | forecast_peak_passed_2h_plus | 9 | 7 | +55.2% | +110.0% | +110.0% | -2.81 | -3.09 |
| runway_current_no | cloud_warming_interaction | warming_through_cloud | 8 | 8 | +93.4% | +52.9% | +45.2% | -1.16 | -1.87 |
| capped_d2_no | cloud_warming_interaction | clear_solar_warming | 20 | 17 | +72.4% | +25.0% | +24.1% | -5.54 | -1.51 |
| runway_current_no | marine_thermal_state | coastal_light_or_unclear_flow | 65 | 32 | +93.3% | +41.0% | +41.1% | -2.28 | -0.80 |
| capped_d2_no | marine_thermal_state | inland_wind_mixing | 31 | 25 | +78.1% | +9.3% | +9.5% | -7.60 | -0.63 |
| runway_current_no | moisture_cloud_interaction | cloud_suppression | 13 | 12 | +78.5% | +30.9% | +36.1% | -4.93 | -0.43 |
| capped_d2_no | moisture_cloud_interaction | humid_convective_risk | 8 | 6 | +81.8% | +22.1% | +22.6% | -1.35 | -0.27 |
| capped_d2_no | cloud_warming_interaction | clear_but_not_warming | 8 | 8 | +89.6% | +44.6% | +46.8% | -0.83 | -0.21 |
| capped_d2_no | moisture_cloud_interaction | dry_heat_inertia | 16 | 13 | +79.7% | +12.0% | +14.2% | -3.89 | -0.14 |
| capped_d2_no | marine_thermal_state | onshore_marine_cooling_risk | 18 | 16 | +82.0% | +2.4% | +2.4% | -3.66 | -0.08 |
| capped_d2_no | cloud_warming_interaction | mixed_sky_flat_or_cooling | 7 | 5 | +94.2% | +29.5% | +30.5% | -0.40 | -0.05 |

## Interpretation

- The overlay mainly helps by reducing size in historically bad or weakly negative scenes without deleting them entirely.
- It helps most in `runway_current_no` scenes with mixed-sky warming / dry heat inertia / peak still ahead, and by trimming weak coastal-direction-unknown or near-past-peak rows.
- It hurts when it trims some profitable `onshore_marine_cooling_risk`, humid convective, and mixed-sky flat/cooling rows.  That says wind/ocean and cloud context still need route-specific calibration, not a single universal haircut.
- Wind/ocean context is still limited by missing wind direction in the broad historical feature layer; it is better as forward telemetry until direction coverage is stable.

## Gates

- significance=FAIL: temperature-context delta ROI CI crosses 0.
- baseline=FAIL: point estimate beats current `soft_balanced`, but date-block delta CI still crosses 0.
- forward=NA: no frozen-forward window evaluated for this overlay yet.
- conclusion=inconclusive_shadow_only.
