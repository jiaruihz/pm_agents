# Current-Bracket NO Regime Atlas Overlay V1

## 结论

先纠正口径：最早 classifier 用的 `actual_peak_afternoon` 不是“决策后会不会升破当前温度”，而是“全天最高温第一次出现是否在 13 点以后”。这个 label 和 current-bracket NO payoff 相关，但不等价。真正交易 payoff 是 `label_no_wins = 1 - current_bracket_held`，或 remaining-heat 口径里的 `future_delta_to_daymax_f > required_gap_f`。

把 intraday regime atlas 加进去以后，regime 能解释坏日/坏状态，但第一版 soft sizing 没有把策略变成 confirmed。它更适合作为共享机制特征和 forward 记录字段，而不是直接当买卖规则。

Verdict: `atlas_feature_layer_useful_but_not_confirmed_trading_rule`，live_ready=`False`。

## Label Mismatch

| cohort | rows | actual_peak_afternoon_rate | future_max_above_running_rate | no_win_rate | roi | afternoon_yes_no_loss_rows | afternoon_yes_no_loss_share_of_afternoon | fp_peak_already_started_share | fp_final_did_not_cross_upper_share | fp_future_max_above_running_share |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all_h10_14_current_no_states | 4306 | +62.7% | +65.4% | +62.9% | -14.8% | 405 | 0.150 | +87.4% | +61.5% | +18.8% |
| trade_base_ask10_35_depth5 | 499 | +39.1% | +20.0% | +17.8% | -19.8% | 111 | 0.569 | +96.4% | +65.8% | +8.1% |
| classifier_v1_selected_logit_c0p2_ge_0p50 | 150 | +78.0% | +36.7% | +32.0% | +37.6% | 70 | 0.598 | +95.7% | +62.9% | +8.6% |

解释：`actual_peak_afternoon=1` 只说明最高温首次出现时间在午后；如果 13 点已经触顶，14 点买入时后面不再创新高，它仍会被这个 label 算作 afternoon peak。另一个错配来自最高温没有穿过所买 bracket upper + margin，NO payoff 仍输。少数明细里 raw/native 温度看似略高于 upper，但 settlement 的 `current_bracket_held` 仍为 1；这类属于 source/rounding/market-bracket 口径，交易 payoff 必须以 settlement label 为准。

## Base P40 By Day Regime

| slice | trades | active_dates | win_rate | roi | avg_no_ask | avg_p_cross | avg_p_cap | avg_actual_margin_f | avg_pred_error_f |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| day_space_unknown | 4 | 1 | +75.0% | +151.5% | 0.268 | 0.566 | 0.677 | 0.737 | -0.559 |
| day_marginal_runway | 70 | 28 | +35.7% | +47.7% | 0.243 | 0.634 | 0.566 | 0.144 | 0.282 |
| day_open_runway | 42 | 23 | +31.0% | +39.5% | 0.244 | 0.681 | 0.572 | 0.185 | 0.430 |
| day_forecast_busted | 103 | 34 | +19.4% | -7.9% | 0.225 | 0.629 | 0.686 | -0.010 | 0.393 |
| day_forecast_capped | 91 | 30 | +17.6% | -22.3% | 0.234 | 0.611 | 0.641 | -0.143 | 0.465 |

## Base P40 By Intraday State

| slice | trades | active_dates | win_rate | roi | avg_no_ask | avg_p_cross | avg_p_cap | avg_actual_margin_f | avg_pred_error_f |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| reheating_after_dip | 2 | 2 | +100.0% | +293.7% | 0.274 | 0.693 | 0.448 | 1.450 | -0.477 |
| false_fade_risk | 49 | 26 | +26.5% | +33.0% | 0.237 | 0.599 | 0.698 | 0.098 | 0.193 |
| mature_fade | 27 | 21 | +29.6% | +32.5% | 0.236 | 0.591 | 0.753 | 0.254 | 0.018 |
| fresh_high | 61 | 28 | +24.6% | +4.5% | 0.237 | 0.611 | 0.593 | -0.080 | 0.452 |
| active_warming | 107 | 34 | +24.3% | +2.9% | 0.235 | 0.655 | 0.556 | -0.127 | 0.591 |
| slow_warming | 9 | 7 | +22.2% | -1.2% | 0.211 | 0.725 | 0.515 | 0.306 | 0.441 |
| plateau_near_high | 44 | 23 | +20.5% | -13.8% | 0.238 | 0.669 | 0.689 | 0.200 | 0.300 |
| pullback_uncertain | 11 | 10 | +18.2% | -25.9% | 0.220 | 0.518 | 0.842 | -0.082 | 0.137 |

## Rolling 2-Day Soft Sizing

| policy | blocks | total_trades | weighted_cost_usd | weighted_profit_usd | cost_weighted_roi | positive_blocks | bad_blocks_roi_le_minus50 | avg_notional_retained |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| atlas_physical_trade_cap_soft_size | 33 | 625 | $+1,087.94 | $+78.20 | +7.2% | 18 | 4 | +34.5% |
| atlas_train_shrunk_trade_cap_soft_size | 33 | 625 | $+1,315.79 | $+51.78 | +3.9% | 17 | 5 | +41.9% |
| atlas_physical_soft_size | 33 | 625 | $+1,968.75 | $+7.72 | +0.4% | 15 | 3 | +62.8% |
| trade_cap_soft_size | 33 | 625 | $+1,692.91 | $-4.29 | -0.3% | 16 | 6 | +53.9% |
| atlas_train_shrunk_soft_size | 33 | 625 | $+2,397.24 | $-73.49 | -3.1% | 16 | 4 | +76.7% |
| full_size_base_p40 | 33 | 625 | $+3,125.00 | $-180.44 | -5.8% | 15 | 4 | +100.0% |

## 6/21-6/22 Forward Stress Block

| policy | settled_trades | weighted_cost_usd | weighted_profit_usd | roi | notional_retained | avg_atlas_physical_multiplier | avg_trade_cap_multiplier | day_regime_mix |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| atlas_physical_trade_cap_soft_size | 26 | $+56.06 | $-28.80 | -51.4% | +43.1% | 0.690 | 0.596 | day_forecast_busted:6,day_forecast_capped:4,day_marginal_runway:6,day_open_runway:5,day_space_unknown:5 |
| atlas_physical_soft_size | 26 | $+89.75 | $-44.83 | -50.0% | +69.0% | 0.690 | 0.596 | day_forecast_busted:6,day_forecast_capped:4,day_marginal_runway:6,day_open_runway:5,day_space_unknown:5 |
| atlas_train_shrunk_trade_cap_soft_size | 26 | $+66.71 | $-33.01 | -49.5% | +51.3% | 0.690 | 0.596 | day_forecast_busted:6,day_forecast_capped:4,day_marginal_runway:6,day_open_runway:5,day_space_unknown:5 |
| atlas_train_shrunk_soft_size | 26 | $+110.22 | $-54.50 | -49.4% | +84.8% | 0.690 | 0.596 | day_forecast_busted:6,day_forecast_capped:4,day_marginal_runway:6,day_open_runway:5,day_space_unknown:5 |
| full_size_base_p40 | 26 | $+130.00 | $-62.05 | -47.7% | +100.0% | 0.690 | 0.596 | day_forecast_busted:6,day_forecast_capped:4,day_marginal_runway:6,day_open_runway:5,day_space_unknown:5 |
| trade_cap_soft_size | 26 | $+77.48 | $-36.26 | -46.8% | +59.6% | 0.690 | 0.596 | day_forecast_busted:6,day_forecast_capped:4,day_marginal_runway:6,day_open_runway:5,day_space_unknown:5 |

这一块仍然很差：atlas+trade_cap 没救回 6/21-6/22，只是减少暴露。说明 atlas 第一版能解释 regime，不足以独立解决 forward tail。

## Interpretation

1. 你说的逻辑在严格定义下是对的：如果真的在决策时买 running max 所在单档 NO，且后面真实升破该档，那么 NO 应该赢。
2. 早期错位不是这个物理逻辑错，而是 label 用了 `actual_peak_afternoon` 这种日级 peak-time proxy；它没有要求“决策后继续升破所买档”。
3. atlas 加入后能看出 current-bracket NO 最怕 `forecast_capped/busted` 与成熟回落状态；但简单物理 multiplier / train-shrunk multiplier 仍只是减 tail，不是 confirmed alpha。
4. 下一步应该把 atlas 字段接进 frozen forward ledger，每天记录 regime，再等新 settled dates，而不是直接把某个 regime 变 hard gate。

## Files

- JSON: `docs/analysis/2026-06/generated/current_bracket_no_regime_atlas_overlay_v1/summary.json`
- Label mismatch details: `docs/analysis/2026-06/generated/current_bracket_no_regime_atlas_overlay_v1/afternoon_label_payoff_mismatch.csv`
- Regime slice performance: `docs/analysis/2026-06/generated/current_bracket_no_regime_atlas_overlay_v1/base_p40_regime_slice_performance.csv`
- Rolling blocks: `docs/analysis/2026-06/generated/current_bracket_no_regime_atlas_overlay_v1/atlas_soft_sizing_rolling_blocks.csv`
- Policy summary: `docs/analysis/2026-06/generated/current_bracket_no_regime_atlas_overlay_v1/atlas_soft_sizing_policy_summary.csv`
