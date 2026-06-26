# Temperature Context Feature Layer V1

Generated: `2026-06-26T13:12:00+00:00`

## Verdict

`reheat_feature_factory_v1` is already the shared intraday temperature state layer, not just a reheat-specific table.  This pass adds general mechanism labels for cloud/warming, moisture/cloud, wind/ocean/geography, and forecast peak clock so the same context can be reused by current YES, current-bracket NO, d1/d2 NO, Range RV, and timing studies.

This is a feature/context layer, not a trading rule.  The labels are meant to explain and calibrate probability heads separately: current YES survive, current-bracket NO pass-through, and d1/d2 NO escape.

Coverage: `9800` city-date-hour state rows, `36` cities, `2026-05-19`..`2026-06-17`.

## Expression Matrix

| context_feature | context_bucket | state_rows | dates | cities | current_yes_win_rate | current_no_pass_through_rate | d1_hit_rate | d2_hit_rate | avg_decline_native | avg_trend1_f |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| moisture_cloud_interaction | mixed_moisture_cloud | 5216 | 30 | 36 | +56.3% | +43.7% | +15.6% | +10.6% | +0.88 | +0.44 |
| marine_thermal_state | inland_wind_mixing | 3898 | 30 | 14 | +47.6% | +52.4% | +15.6% | +11.2% | +0.94 | +0.61 |
| forecast_peak_clock_state | forecast_peak_2h_plus_ahead | 3453 | 29 | 36 | +13.6% | +86.4% | +21.9% | +22.8% | +0.24 | +1.72 |
| marine_thermal_state | coastal_light_or_unclear_flow | 3118 | 30 | 22 | +54.2% | +45.8% | +16.6% | +11.5% | +1.19 | +0.47 |
| forecast_peak_clock_state | forecast_peak_passed_2h_plus | 2787 | 30 | 36 | +87.9% | +12.1% | +3.4% | +0.8% | +2.62 | -1.01 |
| marine_thermal_state | coastal_direction_unknown_mixing | 2781 | 30 | 22 | +71.4% | +28.6% | +14.2% | +7.1% | +1.12 | -0.07 |
| cloud_warming_interaction | clear_but_not_warming | 2634 | 30 | 36 | +75.2% | +24.8% | +11.7% | +5.2% | +1.63 | -0.99 |
| cloud_warming_interaction | cloud_warming_unknown | 2359 | 30 | 23 | +57.7% | +42.3% | +11.1% | +9.2% | +1.00 | +0.37 |
| moisture_cloud_interaction | dry_heat_inertia | 1837 | 30 | 30 | +54.1% | +45.9% | +17.9% | +10.8% | +0.62 | +0.71 |
| forecast_peak_clock_state | forecast_peak_0_to_2h_ahead | 1833 | 30 | 36 | +58.4% | +41.6% | +29.6% | +8.5% | +0.30 | +0.75 |
| cloud_warming_interaction | clear_solar_warming | 1794 | 30 | 36 | +32.9% | +67.1% | +24.2% | +16.1% | +0.35 | +2.34 |
| moisture_cloud_interaction | humid_convective_risk | 1767 | 30 | 34 | +61.0% | +39.0% | +12.8% | +7.5% | +2.07 | -0.21 |
| cloud_warming_interaction | mixed_sky_flat_or_cooling | 1061 | 30 | 35 | +68.8% | +31.2% | +14.2% | +6.4% | +1.62 | -0.98 |
| forecast_peak_clock_state | forecast_peak_passed_0_to_1h | 888 | 30 | 36 | +86.5% | +13.5% | +10.0% | +2.1% | +0.75 | -0.24 |
| forecast_peak_clock_state | forecast_peak_passed_1_to_2h | 839 | 30 | 36 | +92.3% | +7.7% | +5.1% | +1.0% | +1.34 | -0.79 |
| cloud_warming_interaction | mixed_sky_warming | 839 | 30 | 34 | +32.4% | +67.6% | +23.5% | +19.0% | +0.29 | +2.16 |
| cloud_warming_interaction | cloud_limited_flat_or_cooling | 670 | 30 | 30 | +71.5% | +28.5% | +11.6% | +6.0% | +1.63 | -0.80 |
| moisture_cloud_interaction | cloud_suppression | 513 | 30 | 29 | +48.5% | +51.5% | +20.3% | +15.2% | +0.68 | +0.77 |
| moisture_cloud_interaction | humid_cloud_suppression | 464 | 29 | 24 | +59.3% | +40.7% | +10.6% | +6.9% | +1.60 | -0.02 |

## Feature Families

- `cloud_warming_interaction`: distinguishes clear solar warming, warming through cloud, cloud-limited flat/cooling, and mixed-sky regimes.
- `moisture_cloud_interaction`: separates humid cloud suppression, humid convective risk, dry heat inertia, and generic cloud suppression.
- `marine_thermal_state`: combines city geography, wind speed, and wind direction when available; if wind direction is missing it explicitly marks coastal direction unknown.
- `forecast_peak_clock_state`: turns forecast peak timing into reusable context rather than a hard gate.

## Boundary

- Wind direction is not yet broadly present in `reheat_feature_factory_v1`; this layer can consume it when present, otherwise it keeps `flow_unknown` instead of backfilling future archive data.
- The next production-quality step is to rename/promote the factory conceptually to `temperature_state_feature_factory`, while keeping old paths as compatibility aliases.
