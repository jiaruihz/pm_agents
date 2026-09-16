# External Tmax Baseline v0

> generated_at_utc: `2026-07-09T03:41:56Z`
> external_repo_head: `35b3fdc054491cdfb3fceb55921e53901e2f0325`
> Scope: research/shadow only. No live runner/config/order behavior changed.

## 结论

- 这版把 `polymarket-tmax-lab` 可迁移的概率形状做成同分母外部对照组：daily-max forecast error -> Gaussian outcome probabilities -> market blend。
- 它不是外部 alpha 复刻；没有使用外部 live/execution/ROI 逻辑，只用它的概率工程思想挑战我们当前 tmax 模型。
- pre-cutoff expanding CV 选出的 market blend alpha = `0.0`；alpha 越低表示外部 raw forecast 分布越不如盘口。
- 交易 replay 使用 exact-book bridge 同一表达集合：`current_no/d1_no/d2_no/d1_yes/d2_yes`，ask>=0.40，fee-adjusted edge>=0.02，每 city-day 第一条。

## Probability Score

| scope | method | n | dates | cities | logloss | logloss_delta_vs_market | brier | brier_delta_vs_market | top1 | winner_prob |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dev_cv | external_gaussian_market_blend | 1743 | 9 | 36 | 0.6885 | -0.0000 | 0.3726 | 0.0000 | 0.71 | 0.6297 |
| dev_cv | market_local_norm | 1743 | 9 | 36 | 0.6885 | 0.0000 | 0.3726 | 0.0000 | 0.71 | 0.6297 |
| dev_cv | external_gaussian_recency | 1743 | 9 | 36 | 1.7294 | 1.0409 | 0.8950 | 0.5223 | 0.25 | 0.2626 |
| verified_forward | external_gaussian_market_blend | 1080 | 14 | 36 | 0.6821 | -0.0000 | 0.3243 | -0.0000 | 0.77 | 0.6642 |
| verified_forward | market_local_norm | 1080 | 14 | 36 | 0.6821 | 0.0000 | 0.3243 | 0.0000 | 0.77 | 0.6642 |
| verified_forward | external_gaussian_recency | 1080 | 14 | 36 | 1.6940 | 1.0119 | 0.8377 | 0.5133 | 0.30 | 0.2968 |

## Trade Replay

| scope | method | rows | dates | cities | win_rate | cost_net | pnl_net | roi_net | roi_net_ci_low | roi_net_ci_high | current_no_rows | d1_no_rows | d2_no_rows | d1_yes_rows | d2_yes_rows |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dev_cv | external_gaussian_market_blend | 6 | 3 | 6 | 50.0% | 3.22 | -0.22 | -6.8% | -16.8% | +5.8% | 1 | 4 | 1 | 0 | 0 |
| verified_forward | external_gaussian_market_blend | 12 | 10 | 9 | 83.3% | 7.54 | 2.46 | +32.6% | +1.2% | +59.4% | 8 | 2 | 2 | 0 | 0 |

## Verdict

conclusion=`external_baseline_candidate_for_v2_blend`.

该对照组只有在同分母 logloss/EV 同时稳定优于 `our_tmax_current` 时才值得进入 v2 blend；否则只保留 previous-runs/source-profile/calibration checker 的工程方法。

## Artifacts

- `docs/analysis/2026-07/generated/external_tmax_baseline_v0/probability_rows.csv`
- `docs/analysis/2026-07/generated/external_tmax_baseline_v0/probability_scores.csv`
- `docs/analysis/2026-07/generated/external_tmax_baseline_v0/probability_summary.csv`
- `docs/analysis/2026-07/generated/external_tmax_baseline_v0/expression_candidates.csv`
- `docs/analysis/2026-07/generated/external_tmax_baseline_v0/selected_trades.csv`
- `docs/analysis/2026-07/generated/external_tmax_baseline_v0/trade_summary.csv`
- `docs/analysis/2026-07/2026-07-08-external-tmax-baseline-v0.json`
