# Regime-Routed V3 Route-Specific Timing WF V1

## Conclusion

Route-specific entry timing improves the diagnostic surface but does not validate a live strategy. The best static route/timing rows can look strong, but nested walk-forward remains selection-sensitive and the 6/21+ window is still fragile.

Verdict: `inconclusive_shadow_only`，live_ready=`False`。

## Data Snapshot

- Synced/rebuilt at: `2026-06-29T10:09:32+00:00`
- All routed hourly candidates: `366` rows, `2026-05-20`..`2026-06-28`
- Unique routed hourly rows with payoff: `361` rows through `2026-06-26`
- Candidate-policy settled rows used for menu evaluation: `14796` rows
- 6/27+ rows are kept only as unresolved candidate telemetry, not as PnL.

## Static Candidate Leaders

| candidate_id | rows | dates | cities | win_rate | avg_ask | avg_weight | pnl_usd | roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| route_price_disciplined_v1__all_v3__row_risk_soft_v1 | 165 | 37 | 34 | +47.3% | 0.431 | 0.404 | $+104.92 | +31.5% |
| route_price_disciplined_v1__all_v3__temp_context_row_soft_v1 | 165 | 37 | 34 | +47.3% | 0.431 | 0.404 | $+104.92 | +31.5% |
| route_price_disciplined_v1__no_pullback_yes__row_risk_soft_v1 | 163 | 37 | 34 | +46.6% | 0.430 | 0.407 | $+102.77 | +31.0% |
| route_price_disciplined_v1__no_pullback_yes__temp_context_row_soft_v1 | 163 | 37 | 34 | +46.6% | 0.430 | 0.407 | $+102.77 | +31.0% |
| route_price_disciplined_v1__core_no__row_risk_soft_v1 | 156 | 37 | 34 | +47.4% | 0.437 | 0.418 | $+97.47 | +29.9% |
| route_price_disciplined_v1__core_no__temp_context_row_soft_v1 | 156 | 37 | 34 | +47.4% | 0.437 | 0.418 | $+97.47 | +29.9% |
| route_price_disciplined_v1__fresh_only__row_risk_soft_v1 | 96 | 33 | 26 | +41.7% | 0.386 | 0.495 | $+70.94 | +29.9% |
| route_price_disciplined_v1__fresh_only__temp_context_row_soft_v1 | 96 | 33 | 26 | +41.7% | 0.386 | 0.495 | $+70.94 | +29.9% |
| route_price_disciplined_v1__fresh_capped__row_risk_soft_v1 | 133 | 34 | 31 | +45.1% | 0.429 | 0.450 | $+75.41 | +25.2% |
| route_price_disciplined_v1__fresh_capped__temp_context_row_soft_v1 | 133 | 34 | 31 | +45.1% | 0.429 | 0.450 | $+75.41 | +25.2% |
| route_confirmed_momentum_v1__core_no__temp_context_row_soft_v1 | 129 | 36 | 34 | +58.1% | 0.549 | 0.417 | $+67.38 | +25.1% |
| route_confirmed_momentum_v1__core_no__row_risk_soft_v1 | 129 | 36 | 34 | +58.1% | 0.549 | 0.417 | $+67.38 | +25.1% |
| route_confirmed_momentum_v1__no_pullback_yes__row_risk_soft_v1 | 132 | 36 | 34 | +56.8% | 0.543 | 0.410 | $+65.79 | +24.3% |
| route_confirmed_momentum_v1__no_pullback_yes__temp_context_row_soft_v1 | 132 | 36 | 34 | +56.8% | 0.543 | 0.410 | $+65.79 | +24.3% |
| route_confirmed_momentum_v1__all_v3__row_risk_soft_v1 | 138 | 36 | 34 | +57.2% | 0.554 | 0.397 | $+65.54 | +23.9% |
| route_confirmed_momentum_v1__all_v3__temp_context_row_soft_v1 | 138 | 36 | 34 | +57.2% | 0.554 | 0.397 | $+65.54 | +23.9% |

## Forward 6/21+ Static Leaders

| candidate_id | rows | dates | cities | win_rate | avg_ask | avg_weight | pnl_usd | roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| route_price_disciplined_v1__no_pullback_yes__row_risk_soft_v1 | 14 | 6 | 10 | +42.9% | 0.460 | 0.343 | $+1.46 | +6.1% |
| route_price_disciplined_v1__no_pullback_yes__temp_context_row_soft_v1 | 14 | 6 | 10 | +42.9% | 0.460 | 0.343 | $+1.46 | +6.1% |
| route_price_disciplined_v1__all_v3__row_risk_soft_v1 | 16 | 6 | 11 | +43.8% | 0.463 | 0.324 | $+1.18 | +4.6% |
| route_price_disciplined_v1__all_v3__temp_context_row_soft_v1 | 16 | 6 | 11 | +43.8% | 0.463 | 0.324 | $+1.18 | +4.6% |
| route_price_disciplined_v1__no_pullback_yes__full_size | 14 | 6 | 10 | +42.9% | 0.460 | 1.000 | $+0.43 | +0.6% |
| route_price_disciplined_v1__all_v3__full_size | 16 | 6 | 11 | +43.8% | 0.463 | 1.000 | $-0.80 | -1.0% |
| global_first_eligible__no_pullback_yes__row_risk_soft_v1 | 28 | 6 | 17 | +46.4% | 0.600 | 0.279 | $-2.29 | -5.9% |
| global_first_eligible__no_pullback_yes__temp_context_row_soft_v1 | 28 | 6 | 17 | +46.4% | 0.600 | 0.279 | $-2.29 | -5.9% |
| global_first_eligible__all_v3__row_risk_soft_v1 | 31 | 6 | 17 | +45.2% | 0.597 | 0.268 | $-3.14 | -7.6% |
| global_first_eligible__all_v3__temp_context_row_soft_v1 | 31 | 6 | 17 | +45.2% | 0.597 | 0.268 | $-3.14 | -7.6% |
| route_confirmed_momentum_v1__fresh_only__temp_context_row_soft_v1 | 6 | 3 | 5 | +50.0% | 0.585 | 0.436 | $-0.99 | -7.6% |
| route_confirmed_momentum_v1__fresh_only__row_risk_soft_v1 | 6 | 3 | 5 | +50.0% | 0.585 | 0.436 | $-0.99 | -7.6% |
| global_fixed_noon_priority__no_pullback_yes__row_risk_soft_v1 | 28 | 6 | 17 | +46.4% | 0.585 | 0.295 | $-4.50 | -10.9% |
| global_fixed_noon_priority__no_pullback_yes__temp_context_row_soft_v1 | 28 | 6 | 17 | +46.4% | 0.585 | 0.295 | $-4.50 | -10.9% |
| route_confirmed_momentum_v1__fresh_only__full_size | 6 | 3 | 5 | +50.0% | 0.585 | 1.000 | $-3.36 | -11.2% |
| global_fixed_noon_priority__all_v3__row_risk_soft_v1 | 31 | 6 | 17 | +45.2% | 0.583 | 0.282 | $-5.35 | -12.2% |

## Promising Shadow Candidate

Candidate: `route_price_disciplined_v1__no_pullback_yes__row_risk_soft_v1`.

- All settled ROI: +31.0% (date-bootstrap CI +3.6%..+59.9%)
- 6/21+ ROI: +6.1% (date-bootstrap CI -36.4%..+47.6%)
- This is a shadow candidate, not live approval: forward has only 6 settled dates and still includes a small -100% day.

| target_date | rows | wins | win_rate | avg_ask | avg_weight | cost_usd | pnl_usd | roi | route_mix |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-21 | 3 | 1 | +33.3% | 0.417 | 0.456 | $+6.83 | $-0.29 | -4.3% | capped_d2_no:2,fresh_runway_current_no:1 |
| 2026-06-22 | 2 | 0 | +0.0% | 0.429 | 0.060 | $+0.60 | $-0.60 | -100.0% | cheap_stale_tail_current_no:1,false_fade_reheat_current_no:1 |
| 2026-06-23 | 3 | 2 | +66.7% | 0.453 | 0.353 | $+5.30 | $+3.26 | +61.5% | capped_d2_no:1,cheap_stale_tail_current_no:1,fresh_runway_current_no:1 |
| 2026-06-24 | 1 | 1 | +100.0% | 0.572 | 0.350 | $+1.75 | $+1.31 | +74.8% | false_fade_reheat_current_no:1 |
| 2026-06-25 | 2 | 1 | +50.0% | 0.485 | 0.453 | $+4.53 | $+0.94 | +20.7% | fresh_runway_current_no:2 |
| 2026-06-26 | 3 | 1 | +33.3% | 0.476 | 0.333 | $+4.99 | $-3.15 | -63.2% | false_fade_reheat_current_no:1,fresh_runway_current_no:2 |

## Nested Walk-Forward

| menu | validation_selector | window | test_days | test_rows | pnl_usd | roi | avg_val_roi | chosen_entry_policies | chosen_route_sets | chosen_sizing |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| route_timing_full_only | expanding_prior | all_walk_forward | 24 | 78 | $+46.20 | +11.8% | +24.7% | route_clock_physics_v1:1;route_confirmed_momentum_v1:15;route_price_disciplined_v1:8 | all_v3:8;capped_only:3;core_no:7;fresh_only:6 | full_size:24 |
| route_timing_full_only | expanding_prior | forward_2026-06-21_plus | 6 | 17 | $-7.32 | -8.6% | +24.8% | route_confirmed_momentum_v1:1;route_price_disciplined_v1:5 | all_v3:5;core_no:1 | full_size:6 |
| route_timing_full_only | trailing_10_dates | all_walk_forward | 26 | 79 | $+101.66 | +25.7% | +39.9% | route_clock_physics_v1:6;route_confirmed_momentum_v1:11;route_price_disciplined_v1:9 | all_v3:5;capped_only:6;core_no:6;fresh_capped:1;fresh_only:8 | full_size:26 |
| route_timing_full_only | trailing_10_dates | forward_2026-06-21_plus | 6 | 11 | $-16.44 | -29.9% | +32.4% | route_confirmed_momentum_v1:1;route_price_disciplined_v1:5 | all_v3:3;fresh_only:2;no_pullback_yes:1 | full_size:6 |
| route_timing_no_daily_soft | expanding_prior | all_walk_forward | 25 | 89 | $+47.39 | +25.0% | +35.4% | route_confirmed_momentum_v1:13;route_price_disciplined_v1:12 | all_v3:10;capped_only:2;core_no:7;fresh_only:6 | row_risk_soft_v1:25 |
| route_timing_no_daily_soft | expanding_prior | forward_2026-06-21_plus | 5 | 12 | $-4.05 | -17.9% | +33.4% | route_price_disciplined_v1:5 | all_v3:4;fresh_only:1 | row_risk_soft_v1:5 |
| route_timing_no_daily_soft | trailing_10_dates | all_walk_forward | 26 | 76 | $+51.32 | +26.6% | +48.3% | route_clock_physics_v1:6;route_confirmed_momentum_v1:10;route_price_disciplined_v1:10 | all_v3:4;capped_only:6;core_no:5;fresh_capped:1;fresh_only:10 | full_size:4;row_risk_soft_v1:22 |
| route_timing_no_daily_soft | trailing_10_dates | forward_2026-06-21_plus | 5 | 9 | $-2.75 | -9.0% | +33.5% | route_price_disciplined_v1:5 | all_v3:2;fresh_only:2;no_pullback_yes:1 | full_size:2;row_risk_soft_v1:3 |
| route_timing_with_row_soft | expanding_prior | all_walk_forward | 25 | 89 | $+47.39 | +25.0% | +35.4% | route_confirmed_momentum_v1:13;route_price_disciplined_v1:12 | all_v3:10;capped_only:2;core_no:7;fresh_only:6 | row_risk_soft_v1:25 |
| route_timing_with_row_soft | expanding_prior | forward_2026-06-21_plus | 5 | 12 | $-4.05 | -17.9% | +33.4% | route_price_disciplined_v1:5 | all_v3:4;fresh_only:1 | row_risk_soft_v1:5 |
| route_timing_with_row_soft | trailing_10_dates | all_walk_forward | 26 | 76 | $+51.32 | +26.6% | +48.3% | route_clock_physics_v1:6;route_confirmed_momentum_v1:10;route_price_disciplined_v1:10 | all_v3:4;capped_only:6;core_no:5;fresh_capped:1;fresh_only:10 | full_size:4;row_risk_soft_v1:22 |
| route_timing_with_row_soft | trailing_10_dates | forward_2026-06-21_plus | 5 | 9 | $-2.75 | -9.0% | +33.5% | route_price_disciplined_v1:5 | all_v3:2;fresh_only:2;no_pullback_yes:1 | full_size:2;row_risk_soft_v1:3 |

## Recent Nested Decisions

| menu | validation_selector | test_date | candidate_id | val_roi | test_rows | test_pnl_usd | test_roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| route_timing_full_only | expanding_prior | 2026-06-21 | route_confirmed_momentum_v1__core_no__full_size | +26.3% | 5 | $-6.64 | -26.6% |
| route_timing_full_only | expanding_prior | 2026-06-22 | route_price_disciplined_v1__all_v3__full_size | +24.9% | 2 | $-10.00 | -100.0% |
| route_timing_full_only | expanding_prior | 2026-06-23 | route_price_disciplined_v1__all_v3__full_size | +23.3% | 3 | $+16.87 | +112.5% |
| route_timing_full_only | expanding_prior | 2026-06-24 | route_price_disciplined_v1__all_v3__full_size | +25.0% | 1 | $+3.74 | +74.8% |
| route_timing_full_only | expanding_prior | 2026-06-25 | route_price_disciplined_v1__all_v3__full_size | +25.3% | 3 | $-4.36 | -29.1% |
| route_timing_full_only | expanding_prior | 2026-06-26 | route_price_disciplined_v1__all_v3__full_size | +24.3% | 3 | $-6.94 | -46.2% |
| route_timing_full_only | trailing_10_dates | 2026-06-21 | route_price_disciplined_v1__fresh_only__full_size | +46.2% | 1 | $+6.11 | +122.2% |
| route_timing_full_only | trailing_10_dates | 2026-06-22 | route_confirmed_momentum_v1__all_v3__full_size | +43.2% | 2 | $-10.00 | -100.0% |
| route_timing_full_only | trailing_10_dates | 2026-06-23 | route_price_disciplined_v1__fresh_only__full_size | +47.1% | 1 | $-5.00 | -100.0% |
| route_timing_full_only | trailing_10_dates | 2026-06-24 | route_price_disciplined_v1__all_v3__full_size | +45.9% | 1 | $+3.74 | +74.8% |
| route_timing_full_only | trailing_10_dates | 2026-06-25 | route_price_disciplined_v1__all_v3__full_size | +16.8% | 3 | $-4.36 | -29.1% |
| route_timing_full_only | trailing_10_dates | 2026-06-26 | route_price_disciplined_v1__no_pullback_yes__full_size | -5.1% | 3 | $-6.94 | -46.2% |
| route_timing_no_daily_soft | expanding_prior | 2026-06-21 | route_price_disciplined_v1__all_v3__row_risk_soft_v1 | +33.8% | 4 | $+0.40 | +5.2% |
| route_timing_no_daily_soft | expanding_prior | 2026-06-23 | route_price_disciplined_v1__fresh_only__row_risk_soft_v1 | +33.7% | 1 | $-2.57 | -100.0% |
| route_timing_no_daily_soft | expanding_prior | 2026-06-24 | route_price_disciplined_v1__all_v3__row_risk_soft_v1 | +33.3% | 1 | $+1.31 | +74.8% |
| route_timing_no_daily_soft | expanding_prior | 2026-06-25 | route_price_disciplined_v1__all_v3__row_risk_soft_v1 | +33.5% | 3 | $-0.04 | -0.7% |
| route_timing_no_daily_soft | expanding_prior | 2026-06-26 | route_price_disciplined_v1__all_v3__row_risk_soft_v1 | +32.9% | 3 | $-3.15 | -63.2% |
| route_timing_no_daily_soft | trailing_10_dates | 2026-06-21 | route_price_disciplined_v1__fresh_only__row_risk_soft_v1 | +53.3% | 1 | $+3.60 | +122.2% |
| route_timing_no_daily_soft | trailing_10_dates | 2026-06-23 | route_price_disciplined_v1__fresh_only__row_risk_soft_v1 | +50.2% | 1 | $-2.57 | -100.0% |
| route_timing_no_daily_soft | trailing_10_dates | 2026-06-24 | route_price_disciplined_v1__all_v3__full_size | +45.9% | 1 | $+3.74 | +74.8% |
| route_timing_no_daily_soft | trailing_10_dates | 2026-06-25 | route_price_disciplined_v1__all_v3__full_size | +16.8% | 3 | $-4.36 | -29.1% |
| route_timing_no_daily_soft | trailing_10_dates | 2026-06-26 | route_price_disciplined_v1__no_pullback_yes__row_risk_soft_v1 | +1.2% | 3 | $-3.15 | -63.2% |
| route_timing_with_row_soft | expanding_prior | 2026-06-21 | route_price_disciplined_v1__all_v3__row_risk_soft_v1 | +33.8% | 4 | $+0.40 | +5.2% |
| route_timing_with_row_soft | expanding_prior | 2026-06-23 | route_price_disciplined_v1__fresh_only__row_risk_soft_v1 | +33.7% | 1 | $-2.57 | -100.0% |
| route_timing_with_row_soft | expanding_prior | 2026-06-24 | route_price_disciplined_v1__all_v3__row_risk_soft_v1 | +33.3% | 1 | $+1.31 | +74.8% |
| route_timing_with_row_soft | expanding_prior | 2026-06-25 | route_price_disciplined_v1__all_v3__row_risk_soft_v1 | +33.5% | 3 | $-0.04 | -0.7% |
| route_timing_with_row_soft | expanding_prior | 2026-06-26 | route_price_disciplined_v1__all_v3__row_risk_soft_v1 | +32.9% | 3 | $-3.15 | -63.2% |
| route_timing_with_row_soft | trailing_10_dates | 2026-06-21 | route_price_disciplined_v1__fresh_only__row_risk_soft_v1 | +53.3% | 1 | $+3.60 | +122.2% |
| route_timing_with_row_soft | trailing_10_dates | 2026-06-23 | route_price_disciplined_v1__fresh_only__row_risk_soft_v1 | +50.2% | 1 | $-2.57 | -100.0% |
| route_timing_with_row_soft | trailing_10_dates | 2026-06-24 | route_price_disciplined_v1__all_v3__full_size | +45.9% | 1 | $+3.74 | +74.8% |
| route_timing_with_row_soft | trailing_10_dates | 2026-06-25 | route_price_disciplined_v1__all_v3__full_size | +16.8% | 3 | $-4.36 | -29.1% |
| route_timing_with_row_soft | trailing_10_dates | 2026-06-26 | route_price_disciplined_v1__no_pullback_yes__row_risk_soft_v1 | +1.2% | 3 | $-3.15 | -63.2% |

## Frozen Failure Attribution

| router_route | train_rows | forward_rows | train_win_rate | forward_win_rate | train_avg_ask | forward_avg_ask | train_roi | forward_roi_actual | forward_roi_at_train_median_ask | price_effect_roi | train_forecast_error_mean_realized | forward_forecast_error_mean_realized |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fresh_runway_current_no | 140 | 14 | +47.9% | +42.9% | 0.507 | 0.572 | +3.3% | -0.268 | -0.221 | -4.7% | 0.922 | 1.398 |
| capped_d2_no | 76 | 8 | +63.2% | +50.0% | 0.614 | 0.655 | +5.1% | -0.250 | -0.212 | -3.8% | -1.732 | -1.528 |
| false_fade_reheat_current_no | 25 | 4 | +64.0% | +50.0% | 0.489 | 0.623 | +55.2% | -0.160 | 0.064 | -22.4% | 0.873 | 3.333 |
| cheap_stale_tail_current_no | 8 | 2 | +37.5% | +50.0% | 0.334 | 0.320 | +32.3% | 1.381 | 0.605 | +77.6% | 0.700 | 2.506 |

## Interpretation

- 这次不是用更多 hard gate 追坏例子，而是把 route 的入场时点变成可事前声明的候选，然后用 nested WF 选择。
- `price_effect_roi` 是把 forward 的同一批输赢按训练窗中位 ask 重新计价；它只解释价格变贵的贡献，不是 live 规则。
- `forecast_error_mean_realized` 是事后解释字段，不能用于下单，但它说明 6/21+ 的主要坏因是 forecast overestimate/regime shift，而不是单纯少等一小时。
- 若要继续推进，下一步应把 `fresh_runway_current_no` 和 `capped_d2_no` 分开建概率/EV 校准，而不是让一个 router 同时处理两个 payoff 机制。
