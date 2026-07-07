# Late-Window Residual Capture Per-Poll v3

Status: `snapshot`
Evidence: `paper_snapshots/snapshot_YYYYMMDD_HHMM.json` per polling cycle; settlement joined only after PIT candidate generation.

## Verdict
Per-poll replay confirms the Chengdu-style 39 NO opportunity exists in the snapshot layer, but long-run first-cross evidence is mixed: broad current YES / d1 NO / d2 NO are not robust, and any positive d3 NO or narrow slice remains low-sample with CI crossing zero. Conclusion: `inconclusive_research_shadow_only`; do not change live. Positive forward point estimates exist for: d3_no.

## Data Snapshot
- paper snapshots scanned: 3467
- raw PIT leg rows: 4341
- strict residual rows before first-cross: 637
- first-cross rows: 171
- settlement-complete dates used for headline: through 2026-07-04
- incomplete dates excluded from headline ROI: 2026-07-05, 2026-07-06

## PIT Rules
- Decision denominator is each paper snapshot polling cycle, not hourly resampling.
- Candidate fields are only from the row itself: METAR current high/latest, forecast peak, top-of-book and depth.
- Strict executable residual means taker ask is 0.95-0.99 and top ask size or 5c ask depth is at least 5 shares.
- First-cross dedupe keeps the first city/date/leg/bracket time entering the strict band; later reprices are hold/no-add diagnostics.
- Settlement labels from `settlement_outcomes` are joined after candidate generation.

## First-Cross By Leg
| leg | period | rows | settled_rows | active_dates | cities | avg_entry | hit_rate | roi | roi_ci_low | roi_ci_high | pnl | depth_5c_sum |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | forward_2026-06-29_2026-07-04 | 29 | 29 | 5 | 17 | 0.977 | 0.897 | -0.083 | -0.165 | 0.026 | -2.356 | 40122.070 |
| current_yes | train_to_2026-06-28 | 1 | 1 | 1 | 1 | 0.960 | 1.000 | 0.040 |  |  | 0.038 | 573.080 |
| d1_no | forward_2026-06-29_2026-07-04 | 50 | 50 | 5 | 27 | 0.972 | 0.960 | -0.013 | -0.072 | 0.033 | -0.656 | 70566.520 |
| d1_no | train_to_2026-06-28 | 9 | 9 | 7 | 4 | 0.976 | 1.000 | 0.024 | 0.017 | 0.031 | 0.208 | 8596.630 |
| d2_no | forward_2026-06-29_2026-07-04 | 44 | 44 | 5 | 22 | 0.974 | 0.932 | -0.045 | -0.107 | 0.026 | -1.926 | 75374.380 |
| d2_no | train_to_2026-06-28 | 1 | 1 | 1 | 1 | 0.989 | 1.000 | 0.011 |  |  | 0.010 | 3535.570 |
| d3_no | forward_2026-06-29_2026-07-04 | 37 | 37 | 3 | 24 | 0.976 | 1.000 | 0.023 | 0.023 | 0.024 | 0.848 | 83806.600 |

## Peak-Window Slices
| leg | peak_delta_bucket | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | peak_1h_to_15m_ahead | 4 | 4 | 4 | 0.750 | -0.222 | -0.740 | 0.040 |
| current_yes | near_peak | 4 | 4 | 3 | 1.000 | 0.017 | 0.010 | 0.029 |
| current_yes | post_peak_0_5_1_5h | 9 | 9 | 4 | 0.889 | -0.093 | -0.218 | 0.026 |
| current_yes | post_peak_gt1_5h | 13 | 13 | 4 | 0.923 | -0.055 | -0.181 | 0.026 |
| d1_no | peak_gt1h_ahead | 26 | 26 | 11 | 1.000 | 0.023 | 0.019 | 0.029 |
| d1_no | peak_1h_to_15m_ahead | 5 | 5 | 5 | 0.800 | -0.173 | -0.586 | 0.037 |
| d1_no | near_peak | 4 | 4 | 3 | 1.000 | 0.020 | 0.010 | 0.029 |
| d1_no | post_peak_0_5_1_5h | 14 | 14 | 4 | 0.929 | -0.041 | -0.136 | 0.044 |
| d1_no | post_peak_gt1_5h | 10 | 10 | 4 | 1.000 | 0.027 | 0.022 | 0.030 |
| d2_no | peak_gt1h_ahead | 26 | 26 | 6 | 0.962 | -0.015 | -0.102 | 0.026 |
| d2_no | peak_1h_to_15m_ahead | 5 | 5 | 3 | 0.800 | -0.183 | -0.321 | 0.040 |
| d2_no | near_peak | 4 | 4 | 3 | 1.000 | 0.017 | 0.016 | 0.019 |
| d2_no | post_peak_0_5_1_5h | 5 | 5 | 3 | 1.000 | 0.029 | 0.020 | 0.045 |
| d2_no | post_peak_gt1_5h | 5 | 5 | 3 | 0.800 | -0.174 | -0.311 | 0.040 |
| d3_no | peak_gt1h_ahead | 32 | 32 | 3 | 1.000 | 0.024 | 0.023 | 0.025 |
| d3_no | near_peak | 2 | 2 | 2 | 1.000 | 0.014 | 0.011 | 0.017 |
| d3_no | post_peak_0_5_1_5h | 2 | 2 | 2 | 1.000 | 0.026 | 0.021 | 0.030 |
| d3_no | post_peak_gt1_5h | 1 | 1 | 1 | 1.000 | 0.011 |  |  |

## Forecast-Gap Slices
| leg | forecast_gap_bucket | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | busted_lt_minus1 | 7 | 7 | 4 | 0.857 | -0.118 | -0.413 | 0.034 |
| current_yes | capped_minus1_0 | 12 | 12 | 4 | 0.917 | -0.064 | -0.187 | 0.025 |
| current_yes | room_0_1 | 2 | 2 | 2 | 1.000 | 0.028 | 0.027 | 0.029 |
| current_yes | room_1_2 | 4 | 4 | 4 | 0.750 | -0.239 | -0.746 | 0.017 |
| current_yes | room_gt2 | 5 | 5 | 3 | 1.000 | 0.027 | 0.019 | 0.040 |
| d1_no | busted_lt_minus1 | 8 | 8 | 4 | 0.875 | -0.100 | -0.360 | 0.034 |
| d1_no | capped_minus1_0 | 12 | 12 | 4 | 0.917 | -0.059 | -0.186 | 0.033 |
| d1_no | room_0_1 | 7 | 7 | 6 | 1.000 | 0.030 | 0.020 | 0.036 |
| d1_no | room_1_2 | 13 | 13 | 7 | 1.000 | 0.028 | 0.018 | 0.037 |
| d1_no | room_gt2 | 19 | 19 | 7 | 1.000 | 0.025 | 0.019 | 0.031 |
| d2_no | busted_lt_minus1 | 4 | 4 | 2 | 1.000 | 0.015 | 0.014 | 0.015 |
| d2_no | capped_minus1_0 | 9 | 9 | 3 | 1.000 | 0.025 | 0.019 | 0.029 |
| d2_no | room_0_1 | 6 | 6 | 3 | 1.000 | 0.024 | 0.022 | 0.030 |
| d2_no | room_1_2 | 6 | 6 | 4 | 0.667 | -0.311 | -0.740 | 0.027 |
| d2_no | room_gt2 | 20 | 20 | 6 | 0.950 | -0.027 | -0.120 | 0.026 |
| d3_no | busted_lt_minus1 | 1 | 1 | 1 | 1.000 | 0.017 |  |  |
| d3_no | capped_minus1_0 | 2 | 2 | 2 | 1.000 | 0.028 | 0.017 | 0.040 |
| d3_no | room_0_1 | 5 | 5 | 3 | 1.000 | 0.025 | 0.021 | 0.033 |
| d3_no | room_1_2 | 3 | 3 | 2 | 1.000 | 0.018 | 0.016 | 0.022 |
| d3_no | room_gt2 | 26 | 26 | 3 | 1.000 | 0.024 | 0.023 | 0.026 |

## Path-State Slices
| leg | path_state | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | at_high | 18 | 18 | 6 | 0.944 | -0.034 | -0.100 | 0.031 |
| current_yes | decline | 12 | 12 | 3 | 0.833 | -0.146 | -0.314 | 0.023 |
| d1_no | at_high | 39 | 39 | 11 | 0.974 | -0.000 | -0.035 | 0.032 |
| d1_no | decline | 20 | 20 | 8 | 0.950 | -0.023 | -0.146 | 0.031 |
| d2_no | at_high | 33 | 33 | 6 | 1.000 | 0.023 | 0.021 | 0.027 |
| d2_no | decline | 12 | 12 | 3 | 0.750 | -0.229 | -0.316 | 0.025 |
| d3_no | at_high | 24 | 24 | 3 | 1.000 | 0.022 | 0.019 | 0.025 |
| d3_no | decline | 13 | 13 | 3 | 1.000 | 0.025 | 0.021 | 0.033 |

## Basket
| period | rows | active_dates | cities | avg_legs | cost | pnl | roi | max_basket_loss | depth_5c_sum |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| forward_2026-06-29_2026-07-04 | 140 | 5 | 36 | 1.143 | 156.090 | -4.090 | -0.026 | -1.971 | 269869.570 |
| train_to_2026-06-28 | 10 | 7 | 4 | 1.100 | 10.743 | 0.257 | 0.024 | 0.010 | 12705.280 |

## Failure Cases
| city | target_date | ts_beijing | leg | bracket | entry_price | residual_points | pnl_per_share | running_value | forecast_max_native | forecast_peak_delta_hours_local | path_state | forecast_gap_bucket |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Shanghai | 2026-06-30 | 2026-06-30 15:26:03 | current_yes | 27 | 0.985 | 1.500 | -0.986 | 27 | 26.800 | 1.430 | at_high | capped_minus1_0 |
| Shanghai | 2026-06-30 | 2026-06-30 15:26:03 | d1_no | 28 | 0.985 | 1.500 | -0.986 | 27 | 26.800 | 1.430 | at_high | capped_minus1_0 |
| Guangzhou | 2026-07-01 | 2026-07-01 16:37:18 | current_yes | 32 | 0.980 | 2.000 | -0.981 | 32 | 33.100 | 3.620 | decline | room_1_2 |
| Helsinki | 2026-07-04 | 2026-07-04 18:16:58 | d2_no | 20 | 0.970 | 3.000 | -0.971 | 18 | 20.100 | -0.730 | decline | room_gt2 |
| Chongqing | 2026-06-30 | 2026-06-30 15:26:03 | current_yes | 26 | 0.960 | 4.000 | -0.962 | 26 | 24.800 | -0.570 | decline | busted_lt_minus1 |
| Chongqing | 2026-06-30 | 2026-06-30 15:26:03 | d1_no | 27 | 0.960 | 4.000 | -0.962 | 26 | 24.800 | -0.570 | decline | busted_lt_minus1 |
| Guangzhou | 2026-07-01 | 2026-07-01 15:15:54 | d2_no | 34 | 0.960 | 4.000 | -0.962 | 32 | 33.100 | 2.250 | decline | room_1_2 |
| Amsterdam | 2026-07-04 | 2026-07-04 18:33:47 | d2_no | 23 | 0.960 | 4.000 | -0.962 | 21 | 22.700 | -3.450 | decline | room_1_2 |

## Chengdu 2026-07-06 Case
Chengdu 2026-07-06 is retained as a per-poll case replay, not in headline ROI because settlement coverage for 2026-07-06 is incomplete in the local DB.
| ts_beijing | leg | bracket | entry_price | residual_points | running_value | forecast_max_native | forecast_peak_delta_hours_local | path_state |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-06 15:45:43 | d2_no | 39 | 0.960 | 4.000 | 37 | 37.600 | -0.250 | at_high |
| 2026-07-06 16:02:17 | d2_no | 39 | 0.988 | 1.200 | 37 | 37.600 | 0.030 | at_high |
| 2026-07-06 16:19:07 | d2_no | 39 | 0.988 | 1.200 | 37 | 37.600 | 0.320 | at_high |
| 2026-07-06 17:10:02 | current_yes | 37 | 0.989 | 1.100 | 37 | 37.800 | 1.170 | at_high |
| 2026-07-06 17:10:02 | d1_no | 38 | 0.989 | 1.100 | 37 | 37.800 | 1.170 | at_high |
| 2026-07-06 17:27:12 | current_yes | 37 | 0.963 | 3.700 | 37 | 37.800 | 1.450 | at_high |
| 2026-07-06 17:27:12 | d1_no | 38 | 0.960 | 4.000 | 37 | 37.800 | 1.450 | at_high |

## Contract Gates
significance=FAIL/NA because current YES, d1 NO, and d2 NO forward date-block ROI CIs cross 0, while d3 NO is positive but below the active-date support gate; baseline=FAIL/NA because broad residual capture does not beat a robust zero/market baseline after fees; forward=FAIL/NA because 2026-06-29..2026-07-04 support is small and unstable; conclusion=inconclusive.
