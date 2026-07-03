# Regime-Routed Expression Router V3 Live-Like Entry V1

## Conclusion

This replay removes the day-level `best_ask` selector.  `first_eligible` is the strictest causal policy; `fixed_noon_priority` is a pre-declared local-hour priority and does not select by price.

Verdict: `inconclusive_shadow_only`，live_ready=`False`。

## Policy Summary

| entry_policy | window | rows | dates | cities | win_rate | avg_ask | pnl_usd | roi | weighted_pnl_usd | weighted_roi | weighted_roi_ci_low | weighted_roi_ci_high | daily_roi_eq_minus100 | worst_day_pnl_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first_eligible | all | 282 | 38 | 35 | +53.2% | 0.552 | $+50.01 | +3.5% | $+79.75 | +16.9% | +0.2% | +33.2% | 1 | $-25.39 |
| first_eligible | forward_2026-06-21_plus | 31 | 6 | 17 | +45.2% | 0.597 | $-24.37 | -15.7% | $-0.59 | -1.3% | -39.9% | +32.3% | 1 | $-15.00 |
| first_eligible | train_to_2026_06_20 | 251 | 32 | 35 | +54.2% | 0.546 | $+74.38 | +5.9% | $+80.33 | +18.8% | +0.5% | +35.5% | 0 | $-25.39 |
| fixed_noon_priority | all | 282 | 38 | 35 | +53.2% | 0.545 | $+65.77 | +4.7% | $+80.79 | +16.9% | -1.0% | +34.0% | 1 | $-25.39 |
| fixed_noon_priority | forward_2026-06-21_plus | 31 | 6 | 17 | +45.2% | 0.583 | $-24.37 | -15.7% | $-2.13 | -4.7% | -40.4% | +29.7% | 1 | $-15.00 |
| fixed_noon_priority | train_to_2026_06_20 | 251 | 32 | 35 | +54.2% | 0.540 | $+90.14 | +7.2% | $+82.92 | +19.2% | -0.4% | +37.2% | 0 | $-25.39 |

## Recent Daily

| entry_policy | target_date | rows | wins | win_rate | avg_ask | pnl_usd | roi | route_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first_eligible | 2026-06-21 | 9 | 4 | +44.4% | 0.629 | $-10.63 | -23.6% | capped_d2_no:4,fresh_runway_current_no:4,pullback_uncertain_current_high_yes:1 | Beijing,CapeTown,Denver,Singapore,TelAviv |
| first_eligible | 2026-06-22 | 3 | 0 | +0.0% | 0.579 | $-15.00 | -100.0% | capped_d2_no:1,cheap_stale_tail_current_no:1,false_fade_reheat_current_no:1 | NYC,Shanghai,Tokyo |
| first_eligible | 2026-06-23 | 6 | 3 | +50.0% | 0.552 | $+9.94 | +33.1% | capped_d2_no:1,cheap_stale_tail_current_no:1,false_fade_reheat_current_no:1,fresh_runway_current_no:3 | Chongqing,Jeddah,Karachi |
| first_eligible | 2026-06-24 | 3 | 1 | +33.3% | 0.661 | $-6.26 | -41.7% | capped_d2_no:1,false_fade_reheat_current_no:1,pullback_uncertain_current_high_yes:1 | Ankara,Jeddah |
| first_eligible | 2026-06-25 | 5 | 3 | +60.0% | 0.568 | $-1.74 | -7.0% | capped_d2_no:1,fresh_runway_current_no:3,pullback_uncertain_current_high_yes:1 | Karachi,Shanghai |
| first_eligible | 2026-06-26 | 5 | 3 | +60.0% | 0.594 | $-0.67 | -2.7% | cheap_stale_tail_current_no:1,false_fade_reheat_current_no:1,fresh_runway_current_no:3 | Chongqing,TelAviv |
| fixed_noon_priority | 2026-06-21 | 9 | 4 | +44.4% | 0.616 | $-10.63 | -23.6% | capped_d2_no:4,fresh_runway_current_no:4,pullback_uncertain_current_high_yes:1 | Beijing,CapeTown,Denver,Singapore,TelAviv |
| fixed_noon_priority | 2026-06-22 | 3 | 0 | +0.0% | 0.579 | $-15.00 | -100.0% | capped_d2_no:1,cheap_stale_tail_current_no:1,false_fade_reheat_current_no:1 | NYC,Shanghai,Tokyo |
| fixed_noon_priority | 2026-06-23 | 6 | 3 | +50.0% | 0.552 | $+9.94 | +33.1% | capped_d2_no:1,cheap_stale_tail_current_no:1,false_fade_reheat_current_no:1,fresh_runway_current_no:3 | Chongqing,Jeddah,Karachi |
| fixed_noon_priority | 2026-06-24 | 3 | 1 | +33.3% | 0.661 | $-6.26 | -41.7% | capped_d2_no:1,false_fade_reheat_current_no:1,pullback_uncertain_current_high_yes:1 | Ankara,Jeddah |
| fixed_noon_priority | 2026-06-25 | 5 | 3 | +60.0% | 0.568 | $-1.74 | -7.0% | capped_d2_no:1,fresh_runway_current_no:3,pullback_uncertain_current_high_yes:1 | Karachi,Shanghai |
| fixed_noon_priority | 2026-06-26 | 5 | 3 | +60.0% | 0.532 | $-0.67 | -2.7% | false_fade_reheat_current_no:1,fresh_runway_current_no:4 | Chongqing,TelAviv |

## Route Summary

| entry_policy | slice | window | rows | dates | win_rate | avg_ask | pnl_usd | roi | weighted_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first_eligible | fresh_runway_current_no | forward_2026_06_21_plus | 13 | 4 | +46.2% | 0.594 | $-13.77 | -21.2% | -17.1% |
| first_eligible | capped_d2_no | forward_2026_06_21_plus | 8 | 5 | +50.0% | 0.670 | $-9.99 | -25.0% | -23.8% |
| first_eligible | false_fade_reheat_current_no | forward_2026_06_21_plus | 4 | 4 | +50.0% | 0.623 | $-3.19 | -16.0% | +11.3% |
| first_eligible | cheap_stale_tail_current_no | forward_2026_06_21_plus | 3 | 3 | +33.3% | 0.413 | $+8.81 | +58.7% | +110.0% |
| first_eligible | pullback_uncertain_current_high_yes | forward_2026_06_21_plus | 3 | 3 | +33.3% | 0.563 | $-6.23 | -41.5% | -45.7% |
| first_eligible | fresh_runway_current_no | train_to_2026_06_20 | 138 | 31 | +46.4% | 0.513 | $-1.36 | -0.2% | +14.0% |
| first_eligible | capped_d2_no | train_to_2026_06_20 | 76 | 29 | +63.2% | 0.613 | $+19.25 | +5.1% | +8.3% |
| first_eligible | false_fade_reheat_current_no | train_to_2026_06_20 | 26 | 14 | +69.2% | 0.523 | $+50.78 | +39.1% | +64.7% |
| first_eligible | pullback_uncertain_current_high_yes | train_to_2026_06_20 | 6 | 6 | +66.7% | 0.725 | $+0.50 | +1.7% | -11.7% |
| first_eligible | cheap_stale_tail_current_no | train_to_2026_06_20 | 5 | 5 | +40.0% | 0.371 | $+5.20 | +20.8% | +26.5% |
| fixed_noon_priority | fresh_runway_current_no | forward_2026_06_21_plus | 14 | 4 | +42.9% | 0.572 | $-18.77 | -26.8% | -26.5% |
| fixed_noon_priority | capped_d2_no | forward_2026_06_21_plus | 8 | 5 | +50.0% | 0.655 | $-9.99 | -25.0% | -27.0% |
| fixed_noon_priority | false_fade_reheat_current_no | forward_2026_06_21_plus | 4 | 4 | +50.0% | 0.623 | $-3.19 | -16.0% | +11.8% |
| fixed_noon_priority | pullback_uncertain_current_high_yes | forward_2026_06_21_plus | 3 | 3 | +33.3% | 0.563 | $-6.23 | -41.5% | -45.5% |
| fixed_noon_priority | cheap_stale_tail_current_no | forward_2026_06_21_plus | 2 | 2 | +50.0% | 0.320 | $+13.81 | +138.1% | +177.2% |
| fixed_noon_priority | fresh_runway_current_no | train_to_2026_06_20 | 138 | 31 | +47.8% | 0.507 | $+23.75 | +3.4% | +17.2% |
| fixed_noon_priority | capped_d2_no | train_to_2026_06_20 | 76 | 29 | +63.2% | 0.614 | $+19.50 | +5.1% | +8.5% |
| fixed_noon_priority | false_fade_reheat_current_no | train_to_2026_06_20 | 24 | 16 | +62.5% | 0.504 | $+32.30 | +26.9% | +47.8% |
| fixed_noon_priority | cheap_stale_tail_current_no | train_to_2026_06_20 | 7 | 7 | +42.9% | 0.315 | $+17.93 | +51.2% | +43.5% |
| fixed_noon_priority | pullback_uncertain_current_high_yes | train_to_2026_06_20 | 6 | 6 | +66.7% | 0.775 | $-3.34 | -11.1% | -22.6% |

## Data Notes

- Source atlas: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`
- Settled performance range: `2026-05-20`..`2026-06-26`
- 6/27..6/28 remain unresolved for ROI in the current fact layer.
