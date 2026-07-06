# Tmax Cross-Hour Coherence Audit v2

> generated_at_utc: `2026-07-06T15:24:40+00:00`
> Scope: research-only. No tmax live runner/config/order behavior changed.

## 结论 / 交易动作

- Fable 的核心修正成立：P0e v1 的 `no_reanchor +7pp` 主要是 coherent baseline 没有吃进“又过一小时仍未突破”的 survival 信息，不是一个可交易的 no-reanchor debt。
- 修正后 `no_reanchor` verified 残差从约 `+7.0pp` 收敛到接近 `0pp`；`d1_reanchor` 仍接近 0 或略负，`d2_reanchor` 负。结论：不要做 `reanchor penalty`，Lucknow 是尾部个案，不是系统性 reanchor 规则。
- E2 below materializer 这轮用官方 `settlement_outcomes` 重新 join，仍得到 `below_rows=0`。这不等于 below 风险不存在，而是说明当前 materialized current/running-max 分母没有覆盖 Fable 所说的 below 负例；上一版 `current_yes verified +25.8%` 必须标为 `censored-inflated`，不能作为 live/current-YES 依据。
- Hazard-chain 原型已跑：它是正确方向的模型形态，但在当前四桶、below 未补齐的分母上只能算 shadow prototype；是否继续投入看它是否稳定压过 market 分布评分。
- 交易动作：`tmax live` 继续停；`current_yes` 继续 shadow；下一步不是加 gate，而是把真实 five-bucket/below 目标和 target-book ledger 做成可重放账本。

## 数据与冻结口径

- Materialized rows: `8028` rows, `2026-05-19`..`2026-07-03`, `36` cities.
- P0e v2 survival update: `p_current/(p_current+(1-p_current)*q)` with `q=0.5268` from dev_cv `638/1211` non-current paths that still had no next-bracket break by the next adjacent hour.
- E2 official settlement join: `{'atlas_rows': 13860, 'atlas_date_range': ['2026-05-19', '2026-07-04'], 'settlement_winners': 2551, 'settlement_date_range': ['2026-05-04', '2026-07-05'], 'joined_rows': 13852, 'below_rows_settlement': 0}`

## P0e v2 Coherence

| grouping | scope | reanchor_group | rows | dates | cities | v1_incoherence_mean | v2_incoherence_mean | v2_minus_v1_mean | positive_rate_v1 | positive_rate_v2 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| reanchor_group | n/a | d1_reanchor | 1292 | 31 | 36 | +0.9% | +0.9% | +0.0% | +53.9% | +53.9% |
| reanchor_group | n/a | d2_reanchor | 246 | 31 | 36 | -5.2% | -5.2% | -0.0% | +28.5% | +28.5% |
| reanchor_group | n/a | no_reanchor | 2941 | 31 | 36 | +7.1% | -0.2% | -7.3% | +78.3% | +50.5% |
| scope+reanchor_group | dev_cv | d1_reanchor | 870 | 19 | 36 | +1.8% | +1.8% | +0.0% | +56.3% | +56.3% |
| scope+reanchor_group | dev_cv | d2_reanchor | 170 | 19 | 36 | -4.9% | -4.9% | -0.0% | +32.9% | +32.9% |
| scope+reanchor_group | dev_cv | no_reanchor | 2039 | 19 | 36 | +7.2% | -0.2% | -7.4% | +79.0% | +51.1% |
| scope+reanchor_group | verified_forward | d1_reanchor | 422 | 12 | 36 | -1.0% | -1.0% | -0.0% | +48.8% | +48.8% |
| scope+reanchor_group | verified_forward | d2_reanchor | 76 | 12 | 28 | -5.9% | -5.9% | -0.0% | +18.4% | +18.4% |
| scope+reanchor_group | verified_forward | no_reanchor | 902 | 12 | 36 | +7.0% | +0.0% | -7.0% | +76.8% | +49.3% |

## E2 Below-Bucket Audit

Interpretation: `below_rows=0` after official settlement join means the present materialized layer cannot answer E2. It also means any four-bucket current-YES ROI remains censored because the target space itself has not been rebuilt to emit below/current/d1/d2/tail.

| source | slice | rows | cities | dates | below_rows | below_rate | current | d1 | d2 | tail | other | missing |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bucket_from_settlement | train_pre_2026_06_21 | 10792 | 36 | 33 | 0 | +0.0% | 4245 | 1485 | 954 | 409 | 611 | 3088 |
| bucket_from_settlement | forward_2026_06_21_plus | 3068 | 36 | 13 | 0 | +0.0% | 1082 | 412 | 273 | 119 | 154 | 1028 |
| bucket_from_atlas_final | train_pre_2026_06_21 | 10792 | 36 | 33 | 0 | +0.0% | 4245 | 1485 | 954 | 409 | 611 | 3088 |
| bucket_from_atlas_final | forward_2026_06_21_plus | 3068 | 36 | 13 | 0 | +0.0% | 1082 | 412 | 273 | 119 | 154 | 1028 |

## P1 Hazard-Chain Prototype

This prototype models `P(escape current)`, then `P(escape d1 | escaped current)`, then `P(tail | escaped d2)` with physical/context features and no per-city free parameter. It is a prototype on the current four-bucket layer, not a live-ready model.

| scope | alpha | rows | dates | logloss | market_logloss | logloss_delta_vs_market | brier | market_brier | brier_delta_vs_market |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| verified_forward | 0.2500 | 1729 | 12 | 0.6110 | 0.6438 | -3.3% | 0.3348 | 0.3271 | +0.8% |
| verified_forward | 0.0000 | 1729 | 12 | 0.6438 | 0.6438 | +0.0% | 0.3271 | 0.3271 | -0.0% |
| verified_forward | 0.5000 | 1729 | 12 | 0.6616 | 0.6438 | +1.8% | 0.3606 | 0.3271 | +3.3% |
| verified_forward | 0.7500 | 1729 | 12 | 0.7345 | 0.6438 | +9.1% | 0.4046 | 0.3271 | +7.7% |
| verified_forward | 1.0000 | 1729 | 12 | 0.8466 | 0.6438 | +20.3% | 0.4667 | 0.3271 | +14.0% |

## 三道门

- significance: `PARTIAL` for P0e v2 correction; `FAIL/UNANSWERED` for E2 below because current layer still has zero below rows.
- baseline: `PARTIAL` for hazard-chain prototype only if it beats market scoring on verified_forward; otherwise `FAIL`.
- forward: `FAIL` for live promotion. No fresh forward target-book ledger yet, no below target, and no position-aware execution replay attached to real holdings.

Verdict: `shadow_candidate_model_rebuild_not_live`.

## Artifacts

- `docs/analysis/2026-07/generated/tmax_cross_hour_coherence_audit_v2/coherence_pairs_v2.csv`
- `docs/analysis/2026-07/generated/tmax_cross_hour_coherence_audit_v2/coherence_summary_v2.csv`
- `docs/analysis/2026-07/generated/tmax_cross_hour_coherence_audit_v2/e2_below_materializer_rows.csv`
- `docs/analysis/2026-07/generated/tmax_cross_hour_coherence_audit_v2/e2_below_summary.csv`
- `docs/analysis/2026-07/generated/tmax_cross_hour_coherence_audit_v2/hazard_chain_predictions.csv`
- `docs/analysis/2026-07/generated/tmax_cross_hour_coherence_audit_v2/hazard_chain_scorecard.csv`
- `docs/analysis/2026-07/2026-07-06-tmax-cross-hour-coherence-audit-v2.json`
