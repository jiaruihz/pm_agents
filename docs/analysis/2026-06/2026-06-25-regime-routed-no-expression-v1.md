# Regime-Routed NO Expression V1

## 结论

把 regime 当成表达式路由器这个方向可以测，但不能只看 strict 口径。这里并排比较 A strict、B relaxed ask cap、C best-ask timing：C 只按当时盘口最低 ask 选每 city-day 一笔，不用 payoff 挑时点。本报告把已结算 ROI 和最近模型候选分开，避免未结算日期被静默过滤。

Verdict: `balanced_shadow_candidate_but_not_live_ready`，live_ready=`False`。

raw 候选里 `routed_capped_d1_no_relaxed50_best_ask` 的 ROI 最高但日内 tail 偏薄；当前更平衡的 shadow 候选是 `routed_capped_d2_no_relaxed70_best_ask + soft_balanced`：保留 271 笔 / 35 天 / 35 城，胜率 51.7%，weighted ROI +22.8%，但 date-block CI 仍跨 0，所以不是 live 规则。

## Variant Summary

| variant | selected_trades | active_dates | cities | win_rate | avg_ask | profit_usd | roi | roi_ci_low | roi_ci_high | baseline_roi | excess_roi_vs_baseline | roi_le_minus50_days | roi_eq_minus100_days | open_runway_trades | marginal_runway_trades | forecast_capped_trades |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_current_no_strict35_near_noon | 104 | 32 | 32 | +25.0% | 0.254 | $+13.19 | +2.5% | -35.6% | +42.0% | NA | NA | 14 | 14 | 18 | 36 | 50 |
| routed_capped_d1_no_strict35_near_noon | 70 | 30 | 30 | +35.7% | 0.258 | $+160.52 | +45.9% | -2.0% | +89.1% | +2.5% | +43.3% | 14 | 14 | 18 | 36 | 16 |
| routed_capped_d2_no_strict35_near_noon | 56 | 27 | 26 | +39.3% | 0.261 | $+167.96 | +60.0% | +5.2% | +112.9% | +2.5% | +57.4% | 12 | 12 | 18 | 36 | 2 |
| routed_capped_d1_no_strict35_best_ask | 70 | 30 | 30 | +35.7% | 0.251 | $+191.69 | +54.8% | +0.9% | +107.7% | +2.5% | +52.2% | 14 | 14 | 18 | 36 | 16 |
| routed_capped_d2_no_strict35_best_ask | 56 | 27 | 26 | +39.3% | 0.255 | $+199.13 | +71.1% | +7.1% | +135.0% | +2.5% | +68.6% | 12 | 12 | 18 | 36 | 2 |
| routed_capped_d1_no_relaxed50_near_noon | 171 | 35 | 35 | +39.8% | 0.372 | $+133.42 | +15.6% | -10.9% | +40.2% | +2.5% | +13.1% | 9 | 6 | 34 | 73 | 64 |
| routed_capped_d2_no_relaxed50_near_noon | 115 | 33 | 32 | +41.7% | 0.358 | $+165.36 | +28.8% | -6.4% | +63.8% | +2.5% | +26.2% | 9 | 9 | 34 | 73 | 8 |
| routed_capped_d1_no_relaxed50_best_ask | 171 | 35 | 35 | +39.8% | 0.363 | $+179.49 | +21.0% | -7.4% | +49.4% | +2.5% | +18.5% | 9 | 6 | 34 | 73 | 64 |
| routed_capped_d2_no_relaxed50_best_ask | 115 | 33 | 32 | +41.7% | 0.350 | $+205.07 | +35.7% | -3.8% | +75.4% | +2.5% | +33.1% | 9 | 9 | 34 | 73 | 8 |
| routed_capped_d1_no_relaxed70_near_noon | 393 | 35 | 35 | +52.4% | 0.528 | $+75.52 | +3.8% | -8.2% | +15.7% | +2.5% | +1.3% | 3 | 1 | 68 | 120 | 205 |
| routed_capped_d2_no_relaxed70_near_noon | 271 | 35 | 35 | +52.4% | 0.533 | $+79.90 | +5.9% | -9.5% | +21.6% | +2.5% | +3.4% | 3 | 1 | 68 | 121 | 82 |
| routed_capped_d1_no_relaxed70_best_ask | 393 | 35 | 35 | +50.9% | 0.505 | $+149.25 | +7.6% | -7.0% | +22.5% | +2.5% | +5.1% | 4 | 1 | 68 | 119 | 206 |
| routed_capped_d2_no_relaxed70_best_ask | 271 | 35 | 35 | +51.7% | 0.510 | $+155.73 | +11.5% | -6.2% | +30.6% | +2.5% | +9.0% | 4 | 1 | 68 | 121 | 82 |

## Main Candidate Daily

| target_date | trades | cities | wins | win_rate | profit_usd | roi | regime_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-20 | 6 | 6 | 4 | +66.7% | $+46.06 | +153.5% | day_forecast_capped:3,day_marginal_runway:1,day_open_runway:2 | Beijing,Taipei |
| 2026-05-21 | 3 | 3 | 1 | +33.3% | $-3.37 | -22.5% | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:1 | Ankara,SaoPaulo |
| 2026-05-22 | 6 | 6 | 1 | +16.7% | $-12.14 | -40.5% | day_forecast_capped:3,day_marginal_runway:3 | Helsinki,Miami,Munich,SaoPaulo,Shanghai |
| 2026-05-23 | 3 | 3 | 1 | +33.3% | $-4.80 | -32.0% | day_marginal_runway:1,day_open_runway:2 | Amsterdam,Lucknow |
| 2026-05-24 | 1 | 1 | 0 | +0.0% | $-5.00 | -100.0% | day_forecast_capped:1 | Chengdu |
| 2026-05-25 | 6 | 6 | 2 | +33.3% | $-8.93 | -29.8% | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:1 | Shanghai,TelAviv,Wellington,Wuhan |
| 2026-05-26 | 5 | 5 | 4 | +80.0% | $+26.91 | +107.7% | day_forecast_capped:1,day_marginal_runway:3,day_open_runway:1 | Wuhan |
| 2026-05-27 | 4 | 4 | 0 | +0.0% | $-20.00 | -100.0% | day_forecast_capped:3,day_marginal_runway:1 | Miami,SaoPaulo,Singapore,Warsaw |
| 2026-05-28 | 11 | 11 | 5 | +45.5% | $+15.76 | +28.7% | day_forecast_capped:1,day_marginal_runway:7,day_open_runway:3 | Ankara,Chongqing,Manila,NYC,TelAviv,Wuhan |
| 2026-05-29 | 4 | 4 | 3 | +75.0% | $+20.62 | +103.1% | day_forecast_capped:2,day_marginal_runway:2 | Tokyo |
| 2026-05-30 | 3 | 3 | 1 | +33.3% | $-4.80 | -32.0% | day_forecast_capped:3 | Atlanta,Singapore |
| 2026-05-31 | 7 | 7 | 1 | +14.3% | $-23.37 | -66.8% | day_forecast_capped:2,day_marginal_runway:4,day_open_runway:1 | BuenosAires,Busan,Miami,TelAviv,Tokyo,Wuhan |
| 2026-06-01 | 7 | 7 | 2 | +28.6% | $-11.54 | -33.0% | day_forecast_capped:3,day_marginal_runway:3,day_open_runway:1 | CapeTown,Houston,NYC,Shanghai,TelAviv |
| 2026-06-02 | 8 | 8 | 3 | +37.5% | $-2.16 | -5.4% | day_forecast_capped:5,day_marginal_runway:3 | Amsterdam,Dallas,Houston,Shanghai,TelAviv |
| 2026-06-03 | 3 | 3 | 1 | +33.3% | $+1.67 | +11.1% | day_forecast_capped:1,day_marginal_runway:2 | Manila,Wuhan |
| 2026-06-04 | 3 | 3 | 2 | +66.7% | $+5.20 | +34.7% | day_forecast_capped:2,day_open_runway:1 | Ankara |
| 2026-06-05 | 3 | 3 | 0 | +0.0% | $-15.00 | -100.0% | day_open_runway:3 | Helsinki,Taipei,Tokyo |
| 2026-06-06 | 2 | 2 | 2 | +100.0% | $+30.58 | +305.8% | day_marginal_runway:1,day_open_runway:1 |  |
| 2026-06-07 | 5 | 5 | 0 | +0.0% | $-25.00 | -100.0% | day_forecast_capped:2,day_marginal_runway:1,day_open_runway:2 | Istanbul,Miami,NYC,SanFrancisco,Wellington |
| 2026-06-08 | 6 | 6 | 3 | +50.0% | $+10.31 | +34.4% | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:1 | Ankara,TelAviv,Tokyo |
| 2026-06-09 | 3 | 3 | 2 | +66.7% | $+25.94 | +172.9% | day_marginal_runway:2,day_open_runway:1 | Istanbul |
| 2026-06-10 | 6 | 6 | 1 | +16.7% | $-16.49 | -55.0% | day_forecast_capped:3,day_marginal_runway:1,day_open_runway:2 | Beijing,Busan,Karachi,Tokyo,Warsaw |
| 2026-06-11 | 8 | 8 | 4 | +50.0% | $+34.51 | +86.3% | day_forecast_capped:1,day_marginal_runway:6,day_open_runway:1 | Istanbul,Manila,TelAviv,Wuhan |
| 2026-06-12 | 11 | 11 | 4 | +36.4% | $+24.38 | +44.3% | day_forecast_capped:4,day_marginal_runway:5,day_open_runway:2 | Atlanta,Busan,Houston,Manila,TelAviv,Tokyo,Wellington |
| 2026-06-13 | 4 | 4 | 1 | +25.0% | $-7.50 | -37.5% | day_forecast_capped:2,day_marginal_runway:2 | Helsinki,Singapore,Wellington |
| 2026-06-14 | 10 | 10 | 6 | +60.0% | $+73.76 | +147.5% | day_forecast_capped:3,day_marginal_runway:5,day_open_runway:2 | Karachi,Lucknow,Shanghai,Singapore |
| 2026-06-15 | 7 | 7 | 5 | +71.4% | $+31.28 | +89.4% | day_forecast_capped:2,day_marginal_runway:4,day_open_runway:1 | LA,Lucknow |
| 2026-06-16 | 2 | 2 | 1 | +50.0% | $+2.82 | +28.2% | day_open_runway:2 | TelAviv |
| 2026-06-17 | 6 | 6 | 1 | +16.7% | $-18.10 | -60.3% | day_forecast_capped:4,day_marginal_runway:1,day_open_runway:1 | Beijing,Chengdu,Chongqing,Helsinki,Houston |
| 2026-06-18 | 1 | 1 | 0 | +0.0% | $-5.00 | -100.0% | day_forecast_capped:1 | Karachi |
| 2026-06-19 | 9 | 9 | 4 | +44.4% | $+2.72 | +6.0% | day_forecast_capped:4,day_marginal_runway:5 | Busan,Houston,Miami,TelAviv,Tokyo |
| 2026-06-20 | 3 | 3 | 1 | +33.3% | $-4.80 | -32.0% | day_marginal_runway:3 | Jeddah,Tokyo |
| 2026-06-21 | 2 | 2 | 1 | +50.0% | $+6.13 | +61.3% | day_marginal_runway:1,day_open_runway:1 | Jeddah |
| 2026-06-22 | 1 | 1 | 0 | +0.0% | $-5.00 | -100.0% | day_marginal_runway:1 | Shanghai |
| 2026-06-23 | 2 | 2 | 1 | +50.0% | $+13.81 | +138.1% | day_forecast_capped:1,day_open_runway:1 | Wellington |

## Main Candidate Worst Days

| target_date | trades | wins | win_rate | profit_usd | roi | regime_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-07 | 5 | 0 | +0.0% | $-25.00 | -100.0% | day_forecast_capped:2,day_marginal_runway:1,day_open_runway:2 | Istanbul,Miami,NYC,SanFrancisco,Wellington |
| 2026-06-22 | 1 | 0 | +0.0% | $-5.00 | -100.0% | day_marginal_runway:1 | Shanghai |
| 2026-05-24 | 1 | 0 | +0.0% | $-5.00 | -100.0% | day_forecast_capped:1 | Chengdu |
| 2026-06-05 | 3 | 0 | +0.0% | $-15.00 | -100.0% | day_open_runway:3 | Helsinki,Taipei,Tokyo |
| 2026-06-18 | 1 | 0 | +0.0% | $-5.00 | -100.0% | day_forecast_capped:1 | Karachi |
| 2026-05-27 | 4 | 0 | +0.0% | $-20.00 | -100.0% | day_forecast_capped:3,day_marginal_runway:1 | Miami,SaoPaulo,Singapore,Warsaw |
| 2026-05-31 | 7 | 1 | +14.3% | $-23.37 | -66.8% | day_forecast_capped:2,day_marginal_runway:4,day_open_runway:1 | BuenosAires,Busan,Miami,TelAviv,Tokyo,Wuhan |
| 2026-06-17 | 6 | 1 | +16.7% | $-18.10 | -60.3% | day_forecast_capped:4,day_marginal_runway:1,day_open_runway:1 | Beijing,Chengdu,Chongqing,Helsinki,Houston |
| 2026-06-10 | 6 | 1 | +16.7% | $-16.49 | -55.0% | day_forecast_capped:3,day_marginal_runway:1,day_open_runway:2 | Beijing,Busan,Karachi,Tokyo,Warsaw |
| 2026-05-22 | 6 | 1 | +16.7% | $-12.14 | -40.5% | day_forecast_capped:3,day_marginal_runway:3 | Helsinki,Miami,Munich,SaoPaulo,Shanghai |
| 2026-06-13 | 4 | 1 | +25.0% | $-7.50 | -37.5% | day_forecast_capped:2,day_marginal_runway:2 | Helsinki,Singapore,Wellington |
| 2026-06-01 | 7 | 2 | +28.6% | $-11.54 | -33.0% | day_forecast_capped:3,day_marginal_runway:3,day_open_runway:1 | CapeTown,Houston,NYC,Shanghai,TelAviv |

## Route-Leg Breakdown

| route_leg | day_regime | expression | trades | active_dates | cities | win_rate | avg_ask | profit_usd | roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| capped_d1_no | day_forecast_capped | d1_no | 64 | 27 | 26 | +39.1% | 0.394 | $-5.83 | -1.8% |
| runway_current_no | day_marginal_runway | current_bracket_no | 73 | 28 | 29 | +45.2% | 0.344 | $+168.81 | +46.2% |
| runway_current_no | day_open_runway | current_bracket_no | 34 | 23 | 18 | +29.4% | 0.347 | $+16.52 | +9.7% |

## Soft Weight Overlay

soft weight 只改 notional，不筛单。固定机制权重：`route_multiplier × price_multiplier × weather_multiplier × day_multiplier`；不使用 payoff、final max 或 settlement 训练。

| variant | weight_policy | trades | active_dates | cities | win_rate | avg_ask | notional_retained | weighted_profit_usd | weighted_roi | weighted_roi_ci_low | weighted_roi_ci_high | roi_le_minus50_days | roi_eq_minus100_days | loss_ge_10usd_days | worst_day_profit_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| routed_capped_d1_no_relaxed50_best_ask | full_size | 171 | 35 | 35 | +39.8% | 0.363 | +100.0% | $+179.49 | +21.0% | -7.4% | +49.4% | 9 | 6 | 8 | $-25.00 |
| routed_capped_d1_no_relaxed50_best_ask | soft_moderate | 171 | 35 | 35 | +39.8% | 0.363 | +76.1% | $+176.54 | +27.1% | -3.9% | +59.0% | 10 | 6 | 8 | $-22.02 |
| routed_capped_d1_no_relaxed50_best_ask | soft_balanced | 171 | 35 | 35 | +39.8% | 0.363 | +55.4% | $+159.07 | +33.6% | -0.6% | +68.5% | 12 | 6 | 3 | $-17.21 |
| routed_capped_d2_no_relaxed50_best_ask | full_size | 115 | 33 | 32 | +41.7% | 0.350 | +100.0% | $+205.07 | +35.7% | -3.8% | +75.4% | 9 | 9 | 7 | $-25.00 |
| routed_capped_d2_no_relaxed50_best_ask | soft_moderate | 115 | 33 | 32 | +41.7% | 0.350 | +87.1% | $+190.36 | +38.0% | -2.5% | +79.6% | 10 | 9 | 6 | $-23.16 |
| routed_capped_d2_no_relaxed50_best_ask | soft_balanced | 115 | 33 | 32 | +41.7% | 0.350 | +69.9% | $+165.29 | +41.1% | -1.0% | +83.9% | 11 | 9 | 4 | $-18.66 |
| routed_capped_d1_no_relaxed70_best_ask | full_size | 393 | 35 | 35 | +50.9% | 0.505 | +100.0% | $+149.25 | +7.6% | -7.0% | +22.5% | 4 | 1 | 12 | $-34.76 |
| routed_capped_d1_no_relaxed70_best_ask | soft_moderate | 393 | 35 | 35 | +50.9% | 0.505 | +66.2% | $+151.44 | +11.6% | -5.4% | +29.6% | 4 | 1 | 9 | $-23.14 |
| routed_capped_d1_no_relaxed70_best_ask | soft_balanced | 393 | 35 | 35 | +50.9% | 0.505 | +38.0% | $+135.58 | +18.1% | -2.8% | +40.3% | 5 | 1 | 4 | $-16.58 |
| routed_capped_d2_no_relaxed70_best_ask | full_size | 271 | 35 | 35 | +51.7% | 0.510 | +100.0% | $+155.73 | +11.5% | -6.2% | +30.6% | 4 | 1 | 9 | $-25.39 |
| routed_capped_d2_no_relaxed70_best_ask | soft_moderate | 271 | 35 | 35 | +51.7% | 0.510 | +73.1% | $+156.60 | +15.8% | -4.6% | +37.8% | 4 | 1 | 6 | $-22.66 |
| routed_capped_d2_no_relaxed70_best_ask | soft_balanced | 271 | 35 | 35 | +51.7% | 0.510 | +45.3% | $+139.86 | +22.8% | -2.3% | +49.8% | 5 | 1 | 3 | $-17.38 |

## Balanced Candidate Daily

| target_date | trades | cities | wins | win_rate | avg_weight | weighted_profit_usd | weighted_roi | day_risk | regime_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-20 | 12 | 12 | 6 | +50.0% | 0.321 | $+13.22 | +68.7% | 0.415 | day_forecast_capped:6,day_marginal_runway:2,day_open_runway:4 | Beijing,Jeddah,Miami,Munich,SanFrancisco,Singapore |
| 2026-05-21 | 8 | 8 | 3 | +37.5% | 0.364 | $-2.34 | -16.1% | 0.366 | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:3 | Manila,Munich,SanFrancisco,SaoPaulo,TelAviv |
| 2026-05-22 | 8 | 8 | 2 | +25.0% | 0.437 | $-1.39 | -8.0% | 0.347 | day_forecast_capped:4,day_marginal_runway:3,day_open_runway:1 | Chongqing,Helsinki,Istanbul,Manila,Munich,Shanghai |
| 2026-05-23 | 8 | 8 | 4 | +50.0% | 0.483 | $-0.08 | -0.4% | 0.219 | day_marginal_runway:4,day_open_runway:4 | Amsterdam,Istanbul,Lucknow,Warsaw |
| 2026-05-24 | 4 | 4 | 2 | +50.0% | 0.372 | $-2.73 | -36.7% | 0.320 | day_forecast_capped:1,day_marginal_runway:2,day_open_runway:1 | Lucknow,NYC |
| 2026-05-25 | 8 | 8 | 2 | +25.0% | 0.485 | $-13.78 | -71.1% | 0.268 | day_forecast_capped:1,day_marginal_runway:5,day_open_runway:2 | CapeTown,Jeddah,Shanghai,TelAviv,Tokyo,Wuhan |
| 2026-05-26 | 7 | 7 | 6 | +85.7% | 0.560 | $+17.18 | +87.6% | 0.223 | day_marginal_runway:6,day_open_runway:1 | Wuhan |
| 2026-05-27 | 5 | 5 | 2 | +40.0% | 0.491 | $-5.28 | -43.0% | 0.254 | day_marginal_runway:4,day_open_runway:1 | Beijing,Manila,SaoPaulo |
| 2026-05-28 | 15 | 15 | 9 | +60.0% | 0.580 | $+12.17 | +28.0% | 0.216 | day_forecast_capped:2,day_marginal_runway:10,day_open_runway:3 | Ankara,Chongqing,Manila,NYC,TelAviv,Wuhan |
| 2026-05-29 | 8 | 8 | 5 | +62.5% | 0.488 | $+12.44 | +63.7% | 0.269 | day_forecast_capped:2,day_marginal_runway:6 | LA,SaoPaulo,Warsaw |
| 2026-05-30 | 3 | 3 | 2 | +66.7% | 0.223 | $+1.67 | +49.9% | 0.543 | day_forecast_capped:3 | Shanghai |
| 2026-05-31 | 8 | 8 | 2 | +25.0% | 0.511 | $-17.38 | -85.1% | 0.310 | day_forecast_capped:3,day_marginal_runway:4,day_open_runway:1 | Busan,Karachi,Miami,TelAviv,Tokyo,Wuhan |
| 2026-06-01 | 5 | 5 | 1 | +20.0% | 0.596 | $-13.30 | -89.3% | 0.243 | day_forecast_capped:1,day_marginal_runway:3,day_open_runway:1 | CapeTown,Houston,NYC,TelAviv |
| 2026-06-02 | 9 | 9 | 4 | +44.4% | 0.421 | $-9.04 | -47.7% | 0.328 | day_forecast_capped:3,day_marginal_runway:3,day_open_runway:3 | Amsterdam,Beijing,Busan,Houston,TelAviv |
| 2026-06-03 | 5 | 5 | 4 | +80.0% | 0.400 | $+7.11 | +71.0% | 0.373 | day_forecast_capped:3,day_marginal_runway:2 | Wuhan |
| 2026-06-04 | 6 | 6 | 5 | +83.3% | 0.337 | $+5.80 | +57.4% | 0.366 | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:1 | Dallas |
| 2026-06-05 | 7 | 7 | 4 | +57.1% | 0.314 | $-3.88 | -35.3% | 0.432 | day_forecast_capped:3,day_marginal_runway:1,day_open_runway:3 | Helsinki,Taipei,Tokyo |
| 2026-06-06 | 4 | 4 | 4 | +100.0% | 0.489 | $+23.01 | +235.3% | 0.322 | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:2 |  |
| 2026-06-07 | 5 | 5 | 2 | +40.0% | 0.470 | $-7.14 | -60.8% | 0.273 | day_forecast_capped:2,day_marginal_runway:1,day_open_runway:2 | Istanbul,NYC,SanFrancisco |
| 2026-06-08 | 9 | 9 | 8 | +88.9% | 0.372 | $+11.29 | +67.4% | 0.388 | day_forecast_capped:5,day_marginal_runway:3,day_open_runway:1 | TelAviv |
| 2026-06-09 | 10 | 10 | 5 | +50.0% | 0.442 | $+17.54 | +79.4% | 0.324 | day_forecast_capped:4,day_marginal_runway:5,day_open_runway:1 | CapeTown,Chengdu,Istanbul,SanFrancisco,Singapore |
| 2026-06-10 | 7 | 7 | 4 | +57.1% | 0.400 | $-5.73 | -40.9% | 0.308 | day_forecast_capped:1,day_marginal_runway:2,day_open_runway:4 | Beijing,Busan,Warsaw |
| 2026-06-11 | 10 | 10 | 4 | +40.0% | 0.629 | $+10.03 | +31.9% | 0.203 | day_forecast_capped:1,day_marginal_runway:6,day_open_runway:3 | Ankara,Istanbul,Manila,SanFrancisco,TelAviv,Wuhan |
| 2026-06-12 | 14 | 14 | 5 | +35.7% | 0.574 | $+13.58 | +33.8% | 0.216 | day_forecast_capped:2,day_marginal_runway:9,day_open_runway:3 | Ankara,Atlanta,Busan,Houston,Karachi,LA,Manila,SanFrancisco,TelAviv |
| 2026-06-13 | 7 | 7 | 5 | +71.4% | 0.439 | $+4.12 | +26.8% | 0.325 | day_forecast_capped:2,day_marginal_runway:4,day_open_runway:1 | Helsinki,Karachi |
| 2026-06-14 | 10 | 10 | 8 | +80.0% | 0.570 | $+63.51 | +222.9% | 0.255 | day_forecast_capped:2,day_marginal_runway:5,day_open_runway:3 | Lucknow,Shanghai |
| 2026-06-15 | 12 | 12 | 7 | +58.3% | 0.546 | $+23.88 | +72.9% | 0.217 | day_forecast_capped:3,day_marginal_runway:7,day_open_runway:2 | Lucknow,Munich,NYC,SanFrancisco,TelAviv |
| 2026-06-16 | 5 | 5 | 2 | +40.0% | 0.380 | $+1.49 | +15.7% | 0.386 | day_forecast_capped:2,day_marginal_runway:1,day_open_runway:2 | Austin,Guangzhou,TelAviv |
| 2026-06-17 | 9 | 9 | 4 | +44.4% | 0.344 | $-7.51 | -48.6% | 0.365 | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:4 | Beijing,Chengdu,Istanbul,Karachi,Wuhan |
| 2026-06-18 | 5 | 5 | 3 | +60.0% | 0.279 | $-1.97 | -28.2% | 0.440 | day_forecast_capped:3,day_marginal_runway:2 | CapeTown,Manila |
| 2026-06-19 | 12 | 12 | 6 | +50.0% | 0.478 | $+0.28 | +1.0% | 0.298 | day_forecast_capped:4,day_marginal_runway:5,day_open_runway:3 | BuenosAires,Busan,Houston,Karachi,Taipei,TelAviv |
| 2026-06-20 | 8 | 8 | 4 | +50.0% | 0.419 | $-6.03 | -35.9% | 0.357 | day_forecast_capped:3,day_marginal_runway:4,day_open_runway:1 | Jeddah,Karachi,Manila,Tokyo |
| 2026-06-21 | 9 | 9 | 3 | +33.3% | 0.385 | $-1.55 | -8.9% | 0.337 | day_forecast_capped:4,day_marginal_runway:3,day_open_runway:2 | Beijing,CapeTown,Denver,Jeddah,Singapore,TelAviv |
| 2026-06-22 | 3 | 3 | 0 | +0.0% | 0.351 | $-5.26 | -100.0% | 0.432 | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:1 | NYC,Shanghai,Tokyo |
| 2026-06-23 | 6 | 6 | 3 | +50.0% | 0.347 | $+5.94 | +57.0% | 0.362 | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:4 | Chongqing,Jeddah,Karachi |

## Balanced Candidate Worst PnL Days

| target_date | trades | wins | win_rate | avg_weight | weighted_profit_usd | weighted_roi | regime_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-31 | 8 | 2 | +25.0% | 0.511 | $-17.38 | -85.1% | day_forecast_capped:3,day_marginal_runway:4,day_open_runway:1 | Busan,Karachi,Miami,TelAviv,Tokyo,Wuhan |
| 2026-05-25 | 8 | 2 | +25.0% | 0.485 | $-13.78 | -71.1% | day_forecast_capped:1,day_marginal_runway:5,day_open_runway:2 | CapeTown,Jeddah,Shanghai,TelAviv,Tokyo,Wuhan |
| 2026-06-01 | 5 | 1 | +20.0% | 0.596 | $-13.30 | -89.3% | day_forecast_capped:1,day_marginal_runway:3,day_open_runway:1 | CapeTown,Houston,NYC,TelAviv |
| 2026-06-02 | 9 | 4 | +44.4% | 0.421 | $-9.04 | -47.7% | day_forecast_capped:3,day_marginal_runway:3,day_open_runway:3 | Amsterdam,Beijing,Busan,Houston,TelAviv |
| 2026-06-17 | 9 | 4 | +44.4% | 0.344 | $-7.51 | -48.6% | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:4 | Beijing,Chengdu,Istanbul,Karachi,Wuhan |
| 2026-06-07 | 5 | 2 | +40.0% | 0.470 | $-7.14 | -60.8% | day_forecast_capped:2,day_marginal_runway:1,day_open_runway:2 | Istanbul,NYC,SanFrancisco |
| 2026-06-20 | 8 | 4 | +50.0% | 0.419 | $-6.03 | -35.9% | day_forecast_capped:3,day_marginal_runway:4,day_open_runway:1 | Jeddah,Karachi,Manila,Tokyo |
| 2026-06-10 | 7 | 4 | +57.1% | 0.400 | $-5.73 | -40.9% | day_forecast_capped:1,day_marginal_runway:2,day_open_runway:4 | Beijing,Busan,Warsaw |
| 2026-05-27 | 5 | 2 | +40.0% | 0.491 | $-5.28 | -43.0% | day_marginal_runway:4,day_open_runway:1 | Beijing,Manila,SaoPaulo |
| 2026-06-22 | 3 | 0 | +0.0% | 0.351 | $-5.26 | -100.0% | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:1 | NYC,Shanghai,Tokyo |
| 2026-06-05 | 7 | 4 | +57.1% | 0.314 | $-3.88 | -35.3% | day_forecast_capped:3,day_marginal_runway:1,day_open_runway:3 | Helsinki,Taipei,Tokyo |
| 2026-05-24 | 4 | 2 | +50.0% | 0.372 | $-2.73 | -36.7% | day_forecast_capped:1,day_marginal_runway:2,day_open_runway:1 | Lucknow,NYC |

## City Distribution

| city | trades | active_dates | wins | win_rate | avg_ask | profit_usd | roi | route_mix | loss_dates |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TelAviv | 11 | 11 | 1 | +9.1% | 0.333 | $-38.33 | -69.7% | runway_current_no:11 | 2026-05-25,2026-05-28,2026-05-31,2026-06-01,2026-06-02,2026-06-08,2026-06-11,2026-06-12,2026-06-16,2026-06-19 |
| Tokyo | 10 | 10 | 2 | +20.0% | 0.384 | $-28.93 | -57.9% | capped_d1_no:6,runway_current_no:4 | 2026-05-29,2026-05-31,2026-06-05,2026-06-08,2026-06-10,2026-06-12,2026-06-19,2026-06-20 |
| SaoPaulo | 3 | 3 | 0 | +0.0% | 0.410 | $-15.00 | -100.0% | capped_d1_no:1,runway_current_no:2 | 2026-05-21,2026-05-22,2026-05-27 |
| Ankara | 5 | 5 | 1 | +20.0% | 0.424 | $-14.80 | -59.2% | capped_d1_no:4,runway_current_no:1 | 2026-05-21,2026-05-28,2026-06-04,2026-06-08 |
| Wuhan | 8 | 8 | 2 | +25.0% | 0.391 | $-14.68 | -36.7% | capped_d1_no:1,runway_current_no:7 | 2026-05-25,2026-05-26,2026-05-28,2026-05-31,2026-06-03,2026-06-11 |
| Houston | 7 | 7 | 2 | +28.6% | 0.406 | $-13.43 | -38.4% | capped_d1_no:1,runway_current_no:6 | 2026-06-01,2026-06-02,2026-06-12,2026-06-17,2026-06-19 |
| Manila | 5 | 5 | 1 | +20.0% | 0.332 | $-12.50 | -50.0% | capped_d1_no:1,runway_current_no:4 | 2026-05-28,2026-06-03,2026-06-11,2026-06-12 |
| Chengdu | 2 | 2 | 0 | +0.0% | 0.435 | $-10.00 | -100.0% | capped_d1_no:1,runway_current_no:1 | 2026-05-24,2026-06-17 |
| BuenosAires | 1 | 1 | 0 | +0.0% | 0.487 | $-5.00 | -100.0% | capped_d1_no:1 | 2026-05-31 |
| Dallas | 1 | 1 | 0 | +0.0% | 0.170 | $-5.00 | -100.0% | capped_d1_no:1 | 2026-06-02 |
| Singapore | 7 | 7 | 3 | +42.9% | 0.436 | $-3.95 | -11.3% | capped_d1_no:6,runway_current_no:1 | 2026-05-27,2026-05-30,2026-06-13,2026-06-14 |
| Karachi | 4 | 4 | 1 | +25.0% | 0.400 | $-3.87 | -19.4% | capped_d1_no:3,runway_current_no:1 | 2026-06-10,2026-06-14,2026-06-18 |
| Chongqing | 3 | 3 | 1 | +33.3% | 0.312 | $-3.64 | -24.2% | capped_d1_no:1,runway_current_no:2 | 2026-05-28,2026-06-17 |
| Helsinki | 6 | 6 | 2 | +33.3% | 0.388 | $-2.14 | -7.1% | capped_d1_no:2,runway_current_no:4 | 2026-05-22,2026-06-05,2026-06-13,2026-06-17 |
| Miami | 8 | 8 | 3 | +37.5% | 0.381 | $+0.06 | +0.2% | capped_d1_no:7,runway_current_no:1 | 2026-05-22,2026-05-27,2026-05-31,2026-06-07,2026-06-19 |
| CapeTown | 2 | 2 | 1 | +50.0% | 0.420 | $+0.20 | +2.0% | runway_current_no:2 | 2026-06-01 |
| Wellington | 9 | 9 | 4 | +44.4% | 0.389 | $+1.29 | +2.9% | capped_d1_no:8,runway_current_no:1 | 2026-05-25,2026-06-07,2026-06-12,2026-06-13,2026-06-23 |
| Warsaw | 4 | 4 | 2 | +50.0% | 0.360 | $+2.04 | +10.2% | capped_d1_no:2,runway_current_no:2 | 2026-05-27,2026-06-10 |
| Shanghai | 8 | 8 | 2 | +25.0% | 0.293 | $+4.47 | +11.2% | capped_d1_no:3,runway_current_no:5 | 2026-05-22,2026-05-25,2026-06-01,2026-06-02,2026-06-14,2026-06-22 |
| Istanbul | 6 | 6 | 3 | +50.0% | 0.382 | $+7.55 | +25.2% | capped_d1_no:1,runway_current_no:5 | 2026-06-07,2026-06-09,2026-06-11 |
| Guangzhou | 1 | 1 | 1 | +100.0% | 0.390 | $+7.82 | +156.4% | capped_d1_no:1 |  |
| Atlanta | 5 | 5 | 3 | +60.0% | 0.452 | $+9.23 | +36.9% | capped_d1_no:2,runway_current_no:3 | 2026-05-30,2026-06-12 |
| Denver | 1 | 1 | 1 | +100.0% | 0.280 | $+12.86 | +257.1% | runway_current_no:1 |  |
| Lucknow | 4 | 4 | 1 | +25.0% | 0.258 | $+13.33 | +66.7% | runway_current_no:4 | 2026-05-23,2026-06-14,2026-06-15 |
| Munich | 4 | 4 | 3 | +75.0% | 0.430 | $+14.45 | +72.2% | capped_d1_no:4 | 2026-05-22 |
| Seattle | 1 | 1 | 1 | +100.0% | 0.250 | $+15.00 | +300.0% | runway_current_no:1 |  |
| Amsterdam | 4 | 4 | 2 | +50.0% | 0.315 | $+20.58 | +102.9% | runway_current_no:4 | 2026-05-23,2026-06-02 |
| SanFrancisco | 3 | 3 | 2 | +66.7% | 0.367 | $+23.65 | +157.6% | capped_d1_no:1,runway_current_no:2 | 2026-06-07 |
| NYC | 6 | 6 | 3 | +50.0% | 0.357 | $+24.82 | +82.7% | runway_current_no:6 | 2026-05-28,2026-06-01,2026-06-07 |
| LA | 3 | 3 | 2 | +66.7% | 0.253 | $+24.97 | +166.5% | capped_d1_no:1,runway_current_no:2 | 2026-06-15 |
| Busan | 7 | 7 | 3 | +42.9% | 0.286 | $+25.27 | +72.2% | runway_current_no:7 | 2026-05-31,2026-06-10,2026-06-12,2026-06-19 |
| Beijing | 8 | 8 | 5 | +62.5% | 0.398 | $+26.93 | +67.3% | runway_current_no:8 | 2026-05-20,2026-06-10,2026-06-17 |
| Jeddah | 6 | 6 | 4 | +66.7% | 0.332 | $+34.59 | +115.3% | capped_d1_no:2,runway_current_no:4 | 2026-06-20,2026-06-21 |
| Austin | 3 | 3 | 3 | +100.0% | 0.317 | $+37.65 | +251.0% | capped_d1_no:1,runway_current_no:2 |  |
| Taipei | 5 | 5 | 3 | +60.0% | 0.287 | $+43.97 | +175.9% | capped_d1_no:2,runway_current_no:3 | 2026-05-20,2026-06-05 |

## Recent Model Predictions

| target_date | settlement_known | predicted_trades | cities | avg_ask | known_wins | known_profit_usd |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-06-21 | True | 2 | 2 | 0.375 | 1.000 | $+6.13 |
| 2026-06-22 | True | 1 | 1 | 0.220 | 0.000 | $-5.00 |
| 2026-06-23 | True | 2 | 2 | 0.355 | 1.000 | $+13.81 |
| 2026-06-24 | True | 2 | 2 | 0.420 | 1.000 | $+2.50 |

| target_date | city | decision_hour_local | model_action | day_regime | expression | ask | settlement_known | resolved_payoff | resolved_profit_usd | gamma_check_status | current_bracket | d1_no_bracket | forecast_max_native | running_native | intraday_state | city_family |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-21 | Jeddah | 13 | buy current-bracket NO | day_open_runway | current_bracket_no | 0.440 | True | 0.000 | $-5.00 |  | 35 | 36 | 38.100 | 35.000 | pullback_uncertain | continental_dry_hot |
| 2026-06-21 | Karachi | 13 | buy current-bracket NO | day_marginal_runway | current_bracket_no | 0.310 | True | 1.000 | $+11.13 |  | 34 | 35 | 34.900 | 33.889 | active_warming | continental_dry_hot |
| 2026-06-22 | Shanghai | 14 | buy current-bracket NO | day_marginal_runway | current_bracket_no | 0.220 | True | 0.000 | $-5.00 |  | 24 | 25 | 24.600 | 23.889 | mature_fade | humid_low_latitude |
| 2026-06-23 | NYC | 10 | buy current-bracket NO | day_open_runway | current_bracket_no | 0.210 | True | 1.000 | $+18.81 |  | 70-71 | 72-73 | 75.300 | 71.000 | mature_fade | southern_or_maritime |
| 2026-06-23 | Wellington | 11 | buy d1 NO | day_forecast_capped | d1_no | 0.500 | True | 0.000 | $-5.00 |  | 13 | 14 | 12.900 | 12.778 | active_warming | southern_or_maritime |
| 2026-06-24 | Beijing | 14 | buy d1 NO | day_forecast_capped | d1_no | 0.440 | True | 0.000 | $-5.00 | closed_binary | 29 | 30 | 29.300 | 28.889 | fresh_high | continental_dry_hot |
| 2026-06-24 | Jeddah | 13 | buy current-bracket NO | day_open_runway | current_bracket_no | 0.400 | True | 1.000 | $+7.50 | closed_binary | 34 | 35 | 37.100 | 33.889 | pullback_uncertain | continental_dry_hot |

## PIT Boundary Audit

| regime_label | live_inputs | feature_timing | uses_future_observation | uses_settlement | notes |
| --- | --- | --- | --- | --- | --- |
| day_regime | forecast_max_native,running_native,unit | forecast chosen as-of decision snapshot; running max to decision hour only | False | False | implemented via forecast_gap_to_running_native; safe if forecast source is PIT |
| intraday_state | temp_trend_1h_f,temp_trend_3h_f,decline_native,minutes_since_running_max,decision_hour_local,unit | IEM/METAR as-of decision snapshot with 90 minute tolerance; trend looks backward 1h/3h | False | False | safe for live only after same as-of observation cache is available before the decision |
| moisture_cloud_regime | relative_humidity_pct,sky_cover_code,dewpoint_depression_f | IEM/METAR as-of decision snapshot with 90 minute tolerance | False | False | safe if live observation feed has these fields; otherwise must be missing/unknown, not backfilled |
| wind_regime | wind_speed_kt | IEM/METAR as-of decision snapshot with 90 minute tolerance | False | False | safe if live observation feed has wind before the decision |
| running_max_state | minutes_since_running_max,decline_native,unit | current/running max only from observations up to decision snapshot | False | False | safe; depends on observation freshness and cadence |
| realized_context | final_max_native,final_winning_bracket,current_bracket_held,d1_hit,d2_hit,target_hit | known only after target date settlement/final observations | True | True | not allowed for live regime identification; payoff/calibration only |

## Interpretation

1. `day_open_runway/day_marginal_runway` 的 current-bracket NO 是 PIT 可识别的表达式，不需要知道最终最高温。
2. `day_forecast_capped` 买 higher NO 也可以 PIT 识别，但不能用当天之后的 final max 或 settlement 来挑 d1/d2；只能用当时盘口和 forecast ceiling margin。
3. strict 口径样本少主要来自 ask band 和 capacity；relaxed 口径用于判断信号容量，不代表已经可以下真钱。
4. best-ask timing 是 PIT-safe 的替代选择；如果它显著改变表现，说明 fixed near-noon 可能错过了更好的入场时点。
5. 当前 atlas label 本身没有用 future/settlement；future 字段只在 payoff/calibration 层。代码已把 `add_pit_context` 和 `add_realized_context` 拆开，降低误用风险。
6. live 里真正难点不是 regime 当天识别不到，而是观测 feed 延迟/缺字段时必须让 label 变 `unknown`，不能用当天事后补齐的 IEM cache 假装当时可见。
7. 6/25 本轮没有生成可用 observed-state rows；不能报模型候选。等 live observed feed 落表后再跑同一脚本即可进入 recent predictions。

## Files

- JSON: `docs/analysis/2026-06/generated/regime_routed_no_expression_v1/summary.json`
- Variant summary: `docs/analysis/2026-06/generated/regime_routed_no_expression_v1/variant_summary.csv`
- Daily summary: `docs/analysis/2026-06/generated/regime_routed_no_expression_v1/daily_variant_summary.csv`
- Selected details: `docs/analysis/2026-06/generated/regime_routed_no_expression_v1/selected_trade_details.csv`
- Route-leg summary: `docs/analysis/2026-06/generated/regime_routed_no_expression_v1/route_leg_summary.csv`
- City summary: `docs/analysis/2026-06/generated/regime_routed_no_expression_v1/city_summary_main_candidate.csv`
- Recent predictions: `docs/analysis/2026-06/generated/regime_routed_no_expression_v1/recent_model_predictions.csv`
- Soft weight summary: `docs/analysis/2026-06/generated/regime_routed_no_expression_v1/soft_weight_summary.csv`
- Soft daily summary: `docs/analysis/2026-06/generated/regime_routed_no_expression_v1/soft_weight_daily_summary.csv`
- PIT audit: `docs/analysis/2026-06/generated/regime_routed_no_expression_v1/pit_regime_feature_audit.csv`
