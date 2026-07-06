# Late-window residual capture for exact temperature brackets v1

## 数据快照
- sync/rebuild: `scripts/ops/sync_weather_remote.sh && scripts/weather_dashboard/run_stack.sh on 2026-07-06 17:47-17:51 Asia/Shanghai`
- DB fact built: `2026-07-06T09:57:36.505304+00:00`; CLOB gate: `True`
- settlement_outcomes: `2026-05-04..2026-07-05`
- observed shards: `2026-05-19..2026-07-04`; rows `6709`
- orderbook matched leg quote rows: `2699`; strict 1-5 point rows with depth: `1141`

## 结论
Strict 1-5 point residual capture is not live-ready. Taker rows are scarce because the high-probability NO legs often have no real ask or ask above 99c; maker rows have better point estimates but are queue/fill-probability assumptions, not executable fills. Current YES and d+1 NO are the fragile legs; d+2/d+3 NO are safer mechanically but usually priced too close to 1.00 for the requested 1-5 point residual.

## Strict 1-5 point residual legs
| execution_mode | leg | settled_rows | active_dates | avg_entry_price | hit_rate | roi | daily_pnl_ci_low | daily_pnl_ci_high | top_size_sum |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| maker | current_yes | 191 | 36 | 0.976 | 0.984 | 0.008 | -2.301 | 4.488 | 19599.740 |
| maker | d1_no | 187 | 37 | 0.976 | 0.979 | 0.003 | -3.569 | 3.680 | 11257.100 |
| maker | d2_no | 199 | 35 | 0.978 | 0.990 | 0.012 | -0.553 | 4.785 | 10521.170 |
| maker | d3_no | 75 | 23 | 0.980 | 1.000 | 0.021 | 1.041 | 2.013 | 5028.320 |
| taker | current_yes | 159 | 32 | 0.979 | 0.962 | -0.018 | -9.329 | 1.839 | 20043.380 |
| taker | d1_no | 185 | 33 | 0.977 | 0.957 | -0.022 | -13.935 | 2.617 | 17417.450 |
| taker | d2_no | 103 | 27 | 0.980 | 0.981 | -0.000 | -2.852 | 2.060 | 36722.760 |
| taker | d3_no | 41 | 15 | 0.982 | 1.000 | 0.018 | 0.534 | 0.903 | 35716.010 |

## Forward 2026-06-29..2026-07-04
| execution_mode | leg | settled_rows | active_dates | avg_entry_price | hit_rate | roi | daily_pnl_ci_low | daily_pnl_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| maker | current_yes | 80 | 6 | 0.973 | 0.975 | 0.002 | -3.174 | 2.280 |
| maker | d1_no | 78 | 6 | 0.975 | 0.974 | -0.001 | -3.109 | 2.062 |
| maker | d2_no | 71 | 6 | 0.980 | 0.986 | 0.006 | -1.571 | 2.080 |
| maker | d3_no | 29 | 5 | 0.980 | 1.000 | 0.020 | 0.343 | 0.841 |
| taker | current_yes | 69 | 5 | 0.979 | 0.942 | -0.039 | -7.711 | 0.875 |
| taker | d1_no | 97 | 6 | 0.978 | 0.948 | -0.031 | -11.834 | 1.821 |
| taker | d2_no | 36 | 6 | 0.979 | 0.972 | -0.008 | -2.052 | 0.684 |
| taker | d3_no | 19 | 5 | 0.984 | 1.000 | 0.015 | 0.164 | 0.403 |

## Basket risk
| execution_mode | rows | settled_rows | active_dates | cost | pnl | roi | daily_pnl_ci_low | daily_pnl_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| maker | 440 | 440 | 38 | 637.005 | 5.995 | 0.009 | -1.941 | 12.172 |
| taker | 349 | 349 | 37 | 478.087 | -6.087 | -0.013 | -22.614 | 5.377 |

## Chengdu 2026-07-06 As-of Case
| decision_hour_local | leg | execution_mode | bracket | entry_price | residual_points | top_size | spread | book_age_min | running_value | current_native | decline_native |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 17 | current_yes | taker | 37 | 0.930 | 7.000 | 10.480 | 0.040 | 6.917 | 37.000 | 37.000 | 0.000 |
| 17 | d1_no | taker | 38 | 0.940 | 6.000 | 29.990 | 0.050 | 6.917 | 37.000 | 37.000 | 0.000 |
| 17 | d2_no | maker | 39 | 0.990 | 1.000 | 129.930 | 0.008 | 6.917 | 37.000 | 37.000 | 0.000 |

## Failure cases
| target_date | city | decision_hour_local | leg | execution_mode | bracket | entry_price | residual_points | final_winning_bracket | pnl_per_share |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-30 | NYC | 16 | d1_no | taker | 90-91 | 0.990 | 1.000 | 90-91 | -0.990 |
| 2026-06-12 | Munich | 16 | d2_no | taker | 17 | 0.990 | 1.000 | 17 | -0.990 |
| 2026-06-12 | SaoPaulo | 16 | d1_no | taker | 21 | 0.990 | 1.000 | 21 | -0.990 |
| 2026-06-12 | SaoPaulo | 16 | current_yes | taker | 20 | 0.990 | 1.000 | 21 | -0.990 |
| 2026-07-01 | Guangzhou | 16 | d2_no | taker | 34 | 0.990 | 1.000 | 34 | -0.990 |
| 2026-06-30 | Shanghai | 16 | d1_no | taker | 28 | 0.985 | 1.500 | 28 | -0.986 |
| 2026-06-30 | Shanghai | 16 | current_yes | taker | 27 | 0.985 | 1.500 | 28 | -0.986 |
| 2026-07-01 | Guangzhou | 17 | current_yes | taker | 32 | 0.980 | 2.000 | 34 | -0.981 |
| 2026-06-30 | NYC | 16 | current_yes | taker | 88-89 | 0.979 | 2.100 | 90-91 | -0.980 |
| 2026-06-12 | SaoPaulo | 16 | current_yes | maker | 20 | 0.980 | 2.000 | 21 | -0.980 |
| 2026-06-12 | SaoPaulo | 16 | d1_no | maker | 21 | 0.971 | 2.900 | 21 | -0.971 |
| 2026-06-30 | Shanghai | 16 | d1_no | maker | 28 | 0.970 | 3.000 | 28 | -0.970 |
| 2026-06-12 | Munich | 16 | d2_no | maker | 17 | 0.970 | 3.000 | 17 | -0.970 |
| 2026-07-01 | Guangzhou | 16 | d2_no | maker | 34 | 0.970 | 3.000 | 34 | -0.970 |
| 2026-06-07 | Dallas | 16 | d1_no | taker | 92-93 | 0.968 | 3.200 | 92-93 | -0.970 |
| 2026-05-23 | Atlanta | 17 | d1_no | taker | 84-85 | 0.968 | 3.200 | 84-85 | -0.970 |
| 2026-06-30 | Chongqing | 16 | d1_no | taker | 27 | 0.960 | 4.000 | 27 | -0.962 |
| 2026-06-30 | Chongqing | 16 | current_yes | taker | 26 | 0.960 | 4.000 | 27 | -0.962 |
| 2026-06-30 | NYC | 15 | d1_no | taker | 90-91 | 0.959 | 4.100 | 90-91 | -0.961 |
| 2026-06-30 | Shanghai | 15 | d1_no | taker | 28 | 0.958 | 4.200 | 28 | -0.960 |
| 2026-06-30 | Shanghai | 16 | current_yes | maker | 27 | 0.960 | 4.000 | 28 | -0.960 |
| 2026-06-30 | NYC | 16 | d1_no | maker | 90-91 | 0.960 | 4.000 | 90-91 | -0.960 |
| 2026-06-07 | Dallas | 16 | d1_no | maker | 92-93 | 0.958 | 4.200 | 92-93 | -0.958 |
| 2026-05-23 | Atlanta | 17 | current_yes | taker | 82-83 | 0.953 | 4.700 | 84-85 | -0.955 |
| 2026-06-30 | NYC | 16 | current_yes | maker | 88-89 | 0.952 | 4.800 | 90-91 | -0.952 |

## 8环覆盖
- 描述性绩效/统计推断/执行微结构/容量/组合相关性/基准: covered at research replay level.
- 信号判别/概率分布评估: not covered; this v1 is model-free residual replay.
- 前瞻门: checked on 2026-06-29..2026-07-04 but sample remains thin.

## Gate verdict
在 2026-05-19..2026-07-04，late-window strict residual 1-5 point replay 相对 market-implied zero EV 的 fee-after ROI 未同时通过显著性、可执行基准和 forward 三门；significance=FAIL/NA, baseline=FAIL, forward=FAIL/NA, conclusion=inconclusive。
