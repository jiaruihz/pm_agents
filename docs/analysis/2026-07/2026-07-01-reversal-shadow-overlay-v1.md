# Reversal Shadow Overlay v1

Generated: 2026-07-01

## Practical Prompt

If original current-NO strategy emits a current_bracket_no candidate, attach zero-notional `reversal_shadow` fields.  For `high_current_no_reverse_current_yes`: require current_bracket_no_ask>=0.70, current_high_yes_ask<=0.50, and day_forecast_capped/day_forecast_busted or forecast_gap_to_running_native<=1.0.  For `expanded_pullback_uncertain_current_high_yes`: require intraday_state=pullback_uncertain, current_high_yes_ask in [0.50,0.90], and current_bracket_no_ask>=0.40.  Record opposite current_high YES ask/payoff; do not place orders.

## Verdict

`expanded_pullback_uncertain_current_high_yes` is the sharper practical label on original/current-router rows, but remains shadow-only because selected-row sample is thin.  The broad high-current-NO inverse is useful as a wrong-way diagnostic, not as a standalone trade.

## Review: Real Issue vs Overfit Risk

### Real Issues

- The original current-bracket NO expression has a genuine wrong-way failure mode when the market prices current NO high while PIT weather state is capped or low-runway. On the broad event-matrix denominator, the same rows have current-NO baseline ROI -26.4% versus inverse current-YES ROI +7.4%, so the direction flip is informative even though the inverse itself is not yet strong.
- This should be recorded at the original strategy decision point. The runner must keep the original current-NO candidate and attach zero-notional `reversal_shadow_*` fields with the opposite current-high YES token/market/ask lineage.
- The feature boundary is valid: the tag uses PIT prices and PIT weather state only. It must not use final max, settlement winner, or realized forecast error as a live selector.

### Overfit / Fragile Parts

- The +7.4% inverse ROI is not enough for live approval; CI crosses zero and recent slices are weak.
- The thresholds `current_bracket_no_ask>=0.70` and `current_high_yes_ask<=0.50` are useful telemetry cut points, not proven optimal execution thresholds.
- `expanded_pullback_uncertain_current_high_yes` is mechanically cleaner but only 12 city-date rows in the full matrix and 4 rows on selected runner rows. Treat it as a sharper label to monitor, not a trading rule.
- Current-high YES has exact-bracket risk: it wins only if final settlement remains in the current high bracket. Overshoot to d1/d2 can still make the inverse lose.

### Current Implementation Decision

Keep the main strategy unchanged. Add/maintain zero-notional `reversal_shadow_*` tags only:

- `high_current_no_reverse_current_yes` for high current-NO / cheap current-high YES plus capped or low-runway PIT state.
- `expanded_pullback_uncertain_current_high_yes` for pullback-uncertain current-NO rows where the current-high YES is explicitly priced.

The live runner should never place an order from this tag. It should write `reversal_shadow_live_order_allowed=false`, `reversal_shadow_notional_usd=0`, and enough token/market lineage to settle the shadow expression later.

## Full Window: Original Selected Rows

| dataset | policy | label | rows | dates | cities | YES ask | win | shadow ROI | CI low | CI high | orig NO ROI | excess | loss days | max loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| regime_routed_expression_router_v3_live_like | first_eligible | expanded_pullback_uncertain_current_high_yes | 4 | 4 | 3 | 0.667 | +75.0% | +16.5% | -63.1% | +67.3% | -37.5% | +54.0% | 1 | $-5.00 |
| regime_routed_expression_router_v3_live_like | first_eligible | high_current_no_reverse_current_yes | 1 | 1 | 1 | 0.320 | +0.0% | -100.0% |  |  | +42.9% | -142.9% | 1 | $-5.00 |
| regime_routed_expression_router_v3_live_like | fixed_noon_priority | expanded_pullback_uncertain_current_high_yes | 4 | 4 | 3 | 0.667 | +75.0% | +16.5% | -63.1% | +67.3% | -37.5% | +54.0% | 1 | $-5.00 |
| regime_routed_expression_router_v3_live_like | fixed_noon_priority | high_current_no_reverse_current_yes | 1 | 1 | 1 | 0.320 | +0.0% | -100.0% |  |  | +42.9% | -142.9% | 1 | $-5.00 |
| regime_routed_no_expression_v1 | best_ask | expanded_pullback_uncertain_current_high_yes | 4 | 4 | 3 | 0.667 | +75.0% | +16.5% | -63.1% | +67.3% | -37.5% | +54.0% | 1 | $-5.00 |
| regime_routed_no_expression_v1 | best_ask | high_current_no_reverse_current_yes | 1 | 1 | 1 | 0.320 | +0.0% | -100.0% |  |  | +42.9% | -142.9% | 1 | $-5.00 |
| regime_routed_no_expression_v1 | near_noon | expanded_pullback_uncertain_current_high_yes | 4 | 4 | 3 | 0.667 | +75.0% | +16.5% | -63.1% | +67.3% | -37.5% | +54.0% | 1 | $-5.00 |
| regime_routed_no_expression_v1 | near_noon | high_current_no_reverse_current_yes | 1 | 1 | 1 | 0.320 | +0.0% | -100.0% |  |  | +42.9% | -142.9% | 1 | $-5.00 |

## Holdout

| dataset | policy | label | rows | dates | cities | YES ask | win | shadow ROI | CI low | CI high | orig NO ROI | excess | loss days | max loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| regime_routed_expression_router_v3_live_like | first_eligible | expanded_pullback_uncertain_current_high_yes | 2 | 2 | 1 | 0.645 | +50.0% | -12.3% |  |  | +25.0% | -37.3% | 1 | $-5.00 |
| regime_routed_expression_router_v3_live_like | fixed_noon_priority | expanded_pullback_uncertain_current_high_yes | 2 | 2 | 1 | 0.645 | +50.0% | -12.3% |  |  | +25.0% | -37.3% | 1 | $-5.00 |
| regime_routed_no_expression_v1 | best_ask | expanded_pullback_uncertain_current_high_yes | 2 | 2 | 1 | 0.645 | +50.0% | -12.3% |  |  | +25.0% | -37.3% | 1 | $-5.00 |
| regime_routed_no_expression_v1 | near_noon | expanded_pullback_uncertain_current_high_yes | 2 | 2 | 1 | 0.645 | +50.0% | -12.3% |  |  | +25.0% | -37.3% | 1 | $-5.00 |

## Recent

| dataset | policy | label | rows | dates | cities | YES ask | win | shadow ROI | CI low | CI high | orig NO ROI | excess | loss days | max loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| regime_routed_expression_router_v3_live_like | first_eligible | expanded_pullback_uncertain_current_high_yes | 2 | 2 | 1 | 0.645 | +50.0% | -12.3% |  |  | +25.0% | -37.3% | 1 | $-5.00 |
| regime_routed_expression_router_v3_live_like | fixed_noon_priority | expanded_pullback_uncertain_current_high_yes | 2 | 2 | 1 | 0.645 | +50.0% | -12.3% |  |  | +25.0% | -37.3% | 1 | $-5.00 |
| regime_routed_no_expression_v1 | best_ask | expanded_pullback_uncertain_current_high_yes | 2 | 2 | 1 | 0.645 | +50.0% | -12.3% |  |  | +25.0% | -37.3% | 1 | $-5.00 |
| regime_routed_no_expression_v1 | near_noon | expanded_pullback_uncertain_current_high_yes | 2 | 2 | 1 | 0.645 | +50.0% | -12.3% |  |  | +25.0% | -37.3% | 1 | $-5.00 |

## Full Matrix Sanity

Same labels on the broader expression matrix, first city-date per label.  This checks whether selected-row results are just router selection artifacts.

| dataset | policy | label | rows | dates | cities | YES ask | win | shadow ROI | CI low | CI high | orig NO ROI | excess | loss days | max loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| event_matrix | all | expanded_pullback_uncertain_current_high_yes | 12 | 11 | 10 | 0.647 | +75.0% | +21.5% | -15.0% | +55.5% | -39.0% | +60.4% | 3 | $-5.00 |
| event_matrix | all | high_current_no_reverse_current_yes | 51 | 33 | 21 | 0.403 | +43.1% | +7.4% | -21.8% | +39.1% | -26.4% | +33.8% | 16 | $-15.00 |

## Top Shadow Rows

| dataset | policy | label | city | date | hour | day | state | runmax | YES ask | YES pnl | NO ask | NO pnl | excess |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| regime_routed_expression_router_v3_live_like | first_eligible | expanded_pullback_uncertain_current_high_yes | Jeddah | 2026-06-21 | 13 | day_open_runway | pullback_uncertain | pullback_from_high | 0.570 | $+3.77 | 0.440 | $-5.00 | $+8.77 |
| regime_routed_no_expression_v1 | near_noon | expanded_pullback_uncertain_current_high_yes | Jeddah | 2026-06-21 | 13 | day_open_runway | pullback_uncertain | pullback_from_high | 0.570 | $+3.77 | 0.440 | $-5.00 | $+8.77 |
| regime_routed_expression_router_v3_live_like | fixed_noon_priority | expanded_pullback_uncertain_current_high_yes | Jeddah | 2026-06-21 | 13 | day_open_runway | pullback_uncertain | pullback_from_high | 0.570 | $+3.77 | 0.440 | $-5.00 | $+8.77 |
| regime_routed_no_expression_v1 | best_ask | expanded_pullback_uncertain_current_high_yes | Jeddah | 2026-06-21 | 13 | day_open_runway | pullback_uncertain | pullback_from_high | 0.570 | $+3.77 | 0.440 | $-5.00 | $+8.77 |
| regime_routed_expression_router_v3_live_like | first_eligible | expanded_pullback_uncertain_current_high_yes | Helsinki | 2026-06-05 | 11 | day_open_runway | pullback_uncertain | pullback_from_high | 0.677 | $+2.39 | 0.429 | $-5.00 | $+7.39 |
| regime_routed_no_expression_v1 | near_noon | expanded_pullback_uncertain_current_high_yes | Helsinki | 2026-06-05 | 11 | day_open_runway | pullback_uncertain | pullback_from_high | 0.677 | $+2.39 | 0.429 | $-5.00 | $+7.39 |
| regime_routed_expression_router_v3_live_like | fixed_noon_priority | expanded_pullback_uncertain_current_high_yes | Helsinki | 2026-06-05 | 11 | day_open_runway | pullback_uncertain | pullback_from_high | 0.677 | $+2.39 | 0.429 | $-5.00 | $+7.39 |
| regime_routed_no_expression_v1 | best_ask | expanded_pullback_uncertain_current_high_yes | Helsinki | 2026-06-05 | 11 | day_open_runway | pullback_uncertain | pullback_from_high | 0.677 | $+2.39 | 0.429 | $-5.00 | $+7.39 |
| regime_routed_expression_router_v3_live_like | first_eligible | expanded_pullback_uncertain_current_high_yes | SaoPaulo | 2026-05-21 | 11 | day_open_runway | pullback_uncertain | pullback_from_high | 0.700 | $+2.14 | 0.430 | $-5.00 | $+7.14 |
| regime_routed_no_expression_v1 | near_noon | expanded_pullback_uncertain_current_high_yes | SaoPaulo | 2026-05-21 | 11 | day_open_runway | pullback_uncertain | pullback_from_high | 0.700 | $+2.14 | 0.430 | $-5.00 | $+7.14 |
| regime_routed_no_expression_v1 | best_ask | expanded_pullback_uncertain_current_high_yes | SaoPaulo | 2026-05-21 | 11 | day_open_runway | pullback_uncertain | pullback_from_high | 0.700 | $+2.14 | 0.430 | $-5.00 | $+7.14 |
| regime_routed_expression_router_v3_live_like | fixed_noon_priority | expanded_pullback_uncertain_current_high_yes | SaoPaulo | 2026-05-21 | 11 | day_open_runway | pullback_uncertain | pullback_from_high | 0.700 | $+2.14 | 0.430 | $-5.00 | $+7.14 |
| regime_routed_expression_router_v3_live_like | fixed_noon_priority | high_current_no_reverse_current_yes | Miami | 2026-06-12 | 13 | day_marginal_runway | false_fade_risk | pullback_from_high | 0.320 | $-5.00 | 0.700 | $+2.14 | $-7.14 |
| regime_routed_no_expression_v1 | best_ask | high_current_no_reverse_current_yes | Miami | 2026-06-12 | 13 | day_marginal_runway | false_fade_risk | pullback_from_high | 0.320 | $-5.00 | 0.700 | $+2.14 | $-7.14 |
| regime_routed_expression_router_v3_live_like | first_eligible | high_current_no_reverse_current_yes | Miami | 2026-06-12 | 13 | day_marginal_runway | false_fade_risk | pullback_from_high | 0.320 | $-5.00 | 0.700 | $+2.14 | $-7.14 |
| regime_routed_no_expression_v1 | near_noon | high_current_no_reverse_current_yes | Miami | 2026-06-12 | 13 | day_marginal_runway | false_fade_risk | pullback_from_high | 0.320 | $-5.00 | 0.700 | $+2.14 | $-7.14 |
| regime_routed_expression_router_v3_live_like | fixed_noon_priority | expanded_pullback_uncertain_current_high_yes | Jeddah | 2026-06-24 | 13 | day_open_runway | pullback_uncertain | pullback_from_high | 0.720 | $-5.00 | 0.400 | $+7.50 | $-12.50 |
| regime_routed_no_expression_v1 | best_ask | expanded_pullback_uncertain_current_high_yes | Jeddah | 2026-06-24 | 13 | day_open_runway | pullback_uncertain | pullback_from_high | 0.720 | $-5.00 | 0.400 | $+7.50 | $-12.50 |
| regime_routed_expression_router_v3_live_like | first_eligible | expanded_pullback_uncertain_current_high_yes | Jeddah | 2026-06-24 | 13 | day_open_runway | pullback_uncertain | pullback_from_high | 0.720 | $-5.00 | 0.400 | $+7.50 | $-12.50 |
| regime_routed_no_expression_v1 | near_noon | expanded_pullback_uncertain_current_high_yes | Jeddah | 2026-06-24 | 13 | day_open_runway | pullback_uncertain | pullback_from_high | 0.720 | $-5.00 | 0.400 | $+7.50 | $-12.50 |

## Artifacts

- Details CSV: `docs/analysis/2026-07/generated/reversal_shadow_overlay_v1/shadow_details.csv`
- Summary CSV: `docs/analysis/2026-07/generated/reversal_shadow_overlay_v1/summary.csv`
- Script: `scripts/analysis/forecast_quality/research_reversal_shadow_overlay_v1.py`
