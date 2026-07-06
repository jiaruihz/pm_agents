# Late-window residual capture for exact temperature brackets v1

## 数据快照
- sync/rebuild: `scripts/ops/sync_weather_remote.sh && scripts/weather_dashboard/run_stack.sh on 2026-07-06 17:47-17:51 Asia/Shanghai`
- DB fact built: `2026-07-06T09:57:36.505304+00:00`; CLOB gate: `True`
- settlement_outcomes: `2026-05-04..2026-07-05`
- observed shards: `2026-05-19..2026-07-04`; rows `6709`
- orderbook matched snapshot-level leg quote rows: `32028`; strict 1-5 point rows with depth: `12676`

## 结论
Snapshot-level replay fixes the top-hour miss: Chengdu 39 NO did show a tradable 95-96c residual window around 15:28-15:45 BJ on 2026-07-06. Systematically, strict 1-5 point residual capture is still not live-ready as a taker strategy: current YES, d1 NO, and d2 NO are negative fee-after in both full sample and forward; d3 NO is positive but thin and highly autocorrelated across repeated snapshots. Maker rows look positive across legs, but that is queue/fill-probability evidence, not realized execution.

## Strict 1-5 point residual legs
| execution_mode | leg | settled_rows | active_dates | avg_entry_price | hit_rate | roi | daily_pnl_ci_low | daily_pnl_ci_high | top_size_sum |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| maker | current_yes | 2308 | 46 | 0.977 | 0.990 | 0.013 | 16.942 | 41.973 | 328117.330 |
| maker | d1_no | 2134 | 46 | 0.977 | 0.987 | 0.011 | 7.636 | 36.590 | 178537.250 |
| maker | d2_no | 1964 | 47 | 0.979 | 0.993 | 0.015 | 15.455 | 38.458 | 157885.720 |
| maker | d3_no | 803 | 46 | 0.981 | 0.996 | 0.016 | 6.645 | 16.765 | 60394.870 |
| taker | current_yes | 2031 | 46 | 0.980 | 0.977 | -0.003 | -28.008 | 12.340 | 275851.550 |
| taker | d1_no | 2086 | 46 | 0.979 | 0.972 | -0.009 | -40.683 | 4.468 | 241554.250 |
| taker | d2_no | 995 | 47 | 0.979 | 0.967 | -0.014 | -30.243 | 2.066 | 254844.160 |
| taker | d3_no | 348 | 46 | 0.982 | 0.989 | 0.006 | -4.139 | 6.473 | 177863.960 |

## Forward 2026-06-29..2026-07-04
| execution_mode | leg | settled_rows | active_dates | avg_entry_price | hit_rate | roi | daily_pnl_ci_low | daily_pnl_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| maker | current_yes | 196 | 6 | 0.974 | 0.990 | 0.016 | -0.148 | 5.464 |
| maker | d1_no | 174 | 6 | 0.976 | 0.983 | 0.007 | -4.027 | 4.596 |
| maker | d2_no | 147 | 6 | 0.980 | 0.986 | 0.007 | -2.890 | 3.389 |
| maker | d3_no | 48 | 6 | 0.981 | 1.000 | 0.019 | 0.576 | 1.274 |
| taker | current_yes | 149 | 6 | 0.978 | 0.940 | -0.040 | -21.023 | 3.031 |
| taker | d1_no | 169 | 6 | 0.979 | 0.959 | -0.022 | -17.304 | 3.997 |
| taker | d2_no | 93 | 6 | 0.979 | 0.968 | -0.012 | -4.807 | 1.456 |
| taker | d3_no | 26 | 6 | 0.982 | 1.000 | 0.018 | 0.259 | 0.655 |

## Basket risk
| execution_mode | rows | settled_rows | active_dates | cost | pnl | roi | daily_pnl_ci_low | daily_pnl_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| maker | 4860 | 4860 | 47 | 7048.911 | 93.089 | 0.013 | 58.052 | 124.066 |
| taker | 3713 | 3713 | 47 | 5353.651 | -35.651 | -0.007 | -86.485 | 9.509 |

## Chengdu 2026-07-06 As-of Case
| snapshot_ts_utc | decision_hour_local | decision_minute_local | leg | execution_mode | bracket | entry_price | residual_points | top_size | spread | book_age_min | running_value | current_native | decline_native |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-06T09:10:02+00:00 | 17 | 10 | current_yes | maker | 37 | 0.981 | 1.900 | 480.200 | 0.008 | 10.033 | 37.000 | 36.000 | 1.000 |
| 2026-07-06T09:10:02+00:00 | 17 | 10 | current_yes | taker | 37 | 0.989 | 1.100 | 10.000 | 0.008 | 10.033 | 37.000 | 36.000 | 1.000 |
| 2026-07-06T09:10:02+00:00 | 17 | 10 | d1_no | maker | 38 | 0.971 | 2.900 | 143.580 | 0.018 | 10.033 | 37.000 | 36.000 | 1.000 |
| 2026-07-06T09:10:02+00:00 | 17 | 10 | d1_no | taker | 38 | 0.989 | 1.100 | 15.000 | 0.018 | 10.033 | 37.000 | 36.000 | 1.000 |
| 2026-07-06T09:27:12+00:00 | 17 | 27 | current_yes | maker | 37 | 0.961 | 3.900 | 6.840 | 0.002 | 27.200 | 37.000 | 36.000 | 1.000 |
| 2026-07-06T09:27:12+00:00 | 17 | 27 | current_yes | taker | 37 | 0.963 | 3.700 | 30.000 | 0.002 | 27.200 | 37.000 | 36.000 | 1.000 |
| 2026-07-06T09:27:12+00:00 | 17 | 27 | d1_no | maker | 38 | 0.947 | 5.300 | 13.150 | 0.013 | 27.200 | 37.000 | 36.000 | 1.000 |
| 2026-07-06T09:27:12+00:00 | 17 | 27 | d1_no | taker | 38 | 0.960 | 4.000 | 33.260 | 0.013 | 27.200 | 37.000 | 36.000 | 1.000 |
| 2026-07-06T09:44:01+00:00 | 17 | 44 | current_yes | maker | 37 | 0.924 | 7.600 | 30.000 | 0.072 | 44.017 | 37.000 | 36.000 | 1.000 |

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
| 2026-06-14 | Atlanta | 15 | d2_no | taker | 90-91 | 0.990 | 1.000 | 90-91 | -0.990 |
| 2026-06-06 | Munich | 16 | current_yes | taker | 23 | 0.990 | 1.000 | 24+ | -0.990 |
| 2026-06-12 | Munich | 15 | d3_no | taker | 17 | 0.990 | 1.000 | 17 | -0.990 |
| 2026-06-12 | Munich | 15 | d3_no | taker | 17 | 0.990 | 1.000 | 17 | -0.990 |
| 2026-06-12 | SaoPaulo | 15 | current_yes | taker | 20 | 0.990 | 1.000 | 21 | -0.990 |
| 2026-06-12 | SaoPaulo | 15 | d1_no | taker | 21 | 0.990 | 1.000 | 21 | -0.990 |
| 2026-06-12 | SaoPaulo | 15 | current_yes | taker | 20 | 0.990 | 1.000 | 21 | -0.990 |
| 2026-06-04 | Austin | 15 | d2_no | taker | 86-87 | 0.990 | 1.000 | 86-87 | -0.990 |
| 2026-06-12 | SaoPaulo | 15 | d1_no | taker | 21 | 0.990 | 1.000 | 21 | -0.990 |
| 2026-06-30 | NYC | 15 | d1_no | taker | 90-91 | 0.990 | 1.000 | 90-91 | -0.990 |
| 2026-05-22 | Ankara | 17 | d1_no | taker | 20 | 0.990 | 1.000 | 20 | -0.990 |
| 2026-05-22 | Ankara | 17 | current_yes | taker | 19 | 0.990 | 1.000 | 20 | -0.990 |
| 2026-07-01 | Guangzhou | 15 | d2_no | taker | 34 | 0.990 | 1.000 | 34 | -0.990 |
| 2026-06-13 | Wellington | 15 | d1_no | taker | 15 | 0.990 | 1.000 | 15 | -0.990 |
| 2026-06-04 | Wellington | 18 | d1_no | taker | 18 | 0.990 | 1.000 | 18 | -0.990 |
| 2026-06-13 | Wellington | 15 | d1_no | maker | 15 | 0.990 | 1.000 | 15 | -0.990 |
| 2026-06-13 | Wellington | 15 | current_yes | maker | 14 | 0.990 | 1.000 | 15 | -0.990 |
| 2026-06-12 | SaoPaulo | 17 | d1_no | maker | 21 | 0.990 | 1.000 | 21 | -0.990 |
| 2026-06-13 | Wellington | 15 | current_yes | maker | 14 | 0.990 | 1.000 | 15 | -0.990 |
| 2026-06-04 | Wellington | 15 | d2_no | maker | 18 | 0.990 | 1.000 | 18 | -0.990 |
| 2026-06-15 | Dallas | 16 | d2_no | taker | 86-87 | 0.989 | 1.100 | 86-87 | -0.990 |
| 2026-06-30 | Seattle | 17 | d1_no | taker | 64-65 | 0.989 | 1.100 | 64-65 | -0.990 |
| 2026-06-25 | Wuhan | 16 | d2_no | taker | 31 | 0.989 | 1.100 | 31 | -0.990 |
| 2026-06-25 | Wuhan | 15 | d2_no | taker | 31 | 0.989 | 1.100 | 31 | -0.990 |
| 2026-06-17 | Busan | 15 | d1_no | taker | 30 | 0.989 | 1.100 | 30 | -0.990 |
| 2026-06-16 | Denver | 15 | d2_no | taker | 94-95 | 0.989 | 1.100 | 94-95 | -0.990 |
| 2026-06-15 | Dallas | 16 | d2_no | taker | 86-87 | 0.989 | 1.100 | 86-87 | -0.990 |
| 2026-06-15 | Miami | 15 | current_yes | taker | 92-93 | 0.989 | 1.100 | 94-95 | -0.990 |
| 2026-06-14 | Atlanta | 15 | d2_no | taker | 90-91 | 0.989 | 1.100 | 90-91 | -0.990 |
| 2026-06-04 | Wellington | 18 | current_yes | taker | 17 | 0.989 | 1.100 | 18 | -0.990 |

## 8环覆盖
- 描述性绩效/统计推断/执行微结构/容量/组合相关性/基准: covered at research replay level.
- 信号判别/概率分布评估: not covered; this v1 is model-free residual replay.
- 前瞻门: checked on 2026-06-29..2026-07-04 but sample remains thin.

## Gate verdict
在 2026-05-19..2026-07-04，late-window strict residual 1-5 point replay 相对 market-implied zero EV 的 fee-after ROI 未同时通过显著性、可执行基准和 forward 三门；significance=FAIL/NA, baseline=FAIL, forward=FAIL/NA, conclusion=inconclusive。
