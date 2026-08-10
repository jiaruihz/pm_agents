# Tmax Path-Market Fusion V2 Baseline

> generated_at_utc: `2026-08-10T04:33:56+00:00`
> Scope: offline data-answerability audit only. No live runner, configuration, order behavior, or database was changed.

## 结论

- **当前历史数据可以形成最小 absolute-ladder PIT 分母，但样本极薄；本轮只完成可答性审计，不拟合 hazard，也不输出无统计意义的 ROI。**
- 可回指 paper snapshot 的 P0 state 是 `8325` / `8347`；完整逐档 quoted state `72`，带标签可评估 `47`。pre-cutoff `30` 行 / `11` 天，post-cutoff `17` 行 / `4` 天；仅 `1` 个 city-day 在多个 state 上持续保存完整固定梯子。
- `settlement_outcomes` 只用于事后 collector-completeness 审计；final bracket、actual bucket、outcome-derived slice 都没有进入特征。P3/atlas 的历史 forecast backfill 与全窗口 city bias 被明确排除。

## Funnel

| stage | rows | dates | cities | note |
| --- | --- | --- | --- | --- |
| p0_relative_state_universe | 8347 | 48 | 36 | relative current/d1/d2/tail; not an absolute ladder |
| has_decision_snapshot_timestamp | 8347 | 48 | 36 | P0 decision-state lineage |
| snapshot_path_matched | 8325 | 48 | 36 | captured paper snapshot at matching Beijing wall-clock minute |
| complete_identity_ladder_audit | 73 | 25 | 23 | after-the-fact settlement inventory audit only |
| complete_quoted_absolute_ladder | 72 | 24 | 23 | all audited siblings have a direct/cross-side YES quote |
| absolute_eligible_with_label | 47 | 15 | 19 | complete quote + ordered anchors + final label present; label not a feature |
| fixed_complete_ladder_city_days | 1 | 1 | 1 | at least two matched states; every state complete-quoted; one identical absolute signature |
| pre_cutoff_absolute_eligible | 30 | 11 | 17 | requires at least 5 dates for inner walk-forward |

## Same-Denominator Model Comparison

四个预注册模型已有同一 absolute PIT 分母，但 47 个可评估 state 只够 smoke test，不够在 inner selection 后再给独立 forward 结论。本审计因此保留 metrics/CI/ROI 为 `NA`，后续模型实验必须继续积累 fresh complete-ladder states。

| model | status | date_equal_logloss | date_equal_brier | date_block_ci | roi_fee_adjusted | reason |
| --- | --- | --- | --- | --- | --- | --- |
| market_full_ladder | not_run_audit_only_thin | NA | NA | NA | NA | minimal denominator is available but thin: pre=30 rows/11 dates, post=17 rows/4 dates |
| current_coherent_base | not_run_audit_only_thin | NA | NA | NA | NA | minimal denominator is available but thin: pre=30 rows/11 dates, post=17 rows/4 dates |
| global_physical_hazard | not_run_audit_only_thin | NA | NA | NA | NA | minimal denominator is available but thin: pre=30 rows/11 dates, post=17 rows/4 dates |
| market_plus_global_hazard_residual | not_run_audit_only_thin | NA | NA | NA | NA | minimal denominator is available but thin: pre=30 rows/11 dates, post=17 rows/4 dates |

## 日期覆盖

| target_date | snapshot_matched_states | complete_identity_states | complete_quoted_states | cities | pre_cutoff |
| --- | --- | --- | --- | --- | --- |
| 2026-05-19 | 10 | 7 | 7 | 6 | True |
| 2026-05-20 | 241 | 3 | 3 | 34 | True |
| 2026-05-21 | 220 | 2 | 2 | 32 | True |
| 2026-05-22 | 177 | 0 | 0 | 34 | True |
| 2026-05-23 | 189 | 3 | 3 | 34 | True |
| 2026-05-24 | 170 | 0 | 0 | 30 | True |
| 2026-05-25 | 168 | 0 | 0 | 32 | True |
| 2026-05-26 | 156 | 1 | 1 | 33 | True |
| 2026-05-27 | 253 | 2 | 2 | 35 | True |
| 2026-05-28 | 184 | 3 | 3 | 30 | True |
| 2026-05-29 | 199 | 1 | 1 | 32 | True |
| 2026-05-30 | 186 | 3 | 3 | 31 | True |
| 2026-05-31 | 207 | 2 | 2 | 33 | True |
| 2026-06-01 | 195 | 4 | 4 | 33 | True |
| 2026-06-02 | 174 | 0 | 0 | 30 | True |
| 2026-06-03 | 162 | 0 | 0 | 31 | True |
| 2026-06-04 | 181 | 1 | 1 | 34 | True |
| 2026-06-05 | 196 | 2 | 2 | 32 | True |
| 2026-06-06 | 214 | 7 | 7 | 35 | True |
| 2026-06-07 | 221 | 0 | 0 | 36 | True |
| 2026-06-08 | 219 | 0 | 0 | 36 | True |
| 2026-06-09 | 230 | 2 | 2 | 36 | True |
| 2026-06-10 | 208 | 1 | 1 | 33 | True |
| 2026-06-11 | 195 | 1 | 1 | 35 | True |
| 2026-06-12 | 208 | 0 | 0 | 36 | True |
| 2026-06-13 | 192 | 0 | 0 | 35 | True |
| 2026-06-14 | 220 | 0 | 0 | 36 | True |
| 2026-06-15 | 188 | 2 | 2 | 34 | True |
| 2026-06-16 | 207 | 0 | 0 | 35 | True |
| 2026-06-17 | 200 | 0 | 0 | 34 | True |
| 2026-06-18 | 146 | 0 | 0 | 24 | True |
| 2026-06-19 | 210 | 0 | 0 | 35 | True |
| 2026-06-20 | 155 | 0 | 0 | 30 | True |
| 2026-06-21 | 201 | 0 | 0 | 34 | False |
| 2026-06-22 | 215 | 0 | 0 | 31 | False |
| 2026-06-23 | 146 | 0 | 0 | 27 | False |
| 2026-06-25 | 222 | 6 | 6 | 36 | False |
| 2026-06-26 | 235 | 2 | 2 | 36 | False |
| 2026-06-27 | 202 | 2 | 2 | 35 | False |
| 2026-06-28 | 28 | 0 | 0 | 11 | False |
| 2026-06-29 | 81 | 1 | 1 | 20 | False |
| 2026-06-30 | 155 | 2 | 2 | 30 | False |
| 2026-07-01 | 132 | 12 | 12 | 29 | False |
| 2026-07-02 | 29 | 0 | 0 | 9 | False |
| 2026-07-03 | 79 | 0 | 0 | 19 | False |
| 2026-07-05 | 155 | 0 | 0 | 26 | False |
| 2026-07-06 | 106 | 0 | 0 | 18 | False |
| 2026-07-07 | 58 | 1 | 0 | 10 | False |

## PIT / Outcome Boundary

- 可用：同一 decision snapshot 内已捕获的 full sibling quote、同一快照中的 observation/path 与 forecast 字段（forecast run timestamp 仍标 `estimated`）。
- 排除：atlas/P3 historical backfill、full-window city bias、final outcome、actual overshoot/tail 等 outcome-derived slice。
- Quote/settlement sibling 完整性以 canonical `settlement_outcomes` 做**事后审计**，绝不回灌到特征或训练输入。

## Verdict

significance=NA; baseline=NA; forward=FAIL_THIN; conclusion=`absolute_ladder_minimally_answerable_but_too_thin_for_strategy_claim`。

现有 47 行只能验证 materializer/model plumbing。策略结论必须等待更多完整逐档 fresh states，并按 target_date 做 outer walk-forward；proper score 先选模型，fee-adjusted ROI 只能在模型冻结后作为 secondary。

## Artifacts

- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/funnel.csv`
- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/absolute_ladder_state_audit.csv`
- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/city_day_ladder_audit.csv`
- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/expression_audit.csv`
- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/daily_audit.csv`
- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/pit_feature_audit.csv`
- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/model_comparison.csv`
