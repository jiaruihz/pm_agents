# Current-YES Carry 连续 residual / 入场时机研究 v2

Status: `exploratory_frozen_forward_challenger / taker-only / no-live-change`
Generated: `2026-07-23T16:08:54+00:00`

## 真正的新结论

这轮不是再拼一个天气条件漏斗，而是在相同 PIT states 上让 market 做基准，再逐组加入物理机制。结果是一个可继续 forward 的连续候选，而不是 CC/H1 的硬合并：

- 候选 `core`：`market midpoint + local/forecast peak clock + dewpoint depression + wind speed + observation age`。价格只通过概率基准和 taker cost 进入，不设 `ask>=0.95`，也不设 support count。
- 旧 `minutes_since_running_max`/decline/trend path 组加入后变差。根因不是“回落无用”，而是历史时钟会在相同高点再次出现时重置，不能表达 strict new high age；在语义修正前不能拿它当确认机制。
- CC 的 debiased ceiling/route 再加入也没有改善，因此不合并进 challenger。
- 最好的历史执行时机是首次 `OOF p_hold > fresh taker effective cost`；二次确认和等下一小时都更贵，没有提升结果。

## Headline（探索性，不是 live 结论）

`core` 在 carry expression domain（market midpoint≥0.80）首个正 EV：148 city-days / 30 dates / 29 cities，win +95.27%，avg ask 0.908，fee-adjusted taker ROI +4.47%，date-block 95% CI [+0.65%, +7.91%]。
前/后半目标日 ROI：+3.75% / +5.52%；leave-one-date-out [+4.04%, +5.18%]。
Proper score 相对同训练过程 market calibration：Brier Δ -0.001050 CI[-0.002885, 0.000641]；logloss Δ -0.005889 CI[-0.012054, 0.000056]。负值代表改善，但 CI 尚未在两项都完全小于 0。

## 机制消融

| model | features added after market | Brier | logloss | ΔBrier vs market-cal | Δlogloss vs market-cal | first-EV n | avg ask | taker ROI [95%CI] |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| market_cal | calibration only | 0.0625 | 0.2334 | — | — | 0 | NA | NA [NA, NA] |
| market_plus_clock | decision_hour_local, forecast_peak_delta_hours_local | 0.0619 | 0.2317 | -0.000576 | -0.001692 | 65 | 0.907 | -0.39% [-9.55%, +7.21%] |
| core | decision_hour_local, forecast_peak_delta_hours_local, dewpoint_depression_f, wind_speed_kt, obs_age_min | 0.0615 | 0.2275 | -0.001050 | -0.005889 | 148 | 0.908 | +4.47% [+0.65%, +7.91%] |
| core_no_obs_age | decision_hour_local, forecast_peak_delta_hours_local, dewpoint_depression_f, wind_speed_kt | 0.0614 | 0.2277 | -0.001130 | -0.005691 | 139 | 0.909 | +4.83% [+1.06%, +8.12%] |
| core_no_clock | dewpoint_depression_f, wind_speed_kt, obs_age_min | 0.0618 | 0.2297 | -0.000684 | -0.003730 | 103 | 0.903 | +4.92% [+1.15%, +8.40%] |
| core_no_wind | decision_hour_local, forecast_peak_delta_hours_local, dewpoint_depression_f, obs_age_min | 0.0617 | 0.2302 | -0.000814 | -0.003224 | 88 | 0.912 | +1.70% [-4.71%, +6.83%] |
| no_path | decision_hour_local, forecast_peak_delta_hours_local, relative_humidity_pct, sky_cover_code, dewpoint_depression_f, wind_speed_kt, obs_age_min | 0.0617 | 0.2274 | -0.000868 | -0.006019 | 154 | 0.910 | +4.42% [+1.20%, +7.56%] |
| path_added | decision_hour_local, forecast_peak_delta_hours_local, relative_humidity_pct, sky_cover_code, dewpoint_depression_f, wind_speed_kt, obs_age_min, decline_steps, minutes_since_running_max_log, temp_trend_1h_f, temp_trend_3h_f | 0.0625 | 0.2299 | -0.000069 | -0.003477 | 185 | 0.919 | +1.96% [-1.78%, +5.48%] |
| cc_added | decision_hour_local, forecast_peak_delta_hours_local, relative_humidity_pct, sky_cover_code, dewpoint_depression_f, wind_speed_kt, obs_age_min, decline_steps, minutes_since_running_max_log, temp_trend_1h_f, temp_trend_3h_f, gap_debiased_steps, bias_known_num, path_faded_num, route_num | 0.0626 | 0.2310 | 0.000096 | -0.002342 | 230 | 0.925 | +2.58% [-0.29%, +5.23%] |

单一机制组并没有各自稳定赚钱；`core` 的价值来自 market 条件下的连续交互，不能翻译成clock AND wind AND dewpoint 三道门。`core`/`no_path` 是查看消融后选出的 exploratory candidates，因此即使交易 ROI CI 为正，也必须先冻结再 forward，不能把 post-selection bootstrap 当独立显著性。

## 入场时机（core，同 city-day / 同 bracket 配对）

| policy | city-days | avg ask | ROI | paired n | paired ask Δ | paired ROI Δ vs first |
|---|---:|---:|---:|---:|---:|---:|
| first_positive_taker_ev | 148 | 0.908 | +4.47% | — | NA | NA |
| two_consecutive_positive_ev | 54 | 0.946 | +3.44% | 54 | 0.0446 | -4.88% |
| fixed_next_retained_hour_same_bracket | 89 | 0.944 | +3.33% | 89 | 0.0461 | -5.05% |

这回答的是入场方向，不是分钟级最优点：历史 archive 每城每小时约一帧。当前可执行定义先冻结为“首个正 taker EV checkpoint”，不等待赔率升到 0.95，也不等待第二份确认。

## 真实 taker 容量

| qty | raw book match | full executable | avg VWAP | effective cost/share | slippage vs best ask | VWAP ROI |
|---:|---:|---:|---:|---:|---:|---:|
| 5 | +100.00% | +100.00% | 0.9083 | 0.9123 | 0.041c | +4.43% |
| 10 | +100.00% | +100.00% | 0.9088 | 0.9128 | 0.091c | +4.37% |

Maker 暂不计收益：这版先用真实 ask ladder 把 taker 分母钉死，maker 只保留后续同 signal 反事实空间。

## 为什么现在还不能改 live

新的 transition-aware forward ledger 研究窗口只有 2 个 target date (2026-07-22, 2026-07-23)、517 个 state。其中 direct current-YES ask 152/517，strict-new-high clock 0/517。这是 evidence coverage gap，不是 signal 被策略筛掉。

因此当前动作是：冻结 `core` challenger，zero-notional 连续采集完整 direct quote + strict-high clock + settlement；旧 H1/H2 live 不因本报告自动改变。待独立 forward 后同时复核 proper score、taker ROI、入场 timing 和archive/live parity，再决定是否替换。

## 产物

- Script: `scripts/analysis/reheat_risk/research_current_yes_carry_residual_entry_v2.py`
- JSON: `docs/analysis/2026-07/2026-07-22-current-yes-carry-residual-entry-v2.json`
- OOF states: `docs/analysis/2026-07/generated/current_yes_carry_residual_entry_v2/oof_state_predictions.csv`
- Core entries: `docs/analysis/2026-07/generated/current_yes_carry_residual_entry_v2/entries_core.csv`
- Core taker ladder: `docs/analysis/2026-07/generated/current_yes_carry_residual_entry_v2/core_taker_ladder.csv`
