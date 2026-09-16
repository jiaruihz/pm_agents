# Current-Bracket NO Signal Swap Stability V1

## 结论

这次换训练/验证窗口后，结论不是“某些城市该删”，而是当前 remaining-heat 信号本身跨窗口不稳定。early 训练出来的 enhanced 模型在 late 能改善部分指标，但 late 训练反看 early 和 forward 都不能给出稳定交易收益；p_cross 分桶也不是稳定单调。问题集中在 capped-day/model-overestimate：模型能识别一部分午后升温机会，但没有稳定识别“会不会真的突破 current upper + margin”。

Verdict: `signal_mechanism_not_stable_enough_for_live`，live_ready=`False`。

## Data

- Generated at UTC: `2026-06-24T07:54:03+00:00`
- Historical dates: `2026-05-20`..`2026-06-20`
- Historical mechanism rows: `4306`
- Historical trade-base rows: `499`
- Forward rows: `580`

## Model Swap Metrics

| train_spec | eval_window | rows | active_dates | trade_base_rows | mae_f | rmse_f | r2 | cross_rate | cross_auc | cross_brier |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base_early_all | early | 3033 | 22 | 348 | 0.811 | 1.066 | 0.830 | +63.6% | 0.945 | 0.130 |
| base_early_all | late | 1273 | 10 | 151 | 1.119 | 1.508 | 0.596 | +64.3% | 0.877 | 0.157 |
| base_early_all | forward_settled | 434 | 2 | 50 | 0.898 | 1.410 | 0.111 | +22.1% | 0.848 | 0.251 |
| base_late_all | early | 3033 | 22 | 348 | 1.192 | 1.575 | 0.629 | +63.6% | 0.885 | 0.184 |
| base_late_all | late | 1273 | 10 | 151 | 0.631 | 0.828 | 0.878 | +64.3% | 0.965 | 0.110 |
| base_late_all | forward_settled | 434 | 2 | 50 | 1.035 | 1.614 | -0.164 | +22.1% | 0.813 | 0.293 |
| enhanced_early_all | early | 3033 | 22 | 348 | 0.735 | 0.958 | 0.863 | +63.6% | 0.953 | 0.125 |
| enhanced_early_all | late | 1273 | 10 | 151 | 1.110 | 1.500 | 0.600 | +64.3% | 0.883 | 0.156 |
| enhanced_early_all | forward_settled | 434 | 2 | 50 | 0.927 | 1.409 | 0.113 | +22.1% | 0.824 | 0.255 |
| enhanced_late_all | early | 3033 | 22 | 348 | 1.197 | 1.583 | 0.625 | +63.6% | 0.873 | 0.187 |
| enhanced_late_all | late | 1273 | 10 | 151 | 0.542 | 0.704 | 0.912 | +64.3% | 0.974 | 0.099 |
| enhanced_late_all | forward_settled | 434 | 2 | 50 | 1.149 | 1.606 | -0.153 | +22.1% | 0.775 | 0.371 |

## Fixed Signal Variant: p40_ev10

| train_spec | eval_window | selected_trades | settled_trades | active_dates | win_rate | roi | profit_usd | avg_p_cross | avg_pred_error_f | avg_actual_margin_f | loss_reason_counts |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| enhanced_early_all | early | 196 | 196 | 22 | +23.0% | +0.1% | $+0.77 | 0.623 | 0.347 | 0.001 | capped_day_model_overestimate:108,capped_day_shortfall:43,win:45 |
| enhanced_early_all | late | 84 | 84 | 10 | +28.6% | +33.6% | $+140.98 | 0.666 | 0.455 | 0.074 | capped_day_model_overestimate:55,capped_day_shortfall:5,win:24 |
| enhanced_early_all | forward_settled | 30 | 21 | 3 | +19.0% | -35.3% | $-37.05 | 0.733 | 1.159 | -0.119 | capped_day_model_overestimate:15,capped_day_shortfall:2,win:4 |
| enhanced_late_all | early | 195 | 195 | 22 | +21.5% | -5.2% | $-50.56 | 0.732 | 0.721 | -0.053 | capped_day_model_overestimate:131,capped_day_shortfall:22,win:42 |
| enhanced_late_all | late | 66 | 66 | 10 | +39.4% | +80.0% | $+263.99 | 0.717 | 0.175 | 0.352 | capped_day_model_overestimate:32,capped_day_shortfall:8,win:26 |
| enhanced_late_all | forward_settled | 33 | 24 | 3 | +16.7% | -43.4% | $-52.05 | 0.799 | 1.279 | -0.171 | capped_day_model_overestimate:19,capped_day_shortfall:1,win:4 |

## p_cross Bucket Calibration

| train_spec | eval_window | bucket | rows | active_dates | avg_p_cross | cross_rate | avg_edge | avg_actual_margin_f | avg_pred_error_f |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| enhanced_early_all | early | 1 | 70 | 21 | 0.240 | +2.9% | 0.042 | -0.854 | 0.146 |
| enhanced_early_all | early | 2 | 69 | 22 | 0.407 | +10.1% | 0.189 | -0.416 | 0.190 |
| enhanced_early_all | early | 3 | 70 | 22 | 0.510 | +8.6% | 0.279 | -0.322 | 0.347 |
| enhanced_early_all | early | 4 | 69 | 22 | 0.622 | +20.3% | 0.391 | -0.044 | 0.344 |
| enhanced_early_all | early | 5 | 70 | 20 | 0.818 | +45.7% | 0.572 | 0.498 | 0.434 |
| enhanced_early_all | late | 1 | 31 | 10 | 0.258 | +0.0% | 0.069 | -0.845 | 0.185 |
| enhanced_early_all | late | 2 | 30 | 10 | 0.409 | +20.0% | 0.184 | -0.107 | -0.113 |
| enhanced_early_all | late | 3 | 30 | 10 | 0.520 | +10.0% | 0.286 | -0.355 | 0.404 |
| enhanced_early_all | late | 4 | 30 | 10 | 0.645 | +33.3% | 0.427 | 0.115 | 0.244 |
| enhanced_early_all | late | 5 | 30 | 8 | 0.886 | +30.0% | 0.633 | 0.235 | 1.074 |
| enhanced_early_all | forward_settled | 1 | 10 | 2 | 0.120 | +10.0% | -0.062 | -0.985 | -0.226 |
| enhanced_early_all | forward_settled | 2 | 10 | 2 | 0.299 | +20.0% | 0.065 | -0.225 | -0.285 |
| enhanced_early_all | forward_settled | 3 | 10 | 2 | 0.448 | +20.0% | 0.203 | -0.095 | -0.031 |
| enhanced_early_all | forward_settled | 4 | 10 | 2 | 0.681 | +10.0% | 0.478 | -0.370 | 0.844 |
| enhanced_early_all | forward_settled | 5 | 10 | 2 | 0.959 | +20.0% | 0.728 | -0.025 | 2.523 |
| enhanced_late_all | early | 1 | 70 | 21 | 0.186 | +11.4% | -0.031 | -0.672 | -0.016 |
| enhanced_late_all | early | 2 | 69 | 21 | 0.432 | +13.0% | 0.206 | -0.318 | 0.197 |
| enhanced_late_all | early | 3 | 70 | 22 | 0.590 | +18.6% | 0.376 | -0.130 | 0.291 |
| enhanced_late_all | early | 4 | 69 | 22 | 0.785 | +20.3% | 0.548 | 0.007 | 0.566 |
| enhanced_late_all | early | 5 | 70 | 21 | 0.970 | +24.3% | 0.741 | -0.022 | 1.673 |
| enhanced_late_all | late | 1 | 31 | 8 | 0.136 | +0.0% | -0.059 | -1.003 | 0.158 |
| enhanced_late_all | late | 2 | 30 | 10 | 0.295 | +0.0% | 0.063 | -0.528 | 0.148 |
| enhanced_late_all | late | 3 | 30 | 10 | 0.441 | +6.7% | 0.210 | -0.330 | 0.224 |
| enhanced_late_all | late | 4 | 30 | 10 | 0.645 | +20.0% | 0.411 | -0.008 | 0.276 |
| enhanced_late_all | late | 5 | 30 | 9 | 0.887 | +66.7% | 0.659 | 0.918 | 0.075 |
| enhanced_late_all | forward_settled | 1 | 10 | 2 | 0.136 | +0.0% | -0.041 | -1.085 | 0.256 |
| enhanced_late_all | forward_settled | 2 | 10 | 2 | 0.302 | +20.0% | 0.089 | -0.325 | -0.045 |
| enhanced_late_all | forward_settled | 3 | 10 | 2 | 0.548 | +30.0% | 0.300 | 0.045 | 0.043 |
| enhanced_late_all | forward_settled | 4 | 10 | 2 | 0.859 | +20.0% | 0.624 | -0.110 | 0.914 |
| enhanced_late_all | forward_settled | 5 | 10 | 2 | 0.992 | +10.0% | 0.769 | -0.225 | 2.668 |

## Bad Daily Context

| train_spec | eval_window | target_date | trades | win_rate | roi | profit_usd | avg_p_cross | avg_pred_error_f | avg_actual_margin_f | loss_reasons | cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base_early_all | early | 2026-05-21 | 4 | +0.0% | -100.0% | $-20.00 | 0.615 | 0.832 | -0.513 | capped_day_model_overestimate | Beijing,LA,SaoPaulo,Shanghai |
| base_early_all | early | 2026-05-25 | 9 | +0.0% | -100.0% | $-45.00 | 0.630 | 0.736 | -0.361 | capped_day_model_overestimate,capped_day_shortfall | Beijing,Jeddah,Karachi,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| base_early_all | early | 2026-05-31 | 7 | +0.0% | -100.0% | $-35.00 | 0.638 | 1.018 | -0.607 | capped_day_model_overestimate | Amsterdam,Atlanta,Busan,LA,Munich,Taipei,Wellington |
| base_early_all | early | 2026-06-01 | 12 | +0.0% | -100.0% | $-60.00 | 0.609 | 0.690 | -0.354 | capped_day_model_overestimate,capped_day_shortfall | BuenosAires,Busan,CapeTown,Chongqing,Istanbul,LA,Manila,Singapore,TelAviv,Warsaw,Wellington,Wuhan |
| base_early_all | early | 2026-06-10 | 8 | +0.0% | -100.0% | $-40.00 | 0.632 | 0.849 | -0.450 | capped_day_model_overestimate,capped_day_shortfall | Amsterdam,BuenosAires,Karachi,Munich,Taipei,TelAviv,Warsaw,Wellington |
| base_late_all | early | 2026-05-21 | 6 | +0.0% | -100.0% | $-30.00 | 0.739 | 1.232 | -0.567 | capped_day_model_overestimate | Beijing,Helsinki,LA,SanFrancisco,SaoPaulo,Shanghai |
| base_late_all | early | 2026-05-25 | 9 | +0.0% | -100.0% | $-45.00 | 0.734 | 1.195 | -0.544 | capped_day_model_overestimate,capped_day_shortfall | Beijing,Busan,Jeddah,Karachi,LA,Shanghai,Singapore,Tokyo,Wuhan |
| base_late_all | early | 2026-05-30 | 8 | +0.0% | -100.0% | $-40.00 | 0.717 | 1.051 | -0.456 | capped_day_model_overestimate | Busan,CapeTown,Miami,Shanghai,Taipei,Tokyo,Warsaw,Wellington |
| base_late_all | early | 2026-05-31 | 6 | +0.0% | -100.0% | $-30.00 | 0.851 | 1.720 | -0.633 | capped_day_model_overestimate | Amsterdam,Atlanta,Busan,LA,Munich,Wellington |
| base_late_all | early | 2026-06-01 | 13 | +0.0% | -100.0% | $-65.00 | 0.773 | 1.177 | -0.412 | capped_day_model_overestimate | BuenosAires,Busan,CapeTown,Chongqing,Istanbul,LA,Manila,NYC,Singapore,Taipei,Warsaw,Wellington,Wuhan |
| base_late_all | early | 2026-06-10 | 8 | +0.0% | -100.0% | $-40.00 | 0.770 | 1.473 | -0.500 | capped_day_model_overestimate,capped_day_shortfall | Amsterdam,BuenosAires,Karachi,Munich,Shanghai,TelAviv,Warsaw,Wellington |
| enhanced_early_all | early | 2026-05-21 | 5 | +0.0% | -100.0% | $-25.00 | 0.554 | 0.647 | -0.510 | capped_day_model_overestimate,capped_day_shortfall | Beijing,LA,SanFrancisco,SaoPaulo,Shanghai |
| enhanced_early_all | early | 2026-05-25 | 9 | +0.0% | -100.0% | $-45.00 | 0.611 | 0.646 | -0.361 | capped_day_model_overestimate,capped_day_shortfall | Beijing,Jeddah,Karachi,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| enhanced_early_all | early | 2026-05-31 | 6 | +0.0% | -100.0% | $-30.00 | 0.649 | 1.021 | -0.633 | capped_day_model_overestimate | Amsterdam,Atlanta,Busan,LA,Munich,Wellington |
| enhanced_early_all | early | 2026-06-01 | 12 | +0.0% | -100.0% | $-60.00 | 0.595 | 0.600 | -0.354 | capped_day_model_overestimate,capped_day_shortfall | BuenosAires,Busan,CapeTown,Chongqing,Istanbul,LA,Manila,Singapore,TelAviv,Warsaw,Wellington,Wuhan |
| enhanced_early_all | early | 2026-06-10 | 8 | +0.0% | -100.0% | $-40.00 | 0.593 | 0.684 | -0.450 | capped_day_model_overestimate,capped_day_shortfall | Amsterdam,BuenosAires,Karachi,Munich,Taipei,TelAviv,Warsaw,Wellington |
| enhanced_early_trade_base | early | 2026-05-21 | 2 | +0.0% | -100.0% | $-10.00 | 0.558 | 0.089 | -0.050 | capped_day_shortfall | Beijing,SaoPaulo |
| enhanced_early_trade_base | early | 2026-05-25 | 3 | +0.0% | -100.0% | $-15.00 | 0.513 | 0.191 | -0.183 | capped_day_model_overestimate,capped_day_shortfall | Beijing,Singapore,Tokyo |
| enhanced_early_trade_base | early | 2026-06-10 | 1 | +0.0% | -100.0% | $-5.00 | 0.581 | 0.101 | -0.050 | capped_day_shortfall | Amsterdam |
| enhanced_early_trade_base | late | 2026-06-15 | 4 | +0.0% | -100.0% | $-20.00 | 0.749 | 0.519 | -0.200 | capped_day_model_overestimate,capped_day_shortfall | Lucknow,Manila,Tokyo,Wellington |
| enhanced_early_trade_base | late | 2026-06-18 | 2 | +0.0% | -100.0% | $-10.00 | 0.908 | 0.843 | -0.350 | capped_day_model_overestimate | Singapore,Taipei |
| enhanced_early_trade_base | late | 2026-06-20 | 3 | +0.0% | -100.0% | $-15.00 | 0.754 | 0.379 | -0.117 | capped_day_model_overestimate,capped_day_shortfall | Jeddah,Manila,Wellington |
| enhanced_late_all | early | 2026-05-21 | 5 | +0.0% | -100.0% | $-25.00 | 0.750 | 1.037 | -0.510 | capped_day_model_overestimate,capped_day_shortfall | Beijing,LA,SanFrancisco,SaoPaulo,Shanghai |
| enhanced_late_all | early | 2026-05-25 | 9 | +0.0% | -100.0% | $-45.00 | 0.719 | 0.995 | -0.406 | capped_day_model_overestimate,capped_day_shortfall | Beijing,Busan,Jeddah,Karachi,Shanghai,Singapore,Taipei,Tokyo,Wuhan |
| enhanced_late_all | early | 2026-05-30 | 8 | +0.0% | -100.0% | $-40.00 | 0.662 | 1.278 | -0.487 | capped_day_model_overestimate,capped_day_shortfall | Busan,CapeTown,LA,Miami,Shanghai,Taipei,Tokyo,Warsaw |
| enhanced_late_all | early | 2026-05-31 | 7 | +0.0% | -100.0% | $-35.00 | 0.763 | 1.688 | -0.657 | capped_day_model_overestimate | Amsterdam,Atlanta,Busan,Munich,Shanghai,Taipei,Wellington |
| enhanced_late_all | early | 2026-06-01 | 11 | +0.0% | -100.0% | $-55.00 | 0.691 | 0.809 | -0.345 | capped_day_model_overestimate,capped_day_shortfall | BuenosAires,Busan,CapeTown,Chongqing,Istanbul,LA,Manila,Singapore,TelAviv,Warsaw,Wuhan |
| enhanced_late_all | early | 2026-06-05 | 6 | +0.0% | -100.0% | $-30.00 | 0.738 | 1.010 | -0.350 | capped_day_model_overestimate,capped_day_shortfall | Ankara,Jeddah,SaoPaulo,Shanghai,Singapore,Warsaw |
| enhanced_late_all | early | 2026-06-10 | 7 | +0.0% | -100.0% | $-35.00 | 0.813 | 1.502 | -0.507 | capped_day_model_overestimate,capped_day_shortfall | Amsterdam,BuenosAires,Karachi,Munich,Shanghai,Warsaw,Wellington |
| enhanced_late_trade_base | early | 2026-05-21 | 3 | +0.0% | -100.0% | $-15.00 | 0.717 | 0.473 | -0.317 | capped_day_model_overestimate,capped_day_shortfall | Beijing,Helsinki,SaoPaulo |
| enhanced_late_trade_base | early | 2026-05-25 | 4 | +0.0% | -100.0% | $-20.00 | 0.730 | 0.524 | -0.350 | capped_day_model_overestimate | Beijing,TelAviv,Tokyo,Wuhan |
| enhanced_late_trade_base | early | 2026-05-30 | 6 | +0.0% | -100.0% | $-30.00 | 0.714 | 0.664 | -0.425 | capped_day_model_overestimate,capped_day_shortfall | Busan,CapeTown,Miami,Shanghai,Warsaw,Wellington |
| enhanced_late_trade_base | early | 2026-05-31 | 5 | +0.0% | -100.0% | $-25.00 | 0.920 | 1.175 | -0.660 | capped_day_model_overestimate | Amsterdam,Atlanta,Busan,Munich,Shanghai |
| enhanced_late_trade_base | early | 2026-06-01 | 9 | +0.0% | -100.0% | $-45.00 | 0.795 | 0.602 | -0.294 | capped_day_model_overestimate,capped_day_shortfall | BuenosAires,CapeTown,Chongqing,Istanbul,Manila,Singapore,TelAviv,Warsaw,Wuhan |
| enhanced_late_trade_base | early | 2026-06-05 | 7 | +0.0% | -100.0% | $-35.00 | 0.832 | 0.945 | -0.421 | capped_day_model_overestimate,capped_day_shortfall | Ankara,Busan,Jeddah,Manila,SaoPaulo,Singapore,Warsaw |
| enhanced_late_trade_base | early | 2026-06-07 | 5 | +0.0% | -100.0% | $-25.00 | 0.717 | 0.475 | -0.290 | capped_day_model_overestimate,capped_day_shortfall | BuenosAires,Chengdu,Karachi,Shanghai,Warsaw |
| enhanced_late_trade_base | early | 2026-06-10 | 7 | +0.0% | -100.0% | $-35.00 | 0.738 | 0.799 | -0.593 | capped_day_model_overestimate | Amsterdam,BuenosAires,Helsinki,Munich,Shanghai,Warsaw,Wellington |

## Interpretation

1. 如果问题主要是样本波动，互换训练后至少应看到同一类高分信号在反向窗口保持同号收益；实际没有稳定保持。
2. 如果问题主要是城市机制，模型指标和 score buckets 应仍然稳，只是某些城市 drag；实际 score bucket 的跨窗口校准也漂。
3. 因此当前证据指向机制缺口：day-level capped heat / forecast overestimate regime 没建模。城市和气候族群应作为 interaction feature，而不是主过滤器。

## Files

- JSON: `docs/analysis/2026-06/generated/current_bracket_no_signal_swap_stability_v1/summary.json`
- Model metrics: `docs/analysis/2026-06/generated/current_bracket_no_signal_swap_stability_v1/model_swap_metrics.csv`
- Variant performance: `docs/analysis/2026-06/generated/current_bracket_no_signal_swap_stability_v1/variant_swap_performance.csv`
- Score bucket calibration: `docs/analysis/2026-06/generated/current_bracket_no_signal_swap_stability_v1/score_bucket_calibration.csv`
- Daily context: `docs/analysis/2026-06/generated/current_bracket_no_signal_swap_stability_v1/selected_daily_context.csv`
