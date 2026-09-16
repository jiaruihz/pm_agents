# Current-Bracket NO Remaining-Heat Boundary V2

## 结论

V1 的 first-principles target 是对的，但训练分母不够贴交易边界。V2 比较 all rows、near-boundary、trade-base 三种训练分母。
结果：边界训练能改变排序，但没有解决 6/21..6/23 forward；因此当前不是加 gate，而是说明可见特征仍不足以稳定估计临界 remaining heat。

Verdict: `mechanism_boundary_inconclusive_shadow_only`，live_ready=`False`。

## 数据层

- Generated at UTC: `2026-06-24T03:19:21+00:00`
- Raw rows: `4306`
- Mechanism rows: `4306`
- Trade-base rows: `499`
- Split date: `2026-06-10`

## Model Diagnostics

| score_scope | period | rows | active_dates | mae_f | rmse_f | r2 | cross_rate | cross_auc | cross_brier |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all_rows | train_scope | 3033 | 22 | 0.81 | 1.07 | 0.83 | +63.6% | 0.94 | 0.13 |
| all_rows | trade_base_train | 348 | 22 | 0.61 | 0.77 | 0.07 | +17.5% | 0.74 | 0.26 |
| all_rows | trade_base_holdout | 151 | 10 | 0.76 | 0.98 | -0.47 | +18.5% | 0.65 | 0.29 |
| near_boundary_gap_le_2f | train_scope | 3033 | 22 | 0.81 | 1.07 | 0.83 | +63.6% | 0.94 | 0.13 |
| near_boundary_gap_le_2f | trade_base_train | 348 | 22 | 0.61 | 0.77 | 0.07 | +17.5% | 0.74 | 0.26 |
| near_boundary_gap_le_2f | trade_base_holdout | 151 | 10 | 0.76 | 0.98 | -0.47 | +18.5% | 0.65 | 0.29 |
| trade_base | train_scope | 348 | 22 | 0.20 | 0.28 | 0.88 | +17.5% | 0.98 | 0.07 |
| trade_base | trade_base_train | 348 | 22 | 0.20 | 0.28 | 0.88 | +17.5% | 0.98 | 0.07 |
| trade_base | trade_base_holdout | 151 | 10 | 0.58 | 0.84 | -0.07 | +18.5% | 0.61 | 0.25 |

## Trade Variants

| variant | selected_trades | active_dates | win_rate | roi | roi_ci_low | roi_ci_high | holdout_roi | avg_required_gap_f | avg_pred_remaining_heat_f | avg_actual_remaining_heat_f | selected_all_loss_days | selected_all_loss_dates |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all_rows::baseline_trade_base | 375 | 32 | +20.5% | -8.4% | -27.1% | +10.5% | +7.0% | 0.59 | 0.75 | 0.43 | 4 | 2026-05-21,2026-05-25,2026-05-31,2026-06-10 |
| all_rows::remaining_heat_p40_ev10 | 284 | 32 | +24.3% | +7.8% | -15.4% | +30.8% | +26.4% | 0.48 | 0.89 | 0.50 | 5 | 2026-05-21,2026-05-25,2026-05-31,2026-06-01,2026-06-10 |
| all_rows::remaining_heat_p45_ev10 | 257 | 32 | +25.3% | +13.0% | -12.9% | +38.1% | +32.1% | 0.47 | 0.94 | 0.52 | 5 | 2026-05-21,2026-05-25,2026-05-31,2026-06-01,2026-06-10 |
| near_boundary_gap_le_2f::baseline_trade_base | 375 | 32 | +20.5% | -8.4% | -27.1% | +10.5% | +7.0% | 0.59 | 0.75 | 0.43 | 4 | 2026-05-21,2026-05-25,2026-05-31,2026-06-10 |
| near_boundary_gap_le_2f::remaining_heat_p40_ev10 | 284 | 32 | +24.3% | +7.8% | -15.4% | +30.8% | +26.4% | 0.48 | 0.89 | 0.50 | 5 | 2026-05-21,2026-05-25,2026-05-31,2026-06-01,2026-06-10 |
| near_boundary_gap_le_2f::remaining_heat_p45_ev10 | 257 | 32 | +25.3% | +13.0% | -12.9% | +38.1% | +32.1% | 0.47 | 0.94 | 0.52 | 5 | 2026-05-21,2026-05-25,2026-05-31,2026-06-01,2026-06-10 |
| trade_base::baseline_trade_base | 375 | 32 | +20.5% | -8.4% | -27.1% | +10.5% | +7.0% | 0.59 | 0.39 | 0.43 | 4 | 2026-05-21,2026-05-25,2026-05-31,2026-06-10 |
| trade_base::remaining_heat_p40_ev10 | 132 | 31 | +47.0% | +104.2% | +60.0% | +148.4% | +25.5% | 0.37 | 0.81 | 0.92 | 5 | 2026-05-21,2026-05-25,2026-06-10,2026-06-15,2026-06-18 |
| trade_base::remaining_heat_p45_ev10 | 122 | 31 | +50.0% | +115.4% | +68.6% | +162.5% | +25.5% | 0.37 | 0.86 | 0.98 | 5 | 2026-05-21,2026-05-25,2026-06-10,2026-06-15,2026-06-18 |

## Forward 6/21..6/23

| variant | selected_trades | settled_trades | open_shadow_trades | settled_win_rate | settled_roi | settled_profit_usd | dates |
| --- | --- | --- | --- | --- | --- | --- | --- |
| all_rows::baseline_trade_base | 45 | 36 | 9 | +13.9% | -53.8% | $-96.90 | 2026-06-21,2026-06-22,2026-06-23 |
| all_rows::remaining_heat_p40_ev10 | 31 | 22 | 9 | +13.6% | -56.4% | $-62.05 | 2026-06-21,2026-06-22,2026-06-23 |
| all_rows::remaining_heat_p45_ev10 | 28 | 19 | 9 | +15.8% | -49.5% | $-47.05 | 2026-06-21,2026-06-22,2026-06-23 |
| near_boundary_gap_le_2f::baseline_trade_base | 45 | 36 | 9 | +13.9% | -53.8% | $-96.90 | 2026-06-21,2026-06-22,2026-06-23 |
| near_boundary_gap_le_2f::remaining_heat_p40_ev10 | 31 | 22 | 9 | +13.6% | -56.4% | $-62.05 | 2026-06-21,2026-06-22,2026-06-23 |
| near_boundary_gap_le_2f::remaining_heat_p45_ev10 | 28 | 19 | 9 | +15.8% | -49.5% | $-47.05 | 2026-06-21,2026-06-22,2026-06-23 |
| trade_base::baseline_trade_base | 45 | 36 | 9 | +13.9% | -53.8% | $-96.90 | 2026-06-21,2026-06-22,2026-06-23 |
| trade_base::remaining_heat_p40_ev10 | 18 | 11 | 7 | +0.0% | -100.0% | $-55.00 | 2026-06-21,2026-06-22,2026-06-23 |
| trade_base::remaining_heat_p45_ev10 | 17 | 10 | 7 | +0.0% | -100.0% | $-50.00 | 2026-06-21,2026-06-22,2026-06-23 |

## 机制判断

1. 全量模型会被大量非临界行主导，trade-base holdout 才是关键诊断层。
2. trade-base/near-boundary 训练没有把 forward 拉正，说明当前可见特征还不能稳定估计临界剩余升温。
3. 下一步应补真正机制特征：forecast hourly curve 的剩余斜率、观测曲线 plateau count、太阳高度/海风/云量变化，而不是继续换阈值。

## Files

- JSON: `docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_boundary_v2/summary.json`
- Variants: `docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_boundary_v2/variant_summary.csv`
- Forward: `docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_boundary_v2/forward_validation_and_shadow.csv`
