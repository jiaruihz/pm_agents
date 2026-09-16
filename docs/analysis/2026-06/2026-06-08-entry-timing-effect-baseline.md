# Entry Timing Effect Baseline - 2026-06-08

## 数据快照

- 数据源: `runtime/weather.db` (`/home/rui/projects/pm_agent/runtime/weather.db`)
- DB mtime UTC: `2026-06-07T17:03:02.974936+00:00`
- fact_built_at_utc: `2026-06-07T17:02:43.560681+00:00`
- CLOB coverage gate: `gate_pass=True`, live_real fill_ids=1320, db_fill_cost_minus_fact_cost=0.0
- run_stack note: sync_weather_remote.sh succeeded; run_stack.sh rebuilt DB/facts but API start returned non-zero because port 8000 was already in use.

### 强制 SQL 自检

trade_class:
| trade_class | n |
| --- | --- |
| live_real | 1320 |
| live_simulated | 1093 |
| paper | 2285 |
| snapshot_replay | 636 |

settlement_status:
| settlement_status | n |
| --- | --- |
|  | 301 |
| settled | 5033 |

fact_signal_candidates:
| rows | eligible | paper_ordered | live_filled |
| --- | --- | --- | --- |
| 23487 | 7642 | 2835 | 513 |

orders x fills:
| status | orders | with_fill |
| --- | --- | --- |
| error | 151 | 0 |
| submitted | 1546 | 1320 |

## 结论摘要

当前 DB 支持的结论先限定在 timing 基线，不发布 forecast 更新卡点收益结论。
- `<T-18`: L1 unavailable outside current decision-window fact table, L3 live_real roi=1.1306 (fills=20), L4 city-day roi=1.1306 (city_days=9).
- `T-18-20`: L1 unavailable outside current decision-window fact table, L3 live_real roi=-0.0636 (fills=21), L4 city-day roi=-0.0636 (city_days=13).
- `T-20-22`: L1 unavailable outside current decision-window fact table, L3 live_real roi=-0.2232 (fills=55), L4 city-day roi=-0.2232 (city_days=25).
- `T-22-24`: L1 cf_roi_proxy=0.0863, L3 live_real roi=0.2922 (fills=172), L4 city-day roi=0.2922 (city_days=63).
- `T-24-26`: L1 unavailable outside current decision-window fact table, L3 live_real roi=0.0477 (fills=289), L4 city-day roi=0.0477 (city_days=134).
- `T-26-28`: L1 unavailable outside current decision-window fact table, L3 live_real roi=-0.306 (fills=66), L4 city-day roi=-0.306 (city_days=27).
- `>T-28`: L1 unavailable outside current decision-window fact table, L3 live_real roi=-0.1117 (fills=579), L4 city-day roi=-0.1117 (city_days=243).
`T-24-26` 不能直接并入主窗口：本轮可见 live_real/city-day 基线需要和 forecast checkpoint 再拆一次。
`fact_signal_candidates` 当前只给 T-22~24 决策窗，不能用它证明 `<T-22` 或 `T-24-26` 的机会层 alpha；这些窗口先以 live fill/city-day 结果作为风险基线。
raw paper snapshots 已有 estimated model init/run age metadata；forecast checkpoint 字段当前不在 fact/signals/plans 中，下一步是把这些原料接入派生层，不能用理论 6h cadence 硬贴标签。

## L0 Opportunity

注意：`signals` 是 snapshot signal row 粒度，只能看覆盖和 edge 分布，不是结算反事实 PnL。

| timing_bin | signal_rows | avg_abs_edge | avg_market_price |
| --- | --- | --- | --- |
| <T-18 | 97 | 0.2813 | 0.5087 |
| T-18-20 | 239 | 0.2556 | 0.5208 |
| T-20-22 | 303 | 0.2593 | 0.5240 |
| T-22-24 | 494 | 0.1970 | 0.3644 |
| T-24-26 | 1931 | 0.2156 | 0.3301 |
| T-26-28 | 468 | 0.2216 | 0.5708 |
| >T-28 | 1786 | 0.2124 | 0.5970 |

## L0 Decision-Window Candidates

注意：当前 `fact_signal_candidates` 只覆盖决策窗，实际只有 `T-22-24`；其它 timing bin 的机会层 alpha 是数据缺口，不应填 `0`。

| timing_bin | opportunities | eligible | eligible_rate | paper_ordered | live_filled | live_fill_rate | avg_abs_edge | avg_decision_entry_price |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T-22-24 | 2213 | 854 | 0.3859 | 1771 | 416 | 0.1880 | 0.1639 | 0.5043 |

## L1 Eligible Opportunity

| timing_bin | eligible_opportunities | cf_cost_proxy | cf_pnl | cf_roi_proxy | cf_win_rate | live_filled | live_fill_rate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T-22-24 | 854 | 437.2280 | 37.7200 | 0.0863 | 0.5164 | 410 | 0.4801 |

## L2 Submitted Orders

| timing_bin | submitted_orders | submitted_notional_usd | posted_cost_usd | filled_executions | execution_fill_rate | status_counts |
| --- | --- | --- | --- | --- | --- | --- |
| <T-18 | 27 | 102.6000 | 59.9257 | 11 | 0.4074 | {'submitted': 15, 'error': 12} |
| T-18-20 | 26 | 108.8000 | 94.0271 | 16 | 0.6154 | {'submitted': 22, 'error': 4} |
| T-20-22 | 61 | 254.2000 | 192.4454 | 38 | 0.6230 | {'submitted': 45, 'error': 16} |
| T-22-24 | 168 | 720.4000 | 588.0913 | 99 | 0.5893 | {'submitted': 131, 'error': 37} |
| T-24-26 | 290 | 1396.7000 | 1210.6805 | 191 | 0.6586 | {'submitted': 255, 'error': 35} |
| T-26-28 | 59 | 264.0000 | 223.9956 | 37 | 0.6271 | {'submitted': 51, 'error': 8} |
| >T-28 | 543 | 2494.1000 | 2303.4616 | 405 | 0.7459 | {'submitted': 504, 'error': 39} |

## L3 Live Real Settled

| timing_bin | fills | city_days | cost_usd | pnl_usd | roi | win_rate | avg_fill_price |
| --- | --- | --- | --- | --- | --- | --- | --- |
| <T-18 | 20 | 9 | 45.4838 | 51.4226 | 1.1306 | 0.8000 | 0.3403 |
| T-18-20 | 21 | 13 | 60.9018 | -3.8712 | -0.0636 | 0.5238 | 0.4890 |
| T-20-22 | 55 | 25 | 137.9389 | -30.7856 | -0.2232 | 0.4364 | 0.4173 |
| T-22-24 | 172 | 63 | 391.7694 | 114.4555 | 0.2922 | 0.5058 | 0.4316 |
| T-24-26 | 289 | 134 | 824.6578 | 39.3372 | 0.0477 | 0.5433 | 0.5089 |
| T-26-28 | 66 | 27 | 140.5101 | -43.0004 | -0.3060 | 0.2424 | 0.3977 |
| >T-28 | 579 | 243 | 1605.9656 | -179.3752 | -0.1117 | 0.5043 | 0.5455 |

## L4 Live Real City-Day Portfolio

| timing_bin | city_days | cost_usd | pnl_usd | roi | avg_city_day_pnl | median_city_day_pnl | negative_city_day_rate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| <T-18 | 9 | 45.4838 | 51.4226 | 1.1306 | 5.7136 | 4.8375 | 0.3333 |
| T-18-20 | 13 | 60.9018 | -3.8712 | -0.0636 | -0.2978 | 1.7446 | 0.4615 |
| T-20-22 | 25 | 137.9388 | -30.7856 | -0.2232 | -1.2314 | -4.7089 | 0.6000 |
| T-22-24 | 63 | 391.7694 | 114.4555 | 0.2922 | 1.8168 | 1.9432 | 0.4444 |
| T-24-26 | 134 | 824.6578 | 39.3372 | 0.0477 | 0.2936 | 1.4826 | 0.4701 |
| T-26-28 | 27 | 140.5101 | -43.0004 | -0.3060 | -1.5926 | -4.3911 | 0.5556 |
| >T-28 | 243 | 1605.9656 | -179.3752 | -0.1117 | -0.7382 | -1.8548 | 0.5638 |

## Period Split - Live Real

| period | timing_bin | fills | cost_usd | pnl_usd | roi | win_rate |
| --- | --- | --- | --- | --- | --- | --- |
| holdout_2026_05_26_to_05_31 | <T-18 | 16 | 31.5826 | 65.3239 | 2.0684 | 1.0000 |
| holdout_2026_05_26_to_05_31 | T-18-20 | 10 | 28.3407 | -11.7807 | -0.4157 | 0.4000 |
| holdout_2026_05_26_to_05_31 | T-20-22 | 10 | 27.8962 | 2.2738 | 0.0815 | 0.6000 |
| holdout_2026_05_26_to_05_31 | T-22-24 | 55 | 140.5561 | -4.3141 | -0.0307 | 0.3818 |
| holdout_2026_05_26_to_05_31 | T-24-26 | 103 | 312.9833 | -39.7818 | -0.1271 | 0.5146 |
| holdout_2026_05_26_to_05_31 | T-26-28 | 10 | 27.9845 | 2.0155 | 0.0720 | 0.3000 |
| holdout_2026_05_26_to_05_31 | >T-28 | 282 | 775.5487 | -90.4763 | -0.1167 | 0.5213 |
| post_2026_06_01 | <T-18 | 4 | 13.9013 | -13.9013 | -1.0000 | 0.0000 |
| post_2026_06_01 | T-18-20 | 9 | 22.8681 | 1.9724 | 0.0863 | 0.5556 |
| post_2026_06_01 | T-20-22 | 33 | 81.6783 | -47.4521 | -0.5810 | 0.2424 |
| post_2026_06_01 | T-22-24 | 94 | 196.8970 | 117.8726 | 0.5987 | 0.5745 |
| post_2026_06_01 | T-24-26 | 66 | 161.6740 | -26.7313 | -0.1653 | 0.3485 |
| post_2026_06_01 | T-26-28 | 56 | 112.5255 | -45.0159 | -0.4001 | 0.2321 |
| post_2026_06_01 | >T-28 | 291 | 811.0805 | -94.7024 | -0.1168 | 0.4845 |
| pre_2026_05_26 | T-18-20 | 2 | 9.6930 | 5.9370 | 0.6125 | 1.0000 |
| pre_2026_05_26 | T-20-22 | 12 | 28.3643 | 14.3927 | 0.5074 | 0.8333 |
| pre_2026_05_26 | T-22-24 | 23 | 54.3162 | 0.8971 | 0.0165 | 0.5217 |
| pre_2026_05_26 | T-24-26 | 120 | 350.0006 | 105.8503 | 0.3024 | 0.6750 |
| pre_2026_05_26 | >T-28 | 6 | 19.3365 | 5.8035 | 0.3001 | 0.6667 |

## Strategy Split - Live Real

| strategy_id | execution_policy | timing_bin | city_days | fills | cost_usd | pnl_usd | roi | win_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| live_weather_edge_v1_c13ccf0c3181 | maker_queue_v1 | T-20-22 | 3 | 7 | 23.3562 | 0.7638 | 0.0327 | 0.5714 |
| live_weather_edge_v1_c13ccf0c3181 | maker_queue_v1 | T-22-24 | 4 | 6 | 13.8476 | -5.8676 | -0.4237 | 0.5000 |
| live_weather_edge_v1_c13ccf0c3181 | maker_queue_v1 | T-24-26 | 13 | 22 | 58.6841 | -5.9152 | -0.1008 | 0.5000 |
| live_weather_edge_v1_c13ccf0c3181 | maker_queue_v1 | >T-28 | 18 | 41 | 117.4673 | -17.3144 | -0.1474 | 0.5610 |
| live_weather_edge_v1_edb2b6f8df82 | maker_queue_v2 | <T-18 | 2 | 7 | 11.6160 | 34.1081 | 2.9363 | 1.0000 |
| live_weather_edge_v1_edb2b6f8df82 | maker_queue_v2 | T-18-20 | 2 | 4 | 13.4386 | -13.4386 | -1.0000 | 0.0000 |
| live_weather_edge_v1_edb2b6f8df82 | maker_queue_v2 | T-20-22 | 1 | 1 | 4.2174 | 3.5926 | 0.8519 | 1.0000 |
| live_weather_edge_v1_edb2b6f8df82 | maker_queue_v2 | T-22-24 | 2 | 2 | 5.7522 | 8.2478 | 1.4338 | 1.0000 |
| live_weather_edge_v1_edb2b6f8df82 | maker_queue_v2 | T-24-26 | 8 | 12 | 41.4742 | -10.5442 | -0.2542 | 0.4167 |
| live_weather_edge_v1_edb2b6f8df82 | maker_queue_v2 | >T-28 | 19 | 33 | 111.7478 | 1.0222 | 0.0091 | 0.4848 |
| live_weather_edge_v1_4b07f7abc42f | mid_price_core_v1 | T-18-20 | 1 | 1 | 4.9980 | 3.3320 | 0.6667 | 1.0000 |
| live_weather_edge_v1_4b07f7abc42f | mid_price_core_v1 | T-22-24 | 4 | 10 | 20.4794 | 2.1906 | 0.1070 | 0.5000 |
| live_weather_edge_v1_4b07f7abc42f | mid_price_core_v1 | T-24-26 | 6 | 15 | 32.3268 | -24.2705 | -0.7508 | 0.1333 |
| live_weather_edge_v1_4b07f7abc42f | mid_price_core_v1 | T-26-28 | 1 | 2 | 4.9920 | 3.0596 | 0.6129 | 1.0000 |
| live_weather_edge_v1_4b07f7abc42f | mid_price_core_v1 | >T-28 | 19 | 34 | 111.7433 | 10.3868 | 0.0930 | 0.6765 |
| live_weather_edge_v1_4ef9b3ec3e2e | mid_price_core_v1 | <T-18 | 3 | 7 | 14.9824 | 10.4200 | 0.6955 | 0.8571 |
| live_weather_edge_v1_4ef9b3ec3e2e | mid_price_core_v1 | T-18-20 | 6 | 10 | 29.6781 | 9.6024 | 0.3236 | 0.7000 |
| live_weather_edge_v1_4ef9b3ec3e2e | mid_price_core_v1 | T-20-22 | 10 | 23 | 56.7585 | -16.3415 | -0.2879 | 0.4783 |
| live_weather_edge_v1_4ef9b3ec3e2e | mid_price_core_v1 | T-22-24 | 37 | 100 | 244.4472 | 73.2035 | 0.2995 | 0.5500 |
| live_weather_edge_v1_4ef9b3ec3e2e | mid_price_core_v1 | T-24-26 | 89 | 208 | 600.9540 | 79.9324 | 0.1330 | 0.6010 |
| live_weather_edge_v1_4ef9b3ec3e2e | mid_price_core_v1 | T-26-28 | 17 | 41 | 93.0318 | -19.8337 | -0.2132 | 0.2927 |
| live_weather_edge_v1_4ef9b3ec3e2e | mid_price_core_v1 | >T-28 | 144 | 385 | 1053.0940 | -129.4675 | -0.1229 | 0.4987 |
| live_weather_edge_v1_91f019941593 | mid_price_core_v1 | <T-18 | 1 | 2 | 4.9998 | -4.9998 | -1.0000 | 0.0000 |
| live_weather_edge_v1_91f019941593 | mid_price_core_v1 | T-20-22 | 5 | 12 | 28.0981 | -13.9019 | -0.4948 | 0.3333 |
| live_weather_edge_v1_91f019941593 | mid_price_core_v1 | T-22-24 | 4 | 20 | 36.6282 | 37.8298 | 1.0328 | 0.5500 |
| live_weather_edge_v1_91f019941593 | mid_price_core_v1 | T-24-26 | 3 | 7 | 17.9525 | 20.0908 | 1.1191 | 0.7143 |
| live_weather_edge_v1_91f019941593 | mid_price_core_v1 | T-26-28 | 4 | 13 | 19.4927 | -11.5627 | -0.5932 | 0.0769 |
| live_weather_edge_v1_986d901ccc58 | mid_price_core_v2 | <T-18 | 3 | 4 | 13.8857 | 11.8943 | 0.8566 | 0.7500 |
| live_weather_edge_v1_986d901ccc58 | mid_price_core_v2 | T-18-20 | 4 | 6 | 12.7871 | -3.3671 | -0.2633 | 0.5000 |
| live_weather_edge_v1_986d901ccc58 | mid_price_core_v2 | T-20-22 | 6 | 12 | 25.5087 | -4.8987 | -0.1920 | 0.3333 |
| live_weather_edge_v1_986d901ccc58 | mid_price_core_v2 | T-22-24 | 12 | 34 | 70.6147 | -1.1486 | -0.0163 | 0.3235 |
| live_weather_edge_v1_986d901ccc58 | mid_price_core_v2 | T-24-26 | 15 | 25 | 73.2662 | -19.9562 | -0.2724 | 0.3600 |
| live_weather_edge_v1_986d901ccc58 | mid_price_core_v2 | T-26-28 | 5 | 10 | 22.9936 | -14.6636 | -0.6377 | 0.1000 |
| live_weather_edge_v1_986d901ccc58 | mid_price_core_v2 | >T-28 | 43 | 86 | 211.9133 | -44.0023 | -0.2076 | 0.4419 |

## Side x Model Split - Live Real

| timing_bin | side | model_version | fills | cost_usd | pnl_usd | roi | win_rate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| <T-18 | BUY_NO | gfs | 5 | 13.7397 | 15.5827 | 1.1341 | 1.0000 |
| <T-18 | BUY_YES | gfs | 15 | 31.7442 | 35.8399 | 1.1290 | 0.7333 |
| T-18-20 | BUY_NO | gfs | 13 | 44.6861 | 2.9244 | 0.0654 | 0.6154 |
| T-18-20 | BUY_YES | gfs | 8 | 16.2157 | -6.7957 | -0.4191 | 0.3750 |
| T-20-22 | BUY_NO | gfs | 24 | 77.1001 | -27.2301 | -0.3532 | 0.4583 |
| T-20-22 | BUY_YES | gfs | 31 | 60.8388 | -3.5556 | -0.0584 | 0.4194 |
| T-22-24 | BUY_NO | ecmwf | 12 | 42.9340 | -0.1840 | -0.0043 | 0.5000 |
| T-22-24 | BUY_NO | gfs | 63 | 162.8391 | 22.7045 | 0.1394 | 0.6349 |
| T-22-24 | BUY_YES | ecmwf | 17 | 33.4524 | -4.0190 | -0.1201 | 0.2941 |
| T-22-24 | BUY_YES | gfs | 80 | 152.5439 | 95.9542 | 0.6290 | 0.4500 |
| T-24-26 | BUY_NO | ecmwf | 54 | 188.3248 | 5.6852 | 0.0302 | 0.6111 |
| T-24-26 | BUY_NO | gfs | 142 | 437.2456 | 32.7632 | 0.0749 | 0.6620 |
| T-24-26 | BUY_YES | ecmwf | 15 | 33.5992 | 65.3659 | 1.9455 | 0.9333 |
| T-24-26 | BUY_YES | gfs | 78 | 165.4882 | -64.4771 | -0.3896 | 0.2051 |
| T-26-28 | BUY_NO | ecmwf | 21 | 65.5096 | -1.3299 | -0.0203 | 0.6190 |
| T-26-28 | BUY_YES | ecmwf | 42 | 70.0021 | -36.6721 | -0.5239 | 0.0714 |
| T-26-28 | BUY_YES | gfs | 3 | 4.9984 | -4.9984 | -1.0000 | 0.0000 |
| >T-28 | BUY_NO | ecmwf | 284 | 843.5338 | -57.3428 | -0.0680 | 0.5986 |
| >T-28 | BUY_NO | gfs | 145 | 436.5785 | -16.4255 | -0.0376 | 0.6138 |
| >T-28 | BUY_YES | ecmwf | 128 | 269.7247 | -94.0982 | -0.3489 | 0.2188 |
| >T-28 | BUY_YES | gfs | 22 | 56.1287 | -11.5087 | -0.2050 | 0.2273 |

## Settlement Status by Timing - Live Real

| timing_bin | settlement_status | fills |
| --- | --- | --- |
| <T-18 | settled | 20 |
| T-18-20 | settled | 21 |
| T-18-20 |  | 2 |
| T-20-22 | settled | 55 |
| T-20-22 |  | 6 |
| T-22-24 | settled | 172 |
| T-22-24 |  | 6 |
| T-24-26 | settled | 289 |
| T-24-26 |  | 20 |
| T-26-28 |  | 6 |
| T-26-28 | settled | 66 |
| >T-28 | settled | 579 |
| >T-28 |  | 78 |

## Worst City-Days

| target_date | city | execution_policy | timing_bin | fills | cost_usd | pnl_usd | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-31 | Istanbul | mid_price_core_v2 | >T-28 | 4 | 12.6014 | -12.6014 | -1.0000 |
| 2026-06-01 | NYC | mid_price_core_v2 | T-22-24 | 6 | 10.1278 | -10.1278 | -1.0000 |
| 2026-05-25 | Austin | mid_price_core_v1 | T-24-26 | 4 | 9.9980 | -9.9980 | -1.0000 |
| 2026-06-04 | NYC | mid_price_core_v1 | T-24-26 | 6 | 9.9980 | -9.9980 | -1.0000 |
| 2026-06-05 | Karachi | mid_price_core_v1 | >T-28 | 3 | 9.9975 | -9.9975 | -1.0000 |
| 2026-06-04 | London | mid_price_core_v1 | >T-28 | 2 | 9.9971 | -9.9971 | -1.0000 |
| 2026-06-05 | Jeddah | mid_price_core_v1 | >T-28 | 4 | 9.9963 | -9.9963 | -1.0000 |
| 2026-06-06 | Miami | mid_price_core_v1 | T-20-22 | 5 | 9.9963 | -9.9963 | -1.0000 |
| 2026-06-05 | Miami | mid_price_core_v1 | T-24-26 | 3 | 9.9962 | -9.9962 | -1.0000 |
| 2026-05-31 | Istanbul | mid_price_core_v1 | >T-28 | 4 | 9.9959 | -9.9959 | -1.0000 |
| 2026-05-30 | NYC | mid_price_core_v1 | T-24-26 | 5 | 9.9957 | -9.9957 | -1.0000 |
| 2026-05-30 | Munich | mid_price_core_v1 | >T-28 | 2 | 9.9956 | -9.9956 | -1.0000 |

## Forecast Checkpoint Data Gap

### Raw Snapshot Metadata Probe

- snapshot_dir: `/home/rui/projects/pm_agent/runtime/weather_edge_v1/market_data/paper_snapshots`
- sampled files: 120 / 1593
- sampled records: 78094
- coverage: `{'model_init_utc_estimated': 1.0, 'model_run_age_hours_estimated': 1.0, 'forecast_source': 1.0, 'settle_utc': 1.0}`

raw snapshot 原料里已有 estimated forecast run metadata，但当前 DB/fact 未派生这些字段；下一步应先接入 DB 再发布 checkpoint PnL。

| timing_bin | model | model_init_utc_estimated | records | avg_model_run_age_hours_estimated |
| --- | --- | --- | --- | --- |
| <T-18 | ecmwf | 00Z | 9379 | 9.1942 |
| <T-18 | ecmwf | 12Z | 3995 | 10.7367 |
| <T-18 | gfs | 00Z | 1613 | 6.6944 |
| <T-18 | gfs | 06Z | 2771 | 6.8060 |
| <T-18 | gfs | 12Z | 1729 | 6.7770 |
| <T-18 | gfs | 18Z | 1445 | 6.4592 |
| T-18-20 | ecmwf | 00Z | 547 | 8.1335 |
| T-18-20 | ecmwf | 12Z | 1495 | 10.6280 |
| T-18-20 | gfs | 00Z | 625 | 8.5352 |
| T-18-20 | gfs | 06Z | 358 | 5.4693 |
| T-18-20 | gfs | 12Z | 430 | 6.9395 |
| T-18-20 | gfs | 18Z | 130 | 7.8577 |
| T-20-22 | ecmwf | 00Z | 496 | 7.8589 |
| T-20-22 | ecmwf | 12Z | 1663 | 8.6340 |
| T-20-22 | gfs | 00Z | 902 | 7.1103 |
| T-20-22 | gfs | 06Z | 154 | 6.7630 |
| T-20-22 | gfs | 12Z | 476 | 5.0555 |
| T-20-22 | gfs | 18Z | 130 | 5.5808 |
| T-22-24 | ecmwf | 00Z | 1451 | 12.2888 |
| T-22-24 | ecmwf | 12Z | 1728 | 8.7132 |
| T-22-24 | gfs | 00Z | 951 | 5.4837 |
| T-22-24 | gfs | 06Z | 839 | 8.0453 |
| T-22-24 | gfs | 12Z | 256 | 6.2012 |
| T-22-24 | gfs | 18Z | 231 | 7.7078 |
| T-24-26 | ecmwf | 00Z | 1315 | 12.2734 |
| T-24-26 | ecmwf | 12Z | 1386 | 8.0937 |
| T-24-26 | gfs | 00Z | 327 | 6.0076 |
| T-24-26 | gfs | 06Z | 704 | 6.5732 |
| T-24-26 | gfs | 12Z | 147 | 7.2517 |
| T-24-26 | gfs | 18Z | 535 | 8.3561 |
| T-26-28 | ecmwf | 00Z | 1596 | 11.8020 |
| T-26-28 | ecmwf | 12Z | 1303 | 7.1612 |
| T-26-28 | gfs | 00Z | 186 | 7.5430 |
| T-26-28 | gfs | 06Z | 657 | 4.7100 |
| T-26-28 | gfs | 12Z | 171 | 5.1667 |
| T-26-28 | gfs | 18Z | 713 | 6.8289 |
| >T-28 | ecmwf | 00Z | 15761 | 9.2686 |
| >T-28 | ecmwf | 12Z | 4030 | 10.4018 |
| >T-28 | gfs | 00Z | 6216 | 6.9606 |
| >T-28 | gfs | 06Z | 4554 | 6.7456 |
| >T-28 | gfs | 12Z | 2746 | 6.4997 |
| >T-28 | gfs | 18Z | 1953 | 6.7875 |

### DB/Facts Missing Fields

| table | missing_fields | impact |
| --- | --- | --- |
| fact_trades | forecast_update_checkpoint, last_forecast_run_ts_utc, minutes_since_last_forecast_run, minutes_to_next_forecast_run, next_forecast_run_ts_utc | Cannot publish forecast-update checkpoint performance from DB facts yet. |
| fact_signal_candidates | forecast_update_checkpoint, last_forecast_run_ts_utc, minutes_since_last_forecast_run, minutes_to_next_forecast_run, next_forecast_run_ts_utc | Cannot publish forecast-update checkpoint performance from DB facts yet. |
| signals | forecast_update_checkpoint, last_forecast_run_ts_utc, minutes_since_last_forecast_run, minutes_to_next_forecast_run, next_forecast_run_ts_utc | Cannot publish forecast-update checkpoint performance from DB facts yet. |
| plans | forecast_update_checkpoint, last_forecast_run_ts_utc, minutes_since_last_forecast_run, minutes_to_next_forecast_run, next_forecast_run_ts_utc | Cannot publish forecast-update checkpoint performance from DB facts yet. |

## 下一步

1. 先把 `last_forecast_run_ts_utc` / `forecast_update_checkpoint` 从 snapshot 或 cache lineage 接入 fact 派生层，再做 `T-24-26 x pre/post update` 结论。
2. 对 `T-24-26`、`T-26-28`、`<T-22` 做 leave-date-out policy simulation；本报告只给当前 DB 基线，不宣称最优 cut。
3. 对亏损 city-day 追 `forecast_jump_after_entry_f` 和 `side_flip_after_entry`，否则不能区分 timing 坏和 forecast stale 坏。
