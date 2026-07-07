# Late-Window Residual Calibrated Features v1

Status: `snapshot`
Model id: `late_window_residual_physical_logit_v1`

## Verdict
This run promotes the hand-scored heating/residual flags into a calibrated research feature candidate: `p_leg_win_physical_v1`. The model uses only PIT physical/state fields and excludes orderbook price, spread, depth, and settlement labels from inputs. It is useful as a shared feature candidate, but not live-ready: leave-one-date validation improves over the heuristic score, while strict expanding-forward support is still small and market price remains a strong execution baseline.

## Data
- Input rows: 1005
- Settled first-cross rows used for modeling: 998
- Target dates: 2026-06-20..2026-07-06 (15 dates)
- Features: `local_time_float, forecast_peak_delta_hours_local, forecast_gap_to_running_native, decline_native, target_distance_native, forecast_margin_to_target_native, metar_obs_count_today, running_value, leg, unit, path_state, peak_delta_bucket, forecast_gap_bucket`
- Excluded from model inputs: `entry_price`, `best_bid`, `spread`, `top_size`, `depth_5c`, `final_yes`, `settlement`, `pnl`.

## Validation Metrics
| model | scope | rows | dates | hit_rate | logloss | brier | auc | avg_p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| physical_logit | all_lodo | 998 | 15 | 0.8026 | 0.3960 | 0.1193 | 0.7845 | 0.8017 |
| heuristic_leg_score | all_lodo | 998 | 15 | 0.8026 | 6.2195 | 0.5479 | 0.5575 | 0.2298 |
| market_entry_price | all_lodo | 998 | 15 | 0.8026 | 0.2616 | 0.0850 | 0.9214 | 0.8057 |
| physical_logit | forward_lodo | 938 | 7 | 0.8092 | 0.3932 | 0.1182 | 0.7801 | 0.8085 |
| heuristic_leg_score | forward_lodo | 938 | 7 | 0.8092 | 6.0882 | 0.5409 | 0.5654 | 0.2409 |
| market_entry_price | forward_lodo | 938 | 7 | 0.8092 | 0.2643 | 0.0855 | 0.9167 | 0.8090 |
| physical_logit | forward_expanding | 731 | 6 | 0.8071 | 0.4150 | 0.1254 | 0.7553 | 0.7987 |
| heuristic_leg_score | forward_expanding | 731 | 6 | 0.8071 | 6.0753 | 0.5435 | 0.5627 | 0.2336 |
| market_entry_price | forward_expanding | 731 | 6 | 0.8071 | 0.2783 | 0.0920 | 0.9067 | 0.8021 |

## Probability Slices
| leg | p_bucket | rows | dates | avg_p | hit_rate | avg_entry | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | 0_25 | 30 | 11 | 0.2184 | 0.1000 | 0.1579 | -0.3844 |
| current_yes | 25_50 | 63 | 10 | 0.3521 | 0.2857 | 0.2485 | 0.1256 |
| current_yes | 50_70 | 36 | 9 | 0.6196 | 0.8333 | 0.6911 | 0.1952 |
| current_yes | 70_85 | 39 | 8 | 0.7654 | 0.7179 | 0.7761 | -0.0784 |
| current_yes | 85_100 | 14 | 5 | 0.8939 | 0.7857 | 0.8878 | -0.1187 |
| d1_no | 50_70 | 39 | 13 | 0.6687 | 0.7949 | 0.7976 | -0.0104 |
| d1_no | 70_85 | 104 | 14 | 0.7914 | 0.7692 | 0.7950 | -0.0393 |
| d1_no | 85_100 | 142 | 10 | 0.9286 | 0.9225 | 0.9019 | 0.0196 |
| d2_no | 50_70 | 22 | 7 | 0.6747 | 0.5455 | 0.7045 | -0.2355 |
| d2_no | 70_85 | 84 | 10 | 0.7877 | 0.7857 | 0.7802 | -0.0016 |
| d2_no | 85_100 | 171 | 10 | 0.9251 | 0.9532 | 0.9613 | -0.0100 |
| d3_no | 50_70 | 3 | 2 | 0.6465 | 1.0000 | 0.9097 | 0.0951 |
| d3_no | 70_85 | 51 | 5 | 0.7800 | 0.8627 | 0.8304 | 0.0324 |
| d3_no | 85_100 | 200 | 12 | 0.9275 | 0.9050 | 0.9188 | -0.0179 |

## Top Coefficients
| feature | coef | abs_coef |
| --- | --- | --- |
| categorical__peak_delta_bucket_peak_gt1h_ahead | -1.5906 | 1.5906 |
| categorical__leg_current_yes | -1.1512 | 1.1512 |
| categorical__peak_delta_bucket_post_peak_gt1_5h | 0.6864 | 0.6864 |
| categorical__leg_d1_no | 0.5689 | 0.5689 |
| categorical__forecast_gap_bucket_room_1_2 | -0.5653 | 0.5653 |
| categorical__forecast_gap_bucket_busted_lt_minus1 | 0.5416 | 0.5416 |
| numeric__target_distance_native | 0.5089 | 0.5089 |
| categorical__forecast_gap_bucket_capped_minus1_0 | 0.4629 | 0.4629 |
| categorical__peak_delta_bucket_near_peak | 0.4546 | 0.4546 |
| categorical__forecast_gap_bucket_room_0_1 | -0.3833 | 0.3833 |
| categorical__peak_delta_bucket_post_peak_0_5_1_5h | 0.3636 | 0.3636 |
| numeric__forecast_gap_to_running_native | 0.3545 | 0.3545 |
| categorical__leg_d2_no | 0.3417 | 0.3417 |
| numeric__running_value | -0.3379 | 0.3379 |
| numeric__decline_native | 0.3251 | 0.3251 |
| numeric__forecast_peak_delta_hours_local | -0.2855 | 0.2855 |
| categorical__leg_d3_no | 0.2492 | 0.2492 |
| numeric__forecast_margin_to_target_native | -0.1958 | 0.1958 |
| categorical__unit_F | 0.1170 | 0.1170 |
| categorical__unit_C | -0.1084 | 0.1084 |
| categorical__peak_delta_bucket_peak_1h_to_15m_ahead | 0.0947 | 0.0947 |
| numeric__metar_obs_count_today | 0.0689 | 0.0689 |
| categorical__forecast_gap_bucket_room_gt2 | -0.0473 | 0.0473 |
| numeric__local_time_float | 0.0356 | 0.0356 |
| categorical__path_state_at_high | 0.0253 | 0.0253 |

## Recommendation
- Keep `heating_done_score_v1` as an interpretable state summary, not a probability.
- Treat `p_leg_win_physical_v1` as the next shared feature candidate for late-window exact-bracket residual work.
- Do not use it as a live gate until it has more complete forward dates and an execution-cost model using shared `weather_feature_layer.execution` fields.
