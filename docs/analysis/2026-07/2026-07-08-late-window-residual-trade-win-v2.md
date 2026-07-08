# Late-Window Residual Trade-Win v2

Status: snapshot
Date: 2026-07-08
Scope: research-only; no live/shadow runner changes

## Data Snapshot
- Synced Mac market data before run; snapshots seen: 647.
- Candidate rows generated: 10874; settled d1 rows: 3177.
- Settlement coverage 2026-07-05..07: {'2026-07-01': {'cities': 47, 'rows': 517}, '2026-07-02': {'cities': 47, 'rows': 517}, '2026-07-03': {'cities': 47, 'rows': 517}, '2026-07-04': {'cities': 47, 'rows': 446}, '2026-07-05': {'cities': 47, 'rows': 517}, '2026-07-06': {'cities': 47, 'rows': 515}, '2026-07-07': {'cities': 47, 'rows': 517}}.
- Forecast hourly curve records: 34201; observation history rows: 9961.
- TAF history, vertical profile history, and PIT multi-model forecast history were not present in the local market-data mirror; v2 keeps their columns as unavailable rather than leaking current/future data.

## Verdict
- Chengdu 2026-07-07 37 NO is the named failure mode: at the nearest 15:32 BJ snapshot, the incident scorer reported `p_leg_win_physical_v1` p(NO win)≈0.951; live entry followed at 2026-07-07 15:39:00.
- The script's `old_v1_like` column is a retrained single-stage baseline on this per-poll frame, not the exact old production/research scorer.
- Incident context says the next key METAR was 2026-07-07 16:00:00 and touched 37C; historical paper snapshots imply touch/final-exact but do not preserve the raw first-touch minute for the 15:32 row.
- v2 full model at that snapshot: P(touch 37 after decision)=0.652, P(final exactly 37)=0.724, P(37 NO win)=0.276.
- old_v1_like_p_no_win=0.485; raw physical score=0.450; market_p_no_win=0.060.
- full_v2 forward d1 NO no_win calibration: rows=3048, dates=8, logloss=0.5840, Brier=0.1825, AUC=0.7415.
- old v1-like forward no_win: logloss=0.5336, Brier=0.1750, AUC=0.7858; p_leg_win_physical_v1 should be treated as raw physical score, not calibrated trade probability.
- full_v2 edge>=0 executable replay: rows=213, dates=8, ROI=-3.6%, date-block CI [-12.0%,5.0%].
- old v1-like edge>=0 replay: rows=98, dates=8, ROI=-1.2%, date-block CI [-16.2%,11.4%].

Conclusion gates: significance=FAIL/NA, baseline=FAIL/NA, forward=FAIL; conclusion=inconclusive_research_only.

## Model Design
- Grain: polling snapshot x city x target_date x candidate leg/token.
- Main model: d1 NO only. d2/d3/current YES are retained in candidate frame for diagnostics but not mixed into the main target.
- Stage A predicts `touch_target_after_decision`; Stage B predicts `final_exact_target`; `p_trade_win_v2 = 1 - P(final_exact_target)` for NO.
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
| base_physical_only | final_exact | forward_expanding | 3048 | 8 | 0.1073 | 0.5379 | 0.1771 | 0.7800 | 0.3338 |
| base_physical_only | no_win | forward_expanding | 3048 | 8 | 0.8927 | 0.5379 | 0.1771 | 0.7800 | 0.6662 |
| obs_clock_cadence | touch | forward_expanding | 3048 | 8 | 0.1234 | 0.5472 | 0.1782 | 0.7953 | 0.3488 |
| obs_clock_cadence | final_exact | forward_expanding | 3048 | 8 | 0.1073 | 0.5625 | 0.1842 | 0.7715 | 0.3328 |
| obs_clock_cadence | no_win | forward_expanding | 3048 | 8 | 0.8927 | 0.5625 | 0.1842 | 0.7715 | 0.6672 |
| multi_model_forecast | touch | forward_expanding | 3048 | 8 | 0.1234 | 0.5328 | 0.1729 | 0.7912 | 0.3347 |
| multi_model_forecast | final_exact | forward_expanding | 3048 | 8 | 0.1073 | 0.5389 | 0.1751 | 0.7652 | 0.3115 |
| multi_model_forecast | no_win | forward_expanding | 3048 | 8 | 0.8927 | 0.5389 | 0.1751 | 0.7652 | 0.6885 |
| taf | touch | forward_expanding | 3048 | 8 | 0.1234 | 0.5297 | 0.1713 | 0.7941 | 0.3283 |
| taf | final_exact | forward_expanding | 3048 | 8 | 0.1073 | 0.5481 | 0.1778 | 0.7594 | 0.3051 |
| taf | no_win | forward_expanding | 3048 | 8 | 0.8927 | 0.5481 | 0.1778 | 0.7594 | 0.6949 |
| vertical_hourly_context | touch | forward_expanding | 3048 | 8 | 0.1234 | 0.5322 | 0.1709 | 0.7936 | 0.3275 |
| vertical_hourly_context | final_exact | forward_expanding | 3048 | 8 | 0.1073 | 0.5484 | 0.1776 | 0.7584 | 0.3044 |
| vertical_hourly_context | no_win | forward_expanding | 3048 | 8 | 0.8927 | 0.5484 | 0.1776 | 0.7584 | 0.6956 |
| city_late_reheat_calibration | touch | forward_expanding | 3048 | 8 | 0.1234 | 0.5517 | 0.1758 | 0.7787 | 0.3218 |
| city_late_reheat_calibration | final_exact | forward_expanding | 3048 | 8 | 0.1073 | 0.5832 | 0.1826 | 0.7424 | 0.2945 |
| city_late_reheat_calibration | no_win | forward_expanding | 3048 | 8 | 0.8927 | 0.5832 | 0.1826 | 0.7424 | 0.7055 |
| full_v2 | touch | forward_expanding | 3048 | 8 | 0.1234 | 0.5545 | 0.1755 | 0.7781 | 0.3213 |
| full_v2 | final_exact | forward_expanding | 3048 | 8 | 0.1073 | 0.5840 | 0.1825 | 0.7415 | 0.2940 |
| full_v2 | no_win | forward_expanding | 3048 | 8 | 0.8927 | 0.5840 | 0.1825 | 0.7415 | 0.7060 |
| market_no_ask | no_win | forward_expanding | 1474 | 9 | 0.8847 | 4.1651 | 0.8248 | 0.0806 | 0.1070 |
| clock_only_hazard | no_win | forward_expanding | 3177 | 9 | 0.8930 | 0.4058 | 0.1214 | 0.6331 | 0.7411 |
| raw_physical_score_v1 | no_win | forward_expanding | 3177 | 9 | 0.8930 | 1.2606 | 0.2492 | 0.7733 | 0.5242 |
| old_v1_like | no_win | forward_expanding | 3048 | 8 | 0.8927 | 0.5336 | 0.1750 | 0.7858 | 0.6621 |

## ROI Replay
| model | edge_threshold | rows | dates | cities | cost | pnl | roi | date_block_ci_low | date_block_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base_physical_only | 0.0000 | 133 | 8 | 36 | 94.9207 | -4.9207 | -0.0518 | -0.2017 | 0.1515 |
| base_physical_only | 0.0100 | 83 | 8 | 26 | 45.7582 | -5.7582 | -0.1258 | -0.2995 | 0.1740 |
| base_physical_only | 0.0200 | 74 | 8 | 23 | 37.3855 | -4.3855 | -0.1173 | -0.2728 | 0.2271 |
| base_physical_only | 0.0300 | 67 | 8 | 21 | 31.2900 | -4.2900 | -0.1371 | -0.2764 | 0.2540 |
| base_physical_only | 0.0500 | 56 | 8 | 18 | 22.0327 | -2.0327 | -0.0923 | -0.2743 | 0.3083 |
| obs_clock_cadence | 0.0000 | 157 | 8 | 36 | 111.9841 | -0.9841 | -0.0088 | -0.0742 | 0.1197 |
| obs_clock_cadence | 0.0100 | 111 | 8 | 33 | 67.4728 | 0.5272 | 0.0078 | -0.1264 | 0.1775 |
| obs_clock_cadence | 0.0200 | 97 | 7 | 30 | 54.4054 | 1.5945 | 0.0293 | -0.0977 | 0.2030 |
| obs_clock_cadence | 0.0300 | 90 | 7 | 28 | 47.9332 | 1.0668 | 0.0223 | -0.1104 | 0.2213 |
| obs_clock_cadence | 0.0500 | 77 | 7 | 26 | 36.2554 | 1.7446 | 0.0481 | -0.1402 | 0.2299 |
| multi_model_forecast | 0.0000 | 169 | 8 | 38 | 120.7780 | -1.7780 | -0.0147 | -0.0861 | 0.0963 |
| multi_model_forecast | 0.0100 | 121 | 8 | 34 | 74.5004 | -0.5004 | -0.0067 | -0.1051 | 0.1453 |
| multi_model_forecast | 0.0200 | 111 | 8 | 31 | 65.1925 | 0.8075 | 0.0124 | -0.1047 | 0.1504 |
| multi_model_forecast | 0.0300 | 100 | 8 | 30 | 55.6767 | 1.3233 | 0.0238 | -0.0700 | 0.2072 |
| multi_model_forecast | 0.0500 | 85 | 8 | 25 | 41.6574 | 2.3426 | 0.0562 | -0.0713 | 0.2478 |
| taf | 0.0000 | 174 | 8 | 39 | 126.7466 | -4.7466 | -0.0374 | -0.1372 | 0.0515 |
| taf | 0.0100 | 131 | 8 | 36 | 84.2716 | -4.2716 | -0.0507 | -0.1841 | 0.0755 |
| taf | 0.0200 | 115 | 8 | 32 | 69.4347 | -1.4347 | -0.0207 | -0.1958 | 0.1026 |
| taf | 0.0300 | 108 | 8 | 31 | 63.0993 | -2.0993 | -0.0333 | -0.2263 | 0.1007 |
| taf | 0.0500 | 84 | 8 | 24 | 41.6335 | 1.3665 | 0.0328 | -0.1809 | 0.2170 |
| vertical_hourly_context | 0.0000 | 175 | 8 | 39 | 127.7438 | -4.7438 | -0.0371 | -0.1372 | 0.0510 |
| vertical_hourly_context | 0.0100 | 131 | 8 | 36 | 84.2716 | -4.2716 | -0.0507 | -0.1841 | 0.0755 |
| vertical_hourly_context | 0.0200 | 115 | 8 | 32 | 69.4347 | -1.4347 | -0.0207 | -0.1958 | 0.1026 |
| vertical_hourly_context | 0.0300 | 106 | 8 | 30 | 61.2347 | -2.2348 | -0.0365 | -0.2329 | 0.1020 |
| vertical_hourly_context | 0.0500 | 82 | 8 | 24 | 40.2133 | 0.7867 | 0.0196 | -0.2011 | 0.2013 |
| city_late_reheat_calibration | 0.0000 | 213 | 8 | 40 | 159.7941 | -5.7941 | -0.0363 | -0.1202 | 0.0502 |
| city_late_reheat_calibration | 0.0100 | 157 | 8 | 34 | 105.5465 | -5.5465 | -0.0526 | -0.1629 | 0.0729 |
| city_late_reheat_calibration | 0.0200 | 140 | 8 | 31 | 89.6294 | -3.6294 | -0.0405 | -0.1697 | 0.0965 |
| city_late_reheat_calibration | 0.0300 | 130 | 7 | 30 | 80.8309 | -2.8309 | -0.0350 | -0.1797 | 0.1079 |
| city_late_reheat_calibration | 0.0500 | 110 | 7 | 22 | 62.1004 | -2.1005 | -0.0338 | -0.2138 | 0.1313 |
| full_v2 | 0.0000 | 213 | 8 | 40 | 159.7941 | -5.7941 | -0.0363 | -0.1202 | 0.0502 |
| full_v2 | 0.0100 | 157 | 8 | 34 | 105.5465 | -5.5465 | -0.0526 | -0.1629 | 0.0729 |
| full_v2 | 0.0200 | 140 | 8 | 31 | 89.6294 | -3.6294 | -0.0405 | -0.1697 | 0.0965 |
| full_v2 | 0.0300 | 130 | 7 | 30 | 80.8309 | -2.8309 | -0.0350 | -0.1797 | 0.1079 |
| full_v2 | 0.0500 | 110 | 7 | 22 | 62.1004 | -2.1005 | -0.0338 | -0.2138 | 0.1313 |
| market_no_ask | 0.0000 | 101 | 9 | 25 | 30.4490 | 0.5510 | 0.0181 | -0.4293 | 0.4572 |
| market_no_ask | 0.0100 | 94 | 9 | 25 | 26.9315 | 1.0685 | 0.0397 | -0.4449 | 0.4970 |
| market_no_ask | 0.0200 | 94 | 9 | 25 | 26.9315 | 1.0685 | 0.0397 | -0.4449 | 0.4970 |
| market_no_ask | 0.0300 | 88 | 9 | 25 | 23.9736 | 1.0264 | 0.0428 | -0.4529 | 0.5065 |
| market_no_ask | 0.0500 | 86 | 9 | 25 | 23.0087 | -0.0087 | -0.0004 | -0.4923 | 0.4902 |
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
| old_v1_like | 0.0000 | 98 | 8 | 27 | 61.7215 | -0.7215 | -0.0117 | -0.1616 | 0.1140 |
| old_v1_like | 0.0100 | 65 | 8 | 20 | 30.2103 | -0.2103 | -0.0070 | -0.2065 | 0.1998 |
| old_v1_like | 0.0200 | 60 | 8 | 19 | 25.3569 | -0.3569 | -0.0141 | -0.2316 | 0.2517 |
| old_v1_like | 0.0300 | 52 | 8 | 18 | 18.8773 | 0.1227 | 0.0065 | -0.1984 | 0.3341 |
| old_v1_like | 0.0500 | 46 | 8 | 17 | 14.4242 | 0.5758 | 0.0399 | -0.1421 | 0.4452 |

## Required Slices
| slice | rows | dates | cities | touch_rate | exact_rate | no_win_rate | avg_p_no_win | tail_miss_rate | model |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| d1_no | 3048 | 8.0000 | 47.0000 | 0.1234 | 0.1073 | 0.8927 | 0.6662 | 0.0105 | base_physical_only |
| d1_no_next_obs_le30 | 2342 | 8.0000 | 47.0000 | 0.1354 | 0.1174 | 0.8826 | 0.6425 | 0.0115 | base_physical_only |
| d1_no_cadence_approx60 | 1207 | 8.0000 | 34.0000 | 0.0903 | 0.0829 | 0.9171 | 0.7101 | 0.0075 | base_physical_only |
| d1_no_path_decline | 1546 | 8.0000 | 47.0000 | 0.0375 | 0.0336 | 0.9664 | 0.8704 | 0.0155 | base_physical_only |
| d1_no_forecast_below_target | 2386 | 8.0000 | 47.0000 | 0.0985 | 0.0897 | 0.9103 | 0.7152 | 0.0130 | base_physical_only |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | base_physical_only |
| d1_no_chengdu_like_asia_hot | 741 | 8.0000 | 12.0000 | 0.0850 | 0.0756 | 0.9244 | 0.7008 | 0.0189 | base_physical_only |
| d1_no | 3048 | 8.0000 | 47.0000 | 0.1234 | 0.1073 | 0.8927 | 0.6672 | 0.0112 | obs_clock_cadence |
| d1_no_next_obs_le30 | 2342 | 8.0000 | 47.0000 | 0.1354 | 0.1174 | 0.8826 | 0.6287 | 0.0115 | obs_clock_cadence |
| d1_no_cadence_approx60 | 1207 | 8.0000 | 34.0000 | 0.0903 | 0.0829 | 0.9171 | 0.7418 | 0.0108 | obs_clock_cadence |
| d1_no_path_decline | 1546 | 8.0000 | 47.0000 | 0.0375 | 0.0336 | 0.9664 | 0.8622 | 0.0162 | obs_clock_cadence |
| d1_no_forecast_below_target | 2386 | 8.0000 | 47.0000 | 0.0985 | 0.0897 | 0.9103 | 0.7173 | 0.0138 | obs_clock_cadence |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | obs_clock_cadence |
| d1_no_chengdu_like_asia_hot | 741 | 8.0000 | 12.0000 | 0.0850 | 0.0756 | 0.9244 | 0.6928 | 0.0229 | obs_clock_cadence |
| d1_no | 3048 | 8.0000 | 47.0000 | 0.1234 | 0.1073 | 0.8927 | 0.6885 | 0.0115 | multi_model_forecast |
| d1_no_next_obs_le30 | 2342 | 8.0000 | 47.0000 | 0.1354 | 0.1174 | 0.8826 | 0.6491 | 0.0115 | multi_model_forecast |
| d1_no_cadence_approx60 | 1207 | 8.0000 | 34.0000 | 0.0903 | 0.0829 | 0.9171 | 0.7652 | 0.0108 | multi_model_forecast |
| d1_no_path_decline | 1546 | 8.0000 | 47.0000 | 0.0375 | 0.0336 | 0.9664 | 0.8745 | 0.0162 | multi_model_forecast |
| d1_no_forecast_below_target | 2386 | 8.0000 | 47.0000 | 0.0985 | 0.0897 | 0.9103 | 0.7405 | 0.0138 | multi_model_forecast |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | multi_model_forecast |
| d1_no_chengdu_like_asia_hot | 741 | 8.0000 | 12.0000 | 0.0850 | 0.0756 | 0.9244 | 0.7248 | 0.0256 | multi_model_forecast |
| d1_no | 3048 | 8.0000 | 47.0000 | 0.1234 | 0.1073 | 0.8927 | 0.6949 | 0.0135 | taf |
| d1_no_next_obs_le30 | 2342 | 8.0000 | 47.0000 | 0.1354 | 0.1174 | 0.8826 | 0.6558 | 0.0132 | taf |
| d1_no_cadence_approx60 | 1207 | 8.0000 | 34.0000 | 0.0903 | 0.0829 | 0.9171 | 0.7725 | 0.0124 | taf |
| d1_no_path_decline | 1546 | 8.0000 | 47.0000 | 0.0375 | 0.0336 | 0.9664 | 0.8787 | 0.0162 | taf |
| d1_no_forecast_below_target | 2386 | 8.0000 | 47.0000 | 0.0985 | 0.0897 | 0.9103 | 0.7432 | 0.0163 | taf |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | taf |
| d1_no_chengdu_like_asia_hot | 741 | 8.0000 | 12.0000 | 0.0850 | 0.0756 | 0.9244 | 0.7219 | 0.0256 | taf |
| d1_no | 3048 | 8.0000 | 47.0000 | 0.1234 | 0.1073 | 0.8927 | 0.6956 | 0.0138 | vertical_hourly_context |
| d1_no_next_obs_le30 | 2342 | 8.0000 | 47.0000 | 0.1354 | 0.1174 | 0.8826 | 0.6567 | 0.0137 | vertical_hourly_context |
| d1_no_cadence_approx60 | 1207 | 8.0000 | 34.0000 | 0.0903 | 0.0829 | 0.9171 | 0.7726 | 0.0124 | vertical_hourly_context |
| d1_no_path_decline | 1546 | 8.0000 | 47.0000 | 0.0375 | 0.0336 | 0.9664 | 0.8795 | 0.0162 | vertical_hourly_context |
| d1_no_forecast_below_target | 2386 | 8.0000 | 47.0000 | 0.0985 | 0.0897 | 0.9103 | 0.7440 | 0.0168 | vertical_hourly_context |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | vertical_hourly_context |
| d1_no_chengdu_like_asia_hot | 741 | 8.0000 | 12.0000 | 0.0850 | 0.0756 | 0.9244 | 0.7222 | 0.0256 | vertical_hourly_context |
| d1_no | 3048 | 8.0000 | 47.0000 | 0.1234 | 0.1073 | 0.8927 | 0.7055 | 0.0184 | city_late_reheat_calibration |
| d1_no_next_obs_le30 | 2342 | 8.0000 | 47.0000 | 0.1354 | 0.1174 | 0.8826 | 0.6665 | 0.0167 | city_late_reheat_calibration |
| d1_no_cadence_approx60 | 1207 | 8.0000 | 34.0000 | 0.0903 | 0.0829 | 0.9171 | 0.7872 | 0.0157 | city_late_reheat_calibration |
| d1_no_path_decline | 1546 | 8.0000 | 47.0000 | 0.0375 | 0.0336 | 0.9664 | 0.8716 | 0.0162 | city_late_reheat_calibration |
| d1_no_forecast_below_target | 2386 | 8.0000 | 47.0000 | 0.0985 | 0.0897 | 0.9103 | 0.7482 | 0.0214 | city_late_reheat_calibration |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | city_late_reheat_calibration |
| d1_no_chengdu_like_asia_hot | 741 | 8.0000 | 12.0000 | 0.0850 | 0.0756 | 0.9244 | 0.7254 | 0.0297 | city_late_reheat_calibration |
| d1_no | 3048 | 8.0000 | 47.0000 | 0.1234 | 0.1073 | 0.8927 | 0.7060 | 0.0187 | full_v2 |
| d1_no_next_obs_le30 | 2342 | 8.0000 | 47.0000 | 0.1354 | 0.1174 | 0.8826 | 0.6672 | 0.0171 | full_v2 |
| d1_no_cadence_approx60 | 1207 | 8.0000 | 34.0000 | 0.0903 | 0.0829 | 0.9171 | 0.7871 | 0.0157 | full_v2 |
| d1_no_path_decline | 1546 | 8.0000 | 47.0000 | 0.0375 | 0.0336 | 0.9664 | 0.8723 | 0.0162 | full_v2 |
| d1_no_forecast_below_target | 2386 | 8.0000 | 47.0000 | 0.0985 | 0.0897 | 0.9103 | 0.7489 | 0.0218 | full_v2 |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | full_v2 |
| d1_no_chengdu_like_asia_hot | 741 | 8.0000 | 12.0000 | 0.0850 | 0.0756 | 0.9244 | 0.7256 | 0.0297 | full_v2 |
| d1_no | 1474 | 9.0000 | 47.0000 | 0.1330 | 0.1153 | 0.8847 | 0.1070 | 0.0149 | market_no_ask |
| d1_no_next_obs_le30 | 1107 | 9.0000 | 46.0000 | 0.1481 | 0.1292 | 0.8708 | 0.1174 | 0.0181 | market_no_ask |
| d1_no_cadence_approx60 | 638 | 9.0000 | 32.0000 | 0.1097 | 0.0987 | 0.9013 | 0.0981 | 0.0110 | market_no_ask |
| d1_no_path_decline | 732 | 9.0000 | 47.0000 | 0.0519 | 0.0464 | 0.9536 | 0.0382 | 0.0205 | market_no_ask |
| d1_no_forecast_below_target | 1127 | 9.0000 | 47.0000 | 0.1065 | 0.0967 | 0.9033 | 0.0840 | 0.0186 | market_no_ask |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | market_no_ask |
| d1_no_chengdu_like_asia_hot | 329 | 8.0000 | 12.0000 | 0.1094 | 0.0881 | 0.9119 | 0.0979 | 0.0395 | market_no_ask |
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
| d1_no | 3048 | 8.0000 | 47.0000 | 0.1234 | 0.1073 | 0.8927 | 0.6621 | 0.0085 | old_v1_like |
| d1_no_next_obs_le30 | 2342 | 8.0000 | 47.0000 | 0.1354 | 0.1174 | 0.8826 | 0.6396 | 0.0085 | old_v1_like |
| d1_no_cadence_approx60 | 1207 | 8.0000 | 34.0000 | 0.0903 | 0.0829 | 0.9171 | 0.7045 | 0.0066 | old_v1_like |
| d1_no_path_decline | 1546 | 8.0000 | 47.0000 | 0.0375 | 0.0336 | 0.9664 | 0.8635 | 0.0142 | old_v1_like |
| d1_no_forecast_below_target | 2386 | 8.0000 | 47.0000 | 0.0985 | 0.0897 | 0.9103 | 0.7093 | 0.0109 | old_v1_like |
| d1_no_multi_model_spread_high | 0 |  |  |  |  |  |  |  | old_v1_like |
| d1_no_chengdu_like_asia_hot | 741 | 8.0000 | 12.0000 | 0.0850 | 0.0756 | 0.9244 | 0.6952 | 0.0162 | old_v1_like |

## Chengdu 2026-07-07 Case
| snapshot_ts_utc | ts_beijing | entry_price | best_bid | spread | top_size | running_value | latest_native | path_state | obs_age_minutes | obs_cadence_min | minutes_to_next_obs | touch_target_after_decision | first_touch_minutes_after_decision | final_exact_target | no_win | incident_reported_p_leg_win_physical_v1 | incident_live_order_ts_bj | incident_next_key_metar_ts_bj | incident_first_touch_minutes_context | old_v1_like_p_no_win | full_v2_p_touch | full_v2_p_exact | full_v2_p_no_win |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-07T07:32:00Z | 2026-07-07 15:32:00 | 0.9400 | 0.9200 | 0.0200 | 5.0100 | 36 | 35.0000 | decline | 32.0000 | 60.0000 | 28.0000 | True |  | True | False | 0.9510 | 2026-07-07 15:39:00 | 2026-07-07 16:00:00 | 28.0000 | 0.4848 | 0.6519 | 0.7244 | 0.2756 |
| 2026-07-07T07:46:00Z | 2026-07-07 15:46:00 |  |  |  |  | 36 | 35.0000 | decline | 46.0000 | 60.0000 | 14.0000 | True |  | True | False |  |  |  |  | 0.6652 | 0.4128 | 0.4759 | 0.5241 |
| 2026-07-07T08:02:00Z | 2026-07-07 16:02:00 |  |  |  |  | 36 | 35.0000 | decline | 62.0000 | 60.0000 | 0.0000 | True |  | True | False |  |  |  |  | 0.8399 | 0.1653 | 0.1776 | 0.8224 |
| 2026-07-07T08:15:00Z | 2026-07-07 16:15:00 |  |  |  |  | 36 | 36.1111 | at_high | 15.0000 | 60.0000 | 45.0000 | True | 45.0000 | True | False |  |  |  |  | 0.7673 | 0.3660 | 0.3825 | 0.6175 |
| 2026-07-07T08:30:00Z | 2026-07-07 16:30:00 |  |  |  |  | 36 | 36.1111 | at_high | 30.0000 | 60.0000 | 30.0000 | True |  | True | False |  |  |  |  | 0.7454 | 0.3069 | 0.3260 | 0.6740 |

## Data Gaps Before Live
- Preserve historical source_events/observation cache versions, not only latest, so cadence and next-observation hazard can be computed from source truth rather than paper-snapshot proxy.
- Persist PIT TAF snapshots and parsed TX/TN/TEMPO/BECMG/TSRA/SHRA features.
- Persist PIT vertical profile and full Open-Meteo hourly context variables; current mirror only has temperature curves.
- Run at least 10 active forward dates after the Chengdu failure with the v2 scorer before any tiny-live discussion.
