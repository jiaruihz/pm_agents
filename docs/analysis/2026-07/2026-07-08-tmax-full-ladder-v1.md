# Tmax Full-Ladder v1

> generated_at_utc: `2026-07-08T15:49:03Z`
> Scope: offline model experiment only. No live runner/config/order behavior changed.

## 结论

- 这版把 tail 拆成 `step3` / `step4plus`，并用 direct multinomial 预测 exact-ish step。
- dev-CV 按 exact logloss 选出的 alpha=`0.0`；alpha=0 代表只用原 B_exec 四桶 + 训练窗 tail split。
- forward 诊断上 alpha=`0.25` 的 exact-step logloss 有改善，但 dev 没选中，且聚合回四桶几乎不改善，所以不能升格为执行概率。
- 这不是否定 full-ladder 方向，而是说明“直接多分类 + 现有特征”不够稳；下一步要引入真实逐档 book、station/source basis 和 monotone survival 约束。

## Dev-CV Summary

| scope | method | alpha | rows | dates | cities | exact_logloss | exact_delta_vs_base | bucket_logloss | bucket_delta_vs_base | bucket_top1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dev_cv | base_tail_split | 0.0000 | 2816 | 14 | 36 | 0.6863 | 0.0000 | 0.6152 | 0.0000 | 73.3% |
| dev_cv | full_ladder_v1 | 0.2500 | 2816 | 14 | 36 | 0.6891 | 0.0029 | 0.6226 | 0.0074 | 73.0% |
| dev_cv | full_ladder_v1 | 0.5000 | 2816 | 14 | 36 | 0.7070 | 0.0207 | 0.6420 | 0.0268 | 72.9% |
| dev_cv | full_ladder_v1 | 0.7500 | 2816 | 14 | 36 | 0.7414 | 0.0552 | 0.6753 | 0.0601 | 72.0% |
| dev_cv | full_ladder_v1 | 1.0000 | 2816 | 14 | 36 | 0.8093 | 0.1230 | 0.7372 | 0.1221 | 71.3% |

## Verified Summary

| scope | method | alpha | rows | dates | cities | exact_logloss | exact_delta_vs_base | exact_delta_ci_low | exact_delta_ci_high | bucket_logloss | bucket_delta_vs_base | bucket_top1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| verified_forward | full_ladder_v1 | 0.2500 | 1729 | 12 | 36 | 0.6571 | -0.0109 | -0.0342 | -0.0005 | 0.5794 | 0.0003 | 76.7% |
| verified_forward | full_ladder_v1 | 0.5000 | 1729 | 12 | 36 | 0.6628 | -0.0052 | -0.0503 | 0.0128 | 0.5915 | 0.0124 | 76.8% |
| verified_forward | base_tail_split | 0.0000 | 1729 | 12 | 36 | 0.6680 | 0.0000 | 0.0000 | 0.0000 | 0.5791 | 0.0000 | 76.7% |
| verified_forward | full_ladder_v1 | 0.7500 | 1729 | 12 | 36 | 0.6825 | 0.0145 | -0.0528 | 0.0394 | 0.6148 | 0.0357 | 75.5% |
| verified_forward | full_ladder_v1 | 1.0000 | 1729 | 12 | 36 | 0.7263 | 0.0583 | -0.0369 | 0.0919 | 0.6592 | 0.0800 | 74.5% |

## Slice Summary

| slice | method | alpha | rows | dates | cities | exact_logloss | exact_logloss_delta_vs_base | bucket_logloss | bucket_logloss_delta_vs_base | bucket_top1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| active_warming | full_ladder_v1 | 0.2500 | 739 | 12 | 36 | 0.8908 | -0.0264 | 0.7395 | -0.0051 | 70.1% |
| active_warming | base_tail_split | 0.0000 | 739 | 12 | 36 | 0.9172 | 0.0000 | 0.7446 | 0.0000 | 70.0% |
| all_verified | full_ladder_v1 | 0.2500 | 1729 | 12 | 36 | 0.6571 | -0.0109 | 0.5794 | 0.0003 | 76.7% |
| all_verified | base_tail_split | 0.0000 | 1729 | 12 | 36 | 0.6680 | 0.0000 | 0.5791 | 0.0000 | 76.7% |
| overshoot_actual | full_ladder_v1 | 0.2500 | 879 | 12 | 36 | 1.0117 | -0.0590 | 0.8589 | -0.0370 | 63.8% |
| overshoot_actual | base_tail_split | 0.0000 | 879 | 12 | 36 | 1.0708 | 0.0000 | 0.8960 | 0.0000 | 62.8% |
| reanchored_up | base_tail_split | 0.0000 | 1095 | 12 | 36 | 0.5506 | 0.0000 | 0.5192 | 0.0000 | 78.6% |
| reanchored_up | full_ladder_v1 | 0.2500 | 1095 | 12 | 36 | 0.5507 | 0.0001 | 0.5233 | 0.0041 | 79.5% |
| tail_actual | full_ladder_v1 | 0.2500 | 221 | 12 | 34 | 1.1863 | -0.1954 | 0.5784 | -0.1080 | 80.1% |
| tail_actual | base_tail_split | 0.0000 | 221 | 12 | 34 | 1.3817 | 0.0000 | 0.6865 | 0.0000 | 77.8% |

## Verdict

```text
full_ladder_v1 = not promoted
keep = B_exec four-bucket for target-book shadow
next = monotone full-ladder / survival with real sibling book and source-basis features
live_action = none
```

## Artifacts

- `docs/analysis/2026-07/generated/tmax_full_ladder_v1/scored_rows.csv`
- `docs/analysis/2026-07/generated/tmax_full_ladder_v1/summary.csv`
- `docs/analysis/2026-07/generated/tmax_full_ladder_v1/slice_summary.csv`
- `docs/analysis/2026-07/2026-07-08-tmax-full-ladder-v1.json`
