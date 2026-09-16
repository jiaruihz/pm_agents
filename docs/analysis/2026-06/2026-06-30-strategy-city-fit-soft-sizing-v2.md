# Strategy City-Fit Soft Sizing v2

Generated: 2026-06-30

## Verdict

对比对象是当前在跑的 tiny-live forward probe：`regime_routed_no_route_price_disciplined_tiny_live_v1`。本报告只做 replay/shadow sizing 对比，不改 live。

结论：city/source forecast-bias fit 适合作为 soft sizing prior，但当前证据仍是 `inconclusive_shadow_only`。同分母 283 笔上，总 ROI 小幅改善，但 2026-06-21 之后 forward 变弱；它改善组合形态的方向主要来自降低逆 city-fit 的 notional，而不是发现了新的独立 alpha。

## Policy Comparison

| policy | rows | dates | cities | cost | pnl | ROI | avg/day | losing days | <=-50% days | max loss | forward cost | forward pnl | forward ROI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| running_current_row_risk_soft | 283 | 38 | 35 | $+491.32 | $+127.20 | +25.9% | $+12.93 | 16 | 5 | $-9.52 | $+47.01 | $+3.12 | +6.6% |
| city_fit_soft_overlay_v2 | 283 | 38 | 35 | $+405.03 | $+110.50 | +27.3% | $+10.66 | 15 | 6 | $-7.67 | $+39.01 | $+0.89 | +2.3% |

## Daily Comparison

| date | rows | running cost | running pnl | running ROI | city-fit cost | city-fit pnl | city-fit ROI | delta pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-20 | 12 | $+14.62 | $+13.79 | +94.3% | $+12.73 | $+12.18 | +95.7% | $-1.62 |
| 2026-05-21 | 8 | $+12.69 | $-0.33 | -2.6% | $+10.11 | $+0.44 | +4.3% | $+0.77 |
| 2026-05-22 | 8 | $+11.45 | $+0.20 | +1.8% | $+9.37 | $-0.76 | -8.1% | $-0.97 |
| 2026-05-23 | 8 | $+16.54 | $+1.22 | +7.4% | $+13.29 | $+1.17 | +8.8% | $-0.05 |
| 2026-05-24 | 4 | $+6.77 | $-3.09 | -45.6% | $+4.88 | $-2.13 | -43.6% | $+0.96 |
| 2026-05-25 | 8 | $+12.35 | $-6.74 | -54.6% | $+10.38 | $-6.45 | -62.2% | $+0.28 |
| 2026-05-26 | 7 | $+17.14 | $+14.26 | +83.2% | $+14.08 | $+10.68 | +75.9% | $-3.58 |
| 2026-05-27 | 5 | $+10.35 | $-3.34 | -32.3% | $+9.77 | $-2.37 | -24.2% | $+0.97 |
| 2026-05-28 | 15 | $+30.58 | $+13.48 | +44.1% | $+27.97 | $+11.08 | +39.6% | $-2.40 |
| 2026-05-29 | 8 | $+13.85 | $+3.90 | +28.1% | $+12.11 | $+4.77 | +39.4% | $+0.87 |
| 2026-05-30 | 3 | $+3.35 | $+1.67 | +49.9% | $+2.63 | $+1.71 | +65.0% | $+0.04 |
| 2026-05-31 | 8 | $+13.40 | $-8.24 | -61.5% | $+10.83 | $-6.89 | -63.6% | $+1.35 |
| 2026-06-01 | 5 | $+10.77 | $-9.01 | -83.6% | $+8.01 | $-6.42 | -80.2% | $+2.58 |
| 2026-06-02 | 9 | $+17.14 | $-9.52 | -55.5% | $+13.62 | $-7.67 | -56.3% | $+1.84 |
| 2026-06-03 | 5 | $+7.65 | $+10.55 | +137.9% | $+6.29 | $+8.50 | +135.3% | $-2.05 |
| 2026-06-04 | 6 | $+8.62 | $+4.46 | +51.8% | $+6.56 | $+3.53 | +53.8% | $-0.94 |
| 2026-06-05 | 7 | $+9.25 | $+1.42 | +15.4% | $+7.80 | $+1.37 | +17.5% | $-0.06 |
| 2026-06-06 | 4 | $+9.00 | $+5.72 | +63.6% | $+7.63 | $+4.65 | +60.9% | $-1.08 |
| 2026-06-07 | 5 | $+8.32 | $-0.60 | -7.2% | $+6.52 | $+0.05 | +0.8% | $+0.65 |
| 2026-06-08 | 9 | $+14.79 | $+10.52 | +71.1% | $+11.19 | $+7.52 | +67.2% | $-3.01 |
| 2026-06-09 | 10 | $+17.40 | $+12.09 | +69.5% | $+13.38 | $+8.26 | +61.7% | $-3.84 |
| 2026-06-10 | 7 | $+10.27 | $-2.63 | -25.7% | $+7.84 | $-2.04 | -26.0% | $+0.59 |
| 2026-06-11 | 10 | $+24.69 | $+11.46 | +46.4% | $+21.40 | $+9.95 | +46.5% | $-1.52 |
| 2026-06-12 | 14 | $+30.95 | $+13.74 | +44.4% | $+27.69 | $+17.81 | +64.3% | $+4.08 |
| 2026-06-13 | 7 | $+11.08 | $+6.26 | +56.5% | $+9.56 | $+6.42 | +67.2% | $+0.16 |
| 2026-06-14 | 10 | $+18.45 | $+34.56 | +187.3% | $+14.19 | $+24.97 | +176.0% | $-9.58 |
| 2026-06-15 | 12 | $+21.61 | $+10.93 | +50.6% | $+17.33 | $+10.83 | +62.5% | $-0.10 |
| 2026-06-16 | 5 | $+8.79 | $+0.39 | +4.5% | $+6.46 | $+0.14 | +2.2% | $-0.25 |
| 2026-06-17 | 9 | $+12.81 | $-6.32 | -49.4% | $+10.61 | $-5.66 | -53.3% | $+0.66 |
| 2026-06-18 | 5 | $+6.99 | $-1.97 | -28.2% | $+5.13 | $-1.42 | -27.6% | $+0.55 |
| 2026-06-19 | 11 | $+19.41 | $+7.71 | +39.7% | $+14.83 | $+6.32 | +42.6% | $-1.39 |
| 2026-06-20 | 8 | $+13.23 | $-2.48 | -18.8% | $+11.83 | $-0.92 | -7.7% | $+1.56 |
| 2026-06-21 | 9 | $+14.10 | $+0.72 | +5.1% | $+10.95 | $+1.00 | +9.2% | $+0.28 |
| 2026-06-22 | 3 | $+1.68 | $-1.68 | -100.0% | $+1.70 | $-1.70 | -100.0% | $-0.02 |
| 2026-06-23 | 6 | $+9.79 | $+6.55 | +66.8% | $+7.96 | $+4.36 | +54.7% | $-2.19 |
| 2026-06-24 | 3 | $+4.65 | $-1.33 | -28.7% | $+4.06 | $-1.57 | -38.7% | $-0.24 |
| 2026-06-25 | 5 | $+6.65 | $-0.64 | -9.6% | $+4.86 | $-0.12 | -2.4% | $+0.52 |
| 2026-06-26 | 5 | $+10.13 | $-0.49 | -4.9% | $+9.48 | $-1.08 | -11.4% | $-0.58 |

## Running Strategy By Expression

| expression | rows | dates | cities | cost | pnl | ROI | avg city mult |
| --- | --- | --- | --- | --- | --- | --- | --- |
| current_bracket_no | 188 | 37 | 33 | $+380.76 | $+126.43 | +33.2% | 0.84 |
| higher_no_d2 | 84 | 34 | 24 | $+87.87 | $+3.82 | +4.4% | 0.72 |
| current_high_yes | 11 | 11 | 8 | $+22.69 | $-3.05 | -13.5% | 0.94 |

## City-Fit Overlay By Expression

| expression | rows | dates | cities | cost | pnl | ROI | avg city mult |
| --- | --- | --- | --- | --- | --- | --- | --- |
| current_bracket_no | 188 | 37 | 33 | $+320.76 | $+109.26 | +34.1% | 0.84 |
| higher_no_d2 | 84 | 34 | 24 | $+63.18 | $+3.41 | +5.4% | 0.72 |
| current_high_yes | 11 | 11 | 8 | $+21.08 | $-2.18 | -10.3% | 0.94 |

## Route x Source-Bias Regime

| route | source-bias regime | rows | dates | cities | cost | pnl | ROI | avg mult |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fresh_runway_current_no | cold_overforecast_noisy | 50 | 27 | 7 | $+76.51 | $+41.94 | +54.8% | 0.75 |
| fresh_runway_current_no | hot_underforecast_clean | 27 | 17 | 11 | $+63.50 | $+25.66 | +40.4% | 1.15 |
| fresh_runway_current_no | mild_or_mixed | 28 | 19 | 6 | $+44.55 | $-18.95 | -42.5% | 0.80 |
| capped_d2_no | hot_underforecast_clean | 51 | 29 | 13 | $+35.59 | $+0.15 | +0.4% | 0.65 |
| fresh_runway_current_no | cold_overforecast_clean | 22 | 17 | 4 | $+32.05 | $+7.67 | +23.9% | 0.65 |
| fresh_runway_current_no | balanced_tight | 15 | 12 | 4 | $+23.09 | $+1.39 | +6.0% | 0.90 |
| false_fade_reheat_current_no | hot_underforecast_clean | 6 | 6 | 6 | $+17.68 | $+7.44 | +42.1% | 1.15 |
| capped_d2_no | hot_underforecast_noisy | 18 | 16 | 3 | $+14.47 | $+0.69 | +4.8% | 0.75 |
| pullback_uncertain_current_high_yes | mild_or_mixed | 6 | 6 | 4 | $+12.78 | $-1.56 | -12.2% | 0.90 |
| false_fade_reheat_current_no | balanced_tight | 6 | 5 | 4 | $+11.54 | $+14.09 | +122.1% | 0.90 |
| false_fade_reheat_current_no | mild_or_mixed | 6 | 6 | 5 | $+11.07 | $-6.32 | -57.1% | 0.80 |
| false_fade_reheat_current_no | cold_overforecast_noisy | 7 | 6 | 4 | $+10.67 | $+11.59 | +108.6% | 0.75 |
| cheap_stale_tail_current_no | hot_underforecast_clean | 5 | 5 | 4 | $+7.76 | $+12.99 | +167.4% | 1.15 |
| fresh_runway_current_no | hot_underforecast_noisy | 4 | 4 | 3 | $+6.35 | $+4.82 | +76.0% | 0.85 |
| false_fade_reheat_current_no | cold_overforecast_clean | 4 | 3 | 3 | $+4.54 | $-4.54 | -100.0% | 0.65 |
| pullback_uncertain_current_high_yes | cold_overforecast_clean | 2 | 2 | 1 | $+3.99 | $-0.62 | -15.5% | 1.10 |
| capped_d2_no | balanced_tight | 4 | 4 | 3 | $+3.77 | $-0.71 | -18.9% | 1.00 |
| cheap_stale_tail_current_no | balanced_tight | 1 | 1 | 1 | $+3.39 | $-3.39 | -100.0% | 0.90 |
| capped_d2_no | mild_or_mixed | 3 | 3 | 2 | $+3.02 | $+2.23 | +73.9% | 0.90 |
| pullback_uncertain_current_high_yes | cold_overforecast_noisy | 2 | 2 | 2 | $+3.01 | $+1.30 | +43.2% | 1.05 |
| capped_d2_no | cold_overforecast_noisy | 3 | 3 | 2 | $+2.82 | $+0.19 | +6.8% | 1.05 |
| fresh_runway_current_no | two_sided_noisy | 2 | 2 | 1 | $+2.53 | $+5.59 | +221.3% | 0.60 |
| cheap_stale_tail_current_no | cold_overforecast_noisy | 1 | 1 | 1 | $+1.93 | $+7.28 | +376.2% | 0.75 |
| capped_d2_no | cold_overforecast_clean | 2 | 2 | 1 | $+1.81 | $+0.89 | +49.3% | 1.15 |
| capped_d2_no | two_sided_noisy | 3 | 3 | 1 | $+1.70 | $-0.04 | -2.1% | 0.70 |
| cheap_stale_tail_current_no | mild_or_mixed | 1 | 1 | 1 | $+1.42 | $-1.42 | -100.0% | 0.80 |
| pullback_uncertain_current_high_yes | hot_underforecast_clean | 1 | 1 | 1 | $+1.30 | $-1.30 | -100.0% | 0.65 |
| false_fade_reheat_current_no | hot_underforecast_noisy | 1 | 1 | 1 | $+1.14 | $+0.62 | +53.8% | 0.85 |
| cheap_stale_tail_current_no | two_sided_noisy | 1 | 1 | 1 | $+0.58 | $-0.58 | -100.0% | 0.60 |
| false_fade_reheat_current_no | two_sided_noisy | 1 | 1 | 1 | $+0.46 | $+3.38 | +733.3% | 0.60 |

## Day Regime x Source-Bias Regime

| day_regime | source-bias regime | rows | dates | cities | cost | pnl | ROI | avg mult |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| day_marginal_runway | hot_underforecast_clean | 28 | 18 | 12 | $+69.29 | $+40.48 | +58.4% | 1.13 |
| day_marginal_runway | cold_overforecast_noisy | 33 | 21 | 7 | $+59.51 | $+51.99 | +87.4% | 0.76 |
| day_marginal_runway | mild_or_mixed | 30 | 24 | 6 | $+54.60 | $-27.27 | -49.9% | 0.81 |
| day_forecast_capped | hot_underforecast_clean | 51 | 29 | 13 | $+35.59 | $+0.15 | +0.4% | 0.65 |
| day_open_runway | cold_overforecast_noisy | 27 | 22 | 6 | $+32.62 | $+10.12 | +31.0% | 0.76 |
| day_marginal_runway | balanced_tight | 15 | 13 | 4 | $+28.75 | $+10.48 | +36.4% | 0.90 |
| day_marginal_runway | cold_overforecast_clean | 15 | 15 | 3 | $+25.30 | $+6.01 | +23.8% | 0.65 |
| day_open_runway | hot_underforecast_clean | 11 | 9 | 7 | $+20.94 | $+4.32 | +20.6% | 1.15 |
| day_open_runway | cold_overforecast_clean | 13 | 11 | 4 | $+15.29 | $-3.50 | -22.9% | 0.72 |
| day_open_runway | mild_or_mixed | 11 | 10 | 4 | $+15.22 | $-0.98 | -6.5% | 0.82 |
| day_forecast_capped | hot_underforecast_noisy | 18 | 16 | 3 | $+14.47 | $+0.69 | +4.8% | 0.75 |
| day_open_runway | balanced_tight | 7 | 6 | 3 | $+9.26 | $+1.61 | +17.4% | 0.90 |
| day_marginal_runway | hot_underforecast_noisy | 3 | 3 | 3 | $+5.11 | $+4.07 | +79.6% | 0.85 |
| day_forecast_capped | balanced_tight | 4 | 4 | 3 | $+3.77 | $-0.71 | -18.9% | 1.00 |
| day_forecast_capped | mild_or_mixed | 3 | 3 | 2 | $+3.02 | $+2.23 | +73.9% | 0.90 |
| day_forecast_capped | cold_overforecast_noisy | 3 | 3 | 2 | $+2.82 | $+0.19 | +6.8% | 1.05 |
| day_marginal_runway | two_sided_noisy | 2 | 2 | 1 | $+2.81 | $+8.85 | +315.4% | 0.60 |
| day_open_runway | hot_underforecast_noisy | 2 | 2 | 2 | $+2.38 | $+1.37 | +57.7% | 0.85 |
| day_forecast_capped | cold_overforecast_clean | 2 | 2 | 1 | $+1.81 | $+0.89 | +49.3% | 1.15 |
| day_forecast_capped | two_sided_noisy | 3 | 3 | 1 | $+1.70 | $-0.04 | -2.1% | 0.70 |
| day_open_runway | two_sided_noisy | 2 | 2 | 1 | $+0.76 | $-0.46 | -60.1% | 0.60 |

## Forward Since 2026-06-21

| expression | source-bias regime | rows | dates | cities | cost | pnl | ROI | avg mult |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_bracket_no | hot_underforecast_clean | 4 | 3 | 3 | $+8.02 | $-4.57 | -57.0% | 1.15 |
| current_bracket_no | cold_overforecast_noisy | 6 | 5 | 2 | $+7.97 | $+10.77 | +135.0% | 0.75 |
| current_bracket_no | mild_or_mixed | 4 | 2 | 3 | $+4.93 | $-4.93 | -100.0% | 0.80 |
| higher_no_d2 | hot_underforecast_clean | 7 | 4 | 6 | $+4.86 | $-1.43 | -29.4% | 0.65 |
| current_high_yes | cold_overforecast_clean | 2 | 2 | 1 | $+3.99 | $-0.62 | -15.5% | 1.10 |
| current_bracket_no | hot_underforecast_noisy | 2 | 2 | 1 | $+2.96 | $+2.70 | +91.3% | 0.85 |
| current_bracket_no | cold_overforecast_clean | 2 | 2 | 1 | $+2.61 | $+0.06 | +2.4% | 0.65 |
| current_bracket_no | balanced_tight | 1 | 1 | 1 | $+1.41 | $+0.87 | +61.3% | 0.90 |
| current_high_yes | hot_underforecast_clean | 1 | 1 | 1 | $+1.30 | $-1.30 | -100.0% | 0.65 |
| higher_no_d2 | balanced_tight | 1 | 1 | 1 | $+0.77 | $-0.77 | -100.0% | 1.00 |
| current_bracket_no | two_sided_noisy | 1 | 1 | 1 | $+0.18 | $+0.12 | +66.7% | 0.60 |

## Mechanism Rules

- For `current_bracket_no`, `hot_underforecast_clean` gets multiplier 1.15; `hot_underforecast_noisy` is reduced to 0.85; cold-overforecast and two-sided noisy are reduced more.
- For `higher_no_d2`, cold-overforecast gets 1.05-1.15; hot-underforecast is reduced to 0.65-0.75.
- For `current_high_yes`, cold-overforecast gets 1.05-1.10; hot-underforecast is reduced.
- These values are mechanism priors, not PnL-fitted thresholds. They should be shadow logged before any live sizing change.

## Data Notes

- Row-level city fit uses each row's actual forecast source: `open_meteo_live_ecmwf` -> ECMWF, `gfs_seamless/open_meteo_live_gfs` -> GFS.
- Current running policy is proxied by `router_weighted_cost_usd/router_weighted_pnl_usd` from `regime_routed_expression_router_v3`, matching the row-risk-soft design used by the live runner.
- This is replay on generated research rows, not CLOB fill-realized PnL.

## Artifacts

- Rows: `docs/analysis/2026-06/generated/strategy_city_fit_soft_sizing_v2/strategy_city_fit_soft_sizing_rows.csv`
- Policy summary: `docs/analysis/2026-06/generated/strategy_city_fit_soft_sizing_v2/policy_comparison.csv`
- Daily summary: `docs/analysis/2026-06/generated/strategy_city_fit_soft_sizing_v2/daily_policy_comparison.csv`
