# Late-Window Residual Capture Per-Poll v3

Status: `snapshot`
Evidence: `paper_snapshots/snapshot_YYYYMMDD_HHMM.json` per polling cycle; settlement joined only after PIT candidate generation.

## Verdict
Per-poll replay confirms the Chengdu-style 39 NO opportunity exists in the snapshot layer, and a separate late-confirmed d1 NO policy captures the Chengdu 17:10-style 38 NO setup. Broad current YES / d1 NO / d2 NO first-cross remains mixed, while the plug-in late policies are positive but still low-sample after 2026-07-05..2026-07-06 label completion. Conclusion: `inconclusive_research_shadow_only`; do not change live. Positive forward point estimates exist for: d3_no.

## Data Snapshot
- paper snapshots scanned: 3467
- raw PIT leg rows: 4341
- strict residual rows before first-cross: 692
- first-cross rows: 264
- settlement-complete dates used for headline: through 2026-07-06
- incomplete dates excluded from headline ROI: none

## PIT Rules
- Decision denominator is each paper snapshot polling cycle, not hourly resampling.
- Candidate fields are only from the row itself: METAR current high/latest, forecast peak, top-of-book and depth.
- Strict executable residual means taker ask is 0.95-0.99 and top ask size or 5c ask depth is at least 5 shares.
- First-cross dedupe keeps the first city/date/leg/bracket time entering the strict band; later reprices are hold/no-add diagnostics.
- Settlement labels from `settlement_outcomes` are joined after candidate generation.

## First-Cross By Leg
| leg | period | rows | settled_rows | active_dates | cities | avg_entry | hit_rate | roi | roi_ci_low | roi_ci_high | pnl | depth_5c_sum |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | forward_2026-06-29_2026-07-06 | 46 | 46 | 7 | 17 | 0.969 | 0.935 | -0.037 | -0.115 | 0.033 | -1.630 | 61081.970 |
| current_yes | train_to_2026-06-28 | 1 | 1 | 1 | 1 | 0.960 | 1.000 | 0.040 |  |  | 0.038 | 573.080 |
| d1_no | forward_2026-06-29_2026-07-06 | 76 | 76 | 7 | 30 | 0.972 | 0.961 | -0.013 | -0.060 | 0.031 | -0.968 | 121093.980 |
| d1_no | train_to_2026-06-28 | 10 | 10 | 8 | 4 | 0.973 | 1.000 | 0.026 | 0.019 | 0.035 | 0.256 | 9046.750 |
| d2_no | forward_2026-06-29_2026-07-06 | 74 | 74 | 7 | 25 | 0.971 | 0.932 | -0.041 | -0.114 | 0.016 | -2.966 | 135720.010 |
| d2_no | train_to_2026-06-28 | 1 | 1 | 1 | 1 | 0.989 | 1.000 | 0.011 |  |  | 0.010 | 3535.570 |
| d3_no | forward_2026-06-29_2026-07-06 | 56 | 56 | 5 | 28 | 0.974 | 1.000 | 0.026 | 0.025 | 0.027 | 1.410 | 124688.910 |

## Pluggable Entry Policies
| entry_policy | period | rows | settled_rows | active_dates | cities | avg_entry | hit_rate | roi | roi_ci_low | roi_ci_high | pnl | depth_5c_sum |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes_late_survival | forward_2026-06-29_2026-07-06 | 15 | 15 | 5 | 10 | 0.979 | 1.000 | 0.021 | 0.015 | 0.027 | 0.306 | 31540.340 |
| d1_no_late_confirmed | forward_2026-06-29_2026-07-06 | 15 | 15 | 5 | 10 | 0.975 | 1.000 | 0.025 | 0.017 | 0.033 | 0.362 | 20019.580 |
| d1_no_late_confirmed_any_gap | forward_2026-06-29_2026-07-06 | 19 | 19 | 5 | 12 | 0.972 | 1.000 | 0.027 | 0.020 | 0.034 | 0.501 | 26351.930 |
| d2_no_late_confirmed | forward_2026-06-29_2026-07-06 | 4 | 4 | 4 | 4 | 0.960 | 1.000 | 0.040 | 0.032 | 0.047 | 0.152 | 4561.170 |
| d2_no_near_peak_at_high | forward_2026-06-29_2026-07-06 | 11 | 11 | 6 | 7 | 0.977 | 0.909 | -0.071 | -0.261 | 0.028 | -0.763 | 17505.700 |
| d3_no_residual | forward_2026-06-29_2026-07-06 | 56 | 56 | 5 | 28 | 0.974 | 1.000 | 0.026 | 0.025 | 0.027 | 1.410 | 124688.910 |
| late_residual_combo | forward_2026-06-29_2026-07-06 | 97 | 97 | 6 | 34 | 0.975 | 0.990 | 0.014 | -0.009 | 0.024 | 1.315 | 193754.530 |

## Policy By Leg
| entry_policy | leg | period | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes_late_survival | current_yes | forward_2026-06-29_2026-07-06 | 15 | 15 | 5 | 1.000 | 0.021 | 0.015 | 0.027 |
| d1_no_late_confirmed | d1_no | forward_2026-06-29_2026-07-06 | 15 | 15 | 5 | 1.000 | 0.025 | 0.017 | 0.033 |
| d1_no_late_confirmed_any_gap | d1_no | forward_2026-06-29_2026-07-06 | 19 | 19 | 5 | 1.000 | 0.027 | 0.020 | 0.034 |
| d2_no_late_confirmed | d2_no | forward_2026-06-29_2026-07-06 | 4 | 4 | 4 | 1.000 | 0.040 | 0.032 | 0.047 |
| d2_no_near_peak_at_high | d2_no | forward_2026-06-29_2026-07-06 | 11 | 11 | 6 | 0.909 | -0.071 | -0.261 | 0.028 |
| d3_no_residual | d3_no | forward_2026-06-29_2026-07-06 | 56 | 56 | 5 | 1.000 | 0.026 | 0.025 | 0.027 |
| late_residual_combo | current_yes | forward_2026-06-29_2026-07-06 | 15 | 15 | 5 | 1.000 | 0.021 | 0.015 | 0.027 |
| late_residual_combo | d1_no | forward_2026-06-29_2026-07-06 | 15 | 15 | 5 | 1.000 | 0.025 | 0.017 | 0.033 |
| late_residual_combo | d2_no | forward_2026-06-29_2026-07-06 | 11 | 11 | 6 | 0.909 | -0.071 | -0.261 | 0.028 |
| late_residual_combo | d3_no | forward_2026-06-29_2026-07-06 | 56 | 56 | 5 | 1.000 | 0.026 | 0.025 | 0.027 |

## Peak-Window Slices
| leg | peak_delta_bucket | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | peak_1h_to_15m_ahead | 5 | 5 | 5 | 0.800 | -0.168 | -0.584 | 0.044 |
| current_yes | near_peak | 11 | 11 | 6 | 1.000 | 0.035 | 0.023 | 0.042 |
| current_yes | post_peak_0_5_1_5h | 14 | 14 | 5 | 0.929 | -0.042 | -0.120 | 0.041 |
| current_yes | post_peak_gt1_5h | 17 | 17 | 6 | 0.941 | -0.035 | -0.137 | 0.027 |
| d1_no | peak_gt1h_ahead | 37 | 37 | 14 | 0.973 | -0.005 | -0.050 | 0.028 |
| d1_no | peak_1h_to_15m_ahead | 5 | 5 | 5 | 0.800 | -0.173 | -0.586 | 0.037 |
| d1_no | near_peak | 10 | 10 | 5 | 1.000 | 0.034 | 0.021 | 0.041 |
| d1_no | post_peak_0_5_1_5h | 17 | 17 | 6 | 0.941 | -0.031 | -0.116 | 0.038 |
| d1_no | post_peak_gt1_5h | 17 | 17 | 6 | 1.000 | 0.029 | 0.024 | 0.034 |
| d2_no | peak_gt1h_ahead | 46 | 46 | 8 | 0.957 | -0.017 | -0.121 | 0.030 |
| d2_no | peak_1h_to_15m_ahead | 8 | 8 | 5 | 0.750 | -0.232 | -0.388 | 0.038 |
| d2_no | near_peak | 9 | 9 | 4 | 1.000 | 0.027 | 0.020 | 0.034 |
| d2_no | post_peak_0_5_1_5h | 4 | 4 | 4 | 1.000 | 0.031 | 0.022 | 0.041 |
| d2_no | post_peak_gt1_5h | 8 | 8 | 5 | 0.875 | -0.098 | -0.249 | 0.034 |
| d3_no | peak_gt1h_ahead | 50 | 50 | 5 | 1.000 | 0.027 | 0.025 | 0.029 |
| d3_no | near_peak | 3 | 3 | 3 | 1.000 | 0.013 | 0.010 | 0.017 |
| d3_no | post_peak_0_5_1_5h | 2 | 2 | 2 | 1.000 | 0.035 | 0.021 | 0.050 |
| d3_no | post_peak_gt1_5h | 1 | 1 | 1 | 1.000 | 0.011 |  |  |

## Forecast-Gap Slices
| leg | forecast_gap_bucket | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | busted_lt_minus1 | 14 | 14 | 7 | 0.929 | -0.042 | -0.228 | 0.039 |
| current_yes | capped_minus1_0 | 17 | 17 | 6 | 0.941 | -0.033 | -0.150 | 0.035 |
| current_yes | room_0_1 | 4 | 4 | 4 | 1.000 | 0.029 | 0.015 | 0.044 |
| current_yes | room_1_2 | 5 | 5 | 5 | 0.800 | -0.178 | -0.591 | 0.042 |
| current_yes | room_gt2 | 7 | 7 | 5 | 1.000 | 0.041 | 0.031 | 0.047 |
| d1_no | busted_lt_minus1 | 13 | 13 | 6 | 0.923 | -0.049 | -0.235 | 0.035 |
| d1_no | capped_minus1_0 | 17 | 17 | 6 | 0.941 | -0.032 | -0.147 | 0.036 |
| d1_no | room_0_1 | 10 | 10 | 8 | 1.000 | 0.028 | 0.020 | 0.034 |
| d1_no | room_1_2 | 16 | 16 | 10 | 1.000 | 0.030 | 0.022 | 0.038 |
| d1_no | room_gt2 | 30 | 30 | 9 | 0.967 | -0.010 | -0.066 | 0.029 |
| d2_no | busted_lt_minus1 | 9 | 9 | 4 | 1.000 | 0.024 | 0.018 | 0.030 |
| d2_no | capped_minus1_0 | 12 | 12 | 5 | 1.000 | 0.032 | 0.028 | 0.045 |
| d2_no | room_0_1 | 7 | 7 | 4 | 1.000 | 0.030 | 0.021 | 0.046 |
| d2_no | room_1_2 | 9 | 9 | 5 | 0.778 | -0.195 | -0.482 | 0.031 |
| d2_no | room_gt2 | 38 | 38 | 8 | 0.921 | -0.055 | -0.156 | 0.024 |
| d3_no | busted_lt_minus1 | 2 | 2 | 2 | 1.000 | 0.013 | 0.010 | 0.017 |
| d3_no | capped_minus1_0 | 8 | 8 | 4 | 1.000 | 0.030 | 0.024 | 0.036 |
| d3_no | room_0_1 | 6 | 6 | 4 | 1.000 | 0.028 | 0.022 | 0.038 |
| d3_no | room_1_2 | 4 | 4 | 3 | 1.000 | 0.016 | 0.012 | 0.022 |
| d3_no | room_gt2 | 36 | 36 | 5 | 1.000 | 0.026 | 0.025 | 0.029 |

## Path-State Slices
| leg | path_state | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | at_high | 30 | 30 | 8 | 0.967 | -0.004 | -0.064 | 0.036 |
| current_yes | decline | 17 | 17 | 5 | 0.882 | -0.090 | -0.212 | 0.033 |
| d1_no | at_high | 59 | 59 | 14 | 0.983 | 0.009 | -0.023 | 0.031 |
| d1_no | decline | 27 | 27 | 10 | 0.926 | -0.047 | -0.143 | 0.030 |
| d2_no | at_high | 57 | 57 | 8 | 0.982 | 0.010 | -0.032 | 0.029 |
| d2_no | decline | 18 | 18 | 5 | 0.778 | -0.199 | -0.315 | -0.037 |
| d3_no | at_high | 40 | 40 | 5 | 1.000 | 0.025 | 0.022 | 0.027 |
| d3_no | decline | 16 | 16 | 5 | 1.000 | 0.029 | 0.023 | 0.038 |

## Basket
| period | rows | active_dates | cities | avg_legs | cost | pnl | roi | max_basket_loss | depth_5c_sum |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| forward_2026-06-29_2026-07-06 | 213 | 7 | 38 | 1.183 | 245.154 | -4.154 | -0.017 | -1.971 | 442584.870 |
| train_to_2026-06-28 | 11 | 8 | 4 | 1.091 | 11.695 | 0.305 | 0.026 | 0.010 | 13155.400 |

## Failure Cases
| city | target_date | ts_beijing | leg | bracket | entry_price | residual_points | pnl_per_share | running_value | forecast_max_native | forecast_peak_delta_hours_local | path_state | forecast_gap_bucket |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Shanghai | 2026-06-30 | 2026-06-30 15:26:03 | current_yes | 27 | 0.985 | 1.500 | -0.986 | 27 | 26.800 | 1.430 | at_high | capped_minus1_0 |
| Shanghai | 2026-06-30 | 2026-06-30 15:26:03 | d1_no | 28 | 0.985 | 1.500 | -0.986 | 27 | 26.800 | 1.430 | at_high | capped_minus1_0 |
| Guangzhou | 2026-07-01 | 2026-07-01 16:37:18 | current_yes | 32 | 0.980 | 2.000 | -0.981 | 32 | 33.100 | 3.620 | decline | room_1_2 |
| Lucknow | 2026-07-05 | 2026-07-05 15:12:26 | d2_no | 38 | 0.980 | 2.000 | -0.981 | 36 | 41.300 | -0.300 | decline | room_gt2 |
| Helsinki | 2026-07-04 | 2026-07-04 18:16:58 | d2_no | 20 | 0.970 | 3.000 | -0.971 | 18 | 20.100 | -0.730 | decline | room_gt2 |
| NYC | 2026-07-05 | 2026-07-05 15:12:26 | d1_no | 78-79 | 0.970 | 3.000 | -0.971 | 76 | 86.500 | -11.800 | decline | room_gt2 |
| Chongqing | 2026-06-30 | 2026-06-30 15:26:03 | current_yes | 26 | 0.960 | 4.000 | -0.962 | 26 | 24.800 | -0.570 | decline | busted_lt_minus1 |
| Chongqing | 2026-06-30 | 2026-06-30 15:26:03 | d1_no | 27 | 0.960 | 4.000 | -0.962 | 26 | 24.800 | -0.570 | decline | busted_lt_minus1 |
| Guangzhou | 2026-07-01 | 2026-07-01 15:15:54 | d2_no | 34 | 0.960 | 4.000 | -0.962 | 32 | 33.100 | 2.250 | decline | room_1_2 |
| Amsterdam | 2026-07-04 | 2026-07-04 18:33:47 | d2_no | 23 | 0.960 | 4.000 | -0.962 | 21 | 22.700 | -3.450 | decline | room_1_2 |
| Jeddah | 2026-07-04 | 2026-07-04 15:28:21 | d2_no | 36 | 0.950 | 5.000 | -0.952 | 34 | 38.200 | -1.530 | at_high | room_gt2 |

## Chengdu 2026-07-06 Case
Chengdu 2026-07-06 is retained as a per-poll case replay, not in headline ROI because settlement coverage for 2026-07-06 is incomplete in the local DB.
| ts_beijing | leg | bracket | entry_price | residual_points | running_value | forecast_max_native | forecast_peak_delta_hours_local | path_state |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-06 15:28:57 | d2_no | 39 | 0.950 | 5.000 | 37 | 37.600 | -0.530 | at_high |
| 2026-07-06 15:45:43 | d2_no | 39 | 0.960 | 4.000 | 37 | 37.600 | -0.250 | at_high |
| 2026-07-06 16:02:17 | d2_no | 39 | 0.988 | 1.200 | 37 | 37.600 | 0.030 | at_high |
| 2026-07-06 16:19:07 | d2_no | 39 | 0.988 | 1.200 | 37 | 37.600 | 0.320 | at_high |
| 2026-07-06 17:10:02 | current_yes | 37 | 0.989 | 1.100 | 37 | 37.800 | 1.170 | at_high |
| 2026-07-06 17:10:02 | d1_no | 38 | 0.989 | 1.100 | 37 | 37.800 | 1.170 | at_high |
| 2026-07-06 17:27:12 | current_yes | 37 | 0.963 | 3.700 | 37 | 37.800 | 1.450 | at_high |
| 2026-07-06 17:27:12 | d1_no | 38 | 0.960 | 4.000 | 37 | 37.800 | 1.450 | at_high |

## Chengdu Policy Rows
| entry_policy | ts_beijing | leg | bracket | entry_price | residual_points | running_value | forecast_max_native | forecast_peak_delta_hours_local | path_state |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes_late_survival | 2026-07-06 17:10:02 | current_yes | 37 | 0.989 | 1.100 | 37 | 37.800 | 1.170 | at_high |
| d1_no_late_confirmed | 2026-07-06 17:10:02 | d1_no | 38 | 0.989 | 1.100 | 37 | 37.800 | 1.170 | at_high |
| d1_no_late_confirmed_any_gap | 2026-07-06 17:10:02 | d1_no | 38 | 0.989 | 1.100 | 37 | 37.800 | 1.170 | at_high |
| d2_no_near_peak_at_high | 2026-07-06 15:28:57 | d2_no | 39 | 0.950 | 5.000 | 37 | 37.600 | -0.530 | at_high |
| late_residual_combo | 2026-07-06 15:28:57 | d2_no | 39 | 0.950 | 5.000 | 37 | 37.600 | -0.530 | at_high |
| late_residual_combo | 2026-07-06 17:10:02 | current_yes | 37 | 0.989 | 1.100 | 37 | 37.800 | 1.170 | at_high |
| late_residual_combo | 2026-07-06 17:10:02 | d1_no | 38 | 0.989 | 1.100 | 37 | 37.800 | 1.170 | at_high |

## Contract Gates
significance=FAIL/NA because current YES, d1 NO, and d2 NO forward date-block ROI CIs cross 0, while d3 NO is positive but below the active-date support gate; baseline=FAIL/NA because broad residual capture does not beat a robust zero/market baseline after fees; forward=FAIL/NA because 2026-06-29..2026-07-06 support is still small and unstable; conclusion=inconclusive.
