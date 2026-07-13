# Tmax Distribution v3 Model v1

> generated_at_utc: `2026-07-13T09:17:29+00:00` · model `tmax_distribution_v3` · primary route `market_path`
> Scope: offline replay + zero-notional shadow artifacts. No live restore, no live config change, no real order.

## 开头直接回答

- **新模型是什么**：full-ladder 竞争风险 hazard chain + 两个 constrained market recalibrator。global 仅使用 log(market five-bucket probs)，path 只增加预注册连续 residual，无 city；C/alpha 均只按过去训练窗 date-equal logloss 选择。
- **比当前 clean fusion**：clean fusion paired logloss `0.6043` vs market `0.4984`（2,777 rows）。本轮 primary `market_path` 在 `2777` paired rows 上 date-equal logloss `0.4241` vs market `0.4219`（row-mean `0.5029` vs `0.4984`）。
- **比旧 full-feature (+8.08%) / coherent_cal_quote (+11.19%)**：旧数字是混合血缘分母（非 saved-snapshot 可重建），不可同分母复算；本轮同 clean 分母的 first-lock ROI 见下执行表，直接对比只在 clean 分母内做。
- **改善来自哪层**：见 Ablation 表（date-bootstrap CI）；CI 跨零的层只保留 calibration/机制价值，不宣称 alpha 证实。
- **哪些只是假设**：weather_mechanism（湿度/云/风）为 archive 重建 upper bound，不入 live artifact；forecast curve 剩余加热特征仅 7/04+ 有覆盖；双源增量不可答（见重测数字）。
- **能否 shadow/live**：`shadow_candidate_no_live`。baseline gate `FAIL`，significance gate `FAIL`，forward gate `FAIL_fresh_forward_0`。唯一缺口：primary does not beat market on paired proper score; shadow forward evidence still required。**不恢复 live。**
- **Temporal coherence**：candidate anchor-shift conditioning 存在，但本轮 frozen coherence weight=0，未被 proper score 选择；shift=0 的 elapsed-time no-break survival decay 尚未实现。因此不能宣称已经解决跨小时一致性，这是 residual blocker。

## 重测的两个数字（任务书标注需重测）

- 双 GFS+ECMWF as-of 同刻覆盖率（本分母 states）：`2.25%`；非零日期：`{"2026-06-19": 0.9029, "2026-06-20": 0.1926}`。
- 双源 strict-asof lag<=60m 行数：`{"2026-06-19": 93, "2026-06-20": 89}`；6/21+ 为 0，结论 `not_answerable`，不产性能。
- archive meteo strict-asof lag<=60m coverage：`2086` / `8094`；lineage=`report_time_reconstruction_only_upper_bound`。
- atlas 直接 `city/date/hour` 连接的未来泄漏：`1231` / `2230` 行泄漏（占比 `55.2%`），正泄漏中位 `30.2` 分钟（p90 `43.8`）。因此本轮一律 as-of 连接。

## Paired Proper Score（primary denominator）

| route | rows | dates | logloss_date_equal | brier_date_equal | logloss_row_mean | brier_row_mean |
| --- | --- | --- | --- | --- | --- | --- |
| archive_upper_bound | 2777.0000 | 16.0000 | 0.4234 | 0.2257 | 0.4964 | 0.2630 |
| market | 2777.0000 | 16.0000 | 0.4219 | 0.2273 | 0.4984 | 0.2656 |
| market_path | 2777.0000 | 16.0000 | 0.4241 | 0.2265 | 0.5029 | 0.2656 |
| market_path_source | 2777.0000 | 16.0000 | 0.4251 | 0.2266 | 0.5043 | 0.2656 |
| market_recal_global | 2777.0000 | 16.0000 | 0.4413 | 0.2383 | 0.5320 | 0.2831 |
| market_recal_path | 2777.0000 | 16.0000 | 0.4678 | 0.2499 | 0.5488 | 0.2904 |
| strict_pit_full | 2777.0000 | 16.0000 | 0.4272 | 0.2271 | 0.5048 | 0.2658 |
| weather_only | 2777.0000 | 16.0000 | 0.8160 | 0.4035 | 0.9151 | 0.4607 |

所有 in-window 结果均为 `retrospective_diagnostic`；fresh-forward dates = 0。

## Ablation increments（date-equal logloss delta，负为更好）

| challenger | baseline | dates | delta_logloss_date_equal | delta_ci_low | delta_ci_high | direction_stable |
| --- | --- | --- | --- | --- | --- | --- |
| market_recal_global | market | 16 | 0.0193 | -0.0069 | 0.0462 | False |
| market_recal_path | market_recal_global | 16 | 0.0265 | 0.0011 | 0.0570 | False |
| market_recal_path | market | 16 | 0.0459 | 0.0177 | 0.0811 | False |
| weather_only | market | 16 | 0.3941 | 0.2964 | 0.5100 | False |
| market_path | market | 16 | 0.0022 | -0.0033 | 0.0109 | False |
| market_path_source | market_path | 16 | 0.0010 | -0.0012 | 0.0033 | False |
| strict_pit_full | market_path_source | 16 | 0.0021 | -0.0016 | 0.0063 | False |
| strict_pit_full | market | 16 | 0.0053 | -0.0029 | 0.0162 | False |
| archive_upper_bound | strict_pit_full | 16 | -0.0038 | -0.0147 | 0.0041 | False |
| archive_upper_bound | market | 16 | 0.0015 | -0.0038 | 0.0077 | False |

## Trailing-window robustness（primary，train=最近10天）

| route | rows | dates | logloss_date_equal | brier_date_equal | logloss_row_mean | brier_row_mean |
| --- | --- | --- | --- | --- | --- | --- |
| market | 2777.0000 | 16.0000 | 0.4219 | 0.2273 | 0.4984 | 0.2656 |
| market_path | 2777.0000 | 16.0000 | 0.4241 | 0.2265 | 0.5029 | 0.2656 |

## Execution first-lock（frozen policy，与旧口径可比）

| route | rows | dates | wins | cost | pnl | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| archive_upper_bound | 169 | 17 | 115.0000 | 570.1876 | 4.8124 | 0.0084 | -0.0996 | 0.0992 |
| market | 23 | 13 | 15.0000 | 68.0782 | 6.9218 | 0.1017 | -0.2279 | 0.3906 |
| market_path | 157 | 16 | 106.0000 | 539.4480 | -9.4480 | -0.0175 | -0.1223 | 0.0774 |
| market_path_source | 163 | 16 | 111.0000 | 552.3490 | 2.6510 | 0.0048 | -0.1060 | 0.1091 |
| market_recal_global | 241 | 14 | 160.0000 | 827.6162 | -27.6162 | -0.0334 | -0.1297 | 0.0697 |
| market_recal_path | 246 | 13 | 167.0000 | 881.9903 | -46.9903 | -0.0533 | -0.1412 | 0.0151 |
| strict_pit_full | 179 | 17 | 118.0000 | 597.9199 | -7.9199 | -0.0132 | -0.1249 | 0.0897 |
| weather_only | 286 | 17 | 174.0000 | 940.4990 | -70.4990 | -0.0750 | -0.1536 | 0.0079 |

### Side / Expression / Unit

| route | side | rows | dates | wins | cost | pnl | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| archive_upper_bound | NO | 143 | 16 | 99.0000 | 502.8989 | -7.8989 | -0.0157 | -0.1257 | 0.0780 |
| archive_upper_bound | YES | 26 | 9 | 16.0000 | 67.2887 | 12.7113 | 0.1889 | -0.4023 | 0.7217 |
| market | NO | 10 | 7 | 4.0000 | 26.9879 | -6.9879 | -0.2589 | -0.7628 | 0.3815 |
| market | YES | 13 | 10 | 11.0000 | 41.0903 | 13.9097 | 0.3385 | 0.0781 | 0.5409 |
| market_path | NO | 131 | 16 | 91.0000 | 468.8532 | -13.8532 | -0.0295 | -0.1471 | 0.0619 |
| market_path | YES | 26 | 8 | 15.0000 | 70.5948 | 4.4052 | 0.0624 | -0.4024 | 0.6184 |
| market_path_source | NO | 129 | 16 | 89.0000 | 461.1721 | -16.1721 | -0.0351 | -0.1541 | 0.0659 |
| market_path_source | YES | 34 | 8 | 22.0000 | 91.1769 | 18.8231 | 0.2064 | -0.3063 | 0.6809 |
| market_recal_global | NO | 235 | 14 | 157.0000 | 811.5578 | -26.5578 | -0.0327 | -0.1269 | 0.0729 |
| market_recal_global | YES | 6 | 4 | 3.0000 | 16.0583 | -1.0583 | -0.0659 | -1.0000 | 0.9023 |
| market_recal_path | NO | 233 | 13 | 164.0000 | 849.1514 | -29.1514 | -0.0343 | -0.1203 | 0.0340 |
| market_recal_path | YES | 13 | 6 | 3.0000 | 32.8389 | -17.8389 | -0.5432 | -0.8754 | -0.0679 |
| strict_pit_full | NO | 144 | 17 | 96.0000 | 504.4240 | -24.4240 | -0.0484 | -0.1641 | 0.0539 |
| strict_pit_full | YES | 35 | 8 | 22.0000 | 93.4959 | 16.5041 | 0.1765 | -0.3358 | 0.6177 |
| weather_only | NO | 283 | 17 | 174.0000 | 933.7799 | -63.7799 | -0.0683 | -0.1511 | 0.0203 |
| weather_only | YES | 3 | 2 | 0.0000 | 6.7192 | -6.7192 | -1.0000 | NA | NA |

| route | expression | rows | dates | wins | cost | pnl | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| archive_upper_bound | current_no | 12 | 9 | 7.0000 | 42.7178 | -7.7178 | -0.1807 | -0.4797 | 0.0675 |
| archive_upper_bound | d1_no | 50 | 13 | 31.0000 | 169.8718 | -14.8718 | -0.0875 | -0.3139 | 0.0767 |
| archive_upper_bound | d1_yes | 9 | 5 | 6.0000 | 26.2911 | 3.7089 | 0.1411 | -0.3920 | 0.6907 |
| archive_upper_bound | d2_no | 81 | 15 | 61.0000 | 290.3093 | 14.6907 | 0.0506 | -0.0635 | 0.1584 |
| archive_upper_bound | d2_yes | 17 | 6 | 10.0000 | 40.9976 | 9.0024 | 0.2196 | -0.5024 | 0.8553 |
| market | current_no | 3 | 3 | 0.0000 | 8.5891 | -8.5891 | -1.0000 | -1.0000 | -1.0000 |
| market | d1_no | 1 | 1 | 0.0000 | 2.3119 | -2.3119 | -1.0000 | NA | NA |
| market | d1_yes | 9 | 8 | 8.0000 | 29.8462 | 10.1538 | 0.3402 | 0.0124 | 0.6033 |
| market | d2_no | 6 | 5 | 4.0000 | 16.0869 | 3.9131 | 0.2433 | -0.2856 | 0.8450 |
| market | d2_yes | 4 | 4 | 3.0000 | 11.2442 | 3.7558 | 0.3340 | -0.5237 | 0.8356 |
| market_path | current_no | 5 | 5 | 1.0000 | 14.8912 | -9.8912 | -0.6642 | -1.0000 | -0.2004 |
| market_path | d1_no | 35 | 14 | 21.0000 | 121.7016 | -16.7016 | -0.1372 | -0.3641 | 0.0120 |
| market_path | d1_yes | 8 | 6 | 4.0000 | 23.0727 | -3.0727 | -0.1332 | -0.6552 | 0.6273 |
| market_path | d2_no | 91 | 16 | 69.0000 | 332.2604 | 12.7396 | 0.0383 | -0.0763 | 0.1391 |
| market_path | d2_yes | 18 | 5 | 11.0000 | 47.5221 | 7.4779 | 0.1574 | -0.3245 | 0.6902 |
| market_path_source | current_no | 9 | 6 | 4.0000 | 31.0614 | -11.0614 | -0.3561 | -0.6330 | -0.1192 |
| market_path_source | d1_no | 43 | 13 | 25.0000 | 147.4811 | -22.4811 | -0.1524 | -0.3720 | 0.0084 |
| market_path_source | d1_yes | 9 | 6 | 5.0000 | 26.4289 | -1.4288 | -0.0541 | -0.4841 | 0.6273 |
| market_path_source | d2_no | 77 | 16 | 60.0000 | 282.6297 | 17.3703 | 0.0615 | -0.0766 | 0.2004 |
| market_path_source | d2_yes | 25 | 6 | 17.0000 | 64.7480 | 20.2520 | 0.3128 | -0.2271 | 0.7586 |
| market_recal_global | current_no | 3 | 3 | 1.0000 | 6.8345 | -1.8345 | -0.2684 | -1.0000 | 1.3691 |
| market_recal_global | d1_no | 49 | 11 | 28.0000 | 150.6298 | -10.6298 | -0.0706 | -0.2194 | 0.1469 |
| market_recal_global | d1_yes | 6 | 4 | 3.0000 | 16.0583 | -1.0583 | -0.0659 | -1.0000 | 0.9023 |
| market_recal_global | d2_no | 183 | 12 | 128.0000 | 654.0935 | -14.0935 | -0.0215 | -0.1237 | 0.1009 |
| market_recal_path | current_no | 2 | 2 | 1.0000 | 5.7160 | -0.7160 | -0.1253 | NA | NA |
| market_recal_path | d1_no | 70 | 10 | 50.0000 | 239.8033 | 10.1967 | 0.0425 | -0.0597 | 0.1554 |
| market_recal_path | d1_yes | 7 | 4 | 2.0000 | 17.7191 | -7.7191 | -0.4356 | -1.0000 | 0.5709 |
| market_recal_path | d2_no | 161 | 12 | 113.0000 | 603.6321 | -38.6321 | -0.0640 | -0.1744 | 0.0389 |
| market_recal_path | d2_yes | 6 | 3 | 1.0000 | 15.1198 | -10.1198 | -0.6693 | -1.0000 | -0.3577 |
| strict_pit_full | current_no | 13 | 8 | 7.0000 | 46.3309 | -11.3309 | -0.2446 | -0.4662 | -0.0334 |
| strict_pit_full | d1_no | 48 | 14 | 27.0000 | 161.8442 | -26.8442 | -0.1659 | -0.3890 | 0.0036 |
| strict_pit_full | d1_yes | 10 | 6 | 5.0000 | 28.7479 | -3.7479 | -0.1304 | -0.5535 | 0.4222 |
| strict_pit_full | d2_no | 83 | 16 | 62.0000 | 296.2489 | 13.7511 | 0.0464 | -0.0841 | 0.1722 |
| strict_pit_full | d2_yes | 25 | 6 | 17.0000 | 64.7480 | 20.2520 | 0.3128 | -0.2271 | 0.7586 |
| weather_only | current_no | 17 | 12 | 6.0000 | 50.4563 | -20.4563 | -0.4054 | -0.7028 | -0.1035 |
| weather_only | d1_no | 103 | 16 | 62.0000 | 318.7717 | -8.7717 | -0.0275 | -0.1403 | 0.1073 |
| weather_only | d2_no | 163 | 16 | 106.0000 | 564.5518 | -34.5518 | -0.0612 | -0.1799 | 0.0573 |
| weather_only | d2_yes | 3 | 2 | 0.0000 | 6.7192 | -6.7192 | -1.0000 | NA | NA |

| route | unit | rows | dates | wins | cost | pnl | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| archive_upper_bound | C | 93 | 15 | 66.0000 | 314.7377 | 15.2623 | 0.0485 | -0.1416 | 0.1970 |
| archive_upper_bound | F | 76 | 15 | 49.0000 | 255.4499 | -10.4499 | -0.0409 | -0.1195 | 0.0518 |
| market | C | 11 | 9 | 7.0000 | 31.5476 | 3.4524 | 0.1094 | -0.4500 | 0.6553 |
| market | F | 12 | 9 | 8.0000 | 36.5306 | 3.4694 | 0.0950 | -0.2889 | 0.4473 |
| market_path | C | 86 | 15 | 63.0000 | 300.9538 | 14.0462 | 0.0467 | -0.0971 | 0.1653 |
| market_path | F | 71 | 15 | 43.0000 | 238.4942 | -23.4942 | -0.0985 | -0.2141 | 0.0338 |
| market_path_source | C | 96 | 16 | 69.0000 | 328.6718 | 16.3282 | 0.0497 | -0.0974 | 0.1797 |
| market_path_source | F | 67 | 15 | 42.0000 | 223.6772 | -13.6772 | -0.0611 | -0.1929 | 0.0851 |
| market_recal_global | C | 152 | 13 | 101.0000 | 531.1109 | -26.1109 | -0.0492 | -0.1625 | 0.0697 |
| market_recal_global | F | 89 | 11 | 59.0000 | 296.5053 | -1.5053 | -0.0051 | -0.1300 | 0.1273 |
| market_recal_path | C | 158 | 12 | 113.0000 | 579.9122 | -14.9122 | -0.0257 | -0.1278 | 0.0523 |
| market_recal_path | F | 88 | 10 | 54.0000 | 302.0781 | -32.0781 | -0.1062 | -0.2187 | -0.0023 |
| strict_pit_full | C | 102 | 17 | 70.0000 | 344.3371 | 5.6629 | 0.0164 | -0.1523 | 0.1517 |
| strict_pit_full | F | 77 | 15 | 48.0000 | 253.5828 | -13.5828 | -0.0536 | -0.1644 | 0.0641 |
| weather_only | C | 184 | 17 | 114.0000 | 632.0017 | -62.0017 | -0.0981 | -0.1849 | -0.0043 |
| weather_only | F | 102 | 15 | 60.0000 | 308.4973 | -8.4973 | -0.0275 | -0.1680 | 0.1096 |

### Primary route source/family 切片

| forecast_source | rows | dates | wins | cost | pnl | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| open_meteo_live_ecmwf | 68 | 13 | 50.0000 | 235.9423 | 14.0577 | 0.0596 | -0.1056 | 0.1938 |
| open_meteo_live_gfs | 89 | 16 | 56.0000 | 303.5057 | -23.5057 | -0.0774 | -0.1753 | 0.0286 |

| family | rows | dates | wins | cost | pnl | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| continental_dry_hot | 32 | 15 | 23.0000 | 113.8180 | 1.1820 | 0.0104 | -0.1711 | 0.1740 |
| europe_cloud_break | 14 | 11 | 9.0000 | 49.2769 | -4.2769 | -0.0868 | -0.4189 | 0.2515 |
| humid_low_latitude | 48 | 16 | 33.0000 | 161.7612 | 3.2388 | 0.0200 | -0.1380 | 0.1997 |
| southern_or_maritime | 32 | 14 | 18.0000 | 100.8631 | -10.8631 | -0.1077 | -0.3508 | 0.1079 |
| unknown | 31 | 10 | 23.0000 | 113.7288 | 1.2712 | 0.0112 | -0.3410 | 0.2233 |

### Stability：`{"route": "market_path", "roi": -0.01751420880683394, "top2_days_removed_roi": -0.07722717765888402, "top2_days_removed_ci": [-0.1698531263711108, -0.005344938665313765], "top2_days": ["2026-06-25", "2026-07-07"], "max_drawdown_usd": -32.67758, "trades": 157, "dates": 16}`

## Position-aware target-book（primary）

`{"net_pnl": 0.6013602500000275, "gross_cost": 704.39863975, "roi": 0.0008537214810827258, "actions": {"hold": 1321, "hold_missing_complement_book": 233, "open": 157, "payout": 141, "hold_missing_revalue": 73, "close_for_rebalance": 26, "reopen": 26, "close_lock": 11}, "positions": 183, "locked_positions": 37, "max_drawdown_usd": -27.887825999999997, "dates": 16}`

## Saved visible-ladder quote secondary audit（不等同完整 absolute event ladder）

`{"rows": 2777, "dates": 16, "market_exact_rung_logloss": 1.0946643083831613, "model_exact_rung_logloss": 1.438718074133393, "status": "saved_visible_ladder_only_not_proof_of_complete_absolute_event_ladder"}`

## Lucknow 2026-07-05 固定案例

`{"decisions": 39, "ledger_actions": 0, "first_lock_trades": 0, "flat_hold_decisions": 39, "self_cross_possible": false, "note": "single tracked position per city-day; every close references its open position_id"}`

逐决策全 ladder 概率：`generated/tmax_distribution_v3/lucknow_20260705_decisions.csv`；ledger：`lucknow_20260705_ledger.csv`。ledger 结构杜绝无持仓 YES/NO 自撞，但该模型该日未触发交易，未验证原交易反事实。

## Frozen artifact / shadow

`{"model_version": "tmax_distribution_v3", "artifact": "frozen_primary_hazard_chain", "candidate_status": "shadow_candidate", "promotion": "no_live", "primary_route": "market_path", "train_through": "2026-07-10", "train_rows": 4121, "train_dates": 22, "fusion_alpha_frozen": 1.0, "coherence_weight_frozen": 0.0, "calibrator_c_frozen": null, "h_cont": 0.5893491124260355, "model_description": {"model_version": "tmax_distribution_v3", "feature_columns": ["market_p_below", "market_p_current", "market_p_d1", "market_p_d2", "market_p_tail", "market_entropy", "ladder_overround", "quoted_rung_fraction", "current_yes_spread", "d1_yes_spread", "d2_yes_spread", "log_depth_min_anchor", "rungs_above_count", "boundary_pos_in_bracket", "forecast_gap_to_running_f", "current_minus_running_f", "temp_trend_1h_f", "temp_trend_3h_f", "max_age_min", "minutes_since_running_max", "forecast_peak_delta_hours_local", "forecast_ceiling_margin_f", "remaining_heat_integral_f", "forecast_curve_available", "tracking_residual_f", "obs_count_today"], "c_value": 0.1, "train_rows": 4121, "train_dates": 22, "train_through": "2026-07-10", "p_below": 0.000120598166907863, "h_cont": 0.5893491124260355, "heads": {"h0": {"mode": "model", "train_rows": 4121}, "h1": {"mode": "model", "train_rows": 3111}, "h2": {"mode": "model", "train_rows": 2573}}}, "pipeline_sha256": "181bd03c18d1fe4da30b1ea274eb3fd210a76a973985fde5d5e85020d30d8c16", "reload_prediction_max_abs_delta": 0.0, "zero_notional": true, "no_order_placed": true}`

shadow source rows: `92` → `docs/analysis/2026-07/generated/tmax_distribution_v3/shadow_events.csv`。复用统一 runner：`.venv/bin/python scripts/ops/tmax_distribution_edge_shadow_v1.py run --source docs/analysis/2026-07/generated/tmax_distribution_v3/shadow_events.csv --runtime-dir runtime/weather_edge_v1/tmax_distribution_v3_shadow`。

## Three Gates

baseline=`FAIL`；significance=`FAIL`；forward=`FAIL_fresh_forward_0`；conclusion=`shadow_candidate_no_live`。

## Probability Artifact Schema

Primary `market_path` 输出 `6102` 行 full-ladder + five-bucket + sequential hazards；每行 two probability spaces 均归一化，artifact 为 `generated/tmax_distribution_v3/primary_probability_artifact_rows.csv`。

GFS+ECMWF variant：`not_answerable`；strict as-of lag<=60m 双源覆盖在 2026-06-21+ 为 0，因此没有训练或性能结果。
