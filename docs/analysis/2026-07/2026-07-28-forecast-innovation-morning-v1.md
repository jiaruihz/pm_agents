# Forecast innovation：凌晨/早晨观测校准 Tmax v1

## 数据快照

- 数据源：`/Users/deepsleep/projects/pm_agents/runtime/weather.db` canonical `tmax_v2_*` + `settlement_outcomes(pm_history)`。
- 覆盖：2026-07-11..2026-07-27；17 个 target dates，40 城。
- canonical states=281,269；PIT+normalized curve states=64,630；checkpoint rows=2,298。
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
| canonical raw state | city-date-snapshot | 281269 | 20 |
| PIT observation+curve | city-date-snapshot | 64630 | 14 |
| first checkpoint state | city-date-checkpoint | 2375 | 13 |
| settled + common model fields | city-date-checkpoint | 2253 | 17 |

## Evidence funnel

| 层 | grain | rows | dates | gap |
|---|---|---:|---:|---|
| PIT feature/source | city-date-checkpoint | 2298 | 17 | none after selected checkpoint |
| continuous settlement midpoint | city-date-checkpoint | 2253 | 17 | open top/bottom winners excluded from MAE |
| expanding OOF score | city-date-checkpoint | 1544 | 12 | first 5 dates train-only |
| PIT full two-sided ladder | city-date-checkpoint | 109 | NA | archive/book coverage gap |
| executable expression | expression | 0 | 0 | not evaluated |
| fill | fill | 0 | 0 | feature study, not fill study |

## Wide-denominator sanity

| checkpoint_hour_local | rows | dates | innovation_final_error_corr | mean_innovation_f |
| --- | --- | --- | --- | --- |
| 3 | 616.0000 | 17.0000 | 0.0838 | 1.4292 |
| 6 | 563.0000 | 17.0000 | 0.0572 | 0.8477 |
| 9 | 539.0000 | 17.0000 | 0.4907 | -0.0584 |
| 12 | 535.0000 | 17.0000 | 0.6820 | -0.7428 |

03:00/06:00 的弱相关与 09:00/12:00 的增强是主结果：夜间 boundary-layer/station-grid bias 并不自动延续到白天 Tmax；日出后的 path innovation 才更接近“当天升温轨迹整体偏离模型”。

## Expanding walk-forward

| checkpoint_hour_local | variant | rows | dates | mae_f | rmse_f | mae_delta_vs_rolling_bias_f | delta_ci_low_f | delta_ci_high_f |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3 | rolling_bias | 449 | 12 | 2.0701 | 2.7552 | NA | NA | NA |
| 3 | innovation | 449 | 12 | 2.0737 | 2.7516 | 0.0037 | -0.0311 | 0.0392 |
| 3 | innovation_regime | 449 | 12 | 2.0828 | 2.7780 | 0.0127 | -0.0295 | 0.0617 |
| 6 | rolling_bias | 387 | 12 | 2.1009 | 2.8068 | NA | NA | NA |
| 6 | innovation | 387 | 12 | 2.0903 | 2.7937 | -0.0106 | -0.0221 | 0.0020 |
| 6 | innovation_regime | 387 | 12 | 2.1022 | 2.8268 | 0.0013 | -0.0400 | 0.0566 |
| 9 | rolling_bias | 365 | 12 | 2.0254 | 2.5899 | NA | NA | NA |
| 9 | innovation | 365 | 12 | 1.8490 | 2.3802 | -0.1764 | -0.2390 | -0.0759 |
| 9 | innovation_regime | 365 | 12 | 1.8748 | 2.3938 | -0.1506 | -0.2259 | -0.0332 |
| 12 | rolling_bias | 343 | 12 | 1.9546 | 2.5208 | NA | NA | NA |
| 12 | innovation | 343 | 12 | 1.5661 | 1.9733 | -0.3885 | -0.4668 | -0.3071 |
| 12 | innovation_regime | 343 | 12 | 1.6294 | 2.0659 | -0.3252 | -0.4218 | -0.2462 |

Primary 09:00 rows：

| variant | rows | dates | mae_f | mae_delta_vs_rolling_bias_f | delta_ci_low_f | delta_ci_high_f |
| --- | --- | --- | --- | --- | --- | --- |
| rolling_bias | 365 | 12 | 2.0254 | NA | NA | NA |
| innovation | 365 | 12 | 1.8490 | -0.1764 | -0.2390 | -0.0759 |
| innovation_regime | 365 | 12 | 1.8748 | -0.1506 | -0.2259 | -0.0332 |

## β stability

| checkpoint_hour_local | target_date | train_dates | beta_innovation | beta_innovation_regime |
| --- | --- | --- | --- | --- |
| 6 | 2026-07-27 | 16 | 0.0979 | 0.0720 |
| 3 | 2026-07-27 | 16 | 0.1672 | 0.2532 |
| 9 | 2026-07-27 | 16 | 0.4507 | 0.4184 |
| 12 | 2026-07-27 | 16 | 0.5828 | 0.5873 |

β 是概率模型参数，不写死在数据层。共享层只保存 raw innovation、model current、remaining warming 与 PIT lineage；不同策略在自己的 frozen OOF 模型里学习 β。

## Region diagnostic（09:00，不作 allowlist）

| region | rows | dates | cities | rolling_bias_mae_f | innovation_mae_f | mae_delta_f | delta_ci_low_f | delta_ci_high_f |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AF | 11 | 11 | 1 | 1.6392 | 1.6993 | 0.0601 | -0.3882 | 0.4623 |
| AS | 152 | 12 | 13 | 2.1015 | 1.7495 | -0.3520 | -0.4717 | -0.2244 |
| EU | 106 | 12 | 9 | 1.9470 | 1.8843 | -0.0627 | -0.2537 | 0.1772 |
| ME | 17 | 12 | 2 | 1.7889 | 1.6986 | -0.0904 | -0.6071 | 0.2662 |
| OC | 9 | 9 | 1 | 0.8044 | 1.5581 | 0.7538 | -0.0578 | 1.5918 |
| SA | 15 | 5 | 3 | 2.0978 | 1.9974 | -0.1004 | -0.3460 | 0.1583 |
| US | 55 | 5 | 11 | 2.2966 | 2.1398 | -0.1568 | -0.2478 | -0.0566 |

该切片只解释传递机制，不作为事后城市/region gate。

## Market / execution coverage

| checkpoint_hour_local | checkpoint_rows | dates | full_two_sided_ladders | full_direct_yes_ask_ladders |
| --- | --- | --- | --- | --- |
| 3 | 628 | 17 | 25 | 130 |
| 6 | 574 | 17 | 11 | 109 |
| 9 | 550 | 17 | 44 | 124 |
| 12 | 546 | 17 | 29 | 96 |

盘口缺失属于 evidence gap，不是策略筛选。当前不能把较少的完整 book 行包装成“精选可交易样本”，也不能从 weather MAE 改善外推 ROI。

### 完整 two-sided ladder 上的诊断性 proper score

| checkpoint_hour_local | variant | rows | dates | cities | date_equal_logloss | date_equal_brier |
| --- | --- | --- | --- | --- | --- | --- |
| 3 | fusion_innovation | 21 | 3 | 20 | 1.3497 | 0.0892 |
| 3 | fusion_rolling_bias | 21 | 3 | 20 | 1.3569 | 0.0898 |
| 3 | market | 21 | 3 | 20 | 1.3152 | 0.0861 |
| 3 | weather_innovation | 21 | 3 | 20 | 1.9236 | 0.1157 |
| 3 | weather_rolling_bias | 21 | 3 | 20 | 1.9733 | 0.1171 |
| 6 | fusion_innovation | 9 | 4 | 9 | 1.3053 | 0.0903 |
| 6 | fusion_rolling_bias | 9 | 4 | 9 | 1.3050 | 0.0904 |
| 6 | market | 9 | 4 | 9 | 1.2299 | 0.0890 |
| 6 | weather_innovation | 9 | 4 | 9 | 1.7546 | 0.1076 |
| 6 | weather_rolling_bias | 9 | 4 | 9 | 1.7474 | 0.1073 |
| 9 | fusion_innovation | 29 | 3 | 27 | 1.2106 | 0.0844 |
| 9 | fusion_rolling_bias | 29 | 3 | 27 | 1.2188 | 0.0849 |
| 9 | market | 29 | 3 | 27 | 1.1779 | 0.0841 |
| 9 | weather_innovation | 29 | 3 | 27 | 1.7219 | 0.1139 |
| 9 | weather_rolling_bias | 29 | 3 | 27 | 1.8060 | 0.1174 |
| 12 | fusion_innovation | 24 | 3 | 17 | 1.0451 | 0.1038 |
| 12 | fusion_rolling_bias | 24 | 3 | 17 | 1.0521 | 0.1050 |
| 12 | market | 24 | 3 | 17 | 1.0107 | 0.1030 |
| 12 | weather_innovation | 24 | 3 | 17 | 1.5227 | 0.1367 |
| 12 | weather_rolling_bias | 24 | 3 | 17 | 1.6611 | 0.1471 |

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
