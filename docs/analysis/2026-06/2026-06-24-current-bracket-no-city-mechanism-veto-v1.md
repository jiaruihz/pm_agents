# Current-Bracket NO City Mechanism Veto V1

## 结论

可以继续研究 city veto，但现在还不能把它当 live 删除规则。唯一在 forward 也同向改善的是 `train_zero_win_min4`，它只抓出 `BuenosAires,Jeddah`：train 去掉后 ROI +5.4pct，holdout +0.8pct，forward settled 从 -35.3% 到 -28.5%。这是候选 shadow veto，不是 confirmed。

更“机制化”的低胜率+负 margin+模型高估规则能改善 train/holdout，但 forward 反而变差，说明它会删掉 forward 里的赢家，不能上线。

Verdict: `candidate_city_veto_shadow_only`，live_ready=`False`。

## 数据层

- Generated at UTC: `2026-06-24T03:54:44+00:00`
- Source: `docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_mechanism_features_v3/selected_trade_rows.csv`
- Focus: `enhanced_all_rows::remaining_heat_p40_ev10`
- Train end: `2026-06-10`; holdout end: `2026-06-20`

## Rule Evaluation

| rule | period | removed_cities | base_trades | base_roi | kept_trades | kept_roi | removed_trades | removed_roi | roi_lift | kept_win_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| train_zero_win_min4 | train | BuenosAires,Jeddah | 196 | +0.1% | 186 | +5.5% | 10 | -100.0% | +5.4% | +24.2% |
| train_zero_win_min4 | holdout | BuenosAires,Jeddah | 84 | +33.6% | 81 | +34.4% | 3 | +11.1% | +0.8% | +28.4% |
| train_zero_win_min4 | forward_settled | BuenosAires,Jeddah | 21 | -35.3% | 19 | -28.5% | 2 | -100.0% | +6.8% | +21.1% |
| mechanism_low15_min6_neg_margin_overpred | train | Karachi,SaoPaulo,Busan,Warsaw,Wuhan | 196 | +0.1% | 158 | +13.4% | 38 | -55.4% | +13.3% | +25.3% |
| mechanism_low15_min6_neg_margin_overpred | holdout | Karachi,SaoPaulo,Busan,Warsaw,Wuhan | 84 | +33.6% | 68 | +36.9% | 16 | +19.2% | +3.4% | +29.4% |
| mechanism_low15_min6_neg_margin_overpred | forward_settled | Karachi,SaoPaulo,Busan,Warsaw,Wuhan | 21 | -35.3% | 18 | -64.6% | 3 | +140.9% | -29.4% | +11.1% |
| mechanism_low20_min8_neg_margin | train | Karachi,SaoPaulo,Shanghai | 196 | +0.1% | 166 | +10.5% | 30 | -57.4% | +10.4% | +24.7% |
| mechanism_low20_min8_neg_margin | holdout | Karachi,SaoPaulo,Shanghai | 84 | +33.6% | 72 | +42.1% | 12 | -17.5% | +8.5% | +30.6% |
| mechanism_low20_min8_neg_margin | forward_settled | Karachi,SaoPaulo,Shanghai | 21 | -35.3% | 18 | -42.4% | 3 | +7.5% | -7.1% | +16.7% |

## Current Shadow Version

当前研究版定义：`enhanced_all_rows::remaining_heat_p40_ev10` + shadow veto `BuenosAires,Jeddah`。这不是 live 规则。

| period | selected_trades | settled_trades | open_trades | active_dates | cities | win_rate | roi | profit_usd | avg_trades_per_day |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| train | 186 | 186 | 0 | 22 | 31 | +24.2% | +5.5% | $+50.77 | 8.455 |
| holdout | 81 | 81 | 0 | 10 | 28 | +28.4% | +34.4% | $+139.31 | 8.100 |
| historical_all | 267 | 267 | 0 | 32 | 33 | +25.5% | +14.2% | $+190.09 | 8.344 |
| forward_all | 27 | 19 | 8 | 3 | 16 | +21.1% | -28.5% | $-27.05 | 9.000 |

## Current Daily Snapshot

| target_date | period | trades | wins | win_rate | roi | profit_usd | cities |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-21 | train | 5 | 0.000 | +0.0% | -100.0% | $-25.00 | Beijing,LA,SanFrancisco,SaoPaulo,Shanghai |
| 2026-05-25 | train | 8 | 0.000 | +0.0% | -100.0% | $-40.00 | Beijing,Karachi,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| 2026-05-31 | train | 6 | 0.000 | +0.0% | -100.0% | $-30.00 | Amsterdam,Atlanta,Busan,LA,Munich,Wellington |
| 2026-06-01 | train | 11 | 0.000 | +0.0% | -100.0% | $-55.00 | Busan,CapeTown,Chongqing,Istanbul,LA,Manila,Singapore,TelAviv,Warsaw,Wellington,Wuhan |
| 2026-06-10 | train | 7 | 0.000 | +0.0% | -100.0% | $-35.00 | Amsterdam,Karachi,Munich,Taipei,TelAviv,Warsaw,Wellington |
| 2026-06-15 | holdout | 8 | 2.000 | +25.0% | +37.4% | $+14.95 | Busan,Houston,Lucknow,Manila,Miami,Shanghai,Taipei,Tokyo |
| 2026-06-16 | holdout | 11 | 3.000 | +27.3% | +0.8% | $+0.45 | Atlanta,Busan,Chongqing,Guangzhou,Istanbul,Karachi,Miami,Munich,SaoPaulo,Shanghai,TelAviv |
| 2026-06-17 | holdout | 4 | 1.000 | +25.0% | -21.9% | $-4.38 | Chengdu,Karachi,Manila,Shanghai |
| 2026-06-18 | holdout | 5 | 1.000 | +20.0% | +100.0% | $+25.00 | Ankara,Istanbul,Singapore,Taipei,Wuhan |
| 2026-06-19 | holdout | 5 | 1.000 | +20.0% | -23.1% | $-5.77 | CapeTown,Dallas,Lucknow,TelAviv,Wellington |
| 2026-06-20 | holdout | 4 | 1.000 | +25.0% | +0.0% | $+0.00 | Manila,Singapore,Tokyo,Wellington |
| 2026-06-21 | forward | 10 | 3.000 | +30.0% | +2.6% | $+1.28 | CapeTown,Chongqing,Denver,Karachi,Lucknow,NYC,Shanghai,Tokyo,Warsaw,Wuhan |
| 2026-06-22 | forward | 9 | 1.000 | +11.1% | -63.0% | $-28.33 | Ankara,Beijing,Chongqing,Denver,NYC,Seattle,Shanghai,TelAviv,Wellington |

## Current City Distribution

| city | trades | active_dates | win_rate | roi | profit_usd | avg_no_ask | avg_required_gap_f | avg_pred_remaining_heat_f | avg_actual_remaining_heat_f |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Guangzhou | 9 | 9 | +66.7% | +169.7% | $+76.36 | 0.277 | 0.450 | 1.358 | 1.222 |
| Ankara | 4 | 4 | +50.0% | +282.3% | $+56.46 | 0.237 | 0.650 | 1.041 | 1.500 |
| Beijing | 7 | 7 | +57.1% | +154.1% | $+53.93 | 0.247 | 0.421 | 1.133 | 1.000 |
| Wellington | 15 | 15 | +20.0% | +50.3% | $+37.75 | 0.207 | 0.410 | 0.754 | 0.467 |
| Atlanta | 6 | 6 | +50.0% | +114.6% | $+34.39 | 0.250 | 1.000 | 1.207 | 1.167 |
| Seattle | 4 | 4 | +50.0% | +136.7% | $+27.34 | 0.198 | 1.250 | 2.114 | 1.500 |
| Miami | 5 | 5 | +40.0% | +101.7% | $+25.42 | 0.252 | 0.700 | 0.965 | 0.600 |
| Helsinki | 8 | 8 | +25.0% | +55.1% | $+22.03 | 0.211 | 0.450 | 0.754 | 0.500 |
| Austin | 1 | 1 | +100.0% | +426.3% | $+21.32 | 0.190 | 1.500 | 1.740 | 2.000 |
| Amsterdam | 6 | 6 | +50.0% | +59.0% | $+17.71 | 0.265 | 0.483 | 0.945 | 0.833 |
| Denver | 1 | 1 | +100.0% | +257.1% | $+12.86 | 0.280 | 1.500 | 2.938 | 4.000 |
| CapeTown | 7 | 7 | +28.6% | +31.3% | $+10.96 | 0.286 | 0.421 | 0.750 | 0.571 |
| SanFrancisco | 4 | 4 | +25.0% | +38.9% | $+7.78 | 0.200 | 0.750 | 0.699 | 0.750 |
| Lucknow | 9 | 9 | +22.2% | +16.8% | $+7.56 | 0.213 | 0.228 | 1.318 | 0.333 |
| Istanbul | 7 | 7 | +28.6% | +17.7% | $+6.19 | 0.312 | 0.621 | 0.793 | 0.571 |
| LA | 15 | 15 | +26.7% | +6.4% | $+4.80 | 0.232 | 0.767 | 0.881 | 0.467 |
| NYC | 3 | 3 | +33.3% | +19.0% | $+2.86 | 0.270 | 1.167 | 1.278 | 1.000 |
| Busan | 13 | 13 | +23.1% | +2.3% | $+1.50 | 0.236 | 0.558 | 0.698 | 0.385 |
| Munich | 5 | 5 | +20.0% | +0.0% | $+0.00 | 0.230 | 0.450 | 1.196 | 0.400 |
| TelAviv | 15 | 15 | +20.0% | -1.5% | $-1.14 | 0.252 | 0.357 | 0.677 | 0.400 |
| Singapore | 14 | 14 | +28.6% | -6.7% | $-4.68 | 0.238 | 0.236 | 0.619 | 0.429 |
| Dallas | 1 | 1 | +0.0% | -100.0% | $-5.00 | 0.140 | 1.500 | 2.061 | 0.000 |
| Wuhan | 11 | 11 | +18.2% | -16.7% | $-9.17 | 0.232 | 0.450 | 1.033 | 0.364 |
| Chengdu | 2 | 2 | +0.0% | -100.0% | $-10.00 | 0.140 | 0.450 | 0.934 | 0.000 |
| Houston | 2 | 2 | +0.0% | -100.0% | $-10.00 | 0.325 | 1.000 | 1.415 | 0.000 |
| Tokyo | 13 | 13 | +15.4% | -24.5% | $-15.89 | 0.200 | 0.342 | 0.820 | 0.308 |
| SaoPaulo | 11 | 11 | +18.2% | -30.2% | $-16.59 | 0.225 | 0.377 | 0.982 | 0.364 |
| Taipei | 15 | 15 | +20.0% | -24.1% | $-18.08 | 0.228 | 0.370 | 0.737 | 0.400 |
| Chongqing | 4 | 4 | +0.0% | -100.0% | $-20.00 | 0.275 | 0.450 | 0.557 | 0.000 |
| Warsaw | 7 | 7 | +14.3% | -59.2% | $-20.71 | 0.243 | 0.450 | 0.936 | 0.286 |
| Manila | 12 | 12 | +16.7% | -43.2% | $-25.92 | 0.246 | 0.400 | 0.755 | 0.333 |
| Shanghai | 19 | 19 | +15.8% | -36.9% | $-35.08 | 0.252 | 0.408 | 0.847 | 0.316 |
| Karachi | 12 | 12 | +8.3% | -74.7% | $-44.85 | 0.253 | 0.350 | 0.558 | 0.167 |

## Current Family Distribution

| city_family | trades | cities | active_dates | win_rate | roi | profit_usd |
| --- | --- | --- | --- | --- | --- | --- |
| southern_or_maritime | 81 | 9 | 30 | +24.7% | +19.7% | $+79.94 |
| east_asia_continental | 7 | 1 | 7 | +57.1% | +154.1% | $+53.93 |
| continental_dry_hot | 28 | 6 | 21 | +25.0% | +34.5% | $+48.34 |
| europe_cloud_break | 26 | 4 | 19 | +26.9% | +14.6% | $+19.03 |
| humid_low_latitude | 125 | 13 | 31 | +24.0% | -1.8% | $-11.15 |

## Rule City Membership

| rule | city | city_family | trades | win_rate | roi | avg_actual_margin_f | avg_pred_error_f | avg_curve_error_f | avg_relative_humidity_pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| train_zero_win_min4 | BuenosAires | southern_or_maritime | 5 | +0.0% | -100.0% | -0.490 | 0.654 | 0.800 | 76.114 |
| train_zero_win_min4 | Jeddah | continental_dry_hot | 5 | +0.0% | -100.0% | -0.170 | 0.280 | -2.280 | 38.444 |
| mechanism_low15_min6_neg_margin_overpred | Karachi | continental_dry_hot | 9 | +11.1% | -66.3% | -0.072 | 0.291 | -2.567 | 49.366 |
| mechanism_low15_min6_neg_margin_overpred | SaoPaulo | southern_or_maritime | 8 | +12.5% | -58.3% | -0.125 | 0.785 | 0.050 | 68.366 |
| mechanism_low15_min6_neg_margin_overpred | Busan | humid_low_latitude | 7 | +14.3% | -49.0% | -0.250 | 0.474 | -1.157 | 51.589 |
| mechanism_low15_min6_neg_margin_overpred | Warsaw | europe_cloud_break | 7 | +14.3% | -59.2% | -0.164 | 0.651 | -1.443 | 54.439 |
| mechanism_low15_min6_neg_margin_overpred | Wuhan | humid_low_latitude | 7 | +14.3% | -40.5% | -0.107 | 0.622 | -2.600 | 79.584 |
| mechanism_low20_min8_neg_margin | Karachi | continental_dry_hot | 9 | +11.1% | -66.3% | -0.072 | 0.291 | -2.567 | 49.366 |
| mechanism_low20_min8_neg_margin | SaoPaulo | southern_or_maritime | 8 | +12.5% | -58.3% | -0.125 | 0.785 | 0.050 | 68.366 |
| mechanism_low20_min8_neg_margin | Shanghai | humid_low_latitude | 13 | +15.4% | -50.5% | -0.096 | 0.470 | -2.154 | 69.493 |
| humid_low_latitude_family | Chongqing | humid_low_latitude | 2 | +0.0% | -100.0% | -0.550 | 0.417 | -3.950 | 100.000 |
| humid_low_latitude_family | Miami | humid_low_latitude | 2 | +0.0% | -100.0% | -0.500 | 0.838 | -1.600 | 62.660 |
| humid_low_latitude_family | Chengdu | humid_low_latitude | 1 | +0.0% | -100.0% | -0.450 | 0.574 | -5.200 | 83.000 |
| humid_low_latitude_family | Houston | humid_low_latitude | 1 | +0.0% | -100.0% | -0.500 | 0.541 | -1.600 | 81.440 |
| humid_low_latitude_family | Busan | humid_low_latitude | 7 | +14.3% | -49.0% | -0.250 | 0.474 | -1.157 | 51.589 |
| humid_low_latitude_family | Wuhan | humid_low_latitude | 7 | +14.3% | -40.5% | -0.107 | 0.622 | -2.600 | 79.584 |
| humid_low_latitude_family | Shanghai | humid_low_latitude | 13 | +15.4% | -50.5% | -0.096 | 0.470 | -2.154 | 69.493 |
| humid_low_latitude_family | Manila | humid_low_latitude | 6 | +16.7% | -38.5% | -0.017 | 0.212 | -0.667 | 68.697 |
| humid_low_latitude_family | Tokyo | humid_low_latitude | 10 | +20.0% | -1.8% | 0.050 | 0.527 | -0.160 | 72.912 |
| humid_low_latitude_family | Taipei | humid_low_latitude | 9 | +22.2% | -14.7% | 0.194 | 0.088 | -2.522 | 68.127 |
| humid_low_latitude_family | Singapore | humid_low_latitude | 10 | +30.0% | -9.4% | 0.190 | 0.310 | -1.340 | 71.229 |
| humid_low_latitude_family | Atlanta | humid_low_latitude | 4 | +50.0% | +121.6% | -0.250 | 0.580 | -0.125 | 62.855 |
| humid_low_latitude_family | Guangzhou | humid_low_latitude | 6 | +66.7% | +198.0% | 0.650 | -0.158 | -2.967 | 68.010 |

## Train City Diagnostics

| city | city_family | trades | win_rate | roi | avg_actual_margin_f | avg_pred_error_f | avg_curve_error_f | avg_relative_humidity_pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BuenosAires | southern_or_maritime | 5 | +0.0% | -100.0% | -0.490 | 0.654 | 0.800 | 76.114 |
| Jeddah | continental_dry_hot | 5 | +0.0% | -100.0% | -0.170 | 0.280 | -2.280 | 38.444 |
| Munich | europe_cloud_break | 3 | +0.0% | -100.0% | -0.650 | 0.871 | -2.133 | 51.800 |
| Chongqing | humid_low_latitude | 2 | +0.0% | -100.0% | -0.550 | 0.417 | -3.950 | 100.000 |
| Istanbul | southern_or_maritime | 2 | +0.0% | -100.0% | -0.450 | 0.972 | 1.000 | 47.520 |
| Miami | humid_low_latitude | 2 | +0.0% | -100.0% | -0.500 | 0.838 | -1.600 | 62.660 |
| SanFrancisco | southern_or_maritime | 2 | +0.0% | -100.0% | -0.500 | 0.303 | 1.550 | 54.390 |
| Chengdu | humid_low_latitude | 1 | +0.0% | -100.0% | -0.450 | 0.574 | -5.200 | 83.000 |
| Houston | humid_low_latitude | 1 | +0.0% | -100.0% | -0.500 | 0.541 | -1.600 | 81.440 |
| Karachi | continental_dry_hot | 9 | +11.1% | -66.3% | -0.072 | 0.291 | -2.567 | 49.366 |
| SaoPaulo | southern_or_maritime | 8 | +12.5% | -58.3% | -0.125 | 0.785 | 0.050 | 68.366 |
| Busan | humid_low_latitude | 7 | +14.3% | -49.0% | -0.250 | 0.474 | -1.157 | 51.589 |
| Warsaw | europe_cloud_break | 7 | +14.3% | -59.2% | -0.164 | 0.651 | -1.443 | 54.439 |
| Wuhan | humid_low_latitude | 7 | +14.3% | -40.5% | -0.107 | 0.622 | -2.600 | 79.584 |
| Shanghai | humid_low_latitude | 13 | +15.4% | -50.5% | -0.096 | 0.470 | -2.154 | 69.493 |
| Manila | humid_low_latitude | 6 | +16.7% | -38.5% | -0.017 | 0.212 | -0.667 | 68.697 |
| Wellington | southern_or_maritime | 11 | +18.2% | +51.5% | 0.023 | 0.284 | -2.845 | 71.982 |
| Tokyo | humid_low_latitude | 10 | +20.0% | -1.8% | 0.050 | 0.527 | -0.160 | 72.912 |
| Taipei | humid_low_latitude | 9 | +22.2% | -14.7% | 0.194 | 0.088 | -2.522 | 68.127 |
| TelAviv | southern_or_maritime | 12 | +25.0% | +23.1% | 0.217 | 0.060 | 0.042 | 49.652 |
| Helsinki | europe_cloud_break | 8 | +25.0% | +55.1% | 0.050 | 0.254 | -2.575 | 49.376 |
| Lucknow | continental_dry_hot | 4 | +25.0% | +66.7% | 0.200 | 0.072 | 9.275 | 24.960 |
| LA | southern_or_maritime | 14 | +28.6% | +14.0% | -0.286 | 0.424 | -0.586 | 63.652 |
| Singapore | humid_low_latitude | 10 | +30.0% | -9.4% | 0.190 | 0.310 | -1.340 | 71.229 |
| CapeTown | southern_or_maritime | 6 | +33.3% | +53.2% | 0.183 | 0.028 | 0.600 | 71.322 |
| Ankara | continental_dry_hot | 3 | +33.3% | +76.4% | 0.683 | -0.243 | -1.267 | 57.990 |
| NYC | southern_or_maritime | 3 | +33.3% | +19.0% | -0.167 | 0.278 | 2.967 | 52.023 |
| Amsterdam | europe_cloud_break | 5 | +40.0% | +19.4% | 0.150 | 0.307 | -0.620 | 63.636 |
| Beijing | east_asia_continental | 6 | +50.0% | +103.8% | 0.517 | 0.041 | 1.950 | 55.705 |
| Atlanta | humid_low_latitude | 4 | +50.0% | +121.6% | -0.250 | 0.580 | -0.125 | 62.855 |

## 机制判断

1. `BuenosAires,Jeddah` 是目前最像 candidate veto 的组合，但共同机制不够干净：一个高湿南半球/海洋，一个干热低纬。
2. 更像机制的规则在 forward 删除了赢家，说明单靠城市历史失败和平均 margin 还不够。
3. 下一步不是直接删城，而是把 city/family 作为分层校准项：对这些城市要求更高的 `p_cross` 或更大的 forecast surplus，并继续 shadow 验证。

## Files

- JSON: `docs/analysis/2026-06/generated/current_bracket_no_city_mechanism_veto_v1/summary.json`
- Train city summary: `docs/analysis/2026-06/generated/current_bracket_no_city_mechanism_veto_v1/train_city_mechanism_summary.csv`
- Rule summary: `docs/analysis/2026-06/generated/current_bracket_no_city_mechanism_veto_v1/rule_summary.csv`
- Rule city membership: `docs/analysis/2026-06/generated/current_bracket_no_city_mechanism_veto_v1/rule_city_membership.csv`
- Current strategy summary: `docs/analysis/2026-06/generated/current_bracket_no_city_mechanism_veto_v1/current_shadow_strategy_summary.csv`
- Current daily summary: `docs/analysis/2026-06/generated/current_bracket_no_city_mechanism_veto_v1/current_shadow_daily_summary.csv`
- Current city distribution: `docs/analysis/2026-06/generated/current_bracket_no_city_mechanism_veto_v1/current_shadow_city_distribution.csv`
- Current family distribution: `docs/analysis/2026-06/generated/current_bracket_no_city_mechanism_veto_v1/current_shadow_family_distribution.csv`
