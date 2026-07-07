# Late-Window Residual Capture Heating-Done v1

Status: `snapshot`
Evidence: `paper_snapshots/snapshot_YYYYMMDD_HHMM.json` per polling cycle; settlement joined only after PIT candidate generation.

## Verdict
Per-poll replay now separates physical residual state from price: entry is based on `heating_done_score_v1` and bracket-specific `leg_residual_done_score_v1`, while price is only reported as execution/EV. This fixes the overly rigid 17:00 and 1-5c entry gates, but the available settled forward window is still too small for live promotion. Conclusion: `inconclusive_research_shadow_only`; do not change live. Positive forward point estimates exist for feature policies: current_yes_heating_done, d1_no_heating_done, d2_no_residual_done, d3_no_residual_done, heating_done_residual_combo.

## Data Snapshot
- paper snapshots scanned: 3467
- raw PIT leg rows: 4341
- book-executable rows before first-cross, no price filter: 4012
- legacy strict 1-5pt rows, diagnostics only: 692
- first-cross rows: 688
- settlement-complete dates used for headline: through 2026-07-04
- incomplete dates excluded from headline ROI: 2026-07-05, 2026-07-06

## PIT Rules
- Decision denominator is each paper snapshot polling cycle, not hourly resampling.
- Candidate fields are only from the row itself: METAR current high/latest, forecast peak, top-of-book and depth.
- Physical entry uses `heating_done_score_v1` / `leg_residual_done_score_v1`, not fixed 17:00 or a 1-5c price band.
- Book-executable means a finite taker ask and top ask size or 5c ask depth is at least 5 shares.
- First-cross dedupe keeps the first city/date/leg/bracket time entering the feature policy; later reprices are hold/no-add diagnostics.
- Settlement labels from `settlement_outcomes` are joined after candidate generation.

## First-Cross By Leg
| leg | period | rows | settled_rows | active_dates | cities | avg_entry | hit_rate | roi | roi_ci_low | roi_ci_high | pnl | depth_5c_sum |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | forward_2026-06-29_2026-07-04 | 116 | 115 | 5 | 30 | 0.519 | 0.539 | 0.031 | -0.086 | 0.169 | 1.848 | 65119.000 |
| current_yes | train_to_2026-06-28 | 15 | 15 | 6 | 4 | 0.253 | 0.267 | 0.044 | -0.184 | 0.232 | 0.169 | 5696.130 |
| d1_no | forward_2026-06-29_2026-07-04 | 173 | 171 | 5 | 38 | 0.843 | 0.854 | 0.008 | -0.024 | 0.063 | 1.140 | 411051.340 |
| d1_no | train_to_2026-06-28 | 33 | 33 | 8 | 4 | 0.923 | 0.879 | -0.050 | -0.104 | 0.009 | -1.534 | 23357.650 |
| d2_no | forward_2026-06-29_2026-07-04 | 177 | 175 | 5 | 43 | 0.879 | 0.874 | -0.010 | -0.049 | 0.014 | -1.497 | 514180.610 |
| d2_no | train_to_2026-06-28 | 7 | 7 | 6 | 3 | 0.902 | 0.714 | -0.211 | -0.575 | 0.005 | -1.335 | 12907.410 |
| d3_no | forward_2026-06-29_2026-07-04 | 162 | 160 | 5 | 41 | 0.904 | 0.900 | -0.008 | -0.044 | 0.035 | -1.104 | 626385.520 |
| d3_no | train_to_2026-06-28 | 5 | 5 | 5 | 2 | 0.932 | 0.800 | -0.143 | -0.506 | 0.005 | -0.670 | 21693.590 |

## Pluggable Entry Policies
| entry_policy | period | rows | settled_rows | active_dates | cities | avg_entry | hit_rate | roi | roi_ci_low | roi_ci_high | pnl | depth_5c_sum |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes_heating_done | forward_2026-06-29_2026-07-04 | 52 | 52 | 5 | 21 | 0.851 | 0.865 | 0.013 | -0.061 | 0.092 | 0.564 | 47170.320 |
| current_yes_heating_done | train_to_2026-06-28 | 1 | 1 | 1 | 1 | 0.999 | 1.000 | 0.001 |  |  | 0.001 | 120.620 |
| d1_no_heating_done | forward_2026-06-29_2026-07-04 | 48 | 48 | 4 | 20 | 0.878 | 0.917 | 0.040 | -0.070 | 0.142 | 1.690 | 44016.900 |
| d2_no_residual_done | forward_2026-06-29_2026-07-04 | 70 | 69 | 5 | 24 | 0.873 | 0.899 | 0.024 | -0.015 | 0.088 | 1.475 | 73611.750 |
| d3_no_residual_done | forward_2026-06-29_2026-07-04 | 65 | 64 | 3 | 23 | 0.887 | 0.906 | 0.018 | -0.018 | 0.039 | 1.002 | 103373.750 |
| heating_done_residual_combo | forward_2026-06-29_2026-07-04 | 235 | 233 | 5 | 29 | 0.873 | 0.897 | 0.023 | -0.037 | 0.076 | 4.731 | 268172.720 |
| heating_done_residual_combo | train_to_2026-06-28 | 1 | 1 | 1 | 1 | 0.999 | 1.000 | 0.001 |  |  | 0.001 | 120.620 |

## Policy By Leg
| entry_policy | leg | period | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes_heating_done | current_yes | forward_2026-06-29_2026-07-04 | 52 | 52 | 5 | 0.865 | 0.013 | -0.061 | 0.092 |
| current_yes_heating_done | current_yes | train_to_2026-06-28 | 1 | 1 | 1 | 1.000 | 0.001 |  |  |
| d1_no_heating_done | d1_no | forward_2026-06-29_2026-07-04 | 48 | 48 | 4 | 0.917 | 0.040 | -0.070 | 0.142 |
| d2_no_residual_done | d2_no | forward_2026-06-29_2026-07-04 | 70 | 69 | 5 | 0.899 | 0.024 | -0.015 | 0.088 |
| d3_no_residual_done | d3_no | forward_2026-06-29_2026-07-04 | 65 | 64 | 3 | 0.906 | 0.018 | -0.018 | 0.039 |
| heating_done_residual_combo | current_yes | forward_2026-06-29_2026-07-04 | 52 | 52 | 5 | 0.865 | 0.013 | -0.061 | 0.092 |
| heating_done_residual_combo | current_yes | train_to_2026-06-28 | 1 | 1 | 1 | 1.000 | 0.001 |  |  |
| heating_done_residual_combo | d1_no | forward_2026-06-29_2026-07-04 | 48 | 48 | 4 | 0.917 | 0.040 | -0.070 | 0.142 |
| heating_done_residual_combo | d2_no | forward_2026-06-29_2026-07-04 | 70 | 69 | 5 | 0.899 | 0.024 | -0.015 | 0.088 |
| heating_done_residual_combo | d3_no | forward_2026-06-29_2026-07-04 | 65 | 64 | 3 | 0.906 | 0.018 | -0.018 | 0.039 |

## Peak-Window Slices
| leg | peak_delta_bucket | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | peak_gt1h_ahead | 68 | 67 | 10 | 0.164 | -0.032 | -0.524 | 0.363 |
| current_yes | peak_1h_to_15m_ahead | 17 | 17 | 6 | 0.824 | 0.084 | -0.026 | 0.193 |
| current_yes | near_peak | 13 | 13 | 3 | 0.923 | 0.143 | 0.117 | 0.175 |
| current_yes | post_peak_0_5_1_5h | 11 | 11 | 4 | 0.909 | 0.018 | -0.184 | 0.462 |
| current_yes | post_peak_gt1_5h | 22 | 22 | 4 | 0.864 | -0.020 | -0.064 | 0.013 |
| d1_no | peak_gt1h_ahead | 151 | 149 | 13 | 0.839 | -0.020 | -0.053 | 0.022 |
| d1_no | peak_1h_to_15m_ahead | 14 | 14 | 5 | 0.857 | 0.039 | -0.133 | 0.202 |
| d1_no | near_peak | 13 | 13 | 3 | 0.923 | 0.122 | 0.105 | 0.146 |
| d1_no | post_peak_0_5_1_5h | 10 | 10 | 3 | 0.800 | -0.031 | -0.242 | 0.502 |
| d1_no | post_peak_gt1_5h | 18 | 18 | 3 | 1.000 | 0.034 | 0.016 | 0.056 |
| d2_no | peak_gt1h_ahead | 142 | 140 | 11 | 0.843 | -0.017 | -0.087 | 0.020 |
| d2_no | peak_1h_to_15m_ahead | 12 | 12 | 4 | 0.917 | -0.029 | -0.138 | 0.094 |
| d2_no | near_peak | 10 | 10 | 3 | 1.000 | 0.025 | 0.017 | 0.040 |
| d2_no | post_peak_0_5_1_5h | 10 | 10 | 3 | 1.000 | 0.027 | 0.001 | 0.124 |
| d2_no | post_peak_gt1_5h | 10 | 10 | 2 | 0.900 | -0.092 | -0.320 | 0.004 |
| d3_no | peak_gt1h_ahead | 141 | 139 | 10 | 0.878 | -0.018 | -0.070 | 0.021 |
| d3_no | peak_1h_to_15m_ahead | 8 | 8 | 3 | 1.000 | 0.045 | 0.002 | 0.092 |
| d3_no | near_peak | 8 | 8 | 3 | 1.000 | 0.004 | 0.002 | 0.005 |
| d3_no | post_peak_0_5_1_5h | 3 | 3 | 2 | 1.000 | 0.008 | 0.003 | 0.011 |
| d3_no | post_peak_gt1_5h | 7 | 7 | 2 | 1.000 | 0.004 | 0.001 | 0.010 |

## Forecast-Gap Slices
| leg | forecast_gap_bucket | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | busted_lt_minus1 | 25 | 25 | 7 | 0.800 | 0.053 | -0.075 | 0.251 |
| current_yes | capped_minus1_0 | 39 | 38 | 7 | 0.711 | 0.059 | -0.085 | 0.226 |
| current_yes | room_0_1 | 25 | 25 | 10 | 0.240 | -0.263 | -0.438 | -0.150 |
| current_yes | room_1_2 | 20 | 20 | 7 | 0.350 | 0.039 | -0.238 | 0.333 |
| current_yes | room_gt2 | 22 | 22 | 6 | 0.273 | 0.303 | -0.667 | 1.444 |
| d1_no | busted_lt_minus1 | 21 | 21 | 5 | 0.857 | 0.011 | -0.118 | 0.141 |
| d1_no | capped_minus1_0 | 36 | 35 | 6 | 0.829 | 0.035 | -0.096 | 0.214 |
| d1_no | room_0_1 | 29 | 28 | 11 | 0.536 | -0.253 | -0.415 | -0.098 |
| d1_no | room_1_2 | 35 | 35 | 11 | 0.943 | 0.101 | -0.031 | 0.208 |
| d1_no | room_gt2 | 85 | 85 | 13 | 0.941 | 0.006 | -0.051 | 0.060 |
| d2_no | busted_lt_minus1 | 12 | 12 | 3 | 1.000 | 0.016 | 0.001 | 0.044 |
| d2_no | capped_minus1_0 | 28 | 27 | 5 | 0.889 | -0.023 | -0.160 | 0.053 |
| d2_no | room_0_1 | 23 | 23 | 5 | 0.913 | 0.196 | 0.037 | 0.424 |
| d2_no | room_1_2 | 29 | 28 | 7 | 0.464 | -0.393 | -0.658 | -0.221 |
| d2_no | room_gt2 | 92 | 92 | 9 | 0.957 | 0.030 | -0.024 | 0.076 |
| d3_no | busted_lt_minus1 | 6 | 6 | 3 | 1.000 | 0.005 | 0.001 | 0.010 |
| d3_no | capped_minus1_0 | 21 | 20 | 3 | 0.950 | -0.020 | -0.098 | 0.019 |
| d3_no | room_0_1 | 16 | 16 | 3 | 0.938 | 0.015 | -0.085 | 0.132 |
| d3_no | room_1_2 | 18 | 18 | 3 | 0.833 | 0.114 | -0.085 | 0.357 |
| d3_no | room_gt2 | 106 | 105 | 10 | 0.886 | -0.033 | -0.119 | 0.026 |

## Path-State Slices
| leg | path_state | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | at_high | 99 | 98 | 11 | 0.408 | 0.047 | -0.103 | 0.220 |
| current_yes | decline | 32 | 32 | 8 | 0.812 | 0.009 | -0.066 | 0.071 |
| d1_no | at_high | 163 | 161 | 13 | 0.826 | -0.012 | -0.053 | 0.035 |
| d1_no | decline | 43 | 43 | 8 | 0.977 | 0.031 | -0.033 | 0.109 |
| d2_no | at_high | 145 | 143 | 11 | 0.839 | -0.020 | -0.106 | 0.023 |
| d2_no | decline | 39 | 39 | 6 | 0.974 | -0.009 | -0.058 | 0.048 |
| d3_no | at_high | 135 | 133 | 10 | 0.872 | -0.019 | -0.080 | 0.025 |
| d3_no | decline | 32 | 32 | 4 | 1.000 | 0.015 | 0.008 | 0.024 |

## Heating-Done Slices
| leg | heating_done_bucket_v1 | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | heating_done_confirmed | 18 | 18 | 6 | 0.944 | 0.007 | 0.001 | 0.012 |
| current_yes | heating_done_probable | 14 | 14 | 4 | 0.857 | 0.017 | -0.155 | 0.224 |
| current_yes | heating_done_uncertain | 10 | 10 | 4 | 0.800 | -0.013 | -0.148 | 0.256 |
| current_yes | near_peak_capping | 7 | 7 | 4 | 0.857 | -0.025 | -0.544 | 0.255 |
| current_yes | runway_still_open | 82 | 81 | 11 | 0.284 | 0.093 | -0.133 | 0.354 |
| d1_no | heating_done_confirmed | 14 | 14 | 3 | 1.000 | 0.010 | 0.002 | 0.015 |
| d1_no | heating_done_probable | 13 | 13 | 4 | 0.923 | 0.018 | -0.155 | 0.228 |
| d1_no | heating_done_uncertain | 10 | 10 | 4 | 0.900 | 0.181 | 0.076 | 0.282 |
| d1_no | near_peak_capping | 6 | 6 | 3 | 0.833 | -0.070 | -1.000 | 0.297 |
| d1_no | runway_still_open | 163 | 161 | 13 | 0.839 | -0.013 | -0.047 | 0.024 |
| d2_no | heating_done_confirmed | 8 | 8 | 3 | 1.000 | 0.003 | 0.001 | 0.004 |
| d2_no | heating_done_probable | 8 | 8 | 3 | 1.000 | 0.006 | 0.001 | 0.012 |
| d2_no | heating_done_uncertain | 9 | 9 | 3 | 0.778 | -0.194 | -0.296 | 0.035 |
| d2_no | near_peak_capping | 5 | 5 | 3 | 1.000 | 0.007 | 0.001 | 0.009 |
| d2_no | runway_still_open | 154 | 152 | 11 | 0.855 | -0.010 | -0.075 | 0.025 |
| d3_no | heating_done_confirmed | 4 | 4 | 1 | 1.000 | 0.001 |  |  |
| d3_no | heating_done_probable | 3 | 3 | 3 | 1.000 | 0.004 | 0.002 | 0.009 |
| d3_no | heating_done_uncertain | 9 | 9 | 3 | 1.000 | 0.004 | 0.003 | 0.004 |
| d3_no | near_peak_capping | 2 | 2 | 2 | 1.000 | 0.001 | 0.001 | 0.002 |
| d3_no | runway_still_open | 149 | 147 | 10 | 0.884 | -0.014 | -0.067 | 0.028 |

## Leg Residual-Done Slices
| leg | leg_residual_done_bucket_v1 | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | leg_residual_done_confirmed | 18 | 18 | 6 | 0.944 | 0.007 | 0.001 | 0.012 |
| current_yes | leg_residual_done_probable | 14 | 14 | 4 | 0.857 | 0.017 | -0.155 | 0.224 |
| current_yes | leg_residual_marginal | 9 | 9 | 4 | 0.889 | 0.053 | -0.459 | 0.239 |
| current_yes | leg_residual_not_done | 90 | 89 | 11 | 0.326 | 0.047 | -0.071 | 0.221 |
| d1_no | leg_residual_done_confirmed | 14 | 14 | 3 | 1.000 | 0.010 | 0.002 | 0.015 |
| d1_no | leg_residual_done_probable | 13 | 13 | 4 | 0.923 | 0.018 | -0.155 | 0.228 |
| d1_no | leg_residual_marginal | 9 | 9 | 3 | 0.778 | -0.002 | -1.000 | 0.190 |
| d1_no | leg_residual_not_done | 170 | 168 | 13 | 0.845 | -0.005 | -0.041 | 0.029 |
| d2_no | leg_residual_done_confirmed | 30 | 30 | 3 | 0.933 | -0.053 | -0.103 | 0.008 |
| d2_no | leg_residual_done_probable | 17 | 16 | 5 | 0.875 | 0.025 | -0.243 | 0.186 |
| d2_no | leg_residual_marginal | 19 | 19 | 5 | 0.895 | 0.249 | 0.044 | 0.589 |
| d2_no | leg_residual_not_done | 118 | 117 | 11 | 0.846 | -0.048 | -0.130 | -0.011 |
| d3_no | leg_residual_done_confirmed | 18 | 18 | 3 | 1.000 | 0.003 | 0.002 | 0.005 |
| d3_no | leg_residual_done_probable | 14 | 13 | 3 | 0.923 | -0.031 | -0.216 | 0.018 |
| d3_no | leg_residual_marginal | 30 | 30 | 3 | 0.833 | 0.054 | -0.038 | 0.125 |
| d3_no | leg_residual_not_done | 105 | 104 | 10 | 0.894 | -0.028 | -0.110 | 0.026 |

## Basket
| period | rows | active_dates | cities | avg_legs | cost | pnl | roi | max_basket_loss | depth_5c_sum |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| forward_2026-06-29_2026-07-04 | 225 | 5 | 45 | 2.760 | 504.613 | 0.387 | 0.001 | -1.971 | 1616736.470 |
| train_to_2026-06-28 | 48 | 8 | 4 | 1.250 | 45.370 | -3.370 | -0.074 | -0.937 | 63654.780 |

## Failure Cases
| city | target_date | ts_beijing | leg | bracket | entry_price | residual_points | pnl_per_share | running_value | forecast_max_native | forecast_peak_delta_hours_local | heating_done_score_v1 | leg_residual_done_score_v1 | path_state | forecast_gap_bucket |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Shanghai | 2026-06-30 | 2026-06-30 15:26:03 | current_yes | 27 | 0.985 | 1.500 | -0.986 | 27 | 26.800 | 1.430 | 0.580 | 0.580 | at_high | capped_minus1_0 |
| Shanghai | 2026-06-30 | 2026-06-30 15:26:03 | d1_no | 28 | 0.985 | 1.500 | -0.986 | 27 | 26.800 | 1.430 | 0.580 | 0.580 | at_high | capped_minus1_0 |
| Chongqing | 2026-06-30 | 2026-06-30 15:26:03 | current_yes | 26 | 0.960 | 4.000 | -0.962 | 26 | 24.800 | -0.570 | 0.450 | 0.450 | decline | busted_lt_minus1 |
| Chongqing | 2026-06-30 | 2026-06-30 15:26:03 | d1_no | 27 | 0.960 | 4.000 | -0.962 | 26 | 24.800 | -0.570 | 0.450 | 0.450 | decline | busted_lt_minus1 |
| Guangzhou | 2026-07-01 | 2026-07-01 15:15:54 | d2_no | 34 | 0.960 | 4.000 | -0.962 | 32 | 33.100 | 2.250 | 0.350 | 0.700 | decline | room_1_2 |
| Chongqing | 2026-07-04 | 2026-07-04 15:11:44 | d2_no | 34 | 0.919 | 8.100 | -0.923 | 32 | 31.500 | -0.820 | 0.300 | 0.750 | at_high | capped_minus1_0 |
| Guangzhou | 2026-07-01 | 2026-07-01 15:15:54 | current_yes | 32 | 0.749 | 25.100 | -0.758 | 32 | 33.100 | 2.250 | 0.350 | 0.350 | decline | room_1_2 |
| CapeTown | 2026-06-30 | 2026-06-30 18:39:09 | d2_no | 16 | 0.720 | 28.000 | -0.730 | 14 | 14.800 | -1.350 | 0.000 | 0.450 | at_high | room_0_1 |
| Ankara | 2026-06-30 | 2026-06-30 17:56:13 | d3_no | 32 | 0.710 | 29.000 | -0.720 | 29 | 30.100 | -4.070 | 0.000 | 0.450 | at_high | room_1_2 |
| Helsinki | 2026-06-30 | 2026-06-30 16:40:31 | d2_no | 26 | 0.710 | 29.000 | -0.720 | 24 | 24.900 | -5.330 | 0.000 | 0.450 | at_high | room_0_1 |
| Moscow | 2026-07-01 | 2026-07-01 15:15:54 | d3_no | 29 | 0.700 | 30.000 | -0.710 | 26 | 26.900 | -4.750 | 0.000 | 0.450 | at_high | room_0_1 |
| Ankara | 2026-07-02 | 2026-07-02 17:29:28 | d2_no | 34 | 0.660 | 34.000 | -0.671 | 32 | 31.900 | -3.520 | 0.100 | 0.550 | at_high | capped_minus1_0 |
| Munich | 2026-06-30 | 2026-06-30 18:39:09 | d3_no | 28 | 0.650 | 35.000 | -0.661 | 25 | 26.900 | -3.350 | 0.000 | 0.450 | at_high | room_1_2 |
| Moscow | 2026-07-01 | 2026-07-01 16:37:18 | d2_no | 29 | 0.580 | 42.000 | -0.592 | 27 | 27.000 | -3.380 | 0.100 | 0.550 | at_high | capped_minus1_0 |
| Ankara | 2026-07-04 | 2026-07-04 17:59:52 | d2_no | 30 | 0.550 | 45.000 | -0.562 | 28 | 28.600 | -2.020 | 0.000 | 0.450 | at_high | room_0_1 |
| Munich | 2026-07-04 | 2026-07-04 18:33:47 | d3_no | 27 | 0.542 | 45.800 | -0.554 | 24 | 23.400 | -3.450 | 0.100 | 0.550 | at_high | capped_minus1_0 |
| Ankara | 2026-07-04 | 2026-07-04 17:26:13 | d3_no | 30 | 0.480 | 52.000 | -0.492 | 27 | 28.600 | -2.570 | 0.000 | 0.450 | at_high | room_1_2 |
| LA | 2026-07-01 | 2026-07-01 15:56:37 | d3_no | 70-71 | 0.450 | 55.000 | -0.462 | 64 | 66.700 | -13.070 | 0.000 | 0.450 | at_high | room_gt2 |
| Shenzhen | 2026-07-01 | 2026-07-01 15:56:37 | d1_no | 33 | 0.300 | 70.000 | -0.310 | 32 | 31.500 | 0.930 | 0.500 | 0.500 | at_high | capped_minus1_0 |
| Shenzhen | 2026-07-01 | 2026-07-01 15:56:37 | current_yes | 32 | 0.270 | 73.000 | -0.280 | 32 | 31.500 | 0.930 | 0.500 | 0.500 | at_high | capped_minus1_0 |
| Chongqing | 2026-07-04 | 2026-07-04 15:45:27 | current_yes | 32 | 0.150 | 85.000 | -0.156 | 32 | 31.500 | -0.250 | 0.400 | 0.400 | at_high | capped_minus1_0 |
| HongKong | 2026-07-01 | 2026-07-01 15:15:54 | current_yes | 34 | 0.079 | 92.100 | -0.083 | 34 | 31.100 | 3.250 | 0.650 | 0.650 | at_high | busted_lt_minus1 |
| Busan | 2026-07-01 | 2026-07-01 15:56:37 | d1_no | 23 | 0.061 | 93.900 | -0.064 | 22 | 22.300 | 0.930 | 0.400 | 0.400 | at_high | room_0_1 |
| HongKong | 2026-06-30 | 2026-06-30 15:26:03 | current_yes | 33 | 0.049 | 95.100 | -0.051 | 33 | 30.000 | 3.430 | 0.800 | 0.800 | decline | busted_lt_minus1 |

## Chengdu 2026-07-06 Case
Chengdu 2026-07-06 is retained as a per-poll case replay, not in headline ROI because settlement coverage for 2026-07-06 is incomplete in the local DB.
| ts_beijing | leg | bracket | entry_price | residual_points | running_value | forecast_max_native | forecast_peak_delta_hours_local | heating_done_score_v1 | leg_residual_done_score_v1 | path_state |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-06 15:12:18 | current_yes | 37 | 0.630 | 37.000 | 37 | 37.600 | -0.800 | 0.100 | 0.100 | at_high |
| 2026-07-06 15:12:18 | d1_no | 38 | 0.700 | 30.000 | 37 | 37.600 | -0.800 | 0.100 | 0.100 | at_high |
| 2026-07-06 15:12:18 | d2_no | 39 | 0.930 | 7.000 | 37 | 37.600 | -0.800 | 0.100 | 0.550 | at_high |
| 2026-07-06 15:12:18 | d3_no | 40 | 0.998 | 0.200 | 37 | 37.600 | -0.800 | 0.100 | 0.550 | at_high |
| 2026-07-06 15:28:57 | current_yes | 37 | 0.670 | 33.000 | 37 | 37.600 | -0.530 | 0.100 | 0.100 | at_high |
| 2026-07-06 15:28:57 | d1_no | 38 | 0.700 | 30.000 | 37 | 37.600 | -0.530 | 0.100 | 0.100 | at_high |
| 2026-07-06 15:28:57 | d2_no | 39 | 0.950 | 5.000 | 37 | 37.600 | -0.530 | 0.100 | 0.550 | at_high |
| 2026-07-06 15:28:57 | d3_no | 40 | 0.997 | 0.300 | 37 | 37.600 | -0.530 | 0.100 | 0.550 | at_high |
| 2026-07-06 15:45:43 | current_yes | 37 | 0.640 | 36.000 | 37 | 37.600 | -0.250 | 0.200 | 0.200 | at_high |
| 2026-07-06 15:45:43 | d1_no | 38 | 0.680 | 32.000 | 37 | 37.600 | -0.250 | 0.200 | 0.200 | at_high |
| 2026-07-06 15:45:43 | d2_no | 39 | 0.960 | 4.000 | 37 | 37.600 | -0.250 | 0.200 | 0.650 | at_high |
| 2026-07-06 15:45:43 | d3_no | 40 | 0.999 | 0.100 | 37 | 37.600 | -0.250 | 0.200 | 0.650 | at_high |
| 2026-07-06 16:02:17 | current_yes | 37 | 0.870 | 13.000 | 37 | 37.600 | 0.030 | 0.200 | 0.200 | at_high |
| 2026-07-06 16:02:17 | d1_no | 38 | 0.860 | 14.000 | 37 | 37.600 | 0.030 | 0.200 | 0.200 | at_high |
| 2026-07-06 16:02:17 | d2_no | 39 | 0.988 | 1.200 | 37 | 37.600 | 0.030 | 0.200 | 0.650 | at_high |
| 2026-07-06 16:19:07 | current_yes | 37 | 0.910 | 9.000 | 37 | 37.600 | 0.320 | 0.300 | 0.300 | at_high |
| 2026-07-06 16:19:07 | d1_no | 38 | 0.930 | 7.000 | 37 | 37.600 | 0.320 | 0.300 | 0.300 | at_high |
| 2026-07-06 16:19:07 | d2_no | 39 | 0.988 | 1.200 | 37 | 37.600 | 0.320 | 0.300 | 0.750 | at_high |
| 2026-07-06 16:19:07 | d3_no | 40 | 0.999 | 0.100 | 37 | 37.600 | 0.320 | 0.300 | 0.750 | at_high |
| 2026-07-06 16:35:57 | current_yes | 37 | 0.890 | 11.000 | 37 | 37.600 | 0.580 | 0.300 | 0.300 | at_high |
| 2026-07-06 16:35:57 | d1_no | 38 | 0.910 | 9.000 | 37 | 37.600 | 0.580 | 0.300 | 0.300 | at_high |
| 2026-07-06 16:35:57 | d2_no | 39 | 0.998 | 0.200 | 37 | 37.600 | 0.580 | 0.300 | 0.750 | at_high |
| 2026-07-06 16:53:05 | current_yes | 37 | 0.930 | 7.000 | 37 | 37.800 | 0.880 | 0.300 | 0.300 | at_high |
| 2026-07-06 16:53:05 | d1_no | 38 | 0.940 | 6.000 | 37 | 37.800 | 0.880 | 0.300 | 0.300 | at_high |
| 2026-07-06 16:53:05 | d2_no | 39 | 0.998 | 0.200 | 37 | 37.800 | 0.880 | 0.300 | 0.750 | at_high |
| 2026-07-06 17:10:02 | current_yes | 37 | 0.989 | 1.100 | 37 | 37.800 | 1.170 | 0.380 | 0.380 | at_high |
| 2026-07-06 17:10:02 | d1_no | 38 | 0.989 | 1.100 | 37 | 37.800 | 1.170 | 0.380 | 0.380 | at_high |
| 2026-07-06 17:10:02 | d2_no | 39 | 0.999 | 0.100 | 37 | 37.800 | 1.170 | 0.380 | 0.830 | at_high |
| 2026-07-06 17:27:12 | current_yes | 37 | 0.963 | 3.700 | 37 | 37.800 | 1.450 | 0.380 | 0.380 | at_high |
| 2026-07-06 17:27:12 | d1_no | 38 | 0.960 | 4.000 | 37 | 37.800 | 1.450 | 0.380 | 0.380 | at_high |

## Chengdu Policy Rows
| entry_policy | ts_beijing | leg | bracket | entry_price | residual_points | running_value | forecast_max_native | forecast_peak_delta_hours_local | heating_done_score_v1 | leg_residual_done_score_v1 | path_state |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes_heating_done | 2026-07-06 17:10:02 | current_yes | 37 | 0.989 | 1.100 | 37 | 37.800 | 1.170 | 0.380 | 0.380 | at_high |
| d1_no_heating_done | 2026-07-06 17:10:02 | d1_no | 38 | 0.989 | 1.100 | 37 | 37.800 | 1.170 | 0.380 | 0.380 | at_high |
| d2_no_residual_done | 2026-07-06 15:12:18 | d2_no | 39 | 0.930 | 7.000 | 37 | 37.600 | -0.800 | 0.100 | 0.550 | at_high |
| d3_no_residual_done | 2026-07-06 15:12:18 | d3_no | 40 | 0.998 | 0.200 | 37 | 37.600 | -0.800 | 0.100 | 0.550 | at_high |
| heating_done_residual_combo | 2026-07-06 15:12:18 | d2_no | 39 | 0.930 | 7.000 | 37 | 37.600 | -0.800 | 0.100 | 0.550 | at_high |
| heating_done_residual_combo | 2026-07-06 15:12:18 | d3_no | 40 | 0.998 | 0.200 | 37 | 37.600 | -0.800 | 0.100 | 0.550 | at_high |
| heating_done_residual_combo | 2026-07-06 17:10:02 | current_yes | 37 | 0.989 | 1.100 | 37 | 37.800 | 1.170 | 0.380 | 0.380 | at_high |
| heating_done_residual_combo | 2026-07-06 17:10:02 | d1_no | 38 | 0.989 | 1.100 | 37 | 37.800 | 1.170 | 0.380 | 0.380 | at_high |

## Contract Gates
significance=FAIL/NA because current YES, d1 NO, and d2 NO forward date-block ROI CIs cross 0, while d3 NO is positive but below the active-date support gate; baseline=FAIL/NA because broad residual capture does not beat a robust zero/market baseline after fees; forward=FAIL/NA because 2026-06-29..2026-07-04 support is small and unstable; conclusion=inconclusive.
