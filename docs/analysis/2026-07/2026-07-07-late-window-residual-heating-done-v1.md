# Late-Window Residual Capture Heating-Done v1

Status: `snapshot`
Evidence: `paper_snapshots/snapshot_YYYYMMDD_HHMM.json` per polling cycle; settlement joined only after PIT candidate generation.

## Verdict
Per-poll replay now separates physical residual state from price: entry is based on `heating_done_score_v1` and bracket-specific `leg_residual_done_score_v1`, while price is only reported as execution/EV. This fixes the overly rigid 17:00 and 1-5c entry gates, but the available settled forward window is still too small for live promotion. Conclusion: `inconclusive_research_shadow_only`; do not change live. Positive forward point estimates exist for feature policies: d1_no_heating_done, d2_no_residual_done, d3_no_residual_done, heating_done_residual_combo.

## Data Snapshot
- paper snapshots scanned: 3467
- raw PIT leg rows: 4341
- book-executable rows before first-cross, no price filter: 4012
- legacy strict 1-5pt rows, diagnostics only: 692
- first-cross rows: 1005
- settlement-complete dates used for headline: through 2026-07-06
- incomplete dates excluded from headline ROI: none

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
| current_yes | forward_2026-06-29_2026-07-06 | 168 | 167 | 7 | 31 | 0.504 | 0.515 | 0.012 | -0.073 | 0.110 | 1.001 | 82453.740 |
| current_yes | train_to_2026-06-28 | 15 | 15 | 6 | 4 | 0.253 | 0.267 | 0.044 | -0.184 | 0.232 | 0.169 | 5696.130 |
| d1_no | forward_2026-06-29_2026-07-06 | 254 | 252 | 7 | 41 | 0.839 | 0.845 | 0.002 | -0.029 | 0.045 | 0.454 | 509450.890 |
| d1_no | train_to_2026-06-28 | 33 | 33 | 8 | 4 | 0.923 | 0.879 | -0.050 | -0.104 | 0.009 | -1.534 | 23357.650 |
| d2_no | forward_2026-06-29_2026-07-06 | 272 | 270 | 7 | 45 | 0.886 | 0.874 | -0.017 | -0.041 | 0.005 | -4.112 | 666334.680 |
| d2_no | train_to_2026-06-28 | 7 | 7 | 6 | 3 | 0.902 | 0.714 | -0.211 | -0.575 | 0.005 | -1.335 | 12907.410 |
| d3_no | forward_2026-06-29_2026-07-06 | 251 | 249 | 7 | 43 | 0.900 | 0.900 | -0.004 | -0.029 | 0.020 | -0.990 | 774826.060 |
| d3_no | train_to_2026-06-28 | 5 | 5 | 5 | 2 | 0.932 | 0.800 | -0.143 | -0.506 | 0.005 | -0.670 | 21693.590 |

## Pluggable Entry Policies
| entry_policy | period | rows | settled_rows | active_dates | cities | avg_entry | hit_rate | roi | roi_ci_low | roi_ci_high | pnl | depth_5c_sum |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes_heating_done | forward_2026-06-29_2026-07-06 | 72 | 72 | 7 | 22 | 0.830 | 0.833 | -0.001 | -0.062 | 0.070 | -0.035 | 61496.600 |
| current_yes_heating_done | train_to_2026-06-28 | 1 | 1 | 1 | 1 | 0.999 | 1.000 | 0.001 |  |  | 0.001 | 120.620 |
| d1_no_heating_done | forward_2026-06-29_2026-07-06 | 69 | 69 | 6 | 21 | 0.860 | 0.884 | 0.024 | -0.067 | 0.128 | 1.408 | 57988.030 |
| d2_no_residual_done | forward_2026-06-29_2026-07-06 | 103 | 102 | 7 | 26 | 0.866 | 0.873 | 0.003 | -0.059 | 0.056 | 0.240 | 110546.890 |
| d3_no_residual_done | forward_2026-06-29_2026-07-06 | 97 | 96 | 5 | 26 | 0.899 | 0.906 | 0.004 | -0.022 | 0.027 | 0.375 | 156301.660 |
| heating_done_residual_combo | forward_2026-06-29_2026-07-06 | 341 | 339 | 7 | 30 | 0.867 | 0.876 | 0.007 | -0.035 | 0.052 | 1.988 | 386333.180 |
| heating_done_residual_combo | train_to_2026-06-28 | 1 | 1 | 1 | 1 | 0.999 | 1.000 | 0.001 |  |  | 0.001 | 120.620 |

## Policy By Leg
| entry_policy | leg | period | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes_heating_done | current_yes | forward_2026-06-29_2026-07-06 | 72 | 72 | 7 | 0.833 | -0.001 | -0.062 | 0.070 |
| current_yes_heating_done | current_yes | train_to_2026-06-28 | 1 | 1 | 1 | 1.000 | 0.001 |  |  |
| d1_no_heating_done | d1_no | forward_2026-06-29_2026-07-06 | 69 | 69 | 6 | 0.884 | 0.024 | -0.067 | 0.128 |
| d2_no_residual_done | d2_no | forward_2026-06-29_2026-07-06 | 103 | 102 | 7 | 0.873 | 0.003 | -0.059 | 0.056 |
| d3_no_residual_done | d3_no | forward_2026-06-29_2026-07-06 | 97 | 96 | 5 | 0.906 | 0.004 | -0.022 | 0.027 |
| heating_done_residual_combo | current_yes | forward_2026-06-29_2026-07-06 | 72 | 72 | 7 | 0.833 | -0.001 | -0.062 | 0.070 |
| heating_done_residual_combo | current_yes | train_to_2026-06-28 | 1 | 1 | 1 | 1.000 | 0.001 |  |  |
| heating_done_residual_combo | d1_no | forward_2026-06-29_2026-07-06 | 69 | 69 | 6 | 0.884 | 0.024 | -0.067 | 0.128 |
| heating_done_residual_combo | d2_no | forward_2026-06-29_2026-07-06 | 103 | 102 | 7 | 0.873 | 0.003 | -0.059 | 0.056 |
| heating_done_residual_combo | d3_no | forward_2026-06-29_2026-07-06 | 97 | 96 | 5 | 0.906 | 0.004 | -0.022 | 0.027 |

## Peak-Window Slices
| leg | peak_delta_bucket | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | peak_gt1h_ahead | 94 | 93 | 12 | 0.172 | -0.011 | -0.349 | 0.262 |
| current_yes | peak_1h_to_15m_ahead | 21 | 21 | 8 | 0.762 | 0.039 | -0.080 | 0.156 |
| current_yes | near_peak | 20 | 20 | 5 | 0.850 | 0.078 | -0.031 | 0.149 |
| current_yes | post_peak_0_5_1_5h | 18 | 18 | 6 | 0.833 | -0.061 | -0.208 | 0.188 |
| current_yes | post_peak_gt1_5h | 30 | 30 | 6 | 0.867 | 0.019 | -0.040 | 0.110 |
| d1_no | peak_gt1h_ahead | 205 | 203 | 15 | 0.833 | -0.024 | -0.056 | 0.012 |
| d1_no | peak_1h_to_15m_ahead | 18 | 18 | 7 | 0.833 | 0.043 | -0.109 | 0.187 |
| d1_no | near_peak | 20 | 20 | 5 | 0.950 | 0.160 | 0.115 | 0.226 |
| d1_no | post_peak_0_5_1_5h | 17 | 17 | 5 | 0.765 | -0.099 | -0.244 | 0.135 |
| d1_no | post_peak_gt1_5h | 27 | 27 | 5 | 0.963 | 0.053 | 0.025 | 0.095 |
| d2_no | peak_gt1h_ahead | 214 | 212 | 13 | 0.854 | -0.012 | -0.052 | 0.013 |
| d2_no | peak_1h_to_15m_ahead | 16 | 16 | 6 | 0.875 | -0.078 | -0.242 | 0.059 |
| d2_no | near_peak | 16 | 16 | 5 | 0.875 | -0.097 | -0.268 | 0.028 |
| d2_no | post_peak_0_5_1_5h | 17 | 17 | 5 | 1.000 | 0.020 | 0.003 | 0.059 |
| d2_no | post_peak_gt1_5h | 16 | 16 | 4 | 0.938 | -0.054 | -0.217 | 0.011 |
| d3_no | peak_gt1h_ahead | 217 | 215 | 12 | 0.879 | -0.011 | -0.042 | 0.014 |
| d3_no | peak_1h_to_15m_ahead | 12 | 12 | 5 | 1.000 | 0.031 | 0.002 | 0.068 |
| d3_no | near_peak | 11 | 11 | 4 | 1.000 | 0.004 | 0.003 | 0.005 |
| d3_no | post_peak_0_5_1_5h | 7 | 7 | 3 | 1.000 | 0.005 | 0.002 | 0.011 |
| d3_no | post_peak_gt1_5h | 9 | 9 | 4 | 1.000 | 0.003 | 0.001 | 0.008 |

## Forecast-Gap Slices
| leg | forecast_gap_bucket | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | busted_lt_minus1 | 37 | 37 | 9 | 0.784 | 0.063 | -0.045 | 0.195 |
| current_yes | capped_minus1_0 | 51 | 50 | 9 | 0.680 | 0.062 | -0.049 | 0.192 |
| current_yes | room_0_1 | 34 | 34 | 12 | 0.265 | -0.209 | -0.345 | -0.098 |
| current_yes | room_1_2 | 26 | 26 | 9 | 0.346 | 0.020 | -0.232 | 0.323 |
| current_yes | room_gt2 | 35 | 35 | 8 | 0.257 | -0.036 | -0.501 | 0.898 |
| d1_no | busted_lt_minus1 | 34 | 34 | 7 | 0.853 | 0.046 | -0.068 | 0.174 |
| d1_no | capped_minus1_0 | 48 | 47 | 8 | 0.787 | -0.001 | -0.110 | 0.116 |
| d1_no | room_0_1 | 39 | 38 | 13 | 0.605 | -0.177 | -0.337 | -0.038 |
| d1_no | room_1_2 | 43 | 43 | 13 | 0.884 | 0.071 | -0.047 | 0.174 |
| d1_no | room_gt2 | 123 | 123 | 15 | 0.935 | 0.001 | -0.040 | 0.042 |
| d2_no | busted_lt_minus1 | 24 | 24 | 5 | 0.958 | -0.022 | -0.096 | 0.026 |
| d2_no | capped_minus1_0 | 39 | 38 | 7 | 0.895 | 0.008 | -0.078 | 0.068 |
| d2_no | room_0_1 | 33 | 33 | 7 | 0.818 | 0.083 | -0.135 | 0.295 |
| d2_no | room_1_2 | 37 | 36 | 9 | 0.556 | -0.286 | -0.492 | -0.103 |
| d2_no | room_gt2 | 146 | 146 | 11 | 0.938 | 0.006 | -0.052 | 0.061 |
| d3_no | busted_lt_minus1 | 13 | 13 | 5 | 1.000 | 0.004 | 0.002 | 0.007 |
| d3_no | capped_minus1_0 | 29 | 28 | 5 | 0.964 | -0.006 | -0.056 | 0.025 |
| d3_no | room_0_1 | 25 | 25 | 5 | 0.920 | 0.017 | -0.051 | 0.086 |
| d3_no | room_1_2 | 26 | 26 | 5 | 0.808 | 0.025 | -0.152 | 0.210 |
| d3_no | room_gt2 | 163 | 162 | 12 | 0.889 | -0.017 | -0.077 | 0.025 |

## Path-State Slices
| leg | path_state | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | at_high | 140 | 139 | 13 | 0.410 | 0.011 | -0.091 | 0.131 |
| current_yes | decline | 43 | 43 | 10 | 0.767 | 0.017 | -0.085 | 0.127 |
| d1_no | at_high | 229 | 227 | 15 | 0.828 | -0.009 | -0.042 | 0.025 |
| d1_no | decline | 58 | 58 | 10 | 0.931 | 0.011 | -0.082 | 0.093 |
| d2_no | at_high | 221 | 219 | 13 | 0.845 | -0.027 | -0.070 | 0.008 |
| d2_no | decline | 58 | 58 | 8 | 0.966 | -0.007 | -0.042 | 0.032 |
| d3_no | at_high | 210 | 208 | 12 | 0.880 | -0.012 | -0.047 | 0.015 |
| d3_no | decline | 46 | 46 | 6 | 0.978 | 0.014 | 0.005 | 0.024 |

## Heating-Done Slices
| leg | heating_done_bucket_v1 | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | heating_done_confirmed | 24 | 24 | 8 | 0.917 | 0.022 | 0.002 | 0.055 |
| current_yes | heating_done_probable | 19 | 19 | 6 | 0.842 | 0.002 | -0.125 | 0.169 |
| current_yes | heating_done_uncertain | 17 | 17 | 6 | 0.706 | 0.038 | -0.093 | 0.241 |
| current_yes | near_peak_capping | 9 | 9 | 5 | 0.889 | 0.005 | -0.391 | 0.220 |
| current_yes | runway_still_open | 114 | 113 | 13 | 0.283 | 0.006 | -0.152 | 0.214 |
| d1_no | heating_done_confirmed | 21 | 21 | 5 | 0.952 | 0.021 | 0.000 | 0.046 |
| d1_no | heating_done_probable | 18 | 18 | 6 | 0.889 | -0.003 | -0.131 | 0.168 |
| d1_no | heating_done_uncertain | 17 | 17 | 6 | 0.824 | 0.102 | -0.144 | 0.341 |
| d1_no | near_peak_capping | 8 | 8 | 4 | 0.875 | -0.031 | -0.548 | 0.200 |
| d1_no | runway_still_open | 223 | 221 | 15 | 0.837 | -0.013 | -0.038 | 0.013 |
| d2_no | heating_done_confirmed | 13 | 13 | 5 | 1.000 | 0.004 | 0.001 | 0.006 |
| d2_no | heating_done_probable | 13 | 13 | 5 | 1.000 | 0.011 | 0.003 | 0.020 |
| d2_no | heating_done_uncertain | 16 | 16 | 5 | 0.812 | -0.106 | -0.263 | 0.103 |
| d2_no | near_peak_capping | 7 | 7 | 4 | 1.000 | 0.006 | 0.003 | 0.008 |
| d2_no | runway_still_open | 230 | 228 | 13 | 0.855 | -0.021 | -0.069 | 0.013 |
| d3_no | heating_done_confirmed | 6 | 6 | 3 | 1.000 | 0.002 | 0.001 | 0.006 |
| d3_no | heating_done_probable | 4 | 4 | 4 | 1.000 | 0.004 | 0.002 | 0.007 |
| d3_no | heating_done_uncertain | 14 | 14 | 5 | 1.000 | 0.014 | 0.004 | 0.028 |
| d3_no | near_peak_capping | 4 | 4 | 3 | 1.000 | 0.001 | 0.001 | 0.002 |
| d3_no | runway_still_open | 228 | 226 | 12 | 0.885 | -0.009 | -0.040 | 0.017 |

## Leg Residual-Done Slices
| leg | leg_residual_done_bucket_v1 | rows | settled_rows | active_dates | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | leg_residual_done_confirmed | 24 | 24 | 8 | 0.917 | 0.022 | 0.002 | 0.055 |
| current_yes | leg_residual_done_probable | 19 | 19 | 6 | 0.842 | 0.002 | -0.125 | 0.169 |
| current_yes | leg_residual_marginal | 13 | 13 | 6 | 0.846 | 0.037 | -0.308 | 0.197 |
| current_yes | leg_residual_not_done | 127 | 126 | 13 | 0.325 | 0.007 | -0.088 | 0.126 |
| d1_no | leg_residual_done_confirmed | 21 | 21 | 5 | 0.952 | 0.021 | 0.000 | 0.046 |
| d1_no | leg_residual_done_probable | 18 | 18 | 6 | 0.889 | -0.003 | -0.131 | 0.168 |
| d1_no | leg_residual_marginal | 13 | 13 | 5 | 0.846 | 0.075 | -0.311 | 0.266 |
| d1_no | leg_residual_not_done | 235 | 233 | 15 | 0.837 | -0.011 | -0.041 | 0.018 |
| d2_no | leg_residual_done_confirmed | 49 | 49 | 5 | 0.939 | -0.027 | -0.100 | 0.036 |
| d2_no | leg_residual_done_probable | 24 | 23 | 7 | 0.870 | 0.013 | -0.159 | 0.112 |
| d2_no | leg_residual_marginal | 26 | 26 | 7 | 0.769 | 0.108 | -0.189 | 0.426 |
| d2_no | leg_residual_not_done | 180 | 179 | 13 | 0.866 | -0.039 | -0.094 | 0.004 |
| d3_no | leg_residual_done_confirmed | 28 | 28 | 5 | 1.000 | 0.008 | 0.003 | 0.015 |
| d3_no | leg_residual_done_probable | 21 | 20 | 5 | 0.950 | -0.016 | -0.115 | 0.019 |
| d3_no | leg_residual_marginal | 45 | 45 | 5 | 0.822 | 0.011 | -0.060 | 0.085 |
| d3_no | leg_residual_not_done | 162 | 161 | 12 | 0.894 | -0.014 | -0.068 | 0.025 |

## Basket
| period | rows | active_dates | cities | avg_legs | cost | pnl | roi | max_basket_loss | depth_5c_sum |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| forward_2026-06-29_2026-07-06 | 330 | 7 | 46 | 2.842 | 762.648 | -3.648 | -0.005 | -1.971 | 2033065.370 |
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
| Karachi | 2026-07-05 | 2026-07-05 18:00:17 | current_yes | 33 | 0.911 | 8.900 | -0.915 | 33 | 32.600 | 0.000 | 0.400 | 0.400 | at_high | capped_minus1_0 |
| Karachi | 2026-07-05 | 2026-07-05 18:00:17 | d1_no | 34 | 0.910 | 9.000 | -0.914 | 33 | 32.600 | 0.000 | 0.400 | 0.400 | at_high | capped_minus1_0 |
| Amsterdam | 2026-07-06 | 2026-07-06 17:10:02 | d3_no | 26+ | 0.886 | 11.400 | -0.891 | 23 | 24.500 | -6.830 | 0.000 | 0.450 | at_high | room_1_2 |
| Chongqing | 2026-07-06 | 2026-07-06 15:12:18 | d2_no | 37 | 0.860 | 14.000 | -0.866 | 35 | 33.300 | 0.200 | 0.400 | 0.850 | at_high | busted_lt_minus1 |
| Guangzhou | 2026-07-01 | 2026-07-01 15:15:54 | current_yes | 32 | 0.749 | 25.100 | -0.758 | 32 | 33.100 | 2.250 | 0.350 | 0.350 | decline | room_1_2 |
| Munich | 2026-07-05 | 2026-07-05 18:17:04 | d3_no | 26 | 0.726 | 27.400 | -0.736 | 23 | 23.200 | -4.720 | 0.000 | 0.450 | at_high | room_0_1 |
| CapeTown | 2026-06-30 | 2026-06-30 18:39:09 | d2_no | 16 | 0.720 | 28.000 | -0.730 | 14 | 14.800 | -1.350 | 0.000 | 0.450 | at_high | room_0_1 |
| Ankara | 2026-06-30 | 2026-06-30 17:56:13 | d3_no | 32 | 0.710 | 29.000 | -0.720 | 29 | 30.100 | -4.070 | 0.000 | 0.450 | at_high | room_1_2 |
| Helsinki | 2026-06-30 | 2026-06-30 16:40:31 | d2_no | 26 | 0.710 | 29.000 | -0.720 | 24 | 24.900 | -5.330 | 0.000 | 0.450 | at_high | room_0_1 |
| Moscow | 2026-07-01 | 2026-07-01 15:15:54 | d3_no | 29 | 0.700 | 30.000 | -0.710 | 26 | 26.900 | -4.750 | 0.000 | 0.450 | at_high | room_0_1 |
| Chengdu | 2026-07-05 | 2026-07-05 15:12:26 | d1_no | 37 | 0.680 | 32.000 | -0.691 | 36 | 33.700 | 1.200 | 0.580 | 0.580 | at_high | busted_lt_minus1 |
| Ankara | 2026-07-02 | 2026-07-02 17:29:28 | d2_no | 34 | 0.660 | 34.000 | -0.671 | 32 | 31.900 | -3.520 | 0.100 | 0.550 | at_high | capped_minus1_0 |
| Munich | 2026-06-30 | 2026-06-30 18:39:09 | d3_no | 28 | 0.650 | 35.000 | -0.661 | 25 | 26.900 | -3.350 | 0.000 | 0.450 | at_high | room_1_2 |
| Chengdu | 2026-07-05 | 2026-07-05 15:12:26 | current_yes | 36 | 0.650 | 35.000 | -0.661 | 36 | 33.700 | 1.200 | 0.580 | 0.580 | at_high | busted_lt_minus1 |
| Munich | 2026-07-05 | 2026-07-05 18:50:47 | d2_no | 26 | 0.647 | 35.300 | -0.658 | 24 | 23.200 | -4.170 | 0.100 | 0.550 | at_high | capped_minus1_0 |
| Ankara | 2026-07-06 | 2026-07-06 17:10:02 | d2_no | 27 | 0.640 | 36.000 | -0.652 | 25 | 25.100 | -3.830 | 0.000 | 0.450 | at_high | room_0_1 |
| Istanbul | 2026-07-06 | 2026-07-06 15:12:18 | d2_no | 27 | 0.600 | 40.000 | -0.612 | 25 | 25.100 | -3.800 | 0.000 | 0.450 | at_high | room_0_1 |
| Moscow | 2026-07-01 | 2026-07-01 16:37:18 | d2_no | 29 | 0.580 | 42.000 | -0.592 | 27 | 27.000 | -3.380 | 0.100 | 0.550 | at_high | capped_minus1_0 |
| Ankara | 2026-07-05 | 2026-07-05 15:45:58 | d3_no | 25 | 0.580 | 42.000 | -0.592 | 22 | 23.400 | -3.250 | 0.000 | 0.450 | at_high | room_1_2 |
| Ankara | 2026-07-05 | 2026-07-05 16:02:38 | d2_no | 25 | 0.560 | 44.000 | -0.572 | 23 | 23.400 | -2.970 | 0.000 | 0.450 | at_high | room_0_1 |
| Ankara | 2026-07-04 | 2026-07-04 17:59:52 | d2_no | 30 | 0.550 | 45.000 | -0.562 | 28 | 28.600 | -2.020 | 0.000 | 0.450 | at_high | room_0_1 |
| Munich | 2026-07-04 | 2026-07-04 18:33:47 | d3_no | 27 | 0.542 | 45.800 | -0.554 | 24 | 23.400 | -3.450 | 0.100 | 0.550 | at_high | capped_minus1_0 |
| Ankara | 2026-07-04 | 2026-07-04 17:26:13 | d3_no | 30 | 0.480 | 52.000 | -0.492 | 27 | 28.600 | -2.570 | 0.000 | 0.450 | at_high | room_1_2 |
| Moscow | 2026-07-06 | 2026-07-06 15:45:43 | d2_no | 22 | 0.460 | 54.000 | -0.472 | 20 | 20.900 | -3.250 | 0.000 | 0.450 | at_high | room_0_1 |
| LA | 2026-07-01 | 2026-07-01 15:56:37 | d3_no | 70-71 | 0.450 | 55.000 | -0.462 | 64 | 66.700 | -13.070 | 0.000 | 0.450 | at_high | room_gt2 |
| Ankara | 2026-07-05 | 2026-07-05 18:50:47 | d1_no | 25 | 0.450 | 55.000 | -0.462 | 24 | 23.400 | -0.170 | 0.400 | 0.400 | at_high | capped_minus1_0 |
| Shenzhen | 2026-07-01 | 2026-07-01 15:56:37 | d1_no | 33 | 0.300 | 70.000 | -0.310 | 32 | 31.500 | 0.930 | 0.500 | 0.500 | at_high | capped_minus1_0 |
| Shenzhen | 2026-07-01 | 2026-07-01 15:56:37 | current_yes | 32 | 0.270 | 73.000 | -0.280 | 32 | 31.500 | 0.930 | 0.500 | 0.500 | at_high | capped_minus1_0 |
| Chongqing | 2026-07-06 | 2026-07-06 15:12:18 | current_yes | 35 | 0.250 | 75.000 | -0.259 | 35 | 33.300 | 0.200 | 0.400 | 0.400 | at_high | busted_lt_minus1 |
| Ankara | 2026-07-05 | 2026-07-05 18:50:47 | current_yes | 24 | 0.200 | 80.000 | -0.208 | 24 | 23.400 | -0.170 | 0.400 | 0.400 | at_high | capped_minus1_0 |
| Chongqing | 2026-07-04 | 2026-07-04 15:45:27 | current_yes | 32 | 0.150 | 85.000 | -0.156 | 32 | 31.500 | -0.250 | 0.400 | 0.400 | at_high | capped_minus1_0 |
| HongKong | 2026-07-06 | 2026-07-06 15:12:18 | current_yes | 32 | 0.130 | 87.000 | -0.136 | 32 | 28.200 | 11.200 | 0.800 | 0.800 | decline | busted_lt_minus1 |
| HongKong | 2026-07-01 | 2026-07-01 15:15:54 | current_yes | 34 | 0.079 | 92.100 | -0.083 | 34 | 31.100 | 3.250 | 0.650 | 0.650 | at_high | busted_lt_minus1 |
| Busan | 2026-07-01 | 2026-07-01 15:56:37 | d1_no | 23 | 0.061 | 93.900 | -0.064 | 22 | 22.300 | 0.930 | 0.400 | 0.400 | at_high | room_0_1 |
| HongKong | 2026-06-30 | 2026-06-30 15:26:03 | current_yes | 33 | 0.049 | 95.100 | -0.051 | 33 | 30.000 | 3.430 | 0.800 | 0.800 | decline | busted_lt_minus1 |
| HongKong | 2026-07-05 | 2026-07-05 15:12:26 | d1_no | 33 | 0.018 | 98.200 | -0.019 | 32 | 29.700 | 2.200 | 0.800 | 0.800 | decline | busted_lt_minus1 |

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
significance=FAIL/NA because current YES, d1 NO, and d2 NO forward date-block ROI CIs cross 0, while d3 NO is positive but below the active-date support gate; baseline=FAIL/NA because broad residual capture does not beat a robust zero/market baseline after fees; forward=FAIL/NA because 2026-06-29..2026-07-06 support is still small and unstable; conclusion=inconclusive.
