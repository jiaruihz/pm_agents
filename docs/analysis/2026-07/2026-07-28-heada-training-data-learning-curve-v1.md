# HeadA 后采数据入训 learning curve v1

Generated: 2026-07-28T13:26:51+00:00

## 数据快照

| 字段 | 值 |
| --- | --- |
| 数据源 | canonical checkpoint CSV + HeadA historical/current fixed denominators |
| DB mtime UTC | 2026-07-28T13:23:18+00:00 |
| canonical Tmax rows / dates | 281269 / 2026-07-04..2026-07-28 |
| settlement max target_date | 2026-07-27 |
| HeadA historical | 333 rows / 53 dates |
| HeadA current | 84 rows / 8 dates |
| unsettled / missing_bracket | 0 / 0（本研究固定 settled label） |
| live fill coverage gate | NA（无 live_real PnL；仅 shadow/research probability） |

## 结论

**可以把后采数据分批入训，但当前证据只支持更新底层天气状态模型，不支持把新数据直接
等权灌入 HeadA entry/exact-ticket 概率头。**

- 底层 weather center 明确受益：最后 6 个日期上，expanding innovation 相对最早 5 日
  frozen model，09:00 MAE 降 `0.14676°F`（95% CI `[-0.20152,-0.09119]`），
  12:00 降 `0.06832°F`（`[-0.09353,-0.02401]`）。
- 但同一 frozen HeadA exact-ticket 分母上反向：expanding 的 09:00/12:00 Brier
  分别比 frozen-5 **变差** `+0.00348/+0.00241`，CI 都高于 0；logloss 也分别
  变差 `+0.01113/+0.00651`。
- D-1 entry 也没有“多塞数据就好”：把 6/21..6/30 加入原训练集后，frozen 4 日
  entry-base Brier/logloss 分别变差 `+0.00354/+0.01211`，CI 均高于 0；
  继续加入 prior current labels 的点估仍更差。

根因不是“新数据无用”，而是**训练目标错层**：宽分母模型在优化最终 Tmax 中心误差，
HeadA 需要的是完整 ladder 上的尾部质量、overshoot 和 exact landing 概率。expanding
在 frozen HeadA losers 上通常改善/不伤 Tmax MAE，却把 09:00 两个 winners 的概率平均
下调 `1.65pp`、12:00 唯一 winner 下调 `2.25pp`；winner Brier 分别恶化
`+0.02656/+0.03184`。这正是 center 准确度提升但 tail probability 变差的选择分布错位。

所以动作是：weather center 继续 expanding shadow；HeadA 概率头改成
**market-anchored、跨整条 ladder 归一化的分布模型**，后采数据按 15 个 adaptation
target dates + 15 个 untouched frozen target dates 批量更新，不做每日看完 ROI 就重训。
协议已冻结在 `configs/weather/heada_training_data_protocol_v1.json`。

本报告专门回答“后采、已结算数据加入训练，是否比最早模型更好”。所有预测都只使用
严格更早的 `target_date`；没有随机拆 ticket rows。天气模型的最后
6 个日期（2026-07-22..2026-07-27）和 HeadA
最后 4 个日期（2026-07-22..2026-07-25）是 frozen-forward 报告窗。

主比较是：

- weather：`expanding_prior` 对 `frozen_first5`；
- entry：`all_historical` / `expanding_current` 对原 `<= 2026-06-20` frozen model；
- rolling 5/10/30 只作 regime/遗忘敏感性，不据最漂亮点选 live 规则。

## A. 天气状态模型：后采日期是否改善 Tmax

同一 frozen-forward city-date-checkpoint，innovation 模型：

| checkpoint_hour_local | training_policy | rows | dates | train_dates_min | train_dates_max | mae_f | rmse_f | mean_error_f |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 9 | expanding_prior | 177 | 6 | 11 | 16 | 1.85444 | 2.36262 | -0.39741 |
| 9 | frozen_first5 | 177 | 6 | 5 | 5 | 2.00120 | 2.48338 | -0.38287 |
| 9 | rolling10 | 177 | 6 | 10 | 10 | 1.88745 | 2.39802 | -0.42485 |
| 9 | rolling5 | 177 | 6 | 5 | 5 | 1.99653 | 2.50575 | -0.30276 |
| 12 | expanding_prior | 163 | 6 | 11 | 16 | 1.50058 | 1.80609 | -0.06101 |
| 12 | frozen_first5 | 163 | 6 | 5 | 5 | 1.56889 | 1.87625 | 0.14952 |
| 12 | rolling10 | 163 | 6 | 10 | 10 | 1.51946 | 1.82045 | -0.15504 |
| 12 | rolling5 | 163 | 6 | 5 | 5 | 1.54804 | 1.87785 | -0.11799 |

相对最早 5 日 frozen model；负数为改善：

| checkpoint_hour_local | candidate_policy | rows | dates | abs_error_f_delta | abs_error_f_delta_ci_low | abs_error_f_delta_ci_high | squared_error_f_delta | squared_error_f_delta_ci_low | squared_error_f_delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 9 | expanding_prior | 177 | 6 | -0.14676 | -0.20152 | -0.09119 | -0.58519 | -0.94653 | -0.23315 |
| 12 | expanding_prior | 163 | 6 | -0.06832 | -0.09353 | -0.02401 | -0.25835 | -0.37717 | -0.12592 |

## B. HeadA exact-ticket：后采训练是否变成 market residual

固定 HeadA ticket、同刻 market proxy、gamma=1；模型只提供
`P_innovation - P_rolling_bias` 的增量。负 delta 才比 market 好：

| checkpoint_hour_local | training_policy | rows | dates | wins | train_dates_min | train_dates_max | tmax_mae_f | observed_rate | mean_market_p | mean_p_hat | brier_delta_vs_market | brier_delta_ci_low | brier_delta_ci_high | logloss_delta_vs_market | logloss_delta_ci_low | logloss_delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 9 | expanding_prior | 21 | 4 | 2 | 11 | 14 | 1.98033 | 0.09524 | 0.09188 | 0.08960 | -0.00305 | -0.01096 | 0.00275 | -0.02211 | -0.04929 | 0.00442 |
| 9 | frozen_first5 | 21 | 4 | 2 | 5 | 5 | 2.15146 | 0.09524 | 0.09188 | 0.08916 | -0.00653 | -0.01652 | 0.00009 | -0.03325 | -0.06991 | -0.00290 |
| 9 | rolling10 | 21 | 4 | 2 | 10 | 10 | 1.94390 | 0.09524 | 0.09188 | 0.08964 | -0.00294 | -0.01050 | 0.00239 | -0.02138 | -0.04567 | 0.00298 |
| 9 | rolling5 | 21 | 4 | 2 | 5 | 5 | 2.19955 | 0.09524 | 0.09188 | 0.08970 | -0.00197 | -0.00790 | 0.00215 | -0.01658 | -0.03621 | 0.00232 |
| 12 | expanding_prior | 19 | 4 | 1 | 11 | 14 | 1.72347 | 0.05263 | 0.07184 | 0.06422 | 0.00096 | -0.00096 | 0.00271 | -0.00555 | -0.02478 | 0.00645 |
| 12 | frozen_first5 | 19 | 4 | 1 | 5 | 5 | 1.72410 | 0.05263 | 0.07184 | 0.06341 | -0.00146 | -0.00303 | 0.00121 | -0.01206 | -0.02829 | -0.00005 |
| 12 | rolling10 | 19 | 4 | 1 | 10 | 10 | 1.68695 | 0.05263 | 0.07184 | 0.06504 | 0.00140 | -0.00091 | 0.00318 | -0.00450 | -0.02764 | 0.00563 |
| 12 | rolling5 | 19 | 4 | 1 | 5 | 5 | 1.76192 | 0.05263 | 0.07184 | 0.06760 | 0.00203 | -0.00088 | 0.00307 | -0.00063 | -0.02315 | 0.00528 |

expanding 相对最早 5 日 model；负数为改善：

| checkpoint_hour_local | candidate_policy | rows | dates | brier_delta_vs_frozen5 | brier_delta_vs_frozen5_ci_low | brier_delta_vs_frozen5_ci_high | logloss_delta_vs_frozen5 | logloss_delta_vs_frozen5_ci_low | logloss_delta_vs_frozen5_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 9 | expanding_prior | 21 | 4 | 0.00348 | 0.00101 | 0.00556 | 0.01113 | 0.00261 | 0.02062 |
| 12 | expanding_prior | 19 | 4 | 0.00241 | 0.00045 | 0.00340 | 0.00651 | 0.00341 | 0.00850 |

expanding 相对 frozen-5 的诊断切片（post-hoc，只解释变差来自哪里）：

| checkpoint_hour_local | dimension | value | rows | dates | wins | p_hat_delta | tmax_mae_delta_f | brier_delta_vs_frozen5 | logloss_delta_vs_frozen5 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 9 | outcome | loser | 19 | 4 | 0 | 0.00221 | -0.19828 | 0.00105 | 0.00298 |
| 9 | outcome | winner | 2 | 2 | 2 | -0.01648 | 0.08683 | 0.02656 | 0.08855 |
| 9 | source | ecmwf | 14 | 4 | 1 | 0.00244 | -0.19556 | 0.00156 | 0.00562 |
| 9 | source | gfs | 7 | 3 | 1 | -0.00359 | -0.12227 | 0.00731 | 0.02217 |
| 12 | outcome | loser | 18 | 4 | 0 | 0.00211 | -0.02592 | 0.00078 | 0.00261 |
| 12 | outcome | winner | 1 | 1 | 1 | -0.02255 | 0.45445 | 0.03184 | 0.07673 |
| 12 | source | ecmwf | 14 | 4 | 1 | 0.00132 | 0.02673 | 0.00326 | 0.00905 |
| 12 | source | gfs | 5 | 2 | 0 | -0.00061 | -0.07726 | 0.00004 | -0.00058 |

## C. D-1 entry 概率头：加入 6/21 后数据

固定 current frozen-forward rows：

| variant | training_policy | rows | dates | wins | train_rows_min | train_rows_max | train_dates_min | train_dates_max | train_win_rate_min | train_win_rate_max | observed_rate | mean_p_hat | mean_unit_cost | brier | logloss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| entry_base | all_historical | 51 | 4 | 6 | 333 | 333 | 53 | 53 | 0.15015 | 0.15015 | 0.11765 | 0.16982 | 0.11835 | 0.10016 | 0.34717 |
| entry_base | expanding_current | 51 | 4 | 6 | 366 | 401 | 57 | 60 | 0.14464 | 0.15301 | 0.11765 | 0.16665 | 0.11835 | 0.10134 | 0.34954 |
| entry_base | original_frozen | 51 | 4 | 6 | 275 | 275 | 44 | 44 | 0.14545 | 0.14545 | 0.11765 | 0.16338 | 0.11835 | 0.09662 | 0.33506 |
| entry_base | rolling30 | 51 | 4 | 6 | 218 | 234 | 30 | 30 | 0.12821 | 0.13596 | 0.11765 | 0.14600 | 0.11835 | 0.10309 | 0.35488 |
| entry_plus_prior_bias | all_historical | 51 | 4 | 6 | 333 | 333 | 53 | 53 | 0.15015 | 0.15015 | 0.11765 | 0.18509 | 0.11835 | 0.10031 | 0.34677 |
| entry_plus_prior_bias | expanding_current | 51 | 4 | 6 | 366 | 401 | 57 | 60 | 0.14464 | 0.15301 | 0.11765 | 0.18096 | 0.11835 | 0.10197 | 0.34978 |
| entry_plus_prior_bias | original_frozen | 51 | 4 | 6 | 275 | 275 | 44 | 44 | 0.14545 | 0.14545 | 0.11765 | 0.18173 | 0.11835 | 0.09750 | 0.33751 |
| entry_plus_prior_bias | rolling30 | 51 | 4 | 6 | 218 | 234 | 30 | 30 | 0.12821 | 0.13596 | 0.11765 | 0.15267 | 0.11835 | 0.10262 | 0.35013 |

相对原 `<= 2026-06-20` 模型；负数为改善：

| variant | candidate_policy | rows | dates | brier_delta_vs_original | brier_delta_vs_original_ci_low | brier_delta_vs_original_ci_high | logloss_delta_vs_original | logloss_delta_vs_original_ci_low | logloss_delta_vs_original_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| entry_base | all_historical | 51 | 4 | 0.00354 | 0.00101 | 0.00625 | 0.01211 | 0.00238 | 0.02166 |
| entry_base | expanding_current | 51 | 4 | 0.00472 | -0.00158 | 0.01084 | 0.01447 | -0.00134 | 0.03058 |
| entry_plus_prior_bias | all_historical | 51 | 4 | 0.00281 | -0.00043 | 0.00579 | 0.00926 | -0.00131 | 0.01849 |
| entry_plus_prior_bias | expanding_current | 51 | 4 | 0.00447 | -0.00311 | 0.01208 | 0.01227 | -0.00590 | 0.03087 |

使用固定 `p_hat > fee-adjusted cost_eval` 的 secondary trade expression：

| variant | training_policy | opportunity_rows | action_rows | action_dates | wins | cost_usd | pnl_usd | roi | roi_ci_low | roi_ci_high | all_buy_cost_usd | all_buy_pnl_usd | all_buy_roi | all_buy_roi_ci_low | all_buy_roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| entry_base | all_historical | 51 | 41 | 4 | 4 | 25.25864 | -5.25864 | -0.20819 | -1.00000 | 0.14954 | 30.18032 | -0.18032 | -0.00597 | -1.00000 | 0.67657 |
| entry_base | expanding_current | 51 | 43 | 4 | 5 | 26.87517 | -1.87517 | -0.06977 | -1.00000 | 0.34636 | 30.18032 | -0.18032 | -0.00597 | -1.00000 | 0.67657 |
| entry_base | original_frozen | 51 | 41 | 4 | 5 | 25.62237 | -0.62237 | -0.02429 | -1.00000 | 0.41492 | 30.18032 | -0.18032 | -0.00597 | -1.00000 | 0.67657 |
| entry_base | rolling30 | 51 | 36 | 4 | 5 | 22.28442 | 2.71558 | 0.12186 | -1.00000 | 0.73163 | 30.18032 | -0.18032 | -0.00597 | -1.00000 | 0.67657 |
| entry_plus_prior_bias | all_historical | 51 | 44 | 4 | 5 | 26.61216 | -1.61216 | -0.06058 | -1.00000 | 0.42841 | 30.18032 | -0.18032 | -0.00597 | -1.00000 | 0.67657 |
| entry_plus_prior_bias | expanding_current | 51 | 43 | 4 | 5 | 25.83028 | -0.83028 | -0.03214 | -1.00000 | 0.49521 | 30.18032 | -0.18032 | -0.00597 | -1.00000 | 0.67657 |
| entry_plus_prior_bias | original_frozen | 51 | 42 | 4 | 6 | 25.57349 | 4.42651 | 0.17309 | -1.00000 | 0.94599 | 30.18032 | -0.18032 | -0.00597 | -1.00000 | 0.67657 |
| entry_plus_prior_bias | rolling30 | 51 | 40 | 4 | 5 | 23.64038 | 1.35962 | 0.05751 | -1.00000 | 1.00761 | 30.18032 | -0.18032 | -0.00597 | -1.00000 | 0.67657 |

这里只是 selected-trade secondary diagnostic；是否改善首先以固定分母 Brier/logloss 判定，
不以少数彩票 ROI 反向挑模型。

## 固定漏斗

Signal funnel：

- weather raw：2298 checkpoint rows / 17 dates；
- weather eligible：09/12 checkpoint，前 5 日期 train-only，后 12 日期 OOF；
- HeadA historical：333 tickets / 53 dates；
- HeadA current：84 tickets / 8 dates。

Evidence funnel：

- PIT canonical checkpoint / settlement：weather learning curve 全部同 rows；
- current HeadA entry ask / settlement：84/84；
- HeadA 09/12 proxy book：见 exact scorecard rows；
- actual fill：0，zero-notional/research；
- historical weather/multisource archive 仍是 post-hoc，未拿它训练新 entry weather gate。

## 解释边界与下一轮训练协议

1. “更多 rows”不等于“更多独立信息”；有效样本是 target dates，不是同日城市票数。
2. expanding 若改善 frozen proper score，说明最早 5 日/旧截止训练确实 underfit；
   若 rolling 优于 expanding，则主要是 regime drift，不应把全部历史等权混入。
3. 训练更新只能吸收已 settlement 的 prior target dates；生产应保留一个永不参与模型选择的
   rolling frozen buffer，再按固定 cadence 批量升级，不能每天看完 ROI 临时改参数。
4. 本轮仍只有 6 个 broad frozen dates、4 个 HeadA frozen dates；CI 跨 0 时结论仍是
   `inconclusive`，不会因为“数据更多”自动升 live。

## 八环与三门

1. hypothesis：后采 settled 数据可能降低模型 variance / 修正 regime。
2. universe：固定 weather checkpoint 与 HeadA current denominators。
3. time：strict prior target-date walk-forward；无随机 row split。
4. model：原 frozen、expanding、rolling 同超参数。
5. probability：Brier/logloss + target-date block CI。
6. execution：仅 secondary fee-adjusted `cost_eval` expression，无 fresh live fill。
7. robustness：rolling 5/10/30；没有从结果反选阈值。
8. deployment：不改 live；只决定后续 training protocol。

```text
significance = 见 paired block CI
baseline = market / original frozen model
forward = PARTIAL（retrospective frozen tail，尚非全新未看日期）
conclusion = inconclusive 或 shadow_candidate；live_action=none
```

## 产物

- evaluator：`scripts/analysis/forecast_quality/research_heada_training_data_learning_curve_v1.py`
- structured：`generated/heada_training_data_learning_curve_v1/summary.json`
- weather：`weather_scorecard.csv`, `weather_policy_deltas.csv`
- exact-ticket：`heada_checkpoint_scorecard.csv`, `heada_checkpoint_policy_deltas.csv`
- 诊断切片：`heada_checkpoint_policy_slices.csv`
- entry：`entry_scorecard.csv`, `entry_policy_deltas.csv`,
  `entry_selected_trade_scorecard.csv`
