# Regime-Routed NO Expression V1

## 结论

把 regime 当成表达式路由器这个方向可以测，但不能只看 strict 口径。这里并排比较 A strict、B relaxed ask cap、C first-eligible live-like timing、D best-ask optimistic timing；主结论只看 first-eligible，best-ask 仅作上界对照。本报告把已结算 ROI 和最近模型候选分开，避免未结算日期被静默过滤。

Verdict: `balanced_shadow_candidate_but_not_live_ready`，live_ready=`False`。

raw 候选里 `routed_capped_d1_no_relaxed50_best_ask` 的 ROI 最高但日内 tail 偏薄；当前更平衡的 shadow 候选是 `routed_capped_d2_no_relaxed70_first_eligible + soft_balanced`：保留 285 笔 / 38 天 / 35 城，胜率 +53.3%，weighted ROI +22.3%，date-block CI [+1.7%, +42.1%]；仍是 shadow 候选，不是 live 规则。

## Variant Summary

| variant | selected_trades | active_dates | cities | win_rate | avg_ask | profit_usd | roi | roi_ci_low | roi_ci_high | baseline_roi | excess_roi_vs_baseline | roi_le_minus50_days | roi_eq_minus100_days | open_runway_trades | marginal_runway_trades | forecast_capped_trades |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_current_no_strict35_near_noon | 110 | 35 | 32 | +24.5% | 0.251 | $+33.19 | +6.0% | -32.6% | +45.3% | NA | NA | 16 | 16 | 19 | 37 | 54 |
| routed_capped_d1_no_strict35_near_noon | 74 | 32 | 30 | +35.1% | 0.257 | $+190.52 | +51.5% | +4.3% | +94.6% | +6.0% | +45.5% | 15 | 15 | 19 | 37 | 18 |
| routed_capped_d2_no_strict35_near_noon | 58 | 29 | 26 | +39.7% | 0.258 | $+207.96 | +71.7% | +12.1% | +132.6% | +6.0% | +65.7% | 13 | 13 | 19 | 37 | 2 |
| routed_capped_d1_no_strict35_first_eligible | 74 | 32 | 30 | +35.1% | 0.259 | $+180.57 | +48.8% | +2.4% | +91.7% | +6.0% | +42.8% | 15 | 15 | 19 | 37 | 18 |
| routed_capped_d2_no_strict35_first_eligible | 58 | 29 | 26 | +39.7% | 0.259 | $+203.09 | +70.0% | +11.4% | +130.3% | +6.0% | +64.0% | 13 | 13 | 19 | 37 | 2 |
| routed_capped_d1_no_strict35_best_ask | 74 | 32 | 30 | +35.1% | 0.250 | $+221.69 | +59.9% | +7.5% | +111.8% | +6.0% | +53.9% | 15 | 15 | 19 | 37 | 18 |
| routed_capped_d2_no_strict35_best_ask | 58 | 29 | 26 | +39.7% | 0.253 | $+239.13 | +82.5% | +15.6% | +153.0% | +6.0% | +76.4% | 13 | 13 | 19 | 37 | 2 |
| routed_capped_d1_no_relaxed50_near_noon | 180 | 38 | 35 | +38.9% | 0.372 | $+149.06 | +16.6% | -9.2% | +41.0% | +6.0% | +10.5% | 10 | 7 | 36 | 75 | 69 |
| routed_capped_d2_no_relaxed50_near_noon | 119 | 36 | 32 | +42.0% | 0.357 | $+206.00 | +34.6% | -1.1% | +71.4% | +6.0% | +28.6% | 10 | 10 | 36 | 75 | 8 |
| routed_capped_d1_no_relaxed50_first_eligible | 180 | 38 | 35 | +38.9% | 0.375 | $+136.83 | +15.2% | -9.7% | +39.1% | +6.0% | +9.2% | 10 | 7 | 36 | 75 | 69 |
| routed_capped_d2_no_relaxed50_first_eligible | 119 | 36 | 32 | +42.0% | 0.360 | $+198.84 | +33.4% | -1.7% | +69.5% | +6.0% | +27.4% | 10 | 10 | 36 | 75 | 8 |
| routed_capped_d1_no_relaxed50_best_ask | 180 | 38 | 35 | +38.9% | 0.363 | $+195.13 | +21.7% | -5.9% | +49.2% | +6.0% | +15.6% | 10 | 7 | 36 | 75 | 69 |
| routed_capped_d2_no_relaxed50_best_ask | 119 | 36 | 32 | +42.0% | 0.350 | $+245.71 | +41.3% | +1.9% | +83.3% | +6.0% | +35.3% | 10 | 10 | 36 | 75 | 8 |
| routed_capped_d1_no_relaxed70_near_noon | 412 | 38 | 35 | +52.2% | 0.528 | $+53.08 | +2.6% | -9.2% | +14.1% | +6.0% | -3.5% | 3 | 1 | 76 | 124 | 212 |
| routed_capped_d2_no_relaxed70_near_noon | 285 | 38 | 35 | +53.3% | 0.536 | $+89.39 | +6.3% | -7.8% | +20.5% | +6.0% | +0.2% | 3 | 1 | 76 | 126 | 83 |
| routed_capped_d1_no_relaxed70_first_eligible | 412 | 38 | 35 | +51.5% | 0.535 | $+3.88 | +0.2% | -11.4% | +11.4% | +6.0% | -5.8% | 4 | 1 | 77 | 125 | 210 |
| routed_capped_d2_no_relaxed70_first_eligible | 285 | 38 | 35 | +53.3% | 0.546 | $+69.12 | +4.9% | -8.5% | +18.1% | +6.0% | -1.2% | 3 | 1 | 77 | 125 | 83 |
| routed_capped_d1_no_relaxed70_best_ask | 412 | 38 | 35 | +50.7% | 0.504 | $+171.79 | +8.3% | -5.9% | +23.0% | +6.0% | +2.3% | 4 | 1 | 74 | 125 | 213 |
| routed_capped_d2_no_relaxed70_best_ask | 285 | 38 | 35 | +52.6% | 0.512 | $+210.19 | +14.8% | -2.1% | +33.6% | +6.0% | +8.7% | 4 | 1 | 74 | 128 | 83 |

## Main Candidate Daily

| target_date | trades | cities | wins | win_rate | profit_usd | roi | regime_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-20 | 6 | 6 | 4 | +66.7% | $+41.19 | +137.3% | day_forecast_capped:3,day_marginal_runway:1,day_open_runway:2 | Beijing,Taipei |
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
| 2026-06-02 | 8 | 8 | 3 | +37.5% | $-8.52 | -21.3% | day_forecast_capped:5,day_marginal_runway:3 | Amsterdam,Dallas,Houston,Shanghai,TelAviv |
| 2026-06-03 | 3 | 3 | 1 | +33.3% | $+1.67 | +11.1% | day_forecast_capped:1,day_marginal_runway:2 | Manila,Wuhan |
| 2026-06-04 | 3 | 3 | 2 | +66.7% | $+5.20 | +34.7% | day_forecast_capped:2,day_open_runway:1 | Ankara |
| 2026-06-05 | 3 | 3 | 0 | +0.0% | $-15.00 | -100.0% | day_open_runway:3 | Helsinki,Taipei,Tokyo |
| 2026-06-06 | 2 | 2 | 2 | +100.0% | $+22.56 | +225.6% | day_marginal_runway:1,day_open_runway:1 |  |
| 2026-06-07 | 5 | 5 | 0 | +0.0% | $-25.00 | -100.0% | day_forecast_capped:2,day_marginal_runway:1,day_open_runway:2 | Istanbul,Miami,NYC,SanFrancisco,Wellington |
| 2026-06-08 | 6 | 6 | 3 | +50.0% | $+10.31 | +34.4% | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:1 | Ankara,TelAviv,Tokyo |
| 2026-06-09 | 3 | 3 | 2 | +66.7% | $+25.94 | +172.9% | day_marginal_runway:2,day_open_runway:1 | Istanbul |
| 2026-06-10 | 6 | 6 | 1 | +16.7% | $-16.49 | -55.0% | day_forecast_capped:3,day_marginal_runway:1,day_open_runway:2 | Beijing,Busan,Karachi,Tokyo,Warsaw |
| 2026-06-11 | 8 | 8 | 4 | +50.0% | $+29.44 | +73.6% | day_forecast_capped:1,day_marginal_runway:6,day_open_runway:1 | Istanbul,Manila,TelAviv,Wuhan |
| 2026-06-12 | 11 | 11 | 4 | +36.4% | $+24.38 | +44.3% | day_forecast_capped:4,day_marginal_runway:5,day_open_runway:2 | Atlanta,Busan,Houston,Manila,TelAviv,Tokyo,Wellington |
| 2026-06-13 | 4 | 4 | 1 | +25.0% | $-7.50 | -37.5% | day_forecast_capped:2,day_marginal_runway:2 | Helsinki,Singapore,Wellington |
| 2026-06-14 | 10 | 10 | 6 | +60.0% | $+47.09 | +94.2% | day_forecast_capped:3,day_marginal_runway:5,day_open_runway:2 | Karachi,Lucknow,Shanghai,Singapore |
| 2026-06-15 | 7 | 7 | 5 | +71.4% | $+29.83 | +85.2% | day_forecast_capped:2,day_marginal_runway:4,day_open_runway:1 | LA,Lucknow |
| 2026-06-16 | 2 | 2 | 1 | +50.0% | $+2.82 | +28.2% | day_open_runway:2 | TelAviv |
| 2026-06-17 | 6 | 6 | 1 | +16.7% | $-18.10 | -60.3% | day_forecast_capped:4,day_marginal_runway:1,day_open_runway:1 | Beijing,Chengdu,Chongqing,Helsinki,Houston |
| 2026-06-18 | 1 | 1 | 0 | +0.0% | $-5.00 | -100.0% | day_forecast_capped:1 | Karachi |
| 2026-06-19 | 9 | 9 | 4 | +44.4% | $+1.89 | +4.2% | day_forecast_capped:4,day_marginal_runway:5 | Busan,Houston,Miami,TelAviv,Tokyo |
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
| 2026-06-07 | 5 | 0 | +0.0% | $-25.00 | -100.0% | day_forecast_capped:2,day_marginal_runway:1,day_open_runway:2 | Istanbul,Miami,NYC,SanFrancisco,Wellington |
| 2026-06-18 | 1 | 0 | +0.0% | $-5.00 | -100.0% | day_forecast_capped:1 | Karachi |
| 2026-06-22 | 1 | 0 | +0.0% | $-5.00 | -100.0% | day_marginal_runway:1 | Shanghai |
| 2026-06-26 | 2 | 0 | +0.0% | $-10.00 | -100.0% | day_forecast_capped:1,day_open_runway:1 | Chongqing,Warsaw |
| 2026-05-27 | 4 | 0 | +0.0% | $-20.00 | -100.0% | day_forecast_capped:3,day_marginal_runway:1 | Miami,SaoPaulo,Singapore,Warsaw |
| 2026-06-05 | 3 | 0 | +0.0% | $-15.00 | -100.0% | day_open_runway:3 | Helsinki,Taipei,Tokyo |
| 2026-05-24 | 1 | 0 | +0.0% | $-5.00 | -100.0% | day_forecast_capped:1 | Chengdu |
| 2026-05-31 | 7 | 1 | +14.3% | $-23.37 | -66.8% | day_forecast_capped:2,day_marginal_runway:4,day_open_runway:1 | BuenosAires,Busan,Miami,TelAviv,Tokyo,Wuhan |
| 2026-06-17 | 6 | 1 | +16.7% | $-18.10 | -60.3% | day_forecast_capped:4,day_marginal_runway:1,day_open_runway:1 | Beijing,Chengdu,Chongqing,Helsinki,Houston |
| 2026-06-10 | 6 | 1 | +16.7% | $-16.49 | -55.0% | day_forecast_capped:3,day_marginal_runway:1,day_open_runway:2 | Beijing,Busan,Karachi,Tokyo,Warsaw |
| 2026-05-22 | 6 | 1 | +16.7% | $-12.14 | -40.5% | day_forecast_capped:3,day_marginal_runway:3 | Helsinki,Miami,Munich,SaoPaulo,Shanghai |
| 2026-06-13 | 4 | 1 | +25.0% | $-7.50 | -37.5% | day_forecast_capped:2,day_marginal_runway:2 | Helsinki,Singapore,Wellington |

## Route-Leg Breakdown

| route_leg | day_regime | expression | trades | active_dates | cities | win_rate | avg_ask | profit_usd | roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| capped_d1_no | day_forecast_capped | d1_no | 69 | 30 | 28 | +36.2% | 0.405 | $-42.26 | -12.3% |
| runway_current_no | day_marginal_runway | current_bracket_no | 75 | 30 | 29 | +46.7% | 0.356 | $+185.47 | +49.5% |
| runway_current_no | day_open_runway | current_bracket_no | 36 | 25 | 20 | +27.8% | 0.357 | $-6.37 | -3.5% |

## Soft Weight Overlay

soft weight 只改 notional，不筛单。固定机制权重：`route_multiplier × price_multiplier × weather_multiplier × day_multiplier`；`day_multiplier` 已改为 row-local/live-like，不使用当天完整截面、payoff、final max 或 settlement 训练。

| variant | weight_policy | trades | active_dates | cities | win_rate | avg_ask | notional_retained | weighted_profit_usd | weighted_roi | weighted_roi_ci_low | weighted_roi_ci_high | roi_le_minus50_days | roi_eq_minus100_days | loss_ge_10usd_days | worst_day_profit_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| routed_capped_d1_no_relaxed50_first_eligible | full_size | 180 | 38 | 35 | +38.9% | 0.375 | +100.0% | $+136.83 | +15.2% | -9.7% | +39.1% | 10 | 7 | 9 | $-25.00 |
| routed_capped_d1_no_relaxed50_first_eligible | soft_moderate | 180 | 38 | 35 | +38.9% | 0.375 | +67.7% | $+144.71 | +23.7% | -3.9% | +50.7% | 10 | 7 | 7 | $-18.23 |
| routed_capped_d1_no_relaxed50_first_eligible | soft_balanced | 180 | 38 | 35 | +38.9% | 0.375 | +42.7% | $+134.22 | +34.9% | +3.6% | +65.0% | 12 | 7 | 3 | $-11.57 |
| routed_capped_d2_no_relaxed50_first_eligible | full_size | 119 | 36 | 32 | +42.0% | 0.360 | +100.0% | $+198.84 | +33.4% | -1.7% | +69.5% | 10 | 10 | 7 | $-25.00 |
| routed_capped_d2_no_relaxed50_first_eligible | soft_moderate | 119 | 36 | 32 | +42.0% | 0.360 | +74.7% | $+178.65 | +40.2% | +3.7% | +77.8% | 11 | 10 | 4 | $-19.20 |
| routed_capped_d2_no_relaxed50_first_eligible | soft_balanced | 119 | 36 | 32 | +42.0% | 0.360 | +49.9% | $+152.10 | +51.3% | +13.0% | +90.8% | 11 | 10 | 3 | $-12.27 |
| routed_capped_d1_no_relaxed70_first_eligible | full_size | 412 | 38 | 35 | +51.5% | 0.535 | +100.0% | $+3.88 | +0.2% | -11.4% | +11.4% | 4 | 1 | 14 | $-35.90 |
| routed_capped_d1_no_relaxed70_first_eligible | soft_moderate | 412 | 38 | 35 | +51.5% | 0.535 | +61.0% | $+55.74 | +4.4% | -8.5% | +16.9% | 4 | 1 | 6 | $-21.14 |
| routed_capped_d1_no_relaxed70_first_eligible | soft_balanced | 412 | 38 | 35 | +51.5% | 0.535 | +31.0% | $+90.46 | +14.2% | -3.5% | +31.0% | 5 | 1 | 2 | $-11.76 |
| routed_capped_d2_no_relaxed70_first_eligible | full_size | 285 | 38 | 35 | +53.3% | 0.546 | +100.0% | $+69.12 | +4.9% | -8.5% | +18.1% | 3 | 1 | 9 | $-25.39 |
| routed_capped_d2_no_relaxed70_first_eligible | soft_moderate | 285 | 38 | 35 | +53.3% | 0.546 | +65.4% | $+91.19 | +9.8% | -5.2% | +24.6% | 4 | 1 | 5 | $-19.25 |
| routed_capped_d2_no_relaxed70_first_eligible | soft_balanced | 285 | 38 | 35 | +53.3% | 0.546 | +34.4% | $+109.55 | +22.3% | +1.7% | +42.1% | 6 | 1 | 1 | $-12.77 |
| routed_capped_d1_no_relaxed50_best_ask | full_size | 180 | 38 | 35 | +38.9% | 0.363 | +100.0% | $+195.13 | +21.7% | -5.9% | +49.2% | 10 | 7 | 9 | $-25.00 |
| routed_capped_d1_no_relaxed50_best_ask | soft_moderate | 180 | 38 | 35 | +38.9% | 0.363 | +67.6% | $+187.04 | +30.8% | +0.5% | +60.7% | 10 | 7 | 6 | $-17.60 |
| routed_capped_d1_no_relaxed50_best_ask | soft_balanced | 180 | 38 | 35 | +38.9% | 0.363 | +42.6% | $+162.06 | +42.3% | +10.0% | +73.8% | 11 | 7 | 1 | $-10.32 |
| routed_capped_d2_no_relaxed50_best_ask | full_size | 119 | 36 | 32 | +42.0% | 0.350 | +100.0% | $+245.71 | +41.3% | +1.9% | +83.3% | 10 | 10 | 7 | $-25.00 |
| routed_capped_d2_no_relaxed50_best_ask | soft_moderate | 119 | 36 | 32 | +42.0% | 0.350 | +74.4% | $+214.58 | +48.5% | +8.6% | +90.7% | 11 | 10 | 4 | $-18.57 |
| routed_capped_d2_no_relaxed50_best_ask | soft_balanced | 119 | 36 | 32 | +42.0% | 0.350 | +49.4% | $+176.20 | +59.9% | +19.3% | +102.7% | 11 | 10 | 3 | $-12.24 |
| routed_capped_d1_no_relaxed70_best_ask | full_size | 412 | 38 | 35 | +50.7% | 0.504 | +100.0% | $+171.79 | +8.3% | -5.9% | +23.0% | 4 | 1 | 12 | $-34.76 |
| routed_capped_d1_no_relaxed70_best_ask | soft_moderate | 412 | 38 | 35 | +50.7% | 0.504 | +61.3% | $+176.51 | +14.0% | -2.1% | +30.8% | 4 | 1 | 7 | $-20.12 |
| routed_capped_d1_no_relaxed70_best_ask | soft_balanced | 412 | 38 | 35 | +50.7% | 0.504 | +32.1% | $+157.37 | +23.8% | +3.7% | +43.5% | 7 | 1 | 2 | $-11.37 |
| routed_capped_d2_no_relaxed70_best_ask | full_size | 285 | 38 | 35 | +52.6% | 0.512 | +100.0% | $+210.19 | +14.8% | -2.1% | +33.6% | 4 | 1 | 9 | $-25.39 |
| routed_capped_d2_no_relaxed70_best_ask | soft_moderate | 285 | 38 | 35 | +52.6% | 0.512 | +65.8% | $+200.18 | +21.3% | +2.4% | +42.1% | 4 | 1 | 5 | $-18.44 |
| routed_capped_d2_no_relaxed70_best_ask | soft_balanced | 285 | 38 | 35 | +52.6% | 0.512 | +35.9% | $+170.98 | +33.5% | +9.9% | +57.8% | 7 | 1 | 2 | $-11.25 |

## Balanced Candidate Daily

| target_date | trades | cities | wins | win_rate | avg_weight | weighted_profit_usd | weighted_roi | day_risk | regime_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-20 | 12 | 12 | 6 | +50.0% | 0.260 | $+14.19 | +90.8% | 0.403 | day_forecast_capped:6,day_marginal_runway:2,day_open_runway:4 | Beijing,Jeddah,Miami,Munich,SanFrancisco,Singapore |
| 2026-05-21 | 8 | 8 | 3 | +37.5% | 0.330 | $-2.62 | -19.8% | 0.347 | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:3 | Manila,Munich,SanFrancisco,SaoPaulo,TelAviv |
| 2026-05-22 | 8 | 8 | 2 | +25.0% | 0.300 | $+1.62 | +13.5% | 0.347 | day_forecast_capped:4,day_marginal_runway:3,day_open_runway:1 | Chongqing,Helsinki,Istanbul,Manila,Munich,Shanghai |
| 2026-05-23 | 8 | 8 | 4 | +50.0% | 0.419 | $+1.68 | +10.0% | 0.219 | day_marginal_runway:4,day_open_runway:4 | Amsterdam,Istanbul,Lucknow,Warsaw |
| 2026-05-24 | 4 | 4 | 2 | +50.0% | 0.351 | $-3.40 | -48.4% | 0.320 | day_forecast_capped:1,day_marginal_runway:2,day_open_runway:1 | Lucknow,NYC |
| 2026-05-25 | 8 | 8 | 2 | +25.0% | 0.310 | $-6.78 | -54.7% | 0.268 | day_forecast_capped:1,day_marginal_runway:5,day_open_runway:2 | CapeTown,Jeddah,Shanghai,TelAviv,Tokyo,Wuhan |
| 2026-05-26 | 7 | 7 | 6 | +85.7% | 0.474 | $+12.87 | +77.6% | 0.233 | day_marginal_runway:6,day_open_runway:1 | Wuhan |
| 2026-05-27 | 5 | 5 | 2 | +40.0% | 0.339 | $-1.65 | -19.5% | 0.294 | day_marginal_runway:4,day_open_runway:1 | Beijing,Manila,SaoPaulo |
| 2026-05-28 | 15 | 15 | 9 | +60.0% | 0.413 | $+12.75 | +41.2% | 0.230 | day_forecast_capped:2,day_marginal_runway:10,day_open_runway:3 | Ankara,Chongqing,Manila,NYC,TelAviv,Wuhan |
| 2026-05-29 | 8 | 8 | 5 | +62.5% | 0.345 | $+4.60 | +33.3% | 0.274 | day_forecast_capped:2,day_marginal_runway:6 | LA,SaoPaulo,Warsaw |
| 2026-05-30 | 3 | 3 | 2 | +66.7% | 0.224 | $+1.77 | +52.7% | 0.543 | day_forecast_capped:3 | Shanghai |
| 2026-05-31 | 8 | 8 | 2 | +25.0% | 0.384 | $-12.77 | -83.2% | 0.327 | day_forecast_capped:3,day_marginal_runway:4,day_open_runway:1 | Busan,Karachi,Miami,TelAviv,Tokyo,Wuhan |
| 2026-06-01 | 5 | 5 | 2 | +40.0% | 0.368 | $-6.54 | -71.1% | 0.307 | day_forecast_capped:1,day_marginal_runway:3,day_open_runway:1 | Houston,NYC,TelAviv |
| 2026-06-02 | 9 | 9 | 4 | +44.4% | 0.375 | $-9.54 | -56.6% | 0.326 | day_forecast_capped:3,day_marginal_runway:3,day_open_runway:3 | Amsterdam,Beijing,Busan,Houston,TelAviv |
| 2026-06-03 | 5 | 5 | 4 | +80.0% | 0.334 | $+12.47 | +149.4% | 0.373 | day_forecast_capped:3,day_marginal_runway:2 | Wuhan |
| 2026-06-04 | 6 | 6 | 5 | +83.3% | 0.257 | $+2.86 | +37.1% | 0.389 | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:1 | Dallas |
| 2026-06-05 | 7 | 7 | 4 | +57.1% | 0.262 | $-2.23 | -24.3% | 0.442 | day_forecast_capped:3,day_marginal_runway:1,day_open_runway:3 | Helsinki,Taipei,Tokyo |
| 2026-06-06 | 4 | 4 | 4 | +100.0% | 0.478 | $+19.48 | +203.9% | 0.285 | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:2 |  |
| 2026-06-07 | 5 | 5 | 2 | +40.0% | 0.334 | $-4.26 | -50.9% | 0.312 | day_forecast_capped:2,day_marginal_runway:1,day_open_runway:2 | Istanbul,NYC,SanFrancisco |
| 2026-06-08 | 9 | 9 | 8 | +88.9% | 0.343 | $+10.98 | +71.2% | 0.391 | day_forecast_capped:5,day_marginal_runway:3,day_open_runway:1 | TelAviv |
| 2026-06-09 | 10 | 10 | 5 | +50.0% | 0.369 | $+14.71 | +79.8% | 0.324 | day_forecast_capped:4,day_marginal_runway:5,day_open_runway:1 | CapeTown,Chengdu,Istanbul,SanFrancisco,Singapore |
| 2026-06-10 | 7 | 7 | 5 | +71.4% | 0.315 | $-1.57 | -14.2% | 0.330 | day_forecast_capped:1,day_marginal_runway:2,day_open_runway:4 | Beijing,Busan |
| 2026-06-11 | 10 | 10 | 4 | +40.0% | 0.407 | $-1.12 | -5.5% | 0.256 | day_forecast_capped:1,day_marginal_runway:6,day_open_runway:3 | Ankara,Istanbul,Manila,SanFrancisco,TelAviv,Wuhan |
| 2026-06-12 | 14 | 14 | 5 | +35.7% | 0.455 | $+18.41 | +57.8% | 0.228 | day_forecast_capped:2,day_marginal_runway:9,day_open_runway:3 | Ankara,Atlanta,Busan,Houston,Karachi,LA,Manila,SanFrancisco,TelAviv |
| 2026-06-13 | 7 | 7 | 5 | +71.4% | 0.350 | $+3.45 | +28.2% | 0.330 | day_forecast_capped:2,day_marginal_runway:4,day_open_runway:1 | Helsinki,Karachi |
| 2026-06-14 | 10 | 10 | 8 | +80.0% | 0.372 | $+28.12 | +151.2% | 0.292 | day_forecast_capped:2,day_marginal_runway:5,day_open_runway:3 | Lucknow,Shanghai |
| 2026-06-15 | 12 | 12 | 7 | +58.3% | 0.357 | $+5.21 | +24.3% | 0.237 | day_forecast_capped:3,day_marginal_runway:7,day_open_runway:2 | Lucknow,Munich,NYC,SanFrancisco,TelAviv |
| 2026-06-16 | 5 | 5 | 2 | +40.0% | 0.250 | $-1.78 | -28.5% | 0.463 | day_forecast_capped:2,day_marginal_runway:1,day_open_runway:2 | Austin,Guangzhou,TelAviv |
| 2026-06-17 | 9 | 9 | 4 | +44.4% | 0.263 | $-5.69 | -48.0% | 0.383 | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:4 | Beijing,Chengdu,Istanbul,Karachi,Wuhan |
| 2026-06-18 | 5 | 5 | 3 | +60.0% | 0.303 | $-2.42 | -31.9% | 0.440 | day_forecast_capped:3,day_marginal_runway:2 | CapeTown,Manila |
| 2026-06-19 | 12 | 12 | 6 | +50.0% | 0.366 | $+7.45 | +33.9% | 0.309 | day_forecast_capped:4,day_marginal_runway:5,day_open_runway:3 | BuenosAires,Busan,Houston,Karachi,Taipei,TelAviv |
| 2026-06-20 | 8 | 8 | 4 | +50.0% | 0.345 | $-3.59 | -25.9% | 0.357 | day_forecast_capped:3,day_marginal_runway:4,day_open_runway:1 | Jeddah,Karachi,Manila,Tokyo |
| 2026-06-21 | 9 | 9 | 3 | +33.3% | 0.282 | $-4.33 | -34.2% | 0.373 | day_forecast_capped:4,day_marginal_runway:3,day_open_runway:2 | Beijing,CapeTown,Denver,Jeddah,Singapore,TelAviv |
| 2026-06-22 | 3 | 3 | 0 | +0.0% | 0.196 | $-2.95 | -100.0% | 0.448 | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:1 | NYC,Shanghai,Tokyo |
| 2026-06-23 | 6 | 6 | 3 | +50.0% | 0.329 | $+6.67 | +67.6% | 0.362 | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:4 | Chongqing,Jeddah,Karachi |
| 2026-06-25 | 5 | 5 | 4 | +80.0% | 0.233 | $+0.66 | +11.3% | 0.431 | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:3 | Karachi |
| 2026-06-26 | 5 | 5 | 3 | +60.0% | 0.347 | $+0.95 | +11.0% | 0.289 | day_marginal_runway:3,day_open_runway:2 | Chongqing,TelAviv |
| 2026-06-30 | 4 | 4 | 3 | +75.0% | 0.231 | $+1.87 | +40.6% | 0.388 | day_open_runway:4 | Singapore |

## Balanced Candidate Worst PnL Days

| target_date | trades | wins | win_rate | avg_weight | weighted_profit_usd | weighted_roi | regime_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-31 | 8 | 2 | +25.0% | 0.384 | $-12.77 | -83.2% | day_forecast_capped:3,day_marginal_runway:4,day_open_runway:1 | Busan,Karachi,Miami,TelAviv,Tokyo,Wuhan |
| 2026-06-02 | 9 | 4 | +44.4% | 0.375 | $-9.54 | -56.6% | day_forecast_capped:3,day_marginal_runway:3,day_open_runway:3 | Amsterdam,Beijing,Busan,Houston,TelAviv |
| 2026-05-25 | 8 | 2 | +25.0% | 0.310 | $-6.78 | -54.7% | day_forecast_capped:1,day_marginal_runway:5,day_open_runway:2 | CapeTown,Jeddah,Shanghai,TelAviv,Tokyo,Wuhan |
| 2026-06-01 | 5 | 2 | +40.0% | 0.368 | $-6.54 | -71.1% | day_forecast_capped:1,day_marginal_runway:3,day_open_runway:1 | Houston,NYC,TelAviv |
| 2026-06-17 | 9 | 4 | +44.4% | 0.263 | $-5.69 | -48.0% | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:4 | Beijing,Chengdu,Istanbul,Karachi,Wuhan |
| 2026-06-21 | 9 | 3 | +33.3% | 0.282 | $-4.33 | -34.2% | day_forecast_capped:4,day_marginal_runway:3,day_open_runway:2 | Beijing,CapeTown,Denver,Jeddah,Singapore,TelAviv |
| 2026-06-07 | 5 | 2 | +40.0% | 0.334 | $-4.26 | -50.9% | day_forecast_capped:2,day_marginal_runway:1,day_open_runway:2 | Istanbul,NYC,SanFrancisco |
| 2026-06-20 | 8 | 4 | +50.0% | 0.345 | $-3.59 | -25.9% | day_forecast_capped:3,day_marginal_runway:4,day_open_runway:1 | Jeddah,Karachi,Manila,Tokyo |
| 2026-05-24 | 4 | 2 | +50.0% | 0.351 | $-3.40 | -48.4% | day_forecast_capped:1,day_marginal_runway:2,day_open_runway:1 | Lucknow,NYC |
| 2026-06-22 | 3 | 0 | +0.0% | 0.196 | $-2.95 | -100.0% | day_forecast_capped:1,day_marginal_runway:1,day_open_runway:1 | NYC,Shanghai,Tokyo |
| 2026-05-21 | 8 | 3 | +37.5% | 0.330 | $-2.62 | -19.8% | day_forecast_capped:3,day_marginal_runway:2,day_open_runway:3 | Manila,Munich,SanFrancisco,SaoPaulo,TelAviv |
| 2026-06-18 | 5 | 3 | +60.0% | 0.303 | $-2.42 | -31.9% | day_forecast_capped:3,day_marginal_runway:2 | CapeTown,Manila |

## City Distribution

| city | trades | active_dates | wins | win_rate | avg_ask | profit_usd | roi | route_mix | loss_dates |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TelAviv | 11 | 11 | 1 | +9.1% | 0.339 | $-38.33 | -69.7% | runway_current_no:11 | 2026-05-25,2026-05-28,2026-05-31,2026-06-01,2026-06-02,2026-06-08,2026-06-11,2026-06-12,2026-06-16,2026-06-19 |
| Tokyo | 10 | 10 | 2 | +20.0% | 0.384 | $-28.93 | -57.9% | capped_d1_no:6,runway_current_no:4 | 2026-05-29,2026-05-31,2026-06-05,2026-06-08,2026-06-10,2026-06-12,2026-06-19,2026-06-20 |
| SaoPaulo | 3 | 3 | 0 | +0.0% | 0.427 | $-15.00 | -100.0% | capped_d1_no:1,runway_current_no:2 | 2026-05-21,2026-05-22,2026-05-27 |
| Ankara | 5 | 5 | 1 | +20.0% | 0.424 | $-14.80 | -59.2% | capped_d1_no:4,runway_current_no:1 | 2026-05-21,2026-05-28,2026-06-04,2026-06-08 |
| Karachi | 5 | 5 | 1 | +20.0% | 0.448 | $-13.89 | -55.6% | capped_d1_no:3,runway_current_no:2 | 2026-06-10,2026-06-14,2026-06-18,2026-06-25 |
| Houston | 7 | 7 | 2 | +28.6% | 0.407 | $-13.43 | -38.4% | capped_d1_no:1,runway_current_no:6 | 2026-06-01,2026-06-02,2026-06-12,2026-06-17,2026-06-19 |
| Manila | 5 | 5 | 1 | +20.0% | 0.332 | $-12.50 | -50.0% | capped_d1_no:1,runway_current_no:4 | 2026-05-28,2026-06-03,2026-06-11,2026-06-12 |
| Helsinki | 7 | 7 | 2 | +28.6% | 0.430 | $-11.64 | -33.3% | capped_d1_no:3,runway_current_no:4 | 2026-05-22,2026-06-05,2026-06-13,2026-06-17,2026-06-30 |
| Wuhan | 9 | 9 | 3 | +33.3% | 0.408 | $-10.90 | -24.2% | capped_d1_no:1,runway_current_no:8 | 2026-05-25,2026-05-26,2026-05-28,2026-05-31,2026-06-03,2026-06-11 |
| Chengdu | 2 | 2 | 0 | +0.0% | 0.435 | $-10.00 | -100.0% | capped_d1_no:1,runway_current_no:1 | 2026-05-24,2026-06-17 |
| CapeTown | 4 | 4 | 1 | +25.0% | 0.357 | $-9.80 | -49.0% | capped_d1_no:2,runway_current_no:2 | 2026-06-01,2026-06-25,2026-06-30 |
| Shanghai | 8 | 8 | 2 | +25.0% | 0.336 | $-5.48 | -13.7% | capped_d1_no:3,runway_current_no:5 | 2026-05-22,2026-05-25,2026-06-01,2026-06-02,2026-06-14,2026-06-22 |
| BuenosAires | 1 | 1 | 0 | +0.0% | 0.487 | $-5.00 | -100.0% | capped_d1_no:1 | 2026-05-31 |
| Dallas | 1 | 1 | 0 | +0.0% | 0.170 | $-5.00 | -100.0% | capped_d1_no:1 | 2026-06-02 |
| Singapore | 7 | 7 | 3 | +42.9% | 0.463 | $-3.95 | -11.3% | capped_d1_no:6,runway_current_no:1 | 2026-05-27,2026-05-30,2026-06-13,2026-06-14 |
| Warsaw | 5 | 5 | 2 | +40.0% | 0.366 | $-2.96 | -11.8% | capped_d1_no:3,runway_current_no:2 | 2026-05-27,2026-06-10,2026-06-26 |
| Miami | 8 | 8 | 3 | +37.5% | 0.381 | $+0.06 | +0.2% | capped_d1_no:7,runway_current_no:1 | 2026-05-22,2026-05-27,2026-05-31,2026-06-07,2026-06-19 |
| Wellington | 9 | 9 | 4 | +44.4% | 0.411 | $+1.29 | +2.9% | capped_d1_no:8,runway_current_no:1 | 2026-05-25,2026-06-07,2026-06-12,2026-06-13,2026-06-23 |
| Istanbul | 6 | 6 | 3 | +50.0% | 0.412 | $+3.20 | +10.7% | capped_d1_no:1,runway_current_no:5 | 2026-06-07,2026-06-09,2026-06-11 |
| Guangzhou | 1 | 1 | 1 | +100.0% | 0.390 | $+7.82 | +156.4% | capped_d1_no:1 |  |
| Atlanta | 5 | 5 | 3 | +60.0% | 0.452 | $+9.23 | +36.9% | capped_d1_no:2,runway_current_no:3 | 2026-05-30,2026-06-12 |
| Amsterdam | 4 | 4 | 2 | +50.0% | 0.345 | $+12.56 | +62.8% | runway_current_no:4 | 2026-05-23,2026-06-02 |
| Denver | 1 | 1 | 1 | +100.0% | 0.280 | $+12.86 | +257.1% | runway_current_no:1 |  |
| Lucknow | 4 | 4 | 1 | +25.0% | 0.258 | $+13.33 | +66.7% | runway_current_no:4 | 2026-05-23,2026-06-14,2026-06-15 |
| Munich | 4 | 4 | 3 | +75.0% | 0.430 | $+14.45 | +72.2% | capped_d1_no:4 | 2026-05-22 |
| Seattle | 1 | 1 | 1 | +100.0% | 0.250 | $+15.00 | +300.0% | runway_current_no:1 |  |
| Busan | 8 | 8 | 3 | +37.5% | 0.294 | $+20.27 | +50.7% | capped_d1_no:1,runway_current_no:7 | 2026-05-31,2026-06-10,2026-06-12,2026-06-19,2026-06-30 |
| Taipei | 5 | 5 | 3 | +60.0% | 0.317 | $+20.82 | +83.3% | capped_d1_no:2,runway_current_no:3 | 2026-05-20,2026-06-05 |
| SanFrancisco | 3 | 3 | 2 | +66.7% | 0.367 | $+23.65 | +157.6% | capped_d1_no:1,runway_current_no:2 | 2026-06-07 |
| NYC | 6 | 6 | 3 | +50.0% | 0.358 | $+24.82 | +82.7% | runway_current_no:6 | 2026-05-28,2026-06-01,2026-06-07 |
| LA | 3 | 3 | 2 | +66.7% | 0.317 | $+24.97 | +166.5% | capped_d1_no:1,runway_current_no:2 | 2026-06-15 |
| Beijing | 8 | 8 | 5 | +62.5% | 0.398 | $+26.93 | +67.3% | runway_current_no:8 | 2026-05-20,2026-06-10,2026-06-17 |
| Jeddah | 6 | 6 | 4 | +66.7% | 0.332 | $+34.59 | +115.3% | capped_d1_no:2,runway_current_no:4 | 2026-06-20,2026-06-21 |
| Austin | 3 | 3 | 3 | +100.0% | 0.333 | $+36.19 | +241.3% | capped_d1_no:1,runway_current_no:2 |  |
| Chongqing | 5 | 5 | 2 | +40.0% | 0.305 | $+36.36 | +145.5% | capped_d1_no:1,runway_current_no:4 | 2026-05-28,2026-06-17,2026-06-26 |

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
