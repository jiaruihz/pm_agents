# Tmax Full-Ladder Survival v2

> generated_at_utc: `2026-07-08T16:14:02Z`
> Scope: offline model experiment only. No live runner/config/order behavior changed.
>
> Status: `historical structure experiment / superseded_for_decision_use`.
> Dev-CV selected `alpha=0`; the reported non-zero `alpha=0.35` forward result
> was a diagnostic challenger, not the selected model. The later clean
> `tmax_distribution_v3` and D1 bounded-reheat studies did not beat the
> same-row market baseline. This report supports monotone probability
> coherence, not a promoted hazard alpha.

## 结论

- 这版把 full-ladder 改成 monotone survival：逐档估计 `P(final step > k | final step > k-1)`，再还原为 `current/d1/d2/step3/step4plus`。
- 相比 v1 的 direct multinomial，这版概率天然单调，不会出现高阶尾部概率结构不一致的问题。
- dev-CV 按 bucket logloss 选中 spec=`survival_city` alpha=`0.0`；alpha=0 仍代表只用 B_exec 四桶 + 训练窗 tail split。
- forward 诊断最好的非零版本是 spec=`survival_city` alpha=`0.35`。
- Verdict: `research_only_not_promoted`。

## Evidence Funnel

- Source rows: `5472`
- Scored rows: `190890`
- Date range: `2026-06-02..2026-07-03`
- Train cutoff: `2026-06-21`

## Dev-CV Summary

| scope | spec | method | alpha | rows | dates | cities | exact_logloss | exact_delta_vs_base | bucket_logloss | bucket_delta_vs_base | bucket_top1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dev_cv | survival_city | base_tail_split | 0.0000 | 2816 | 14 | 36 | 0.6863 | 0.0000 | 0.6152 | 0.0000 | 73.3% |
| dev_cv | survival_context | base_tail_split | 0.0000 | 2816 | 14 | 36 | 0.6863 | 0.0000 | 0.6152 | 0.0000 | 73.3% |
| dev_cv | survival_context | full_ladder_survival_v2 | 0.0500 | 2816 | 14 | 36 | 0.6853 | -0.0010 | 0.6154 | 0.0003 | 73.3% |
| dev_cv | survival_city | full_ladder_survival_v2 | 0.0500 | 2816 | 14 | 36 | 0.6855 | -0.0007 | 0.6157 | 0.0005 | 73.3% |
| dev_cv | survival_context | full_ladder_survival_v2 | 0.1000 | 2816 | 14 | 36 | 0.6846 | -0.0016 | 0.6159 | 0.0007 | 73.0% |
| dev_cv | survival_city | full_ladder_survival_v2 | 0.1000 | 2816 | 14 | 36 | 0.6851 | -0.0012 | 0.6163 | 0.0012 | 73.2% |
| dev_cv | survival_context | full_ladder_survival_v2 | 0.1500 | 2816 | 14 | 36 | 0.6842 | -0.0020 | 0.6166 | 0.0014 | 73.0% |
| dev_cv | survival_city | full_ladder_survival_v2 | 0.1500 | 2816 | 14 | 36 | 0.6849 | -0.0014 | 0.6172 | 0.0020 | 73.1% |

## Verified Summary

| scope | spec | method | alpha | rows | dates | cities | exact_logloss | exact_delta_vs_base | exact_delta_ci_low | exact_delta_ci_high | bucket_logloss | bucket_delta_vs_base | bucket_delta_ci_low | bucket_delta_ci_high | bucket_top1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| verified_forward | survival_city | full_ladder_survival_v2 | 0.3500 | 1729 | 12 | 36 | 0.6494 | -0.0186 | -0.0437 | -0.0123 | 0.5755 | -0.0037 | -0.0199 | -0.0008 | 76.5% |
| verified_forward | survival_city | base_tail_split | 0.0000 | 1729 | 12 | 36 | 0.6680 | 0.0000 | 0.0000 | 0.0000 | 0.5791 | 0.0000 | 0.0000 | 0.0000 | 76.7% |
| verified_forward | survival_context | base_tail_split | 0.0000 | 1729 | 12 | 36 | 0.6680 | 0.0000 | 0.0000 | 0.0000 | 0.5791 | 0.0000 | 0.0000 | 0.0000 | 76.7% |

## Effect Size

- Exact-step logloss delta `-0.0186` means the geometric mean probability assigned to the exact winner moved `+1.9%`: `0.5127` -> `0.5224`.
- Four-bucket logloss delta `-0.0037` means the geometric mean probability assigned to the true trading bucket moved `+0.4%`: `0.5604` -> `0.5624`.
- Over `1729` verified rows, the bucket cumulative log-likelihood gain is `6.3325` nats. This is visible statistically, but small per decision.

## Slice Summary

| slice | method | alpha | rows | dates | cities | exact_logloss | exact_logloss_delta_vs_base | bucket_logloss | bucket_logloss_delta_vs_base | bucket_top1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| active_warming | full_ladder_survival_v2 | 0.3500 | 739 | 12 | 36 | 0.8835 | -0.0338 | 0.7384 | -0.0062 | 70.2% |
| active_warming | base_tail_split | 0.0000 | 739 | 12 | 36 | 0.9172 | 0.0000 | 0.7446 | 0.0000 | 70.0% |
| all_verified | full_ladder_survival_v2 | 0.3500 | 1729 | 12 | 36 | 0.6494 | -0.0186 | 0.5755 | -0.0037 | 76.5% |
| all_verified | base_tail_split | 0.0000 | 1729 | 12 | 36 | 0.6680 | 0.0000 | 0.5791 | 0.0000 | 76.7% |
| overshoot_actual | full_ladder_survival_v2 | 0.3500 | 879 | 12 | 36 | 1.0317 | -0.0390 | 0.8863 | -0.0096 | 63.0% |
| overshoot_actual | base_tail_split | 0.0000 | 879 | 12 | 36 | 1.0708 | 0.0000 | 0.8960 | 0.0000 | 62.8% |
| reanchored_up | full_ladder_survival_v2 | 0.3500 | 1095 | 12 | 36 | 0.5433 | -0.0073 | 0.5166 | -0.0026 | 78.4% |
| reanchored_up | base_tail_split | 0.0000 | 1095 | 12 | 36 | 0.5506 | 0.0000 | 0.5192 | 0.0000 | 78.6% |
| tail_actual | full_ladder_survival_v2 | 0.3500 | 221 | 12 | 34 | 1.2383 | -0.1434 | 0.6599 | -0.0265 | 78.7% |
| tail_actual | base_tail_split | 0.0000 | 221 | 12 | 34 | 1.3817 | 0.0000 | 0.6865 | 0.0000 | 77.8% |

## Interpretation

这版检验的是 full-ladder 是否能从“尾部解释更好”推进到“交易四桶也更好”。如果 dev-CV 仍选 alpha=0，说明当前特征和样本下还不能替代 B_exec；如果非零 alpha 在 forward 切片改善但 dev 不选，只能作为下一版特征方向。

## Verdict

```text
full_ladder_survival_v2 = research_only_not_promoted
live_action = none
```

## Artifacts

- `docs/analysis/2026-07/generated/tmax_full_ladder_survival_v2/source_rows.csv`
- `docs/analysis/2026-07/generated/tmax_full_ladder_survival_v2/scored_rows.csv`
- `docs/analysis/2026-07/generated/tmax_full_ladder_survival_v2/summary.csv`
- `docs/analysis/2026-07/generated/tmax_full_ladder_survival_v2/slice_summary.csv`
- `docs/analysis/2026-07/2026-07-08-tmax-full-ladder-survival-v2.json`
