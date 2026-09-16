# Runway d1 YES Reversal Expression Matrix v1

Generated: 2026-07-03T14:21:44.547985+00:00

## Verdict

```text
scope: runway_d1_yes_reversal continuation; Fabel market-pricing state is absorbed
       into the original runway branch, not split into an anchoring strategy.
action: shadow research only; no live selector / runner / notional change.
PnL: canonical settled only. Unsettled rows are telemetry/coverage only.
```

Main read:

- `one_step_runway` is the only place where d1 YES is plausibly the right expression;
  the cheap-d1 candidate slice is the cleanest forward telemetry head.
- `skip_over_runway` does not rescue d1 YES. In conceded-current states, the matrix
  should route research toward d2/current-NO/basket/avoid, but the settled CI still
  decides whether any of those are more than telemetry.
- `fake_runway` is not an entry state; it is an avoid/TP diagnostic until fresh-forward
  data proves otherwise.

Contract gate read:

```text
candidate_one_step_d1_yes_cheap -> d1_yes:
  rows=162 / dates=30 / cities=25 / avg ask=0.159 / ROI=+32.7%
  date-block CI [-13.3%, +83.9%]
  significance=FAIL, baseline=NA, forward=NA
  conclusion=inconclusive_shadow_research_only

candidate_skip_over_current_conceded:
  d1_yes ROI=-30.3% CI [-41.9%, -16.0%] => bad expression confirmed for this state
  d2_yes ROI=-8.3% CI [-16.5%, +0.9%], current_bracket_NO ROI=+0.4% CI [-2.3%, +2.7%]
  significance=FAIL for positive edge, baseline=NA, forward=NA
  conclusion=inconclusive; avoid d1 YES here, keep d2/current-NO/basket as telemetry only

recent trigger coverage:
  2026-06-21..2026-07-01 corrected mirror has 0 one_step_runway rows.
  That is trigger dormancy, not forward failure.
```

Runway state taxonomy used here:

- `no_runway`: `forecast_gap_native < 0.50` or missing.
- `fake_runway`: forecast runway exists, but obs says it is probably stale/cooling
  (`temp_trend_1h_f <= -0.5`, forecast peak passed by >=1h, or running max stale >=120m).
- `one_step_runway`: runway exists, not fake, and market still treats current as live
  (`current_high YES ask >=0.40`) or neutral with gap <2.25.
- `skip_over_runway`: runway exists, not fake, and market has conceded current or
  forecast gap is >=2.25 native units.

## Data Snapshot

- Feature rows: 139998 quote rows from corrected feature-factory mirror.
- Matrix denominator: 12244 city-date-snapshot rows, 2026-05-19..2026-07-01, 36 cities.
- Canonical settled PnL subset: 11722 rows, 2026-05-19..2026-06-30.
- Unsettled telemetry-only subset: 522 rows, 2026-06-27..2026-07-01.
- Build note: `sync_weather_remote.sh` completed; `run_stack.sh` rebuilt DB/facts/gate but exited after DB work because FE port 5174 stayed busy. CLOB coverage gate was run separately and passed.
- Skipped snapshots while building ladder matrix: {'empty_yes': 7, 'missing_current_bracket': 5742, 'no_current_no_quote': 38}.

Quote coverage:

| quote | all | recent_2026_06_21_plus | settled |
|---|---|---|---|
| d1_yes_ask | +0.898 | +0.881 | +0.898 |
| d2_yes_ask | +0.742 | +0.722 | +0.742 |
| high_tail_yes_ask | +0.463 | +0.443 | +0.463 |
| current_bracket_no_ask | +0.959 | +0.976 | +0.958 |

## State Counts

| runway_state | market_current_state | rows | settled_rows | dates | cities |
|---|---|---|---|---|---|
| fake_runway | current_conceded | 538 | 462 | 42 | 36 |
| fake_runway | current_live | 1389 | 1353 | 42 | 35 |
| fake_runway | neutral | 8 | 8 | 7 | 7 |
| fake_runway | unknown | 168 | 165 | 36 | 30 |
| no_runway | current_conceded | 1122 | 1015 | 43 | 36 |
| no_runway | current_live | 5558 | 5281 | 44 | 36 |
| no_runway | neutral | 52 | 47 | 27 | 27 |
| no_runway | unknown | 645 | 627 | 43 | 35 |
| one_step_runway | current_live | 412 | 412 | 32 | 34 |
| one_step_runway | neutral | 22 | 22 | 17 | 16 |
| one_step_runway | unknown | 9 | 9 | 6 | 4 |
| skip_over_runway | current_conceded | 2178 | 2178 | 35 | 36 |
| skip_over_runway | current_live | 119 | 119 | 32 | 21 |
| skip_over_runway | neutral | 9 | 9 | 8 | 7 |
| skip_over_runway | unknown | 15 | 15 | 9 | 7 |

## One-Step Runway

| slice | expr | rows | dates | cities | win_rate | avg_ask | roi | roi_ci_low | roi_ci_high |
|---|---|---|---|---|---|---|---|---|---|
| one_step_runway | current_bracket_no | 432 | 32 | 34 | +0.424 | +0.412 | +0.039 | -0.170 | +0.237 |
| one_step_runway | d1_yes | 425 | 32 | 34 | +0.365 | +0.355 | +0.105 | -0.126 | +0.324 |
| one_step_runway | d2_yes | 389 | 32 | 33 | +0.067 | +0.081 | -0.279 | -0.630 | +0.156 |
| one_step_runway | high_tail_yes | 295 | 32 | 33 | +0.000 | +0.012 | -1.000 | -1.000 | -1.000 |
| one_step_runway | hotter_basket | 410 | 32 | 33 | +0.439 | +0.444 | +0.033 | -0.166 | +0.217 |

## Cheap d1 Candidate Slice

`candidate_one_step_d1_yes_cheap` = `one_step_runway` with `d1_yes_ask <= 0.30`.

| slice | expr | rows | dates | cities | win_rate | avg_ask | roi | roi_ci_low | roi_ci_high |
|---|---|---|---|---|---|---|---|---|---|
| candidate_one_step_d1_yes_cheap | current_bracket_no | 163 | 30 | 25 | +0.258 | +0.192 | +0.153 | -0.239 | +0.579 |
| candidate_one_step_d1_yes_cheap | d1_yes | 162 | 30 | 25 | +0.241 | +0.159 | +0.327 | -0.133 | +0.839 |
| candidate_one_step_d1_yes_cheap | d2_yes | 133 | 29 | 24 | +0.015 | +0.035 | -0.901 | -1.000 | -0.731 |
| candidate_one_step_d1_yes_cheap | high_tail_yes | 79 | 26 | 24 | +0.000 | +0.009 | -1.000 | -1.000 | -1.000 |
| candidate_one_step_d1_yes_cheap | hotter_basket | 152 | 30 | 24 | +0.263 | +0.194 | +0.179 | -0.214 | +0.614 |

## Skip-Over Runway

| slice | expr | rows | dates | cities | win_rate | avg_ask | roi | roi_ci_low | roi_ci_high |
|---|---|---|---|---|---|---|---|---|---|
| skip_over_runway | current_bracket_no | 1263 | 34 | 36 | +0.876 | +0.871 | -0.001 | -0.045 | +0.039 |
| skip_over_runway | d1_yes | 2085 | 35 | 36 | +0.264 | +0.312 | -0.282 | -0.386 | -0.151 |
| skip_over_runway | d2_yes | 2111 | 35 | 36 | +0.315 | +0.328 | -0.102 | -0.187 | -0.007 |
| skip_over_runway | high_tail_yes | 2043 | 35 | 36 | +0.031 | +0.041 | -0.634 | -0.849 | -0.375 |
| skip_over_runway | hotter_basket | 2132 | 35 | 36 | +0.592 | +0.660 | -0.177 | -0.230 | -0.120 |

## Conceded-Current Skip Slice

`candidate_skip_over_current_conceded` = `skip_over_runway` and `market_current_state=current_conceded`.

| slice | expr | rows | dates | cities | win_rate | avg_ask | roi | roi_ci_low | roi_ci_high |
|---|---|---|---|---|---|---|---|---|---|
| candidate_skip_over_current_conceded | current_bracket_no | 1140 | 34 | 36 | +0.925 | +0.923 | +0.004 | -0.023 | +0.027 |
| candidate_skip_over_current_conceded | d1_yes | 1970 | 35 | 36 | +0.258 | +0.311 | -0.303 | -0.419 | -0.160 |
| candidate_skip_over_current_conceded | d2_yes | 2008 | 35 | 36 | +0.328 | +0.339 | -0.083 | -0.165 | +0.009 |
| candidate_skip_over_current_conceded | high_tail_yes | 1970 | 35 | 36 | +0.032 | +0.042 | -0.620 | -0.844 | -0.350 |
| candidate_skip_over_current_conceded | hotter_basket | 2023 | 35 | 36 | +0.600 | +0.672 | -0.187 | -0.241 | -0.131 |

## Fake Runway

| slice | expr | rows | dates | cities | win_rate | avg_ask | roi | roi_ci_low | roi_ci_high |
|---|---|---|---|---|---|---|---|---|---|
| fake_runway | current_bracket_no | 1295 | 40 | 35 | +0.274 | +0.300 | -0.364 | -0.573 | -0.107 |
| fake_runway | d1_yes | 1359 | 40 | 36 | +0.157 | +0.185 | -0.383 | -0.562 | -0.167 |
| fake_runway | d2_yes | 1059 | 40 | 36 | +0.134 | +0.157 | -0.442 | -0.628 | -0.222 |
| fake_runway | high_tail_yes | 815 | 40 | 36 | +0.006 | +0.019 | -0.939 | -0.986 | -0.876 |
| fake_runway | hotter_basket | 1359 | 40 | 36 | +0.263 | +0.315 | -0.462 | -0.601 | -0.304 |

## Recent Coverage

Recent state totals:

| runway_state | rows | settled_rows | dates | cities |
|---|---|---|---|---|
| fake_runway | 416 | 301 | 10 | 35 |
| no_runway | 2179 | 1772 | 11 | 36 |
| skip_over_runway | 14 | 14 | 3 | 4 |

Recent state by date:

| target_date | runway_state | rows | settled_rows | cities |
|---|---|---|---|---|
| 2026-06-21 | fake_runway | 49 | 49 | 23 |
| 2026-06-21 | no_runway | 265 | 265 | 35 |
| 2026-06-22 | fake_runway | 51 | 51 | 21 |
| 2026-06-22 | no_runway | 237 | 237 | 31 |
| 2026-06-23 | fake_runway | 27 | 27 | 16 |
| 2026-06-23 | no_runway | 193 | 193 | 26 |
| 2026-06-24 | fake_runway | 34 | 34 | 11 |
| 2026-06-24 | no_runway | 180 | 180 | 25 |
| 2026-06-25 | fake_runway | 54 | 54 | 21 |
| 2026-06-25 | no_runway | 295 | 295 | 35 |
| 2026-06-25 | skip_over_runway | 2 | 2 | 2 |
| 2026-06-26 | fake_runway | 53 | 53 | 22 |
| 2026-06-26 | no_runway | 273 | 273 | 35 |
| 2026-06-26 | skip_over_runway | 1 | 1 | 1 |
| 2026-06-27 | fake_runway | 67 | 0 | 24 |
| 2026-06-27 | no_runway | 234 | 0 | 34 |
| 2026-06-28 | fake_runway | 14 | 0 | 5 |
| 2026-06-28 | no_runway | 16 | 0 | 8 |
| 2026-06-29 | no_runway | 136 | 136 | 24 |
| 2026-06-30 | fake_runway | 33 | 33 | 17 |
| 2026-06-30 | no_runway | 193 | 193 | 25 |
| 2026-06-30 | skip_over_runway | 11 | 11 | 1 |
| 2026-07-01 | fake_runway | 34 | 0 | 14 |
| 2026-07-01 | no_runway | 157 | 0 | 26 |

## Output Files

- `docs/analysis/2026-07/generated/runway_d1_yes_reversal_expression_matrix_v1/runway_matrix_rows.csv`
- `docs/analysis/2026-07/generated/runway_d1_yes_reversal_expression_matrix_v1/settled_expression_summary.csv`
- `docs/analysis/2026-07/generated/runway_d1_yes_reversal_expression_matrix_v1/state_counts.csv`
- `docs/analysis/2026-07/generated/runway_d1_yes_reversal_expression_matrix_v1/recent_coverage.csv`
- `docs/analysis/2026-07/2026-07-03-runway-d1-yes-reversal-expression-matrix-v1.json`
