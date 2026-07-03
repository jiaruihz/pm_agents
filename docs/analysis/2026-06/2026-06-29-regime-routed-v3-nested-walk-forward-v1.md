# Regime-Routed V3 Nested Walk-Forward V1

## Conclusion

This is the first nested walk-forward audit for the manual choices made around v3: entry policy, route sleeves, and sizing.  Each test date is evaluated only after a candidate is selected from prior dates.  The result does not validate the strategy; it shows that the candidate menu is highly selection-sensitive and still weak on the 2026-06-21+ window.

Verdict: `inconclusive_shadow_only`，live_ready=`False`。

## Walk-Forward Summary

| menu | validation_selector | window | test_days | test_rows | pnl_usd | roi | avg_val_roi | chosen_route_sets | chosen_sizing |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| live_like_with_soft | expanding_prior | all_walk_forward | 27 | 166 | $+20.38 | +7.5% | +17.4% | all_v3:11;capped_only:5;core_no:8;fresh_only:2;no_pullback_yes:1 | soft_balanced:27 |
| live_like_with_soft | expanding_prior | forward_2026-06-21_plus | 6 | 27 | $-9.32 | -24.9% | +19.3% | core_no:1;no_pullback_yes:5 | soft_balanced:6 |
| live_like_with_soft | trailing_10_dates | all_walk_forward | 27 | 126 | $+13.74 | +6.6% | +30.5% | all_v3:8;capped_only:8;core_no:1;fresh_only:9;no_cheap_tail:1 | soft_balanced:27 |
| live_like_with_soft | trailing_10_dates | forward_2026-06-21_plus | 6 | 19 | $-6.15 | -20.3% | +17.0% | all_v3:1;fresh_only:2;no_pullback_yes:2;none:1 | none:1;soft_balanced:5 |
| live_strict_full | expanding_prior | all_walk_forward | 27 | 145 | $+3.76 | +0.5% | +5.5% | all_v3:10;capped_only:10;fresh_only:2;no_pullback_yes:5 | full_size:27 |
| live_strict_full | expanding_prior | forward_2026-06-21_plus | 6 | 28 | $-18.14 | -13.0% | +6.0% | no_pullback_yes:6 | full_size:6 |
| live_strict_full | trailing_10_dates | all_walk_forward | 27 | 119 | $-3.12 | -0.5% | +18.1% | all_v3:9;capped_only:10;core_no:1;fresh_only:7 | full_size:27 |
| live_strict_full | trailing_10_dates | forward_2026-06-21_plus | 6 | 20 | $-24.50 | -24.5% | +1.2% | all_v3:2;fresh_only:2;no_pullback_yes:1;none:1 | full_size:5;none:1 |
| research_including_best_ask | expanding_prior | all_walk_forward | 27 | 148 | $+53.37 | +20.0% | +21.8% | all_v3:11;capped_only:2;fresh_only:11;no_pullback_yes:3 | soft_balanced:27 |
| research_including_best_ask | expanding_prior | forward_2026-06-21_plus | 6 | 14 | $-1.06 | -4.3% | +29.7% | fresh_only:4;none:2 | none:2;soft_balanced:4 |
| research_including_best_ask | trailing_10_dates | all_walk_forward | 27 | 128 | $+51.04 | +22.7% | +38.3% | all_v3:7;capped_only:7;fresh_only:11;no_cheap_tail:2 | soft_balanced:27 |
| research_including_best_ask | trailing_10_dates | forward_2026-06-21_plus | 6 | 19 | $-1.04 | -3.3% | +32.9% | all_v3:1;fresh_only:2;no_pullback_yes:2;none:1 | none:1;soft_balanced:5 |

## Recent Decisions

| menu | validation_selector | test_date | candidate_id | val_roi | test_rows | test_pnl_usd | test_roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| live_strict_full | expanding_prior | 2026-06-21 | fixed_noon_priority__no_pullback_yes__full_size | +7.6% | 8 | $-14.41 | -36.0% |
| live_strict_full | expanding_prior | 2026-06-22 | fixed_noon_priority__no_pullback_yes__full_size | +6.3% | 3 | $-15.00 | -100.0% |
| live_strict_full | expanding_prior | 2026-06-23 | fixed_noon_priority__no_pullback_yes__full_size | +5.0% | 6 | $+9.94 | +33.1% |
| live_strict_full | expanding_prior | 2026-06-24 | fixed_noon_priority__no_pullback_yes__full_size | +5.6% | 2 | $-1.26 | -12.6% |
| live_strict_full | expanding_prior | 2026-06-25 | fixed_noon_priority__no_pullback_yes__full_size | +5.5% | 4 | $+3.26 | +16.3% |
| live_strict_full | expanding_prior | 2026-06-26 | fixed_noon_priority__no_pullback_yes__full_size | +5.7% | 5 | $-0.67 | -2.7% |
| live_strict_full | trailing_10_dates | 2026-06-21 | fixed_noon_priority__fresh_only__full_size | +12.3% | 4 | $-8.89 | -44.4% |
| live_strict_full | trailing_10_dates | 2026-06-22 | fixed_noon_priority__fresh_only__full_size | +10.4% | 0 | $+0.00 | NA |
| live_strict_full | trailing_10_dates | 2026-06-23 | fixed_noon_priority__fresh_only__full_size | +10.3% | 3 | $-6.94 | -46.2% |
| live_strict_full | trailing_10_dates | 2026-06-24 | fixed_noon_priority__all_v3__full_size | +2.2% | 3 | $-6.26 | -41.7% |
| live_strict_full | trailing_10_dates | 2026-06-25 | fixed_noon_priority__all_v3__full_size | -10.6% | 5 | $-1.74 | -7.0% |
| live_strict_full | trailing_10_dates | 2026-06-26 | fixed_noon_priority__no_pullback_yes__full_size | -17.4% | 5 | $-0.67 | -2.7% |
| live_like_with_soft | expanding_prior | 2026-06-21 | fixed_noon_priority__no_pullback_yes__soft_balanced | +20.3% | 8 | $-3.00 | -28.1% |
| live_like_with_soft | expanding_prior | 2026-06-22 | fixed_noon_priority__no_pullback_yes__soft_balanced | +19.1% | 3 | $-2.91 | -100.0% |
| live_like_with_soft | expanding_prior | 2026-06-23 | first_eligible__core_no__soft_balanced | +18.4% | 5 | $-3.16 | -43.8% |
| live_like_with_soft | expanding_prior | 2026-06-24 | fixed_noon_priority__no_pullback_yes__soft_balanced | +19.4% | 2 | $+0.55 | +19.8% |
| live_like_with_soft | expanding_prior | 2026-06-25 | fixed_noon_priority__no_pullback_yes__soft_balanced | +19.4% | 4 | $-0.30 | -8.1% |
| live_like_with_soft | expanding_prior | 2026-06-26 | fixed_noon_priority__no_pullback_yes__soft_balanced | +19.2% | 5 | $-0.49 | -4.9% |
| live_like_with_soft | trailing_10_dates | 2026-06-21 | fixed_noon_priority__fresh_only__soft_balanced | +29.6% | 4 | $-1.68 | -23.9% |
| live_like_with_soft | trailing_10_dates | 2026-06-22 | fixed_noon_priority__fresh_only__soft_balanced | +30.6% | 0 | $+0.00 | NA |
| live_like_with_soft | trailing_10_dates | 2026-06-23 | fixed_noon_priority__fresh_only__soft_balanced | +22.6% | 3 | $-2.34 | -50.0% |
| live_like_with_soft | trailing_10_dates | 2026-06-24 | fixed_noon_priority__all_v3__soft_balanced | +22.6% | 3 | $-1.33 | -28.7% |
| live_like_with_soft | trailing_10_dates | 2026-06-25 | fixed_noon_priority__no_pullback_yes__soft_balanced | +2.2% | 4 | $-0.30 | -8.1% |
| live_like_with_soft | trailing_10_dates | 2026-06-26 | fixed_noon_priority__no_pullback_yes__soft_balanced | -5.7% | 5 | $-0.49 | -4.9% |
| research_including_best_ask | expanding_prior | 2026-06-21 | old_best_ask__fresh_only__soft_balanced | +30.5% | 4 | $+1.77 | +23.3% |
| research_including_best_ask | expanding_prior | 2026-06-22 | old_best_ask__fresh_only__soft_balanced | +30.3% | 0 | $+0.00 | NA |
| research_including_best_ask | expanding_prior | 2026-06-23 | old_best_ask__fresh_only__soft_balanced | +30.3% | 3 | $-2.34 | -50.0% |
| research_including_best_ask | expanding_prior | 2026-06-24 | old_best_ask__fresh_only__soft_balanced | +29.0% | 0 | $+0.00 | NA |
| research_including_best_ask | expanding_prior | 2026-06-25 | old_best_ask__fresh_only__soft_balanced | +29.0% | 3 | $+0.97 | +25.2% |
| research_including_best_ask | expanding_prior | 2026-06-26 | old_best_ask__fresh_only__soft_balanced | +29.0% | 4 | $-1.45 | -17.0% |
| research_including_best_ask | trailing_10_dates | 2026-06-21 | old_best_ask__fresh_only__soft_balanced | +52.9% | 4 | $+1.77 | +23.3% |
| research_including_best_ask | trailing_10_dates | 2026-06-22 | old_best_ask__fresh_only__soft_balanced | +48.1% | 0 | $+0.00 | NA |
| research_including_best_ask | trailing_10_dates | 2026-06-23 | old_best_ask__fresh_only__soft_balanced | +43.0% | 3 | $-2.34 | -50.0% |
| research_including_best_ask | trailing_10_dates | 2026-06-24 | old_best_ask__all_v3__soft_balanced | +38.1% | 3 | $-1.33 | -28.7% |
| research_including_best_ask | trailing_10_dates | 2026-06-25 | old_best_ask__no_pullback_yes__soft_balanced | +11.8% | 4 | $+1.36 | +29.3% |
| research_including_best_ask | trailing_10_dates | 2026-06-26 | old_best_ask__no_pullback_yes__soft_balanced | +3.6% | 5 | $-0.49 | -4.9% |

## Static Candidate Leaders

| candidate_id | entry_policy_family | route_set | sizing_policy | window | rows | dates | pnl_usd | roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| old_best_ask__no_pullback_yes__soft_balanced | leaky_best_ask_reference | no_pullback_yes | soft_balanced | all | 272 | 38 | $+130.26 | +27.8% |
| old_best_ask__fresh_only__soft_balanced | leaky_best_ask_reference | fresh_only | soft_balanced | all | 148 | 35 | $+82.89 | +27.7% |
| old_best_ask__all_v3__soft_balanced | leaky_best_ask_reference | all_v3 | soft_balanced | all | 283 | 38 | $+127.20 | +25.9% |
| old_best_ask__core_no__soft_balanced | leaky_best_ask_reference | core_no | soft_balanced | all | 263 | 38 | $+115.76 | +25.6% |
| old_best_ask__no_cheap_tail__soft_balanced | leaky_best_ask_reference | no_cheap_tail | soft_balanced | all | 274 | 38 | $+112.70 | +23.7% |
| old_best_ask__fresh_capped__soft_balanced | leaky_best_ask_reference | fresh_capped | soft_balanced | all | 232 | 38 | $+86.71 | +22.4% |
| first_eligible__no_pullback_yes__soft_balanced | live_like_no_best_ask | no_pullback_yes | soft_balanced | all | 273 | 38 | $+84.08 | +18.7% |
| fixed_noon_priority__no_pullback_yes__soft_balanced | live_like_no_best_ask | no_pullback_yes | soft_balanced | all | 273 | 38 | $+86.01 | +18.7% |
| first_eligible__core_no__soft_balanced | live_like_no_best_ask | core_no | soft_balanced | all | 265 | 38 | $+75.92 | +17.3% |
| first_eligible__all_v3__soft_balanced | live_like_no_best_ask | all_v3 | soft_balanced | all | 282 | 38 | $+79.75 | +16.9% |
| fixed_noon_priority__all_v3__soft_balanced | live_like_no_best_ask | all_v3 | soft_balanced | all | 282 | 38 | $+80.79 | +16.9% |
| fixed_noon_priority__core_no__soft_balanced | live_like_no_best_ask | core_no | soft_balanced | all | 264 | 38 | $+72.69 | +16.4% |
| first_eligible__no_cheap_tail__soft_balanced | live_like_no_best_ask | no_cheap_tail | soft_balanced | all | 274 | 38 | $+71.59 | +15.6% |
| fixed_noon_priority__no_cheap_tail__soft_balanced | live_like_no_best_ask | no_cheap_tail | soft_balanced | all | 273 | 38 | $+67.47 | +14.6% |
| fixed_noon_priority__fresh_only__soft_balanced | live_like_no_best_ask | fresh_only | soft_balanced | all | 152 | 35 | $+40.94 | +13.8% |
| old_best_ask__no_pullback_yes__soft_balanced | leaky_best_ask_reference | no_pullback_yes | soft_balanced | forward_2026-06-21_plus | 28 | 6 | $+5.68 | +13.7% |
| old_best_ask__no_pullback_yes__full_size | leaky_best_ask_reference | no_pullback_yes | full_size | all | 272 | 38 | $+185.02 | +13.6% |
| old_best_ask__all_v3__full_size | leaky_best_ask_reference | all_v3 | full_size | all | 283 | 38 | $+183.98 | +13.0% |
| fixed_noon_priority__fresh_capped__soft_balanced | live_like_no_best_ask | fresh_capped | soft_balanced | all | 236 | 38 | $+45.73 | +11.9% |
| first_eligible__fresh_only__soft_balanced | live_like_no_best_ask | fresh_only | soft_balanced | all | 151 | 35 | $+34.63 | +11.9% |
| old_best_ask__fresh_only__full_size | leaky_best_ask_reference | fresh_only | full_size | all | 148 | 35 | $+87.11 | +11.8% |
| old_best_ask__core_no__full_size | leaky_best_ask_reference | core_no | full_size | all | 263 | 38 | $+153.28 | +11.7% |
| old_best_ask__no_cheap_tail__full_size | leaky_best_ask_reference | no_cheap_tail | full_size | all | 274 | 38 | $+152.24 | +11.1% |
| first_eligible__fresh_capped__soft_balanced | live_like_no_best_ask | fresh_capped | soft_balanced | all | 235 | 38 | $+39.52 | +10.5% |
| old_best_ask__fresh_capped__full_size | leaky_best_ask_reference | fresh_capped | full_size | all | 232 | 38 | $+97.17 | +8.4% |
| old_best_ask__all_v3__soft_balanced | leaky_best_ask_reference | all_v3 | soft_balanced | forward_2026-06-21_plus | 31 | 6 | $+3.12 | +6.6% |
| first_eligible__capped_only__soft_balanced | live_like_no_best_ask | capped_only | soft_balanced | all | 84 | 34 | $+4.89 | +5.7% |
| fixed_noon_priority__capped_only__soft_balanced | live_like_no_best_ask | capped_only | soft_balanced | all | 84 | 34 | $+4.79 | +5.6% |
| fixed_noon_priority__no_pullback_yes__full_size | live_like_no_best_ask | no_pullback_yes | full_size | all | 273 | 38 | $+75.34 | +5.5% |
| first_eligible__no_pullback_yes__soft_balanced | live_like_no_best_ask | no_pullback_yes | soft_balanced | forward_2026-06-21_plus | 28 | 6 | $+1.94 | +5.1% |

## Notes

- `live_strict_full` excludes best-ask and soft sizing.
- `live_like_with_soft` excludes best-ask but lets validation choose full vs soft sizing.
- `research_including_best_ask` is a diagnostic menu only; if it wins, that is evidence of selection/leakage risk, not a live candidate.
