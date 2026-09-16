# Late-Window Residual Physical Feature Ablation v2

Status: `snapshot`
Model ids: `late_window_residual_physical_base_logit_v2`, `late_window_residual_physical_clean_clock_tick_obs_logit_v2`, `late_window_residual_physical_plus_logit_v2`

## Verdict
The lean enriched PIT physical view is the cleaner next research view because it fixes the local-clock and F/C scale issues and adds observation freshness without using market price as a model input. In strict expanding-forward probability validation, base logloss=0.4150, Brier=0.1254, AUC=0.7553; clean logloss=0.4178, Brier=0.1251, AUC=0.7540; plus logloss=0.4284, Brier=0.1288, AUC=0.7513. Forward edge>=0 EV replay ROI is base=-1.9%, clean=-2.6%, plus=-3.0%. Clean improves Brier slightly but not logloss, AUC, or all-leg EV; plus-all overfits more. The d1 NO clean slice remains the only constructive candidate, but it is still research-only.

## Data
- Input first-cross rows: 1005
- Settled model rows: 998
- Target dates: 2026-06-20..2026-07-06 (15 dates)
- Forward window: 2026-06-29..2026-07-06
- Model inputs exclude orderbook price/depth/spread, settlement labels, PnL, and ROI.
- `physical_clean` uses true city-local clock, F/C tick-normalized distances, and observation age/count.
- `physical_plus` further adds forecast source, solar-window proxies, and native duplicate features; it is included as a stress test, not the recommended view.
- The 930,266-byte replay input `first_cross_rows.csv` is archived in JRS
  manifest `late_window_residual_heating_done_v1_cleanup_20260812`; restore it
  with the artifact controller before rerunning the historical producer.

## Absorbed v1 lineage
This report is the canonical snapshot for the former calibrated-features v1 and
`p_leg_win` policy v1 reports.  The `physical_base` rows below reproduce their
998-row probability model and fee-adjusted policy denominator, while the clean
and plus columns are the later ablation.  The superseded reports and their
single-purpose producers were removed after this merge; git history retains the
full generated tables.

- The base model improved materially over the hand score, but remained weaker
  than executable market price: strict expanding logloss/Brier/AUC were
  `0.4150/0.1254/0.7553`, versus market `0.2783/0.0920/0.9067` on the same 731
  rows.
- Base all-leg `edge>=0` selected 259 rows on 6 dates and lost 1.9% ROI; the
  `edge>=3c` point estimate was +0.6%, with CI `[-9.5%, +12.4%]`.  Base d1 NO
  was the only constructive slice at 85 rows / 6 dates / +6.6% ROI, but its CI
  `[-4.2%, +21.4%]` crossed zero.
- Chengdu 2026-07-06 illustrates why probability and price must remain
  separate.  At 15:12/15:28/15:45 BJ, 38 NO scored about 0.877/0.876/0.875
  against fee-adjusted costs 0.711/0.711/0.691, while 39 NO scored about
  0.887/0.886/0.885 against 0.933/0.952/0.962 and was negative EV.  By 17:10,
  38 NO cost 0.990 against model probability 0.901 and was no longer an entry.

## Probability Metrics
| model | scope | rows | dates | hit_rate | logloss | brier | auc | avg_p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| physical_base | all_lodo | 998 | 15 | 0.8026 | 0.3960 | 0.1193 | 0.7845 | 0.8017 |
| physical_base | forward_lodo | 938 | 7 | 0.8092 | 0.3932 | 0.1182 | 0.7801 | 0.8085 |
| physical_clean | all_lodo | 998 | 15 | 0.8026 | 0.3987 | 0.1198 | 0.7834 | 0.8009 |
| physical_clean | forward_lodo | 938 | 7 | 0.8092 | 0.3965 | 0.1188 | 0.7779 | 0.8079 |
| physical_plus | all_lodo | 998 | 15 | 0.8026 | 0.4042 | 0.1213 | 0.7798 | 0.8007 |
| physical_plus | forward_lodo | 938 | 7 | 0.8092 | 0.4021 | 0.1203 | 0.7747 | 0.8072 |
| heuristic_leg_score | all_lodo | 998 | 15 | 0.8026 | 6.2195 | 0.5479 | 0.5575 | 0.2298 |
| heuristic_leg_score | forward_lodo | 938 | 7 | 0.8092 | 6.0882 | 0.5409 | 0.5654 | 0.2409 |
| market_entry_price | all_lodo | 998 | 15 | 0.8026 | 0.2616 | 0.0850 | 0.9214 | 0.8057 |
| market_entry_price | forward_lodo | 938 | 7 | 0.8092 | 0.2643 | 0.0855 | 0.9167 | 0.8090 |
| physical_base | forward_expanding | 731 | 6 | 0.8071 | 0.4150 | 0.1254 | 0.7553 | 0.7987 |
| physical_clean | forward_expanding | 731 | 6 | 0.8071 | 0.4178 | 0.1251 | 0.7540 | 0.7981 |
| physical_plus | forward_expanding | 731 | 6 | 0.8071 | 0.4284 | 0.1288 | 0.7513 | 0.7920 |
| heuristic_leg_score | forward_expanding | 938 | 7 | 0.8092 | 6.0882 | 0.5409 | 0.5654 | 0.2409 |
| market_entry_price | forward_expanding | 938 | 7 | 0.8092 | 0.2643 | 0.0855 | 0.9167 | 0.8090 |

## Forward EV Policy: All Legs
| model | edge_threshold | rows | active_dates | cities | avg_p | avg_entry | avg_edge | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base_expanding | 0.0000 | 259 | 6 | 31 | 0.7490 | 0.5506 | 0.1900 | 0.5483 | -0.0191 | -0.0963 | 0.0736 |
| base_expanding | 0.0100 | 245 | 6 | 31 | 0.7416 | 0.5324 | 0.2006 | 0.5347 | -0.0116 | -0.0796 | 0.0815 |
| base_expanding | 0.0200 | 236 | 6 | 30 | 0.7362 | 0.5198 | 0.2077 | 0.5254 | -0.0058 | -0.0907 | 0.1002 |
| base_expanding | 0.0300 | 224 | 6 | 30 | 0.7277 | 0.5013 | 0.2175 | 0.5134 | 0.0063 | -0.0950 | 0.1240 |
| clean_expanding | 0.0000 | 255 | 6 | 31 | 0.7470 | 0.5513 | 0.1873 | 0.5451 | -0.0259 | -0.1169 | 0.0541 |
| clean_expanding | 0.0100 | 242 | 6 | 30 | 0.7391 | 0.5334 | 0.1972 | 0.5331 | -0.0163 | -0.1102 | 0.0720 |
| clean_expanding | 0.0200 | 232 | 6 | 30 | 0.7310 | 0.5172 | 0.2050 | 0.5302 | 0.0080 | -0.0869 | 0.0968 |
| clean_expanding | 0.0300 | 217 | 6 | 29 | 0.7313 | 0.5050 | 0.2175 | 0.5161 | 0.0045 | -0.0969 | 0.1132 |
| plus_expanding | 0.0000 | 248 | 6 | 31 | 0.7502 | 0.5570 | 0.1849 | 0.5484 | -0.0298 | -0.1277 | 0.0734 |
| plus_expanding | 0.0100 | 237 | 6 | 31 | 0.7437 | 0.5421 | 0.1933 | 0.5359 | -0.0264 | -0.1073 | 0.0659 |
| plus_expanding | 0.0200 | 219 | 6 | 30 | 0.7351 | 0.5186 | 0.2079 | 0.5160 | -0.0213 | -0.1329 | 0.1096 |
| plus_expanding | 0.0300 | 208 | 6 | 30 | 0.7263 | 0.4999 | 0.2176 | 0.5144 | 0.0113 | -0.1241 | 0.1519 |

## Forward EV Policy: d1 NO
| model | edge_threshold | rows | active_dates | cities | avg_p | avg_entry | avg_edge | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base_expanding | 0.0000 | 85 | 6 | 23 | 0.8483 | 0.6418 | 0.1970 | 0.6941 | 0.0658 | -0.0416 | 0.2137 |
| base_expanding | 0.0100 | 82 | 6 | 23 | 0.8446 | 0.6308 | 0.2040 | 0.6951 | 0.0851 | -0.0043 | 0.2170 |
| base_expanding | 0.0200 | 78 | 6 | 23 | 0.8430 | 0.6192 | 0.2138 | 0.6795 | 0.0800 | -0.0179 | 0.2155 |
| base_expanding | 0.0300 | 75 | 6 | 23 | 0.8397 | 0.6081 | 0.2214 | 0.6667 | 0.0783 | -0.0317 | 0.2247 |
| clean_expanding | 0.0000 | 81 | 6 | 23 | 0.8542 | 0.6415 | 0.2032 | 0.7284 | 0.1189 | 0.0179 | 0.2597 |
| clean_expanding | 0.0100 | 80 | 6 | 23 | 0.8532 | 0.6380 | 0.2056 | 0.7250 | 0.1196 | 0.0179 | 0.2597 |
| clean_expanding | 0.0200 | 77 | 6 | 23 | 0.8498 | 0.6270 | 0.2130 | 0.7143 | 0.1218 | 0.0125 | 0.2634 |
| clean_expanding | 0.0300 | 71 | 6 | 22 | 0.8430 | 0.6039 | 0.2289 | 0.6901 | 0.1238 | 0.0024 | 0.2793 |
| plus_expanding | 0.0000 | 87 | 6 | 24 | 0.8453 | 0.6574 | 0.1788 | 0.7126 | 0.0692 | -0.0602 | 0.2236 |
| plus_expanding | 0.0100 | 84 | 6 | 24 | 0.8408 | 0.6464 | 0.1850 | 0.7143 | 0.0892 | -0.0268 | 0.2297 |
| plus_expanding | 0.0200 | 75 | 6 | 23 | 0.8308 | 0.6152 | 0.2056 | 0.6800 | 0.0876 | -0.0505 | 0.2541 |
| plus_expanding | 0.0300 | 72 | 6 | 23 | 0.8260 | 0.6026 | 0.2131 | 0.6667 | 0.0878 | -0.0619 | 0.2585 |

## Top Coefficients
| model | feature | coef | abs_coef |
| --- | --- | --- | --- |
| physical_base_full | categorical__peak_delta_bucket_peak_gt1h_ahead | -1.5906 | 1.5906 |
| physical_base_full | categorical__leg_current_yes | -1.1512 | 1.1512 |
| physical_base_full | categorical__peak_delta_bucket_post_peak_gt1_5h | 0.6864 | 0.6864 |
| physical_base_full | categorical__leg_d1_no | 0.5689 | 0.5689 |
| physical_base_full | categorical__forecast_gap_bucket_room_1_2 | -0.5653 | 0.5653 |
| physical_base_full | categorical__forecast_gap_bucket_busted_lt_minus1 | 0.5416 | 0.5416 |
| physical_base_full | numeric__target_distance_native | 0.5089 | 0.5089 |
| physical_base_full | categorical__forecast_gap_bucket_capped_minus1_0 | 0.4629 | 0.4629 |
| physical_base_full | categorical__peak_delta_bucket_near_peak | 0.4546 | 0.4546 |
| physical_base_full | categorical__forecast_gap_bucket_room_0_1 | -0.3833 | 0.3833 |
| physical_base_full | categorical__peak_delta_bucket_post_peak_0_5_1_5h | 0.3636 | 0.3636 |
| physical_base_full | numeric__forecast_gap_to_running_native | 0.3545 | 0.3545 |
| physical_base_full | categorical__leg_d2_no | 0.3417 | 0.3417 |
| physical_base_full | numeric__running_value | -0.3379 | 0.3379 |
| physical_base_full | numeric__decline_native | 0.3251 | 0.3251 |
| physical_base_full | numeric__forecast_peak_delta_hours_local | -0.2855 | 0.2855 |
| physical_base_full | categorical__leg_d3_no | 0.2492 | 0.2492 |
| physical_base_full | numeric__forecast_margin_to_target_native | -0.1958 | 0.1958 |
| physical_base_full | categorical__unit_F | 0.1170 | 0.1170 |
| physical_base_full | categorical__unit_C | -0.1084 | 0.1084 |
| physical_base_full | categorical__peak_delta_bucket_peak_1h_to_15m_ahead | 0.0947 | 0.0947 |
| physical_base_full | numeric__metar_obs_count_today | 0.0689 | 0.0689 |
| physical_base_full | categorical__forecast_gap_bucket_room_gt2 | -0.0473 | 0.0473 |
| physical_base_full | numeric__local_time_float | 0.0356 | 0.0356 |
| physical_base_full | categorical__path_state_at_high | 0.0253 | 0.0253 |
| physical_base_full | categorical__path_state_decline | -0.0167 | 0.0167 |
| physical_clean_full | categorical__peak_delta_bucket_peak_gt1h_ahead | -1.3202 | 1.3202 |
| physical_clean_full | categorical__leg_current_yes | -0.9003 | 0.9003 |
| physical_clean_full | categorical__true_local_time_bucket_late_afternoon | 0.7901 | 0.7901 |
| physical_clean_full | categorical__leg_d1_no | 0.6499 | 0.6499 |
| physical_clean_full | numeric__target_distance_ticks | 0.6026 | 0.6026 |
| physical_clean_full | categorical__forecast_gap_bucket_room_1_2 | -0.5822 | 0.5822 |
| physical_clean_full | categorical__forecast_gap_bucket_busted_lt_minus1 | 0.5539 | 0.5539 |
| physical_clean_full | categorical__forecast_gap_bucket_capped_minus1_0 | 0.5396 | 0.5396 |
| physical_clean_full | categorical__peak_delta_bucket_post_peak_gt1_5h | 0.4976 | 0.4976 |
| physical_clean_full | categorical__peak_delta_bucket_near_peak | 0.4243 | 0.4243 |
| physical_clean_full | categorical__true_local_time_bucket_late_morning | -0.4070 | 0.4070 |
| physical_clean_full | categorical__forecast_gap_bucket_room_0_1 | -0.3754 | 0.3754 |
| physical_clean_full | numeric__forecast_gap_to_running_ticks | 0.3707 | 0.3707 |
| physical_clean_full | categorical__true_local_time_bucket_early_afternoon | -0.3502 | 0.3502 |

## Recommendation
- Keep `p_leg_win` as an EV scorer, not a done/not-done gate.
- Prefer the lean `physical_clean` view over `physical_plus` unless additional forward dates show the larger view is not overfitting.
- Do not start the shadow scorer yet in this step; first use this report to decide whether the feature view should be frozen as an as-of model manifest.
