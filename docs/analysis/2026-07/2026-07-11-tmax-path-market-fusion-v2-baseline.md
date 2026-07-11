# Tmax Path-Market Fusion V2 Baseline

> generated_at_utc: `2026-07-11T03:17:33+00:00`
> Scope: offline data-answerability audit only. No live runner, configuration, order behavior, or database was changed.

## 结论

- **当前历史数据不能回答 V2 absolute-ladder 模型问题，故本轮不拟合 hazard、不输出伪 logloss/Brier/ROI，也不把 `current/d1/d2/tail` 重命名为 absolute bracket。**
- 可回指 paper snapshot 的 P0 state 是 `834` / `8347`；absolute-complete quoted state 是 `14`，但能证明同一 city-day 多个 state 都拥有相同完整 quoted ladder 的 city-day 是 `0`。pre-cutoff 只有 `0` 个日期，低于 inner walk-forward 最低 `5` 天。
- `settlement_outcomes` 只用于事后 collector-completeness 审计；final bracket、actual bucket、outcome-derived slice 都没有进入特征。P3/atlas 的历史 forecast backfill 与全窗口 city bias 被明确排除。

## Funnel

| stage | rows | dates | cities | note |
| --- | --- | --- | --- | --- |
| p0_relative_state_universe | 8347 | 48 | 36 | relative current/d1/d2/tail; not an absolute ladder |
| has_decision_snapshot_timestamp | 8347 | 48 | 36 | P0 decision-state lineage |
| snapshot_path_matched | 834 | 15 | 36 | captured paper snapshot at matching Beijing wall-clock minute |
| complete_identity_ladder_audit | 15 | 3 | 8 | after-the-fact settlement inventory audit only |
| complete_quoted_absolute_ladder | 14 | 2 | 7 | all audited siblings have a direct/cross-side YES quote |
| absolute_eligible_with_label | 13 | 2 | 6 | complete quote + ordered anchors + final label present; label not a feature |
| fixed_complete_ladder_city_days | 0 | 0 | 0 | at least two matched states; every state complete-quoted; one identical absolute signature |
| pre_cutoff_absolute_eligible | 0 | 0 | 0 | requires at least 5 dates for inner walk-forward |

## Same-Denominator Model Comparison

所有四个预注册模型都要求同一 frozen absolute PIT denominator。这个分母无法形成，因此 metrics、date-block CI 和 fee-adjusted ROI 均为 `NA`，而不是零或负数。

| model | status | date_equal_logloss | date_equal_brier | date_block_ci | roi_fee_adjusted | reason |
| --- | --- | --- | --- | --- | --- | --- |
| market_full_ladder | not_run_data_not_answerable | NA | NA | NA | NA | absolute PIT denominator lacks pre-cutoff inner walk-forward support: 0 dates < 5 |
| current_coherent_base | not_run_data_not_answerable | NA | NA | NA | NA | absolute PIT denominator lacks pre-cutoff inner walk-forward support: 0 dates < 5 |
| global_physical_hazard | not_run_data_not_answerable | NA | NA | NA | NA | absolute PIT denominator lacks pre-cutoff inner walk-forward support: 0 dates < 5 |
| market_plus_global_hazard_residual | not_run_data_not_answerable | NA | NA | NA | NA | absolute PIT denominator lacks pre-cutoff inner walk-forward support: 0 dates < 5 |

## 日期覆盖

| target_date | snapshot_matched_states | complete_identity_states | complete_quoted_states | cities | pre_cutoff |
| --- | --- | --- | --- | --- | --- |
| 2026-06-19 | 9 | 0 | 0 | 2 | True |
| 2026-06-20 | 13 | 0 | 0 | 5 | True |
| 2026-06-21 | 10 | 0 | 0 | 10 | False |
| 2026-06-22 | 9 | 0 | 0 | 9 | False |
| 2026-06-25 | 5 | 0 | 0 | 5 | False |
| 2026-06-26 | 3 | 0 | 0 | 2 | False |
| 2026-06-27 | 10 | 0 | 0 | 10 | False |
| 2026-06-29 | 62 | 0 | 0 | 16 | False |
| 2026-06-30 | 155 | 2 | 2 | 30 | False |
| 2026-07-01 | 132 | 12 | 12 | 29 | False |
| 2026-07-02 | 28 | 0 | 0 | 9 | False |
| 2026-07-03 | 79 | 0 | 0 | 19 | False |
| 2026-07-05 | 155 | 0 | 0 | 26 | False |
| 2026-07-06 | 106 | 0 | 0 | 18 | False |
| 2026-07-07 | 58 | 1 | 0 | 10 | False |

## PIT / Outcome Boundary

- 可用：同一 decision snapshot 内已捕获的 full sibling quote、同一快照中的 observation/path 与 forecast 字段（forecast run timestamp 仍标 `estimated`）。
- 排除：atlas/P3 historical backfill、full-window city bias、final outcome、actual overshoot/tail 等 outcome-derived slice。
- Quote/settlement sibling 完整性以 canonical `settlement_outcomes` 做**事后审计**，绝不回灌到特征或训练输入。

## Verdict

significance=NA; baseline=NA; forward=FAIL; conclusion=`kill_current_historical_absolute_ladder_experiment_continue_forward_collection`。

当前应停止的是**这份历史样本上的 V2 模型拟合**，不是停止方向。继续前要积累：每个 city-day 固定 sibling ladder、每档 direct quote、state-to-snapshot key、以及至少 5 个 pre-cutoff PIT date blocks；满足后再按 target_date 外层 walk-forward，以 proper score 选择正则，最后才冻结 fee-adjusted ROI secondary replay。

## Artifacts

- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/funnel.csv`
- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/absolute_ladder_state_audit.csv`
- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/city_day_ladder_audit.csv`
- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/expression_audit.csv`
- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/daily_audit.csv`
- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/pit_feature_audit.csv`
- `docs/analysis/2026-07/generated/tmax_path_market_fusion_v2_baseline/model_comparison.csv`
