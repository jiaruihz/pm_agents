# Late-Window Residual Trade-Win v2

Status: snapshot
Date: 2026-07-13 correction rerun
Scope: research-only; no live/shadow runner changes

## Data Snapshot
- Synced Mac market data before run; snapshots seen: 647.
- Candidate rows generated: 10874; settled d1 rows: 3177.
- Settlement coverage 2026-07-05..07: {'2026-07-01': {'cities': 47, 'rows': 517}, '2026-07-02': {'cities': 47, 'rows': 517}, '2026-07-03': {'cities': 47, 'rows': 517}, '2026-07-04': {'cities': 47, 'rows': 446}, '2026-07-05': {'cities': 47, 'rows': 517}, '2026-07-06': {'cities': 47, 'rows': 515}, '2026-07-07': {'cities': 47, 'rows': 517}, '2026-07-08': {'cities': 47, 'rows': 517}}.
- Forecast hourly curve records: 54636; observation history rows: 9961.
- TAF history, vertical profile history, and PIT multi-model forecast history were not present in the local market-data mirror; v2 keeps their columns as unavailable rather than leaking current/future data.
- 2026-07-13 correction: `entry_price` is already the executable NO ask, so the market p(NO win) baseline now uses it directly instead of `1-entry_price`.
- 2026-07-13 correction: Stage B now estimates `P(stop exact | touch)` on touched rows; `P(final exact)=P(touch)*P(stop exact | touch)`. The corrected output has zero `P(final exact)>P(touch)` violations.

## Verdict
- Chengdu 2026-07-07 37 NO is the named failure mode: at the nearest 15:32 BJ snapshot, the incident scorer reported `p_leg_win_physical_v1` p(NO win)≈0.951; live entry followed at 2026-07-07 15:39:00.
- The script's `old_v1_like` column is a retrained single-stage baseline on this per-poll frame, not the exact old production/research scorer.
- Incident context says the next key METAR was 2026-07-07 16:00:00 and touched 37C; historical paper snapshots imply touch/final-exact but do not preserve the raw first-touch minute for the 15:32 row.
- v2 full model at that snapshot: P(touch 37 after decision)=0.652, P(final exactly 37)=0.545, P(37 NO win)=0.455.
- v1_original_retrained_p_no_win=0.507; this is the v1 feature framework retrained on the same v2 per-poll d1 NO rows.
- old_v1_like_p_no_win=0.485; raw physical score=0.450; market_p_no_win=0.940.
- full_v2 forward d1 NO no_win calibration: rows=2832, dates=7, logloss=0.4085, Brier=0.1291, AUC=0.7280.
- corrected executable market baseline: rows=1474, dates=9, logloss=0.2072, Brier=0.0635, AUC=0.9194; it remains materially stronger than the physical heads.
- v1 original framework retrained on the same v2 d1 rows: logloss=0.5424, Brier=0.1771, AUC=0.7833.
- old v1-like forward no_win: logloss=0.5336, Brier=0.1750, AUC=0.7858; p_leg_win_physical_v1 should be treated as raw physical score, not calibrated trade probability.
- full_v2 edge>=0 executable replay: rows=280, dates=7, ROI=0.0%, date-block CI [-7.9%,7.6%].
- v1 original retrained edge>=0 replay: rows=92, dates=8, ROI=3.0%, date-block CI [-8.3%,19.1%].
- old v1-like edge>=0 replay: rows=98, dates=8, ROI=-1.2%, date-block CI [-16.2%,11.4%].
- Strategy-model verdict: on the same v2 per-poll d1 NO rows, the v1 original single-stage framework currently beats full_v2 on proper score and edge replay; use the two-stage touch/exact decomposition as a hazard feature layer, not as the final trade probability head yet.

Conclusion gates: significance=FAIL/NA, baseline=FAIL/NA, forward=FAIL; conclusion=inconclusive_research_only.

## Model Design
- Grain: polling snapshot x city x target_date x candidate leg/token.
- Main model: d1 NO only. d2/d3/current YES are retained in candidate frame for diagnostics but not mixed into the main target.
- Stage A predicts `P(touch target after decision)`; Stage B predicts `P(stop exact | touch)` only on touched training rows; `P(final exact)=P(touch)*P(stop exact | touch)` and `p_trade_win_v2=1-P(final exact)` for NO.
- Fair baseline: `v1_original_retrained` uses the original v1 physical feature framework, retrained on the same v2 per-poll d1 NO rows.
- City identity is not a raw model input. City information enters only through prior late-reheat/overshoot rates and region bucket.

## Available PIT Features
- paper snapshot/orderbook fields: entry, bid, spread, depth, running high, latest temp, forecast peak clock.
- observation timing features inferred from historical snapshot-visible METAR report times: cadence, minutes_to_next_obs, minutes_since_running_max, same_running_max_obs_count.
- Open-Meteo hourly curve temperature context is available; cloud/CAPE/CIN/LI/BLH/shortwave/wind are not present in local historical curve files.
- TAF and vertical profile fields are present as explicit unavailable columns; they are not backfilled from non-PIT sources.

## Metrics
| model | target | scope | rows | dates | base_rate | logloss | brier | auc | avg_p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base_physical_only | touch | forward_expanding | 3048 | 8 | 0.1234 | 0.5254 | 0.1709 | 0.8080 | 0.3485 |
| base_physical_only | stop_exact_given_touch | forward_expanding_touched_only | 348 | 7 | 0.8678 | 0.6320 | 0.2160 | 0.6211 | 0.6319 |
| base_physical_only | final_exact | forward_expanding | 2832 | 7 | 0.1066 | 0.3734 | 0.1180 | 0.7494 | 0.2304 |
| base_physical_only | no_win | forward_expanding | 2832 | 7 | 0.8934 | 0.3734 | 0.1180 | 0.7494 | 0.7696 |
| obs_clock_cadence | touch | forward_expanding | 3048 | 8 | 0.1234 | 0.5472 | 0.1782 | 0.7953 | 0.3488 |
| obs_clock_cadence | stop_exact_given_touch | forward_expanding_touched_only | 348 | 7 | 0.8678 | 0.6111 | 0.1995 | 0.6009 | 0.6771 |
| obs_clock_cadence | final_exact | forward_expanding | 2832 | 7 | 0.1066 | 0.3956 | 0.1268 | 0.7539 | 0.2401 |
| obs_clock_cadence | no_win | forward_expanding | 2832 | 7 | 0.8934 | 0.3956 | 0.1268 | 0.7539 | 0.7599 |
| multi_model_forecast | touch | forward_expanding | 3048 | 8 | 0.1234 | 0.5328 | 0.1729 | 0.7912 | 0.3347 |
| multi_model_forecast | stop_exact_given_touch | forward_expanding_touched_only | 348 | 7 | 0.8678 | 0.6376 | 0.2094 | 0.5882 | 0.6736 |
| multi_model_forecast | final_exact | forward_expanding | 2832 | 7 | 0.1066 | 0.3935 | 0.1252 | 0.7470 | 0.2295 |
| multi_model_forecast | no_win | forward_expanding | 2832 | 7 | 0.8934 | 0.3935 | 0.1252 | 0.7470 | 0.7705 |
| taf | touch | forward_expanding | 3048 | 8 | 0.1234 | 0.5297 | 0.1713 | 0.7941 | 0.3283 |
| taf | stop_exact_given_touch | forward_expanding_touched_only | 348 | 7 | 0.8678 | 0.6644 | 0.2149 | 0.5662 | 0.6701 |
| taf | final_exact | forward_expanding | 2832 | 7 | 0.1066 | 0.3989 | 0.1281 | 0.7424 | 0.2276 |
| taf | no_win | forward_expanding | 2832 | 7 | 0.8934 | 0.3989 | 0.1281 | 0.7424 | 0.7724 |
| vertical_hourly_context | touch | forward_expanding | 3048 | 8 | 0.1234 | 0.5322 | 0.1709 | 0.7936 | 0.3275 |
| vertical_hourly_context | stop_exact_given_touch | forward_expanding_touched_only | 348 | 7 | 0.8678 | 0.6598 | 0.2141 | 0.5661 | 0.6705 |
| vertical_hourly_context | final_exact | forward_expanding | 2832 | 7 | 0.1066 | 0.4015 | 0.1281 | 0.7426 | 0.2276 |
| vertical_hourly_context | no_win | forward_expanding | 2832 | 7 | 0.8934 | 0.4015 | 0.1281 | 0.7426 | 0.7724 |
| city_late_reheat_calibration | touch | forward_expanding | 3048 | 8 | 0.1234 | 0.5517 | 0.1758 | 0.7787 | 0.3218 |
| city_late_reheat_calibration | stop_exact_given_touch | forward_expanding_touched_only | 348 | 7 | 0.8678 | 0.7635 | 0.2404 | 0.5396 | 0.6424 |
| city_late_reheat_calibration | final_exact | forward_expanding | 2832 | 7 | 0.1066 | 0.4060 | 0.1290 | 0.7283 | 0.2137 |
| city_late_reheat_calibration | no_win | forward_expanding | 2832 | 7 | 0.8934 | 0.4060 | 0.1290 | 0.7283 | 0.7863 |
| full_v2 | touch | forward_expanding | 3048 | 8 | 0.1234 | 0.5545 | 0.1755 | 0.7781 | 0.3213 |
| full_v2 | stop_exact_given_touch | forward_expanding_touched_only | 348 | 7 | 0.8678 | 0.7602 | 0.2396 | 0.5396 | 0.6427 |
| full_v2 | final_exact | forward_expanding | 2832 | 7 | 0.1066 | 0.4085 | 0.1291 | 0.7280 | 0.2136 |
| full_v2 | no_win | forward_expanding | 2832 | 7 | 0.8934 | 0.4085 | 0.1291 | 0.7280 | 0.7864 |
| market_no_ask | no_win | forward_expanding | 1474 | 9 | 0.8847 | 0.2072 | 0.0635 | 0.9194 | 0.8930 |
| clock_only_hazard | no_win | forward_expanding | 3177 | 9 | 0.8930 | 0.4058 | 0.1214 | 0.6331 | 0.7411 |
| raw_physical_score_v1 | no_win | forward_expanding | 3177 | 9 | 0.8930 | 1.2606 | 0.2492 | 0.7733 | 0.5242 |
| v1_original_retrained | no_win | forward_expanding | 3048 | 8 | 0.8927 | 0.5424 | 0.1771 | 0.7833 | 0.6557 |
| old_v1_like | no_win | forward_expanding | 3048 | 8 | 0.8927 | 0.5336 | 0.1750 | 0.7858 | 0.6621 |

## ROI Replay
| model | edge_threshold | rows | dates | cities | cost | pnl | roi | date_block_ci_low | date_block_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base_physical_only | 0.0000 | 149 | 7 | 25 | 86.1882 | 5.8118 | 0.0674 | -0.0458 | 0.1903 |
| base_physical_only | 0.0100 | 116 | 7 | 23 | 54.6832 | 4.3168 | 0.0789 | -0.0756 | 0.3406 |
| base_physical_only | 0.0200 | 105 | 7 | 22 | 45.8624 | 3.1376 | 0.0684 | -0.1040 | 0.3279 |
| base_physical_only | 0.0300 | 102 | 7 | 22 | 43.9169 | 3.0831 | 0.0702 | -0.1156 | 0.3414 |
| base_physical_only | 0.0500 | 89 | 7 | 20 | 35.0054 | 0.9946 | 0.0284 | -0.2364 | 0.3544 |
| obs_clock_cadence | 0.0000 | 184 | 7 | 35 | 121.0579 | 3.9421 | 0.0326 | -0.0564 | 0.1214 |
| obs_clock_cadence | 0.0100 | 138 | 7 | 29 | 76.3593 | 2.6407 | 0.0346 | -0.0874 | 0.2187 |
| obs_clock_cadence | 0.0200 | 119 | 7 | 26 | 60.2870 | 1.7130 | 0.0284 | -0.1347 | 0.2921 |
| obs_clock_cadence | 0.0300 | 112 | 7 | 26 | 54.5610 | 2.4390 | 0.0447 | -0.1695 | 0.3222 |
| obs_clock_cadence | 0.0500 | 97 | 7 | 22 | 41.9386 | 1.0614 | 0.0253 | -0.1929 | 0.3896 |
| multi_model_forecast | 0.0000 | 185 | 7 | 35 | 124.1286 | 2.8714 | 0.0231 | -0.1016 | 0.1172 |
| multi_model_forecast | 0.0100 | 139 | 7 | 29 | 79.2995 | 2.7005 | 0.0341 | -0.1476 | 0.2161 |
| multi_model_forecast | 0.0200 | 123 | 7 | 24 | 64.4953 | 1.5047 | 0.0233 | -0.1959 | 0.2421 |
| multi_model_forecast | 0.0300 | 117 | 7 | 24 | 59.3069 | 0.6931 | 0.0117 | -0.2345 | 0.2455 |
| multi_model_forecast | 0.0500 | 100 | 7 | 21 | 45.3765 | -0.3765 | -0.0083 | -0.2571 | 0.2781 |
| taf | 0.0000 | 211 | 7 | 37 | 150.6977 | 3.3023 | 0.0219 | -0.0854 | 0.0920 |
| taf | 0.0100 | 137 | 7 | 29 | 79.5240 | 0.4759 | 0.0060 | -0.1982 | 0.1788 |
| taf | 0.0200 | 122 | 7 | 23 | 65.5917 | -0.5917 | -0.0090 | -0.2721 | 0.1955 |
| taf | 0.0300 | 116 | 7 | 23 | 60.1390 | -1.1390 | -0.0189 | -0.2877 | 0.2068 |
| taf | 0.0500 | 97 | 7 | 20 | 44.7424 | -1.7424 | -0.0389 | -0.3883 | 0.3043 |
| vertical_hourly_context | 0.0000 | 213 | 7 | 37 | 152.6863 | 3.3137 | 0.0217 | -0.0844 | 0.0900 |
| vertical_hourly_context | 0.0100 | 137 | 7 | 29 | 79.5240 | 0.4759 | 0.0060 | -0.1982 | 0.1788 |
| vertical_hourly_context | 0.0200 | 122 | 7 | 23 | 65.5917 | -0.5917 | -0.0090 | -0.2721 | 0.1955 |
| vertical_hourly_context | 0.0300 | 115 | 7 | 23 | 59.4383 | -0.4383 | -0.0074 | -0.2877 | 0.2527 |
| vertical_hourly_context | 0.0500 | 96 | 7 | 20 | 44.2700 | -2.2700 | -0.0513 | -0.3892 | 0.3043 |
| city_late_reheat_calibration | 0.0000 | 279 | 7 | 34 | 204.3287 | 0.6713 | 0.0033 | -0.0796 | 0.0781 |
| city_late_reheat_calibration | 0.0100 | 172 | 7 | 27 | 100.6510 | -0.6510 | -0.0065 | -0.1488 | 0.1667 |
| city_late_reheat_calibration | 0.0200 | 156 | 7 | 23 | 87.5525 | -1.5525 | -0.0177 | -0.1877 | 0.1776 |
| city_late_reheat_calibration | 0.0300 | 147 | 7 | 22 | 79.5889 | -2.5889 | -0.0325 | -0.2245 | 0.1902 |
| city_late_reheat_calibration | 0.0500 | 127 | 7 | 20 | 62.8007 | -1.8007 | -0.0287 | -0.3009 | 0.2734 |
| full_v2 | 0.0000 | 280 | 7 | 34 | 204.9486 | 0.0514 | 0.0003 | -0.0793 | 0.0757 |
| full_v2 | 0.0100 | 173 | 7 | 27 | 101.3222 | -1.3222 | -0.0130 | -0.1529 | 0.1621 |
| full_v2 | 0.0200 | 157 | 7 | 23 | 88.5163 | -1.5163 | -0.0171 | -0.1877 | 0.1762 |
| full_v2 | 0.0300 | 147 | 7 | 22 | 79.5889 | -2.5889 | -0.0325 | -0.2245 | 0.1902 |
| full_v2 | 0.0500 | 127 | 7 | 20 | 62.8007 | -1.8007 | -0.0287 | -0.3009 | 0.2734 |
| market_no_ask | 0.0000 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |  |  |
| market_no_ask | 0.0100 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |  |  |
| market_no_ask | 0.0200 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |  |  |
| market_no_ask | 0.0300 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |  |  |
| market_no_ask | 0.0500 | 0 | 0 | 0 | 0.0000 | 0.0000 |  |  |  |
| clock_only_hazard | 0.0000 | 193 | 9 | 29 | 92.2221 | -1.2221 | -0.0133 | -0.2080 | 0.1798 |
| clock_only_hazard | 0.0100 | 184 | 9 | 29 | 86.3328 | -1.3328 | -0.0154 | -0.2132 | 0.1786 |
| clock_only_hazard | 0.0200 | 178 | 9 | 29 | 81.6538 | -0.6537 | -0.0080 | -0.2173 | 0.1901 |
| clock_only_hazard | 0.0300 | 172 | 9 | 28 | 77.5172 | 0.4828 | 0.0062 | -0.2074 | 0.2095 |
| clock_only_hazard | 0.0500 | 155 | 9 | 28 | 66.3329 | -0.3329 | -0.0050 | -0.2560 | 0.2351 |
| raw_physical_score_v1 | 0.0000 | 33 | 7 | 11 | 4.8308 | 1.1692 | 0.2420 | -0.2278 | 0.6952 |
| raw_physical_score_v1 | 0.0100 | 31 | 7 | 10 | 3.8205 | 0.1795 | 0.0470 | -0.7100 | 0.7009 |
| raw_physical_score_v1 | 0.0200 | 31 | 7 | 10 | 3.8205 | 0.1795 | 0.0470 | -0.7100 | 0.7009 |
| raw_physical_score_v1 | 0.0300 | 31 | 7 | 10 | 3.8205 | 0.1795 | 0.0470 | -0.7100 | 0.7009 |
| raw_physical_score_v1 | 0.0500 | 31 | 7 | 10 | 3.8205 | 0.1795 | 0.0470 | -0.7100 | 0.7009 |
| v1_original_retrained | 0.0000 | 92 | 8 | 26 | 56.2909 | 1.7091 | 0.0304 | -0.0832 | 0.1912 |
| v1_original_retrained | 0.0100 | 66 | 8 | 20 | 31.0165 | 2.9835 | 0.0962 | -0.0642 | 0.2988 |
| v1_original_retrained | 0.0200 | 60 | 8 | 20 | 25.6753 | 3.3247 | 0.1295 | 0.0070 | 0.3918 |
| v1_original_retrained | 0.0300 | 56 | 8 | 20 | 22.1874 | 2.8126 | 0.1268 | -0.0025 | 0.4116 |
| v1_original_retrained | 0.0500 | 49 | 8 | 19 | 15.9862 | 2.0138 | 0.1260 | -0.0416 | 0.4864 |
| old_v1_like | 0.0000 | 98 | 8 | 27 | 61.7215 | -0.7215 | -0.0117 | -0.1616 | 0.1140 |
| old_v1_like | 0.0100 | 65 | 8 | 20 | 30.2103 | -0.2103 | -0.0070 | -0.2065 | 0.1998 |
| old_v1_like | 0.0200 | 60 | 8 | 19 | 25.3569 | -0.3569 | -0.0141 | -0.2316 | 0.2517 |
| old_v1_like | 0.0300 | 52 | 8 | 18 | 18.8773 | 0.1227 | 0.0065 | -0.1984 | 0.3341 |
| old_v1_like | 0.0500 | 46 | 8 | 17 | 14.4242 | 0.5758 | 0.0399 | -0.1421 | 0.4452 |

## Required Slices
| slice | rows | dates | cities | touch_rate | exact_rate | no_win_rate | avg_p_no_win | tail_miss_rate | model |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| d1_no | 2832 | 7.0000 | 47.0000 | 0.1229 | 0.1066 | 0.8934 | 0.7696 | 0.0113 | base_physical_only |
| d1_no_next_obs_le30 | 2193 | 7.0000 | 47.0000 | 0.1322 | 0.1145 | 0.8855 | 0.7623 | 0.0119 | base_physical_only |
| d1_no_cadence_approx60 | 1104 | 7.0000 | 32.0000 | 0.0888 | 0.0824 | 0.9176 | 0.7631 | 0.0072 | base_physical_only |
| d1_no_path_decline | 1437 | 7.0000 | 47.0000 | 0.0362 | 0.0320 | 0.9680 | 0.9002 | 0.0125 | base_physical_only |
| d1_no_forecast_below_target | 2204 | 7.0000 | 47.0000 | 0.0989 | 0.0894 | 0.9106 | 0.7775 | 0.0104 | base_physical_only |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | base_physical_only |
| d1_no_chengdu_like_asia_hot | 697 | 7.0000 | 12.0000 | 0.0832 | 0.0732 | 0.9268 | 0.8059 | 0.0158 | base_physical_only |
| d1_no | 2832 | 7.0000 | 47.0000 | 0.1229 | 0.1066 | 0.8934 | 0.7599 | 0.0127 | obs_clock_cadence |
| d1_no_next_obs_le30 | 2193 | 7.0000 | 47.0000 | 0.1322 | 0.1145 | 0.8855 | 0.7408 | 0.0132 | obs_clock_cadence |
| d1_no_cadence_approx60 | 1104 | 7.0000 | 32.0000 | 0.0888 | 0.0824 | 0.9176 | 0.7808 | 0.0109 | obs_clock_cadence |
| d1_no_path_decline | 1437 | 7.0000 | 47.0000 | 0.0362 | 0.0320 | 0.9680 | 0.9006 | 0.0139 | obs_clock_cadence |
| d1_no_forecast_below_target | 2204 | 7.0000 | 47.0000 | 0.0989 | 0.0894 | 0.9106 | 0.7740 | 0.0132 | obs_clock_cadence |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | obs_clock_cadence |
| d1_no_chengdu_like_asia_hot | 697 | 7.0000 | 12.0000 | 0.0832 | 0.0732 | 0.9268 | 0.8000 | 0.0215 | obs_clock_cadence |
| d1_no | 2832 | 7.0000 | 47.0000 | 0.1229 | 0.1066 | 0.8934 | 0.7705 | 0.0138 | multi_model_forecast |
| d1_no_next_obs_le30 | 2193 | 7.0000 | 47.0000 | 0.1322 | 0.1145 | 0.8855 | 0.7498 | 0.0146 | multi_model_forecast |
| d1_no_cadence_approx60 | 1104 | 7.0000 | 32.0000 | 0.0888 | 0.0824 | 0.9176 | 0.7965 | 0.0118 | multi_model_forecast |
| d1_no_path_decline | 1437 | 7.0000 | 47.0000 | 0.0362 | 0.0320 | 0.9680 | 0.9058 | 0.0139 | multi_model_forecast |
| d1_no_forecast_below_target | 2204 | 7.0000 | 47.0000 | 0.0989 | 0.0894 | 0.9106 | 0.7868 | 0.0145 | multi_model_forecast |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | multi_model_forecast |
| d1_no_chengdu_like_asia_hot | 697 | 7.0000 | 12.0000 | 0.0832 | 0.0732 | 0.9268 | 0.8170 | 0.0215 | multi_model_forecast |
| d1_no | 2832 | 7.0000 | 47.0000 | 0.1229 | 0.1066 | 0.8934 | 0.7724 | 0.0145 | taf |
| d1_no_next_obs_le30 | 2193 | 7.0000 | 47.0000 | 0.1322 | 0.1145 | 0.8855 | 0.7518 | 0.0146 | taf |
| d1_no_cadence_approx60 | 1104 | 7.0000 | 32.0000 | 0.0888 | 0.0824 | 0.9176 | 0.7973 | 0.0109 | taf |
| d1_no_path_decline | 1437 | 7.0000 | 47.0000 | 0.0362 | 0.0320 | 0.9680 | 0.9080 | 0.0146 | taf |
| d1_no_forecast_below_target | 2204 | 7.0000 | 47.0000 | 0.0989 | 0.0894 | 0.9106 | 0.7868 | 0.0150 | taf |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | taf |
| d1_no_chengdu_like_asia_hot | 697 | 7.0000 | 12.0000 | 0.0832 | 0.0732 | 0.9268 | 0.7916 | 0.0230 | taf |
| d1_no | 2832 | 7.0000 | 47.0000 | 0.1229 | 0.1066 | 0.8934 | 0.7724 | 0.0145 | vertical_hourly_context |
| d1_no_next_obs_le30 | 2193 | 7.0000 | 47.0000 | 0.1322 | 0.1145 | 0.8855 | 0.7519 | 0.0146 | vertical_hourly_context |
| d1_no_cadence_approx60 | 1104 | 7.0000 | 32.0000 | 0.0888 | 0.0824 | 0.9176 | 0.7973 | 0.0109 | vertical_hourly_context |
| d1_no_path_decline | 1437 | 7.0000 | 47.0000 | 0.0362 | 0.0320 | 0.9680 | 0.9085 | 0.0146 | vertical_hourly_context |
| d1_no_forecast_below_target | 2204 | 7.0000 | 47.0000 | 0.0989 | 0.0894 | 0.9106 | 0.7871 | 0.0150 | vertical_hourly_context |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | vertical_hourly_context |
| d1_no_chengdu_like_asia_hot | 697 | 7.0000 | 12.0000 | 0.0832 | 0.0732 | 0.9268 | 0.7918 | 0.0230 | vertical_hourly_context |
| d1_no | 2832 | 7.0000 | 47.0000 | 0.1229 | 0.1066 | 0.8934 | 0.7863 | 0.0180 | city_late_reheat_calibration |
| d1_no_next_obs_le30 | 2193 | 7.0000 | 47.0000 | 0.1322 | 0.1145 | 0.8855 | 0.7697 | 0.0178 | city_late_reheat_calibration |
| d1_no_cadence_approx60 | 1104 | 7.0000 | 32.0000 | 0.0888 | 0.0824 | 0.9176 | 0.8015 | 0.0109 | city_late_reheat_calibration |
| d1_no_path_decline | 1437 | 7.0000 | 47.0000 | 0.0362 | 0.0320 | 0.9680 | 0.9064 | 0.0139 | city_late_reheat_calibration |
| d1_no_forecast_below_target | 2204 | 7.0000 | 47.0000 | 0.0989 | 0.0894 | 0.9106 | 0.7942 | 0.0172 | city_late_reheat_calibration |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | city_late_reheat_calibration |
| d1_no_chengdu_like_asia_hot | 697 | 7.0000 | 12.0000 | 0.0832 | 0.0732 | 0.9268 | 0.8063 | 0.0258 | city_late_reheat_calibration |
| d1_no | 2832 | 7.0000 | 47.0000 | 0.1229 | 0.1066 | 0.8934 | 0.7864 | 0.0184 | full_v2 |
| d1_no_next_obs_le30 | 2193 | 7.0000 | 47.0000 | 0.1322 | 0.1145 | 0.8855 | 0.7699 | 0.0178 | full_v2 |
| d1_no_cadence_approx60 | 1104 | 7.0000 | 32.0000 | 0.0888 | 0.0824 | 0.9176 | 0.8015 | 0.0109 | full_v2 |
| d1_no_path_decline | 1437 | 7.0000 | 47.0000 | 0.0362 | 0.0320 | 0.9680 | 0.9069 | 0.0139 | full_v2 |
| d1_no_forecast_below_target | 2204 | 7.0000 | 47.0000 | 0.0989 | 0.0894 | 0.9106 | 0.7945 | 0.0172 | full_v2 |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | full_v2 |
| d1_no_chengdu_like_asia_hot | 697 | 7.0000 | 12.0000 | 0.0832 | 0.0732 | 0.9268 | 0.8065 | 0.0258 | full_v2 |
| d1_no | 1474 | 9.0000 | 47.0000 | 0.1330 | 0.1153 | 0.8847 | 0.8930 | 0.0190 | market_no_ask |
| d1_no_next_obs_le30 | 1107 | 9.0000 | 46.0000 | 0.1481 | 0.1292 | 0.8708 | 0.8826 | 0.0217 | market_no_ask |
| d1_no_cadence_approx60 | 638 | 9.0000 | 32.0000 | 0.1097 | 0.0987 | 0.9013 | 0.9019 | 0.0157 | market_no_ask |
| d1_no_path_decline | 732 | 9.0000 | 47.0000 | 0.0519 | 0.0464 | 0.9536 | 0.9618 | 0.0109 | market_no_ask |
| d1_no_forecast_below_target | 1127 | 9.0000 | 47.0000 | 0.1065 | 0.0967 | 0.9033 | 0.9160 | 0.0222 | market_no_ask |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | market_no_ask |
| d1_no_chengdu_like_asia_hot | 329 | 8.0000 | 12.0000 | 0.1094 | 0.0881 | 0.9119 | 0.9021 | 0.0213 | market_no_ask |
| d1_no | 3177 | 9.0000 | 47.0000 | 0.1224 | 0.1070 | 0.8930 | 0.7411 | 0.0006 | clock_only_hazard |
| d1_no_next_obs_le30 | 2408 | 9.0000 | 47.0000 | 0.1358 | 0.1184 | 0.8816 | 0.7068 | 0.0000 | clock_only_hazard |
| d1_no_cadence_approx60 | 1293 | 9.0000 | 34.0000 | 0.0897 | 0.0828 | 0.9172 | 0.6991 | 0.0000 | clock_only_hazard |
| d1_no_path_decline | 1606 | 9.0000 | 47.0000 | 0.0361 | 0.0324 | 0.9676 | 0.8102 | 0.0012 | clock_only_hazard |
| d1_no_forecast_below_target | 2486 | 9.0000 | 47.0000 | 0.0969 | 0.0885 | 0.9115 | 0.7466 | 0.0004 | clock_only_hazard |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | clock_only_hazard |
| d1_no_chengdu_like_asia_hot | 741 | 8.0000 | 12.0000 | 0.0850 | 0.0756 | 0.9244 | 0.7540 | 0.0000 | clock_only_hazard |
| d1_no | 3177 | 9.0000 | 47.0000 | 0.1224 | 0.1070 | 0.8930 | 0.5242 | 0.0000 | raw_physical_score_v1 |
| d1_no_next_obs_le30 | 2408 | 9.0000 | 47.0000 | 0.1358 | 0.1184 | 0.8816 | 0.5120 | 0.0000 | raw_physical_score_v1 |
| d1_no_cadence_approx60 | 1293 | 9.0000 | 34.0000 | 0.0897 | 0.0828 | 0.9172 | 0.5522 | 0.0000 | raw_physical_score_v1 |
| d1_no_path_decline | 1606 | 9.0000 | 47.0000 | 0.0361 | 0.0324 | 0.9676 | 0.6776 | 0.0000 | raw_physical_score_v1 |
| d1_no_forecast_below_target | 2486 | 9.0000 | 47.0000 | 0.0969 | 0.0885 | 0.9115 | 0.6228 | 0.0000 | raw_physical_score_v1 |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | raw_physical_score_v1 |
| d1_no_chengdu_like_asia_hot | 741 | 8.0000 | 12.0000 | 0.0850 | 0.0756 | 0.9244 | 0.5621 | 0.0000 | raw_physical_score_v1 |
| d1_no | 3048 | 8.0000 | 47.0000 | 0.1234 | 0.1073 | 0.8927 | 0.6557 | 0.0089 | v1_original_retrained |
| d1_no_next_obs_le30 | 2342 | 8.0000 | 47.0000 | 0.1354 | 0.1174 | 0.8826 | 0.6458 | 0.0098 | v1_original_retrained |
| d1_no_cadence_approx60 | 1207 | 8.0000 | 34.0000 | 0.0903 | 0.0829 | 0.9171 | 0.7054 | 0.0066 | v1_original_retrained |
| d1_no_path_decline | 1546 | 8.0000 | 47.0000 | 0.0375 | 0.0336 | 0.9664 | 0.8564 | 0.0149 | v1_original_retrained |
| d1_no_forecast_below_target | 2386 | 8.0000 | 47.0000 | 0.0985 | 0.0897 | 0.9103 | 0.7012 | 0.0113 | v1_original_retrained |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | v1_original_retrained |
| d1_no_chengdu_like_asia_hot | 741 | 8.0000 | 12.0000 | 0.0850 | 0.0756 | 0.9244 | 0.6934 | 0.0175 | v1_original_retrained |
| d1_no | 3048 | 8.0000 | 47.0000 | 0.1234 | 0.1073 | 0.8927 | 0.6621 | 0.0085 | old_v1_like |
| d1_no_next_obs_le30 | 2342 | 8.0000 | 47.0000 | 0.1354 | 0.1174 | 0.8826 | 0.6396 | 0.0085 | old_v1_like |
| d1_no_cadence_approx60 | 1207 | 8.0000 | 34.0000 | 0.0903 | 0.0829 | 0.9171 | 0.7045 | 0.0066 | old_v1_like |
| d1_no_path_decline | 1546 | 8.0000 | 47.0000 | 0.0375 | 0.0336 | 0.9664 | 0.8635 | 0.0142 | old_v1_like |
| d1_no_forecast_below_target | 2386 | 8.0000 | 47.0000 | 0.0985 | 0.0897 | 0.9103 | 0.7093 | 0.0109 | old_v1_like |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | old_v1_like |
| d1_no_chengdu_like_asia_hot | 741 | 8.0000 | 12.0000 | 0.0850 | 0.0756 | 0.9244 | 0.6952 | 0.0162 | old_v1_like |

## Chengdu 2026-07-07 Case
| snapshot_ts_utc | ts_beijing | entry_price | best_bid | spread | top_size | running_value | latest_native | path_state | obs_age_minutes | obs_cadence_min | minutes_to_next_obs | touch_target_after_decision | first_touch_minutes_after_decision | final_exact_target | no_win | incident_reported_p_leg_win_physical_v1 | incident_live_order_ts_bj | incident_next_key_metar_ts_bj | incident_first_touch_minutes_context | v1_original_retrained_p_no_win | old_v1_like_p_no_win | full_v2_p_touch | full_v2_p_stop_exact_given_touch | full_v2_p_exact | full_v2_p_no_win |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-07T07:32:00Z | 2026-07-07 15:32:00 | 0.9400 | 0.9200 | 0.0200 | 5.0100 | 36 | 35.0000 | decline | 32.0000 | 60.0000 | 28.0000 | True |  | True | False | 0.9510 | 2026-07-07 15:39:00 | 2026-07-07 16:00:00 | 28.0000 | 0.5073 | 0.4848 | 0.6519 | 0.8362 | 0.5451 | 0.4549 |
| 2026-07-07T07:46:00Z | 2026-07-07 15:46:00 |  |  |  |  | 36 | 35.0000 | decline | 46.0000 | 60.0000 | 14.0000 | True |  | True | False |  |  |  |  | 0.7246 | 0.6652 | 0.4128 | 0.8982 | 0.3707 | 0.6293 |
| 2026-07-07T08:02:00Z | 2026-07-07 16:02:00 |  |  |  |  | 36 | 35.0000 | decline | 62.0000 | 60.0000 | 0.0000 | True |  | True | False |  |  |  |  | 0.9116 | 0.8399 | 0.1653 | 0.8991 | 0.1486 | 0.8514 |
| 2026-07-07T08:15:00Z | 2026-07-07 16:15:00 |  |  |  |  | 36 | 36.1111 | at_high | 15.0000 | 60.0000 | 45.0000 | True | 45.0000 | True | False |  |  |  |  | 0.7778 | 0.7673 | 0.3660 | 0.9282 | 0.3398 | 0.6602 |
| 2026-07-07T08:30:00Z | 2026-07-07 16:30:00 |  |  |  |  | 36 | 36.1111 | at_high | 30.0000 | 60.0000 | 30.0000 | True |  | True | False |  |  |  |  | 0.7966 | 0.7454 | 0.3069 | 0.9108 | 0.2795 | 0.7205 |

## Data Gaps Before Live
- Preserve historical source_events/observation cache versions, not only latest, so cadence and next-observation hazard can be computed from source truth rather than paper-snapshot proxy.
- Persist PIT TAF snapshots and parsed TX/TN/TEMPO/BECMG/TSRA/SHRA features.
- Persist PIT vertical profile and full Open-Meteo hourly context variables; current mirror only has temperature curves.
- Run at least 10 active forward dates after the Chengdu failure with the v2 scorer before any tiny-live discussion.
