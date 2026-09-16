# Tmax Hazard-Chain v0

> generated_at_utc: `2026-07-08T15:45:01Z`
> Scope: offline model experiment only. No live runner/config/order behavior changed.

## 结论

- 这个 v0 不是完整 full-ladder；它先把四桶分布改写为 sequential hazard：break current / break d1 / break d2。
- dev-CV 选出的 best spec=`hazard_city`，alpha=`0.0`；alpha 表示用多少 hazard overlay，0 表示完全退回原四桶。
- 结果：hazard-chain v0 没有稳定打赢原 `B_exec` 四桶；尤其 re-anchor/overshoot 切片没有给出足够改善。
- 所以当前不能把 hazard v0 直接接进执行。正确动作是保留 B_exec，并把下一版升级定义为更真实的 full-ladder/逐档模型，而不是这个简单 hazard overlay。

## Evidence Funnel

- Source rows: `5472`
- Walk-forward scored rows: `9090`
- Date range: `2026-06-02..2026-07-03`

## Verified Summary

| spec | method | alpha | scope | rows | dates | cities | logloss | logloss_delta_vs_base | logloss_delta_ci_low | logloss_delta_ci_high | brier | brier_delta_vs_base | top1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hazard_city | base_four_bucket | 0.0000 | verified_forward | 1729 | 12 | 36 | 0.5791 | 0.0000 | 0.0000 | 0.0000 | 0.3218 | 0.0000 | 76.7% |
| hazard_city | hazard_chain_v0 | 0.0000 | verified_forward | 1729 | 12 | 36 | 0.5791 | 0.0000 | 0.0000 | 0.0000 | 0.3218 | -0.0000 | 76.7% |
| hazard_context | base_four_bucket | 0.0000 | verified_forward | 1729 | 12 | 36 | 0.5791 | 0.0000 | 0.0000 | 0.0000 | 0.3218 | 0.0000 | 76.7% |

## Slice Summary

| slice | method | alpha | rows | dates | cities | logloss | logloss_delta_vs_base | brier | brier_delta_vs_base | top1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| active_warming | base_four_bucket | 0.0000 | 739 | 12 | 36 | 0.7446 | 0.0000 | 0.4199 | 0.0000 | 70.0% |
| active_warming | hazard_chain_v0 | 0.0000 | 739 | 12 | 36 | 0.7446 | 0.0000 | 0.4199 | 0.0000 | 70.0% |
| all_verified | base_four_bucket | 0.0000 | 1729 | 12 | 36 | 0.5791 | 0.0000 | 0.3218 | 0.0000 | 76.7% |
| all_verified | hazard_chain_v0 | 0.0000 | 1729 | 12 | 36 | 0.5791 | 0.0000 | 0.3218 | 0.0000 | 76.7% |
| overshoot_actual | base_four_bucket | 0.0000 | 879 | 12 | 36 | 0.8960 | 0.0000 | 0.5106 | 0.0000 | 62.8% |
| overshoot_actual | hazard_chain_v0 | 0.0000 | 879 | 12 | 36 | 0.8960 | 0.0000 | 0.5106 | 0.0000 | 62.8% |
| plateau_or_fade | base_four_bucket | 0.0000 | 285 | 12 | 36 | 0.4535 | 0.0000 | 0.2290 | 0.0000 | 84.9% |
| plateau_or_fade | hazard_chain_v0 | 0.0000 | 285 | 12 | 36 | 0.4535 | 0.0000 | 0.2290 | 0.0000 | 84.9% |
| reanchored_up | base_four_bucket | 0.0000 | 1095 | 12 | 36 | 0.5192 | 0.0000 | 0.2953 | 0.0000 | 78.6% |
| reanchored_up | hazard_chain_v0 | 0.0000 | 1095 | 12 | 36 | 0.5192 | 0.0000 | 0.2953 | 0.0000 | 78.6% |
| tail_actual | base_four_bucket | 0.0000 | 221 | 12 | 34 | 0.6865 | 0.0000 | 0.3177 | 0.0000 | 77.8% |
| tail_actual | hazard_chain_v0 | 0.0000 | 221 | 12 | 34 | 0.6865 | 0.0000 | 0.3177 | 0.0000 | 77.8% |

## Interpretation

这回答了刚才的第一性原理问题：first-lock 不是因为第一次触发天然最优，而是因为当前模型还没证明后验更新能转成净增 EV。hazard-chain v0 尝试让后验更新更符合“继续突破几档”的物理结构，但在现有分母上还没赢。

下一步如果继续做模型升级，应跳过这个浅层 overlay，直接做 full-ladder v1：逐 exact bracket 概率、按 sibling book 对齐、输出每个 bracket YES/NO 的 `p_win`，再给 target-book/rebalance 使用。

## Verdict

```text
hazard_chain_v0 = not promoted
keep = B_exec four-bucket target-book probability
next_model = full-ladder v1, not hazard overlay
live_action = none
```

## Artifacts

- `docs/analysis/2026-07/generated/tmax_hazard_chain_v0/scored_rows.csv`
- `docs/analysis/2026-07/generated/tmax_hazard_chain_v0/summary.csv`
- `docs/analysis/2026-07/generated/tmax_hazard_chain_v0/slice_summary.csv`
- `docs/analysis/2026-07/2026-07-08-tmax-hazard-chain-v0.json`
