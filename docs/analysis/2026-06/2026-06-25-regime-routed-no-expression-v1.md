# Regime-Routed NO Expression V1

## 结论

把 regime 当成表达式路由器这个方向可以测，但不能只看 strict 口径。这里并排比较 A strict、B relaxed ask cap、C best-ask timing：C 只按当时盘口最低 ask 选每 city-day 一笔，不用 payoff 挑时点。本报告把已结算 ROI 和最近模型候选分开，避免未结算日期被静默过滤。

Verdict: `balanced_shadow_candidate_but_not_live_ready`，live_ready=`False`。

raw 候选里 `routed_capped_d1_no_relaxed50_best_ask` 的 ROI 最高但日内 tail 偏薄；当前更平衡的 shadow 候选是 `routed_capped_d2_no_relaxed70_best_ask + soft_balanced`：保留 284 笔 / 38 天 / 35 城，胜率 +52.5%，weighted ROI +26.7%，date-block CI [+5.5%, +47.4%]；仍是 shadow 候选，不是 live 规则。

## Variant Summary

| variant | selected_trades | active_dates | cities | win_rate | avg_ask | profit_usd | roi | roi_ci_low | roi_ci_high | baseline_roi | excess_roi_vs_baseline | roi_le_minus50_days | roi_eq_minus100_days | open_runway_trades | marginal_runway_trades | forecast_capped_trades |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_current_no_strict35_near_noon | 108 | 35 | 32 | +24.1% | 0.252 | $-6.81 | -1.3% | -38.5% | +37.2% | NA | NA | 17 | 17 | 19 | 36 | 53 |
| routed_capped_d1_no_strict35_near_noon | 71 | 31 | 30 | +35.2% | 0.259 | $+155.52 | +43.8% | -2.8% | +85.1% | -1.3% | +45.1% | 15 | 15 | 19 | 36 | 16 |
| routed_capped_d2_no_strict35_near_noon | 57 | 28 | 26 | +38.6% | 0.261 | $+162.96 | +57.2% | +2.2% | +110.5% | -1.3% | +58.4% | 13 | 13 | 19 | 36 | 2 |
| routed_capped_d1_no_strict35_best_ask | 71 | 31 | 30 | +35.2% | 0.252 | $+186.69 | +52.6% | +0.0% | +103.1% | -1.3% | +53.8% | 15 | 15 | 19 | 36 | 16 |
| routed_capped_d2_no_strict35_best_ask | 57 | 28 | 26 | +38.6% | 0.256 | $+194.13 | +68.1% | +5.5% | +131.7% | -1.3% | +69.4% | 13 | 13 | 19 | 36 | 2 |
| routed_capped_d1_no_relaxed50_near_noon | 178 | 38 | 35 | +39.3% | 0.374 | $+121.56 | +13.7% | -10.9% | +37.8% | -1.3% | +14.9% | 10 | 7 | 37 | 74 | 67 |
| routed_capped_d2_no_relaxed50_near_noon | 119 | 36 | 32 | +42.0% | 0.360 | $+168.50 | +28.3% | -5.4% | +60.8% | -1.3% | +29.6% | 10 | 10 | 37 | 74 | 8 |
| routed_capped_d1_no_relaxed50_best_ask | 178 | 38 | 35 | +39.3% | 0.365 | $+167.63 | +18.8% | -8.0% | +45.8% | -1.3% | +20.1% | 10 | 7 | 37 | 74 | 67 |
| routed_capped_d2_no_relaxed50_best_ask | 119 | 36 | 32 | +42.0% | 0.352 | $+208.21 | +35.0% | -2.7% | +73.7% | -1.3% | +36.3% | 10 | 10 | 37 | 74 | 8 |
| routed_capped_d1_no_relaxed70_near_noon | 410 | 38 | 35 | +52.7% | 0.529 | $+75.32 | +3.7% | -7.9% | +15.4% | -1.3% | +4.9% | 3 | 1 | 75 | 123 | 212 |
| routed_capped_d2_no_relaxed70_near_noon | 284 | 38 | 35 | +53.2% | 0.535 | $+91.67 | +6.5% | -7.8% | +20.7% | -1.3% | +7.7% | 3 | 1 | 75 | 125 | 84 |
| routed_capped_d1_no_relaxed70_best_ask | 410 | 38 | 35 | +51.2% | 0.506 | $+152.22 | +7.4% | -6.8% | +22.1% | -1.3% | +8.7% | 4 | 1 | 74 | 123 | 213 |
| routed_capped_d2_no_relaxed70_best_ask | 284 | 38 | 35 | +52.5% | 0.512 | $+170.67 | +12.0% | -4.8% | +29.3% | -1.3% | +13.3% | 4 | 1 | 74 | 126 | 84 |

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
| 2026-06-24 | 2 | 2 | 1 | +50.0% | $+2.50 | +25.0% | day_forecast_capped:1,day_open_runway:1 | Beijing |
| 2026-06-25 | 3 | 3 | 1 | +33.3% | $-4.36 | -29.1% | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:1 | CapeTown,Karachi |
| 2026-06-26 | 2 | 2 | 0 | +0.0% | $-10.00 | -100.0% | day_forecast_capped:1,day_open_runway:1 | Chongqing,Warsaw |

## Main Candidate Worst Days

| target_date | trades | wins | win_rate | profit_usd | roi | regime_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-07 | 5 | 0 | +0.0% | $-25.00 | -100.0% | day_forecast_capped:2,day_marginal_runway:1,day_open_runway:2 | Istanbul,Miami,NYC,SanFrancisco,Wellington |
| 2026-06-22 | 1 | 0 | +0.0% | $-5.00 | -100.0% | day_marginal_runway:1 | Shanghai |
| 2026-06-18 | 1 | 0 | +0.0% | $-5.00 | -100.0% | day_forecast_capped:1 | Karachi |
| 2026-06-05 | 3 | 0 | +0.0% | $-15.00 | -100.0% | day_open_runway:3 | Helsinki,Taipei,Tokyo |
| 2026-05-27 | 4 | 0 | +0.0% | $-20.00 | -100.0% | day_forecast_capped:3,day_marginal_runway:1 | Miami,SaoPaulo,Singapore,Warsaw |
| 2026-06-26 | 2 | 0 | +0.0% | $-10.00 | -100.0% | day_forecast_capped:1,day_open_runway:1 | Chongqing,Warsaw |
| 2026-05-24 | 1 | 0 | +0.0% | $-5.00 | -100.0% | day_forecast_capped:1 | Chengdu |
| 2026-05-31 | 7 | 1 | +14.3% | $-23.37 | -66.8% | day_forecast_capped:2,day_marginal_runway:4,day_open_runway:1 | BuenosAires,Busan,Miami,TelAviv,Tokyo,Wuhan |
| 2026-06-17 | 6 | 1 | +16.7% | $-18.10 | -60.3% | day_forecast_capped:4,day_marginal_runway:1,day_open_runway:1 | Beijing,Chengdu,Chongqing,Helsinki,Houston |
| 2026-06-10 | 6 | 1 | +16.7% | $-16.49 | -55.0% | day_forecast_capped:3,day_marginal_runway:1,day_open_runway:2 | Beijing,Busan,Karachi,Tokyo,Warsaw |
| 2026-05-22 | 6 | 1 | +16.7% | $-12.14 | -40.5% | day_forecast_capped:3,day_marginal_runway:3 | Helsinki,Miami,Munich,SaoPaulo,Shanghai |
| 2026-06-13 | 4 | 1 | +25.0% | $-7.50 | -37.5% | day_forecast_capped:2,day_marginal_runway:2 | Helsinki,Singapore,Wellington |

## Route-Leg Breakdown

| route_leg | day_regime | expression | trades | active_dates | cities | win_rate | avg_ask | profit_usd | roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| capped_d1_no | day_forecast_capped | d1_no | 67 | 30 | 28 | +37.3% | 0.394 | $-20.83 | -6.2% |
| runway_current_no | day_marginal_runway | current_bracket_no | 74 | 29 | 29 | +45.9% | 0.346 | $+174.44 | +47.1% |
| runway_current_no | day_open_runway | current_bracket_no | 37 | 26 | 20 | +29.7% | 0.351 | $+14.02 | +7.6% |

## Soft Weight Overlay

soft weight 只改 notional，不筛单。固定机制权重：`route_multiplier × price_multiplier × weather_multiplier × day_multiplier`；不使用 payoff、final max 或 settlement 训练。

| variant | weight_policy | trades | active_dates | cities | win_rate | avg_ask | notional_retained | weighted_profit_usd | weighted_roi | weighted_roi_ci_low | weighted_roi_ci_high | roi_le_minus50_days | roi_eq_minus100_days | loss_ge_10usd_days | worst_day_profit_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| routed_capped_d1_no_relaxed50_best_ask | full_size | 178 | 38 | 35 | +39.3% | 0.365 | +100.0% | $+167.63 | +18.8% | -8.0% | +45.8% | 10 | 7 | 9 | $-25.00 |
| routed_capped_d1_no_relaxed50_best_ask | soft_moderate | 178 | 38 | 35 | +39.3% | 0.365 | +67.4% | $+157.63 | +26.3% | -2.5% | +54.7% | 10 | 7 | 6 | $-17.46 |
| routed_capped_d1_no_relaxed50_best_ask | soft_balanced | 178 | 38 | 35 | +39.3% | 0.365 | +41.9% | $+133.04 | +35.7% | +5.6% | +64.2% | 9 | 7 | 0 | $-9.87 |
| routed_capped_d2_no_relaxed50_best_ask | full_size | 119 | 36 | 32 | +42.0% | 0.352 | +100.0% | $+208.21 | +35.0% | -2.7% | +73.7% | 10 | 10 | 7 | $-25.00 |
| routed_capped_d2_no_relaxed50_best_ask | soft_moderate | 119 | 36 | 32 | +42.0% | 0.352 | +74.1% | $+179.88 | +40.8% | +2.9% | +78.9% | 11 | 10 | 4 | $-18.57 |
| routed_capped_d2_no_relaxed50_best_ask | soft_balanced | 119 | 36 | 32 | +42.0% | 0.352 | +48.9% | $+145.40 | +50.0% | +12.1% | +87.7% | 11 | 10 | 3 | $-12.25 |
| routed_capped_d1_no_relaxed70_best_ask | full_size | 410 | 38 | 35 | +51.2% | 0.506 | +100.0% | $+152.22 | +7.4% | -6.8% | +22.1% | 4 | 1 | 12 | $-34.76 |
| routed_capped_d1_no_relaxed70_best_ask | soft_moderate | 410 | 38 | 35 | +51.2% | 0.506 | +61.0% | $+149.32 | +11.9% | -3.5% | +27.8% | 4 | 1 | 7 | $-19.84 |
| routed_capped_d1_no_relaxed70_best_ask | soft_balanced | 410 | 38 | 35 | +51.2% | 0.506 | +31.0% | $+122.07 | +19.2% | +1.2% | +36.5% | 5 | 1 | 1 | $-10.32 |
| routed_capped_d2_no_relaxed70_best_ask | full_size | 284 | 38 | 35 | +52.5% | 0.512 | +100.0% | $+170.67 | +12.0% | -4.8% | +29.3% | 4 | 1 | 9 | $-25.39 |
| routed_capped_d2_no_relaxed70_best_ask | soft_moderate | 284 | 38 | 35 | +52.5% | 0.512 | +65.6% | $+162.53 | +17.5% | -0.6% | +35.8% | 4 | 1 | 5 | $-18.13 |
| routed_capped_d2_no_relaxed70_best_ask | soft_balanced | 284 | 38 | 35 | +52.5% | 0.512 | +34.7% | $+131.43 | +26.7% | +5.5% | +47.4% | 5 | 1 | 1 | $-10.35 |

## Balanced Candidate Daily

| target_date | trades | cities | wins | win_rate | avg_weight | weighted_profit_usd | weighted_roi | day_risk | regime_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-20 | 12 | 12 | 6 | +50.0% | 0.244 | $+13.79 | +94.3% | 0.415 | day_forecast_capped:6,day_marginal_runway:2,day_open_runway:4 | Beijing,Jeddah,Miami,Munich,SanFrancisco,Singapore |
| 2026-05-21 | 8 | 8 | 3 | +37.5% | 0.317 | $-2.86 | -22.5% | 0.347 | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:3 | Manila,Munich,SanFrancisco,SaoPaulo,TelAviv |
| 2026-05-22 | 8 | 8 | 2 | +25.0% | 0.286 | $+0.20 | +1.8% | 0.347 | day_forecast_capped:4,day_marginal_runway:3,day_open_runway:1 | Chongqing,Helsinki,Istanbul,Manila,Munich,Shanghai |
| 2026-05-23 | 8 | 8 | 4 | +50.0% | 0.413 | $+1.22 | +7.4% | 0.219 | day_marginal_runway:4,day_open_runway:4 | Amsterdam,Istanbul,Lucknow,Warsaw |
| 2026-05-24 | 4 | 4 | 2 | +50.0% | 0.338 | $-3.09 | -45.6% | 0.320 | day_forecast_capped:1,day_marginal_runway:2,day_open_runway:1 | Lucknow,NYC |
| 2026-05-25 | 8 | 8 | 2 | +25.0% | 0.309 | $-6.74 | -54.6% | 0.268 | day_forecast_capped:1,day_marginal_runway:5,day_open_runway:2 | CapeTown,Jeddah,Shanghai,TelAviv,Tokyo,Wuhan |
| 2026-05-26 | 7 | 7 | 6 | +85.7% | 0.490 | $+14.26 | +83.2% | 0.223 | day_marginal_runway:6,day_open_runway:1 | Wuhan |
| 2026-05-27 | 5 | 5 | 2 | +40.0% | 0.414 | $-3.34 | -32.3% | 0.254 | day_marginal_runway:4,day_open_runway:1 | Beijing,Manila,SaoPaulo |
| 2026-05-28 | 15 | 15 | 9 | +60.0% | 0.408 | $+13.48 | +44.1% | 0.216 | day_forecast_capped:2,day_marginal_runway:10,day_open_runway:3 | Ankara,Chongqing,Manila,NYC,TelAviv,Wuhan |
| 2026-05-29 | 8 | 8 | 5 | +62.5% | 0.346 | $+3.90 | +28.1% | 0.269 | day_forecast_capped:2,day_marginal_runway:6 | LA,SaoPaulo,Warsaw |
| 2026-05-30 | 3 | 3 | 2 | +66.7% | 0.223 | $+1.67 | +49.9% | 0.543 | day_forecast_capped:3 | Shanghai |
| 2026-05-31 | 8 | 8 | 2 | +25.0% | 0.335 | $-10.35 | -77.2% | 0.310 | day_forecast_capped:3,day_marginal_runway:4,day_open_runway:1 | Busan,Karachi,Miami,TelAviv,Tokyo,Wuhan |
| 2026-06-01 | 5 | 5 | 1 | +20.0% | 0.431 | $-9.01 | -83.6% | 0.243 | day_forecast_capped:1,day_marginal_runway:3,day_open_runway:1 | CapeTown,Houston,NYC,TelAviv |
| 2026-06-02 | 9 | 9 | 4 | +44.4% | 0.381 | $-9.52 | -55.5% | 0.328 | day_forecast_capped:3,day_marginal_runway:3,day_open_runway:3 | Amsterdam,Beijing,Busan,Houston,TelAviv |
| 2026-06-03 | 5 | 5 | 4 | +80.0% | 0.306 | $+10.55 | +137.9% | 0.373 | day_forecast_capped:3,day_marginal_runway:2 | Wuhan |
| 2026-06-04 | 6 | 6 | 5 | +83.3% | 0.287 | $+4.46 | +51.8% | 0.366 | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:1 | Dallas |
| 2026-06-05 | 7 | 7 | 4 | +57.1% | 0.264 | $-2.13 | -23.0% | 0.432 | day_forecast_capped:3,day_marginal_runway:1,day_open_runway:3 | Helsinki,Taipei,Tokyo |
| 2026-06-06 | 4 | 4 | 4 | +100.0% | 0.450 | $+21.04 | +233.7% | 0.285 | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:2 |  |
| 2026-06-07 | 5 | 5 | 2 | +40.0% | 0.333 | $-3.71 | -44.7% | 0.273 | day_forecast_capped:2,day_marginal_runway:1,day_open_runway:2 | Istanbul,NYC,SanFrancisco |
| 2026-06-08 | 9 | 9 | 8 | +88.9% | 0.329 | $+10.52 | +71.1% | 0.388 | day_forecast_capped:5,day_marginal_runway:3,day_open_runway:1 | TelAviv |
| 2026-06-09 | 10 | 10 | 5 | +50.0% | 0.348 | $+12.09 | +69.5% | 0.324 | day_forecast_capped:4,day_marginal_runway:5,day_open_runway:1 | CapeTown,Chengdu,Istanbul,SanFrancisco,Singapore |
| 2026-06-10 | 7 | 7 | 4 | +57.1% | 0.293 | $-2.63 | -25.7% | 0.308 | day_forecast_capped:1,day_marginal_runway:2,day_open_runway:4 | Beijing,Busan,Warsaw |
| 2026-06-11 | 10 | 10 | 4 | +40.0% | 0.494 | $+9.21 | +37.3% | 0.203 | day_forecast_capped:1,day_marginal_runway:6,day_open_runway:3 | Ankara,Istanbul,Manila,SanFrancisco,TelAviv,Wuhan |
| 2026-06-12 | 14 | 14 | 5 | +35.7% | 0.442 | $+13.74 | +44.4% | 0.216 | day_forecast_capped:2,day_marginal_runway:9,day_open_runway:3 | Ankara,Atlanta,Busan,Houston,Karachi,LA,Manila,SanFrancisco,TelAviv |
| 2026-06-13 | 7 | 7 | 5 | +71.4% | 0.317 | $+4.22 | +38.1% | 0.325 | day_forecast_capped:2,day_marginal_runway:4,day_open_runway:1 | Helsinki,Karachi |
| 2026-06-14 | 10 | 10 | 8 | +80.0% | 0.369 | $+34.56 | +187.3% | 0.255 | day_forecast_capped:2,day_marginal_runway:5,day_open_runway:3 | Lucknow,Shanghai |
| 2026-06-15 | 12 | 12 | 7 | +58.3% | 0.360 | $+10.93 | +50.6% | 0.217 | day_forecast_capped:3,day_marginal_runway:7,day_open_runway:2 | Lucknow,Munich,NYC,SanFrancisco,TelAviv |
| 2026-06-16 | 5 | 5 | 2 | +40.0% | 0.352 | $+0.39 | +4.5% | 0.386 | day_forecast_capped:2,day_marginal_runway:1,day_open_runway:2 | Austin,Guangzhou,TelAviv |
| 2026-06-17 | 9 | 9 | 4 | +44.4% | 0.285 | $-6.32 | -49.4% | 0.365 | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:4 | Beijing,Chengdu,Istanbul,Karachi,Wuhan |
| 2026-06-18 | 5 | 5 | 3 | +60.0% | 0.279 | $-1.97 | -28.2% | 0.440 | day_forecast_capped:3,day_marginal_runway:2 | CapeTown,Manila |
| 2026-06-19 | 12 | 12 | 6 | +50.0% | 0.347 | $+7.40 | +35.5% | 0.298 | day_forecast_capped:4,day_marginal_runway:5,day_open_runway:3 | BuenosAires,Busan,Houston,Karachi,Taipei,TelAviv |
| 2026-06-20 | 8 | 8 | 4 | +50.0% | 0.331 | $-2.48 | -18.8% | 0.357 | day_forecast_capped:3,day_marginal_runway:4,day_open_runway:1 | Jeddah,Karachi,Manila,Tokyo |
| 2026-06-21 | 9 | 9 | 3 | +33.3% | 0.313 | $-2.35 | -16.6% | 0.337 | day_forecast_capped:4,day_marginal_runway:3,day_open_runway:2 | Beijing,CapeTown,Denver,Jeddah,Singapore,TelAviv |
| 2026-06-22 | 3 | 3 | 0 | +0.0% | 0.112 | $-1.68 | -100.0% | 0.432 | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:1 | NYC,Shanghai,Tokyo |
| 2026-06-23 | 6 | 6 | 3 | +50.0% | 0.326 | $+6.55 | +66.8% | 0.362 | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:4 | Chongqing,Jeddah,Karachi |
| 2026-06-24 | 3 | 3 | 2 | +66.7% | 0.310 | $+3.37 | +72.4% | 0.333 | day_forecast_capped:1,day_open_runway:2 | Ankara |
| 2026-06-25 | 5 | 5 | 4 | +80.0% | 0.266 | $+2.54 | +38.2% | 0.378 | day_forecast_capped:1,day_marginal_runway:2,day_open_runway:2 | Karachi |
| 2026-06-26 | 5 | 5 | 3 | +60.0% | 0.405 | $-0.49 | -4.9% | 0.231 | day_marginal_runway:3,day_open_runway:2 | Chongqing,TelAviv |

## Balanced Candidate Worst PnL Days

| target_date | trades | wins | win_rate | avg_weight | weighted_profit_usd | weighted_roi | regime_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-31 | 8 | 2 | +25.0% | 0.335 | $-10.35 | -77.2% | day_forecast_capped:3,day_marginal_runway:4,day_open_runway:1 | Busan,Karachi,Miami,TelAviv,Tokyo,Wuhan |
| 2026-06-02 | 9 | 4 | +44.4% | 0.381 | $-9.52 | -55.5% | day_forecast_capped:3,day_marginal_runway:3,day_open_runway:3 | Amsterdam,Beijing,Busan,Houston,TelAviv |
| 2026-06-01 | 5 | 1 | +20.0% | 0.431 | $-9.01 | -83.6% | day_forecast_capped:1,day_marginal_runway:3,day_open_runway:1 | CapeTown,Houston,NYC,TelAviv |
| 2026-05-25 | 8 | 2 | +25.0% | 0.309 | $-6.74 | -54.6% | day_forecast_capped:1,day_marginal_runway:5,day_open_runway:2 | CapeTown,Jeddah,Shanghai,TelAviv,Tokyo,Wuhan |
| 2026-06-17 | 9 | 4 | +44.4% | 0.285 | $-6.32 | -49.4% | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:4 | Beijing,Chengdu,Istanbul,Karachi,Wuhan |
| 2026-06-07 | 5 | 2 | +40.0% | 0.333 | $-3.71 | -44.7% | day_forecast_capped:2,day_marginal_runway:1,day_open_runway:2 | Istanbul,NYC,SanFrancisco |
| 2026-05-27 | 5 | 2 | +40.0% | 0.414 | $-3.34 | -32.3% | day_marginal_runway:4,day_open_runway:1 | Beijing,Manila,SaoPaulo |
| 2026-05-24 | 4 | 2 | +50.0% | 0.338 | $-3.09 | -45.6% | day_forecast_capped:1,day_marginal_runway:2,day_open_runway:1 | Lucknow,NYC |
| 2026-05-21 | 8 | 3 | +37.5% | 0.317 | $-2.86 | -22.5% | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:3 | Manila,Munich,SanFrancisco,SaoPaulo,TelAviv |
| 2026-06-10 | 7 | 4 | +57.1% | 0.293 | $-2.63 | -25.7% | day_forecast_capped:1,day_marginal_runway:2,day_open_runway:4 | Beijing,Busan,Warsaw |
| 2026-06-20 | 8 | 4 | +50.0% | 0.331 | $-2.48 | -18.8% | day_forecast_capped:3,day_marginal_runway:4,day_open_runway:1 | Jeddah,Karachi,Manila,Tokyo |
| 2026-06-21 | 9 | 3 | +33.3% | 0.313 | $-2.35 | -16.6% | day_forecast_capped:4,day_marginal_runway:3,day_open_runway:2 | Beijing,CapeTown,Denver,Jeddah,Singapore,TelAviv |

## City Distribution

| city | trades | active_dates | wins | win_rate | avg_ask | profit_usd | roi | route_mix | loss_dates |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TelAviv | 11 | 11 | 1 | +9.1% | 0.333 | $-38.33 | -69.7% | runway_current_no:11 | 2026-05-25,2026-05-28,2026-05-31,2026-06-01,2026-06-02,2026-06-08,2026-06-11,2026-06-12,2026-06-16,2026-06-19 |
| Tokyo | 10 | 10 | 2 | +20.0% | 0.384 | $-28.93 | -57.9% | capped_d1_no:6,runway_current_no:4 | 2026-05-29,2026-05-31,2026-06-05,2026-06-08,2026-06-10,2026-06-12,2026-06-19,2026-06-20 |
| SaoPaulo | 3 | 3 | 0 | +0.0% | 0.410 | $-15.00 | -100.0% | capped_d1_no:1,runway_current_no:2 | 2026-05-21,2026-05-22,2026-05-27 |
| Ankara | 5 | 5 | 1 | +20.0% | 0.424 | $-14.80 | -59.2% | capped_d1_no:4,runway_current_no:1 | 2026-05-21,2026-05-28,2026-06-04,2026-06-08 |
| Houston | 7 | 7 | 2 | +28.6% | 0.406 | $-13.43 | -38.4% | capped_d1_no:1,runway_current_no:6 | 2026-06-01,2026-06-02,2026-06-12,2026-06-17,2026-06-19 |
| Manila | 5 | 5 | 1 | +20.0% | 0.332 | $-12.50 | -50.0% | capped_d1_no:1,runway_current_no:4 | 2026-05-28,2026-06-03,2026-06-11,2026-06-12 |
| Chengdu | 2 | 2 | 0 | +0.0% | 0.435 | $-10.00 | -100.0% | capped_d1_no:1,runway_current_no:1 | 2026-05-24,2026-06-17 |
| Wuhan | 9 | 9 | 3 | +33.3% | 0.400 | $-9.04 | -20.1% | capped_d1_no:1,runway_current_no:8 | 2026-05-25,2026-05-26,2026-05-28,2026-05-31,2026-06-03,2026-06-11 |
| Karachi | 5 | 5 | 1 | +20.0% | 0.420 | $-8.87 | -35.5% | capped_d1_no:3,runway_current_no:2 | 2026-06-10,2026-06-14,2026-06-18,2026-06-25 |
| Chongqing | 4 | 4 | 1 | +25.0% | 0.306 | $-8.64 | -43.2% | capped_d1_no:1,runway_current_no:3 | 2026-05-28,2026-06-17,2026-06-26 |
| BuenosAires | 1 | 1 | 0 | +0.0% | 0.487 | $-5.00 | -100.0% | capped_d1_no:1 | 2026-05-31 |
| Dallas | 1 | 1 | 0 | +0.0% | 0.170 | $-5.00 | -100.0% | capped_d1_no:1 | 2026-06-02 |
| CapeTown | 3 | 3 | 1 | +33.3% | 0.407 | $-4.80 | -32.0% | capped_d1_no:1,runway_current_no:2 | 2026-06-01,2026-06-25 |
| Singapore | 7 | 7 | 3 | +42.9% | 0.436 | $-3.95 | -11.3% | capped_d1_no:6,runway_current_no:1 | 2026-05-27,2026-05-30,2026-06-13,2026-06-14 |
| Warsaw | 5 | 5 | 2 | +40.0% | 0.366 | $-2.96 | -11.8% | capped_d1_no:3,runway_current_no:2 | 2026-05-27,2026-06-10,2026-06-26 |
| Helsinki | 6 | 6 | 2 | +33.3% | 0.388 | $-2.14 | -7.1% | capped_d1_no:2,runway_current_no:4 | 2026-05-22,2026-06-05,2026-06-13,2026-06-17 |
| Miami | 8 | 8 | 3 | +37.5% | 0.381 | $+0.06 | +0.2% | capped_d1_no:7,runway_current_no:1 | 2026-05-22,2026-05-27,2026-05-31,2026-06-07,2026-06-19 |
| Wellington | 9 | 9 | 4 | +44.4% | 0.389 | $+1.29 | +2.9% | capped_d1_no:8,runway_current_no:1 | 2026-05-25,2026-06-07,2026-06-12,2026-06-13,2026-06-23 |
| Shanghai | 8 | 8 | 2 | +25.0% | 0.293 | $+4.47 | +11.2% | capped_d1_no:3,runway_current_no:5 | 2026-05-22,2026-05-25,2026-06-01,2026-06-02,2026-06-14,2026-06-22 |
| Istanbul | 6 | 6 | 3 | +50.0% | 0.382 | $+7.55 | +25.2% | capped_d1_no:1,runway_current_no:5 | 2026-06-07,2026-06-09,2026-06-11 |
| Guangzhou | 1 | 1 | 1 | +100.0% | 0.390 | $+7.82 | +156.4% | capped_d1_no:1 |  |
| Atlanta | 5 | 5 | 3 | +60.0% | 0.452 | $+9.23 | +36.9% | capped_d1_no:2,runway_current_no:3 | 2026-05-30,2026-06-12 |
| Denver | 1 | 1 | 1 | +100.0% | 0.280 | $+12.86 | +257.1% | runway_current_no:1 |  |
| Lucknow | 4 | 4 | 1 | +25.0% | 0.258 | $+13.33 | +66.7% | runway_current_no:4 | 2026-05-23,2026-06-14,2026-06-15 |
| Munich | 4 | 4 | 3 | +75.0% | 0.430 | $+14.45 | +72.2% | capped_d1_no:4 | 2026-05-22 |
| Seattle | 1 | 1 | 1 | +100.0% | 0.250 | $+15.00 | +300.0% | runway_current_no:1 |  |
| Amsterdam | 4 | 4 | 2 | +50.0% | 0.315 | $+20.58 | +102.9% | runway_current_no:4 | 2026-05-23,2026-06-02 |
| Beijing | 9 | 9 | 5 | +55.6% | 0.402 | $+21.93 | +48.7% | capped_d1_no:1,runway_current_no:8 | 2026-05-20,2026-06-10,2026-06-17,2026-06-24 |
| SanFrancisco | 3 | 3 | 2 | +66.7% | 0.367 | $+23.65 | +157.6% | capped_d1_no:1,runway_current_no:2 | 2026-06-07 |
| NYC | 6 | 6 | 3 | +50.0% | 0.357 | $+24.82 | +82.7% | runway_current_no:6 | 2026-05-28,2026-06-01,2026-06-07 |
| LA | 3 | 3 | 2 | +66.7% | 0.253 | $+24.97 | +166.5% | capped_d1_no:1,runway_current_no:2 | 2026-06-15 |
| Busan | 7 | 7 | 3 | +42.9% | 0.286 | $+25.27 | +72.2% | runway_current_no:7 | 2026-05-31,2026-06-10,2026-06-12,2026-06-19 |
| Austin | 3 | 3 | 3 | +100.0% | 0.317 | $+37.65 | +251.0% | capped_d1_no:1,runway_current_no:2 |  |
| Jeddah | 7 | 7 | 5 | +71.4% | 0.342 | $+42.09 | +120.3% | capped_d1_no:2,runway_current_no:5 | 2026-06-20,2026-06-21 |
| Taipei | 5 | 5 | 3 | +60.0% | 0.287 | $+43.97 | +175.9% | capped_d1_no:2,runway_current_no:3 | 2026-05-20,2026-06-05 |

## Recent Model Predictions

| target_date | settlement_known | predicted_trades | cities | avg_ask | known_wins | known_profit_usd |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-06-21 | True | 2 | 2 | 0.375 | 1.000 | $+6.13 |
| 2026-06-22 | True | 1 | 1 | 0.220 | 0.000 | $-5.00 |
| 2026-06-23 | True | 2 | 2 | 0.355 | 1.000 | $+13.81 |
| 2026-06-24 | True | 2 | 2 | 0.420 | 1.000 | $+2.50 |
| 2026-06-25 | True | 3 | 3 | 0.450 | 1.000 | $-4.36 |
| 2026-06-26 | True | 2 | 2 | 0.340 | 0.000 | $-10.00 |
| 2026-06-27 | False | 2 | 2 | 0.295 | 0.000 | $+0.00 |

| target_date | city | decision_hour_local | model_action | day_regime | expression | ask | settlement_known | resolved_payoff | resolved_profit_usd | gamma_check_status | current_bracket | d1_no_bracket | forecast_max_native | running_native | intraday_state | city_family |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-21 | Jeddah | 13 | buy current-bracket NO | day_open_runway | current_bracket_no | 0.440 | True | 0.000 | $-5.00 |  | 35 | 36 | 38.100 | 35.000 | pullback_uncertain | continental_dry_hot |
| 2026-06-21 | Karachi | 13 | buy current-bracket NO | day_marginal_runway | current_bracket_no | 0.310 | True | 1.000 | $+11.13 |  | 34 | 35 | 34.900 | 33.889 | active_warming | continental_dry_hot |
| 2026-06-22 | Shanghai | 14 | buy current-bracket NO | day_marginal_runway | current_bracket_no | 0.220 | True | 0.000 | $-5.00 |  | 24 | 25 | 24.600 | 23.889 | mature_fade | humid_low_latitude |
| 2026-06-23 | NYC | 10 | buy current-bracket NO | day_open_runway | current_bracket_no | 0.210 | True | 1.000 | $+18.81 |  | 70-71 | 72-73 | 75.300 | 71.000 | mature_fade | southern_or_maritime |
| 2026-06-23 | Wellington | 11 | buy d1 NO | day_forecast_capped | d1_no | 0.500 | True | 0.000 | $-5.00 |  | 13 | 14 | 12.900 | 12.778 | active_warming | southern_or_maritime |
| 2026-06-24 | Beijing | 14 | buy d1 NO | day_forecast_capped | d1_no | 0.440 | True | 0.000 | $-5.00 |  | 29 | 30 | 29.300 | 28.889 | fresh_high | continental_dry_hot |
| 2026-06-24 | Jeddah | 13 | buy current-bracket NO | day_open_runway | current_bracket_no | 0.400 | True | 1.000 | $+7.50 |  | 34 | 35 | 37.100 | 33.889 | pullback_uncertain | continental_dry_hot |
| 2026-06-25 | CapeTown | 13 | buy d1 NO | day_forecast_capped | d1_no | 0.380 | True | 0.000 | $-5.00 |  | 21 | 22 | 21.300 | 21.111 | active_warming | southern_or_maritime |
| 2026-06-25 | Karachi | 13 | buy current-bracket NO | day_open_runway | current_bracket_no | 0.500 | True | 0.000 | $-5.00 |  | 33 | 34 | 35.000 | 32.778 | fresh_high | continental_dry_hot |
| 2026-06-25 | Wuhan | 14 | buy current-bracket NO | day_marginal_runway | current_bracket_no | 0.470 | True | 1.000 | $+5.64 |  | 29 | 30 | 29.600 | 28.889 | active_warming | humid_low_latitude |
| 2026-06-26 | Chongqing | 12 | buy current-bracket NO | day_open_runway | current_bracket_no | 0.290 | True | 0.000 | $-5.00 |  | 23 | 24 | 28.300 | 22.778 | fresh_high | humid_low_latitude |
| 2026-06-26 | Warsaw | 14 | buy d1 NO | day_forecast_capped | d1_no | 0.390 | True | 0.000 | $-5.00 |  | 31 | 32 | 31.500 | 31.111 | active_warming | europe_cloud_break |
| 2026-06-27 | Shanghai | 11 | buy d1 NO | day_forecast_capped | d1_no | 0.300 | False | NA | NA |  | 26 | 27 | 25.900 | 26.111 | fresh_high | humid_low_latitude |
| 2026-06-27 | TelAviv | 13 | buy current-bracket NO | day_marginal_runway | current_bracket_no | 0.290 | False | NA | NA |  | 30 | 31 | 31.200 | 30.000 | fresh_high | southern_or_maritime |

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
