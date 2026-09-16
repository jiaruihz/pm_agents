# METAR Reversal Expression Matrix v1 (pre-registered)

Generated: 2026-07-04T15:57:03.998095+00:00

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

- Denominator: 8096 settled city-date-hour snapshots, 2026-05-19..2026-07-02, 36 cities.
- Leg quote coverage: current_high_yes=92%, current_bracket_no=95%, d1_yes=88%, d2_yes=73%, high_tail_yes=58%, d1_no=84%, d2_no=68%
- K=2 pre-registered triggers; sensitivity grid is diagnostic only.

## Trigger and baseline table

| strategy | period | rows | dates | cities | win_rate | avg_ask | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline_all_current_high_yes | full | 4458 | 41 | 36 | +0.497 | +0.526 | -0.149 | -0.273 | +0.008 | -0.240 |
| baseline_all_current_bracket_no | full | 4861 | 41 | 36 | +0.356 | +0.387 | -0.273 | -0.364 | -0.171 | -0.335 |
| baseline_all_d1_yes | full | 5435 | 40 | 36 | +0.220 | +0.255 | -0.249 | -0.354 | -0.135 | -0.315 |
| baseline_all_d2_yes | full | 4485 | 40 | 36 | +0.169 | +0.191 | -0.362 | -0.456 | -0.253 | -0.414 |
| baseline_all_high_tail_yes | full | 1142 | 40 | 36 | +0.040 | +0.055 | -0.770 | -0.893 | -0.631 | -0.872 |
| baseline_all_d1_no | full | 4731 | 41 | 36 | +0.734 | +0.760 | -0.047 | -0.070 | -0.024 | -0.057 |
| baseline_all_d2_no | full | 3611 | 41 | 36 | +0.771 | +0.804 | -0.050 | -0.075 | -0.027 | -0.055 |
| heat_death_current_high_yes | full | 131 | 36 | 33 | +0.550 | +0.592 | -0.138 | -0.398 | +0.150 | -0.275 |
| heat_death_taker_plus_1c | full | 131 | 36 | 33 | +0.550 | +0.592 | -0.159 | -0.408 | +0.112 | -0.285 |
| heat_death_complement_same_hours | full | 4219 | 41 | 36 | +0.485 | +0.513 | -0.153 | -0.281 | +0.011 | -0.249 |
| heat_death_alt_d1_no | full | 134 | 36 | 33 | +0.709 | +0.748 | -0.026 | -0.176 | +0.124 | -0.100 |
| heat_death_alt_current_bracket_no | full | 126 | 36 | 32 | +0.429 | +0.487 | -0.111 | -0.397 | +0.199 | -0.266 |
| heat_death_alt_d1_yes | full | 130 | 35 | 32 | +0.300 | +0.331 | +0.002 | -0.382 | +0.425 | -0.225 |
| false_fade_reheat_conflict_d1yes | full | 47 | 25 | 18 | +0.298 | +0.145 | +0.610 | -0.316 | +1.727 | -0.175 |
| false_fade_reheat_conflict_taker_plus_1c | full | 47 | 25 | 18 | +0.298 | +0.145 | +0.514 | -0.351 | +1.551 | -0.207 |
| false_fade_reheat_conflict_complement_warming_pool | full | 1395 | 36 | 36 | +0.283 | +0.330 | -0.222 | -0.357 | -0.040 | -0.343 |
| false_fade_reheat_conflict_alt_current_high_yes | full | 43 | 23 | 17 | +0.674 | +0.852 | -0.238 | -0.461 | -0.038 | -0.316 |
| false_fade_reheat_conflict_alt_d2_yes | full | 37 | 22 | 15 | +0.000 | +0.044 | -1.000 | -1.000 | -1.000 | -1.000 |
| false_fade_reheat_conflict_alt_high_tail_yes | full | 5 | 4 | 5 | +0.000 | +0.009 | -1.000 | -1.000 | -1.000 |  |
| false_fade_reheat_conflict_alt_current_bracket_no | full | 47 | 25 | 18 | +0.298 | +0.180 | +0.336 | -0.405 | +1.222 | -0.341 |
| heat_death_month_2026-05 | monthly | 41 | 13 | 21 | +0.561 | +0.600 | -0.219 | -0.478 | +0.159 | -0.368 |
| heat_death_month_2026-06 | monthly | 89 | 22 | 26 | +0.551 | +0.592 | -0.092 | -0.435 | +0.296 | -0.286 |
| heat_death_month_2026-07 | monthly | 1 | 1 | 1 | +0.000 | +0.157 | -1.000 |  |  |  |
| false_fade_reheat_conflict_month_2026-05 | monthly | 16 | 9 | 10 | +0.438 | +0.165 | +1.723 | -0.340 | +4.415 | -0.383 |
| false_fade_reheat_conflict_month_2026-06 | monthly | 31 | 16 | 14 | +0.226 | +0.135 | +0.035 | -0.713 | +0.901 | -0.744 |

## Sensitivity diagnostic (not for selection)

| strategy | period | rows | dates | cities | win_rate | avg_ask | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
|---|---|---|---|---|---|---|---|---|---|---|
| heat_death_diag_m60_cap0.8 | diag | 101 | 35 | 30 | +0.485 | +0.509 | -0.147 | -0.450 | +0.225 | -0.327 |
| heat_death_diag_m60_cap0.85 | diag | 138 | 37 | 33 | +0.558 | +0.595 | -0.131 | -0.374 | +0.145 | -0.261 |
| heat_death_diag_m60_cap0.9 | diag | 173 | 39 | 34 | +0.630 | +0.653 | -0.097 | -0.300 | +0.130 | -0.198 |
| heat_death_diag_m90_cap0.8 | diag | 97 | 34 | 29 | +0.485 | +0.507 | -0.146 | -0.475 | +0.239 | -0.334 |
| heat_death_diag_m90_cap0.85 | diag | 131 | 36 | 33 | +0.550 | +0.592 | -0.138 | -0.398 | +0.150 | -0.275 |
| heat_death_diag_m90_cap0.9 | diag | 163 | 38 | 34 | +0.620 | +0.649 | -0.106 | -0.318 | +0.134 | -0.214 |
| heat_death_diag_m120_cap0.8 | diag | 78 | 30 | 24 | +0.462 | +0.509 | -0.270 | -0.560 | +0.034 | -0.386 |
| heat_death_diag_m120_cap0.85 | diag | 106 | 33 | 27 | +0.547 | +0.594 | -0.213 | -0.449 | +0.022 | -0.295 |
| heat_death_diag_m120_cap0.9 | diag | 130 | 35 | 29 | +0.615 | +0.647 | -0.167 | -0.375 | +0.021 | -0.231 |
| false_fade_reheat_conflict_diag_tr0.3_gap0.5 | diag | 56 | 26 | 20 | +0.268 | +0.143 | +0.422 | -0.329 | +1.377 | -0.242 |
| false_fade_reheat_conflict_diag_tr0.3_gap1.0 | diag | 47 | 25 | 18 | +0.298 | +0.145 | +0.610 | -0.316 | +1.727 | -0.175 |
| false_fade_reheat_conflict_diag_tr0.3_gap1.5 | diag | 42 | 25 | 15 | +0.333 | +0.145 | +0.801 | -0.215 | +1.993 | -0.064 |
| false_fade_reheat_conflict_diag_tr0.5_gap0.5 | diag | 56 | 26 | 20 | +0.268 | +0.143 | +0.422 | -0.329 | +1.377 | -0.242 |
| false_fade_reheat_conflict_diag_tr0.5_gap1.0 | diag | 47 | 25 | 18 | +0.298 | +0.145 | +0.610 | -0.316 | +1.727 | -0.175 |
| false_fade_reheat_conflict_diag_tr0.5_gap1.5 | diag | 42 | 25 | 15 | +0.333 | +0.145 | +0.801 | -0.215 | +1.993 | -0.064 |
| false_fade_reheat_conflict_diag_tr1.0_gap0.5 | diag | 56 | 26 | 20 | +0.268 | +0.143 | +0.422 | -0.329 | +1.377 | -0.242 |
| false_fade_reheat_conflict_diag_tr1.0_gap1.0 | diag | 47 | 25 | 18 | +0.298 | +0.145 | +0.610 | -0.316 | +1.727 | -0.175 |
| false_fade_reheat_conflict_diag_tr1.0_gap1.5 | diag | 42 | 25 | 15 | +0.333 | +0.145 | +0.801 | -0.215 | +1.993 | -0.064 |
