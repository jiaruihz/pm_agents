# METAR Reversal Expression Matrix v1 (pre-registered)

Generated: 2026-07-03T13:45:50.181235+00:00

## Verdict

```text
false_fade_reheat_conflict d1_yes: significance=PASS (full CI > 0, top5-removed > 0, +1c robust,
                          complement separated, city-concentration robust)
                          baseline=PASS (same-leg whole-denominator baseline is deeply negative)
                          forward=NA (no fresh forward rows yet)
                          conclusion=shadow_candidate
heat_death current_high_yes:        significance=FAIL (CI crosses 0) conclusion=inconclusive, keep as telemetry
```

Trading action (before evidence): no live change. `false_fade_reheat_conflict` goes to zero-notional shadow
(`scripts/ops/metar_reversal_false_fade_reheat_shadow_v1.py`) to resolve the three open risks that cap it
at shadow_candidate: historical quote staleness, live fill feasibility (median best-ask size
was ~16 shares), and June decay (May +238% vs June +38.6% point, June CI crosses 0).
`heat_death` stays a diagnostic tag; at heat_death snapshots current_high_yes beats every sibling leg in point
estimate but is not significant on its own.

Registered as a NEW strategy family candidate (`metar_reversal`), separate from the D-1
forecast-tail lottery: different denominator (intraday city-date-hour), different mechanism
(obs-vs-market conflict, not station-bias prior).

- Denominator: 7750 settled city-date-hour snapshots, 2026-05-19..2026-06-26, 36 cities.
- Leg quote coverage: current_high_yes=92%, current_bracket_no=95%, d1_yes=89%, d2_yes=73%, high_tail_yes=58%, d1_no=84%, d2_no=68%
- K=2 pre-registered triggers; sensitivity grid is diagnostic only.

## Trigger and baseline table

| strategy | period | rows | dates | cities | win_rate | avg_ask | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline_all_current_high_yes | full | 4275 | 38 | 36 | +0.499 | +0.527 | -0.152 | -0.280 | +0.014 | -0.246 |
| baseline_all_current_bracket_no | full | 4653 | 38 | 36 | +0.355 | +0.386 | -0.279 | -0.370 | -0.176 | -0.343 |
| baseline_all_d1_yes | full | 5243 | 38 | 36 | +0.220 | +0.254 | -0.251 | -0.358 | -0.136 | -0.319 |
| baseline_all_d2_yes | full | 4324 | 38 | 36 | +0.169 | +0.191 | -0.363 | -0.461 | -0.250 | -0.417 |
| baseline_all_high_tail_yes | full | 1116 | 38 | 36 | +0.040 | +0.055 | -0.766 | -0.894 | -0.617 | -0.870 |
| baseline_all_d1_no | full | 4530 | 38 | 36 | +0.734 | +0.761 | -0.050 | -0.073 | -0.026 | -0.058 |
| baseline_all_d2_no | full | 3455 | 38 | 36 | +0.772 | +0.804 | -0.049 | -0.074 | -0.025 | -0.054 |
| heat_death_current_high_yes | full | 80 | 28 | 22 | +0.713 | +0.672 | +0.145 | -0.205 | +0.568 | -0.090 |
| heat_death_taker_plus_1c | full | 80 | 28 | 22 | +0.713 | +0.672 | +0.114 | -0.218 | +0.506 | -0.102 |
| heat_death_complement_same_hours | full | 3843 | 38 | 36 | +0.459 | +0.489 | -0.169 | -0.308 | +0.013 | -0.275 |
| heat_death_alt_d1_no | full | 82 | 28 | 23 | +0.780 | +0.794 | -0.024 | -0.186 | +0.137 | -0.084 |
| heat_death_alt_current_bracket_no | full | 78 | 28 | 21 | +0.269 | +0.388 | -0.355 | -0.685 | +0.001 | -0.597 |
| heat_death_alt_d1_yes | full | 82 | 28 | 23 | +0.220 | +0.273 | -0.273 | -0.662 | +0.151 | -0.566 |
| false_fade_reheat_conflict_d1yes | full | 32 | 22 | 15 | +0.438 | +0.175 | +1.195 | +0.298 | +2.162 | +0.272 |
| false_fade_reheat_conflict_taker_plus_1c | full | 32 | 22 | 15 | +0.438 | +0.175 | +1.076 | +0.239 | +1.976 | +0.225 |
| false_fade_reheat_conflict_complement_warming_pool | full | 1432 | 36 | 36 | +0.293 | +0.335 | -0.177 | -0.317 | +0.005 | -0.313 |
| false_fade_reheat_conflict_alt_current_high_yes | full | 32 | 22 | 15 | +0.531 | +0.837 | -0.405 | -0.592 | -0.236 | -0.526 |
| false_fade_reheat_conflict_alt_d2_yes | full | 27 | 21 | 14 | +0.037 | +0.059 | -0.884 | -1.000 | -0.677 | -1.000 |
| false_fade_reheat_conflict_alt_high_tail_yes | full | 2 | 2 | 2 | +0.000 | +0.006 | -1.000 |  |  |  |
| false_fade_reheat_conflict_alt_current_bracket_no | full | 32 | 22 | 15 | +0.469 | +0.230 | +0.930 | +0.179 | +1.760 | +0.038 |
| heat_death_month_2026-05 | monthly | 25 | 9 | 14 | +0.840 | +0.721 | +0.278 | -0.162 | +0.814 | +0.014 |
| heat_death_month_2026-06 | monthly | 55 | 19 | 20 | +0.655 | +0.650 | +0.084 | -0.370 | +0.657 | -0.213 |
| false_fade_reheat_conflict_month_2026-05 | monthly | 13 | 8 | 8 | +0.615 | +0.172 | +2.378 | +1.132 | +3.670 | +0.375 |
| false_fade_reheat_conflict_month_2026-06 | monthly | 19 | 14 | 12 | +0.316 | +0.177 | +0.386 | -0.493 | +1.487 | -0.754 |

## Sensitivity diagnostic (not for selection)

| strategy | period | rows | dates | cities | win_rate | avg_ask | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
|---|---|---|---|---|---|---|---|---|---|---|
| heat_death_diag_m60_cap0.8 | diag | 55 | 26 | 22 | +0.655 | +0.571 | +0.249 | -0.235 | +0.859 | -0.098 |
| heat_death_diag_m60_cap0.85 | diag | 88 | 30 | 26 | +0.727 | +0.669 | +0.162 | -0.154 | +0.555 | -0.052 |
| heat_death_diag_m60_cap0.9 | diag | 114 | 31 | 29 | +0.772 | +0.718 | +0.135 | -0.120 | +0.426 | -0.029 |
| heat_death_diag_m90_cap0.8 | diag | 50 | 25 | 21 | +0.640 | +0.576 | +0.232 | -0.306 | +0.889 | -0.149 |
| heat_death_diag_m90_cap0.85 | diag | 80 | 28 | 22 | +0.713 | +0.672 | +0.145 | -0.205 | +0.568 | -0.090 |
| heat_death_diag_m90_cap0.9 | diag | 103 | 29 | 27 | +0.757 | +0.720 | +0.119 | -0.165 | +0.446 | -0.062 |
| heat_death_diag_m120_cap0.8 | diag | 32 | 18 | 17 | +0.625 | +0.609 | -0.032 | -0.423 | +0.386 | -0.238 |
| heat_death_diag_m120_cap0.85 | diag | 52 | 21 | 18 | +0.750 | +0.694 | +0.035 | -0.239 | +0.306 | -0.076 |
| heat_death_diag_m120_cap0.9 | diag | 70 | 25 | 21 | +0.800 | +0.744 | +0.043 | -0.174 | +0.241 | -0.037 |
| false_fade_reheat_conflict_diag_tr0.3_gap0.5 | diag | 48 | 26 | 20 | +0.396 | +0.169 | +1.137 | +0.222 | +2.089 | +0.307 |
| false_fade_reheat_conflict_diag_tr0.3_gap1.0 | diag | 32 | 22 | 15 | +0.438 | +0.175 | +1.195 | +0.298 | +2.162 | +0.272 |
| false_fade_reheat_conflict_diag_tr0.3_gap1.5 | diag | 22 | 16 | 12 | +0.545 | +0.188 | +1.849 | +0.733 | +3.132 | +0.575 |
| false_fade_reheat_conflict_diag_tr0.5_gap0.5 | diag | 48 | 26 | 20 | +0.396 | +0.169 | +1.137 | +0.222 | +2.089 | +0.307 |
| false_fade_reheat_conflict_diag_tr0.5_gap1.0 | diag | 32 | 22 | 15 | +0.438 | +0.175 | +1.195 | +0.298 | +2.162 | +0.272 |
| false_fade_reheat_conflict_diag_tr0.5_gap1.5 | diag | 22 | 16 | 12 | +0.545 | +0.188 | +1.849 | +0.733 | +3.132 | +0.575 |
| false_fade_reheat_conflict_diag_tr1.0_gap0.5 | diag | 48 | 26 | 20 | +0.396 | +0.169 | +1.137 | +0.222 | +2.089 | +0.307 |
| false_fade_reheat_conflict_diag_tr1.0_gap1.0 | diag | 32 | 22 | 15 | +0.438 | +0.175 | +1.195 | +0.298 | +2.162 | +0.272 |
| false_fade_reheat_conflict_diag_tr1.0_gap1.5 | diag | 22 | 16 | 12 | +0.545 | +0.188 | +1.849 | +0.733 | +3.132 | +0.575 |
