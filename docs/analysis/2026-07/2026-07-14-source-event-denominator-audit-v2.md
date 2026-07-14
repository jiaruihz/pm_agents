# Source-Event Denominator Audit v2

> 2026-07-14; research-only; zero notional; supersedes treating v1's 42 rows as the full fast-source denominator.

## 数据快照

- canonical DB: `/Users/deepsleep/projects/pm_agents/runtime/weather.db`; mtime `2026-07-14T02:36:29.208461+00:00`.
- fact_signal_candidates `53834`; fact_trades `4571`; unsettled `0`; missing_bracket `0`.
- settlement_outcomes: `2026-05-04..2026-07-10` / `67` dates / `49` cities.
- strategy grain is PIT saved ladder state / first quote after a generic running-high bracket advance; it is not fill-grain realized PnL.

## 结论先行

用户对分母的质疑成立。v1 的 `42 rows` 是最终模型筛选结果，不是全部日期城市。
完整 raw hourly-last inventory 有 `68026` city-date-hours / `69` dates / `49` cities。但它不能直接除以 `8094`：后者是另一条 v3 reconstructed-PIT lineage，包含 `22` 个 settled dates 上的每个 decision snapshot，同一小时可有多行。
同窗 raw hourly-last 是 `23850`；v3 clean state 去重到同一 city/date/hour 后是 `4121`。策略实际使用未按小时去重的 `8094` decision snapshots，其中 generic running-high bracket advances 为 `518`。

更关键的是：v1 的 `cross_event` 只由 `previous_current_key != current_key` 定义，代码没有 join JMA/AMOS/HKO/MSS/MADIS，也没有使用 city×source profile。所以 v1 实际评估的是 generic observed running-high 跨档，不是快源领先策略。

把全部 `499` 个有真实 ask/depth 的 generic cross current-NO 都交易，fee-adjusted ROI `-2.7%`，date bootstrap CI `[-7.1%,0.9%]`；相对同价带 initial-anchor baseline 的 date-equal excess `-0.6%`，CI `[-3.7%,2.1%]`。没有全体事件 alpha。

旧 edge>=2c current-NO 只有 `42` rows，ROI `11.6%`、CI `[-11.4%,38.1%]`；这是一个事后由 expanding model 选出的稀疏子集，显著性和 baseline 均未通过，不能代表全部 source-event。

## 分母漏斗

| lineage | stage | rows | dates | cities | grain |
| --- | --- | --- | --- | --- | --- |
| raw saved-field audit | all-window hourly-last inventory | 68026 | 69 | 49 | last saved snapshot per city/date/local-hour |
| raw saved-field audit | same-window hourly-last inventory | 23850 | 22 | 47 | hourly-last inventory restricted to the v3 labeled date window |
| v3 reconstructed PIT | clean labeled decision snapshots | 8094 | 22 | 47 | every reconstructable decision snapshot; multiple rows per local hour |
| v3 reconstructed PIT | clean labeled unique city-date-hours | 4121 | 22 | 47 | v3 states deduplicated to city/date/local-hour for comparability |
| v3 reconstructed PIT | generic running-high bracket advances | 518 | 22 | 40 | current bracket changed from previous state; not source-specific |
| v3 reconstructed PIT | generic cross current-NO executable | 499 | 22 | 40 | all cross rows with ask 0.001..0.999 and ask_size>=5 |
| v3 reconstructed PIT | v1 model edge>=2c current-NO | 42 | 14 | 17 | final model-selected subset |

### 独立的 raw saved-field hourly-last 审计排除原因

| scope | state_status | rows | dates | cities |
| --- | --- | --- | --- | --- |
| inner_pre_2026_06_21 | missing_saved_running_max | 44358 | 47 | 49 |
| inner_pre_2026_06_21 | insufficient_saved_rungs_after_current | 485 | 4 | 48 |
| inner_pre_2026_06_21 | missing_quote_or_settlement_label | 280 | 3 | 38 |
| untouched_2026_06_21_plus | missing_saved_running_max | 8731 | 22 | 47 |
| untouched_2026_06_21_plus | insufficient_saved_rungs_after_current | 8668 | 21 | 47 |
| untouched_2026_06_21_plus | missing_quote_or_settlement_label | 4337 | 21 | 47 |
| untouched_2026_06_21_plus | ready_market_local | 1167 | 9 | 47 |

## 全部 generic cross 的双边交易结果

| expression | rows | dates | cities | avg_ask | win_rate | roi | roi_ci_low | roi_ci_high | same_band_initial_rows | same_band_initial_roi | date_equal_excess_roi | excess_ci_low | excess_ci_high | recent_4_dates | recent_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | 502 | 22 | 40 | 0.3582 | 31.7% | -12.7% | -19.3% | -4.6% | 945 | -5.5% | -12.5% | -25.4% | -3.1% | 2026-07-07,2026-07-08,2026-07-09,2026-07-10 | -15.3% |
| current_no | 499 | 22 | 40 | 0.6959 | 68.1% | -2.7% | -7.1% | 0.9% | 934 | -3.6% | -0.6% | -3.7% | 2.1% | 2026-07-07,2026-07-08,2026-07-09,2026-07-10 | 0.2% |
| d1_yes | 434 | 17 | 40 | 0.3021 | 24.7% | -20.4% | -27.8% | -13.1% | 855 | -22.3% | -2.5% | -14.4% | 8.7% | 2026-07-03,2026-07-04,2026-07-05,2026-07-06 | -39.2% |
| d1_no | 502 | 22 | 39 | 0.7684 | 74.5% | -3.9% | -6.7% | -0.7% | 939 | -1.4% | 0.9% | -2.3% | 4.7% | 2026-07-07,2026-07-08,2026-07-09,2026-07-10 | -0.1% |
| previous_no | 105 | 15 | 26 | 0.9989 | 100.0% | 0.1% | 0.1% | 0.1% | 114 | 0.1% | -0.0% | -0.1% | -0.0% | 2026-07-03,2026-07-04,2026-07-05,2026-07-06 | 0.1% |
| previous_yes | 131 | 15 | 29 | 0.0026 | 0.0% | -100.0% | -100.0% | -100.0% | 140 | -100.0% | 0.0% | 0.0% | 0.0% | 2026-07-03,2026-07-04,2026-07-05,2026-07-06 | -100.0% |

解释：current YES 和 d1 YES 在完整 cross 分母上显著为负；current NO 接近打平但 CI 跨 0，且相对同价带 baseline 没有正 excess；d1 NO 也是负收益。previous NO 约 0.1% 只是市场已把已跨过档位买到接近 1，并非可扩张 alpha。

## 真正快源历史覆盖

- profile-matched fast observations: raw repolls `201291`，unique observations `19080`.
- window `2026-07-07..2026-07-14` / `8` dates / `18` cities.
- 与当前已结算 label 重叠只有 `4` dates / `61` city-dates（`2026-07-07..2026-07-10`）。

因此两个月盘口历史并不等于两个月 fast-source 历史。5/05 起确实有盘口，但 profile-matched 高频文件从 7/08 UTC 才开始持续保存；按城市本地 target_date 归属后最早可落到 7/07。不能用 generic METAR/WU 跨档冒充 fast-source alpha，也不能把缺失的早期快源事后补造成 PIT 数据。

## Three Gates

- significance=FAIL：全量 current-NO CI 跨 0；其余主要表达为负。
- baseline=FAIL：current-NO date-equal excess 点估为负且 CI 跨 0。
- forward=FAIL：真正 profile-matched fast-source 只有少量已结算日期，尚无独立 forward 窗。
- conclusion=`inconclusive`；保留 zero-notional side-neutral collector，不批准 5-share live。

## 8 环覆盖

- covered: 描述性双边结果、date bootstrap、真实 ask/depth、official taker fee、同价带 baseline、日期/城市漏斗。
- partial: capacity 只验证 top ask size>=5；组合相关性只按 target_date block。
- missing: 两个月 profile-matched fast-source PIT 历史、独立 settled forward、真实 fill/latency/queue。
