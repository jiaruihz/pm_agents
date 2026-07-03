# Regime-Routed NO V1 vs V2 Diff Review

## 结论

v2 比最早的原始混合 v1 低，主要不是因为 fresh/reheat 机制本身差，而是因为 v2 把 `unapproved_stale_current_no` 从主策略里拿掉了。这个尾部组只有 35 笔、胜率 37.1%，但平均 ask 只有 0.385，靠少数便宜 NO 打出高赔率，给 v1 贡献了正 PnL。

- 原始混合 v1：271 笔，full ROI +11.5%，weighted ROI +26.3%。
- 机制拆分 v2：236 笔，full ROI +10.8%，weighted ROI +25.0%。
- v1 有、v2 没有的 35 笔：full PnL $+28.34，weighted PnL $+21.92，weighted ROI +34.1%。
- fresh-only 到 v2 加回的 false-fade/reheat 43 笔：full PnL $+63.90，weighted PnL $+30.57，weighted ROI +34.8%。

所以这里不是简单“v2 优化后变差”。更准确是：v2 把旧 v1 的便宜尾部票剥离了，牺牲了一点历史点估和 forward 3 天点估，但减少了把 stale/pullback 误叫 runway 的语义错误。

## Diff Summary

| slice | rows | dates | cities | wins | win_rate | avg_ask | pnl_usd | roi | weighted_pnl_usd | weighted_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| original_mixed_v1 | 271 | 35 | 35 | 140 | +51.7% | 0.510 | $+155.73 | +11.5% | $+123.21 | +26.3% |
| mechanism_split_v2 | 236 | 35 | 34 | 127 | +53.8% | 0.529 | $+127.39 | +10.8% | $+101.30 | +25.0% |
| v1_only_excluded_from_v2 | 35 | 29 | 19 | 13 | +37.1% | 0.385 | $+28.34 | +16.2% | $+21.92 | +34.1% |
| fresh_only_to_v2_added_false_fade_reheat | 43 | 23 | 26 | 25 | +58.1% | 0.501 | $+63.90 | +29.7% | $+30.57 | +34.8% |

## Removed From V2 By State

| running_max_state | intraday_state | rows | dates | cities | wins | win_rate | avg_ask | pnl_usd | roi | weighted_pnl_usd | weighted_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pullback_from_high | pullback_uncertain | 7 | 7 | 5 | 0 | +0.0% | 0.316 | $-35.00 | -100.0% | $-12.75 | -100.0% |
| stalled_high | plateau_near_high | 13 | 12 | 9 | 5 | +38.5% | 0.477 | $-13.72 | -21.1% | $-6.01 | -28.0% |
| running_max_clock_unknown | slow_warming | 2 | 2 | 2 | 2 | +100.0% | 0.485 | $+14.13 | +141.3% | $+9.60 | +174.3% |
| running_max_clock_unknown | active_warming | 2 | 2 | 2 | 1 | +50.0% | 0.370 | $+23.33 | +233.3% | $+8.45 | +244.8% |
| mature_fade | mature_fade | 11 | 11 | 10 | 5 | +45.5% | 0.304 | $+39.60 | +72.0% | $+22.63 | +106.8% |

## Removed From V2 By Ask Bucket

| ask_bucket | rows | wins | win_rate | avg_ask | pnl_usd | roi | weighted_pnl_usd | weighted_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.35-0.50 | 11 | 3 | +27.3% | 0.427 | $-18.82 | -34.2% | $-7.43 | -39.2% |
| 0.50-0.70 | 7 | 4 | +57.1% | 0.610 | $-2.24 | -6.4% | $+0.46 | +4.1% |
| <=0.20 | 2 | 1 | +50.0% | 0.130 | $+23.33 | +233.3% | $+8.10 | +212.7% |
| 0.20-0.35 | 15 | 5 | +33.3% | 0.282 | $+26.06 | +34.7% | $+20.79 | +68.4% |

## Daily Delta

| target_date | diff_group | rows | wins | win_rate | avg_ask | pnl_usd | roi | weighted_pnl_usd | weighted_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-31 | v1_only_excluded_from_v2 | 2 | 0 | +0.0% | 0.310 | $-10.00 | -100.0% | $-3.67 | -100.0% |
| 2026-06-05 | v1_only_excluded_from_v2 | 2 | 0 | +0.0% | 0.366 | $-10.00 | -100.0% | $-3.37 | -100.0% |
| 2026-06-11 | v1_only_excluded_from_v2 | 2 | 0 | +0.0% | 0.225 | $-10.00 | -100.0% | $-5.98 | -100.0% |
| 2026-06-21 | v1_only_excluded_from_v2 | 2 | 0 | +0.0% | 0.515 | $-10.00 | -100.0% | $-3.42 | -100.0% |
| 2026-05-21 | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.430 | $-5.00 | -100.0% | $-1.58 | -100.0% |
| 2026-05-22 | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.310 | $-5.00 | -100.0% | $-0.83 | -100.0% |
| 2026-05-23 | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.500 | $-5.00 | -100.0% | $-1.67 | -100.0% |
| 2026-05-24 | fresh_only_to_v2_added_false_fade_reheat | 1 | 0 | +0.0% | 0.580 | $-5.00 | -100.0% | $-1.88 | -100.0% |
| 2026-05-25 | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.550 | $-5.00 | -100.0% | $-0.94 | -100.0% |
| 2026-05-25 | fresh_only_to_v2_added_false_fade_reheat | 1 | 0 | +0.0% | 0.470 | $-5.00 | -100.0% | $-1.51 | -100.0% |
| 2026-05-27 | fresh_only_to_v2_added_false_fade_reheat | 1 | 0 | +0.0% | 0.350 | $-5.00 | -100.0% | $-3.06 | -100.0% |
| 2026-05-28 | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.290 | $-5.00 | -100.0% | $-0.70 | -100.0% |
| 2026-06-01 | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.320 | $-5.00 | -100.0% | $-1.78 | -100.0% |
| 2026-06-02 | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.330 | $-5.00 | -100.0% | $-3.76 | -100.0% |
| 2026-06-07 | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.350 | $-5.00 | -100.0% | $-2.12 | -100.0% |
| 2026-06-07 | fresh_only_to_v2_added_false_fade_reheat | 1 | 0 | +0.0% | 0.430 | $-5.00 | -100.0% | $-1.19 | -100.0% |
| 2026-06-08 | fresh_only_to_v2_added_false_fade_reheat | 1 | 0 | +0.0% | 0.400 | $-5.00 | -100.0% | $-2.60 | -100.0% |
| 2026-06-09 | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.670 | $-5.00 | -100.0% | $-1.46 | -100.0% |
| 2026-06-10 | fresh_only_to_v2_added_false_fade_reheat | 1 | 0 | +0.0% | 0.490 | $-5.00 | -100.0% | $-2.31 | -100.0% |
| 2026-06-13 | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.373 | $-5.00 | -100.0% | $-1.82 | -100.0% |
| 2026-06-14 | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.427 | $-5.00 | -100.0% | $-0.67 | -100.0% |
| 2026-06-17 | fresh_only_to_v2_added_false_fade_reheat | 1 | 0 | +0.0% | 0.380 | $-5.00 | -100.0% | $-2.40 | -100.0% |
| 2026-06-19 | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.260 | $-5.00 | -100.0% | $-0.75 | -100.0% |
| 2026-06-22 | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.220 | $-5.00 | -100.0% | $-0.61 | -100.0% |
| 2026-06-22 | fresh_only_to_v2_added_false_fade_reheat | 1 | 0 | +0.0% | 0.638 | $-5.00 | -100.0% | $-0.30 | -100.0% |
| 2026-06-23 | fresh_only_to_v2_added_false_fade_reheat | 1 | 0 | +0.0% | 0.661 | $-5.00 | -100.0% | $-1.47 | -100.0% |
| 2026-05-20 | fresh_only_to_v2_added_false_fade_reheat | 2 | 1 | +50.0% | 0.638 | $-2.75 | -27.5% | $-0.54 | -22.3% |
| 2026-06-20 | fresh_only_to_v2_added_false_fade_reheat | 2 | 1 | +50.0% | 0.391 | $-1.83 | -18.3% | $-1.93 | -44.3% |
| 2026-06-17 | v1_only_excluded_from_v2 | 2 | 1 | +50.0% | 0.524 | $-1.36 | -13.6% | $-1.50 | -43.5% |
| 2026-06-12 | fresh_only_to_v2_added_false_fade_reheat | 5 | 2 | +40.0% | 0.480 | $-0.00 | -0.0% | $+3.55 | +37.9% |

## City Delta

| city | diff_group | rows | wins | win_rate | avg_ask | pnl_usd | roi | weighted_pnl_usd | weighted_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Jeddah | fresh_only_to_v2_added_false_fade_reheat | 3 | 0 | +0.0% | 0.473 | $-15.00 | -100.0% | $-5.46 | -100.0% |
| TelAviv | fresh_only_to_v2_added_false_fade_reheat | 3 | 0 | +0.0% | 0.487 | $-15.00 | -100.0% | $-7.69 | -100.0% |
| CapeTown | v1_only_excluded_from_v2 | 4 | 1 | +25.0% | 0.590 | $-10.91 | -54.5% | $-2.31 | -37.2% |
| Lucknow | fresh_only_to_v2_added_false_fade_reheat | 2 | 0 | +0.0% | 0.365 | $-10.00 | -100.0% | $-4.02 | -100.0% |
| Wuhan | fresh_only_to_v2_added_false_fade_reheat | 3 | 1 | +33.3% | 0.513 | $-6.94 | -46.2% | $-4.16 | -71.0% |
| Helsinki | v1_only_excluded_from_v2 | 4 | 1 | +25.0% | 0.373 | $-6.84 | -34.2% | $-3.66 | -62.1% |
| Atlanta | fresh_only_to_v2_added_false_fade_reheat | 1 | 0 | +0.0% | 0.440 | $-5.00 | -100.0% | $-2.36 | -100.0% |
| Beijing | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.470 | $-5.00 | -100.0% | $-2.32 | -100.0% |
| Chongqing | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.290 | $-5.00 | -100.0% | $-0.70 | -100.0% |
| Houston | fresh_only_to_v2_added_false_fade_reheat | 1 | 0 | +0.0% | 0.420 | $-5.00 | -100.0% | $-1.71 | -100.0% |
| Jeddah | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.440 | $-5.00 | -100.0% | $-1.75 | -100.0% |
| SanFrancisco | fresh_only_to_v2_added_false_fade_reheat | 1 | 0 | +0.0% | 0.562 | $-5.00 | -100.0% | $-0.94 | -100.0% |
| SaoPaulo | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.430 | $-5.00 | -100.0% | $-1.58 | -100.0% |
| SaoPaulo | fresh_only_to_v2_added_false_fade_reheat | 1 | 0 | +0.0% | 0.350 | $-5.00 | -100.0% | $-3.06 | -100.0% |
| Taipei | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.303 | $-5.00 | -100.0% | $-0.96 | -100.0% |
| Tokyo | v1_only_excluded_from_v2 | 1 | 0 | +0.0% | 0.370 | $-5.00 | -100.0% | $-1.76 | -100.0% |
| TelAviv | v1_only_excluded_from_v2 | 4 | 1 | +25.0% | 0.245 | $-3.33 | -16.7% | $+3.03 | +32.1% |
| Chengdu | fresh_only_to_v2_added_false_fade_reheat | 2 | 1 | +50.0% | 0.535 | $-2.75 | -27.5% | $-2.01 | -61.2% |
| Amsterdam | v1_only_excluded_from_v2 | 2 | 1 | +50.0% | 0.495 | $-2.42 | -24.2% | $-2.68 | -45.8% |
| Istanbul | fresh_only_to_v2_added_false_fade_reheat | 2 | 1 | +50.0% | 0.544 | $-2.41 | -24.1% | $-0.04 | -1.1% |
| NYC | fresh_only_to_v2_added_false_fade_reheat | 2 | 1 | +50.0% | 0.644 | $-2.31 | -23.1% | $+0.55 | +29.2% |
| Busan | v1_only_excluded_from_v2 | 2 | 1 | +50.0% | 0.419 | $-1.36 | -13.6% | $+0.07 | +3.8% |
| Ankara | fresh_only_to_v2_added_false_fade_reheat | 1 | 1 | +100.0% | 0.690 | $+2.25 | +44.9% | $+0.58 | +44.9% |
| Helsinki | fresh_only_to_v2_added_false_fade_reheat | 1 | 1 | +100.0% | 0.690 | $+2.25 | +44.9% | $+0.82 | +44.9% |
| Denver | fresh_only_to_v2_added_false_fade_reheat | 1 | 1 | +100.0% | 0.670 | $+2.46 | +49.3% | $+1.09 | +49.3% |
| Guangzhou | v1_only_excluded_from_v2 | 1 | 1 | +100.0% | 0.670 | $+2.46 | +49.3% | $+0.87 | +49.3% |
| Guangzhou | fresh_only_to_v2_added_false_fade_reheat | 1 | 1 | +100.0% | 0.650 | $+2.69 | +53.8% | $+0.72 | +53.8% |
| Wuhan | v1_only_excluded_from_v2 | 2 | 1 | +50.0% | 0.365 | $+2.82 | +28.2% | $-1.40 | -25.1% |
| CapeTown | fresh_only_to_v2_added_false_fade_reheat | 1 | 1 | +100.0% | 0.620 | $+3.06 | +61.3% | $+1.10 | +61.3% |
| Shanghai | fresh_only_to_v2_added_false_fade_reheat | 1 | 1 | +100.0% | 0.612 | $+3.17 | +63.4% | $+0.94 | +63.4% |
| Busan | fresh_only_to_v2_added_false_fade_reheat | 1 | 1 | +100.0% | 0.580 | $+3.62 | +72.4% | $+1.27 | +72.4% |
| Singapore | v1_only_excluded_from_v2 | 1 | 1 | +100.0% | 0.490 | $+5.20 | +104.1% | $+2.60 | +104.1% |
| Tokyo | fresh_only_to_v2_added_false_fade_reheat | 1 | 1 | +100.0% | 0.460 | $+5.87 | +117.4% | $+0.76 | +117.4% |
| Wellington | fresh_only_to_v2_added_false_fade_reheat | 1 | 1 | +100.0% | 0.440 | $+6.36 | +127.3% | $+4.87 | +127.3% |
| Warsaw | fresh_only_to_v2_added_false_fade_reheat | 1 | 1 | +100.0% | 0.430 | $+6.63 | +132.6% | $+3.31 | +132.6% |
| Shanghai | v1_only_excluded_from_v2 | 3 | 1 | +33.3% | 0.289 | $+7.73 | +51.5% | $+6.38 | +185.0% |
| Austin | fresh_only_to_v2_added_false_fade_reheat | 1 | 1 | +100.0% | 0.390 | $+7.82 | +156.4% | $+6.53 | +156.4% |
| Miami | fresh_only_to_v2_added_false_fade_reheat | 3 | 3 | +100.0% | 0.647 | $+8.38 | +55.8% | $+3.31 | +59.1% |
| Amsterdam | fresh_only_to_v2_added_false_fade_reheat | 1 | 1 | +100.0% | 0.280 | $+12.86 | +257.1% | $+8.03 | +257.1% |
| Denver | v1_only_excluded_from_v2 | 1 | 1 | +100.0% | 0.280 | $+12.86 | +257.1% | $+9.71 | +257.1% |

## Worst Removed Cases

| target_date | city | ask | payoff | stake_profit_usd | weighted_profit_usd | running_max_state | intraday_state | minutes_since_running_max | forecast_peak_delta_hours_local | forecast_gap_to_running_native | temp_trend_1h_f | temp_trend_3h_f | wind_regime | moisture_cloud_regime |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-05 | Taipei | 0.303 | 0.0 | $-5.00 | $-0.96 | mature_fade | mature_fade | 210.88333333333333 | 1.0 | 1.6666666666666643 | 0.0 | 0.0 | light_wind | humid_convective_risk |
| 2026-06-19 | Busan | 0.26 | 0.0 | $-5.00 | $-0.75 | pullback_from_high | pullback_uncertain | 91.0 | 2.0 | 1.0 | -3.6000000000000085 | -1.7999999999999972 | light_wind | humid_convective_risk |
| 2026-06-17 | Beijing | 0.47 | 0.0 | $-5.00 | $-2.32 | stalled_high | plateau_near_high | 21450.883333333335 | -1.0 | 1.0 | 0.0 | 1.7999999999999972 | light_wind | mixed_moisture |
| 2026-06-14 | Shanghai | 0.427 | 0.0 | $-5.00 | $-0.67 | stalled_high | plateau_near_high | 1110.8833333333334 | 2.0 | 0.5000000000000036 | 0.0 | 0.0 | moderate_wind | humid_overcast_suppression |
| 2026-06-13 | Helsinki | 0.373 | 0.0 | $-5.00 | $-1.82 | pullback_from_high | pullback_uncertain | 70.88333333333334 | 1.0 | 1.0555555555555571 | -1.7999999999999972 | -1.7999999999999972 | light_wind | humid_convective_risk |
| 2026-06-11 | Wuhan | 0.34 | 0.0 | $-5.00 | $-3.95 | stalled_high | plateau_near_high | 6750.883333333333 | -4.0 | 0.7222222222222214 | 0.0 | 3.5999999999999943 | light_wind | mixed_moisture |
| 2026-06-11 | TelAviv | 0.11 | 0.0 | $-5.00 | $-2.02 | pullback_from_high | pullback_uncertain | 40.88333333333333 | 1.0 | 0.6666666666666679 | 0.0 | 0.0 | moderate_wind | mixed_moisture |
| 2026-06-09 | CapeTown | 0.67 | 0.0 | $-5.00 | $-1.46 | stalled_high | plateau_near_high | 2791.883333333333 | 0.0 | 1.166666666666668 | 0.0 | 1.8000000000000045 | light_wind | mixed_moisture |
| 2026-06-07 | NYC | 0.35 | 0.0 | $-5.00 | $-2.12 | pullback_from_high | pullback_uncertain | 99.88333333333334 | -1.0 | 3.4000000000000057 | -2.0 | -1.0 | light_wind | mixed_moisture |
| 2026-06-22 | Shanghai | 0.22 | 0.0 | $-5.00 | $-0.61 | mature_fade | mature_fade | 660.8833333333333 | 4.0 | 0.7111111111111121 | 0.0 | 0.0 | moderate_wind | humid_overcast_suppression |
| 2026-06-21 | CapeTown | 0.59 | 0.0 | $-5.00 | $-1.67 | running_max_clock_unknown | active_warming | nan | 0.0 | 1.477777777777778 | 1.7999999999999972 | 7.199999999999989 | light_wind | mixed_moisture |
| 2026-06-05 | Helsinki | 0.429 | 0.0 | $-5.00 | $-2.40 | pullback_from_high | pullback_uncertain | 100.88333333333334 | -6.0 | 1.5555555555555536 | 0.0 | -1.7999999999999972 | light_wind | mixed_moisture |
| 2026-06-21 | Jeddah | 0.44 | 0.0 | $-5.00 | $-1.75 | pullback_from_high | pullback_uncertain | 90.88333333333334 | 0.0 | 3.1000000000000014 | -1.7999999999999972 | 0.0 | light_wind | mixed_moisture |
| 2026-06-01 | TelAviv | 0.32 | 0.0 | $-5.00 | $-1.78 | mature_fade | mature_fade | 1480.8833333333334 | 1.0 | 1.166666666666668 | 0.0 | 0.0 | moderate_wind | mixed_moisture |
| 2026-05-31 | Tokyo | 0.37 | 0.0 | $-5.00 | $-1.76 | stalled_high | plateau_near_high | 1381.8833333333334 | 0.0 | 1.8333333333333357 | 0.0 | 5.3999999999999915 | light_wind | mixed_moisture |

## Best Removed Cases

| target_date | city | ask | payoff | stake_profit_usd | weighted_profit_usd | running_max_state | intraday_state | minutes_since_running_max | forecast_peak_delta_hours_local | forecast_gap_to_running_native | temp_trend_1h_f | temp_trend_3h_f | wind_regime | moisture_cloud_regime |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-20 | Lucknow | 0.15 | 1.0 | $+28.33 | $+10.12 | running_max_clock_unknown | active_warming | nan | 0.0 | 3.444444444444443 | 1.7999999999999972 | 7.199999999999989 | light_wind | dry_heat_inertia |
| 2026-06-23 | NYC | 0.21 | 1.0 | $+18.81 | $+9.71 | mature_fade | mature_fade | 519.3666666666667 | -8.0 | 4.299999999999997 | 0.0 | -1.0 | light_wind | humid_convective_risk |
| 2026-05-20 | Shanghai | 0.22 | 1.0 | $+17.73 | $+7.67 | mature_fade | mature_fade | 1170.8833333333334 | -3.0 | 1.9444444444444464 | 0.0 | -3.5999999999999943 | light_wind | humid_convective_risk |
| 2026-05-29 | Seattle | 0.25 | 1.0 | $+15.00 | $+2.34 | mature_fade | mature_fade | 630.8833333333333 | 11.0 | 1.3999999999999986 | -2.0 | -2.0 | light_wind | humid_convective_risk |
| 2026-06-06 | Denver | 0.28 | 1.0 | $+12.86 | $+9.71 | mature_fade | mature_fade | 212.88333333333333 | -2.0 | 2.200000000000003 | -1.7999999999999972 | -3.799999999999997 | light_wind | dry_heat_inertia |
| 2026-06-03 | TelAviv | 0.3 | 1.0 | $+11.67 | $+8.73 | running_max_clock_unknown | slow_warming | nan | -3.0 | 0.6666666666666679 | -1.7999999999999972 | 5.400000000000006 | light_wind | dry_heat_inertia |
| 2026-06-15 | Helsinki | 0.38 | 1.0 | $+8.16 | $+1.39 | stalled_high | plateau_near_high | 4090.6 | 3.0 | 1.4444444444444464 | -1.7999999999999972 | 1.7999999999999972 | moderate_wind | humid_convective_risk |
| 2026-06-16 | Wuhan | 0.39 | 1.0 | $+7.82 | $+2.55 | stalled_high | plateau_near_high | 1231.1166666666666 | -1.0 | 1.6111111111111107 | 0.0 | 3.5999999999999943 | light_wind | mixed_moisture |
| 2026-06-20 | Singapore | 0.49 | 1.0 | $+5.20 | $+2.60 | mature_fade | mature_fade | 120.3 | -2.0 | 0.6999999999999993 | -1.8000000000000114 | 0.0 | light_wind | humid_convective_risk |
| 2026-06-12 | CapeTown | 0.55 | 1.0 | $+4.09 | $+1.75 | stalled_high | plateau_near_high | 2850.883333333333 | -1.0 | 1.0 | 0.0 | 3.5999999999999943 | moderate_wind | mixed_moisture |
| 2026-06-17 | Busan | 0.579 | 1.0 | $+3.64 | $+0.82 | stalled_high | plateau_near_high | 1231.2 | -1.0 | 2.2222222222222214 | 0.0 | 0.0 | moderate_wind | mixed_moisture |
| 2026-06-04 | Amsterdam | 0.66 | 1.0 | $+2.58 | $+1.08 | stalled_high | plateau_near_high | 1175.8833333333334 | -3.0 | 0.9444444444444464 | 0.0 | 1.8000000000000045 | windy_mixing_noise | mixed_moisture |
| 2026-05-26 | Guangzhou | 0.67 | 1.0 | $+2.46 | $+0.87 | running_max_clock_unknown | slow_warming | nan | -2.0 | 1.1111111111111072 | 0.0 | 3.5999999999999943 | light_wind | mixed_moisture |
| 2026-06-19 | Busan | 0.26 | 0.0 | $-5.00 | $-0.75 | pullback_from_high | pullback_uncertain | 91.0 | 2.0 | 1.0 | -3.6000000000000085 | -1.7999999999999972 | light_wind | humid_convective_risk |
| 2026-06-11 | Wuhan | 0.34 | 0.0 | $-5.00 | $-3.95 | stalled_high | plateau_near_high | 6750.883333333333 | -4.0 | 0.7222222222222214 | 0.0 | 3.5999999999999943 | light_wind | mixed_moisture |

## 机制判断

- `pullback_from_high / pullback_uncertain` 是明确坏形态：7 笔全输，应继续排除。
- `stalled_high / plateau_near_high` 也不支持主策略：13 笔 ROI 为负，像是真的封顶/停滞。
- `mature_fade / mature_fade` 和 `running_max_clock_unknown` 历史点估好，但样本小、胜率低或 clock 缺失，不应该直接并回 live；它更像低价 tail NO 或数据 freshness/clock 语义问题。
- `false_fade/reheat` 是更像机制的一组，但 6/21..6/23 forward 两笔全错，因此只能 shadow，不进 live。

## 需要调整的地方

1. 策略层：保留 v2 的 route 分账，不把 removed 35 笔并回 `runway_current_no`。
2. 数据层：`running_max_clock_unknown` 不能作为 live 交易态，应单独记录 clock/freshness 缺失原因；如果 live 里出现 unknown 但仍能下单，这是数据链路问题。
3. 机制层：单独研究 `cheap stale current-NO tail`，但它的目标不是午后 runway，而是低价 NO tail 的市场结构/赔率问题。
4. shadow 层：继续记录 `false_fade_reheat_current_no`，但必须看更多 forward 日期后再决定是否进入真钱 sizing。
