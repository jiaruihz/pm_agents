# City x Entry Timing Research - 2026-06-08

## 数据快照

- 数据源: `runtime/weather.db` (`/home/rui/projects/pm_agent/runtime/weather.db`)
- DB mtime UTC: `2026-06-08T01:32:53.116501+00:00`
- fact_built_at_utc: `2026-06-08T01:32:33.618777+00:00`
- CLOB coverage gate: `gate_pass=True`, live_real fill_ids=1333, db_fill_cost_minus_fact_cost=0.0
- sync/rebuild: `sync_weather_remote.sh` succeeded; `run_stack.sh` rebuilt DB/facts and then failed only at API start because port 8000 was already in use.

### 强制 SQL 自检

trade_class:
| trade_class | n |
| --- | --- |
| live_real | 1333 |
| live_simulated | 1104 |
| paper | 2285 |
| snapshot_replay | 636 |

settlement_status:
| settlement_status | n |
| --- | --- |
|  | 325 |
| settled | 5033 |

fact_signal_candidates:
| rows | eligible | paper_ordered | live_filled |
| --- | --- | --- | --- |
| 23893 | 7841 | 2886 | 520 |

orders x fills:
| status | orders | with_fill |
| --- | --- | --- |
| error | 151 | 0 |
| submitted | 1562 | 1333 |

## 结论摘要

- 城市差异存在，但大多数 city x timing cell 的样本仍小；结论应作为 live 风控和 shadow 研究优先级，不应直接放宽已禁窗口。
- `T-26-28`：post slice 中支持 drop 的城市有 `BuenosAires, Warsaw`；出现正向但只能 shadow 例外观察的城市有 `London`。
- `T-24-26`：post slice 中保留候选城市有 `none`；需要城市级复核后再 keep 的城市有 `Miami, NYC`。
- `<T-22` 当前仍不建议恢复 live：它没有完整 opportunity fact，且 post live fill 基线里 `T-20-22` 仍是明显负向。
- 下一步必须接 forecast checkpoint lineage；若某城市的 `T-24-26` 正负分化只是 pre/post forecast update 的混合，城市硬白名单会误判。

## Post-2026-06-01 Policy Matrix

| city | lt22_fills | lt22_roi | T-22-24_fills | T-22-24_roi | T-24-26_fills | T-24-26_roi | T-24-26_action | T-26-28_fills | T-26-28_roi | T-26-28_action | >T-28_fills | >T-28_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Amsterdam |  |  |  |  |  |  | insufficient_or_mixed |  |  | no_reliable_exception | 19 | -0.7981 |
| Ankara |  |  |  |  |  |  | insufficient_or_mixed |  |  | no_reliable_exception | 25 | -0.1801 |
| Austin |  |  |  |  |  |  | insufficient_or_mixed |  |  | no_reliable_exception |  |  |
| Beijing |  |  |  |  |  |  | insufficient_or_mixed |  |  | no_reliable_exception |  |  |
| BuenosAires |  |  | 5 | -0.4537 |  |  | insufficient_or_mixed | 10 | -0.7519 | supports_drop |  |  |
| Chengdu |  |  |  |  |  |  | insufficient_or_mixed |  |  | no_reliable_exception | 10 | -0.0255 |
| Chicago |  |  |  |  |  |  | insufficient_or_mixed |  |  | no_reliable_exception |  |  |
| Guangzhou |  |  |  |  |  |  | insufficient_or_mixed |  |  | no_reliable_exception | 9 | -0.1853 |
| Istanbul |  |  |  |  |  |  | insufficient_or_mixed | 1 | 0.5152 | no_reliable_exception | 11 | -0.6318 |
| Jeddah |  |  |  |  |  |  | insufficient_or_mixed | 6 | -1.0000 | no_reliable_exception | 18 | -0.4918 |
| Karachi |  |  |  |  |  |  | insufficient_or_mixed |  |  | no_reliable_exception | 22 | -0.1465 |
| LA | 14 | -0.6388 | 41 | 0.6840 |  |  | insufficient_or_mixed |  |  | no_reliable_exception |  |  |
| London |  |  |  |  | 1 | 3.7619 | insufficient_or_mixed | 7 | 0.1481 | possible_city_exception_but_keep_shadow_only | 32 | -0.2368 |
| Lucknow |  |  |  |  |  |  | insufficient_or_mixed |  |  | no_reliable_exception | 17 | -0.1343 |
| Madrid |  |  |  |  | 4 | 2.8462 | insufficient_or_mixed | 2 | 0.3889 | no_reliable_exception | 11 | -0.1813 |
| Manila |  |  |  |  |  |  | insufficient_or_mixed |  |  | no_reliable_exception | 15 | -0.0608 |
| Miami | 12 | -1.0000 | 37 | 1.1331 | 24 | -0.5352 | city_specific_review_before_keep |  |  | no_reliable_exception |  |  |
| Moscow |  |  |  |  |  |  | insufficient_or_mixed | 6 | -1.0000 | no_reliable_exception | 18 | -0.0788 |
| Munich |  |  |  |  |  |  | insufficient_or_mixed | 3 | 0.5152 | no_reliable_exception | 15 | -0.3445 |
| NYC | 14 | -0.1168 | 11 | -0.4317 | 34 | -0.4188 | city_specific_review_before_keep | 3 | -1.0000 | no_reliable_exception |  |  |
| Paris |  |  |  |  |  |  | insufficient_or_mixed |  |  | no_reliable_exception |  |  |
| Seattle | 6 | -0.1808 |  |  |  |  | insufficient_or_mixed |  |  | no_reliable_exception |  |  |
| Shanghai |  |  |  |  |  |  | insufficient_or_mixed |  |  | no_reliable_exception | 26 | 0.1136 |
| Singapore |  |  |  |  |  |  | insufficient_or_mixed |  |  | no_reliable_exception | 9 | -0.0471 |
| Tokyo |  |  |  |  |  |  | insufficient_or_mixed |  |  | no_reliable_exception | 18 | 0.4404 |
| Warsaw |  |  |  |  | 3 | 1.7027 | insufficient_or_mixed | 18 | -0.4794 | supports_drop | 16 | 0.5297 |

## Post Negative Direction Cells

| city | period | timing_bin | fills | city_days | cost_usd | pnl_usd | roi | win_rate | evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| NYC | post_2026_06_01 | T-24-26 | 34 | 9 | 85.3102 | -35.7260 | -0.4188 | 0.2941 | negative_direction |
| Amsterdam | post_2026_06_01 | >T-28 | 19 | 4 | 42.6866 | -34.0666 | -0.7981 | 0.1053 | negative_direction |
| Miami | post_2026_06_01 | T-24-26 | 24 | 9 | 61.2348 | -32.7715 | -0.5352 | 0.2083 | negative_direction |
| Miami | post_2026_06_01 | T-20-22 | 12 | 3 | 26.8867 | -26.8867 | -1.0000 | 0.0000 | negative_direction |
| BuenosAires | post_2026_06_01 | T-26-28 | 10 | 5 | 33.0105 | -24.8205 | -0.7519 | 0.1000 | negative_direction |
| Jeddah | post_2026_06_01 | >T-28 | 18 | 7 | 42.9143 | -21.1043 | -0.4918 | 0.3333 | negative_direction |
| London | post_2026_06_01 | >T-28 | 32 | 11 | 87.5582 | -20.7372 | -0.2368 | 0.3750 | negative_direction |
| Warsaw | post_2026_06_01 | T-26-28 | 18 | 5 | 31.2322 | -14.9722 | -0.4794 | 0.1111 | negative_direction |
| Munich | post_2026_06_01 | >T-28 | 15 | 6 | 39.6960 | -13.6746 | -0.3445 | 0.2667 | negative_direction |
| Istanbul | post_2026_06_01 | >T-28 | 11 | 4 | 19.6616 | -12.4216 | -0.6318 | 0.0909 | negative_direction |
| Ankara | post_2026_06_01 | >T-28 | 25 | 7 | 63.1362 | -11.3723 | -0.1801 | 0.4000 | negative_direction |
| LA | post_2026_06_01 | T-20-22 | 10 | 5 | 25.4062 | -11.2100 | -0.4412 | 0.4000 | negative_direction |
| NYC | post_2026_06_01 | T-22-24 | 11 | 2 | 23.1552 | -9.9952 | -0.4317 | 0.1818 | negative_direction |
| Karachi | post_2026_06_01 | >T-28 | 22 | 7 | 59.7168 | -8.7481 | -0.1465 | 0.5455 | negative_direction |
| Guangzhou | post_2026_06_01 | >T-28 | 9 | 6 | 36.9758 | -6.8525 | -0.1853 | 0.5556 | negative_direction |
| Madrid | post_2026_06_01 | >T-28 | 11 | 6 | 37.6462 | -6.8262 | -0.1813 | 0.3636 | negative_direction |
| BuenosAires | post_2026_06_01 | T-22-24 | 5 | 2 | 14.9909 | -6.8009 | -0.4537 | 0.2000 | negative_direction |
| Lucknow | post_2026_06_01 | >T-28 | 17 | 5 | 40.6316 | -5.4559 | -0.1343 | 0.5294 | negative_direction |
| NYC | post_2026_06_01 | T-20-22 | 7 | 4 | 14.3917 | -2.5517 | -0.1773 | 0.2857 | negative_direction |

## Post Positive Direction Cells

| city | period | timing_bin | fills | city_days | cost_usd | pnl_usd | roi | win_rate | evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LA | post_2026_06_01 | T-22-24 | 41 | 9 | 100.6655 | 68.8532 | 0.6840 | 0.6585 | positive_direction |
| Miami | post_2026_06_01 | T-22-24 | 37 | 5 | 58.0853 | 65.8155 | 1.1331 | 0.6486 | positive_direction |
| Warsaw | post_2026_06_01 | >T-28 | 16 | 7 | 46.6924 | 24.7342 | 0.5297 | 0.8125 | positive_direction |
| Tokyo | post_2026_06_01 | >T-28 | 18 | 5 | 48.9970 | 21.5785 | 0.4404 | 0.8333 | positive_direction |
| Shanghai | post_2026_06_01 | >T-28 | 26 | 10 | 85.7102 | 9.7347 | 0.1136 | 0.6923 | positive_direction |
| London | post_2026_06_01 | T-26-28 | 7 | 3 | 18.3877 | 2.7239 | 0.1481 | 0.5714 | positive_direction |

## Full City x Period x Timing

| city | period | timing_bin | fills | city_days | cost_usd | pnl_usd | roi | win_rate | evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Amsterdam | post_2026_06_01 | >T-28 | 19 | 4 | 42.6866 | -34.0666 | -0.7981 | 0.1053 | negative_direction |
| Ankara | post_2026_06_01 | >T-28 | 25 | 7 | 63.1362 | -11.3723 | -0.1801 | 0.4000 | negative_direction |
| BuenosAires | post_2026_06_01 | T-22-24 | 5 | 2 | 14.9909 | -6.8009 | -0.4537 | 0.2000 | negative_direction |
| BuenosAires | post_2026_06_01 | T-26-28 | 10 | 5 | 33.0105 | -24.8205 | -0.7519 | 0.1000 | negative_direction |
| Chengdu | post_2026_06_01 | >T-28 | 10 | 6 | 31.7402 | -0.8102 | -0.0255 | 0.6000 | mixed_or_flat |
| Guangzhou | post_2026_06_01 | >T-28 | 9 | 6 | 36.9758 | -6.8525 | -0.1853 | 0.5556 | negative_direction |
| Istanbul | post_2026_06_01 | T-26-28 | 1 | 1 | 4.9236 | 2.5364 | 0.5152 | 1.0000 | sample_too_small |
| Istanbul | post_2026_06_01 | >T-28 | 11 | 4 | 19.6616 | -12.4216 | -0.6318 | 0.0909 | negative_direction |
| Jeddah | post_2026_06_01 | T-26-28 | 6 | 1 | 4.9998 | -4.9998 | -1.0000 | 0.0000 | sample_too_small |
| Jeddah | post_2026_06_01 | >T-28 | 18 | 7 | 42.9143 | -21.1043 | -0.4918 | 0.3333 | negative_direction |
| Karachi | post_2026_06_01 | >T-28 | 22 | 7 | 59.7168 | -8.7481 | -0.1465 | 0.5455 | negative_direction |
| LA | post_2026_06_01 | <T-18 | 4 | 2 | 13.9013 | -13.9013 | -1.0000 | 0.0000 | sample_too_small |
| LA | post_2026_06_01 | T-20-22 | 10 | 5 | 25.4062 | -11.2100 | -0.4412 | 0.4000 | negative_direction |
| LA | post_2026_06_01 | T-22-24 | 41 | 9 | 100.6655 | 68.8532 | 0.6840 | 0.6585 | positive_direction |
| London | post_2026_06_01 | T-24-26 | 1 | 1 | 4.9980 | 18.8020 | 3.7619 | 1.0000 | sample_too_small |
| London | post_2026_06_01 | T-26-28 | 7 | 3 | 18.3877 | 2.7239 | 0.1481 | 0.5714 | positive_direction |
| London | post_2026_06_01 | >T-28 | 32 | 11 | 87.5582 | -20.7372 | -0.2368 | 0.3750 | negative_direction |
| Lucknow | post_2026_06_01 | >T-28 | 17 | 5 | 40.6316 | -5.4559 | -0.1343 | 0.5294 | negative_direction |
| Madrid | post_2026_06_01 | T-24-26 | 4 | 1 | 4.9976 | 14.2238 | 2.8462 | 1.0000 | sample_too_small |
| Madrid | post_2026_06_01 | T-26-28 | 2 | 1 | 4.9937 | 1.9420 | 0.3889 | 1.0000 | sample_too_small |
| Madrid | post_2026_06_01 | >T-28 | 11 | 6 | 37.6462 | -6.8262 | -0.1813 | 0.3636 | negative_direction |
| Manila | post_2026_06_01 | >T-28 | 15 | 5 | 48.9045 | -2.9745 | -0.0608 | 0.6000 | mixed_or_flat |
| Miami | post_2026_06_01 | T-20-22 | 12 | 3 | 26.8867 | -26.8867 | -1.0000 | 0.0000 | negative_direction |
| Miami | post_2026_06_01 | T-22-24 | 37 | 5 | 58.0853 | 65.8155 | 1.1331 | 0.6486 | positive_direction |
| Miami | post_2026_06_01 | T-24-26 | 24 | 9 | 61.2348 | -32.7715 | -0.5352 | 0.2083 | negative_direction |
| Moscow | post_2026_06_01 | T-26-28 | 6 | 1 | 4.9951 | -4.9951 | -1.0000 | 0.0000 | sample_too_small |
| Moscow | post_2026_06_01 | >T-28 | 18 | 7 | 63.4325 | -4.9999 | -0.0788 | 0.6111 | mixed_or_flat |
| Munich | post_2026_06_01 | T-26-28 | 3 | 1 | 4.9846 | 2.5678 | 0.5152 | 1.0000 | sample_too_small |
| Munich | post_2026_06_01 | >T-28 | 15 | 6 | 39.6960 | -13.6746 | -0.3445 | 0.2667 | negative_direction |
| NYC | post_2026_06_01 | T-18-20 | 7 | 3 | 17.8780 | -1.2180 | -0.0681 | 0.4286 | mixed_or_flat |
| NYC | post_2026_06_01 | T-20-22 | 7 | 4 | 14.3917 | -2.5517 | -0.1773 | 0.2857 | negative_direction |
| NYC | post_2026_06_01 | T-22-24 | 11 | 2 | 23.1552 | -9.9952 | -0.4317 | 0.1818 | negative_direction |
| NYC | post_2026_06_01 | T-24-26 | 34 | 9 | 85.3102 | -35.7260 | -0.4188 | 0.2941 | negative_direction |
| NYC | post_2026_06_01 | T-26-28 | 3 | 1 | 4.9984 | -4.9984 | -1.0000 | 0.0000 | sample_too_small |
| Seattle | post_2026_06_01 | T-18-20 | 2 | 1 | 4.9901 | 3.1904 | 0.6393 | 1.0000 | sample_too_small |
| Seattle | post_2026_06_01 | T-20-22 | 4 | 2 | 14.9937 | -6.8037 | -0.4538 | 0.5000 | sample_too_small |
| Shanghai | post_2026_06_01 | >T-28 | 26 | 10 | 85.7102 | 9.7347 | 0.1136 | 0.6923 | positive_direction |
| Singapore | post_2026_06_01 | >T-28 | 9 | 2 | 14.9803 | -0.7058 | -0.0471 | 0.4444 | mixed_or_flat |
| Tokyo | post_2026_06_01 | >T-28 | 18 | 5 | 48.9970 | 21.5785 | 0.4404 | 0.8333 | positive_direction |
| Warsaw | post_2026_06_01 | T-24-26 | 3 | 2 | 5.1334 | 8.7404 | 1.7027 | 1.0000 | sample_too_small |
| Warsaw | post_2026_06_01 | T-26-28 | 18 | 5 | 31.2322 | -14.9722 | -0.4794 | 0.1111 | negative_direction |
| Warsaw | post_2026_06_01 | >T-28 | 16 | 7 | 46.6924 | 24.7342 | 0.5297 | 0.8125 | positive_direction |
| Amsterdam | pre_2026_06_01 | T-24-26 | 2 | 1 | 4.9985 | 2.6915 | 0.5385 | 1.0000 | sample_too_small |
| Amsterdam | pre_2026_06_01 | >T-28 | 15 | 5 | 40.6442 | 9.9909 | 0.2458 | 0.6667 | positive_direction |
| Ankara | pre_2026_06_01 | T-24-26 | 3 | 2 | 9.8550 | 3.6450 | 0.3699 | 1.0000 | sample_too_small |
| Ankara | pre_2026_06_01 | >T-28 | 25 | 10 | 64.9577 | -29.9610 | -0.4612 | 0.3200 | negative_direction |
| Austin | pre_2026_06_01 | T-18-20 | 1 | 1 | 4.9654 | 1.7446 | 0.3514 | 1.0000 | sample_too_small |
| Austin | pre_2026_06_01 | T-20-22 | 3 | 1 | 4.9504 | 2.3296 | 0.4706 | 1.0000 | sample_too_small |
| Austin | pre_2026_06_01 | T-22-24 | 3 | 3 | 14.9560 | -1.0260 | -0.0686 | 0.6667 | sample_too_small |
| Austin | pre_2026_06_01 | T-24-26 | 13 | 5 | 29.4671 | -4.4714 | -0.1517 | 0.3846 | negative_direction |
| Beijing | pre_2026_06_01 | T-22-24 | 4 | 2 | 9.9968 | -3.0568 | -0.3058 | 0.2500 | sample_too_small |
| Beijing | pre_2026_06_01 | T-24-26 | 8 | 4 | 23.8609 | -9.0609 | -0.3797 | 0.2500 | negative_direction |
| Beijing | pre_2026_06_01 | >T-28 | 1 | 1 | 4.9608 | -4.9608 | -1.0000 | 0.0000 | sample_too_small |
| BuenosAires | pre_2026_06_01 | T-22-24 | 6 | 4 | 17.9919 | 2.3381 | 0.1300 | 0.5000 | positive_direction |
| BuenosAires | pre_2026_06_01 | T-24-26 | 2 | 2 | 8.5432 | 3.2968 | 0.3859 | 1.0000 | sample_too_small |
| BuenosAires | pre_2026_06_01 | T-26-28 | 2 | 2 | 5.2500 | 4.7500 | 0.9048 | 1.0000 | sample_too_small |
| BuenosAires | pre_2026_06_01 | >T-28 | 6 | 3 | 12.8825 | -12.8825 | -1.0000 | 0.0000 | negative_direction |
| Chengdu | pre_2026_06_01 | >T-28 | 6 | 5 | 23.5386 | 9.2314 | 0.3922 | 1.0000 | positive_direction |
| Chicago | pre_2026_06_01 | T-24-26 | 4 | 2 | 14.9166 | -0.7016 | -0.0470 | 0.7500 | sample_too_small |
| Guangzhou | pre_2026_06_01 | T-24-26 | 2 | 1 | 4.4571 | 2.7318 | 0.6129 | 1.0000 | sample_too_small |
| Guangzhou | pre_2026_06_01 | >T-28 | 13 | 7 | 46.8539 | -5.0139 | -0.1070 | 0.4615 | negative_direction |
| Istanbul | pre_2026_06_01 | T-24-26 | 1 | 1 | 4.9985 | 2.6915 | 0.5385 | 1.0000 | sample_too_small |
| Istanbul | pre_2026_06_01 | T-26-28 | 1 | 1 | 5.0000 | 15.0000 | 3.0000 | 1.0000 | sample_too_small |
| Istanbul | pre_2026_06_01 | >T-28 | 18 | 7 | 49.7549 | -31.5961 | -0.6350 | 0.2222 | negative_direction |
| Jeddah | pre_2026_06_01 | >T-28 | 14 | 8 | 38.2919 | -2.8933 | -0.0756 | 0.5714 | mixed_or_flat |
| Karachi | pre_2026_06_01 | T-24-26 | 4 | 2 | 14.8329 | -6.6429 | -0.4478 | 0.2500 | sample_too_small |
| Karachi | pre_2026_06_01 | >T-28 | 31 | 10 | 92.6385 | -11.2892 | -0.1219 | 0.5484 | negative_direction |
| LA | pre_2026_06_01 | <T-18 | 4 | 2 | 16.4139 | 33.2761 | 2.0273 | 1.0000 | sample_too_small |
| LA | pre_2026_06_01 | T-18-20 | 6 | 4 | 17.0897 | 8.3903 | 0.4910 | 0.8333 | positive_direction |
| LA | pre_2026_06_01 | T-20-22 | 3 | 1 | 4.0342 | 11.5758 | 2.8694 | 1.0000 | sample_too_small |
| LA | pre_2026_06_01 | T-22-24 | 24 | 10 | 60.0389 | 2.9231 | 0.0487 | 0.3750 | mixed_or_flat |
| LA | pre_2026_06_01 | T-24-26 | 20 | 8 | 49.6241 | 7.1270 | 0.1436 | 0.5500 | positive_direction |
| London | pre_2026_06_01 | T-22-24 | 7 | 3 | 13.5038 | -6.2138 | -0.4602 | 0.1429 | negative_direction |
| London | pre_2026_06_01 | T-24-26 | 17 | 10 | 62.8058 | 22.5804 | 0.3595 | 0.7647 | positive_direction |
| London | pre_2026_06_01 | >T-28 | 22 | 7 | 60.4594 | 10.4221 | 0.1724 | 0.6364 | positive_direction |
| Lucknow | pre_2026_06_01 | T-24-26 | 1 | 1 | 4.6748 | 3.3852 | 0.7241 | 1.0000 | sample_too_small |
| Lucknow | pre_2026_06_01 | >T-28 | 6 | 2 | 14.3875 | -0.0575 | -0.0040 | 0.6667 | mixed_or_flat |
| Madrid | pre_2026_06_01 | T-22-24 | 1 | 1 | 5.0000 | -5.0000 | -1.0000 | 0.0000 | sample_too_small |
| Madrid | pre_2026_06_01 | T-24-26 | 8 | 7 | 34.2862 | -3.0562 | -0.0891 | 0.3750 | mixed_or_flat |
| Madrid | pre_2026_06_01 | >T-28 | 8 | 3 | 16.6498 | 19.0470 | 1.1440 | 1.0000 | positive_direction |
| Manila | pre_2026_06_01 | >T-28 | 12 | 4 | 32.6210 | -25.8710 | -0.7931 | 0.0833 | negative_direction |
| Miami | pre_2026_06_01 | T-18-20 | 4 | 2 | 11.8931 | -11.8931 | -1.0000 | 0.0000 | sample_too_small |
| Miami | pre_2026_06_01 | T-20-22 | 11 | 6 | 33.9861 | -15.8791 | -0.4672 | 0.4545 | negative_direction |
| Miami | pre_2026_06_01 | T-22-24 | 1 | 1 | 4.9968 | 1.9432 | 0.3889 | 1.0000 | sample_too_small |
| Miami | pre_2026_06_01 | T-24-26 | 49 | 18 | 130.5031 | 17.5621 | 0.1346 | 0.5510 | positive_direction |
| Moscow | pre_2026_06_01 | T-22-24 | 1 | 1 | 4.9956 | -4.9956 | -1.0000 | 0.0000 | sample_too_small |
| Moscow | pre_2026_06_01 | >T-28 | 23 | 9 | 64.8418 | -15.9490 | -0.2460 | 0.4348 | negative_direction |
| Munich | pre_2026_06_01 | >T-28 | 13 | 6 | 37.4783 | -21.3583 | -0.5699 | 0.3077 | negative_direction |
| NYC | pre_2026_06_01 | T-18-20 | 1 | 1 | 4.0855 | -4.0855 | -1.0000 | 0.0000 | sample_too_small |
| NYC | pre_2026_06_01 | T-20-22 | 4 | 1 | 9.0724 | 15.0476 | 1.6586 | 1.0000 | sample_too_small |
| NYC | pre_2026_06_01 | T-22-24 | 11 | 4 | 20.8287 | -15.8287 | -0.7599 | 0.0909 | negative_direction |
| NYC | pre_2026_06_01 | T-24-26 | 36 | 16 | 103.8709 | -20.0309 | -0.1928 | 0.4444 | negative_direction |
| Paris | pre_2026_06_01 | T-24-26 | 14 | 6 | 49.6005 | 10.4843 | 0.2114 | 0.7857 | positive_direction |
| Paris | pre_2026_06_01 | >T-28 | 17 | 6 | 44.1542 | -3.3642 | -0.0762 | 0.6471 | mixed_or_flat |
| Seattle | pre_2026_06_01 | <T-18 | 12 | 4 | 15.1687 | 32.0478 | 2.1128 | 1.0000 | positive_direction |
| Seattle | pre_2026_06_01 | T-20-22 | 1 | 1 | 4.2174 | 3.5926 | 0.8519 | 1.0000 | sample_too_small |
| Seattle | pre_2026_06_01 | T-22-24 | 10 | 6 | 22.7082 | 6.4018 | 0.2819 | 0.6000 | positive_direction |
| Seattle | pre_2026_06_01 | T-24-26 | 2 | 1 | 4.9985 | 2.6915 | 0.5385 | 1.0000 | sample_too_small |
| Shanghai | pre_2026_06_01 | T-24-26 | 3 | 2 | 14.9536 | 7.2864 | 0.4873 | 1.0000 | sample_too_small |
| Shanghai | pre_2026_06_01 | >T-28 | 14 | 8 | 35.6662 | -16.2556 | -0.4558 | 0.3571 | negative_direction |
| Singapore | pre_2026_06_01 | T-22-24 | 1 | 1 | 4.9979 | -4.9979 | -1.0000 | 0.0000 | sample_too_small |
| Singapore | pre_2026_06_01 | T-24-26 | 2 | 1 | 4.9980 | 2.1420 | 0.4286 | 1.0000 | sample_too_small |
| Singapore | pre_2026_06_01 | >T-28 | 4 | 3 | 14.9044 | 2.0290 | 0.1361 | 0.5000 | sample_too_small |
| Tokyo | pre_2026_06_01 | T-22-24 | 4 | 1 | 4.9504 | 4.5696 | 0.9231 | 1.0000 | sample_too_small |
| Tokyo | pre_2026_06_01 | T-24-26 | 17 | 8 | 48.7992 | 11.9624 | 0.2451 | 0.7647 | positive_direction |
| Tokyo | pre_2026_06_01 | >T-28 | 21 | 9 | 58.8841 | 9.4866 | 0.1611 | 0.6667 | positive_direction |
| Warsaw | pre_2026_06_01 | T-22-24 | 5 | 2 | 9.9073 | 19.5260 | 1.9709 | 1.0000 | positive_direction |
| Warsaw | pre_2026_06_01 | T-24-26 | 15 | 6 | 37.9393 | 9.7545 | 0.2571 | 0.7333 | positive_direction |
| Warsaw | pre_2026_06_01 | T-26-28 | 7 | 2 | 17.7345 | -17.7345 | -1.0000 | 0.0000 | negative_direction |
| Warsaw | pre_2026_06_01 | >T-28 | 19 | 7 | 40.3156 | 36.5727 | 0.9072 | 1.0000 | positive_direction |

## City x Timing x Side

| city | timing_bin | side | fills | city_days | cost_usd | pnl_usd | roi | win_rate | evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Amsterdam | T-24-26 | BUY_NO | 2 | 1 | 4.9985 | 2.6915 | 0.5385 | 1.0000 | sample_too_small |
| Amsterdam | >T-28 | BUY_NO | 14 | 7 | 36.1041 | -2.4690 | -0.0684 | 0.6429 | mixed_or_flat |
| Amsterdam | >T-28 | BUY_YES | 20 | 9 | 47.2267 | -21.6067 | -0.4575 | 0.1500 | negative_direction |
| Ankara | T-24-26 | BUY_NO | 3 | 2 | 9.8550 | 3.6450 | 0.3699 | 1.0000 | sample_too_small |
| Ankara | >T-28 | BUY_NO | 39 | 16 | 112.7107 | -25.9501 | -0.2302 | 0.4615 | negative_direction |
| Ankara | >T-28 | BUY_YES | 11 | 4 | 15.3832 | -15.3832 | -1.0000 | 0.0000 | negative_direction |
| Austin | T-18-20 | BUY_NO | 1 | 1 | 4.9654 | 1.7446 | 0.3514 | 1.0000 | sample_too_small |
| Austin | T-20-22 | BUY_NO | 3 | 1 | 4.9504 | 2.3296 | 0.4706 | 1.0000 | sample_too_small |
| Austin | T-22-24 | BUY_NO | 2 | 2 | 9.9562 | 3.9738 | 0.3991 | 1.0000 | sample_too_small |
| Austin | T-22-24 | BUY_YES | 1 | 1 | 4.9998 | -4.9998 | -1.0000 | 0.0000 | sample_too_small |
| Austin | T-24-26 | BUY_NO | 4 | 3 | 14.4687 | -0.8330 | -0.0576 | 0.7500 | sample_too_small |
| Austin | T-24-26 | BUY_YES | 9 | 3 | 14.9984 | -3.6384 | -0.2426 | 0.2222 | negative_direction |
| Beijing | T-22-24 | BUY_NO | 1 | 1 | 4.9968 | 1.9432 | 0.3889 | 1.0000 | sample_too_small |
| Beijing | T-22-24 | BUY_YES | 3 | 1 | 5.0000 | -5.0000 | -1.0000 | 0.0000 | sample_too_small |
| Beijing | T-24-26 | BUY_NO | 8 | 4 | 23.8609 | -9.0609 | -0.3797 | 0.2500 | negative_direction |
| Beijing | >T-28 | BUY_NO | 1 | 1 | 4.9608 | -4.9608 | -1.0000 | 0.0000 | sample_too_small |
| BuenosAires | T-22-24 | BUY_NO | 9 | 6 | 27.9844 | 0.5356 | 0.0191 | 0.4444 | mixed_or_flat |
| BuenosAires | T-22-24 | BUY_YES | 2 | 1 | 4.9984 | -4.9984 | -1.0000 | 0.0000 | sample_too_small |
| BuenosAires | T-24-26 | BUY_NO | 2 | 2 | 8.5432 | 3.2968 | 0.3859 | 1.0000 | sample_too_small |
| BuenosAires | T-26-28 | BUY_NO | 7 | 5 | 23.6297 | -10.4397 | -0.4418 | 0.2857 | negative_direction |
| BuenosAires | T-26-28 | BUY_YES | 5 | 4 | 14.6308 | -9.6308 | -0.6583 | 0.2000 | negative_direction |
| BuenosAires | >T-28 | BUY_NO | 2 | 2 | 7.8842 | -7.8842 | -1.0000 | 0.0000 | sample_too_small |
| BuenosAires | >T-28 | BUY_YES | 4 | 1 | 4.9983 | -4.9983 | -1.0000 | 0.0000 | sample_too_small |
| Chengdu | >T-28 | BUY_NO | 16 | 11 | 55.2788 | 8.4212 | 0.1523 | 0.7500 | positive_direction |
| Chicago | T-24-26 | BUY_NO | 4 | 2 | 14.9166 | -0.7016 | -0.0470 | 0.7500 | sample_too_small |
| Guangzhou | T-24-26 | BUY_NO | 2 | 1 | 4.4571 | 2.7318 | 0.6129 | 1.0000 | sample_too_small |
| Guangzhou | >T-28 | BUY_NO | 22 | 13 | 83.8298 | -11.8664 | -0.1416 | 0.5000 | negative_direction |
| Istanbul | T-24-26 | BUY_NO | 1 | 1 | 4.9985 | 2.6915 | 0.5385 | 1.0000 | sample_too_small |
| Istanbul | T-26-28 | BUY_NO | 1 | 1 | 4.9236 | 2.5364 | 0.5152 | 1.0000 | sample_too_small |
| Istanbul | T-26-28 | BUY_YES | 1 | 1 | 5.0000 | 15.0000 | 3.0000 | 1.0000 | sample_too_small |
| Istanbul | >T-28 | BUY_NO | 17 | 8 | 46.7492 | -28.5904 | -0.6116 | 0.2353 | negative_direction |
| Istanbul | >T-28 | BUY_YES | 12 | 5 | 22.6673 | -15.4273 | -0.6806 | 0.0833 | negative_direction |
| Jeddah | T-26-28 | BUY_YES | 6 | 1 | 4.9998 | -4.9998 | -1.0000 | 0.0000 | sample_too_small |
| Jeddah | >T-28 | BUY_NO | 24 | 14 | 71.2150 | -14.0064 | -0.1967 | 0.5833 | negative_direction |
| Jeddah | >T-28 | BUY_YES | 8 | 2 | 9.9912 | -9.9912 | -1.0000 | 0.0000 | negative_direction |
| Karachi | T-24-26 | BUY_NO | 4 | 2 | 14.8329 | -6.6429 | -0.4478 | 0.2500 | sample_too_small |
| Karachi | >T-28 | BUY_NO | 44 | 17 | 124.6700 | -19.3720 | -0.1554 | 0.5909 | negative_direction |
| Karachi | >T-28 | BUY_YES | 9 | 7 | 27.6853 | -0.6653 | -0.0240 | 0.3333 | mixed_or_flat |
| LA | <T-18 | BUY_NO | 2 | 2 | 8.7551 | 13.0149 | 1.4866 | 1.0000 | sample_too_small |
| LA | <T-18 | BUY_YES | 6 | 4 | 21.5601 | 6.3599 | 0.2950 | 0.3333 | positive_direction |
| LA | T-18-20 | BUY_NO | 3 | 3 | 13.3256 | 2.7344 | 0.2052 | 0.6667 | sample_too_small |
| LA | T-18-20 | BUY_YES | 3 | 1 | 3.7641 | 5.6559 | 1.5026 | 1.0000 | sample_too_small |
| LA | T-20-22 | BUY_NO | 2 | 2 | 7.9754 | -7.9754 | -1.0000 | 0.0000 | sample_too_small |
| LA | T-20-22 | BUY_YES | 11 | 6 | 21.4650 | 8.3412 | 0.3886 | 0.6364 | positive_direction |
| LA | T-22-24 | BUY_NO | 30 | 14 | 83.9960 | 1.6547 | 0.0197 | 0.5667 | mixed_or_flat |
| LA | T-22-24 | BUY_YES | 35 | 17 | 76.7084 | 70.1216 | 0.9141 | 0.5429 | positive_direction |
| LA | T-24-26 | BUY_NO | 12 | 6 | 29.8526 | 0.4174 | 0.0140 | 0.5000 | mixed_or_flat |
| LA | T-24-26 | BUY_YES | 8 | 4 | 19.7715 | 6.7096 | 0.3394 | 0.6250 | positive_direction |
| London | T-22-24 | BUY_NO | 1 | 1 | 4.9572 | 2.3328 | 0.4706 | 1.0000 | sample_too_small |
| London | T-22-24 | BUY_YES | 6 | 2 | 8.5466 | -8.5466 | -1.0000 | 0.0000 | negative_direction |
| London | T-24-26 | BUY_NO | 15 | 10 | 53.0947 | 4.5015 | 0.0848 | 0.7333 | mixed_or_flat |
| London | T-24-26 | BUY_YES | 3 | 3 | 14.7091 | 36.8809 | 2.5074 | 1.0000 | sample_too_small |
| London | T-26-28 | BUY_NO | 4 | 2 | 13.3892 | 7.7224 | 0.5768 | 1.0000 | sample_too_small |
| London | T-26-28 | BUY_YES | 3 | 1 | 4.9985 | -4.9985 | -1.0000 | 0.0000 | sample_too_small |
| London | >T-28 | BUY_NO | 29 | 13 | 93.9929 | -8.7969 | -0.0936 | 0.5517 | mixed_or_flat |
| London | >T-28 | BUY_YES | 25 | 13 | 54.0247 | -1.5182 | -0.0281 | 0.4000 | mixed_or_flat |
| Lucknow | T-24-26 | BUY_NO | 1 | 1 | 4.6748 | 3.3852 | 0.7241 | 1.0000 | sample_too_small |
| Lucknow | >T-28 | BUY_NO | 15 | 6 | 36.5550 | 12.9507 | 0.3543 | 0.8667 | positive_direction |
| Lucknow | >T-28 | BUY_YES | 8 | 3 | 18.4641 | -18.4641 | -1.0000 | 0.0000 | negative_direction |
| Madrid | T-22-24 | BUY_YES | 1 | 1 | 5.0000 | -5.0000 | -1.0000 | 0.0000 | sample_too_small |
| Madrid | T-24-26 | BUY_NO | 7 | 6 | 29.3826 | 1.8474 | 0.0629 | 0.4286 | mixed_or_flat |
| Madrid | T-24-26 | BUY_YES | 5 | 2 | 9.9012 | 9.3202 | 0.9413 | 0.8000 | positive_direction |
| Madrid | T-26-28 | BUY_NO | 2 | 1 | 4.9937 | 1.9420 | 0.3889 | 1.0000 | sample_too_small |
| Madrid | >T-28 | BUY_NO | 15 | 9 | 43.5590 | 6.5778 | 0.1510 | 0.6000 | positive_direction |
| Madrid | >T-28 | BUY_YES | 4 | 3 | 10.7370 | 5.6430 | 0.5256 | 0.7500 | sample_too_small |
| Manila | >T-28 | BUY_NO | 19 | 8 | 63.5679 | -10.8879 | -0.1713 | 0.5263 | negative_direction |
| Manila | >T-28 | BUY_YES | 8 | 4 | 17.9576 | -17.9576 | -1.0000 | 0.0000 | negative_direction |
| Miami | T-18-20 | BUY_NO | 3 | 2 | 7.3235 | -7.3235 | -1.0000 | 0.0000 | sample_too_small |
| Miami | T-18-20 | BUY_YES | 1 | 1 | 4.5696 | -4.5696 | -1.0000 | 0.0000 | sample_too_small |
| Miami | T-20-22 | BUY_NO | 12 | 6 | 36.6496 | -29.8996 | -0.8158 | 0.1667 | negative_direction |
| Miami | T-20-22 | BUY_YES | 11 | 5 | 24.2233 | -12.8663 | -0.5312 | 0.2727 | negative_direction |
| Miami | T-22-24 | BUY_NO | 8 | 3 | 17.1706 | 12.0022 | 0.6990 | 1.0000 | positive_direction |
| Miami | T-22-24 | BUY_YES | 30 | 5 | 45.9115 | 55.7565 | 1.2144 | 0.5667 | positive_direction |
| Miami | T-24-26 | BUY_NO | 46 | 23 | 129.1468 | 17.9518 | 0.1390 | 0.6304 | positive_direction |
| Miami | T-24-26 | BUY_YES | 27 | 14 | 62.5911 | -33.1611 | -0.5298 | 0.1111 | negative_direction |
| Moscow | T-22-24 | BUY_NO | 1 | 1 | 4.9956 | -4.9956 | -1.0000 | 0.0000 | sample_too_small |
| Moscow | T-26-28 | BUY_YES | 6 | 1 | 4.9951 | -4.9951 | -1.0000 | 0.0000 | sample_too_small |
| Moscow | >T-28 | BUY_NO | 30 | 16 | 105.9014 | 1.4239 | 0.0134 | 0.7000 | mixed_or_flat |
| Moscow | >T-28 | BUY_YES | 11 | 5 | 22.3729 | -22.3729 | -1.0000 | 0.0000 | negative_direction |
| Munich | T-26-28 | BUY_NO | 3 | 1 | 4.9846 | 2.5678 | 0.5152 | 1.0000 | sample_too_small |
| Munich | >T-28 | BUY_NO | 20 | 10 | 57.7746 | -15.6332 | -0.2706 | 0.4000 | negative_direction |
| Munich | >T-28 | BUY_YES | 8 | 5 | 19.3997 | -19.3997 | -1.0000 | 0.0000 | negative_direction |
| NYC | T-18-20 | BUY_NO | 4 | 2 | 14.0815 | 2.5785 | 0.1831 | 0.7500 | sample_too_small |
| NYC | T-18-20 | BUY_YES | 4 | 2 | 7.8820 | -7.8820 | -1.0000 | 0.0000 | sample_too_small |
| NYC | T-20-22 | BUY_NO | 3 | 3 | 13.3132 | 6.5268 | 0.4903 | 1.0000 | sample_too_small |
| NYC | T-20-22 | BUY_YES | 8 | 3 | 10.1509 | 5.9691 | 0.5880 | 0.3750 | positive_direction |
| NYC | T-22-24 | BUY_NO | 8 | 4 | 19.0597 | -0.8997 | -0.0472 | 0.3750 | mixed_or_flat |
| NYC | T-22-24 | BUY_YES | 14 | 4 | 24.9242 | -24.9242 | -1.0000 | 0.0000 | negative_direction |
| NYC | T-24-26 | BUY_NO | 36 | 19 | 121.0540 | -21.3698 | -0.1765 | 0.5556 | negative_direction |
| NYC | T-24-26 | BUY_YES | 34 | 15 | 68.1271 | -34.3871 | -0.5047 | 0.1765 | negative_direction |
| NYC | T-26-28 | BUY_YES | 3 | 1 | 4.9984 | -4.9984 | -1.0000 | 0.0000 | sample_too_small |
| Paris | T-24-26 | BUY_NO | 14 | 6 | 49.6005 | 10.4843 | 0.2114 | 0.7857 | positive_direction |
| Paris | >T-28 | BUY_NO | 17 | 6 | 44.1542 | -3.3642 | -0.0762 | 0.6471 | mixed_or_flat |
| Seattle | <T-18 | BUY_NO | 3 | 1 | 4.9846 | 2.5678 | 0.5152 | 1.0000 | sample_too_small |
| Seattle | <T-18 | BUY_YES | 9 | 3 | 10.1841 | 29.4800 | 2.8947 | 1.0000 | positive_direction |
| Seattle | T-18-20 | BUY_NO | 2 | 1 | 4.9901 | 3.1904 | 0.6393 | 1.0000 | sample_too_small |
| Seattle | T-20-22 | BUY_NO | 4 | 2 | 14.2115 | 1.7885 | 0.1258 | 0.7500 | sample_too_small |
| Seattle | T-20-22 | BUY_YES | 1 | 1 | 4.9996 | -4.9996 | -1.0000 | 0.0000 | sample_too_small |
| Seattle | T-22-24 | BUY_NO | 10 | 6 | 22.7082 | 6.4018 | 0.2819 | 0.6000 | positive_direction |
| Seattle | T-24-26 | BUY_NO | 2 | 1 | 4.9985 | 2.6915 | 0.5385 | 1.0000 | sample_too_small |
| Shanghai | T-24-26 | BUY_NO | 3 | 2 | 14.9536 | 7.2864 | 0.4873 | 1.0000 | sample_too_small |
| Shanghai | >T-28 | BUY_NO | 32 | 15 | 92.5805 | -4.4949 | -0.0486 | 0.6562 | mixed_or_flat |
| Shanghai | >T-28 | BUY_YES | 8 | 7 | 28.7960 | -2.0260 | -0.0704 | 0.2500 | mixed_or_flat |
| Singapore | T-22-24 | BUY_NO | 1 | 1 | 4.9979 | -4.9979 | -1.0000 | 0.0000 | sample_too_small |
| Singapore | T-24-26 | BUY_NO | 2 | 1 | 4.9980 | 2.1420 | 0.4286 | 1.0000 | sample_too_small |
| Singapore | >T-28 | BUY_NO | 13 | 5 | 29.8847 | 1.3232 | 0.0443 | 0.4615 | mixed_or_flat |
| Tokyo | T-22-24 | BUY_NO | 4 | 1 | 4.9504 | 4.5696 | 0.9231 | 1.0000 | sample_too_small |
| Tokyo | T-24-26 | BUY_NO | 17 | 8 | 48.7992 | 11.9624 | 0.2451 | 0.7647 | positive_direction |
| Tokyo | >T-28 | BUY_NO | 36 | 14 | 103.4186 | 17.6776 | 0.1709 | 0.7222 | positive_direction |
| Tokyo | >T-28 | BUY_YES | 3 | 1 | 4.4625 | 13.3875 | 3.0000 | 1.0000 | sample_too_small |
| Warsaw | T-22-24 | BUY_YES | 5 | 2 | 9.9073 | 19.5260 | 1.9709 | 1.0000 | positive_direction |
| Warsaw | T-24-26 | BUY_NO | 11 | 6 | 34.0837 | -0.6699 | -0.0197 | 0.6364 | mixed_or_flat |
| Warsaw | T-24-26 | BUY_YES | 7 | 3 | 8.9889 | 19.1648 | 2.1320 | 1.0000 | positive_direction |
| Warsaw | T-26-28 | BUY_NO | 4 | 3 | 13.5888 | -5.6588 | -0.4164 | 0.2500 | sample_too_small |
| Warsaw | T-26-28 | BUY_YES | 21 | 7 | 35.3779 | -27.0479 | -0.7645 | 0.0476 | negative_direction |
| Warsaw | >T-28 | BUY_NO | 24 | 10 | 65.3210 | 36.1339 | 0.5532 | 1.0000 | positive_direction |
| Warsaw | >T-28 | BUY_YES | 11 | 5 | 21.6870 | 25.1730 | 1.1607 | 0.7273 | positive_direction |

## 使用边界

- 本报告只用 `live_real + settled` 成交样本回答城市 x timing 的 realized 风险，不证明未成交 opportunity alpha。
- `sample_too_small` 不等于安全；它只表示当前样本不足以形成城市级例外。
- 已收紧 live 默认窗口到 `22 <= hours_to_settle <= 26`；任何恢复 `T-26-28` 的城市例外都应先走 shadow。
