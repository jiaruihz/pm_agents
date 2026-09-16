# Regime-Routed NO Expression V1

## 结论

把 regime 当成表达式路由器这个方向可以测，但不能只看 strict 口径。这里并排比较 A strict、B relaxed ask cap、C first-eligible live-like timing、D best-ask optimistic timing；主结论只看 first-eligible，best-ask 仅作上界对照。本报告把已结算 ROI 和最近模型候选分开，避免未结算日期被静默过滤。

Verdict: `balanced_shadow_candidate_but_not_live_ready`，live_ready=`False`。

raw 候选里 `routed_capped_d1_no_relaxed50_best_ask` 的 ROI 最高但日内 tail 偏薄；当前更平衡的 shadow 候选是 `routed_capped_d2_no_relaxed70_first_eligible + soft_balanced`：保留 279 笔 / 38 天 / 35 城，胜率 +49.5%，weighted ROI -6.3%，date-block CI [-22.4%, +9.3%]；仍是 shadow 候选，不是 live 规则。

## Variant Summary

| variant | selected_trades | active_dates | cities | win_rate | avg_ask | profit_usd | roi | roi_ci_low | roi_ci_high | baseline_roi | excess_roi_vs_baseline | roi_le_minus50_days | roi_eq_minus100_days | open_runway_trades | marginal_runway_trades | forecast_capped_trades |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_current_no_strict35_near_noon | 101 | 35 | 32 | +24.8% | 0.247 | $+41.81 | +8.3% | -31.7% | +47.3% | NA | NA | 16 | 16 | 26 | 23 | 52 |
| routed_capped_d1_no_strict35_near_noon | 75 | 31 | 28 | +28.0% | 0.262 | $+73.91 | +19.7% | -18.4% | +61.0% | +8.3% | +11.4% | 13 | 13 | 26 | 23 | 26 |
| routed_capped_d2_no_strict35_near_noon | 50 | 28 | 25 | +30.0% | 0.251 | $+85.51 | +34.2% | -27.8% | +98.7% | +8.3% | +25.9% | 16 | 16 | 26 | 23 | 1 |
| routed_capped_d1_no_strict35_first_eligible | 75 | 31 | 28 | +28.0% | 0.266 | $+69.04 | +18.4% | -19.2% | +58.3% | +8.3% | +10.1% | 13 | 13 | 26 | 23 | 26 |
| routed_capped_d2_no_strict35_first_eligible | 50 | 28 | 25 | +30.0% | 0.257 | $+80.64 | +32.3% | -28.5% | +97.7% | +8.3% | +24.0% | 16 | 16 | 26 | 23 | 1 |
| routed_capped_d1_no_strict35_best_ask | 75 | 31 | 28 | +28.0% | 0.258 | $+74.36 | +19.8% | -18.3% | +61.0% | +8.3% | +11.6% | 13 | 13 | 26 | 23 | 26 |
| routed_capped_d2_no_strict35_best_ask | 50 | 28 | 25 | +30.0% | 0.250 | $+85.51 | +34.2% | -27.8% | +98.7% | +8.3% | +25.9% | 16 | 16 | 26 | 23 | 1 |
| routed_capped_d1_no_relaxed50_near_noon | 195 | 38 | 34 | +37.4% | 0.381 | $+64.12 | +6.6% | -12.8% | +25.1% | +8.3% | -1.7% | 10 | 4 | 52 | 49 | 94 |
| routed_capped_d2_no_relaxed50_near_noon | 112 | 36 | 30 | +37.5% | 0.365 | $+84.81 | +15.1% | -17.3% | +49.5% | +8.3% | +6.9% | 14 | 11 | 52 | 49 | 11 |
| routed_capped_d1_no_relaxed50_first_eligible | 195 | 38 | 34 | +37.4% | 0.384 | $+58.94 | +6.0% | -12.6% | +24.1% | +8.3% | -2.2% | 10 | 4 | 52 | 49 | 94 |
| routed_capped_d2_no_relaxed50_first_eligible | 112 | 36 | 30 | +37.5% | 0.369 | $+80.75 | +14.4% | -17.2% | +48.3% | +8.3% | +6.1% | 14 | 11 | 52 | 49 | 11 |
| routed_capped_d1_no_relaxed50_best_ask | 195 | 38 | 34 | +36.9% | 0.370 | $+64.46 | +6.6% | -13.3% | +25.8% | +8.3% | -1.7% | 11 | 4 | 51 | 50 | 94 |
| routed_capped_d2_no_relaxed50_best_ask | 112 | 36 | 30 | +36.6% | 0.356 | $+79.41 | +14.2% | -18.4% | +48.7% | +8.3% | +5.9% | 14 | 11 | 51 | 50 | 11 |
| routed_capped_d1_no_relaxed70_near_noon | 425 | 38 | 35 | +48.5% | 0.525 | $-108.25 | -5.1% | -14.4% | +3.8% | +8.3% | -13.4% | 3 | 1 | 100 | 89 | 236 |
| routed_capped_d2_no_relaxed70_near_noon | 279 | 38 | 35 | +49.5% | 0.545 | $-82.17 | -5.9% | -19.4% | +7.3% | +8.3% | -14.2% | 5 | 2 | 100 | 90 | 89 |
| routed_capped_d1_no_relaxed70_first_eligible | 425 | 38 | 35 | +48.0% | 0.531 | $-129.66 | -6.1% | -15.6% | +3.0% | +8.3% | -14.4% | 3 | 1 | 102 | 88 | 235 |
| routed_capped_d2_no_relaxed70_first_eligible | 279 | 38 | 35 | +49.5% | 0.552 | $-85.53 | -6.1% | -19.6% | +7.0% | +8.3% | -14.4% | 5 | 2 | 102 | 88 | 89 |
| routed_capped_d1_no_relaxed70_best_ask | 425 | 38 | 35 | +47.5% | 0.501 | $-19.52 | -0.9% | -11.6% | +9.2% | +8.3% | -9.2% | 3 | 1 | 96 | 92 | 237 |
| routed_capped_d2_no_relaxed70_best_ask | 279 | 38 | 35 | +48.4% | 0.517 | $-8.69 | -0.6% | -16.1% | +15.6% | +8.3% | -8.9% | 5 | 2 | 96 | 94 | 89 |

## Main Candidate Daily

| target_date | trades | cities | wins | win_rate | profit_usd | roi | regime_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-20 | 5 | 5 | 2 | +40.0% | $+26.19 | +104.8% | day_forecast_capped:1,day_marginal_runway:2,day_open_runway:2 | Ankara,Beijing,Taipei |
| 2026-05-21 | 2 | 2 | 1 | +50.0% | $+1.63 | +16.3% | day_forecast_capped:1,day_marginal_runway:1 | SaoPaulo |
| 2026-05-22 | 8 | 8 | 3 | +37.5% | $+2.31 | +5.8% | day_forecast_capped:5,day_marginal_runway:1,day_open_runway:2 | Helsinki,LA,Miami,Munich,Singapore |
| 2026-05-23 | 4 | 4 | 1 | +25.0% | $-7.18 | -35.9% | day_forecast_capped:2,day_open_runway:2 | Houston,Lucknow,Miami |
| 2026-05-24 | 1 | 1 | 1 | +100.0% | $+7.82 | +156.4% | day_forecast_capped:1 |  |
| 2026-05-25 | 8 | 8 | 3 | +37.5% | $-5.09 | -12.7% | day_forecast_capped:6,day_open_runway:2 | LA,Shanghai,Warsaw,Wellington,Wuhan |
| 2026-05-26 | 5 | 5 | 4 | +80.0% | $+28.71 | +114.8% | day_forecast_capped:2,day_marginal_runway:2,day_open_runway:1 | Wuhan |
| 2026-05-27 | 9 | 9 | 2 | +22.2% | $-22.78 | -50.6% | day_forecast_capped:4,day_marginal_runway:4,day_open_runway:1 | Denver,Helsinki,Houston,Miami,SaoPaulo,Singapore,Warsaw |
| 2026-05-28 | 6 | 6 | 2 | +33.3% | $-0.04 | -0.1% | day_marginal_runway:4,day_open_runway:2 | Ankara,Chongqing,Manila,Wuhan |
| 2026-05-29 | 6 | 6 | 3 | +50.0% | $+10.42 | +34.7% | day_forecast_capped:4,day_marginal_runway:1,day_open_runway:1 | LA,Manila,Tokyo |
| 2026-05-30 | 3 | 3 | 2 | +66.7% | $+5.20 | +34.7% | day_forecast_capped:3 | Singapore |
| 2026-05-31 | 5 | 5 | 1 | +20.0% | $-13.37 | -53.5% | day_forecast_capped:2,day_marginal_runway:2,day_open_runway:1 | BuenosAires,Tokyo,Warsaw,Wuhan |
| 2026-06-01 | 6 | 6 | 2 | +33.3% | $-6.54 | -21.8% | day_forecast_capped:4,day_marginal_runway:1,day_open_runway:1 | Amsterdam,CapeTown,NYC,Shanghai |
| 2026-06-02 | 4 | 4 | 2 | +50.0% | $+0.84 | +4.2% | day_forecast_capped:3,day_open_runway:1 | LA,TelAviv |
| 2026-06-03 | 4 | 4 | 1 | +25.0% | $-10.00 | -50.0% | day_forecast_capped:3,day_open_runway:1 | Atlanta,NYC,Wuhan |
| 2026-06-04 | 4 | 4 | 2 | +50.0% | $+0.20 | +1.0% | day_forecast_capped:2,day_open_runway:2 | Ankara,LA |
| 2026-06-05 | 3 | 3 | 0 | +0.0% | $-15.00 | -100.0% | day_open_runway:3 | Helsinki,Taipei,Tokyo |
| 2026-06-06 | 6 | 6 | 2 | +33.3% | $-1.94 | -6.5% | day_forecast_capped:2,day_marginal_runway:2,day_open_runway:2 | LA,NYC,SaoPaulo,Tokyo |
| 2026-06-07 | 8 | 8 | 3 | +37.5% | $+7.53 | +18.8% | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:3 | Istanbul,LA,NYC,Warsaw,Wellington |
| 2026-06-08 | 5 | 5 | 3 | +60.0% | $+15.31 | +61.3% | day_forecast_capped:2,day_marginal_runway:2,day_open_runway:1 | Miami,TelAviv |
| 2026-06-09 | 3 | 3 | 2 | +66.7% | $+14.82 | +98.8% | day_forecast_capped:1,day_marginal_runway:2 | Istanbul |
| 2026-06-10 | 6 | 6 | 1 | +16.7% | $-16.49 | -55.0% | day_forecast_capped:4,day_marginal_runway:1,day_open_runway:1 | Beijing,Busan,NYC,Singapore,Tokyo |
| 2026-06-11 | 9 | 9 | 1 | +11.1% | $-34.80 | -77.3% | day_forecast_capped:4,day_marginal_runway:3,day_open_runway:2 | Ankara,Austin,Istanbul,Manila,Miami,SaoPaulo,TelAviv,Wuhan |
| 2026-06-12 | 9 | 9 | 3 | +33.3% | $+3.14 | +7.0% | day_forecast_capped:3,day_marginal_runway:3,day_open_runway:3 | Atlanta,Busan,Houston,Manila,TelAviv,Wellington |
| 2026-06-13 | 8 | 8 | 2 | +25.0% | $-13.99 | -35.0% | day_forecast_capped:4,day_marginal_runway:2,day_open_runway:2 | Amsterdam,Helsinki,LA,Miami,Singapore,Wuhan |
| 2026-06-14 | 14 | 14 | 6 | +42.9% | $+30.25 | +43.2% | day_forecast_capped:6,day_marginal_runway:2,day_open_runway:6 | Chongqing,Dallas,Karachi,Lucknow,Seattle,Shanghai,Singapore,TelAviv |
| 2026-06-15 | 11 | 11 | 7 | +63.6% | $+33.38 | +60.7% | day_forecast_capped:5,day_marginal_runway:2,day_open_runway:4 | Houston,Karachi,LA,Lucknow |
| 2026-06-16 | 5 | 5 | 2 | +40.0% | $+6.34 | +25.4% | day_forecast_capped:2,day_marginal_runway:1,day_open_runway:2 | Atlanta,TelAviv,Wellington |
| 2026-06-17 | 5 | 5 | 1 | +20.0% | $-13.10 | -52.4% | day_forecast_capped:5 | CapeTown,Chongqing,Helsinki,Houston |
| 2026-06-18 | 1 | 1 | 0 | +0.0% | $-5.00 | -100.0% | day_forecast_capped:1 | Karachi |
| 2026-06-19 | 5 | 5 | 3 | +60.0% | $+9.39 | +37.6% | day_forecast_capped:3,day_marginal_runway:2 | Busan,Miami |
| 2026-06-20 | 3 | 3 | 1 | +33.3% | $-4.80 | -32.0% | day_marginal_runway:3 | Jeddah,Tokyo |
| 2026-06-21 | 2 | 2 | 1 | +50.0% | $+1.11 | +11.1% | day_marginal_runway:1,day_open_runway:1 | Jeddah |
| 2026-06-22 | 1 | 1 | 0 | +0.0% | $-5.00 | -100.0% | day_marginal_runway:1 | Shanghai |
| 2026-06-23 | 2 | 2 | 1 | +50.0% | $+13.81 | +138.1% | day_forecast_capped:1,day_open_runway:1 | Wellington |
| 2026-06-25 | 3 | 3 | 1 | +33.3% | $-4.36 | -29.1% | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:1 | CapeTown,Karachi |
| 2026-06-26 | 2 | 2 | 0 | +0.0% | $-10.00 | -100.0% | day_forecast_capped:1,day_open_runway:1 | Chongqing,Warsaw |
| 2026-06-30 | 4 | 4 | 1 | +25.0% | $+30.00 | +150.0% | day_forecast_capped:3,day_marginal_runway:1 | Busan,CapeTown,Helsinki |

## Main Candidate Worst Days

| target_date | trades | wins | win_rate | profit_usd | roi | regime_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-05 | 3 | 0 | +0.0% | $-15.00 | -100.0% | day_open_runway:3 | Helsinki,Taipei,Tokyo |
| 2026-06-18 | 1 | 0 | +0.0% | $-5.00 | -100.0% | day_forecast_capped:1 | Karachi |
| 2026-06-26 | 2 | 0 | +0.0% | $-10.00 | -100.0% | day_forecast_capped:1,day_open_runway:1 | Chongqing,Warsaw |
| 2026-06-22 | 1 | 0 | +0.0% | $-5.00 | -100.0% | day_marginal_runway:1 | Shanghai |
| 2026-06-11 | 9 | 1 | +11.1% | $-34.80 | -77.3% | day_forecast_capped:4,day_marginal_runway:3,day_open_runway:2 | Ankara,Austin,Istanbul,Manila,Miami,SaoPaulo,TelAviv,Wuhan |
| 2026-06-10 | 6 | 1 | +16.7% | $-16.49 | -55.0% | day_forecast_capped:4,day_marginal_runway:1,day_open_runway:1 | Beijing,Busan,NYC,Singapore,Tokyo |
| 2026-05-31 | 5 | 1 | +20.0% | $-13.37 | -53.5% | day_forecast_capped:2,day_marginal_runway:2,day_open_runway:1 | BuenosAires,Tokyo,Warsaw,Wuhan |
| 2026-06-17 | 5 | 1 | +20.0% | $-13.10 | -52.4% | day_forecast_capped:5 | CapeTown,Chongqing,Helsinki,Houston |
| 2026-05-27 | 9 | 2 | +22.2% | $-22.78 | -50.6% | day_forecast_capped:4,day_marginal_runway:4,day_open_runway:1 | Denver,Helsinki,Houston,Miami,SaoPaulo,Singapore,Warsaw |
| 2026-06-03 | 4 | 1 | +25.0% | $-10.00 | -50.0% | day_forecast_capped:3,day_open_runway:1 | Atlanta,NYC,Wuhan |
| 2026-05-23 | 4 | 1 | +25.0% | $-7.18 | -35.9% | day_forecast_capped:2,day_open_runway:2 | Houston,Lucknow,Miami |
| 2026-06-13 | 8 | 2 | +25.0% | $-13.99 | -35.0% | day_forecast_capped:4,day_marginal_runway:2,day_open_runway:2 | Amsterdam,Helsinki,LA,Miami,Singapore,Wuhan |

## Route-Leg Breakdown

| route_leg | day_regime | expression | trades | active_dates | cities | win_rate | avg_ask | profit_usd | roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| capped_d1_no | day_forecast_capped | d1_no | 94 | 33 | 31 | +38.3% | 0.409 | $-22.28 | -4.7% |
| runway_current_no | day_marginal_runway | current_bracket_no | 49 | 26 | 21 | +34.7% | 0.369 | $+0.80 | +0.3% |
| runway_current_no | day_open_runway | current_bracket_no | 52 | 28 | 24 | +38.5% | 0.355 | $+80.43 | +30.9% |

## Soft Weight Overlay

soft weight 只改 notional，不筛单。固定机制权重：`route_multiplier × price_multiplier × weather_multiplier × day_multiplier`；`day_multiplier` 已改为 row-local/live-like，不使用当天完整截面、payoff、final max 或 settlement 训练。

| variant | weight_policy | trades | active_dates | cities | win_rate | avg_ask | notional_retained | weighted_profit_usd | weighted_roi | weighted_roi_ci_low | weighted_roi_ci_high | roi_le_minus50_days | roi_eq_minus100_days | loss_ge_10usd_days | worst_day_profit_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| routed_capped_d1_no_relaxed50_first_eligible | full_size | 195 | 38 | 34 | +37.4% | 0.384 | +100.0% | $+58.94 | +6.0% | -12.6% | +24.1% | 10 | 4 | 9 | $-34.80 |
| routed_capped_d1_no_relaxed50_first_eligible | soft_moderate | 195 | 38 | 34 | +37.4% | 0.384 | +64.6% | $+33.77 | +5.4% | -14.3% | +25.2% | 10 | 4 | 5 | $-20.35 |
| routed_capped_d1_no_relaxed50_first_eligible | soft_balanced | 195 | 38 | 34 | +37.4% | 0.384 | +39.7% | $+12.29 | +3.2% | -16.7% | +24.2% | 9 | 4 | 2 | $-11.74 |
| routed_capped_d2_no_relaxed50_first_eligible | full_size | 112 | 36 | 30 | +37.5% | 0.369 | +100.0% | $+80.75 | +14.4% | -17.2% | +48.3% | 14 | 11 | 9 | $-25.00 |
| routed_capped_d2_no_relaxed50_first_eligible | soft_moderate | 112 | 36 | 30 | +37.5% | 0.369 | +72.3% | $+45.09 | +11.1% | -19.8% | +45.7% | 14 | 11 | 4 | $-16.21 |
| routed_capped_d2_no_relaxed50_first_eligible | soft_balanced | 112 | 36 | 30 | +37.5% | 0.369 | +47.1% | $+16.71 | +6.3% | -24.2% | +41.2% | 13 | 11 | 1 | $-12.37 |
| routed_capped_d1_no_relaxed70_first_eligible | full_size | 425 | 38 | 35 | +48.0% | 0.531 | +100.0% | $-129.66 | -6.1% | -15.6% | +3.0% | 3 | 1 | 13 | $-40.51 |
| routed_capped_d1_no_relaxed70_first_eligible | soft_moderate | 425 | 38 | 35 | +48.0% | 0.531 | +59.1% | $-85.92 | -6.8% | -16.6% | +2.7% | 5 | 1 | 8 | $-22.37 |
| routed_capped_d1_no_relaxed70_first_eligible | soft_balanced | 425 | 38 | 35 | +48.0% | 0.531 | +29.3% | $-38.94 | -6.3% | -17.2% | +4.2% | 6 | 1 | 3 | $-11.45 |
| routed_capped_d2_no_relaxed70_first_eligible | full_size | 279 | 38 | 35 | +49.5% | 0.552 | +100.0% | $-85.53 | -6.1% | -19.6% | +7.0% | 5 | 2 | 11 | $-40.00 |
| routed_capped_d2_no_relaxed70_first_eligible | soft_moderate | 279 | 38 | 35 | +49.5% | 0.552 | +62.8% | $-63.55 | -7.3% | -20.9% | +6.2% | 6 | 2 | 7 | $-24.08 |
| routed_capped_d2_no_relaxed70_first_eligible | soft_balanced | 279 | 38 | 35 | +49.5% | 0.552 | +31.4% | $-27.50 | -6.3% | -22.4% | +9.3% | 9 | 2 | 3 | $-10.74 |
| routed_capped_d1_no_relaxed50_best_ask | full_size | 195 | 38 | 34 | +36.9% | 0.370 | +100.0% | $+64.46 | +6.6% | -13.3% | +25.8% | 11 | 4 | 9 | $-34.80 |
| routed_capped_d1_no_relaxed50_best_ask | soft_moderate | 195 | 38 | 34 | +36.9% | 0.370 | +64.4% | $+39.71 | +6.3% | -14.7% | +27.4% | 11 | 4 | 6 | $-20.35 |
| routed_capped_d1_no_relaxed50_best_ask | soft_balanced | 195 | 38 | 34 | +36.9% | 0.370 | +39.5% | $+18.15 | +4.7% | -16.6% | +27.2% | 10 | 4 | 3 | $-11.74 |
| routed_capped_d2_no_relaxed50_best_ask | full_size | 112 | 36 | 30 | +36.6% | 0.356 | +100.0% | $+79.41 | +14.2% | -18.4% | +48.7% | 14 | 11 | 9 | $-25.00 |
| routed_capped_d2_no_relaxed50_best_ask | soft_moderate | 112 | 36 | 30 | +36.6% | 0.356 | +71.7% | $+47.61 | +11.9% | -19.6% | +48.0% | 14 | 11 | 4 | $-16.21 |
| routed_capped_d2_no_relaxed50_best_ask | soft_balanced | 112 | 36 | 30 | +36.6% | 0.356 | +46.3% | $+20.89 | +8.1% | -23.5% | +43.6% | 13 | 11 | 1 | $-12.27 |
| routed_capped_d1_no_relaxed70_best_ask | full_size | 425 | 38 | 35 | +47.5% | 0.501 | +100.0% | $-19.52 | -0.9% | -11.6% | +9.2% | 3 | 1 | 11 | $-40.51 |
| routed_capped_d1_no_relaxed70_best_ask | soft_moderate | 425 | 38 | 35 | +47.5% | 0.501 | +59.5% | $-12.46 | -1.0% | -12.6% | +10.5% | 4 | 1 | 8 | $-22.95 |
| routed_capped_d1_no_relaxed70_best_ask | soft_balanced | 425 | 38 | 35 | +47.5% | 0.501 | +30.7% | $-7.02 | -1.1% | -14.4% | +12.8% | 6 | 1 | 3 | $-12.55 |
| routed_capped_d2_no_relaxed70_best_ask | full_size | 279 | 38 | 35 | +48.4% | 0.517 | +100.0% | $-8.69 | -0.6% | -16.1% | +15.6% | 5 | 2 | 12 | $-40.00 |
| routed_capped_d2_no_relaxed70_best_ask | soft_moderate | 279 | 38 | 35 | +48.4% | 0.517 | +63.4% | $-5.10 | -0.6% | -17.6% | +17.2% | 6 | 2 | 6 | $-24.08 |
| routed_capped_d2_no_relaxed70_best_ask | soft_balanced | 279 | 38 | 35 | +48.4% | 0.517 | +33.3% | $-2.46 | -0.5% | -20.3% | +21.1% | 10 | 2 | 4 | $-11.15 |

## Balanced Candidate Daily

| target_date | trades | cities | wins | win_rate | avg_weight | weighted_profit_usd | weighted_roi | day_risk | regime_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-20 | 10 | 10 | 5 | +50.0% | 0.342 | $+8.83 | +51.7% | 0.325 | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:5 | Beijing,BuenosAires,Dallas,Jeddah,SanFrancisco |
| 2026-05-21 | 6 | 6 | 1 | +16.7% | 0.311 | $-7.61 | -81.6% | 0.400 | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:1 | Manila,Miami,SanFrancisco,SaoPaulo,TelAviv |
| 2026-05-22 | 11 | 11 | 6 | +54.5% | 0.261 | $+6.99 | +48.7% | 0.380 | day_forecast_capped:5,day_marginal_runway:3,day_open_runway:3 | Guangzhou,Helsinki,Istanbul,Manila,Munich |
| 2026-05-23 | 6 | 6 | 2 | +33.3% | 0.299 | $-4.54 | -50.6% | 0.338 | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:4 | Houston,Istanbul,Lucknow,Warsaw |
| 2026-05-24 | 6 | 6 | 4 | +66.7% | 0.276 | $+2.30 | +27.8% | 0.429 | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:1 | Houston,Lucknow |
| 2026-05-25 | 8 | 8 | 0 | +0.0% | 0.268 | $-10.74 | -100.0% | 0.402 | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:3 | Busan,CapeTown,Chengdu,Dallas,Jeddah,Shanghai,Tokyo,Wuhan |
| 2026-05-26 | 6 | 6 | 5 | +83.3% | 0.400 | $+5.09 | +42.4% | 0.238 | day_marginal_runway:5,day_open_runway:1 | Wuhan |
| 2026-05-27 | 7 | 7 | 1 | +14.3% | 0.390 | $-10.19 | -74.6% | 0.275 | day_marginal_runway:4,day_open_runway:3 | Beijing,Denver,Helsinki,Houston,Manila,SaoPaulo |
| 2026-05-28 | 11 | 11 | 7 | +63.6% | 0.365 | $+6.03 | +30.0% | 0.292 | day_forecast_capped:2,day_marginal_runway:6,day_open_runway:3 | Ankara,Chongqing,Manila,Wuhan |
| 2026-05-29 | 6 | 6 | 2 | +33.3% | 0.256 | $-4.46 | -58.1% | 0.405 | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:1 | Amsterdam,BuenosAires,LA,Warsaw |
| 2026-05-30 | 4 | 4 | 3 | +75.0% | 0.229 | $+2.86 | +62.5% | 0.522 | day_forecast_capped:4 | Shanghai |
| 2026-05-31 | 7 | 7 | 3 | +42.9% | 0.383 | $-9.66 | -72.0% | 0.389 | day_forecast_capped:4,day_marginal_runway:2,day_open_runway:1 | Karachi,Tokyo,Warsaw,Wuhan |
| 2026-06-01 | 6 | 6 | 4 | +66.7% | 0.247 | $-2.51 | -33.9% | 0.439 | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:1 | Chengdu,NYC |
| 2026-06-02 | 11 | 11 | 7 | +63.6% | 0.270 | $+1.10 | +7.4% | 0.377 | day_forecast_capped:4,day_marginal_runway:2,day_open_runway:5 | Beijing,SanFrancisco,TelAviv,Wuhan |
| 2026-06-03 | 4 | 4 | 3 | +75.0% | 0.205 | $-0.41 | -9.9% | 0.511 | day_forecast_capped:3,day_open_runway:1 | Wuhan |
| 2026-06-04 | 6 | 6 | 4 | +66.7% | 0.225 | $+1.21 | +18.0% | 0.358 | day_forecast_capped:2,day_marginal_runway:1,day_open_runway:3 | Dallas,LA |
| 2026-06-05 | 7 | 7 | 1 | +14.3% | 0.339 | $-10.39 | -87.5% | 0.395 | day_forecast_capped:3,day_marginal_runway:1,day_open_runway:3 | Amsterdam,Dallas,Helsinki,Miami,Taipei,Tokyo |
| 2026-06-06 | 9 | 9 | 4 | +44.4% | 0.347 | $-3.05 | -19.5% | 0.334 | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:4 | NYC,SanFrancisco,SaoPaulo,TelAviv,Tokyo |
| 2026-06-07 | 8 | 8 | 3 | +37.5% | 0.467 | $-5.24 | -28.0% | 0.233 | day_forecast_capped:1,day_marginal_runway:4,day_open_runway:3 | BuenosAires,Istanbul,NYC,Singapore,Warsaw |
| 2026-06-08 | 5 | 5 | 3 | +60.0% | 0.476 | $+6.74 | +56.6% | 0.214 | day_forecast_capped:1,day_marginal_runway:3,day_open_runway:1 | Ankara,TelAviv |
| 2026-06-09 | 7 | 7 | 3 | +42.9% | 0.339 | $-2.99 | -25.2% | 0.351 | day_forecast_capped:3,day_marginal_runway:4 | CapeTown,Chengdu,Istanbul,Singapore |
| 2026-06-10 | 8 | 8 | 5 | +62.5% | 0.301 | $-3.66 | -30.4% | 0.349 | day_forecast_capped:2,day_marginal_runway:3,day_open_runway:3 | Beijing,Busan,Miami |
| 2026-06-11 | 9 | 9 | 4 | +44.4% | 0.315 | $-3.10 | -21.9% | 0.283 | day_forecast_capped:1,day_marginal_runway:5,day_open_runway:3 | Istanbul,Manila,SanFrancisco,TelAviv,Wuhan |
| 2026-06-12 | 12 | 12 | 5 | +41.7% | 0.389 | $-9.72 | -41.7% | 0.309 | day_forecast_capped:2,day_marginal_runway:5,day_open_runway:5 | Atlanta,Busan,Houston,LA,Manila,SanFrancisco,TelAviv |
| 2026-06-13 | 10 | 10 | 5 | +50.0% | 0.319 | $+3.69 | +23.1% | 0.378 | day_forecast_capped:4,day_marginal_runway:2,day_open_runway:4 | Denver,Helsinki,Karachi,Seattle,Wuhan |
| 2026-06-14 | 11 | 11 | 8 | +72.7% | 0.383 | $+13.86 | +65.7% | 0.286 | day_forecast_capped:2,day_marginal_runway:3,day_open_runway:6 | Dallas,Lucknow,Shanghai |
| 2026-06-15 | 8 | 8 | 4 | +50.0% | 0.359 | $+2.90 | +20.2% | 0.250 | day_marginal_runway:3,day_open_runway:5 | Houston,Lucknow,NYC,TelAviv |
| 2026-06-16 | 8 | 8 | 4 | +50.0% | 0.245 | $-0.57 | -5.8% | 0.499 | day_forecast_capped:4,day_marginal_runway:1,day_open_runway:3 | Atlanta,Guangzhou,Singapore,TelAviv |
| 2026-06-17 | 8 | 8 | 3 | +37.5% | 0.184 | $-4.18 | -56.7% | 0.470 | day_forecast_capped:4,day_open_runway:4 | Istanbul,Karachi,Miami,Tokyo,Wuhan |
| 2026-06-18 | 5 | 5 | 3 | +60.0% | 0.232 | $-1.73 | -29.9% | 0.470 | day_forecast_capped:3,day_marginal_runway:1,day_open_runway:1 | CapeTown,Manila |
| 2026-06-19 | 9 | 9 | 6 | +66.7% | 0.324 | $+4.78 | +32.8% | 0.386 | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:4 | BuenosAires,Busan,Taipei |
| 2026-06-20 | 8 | 8 | 4 | +50.0% | 0.306 | $-2.02 | -16.5% | 0.344 | day_forecast_capped:3,day_marginal_runway:4,day_open_runway:1 | Jeddah,Karachi,Miami,Tokyo |
| 2026-06-21 | 9 | 9 | 3 | +33.3% | 0.282 | $-4.33 | -34.2% | 0.373 | day_forecast_capped:4,day_marginal_runway:3,day_open_runway:2 | Beijing,CapeTown,Denver,Jeddah,Singapore,TelAviv |
| 2026-06-22 | 3 | 3 | 0 | +0.0% | 0.196 | $-2.95 | -100.0% | 0.448 | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:1 | NYC,Shanghai,Tokyo |
| 2026-06-23 | 6 | 6 | 3 | +50.0% | 0.329 | $+6.67 | +67.6% | 0.362 | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:4 | Chongqing,Jeddah,Karachi |
| 2026-06-25 | 5 | 5 | 4 | +80.0% | 0.233 | $+0.66 | +11.3% | 0.431 | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:3 | Karachi |
| 2026-06-26 | 5 | 5 | 3 | +60.0% | 0.347 | $+0.95 | +11.0% | 0.289 | day_marginal_runway:3,day_open_runway:2 | Chongqing,TelAviv |
| 2026-06-30 | 4 | 4 | 3 | +75.0% | 0.231 | $+1.87 | +40.6% | 0.388 | day_open_runway:4 | Singapore |

## Balanced Candidate Worst PnL Days

| target_date | trades | wins | win_rate | avg_weight | weighted_profit_usd | weighted_roi | regime_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-25 | 8 | 0 | +0.0% | 0.268 | $-10.74 | -100.0% | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:3 | Busan,CapeTown,Chengdu,Dallas,Jeddah,Shanghai,Tokyo,Wuhan |
| 2026-06-05 | 7 | 1 | +14.3% | 0.339 | $-10.39 | -87.5% | day_forecast_capped:3,day_marginal_runway:1,day_open_runway:3 | Amsterdam,Dallas,Helsinki,Miami,Taipei,Tokyo |
| 2026-05-27 | 7 | 1 | +14.3% | 0.390 | $-10.19 | -74.6% | day_marginal_runway:4,day_open_runway:3 | Beijing,Denver,Helsinki,Houston,Manila,SaoPaulo |
| 2026-06-12 | 12 | 5 | +41.7% | 0.389 | $-9.72 | -41.7% | day_forecast_capped:2,day_marginal_runway:5,day_open_runway:5 | Atlanta,Busan,Houston,LA,Manila,SanFrancisco,TelAviv |
| 2026-05-31 | 7 | 3 | +42.9% | 0.383 | $-9.66 | -72.0% | day_forecast_capped:4,day_marginal_runway:2,day_open_runway:1 | Karachi,Tokyo,Warsaw,Wuhan |
| 2026-05-21 | 6 | 1 | +16.7% | 0.311 | $-7.61 | -81.6% | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:1 | Manila,Miami,SanFrancisco,SaoPaulo,TelAviv |
| 2026-06-07 | 8 | 3 | +37.5% | 0.467 | $-5.24 | -28.0% | day_forecast_capped:1,day_marginal_runway:4,day_open_runway:3 | BuenosAires,Istanbul,NYC,Singapore,Warsaw |
| 2026-05-23 | 6 | 2 | +33.3% | 0.299 | $-4.54 | -50.6% | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:4 | Houston,Istanbul,Lucknow,Warsaw |
| 2026-05-29 | 6 | 2 | +33.3% | 0.256 | $-4.46 | -58.1% | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:1 | Amsterdam,BuenosAires,LA,Warsaw |
| 2026-06-21 | 9 | 3 | +33.3% | 0.282 | $-4.33 | -34.2% | day_forecast_capped:4,day_marginal_runway:3,day_open_runway:2 | Beijing,CapeTown,Denver,Jeddah,Singapore,TelAviv |
| 2026-06-17 | 8 | 3 | +37.5% | 0.184 | $-4.18 | -56.7% | day_forecast_capped:4,day_open_runway:4 | Istanbul,Karachi,Miami,Tokyo,Wuhan |
| 2026-06-10 | 8 | 5 | +62.5% | 0.301 | $-3.66 | -30.4% | day_forecast_capped:2,day_marginal_runway:3,day_open_runway:3 | Beijing,Busan,Miami |

## City Distribution

| city | trades | active_dates | wins | win_rate | avg_ask | profit_usd | roi | route_mix | loss_dates |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TelAviv | 7 | 7 | 1 | +14.3% | 0.374 | $-25.00 | -71.4% | capped_d1_no:2,runway_current_no:5 | 2026-06-02,2026-06-08,2026-06-11,2026-06-12,2026-06-14,2026-06-16 |
| CapeTown | 4 | 4 | 0 | +0.0% | 0.355 | $-20.00 | -100.0% | capped_d1_no:3,runway_current_no:1 | 2026-06-01,2026-06-17,2026-06-25,2026-06-30 |
| Warsaw | 6 | 6 | 1 | +16.7% | 0.407 | $-19.58 | -65.3% | capped_d1_no:4,runway_current_no:2 | 2026-05-25,2026-05-27,2026-05-31,2026-06-07,2026-06-26 |
| Helsinki | 8 | 8 | 2 | +25.0% | 0.435 | $-16.64 | -41.6% | capped_d1_no:3,runway_current_no:5 | 2026-05-22,2026-05-27,2026-06-05,2026-06-13,2026-06-17,2026-06-30 |
| Karachi | 5 | 5 | 1 | +20.0% | 0.378 | $-13.89 | -55.6% | capped_d1_no:3,runway_current_no:2 | 2026-06-14,2026-06-15,2026-06-18,2026-06-25 |
| Tokyo | 9 | 9 | 3 | +33.3% | 0.378 | $-12.82 | -28.5% | capped_d1_no:3,runway_current_no:6 | 2026-05-29,2026-05-31,2026-06-05,2026-06-06,2026-06-10,2026-06-20 |
| Manila | 5 | 5 | 1 | +20.0% | 0.410 | $-12.50 | -50.0% | capped_d1_no:1,runway_current_no:4 | 2026-05-28,2026-05-29,2026-06-11,2026-06-12 |
| SaoPaulo | 5 | 5 | 1 | +20.0% | 0.372 | $-11.49 | -45.9% | capped_d1_no:1,runway_current_no:4 | 2026-05-21,2026-05-27,2026-06-06,2026-06-11 |
| Miami | 10 | 10 | 3 | +30.0% | 0.390 | $-10.85 | -21.7% | capped_d1_no:10 | 2026-05-22,2026-05-23,2026-05-27,2026-06-08,2026-06-11,2026-06-13,2026-06-19 |
| Amsterdam | 2 | 2 | 0 | +0.0% | 0.450 | $-10.00 | -100.0% | capped_d1_no:2 | 2026-06-01,2026-06-13 |
| LA | 13 | 13 | 4 | +30.8% | 0.383 | $-9.39 | -14.4% | capped_d1_no:11,runway_current_no:2 | 2026-05-22,2026-05-25,2026-05-29,2026-06-02,2026-06-04,2026-06-06,2026-06-07,2026-06-13,2026-06-15 |
| Wuhan | 11 | 11 | 4 | +36.4% | 0.388 | $-9.04 | -16.4% | capped_d1_no:1,runway_current_no:10 | 2026-05-25,2026-05-26,2026-05-28,2026-05-31,2026-06-03,2026-06-11,2026-06-13 |
| Houston | 8 | 8 | 3 | +37.5% | 0.414 | $-8.23 | -20.6% | capped_d1_no:2,runway_current_no:6 | 2026-05-23,2026-05-27,2026-06-12,2026-06-15,2026-06-17 |
| Singapore | 10 | 10 | 4 | +40.0% | 0.459 | $-6.13 | -12.3% | capped_d1_no:9,runway_current_no:1 | 2026-05-22,2026-05-27,2026-05-30,2026-06-10,2026-06-13,2026-06-14 |
| Wellington | 8 | 8 | 3 | +37.5% | 0.404 | $-5.07 | -12.7% | capped_d1_no:8 | 2026-05-25,2026-06-07,2026-06-12,2026-06-16,2026-06-23 |
| Dallas | 1 | 1 | 0 | +0.0% | 0.476 | $-5.00 | -100.0% | runway_current_no:1 | 2026-06-14 |
| Istanbul | 5 | 5 | 2 | +40.0% | 0.408 | $-3.43 | -13.7% | capped_d1_no:1,runway_current_no:4 | 2026-06-07,2026-06-09,2026-06-11 |
| Busan | 6 | 6 | 2 | +33.3% | 0.338 | $+0.86 | +2.9% | capped_d1_no:1,runway_current_no:5 | 2026-06-10,2026-06-12,2026-06-19,2026-06-30 |
| Munich | 2 | 2 | 1 | +50.0% | 0.395 | $+2.82 | +28.2% | capped_d1_no:2 | 2026-05-22 |
| Shanghai | 7 | 7 | 3 | +42.9% | 0.388 | $+4.49 | +12.8% | capped_d1_no:3,runway_current_no:4 | 2026-05-25,2026-06-01,2026-06-14,2026-06-22 |
| BuenosAires | 3 | 3 | 2 | +66.7% | 0.492 | $+5.20 | +34.7% | capped_d1_no:3 | 2026-05-31 |
| Ankara | 6 | 6 | 2 | +33.3% | 0.347 | $+6.00 | +20.0% | capped_d1_no:4,runway_current_no:2 | 2026-05-20,2026-05-28,2026-06-04,2026-06-11 |
| Taipei | 4 | 4 | 2 | +50.0% | 0.328 | $+7.30 | +36.5% | capped_d1_no:2,runway_current_no:2 | 2026-05-20,2026-06-05 |
| Austin | 3 | 3 | 2 | +66.7% | 0.423 | $+9.88 | +65.8% | capped_d1_no:2,runway_current_no:1 | 2026-06-11 |
| Lucknow | 4 | 4 | 1 | +25.0% | 0.258 | $+13.33 | +66.7% | runway_current_no:4 | 2026-05-23,2026-06-14,2026-06-15 |
| Denver | 3 | 3 | 2 | +66.7% | 0.403 | $+13.50 | +90.0% | capped_d1_no:1,runway_current_no:2 | 2026-05-27 |
| Guangzhou | 2 | 2 | 2 | +100.0% | 0.410 | $+14.45 | +144.5% | capped_d1_no:2 |  |
| Seattle | 3 | 3 | 2 | +66.7% | 0.337 | $+16.36 | +109.1% | capped_d1_no:2,runway_current_no:1 | 2026-06-14 |
| Atlanta | 6 | 6 | 3 | +50.0% | 0.381 | $+16.41 | +54.7% | capped_d1_no:1,runway_current_no:5 | 2026-06-03,2026-06-12,2026-06-16 |
| NYC | 9 | 9 | 4 | +44.4% | 0.358 | $+23.71 | +52.7% | capped_d1_no:2,runway_current_no:7 | 2026-06-01,2026-06-03,2026-06-06,2026-06-07,2026-06-10 |
| Beijing | 6 | 6 | 4 | +66.7% | 0.385 | $+24.43 | +81.4% | runway_current_no:6 | 2026-05-20,2026-06-10 |
| Chongqing | 5 | 5 | 1 | +20.0% | 0.317 | $+25.00 | +100.0% | capped_d1_no:2,runway_current_no:3 | 2026-05-28,2026-06-14,2026-06-17,2026-06-26 |
| SanFrancisco | 2 | 2 | 2 | +100.0% | 0.240 | $+34.44 | +344.4% | capped_d1_no:1,runway_current_no:1 |  |
| Jeddah | 7 | 7 | 5 | +71.4% | 0.355 | $+39.80 | +113.7% | capped_d1_no:2,runway_current_no:5 | 2026-06-20,2026-06-21 |

## Recent Model Predictions

| target_date | settlement_known | predicted_trades | cities | avg_ask | known_wins | known_profit_usd |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-06-21 | True | 2 | 2 | 0.445 | 1.000 | $+1.11 |
| 2026-06-22 | True | 1 | 1 | 0.430 | 0.000 | $-5.00 |
| 2026-06-23 | True | 2 | 2 | 0.355 | 1.000 | $+13.81 |
| 2026-06-25 | True | 3 | 3 | 0.450 | 1.000 | $-4.36 |
| 2026-06-26 | True | 2 | 2 | 0.340 | 0.000 | $-10.00 |
| 2026-06-27 | False | 2 | 2 | 0.340 | 0.000 | $+0.00 |
| 2026-06-30 | True | 4 | 4 | 0.282 | 1.000 | $+30.00 |
| 2026-07-01 | False | 1 | 1 | 0.390 | 0.000 | $+0.00 |
| 2026-07-03 | False | 1 | 1 | 0.460 | 0.000 | $+0.00 |

| target_date | city | decision_hour_local | model_action | day_regime | expression | ask | settlement_known | resolved_payoff | resolved_profit_usd | gamma_check_status | current_bracket | d1_no_bracket | forecast_max_native | running_native | intraday_state | city_family |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-21 | Jeddah | 13 | buy current-bracket NO | day_open_runway | current_bracket_no | 0.440 | True | 0.000 | $-5.00 |  | 35 | 36 | 38.100 | 35.000 | pullback_uncertain | continental_dry_hot |
| 2026-06-21 | Karachi | 12 | buy current-bracket NO | day_marginal_runway | current_bracket_no | 0.450 | True | 1.000 | $+6.11 |  | 34 | 35 | 34.900 | 33.889 | active_warming | continental_dry_hot |
| 2026-06-22 | Shanghai | 10 | buy current-bracket NO | day_marginal_runway | current_bracket_no | 0.430 | True | 0.000 | $-5.00 |  | 24 | 25 | 24.600 | 23.889 | mature_fade | humid_low_latitude |
| 2026-06-23 | NYC | 10 | buy current-bracket NO | day_open_runway | current_bracket_no | 0.210 | True | 1.000 | $+18.81 |  | 70-71 | 72-73 | 75.300 | 71.000 | mature_fade | southern_or_maritime |
| 2026-06-23 | Wellington | 11 | buy d1 NO | day_forecast_capped | d1_no | 0.500 | True | 0.000 | $-5.00 |  | 13 | 14 | 12.900 | 12.778 | active_warming | southern_or_maritime |
| 2026-06-25 | CapeTown | 13 | buy d1 NO | day_forecast_capped | d1_no | 0.380 | True | 0.000 | $-5.00 |  | 21 | 22 | 21.300 | 21.111 | active_warming | southern_or_maritime |
| 2026-06-25 | Karachi | 13 | buy current-bracket NO | day_open_runway | current_bracket_no | 0.500 | True | 0.000 | $-5.00 |  | 33 | 34 | 35.000 | 32.778 | fresh_high | continental_dry_hot |
| 2026-06-25 | Wuhan | 14 | buy current-bracket NO | day_marginal_runway | current_bracket_no | 0.470 | True | 1.000 | $+5.64 |  | 29 | 30 | 29.600 | 28.889 | active_warming | humid_low_latitude |
| 2026-06-26 | Chongqing | 12 | buy current-bracket NO | day_open_runway | current_bracket_no | 0.290 | True | 0.000 | $-5.00 |  | 23 | 24 | 28.300 | 22.778 | fresh_high | humid_low_latitude |
| 2026-06-26 | Warsaw | 14 | buy d1 NO | day_forecast_capped | d1_no | 0.390 | True | 0.000 | $-5.00 |  | 31 | 32 | 31.500 | 31.111 | active_warming | europe_cloud_break |
| 2026-06-27 | Shanghai | 10 | buy d1 NO | day_forecast_capped | d1_no | 0.390 | False | NA | NA |  | 26 | 27 | 25.900 | 26.111 | fresh_high | humid_low_latitude |
| 2026-06-27 | TelAviv | 13 | buy current-bracket NO | day_marginal_runway | current_bracket_no | 0.290 | False | NA | NA |  | 30 | 31 | 31.200 | 30.000 | fresh_high | southern_or_maritime |
| 2026-06-30 | Busan | 10 | buy d1 NO | day_forecast_capped | d1_no | 0.350 | True | 0.000 | $-5.00 |  | 27 | 28 | 27.600 | 27.222 | active_warming | humid_low_latitude |
| 2026-06-30 | CapeTown | 13 | buy d1 NO | day_forecast_capped | d1_no | 0.210 | True | 0.000 | $-5.00 |  | 15 | 16 | 14.600 | 15.000 | active_warming | southern_or_maritime |
| 2026-06-30 | Chongqing | 14 | buy current-bracket NO | day_marginal_runway | current_bracket_no | 0.100 | True | 1.000 | $+45.00 |  | 26 | 27 | 27.300 | 26.111 | active_warming | humid_low_latitude |
| 2026-06-30 | Helsinki | 14 | buy d1 NO | day_forecast_capped | d1_no | 0.470 | True | 0.000 | $-5.00 |  | 25 | 26 | 25.300 | 25.000 | plateau_near_high | europe_cloud_break |
| 2026-07-01 | TelAviv | 13 | buy current-bracket NO | day_marginal_runway | current_bracket_no | 0.390 | False | NA | NA |  | 31 | 32 | 31.900 | 31.111 | fresh_high | southern_or_maritime |
| 2026-07-03 | SaoPaulo | 12 | buy d1 NO | day_forecast_capped | d1_no | 0.460 | False | NA | NA |  | 25 | 26 | 24.700 | 25.000 | fresh_high | southern_or_maritime |

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
4. `first_eligible` 是 live-like 主口径；`best_ask` 虽不用 payoff，但仍是日内后视上界，只能辅助判断 fixed near-noon 是否错过入场，不可作为 live 证据。
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
