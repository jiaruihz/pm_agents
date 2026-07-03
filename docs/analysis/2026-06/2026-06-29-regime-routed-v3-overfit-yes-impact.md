# Regime-Routed V3 Overfit And YES Impact Review

## Conclusion

Overfit risk is real, but the latest evidence is not a simple rejection.  The price-discipline candidate is mechanically sensible and the local cap neighborhood survives a small nested walk-forward.  The problem is that support is thin and the broader strategy menu was built after many manual choices, so static ROI is still too optimistic for live approval.

Verdict: `inconclusive_shadow_only`，live_ready=`False`。

## Data Snapshot

- Source: `docs/analysis/2026-06/generated/regime_routed_v3_route_specific_timing_wf_v1/selected_candidate_rows.csv`
- Candidate menu rows: `14973`
- Settled unique routed rows: `361`
- Forward starts: `2026-06-21`; settlement-backed ROI only through `2026-06-26`.

## Static Selection Risk

- Candidate menu size after support filter: `90` candidates.
- Base candidate static all-ROI rank: `3.0` / `90`.
- Base candidate forward-ROI rank: `1.0` / `90`.

| candidate_id | all_rows | all_dates | all_roi | forward_rows | forward_dates | forward_roi | avg_ask | avg_weight |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| route_price_disciplined_v1__all_v3__temp_context_row_soft_v1 | 165 | 37 | +31.5% | 16 | 6 | +4.6% | 0.431 | 0.404 |
| route_price_disciplined_v1__all_v3__row_risk_soft_v1 | 165 | 37 | +31.5% | 16 | 6 | +4.6% | 0.431 | 0.404 |
| route_price_disciplined_v1__no_pullback_yes__temp_context_row_soft_v1 | 163 | 37 | +31.0% | 14 | 6 | +6.1% | 0.430 | 0.407 |
| route_price_disciplined_v1__no_pullback_yes__row_risk_soft_v1 | 163 | 37 | +31.0% | 14 | 6 | +6.1% | 0.430 | 0.407 |
| route_price_disciplined_v1__core_no__row_risk_soft_v1 | 156 | 37 | +29.9% | 12 | 6 | -14.5% | 0.437 | 0.418 |
| route_price_disciplined_v1__core_no__temp_context_row_soft_v1 | 156 | 37 | +29.9% | 12 | 6 | -14.5% | 0.437 | 0.418 |
| route_price_disciplined_v1__fresh_only__temp_context_row_soft_v1 | 96 | 33 | +29.9% | 6 | 4 | -13.6% | 0.386 | 0.495 |
| route_price_disciplined_v1__fresh_only__row_risk_soft_v1 | 96 | 33 | +29.9% | 6 | 4 | -13.6% | 0.386 | 0.495 |
| route_price_disciplined_v1__fresh_capped__temp_context_row_soft_v1 | 133 | 34 | +25.2% | 9 | 4 | -25.6% | 0.429 | 0.450 |
| route_price_disciplined_v1__fresh_capped__row_risk_soft_v1 | 133 | 34 | +25.2% | 9 | 4 | -25.6% | 0.429 | 0.450 |
| route_confirmed_momentum_v1__core_no__temp_context_row_soft_v1 | 129 | 36 | +25.1% | 10 | 5 | -17.6% | 0.549 | 0.417 |
| route_confirmed_momentum_v1__core_no__row_risk_soft_v1 | 129 | 36 | +25.1% | 10 | 5 | -17.6% | 0.549 | 0.417 |

## Price-Cap Grid

Base caps rank by all ROI: `28` / `81`; rank by forward ROI: `28` / `81`.

| cap_id | all_rows | all_dates | all_roi | train_roi | forward_rows | forward_dates | forward_roi | avg_ask | avg_weight | is_base_caps |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fresh0.50_capped0.57_false0.65_cheap0.35 | 135 | 36 | +34.8% | +35.5% | 11 | 6 | +24.2% | 0.400 | 0.420 | False |
| fresh0.50_capped0.57_false0.65_cheap0.40 | 135 | 36 | +34.8% | +35.5% | 11 | 6 | +24.2% | 0.400 | 0.420 | False |
| fresh0.50_capped0.57_false0.70_cheap0.35 | 141 | 37 | +34.6% | +35.8% | 12 | 6 | +18.7% | 0.412 | 0.411 | False |
| fresh0.50_capped0.57_false0.70_cheap0.40 | 141 | 37 | +34.6% | +35.8% | 12 | 6 | +18.7% | 0.412 | 0.411 | False |
| fresh0.50_capped0.57_false0.65_cheap0.45 | 135 | 36 | +34.5% | +35.5% | 11 | 6 | +20.5% | 0.402 | 0.421 | False |
| fresh0.50_capped0.57_false0.70_cheap0.45 | 141 | 37 | +34.4% | +35.8% | 12 | 6 | +15.3% | 0.413 | 0.412 | False |
| fresh0.50_capped0.57_false0.60_cheap0.35 | 130 | 36 | +33.8% | +34.4% | 9 | 6 | +24.2% | 0.389 | 0.430 | False |
| fresh0.50_capped0.57_false0.60_cheap0.40 | 130 | 36 | +33.8% | +34.4% | 9 | 6 | +24.2% | 0.389 | 0.430 | False |
| fresh0.50_capped0.57_false0.60_cheap0.45 | 130 | 36 | +33.5% | +34.4% | 9 | 6 | +20.2% | 0.390 | 0.431 | False |
| fresh0.55_capped0.57_false0.65_cheap0.40 | 148 | 37 | +32.8% | +35.2% | 13 | 6 | +2.6% | 0.412 | 0.420 | False |
| fresh0.55_capped0.57_false0.65_cheap0.35 | 148 | 37 | +32.8% | +35.2% | 13 | 6 | +2.6% | 0.412 | 0.420 | False |
| fresh0.55_capped0.57_false0.70_cheap0.35 | 154 | 37 | +32.7% | +35.4% | 14 | 6 | -1.2% | 0.423 | 0.411 | False |

## Cap-Grid Nested WF

| window | test_days | test_rows | pnl_usd | roi | ci_low | ci_high | chosen_caps |
| --- | --- | --- | --- | --- | --- | --- | --- |
| all_walk_forward | 27 | 121 | $+77.21 | +30.0% | -5.5% | +62.3% | fresh0.50_capped0.57_false0.60_cheap0.35:2;fresh0.50_capped0.57_false0.65_cheap0.35:2;fresh0.50_capped0.57_false0.70_cheap0.35:2;fresh0.55_capped0.57_false0.70_cheap0.35:20;fresh0.55_capped0.67_false0.70_cheap0.35:1 |
| forward_2026_06_21_plus | 6 | 12 | $+3.65 | +18.7% | -24.3% | +89.0% | fresh0.50_capped0.57_false0.65_cheap0.35:3;fresh0.50_capped0.57_false0.70_cheap0.35:3 |

## YES Impact

Adding pullback YES changes all-ROI by +0.5%, and 6/21+ forward ROI by -1.5%.

| slice | rows | dates | cities | win_rate | avg_ask | avg_weight | pnl_usd | roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| no_pullback_all | 163 | 37 | 34 | +46.6% | 0.430 | 0.407 | $+102.77 | +31.0% |
| all_v3_with_yes_all | 165 | 37 | 34 | +47.3% | 0.431 | 0.404 | $+104.92 | +31.5% |
| no_pullback_forward | 14 | 6 | 10 | +42.9% | 0.460 | 0.343 | $+1.46 | +6.1% |
| all_v3_with_yes_forward | 16 | 6 | 11 | +43.8% | 0.463 | 0.324 | $+1.18 | +4.6% |

YES route difference rows:

| target_date | city | effect | yes_route | yes_payoff | yes_ask | yes_pnl | no_route | no_payoff | no_ask | no_pnl | pnl_delta_vs_no |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-02 | Amsterdam | yes_replaced_no | pullback_uncertain_current_high_yes | 1.000 | 0.480 | $+1.11 | cheap_stale_tail_current_no | 0.000 | 0.330 | $-1.32 | 2.433 |
| 2026-06-21 | Jeddah | yes_added_city_day | pullback_uncertain_current_high_yes | 1.000 | 0.570 | $+0.69 | NA | NA | NA | NA | 0.694 |
| 2026-06-25 | Shanghai | yes_added_city_day | pullback_uncertain_current_high_yes | 0.000 | 0.400 | $-0.97 | NA | NA | NA | NA | -0.975 |

## Interpretation

- `route_price_disciplined_v1` is not disproven; it is a useful shadow expression of execution discipline.
- The overfit risk comes from choosing among many timing/route/size variants after seeing history, plus hand-picked price caps.
- The cap-grid neighborhood says price discipline is directionally plausible.  Its nested WF is positive, but still too thin and date-sensitive for live approval.
- Pullback YES should stay separate: in this candidate family it changes 3 city-days and nets +2 rows, slightly helps all-history, but slightly hurts 6/21+ and does not solve tail risk.
