# Late-window residual capture feature-layer v2

## 数据快照
- sync/rebuild: `scripts/ops/sync_weather_remote.sh && scripts/weather_dashboard/run_stack.sh on 2026-07-06 17:47-17:51 Asia/Shanghai`
- DB fact built: `2026-07-06T17:01:32.843071+00:00`; CLOB gate: `True`
- settlement_outcomes: `2026-05-04..2026-07-06`
- feature layer: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`; status `stale`; dates `2026-05-19..2026-07-04`; state rows `5052`
- orderbook matched snapshot-level leg quote rows: `14956`; strict 1-5 point rows with depth: `5621`; first-cross rows `5598`

## 结论
Feature-layer v2 uses the shared intraday weather regime atlas for state and keeps orderbook execution at real bid/ask. First-cross de-duplication is the strategy-relevant view: it removes repeated snapshots of the same city/hour/leg/bracket opportunity. Broad taker residual capture remains negative; the only actionable shape is a shadow scorer, not a live rule: prefer d3 NO when feature-layer runway still has room, d2 NO after plateau/pullback states, and current YES only in narrow near-high/unknown-clock plateau states. All candidate rules remain low-forward-support and must run shadow before any live decision-input rewire.

## Snapshot-level strict 1-5 point residual legs
| execution_mode | leg | settled_rows | active_dates | avg_entry_price | hit_rate | roi | daily_pnl_ci_low | daily_pnl_ci_high | top_size_sum |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| maker | current_yes | 1083 | 45 | 0.977 | 0.989 | 0.012 | 6.489 | 17.995 | 148347.780 |
| maker | d1_no | 978 | 45 | 0.977 | 0.988 | 0.011 | 3.989 | 16.927 | 82205.310 |
| maker | d2_no | 817 | 46 | 0.979 | 0.993 | 0.014 | 6.153 | 16.120 | 67010.740 |
| maker | d3_no | 350 | 44 | 0.981 | 0.994 | 0.014 | 1.204 | 7.331 | 24649.820 |
| taker | current_yes | 905 | 44 | 0.980 | 0.973 | -0.007 | -17.319 | 3.357 | 122803.250 |
| taker | d1_no | 928 | 44 | 0.979 | 0.968 | -0.013 | -25.324 | 0.993 | 114254.740 |
| taker | d2_no | 409 | 46 | 0.979 | 0.956 | -0.025 | -19.859 | -1.237 | 108568.950 |
| taker | d3_no | 151 | 43 | 0.982 | 0.987 | 0.004 | -2.467 | 2.693 | 84056.510 |

## First-cross strict 1-5 point residual legs
| execution_mode | leg | settled_rows | active_dates | avg_entry_price | hit_rate | roi | daily_pnl_ci_low | daily_pnl_ci_high | top_size_sum |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| maker | current_yes | 1077 | 45 | 0.977 | 0.989 | 0.012 | 6.435 | 17.874 | 147593.480 |
| maker | d1_no | 976 | 45 | 0.977 | 0.988 | 0.011 | 3.971 | 16.898 | 81779.810 |
| maker | d2_no | 817 | 46 | 0.979 | 0.993 | 0.014 | 6.153 | 16.120 | 67010.740 |
| maker | d3_no | 350 | 44 | 0.981 | 0.994 | 0.014 | 1.204 | 7.331 | 24649.820 |
| taker | current_yes | 898 | 44 | 0.980 | 0.973 | -0.008 | -17.418 | 3.231 | 121972.290 |
| taker | d1_no | 920 | 44 | 0.979 | 0.967 | -0.013 | -25.519 | 0.690 | 113955.950 |
| taker | d2_no | 409 | 46 | 0.979 | 0.956 | -0.025 | -19.859 | -1.237 | 108568.950 |
| taker | d3_no | 151 | 43 | 0.982 | 0.987 | 0.004 | -2.467 | 2.693 | 84056.510 |

## Forward 2026-06-29..2026-07-04
| execution_mode | leg | settled_rows | active_dates | avg_entry_price | hit_rate | roi | daily_pnl_ci_low | daily_pnl_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| maker | current_yes | 75 | 6 | 0.974 | 0.987 | 0.013 | -0.575 | 2.175 |
| maker | d1_no | 64 | 6 | 0.977 | 0.984 | 0.008 | -1.249 | 1.844 |
| maker | d2_no | 52 | 6 | 0.980 | 0.981 | 0.001 | -1.599 | 1.081 |
| maker | d3_no | 17 | 5 | 0.984 | 1.000 | 0.016 | 0.207 | 0.343 |
| taker | current_yes | 59 | 5 | 0.978 | 0.932 | -0.048 | -8.386 | 1.241 |
| taker | d1_no | 66 | 5 | 0.980 | 0.970 | -0.012 | -4.526 | 1.426 |
| taker | d2_no | 32 | 6 | 0.979 | 0.938 | -0.043 | -3.321 | 0.602 |
| taker | d3_no | 11 | 4 | 0.982 | 1.000 | 0.018 | 0.102 | 0.259 |

## First-cross forward 2026-06-29..2026-07-04
| execution_mode | leg | settled_rows | active_dates | avg_entry_price | hit_rate | roi | daily_pnl_ci_low | daily_pnl_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| maker | current_yes | 75 | 6 | 0.974 | 0.987 | 0.013 | -0.575 | 2.175 |
| maker | d1_no | 64 | 6 | 0.977 | 0.984 | 0.008 | -1.249 | 1.844 |
| maker | d2_no | 52 | 6 | 0.980 | 0.981 | 0.001 | -1.599 | 1.081 |
| maker | d3_no | 17 | 5 | 0.984 | 1.000 | 0.016 | 0.207 | 0.343 |
| taker | current_yes | 59 | 5 | 0.978 | 0.932 | -0.048 | -8.386 | 1.241 |
| taker | d1_no | 66 | 5 | 0.980 | 0.970 | -0.012 | -4.526 | 1.426 |
| taker | d2_no | 32 | 6 | 0.979 | 0.938 | -0.043 | -3.321 | 0.602 |
| taker | d3_no | 11 | 4 | 0.982 | 1.000 | 0.018 | 0.102 | 0.259 |

## Basket risk
| execution_mode | rows | settled_rows | active_dates | cost | pnl | roi | daily_pnl_ci_low | daily_pnl_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| maker | 2156 | 2156 | 46 | 3156.891 | 39.109 | 0.012 | 24.922 | 53.667 |
| taker | 1596 | 1596 | 46 | 2346.509 | -27.509 | -0.012 | -53.399 | -2.238 |

## First-cross basket risk
| execution_mode | rows | settled_rows | active_dates | cost | pnl | roi | daily_pnl_ci_low | daily_pnl_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| maker | 2149 | 2149 | 46 | 3149.004 | 38.996 | 0.012 | 24.823 | 53.566 |
| taker | 1588 | 1588 | 46 | 2331.817 | -27.817 | -0.012 | -53.605 | -2.503 |

## First-cross feature slices
### Day regime
| execution_mode | day_regime | settled_rows | active_dates | hit_rate | roi | daily_pnl_ci_low | daily_pnl_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- |
| maker | day_forecast_busted | 1193 | 43 | 0.988 | 0.010 | 1.047 | 20.757 |
| maker | day_forecast_capped | 701 | 44 | 0.993 | 0.015 | 4.361 | 15.216 |
| maker | day_marginal_runway | 457 | 43 | 0.987 | 0.009 | -3.086 | 9.891 |
| maker | day_open_runway | 527 | 43 | 0.991 | 0.014 | 1.075 | 12.541 |
| maker | day_space_unknown | 342 | 12 | 0.994 | 0.017 | 2.779 | 8.824 |
| taker | day_forecast_busted | 871 | 43 | 0.966 | -0.015 | -31.266 | 3.065 |
| taker | day_forecast_capped | 476 | 43 | 0.968 | -0.013 | -16.140 | 2.512 |
| taker | day_marginal_runway | 343 | 42 | 0.965 | -0.016 | -17.555 | 3.901 |
| taker | day_open_runway | 405 | 43 | 0.975 | -0.005 | -12.663 | 6.031 |
| taker | day_space_unknown | 283 | 12 | 0.975 | -0.006 | -9.111 | 4.372 |

### Intraday state
| execution_mode | intraday_state | settled_rows | active_dates | hit_rate | roi | daily_pnl_ci_low | daily_pnl_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- |
| maker | active_warming | 641 | 43 | 0.991 | 0.014 | 3.701 | 13.422 |
| maker | flat_or_cooling | 8 | 5 | 1.000 | 0.031 | 0.195 | 0.289 |
| maker | fresh_high | 841 | 44 | 0.988 | 0.012 | -0.498 | 17.699 |
| maker | mature_fade | 695 | 44 | 0.993 | 0.013 | 2.551 | 13.794 |
| maker | plateau_near_high | 253 | 37 | 0.992 | 0.018 | -0.707 | 7.662 |
| maker | pullback_uncertain | 568 | 44 | 0.991 | 0.012 | 0.827 | 11.461 |
| maker | reheating_after_dip | 163 | 38 | 0.975 | -0.004 | -4.771 | 2.657 |
| maker | slow_warming | 36 | 12 | 1.000 | 0.022 | 0.469 | 1.187 |
| maker | state_unknown | 15 | 3 | 1.000 | 0.020 | 0.030 | 0.777 |
| taker | active_warming | 580 | 44 | 0.967 | -0.012 | -16.398 | 1.747 |
| taker | flat_or_cooling | 13 | 6 | 0.923 | -0.050 | -2.753 | 0.542 |
| taker | fresh_high | 697 | 44 | 0.963 | -0.018 | -27.179 | 0.519 |
| taker | mature_fade | 367 | 42 | 0.978 | -0.005 | -9.916 | 5.252 |
| taker | plateau_near_high | 249 | 36 | 0.968 | -0.013 | -10.532 | 3.034 |
| taker | pullback_uncertain | 338 | 43 | 0.979 | -0.003 | -8.710 | 4.774 |
| taker | reheating_after_dip | 96 | 29 | 0.948 | -0.034 | -8.478 | 0.957 |
| taker | slow_warming | 33 | 13 | 1.000 | 0.023 | 0.507 | 0.988 |
| taker | state_unknown | 5 | 2 | 1.000 | 0.026 | 0.076 | 0.175 |

### Running max state
| execution_mode | running_max_state | settled_rows | active_dates | hit_rate | roi | daily_pnl_ci_low | daily_pnl_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- |
| maker | fresh_running_high | 1302 | 42 | 0.988 | 0.011 | 3.287 | 24.006 |
| maker | mature_fade | 820 | 45 | 0.990 | 0.010 | 1.475 | 13.728 |
| maker | near_high_plateau | 78 | 9 | 1.000 | 0.025 | 0.871 | 2.952 |
| maker | pullback_from_high | 581 | 44 | 0.990 | 0.011 | 0.025 | 11.318 |
| maker | running_max_clock_unknown | 113 | 22 | 1.000 | 0.023 | 1.781 | 3.443 |
| maker | stalled_high | 326 | 33 | 0.994 | 0.019 | 1.179 | 9.251 |
| taker | fresh_running_high | 1101 | 45 | 0.962 | -0.018 | -39.002 | -2.692 |
| taker | mature_fade | 430 | 43 | 0.974 | -0.008 | -12.569 | 3.998 |
| taker | near_high_plateau | 84 | 12 | 0.988 | 0.008 | -0.707 | 2.011 |
| taker | pullback_from_high | 361 | 43 | 0.975 | -0.007 | -10.961 | 4.516 |
| taker | running_max_clock_unknown | 93 | 21 | 0.989 | 0.010 | -1.329 | 2.347 |
| taker | stalled_high | 309 | 33 | 0.968 | -0.012 | -11.450 | 2.919 |

### Forecast gap
| execution_mode | forecast_gap_bucket | settled_rows | active_dates | hit_rate | roi | daily_pnl_ci_low | daily_pnl_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- |
| maker | busted_lt_minus1 | 914 | 41 | 0.989 | 0.011 | 0.653 | 16.696 |
| maker | capped_minus1_0 | 660 | 44 | 0.986 | 0.008 | -3.662 | 12.189 |
| maker | room_0_1 | 499 | 43 | 0.992 | 0.014 | 1.108 | 11.672 |
| maker | room_1_2 | 351 | 43 | 0.994 | 0.019 | 1.544 | 9.855 |
| maker | room_gt2 | 454 | 42 | 0.989 | 0.012 | -0.800 | 9.853 |
| maker |  | 342 | 12 | 0.994 | 0.017 | 2.779 | 8.824 |
| taker | busted_lt_minus1 | 662 | 40 | 0.967 | -0.014 | -24.093 | 3.606 |
| taker | capped_minus1_0 | 484 | 44 | 0.959 | -0.023 | -26.468 | 1.482 |
| taker | room_0_1 | 335 | 41 | 0.970 | -0.010 | -11.444 | 3.628 |
| taker | room_1_2 | 261 | 40 | 0.981 | 0.001 | -6.917 | 4.937 |
| taker | room_gt2 | 353 | 41 | 0.972 | -0.009 | -13.742 | 4.934 |
| taker |  | 283 | 12 | 0.975 | -0.006 | -9.111 | 4.372 |

## Candidate scorer rules
| rule_id | execution_mode | period | leg | feature_col | feature_values | settled_rows | active_dates | hit_rate | roi | pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| d3_no_day_room | taker | full | d3_no | day_regime | day_forecast_capped,day_marginal_runway,day_open_runway | 109 | 38 | 1.000 | 0.018 | 1.978 |
| d3_no_day_room | taker | forward_2026-06-29_2026-07-04 | d3_no | day_regime | day_forecast_capped,day_marginal_runway,day_open_runway | 3 | 2 | 1.000 | 0.027 | 0.078 |
| d3_no_forecast_room | taker | full | d3_no | forecast_gap_bucket | room_0_1,room_1_2,room_gt2 | 91 | 36 | 1.000 | 0.018 | 1.605 |
| d3_no_forecast_room | taker | forward_2026-06-29_2026-07-04 | d3_no | forecast_gap_bucket | room_0_1,room_1_2,room_gt2 | 2 | 2 | 1.000 | 0.025 | 0.049 |
| d2_no_plateau_pullback | taker | full | d2_no | intraday_state | plateau_near_high,pullback_uncertain | 74 | 33 | 1.000 | 0.018 | 1.309 |
| d2_no_plateau_pullback | taker | forward_2026-06-29_2026-07-04 | d2_no | intraday_state | plateau_near_high,pullback_uncertain | 6 | 3 | 1.000 | 0.018 | 0.107 |
| d2_no_stalled_pullback | taker | full | d2_no | running_max_state | pullback_from_high,stalled_high | 92 | 38 | 1.000 | 0.019 | 1.714 |
| d2_no_stalled_pullback | taker | forward_2026-06-29_2026-07-04 | d2_no | running_max_state | pullback_from_high,stalled_high | 5 | 4 | 1.000 | 0.020 | 0.099 |
| current_yes_plateau_unknown_clock | taker | full | current_yes | running_max_state | near_high_plateau,running_max_clock_unknown | 61 | 25 | 1.000 | 0.021 | 1.274 |
| current_yes_plateau_unknown_clock | taker | forward_2026-06-29_2026-07-04 | current_yes | running_max_state | near_high_plateau,running_max_clock_unknown | 24 | 5 | 1.000 | 0.024 | 0.553 |
| d3_no_day_room | maker | full | d3_no | day_regime | day_forecast_capped,day_marginal_runway,day_open_runway | 254 | 42 | 1.000 | 0.020 | 5.001 |
| d3_no_day_room | maker | forward_2026-06-29_2026-07-04 | d3_no | day_regime | day_forecast_capped,day_marginal_runway,day_open_runway | 6 | 4 | 1.000 | 0.019 | 0.111 |
| d3_no_forecast_room | maker | full | d3_no | forecast_gap_bucket | room_0_1,room_1_2,room_gt2 | 210 | 41 | 1.000 | 0.021 | 4.227 |
| d3_no_forecast_room | maker | forward_2026-06-29_2026-07-04 | d3_no | forecast_gap_bucket | room_0_1,room_1_2,room_gt2 | 6 | 4 | 1.000 | 0.019 | 0.111 |
| d2_no_plateau_pullback | maker | full | d2_no | intraday_state | plateau_near_high,pullback_uncertain | 178 | 41 | 1.000 | 0.021 | 3.733 |
| d2_no_plateau_pullback | maker | forward_2026-06-29_2026-07-04 | d2_no | intraday_state | plateau_near_high,pullback_uncertain | 5 | 3 | 1.000 | 0.024 | 0.118 |
| d2_no_stalled_pullback | maker | full | d2_no | running_max_state | pullback_from_high,stalled_high | 217 | 42 | 1.000 | 0.022 | 4.593 |
| d2_no_stalled_pullback | maker | forward_2026-06-29_2026-07-04 | d2_no | running_max_state | pullback_from_high,stalled_high | 5 | 3 | 1.000 | 0.019 | 0.092 |
| current_yes_plateau_unknown_clock | maker | full | current_yes | running_max_state | near_high_plateau,running_max_clock_unknown | 61 | 24 | 1.000 | 0.025 | 1.498 |
| current_yes_plateau_unknown_clock | maker | forward_2026-06-29_2026-07-04 | current_yes | running_max_state | near_high_plateau,running_max_clock_unknown | 25 | 5 | 1.000 | 0.029 | 0.702 |

## Chengdu 2026-07-06 As-of Case
Feature-layer atlas currently stops at 2026-07-04, so current-day Chengdu is quote evidence only in this v2.
_empty_

## Chengdu 39 NO Orderbook Timeline
This table is quote evidence only: the Mac observation cache kept latest state, not a 15:00/16:00 historical state for 2026-07-06, so these rows are not injected into the settled PIT replay.
| snapshot_time_bj | best_ask | ask_size | best_bid | bid_size | spread | depth_ask_5c |
| --- | --- | --- | --- | --- | --- | --- |
| 14:06:20 | 0.840 | 20.000 | 0.760 | 6.670 | 0.080 | 157.270 |
| 14:22:50 | 0.840 | 20.000 | 0.800 | 169.060 | 0.040 | 133.370 |
| 14:39:09 | 0.890 | 50.000 | 0.850 | 10.000 | 0.040 | 107.330 |
| 14:55:49 | 0.930 | 32.000 | 0.860 | 8.140 | 0.070 | 467.080 |
| 15:12:18 | 0.930 | 8.000 | 0.880 | 5.000 | 0.050 | 540.980 |
| 15:28:57 | 0.950 | 88.000 | 0.940 | 8.000 | 0.010 | 1328.460 |
| 15:45:43 | 0.960 | 180.470 | 0.959 | 20.000 | 0.001 | 1504.170 |
| 16:02:17 | 0.988 | 75.450 | 0.982 | 20.000 | 0.006 | 886.360 |
| 16:19:07 | 0.988 | 6.890 | 0.985 | 10.000 | 0.003 | 872.200 |
| 16:35:57 | 0.998 | 144.700 | 0.985 | 20.000 | 0.013 | 384.030 |
| 16:53:05 | 0.998 | 97.250 | 0.990 | 129.930 | 0.008 | 336.580 |
| 17:10:02 | 0.999 | 33.910 | 0.991 | 166.700 | 0.008 | 33.910 |
| 17:27:12 | 0.999 | 68.030 | 0.997 | 64.580 | 0.002 | 68.030 |

## Failure cases
| target_date | city | decision_hour_local | leg | execution_mode | bracket | entry_price | residual_points | final_winning_bracket | pnl_per_share |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-12 | Munich | 15 | d3_no | taker | 17 | 0.990 | 1.000 | 17 | -0.990 |
| 2026-06-12 | SaoPaulo | 15 | d1_no | taker | 21 | 0.990 | 1.000 | 21 | -0.990 |
| 2026-06-13 | Wellington | 15 | d1_no | taker | 15 | 0.990 | 1.000 | 15 | -0.990 |
| 2026-06-06 | Munich | 16 | current_yes | taker | 23 | 0.990 | 1.000 | 24+ | -0.990 |
| 2026-07-01 | Guangzhou | 15 | d2_no | taker | 34 | 0.990 | 1.000 | 34 | -0.990 |
| 2026-06-14 | Atlanta | 15 | d2_no | taker | 90-91 | 0.990 | 1.000 | 90-91 | -0.990 |
| 2026-06-12 | SaoPaulo | 15 | current_yes | taker | 20 | 0.990 | 1.000 | 21 | -0.990 |
| 2026-06-13 | Wellington | 15 | current_yes | maker | 14 | 0.990 | 1.000 | 15 | -0.990 |
| 2026-06-15 | Miami | 15 | current_yes | taker | 92-93 | 0.989 | 1.100 | 94-95 | -0.990 |
| 2026-06-15 | Dallas | 16 | d2_no | taker | 86-87 | 0.989 | 1.100 | 86-87 | -0.990 |
| 2026-06-17 | Busan | 15 | d1_no | taker | 30 | 0.989 | 1.100 | 30 | -0.990 |
| 2026-06-25 | Wuhan | 16 | d2_no | taker | 31 | 0.989 | 1.100 | 31 | -0.990 |
| 2026-06-25 | Wuhan | 15 | d2_no | taker | 31 | 0.989 | 1.100 | 31 | -0.990 |
| 2026-06-13 | Wellington | 15 | d1_no | maker | 15 | 0.989 | 1.100 | 15 | -0.989 |
| 2026-06-27 | Guangzhou | 15 | current_yes | taker | 34 | 0.987 | 1.300 | 35+ | -0.988 |
| 2026-06-04 | Wellington | 18 | current_yes | taker | 17 | 0.987 | 1.300 | 18 | -0.988 |
| 2026-06-04 | Wellington | 18 | d1_no | taker | 18 | 0.987 | 1.300 | 18 | -0.988 |
| 2026-06-16 | Denver | 15 | d2_no | taker | 94-95 | 0.986 | 1.400 | 94-95 | -0.987 |
| 2026-05-22 | Ankara | 17 | d1_no | maker | 20 | 0.986 | 1.400 | 20 | -0.986 |
| 2026-06-04 | Wellington | 18 | current_yes | maker | 17 | 0.986 | 1.400 | 18 | -0.986 |
| 2026-06-17 | Busan | 15 | current_yes | taker | 29 | 0.985 | 1.500 | 30 | -0.986 |
| 2026-06-30 | Shanghai | 15 | current_yes | taker | 27 | 0.985 | 1.500 | 28 | -0.986 |
| 2026-06-30 | Shanghai | 15 | d1_no | taker | 28 | 0.985 | 1.500 | 28 | -0.986 |
| 2026-05-22 | Ankara | 15 | d2_no | taker | 20 | 0.985 | 1.500 | 20 | -0.986 |
| 2026-06-04 | Wellington | 18 | d1_no | maker | 18 | 0.984 | 1.600 | 18 | -0.984 |
| 2026-06-16 | Denver | 16 | d2_no | taker | 94-95 | 0.982 | 1.800 | 94-95 | -0.983 |
| 2026-06-04 | Wellington | 15 | d2_no | taker | 18 | 0.980 | 2.000 | 18 | -0.981 |
| 2026-07-01 | Guangzhou | 16 | current_yes | taker | 32 | 0.980 | 2.000 | 34 | -0.981 |
| 2026-05-23 | Ankara | 17 | d1_no | taker | 19 | 0.980 | 2.000 | 19 | -0.981 |
| 2026-06-07 | Guangzhou | 16 | d1_no | taker | 35 | 0.980 | 2.000 | 35 | -0.981 |

## 8环覆盖
- 描述性绩效/统计推断/执行微结构/容量/组合相关性/基准: covered at research replay level.
- 信号判别: covered as feature-layer slices, not a trained probability model.
- 前瞻门: checked on 2026-06-29..2026-07-04; v2 remains research/shadow until fresh feature-layer forward accumulates.

## Gate verdict
在 2026-05-19..2026-07-04，late-window strict residual 1-5 point replay 相对 market-implied zero EV 的 fee-after ROI 未同时通过显著性、可执行基准和 forward 三门；significance=FAIL/NA, baseline=FAIL, forward=FAIL/NA, conclusion=inconclusive。
