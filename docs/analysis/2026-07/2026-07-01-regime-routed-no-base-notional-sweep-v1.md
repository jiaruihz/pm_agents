# Regime-Routed NO Base Notional Sweep V1

## Conclusion

Increasing `base_N` should not change the strategy denominator. The correct fix is to keep a fixed signal-quality gate, `city_bias_soft_weight / ask >= 1.0`, and leave the 5-share rule as execution plumbing.

Verdict: `decouple_signal_quality_from_base_notional_with_weight_to_ask_ratio_gate`.

Recommended live default remains `base_N=5`. If notional is raised later, keep the fixed weight/price quality gate so lower-confidence rows do not enter only because the order size grew.

## Coverage

- Frozen/live-like replay: `2026-05-20`..`2026-06-28`, rows `165`.
- Historical best-ask diagnostic: `2026-05-20`..`2026-06-26`, rows `283`.
- Forward split: `>= 2026-06-21`.

## Frozen Live-Like: Daily Cap Equals Base N

| base_notional_usd | daily_cap_usd | rows | dates | cities | win_rate | avg_weight | avg_cost_usd | cost_usd | pnl_usd | roi | daily_negative_100pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| $+5.00 | $+5.00 | 42.000 | 31.000 | 22.000 | +47.6% | 0.477 | $+2.39 | $+100.20 | $+29.72 | +29.7% | 13.000 |
| $+6.00 | $+6.00 | 46.000 | 33.000 | 20.000 | +45.7% | 0.455 | $+2.73 | $+125.58 | $+30.56 | +24.3% | 14.000 |
| $+7.50 | $+7.50 | 53.000 | 34.000 | 22.000 | +43.4% | 0.434 | $+3.26 | $+172.71 | $+33.95 | +19.7% | 14.000 |
| $+8.00 | $+8.00 | 54.000 | 34.000 | 23.000 | +44.4% | 0.432 | $+3.46 | $+186.69 | $+38.78 | +20.8% | 13.000 |
| $+10.00 | $+10.00 | 62.000 | 36.000 | 24.000 | +48.4% | 0.395 | $+3.95 | $+245.19 | $+55.76 | +22.7% | 11.000 |
| $+12.00 | $+12.00 | 70.000 | 36.000 | 24.000 | +44.3% | 0.370 | $+4.44 | $+310.75 | $+40.49 | +13.0% | 12.000 |
| $+15.00 | $+15.00 | 83.000 | 36.000 | 28.000 | +45.8% | 0.337 | $+5.06 | $+419.66 | $+35.28 | +8.4% | 10.000 |

## Frozen Forward: Daily Cap Equals Base N

| base_notional_usd | daily_cap_usd | rows | dates | cities | win_rate | avg_weight | avg_cost_usd | cost_usd | pnl_usd | roi | daily_negative_100pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| $+5.00 | $+5.00 | 5.000 | 4.000 | 5.000 | +60.0% | 0.432 | $+2.16 | $+10.79 | $+7.50 | +69.5% | 1.000 |
| $+6.00 | $+6.00 | 6.000 | 4.000 | 5.000 | +50.0% | 0.445 | $+2.67 | $+16.04 | $+5.92 | +36.9% | 1.000 |
| $+7.50 | $+7.50 | 7.000 | 4.000 | 5.000 | +42.9% | 0.438 | $+3.28 | $+22.98 | $+4.46 | +19.4% | 1.000 |
| $+8.00 | $+8.00 | 7.000 | 4.000 | 5.000 | +42.9% | 0.438 | $+3.50 | $+24.52 | $+4.76 | +19.4% | 1.000 |
| $+10.00 | $+10.00 | 9.000 | 5.000 | 6.000 | +44.4% | 0.410 | $+4.10 | $+36.86 | $+5.85 | +15.9% | 1.000 |
| $+12.00 | $+12.00 | 9.000 | 5.000 | 7.000 | +33.3% | 0.368 | $+4.41 | $+39.72 | $-4.17 | -10.5% | 2.000 |
| $+15.00 | $+15.00 | 10.000 | 5.000 | 8.000 | +40.0% | 0.354 | $+5.31 | $+53.07 | $-3.12 | -5.9% | 1.000 |

## Frozen Live-Like With Fixed Weight/Price Quality Gate

| base_notional_usd | daily_cap_usd | rows | dates | cities | win_rate | avg_weight | avg_cost_usd | cost_usd | pnl_usd | roi | daily_negative_100pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| $+5.00 | $+5.00 | 42.000 | 31.000 | 22.000 | +47.6% | 0.477 | $+2.39 | $+100.20 | $+29.72 | +29.7% | 13.000 |
| $+6.00 | $+6.00 | 42.000 | 31.000 | 22.000 | +47.6% | 0.477 | $+2.86 | $+120.24 | $+35.67 | +29.7% | 13.000 |
| $+7.50 | $+7.50 | 42.000 | 31.000 | 22.000 | +47.6% | 0.477 | $+3.58 | $+150.30 | $+44.59 | +29.7% | 13.000 |
| $+8.00 | $+8.00 | 42.000 | 31.000 | 22.000 | +47.6% | 0.477 | $+3.82 | $+160.32 | $+47.56 | +29.7% | 13.000 |
| $+10.00 | $+10.00 | 42.000 | 31.000 | 22.000 | +47.6% | 0.477 | $+4.77 | $+200.40 | $+59.45 | +29.7% | 13.000 |
| $+12.00 | $+12.00 | 42.000 | 31.000 | 22.000 | +47.6% | 0.477 | $+5.73 | $+240.48 | $+71.34 | +29.7% | 13.000 |
| $+15.00 | $+15.00 | 42.000 | 31.000 | 22.000 | +47.6% | 0.477 | $+7.16 | $+300.61 | $+89.17 | +29.7% | 13.000 |

## Frozen Forward With Fixed Weight/Price Quality Gate

| base_notional_usd | daily_cap_usd | rows | dates | cities | win_rate | avg_weight | avg_cost_usd | cost_usd | pnl_usd | roi | daily_negative_100pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| $+5.00 | $+5.00 | 5.000 | 4.000 | 5.000 | +60.0% | 0.432 | $+2.16 | $+10.79 | $+7.50 | +69.5% | 1.000 |
| $+6.00 | $+6.00 | 5.000 | 4.000 | 5.000 | +60.0% | 0.432 | $+2.59 | $+12.95 | $+9.00 | +69.5% | 1.000 |
| $+7.50 | $+7.50 | 5.000 | 4.000 | 5.000 | +60.0% | 0.432 | $+3.24 | $+16.19 | $+11.25 | +69.5% | 1.000 |
| $+8.00 | $+8.00 | 5.000 | 4.000 | 5.000 | +60.0% | 0.432 | $+3.45 | $+17.27 | $+12.01 | +69.5% | 1.000 |
| $+10.00 | $+10.00 | 5.000 | 4.000 | 5.000 | +60.0% | 0.432 | $+4.32 | $+21.58 | $+15.01 | +69.5% | 1.000 |
| $+12.00 | $+12.00 | 5.000 | 4.000 | 5.000 | +60.0% | 0.432 | $+5.18 | $+25.90 | $+18.01 | +69.5% | 1.000 |
| $+15.00 | $+15.00 | 5.000 | 4.000 | 5.000 | +60.0% | 0.432 | $+6.48 | $+32.38 | $+22.51 | +69.5% | 1.000 |

## Incremental Rows Versus Base N = 5

| base_notional_usd | window | incremental_rows_vs_base5 | dates | cities | win_rate | cost_usd | pnl_usd | roi | route_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| $+6.00 | all | 11 | 10 | 10 | +36.4% | $+27.13 | $-4.27 | -15.7% | capped_d2_no:2,fresh_runway_current_no:9 | Ankara,Busan,Chongqing,Istanbul,Lucknow,Manila,TelAviv |
| $+6.00 | forward_2026-06-21_plus | 1 | 1 | 1 | +0.0% | $+3.09 | $-3.09 | -100.0% | fresh_runway_current_no:1 | Chongqing |
| $+7.50 | all | 24 | 18 | 18 | +45.8% | $+66.72 | $+1.63 | +2.4% | capped_d2_no:4,cheap_stale_tail_current_no:1,false_fade_reheat_current_no:2,fresh_runway_current_no:17 | Amsterdam,Ankara,Ankara,Busan,Chengdu,Chongqing,Istanbul,Karachi,Lucknow,Manila,TelAviv,TelAviv,Tokyo |
| $+7.50 | forward_2026-06-21_plus | 2 | 2 | 2 | +0.0% | $+6.80 | $-6.80 | -100.0% | fresh_runway_current_no:2 | Chongqing,Karachi |
| $+8.00 | all | 26 | 18 | 19 | +46.2% | $+76.09 | $+1.84 | +2.4% | capped_d2_no:6,cheap_stale_tail_current_no:1,false_fade_reheat_current_no:2,fresh_runway_current_no:17 | Amsterdam,Ankara,Ankara,Busan,Chengdu,Chongqing,Istanbul,Karachi,Lucknow,Manila,Munich,TelAviv,TelAviv,Tokyo |
| $+8.00 | forward_2026-06-21_plus | 2 | 2 | 2 | +0.0% | $+7.25 | $-7.25 | -100.0% | fresh_runway_current_no:2 | Chongqing,Karachi |
| $+10.00 | all | 39 | 26 | 21 | +53.8% | $+131.81 | $+17.32 | +13.1% | capped_d2_no:9,cheap_stale_tail_current_no:1,false_fade_reheat_current_no:9,fresh_runway_current_no:20 | Amsterdam,Ankara,Ankara,Atlanta,Busan,Chengdu,Chongqing,Helsinki,Istanbul,Karachi,Lucknow,Manila,Munich,TelAviv,TelAviv,TelAviv,TelAviv,Tokyo |
| $+10.00 | forward_2026-06-21_plus | 4 | 4 | 3 | +25.0% | $+15.27 | $-9.16 | -59.9% | false_fade_reheat_current_no:1,fresh_runway_current_no:3 | Chongqing,Karachi,TelAviv |
| $+12.00 | all | 50 | 29 | 24 | +46.0% | $+189.47 | $+0.06 | +0.0% | capped_d2_no:15,cheap_stale_tail_current_no:2,false_fade_reheat_current_no:10,fresh_runway_current_no:23 | Amsterdam,Ankara,Ankara,Atlanta,BuenosAires,Busan,Busan,CapeTown,Chengdu,Chongqing,Helsinki,Istanbul,Karachi,Karachi,Lucknow,Manila,Munich,Munich,Munich,SaoPaulo,Singapore,TelAviv,TelAviv,TelAviv,TelAviv,TelAviv,Tokyo |
| $+12.00 | forward_2026-06-21_plus | 5 | 5 | 4 | +20.0% | $+20.89 | $-13.55 | -64.8% | capped_d2_no:1,false_fade_reheat_current_no:1,fresh_runway_current_no:3 | Chongqing,Karachi,Singapore,TelAviv |
| $+15.00 | all | 65 | 31 | 28 | +49.2% | $+283.44 | $+3.47 | +1.2% | capped_d2_no:24,cheap_stale_tail_current_no:3,false_fade_reheat_current_no:15,fresh_runway_current_no:23 | Amsterdam,Ankara,Ankara,Atlanta,BuenosAires,Busan,Busan,CapeTown,Chengdu,Chongqing,Helsinki,Istanbul,Karachi,Karachi,Karachi,Karachi,Lucknow,Manila,Munich,Munich,Munich,SaoPaulo,Shanghai,Singapore,Taipei,TelAviv,TelAviv,TelAviv,TelAviv,TelAviv,TelAviv,Tokyo,Wuhan |
| $+15.00 | forward_2026-06-21_plus | 6 | 5 | 5 | +33.3% | $+29.53 | $-14.84 | -50.2% | capped_d2_no:1,false_fade_reheat_current_no:2,fresh_runway_current_no:3 | Chongqing,Karachi,Singapore,TelAviv |

## Historical Diagnostic Cross-Check

| base_notional_usd | daily_cap_usd | rows | dates | cities | win_rate | avg_weight | avg_cost_usd | cost_usd | pnl_usd | roi | daily_negative_100pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| $+5.00 | $+5.00 | 44.000 | 32.000 | 23.000 | +47.7% | 0.481 | $+2.41 | $+105.82 | $+38.59 | +36.5% | 13.000 |
| $+6.00 | $+6.00 | 48.000 | 34.000 | 21.000 | +41.7% | 0.461 | $+2.77 | $+132.77 | $+25.20 | +19.0% | 16.000 |
| $+7.50 | $+7.50 | 58.000 | 35.000 | 24.000 | +41.4% | 0.434 | $+3.25 | $+188.68 | $+31.47 | +16.7% | 14.000 |
| $+8.00 | $+8.00 | 60.000 | 35.000 | 24.000 | +43.3% | 0.429 | $+3.44 | $+206.14 | $+33.87 | +16.4% | 12.000 |
| $+10.00 | $+10.00 | 68.000 | 37.000 | 25.000 | +52.9% | 0.402 | $+4.02 | $+273.45 | $+59.16 | +21.6% | 10.000 |
| $+12.00 | $+12.00 | 77.000 | 37.000 | 25.000 | +51.9% | 0.376 | $+4.52 | $+347.68 | $+55.43 | +15.9% | 9.000 |
| $+15.00 | $+15.00 | 92.000 | 37.000 | 27.000 | +52.2% | 0.341 | $+5.11 | $+470.47 | $+56.51 | +12.0% | 7.000 |

## Required Base N To Clear 5 Shares

| evidence_layer | quantile | required_base_notional_usd |
| --- | --- | --- |
| frozen_live_like_route_price | 0.250 | $+3.58 |
| frozen_live_like_route_price | 0.500 | $+5.31 |
| frozen_live_like_route_price | 0.750 | $+11.72 |
| frozen_live_like_route_price | 0.900 | $+16.09 |
| frozen_live_like_route_price | 0.950 | $+19.62 |
| frozen_live_like_route_price | 1.000 | $+47.55 |
| historical_best_ask_diagnostic | 0.250 | $+4.87 |
| historical_best_ask_diagnostic | 0.500 | $+9.69 |
| historical_best_ask_diagnostic | 0.750 | $+16.09 |
| historical_best_ask_diagnostic | 0.900 | $+25.49 |
| historical_best_ask_diagnostic | 0.950 | $+29.34 |
| historical_best_ask_diagnostic | 1.000 | $+47.55 |

## Interpretation

- The old executable definition used `base_N * weight / ask >= 5`; raising `base_N` mechanically lowers the required weight/price quality.
- The fixed quality gate uses `weight / ask >= 1.0`, which reproduces the current `base_N=5` quality threshold and remains stable under future notional changes.
- With that gate, raising `base_N` scales dollars on the same quality set instead of admitting weak rows.
- The 5-share rule should remain only as execution plumbing for exchange/order-size constraints and top-of-book depth.

## Files

- Summary JSON: `docs/analysis/2026-07/generated/regime_routed_no_base_notional_sweep_v1/summary.json`
- Sweep CSV: `docs/analysis/2026-07/generated/regime_routed_no_base_notional_sweep_v1/base_notional_sweep.csv`
- Incremental CSV: `docs/analysis/2026-07/generated/regime_routed_no_base_notional_sweep_v1/incremental_rows_vs_base5.csv`
- Required base N distribution: `docs/analysis/2026-07/generated/regime_routed_no_base_notional_sweep_v1/required_base_notional_distribution.csv`
