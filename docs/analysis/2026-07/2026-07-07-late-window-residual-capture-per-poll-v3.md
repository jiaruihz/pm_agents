# Late-Window Residual Capture Per-Poll v3

Status: `snapshot`
Evidence: `paper_snapshots/snapshot_YYYYMMDD_HHMM.json` per polling cycle; settlement joined only after PIT candidate generation.

## Verdict
Per-poll replay confirms the Chengdu-style 39 NO opportunity exists in the snapshot layer, and a separate late-confirmed d1 NO policy captures the Chengdu 17:10-style 38 NO setup. Broad current YES / d1 NO / d2 NO first-cross remains mixed, while the plug-in late policies are positive but low-sample, mostly 3-4 forward active dates. Conclusion: `inconclusive_research_shadow_only`; do not change live. Positive forward point estimates exist for: d3_no.

## Data Snapshot
- paper snapshots scanned: 3467
- raw PIT leg rows: 4341
- strict residual rows before first-cross: 692
- first-cross rows: 181
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
| current_yes | forward_2026-06-29_2026-07-04 | 32 | 32 | 5 | 17 | 0.968 | 0.906 | -0.066 | -0.153 | 0.036 | -2.034 | 40879.350 |
| current_yes | train_to_2026-06-28 | 1 | 1 | 1 | 1 | 0.960 | 1.000 | 0.040 |  |  | 0.038 | 573.080 |
| d1_no | forward_2026-06-29_2026-07-04 | 51 | 51 | 5 | 27 | 0.971 | 0.961 | -0.012 | -0.072 | 0.035 | -0.580 | 71302.390 |
| d1_no | train_to_2026-06-28 | 10 | 10 | 8 | 4 | 0.973 | 1.000 | 0.026 | 0.019 | 0.035 | 0.256 | 9046.750 |
| d2_no | forward_2026-06-29_2026-07-04 | 47 | 47 | 5 | 22 | 0.971 | 0.915 | -0.059 | -0.157 | 0.029 | -2.712 | 80023.070 |
| d2_no | train_to_2026-06-28 | 1 | 1 | 1 | 1 | 0.989 | 1.000 | 0.011 |  |  | 0.010 | 3535.570 |
| d3_no | forward_2026-06-29_2026-07-04 | 39 | 39 | 3 | 25 | 0.974 | 1.000 | 0.025 | 0.025 | 0.025 | 0.961 | 84070.910 |

## Pluggable Entry Policies
| entry_policy | period | rows | settled_rows | active_dates | cities | avg_entry | hit_rate | roi | roi_ci_low | roi_ci_high | pnl | depth_5c_sum |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes_late_survival | forward_2026-06-29_2026-07-04 | 10 | 10 | 3 | 8 | 0.980 | 1.000 | 0.019 | 0.016 | 0.020 | 0.190 | 19912.210 |
| d1_no_late_confirmed | forward_2026-06-29_2026-07-04 | 10 | 10 | 3 | 8 | 0.976 | 1.000 | 0.023 | 0.021 | 0.027 | 0.225 | 9911.200 |
| d1_no_late_confirmed_any_gap | forward_2026-06-29_2026-07-04 | 14 | 14 | 3 | 10 | 0.973 | 1.000 | 0.027 | 0.021 | 0.032 | 0.364 | 16243.550 |
| d2_no_late_confirmed | forward_2026-06-29_2026-07-04 | 3 | 3 | 3 | 3 | 0.957 | 1.000 | 0.043 | 0.040 | 0.050 | 0.124 | 2180.330 |
| d2_no_near_peak_at_high | forward_2026-06-29_2026-07-04 | 6 | 6 | 4 | 5 | 0.977 | 1.000 | 0.022 | 0.013 | 0.032 | 0.130 | 7614.730 |
| d3_no_residual | forward_2026-06-29_2026-07-04 | 39 | 39 | 3 | 25 | 0.974 | 1.000 | 0.025 | 0.025 | 0.025 | 0.961 | 84070.910 |
| late_residual_combo | forward_2026-06-29_2026-07-04 | 65 | 65 | 4 | 31 | 0.976 | 1.000 | 0.024 | 0.022 | 0.025 | 1.506 | 121509.050 |

## Policy By Leg
| entry_policy | leg | period | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes_late_survival | current_yes | forward_2026-06-29_2026-07-04 | 10 | 10 | 3 | 1.000 | 0.019 | 0.016 | 0.020 |
| d1_no_late_confirmed | d1_no | forward_2026-06-29_2026-07-04 | 10 | 10 | 3 | 1.000 | 0.023 | 0.021 | 0.027 |
| d1_no_late_confirmed_any_gap | d1_no | forward_2026-06-29_2026-07-04 | 14 | 14 | 3 | 1.000 | 0.027 | 0.021 | 0.032 |
| d2_no_late_confirmed | d2_no | forward_2026-06-29_2026-07-04 | 3 | 3 | 3 | 1.000 | 0.043 | 0.040 | 0.050 |
| d2_no_near_peak_at_high | d2_no | forward_2026-06-29_2026-07-04 | 6 | 6 | 4 | 1.000 | 0.022 | 0.013 | 0.032 |
| d3_no_residual | d3_no | forward_2026-06-29_2026-07-04 | 39 | 39 | 3 | 1.000 | 0.025 | 0.025 | 0.025 |
| late_residual_combo | current_yes | forward_2026-06-29_2026-07-04 | 10 | 10 | 3 | 1.000 | 0.019 | 0.016 | 0.020 |
| late_residual_combo | d1_no | forward_2026-06-29_2026-07-04 | 10 | 10 | 3 | 1.000 | 0.023 | 0.021 | 0.027 |
| late_residual_combo | d2_no | forward_2026-06-29_2026-07-04 | 6 | 6 | 4 | 1.000 | 0.022 | 0.013 | 0.032 |
| late_residual_combo | d3_no | forward_2026-06-29_2026-07-04 | 39 | 39 | 3 | 1.000 | 0.025 | 0.025 | 0.025 |

## Peak-Window Slices
| leg | peak_delta_bucket | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | peak_1h_to_15m_ahead | 5 | 5 | 5 | 0.800 | -0.168 | -0.584 | 0.044 |
| current_yes | near_peak | 6 | 6 | 4 | 1.000 | 0.033 | 0.010 | 0.045 |
| current_yes | post_peak_0_5_1_5h | 11 | 11 | 3 | 0.909 | -0.060 | -0.144 | 0.050 |
| current_yes | post_peak_gt1_5h | 11 | 11 | 4 | 0.909 | -0.069 | -0.196 | 0.027 |
| d1_no | peak_gt1h_ahead | 27 | 27 | 12 | 1.000 | 0.025 | 0.021 | 0.032 |
| d1_no | peak_1h_to_15m_ahead | 5 | 5 | 5 | 0.800 | -0.173 | -0.586 | 0.037 |
| d1_no | near_peak | 5 | 5 | 3 | 1.000 | 0.026 | 0.010 | 0.036 |
| d1_no | post_peak_0_5_1_5h | 14 | 14 | 4 | 0.929 | -0.041 | -0.136 | 0.044 |
| d1_no | post_peak_gt1_5h | 10 | 10 | 4 | 1.000 | 0.029 | 0.022 | 0.035 |
| d2_no | peak_gt1h_ahead | 28 | 28 | 6 | 0.929 | -0.047 | -0.198 | 0.028 |
| d2_no | peak_1h_to_15m_ahead | 5 | 5 | 3 | 0.800 | -0.183 | -0.321 | 0.040 |
| d2_no | near_peak | 7 | 7 | 3 | 1.000 | 0.031 | 0.027 | 0.034 |
| d2_no | post_peak_0_5_1_5h | 3 | 3 | 3 | 1.000 | 0.035 | 0.029 | 0.045 |
| d2_no | post_peak_gt1_5h | 5 | 5 | 3 | 0.800 | -0.174 | -0.311 | 0.040 |
| d3_no | peak_gt1h_ahead | 34 | 34 | 3 | 1.000 | 0.026 | 0.023 | 0.027 |
| d3_no | near_peak | 2 | 2 | 2 | 1.000 | 0.014 | 0.011 | 0.017 |
| d3_no | post_peak_0_5_1_5h | 2 | 2 | 2 | 1.000 | 0.035 | 0.021 | 0.050 |
| d3_no | post_peak_gt1_5h | 1 | 1 | 1 | 1.000 | 0.011 |  |  |

## Forecast-Gap Slices
| leg | forecast_gap_bucket | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | busted_lt_minus1 | 8 | 8 | 5 | 0.875 | -0.098 | -0.357 | 0.040 |
| current_yes | capped_minus1_0 | 13 | 13 | 4 | 0.923 | -0.051 | -0.187 | 0.037 |
| current_yes | room_0_1 | 3 | 3 | 3 | 1.000 | 0.035 | 0.027 | 0.050 |
| current_yes | room_1_2 | 4 | 4 | 4 | 0.750 | -0.234 | -0.746 | 0.040 |
| current_yes | room_gt2 | 5 | 5 | 3 | 1.000 | 0.046 | 0.040 | 0.050 |
| d1_no | busted_lt_minus1 | 8 | 8 | 4 | 0.875 | -0.098 | -0.360 | 0.039 |
| d1_no | capped_minus1_0 | 13 | 13 | 4 | 0.923 | -0.051 | -0.186 | 0.035 |
| d1_no | room_0_1 | 7 | 7 | 6 | 1.000 | 0.030 | 0.020 | 0.036 |
| d1_no | room_1_2 | 14 | 14 | 8 | 1.000 | 0.030 | 0.021 | 0.038 |
| d1_no | room_gt2 | 19 | 19 | 7 | 1.000 | 0.026 | 0.020 | 0.032 |
| d2_no | busted_lt_minus1 | 4 | 4 | 2 | 1.000 | 0.025 | 0.015 | 0.034 |
| d2_no | capped_minus1_0 | 10 | 10 | 3 | 1.000 | 0.028 | 0.025 | 0.034 |
| d2_no | room_0_1 | 5 | 5 | 3 | 1.000 | 0.023 | 0.020 | 0.030 |
| d2_no | room_1_2 | 8 | 8 | 4 | 0.750 | -0.222 | -0.583 | 0.034 |
| d2_no | room_gt2 | 21 | 21 | 6 | 0.905 | -0.072 | -0.228 | 0.026 |
| d3_no | busted_lt_minus1 | 1 | 1 | 1 | 1.000 | 0.017 |  |  |
| d3_no | capped_minus1_0 | 2 | 2 | 2 | 1.000 | 0.028 | 0.017 | 0.040 |
| d3_no | room_0_1 | 5 | 5 | 3 | 1.000 | 0.025 | 0.021 | 0.033 |
| d3_no | room_1_2 | 3 | 3 | 2 | 1.000 | 0.018 | 0.016 | 0.022 |
| d3_no | room_gt2 | 28 | 28 | 3 | 1.000 | 0.026 | 0.025 | 0.030 |

## Path-State Slices
| leg | path_state | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | at_high | 20 | 20 | 6 | 0.950 | -0.021 | -0.089 | 0.040 |
| current_yes | decline | 13 | 13 | 3 | 0.846 | -0.126 | -0.309 | 0.035 |
| d1_no | at_high | 41 | 41 | 12 | 0.976 | 0.002 | -0.031 | 0.034 |
| d1_no | decline | 20 | 20 | 8 | 0.950 | -0.022 | -0.146 | 0.033 |
| d2_no | at_high | 36 | 36 | 6 | 0.972 | -0.001 | -0.068 | 0.029 |
| d2_no | decline | 12 | 12 | 3 | 0.750 | -0.228 | -0.316 | 0.025 |
| d3_no | at_high | 25 | 25 | 3 | 1.000 | 0.024 | 0.019 | 0.027 |
| d3_no | decline | 14 | 14 | 3 | 1.000 | 0.028 | 0.021 | 0.040 |

## Basket
| period | rows | active_dates | cities | avg_legs | cost | pnl | roi | max_basket_loss | depth_5c_sum |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| forward_2026-06-29_2026-07-04 | 144 | 5 | 37 | 1.174 | 164.365 | -4.365 | -0.027 | -1.971 | 276275.720 |
| train_to_2026-06-28 | 11 | 8 | 4 | 1.091 | 11.695 | 0.305 | 0.026 | 0.010 | 13155.400 |

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
significance=FAIL/NA because current YES, d1 NO, and d2 NO forward date-block ROI CIs cross 0, while d3 NO is positive but below the active-date support gate; baseline=FAIL/NA because broad residual capture does not beat a robust zero/market baseline after fees; forward=FAIL/NA because 2026-06-29..2026-07-04 support is small and unstable; conclusion=inconclusive.
