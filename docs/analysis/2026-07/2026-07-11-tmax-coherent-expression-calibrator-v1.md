# Tmax Coherent Expression Calibrator v1

## 数据快照

| 字段 | 值 |
| --- | --- |
| 数据源 | P4/atlas PIT model rows；DB 仅作数据新鲜度自检 |
| 数据快照时间 | 2026-07-10T16:18:41+00:00 |
| 记录行数 | raw 14368；scored 8376；forward states 2077 |
| unsettled 占比 | 0% in scored denominator |
| missing_bracket 数 | 0 in scored denominator |
| 日期 | 2026-05-19..2026-07-08 |
| refresh 状态 | Mac market snapshot 已同步到 7/10；run_stack 在 strategy runtime order migration 失败，未引用本轮 partial rebuild 的 live PnL |

## 结论

- 新模型 primary 是 `coherent_cal_quote`。它不是另起一套天气预测，而是在完整四桶分布上做 coherent second-stage calibration；YES/NO 始终互补。
- 三个候选只在 6/21 前做 date-equal nested selection：global 只校准四桶基准概率；context 加紧凑 PIT 天气状态；quote 加 exact-book 可执行报价几何，均无 city/source 自由参数。
- 相对 `historical_full_features` 的 fee-adjusted first-lock ROI delta 为 +3.1%，95% CI [+0.6%, +5.7%]。
- 四桶 date-equal logloss delta 为 -0.0020，95% CI [-0.0045, -0.0003]；16 天里 10 天 PnL delta 为正。
- 改善不是靠恢复上一轮事后赚钱的 36 个 cancelled d1 NO：primary 只恢复 2 个。它主要来自 quote-aware 新机会、同表达概率重排和移除少量坏机会。
- `d1_yes` selected ROI 仍为负，说明 winner-selection 条件校准尚未完成；这里只记录为 residual risk，不据此追加 hard gate。
- 三道门：significance=PASS；baseline=PASS；forward=FAIL_research_window_already_observed；conclusion=shadow_candidate。
- 这段 6/21+ 数据已经参与模型问题诊断，即使点估改善也不能据此恢复 live；只允许进入 zero-notional shadow。

## 漏斗

- atlas raw: 14368
- scored PIT states: 8376 / 49 dates
- base OOF meta rows: 7539 / 44 dates
- verified-forward: 2077 states / 16 dates
- policy grain: first eligible expression per city + target_date；fee=official weather taker curve rounded to 5 decimals；ask=decision-snapshot historical expression ask proxy

## Pre-cutoff Model Selection

| spec | c | alpha | rows | dates | date_equal_logloss | date_equal_brier |
| --- | --- | --- | --- | --- | --- | --- |
| coherent_cal_quote | 0.3000 | 0.2500 | 3938 | 20 | 0.6078 | 0.3461 |
| coherent_cal_quote | 0.1000 | 0.2500 | 3938 | 20 | 0.6079 | 0.3461 |
| coherent_cal_quote | 0.3000 | 0.5000 | 3938 | 20 | 0.6082 | 0.3468 |
| coherent_cal_quote | 0.0300 | 0.2500 | 3938 | 20 | 0.6084 | 0.3460 |
| coherent_cal_global | 0.3000 | 0.2500 | 3938 | 20 | 0.6084 | 0.3459 |
| coherent_cal_global | 0.1000 | 0.2500 | 3938 | 20 | 0.6085 | 0.3460 |
| coherent_cal_quote | 0.1000 | 0.5000 | 3938 | 20 | 0.6086 | 0.3468 |
| coherent_cal_global | 0.3000 | 0.5000 | 3938 | 20 | 0.6088 | 0.3465 |
| coherent_cal_context | 0.1000 | 0.2500 | 3938 | 20 | 0.6091 | 0.3463 |
| coherent_cal_global | 0.0300 | 0.2500 | 3938 | 20 | 0.6092 | 0.3462 |
| coherent_cal_global | 0.1000 | 0.5000 | 3938 | 20 | 0.6092 | 0.3468 |
| coherent_cal_context | 0.0300 | 0.2500 | 3938 | 20 | 0.6092 | 0.3464 |

## Forward Probability Score

| variant | rows | dates | logloss | brier |
| --- | --- | --- | --- | --- |
| coherent_cal_context | 2077 | 16 | 0.5893 | 0.3293 |
| coherent_cal_global | 2077 | 16 | 0.5879 | 0.3283 |
| coherent_cal_quote | 2077 | 16 | 0.5875 | 0.3277 |
| historical_full_features | 2077 | 16 | 0.5887 | 0.3283 |
| live_missingness_emulation | 2077 | 16 | 0.5926 | 0.3308 |

## Forward Expression Probability

| variant | expression | rows | dates | base_rate | avg_p | logloss | brier |
| --- | --- | --- | --- | --- | --- | --- | --- |
| coherent_cal_quote | current_no | 2077 | 16 | 0.5200 | 0.5119 | 0.2534 | 0.0779 |
| coherent_cal_quote | d1_no | 2077 | 16 | 0.7747 | 0.7752 | 0.3548 | 0.1150 |
| coherent_cal_quote | d2_no | 2077 | 16 | 0.8488 | 0.8522 | 0.2815 | 0.0896 |
| coherent_cal_quote | d1_yes | 2077 | 16 | 0.2253 | 0.2248 | 0.3548 | 0.1150 |
| coherent_cal_quote | d2_yes | 2077 | 16 | 0.1512 | 0.1478 | 0.2815 | 0.0896 |
| historical_full_features | current_no | 2077 | 16 | 0.5200 | 0.5118 | 0.2542 | 0.0781 |
| historical_full_features | d1_no | 2077 | 16 | 0.7747 | 0.7747 | 0.3552 | 0.1152 |
| historical_full_features | d2_no | 2077 | 16 | 0.8488 | 0.8520 | 0.2814 | 0.0897 |
| historical_full_features | d1_yes | 2077 | 16 | 0.2253 | 0.2253 | 0.3552 | 0.1152 |
| historical_full_features | d2_yes | 2077 | 16 | 0.1512 | 0.1480 | 0.2814 | 0.0897 |

## Forward First-lock Policy

| variant | rows | dates | cities | win_rate | avg_ask | pnl | roi | roi_ci_low | roi_ci_high | yes_rows | no_rows |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coherent_cal_context | 190 | 16 | 36 | 0.7053 | 0.6259 | 13.0697 | 0.1081 | 0.0095 | 0.1959 | 40 | 150 |
| coherent_cal_global | 173 | 16 | 36 | 0.7168 | 0.6396 | 11.5507 | 0.1027 | 0.0173 | 0.1739 | 37 | 136 |
| coherent_cal_quote | 167 | 16 | 36 | 0.7186 | 0.6357 | 12.0806 | 0.1119 | 0.0184 | 0.1932 | 38 | 129 |
| historical_full_features | 159 | 16 | 36 | 0.6918 | 0.6296 | 8.2243 | 0.0808 | -0.0161 | 0.1652 | 34 | 125 |
| live_missingness_emulation | 172 | 16 | 36 | 0.7093 | 0.6226 | 13.0717 | 0.1200 | 0.0046 | 0.2329 | 33 | 139 |

## Paired ROI Delta

| candidate | baseline | dates | candidate_roi | baseline_roi | delta_roi | ci_low | ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- |
| coherent_cal_quote | historical_full_features | 16 | 0.1119 | 0.0808 | 0.0311 | 0.0063 | 0.0573 |
| coherent_cal_quote | live_missingness_emulation | 16 | 0.1119 | 0.1200 | -0.0081 | -0.0722 | 0.0615 |

## Probability Score Delta

| candidate | baseline | rows | dates | date_equal_delta_logloss | logloss_ci_low | logloss_ci_high | date_equal_delta_brier | brier_ci_low | brier_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coherent_cal_quote | historical_full_features | 2077 | 16 | -0.0020 | -0.0045 | -0.0003 | -0.0007 | -0.0016 | 0.0000 |

## Early / Late Window

| variant | window | rows | dates | win_rate | pnl | cost | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| coherent_cal_quote | early_0621_0630 | 105 | 9 | 0.7143 | 8.5885 | 66.4115 | 0.1293 |
| coherent_cal_quote | late_0701_0708 | 62 | 7 | 0.7258 | 3.4920 | 41.5080 | 0.0841 |
| historical_full_features | early_0621_0630 | 99 | 9 | 0.6970 | 6.7495 | 62.2505 | 0.1084 |
| historical_full_features | late_0701_0708 | 60 | 7 | 0.6833 | 1.4747 | 39.5253 | 0.0373 |

## Daily Full vs Primary

| variant | target_date | rows | wins | win_rate | pnl | cost | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| coherent_cal_quote | 2026-06-21 | 13 | 9.0000 | 0.6923 | 0.8824 | 8.1175 | 0.1087 |
| coherent_cal_quote | 2026-06-22 | 11 | 9.0000 | 0.8182 | 2.6361 | 6.3639 | 0.4142 |
| coherent_cal_quote | 2026-06-23 | 8 | 7.0000 | 0.8750 | 1.1580 | 5.8420 | 0.1982 |
| coherent_cal_quote | 2026-06-25 | 15 | 9.0000 | 0.6000 | 0.0858 | 8.9142 | 0.0096 |
| coherent_cal_quote | 2026-06-26 | 16 | 12.0000 | 0.7500 | 1.4355 | 10.5645 | 0.1359 |
| coherent_cal_quote | 2026-06-27 | 20 | 16.0000 | 0.8000 | 3.0234 | 12.9766 | 0.2330 |
| coherent_cal_quote | 2026-06-28 | 3 | 2.0000 | 0.6667 | 0.2043 | 1.7957 | 0.1138 |
| coherent_cal_quote | 2026-06-29 | 6 | 2.0000 | 0.3333 | -1.5655 | 3.5655 | -0.4391 |
| coherent_cal_quote | 2026-06-30 | 13 | 9.0000 | 0.6923 | 0.7284 | 8.2716 | 0.0881 |
| coherent_cal_quote | 2026-07-01 | 12 | 11.0000 | 0.9167 | 2.4095 | 8.5905 | 0.2805 |
| coherent_cal_quote | 2026-07-02 | 3 | 2.0000 | 0.6667 | -0.0381 | 2.0381 | -0.0187 |
| coherent_cal_quote | 2026-07-03 | 9 | 7.0000 | 0.7778 | 0.5974 | 6.4026 | 0.0933 |
| coherent_cal_quote | 2026-07-05 | 15 | 8.0000 | 0.5333 | -1.7332 | 9.7332 | -0.1781 |
| coherent_cal_quote | 2026-07-06 | 12 | 8.0000 | 0.6667 | 0.6102 | 7.3898 | 0.0826 |
| coherent_cal_quote | 2026-07-07 | 8 | 7.0000 | 0.8750 | 1.8056 | 5.1944 | 0.3476 |
| coherent_cal_quote | 2026-07-08 | 3 | 2.0000 | 0.6667 | -0.1593 | 2.1593 | -0.0738 |
| historical_full_features | 2026-06-21 | 13 | 9.0000 | 0.6923 | 0.6542 | 8.3458 | 0.0784 |
| historical_full_features | 2026-06-22 | 10 | 8.0000 | 0.8000 | 2.2281 | 5.7719 | 0.3860 |
| historical_full_features | 2026-06-23 | 8 | 6.0000 | 0.7500 | 0.5959 | 5.4041 | 0.1103 |
| historical_full_features | 2026-06-25 | 16 | 10.0000 | 0.6250 | 0.1309 | 9.8691 | 0.0133 |
| historical_full_features | 2026-06-26 | 12 | 9.0000 | 0.7500 | 1.2062 | 7.7938 | 0.1548 |
| historical_full_features | 2026-06-27 | 19 | 15.0000 | 0.7895 | 3.0700 | 11.9300 | 0.2573 |
| historical_full_features | 2026-06-28 | 3 | 2.0000 | 0.6667 | 0.4428 | 1.5572 | 0.2844 |
| historical_full_features | 2026-06-29 | 6 | 2.0000 | 0.3333 | -1.5655 | 3.5655 | -0.4391 |
| historical_full_features | 2026-06-30 | 12 | 8.0000 | 0.6667 | -0.0131 | 8.0131 | -0.0016 |
| historical_full_features | 2026-07-01 | 9 | 8.0000 | 0.8889 | 1.6580 | 6.3420 | 0.2614 |
| historical_full_features | 2026-07-02 | 3 | 2.0000 | 0.6667 | 0.0293 | 1.9707 | 0.0149 |
| historical_full_features | 2026-07-03 | 8 | 6.0000 | 0.7500 | 0.4538 | 5.5462 | 0.0818 |
| historical_full_features | 2026-07-05 | 16 | 9.0000 | 0.5625 | -1.5554 | 10.5554 | -0.1474 |
| historical_full_features | 2026-07-06 | 12 | 7.0000 | 0.5833 | -0.2636 | 7.2636 | -0.0363 |
| historical_full_features | 2026-07-07 | 8 | 7.0000 | 0.8750 | 1.7461 | 5.2539 | 0.3323 |
| historical_full_features | 2026-07-08 | 4 | 2.0000 | 0.5000 | -0.5935 | 2.5935 | -0.2288 |

## Daily Paired Delta

| target_date | candidate_rows | baseline_rows | candidate_pnl | baseline_pnl | delta_pnl | candidate_better |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-06-21 | 13 | 13 | 0.8824 | 0.6542 | 0.2283 | True |
| 2026-06-22 | 11 | 10 | 2.6361 | 2.2281 | 0.4080 | True |
| 2026-06-23 | 8 | 8 | 1.1580 | 0.5959 | 0.5622 | True |
| 2026-06-25 | 15 | 16 | 0.0858 | 0.1309 | -0.0452 | False |
| 2026-06-26 | 16 | 12 | 1.4355 | 1.2062 | 0.2293 | True |
| 2026-06-27 | 20 | 19 | 3.0234 | 3.0700 | -0.0465 | False |
| 2026-06-28 | 3 | 3 | 0.2043 | 0.4428 | -0.2386 | False |
| 2026-06-29 | 6 | 6 | -1.5655 | -1.5655 | 0.0000 | False |
| 2026-06-30 | 13 | 12 | 0.7284 | -0.0131 | 0.7416 | True |
| 2026-07-01 | 12 | 9 | 2.4095 | 1.6580 | 0.7515 | True |
| 2026-07-02 | 3 | 3 | -0.0381 | 0.0293 | -0.0674 | False |
| 2026-07-03 | 9 | 8 | 0.5974 | 0.4538 | 0.1436 | True |
| 2026-07-05 | 15 | 16 | -1.7332 | -1.5554 | -0.1778 | False |
| 2026-07-06 | 12 | 12 | 0.6102 | -0.2636 | 0.8738 | True |
| 2026-07-07 | 8 | 8 | 1.8056 | 1.7461 | 0.0595 | True |
| 2026-07-08 | 3 | 4 | -0.1593 | -0.5935 | 0.4342 | True |

## Selected Expression Breakdown

| variant | expression | rows | dates | win_rate | avg_p | avg_ask | pnl | roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coherent_cal_quote | current_no | 31 | 14 | 0.7419 | 0.7168 | 0.6470 | 2.6289 | 0.1291 |
| coherent_cal_quote | d1_no | 36 | 11 | 0.7778 | 0.7134 | 0.6598 | 3.8813 | 0.1609 |
| coherent_cal_quote | d1_yes | 14 | 11 | 0.4286 | 0.5445 | 0.4867 | -0.9870 | -0.1413 |
| coherent_cal_quote | d2_no | 62 | 16 | 0.7581 | 0.7598 | 0.7057 | 2.6412 | 0.0595 |
| coherent_cal_quote | d2_yes | 24 | 11 | 0.6667 | 0.5458 | 0.4913 | 3.9162 | 0.3241 |
| historical_full_features | current_no | 29 | 13 | 0.7241 | 0.6932 | 0.6152 | 2.8514 | 0.1571 |
| historical_full_features | d1_no | 43 | 14 | 0.7442 | 0.7121 | 0.6600 | 3.1832 | 0.1105 |
| historical_full_features | d1_yes | 15 | 11 | 0.3333 | 0.5330 | 0.4689 | -2.2187 | -0.3074 |
| historical_full_features | d2_no | 53 | 16 | 0.7547 | 0.7577 | 0.7058 | 2.0782 | 0.0548 |
| historical_full_features | d2_yes | 19 | 11 | 0.6316 | 0.5465 | 0.4968 | 2.3301 | 0.2410 |

## Selection Transition vs Full

| transition | rows | dates | baseline_pnl | candidate_pnl | delta_pnl |
| --- | --- | --- | --- | --- | --- |
| switched_expression | 15 | 9 | 1.2712 | 1.4857 | 0.2146 |
| baseline_only | 8 | 7 | -0.4391 | 0.0000 | 0.4391 |
| same_expression | 136 | 16 | 7.3921 | 8.2077 | 0.8155 |
| candidate_only | 16 | 10 | 0.0000 | 2.3872 | 2.3872 |

## Previously Cancelled d1 NO Diagnostic

| rows | dates | win_rate | pnl | avg_ask | old_avg_p | full_avg_p | primary_avg_p | primary_restored_rows |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 36.0000 | 12.0000 | 0.6944 | 2.4308 | 0.6159 | 0.6636 | 0.6222 | 0.6225 | 2.0000 |

该 cohort 是上一轮看完结果后定义的诊断切片，只用于确认新模型行为，不能作为模型选择目标或 forward 证据。

## 8 环覆盖

- [1] 描述性绩效：覆盖；[2] target_date block bootstrap：覆盖；[3] 信号判别：以 expression logloss/selection transition 覆盖。
- [4] coherent 四桶与 expression calibration：覆盖；[5] 执行：历史 ask + 官方 taker fee，fresh live fill 未覆盖；[6] capacity：未覆盖。
- [7] 同日相关：date block 处理；[8] 基准：完整特征模型和旧缺失模型均覆盖，随机/无脑 NO 未新增。

## 边界

- GFS/ECMWF gap 在 6/21+ atlas 仍为 0% 覆盖，本轮没有声称修复了 forecast-gap forward 证据。
- 本轮有限候选 K=3；没有按城市、天气切片或 ROI 阈值继续搜索。
- run_stack 的 runtime-order migration 失败与本模型离线分母无关，但意味着本轮不能发布新的 live_real 绩效。
- tmax live 保持暂停；本报告不修改 runner、config、executor 或资金状态。
