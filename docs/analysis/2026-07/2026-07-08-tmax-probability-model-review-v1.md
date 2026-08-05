# Tmax Probability Model Review v1

> generated_at_utc: `2026-07-08T15:17:43Z`
> window: `2026-06-21..2026-07-03`
> Scope: probability-model review only. No live runner/config/order behavior changed.

## 结论

- 概率模型本体不需要推倒：6/21+ forward 上本地 B 系列稳定赢 market-local A，且 date-block delta CI 不跨 0。
- 需要完善的是 `执行用模型`：当前 target-book v2 用 `loo_no_city_source_blend`，但本轮重跑的 proper scoring 最好是 `mkt_city_source_blend`；二者很接近，不能粗暴切 live，但下一版 shadow 应该并行记录 B_score 和 B_exec。
- 当前最大模型缺口不是外部 tmax，而是四桶分布到 exact ladder 的表达层仍偏粗：`current/d1/d2/tail` 可赚钱地排序，但不能完整回答逐档 sibling book、basis 和 close/reopen。
- 校准层没有发现需要紧急 hard gate 的崩坏；但 tail/current 的 bucket bias 需要在 full-ladder / hazard-chain 版本里继续修。

## Evidence Funnel

- Probability rows: `1729`
- Forward dates: `12`
- Cities: `36`
- Methods reviewed: `market_local_norm, mkt_city_source_blend, loo_no_city_source_blend, loo_no_boundary_blend, mkt_regime_blend`

## Overall Proper Scoring

| slot | method | rows | dates | cities | logloss | delta_vs_A_logloss | delta_logloss_ci_low | delta_logloss_ci_high | brier | delta_vs_A_brier | top1 | winner_prob |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B_score | mkt_city_source_blend | 1729 | 12 | 36 | 0.5779 | -0.0658 | -0.1166 | -0.0121 | 0.3207 | -0.0065 | 76.5% | 0.6651 |
| B_no_boundary | loo_no_boundary_blend | 1729 | 12 | 36 | 0.5782 | -0.0655 | -0.1154 | -0.0118 | 0.3210 | -0.0061 | 76.5% | 0.6651 |
| B_exec | loo_no_city_source_blend | 1729 | 12 | 36 | 0.5791 | -0.0645 | -0.1128 | -0.0071 | 0.3218 | -0.0053 | 76.7% | 0.6642 |
| B_mkt_regime | mkt_regime_blend | 1729 | 12 | 36 | 0.5791 | -0.0645 | -0.1128 | -0.0071 | 0.3218 | -0.0053 | 76.7% | 0.6642 |
| A_market | market_local_norm | 1729 | 12 | 36 | 0.6437 | 0.0000 | 0.0000 | 0.0000 | 0.3271 | 0.0000 | 76.7% | 0.6614 |

## Daily Stability

| target_date | method | rows | cities | logloss | delta_vs_A_logloss | brier | delta_vs_A_brier | top1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-21 | mkt_city_source_blend | 201 | 34 | 0.6234 | -0.0104 | 0.3734 | -0.0065 | 70.6% |
| 2026-06-21 | loo_no_city_source_blend | 201 | 34 | 0.6288 | -0.0051 | 0.3776 | -0.0023 | 70.6% |
| 2026-06-22 | mkt_city_source_blend | 215 | 31 | 0.4519 | -0.0275 | 0.2514 | -0.0046 | 81.4% |
| 2026-06-22 | loo_no_city_source_blend | 215 | 31 | 0.4521 | -0.0274 | 0.2500 | -0.0060 | 82.3% |
| 2026-06-23 | mkt_city_source_blend | 146 | 27 | 0.5684 | -0.0094 | 0.3312 | -0.0076 | 72.6% |
| 2026-06-23 | loo_no_city_source_blend | 146 | 27 | 0.5698 | -0.0079 | 0.3314 | -0.0073 | 72.6% |
| 2026-06-25 | mkt_city_source_blend | 222 | 36 | 0.6747 | -0.0769 | 0.3471 | -0.0054 | 77.5% |
| 2026-06-25 | loo_no_city_source_blend | 222 | 36 | 0.6761 | -0.0755 | 0.3460 | -0.0065 | 78.4% |
| 2026-06-26 | loo_no_city_source_blend | 235 | 36 | 0.5221 | -0.0042 | 0.2964 | -0.0015 | 80.0% |
| 2026-06-26 | mkt_city_source_blend | 235 | 36 | 0.5234 | -0.0029 | 0.2971 | -0.0008 | 79.6% |
| 2026-06-27 | loo_no_city_source_blend | 202 | 35 | 0.5642 | -0.1876 | 0.3138 | -0.0194 | 76.7% |
| 2026-06-27 | mkt_city_source_blend | 202 | 35 | 0.5653 | -0.1866 | 0.3136 | -0.0197 | 77.2% |
| 2026-06-28 | mkt_city_source_blend | 28 | 11 | 0.7106 | -0.0387 | 0.4343 | -0.0185 | 71.4% |
| 2026-06-28 | loo_no_city_source_blend | 28 | 11 | 0.7490 | -0.0003 | 0.4627 | 0.0098 | 67.9% |
| 2026-06-29 | loo_no_city_source_blend | 81 | 20 | 0.5317 | 0.0056 | 0.2801 | 0.0104 | 85.2% |
| 2026-06-29 | mkt_city_source_blend | 81 | 20 | 0.5354 | 0.0094 | 0.2794 | 0.0097 | 85.2% |
| 2026-06-30 | loo_no_city_source_blend | 155 | 30 | 0.6569 | -0.3159 | 0.3306 | -0.0076 | 74.8% |
| 2026-06-30 | mkt_city_source_blend | 155 | 30 | 0.6569 | -0.3158 | 0.3275 | -0.0107 | 74.8% |
| 2026-07-01 | loo_no_city_source_blend | 132 | 29 | 0.6020 | 0.0047 | 0.3515 | -0.0010 | 71.2% |
| 2026-07-01 | mkt_city_source_blend | 132 | 29 | 0.6027 | 0.0054 | 0.3502 | -0.0023 | 72.0% |
| 2026-07-02 | mkt_city_source_blend | 32 | 9 | 0.5491 | -0.0507 | 0.3191 | -0.0332 | 68.8% |
| 2026-07-02 | loo_no_city_source_blend | 32 | 9 | 0.5592 | -0.0407 | 0.3268 | -0.0256 | 71.9% |
| 2026-07-03 | mkt_city_source_blend | 80 | 19 | 0.5560 | 0.0151 | 0.3100 | 0.0044 | 78.8% |
| 2026-07-03 | loo_no_city_source_blend | 80 | 19 | 0.5574 | 0.0165 | 0.3096 | 0.0041 | 78.8% |

## Bucket Diagnostics

| actual_bucket | method | rows | dates | logloss | delta_vs_A_logloss | brier | delta_vs_A_brier | winner_prob |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current | mkt_city_source_blend | 850 | 12 | 0.2493 | -0.0024 | 0.1253 | 0.0026 | 0.8369 |
| current | loo_no_city_source_blend | 850 | 12 | 0.2515 | -0.0002 | 0.1266 | 0.0039 | 0.8358 |
| current | market_local_norm | 850 | 12 | 0.2517 | 0.0000 | 0.1227 | 0.0000 | 0.8381 |
| d1 | loo_no_city_source_blend | 392 | 12 | 0.8678 | -0.0301 | 0.5322 | -0.0177 | 0.4771 |
| d1 | mkt_city_source_blend | 392 | 12 | 0.8727 | -0.0252 | 0.5336 | -0.0163 | 0.4763 |
| d1 | market_local_norm | 392 | 12 | 0.8979 | 0.0000 | 0.5499 | 0.0000 | 0.4678 |
| d2 | mkt_city_source_blend | 266 | 12 | 1.1103 | -0.0139 | 0.6372 | -0.0161 | 0.4059 |
| d2 | loo_no_city_source_blend | 266 | 12 | 1.1114 | -0.0128 | 0.6391 | -0.0142 | 0.4050 |
| d2 | market_local_norm | 266 | 12 | 1.1242 | 0.0000 | 0.6533 | 0.0000 | 0.3951 |
| tail | mkt_city_source_blend | 221 | 12 | 0.6779 | -0.4441 | 0.3137 | -0.0123 | 0.6514 |
| tail | loo_no_city_source_blend | 221 | 12 | 0.6865 | -0.4356 | 0.3177 | -0.0082 | 0.6479 |
| tail | market_local_norm | 221 | 12 | 1.1220 | 0.0000 | 0.3259 | 0.0000 | 0.6461 |

## Calibration Watchlist

| method | bucket | rows | mean_p | empirical_freq | bias_p_minus_freq | ece_decile | max_bin_abs_error |
| --- | --- | --- | --- | --- | --- | --- | --- |
| mkt_city_source_blend | d1 | 1729 | 0.2255 | 0.2267 | -0.0012 | 0.0291 | 0.0803 |
| loo_no_city_source_blend | d1 | 1729 | 0.2267 | 0.2267 | -0.0000 | 0.0280 | 0.0788 |
| mkt_city_source_blend | d2 | 1729 | 0.1469 | 0.1538 | -0.0070 | 0.0227 | 0.0692 |
| loo_no_city_source_blend | current | 1729 | 0.4996 | 0.4916 | 0.0080 | 0.0223 | 0.0554 |
| loo_no_city_source_blend | d2 | 1729 | 0.1471 | 0.1538 | -0.0067 | 0.0217 | 0.0690 |
| mkt_city_source_blend | current | 1729 | 0.5003 | 0.4916 | 0.0086 | 0.0211 | 0.0700 |
| mkt_city_source_blend | tail | 1729 | 0.1273 | 0.1278 | -0.0005 | 0.0137 | 0.0543 |
| loo_no_city_source_blend | tail | 1729 | 0.1266 | 0.1278 | -0.0012 | 0.0109 | 0.0460 |

## B_score vs B_exec Disagreement

| metric | value | rows |
| --- | --- | --- |
| top_bucket_disagreement_rate | 0.0191 | 1729 |
| b_score_correct_on_disagreements | 0.3939 | 33 |
| b_exec_correct_on_disagreements | 0.4848 | 33 |

## Expression EV Cross-Check

| scope | method | edge_threshold | selected_rows | dates | cities | cost | pnl | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| expanding_forward | loo_no_city_source_blend | 0.02 | 300 | 8 | 36 | 120.11 | 7.89 | +6.6% | -5.0% | +16.7% |
| expanding_forward | mkt_city_source_blend | 0.02 | 331 | 8 | 36 | 140.59 | 5.41 | +3.8% | -9.3% | +17.5% |
| expanding_forward | market_local_norm | 0.02 | 59 | 8 | 29 | 12.18 | -1.18 | -9.7% | -53.2% | +68.2% |

## Verdict

```text
model_layer = keep current local tmax family
external_model = do not adopt as alpha
next_model_change = shadow B_score and B_exec side-by-side in target-book ledger
structural_upgrade = full-ladder / hazard-chain probability, not another hard gate
live_action = none
```

按三道门：significance=PASS for proper-score delta vs market; baseline=PASS vs market-local probability baseline; forward=PARTIAL/PASS on 12 settled dates but not live-action eligible because execution parity and target-book fresh shadow are still pending. 结论 `shadow_candidate_model_keep_and_refine`。

## Artifacts

- `docs/analysis/2026-07/generated/tmax_probability_model_review_v1/overall_summary.csv`
- `docs/analysis/2026-07/generated/tmax_probability_model_review_v1/daily_summary.csv`
- `docs/analysis/2026-07/generated/tmax_probability_model_review_v1/bucket_summary.csv`
- `docs/analysis/2026-07/generated/tmax_probability_model_review_v1/calibration_summary.csv`
- `docs/analysis/2026-07/generated/tmax_probability_model_review_v1/disagreement_summary.csv`
- `docs/analysis/2026-07/2026-07-08-tmax-probability-model-review-v1.json`
