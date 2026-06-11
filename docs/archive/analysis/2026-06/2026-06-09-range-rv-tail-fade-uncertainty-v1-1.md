# Tail Fade / Uncertainty Range RV v1.1

> generated_at_utc: `2026-06-09T15:17:24.790572+00:00`
> target_metric: `forecast_tail_fade_range_rv_alpha`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: local counterfactual research only; no N100/live config changed.

## 数据快照

- 数据源: `runtime/weather.db.fact_signal_candidates` / `fact_trades`; raw orderbook snapshots only for executable price matching.
- DB last_modified: `2026-06-09T15:09:06.567273+00:00`
- fact_signal_candidates built at: `2026-06-09T15:09:02.643978+00:00`
- fact_trades built at: `2026-06-09T15:08:45.011642+00:00`
- CLOB gate pass: `True`; reasons: `[]`.
- 本报告不发布 live_real PnL/ROI；CLOB gate 失败时也只影响 live_real 绩效发布，不改变本地 counterfactual 机会表实验。

## Target Metric

`forecast_tail_fade_range_rv_alpha` = forecast low-tail-risk / market high-tail-mass 条件下，BUY_NO tail + BUY_YES adjacent inner/center basket 相对同表达未过滤 baseline 的 excess ROI。

固定表达：`below_tail_no_inner_yes`、`above_tail_no_inner_yes`、`both_tails_no_inner_yes`、`both_tails_no_center_yes`。没有使用旧单腿 `eligible` 作为硬门。

## Filter Funnel

| step | count |
|---|---:|
| `fact_signal_candidates_rows` | 25100 |
| `settled_decision_window_present_rows` | 2292 |
| `decision_sets_count` | 660 |
| `below_tail_candidate_count` | 271 |
| `above_tail_candidate_count` | 271 |
| `both_tail_candidate_count` | 542 |
| `base_strategy_rows` | 1084 |
| `generated_strategy_rows` | 19512 |
| `orderbook_matched_base_rows` | 561 |
| `orderbook_matched_generated_rows` | 10098 |
| `train_rows` | 14544 |
| `holdout_rows` | 4968 |
| `train_active_dates` | 21 |
| `holdout_active_dates` | 9 |

### Strategy Filter Grid

| algorithm | base | market overpriced | model risk | inner edge | spread | after uncertainty/no-trade |
|---|---:|---:|---:|---:|---:|---:|
| `above_tail_no_inner_yes_mt05_mr05_none` | 271 | 118 | 64 | 1 | 1 | 1 |
| `above_tail_no_inner_yes_mt05_mr05_entropy_le_080` | 271 | 118 | 64 | 1 | 1 | 1 |
| `above_tail_no_inner_yes_mt05_mr10_none` | 271 | 118 | 86 | 4 | 4 | 4 |
| `above_tail_no_inner_yes_mt05_mr10_entropy_le_080` | 271 | 118 | 86 | 4 | 4 | 1 |
| `above_tail_no_inner_yes_mt05_mr15_none` | 271 | 118 | 100 | 5 | 5 | 5 |
| `above_tail_no_inner_yes_mt05_mr15_entropy_le_080` | 271 | 118 | 100 | 5 | 5 | 1 |
| `above_tail_no_inner_yes_mt10_mr05_none` | 271 | 103 | 56 | 1 | 1 | 1 |
| `above_tail_no_inner_yes_mt10_mr05_entropy_le_080` | 271 | 103 | 56 | 1 | 1 | 1 |
| `above_tail_no_inner_yes_mt10_mr10_none` | 271 | 103 | 77 | 3 | 3 | 3 |
| `above_tail_no_inner_yes_mt10_mr10_entropy_le_080` | 271 | 103 | 77 | 3 | 3 | 1 |
| `above_tail_no_inner_yes_mt10_mr15_none` | 271 | 103 | 89 | 4 | 4 | 4 |
| `above_tail_no_inner_yes_mt10_mr15_entropy_le_080` | 271 | 103 | 89 | 4 | 4 | 1 |
| `above_tail_no_inner_yes_mt15_mr05_none` | 271 | 76 | 39 | 1 | 1 | 1 |
| `above_tail_no_inner_yes_mt15_mr05_entropy_le_080` | 271 | 76 | 39 | 1 | 1 | 1 |
| `above_tail_no_inner_yes_mt15_mr10_none` | 271 | 76 | 56 | 3 | 3 | 3 |
| `above_tail_no_inner_yes_mt15_mr10_entropy_le_080` | 271 | 76 | 56 | 3 | 3 | 1 |
| `above_tail_no_inner_yes_mt15_mr15_none` | 271 | 76 | 63 | 3 | 3 | 3 |
| `above_tail_no_inner_yes_mt15_mr15_entropy_le_080` | 271 | 76 | 63 | 3 | 3 | 1 |
| `below_tail_no_inner_yes_mt05_mr05_none` | 271 | 138 | 67 | 2 | 2 | 2 |
| `below_tail_no_inner_yes_mt05_mr05_entropy_le_080` | 271 | 138 | 67 | 2 | 2 | 2 |
| `below_tail_no_inner_yes_mt05_mr10_none` | 271 | 138 | 97 | 12 | 12 | 12 |
| `below_tail_no_inner_yes_mt05_mr10_entropy_le_080` | 271 | 138 | 97 | 12 | 12 | 6 |
| `below_tail_no_inner_yes_mt05_mr15_none` | 271 | 138 | 114 | 15 | 15 | 15 |
| `below_tail_no_inner_yes_mt05_mr15_entropy_le_080` | 271 | 138 | 114 | 15 | 15 | 7 |
| `below_tail_no_inner_yes_mt10_mr05_none` | 271 | 110 | 52 | 2 | 2 | 2 |
| `below_tail_no_inner_yes_mt10_mr05_entropy_le_080` | 271 | 110 | 52 | 2 | 2 | 2 |
| `below_tail_no_inner_yes_mt10_mr10_none` | 271 | 110 | 76 | 10 | 10 | 10 |
| `below_tail_no_inner_yes_mt10_mr10_entropy_le_080` | 271 | 110 | 76 | 10 | 10 | 5 |
| `below_tail_no_inner_yes_mt10_mr15_none` | 271 | 110 | 92 | 13 | 13 | 13 |
| `below_tail_no_inner_yes_mt10_mr15_entropy_le_080` | 271 | 110 | 92 | 13 | 13 | 6 |
| `below_tail_no_inner_yes_mt15_mr05_none` | 271 | 74 | 30 | 2 | 2 | 2 |
| `below_tail_no_inner_yes_mt15_mr05_entropy_le_080` | 271 | 74 | 30 | 2 | 2 | 2 |
| `below_tail_no_inner_yes_mt15_mr10_none` | 271 | 74 | 52 | 9 | 9 | 9 |
| `below_tail_no_inner_yes_mt15_mr10_entropy_le_080` | 271 | 74 | 52 | 9 | 9 | 4 |
| `below_tail_no_inner_yes_mt15_mr15_none` | 271 | 74 | 65 | 12 | 12 | 12 |
| `below_tail_no_inner_yes_mt15_mr15_entropy_le_080` | 271 | 74 | 65 | 12 | 12 | 5 |
| `both_tails_no_center_yes_mt05_mr05_none` | 271 | 84 | 4 | 4 | 4 | 4 |
| `both_tails_no_center_yes_mt05_mr05_entropy_le_080` | 271 | 84 | 4 | 4 | 4 | 4 |
| `both_tails_no_center_yes_mt05_mr10_none` | 271 | 84 | 11 | 10 | 10 | 10 |
| `both_tails_no_center_yes_mt05_mr10_entropy_le_080` | 271 | 84 | 11 | 10 | 10 | 8 |
| `both_tails_no_center_yes_mt05_mr15_none` | 271 | 84 | 17 | 16 | 16 | 16 |
| `both_tails_no_center_yes_mt05_mr15_entropy_le_080` | 271 | 84 | 17 | 16 | 16 | 13 |
| `both_tails_no_center_yes_mt10_mr05_none` | 271 | 56 | 3 | 3 | 3 | 3 |
| `both_tails_no_center_yes_mt10_mr05_entropy_le_080` | 271 | 56 | 3 | 3 | 3 | 3 |
| `both_tails_no_center_yes_mt10_mr10_none` | 271 | 56 | 8 | 7 | 7 | 7 |
| `both_tails_no_center_yes_mt10_mr10_entropy_le_080` | 271 | 56 | 8 | 7 | 7 | 6 |
| `both_tails_no_center_yes_mt10_mr15_none` | 271 | 56 | 13 | 12 | 12 | 12 |
| `both_tails_no_center_yes_mt10_mr15_entropy_le_080` | 271 | 56 | 13 | 12 | 12 | 10 |
| `both_tails_no_center_yes_mt15_mr05_none` | 271 | 34 | 2 | 2 | 2 | 2 |
| `both_tails_no_center_yes_mt15_mr05_entropy_le_080` | 271 | 34 | 2 | 2 | 2 | 2 |
| `both_tails_no_center_yes_mt15_mr10_none` | 271 | 34 | 7 | 6 | 6 | 6 |
| `both_tails_no_center_yes_mt15_mr10_entropy_le_080` | 271 | 34 | 7 | 6 | 6 | 5 |
| `both_tails_no_center_yes_mt15_mr15_none` | 271 | 34 | 11 | 10 | 10 | 10 |
| `both_tails_no_center_yes_mt15_mr15_entropy_le_080` | 271 | 34 | 11 | 10 | 10 | 9 |
| `both_tails_no_inner_yes_mt05_mr05_none` | 271 | 84 | 4 | 0 | 0 | 0 |
| `both_tails_no_inner_yes_mt05_mr05_entropy_le_080` | 271 | 84 | 4 | 0 | 0 | 0 |
| `both_tails_no_inner_yes_mt05_mr10_none` | 271 | 84 | 11 | 5 | 5 | 5 |
| `both_tails_no_inner_yes_mt05_mr10_entropy_le_080` | 271 | 84 | 11 | 5 | 5 | 5 |
| `both_tails_no_inner_yes_mt05_mr15_none` | 271 | 84 | 17 | 9 | 9 | 9 |
| `both_tails_no_inner_yes_mt05_mr15_entropy_le_080` | 271 | 84 | 17 | 9 | 9 | 8 |
| `both_tails_no_inner_yes_mt10_mr05_none` | 271 | 56 | 3 | 0 | 0 | 0 |
| `both_tails_no_inner_yes_mt10_mr05_entropy_le_080` | 271 | 56 | 3 | 0 | 0 | 0 |
| `both_tails_no_inner_yes_mt10_mr10_none` | 271 | 56 | 8 | 4 | 4 | 4 |
| `both_tails_no_inner_yes_mt10_mr10_entropy_le_080` | 271 | 56 | 8 | 4 | 4 | 4 |
| `both_tails_no_inner_yes_mt10_mr15_none` | 271 | 56 | 13 | 8 | 8 | 8 |
| `both_tails_no_inner_yes_mt10_mr15_entropy_le_080` | 271 | 56 | 13 | 8 | 8 | 7 |
| `both_tails_no_inner_yes_mt15_mr05_none` | 271 | 34 | 2 | 0 | 0 | 0 |
| `both_tails_no_inner_yes_mt15_mr05_entropy_le_080` | 271 | 34 | 2 | 0 | 0 | 0 |
| `both_tails_no_inner_yes_mt15_mr10_none` | 271 | 34 | 7 | 4 | 4 | 4 |
| `both_tails_no_inner_yes_mt15_mr10_entropy_le_080` | 271 | 34 | 7 | 4 | 4 | 4 |
| `both_tails_no_inner_yes_mt15_mr15_none` | 271 | 34 | 11 | 7 | 7 | 7 |
| `both_tails_no_inner_yes_mt15_mr15_entropy_le_080` | 271 | 34 | 11 | 7 | 7 | 7 |

## Train / Holdout

- Split field: `event_date`; split date `2026-05-29`.
- Train: `2026-05-06` to `2026-05-28`, active dates `21`, generated rows `14544`.
- Holdout: `2026-05-29` to `2026-06-07`, active dates `9`, generated rows `4968`.
- Bootstrap: cluster by `event_date`, iters `1000`.

## Gates

| gate | status |
|---|---|
| `significance` | `FAIL` |
| `baseline` | `FAIL` |
| `forward` | `FAIL` |
| `verdict` | `inconclusive` |

Final verdict: `inconclusive`. 三门未同时通过时只能 `inconclusive`，禁止 live action。

## Decision Proxy Results

| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train baseline | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout baseline | holdout excess | holdout excess CI | holdout top5 removed ROI | holdout worst loss | holdout loss prob | gates | reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `both_tails_no_center_yes_mt15_mr15_entropy_le_080` | 202 | 8 | 6 | 69 | 1 | 1 | +39.8% | [+10.0%, +65.1%] | +5.4% | +34.4% | [+6.0%, +59.4%] | +61.6% | [+61.6%, +61.6%] | +1.9% | +59.7% | [+53.7%, +64.3%] | NA | -0.86 | +13.2% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_center_yes_mt10_mr15_entropy_le_080` | 202 | 9 | 6 | 69 | 1 | 1 | +28.5% | [+6.6%, +64.5%] | +5.4% | +23.1% | [+2.4%, +58.8%] | +61.6% | [+61.6%, +61.6%] | +1.9% | +59.7% | [+53.8%, +65.6%] | NA | -0.86 | +13.2% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_center_yes_mt05_mr15_entropy_le_080` | 202 | 12 | 6 | 69 | 1 | 1 | +28.0% | [+13.3%, +58.8%] | +5.4% | +22.6% | [+9.4%, +50.2%] | +61.6% | [+61.6%, +61.6%] | +1.9% | +59.7% | [+53.7%, +64.4%] | NA | -0.86 | +13.2% | `PASS/PASS/FAIL -> inconclusive` | `holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_center_yes_mt15_mr15_none` | 202 | 8 | 6 | 69 | 2 | 1 | +39.8% | [+8.2%, +65.0%] | +5.4% | +34.4% | [+3.1%, +59.0%] | +56.7% | [+56.7%, +56.7%] | +1.9% | +54.8% | [+48.8%, +60.2%] | NA | -0.91 | +10.9% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_center_yes_mt10_mr15_none` | 202 | 10 | 7 | 69 | 2 | 1 | +25.8% | [+5.5%, +58.7%] | +5.4% | +20.4% | [+2.3%, +50.7%] | +56.7% | [+56.7%, +56.7%] | +1.9% | +54.8% | [+48.7%, +59.6%] | NA | -0.91 | +10.9% | `PASS/PASS/FAIL -> inconclusive` | `holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_center_yes_mt15_mr10_none` | 202 | 5 | 5 | 69 | 1 | 1 | +43.1% | [+18.2%, +60.9%] | +5.4% | +37.7% | [+14.0%, +55.6%] | +52.1% | [+52.1%, +52.1%] | +1.9% | +50.2% | [+44.2%, +56.0%] | NA | -0.97 | +8.6% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_center_yes_mt10_mr10_none` | 202 | 6 | 5 | 69 | 1 | 1 | +26.3% | [-1.9%, +61.0%] | +5.4% | +20.8% | [-5.6%, +55.8%] | +52.1% | [+52.1%, +52.1%] | +1.9% | +50.2% | [+44.3%, +55.7%] | NA | -0.97 | +8.6% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,train_roi_ci_crosses_0,train_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_center_yes_mt05_mr15_none` | 202 | 13 | 7 | 69 | 3 | 2 | +26.0% | [+13.2%, +49.4%] | +5.4% | +20.6% | [+8.8%, +43.5%] | +38.1% | [+1.8%, +56.7%] | +1.9% | +36.1% | [-0.1%, +59.6%] | NA | -0.93 | +9.3% | `PASS/PASS/FAIL -> inconclusive` | `holdout_selected_sample_below_gate,holdout_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_inner_yes_mt10_mr15_none` | 202 | 7 | 6 | 69 | 1 | 1 | +31.8% | [+28.1%, +37.4%] | +6.9% | +25.0% | [+18.3%, +31.9%] | +33.2% | [+33.2%, +33.2%] | -1.3% | +34.5% | [+29.8%, +39.4%] | NA | -1.25 | +13.2% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_inner_yes_mt05_mr15_none` | 202 | 8 | 6 | 69 | 1 | 1 | +31.4% | [+27.4%, +35.3%] | +6.9% | +24.5% | [+17.4%, +30.4%] | +33.2% | [+33.2%, +33.2%] | -1.3% | +34.5% | [+29.8%, +39.7%] | NA | -1.25 | +13.2% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_inner_yes_mt10_mr15_entropy_le_080` | 202 | 6 | 5 | 69 | 1 | 1 | +30.9% | [+26.9%, +36.6%] | +6.9% | +24.0% | [+16.8%, +31.0%] | +33.2% | [+33.2%, +33.2%] | -1.3% | +34.5% | [+29.5%, +39.3%] | NA | -1.25 | +13.2% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_inner_yes_mt15_mr15_entropy_le_080` | 202 | 6 | 5 | 69 | 1 | 1 | +30.9% | [+26.6%, +37.0%] | +6.9% | +24.0% | [+16.8%, +31.3%] | +33.2% | [+33.2%, +33.2%] | -1.3% | +34.5% | [+29.8%, +39.5%] | NA | -1.25 | +13.2% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_inner_yes_mt15_mr15_none` | 202 | 6 | 5 | 69 | 1 | 1 | +30.9% | [+26.6%, +37.4%] | +6.9% | +24.0% | [+16.5%, +31.7%] | +33.2% | [+33.2%, +33.2%] | -1.3% | +34.5% | [+29.5%, +40.0%] | NA | -1.25 | +13.2% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_inner_yes_mt05_mr15_entropy_le_080` | 202 | 7 | 5 | 69 | 1 | 1 | +30.5% | [+26.7%, +33.6%] | +6.9% | +23.7% | [+17.5%, +29.7%] | +33.2% | [+33.2%, +33.2%] | -1.3% | +34.5% | [+29.6%, +39.7%] | NA | -1.25 | +13.2% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_center_yes_mt05_mr10_none` | 202 | 8 | 5 | 69 | 2 | 2 | +24.2% | [+7.8%, +55.2%] | +5.4% | +18.7% | [+0.5%, +48.3%] | +27.0% | [+1.8%, +52.1%] | +1.9% | +25.1% | [+0.2%, +55.5%] | NA | -0.97 | +7.4% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `below_tail_no_inner_yes_mt15_mr10_none` | 202 | 8 | 6 | 69 | 1 | 1 | +48.7% | [+19.3%, +90.6%] | +6.2% | +42.5% | [+10.6%, +86.8%] | -4.8% | [-4.8%, -4.8%] | -1.7% | -3.0% | [-13.1%, +10.3%] | NA | -1.05 | +56.1% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_roi_ci_crosses_0,holdout_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `below_tail_no_inner_yes_mt10_mr10_none` | 202 | 9 | 7 | 69 | 1 | 1 | +39.5% | [+11.5%, +81.1%] | +6.2% | +33.4% | [+2.5%, +75.8%] | -4.8% | [-4.8%, -4.8%] | -1.7% | -3.0% | [-11.6%, +8.5%] | NA | -1.05 | +56.1% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_roi_ci_crosses_0,holdout_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `below_tail_no_inner_yes_mt15_mr15_none` | 202 | 11 | 6 | 69 | 1 | 1 | +26.1% | [+2.2%, +82.1%] | +6.2% | +19.9% | [-3.8%, +76.0%] | -4.8% | [-4.8%, -4.8%] | -1.7% | -3.0% | [-14.8%, +11.4%] | NA | -1.05 | +56.1% | `PASS/FAIL/FAIL -> inconclusive` | `holdout_selected_sample_below_gate,train_excess_ci_crosses_0,holdout_roi_ci_crosses_0,holdout_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `below_tail_no_inner_yes_mt10_mr15_none` | 202 | 12 | 7 | 69 | 1 | 1 | +21.0% | [-2.9%, +59.6%] | +6.2% | +14.8% | [-9.1%, +53.0%] | -4.8% | [-4.8%, -4.8%] | -1.7% | -3.0% | [-12.7%, +12.5%] | NA | -1.05 | +56.1% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_selected_sample_below_gate,train_roi_ci_crosses_0,train_excess_ci_crosses_0,holdout_roi_ci_crosses_0,holdout_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `below_tail_no_inner_yes_mt05_mr10_none` | 202 | 11 | 8 | 69 | 1 | 1 | +18.7% | [-18.2%, +66.9%] | +6.2% | +12.5% | [-26.7%, +63.4%] | -4.8% | [-4.8%, -4.8%] | -1.7% | -3.0% | [-14.8%, +8.6%] | NA | -1.05 | +56.1% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_selected_sample_below_gate,train_roi_ci_crosses_0,train_excess_ci_crosses_0,holdout_roi_ci_crosses_0,holdout_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |

## Executable Orderbook Results

Executable rows require all basket legs to match the latest orderbook snapshot satisfying `orderbook_snapshot_ts <= decision_snapshot_ts_utc`.

- Fully matched base rows: `561` / `1084`.
- Matched expanded rows: `10098`.

| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train baseline | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout baseline | holdout excess | holdout excess CI | holdout top5 removed ROI | holdout worst loss | holdout loss prob | gates | reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `both_tails_no_center_yes_mt15_mr15_entropy_le_080` | 72 | 5 | 3 | 67 | 1 | 1 | +21.5% | [-17.4%, +47.0%] | +2.2% | +19.3% | [-17.1%, +45.4%] | +58.6% | [+58.6%, +58.6%] | -0.1% | +58.8% | [+53.3%, +64.7%] | NA | -0.86 | +13.2% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,train_roi_ci_crosses_0,train_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_center_yes_mt05_mr15_entropy_le_080` | 72 | 9 | 3 | 67 | 1 | 1 | +12.4% | [+2.9%, +20.6%] | +2.2% | +10.2% | [-3.0%, +16.5%] | +58.6% | [+58.6%, +58.6%] | -0.1% | +58.8% | [+53.7%, +65.4%] | NA | -0.86 | +13.2% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,train_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_center_yes_mt10_mr15_entropy_le_080` | 72 | 6 | 3 | 67 | 1 | 1 | +8.3% | [-17.4%, +39.0%] | +2.2% | +6.1% | [-17.1%, +33.1%] | +58.6% | [+58.6%, +58.6%] | -0.1% | +58.8% | [+53.6%, +64.7%] | NA | -0.86 | +13.2% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,train_roi_ci_crosses_0,train_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_center_yes_mt15_mr15_none` | 72 | 5 | 3 | 67 | 2 | 1 | +21.5% | [-17.4%, +47.0%] | +2.2% | +19.3% | [-17.4%, +45.3%] | +52.8% | [+52.8%, +52.8%] | -0.1% | +52.9% | [+47.8%, +58.8%] | NA | -0.91 | +10.9% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,train_roi_ci_crosses_0,train_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_center_yes_mt10_mr15_none` | 72 | 7 | 4 | 67 | 2 | 1 | +7.2% | [-17.4%, +30.0%] | +2.2% | +5.0% | [-14.6%, +17.6%] | +52.8% | [+52.8%, +52.8%] | -0.1% | +52.9% | [+47.6%, +59.1%] | NA | -0.91 | +10.9% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,train_roi_ci_crosses_0,train_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_center_yes_mt15_mr10_none` | 72 | 3 | 3 | 67 | 1 | 1 | +27.7% | [-0.9%, +43.7%] | +2.2% | +25.5% | [+0.2%, +42.6%] | +47.4% | [+47.4%, +47.4%] | -0.1% | +47.5% | [+42.4%, +52.8%] | NA | -0.97 | +8.6% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,train_roi_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_center_yes_mt10_mr10_none` | 72 | 4 | 3 | 67 | 1 | 1 | +7.2% | [-5.1%, +39.0%] | +2.2% | +5.0% | [-12.0%, +33.0%] | +47.4% | [+47.4%, +47.4%] | -0.1% | +47.5% | [+41.9%, +52.8%] | NA | -0.97 | +8.6% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,train_roi_ci_crosses_0,train_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_center_yes_mt05_mr15_none` | 72 | 10 | 4 | 67 | 3 | 2 | +11.3% | [+1.7%, +18.8%] | +2.2% | +9.1% | [-4.5%, +13.1%] | +34.6% | [-0.9%, +52.8%] | -0.1% | +34.7% | [-1.4%, +58.7%] | NA | -0.93 | +9.3% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,train_excess_ci_crosses_0,holdout_roi_ci_crosses_0,holdout_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_inner_yes_mt10_mr15_none` | 72 | 3 | 3 | 67 | 1 | 1 | +30.3% | [+24.2%, +34.8%] | +2.3% | +28.0% | [+15.5%, +34.1%] | +30.9% | [+30.9%, +30.9%] | -3.7% | +34.7% | [+30.2%, +39.7%] | NA | -1.25 | +13.2% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_inner_yes_mt05_mr15_none` | 72 | 4 | 3 | 67 | 1 | 1 | +28.5% | [+24.2%, +34.8%] | +2.3% | +26.2% | [+15.5%, +30.8%] | +30.9% | [+30.9%, +30.9%] | -3.7% | +34.7% | [+30.3%, +39.9%] | NA | -1.25 | +13.2% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_inner_yes_mt10_mr15_entropy_le_080` | 72 | 2 | 2 | 67 | 1 | 1 | +28.2% | [+24.2%, +32.5%] | +2.3% | +25.9% | [+14.7%, +33.9%] | +30.9% | [+30.9%, +30.9%] | -3.7% | +34.7% | [+30.3%, +39.3%] | NA | -1.25 | +13.2% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_inner_yes_mt15_mr15_entropy_le_080` | 72 | 2 | 2 | 67 | 1 | 1 | +28.2% | [+24.2%, +32.5%] | +2.3% | +25.9% | [+14.2%, +33.7%] | +30.9% | [+30.9%, +30.9%] | -3.7% | +34.7% | [+30.5%, +39.2%] | NA | -1.25 | +13.2% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_inner_yes_mt15_mr15_none` | 72 | 2 | 2 | 67 | 1 | 1 | +28.2% | [+24.2%, +32.5%] | +2.3% | +25.9% | [+15.0%, +33.5%] | +30.9% | [+30.9%, +30.9%] | -3.7% | +34.7% | [+30.1%, +40.1%] | NA | -1.25 | +13.2% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_inner_yes_mt05_mr15_entropy_le_080` | 72 | 3 | 2 | 67 | 1 | 1 | +26.6% | [+24.2%, +27.8%] | +2.3% | +24.3% | [+14.9%, +30.4%] | +30.9% | [+30.9%, +30.9%] | -3.7% | +34.7% | [+30.3%, +40.0%] | NA | -1.25 | +13.2% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,holdout_top5_removed_roi_not_positive` |
| `both_tails_no_center_yes_mt05_mr10_none` | 72 | 6 | 3 | 67 | 2 | 2 | +8.5% | [-5.1%, +18.5%] | +2.2% | +6.3% | [-16.4%, +21.5%] | +23.4% | [-0.9%, +47.4%] | -0.1% | +23.5% | [-1.4%, +52.5%] | NA | -0.97 | +7.4% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,train_roi_ci_crosses_0,train_excess_ci_crosses_0,holdout_roi_ci_crosses_0,holdout_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `below_tail_no_inner_yes_mt10_mr10_none` | 73 | 3 | 3 | 67 | 1 | 1 | +40.3% | [-16.0%, +77.9%] | +4.7% | +35.6% | [-29.4%, +81.1%] | -7.4% | [-7.4%, -7.4%] | -4.4% | -3.0% | [-14.6%, +8.1%] | NA | -1.05 | +56.1% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,train_roi_ci_crosses_0,train_excess_ci_crosses_0,holdout_roi_ci_crosses_0,holdout_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `below_tail_no_inner_yes_mt10_mr15_none` | 73 | 3 | 3 | 67 | 1 | 1 | +40.3% | [-16.0%, +77.9%] | +4.7% | +35.6% | [-26.2%, +81.1%] | -7.4% | [-7.4%, -7.4%] | -4.4% | -3.0% | [-14.5%, +8.0%] | NA | -1.05 | +56.1% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,train_roi_ci_crosses_0,train_excess_ci_crosses_0,holdout_roi_ci_crosses_0,holdout_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `below_tail_no_inner_yes_mt15_mr10_none` | 73 | 3 | 3 | 67 | 1 | 1 | +40.3% | [-16.0%, +77.9%] | +4.7% | +35.6% | [-28.2%, +80.8%] | -7.4% | [-7.4%, -7.4%] | -4.4% | -3.0% | [-18.4%, +7.3%] | NA | -1.05 | +56.1% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,train_roi_ci_crosses_0,train_excess_ci_crosses_0,holdout_roi_ci_crosses_0,holdout_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `below_tail_no_inner_yes_mt15_mr15_none` | 73 | 3 | 3 | 67 | 1 | 1 | +40.3% | [-16.0%, +77.9%] | +4.7% | +35.6% | [-28.0%, +80.0%] | -7.4% | [-7.4%, -7.4%] | -4.4% | -3.0% | [-13.5%, +13.0%] | NA | -1.05 | +56.1% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,train_roi_ci_crosses_0,train_excess_ci_crosses_0,holdout_roi_ci_crosses_0,holdout_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |
| `below_tail_no_inner_yes_mt05_mr10_none` | 73 | 5 | 4 | 67 | 1 | 1 | -3.9% | [-54.2%, +77.9%] | +4.7% | -8.6% | [-61.6%, +77.8%] | -7.4% | [-7.4%, -7.4%] | -4.4% | -3.0% | [-12.8%, +8.8%] | NA | -1.05 | +56.1% | `FAIL/FAIL/FAIL -> inconclusive` | `train_selected_sample_below_gate,holdout_selected_sample_below_gate,train_roi_ci_crosses_0,train_excess_ci_crosses_0,holdout_roi_ci_crosses_0,holdout_excess_ci_crosses_0,holdout_top5_removed_roi_not_positive` |

## Best Rows

- Best decision proxy by holdout excess: `both_tails_no_center_yes_mt15_mr15_entropy_le_080` with gates `{'significance': 'FAIL', 'baseline': 'FAIL', 'forward': 'FAIL', 'verdict': 'inconclusive'}`.
- Best executable orderbook by holdout excess: `both_tails_no_center_yes_mt15_mr15_entropy_le_080` with gates `{'significance': 'FAIL', 'baseline': 'FAIL', 'forward': 'FAIL', 'verdict': 'inconclusive'}`.

## Uncertainty Stratification

Stratification is descriptive only; no city/date/model post-selection is used for verdict.

### Decision Proxy

#### `model_entropy_bucket`

| bucket | rows | active dates | cost | pnl | ROI | top5 removed ROI | avg worst loss | avg loss prob |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `entropy_le_0.70` | 220 | 7 | 411.19 | +122.81 | +29.9% | +18.2% | -1.09 | +31.8% |
| `entropy_le_0.85` | 114 | 13 | 150.38 | +35.62 | +23.7% | -21.5% | -1.11 | +38.5% |
| `entropy_gt_0.85` | 18 | 5 | 23.26 | -3.26 | -14.0% | NA | -0.96 | +34.8% |

#### `model_tail_mass_bucket`

| bucket | rows | active dates | cost | pnl | ROI | top5 removed ROI | avg worst loss | avg loss prob |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `model_tail_le_0.10` | 182 | 13 | 308.42 | +87.58 | +28.4% | -8.8% | -1.15 | +35.8% |
| `model_tail_le_0.05` | 108 | 4 | 168.70 | +35.30 | +20.9% | NA | -1.06 | +39.5% |
| `model_tail_le_0.15` | 62 | 8 | 107.71 | +32.29 | +30.0% | -20.0% | -0.96 | +20.0% |

#### `market_tail_mass_bucket`

| bucket | rows | active dates | cost | pnl | ROI | top5 removed ROI | avg worst loss | avg loss prob |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `market_tail_gt_0.25` | 174 | 11 | 283.50 | +84.50 | +29.8% | +21.8% | -1.05 | +31.0% |
| `market_tail_le_0.25` | 156 | 10 | 257.06 | +82.93 | +32.3% | -15.9% | -1.12 | +36.6% |
| `market_tail_le_0.10` | 6 | 1 | 12.68 | -0.68 | -5.4% | NA | -1.11 | +64.6% |
| `market_tail_le_0.15` | 16 | 3 | 31.59 | -11.59 | -36.7% | NA | -1.10 | +32.8% |

#### `tail_mass_delta_bucket`

| bucket | rows | active dates | cost | pnl | ROI | top5 removed ROI | avg worst loss | avg loss prob |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `model_minus_market_le_-0.15` | 300 | 12 | 489.68 | +164.32 | +33.6% | +28.9% | -1.07 | +32.7% |
| `model_minus_market_le_-0.05` | 26 | 5 | 48.98 | +3.02 | +6.2% | NA | -1.19 | +47.0% |
| `model_minus_market_le_-0.10` | 26 | 4 | 46.18 | -12.18 | -26.4% | NA | -1.16 | +37.4% |

#### `decision_hours_to_settle_bucket`

| bucket | rows | active dates | cost | pnl | ROI | top5 removed ROI | avg worst loss | avg loss prob |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `T_22_24` | 350 | 16 | 582.35 | +153.65 | +26.4% | +17.2% | -1.09 | +34.1% |
| `T_24_26` | 2 | 1 | 2.48 | +1.52 | +61.3% | NA | -1.24 | +42.9% |

#### `forecast_source`

| bucket | rows | active dates | cost | pnl | ROI | top5 removed ROI | avg worst loss | avg loss prob |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `open_meteo_live_gfs` | 255 | 12 | 450.17 | +125.83 | +28.0% | +14.5% | -1.11 | +33.3% |
| `open_meteo_live_ecmwf` | 97 | 9 | 134.66 | +29.34 | +21.8% | -21.7% | -1.02 | +36.3% |

#### `model_version`

| bucket | rows | active dates | cost | pnl | ROI | top5 removed ROI | avg worst loss | avg loss prob |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `gfs` | 255 | 12 | 450.17 | +125.83 | +28.0% | +14.5% | -1.11 | +33.3% |
| `ecmwf` | 97 | 9 | 134.66 | +29.34 | +21.8% | -21.7% | -1.02 | +36.3% |

### Executable Orderbook

#### `model_entropy_bucket`

| bucket | rows | active dates | cost | pnl | ROI | top5 removed ROI | avg worst loss | avg loss prob |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `entropy_le_0.70` | 112 | 4 | 228.68 | +53.32 | +23.3% | NA | -1.05 | +32.0% |
| `entropy_le_0.85` | 62 | 8 | 95.13 | +18.87 | +19.8% | -44.0% | -1.09 | +34.3% |
| `entropy_gt_0.85` | 12 | 3 | 19.36 | +0.64 | +3.3% | NA | -1.09 | +37.5% |

#### `model_tail_mass_bucket`

| bucket | rows | active dates | cost | pnl | ROI | top5 removed ROI | avg worst loss | avg loss prob |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `model_tail_le_0.05` | 72 | 3 | 138.37 | +29.63 | +21.4% | NA | -1.05 | +33.4% |
| `model_tail_le_0.10` | 76 | 6 | 128.37 | +21.63 | +16.8% | -75.6% | -1.10 | +40.1% |
| `model_tail_le_0.15` | 38 | 6 | 76.43 | +21.57 | +28.2% | -38.0% | -1.02 | +18.9% |

#### `market_tail_mass_bucket`

| bucket | rows | active dates | cost | pnl | ROI | top5 removed ROI | avg worst loss | avg loss prob |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `market_tail_le_0.25` | 88 | 5 | 150.59 | +55.41 | +36.8% | NA | -1.08 | +37.3% |
| `market_tail_gt_0.25` | 78 | 6 | 149.10 | +32.90 | +22.1% | -7.4% | -1.05 | +26.4% |
| `market_tail_le_0.10` | 6 | 1 | 13.89 | -1.89 | -13.6% | NA | -1.11 | +64.6% |
| `market_tail_le_0.15` | 14 | 2 | 29.58 | -13.58 | -45.9% | NA | -1.08 | +31.4% |

#### `tail_mass_delta_bucket`

| bucket | rows | active dates | cost | pnl | ROI | top5 removed ROI | avg worst loss | avg loss prob |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `model_minus_market_le_-0.15` | 144 | 6 | 257.77 | +84.23 | +32.7% | -7.4% | -1.04 | +30.5% |
| `model_minus_market_le_-0.05` | 24 | 4 | 48.96 | -0.96 | -2.0% | NA | -1.19 | +47.3% |
| `model_minus_market_le_-0.10` | 18 | 3 | 36.43 | -10.43 | -28.6% | NA | -1.10 | +35.2% |

#### `decision_hours_to_settle_bucket`

| bucket | rows | active dates | cost | pnl | ROI | top5 removed ROI | avg worst loss | avg loss prob |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `T_22_24` | 186 | 9 | 343.17 | +72.83 | +21.2% | -12.2% | -1.07 | +33.1% |

#### `forecast_source`

| bucket | rows | active dates | cost | pnl | ROI | top5 removed ROI | avg worst loss | avg loss prob |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `open_meteo_live_gfs` | 130 | 7 | 242.71 | +55.29 | +22.8% | -50.9% | -1.09 | +35.1% |
| `open_meteo_live_ecmwf` | 56 | 5 | 100.46 | +17.54 | +17.5% | NA | -1.02 | +28.5% |

#### `model_version`

| bucket | rows | active dates | cost | pnl | ROI | top5 removed ROI | avg worst loss | avg loss prob |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `gfs` | 130 | 7 | 242.71 | +55.29 | +22.8% | -50.9% | -1.09 | +35.1% |
| `ecmwf` | 56 | 5 | 100.46 | +17.54 | +17.5% | NA | -1.02 | +28.5% |

## Coverage Rings

| ring | status | note |
|---|---|---|
| 1 descriptive slices | covered | fixed expression + uncertainty descriptive splits |
| 2 statistical inference | covered | event_date cluster bootstrap |
| 3 signal discrimination | partial | uses model-vs-market tail mass, not a full IC test |
| 4 probability distribution | partial | entropy/tail-mass layers only, no calibration refit |
| 5 execution microstructure | covered | time-aligned orderbook executable subset |
| 6 capacity | partial | depth fields not yet used for sizing capacity |
| 7 portfolio correlation | partial | event_date clustered, no city covariance model |
| 8 baseline/counterfactual | covered | same-expression unfiltered baseline |

## Conclusion

在 `2026-05-06..2026-06-07`，Tail fade / uncertainty Range RV 相对同表达 baseline 的三门结果为 `{'significance': 'FAIL', 'baseline': 'FAIL', 'forward': 'FAIL', 'verdict': 'inconclusive'}`，结论等级 `inconclusive`。

Shadow/paper suitability: `not_suitable_yet_three_gates_not_passed`.
