# Late-Window Residual Calibrated Features v1

Status: `snapshot`
Model id: `late_window_residual_physical_logit_v1`

## Verdict
This run promotes the hand-scored heating/residual flags into a calibrated research feature candidate: `p_leg_win_physical_v1`. The model uses only PIT physical/state fields and excludes orderbook price, spread, depth, and settlement labels from inputs. It is useful as a shared feature candidate, but not live-ready: leave-one-date validation improves over the heuristic score, while strict expanding-forward support is still small and market price remains a strong execution baseline.

## Data
- Input rows: 688
- Settled first-cross rows used for modeling: 681
- Target dates: 2026-06-20..2026-07-04 (13 dates)
- Features: `local_time_float, forecast_peak_delta_hours_local, forecast_gap_to_running_native, decline_native, target_distance_native, forecast_margin_to_target_native, metar_obs_count_today, running_value, leg, unit, path_state, peak_delta_bucket, forecast_gap_bucket`
- Excluded from model inputs: `entry_price`, `best_bid`, `spread`, `top_size`, `depth_5c`, `final_yes`, `settlement`, `pnl`.

## Validation Metrics
| model | scope | rows | dates | hit_rate | logloss | brier | auc | avg_p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| physical_logit | all_lodo | 681 | 13 | 0.8032 | 0.3804 | 0.1152 | 0.8031 | 0.8011 |
| heuristic_leg_score | all_lodo | 681 | 13 | 0.8032 | 6.2009 | 0.5476 | 0.5585 | 0.2277 |
| market_entry_price | all_lodo | 681 | 13 | 0.8032 | 0.2528 | 0.0820 | 0.9283 | 0.8039 |
| physical_logit | forward_lodo | 621 | 5 | 0.8132 | 0.3751 | 0.1131 | 0.7985 | 0.8098 |
| heuristic_leg_score | forward_lodo | 621 | 5 | 0.8132 | 6.0008 | 0.5370 | 0.5708 | 0.2444 |
| market_entry_price | forward_lodo | 621 | 5 | 0.8132 | 0.2560 | 0.0824 | 0.9223 | 0.8087 |
| physical_logit | forward_expanding | 414 | 4 | 0.8116 | 0.4003 | 0.1228 | 0.7694 | 0.7825 |
| heuristic_leg_score | forward_expanding | 414 | 4 | 0.8116 | 5.9342 | 0.5396 | 0.5693 | 0.2332 |
| market_entry_price | forward_expanding | 414 | 4 | 0.8116 | 0.2766 | 0.0924 | 0.9084 | 0.7964 |

## Probability Slices
| leg | p_bucket | rows | dates | avg_p | hit_rate | avg_entry | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | 0_25 | 28 | 8 | 0.2263 | 0.1786 | 0.2360 | -0.2619 |
| current_yes | 25_50 | 38 | 8 | 0.3638 | 0.1842 | 0.1412 | 0.2677 |
| current_yes | 50_70 | 20 | 7 | 0.6280 | 0.9000 | 0.7702 | 0.1621 |
| current_yes | 70_85 | 27 | 6 | 0.7766 | 0.8148 | 0.7424 | 0.0916 |
| current_yes | 85_100 | 17 | 4 | 0.8888 | 0.8235 | 0.9414 | -0.1274 |
| d1_no | 50_70 | 21 | 6 | 0.6707 | 0.8571 | 0.8356 | 0.0198 |
| d1_no | 70_85 | 76 | 13 | 0.7748 | 0.7500 | 0.7773 | -0.0424 |
| d1_no | 85_100 | 107 | 9 | 0.9358 | 0.9346 | 0.9154 | 0.0182 |
| d2_no | 50_70 | 24 | 4 | 0.6751 | 0.7083 | 0.7195 | -0.0270 |
| d2_no | 70_85 | 50 | 8 | 0.7792 | 0.7200 | 0.7781 | -0.0825 |
| d2_no | 85_100 | 108 | 8 | 0.9345 | 0.9722 | 0.9626 | 0.0084 |
| d3_no | 50_70 | 7 | 1 | 0.6528 | 1.0000 | 0.7860 | 0.2605 |
| d3_no | 70_85 | 35 | 3 | 0.7819 | 0.8571 | 0.8537 | -0.0010 |
| d3_no | 85_100 | 123 | 10 | 0.9403 | 0.9024 | 0.9261 | -0.0279 |

## Top Coefficients
| feature | coef | abs_coef |
| --- | --- | --- |
| categorical__peak_delta_bucket_peak_gt1h_ahead | -1.4930 | 1.4930 |
| categorical__leg_current_yes | -1.0543 | 1.0543 |
| numeric__target_distance_native | 0.6617 | 0.6617 |
| categorical__leg_d1_no | 0.6489 | 0.6489 |
| categorical__forecast_gap_bucket_room_1_2 | -0.5991 | 0.5991 |
| categorical__forecast_gap_bucket_busted_lt_minus1 | 0.5976 | 0.5976 |
| categorical__peak_delta_bucket_near_peak | 0.5777 | 0.5777 |
| numeric__forecast_gap_to_running_native | 0.5663 | 0.5663 |
| categorical__forecast_gap_bucket_capped_minus1_0 | 0.4635 | 0.4635 |
| numeric__forecast_margin_to_target_native | -0.3787 | 0.3787 |
| categorical__peak_delta_bucket_post_peak_0_5_1_5h | 0.3550 | 0.3550 |
| categorical__peak_delta_bucket_post_peak_gt1_5h | 0.3535 | 0.3535 |
| categorical__forecast_gap_bucket_room_0_1 | -0.3441 | 0.3441 |
| categorical__leg_d2_no | 0.2999 | 0.2999 |
| numeric__decline_native | 0.2478 | 0.2478 |
| numeric__running_value | -0.2354 | 0.2354 |
| categorical__path_state_decline | 0.2198 | 0.2198 |
| categorical__path_state_at_high | -0.2169 | 0.2169 |
| categorical__peak_delta_bucket_peak_1h_to_15m_ahead | 0.2097 | 0.2097 |
| categorical__forecast_gap_bucket_room_gt2 | -0.1152 | 0.1152 |
| categorical__leg_d3_no | 0.1084 | 0.1084 |
| numeric__forecast_peak_delta_hours_local | 0.0853 | 0.0853 |
| numeric__local_time_float | 0.0691 | 0.0691 |
| numeric__metar_obs_count_today | 0.0600 | 0.0600 |
| categorical__unit_C | 0.0307 | 0.0307 |

## Recommendation
- Keep `heating_done_score_v1` as an interpretable state summary, not a probability.
- Treat `p_leg_win_physical_v1` as the next shared feature candidate for late-window exact-bracket residual work.
- Do not use it as a live gate until it has more complete forward dates and an execution-cost model using shared `weather_feature_layer.execution` fields.
