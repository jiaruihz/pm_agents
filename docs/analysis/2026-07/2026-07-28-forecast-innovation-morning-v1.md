# Forecast innovation：凌晨/早晨观测校准 Tmax v1

## 数据快照

- 数据源：`/Users/deepsleep/projects/pm_agents/runtime/weather.db` canonical `tmax_v2_*` + `settlement_outcomes(pm_history)`。
- 覆盖：2026-07-11..2026-07-23；13 个 target dates，40 城。
- canonical states=235,248；PIT+normalized curve states=47,269；checkpoint rows=1,762。
- unsettled=0；missing_bracket=0（feature study 只保留已有 pm_history winner 的 city-day）。
- 2026-07-24..27 raw 已同步，但追加物化因全量扫描约 8GB 且本机可用空间仅约 7.7GB而安全中止；本报告冻结到 7/23。

## 结论与动作

这个特征应进入共享 weather state，但不能把“凌晨偏暖”直接当成 Tmax 上移。
在当前严格 PIT 样本里，03:00/06:00 innovation 与最终 forecast error 的相关性很弱；09:00 开始明显，12:00 更强。交易含义是：保留连续 innovation，让概率模型按 local clock/机制学习 β；不要把它做成凌晨固定加减 1°C 的 hard rule。

本轮只证明天气层的增量/失效时段。早晨完整 two-sided ladder 覆盖不足，无法据此发布 fee-adjusted ROI 或宣称相对 market 的 alpha；下一步是 forward collector 在固定 morning checkpoint 双写 feature + full book。

## Target

```text
innovation_t = observed_temp_f - interpolated_model_temp_f(t)
Tmax_hat = model_Tmax + rolling_city/source_bias + beta(checkpoint, regime) * innovation_t
```

- primary checkpoint：本地 09:00，snapshot 容差 0..45 分钟；03/06/12 点为预注册时钟对照。
- label：最终 winning exact bracket 的 native midpoint；top/bottom open bracket 不用于连续 MAE，但仍保留在 coverage 层。
- primary metric：同 rows expanding walk-forward MAE；按 target_date block bootstrap。
- variants：raw forecast、rolling bias、rolling bias+innovation、rolling bias+innovation×forecast cloud/wind/precip。
- fixed ridge alpha=10；每个测试日只用更早 target dates，至少 5 个训练日。

## Data integrity / PIT

- 每行 `pit_status=pit_verified`；observation 与 forecast capture 的 available_at 均不晚于 decision timestamp。
- checkpoint 由每个 market 的 IANA `market_timezone` 从 UTC 独立换算；timezone missing=0、IANA/metadata offset mismatch=0、local target-date mismatch=0。
- 模型当前温度用 curve 对真实 decision local minute 线性插值；没有使用旧 `tracking_residual_f` 的整点向下取整。
- 同一 checkpoint 的四个模型共用完全相同的 city-day rows。
- 这是 retrospective expanding OOF，不是从未查看过的 fresh frozen forward；forward gate 仍为 NA。

## Signal funnel

| 层 | grain | rows | dates |
|---|---|---:|---:|
| canonical raw state | city-date-snapshot | 235248 | 20 |
| PIT observation+curve | city-date-snapshot | 47269 | 14 |
| first checkpoint state | city-date-checkpoint | 1762 | 13 |
| settled + common model fields | city-date-checkpoint | 1728 | 13 |

## Evidence funnel

| 层 | grain | rows | dates | gap |
|---|---|---:|---:|---|
| PIT feature/source | city-date-checkpoint | 1762 | 13 | none after selected checkpoint |
| continuous settlement midpoint | city-date-checkpoint | 1728 | 13 | open top/bottom winners excluded from MAE |
| expanding OOF score | city-date-checkpoint | 1019 | 8 | first 5 dates train-only |
| PIT full two-sided ladder | city-date-checkpoint | 109 | NA | archive/book coverage gap |
| executable expression | expression | 0 | 0 | not evaluated |
| fill | fill | 0 | 0 | feature study, not fill study |

## Wide-denominator sanity

| checkpoint_hour_local | rows | dates | innovation_final_error_corr | mean_innovation_f |
| --- | --- | --- | --- | --- |
| 3 | 460.0000 | 13.0000 | 0.1332 | 1.4130 |
| 6 | 432.0000 | 13.0000 | 0.0452 | 0.7119 |
| 9 | 413.0000 | 13.0000 | 0.4921 | -0.0259 |
| 12 | 423.0000 | 13.0000 | 0.6825 | -0.7703 |

03:00/06:00 的弱相关与 09:00/12:00 的增强是主结果：夜间 boundary-layer/station-grid bias 并不自动延续到白天 Tmax；日出后的 path innovation 才更接近“当天升温轨迹整体偏离模型”。

## Expanding walk-forward

| checkpoint_hour_local | variant | rows | dates | mae_f | rmse_f | mae_delta_vs_rolling_bias_f | delta_ci_low_f | delta_ci_high_f |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3 | rolling_bias | 293 | 8 | 2.0933 | 2.8139 | NA | NA | NA |
| 3 | innovation | 293 | 8 | 2.0708 | 2.7866 | -0.0225 | -0.0648 | 0.0214 |
| 3 | innovation_regime | 293 | 8 | 2.0585 | 2.7966 | -0.0348 | -0.0646 | 0.0079 |
| 6 | rolling_bias | 256 | 8 | 2.0438 | 2.7342 | NA | NA | NA |
| 6 | innovation | 256 | 8 | 2.0379 | 2.7223 | -0.0059 | -0.0180 | 0.0074 |
| 6 | innovation_regime | 256 | 8 | 2.0329 | 2.7542 | -0.0109 | -0.0631 | 0.0730 |
| 9 | rolling_bias | 239 | 8 | 2.0015 | 2.5808 | NA | NA | NA |
| 9 | innovation | 239 | 8 | 1.8268 | 2.3570 | -0.1746 | -0.2782 | -0.0269 |
| 9 | innovation_regime | 239 | 8 | 1.8400 | 2.3586 | -0.1615 | -0.2705 | -0.0046 |
| 12 | rolling_bias | 231 | 8 | 1.9124 | 2.5171 | NA | NA | NA |
| 12 | innovation | 231 | 8 | 1.5574 | 1.9895 | -0.3550 | -0.4557 | -0.2544 |
| 12 | innovation_regime | 231 | 8 | 1.6521 | 2.0818 | -0.2603 | -0.3790 | -0.1673 |

Primary 09:00 rows：

| variant | rows | dates | mae_f | mae_delta_vs_rolling_bias_f | delta_ci_low_f | delta_ci_high_f |
| --- | --- | --- | --- | --- | --- | --- |
| rolling_bias | 239 | 8 | 2.0015 | NA | NA | NA |
| innovation | 239 | 8 | 1.8268 | -0.1746 | -0.2782 | -0.0269 |
| innovation_regime | 239 | 8 | 1.8400 | -0.1615 | -0.2705 | -0.0046 |

## β stability

| checkpoint_hour_local | target_date | train_dates | beta_innovation | beta_innovation_regime |
| --- | --- | --- | --- | --- |
| 6 | 2026-07-23 | 12 | 0.0761 | 0.0648 |
| 3 | 2026-07-23 | 12 | 0.2111 | 0.2980 |
| 9 | 2026-07-23 | 12 | 0.4941 | 0.4368 |
| 12 | 2026-07-23 | 12 | 0.5642 | 0.5723 |

β 是概率模型参数，不写死在数据层。共享层只保存 raw innovation、model current、remaining warming 与 PIT lineage；不同策略在自己的 frozen OOF 模型里学习 β。

## Region diagnostic（09:00，不作 allowlist）

| region | rows | dates | cities | rolling_bias_mae_f | innovation_mae_f | mae_delta_f | delta_ci_low_f | delta_ci_high_f |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AF | 8 | 8 | 1 | 1.5450 | 1.5146 | -0.0304 | -0.5418 | 0.3294 |
| AS | 102 | 8 | 13 | 2.1199 | 1.7338 | -0.3861 | -0.5790 | -0.2119 |
| EU | 70 | 8 | 9 | 1.8858 | 1.8867 | 0.0009 | -0.2798 | 0.3175 |
| ME | 12 | 8 | 2 | 1.9442 | 2.0561 | 0.1119 | -0.2521 | 0.4592 |
| OC | 5 | 5 | 1 | 0.7037 | 1.2601 | 0.5564 | -0.4739 | 1.6810 |
| SA | 9 | 3 | 3 | 1.6983 | 1.4164 | -0.2819 | -0.5106 | -0.0351 |
| US | 33 | 3 | 11 | 2.2913 | 2.1774 | -0.1140 | -0.2770 | 0.0363 |

该切片只解释传递机制，不作为事后城市/region gate。

## Market / execution coverage

| checkpoint_hour_local | checkpoint_rows | dates | full_two_sided_ladders | full_direct_yes_ask_ladders |
| --- | --- | --- | --- | --- |
| 3 | 469 | 13 | 25 | 72 |
| 6 | 440 | 13 | 11 | 62 |
| 9 | 421 | 13 | 44 | 90 |
| 12 | 432 | 13 | 29 | 60 |

盘口缺失属于 evidence gap，不是策略筛选。当前不能把较少的完整 book 行包装成“精选可交易样本”，也不能从 weather MAE 改善外推 ROI。

### 完整 two-sided ladder 上的诊断性 proper score

| checkpoint_hour_local | variant | rows | dates | cities | date_equal_logloss | date_equal_brier |
| --- | --- | --- | --- | --- | --- | --- |
| 3 | fusion_innovation | 21 | 3 | 20 | 1.3504 | 0.0892 |
| 3 | fusion_rolling_bias | 21 | 3 | 20 | 1.3582 | 0.0897 |
| 3 | market | 21 | 3 | 20 | 1.3152 | 0.0861 |
| 3 | weather_innovation | 21 | 3 | 20 | 1.9155 | 0.1143 |
| 3 | weather_rolling_bias | 21 | 3 | 20 | 1.9639 | 0.1153 |
| 6 | fusion_innovation | 9 | 4 | 9 | 1.2807 | 0.0877 |
| 6 | fusion_rolling_bias | 9 | 4 | 9 | 1.2798 | 0.0877 |
| 6 | market | 9 | 4 | 9 | 1.2299 | 0.0890 |
| 6 | weather_innovation | 9 | 4 | 9 | 1.6726 | 0.0990 |
| 6 | weather_rolling_bias | 9 | 4 | 9 | 1.6646 | 0.0986 |
| 9 | fusion_innovation | 29 | 3 | 27 | 1.2075 | 0.0843 |
| 9 | fusion_rolling_bias | 29 | 3 | 27 | 1.2163 | 0.0849 |
| 9 | market | 29 | 3 | 27 | 1.1779 | 0.0841 |
| 9 | weather_innovation | 29 | 3 | 27 | 1.7233 | 0.1142 |
| 9 | weather_rolling_bias | 29 | 3 | 27 | 1.8203 | 0.1182 |
| 12 | fusion_innovation | 24 | 3 | 17 | 1.0425 | 0.1035 |
| 12 | fusion_rolling_bias | 24 | 3 | 17 | 1.0466 | 0.1042 |
| 12 | market | 24 | 3 | 17 | 1.0107 | 0.1030 |
| 12 | weather_innovation | 24 | 3 | 17 | 1.4815 | 0.1335 |
| 12 | weather_rolling_bias | 24 | 3 | 17 | 1.5485 | 0.1400 |

Weather distribution 使用固定 σ=3.0°F；fusion 为固定 log-linear market/weather=0.7/0.3，没有按结果调权重。该表是 coverage-limited diagnostic，不满足 market baseline 晋升门。

## Frozen forward

- train choices frozen：本报告之后冻结 `09:00 primary + continuous innovation + clock interaction`，不冻结 β 数值。
- fresh forward：尚未开始；需要 collector 继续积累 checkpoint feature + full book。
- multiple testing：4 个 checkpoint、3 个 correction variants；未据此选城市或价格门。
- unresolved blocker：morning full-ladder PIT book 覆盖与 fresh-forward duration。

## 8 环覆盖

- 已覆盖：2 统计推断（date block）、3 信号判别（forecast error/MAE）。
- 部分覆盖：4 概率层、8 同时点 market residual 仅有 3--4 个日期的完整 two-sided ladder diagnostic，market baseline 未过。
- 未覆盖：1 fill 绩效、5 执行、6 容量、7 portfolio。

```text
significance=PASS/FAIL 以 scorecard CI 为准
baseline=FAIL（有限完整盘口样本上 fusion 未打败 market）
forward=NA（无 fresh frozen forward）
conclusion=inconclusive / shared_feature_candidate
```

## Bloodline placement

- shared data logic：`weather_data_feed.physical_features` 输出连续 forecast innovation。
- feature layer：`weather_state_v4` additive field，不改变任何现有 selector eligibility。
- probability heads：pre_predict、Tmax distribution、D-1 extreme NO、current/reheat 策略都可消费；β 与 probability calibration 各自 OOF 拟合。
- `fact_signal_candidates`：未来 v2 checkpoint grain 写 raw innovation 与 feature ref，不新建平行 fact。
- live action：none。
