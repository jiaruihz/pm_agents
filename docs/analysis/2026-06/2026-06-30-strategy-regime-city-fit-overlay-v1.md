# Strategy Regime City-Fit Overlay v1

Generated: 2026-06-30

## Verdict

需要继续完善城市分类和 regime 分布。当前证据支持把 `city/source forecast-bias fit` 作为 selection/sizing feature 接入 replay/shadow，但不支持直接做 live hard gate。

主要结论：

- 城市分类本身不是 alpha。raw expression matrix 里 `current_bracket_no` 即使在 hot-underforecast 城市也亏，说明必须叠加 day_regime / intraday_state / price 才能交易。
- 在 selected strategy rows 里，`current_bracket_no × hot_underforecast_clean` 表现最好；但 `hot_underforecast_noisy` 反而弱，说明要区分 clean/noisy，不应把 hot 城市一起扩大。
- `higher_no_d1/d2` 和 `current_yes/current_high_yes` 更适合继续在 cold-overforecast / balanced-tight 城市做 shadow；但 raw matrix 里 current YES 在多类城市都正，可能有 market/base-rate 成分，不能直接说是 forecast-bias alpha。
- `two_sided_noisy` 城市需要继续 shadow 或降 size，不能因为某个 route 点估好就扩大。

## Evidence Layers

- Selected strategy rows: `docs/analysis/2026-06/generated/regime_routed_expression_router_v3/trade_details.csv` (283 rows).
- Expression payoff matrix: `docs/analysis/2026-06/generated/current_yes_peak_yes_execution_timing_v1/peak_yes_timing_v1_event_rows.csv` expanded to current YES / current NO / d1 NO / d2 NO (12606 expression rows).
- City fit map: `docs/analysis/2026-06/generated/city_strategy_fit_by_forecast_bias_v1/city_strategy_fit_by_forecast_bias.csv`.

## Selected Strategy: Expression Summary

| expression | rows | dates | cities | cost | pnl | ROI | win |
| --- | --- | --- | --- | --- | --- | --- | --- |
| current_bracket_no | 188 | 37 | 33 | $+940.00 | $+174.96 | +18.6% | +49.5% |
| higher_no_d2 | 84 | 34 | 24 | $+420.00 | $+10.06 | +2.4% | +61.9% |
| current_high_yes | 11 | 11 | 8 | $+55.00 | $-1.05 | -1.9% | +72.7% |

## Selected Strategy: City-Fit Alignment

| expression | fit | city regime | rows | dates | cities | cost | pnl | ROI | win |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_bracket_no | aligned | hot_underforecast_clean | 42 | 24 | 11 | $+210.00 | $+74.74 | +35.6% | +54.8% |
| current_bracket_no | neutral_or_unknown | mild_or_mixed | 38 | 25 | 4 | $+190.00 | $-56.30 | -29.6% | +36.8% |
| higher_no_d2 | against_fit | hot_underforecast_clean | 33 | 23 | 10 | $+165.00 | $-5.29 | -3.2% | +60.6% |
| current_bracket_no | neutral_or_unknown | unclassified | 27 | 19 | 6 | $+135.00 | $+86.70 | +64.2% | +59.3% |
| current_bracket_no | aligned | hot_underforecast_noisy | 26 | 23 | 4 | $+130.00 | $-23.42 | -18.0% | +38.5% |
| higher_no_d2 | neutral_or_unknown | unclassified | 20 | 18 | 5 | $+100.00 | $+0.89 | +0.9% | +55.0% |
| current_bracket_no | neutral_or_unknown | balanced_tight | 18 | 15 | 3 | $+90.00 | $+8.61 | +9.6% | +50.0% |
| current_bracket_no | neutral_or_unknown | cold_overforecast_noisy | 17 | 16 | 2 | $+85.00 | $+25.97 | +30.6% | +52.9% |
| current_bracket_no | against_fit | cold_overforecast_clean | 16 | 15 | 2 | $+80.00 | $+11.99 | +15.0% | +56.2% |
| higher_no_d2 | aligned | cold_overforecast_noisy | 13 | 13 | 1 | $+65.00 | $+1.87 | +2.9% | +61.5% |
| higher_no_d2 | against_fit | hot_underforecast_noisy | 6 | 6 | 2 | $+30.00 | $+7.44 | +24.8% | +83.3% |
| higher_no_d2 | aligned | cold_overforecast_clean | 5 | 5 | 2 | $+25.00 | $+5.61 | +22.5% | +80.0% |
| current_bracket_no | against_fit | two_sided_noisy | 4 | 4 | 1 | $+20.00 | $+46.67 | +233.3% | +75.0% |
| current_high_yes | neutral_or_unknown | mild_or_mixed | 3 | 3 | 2 | $+15.00 | $-3.89 | -25.9% | +66.7% |
| current_high_yes | neutral_or_unknown | unclassified | 3 | 3 | 2 | $+15.00 | $+4.56 | +30.4% | +100.0% |
| higher_no_d2 | against_fit | two_sided_noisy | 3 | 3 | 1 | $+15.00 | $-0.06 | -0.4% | +66.7% |
| higher_no_d2 | neutral_or_unknown | balanced_tight | 3 | 3 | 2 | $+15.00 | $-3.10 | -20.6% | +33.3% |
| current_high_yes | aligned | cold_overforecast_clean | 2 | 2 | 1 | $+10.00 | $-1.23 | -12.3% | +50.0% |
| current_high_yes | against_fit | hot_underforecast_clean | 1 | 1 | 1 | $+5.00 | $-5.00 | -100.0% | +0.0% |
| current_high_yes | aligned | cold_overforecast_noisy | 1 | 1 | 1 | $+5.00 | $+2.36 | +47.3% | +100.0% |
| current_high_yes | neutral_or_unknown | hot_underforecast_noisy | 1 | 1 | 1 | $+5.00 | $+2.14 | +42.9% | +100.0% |
| higher_no_d2 | neutral_or_unknown | mild_or_mixed | 1 | 1 | 1 | $+5.00 | $+2.69 | +53.8% | +100.0% |

## Selected Strategy: Day Regime

| expression | day_regime | fit | rows | dates | cities | cost | pnl | ROI | win |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_bracket_no | day_marginal_runway | neutral_or_unknown | 65 | 29 | 14 | $+325.00 | $+89.58 | +27.6% | +55.4% |
| higher_no_d2 | day_forecast_capped | against_fit | 42 | 26 | 13 | $+210.00 | $+2.09 | +1.0% | +64.3% |
| current_bracket_no | day_marginal_runway | aligned | 41 | 23 | 14 | $+205.00 | $+33.17 | +16.2% | +46.3% |
| current_bracket_no | day_open_runway | neutral_or_unknown | 35 | 26 | 11 | $+175.00 | $-24.59 | -14.1% | +34.3% |
| current_bracket_no | day_open_runway | aligned | 27 | 21 | 9 | $+135.00 | $+18.16 | +13.4% | +51.9% |
| higher_no_d2 | day_forecast_capped | neutral_or_unknown | 24 | 20 | 8 | $+120.00 | $+0.48 | +0.4% | +54.2% |
| higher_no_d2 | day_forecast_capped | aligned | 18 | 17 | 3 | $+90.00 | $+7.49 | +8.3% | +66.7% |
| current_bracket_no | day_marginal_runway | against_fit | 14 | 14 | 3 | $+70.00 | $+55.46 | +79.2% | +64.3% |
| current_bracket_no | day_open_runway | against_fit | 6 | 6 | 3 | $+30.00 | $+3.20 | +10.7% | +50.0% |
| current_high_yes | day_marginal_runway | neutral_or_unknown | 5 | 5 | 4 | $+25.00 | $-1.71 | -6.8% | +80.0% |
| current_high_yes | day_open_runway | aligned | 3 | 3 | 2 | $+15.00 | $+1.14 | +7.6% | +66.7% |
| current_high_yes | day_open_runway | neutral_or_unknown | 2 | 2 | 2 | $+10.00 | $+4.53 | +45.3% | +100.0% |
| current_high_yes | day_marginal_runway | against_fit | 1 | 1 | 1 | $+5.00 | $-5.00 | -100.0% | +0.0% |

## Expression Matrix: City-Fit Summary

This is not a selected live strategy. It asks what each expression would have done on the same current-YES event rows.

| expression | fit | city regime | rows | dates | cities | cost | pnl | ROI | win |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_bracket_no | aligned | hot_underforecast_clean | 1012 | 36 | 12 | $+5060.00 | $-1572.70 | -31.1% | +22.3% |
| current_yes | against_fit | hot_underforecast_clean | 1012 | 36 | 12 | $+5060.00 | $+342.82 | +6.8% | +77.7% |
| higher_no_d1 | against_fit | hot_underforecast_clean | 1012 | 36 | 12 | $+5060.00 | $+87.66 | +1.7% | +79.6% |
| higher_no_d2 | against_fit | hot_underforecast_clean | 957 | 36 | 12 | $+4785.00 | $+53.79 | +1.1% | +98.0% |
| current_bracket_no | neutral_or_unknown | unclassified | 683 | 35 | 8 | $+3415.00 | $-1069.97 | -31.3% | +24.7% |
| current_yes | neutral_or_unknown | unclassified | 683 | 35 | 8 | $+3415.00 | $-34.88 | -1.0% | +75.3% |
| higher_no_d1 | neutral_or_unknown | unclassified | 683 | 35 | 8 | $+3415.00 | $-98.15 | -2.9% | +77.9% |
| higher_no_d2 | neutral_or_unknown | unclassified | 636 | 35 | 8 | $+3180.00 | $+18.60 | +0.6% | +97.6% |
| current_bracket_no | neutral_or_unknown | mild_or_mixed | 420 | 35 | 4 | $+2100.00 | $-622.22 | -29.6% | +24.0% |
| current_yes | neutral_or_unknown | mild_or_mixed | 420 | 35 | 4 | $+2100.00 | $+187.06 | +8.9% | +76.0% |
| higher_no_d1 | neutral_or_unknown | mild_or_mixed | 420 | 35 | 4 | $+2100.00 | $+127.51 | +6.1% | +80.0% |
| higher_no_d2 | neutral_or_unknown | mild_or_mixed | 405 | 35 | 4 | $+2025.00 | $-10.34 | -0.5% | +95.8% |
| current_bracket_no | aligned | hot_underforecast_noisy | 367 | 35 | 4 | $+1835.00 | $-740.55 | -40.4% | +24.8% |
| current_yes | neutral_or_unknown | hot_underforecast_noisy | 367 | 35 | 4 | $+1835.00 | $+86.46 | +4.7% | +75.2% |
| higher_no_d1 | against_fit | hot_underforecast_noisy | 367 | 35 | 4 | $+1835.00 | $+45.32 | +2.5% | +79.3% |
| higher_no_d2 | against_fit | hot_underforecast_noisy | 341 | 35 | 4 | $+1705.00 | $-1.63 | -0.1% | +95.9% |
| current_bracket_no | against_fit | cold_overforecast_clean | 271 | 34 | 2 | $+1355.00 | $-657.26 | -48.5% | +22.1% |
| current_yes | aligned | cold_overforecast_clean | 271 | 34 | 2 | $+1355.00 | $+126.08 | +9.3% | +77.9% |
| higher_no_d1 | aligned | cold_overforecast_clean | 271 | 34 | 2 | $+1355.00 | $-55.02 | -4.1% | +79.0% |
| higher_no_d2 | aligned | cold_overforecast_clean | 235 | 34 | 2 | $+1175.00 | $+51.23 | +4.4% | +98.7% |
| current_bracket_no | neutral_or_unknown | cold_overforecast_noisy | 191 | 32 | 2 | $+955.00 | $-413.13 | -43.3% | +22.0% |
| current_yes | aligned | cold_overforecast_noisy | 191 | 32 | 2 | $+955.00 | $+51.49 | +5.4% | +78.0% |
| higher_no_d1 | aligned | cold_overforecast_noisy | 191 | 32 | 2 | $+955.00 | $+37.81 | +4.0% | +81.2% |
| current_bracket_no | neutral_or_unknown | balanced_tight | 178 | 30 | 3 | $+890.00 | $-615.92 | -69.2% | +16.3% |
| current_yes | neutral_or_unknown | balanced_tight | 178 | 30 | 3 | $+890.00 | $+39.86 | +4.5% | +83.7% |
| higher_no_d1 | neutral_or_unknown | balanced_tight | 178 | 30 | 3 | $+890.00 | $+1.79 | +0.2% | +83.7% |
| higher_no_d2 | aligned | cold_overforecast_noisy | 168 | 31 | 2 | $+840.00 | $-5.40 | -0.6% | +96.4% |
| higher_no_d2 | neutral_or_unknown | balanced_tight | 167 | 30 | 3 | $+835.00 | $+13.64 | +1.6% | +100.0% |
| current_bracket_no | against_fit | two_sided_noisy | 88 | 26 | 1 | $+440.00 | $-234.50 | -53.3% | +12.5% |
| current_yes | against_fit | two_sided_noisy | 88 | 26 | 1 | $+440.00 | $+89.59 | +20.4% | +87.5% |
| higher_no_d1 | against_fit | two_sided_noisy | 84 | 25 | 1 | $+420.00 | $+62.07 | +14.8% | +91.7% |
| higher_no_d2 | against_fit | two_sided_noisy | 71 | 22 | 1 | $+355.00 | $-4.76 | -1.3% | +94.4% |

## Expression Matrix: Day Regime x Fit

| expression | day_regime | fit | rows | dates | cities | cost | pnl | ROI | win |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | day_forecast_capped | neutral_or_unknown | 636 | 35 | 18 | $+3180.00 | $+224.45 | +7.1% | +81.8% |
| higher_no_d1 | day_forecast_busted | against_fit | 609 | 34 | 16 | $+3045.00 | $+113.95 | +3.7% | +83.6% |
| current_bracket_no | day_forecast_busted | aligned | 563 | 34 | 15 | $+2815.00 | $-808.39 | -28.7% | +19.7% |
| higher_no_d2 | day_forecast_busted | against_fit | 558 | 34 | 16 | $+2790.00 | $+18.16 | +0.7% | +98.0% |
| higher_no_d1 | day_forecast_capped | against_fit | 552 | 35 | 17 | $+2760.00 | $+106.63 | +3.9% | +81.0% |
| current_bracket_no | day_forecast_capped | neutral_or_unknown | 531 | 35 | 16 | $+2655.00 | $-1109.80 | -41.8% | +18.1% |
| current_bracket_no | day_forecast_capped | aligned | 526 | 35 | 16 | $+2630.00 | $-994.44 | -37.8% | +21.5% |
| higher_no_d2 | day_forecast_capped | against_fit | 523 | 35 | 17 | $+2615.00 | $+30.57 | +1.2% | +98.3% |
| current_yes | day_forecast_busted | against_fit | 512 | 34 | 13 | $+2560.00 | $+274.68 | +10.7% | +83.8% |
| higher_no_d1 | day_forecast_capped | neutral_or_unknown | 496 | 34 | 14 | $+2480.00 | $+75.46 | +3.0% | +83.7% |
| higher_no_d2 | day_forecast_capped | neutral_or_unknown | 474 | 34 | 14 | $+2370.00 | $+2.76 | +0.1% | +98.1% |
| current_yes | day_forecast_busted | neutral_or_unknown | 462 | 34 | 17 | $+2310.00 | $-23.01 | -1.0% | +77.3% |
| current_bracket_no | day_forecast_busted | neutral_or_unknown | 428 | 34 | 15 | $+2140.00 | $-969.07 | -45.3% | +18.9% |
| current_yes | day_forecast_capped | against_fit | 412 | 35 | 13 | $+2060.00 | $+163.75 | +7.9% | +78.4% |
| higher_no_d1 | day_forecast_busted | neutral_or_unknown | 361 | 34 | 14 | $+1805.00 | $-59.17 | -3.3% | +79.5% |
| current_yes | day_marginal_runway | neutral_or_unknown | 333 | 36 | 15 | $+1665.00 | $+25.43 | +1.5% | +68.8% |
| current_bracket_no | day_marginal_runway | neutral_or_unknown | 326 | 33 | 14 | $+1630.00 | $-239.64 | -14.7% | +35.0% |
| higher_no_d2 | day_forecast_busted | neutral_or_unknown | 322 | 33 | 13 | $+1610.00 | $+24.58 | +1.5% | +99.1% |
| higher_no_d1 | day_marginal_runway | neutral_or_unknown | 269 | 33 | 12 | $+1345.00 | $-29.01 | -2.2% | +72.5% |
| higher_no_d2 | day_marginal_runway | neutral_or_unknown | 259 | 33 | 12 | $+1295.00 | $-34.92 | -2.7% | +93.4% |
| current_yes | day_open_runway | neutral_or_unknown | 191 | 30 | 13 | $+955.00 | $+31.27 | +3.3% | +68.6% |
| higher_no_d1 | day_marginal_runway | against_fit | 185 | 28 | 13 | $+925.00 | $+4.83 | +0.5% | +73.5% |
| current_bracket_no | day_marginal_runway | aligned | 173 | 28 | 12 | $+865.00 | $-323.17 | -37.4% | +27.2% |
| higher_no_d2 | day_marginal_runway | against_fit | 171 | 27 | 13 | $+855.00 | $+8.65 | +1.0% | +95.9% |
| current_bracket_no | day_open_runway | neutral_or_unknown | 170 | 30 | 12 | $+850.00 | $-335.27 | -39.4% | +28.2% |
| current_yes | day_forecast_busted | aligned | 155 | 22 | 3 | $+775.00 | $+79.58 | +10.3% | +85.2% |
| higher_no_d1 | day_forecast_busted | aligned | 155 | 22 | 3 | $+775.00 | $+19.71 | +2.5% | +85.2% |
| current_yes | day_forecast_capped | aligned | 143 | 22 | 4 | $+715.00 | $+124.25 | +17.4% | +84.6% |
| higher_no_d1 | day_forecast_capped | aligned | 143 | 22 | 4 | $+715.00 | $+23.25 | +3.3% | +84.6% |
| current_bracket_no | day_forecast_busted | against_fit | 138 | 25 | 3 | $+690.00 | $-442.78 | -64.2% | +13.8% |
| higher_no_d1 | day_open_runway | neutral_or_unknown | 138 | 27 | 10 | $+690.00 | $+32.48 | +4.7% | +76.1% |
| higher_no_d2 | day_open_runway | neutral_or_unknown | 138 | 27 | 10 | $+690.00 | $+25.50 | +3.7% | +97.8% |
| current_bracket_no | day_forecast_capped | against_fit | 134 | 21 | 3 | $+670.00 | $-456.36 | -68.1% | +13.4% |
| higher_no_d2 | day_forecast_busted | aligned | 127 | 19 | 3 | $+635.00 | $+22.95 | +3.6% | +100.0% |
| higher_no_d2 | day_forecast_capped | aligned | 127 | 21 | 4 | $+635.00 | $+31.00 | +4.9% | +100.0% |
| current_yes | day_marginal_runway | against_fit | 121 | 22 | 10 | $+605.00 | $-5.87 | -1.0% | +64.5% |
| current_yes | day_marginal_runway | aligned | 112 | 25 | 4 | $+560.00 | $-84.96 | -15.2% | +62.5% |
| higher_no_d1 | day_marginal_runway | aligned | 112 | 25 | 4 | $+560.00 | $-121.86 | -21.8% | +62.5% |
| higher_no_d2 | day_marginal_runway | aligned | 98 | 23 | 4 | $+490.00 | $+19.01 | +3.9% | +100.0% |
| current_bracket_no | day_open_runway | aligned | 93 | 23 | 10 | $+465.00 | $-119.71 | -25.7% | +41.9% |
| higher_no_d1 | day_open_runway | against_fit | 93 | 23 | 10 | $+465.00 | $-28.70 | -6.2% | +69.9% |
| higher_no_d2 | day_open_runway | against_fit | 93 | 23 | 10 | $+465.00 | $-18.95 | -4.1% | +89.2% |
| current_bracket_no | day_marginal_runway | against_fit | 67 | 13 | 3 | $+335.00 | $+63.72 | +19.0% | +41.8% |
| current_yes | day_open_runway | aligned | 49 | 11 | 4 | $+245.00 | $+45.92 | +18.7% | +69.4% |
| higher_no_d1 | day_open_runway | aligned | 49 | 11 | 4 | $+245.00 | $+51.26 | +20.9% | +87.8% |
| higher_no_d2 | day_open_runway | aligned | 48 | 10 | 4 | $+240.00 | $-27.59 | -11.5% | +81.2% |
| current_yes | day_open_runway | against_fit | 40 | 15 | 7 | $+200.00 | $-25.89 | -12.9% | +55.0% |
| current_yes | day_space_unknown | neutral_or_unknown | 26 | 3 | 9 | $+130.00 | $+20.36 | +15.7% | +80.8% |
| current_bracket_no | day_space_unknown | aligned | 24 | 3 | 7 | $+120.00 | $-67.53 | -56.3% | +29.2% |
| higher_no_d1 | day_space_unknown | against_fit | 24 | 3 | 7 | $+120.00 | $-1.66 | -1.4% | +70.8% |
| higher_no_d2 | day_space_unknown | against_fit | 24 | 3 | 7 | $+120.00 | $+8.97 | +7.5% | +100.0% |
| current_bracket_no | day_open_runway | against_fit | 17 | 6 | 2 | $+85.00 | $-41.35 | -48.6% | +35.3% |
| current_bracket_no | day_space_unknown | neutral_or_unknown | 17 | 3 | 7 | $+85.00 | $-67.46 | -79.4% | +11.8% |
| higher_no_d1 | day_space_unknown | neutral_or_unknown | 17 | 3 | 7 | $+85.00 | $+11.39 | +13.4% | +88.2% |
| current_yes | day_space_unknown | against_fit | 15 | 3 | 5 | $+75.00 | $+25.74 | +34.3% | +73.3% |
| higher_no_d2 | day_space_unknown | neutral_or_unknown | 15 | 3 | 7 | $+75.00 | $+3.99 | +5.3% | +100.0% |
| current_bracket_no | day_space_unknown | against_fit | 3 | 1 | 1 | $+15.00 | $-15.00 | -100.0% | +0.0% |
| current_yes | day_space_unknown | aligned | 3 | 1 | 1 | $+15.00 | $+12.78 | +85.2% | +100.0% |
| higher_no_d1 | day_space_unknown | aligned | 3 | 1 | 1 | $+15.00 | $+10.42 | +69.5% | +100.0% |
| higher_no_d2 | day_space_unknown | aligned | 3 | 1 | 1 | $+15.00 | $+0.46 | +3.1% | +100.0% |

## Forward Window Since 2026-06-21

| expression | fit | city regime | rows | dates | cities | cost | pnl | ROI | win |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_bracket_no | neutral_or_unknown | cold_overforecast_noisy | 6 | 5 | 2 | $+30.00 | $+18.68 | +62.3% | +50.0% |
| higher_no_d2 | against_fit | hot_underforecast_clean | 6 | 3 | 5 | $+30.00 | $+0.01 | +0.0% | +66.7% |
| current_bracket_no | aligned | hot_underforecast_clean | 4 | 3 | 3 | $+20.00 | $-11.80 | -59.0% | +25.0% |
| current_bracket_no | neutral_or_unknown | mild_or_mixed | 4 | 2 | 3 | $+20.00 | $-20.00 | -100.0% | +0.0% |
| current_bracket_no | against_fit | cold_overforecast_clean | 2 | 2 | 1 | $+10.00 | $-1.94 | -19.4% | +50.0% |
| current_bracket_no | aligned | hot_underforecast_noisy | 2 | 2 | 1 | $+10.00 | $+8.70 | +87.0% | +100.0% |
| current_high_yes | aligned | cold_overforecast_clean | 2 | 2 | 1 | $+10.00 | $-1.23 | -12.3% | +50.0% |
| current_bracket_no | against_fit | two_sided_noisy | 1 | 1 | 1 | $+5.00 | $+3.33 | +66.7% | +100.0% |
| current_bracket_no | neutral_or_unknown | balanced_tight | 1 | 1 | 1 | $+5.00 | $+3.06 | +61.3% | +100.0% |
| current_high_yes | against_fit | hot_underforecast_clean | 1 | 1 | 1 | $+5.00 | $-5.00 | -100.0% | +0.0% |
| higher_no_d2 | neutral_or_unknown | balanced_tight | 1 | 1 | 1 | $+5.00 | $-5.00 | -100.0% | +0.0% |
| higher_no_d2 | neutral_or_unknown | unclassified | 1 | 1 | 1 | $+5.00 | $-5.00 | -100.0% | +0.0% |

## Next Implementation

- Add `source_bias_regime` and route-specific fit columns to shadow logs for current NO, higher NO, and current YES.
- Replay route policies with city-fit as soft prior/size multiplier, not as a hard city allowlist.
- For expansion: prioritize `hot_underforecast_clean`, not all hot cities, for current-NO shadow.
- For higher-NO/current-YES: prioritize cold-overforecast and balanced-tight city shadow, then require route-specific forward evidence before live sizing.
- Keep `two_sided_noisy` as telemetry or reduced size until expression-specific forward evidence improves.

## Artifacts

- Selected rows with fit: `docs/analysis/2026-06/generated/strategy_regime_city_fit_overlay_v1/selected_strategy_rows_with_city_fit.csv`
- Expression matrix rows with fit: `docs/analysis/2026-06/generated/strategy_regime_city_fit_overlay_v1/expression_matrix_rows_with_city_fit.csv`
- Summaries: `docs/analysis/2026-06/generated/strategy_regime_city_fit_overlay_v1/*.csv`
