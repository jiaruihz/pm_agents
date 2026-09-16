# Reversal / Tail Candidate Survey v1

Generated: 2026-07-01

## Prompt For Original Strategy Shadow Run

When original strategy wants to buy high current_bracket NO, tag a shadow inverse if current_bracket_no_ask>=0.70, current_high_yes_ask<=0.50, and PIT state is capped/low-runway or otherwise conflicted.  Same denominator backtest: original current-NO baseline ROI -26.4%; inverse current-YES ROI +7.4%; excess +33.8%.  This is a shadow diagnostic, not live approval.

## Verdict

`high_current_no_reverse_current_yes` is the only broad current executable direction worth forwarding now, but it is a shadow tag, not a live strategy.  The cleanest sharper mechanism is `expanded_pullback_uncertain_current_high_yes`, which improves point ROI but remains too thin.  The low-price tail ideas are lottery-like with unresolved concentration.

## Candidate Map

| # | candidate | expression | rows | dates | cities | ask | win | ROI | CI low | CI high | baseline | excess | holdout | recent | status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | high_current_no_reverse_current_yes | buy_current_yes | 51 | 33 | 21 | 0.403 | +43.1% | +7.4% | -21.8% | +39.1% | -26.4% | +33.8% | +8.8% | -100.0% | shadow_tag |
| 2 | broad_current_no_overconfidence_inverse | buy_current_yes | 96 | 37 | 31 | 0.401 | +40.6% | +2.5% | -19.3% | +25.4% | -23.3% | +25.8% | +8.4% | +0.6% | diagnostic_baseline |
| 3 | expanded_pullback_uncertain_current_high_yes | buy_current_high_yes | 12 | 11 | 10 | 0.647 | +75.0% | +21.5% | -15.0% | +55.5% | -39.0% | +60.4% | +12.1% | -41.5% | mechanism_candidate_too_thin |
| 4 | pullback_uncertain_current_high_yes | buy_current_high_yes | 7 | 7 | 5 | 0.768 | +100.0% | +33.7% | +19.2% | +52.0% | -100.0% | +133.7% |  |  | mechanism_candidate_too_thin |
| 5 | low_price_yes_reheat_reversal_d1d2 | buy_low_price_target_yes | 409 | 25 | 29 | 0.113 | +16.4% | +44.9% |  |  |  |  | +11.4% |  | zero_notional_shadow |
| 6 | high_price_current_no_case_mining | buy_opposite_current_yes | 270 | 35 | 33 | 0.479 | +47.0% | +0.0% | -19.1% | +18.5% | -31.5% | +31.5% |  |  | case_mining_only |
| 7 | cheap_stale_tail_current_no | buy_current_no | 2 | 2 | 2 | 0.320 | +50.0% | +138.1% |  |  |  |  | +138.1% | +138.1% | too_thin_do_not_chase |

## Read

- The current 7.4% ROI is low; the reason to keep the line is the same-row direction flip: current-NO baseline -26.4% versus inverse current-YES +7.4%.
- `expanded_pullback_uncertain_current_high_yes` is mechanically cleaner than the broad v3 slice: current-high YES +21.5% versus same-row NO baseline -39.0%, but only 12 city-date rows.
- The older 7-row `pullback_uncertain_current_high_yes` case study remains useful as mechanism evidence, not as a standalone rule.
- Low-price YES has genuine convexity, but prior reports show top-day concentration and weak forward baseline excess.  It belongs in zero-notional telemetry, not live.
- High-price NO case mining confirms the failure mode exists, but raw opposite current YES is roughly breakeven until narrowed by PIT state.
- Do not connect this to the regime-routed runner as a live expression selector.  Run it as independent shadow labels over the old strategy's decisions.

## Artifacts Read

- `docs/analysis/2026-07/generated/reversal_archetype_selector_v3/archetype_summary.csv`
- `docs/analysis/2026-06/generated/current_high_yes_from_regime_no_misroutes_v0/slice_summary.csv`
- `docs/analysis/2026-06/generated/low_price_yes_reheat_reversal_v1/scored_rows.csv`
- `docs/analysis/2026-06/generated/high_price_forecast_bias_reversal_cases_v1/high_price_reversal_summary.csv`
- `docs/analysis/2026-06/generated/regime_routed_expression_router_v3_live_like_entry_v1/route_summary.csv`
- `docs/analysis/2026-06/generated/current_yes_peak_yes_execution_timing_v1/peak_yes_timing_v1_event_rows.csv`
