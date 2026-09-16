# Current-Bracket NO Afternoon-Peak Classifier v1

## 数据快照

- 数据源：`docs/analysis/2026-06/generated/current_bracket_no_pass_through_v1_feature_factory/reheat_feature_rows.csv` + `actual_peak_afternoon` label；输出 `docs/analysis/2026-06/generated/current_bracket_no_afternoon_peak_classifier_v1/summary.json`。
- 生成时间：`2026-06-22T16:12:51+00:00`。
- 样本：h10-14 current-bracket NO states `4306` rows / `32` dates / `36` cities。
- Train：`2026-05-20`..`2026-06-10`；Holdout：`2026-06-11`..`2026-06-20`。
- 预测特征：forecast peak hour / GFS+ECMWF peak delta / forecast max gap / 1h-3h temp trend / running max state / humidity-wind-sky proxy / city。
- 重要限制：forecast peak 来自 research backfill，不是已经核验的 point-in-time 前一日 issue forecast；因此不能直接 live。

## 结论

`logit_c0p2_p_ge_0p50` 选出 150 笔 / 32 天，current-bracket NO ROI +37.6%，日期 bootstrap CI [+11.0%, +66.3%]；holdout ROI +37.6%，train ROI +37.6%。

同一 ask/cap baseline `baseline_ask10_35_depth5_ge5` 是 375 笔，ROI -8.4%，CI [-27.4%, +10.0%]。classifier 相对 baseline 的 excess ROI +46.0%，CI [+23.9%, +70.7%]。

Peak classifier holdout AUC `0.828`，Brier `0.173`。

三门：significance=PASS / baseline=PASS / forward=PASS；conclusion=shadow_candidate。

交易动作：这是一个明显更合理的版本，值得进入 zero-notional shadow / point-in-time forecast 修复；但由于前一日 forecast 口径还不是生产级 point-in-time，暂不 live。

## Variant Table

| variant | selected_trades | active_dates | avg_no_ask | no_win_rate | actual_peak_afternoon_rate | roi | roi_ci_low | roi_ci_high | train_roi | holdout_roi | baseline_roi | excess_roi_vs_baseline |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_ask10_35_depth5_ge5 | 375 | 32 | 0.232 | 0.205 | 0.445 | -8.4% | -27.4% | +10.0% | -14.8% | +7.0% | NA | NA |
| logit_c0p2_p_ge_0p50 | 150 | 32 | 0.231 | 0.320 | 0.780 | +37.6% | +11.0% | +66.3% | +37.6% | +37.6% | -8.4% | +46.0% |
| logit_c1_p_ge_0p50 | 148 | 32 | 0.229 | 0.331 | 0.784 | +41.9% | +14.5% | +72.0% | +39.6% | +47.8% | -8.4% | +50.2% |

## 为什么这版不是 oracle

`actual_peak_afternoon` 只作为训练/验证 label；交易筛选时使用的是 classifier score。模型特征不包含 final max、actual peak hour、settlement winner。上一版 oracle 行只是事后机制上界，这版才是在做事前识别。

## 后续硬要求

1. 补真正 point-in-time 的前一日 forecast issue snapshot，替换 research backfill。
2. 用 live/shadow forward rows 验证同样 score 分布和真实可成交 capacity。
3. 再评估 maker-first 执行；现在只是 replay best ask / depth proxy。
