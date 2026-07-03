# 2026-06-29 Regime-Routed Expression Router V3

## Conclusion

V3 changes the model from a NO-only router into an expression router.  The important fix is that stale states no longer disappear or get mislabeled as runway: `pullback_uncertain` routes to `current_high_yes`, while `mature_fade` routes to a separate cheap stale-tail current-NO sleeve.

On the fixed historical denominator `docs/analysis/2026-06/generated/regime_routed_no_mechanism_split_v2/trade_details.csv` covering `2026-05-20`..`2026-06-26`, v2 had 263 rows and full ROI +11.7%.  V3 adds 11 pullback current-high YES rows and 9 mature-fade stale-tail NO rows, reaching 283 rows and full ROI +13.0%.  Soft weighted ROI moves from +25.6% to +25.9%.

This is a mechanism improvement, not live approval.  The new YES rows have price/payoff evidence, but `current_yes_ask_size` is missing in the v2 detail layer; the mature-fade NO sleeve has historical edge but is a separate tail strategy, not runway.

## Route To Expression Map

| priority | route | condition | expression | action | status |
| --- | --- | --- | --- | --- | --- |
| 1 | fresh_runway_current_no | current NO route; running_max_state=fresh_running_high; intraday_state in active_warming/fresh_high | current_bracket_no | BUY_NO | main_shadow |
| 2 | false_fade_reheat_current_no | current NO route; intraday_state=false_fade_risk/reheating_after_dip | current_bracket_no | BUY_NO | research_shadow |
| 3 | capped_d2_no | day_regime=day_forecast_capped | d2_no | BUY_NO | main_shadow |
| 4 | pullback_uncertain_current_high_yes | unapproved_stale_current_no; running_max_state=pullback_from_high; intraday_state=pullback_uncertain | current_high_yes | BUY_YES | new_shadow_candidate |
| 5 | cheap_stale_tail_current_no | unapproved_stale_current_no; running_max_state=mature_fade; intraday_state=mature_fade | current_bracket_no | BUY_NO | research_shadow |
| 90 | plateau_stale_current_no_or_skip | unapproved_stale_current_no; running_max_state=near_high_plateau or intraday_state=plateau_near_high | none | SKIP | diagnostic_skip |
| 91 | clock_unknown_diagnostic | unapproved_stale_current_no; running_max_state=running_max_clock_unknown | none | SKIP | diagnostic_skip |
| 99 | excluded_stale_other | other stale states not assigned above | none | SKIP | excluded_or_separate_research |

## Strategy Summary

| slice | rows | dates | cities | wins | win_rate | avg_ask | pnl_usd | roi | weighted_pnl_usd | weighted_roi | weighted_roi_ci_low | weighted_roi_ci_high | daily_roi_eq_minus100 | worst_day_pnl_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| router_v3 | 283 | 38 | 35 | 153 | +54.1% | 0.526 | $+183.98 | +13.0% | $+127.20 | +25.9% | +6.1% | +44.8% | 1 | $-25.39 |
| mechanism_split_v2 | 263 | 38 | 34 | 141 | +53.6% | 0.525 | $+153.28 | +11.7% | $+115.76 | +25.6% | +5.0% | +45.8% | 1 | $-25.39 |
| original_mixed_v1 | 284 | 38 | 35 | 149 | +52.5% | 0.512 | $+170.67 | +12.0% | $+131.43 | +26.7% | +5.7% | +47.5% | 1 | $-25.39 |

## Route Contribution

| slice | rows | dates | cities | wins | win_rate | avg_ask | pnl_usd | roi | weighted_roi | side_mix | capacity_status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pullback_uncertain_current_high_yes | 11 | 11 | 8 | 8 | +72.7% | 0.727 | $-1.05 | -1.9% | -13.5% | BUY_YES:11 | missing_yes_ask_size_in_v2_details:11 |
| capped_d2_no | 84 | 34 | 24 | 52 | +61.9% | 0.612 | $+10.06 | +2.4% | +4.4% | BUY_NO:84 | known_from_no_book:84 |
| cheap_stale_tail_current_no | 9 | 9 | 8 | 4 | +44.4% | 0.293 | $+31.74 | +70.5% | +91.6% | BUY_NO:9 | known_from_no_book:9 |
| false_fade_reheat_current_no | 31 | 21 | 23 | 18 | +58.1% | 0.500 | $+56.11 | +36.2% | +44.4% | BUY_NO:31 | known_from_no_book:31 |
| fresh_runway_current_no | 148 | 35 | 31 | 71 | +48.0% | 0.482 | $+87.11 | +11.8% | +27.7% | BUY_NO:148 | known_from_no_book:148 |

## Recent Window

The fixed denominator currently covers target dates `2026-05-20`..`2026-06-26`. Later atlas/candidate rows are excluded until settlement is available.

| slice | rows | dates | cities | wins | win_rate | avg_ask | pnl_usd | roi | weighted_roi | daily_roi_eq_minus100 | worst_day_pnl_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| since_2026-06-19 | 50 | 8 | 22 | 24 | +48.0% | 0.538 | $-20.22 | -8.1% | +10.5% | 1 | $-15.00 |
| since_2026-06-21 | 31 | 6 | 17 | 14 | +45.2% | 0.552 | $-16.18 | -10.4% | +6.6% | 1 | $-15.00 |
| since_2026-06-22 | 22 | 5 | 13 | 10 | +45.5% | 0.551 | $-10.56 | -9.6% | +7.3% | 1 | $-15.00 |

## Recent Daily Detail

| target_date | rows | cities | wins | win_rate | avg_ask | pnl_usd | roi | route_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-19 | 11 | 11 | 6 | +54.5% | 0.527 | $+1.83 | +3.3% | capped_d2_no:4,false_fade_reheat_current_no:2,fresh_runway_current_no:4,pullback_uncertain_current_high_yes:1 | BuenosAires,Houston,Karachi,Taipei,TelAviv |
| 2026-06-20 | 8 | 8 | 4 | +50.0% | 0.496 | $-5.88 | -14.7% | capped_d2_no:3,cheap_stale_tail_current_no:1,false_fade_reheat_current_no:2,fresh_runway_current_no:2 | Jeddah,Karachi,Manila,Tokyo |
| 2026-06-21 | 9 | 9 | 4 | +44.4% | 0.555 | $-5.62 | -12.5% | capped_d2_no:4,fresh_runway_current_no:4,pullback_uncertain_current_high_yes:1 | Beijing,CapeTown,Denver,Singapore,TelAviv |
| 2026-06-22 | 3 | 3 | 0 | +0.0% | 0.509 | $-15.00 | -100.0% | capped_d2_no:1,cheap_stale_tail_current_no:1,false_fade_reheat_current_no:1 | NYC,Shanghai,Tokyo |
| 2026-06-23 | 6 | 6 | 3 | +50.0% | 0.552 | $+9.94 | +33.1% | capped_d2_no:1,cheap_stale_tail_current_no:1,false_fade_reheat_current_no:1,fresh_runway_current_no:3 | Chongqing,Jeddah,Karachi |
| 2026-06-24 | 3 | 3 | 1 | +33.3% | 0.661 | $-6.26 | -41.7% | capped_d2_no:1,false_fade_reheat_current_no:1,pullback_uncertain_current_high_yes:1 | Ankara,Jeddah |
| 2026-06-25 | 5 | 5 | 3 | +60.0% | 0.528 | $+1.43 | +5.7% | capped_d2_no:1,fresh_runway_current_no:3,pullback_uncertain_current_high_yes:1 | Karachi,Shanghai |
| 2026-06-26 | 5 | 5 | 3 | +60.0% | 0.532 | $-0.67 | -2.7% | false_fade_reheat_current_no:1,fresh_runway_current_no:4 | Chongqing,TelAviv |

## Read

- V3 now separates route semantics cleanly: fresh runway NO, false-fade/reheat NO, capped d2 NO, pullback current-high YES, and mature-fade stale-tail NO are reported as different sleeves.
- It does not fix the broader recent weakness after 6/21. The remaining losses come from other route legs, so this change should not be sold as a full forward repair.
- Maintaining route -> expression mapping is the right architecture: regime/state is the shared feature layer; each route chooses side/bracket/expression independently.

Verdict: `inconclusive_shadow_candidate`.  Keep these as shadow expression heads; collect YES top-ask size/capacity and evaluate mature-fade tail separately before any live discussion.
