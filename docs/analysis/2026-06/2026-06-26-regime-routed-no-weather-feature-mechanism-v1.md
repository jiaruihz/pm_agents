# Regime-Routed NO Weather Feature Mechanism V1

Generated: `2026-06-26T12:38:42+00:00`

## Verdict

现有版本不是没用风、云层、露点和升温动量，但用法偏粗：这些字段先变成 `intraday_state`、`moisture_cloud_regime`、`wind_regime`、`running_max_state`，再进入 `soft_balanced`。真钱 sizing 里真正直接扣权重的是 `weather_multiplier`，目前主要惩罚 humid city family、mature_fade 和 unknown weather；它没有把风向、海风、具体云层/露点强度做成连续权重。

所以结论是：基础字段已经在，但天气机制层还不够完整。风向/沿海上下文适合作为 shared feature + shadow sizing 先记录；云层/湿度/露点/风速更应该做连续校准，而不是继续加一堆 hard gate。

Denominator: `271` rows, `35` cities, `2026-05-20`..`2026-06-23`.

## Policy Ablation

| weight_policy | rows | dates | cities | exec_rows | exec_dates | hit_rate | roi | exec_roi | avg_weight |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full_size | 271 | 35 | 35 | 271 | 35 | +51.7% | +11.5% | +11.5% | +100.0% |
| soft_without_weather_multiplier | 271 | 35 | 35 | 82 | 31 | +51.7% | +26.4% | +63.8% | +36.6% |
| soft_balanced | 271 | 35 | 35 | 77 | 31 | +51.7% | +26.3% | +69.9% | +34.6% |
| soft_wind_only | 271 | 35 | 35 | 76 | 31 | +51.7% | +26.0% | +71.3% | +33.7% |
| soft_wind_context | 271 | 35 | 35 | 75 | 31 | +51.7% | +25.9% | +73.0% | +33.5% |

## Most Informative Slices

| feature | bucket | rows | dates | cities | hit_rate | full_roi | soft_exec_rows | soft_exec_roi | avg_soft_weight |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| running_max_state | mature_fade | 25 | 17 | 18 | +64.0% | +78.1% | 10 | +138.9% | +0.38 |
| intraday_state | mature_fade | 11 | 11 | 10 | +45.5% | +72.0% | 6 | +145.8% | +0.39 |
| moisture_cloud_regime | humid_overcast_suppression | 9 | 8 | 5 | +33.3% | -42.1% | 0 | NA | +0.27 |
| coastal_flow_state | flow_unknown | 71 | 31 | 18 | +38.0% | -30.7% | 22 | -20.2% | +0.36 |
| dewpoint_dep_bucket | dewdep_gt25 | 44 | 24 | 17 | +56.8% | +52.0% | 16 | +168.6% | +0.37 |
| moisture_cloud_regime | dry_heat_inertia | 43 | 23 | 17 | +58.1% | +51.6% | 15 | +164.8% | +0.37 |
| running_max_state | pullback_from_high | 25 | 15 | 19 | +32.0% | -28.3% | 12 | +15.9% | +0.41 |
| moisture_cloud_regime | humid_convective_risk | 37 | 21 | 21 | +56.8% | +44.2% | 13 | +79.2% | +0.35 |
| wind_regime | windy_mixing_noise | 12 | 9 | 6 | +75.0% | +42.2% | 3 | +110.3% | +0.36 |
| rh_bucket | rh_gt85 | 29 | 20 | 15 | +51.7% | +41.3% | 8 | +91.2% | +0.32 |
| coastal_flow_state | offshore_or_parallel_flow | 47 | 28 | 15 | +57.4% | +40.5% | 12 | +159.8% | +0.33 |
| trend1_bucket | t1_flat_up | 16 | 14 | 5 | +68.8% | +39.3% | 6 | +135.5% | +0.38 |
| wind_speed_bucket | wind_ge18 | 10 | 9 | 5 | +80.0% | +35.0% | 2 | +41.3% | +0.35 |
| intraday_state | false_fade_risk | 30 | 20 | 22 | +56.7% | +34.9% | 12 | +82.4% | +0.41 |
| dewpoint_dep_bucket | dewdep_15_25 | 100 | 33 | 28 | +44.0% | -11.7% | 30 | +14.5% | +0.35 |
| rh_bucket | rh_le45 | 51 | 24 | 17 | +51.0% | +34.1% | 16 | +168.6% | +0.36 |
| trend1_bucket | t1_fast_warming | 27 | 19 | 14 | +51.9% | -11.0% | 4 | -34.4% | +0.27 |
| wind_speed_bucket | wind_10_18 | 72 | 31 | 26 | +41.7% | -9.7% | 18 | +54.1% | +0.34 |
| sky_bucket | nan | 55 | 29 | 16 | +41.8% | -9.6% | 16 | +3.6% | +0.34 |
| trend3_bucket | t3_warming | 73 | 29 | 31 | +46.6% | -9.1% | 24 | +16.4% | +0.37 |

## How Current Model Uses These Inputs

- `temp_trend_1h_f/temp_trend_3h_f` feed `intraday_state`; this is already live-parity gated.
- `relative_humidity_pct/sky_cover_code/dewpoint_depression_f` feed `moisture_cloud_regime`; direct sizing impact is currently weak unless the weather label is unknown.
- `wind_speed_kt` feeds `wind_regime`; direct sizing impact is currently weak unless the weather label is unknown.
- `wind_dir_deg/wind_sector/geo_context/coastal_flow_state` are now shared context features and runner shadow telemetry, not live sizing.

## Boundary

- This report is explanatory, not a new trading rule.
- The next clean step is frozen replay with live-available fields only, then decide whether any continuous weather context belongs in `soft_balanced`.
