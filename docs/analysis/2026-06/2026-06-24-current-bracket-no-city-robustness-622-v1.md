# Current-Bracket NO City Robustness And 6/22 Forensics V1

## 结论

6/22 的大亏不是单个城市失误，而是 broad capped-day shortfall：9 笔 settled 里 8 笔亏，平均 `pred_remaining_heat` 高于实际 remaining heat，实际没有打穿 upper margin。模型仍把多个城市评成可打穿，但 final max 卡在 bracket upper 附近或下方。

训练/验证互换后，城市好坏标签不稳定：早窗 bad 城市在晚窗仍偏弱，但 forward 里反而包含赢家；晚窗 bad 城市在早窗不一定坏。早窗 good 城市晚窗不错，但 forward 样本很薄。结论是：城市标签可以做分层校准特征，不能直接做 live keep/remove。

Verdict: `city_labels_not_stable_enough_for_live`，live_ready=`False`。

## Data

- Generated at UTC: `2026-06-24T04:29:28+00:00`
- Source: `docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_mechanism_features_v3/selected_trade_rows.csv`
- Current expression: `enhanced_all_rows::remaining_heat_p40_ev10` minus `BuenosAires,Jeddah`

## 6/22 Trade Details

| city | city_family | label_no_wins | no_ask | stake_profit_usd | p_cross_upper | required_gap_f | pred_remaining_heat_f | future_delta_to_daymax_f | actual_margin_f | pred_error_f | curve_next_3h_delta_f | relative_humidity_pct | loss_reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Chongqing | humid_low_latitude | 0.000 | 0.240 | $-5.00 | 0.996 | 0.650 | 3.212 | 0.000 | -0.650 | 3.212 | 0.100 | 88.610 | capped_day_model_overestimate |
| Shanghai | humid_low_latitude | 0.000 | 0.300 | $-5.00 | 0.500 | 0.650 | 0.650 | 0.000 | -0.650 | 0.650 | -1.400 | 100.000 | capped_day_model_overestimate |
| TelAviv | southern_or_maritime | 0.000 | 0.320 | $-5.00 | 0.608 | 0.650 | 0.914 | 0.000 | -0.650 | 0.914 | 0.600 | 54.880 | capped_day_model_overestimate |
| Denver | continental_dry_hot | 0.000 | 0.130 | $-5.00 | 0.759 | 0.500 | 1.174 | 0.000 | -0.500 | 1.174 | -6.000 | 14.090 | capped_day_model_overestimate |
| NYC | southern_or_maritime | 0.000 | 0.262 | $-5.00 | 1.000 | 0.500 | 5.253 | 0.000 | -0.500 | 5.253 | -1.300 | 70.910 | capped_day_model_overestimate |
| Seattle | southern_or_maritime | 0.000 | 0.310 | $-5.00 | 0.419 | 1.500 | 1.305 | 1.000 | -0.500 | 0.305 | -3.200 | 28.660 | capped_day_shortfall |
| Ankara | continental_dry_hot | 0.000 | 0.280 | $-5.00 | 0.430 | 0.250 | 0.082 | 0.000 | -0.250 | 0.082 | -5.300 | 29.830 | capped_day_shortfall |
| Beijing | east_asia_continental | 0.000 | 0.190 | $-5.00 | 0.780 | 0.250 | 0.991 | 0.000 | -0.250 | 0.991 | -2.900 | 40.500 | capped_day_model_overestimate |
| Wellington | southern_or_maritime | 1.000 | 0.300 | $+11.67 | 0.476 | 0.850 | 0.793 | 2.000 | 1.150 | -1.207 | -1.600 | 62.460 | win |

## Swap Validation

| city_set | window | city_count | cities | inside_trades | inside_win_rate | inside_roi | outside_trades | outside_roi | outside_minus_base_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| early_bad | early | 7 | Busan,Karachi,Manila,SaoPaulo,Shanghai,Warsaw,Wuhan | 57 | +14.0% | -52.5% | 129 | +31.1% | +25.6% |
| early_bad | late | 7 | Busan,Karachi,Manila,SaoPaulo,Shanghai,Warsaw,Wuhan | 28 | +21.4% | -0.9% | 53 | +53.0% | +18.6% |
| early_bad | forward_settled | 7 | Busan,Karachi,Manila,SaoPaulo,Shanghai,Warsaw,Wuhan | 5 | +40.0% | +44.5% | 14 | -54.5% | -26.1% |
| late_bad | early | 3 | Manila,Shanghai,Taipei | 28 | +17.9% | -36.4% | 158 | +12.9% | +7.4% |
| late_bad | late | 3 | Manila,Shanghai,Taipei | 18 | +16.7% | -31.2% | 63 | +53.1% | +18.7% |
| late_bad | forward_settled | 3 | Manila,Shanghai,Taipei | 2 | +0.0% | -100.0% | 17 | -20.1% | +8.4% |
| early_good | early | 8 | Amsterdam,Beijing,CapeTown,Guangzhou,Helsinki,Lucknow,Seattle,TelAviv | 51 | +37.3% | +73.7% | 135 | -20.3% | -25.8% |
| early_good | late | 8 | Amsterdam,Beijing,CapeTown,Guangzhou,Helsinki,Lucknow,Seattle,TelAviv | 14 | +35.7% | +38.3% | 67 | +33.6% | -0.8% |
| early_good | forward_settled | 8 | Amsterdam,Beijing,CapeTown,Guangzhou,Helsinki,Lucknow,Seattle,TelAviv | 5 | +0.0% | -100.0% | 14 | -2.9% | +25.5% |
| late_good | early | 2 | Istanbul,Wellington | 13 | +15.4% | +28.2% | 173 | +3.8% | -1.7% |
| late_good | late | 2 | Istanbul,Wellington | 9 | +33.3% | +56.9% | 72 | +31.6% | -2.8% |
| late_good | forward_settled | 2 | Istanbul,Wellington | 1 | +100.0% | +233.3% | 18 | -43.0% | -14.5% |

## Focus City Windows

| city | city_family | slice | trades | win_rate | roi | profit_usd | avg_actual_margin_f | avg_pred_error_f |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Ankara | continental_dry_hot | early | 3 | +33.3% | +76.4% | $+11.46 | 0.683 | -0.243 |
| Ankara | continental_dry_hot | forward_settled | 1 | +0.0% | -100.0% | $-5.00 | -0.250 | 0.082 |
| Ankara | continental_dry_hot | late | 1 | +100.0% | +900.0% | $+45.00 | 1.350 | -1.107 |
| Atlanta | humid_low_latitude | early | 4 | +50.0% | +121.6% | $+24.31 | -0.250 | 0.580 |
| Atlanta | humid_low_latitude | late | 2 | +50.0% | +100.8% | $+10.08 | 1.000 | -1.038 |
| Beijing | east_asia_continental | early | 6 | +50.0% | +103.8% | $+31.15 | 0.517 | 0.041 |
| Beijing | east_asia_continental | forward_settled | 1 | +0.0% | -100.0% | $-5.00 | -0.250 | 0.991 |
| Beijing | east_asia_continental | late | 1 | +100.0% | +455.6% | $+22.78 | 0.950 | 0.686 |
| Busan | humid_low_latitude | early | 7 | +14.3% | -49.0% | $-17.14 | -0.250 | 0.474 |
| Busan | humid_low_latitude | late | 6 | +33.3% | +62.1% | $+18.64 | -0.083 | 0.126 |
| Chongqing | humid_low_latitude | early | 2 | +0.0% | -100.0% | $-10.00 | -0.550 | 0.417 |
| Chongqing | humid_low_latitude | forward_settled | 2 | +0.0% | -100.0% | $-10.00 | -0.350 | 2.922 |
| Chongqing | humid_low_latitude | late | 2 | +0.0% | -100.0% | $-10.00 | -0.350 | 0.698 |
| Guangzhou | humid_low_latitude | early | 6 | +66.7% | +198.0% | $+59.41 | 0.650 | -0.158 |
| Guangzhou | humid_low_latitude | late | 3 | +66.7% | +113.0% | $+16.95 | 1.017 | 0.723 |
| Karachi | continental_dry_hot | early | 9 | +11.1% | -66.3% | $-29.85 | -0.072 | 0.291 |
| Karachi | continental_dry_hot | forward_settled | 1 | +100.0% | +222.6% | $+11.13 | 1.350 | -0.104 |
| Karachi | continental_dry_hot | late | 3 | +0.0% | -100.0% | $-15.00 | -0.517 | 0.690 |
| Manila | humid_low_latitude | early | 6 | +16.7% | -38.5% | $-11.55 | -0.017 | 0.212 |
| Manila | humid_low_latitude | late | 6 | +16.7% | -47.9% | $-14.38 | -0.117 | 0.632 |
| SaoPaulo | southern_or_maritime | early | 8 | +12.5% | -58.3% | $-23.33 | -0.125 | 0.785 |
| SaoPaulo | southern_or_maritime | late | 3 | +33.3% | +44.9% | $+6.74 | 0.283 | 0.175 |
| Shanghai | humid_low_latitude | early | 13 | +15.4% | -50.5% | $-32.86 | -0.096 | 0.470 |
| Shanghai | humid_low_latitude | forward_settled | 2 | +0.0% | -100.0% | $-10.00 | -0.350 | 0.816 |
| Shanghai | humid_low_latitude | late | 6 | +16.7% | -7.4% | $-2.22 | -0.083 | 0.663 |
| Taipei | humid_low_latitude | early | 9 | +22.2% | -14.7% | $-6.59 | 0.194 | 0.088 |
| Taipei | humid_low_latitude | late | 6 | +16.7% | -38.3% | $-11.48 | -0.217 | 0.710 |
| Warsaw | europe_cloud_break | early | 7 | +14.3% | -59.2% | $-20.71 | -0.164 | 0.651 |
| Warsaw | europe_cloud_break | forward_settled | 1 | +0.0% | -100.0% | $-5.00 | -0.450 | 0.877 |
| Wellington | southern_or_maritime | early | 11 | +18.2% | +51.5% | $+28.33 | 0.023 | 0.284 |
| Wellington | southern_or_maritime | forward_settled | 1 | +100.0% | +233.3% | $+11.67 | 1.150 | -1.207 |
| Wellington | southern_or_maritime | late | 4 | +25.0% | +47.1% | $+9.41 | 0.150 | 0.297 |
| Wuhan | humid_low_latitude | early | 7 | +14.3% | -40.5% | $-14.17 | -0.107 | 0.622 |
| Wuhan | humid_low_latitude | forward_settled | 1 | +100.0% | +300.0% | $+15.00 | 1.150 | -1.280 |
| Wuhan | humid_low_latitude | late | 4 | +25.0% | +25.0% | $+5.00 | -0.050 | 0.752 |

## Daily Context

| target_date | period | trades | win_rate | roi | profit_usd | avg_pred_error_f | avg_actual_margin_f | loss_reasons |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-21 | early | 5 | +0.0% | -100.0% | $-25.00 | 0.647 | -0.510 | capped_day_model_overestimate,capped_day_shortfall |
| 2026-05-25 | early | 8 | +0.0% | -100.0% | $-40.00 | 0.688 | -0.375 | capped_day_model_overestimate |
| 2026-05-31 | early | 6 | +0.0% | -100.0% | $-30.00 | 1.021 | -0.633 | capped_day_model_overestimate |
| 2026-06-01 | early | 11 | +0.0% | -100.0% | $-55.00 | 0.608 | -0.327 | capped_day_model_overestimate,capped_day_shortfall |
| 2026-06-10 | early | 7 | +0.0% | -100.0% | $-35.00 | 0.636 | -0.450 | capped_day_model_overestimate,capped_day_shortfall |
| 2026-06-15 | late | 8 | +25.0% | +37.4% | $+14.95 | 0.654 | -0.213 | capped_day_model_overestimate,capped_day_shortfall,win |
| 2026-06-16 | late | 11 | +27.3% | +0.8% | $+0.45 | 0.743 | -0.095 | capped_day_model_overestimate,win |
| 2026-06-17 | late | 4 | +25.0% | -21.9% | $-4.38 | 0.224 | 0.050 | capped_day_model_overestimate,win |
| 2026-06-18 | late | 5 | +20.0% | +100.0% | $+25.00 | 0.270 | -0.130 | capped_day_model_overestimate,capped_day_shortfall,win |
| 2026-06-19 | late | 5 | +20.0% | -23.1% | $-5.77 | 0.876 | -0.260 | capped_day_model_overestimate,win |
| 2026-06-20 | late | 4 | +25.0% | +0.0% | $+0.00 | 0.009 | 0.150 | capped_day_model_overestimate,win |
| 2026-06-21 | forward | 10 | +30.0% | +2.6% | $+1.28 | 1.194 | 0.120 | capped_day_model_overestimate,win |
| 2026-06-22 | forward | 9 | +11.1% | -63.0% | $-28.33 | 1.264 | -0.311 | capped_day_model_overestimate,capped_day_shortfall,win |

## Interpretation

1. `Karachi/Shanghai/Manila/Warsaw/Chongqing` 的历史拖累有真实机制迹象：多数是 negative actual margin 或 model overestimate；但它们不是稳定全窗坏城。
2. `Guangzhou/Ankara/Beijing/Wellington/Atlanta` 等好城市也有样本噪声：不少城市只有 4-9 笔，forward 里覆盖不足。
3. 更合理的下一步是分层校准：对高风险城市/族群要求更高 forecast surplus 或更低 model overestimate，而不是删除城市。

## Files

- JSON: `docs/analysis/2026-06/generated/current_bracket_no_city_robustness_622_v1/summary.json`
- 6/22 details: `docs/analysis/2026-06/generated/current_bracket_no_city_robustness_622_v1/forward_2026_06_22_trade_details.csv`
- City window summary: `docs/analysis/2026-06/generated/current_bracket_no_city_robustness_622_v1/city_window_summary.csv`
- City set swap validation: `docs/analysis/2026-06/generated/current_bracket_no_city_robustness_622_v1/city_set_swap_validation.csv`
- Daily summary: `docs/analysis/2026-06/generated/current_bracket_no_city_robustness_622_v1/current_shadow_daily_summary.csv`
