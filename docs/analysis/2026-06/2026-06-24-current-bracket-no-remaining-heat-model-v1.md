# Current-Bracket NO Remaining-Heat Model V1

## 结论

这版把信号机制改成 payoff 对齐：不再预测“午后 peak”或简单升温，而是预测从 decision running max 到日最高温的`remaining_heat`，再和穿过当前 bracket upper 所需的 `required_gap` 比较。

Verdict: `mechanism_rewrite_shadow_only`，live_ready=`False`。

## 数据层

- Generated at UTC: `2026-06-24T03:17:41+00:00`
- Sync/rebuild: `sync_weather_remote.sh completed; run_stack rebuilt facts and CLOB gate passed, then exited non-clean only because FE port 5174 stayed busy.`
- Raw rows: `4306`
- Mechanism rows: `4306`
- Trade-base rows: `499`
- Date range: `2026-05-20`..`2026-06-20`
- Split date: `2026-06-10`

## Model Diagnostics

| period | rows | active_dates | mae_f | rmse_f | r2 | cross_rate | cross_auc | cross_brier |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| train | 3033 | 22 | 0.81 | 1.07 | 0.83 | +63.6% | 0.94 | 0.13 |
| holdout | 1273 | 10 | 1.12 | 1.51 | 0.60 | +64.3% | 0.88 | 0.16 |
| trade_base_holdout | 151 | 10 | 0.76 | 0.98 | -0.47 | +18.5% | 0.65 | 0.29 |

## Trade Variants

| variant | selected_trades | active_dates | cities | win_rate | roi | roi_ci_low | roi_ci_high | holdout_roi | avg_required_gap_f | avg_pred_remaining_heat_f | avg_actual_remaining_heat_f | avg_actual_margin_f | selected_all_loss_days |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_trade_base | 375 | 32 | 35 | +20.5% | -8.4% | -27.1% | +10.5% | +7.0% | 0.59 | 0.75 | 0.43 | -0.16 | 4 |
| remaining_heat_p35_ev05 | 310 | 32 | 34 | +23.9% | +6.3% | -15.5% | +28.4% | +19.8% | 0.50 | 0.84 | 0.49 | -0.01 | 5 |
| remaining_heat_p40_ev10 | 284 | 32 | 34 | +24.3% | +7.8% | -15.4% | +30.8% | +26.4% | 0.48 | 0.89 | 0.50 | 0.02 | 5 |
| remaining_heat_p45_ev10 | 257 | 32 | 34 | +25.3% | +13.0% | -12.9% | +38.1% | +32.1% | 0.47 | 0.94 | 0.52 | 0.05 | 5 |
| remaining_heat_p40_ev10_max2_day | 59 | 32 | 27 | +28.8% | +48.4% | -12.7% | +117.3% | +35.9% | 0.49 | 1.56 | 0.66 | 0.17 | 17 |

## All-Loss Dates

| variant | target_date | trades | wins | roi | avg_p_cross | avg_required_gap_f | avg_pred_remaining_heat_f | avg_actual_remaining_heat_f | avg_margin_f | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_trade_base | 2026-05-21 | 6 | 0 | -100.0% | 0.52 | 0.57 | 0.61 | 0.00 | -0.57 | Beijing,Helsinki,LA,SanFrancisco,SaoPaulo,Shanghai |
| baseline_trade_base | 2026-05-25 | 12 | 0 | -100.0% | 0.54 | 0.54 | 0.67 | 0.00 | -0.54 | Beijing,Busan,Helsinki,Jeddah,Karachi,LA,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| baseline_trade_base | 2026-05-31 | 9 | 0 | -100.0% | 0.57 | 0.77 | 0.98 | 0.11 | -0.66 | Amsterdam,Atlanta,Busan,LA,Munich,Shanghai,Taipei,TelAviv,Wellington |
| baseline_trade_base | 2026-06-10 | 13 | 0 | -100.0% | 0.50 | 0.61 | 0.63 | 0.08 | -0.53 | Amsterdam,BuenosAires,Helsinki,Houston,Karachi,Manila,Miami,Munich,Shanghai,Taipei,TelAviv,Warsaw,Wellington |
| remaining_heat_p35_ev05 | 2026-05-21 | 4 | 0 | -100.0% | 0.62 | 0.51 | 0.83 | 0.00 | -0.51 | Beijing,LA,SaoPaulo,Shanghai |
| remaining_heat_p35_ev05 | 2026-05-25 | 9 | 0 | -100.0% | 0.63 | 0.36 | 0.74 | 0.00 | -0.36 | Beijing,Jeddah,Karachi,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| remaining_heat_p35_ev05 | 2026-05-31 | 7 | 0 | -100.0% | 0.64 | 0.75 | 1.16 | 0.14 | -0.61 | Amsterdam,Atlanta,Busan,LA,Munich,Taipei,Wellington |
| remaining_heat_p35_ev05 | 2026-06-01 | 12 | 0 | -100.0% | 0.61 | 0.35 | 0.69 | 0.00 | -0.35 | BuenosAires,Busan,CapeTown,Chongqing,Istanbul,LA,Manila,Singapore,TelAviv,Warsaw,Wellington,Wuhan |
| remaining_heat_p35_ev05 | 2026-06-10 | 10 | 0 | -100.0% | 0.58 | 0.49 | 0.74 | 0.00 | -0.49 | Amsterdam,BuenosAires,Karachi,Miami,Munich,Shanghai,Taipei,TelAviv,Warsaw,Wellington |
| remaining_heat_p40_ev10 | 2026-05-21 | 4 | 0 | -100.0% | 0.62 | 0.51 | 0.83 | 0.00 | -0.51 | Beijing,LA,SaoPaulo,Shanghai |
| remaining_heat_p40_ev10 | 2026-05-25 | 9 | 0 | -100.0% | 0.63 | 0.36 | 0.74 | 0.00 | -0.36 | Beijing,Jeddah,Karachi,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| remaining_heat_p40_ev10 | 2026-05-31 | 7 | 0 | -100.0% | 0.64 | 0.75 | 1.16 | 0.14 | -0.61 | Amsterdam,Atlanta,Busan,LA,Munich,Taipei,Wellington |
| remaining_heat_p40_ev10 | 2026-06-01 | 12 | 0 | -100.0% | 0.61 | 0.35 | 0.69 | 0.00 | -0.35 | BuenosAires,Busan,CapeTown,Chongqing,Istanbul,LA,Manila,Singapore,TelAviv,Warsaw,Wellington,Wuhan |
| remaining_heat_p40_ev10 | 2026-06-10 | 8 | 0 | -100.0% | 0.63 | 0.45 | 0.85 | 0.00 | -0.45 | Amsterdam,BuenosAires,Karachi,Munich,Taipei,TelAviv,Warsaw,Wellington |
| remaining_heat_p40_ev10_max2_day | 2026-05-21 | 2 | 0 | -100.0% | 0.68 | 0.78 | 1.26 | 0.00 | -0.78 | Beijing,LA |
| remaining_heat_p40_ev10_max2_day | 2026-05-23 | 1 | 0 | -100.0% | 0.91 | 0.85 | 2.25 | 0.00 | -0.85 | SaoPaulo |
| remaining_heat_p40_ev10_max2_day | 2026-05-24 | 2 | 0 | -100.0% | 0.69 | 0.15 | 0.67 | 0.00 | -0.15 | TelAviv,Tokyo |
| remaining_heat_p40_ev10_max2_day | 2026-05-25 | 2 | 0 | -100.0% | 0.76 | 0.65 | 1.42 | 0.00 | -0.65 | Shanghai,Tokyo |
| remaining_heat_p40_ev10_max2_day | 2026-05-30 | 1 | 0 | -100.0% | 0.95 | 0.05 | 1.82 | 0.00 | -0.05 | Warsaw |
| remaining_heat_p40_ev10_max2_day | 2026-05-31 | 2 | 0 | -100.0% | 0.82 | 0.75 | 1.73 | 0.00 | -0.75 | Amsterdam,Munich |
| remaining_heat_p40_ev10_max2_day | 2026-06-01 | 2 | 0 | -100.0% | 0.81 | 0.45 | 1.37 | 0.00 | -0.45 | Warsaw,Wellington |
| remaining_heat_p40_ev10_max2_day | 2026-06-02 | 2 | 0 | -100.0% | 0.81 | 0.35 | 1.29 | 0.00 | -0.35 | SaoPaulo,Shanghai |
| remaining_heat_p40_ev10_max2_day | 2026-06-05 | 2 | 0 | -100.0% | 0.71 | 0.45 | 1.05 | 0.00 | -0.45 | Busan,SaoPaulo |
| remaining_heat_p40_ev10_max2_day | 2026-06-09 | 2 | 0 | -100.0% | 0.75 | 0.88 | 1.63 | 0.50 | -0.38 | Seattle,Taipei |
| remaining_heat_p40_ev10_max2_day | 2026-06-10 | 2 | 0 | -100.0% | 0.85 | 0.25 | 1.34 | 0.00 | -0.25 | BuenosAires,Karachi |
| remaining_heat_p40_ev10_max2_day | 2026-06-11 | 2 | 0 | -100.0% | 0.89 | 0.25 | 1.58 | 0.00 | -0.25 | Lucknow,Wuhan |
| remaining_heat_p40_ev10_max2_day | 2026-06-12 | 2 | 0 | -100.0% | 0.88 | 0.25 | 1.72 | 0.00 | -0.25 | Lucknow,Taipei |
| remaining_heat_p40_ev10_max2_day | 2026-06-14 | 2 | 0 | -100.0% | 0.92 | 0.05 | 1.56 | 0.00 | -0.05 | Guangzhou,Lucknow |
| remaining_heat_p40_ev10_max2_day | 2026-06-16 | 2 | 0 | -100.0% | 0.93 | 0.55 | 2.30 | 0.00 | -0.55 | Shanghai,TelAviv |
| remaining_heat_p40_ev10_max2_day | 2026-06-19 | 2 | 0 | -100.0% | 0.77 | 0.35 | 1.15 | 0.00 | -0.35 | CapeTown,Wellington |
| remaining_heat_p40_ev10_max2_day | 2026-06-20 | 1 | 0 | -100.0% | 0.84 | 0.05 | 1.10 | 0.00 | -0.05 | Wellington |
| remaining_heat_p45_ev10 | 2026-05-21 | 4 | 0 | -100.0% | 0.62 | 0.51 | 0.83 | 0.00 | -0.51 | Beijing,LA,SaoPaulo,Shanghai |
| remaining_heat_p45_ev10 | 2026-05-25 | 9 | 0 | -100.0% | 0.63 | 0.36 | 0.74 | 0.00 | -0.36 | Beijing,Jeddah,Karachi,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| remaining_heat_p45_ev10 | 2026-05-31 | 7 | 0 | -100.0% | 0.64 | 0.75 | 1.16 | 0.14 | -0.61 | Amsterdam,Atlanta,Busan,LA,Munich,Taipei,Wellington |
| remaining_heat_p45_ev10 | 2026-06-01 | 10 | 0 | -100.0% | 0.65 | 0.30 | 0.75 | 0.00 | -0.30 | CapeTown,Chongqing,Istanbul,LA,Manila,Singapore,TelAviv,Warsaw,Wellington,Wuhan |
| remaining_heat_p45_ev10 | 2026-06-10 | 6 | 0 | -100.0% | 0.70 | 0.52 | 1.10 | 0.00 | -0.52 | BuenosAires,Karachi,Munich,TelAviv,Warsaw,Wellington |

## Forward 6/21..6/23

| variant | selected_trades | settled_trades | open_shadow_trades | settled_win_rate | settled_roi | settled_profit_usd | dates |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_trade_base | 45 | 36 | 9 | +13.9% | -53.8% | $-96.90 | 2026-06-21,2026-06-22,2026-06-23 |
| remaining_heat_p35_ev05 | 36 | 27 | 9 | +18.5% | -38.4% | $-51.90 | 2026-06-21,2026-06-22,2026-06-23 |
| remaining_heat_p40_ev10 | 31 | 22 | 9 | +13.6% | -56.4% | $-62.05 | 2026-06-21,2026-06-22,2026-06-23 |
| remaining_heat_p45_ev10 | 28 | 19 | 9 | +15.8% | -49.5% | $-47.05 | 2026-06-21,2026-06-22,2026-06-23 |
| remaining_heat_p40_ev10_max2_day | 6 | 4 | 2 | +0.0% | -100.0% | $-20.00 | 2026-06-21,2026-06-22,2026-06-23 |

## 读法

1. 这是机制重写，不是新增 gate：分数本身就是 `P(remaining_heat > required_gap)`。
2. 如果全错日仍集中，说明我们缺的是 remaining-heat 特征，而不是再补一条日期过滤。
3. 这版仍使用 forced-GFS 做主对照，原因是上一轮 source-policy A/B 显示 source route 不是根因，且非 GFS 缺 true PIT。

## Files

- JSON: `docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_model_v1/summary.json`
- Variants: `docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_model_v1/variant_summary.csv`
- Daily: `docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_model_v1/daily_variant_summary.csv`
- Selected rows: `docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_model_v1/selected_trade_rows.csv`
- Forward: `docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_model_v1/forward_validation_and_shadow.csv`
