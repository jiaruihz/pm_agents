# Source-Event Hazard Router v1

> generated 2026-07-13; research-only; zero notional; no live order or live policy change.

## 结论先行

这轮把 source cross、时间未突破、current/d1 YES/NO 和真实双边 ask 放进同一个分母。
最终 gate: significance=FAIL / baseline=FAIL / forward=FAIL / conclusion=inconclusive。
任何正点估仍只作方向发现；没有一条策略在本轮被升级为 live。

## 直接发现

- `cross current-NO continuation` 是唯一值得继续 forward 的概率腿：42 rows/14 dates，ROI 11.6%，CI [-11.4%,38.1%]，recent 3.5%；CI 与同价 baseline excess 都未过门。
- `cross current-YES exhaustion/reversal` 当前为负：ROI -8.0%，recent -100.0%。
- `cross T+1 YES` 显著失败，说明升温后直接买 exact next rung 承担了 overshoot/stop-location 错配：ROI -44.7%，CI 上界 -4.6%。
- `60m no-break current YES` 的全窗正点估没有 forward 存活：ROI 10.0%，recent -40.7%，同价 excess -28.5%。
- 全表达 router 没有把弱信号变成组合 alpha：ROI -5.5%，recent -7.7%。

## 数据漏斗

- input: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/tmax_distribution_v3/state_rows_v3.csv`
- labeled PIT states: `8094`; dates `22`; cities `47`.
- expanding predictions available: `5607`; cross episodes: `518`.
- grain: one saved ladder snapshot state; strategy replay de-duplicates first row per anchor episode/expression or first router choice per city-date.
- execution: real YES/NO asks and ask size from `ladder_book_json`; min ask size 5; official Weather taker fee on entry and markout exit.

## 概率层：是否打赢盘口

| target | model | rows | dates | date_equal_logloss | date_equal_brier | delta_logloss_vs_market | delta_ci_low | delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | market | 5067 | 15 | 0.1342 | 0.0405 | 0.0% | 0.0% | 0.0% |
| current_yes | market_time | 5067 | 15 | 0.1493 | 0.0451 | 1.5% | 0.0% | 3.1% |
| current_yes | market_physics | 5067 | 15 | 0.1498 | 0.0466 | 1.6% | -1.0% | 4.7% |
| d1_yes | market | 5067 | 15 | 0.1977 | 0.0599 | 0.0% | 0.0% | 0.0% |
| d1_yes | market_time | 5067 | 15 | 0.2097 | 0.0631 | 1.2% | 0.2% | 2.4% |
| d1_yes | market_physics | 5067 | 15 | 0.2071 | 0.0622 | 0.9% | -0.4% | 2.4% |

## 交易表达

| strategy | scope | edge_threshold | rows | dates | cities | avg_ask | win_rate | roi | roi_ci_low | roi_ci_high | same_band_baseline_rows | same_band_baseline_roi | date_equal_excess_roi | excess_ci_low | excess_ci_high | recent_4_dates | recent_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cross_continuation_current_no | cross | 0.0200 | 42 | 14 | 17 | 0.3985 | 45.2% | 11.6% | -11.4% | 38.1% | 187 | -1.4% | -7.0% | -35.2% | 19.9% | 2026-07-07,2026-07-08,2026-07-09,2026-07-10 | 3.5% |
| no_break_60_current_yes | no_break_60 | 0.0200 | 94 | 15 | 38 | 0.3042 | 34.0% | 10.0% | -16.5% | 28.4% | 436 | -2.0% | -28.5% | -58.8% | -0.4% | 2026-07-06,2026-07-07,2026-07-08,2026-07-09 | -40.7% |
| no_break_60_current_yes | no_break_60 | 0.0000 | 146 | 15 | 41 | 0.2767 | 30.1% | 7.3% | -8.3% | 20.1% | 455 | -1.6% | -29.7% | -58.4% | -3.3% | 2026-07-06,2026-07-07,2026-07-08,2026-07-09 | -43.8% |
| cross_continuation_current_no | cross | 0.0000 | 102 | 14 | 29 | 0.6917 | 71.6% | 2.9% | -2.5% | 11.0% | 328 | -0.9% | -6.2% | -28.4% | 11.1% | 2026-07-07,2026-07-08,2026-07-09,2026-07-10 | 3.5% |
| all_state_best_expression_router | all_state | 0.0000 | 358 | 16 | 47 | 0.4411 | 44.1% | -0.6% | -7.7% | 4.1% | 367 | -1.7% | 0.3% | -2.9% | 2.8% | 2026-07-07,2026-07-08,2026-07-09,2026-07-10 | -7.7% |
| all_state_best_expression_router | all_state | 0.0200 | 258 | 16 | 47 | 0.3219 | 31.0% | -5.5% | -19.3% | 6.6% | 338 | -5.9% | 0.3% | -7.9% | 7.2% | 2026-07-07,2026-07-08,2026-07-09,2026-07-10 | -7.7% |
| cross_exhaustion_current_yes | cross | 0.0000 | 123 | 15 | 26 | 0.3857 | 36.6% | -6.6% | -20.7% | 12.9% | 330 | -17.1% | -6.2% | -29.6% | 14.3% | 2026-07-07,2026-07-08,2026-07-09,2026-07-10 | -28.1% |
| cross_exhaustion_current_yes | cross | 0.0200 | 101 | 15 | 24 | 0.3703 | 34.7% | -8.0% | -25.4% | 15.9% | 330 | -17.1% | -9.6% | -37.6% | 16.9% | 2026-07-07,2026-07-08,2026-07-09,2026-07-10 | -100.0% |

## 结构锁定与 source-basis 反转

| strategy | rows | dates | cities | wins | avg_ask | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cross_prev_no | 105 | 15 | 26 | 105 | 0.9989 | 0.1% | 0.1% | 0.1% |
| source_basis_reversal_prev_yes | 131 | 15 | 29 | 0 | 0.0026 | -100.0% | -100.0% | -100.0% |

## Cross 后 15-120 分钟可卖 bid markout

| strategy | edge_threshold | expression | rows | dates | avg_minutes | avg_markout_return | median_markout_return | positive_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all_state_best_expression_router | 0.0200 | current_no | 3 | 3 | 35.0389 | -16.9% | -24.3% | 0.0% |
| all_state_best_expression_router | 0.0200 | current_yes | 22 | 8 | 24.5061 | -56.9% | -63.5% | 0.0% |
| all_state_best_expression_router | 0.0200 | d1_no | 22 | 6 | 28.0015 | -19.8% | -21.2% | 13.6% |
| all_state_best_expression_router | 0.0200 | d1_yes | 11 | 5 | 33.2591 | -38.6% | -39.2% | 9.1% |
| cross_continuation_current_no | 0.0200 | current_no | 36 | 14 | 26.2676 | -30.9% | -21.8% | 19.4% |
| cross_exhaustion_current_yes | 0.0200 | current_yes | 90 | 15 | 26.2765 | -40.3% | -52.0% | 11.1% |
| cross_next1_yes | 0.0200 | d1_yes | 20 | 5 | 34.2533 | -36.2% | -37.2% | 5.0% |

## 时间未突破条件化

| elapsed_bucket | rows | dates | empirical_break_rate | market_p_break | model_p_break | avg_current_yes_ask | avg_current_no_ask |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 120_240 | 296 | 16 | 73.3% | 73.8% | 70.4% | 0.2833 | 0.7461 |
| 30_60 | 598 | 16 | 82.8% | 82.5% | 78.0% | 0.1961 | 0.8387 |
| 60_120 | 437 | 16 | 76.7% | 79.1% | 75.0% | 0.2336 | 0.7997 |
| gt240 | 218 | 16 | 83.5% | 84.6% | 80.5% | 0.1708 | 0.8455 |
| le30 | 433 | 16 | 83.4% | 83.0% | 76.8% | 0.1896 | 0.8419 |

## 判断

1. P0 crossed-prev NO 单独报告，因为它是 source/settlement-basis 结构腿，不应和概率策略混成一个 ROI。
2. continuation=`current NO`、exhaustion/reversal=`current YES`、exact-next=`d1 YES`；全部由同一个 residual probability router 选择，不再用策略名先决定 side。
3. elapsed time 已作为 market-residual 特征进入 expanding model，但这仍是 snapshot 条件化，不是秒级连续 hazard。真正 post-cross 可交易速度必须靠新 forward collector。
4. source-basis reversal 只有在 crossed source 后来被 settlement-aligned source 否定时才成立；若当前分母没有 below-anchor outcome，就不能从零案例宣称没有机会。

## 8 环覆盖

- covered: signal discrimination, probability/proper score, executable top-book microstructure, fee-adjusted replay, date bootstrap, same-price-band baseline, forward-like expanding dates.
- partial: capacity only min top size 5; portfolio correlation only target-date bootstrap.
- missing: native local-first-seen second-level event clock, real fills/queue, fresh independent post-launch dates. Therefore no live verdict.
