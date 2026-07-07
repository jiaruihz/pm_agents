# Late-Window Residual Physical Feature Ablation v2

Status: `snapshot`
Model ids: `late_window_residual_physical_base_logit_v2`, `late_window_residual_physical_clean_clock_tick_obs_logit_v2`, `late_window_residual_physical_plus_logit_v2`

## Verdict
The lean enriched PIT physical view is the cleaner next research view because it fixes the local-clock and F/C scale issues and adds observation freshness without using market price as a model input. In strict expanding-forward probability validation, base logloss=0.4003, Brier=0.1228, AUC=0.7694; clean logloss=0.4021, Brier=0.1219, AUC=0.7720; plus logloss=0.4171, Brier=0.1271, AUC=0.7724. Forward edge>=0 EV replay ROI is base=5.0%, clean=4.2%, plus=3.3%. Clean improves Brier/AUC slightly but not logloss or EV; plus-all overfits more. This remains research-only because support is still only four forward active dates.

## Data
- Input first-cross rows: 688
- Settled model rows: 681
- Target dates: 2026-06-20..2026-07-04 (13 dates)
- Forward window: 2026-06-29..2026-07-04
- Model inputs exclude orderbook price/depth/spread, settlement labels, PnL, and ROI.
- `physical_clean` uses true city-local clock, F/C tick-normalized distances, and observation age/count.
- `physical_plus` further adds forecast source, solar-window proxies, and native duplicate features; it is included as a stress test, not the recommended view.

## Probability Metrics
| model | scope | rows | dates | hit_rate | logloss | brier | auc | avg_p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| physical_base | all_lodo | 681 | 13 | 0.8032 | 0.3804 | 0.1152 | 0.8031 | 0.8011 |
| physical_base | forward_lodo | 621 | 5 | 0.8132 | 0.3751 | 0.1131 | 0.7985 | 0.8098 |
| physical_clean | all_lodo | 681 | 13 | 0.8032 | 0.3786 | 0.1135 | 0.8072 | 0.7971 |
| physical_clean | forward_lodo | 621 | 5 | 0.8132 | 0.3743 | 0.1115 | 0.8002 | 0.8061 |
| physical_plus | all_lodo | 681 | 13 | 0.8032 | 0.3835 | 0.1155 | 0.8048 | 0.7892 |
| physical_plus | forward_lodo | 621 | 5 | 0.8132 | 0.3776 | 0.1131 | 0.8011 | 0.7977 |
| heuristic_leg_score | all_lodo | 681 | 13 | 0.8032 | 6.2009 | 0.5476 | 0.5585 | 0.2277 |
| heuristic_leg_score | forward_lodo | 621 | 5 | 0.8132 | 6.0008 | 0.5370 | 0.5708 | 0.2444 |
| market_entry_price | all_lodo | 681 | 13 | 0.8032 | 0.2528 | 0.0820 | 0.9283 | 0.8039 |
| market_entry_price | forward_lodo | 621 | 5 | 0.8132 | 0.2560 | 0.0824 | 0.9223 | 0.8087 |
| physical_base | forward_expanding | 414 | 4 | 0.8116 | 0.4003 | 0.1228 | 0.7694 | 0.7825 |
| physical_clean | forward_expanding | 414 | 4 | 0.8116 | 0.4021 | 0.1219 | 0.7720 | 0.7806 |
| physical_plus | forward_expanding | 414 | 4 | 0.8116 | 0.4171 | 0.1271 | 0.7724 | 0.7671 |
| heuristic_leg_score | forward_expanding | 621 | 5 | 0.8132 | 6.0008 | 0.5370 | 0.5708 | 0.2444 |
| market_entry_price | forward_expanding | 621 | 5 | 0.8132 | 0.2560 | 0.0824 | 0.9223 | 0.8087 |

## Forward EV Policy: All Legs
| model | edge_threshold | rows | active_dates | cities | avg_p | avg_entry | avg_edge | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base_expanding | 0.0000 | 137 | 4 | 28 | 0.7211 | 0.5130 | 0.1995 | 0.5474 | 0.0496 | -0.0351 | 0.1899 |
| base_expanding | 0.0100 | 130 | 4 | 26 | 0.7147 | 0.4960 | 0.2100 | 0.5308 | 0.0517 | -0.0350 | 0.1889 |
| base_expanding | 0.0200 | 126 | 4 | 26 | 0.7115 | 0.4865 | 0.2162 | 0.5238 | 0.0576 | -0.0546 | 0.2132 |
| base_expanding | 0.0300 | 122 | 4 | 26 | 0.7044 | 0.4731 | 0.2225 | 0.5246 | 0.0885 | -0.0394 | 0.2132 |
| clean_expanding | 0.0000 | 136 | 4 | 27 | 0.7256 | 0.5278 | 0.1895 | 0.5588 | 0.0424 | -0.0001 | 0.0823 |
| clean_expanding | 0.0100 | 128 | 4 | 26 | 0.7146 | 0.5049 | 0.2011 | 0.5469 | 0.0651 | 0.0153 | 0.1011 |
| clean_expanding | 0.0200 | 123 | 4 | 26 | 0.7043 | 0.4868 | 0.2087 | 0.5366 | 0.0828 | 0.0149 | 0.1190 |
| clean_expanding | 0.0300 | 114 | 4 | 25 | 0.7040 | 0.4720 | 0.2232 | 0.5175 | 0.0763 | -0.0100 | 0.1379 |
| plus_expanding | 0.0000 | 129 | 4 | 27 | 0.7114 | 0.5246 | 0.1785 | 0.5504 | 0.0329 | -0.0355 | 0.0948 |
| plus_expanding | 0.0100 | 125 | 4 | 26 | 0.7076 | 0.5152 | 0.1841 | 0.5360 | 0.0239 | -0.0437 | 0.0842 |
| plus_expanding | 0.0200 | 114 | 4 | 26 | 0.6971 | 0.4881 | 0.2004 | 0.5263 | 0.0597 | -0.0416 | 0.1638 |
| plus_expanding | 0.0300 | 106 | 4 | 26 | 0.6827 | 0.4600 | 0.2138 | 0.5283 | 0.1267 | -0.0183 | 0.1709 |

## Forward EV Policy: d1 NO
| model | edge_threshold | rows | active_dates | cities | avg_p | avg_entry | avg_edge | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base_expanding | 0.0000 | 46 | 4 | 21 | 0.8269 | 0.6080 | 0.2090 | 0.6957 | 0.1257 | 0.0404 | 0.3213 |
| base_expanding | 0.0100 | 45 | 4 | 20 | 0.8246 | 0.6010 | 0.2135 | 0.6889 | 0.1273 | 0.0386 | 0.3364 |
| base_expanding | 0.0200 | 43 | 4 | 20 | 0.8249 | 0.5919 | 0.2227 | 0.6744 | 0.1200 | -0.0313 | 0.3570 |
| base_expanding | 0.0300 | 43 | 4 | 20 | 0.8249 | 0.5919 | 0.2227 | 0.6744 | 0.1200 | -0.0313 | 0.3570 |
| clean_expanding | 0.0000 | 44 | 4 | 21 | 0.8440 | 0.6202 | 0.2142 | 0.7500 | 0.1908 | 0.1436 | 0.3583 |
| clean_expanding | 0.0100 | 43 | 4 | 20 | 0.8419 | 0.6132 | 0.2190 | 0.7442 | 0.1947 | 0.1479 | 0.3583 |
| clean_expanding | 0.0200 | 41 | 4 | 20 | 0.8364 | 0.5974 | 0.2290 | 0.7317 | 0.2046 | 0.1562 | 0.3583 |
| clean_expanding | 0.0300 | 38 | 4 | 19 | 0.8304 | 0.5750 | 0.2450 | 0.7105 | 0.2136 | 0.1566 | 0.3701 |
| plus_expanding | 0.0000 | 46 | 4 | 21 | 0.8220 | 0.6280 | 0.1846 | 0.7391 | 0.1595 | 0.0477 | 0.3207 |
| plus_expanding | 0.0100 | 46 | 4 | 21 | 0.8220 | 0.6280 | 0.1846 | 0.7391 | 0.1595 | 0.0477 | 0.3207 |
| plus_expanding | 0.0200 | 41 | 4 | 20 | 0.8089 | 0.5932 | 0.2055 | 0.7073 | 0.1723 | 0.0463 | 0.3522 |
| plus_expanding | 0.0300 | 39 | 4 | 19 | 0.8023 | 0.5769 | 0.2149 | 0.6923 | 0.1786 | 0.0463 | 0.3911 |

## Top Coefficients
| model | feature | coef | abs_coef |
| --- | --- | --- | --- |
| physical_base_full | categorical__peak_delta_bucket_peak_gt1h_ahead | -1.4930 | 1.4930 |
| physical_base_full | categorical__leg_current_yes | -1.0543 | 1.0543 |
| physical_base_full | numeric__target_distance_native | 0.6617 | 0.6617 |
| physical_base_full | categorical__leg_d1_no | 0.6489 | 0.6489 |
| physical_base_full | categorical__forecast_gap_bucket_room_1_2 | -0.5991 | 0.5991 |
| physical_base_full | categorical__forecast_gap_bucket_busted_lt_minus1 | 0.5976 | 0.5976 |
| physical_base_full | categorical__peak_delta_bucket_near_peak | 0.5777 | 0.5777 |
| physical_base_full | numeric__forecast_gap_to_running_native | 0.5663 | 0.5663 |
| physical_base_full | categorical__forecast_gap_bucket_capped_minus1_0 | 0.4635 | 0.4635 |
| physical_base_full | numeric__forecast_margin_to_target_native | -0.3787 | 0.3787 |
| physical_base_full | categorical__peak_delta_bucket_post_peak_0_5_1_5h | 0.3550 | 0.3550 |
| physical_base_full | categorical__peak_delta_bucket_post_peak_gt1_5h | 0.3535 | 0.3535 |
| physical_base_full | categorical__forecast_gap_bucket_room_0_1 | -0.3441 | 0.3441 |
| physical_base_full | categorical__leg_d2_no | 0.2999 | 0.2999 |
| physical_base_full | numeric__decline_native | 0.2478 | 0.2478 |
| physical_base_full | numeric__running_value | -0.2354 | 0.2354 |
| physical_base_full | categorical__path_state_decline | 0.2198 | 0.2198 |
| physical_base_full | categorical__path_state_at_high | -0.2169 | 0.2169 |
| physical_base_full | categorical__peak_delta_bucket_peak_1h_to_15m_ahead | 0.2097 | 0.2097 |
| physical_base_full | categorical__forecast_gap_bucket_room_gt2 | -0.1152 | 0.1152 |
| physical_base_full | categorical__leg_d3_no | 0.1084 | 0.1084 |
| physical_base_full | numeric__forecast_peak_delta_hours_local | 0.0853 | 0.0853 |
| physical_base_full | numeric__local_time_float | 0.0691 | 0.0691 |
| physical_base_full | numeric__metar_obs_count_today | 0.0600 | 0.0600 |
| physical_base_full | categorical__unit_C | 0.0307 | 0.0307 |
| physical_base_full | categorical__unit_F | -0.0279 | 0.0279 |
| physical_clean_full | categorical__peak_delta_bucket_peak_gt1h_ahead | -1.4626 | 1.4626 |
| physical_clean_full | categorical__leg_current_yes | -0.8272 | 0.8272 |
| physical_clean_full | categorical__leg_d1_no | 0.7127 | 0.7127 |
| physical_clean_full | numeric__target_distance_ticks | 0.6836 | 0.6836 |
| physical_clean_full | categorical__forecast_gap_bucket_busted_lt_minus1 | 0.6523 | 0.6523 |
| physical_clean_full | categorical__forecast_gap_bucket_room_1_2 | -0.6234 | 0.6234 |
| physical_clean_full | categorical__peak_delta_bucket_near_peak | 0.5376 | 0.5376 |
| physical_clean_full | numeric__forecast_gap_to_running_ticks | 0.5245 | 0.5245 |
| physical_clean_full | categorical__true_local_time_bucket_peak_window | -0.4617 | 0.4617 |
| physical_clean_full | categorical__true_local_time_bucket_late_afternoon | 0.4550 | 0.4550 |
| physical_clean_full | categorical__forecast_gap_bucket_capped_minus1_0 | 0.4301 | 0.4301 |
| physical_clean_full | categorical__peak_delta_bucket_post_peak_0_5_1_5h | 0.3816 | 0.3816 |
| physical_clean_full | categorical__obs_count_bucket_dense_13_24 | 0.3493 | 0.3493 |
| physical_clean_full | categorical__obs_count_bucket_very_dense_25_plus | -0.3249 | 0.3249 |

## Recommendation
- Keep `p_leg_win` as an EV scorer, not a done/not-done gate.
- Prefer the lean `physical_clean` view over `physical_plus` unless additional forward dates show the larger view is not overfitting.
- Do not start the shadow scorer yet in this step; first use this report to decide whether the feature view should be frozen as an as-of model manifest.
