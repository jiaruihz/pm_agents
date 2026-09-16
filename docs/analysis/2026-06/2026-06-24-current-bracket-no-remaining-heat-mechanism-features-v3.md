# Current-Bracket NO Remaining-Heat Mechanism Features V3

## 结论

这版补的是机制特征，不是新的硬 gate：模型仍然预测 `P(remaining_heat > required_gap)`，但输入增加了小时级 GFS 曲线、曲线平台化、日照小时和观测 plateau/staleness 代理。

结果很明确：`enhanced_all_rows` 有改进但不够。trade-base holdout AUC 从 base 的 0.651 升到 0.700，forward p40 ROI 从 -56.4% 改到 -35.3%；方向对，但没有跨过可交易线。`enhanced_trade_base` 历史 ROI 很漂亮，但 holdout AUC 只有 0.581、forward p40 ROI -67.7%，这是过拟合。

Verdict: `mechanism_features_v3_shadow_only`，live_ready=`False`。

## 数据层

- Generated at UTC: `2026-06-24T03:28:39+00:00`
- Sync/rebuild: `sync_weather_remote.sh completed; run_stack rebuilt facts and CLOB gate passed, then exited non-clean only because FE port 5174 stayed busy.`
- Raw rows: `4306`
- Mechanism rows: `4306`
- Trade-base rows: `499`
- Date range: `2026-05-20`..`2026-06-20`
- Split date: `2026-06-10`

## Mechanism Feature Coverage

| feature | coverage | mean | counts |
| --- | --- | --- | --- |
| curve_remaining_to_peak_f | 1.000 | 2.337 | nan |
| curve_next_3h_delta_f | 1.000 | 0.810 | nan |
| solar_elevation_deg | 1.000 | 63.105 | nan |
| plateau_proxy | 1.000 | 0.054 | nan |
| curve_source_counts | 1.000 | nan | {'gfs_daily_true_prevday': 4174, 'open_meteo_historical_gfs': 132} |

## Base vs Enhanced Model Diagnostics

| model | train_scope | period | rows | active_dates | mae_f | rmse_f | r2 | cross_rate | cross_auc | cross_brier |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base_all_rows | all_rows | train_scope | 3033 | 22 | 0.811 | 1.066 | 0.830 | +63.6% | 0.945 | 0.130 |
| base_all_rows | all_rows | trade_base_train | 348 | 22 | 0.606 | 0.768 | 0.074 | +17.5% | 0.742 | 0.257 |
| base_all_rows | all_rows | trade_base_holdout | 151 | 10 | 0.762 | 0.981 | -0.467 | +18.5% | 0.651 | 0.292 |
| enhanced_all_rows | all_rows | train_scope | 3033 | 22 | 0.735 | 0.958 | 0.863 | +63.6% | 0.953 | 0.125 |
| enhanced_all_rows | all_rows | trade_base_train | 348 | 22 | 0.569 | 0.722 | 0.181 | +17.5% | 0.784 | 0.244 |
| enhanced_all_rows | all_rows | trade_base_holdout | 151 | 10 | 0.738 | 0.974 | -0.447 | +18.5% | 0.700 | 0.284 |
| enhanced_trade_base | trade_base | train_scope | 348 | 22 | 0.145 | 0.209 | 0.932 | +17.5% | 0.996 | 0.051 |
| enhanced_trade_base | trade_base | trade_base_train | 348 | 22 | 0.145 | 0.209 | 0.932 | +17.5% | 0.996 | 0.051 |
| enhanced_trade_base | trade_base | trade_base_holdout | 151 | 10 | 0.618 | 0.859 | -0.125 | +18.5% | 0.581 | 0.299 |

## Trade Variants

| model | variant | selected_trades | active_dates | win_rate | roi | roi_ci_low | roi_ci_high | holdout_roi | avg_required_gap_f | avg_pred_remaining_heat_f | avg_actual_remaining_heat_f | selected_all_loss_days | selected_all_loss_dates |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base_all_rows | baseline_trade_base | 375 | 32 | +20.5% | -8.4% | -27.1% | +10.5% | +7.0% | 0.593 | 0.754 | 0.432 | 4 | 2026-05-21,2026-05-25,2026-05-31,2026-06-10 |
| base_all_rows | remaining_heat_p40_ev10 | 284 | 32 | +24.3% | +7.8% | -15.4% | +30.8% | +26.4% | 0.479 | 0.886 | 0.496 | 5 | 2026-05-21,2026-05-25,2026-05-31,2026-06-01,2026-06-10 |
| base_all_rows | remaining_heat_p45_ev10 | 257 | 32 | +25.3% | +13.0% | -12.9% | +38.1% | +32.1% | 0.466 | 0.939 | 0.518 | 5 | 2026-05-21,2026-05-25,2026-05-31,2026-06-01,2026-06-10 |
| enhanced_all_rows | baseline_trade_base | 375 | 32 | +20.5% | -8.4% | -27.1% | +10.5% | +7.0% | 0.593 | 0.739 | 0.432 | 4 | 2026-05-21,2026-05-25,2026-05-31,2026-06-10 |
| enhanced_all_rows | remaining_heat_p40_ev10 | 280 | 32 | +24.6% | +10.1% | -13.9% | +34.9% | +33.6% | 0.480 | 0.883 | 0.504 | 5 | 2026-05-21,2026-05-25,2026-05-31,2026-06-01,2026-06-10 |
| enhanced_all_rows | remaining_heat_p45_ev10 | 254 | 32 | +26.0% | +16.7% | -9.6% | +42.9% | +30.3% | 0.481 | 0.945 | 0.528 | 6 | 2026-05-21,2026-05-25,2026-05-31,2026-06-01,2026-06-10,2026-06-20 |
| enhanced_trade_base | baseline_trade_base | 375 | 32 | +20.5% | -8.4% | -27.1% | +10.5% | +7.0% | 0.593 | 0.428 | 0.432 | 4 | 2026-05-21,2026-05-25,2026-05-31,2026-06-10 |
| enhanced_trade_base | remaining_heat_p40_ev10 | 127 | 31 | +50.4% | +117.5% | +68.0% | +167.0% | +14.1% | 0.410 | 0.973 | 0.992 | 6 | 2026-05-21,2026-05-25,2026-06-10,2026-06-15,2026-06-18,2026-06-20 |
| enhanced_trade_base | remaining_heat_p45_ev10 | 118 | 31 | +53.4% | +135.0% | +82.1% | +185.6% | +21.9% | 0.428 | 1.039 | 1.051 | 6 | 2026-05-21,2026-05-25,2026-06-10,2026-06-15,2026-06-18,2026-06-20 |

## Forward 6/21..6/23

| model | variant | selected_trades | settled_trades | open_shadow_trades | settled_win_rate | settled_roi | settled_profit_usd | dates |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base_all_rows | baseline_trade_base | 45 | 36 | 9 | +13.9% | -53.8% | $-96.90 | 2026-06-21,2026-06-22,2026-06-23 |
| base_all_rows | remaining_heat_p40_ev10 | 31 | 22 | 9 | +13.6% | -56.4% | $-62.05 | 2026-06-21,2026-06-22,2026-06-23 |
| base_all_rows | remaining_heat_p45_ev10 | 28 | 19 | 9 | +15.8% | -49.5% | $-47.05 | 2026-06-21,2026-06-22,2026-06-23 |
| enhanced_all_rows | baseline_trade_base | 45 | 36 | 9 | +13.9% | -53.8% | $-96.90 | 2026-06-21,2026-06-22,2026-06-23 |
| enhanced_all_rows | remaining_heat_p40_ev10 | 30 | 21 | 9 | +19.0% | -35.3% | $-37.05 | 2026-06-21,2026-06-22,2026-06-23 |
| enhanced_all_rows | remaining_heat_p45_ev10 | 28 | 19 | 9 | +15.8% | -49.5% | $-47.05 | 2026-06-21,2026-06-22,2026-06-23 |
| enhanced_trade_base | baseline_trade_base | 45 | 36 | 9 | +13.9% | -53.8% | $-96.90 | 2026-06-21,2026-06-22,2026-06-23 |
| enhanced_trade_base | remaining_heat_p40_ev10 | 16 | 10 | 6 | +10.0% | -67.7% | $-33.87 | 2026-06-21,2026-06-22,2026-06-23 |
| enhanced_trade_base | remaining_heat_p45_ev10 | 15 | 10 | 5 | +10.0% | -67.7% | $-33.87 | 2026-06-21,2026-06-22,2026-06-23 |

## All-Loss Day Check

| model | variant | target_date | trades | wins | roi | avg_p_cross | avg_required_gap_f | avg_pred_remaining_heat_f | avg_actual_remaining_heat_f | avg_margin_f | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base_all_rows | baseline_trade_base | 2026-05-21 | 6 | 0 | -100.0% | 0.517 | 0.567 | 0.615 | 0.000 | -0.567 | Beijing,Helsinki,LA,SanFrancisco,SaoPaulo,Shanghai |
| base_all_rows | baseline_trade_base | 2026-05-25 | 12 | 0 | -100.0% | 0.545 | 0.537 | 0.668 | 0.000 | -0.537 | Beijing,Busan,Helsinki,Jeddah,Karachi,LA,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| base_all_rows | baseline_trade_base | 2026-05-31 | 9 | 0 | -100.0% | 0.567 | 0.772 | 0.979 | 0.111 | -0.661 | Amsterdam,Atlanta,Busan,LA,Munich,Shanghai,Taipei,TelAviv,Wellington |
| base_all_rows | baseline_trade_base | 2026-06-10 | 13 | 0 | -100.0% | 0.503 | 0.612 | 0.628 | 0.077 | -0.535 | Amsterdam,BuenosAires,Helsinki,Houston,Karachi,Manila,Miami,Munich,Shanghai,Taipei,TelAviv,Warsaw,Wellington |
| base_all_rows | remaining_heat_p35_ev05 | 2026-05-21 | 4 | 0 | -100.0% | 0.615 | 0.513 | 0.832 | 0.000 | -0.513 | Beijing,LA,SaoPaulo,Shanghai |
| base_all_rows | remaining_heat_p35_ev05 | 2026-05-25 | 9 | 0 | -100.0% | 0.630 | 0.361 | 0.736 | 0.000 | -0.361 | Beijing,Jeddah,Karachi,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| base_all_rows | remaining_heat_p35_ev05 | 2026-05-31 | 7 | 0 | -100.0% | 0.638 | 0.750 | 1.161 | 0.143 | -0.607 | Amsterdam,Atlanta,Busan,LA,Munich,Taipei,Wellington |
| base_all_rows | remaining_heat_p35_ev05 | 2026-06-01 | 12 | 0 | -100.0% | 0.609 | 0.354 | 0.690 | 0.000 | -0.354 | BuenosAires,Busan,CapeTown,Chongqing,Istanbul,LA,Manila,Singapore,TelAviv,Warsaw,Wellington,Wuhan |
| base_all_rows | remaining_heat_p35_ev05 | 2026-06-10 | 10 | 0 | -100.0% | 0.578 | 0.495 | 0.738 | 0.000 | -0.495 | Amsterdam,BuenosAires,Karachi,Miami,Munich,Shanghai,Taipei,TelAviv,Warsaw,Wellington |
| base_all_rows | remaining_heat_p40_ev10 | 2026-05-21 | 4 | 0 | -100.0% | 0.615 | 0.513 | 0.832 | 0.000 | -0.513 | Beijing,LA,SaoPaulo,Shanghai |
| base_all_rows | remaining_heat_p40_ev10 | 2026-05-25 | 9 | 0 | -100.0% | 0.630 | 0.361 | 0.736 | 0.000 | -0.361 | Beijing,Jeddah,Karachi,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| base_all_rows | remaining_heat_p40_ev10 | 2026-05-31 | 7 | 0 | -100.0% | 0.638 | 0.750 | 1.161 | 0.143 | -0.607 | Amsterdam,Atlanta,Busan,LA,Munich,Taipei,Wellington |
| base_all_rows | remaining_heat_p40_ev10 | 2026-06-01 | 12 | 0 | -100.0% | 0.609 | 0.354 | 0.690 | 0.000 | -0.354 | BuenosAires,Busan,CapeTown,Chongqing,Istanbul,LA,Manila,Singapore,TelAviv,Warsaw,Wellington,Wuhan |
| base_all_rows | remaining_heat_p40_ev10 | 2026-06-10 | 8 | 0 | -100.0% | 0.632 | 0.450 | 0.849 | 0.000 | -0.450 | Amsterdam,BuenosAires,Karachi,Munich,Taipei,TelAviv,Warsaw,Wellington |
| base_all_rows | remaining_heat_p40_ev10_max2_day | 2026-05-21 | 2 | 0 | -100.0% | 0.676 | 0.775 | 1.264 | 0.000 | -0.775 | Beijing,LA |
| base_all_rows | remaining_heat_p40_ev10_max2_day | 2026-05-23 | 1 | 0 | -100.0% | 0.906 | 0.850 | 2.254 | 0.000 | -0.850 | SaoPaulo |
| base_all_rows | remaining_heat_p40_ev10_max2_day | 2026-05-24 | 2 | 0 | -100.0% | 0.686 | 0.150 | 0.670 | 0.000 | -0.150 | TelAviv,Tokyo |
| base_all_rows | remaining_heat_p40_ev10_max2_day | 2026-05-25 | 2 | 0 | -100.0% | 0.757 | 0.650 | 1.419 | 0.000 | -0.650 | Shanghai,Tokyo |
| base_all_rows | remaining_heat_p40_ev10_max2_day | 2026-05-30 | 1 | 0 | -100.0% | 0.952 | 0.050 | 1.823 | 0.000 | -0.050 | Warsaw |
| base_all_rows | remaining_heat_p40_ev10_max2_day | 2026-05-31 | 2 | 0 | -100.0% | 0.820 | 0.750 | 1.731 | 0.000 | -0.750 | Amsterdam,Munich |
| base_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-01 | 2 | 0 | -100.0% | 0.806 | 0.450 | 1.370 | 0.000 | -0.450 | Warsaw,Wellington |
| base_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-02 | 2 | 0 | -100.0% | 0.810 | 0.350 | 1.290 | 0.000 | -0.350 | SaoPaulo,Shanghai |
| base_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-05 | 2 | 0 | -100.0% | 0.706 | 0.450 | 1.051 | 0.000 | -0.450 | Busan,SaoPaulo |
| base_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-09 | 2 | 0 | -100.0% | 0.748 | 0.875 | 1.635 | 0.500 | -0.375 | Seattle,Taipei |
| base_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-10 | 2 | 0 | -100.0% | 0.847 | 0.250 | 1.342 | 0.000 | -0.250 | BuenosAires,Karachi |
| base_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-11 | 2 | 0 | -100.0% | 0.887 | 0.250 | 1.583 | 0.000 | -0.250 | Lucknow,Wuhan |
| base_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-12 | 2 | 0 | -100.0% | 0.885 | 0.250 | 1.722 | 0.000 | -0.250 | Lucknow,Taipei |
| base_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-14 | 2 | 0 | -100.0% | 0.919 | 0.050 | 1.560 | 0.000 | -0.050 | Guangzhou,Lucknow |
| base_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-16 | 2 | 0 | -100.0% | 0.931 | 0.550 | 2.303 | 0.000 | -0.550 | Shanghai,TelAviv |
| base_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-19 | 2 | 0 | -100.0% | 0.772 | 0.350 | 1.147 | 0.000 | -0.350 | CapeTown,Wellington |
| base_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-20 | 1 | 0 | -100.0% | 0.836 | 0.050 | 1.095 | 0.000 | -0.050 | Wellington |
| base_all_rows | remaining_heat_p45_ev10 | 2026-05-21 | 4 | 0 | -100.0% | 0.615 | 0.513 | 0.832 | 0.000 | -0.513 | Beijing,LA,SaoPaulo,Shanghai |
| base_all_rows | remaining_heat_p45_ev10 | 2026-05-25 | 9 | 0 | -100.0% | 0.630 | 0.361 | 0.736 | 0.000 | -0.361 | Beijing,Jeddah,Karachi,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| base_all_rows | remaining_heat_p45_ev10 | 2026-05-31 | 7 | 0 | -100.0% | 0.638 | 0.750 | 1.161 | 0.143 | -0.607 | Amsterdam,Atlanta,Busan,LA,Munich,Taipei,Wellington |
| base_all_rows | remaining_heat_p45_ev10 | 2026-06-01 | 10 | 0 | -100.0% | 0.648 | 0.295 | 0.745 | 0.000 | -0.295 | CapeTown,Chongqing,Istanbul,LA,Manila,Singapore,TelAviv,Warsaw,Wellington,Wuhan |
| base_all_rows | remaining_heat_p45_ev10 | 2026-06-10 | 6 | 0 | -100.0% | 0.697 | 0.517 | 1.103 | 0.000 | -0.517 | BuenosAires,Karachi,Munich,TelAviv,Warsaw,Wellington |
| enhanced_all_rows | baseline_trade_base | 2026-05-21 | 6 | 0 | -100.0% | 0.500 | 0.567 | 0.562 | 0.000 | -0.567 | Beijing,Helsinki,LA,SanFrancisco,SaoPaulo,Shanghai |
| enhanced_all_rows | baseline_trade_base | 2026-05-25 | 12 | 0 | -100.0% | 0.530 | 0.537 | 0.610 | 0.000 | -0.537 | Beijing,Busan,Helsinki,Jeddah,Karachi,LA,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| enhanced_all_rows | baseline_trade_base | 2026-05-31 | 9 | 0 | -100.0% | 0.536 | 0.772 | 0.872 | 0.111 | -0.661 | Amsterdam,Atlanta,Busan,LA,Munich,Shanghai,Taipei,TelAviv,Wellington |
| enhanced_all_rows | baseline_trade_base | 2026-06-10 | 13 | 0 | -100.0% | 0.466 | 0.612 | 0.505 | 0.077 | -0.535 | Amsterdam,BuenosAires,Helsinki,Houston,Karachi,Manila,Miami,Munich,Shanghai,Taipei,TelAviv,Warsaw,Wellington |
| enhanced_all_rows | remaining_heat_p35_ev05 | 2026-05-21 | 5 | 0 | -100.0% | 0.554 | 0.510 | 0.647 | 0.000 | -0.510 | Beijing,LA,SanFrancisco,SaoPaulo,Shanghai |
| enhanced_all_rows | remaining_heat_p35_ev05 | 2026-05-25 | 10 | 0 | -100.0% | 0.588 | 0.410 | 0.637 | 0.000 | -0.410 | Beijing,Busan,Jeddah,Karachi,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| enhanced_all_rows | remaining_heat_p35_ev05 | 2026-05-31 | 7 | 0 | -100.0% | 0.610 | 0.750 | 1.039 | 0.143 | -0.607 | Amsterdam,Atlanta,Busan,LA,Munich,Taipei,Wellington |
| enhanced_all_rows | remaining_heat_p35_ev05 | 2026-06-10 | 9 | 0 | -100.0% | 0.570 | 0.494 | 0.671 | 0.000 | -0.494 | Amsterdam,BuenosAires,Karachi,Munich,Shanghai,Taipei,TelAviv,Warsaw,Wellington |
| enhanced_all_rows | remaining_heat_p40_ev10 | 2026-05-21 | 5 | 0 | -100.0% | 0.554 | 0.510 | 0.647 | 0.000 | -0.510 | Beijing,LA,SanFrancisco,SaoPaulo,Shanghai |
| enhanced_all_rows | remaining_heat_p40_ev10 | 2026-05-25 | 9 | 0 | -100.0% | 0.611 | 0.361 | 0.646 | 0.000 | -0.361 | Beijing,Jeddah,Karachi,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| enhanced_all_rows | remaining_heat_p40_ev10 | 2026-05-31 | 6 | 0 | -100.0% | 0.649 | 0.800 | 1.188 | 0.167 | -0.633 | Amsterdam,Atlanta,Busan,LA,Munich,Wellington |
| enhanced_all_rows | remaining_heat_p40_ev10 | 2026-06-01 | 12 | 0 | -100.0% | 0.595 | 0.354 | 0.600 | 0.000 | -0.354 | BuenosAires,Busan,CapeTown,Chongqing,Istanbul,LA,Manila,Singapore,TelAviv,Warsaw,Wellington,Wuhan |
| enhanced_all_rows | remaining_heat_p40_ev10 | 2026-06-10 | 8 | 0 | -100.0% | 0.593 | 0.450 | 0.684 | 0.000 | -0.450 | Amsterdam,BuenosAires,Karachi,Munich,Taipei,TelAviv,Warsaw,Wellington |
| enhanced_all_rows | remaining_heat_p40_ev10_max2_day | 2026-05-21 | 2 | 0 | -100.0% | 0.679 | 0.775 | 1.220 | 0.000 | -0.775 | LA,SaoPaulo |
| enhanced_all_rows | remaining_heat_p40_ev10_max2_day | 2026-05-23 | 1 | 0 | -100.0% | 0.950 | 0.850 | 2.428 | 0.000 | -0.850 | SaoPaulo |
| enhanced_all_rows | remaining_heat_p40_ev10_max2_day | 2026-05-24 | 2 | 0 | -100.0% | 0.632 | 0.150 | 0.480 | 0.000 | -0.150 | TelAviv,Tokyo |
| enhanced_all_rows | remaining_heat_p40_ev10_max2_day | 2026-05-25 | 2 | 0 | -100.0% | 0.695 | 0.250 | 0.742 | 0.000 | -0.250 | Singapore,Tokyo |
| enhanced_all_rows | remaining_heat_p40_ev10_max2_day | 2026-05-30 | 1 | 0 | -100.0% | 0.946 | 0.050 | 1.589 | 0.000 | -0.050 | Warsaw |
| enhanced_all_rows | remaining_heat_p40_ev10_max2_day | 2026-05-31 | 2 | 0 | -100.0% | 0.793 | 0.750 | 1.542 | 0.000 | -0.750 | Amsterdam,Munich |
| enhanced_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-01 | 2 | 0 | -100.0% | 0.758 | 0.150 | 0.857 | 0.000 | -0.150 | Istanbul,TelAviv |
| enhanced_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-05 | 2 | 0 | -100.0% | 0.702 | 0.250 | 0.781 | 0.000 | -0.250 | SaoPaulo,Warsaw |
| enhanced_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-10 | 2 | 0 | -100.0% | 0.714 | 0.550 | 1.092 | 0.000 | -0.550 | BuenosAires,Warsaw |
| enhanced_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-12 | 2 | 0 | -100.0% | 0.904 | 0.250 | 1.797 | 0.000 | -0.250 | Lucknow,Taipei |
| enhanced_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-14 | 2 | 0 | -100.0% | 0.966 | 0.050 | 2.171 | 0.000 | -0.050 | Guangzhou,Lucknow |
| enhanced_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-15 | 2 | 0 | -100.0% | 0.831 | 0.350 | 1.273 | 0.000 | -0.350 | Lucknow,Manila |
| enhanced_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-16 | 2 | 0 | -100.0% | 0.979 | 0.250 | 2.213 | 0.000 | -0.250 | Munich,Shanghai |
| enhanced_all_rows | remaining_heat_p40_ev10_max2_day | 2026-06-20 | 2 | 0 | -100.0% | 0.652 | 0.150 | 0.524 | 0.000 | -0.150 | Manila,Wellington |
| enhanced_all_rows | remaining_heat_p45_ev10 | 2026-05-21 | 4 | 0 | -100.0% | 0.592 | 0.513 | 0.741 | 0.000 | -0.513 | Beijing,LA,SaoPaulo,Shanghai |
| enhanced_all_rows | remaining_heat_p45_ev10 | 2026-05-25 | 9 | 0 | -100.0% | 0.611 | 0.361 | 0.646 | 0.000 | -0.361 | Beijing,Jeddah,Karachi,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| enhanced_all_rows | remaining_heat_p45_ev10 | 2026-05-31 | 6 | 0 | -100.0% | 0.649 | 0.800 | 1.188 | 0.167 | -0.633 | Amsterdam,Atlanta,Busan,LA,Munich,Wellington |
| enhanced_all_rows | remaining_heat_p45_ev10 | 2026-06-01 | 11 | 0 | -100.0% | 0.614 | 0.327 | 0.621 | 0.000 | -0.327 | Busan,CapeTown,Chongqing,Istanbul,LA,Manila,Singapore,TelAviv,Warsaw,Wellington,Wuhan |
| enhanced_all_rows | remaining_heat_p45_ev10 | 2026-06-10 | 6 | 0 | -100.0% | 0.647 | 0.517 | 0.882 | 0.000 | -0.517 | BuenosAires,Karachi,Munich,TelAviv,Warsaw,Wellington |
| enhanced_all_rows | remaining_heat_p45_ev10 | 2026-06-20 | 4 | 0 | -100.0% | 0.587 | 0.250 | 0.463 | 0.000 | -0.250 | Jeddah,Manila,Tokyo,Wellington |
| enhanced_trade_base | baseline_trade_base | 2026-05-21 | 6 | 0 | -100.0% | 0.198 | 0.567 | 0.058 | 0.000 | -0.567 | Beijing,Helsinki,LA,SanFrancisco,SaoPaulo,Shanghai |
| enhanced_trade_base | baseline_trade_base | 2026-05-25 | 12 | 0 | -100.0% | 0.190 | 0.537 | 0.129 | 0.000 | -0.537 | Beijing,Busan,Helsinki,Jeddah,Karachi,LA,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| enhanced_trade_base | baseline_trade_base | 2026-05-31 | 9 | 0 | -100.0% | 0.015 | 0.772 | 0.095 | 0.111 | -0.661 | Amsterdam,Atlanta,Busan,LA,Munich,Shanghai,Taipei,TelAviv,Wellington |
| enhanced_trade_base | baseline_trade_base | 2026-06-10 | 13 | 0 | -100.0% | 0.100 | 0.612 | 0.128 | 0.077 | -0.535 | Amsterdam,BuenosAires,Helsinki,Houston,Karachi,Manila,Miami,Munich,Shanghai,Taipei,TelAviv,Warsaw,Wellington |
| enhanced_trade_base | remaining_heat_p35_ev05 | 2026-05-21 | 2 | 0 | -100.0% | 0.558 | 0.050 | 0.089 | 0.000 | -0.050 | Beijing,SaoPaulo |
| enhanced_trade_base | remaining_heat_p35_ev05 | 2026-05-25 | 3 | 0 | -100.0% | 0.513 | 0.183 | 0.191 | 0.000 | -0.183 | Beijing,Singapore,Tokyo |
| enhanced_trade_base | remaining_heat_p35_ev05 | 2026-06-10 | 1 | 0 | -100.0% | 0.581 | 0.050 | 0.101 | 0.000 | -0.050 | Amsterdam |
| enhanced_trade_base | remaining_heat_p35_ev05 | 2026-06-15 | 4 | 0 | -100.0% | 0.749 | 0.200 | 0.519 | 0.000 | -0.200 | Lucknow,Manila,Tokyo,Wellington |
| enhanced_trade_base | remaining_heat_p35_ev05 | 2026-06-18 | 3 | 0 | -100.0% | 0.750 | 0.517 | 0.832 | 0.000 | -0.517 | Istanbul,Singapore,Taipei |
| enhanced_trade_base | remaining_heat_p35_ev05 | 2026-06-20 | 3 | 0 | -100.0% | 0.754 | 0.117 | 0.379 | 0.000 | -0.117 | Jeddah,Manila,Wellington |
| enhanced_trade_base | remaining_heat_p40_ev10 | 2026-05-21 | 2 | 0 | -100.0% | 0.558 | 0.050 | 0.089 | 0.000 | -0.050 | Beijing,SaoPaulo |

## 读法

1. 如果 enhanced 模型在 trade-base holdout 和 forward 都没有比 base 明显改善，说明问题不是“少一条 gate”，而是当前数据还缺真正可观测的剩余热量状态。
2. 小时级 forecast curve 可以识别预报自身是否还在升温；但它不能修正预报系统性高估 final max 的日子。
3. 这版仍是 research/shadow：未通过显著性、基准和前瞻三道门，不允许改 live。

## Files

- JSON: `docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_mechanism_features_v3/summary.json`
- Model metrics: `docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_mechanism_features_v3/model_metrics.csv`
- Variants: `docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_mechanism_features_v3/variant_summary.csv`
- Daily: `docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_mechanism_features_v3/daily_variant_summary.csv`
- Forward: `docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_mechanism_features_v3/forward_validation_and_shadow.csv`
- Selected rows: `docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_mechanism_features_v3/selected_trade_rows.csv`
