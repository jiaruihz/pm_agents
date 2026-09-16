# 2026-06-29 Current-High YES From Regime-NO Misroutes v0

Generated UTC: `2026-06-29T07:14:44+00:00`

## Question

Jeddah 2026-06-21 exposed a clean expression mismatch: the NO route treated a stale/pullback state as if it were still fresh runway. This report tests the inverse expression on the same rows: buy the current-high YES after the day has already touched the high and the latest state is pullback/stall/fade.

This is research/shadow evidence only. It reuses selected historical rows from `regime_routed_no_mechanism_split_v2`; it is not a live approval.

## Evidence Snapshot

- Source rows: `271` selected regime-routed NO rows, date range `2026-05-20`..`2026-06-23`.
- Inverse universe: `35` rows where v2 labels the old route as `unapproved_stale_current_no`.
- Pricing: executable historical `current_yes_ask` and `current_bracket_no_ask` from the same selected trade details.
- Unit: one candidate row = one city + target date + decision snapshot + bracket. PnL assumes `$5` stake at top ask.

## Jeddah 2026-06-21

The 35C high was first printed at 12:00 local. The latest observation available for the 13:30 decision was the 13:00 local METAR/IEM row, which had already fallen back to 33.9C. At that decision snapshot the 35C YES ask was 0.57 and the 35C NO ask was 0.44. The day later revisited 35C at 14:00 local, so 35 YES won and 35 NO lost.

Observation timeline:

| UTC | local | tmpf | temp_c | running_c | wind_kt | note |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-06-21 06:00:00+00:00 | 2026-06-21 09:00 | 89.600 | 32.000 | 32.000 | 0.000 |  |
| 2026-06-21 07:00:00+00:00 | 2026-06-21 10:00 | 93.200 | 34.000 | 34.000 | 6.000 |  |
| 2026-06-21 08:00:00+00:00 | 2026-06-21 11:00 | 93.200 | 34.000 | 34.000 | 8.000 |  |
| 2026-06-21 09:00:00+00:00 | 2026-06-21 12:00 | 95.000 | 35.000 | 35.000 | 8.000 | first 35C high |
| 2026-06-21 10:00:00+00:00 | 2026-06-21 13:00 | 93.200 | 34.000 | 35.000 | 9.000 | dropped to 33.9C; latest obs for 10:30Z decision |
| 2026-06-21 11:00:00+00:00 | 2026-06-21 14:00 | 95.000 | 35.000 | 35.000 | 8.000 | equal-high revisit; still 35C bracket |
| 2026-06-21 12:00:00+00:00 | 2026-06-21 15:00 | 93.200 | 34.000 | 35.000 | 9.000 |  |
| 2026-06-21 13:00:00+00:00 | 2026-06-21 16:00 | 93.200 | 34.000 | 35.000 | 8.000 |  |
| 2026-06-21 14:00:00+00:00 | 2026-06-21 17:00 | 91.400 | 33.000 | 35.000 | 8.000 |  |
| 2026-06-21 15:00:00+00:00 | 2026-06-21 18:00 | 91.400 | 33.000 | 35.000 | 6.000 |  |

Price timeline for Jeddah 35C:

| local | YES ask | NO ask | NO ask size | fcst max C | fcst peak hr | metar src |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-06-21 10:00:20 | 0.170 | 0.850 | 30.000 | 37.900 | 13 | none |
| 2026-06-21 10:31:31 | 0.180 | 0.850 | 40.050 | 37.900 | 13 | none |
| 2026-06-21 11:00:42 | 0.180 | 0.840 | 26.060 | 38.600 | 13 | none |
| 2026-06-21 11:30:18 | 0.210 | 0.800 | 71.170 | 38.600 | 13 | none |
| 2026-06-21 12:00:53 | 0.220 | 0.820 | 17.500 | 38.600 | 13 | none |
| 2026-06-21 12:30:37 | 0.190 | 0.840 | 7.000 | 38.600 | 13 | none |
| 2026-06-21 13:00:22 | 0.550 | 0.710 | 45.000 | 38.600 | 13 | none |
| 2026-06-21 13:30:53 | 0.570 | 0.440 | 57.000 | 38.600 | 13 | none |
| 2026-06-21 14:00:38 | 0.670 | 0.420 | 7.720 | 38.600 | 13 | none |
| 2026-06-21 14:30:51 | 0.720 | 0.380 | 7.210 | 38.600 | 13 | none |
| 2026-06-21 15:00:34 | 0.850 | 0.230 | 6.000 | 38.600 | 13 | none |

## Slice Result

Current-high YES on stale-NO rows:

| slice | rows | dates | cities | wins | win rate | avg ask | PnL | ROI | CI low | CI high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all_unapproved_stale | 35 | 29 | 19 | 22 | +62.9% | 0.706 | $-21.73 | -12.4% | -37.8% | +11.3% |
| pullback_uncertain | 7 | 7 | 5 | 7 | +100.0% | 0.768 | $+11.81 | +33.7% | +19.2% | +52.0% |
| plateau_near_high | 13 | 12 | 9 | 8 | +61.5% | 0.637 | $-5.40 | -8.3% | -43.7% | +26.5% |
| mature_fade | 11 | 11 | 10 | 6 | +54.5% | 0.768 | $-16.20 | -29.5% | -66.5% | +9.7% |
| clock_unknown | 4 | 4 | 4 | 1 | +25.0% | 0.647 | $-11.94 | -59.7% | -100.0% | +21.0% |
| pullback_or_plateau | 20 | 17 | 12 | 15 | +75.0% | 0.683 | $+6.41 | +6.4% | -21.2% | +30.9% |

YES vs the old NO side on the same rows:

| slice | side | rows | wins | avg ask | PnL | ROI |
| --- | --- | --- | --- | --- | --- | --- |
| all_unapproved_stale | yes | 35 | 22 | 0.706 | $-21.73 | -12.4% |
| all_unapproved_stale | no | 35 | 13 | 0.385 | $+28.34 | +16.2% |
| pullback_uncertain | yes | 7 | 7 | 0.768 | $+11.81 | +33.7% |
| pullback_uncertain | no | 7 | 0 | 0.316 | $-35.00 | -100.0% |
| plateau_near_high | yes | 13 | 8 | 0.637 | $-5.40 | -8.3% |
| plateau_near_high | no | 13 | 5 | 0.477 | $-13.72 | -21.1% |
| mature_fade | yes | 11 | 6 | 0.768 | $-16.20 | -29.5% |
| mature_fade | no | 11 | 5 | 0.304 | $+39.60 | +72.0% |

The cleanest slice is `pullback_uncertain`: 7 rows, 7 wins, YES ROI +33.7%; the old NO side on those exact rows was 0 wins and -100% ROI. This is not enough sample for live, but it is a coherent mechanism worth shadowing.

Pullback details:

| date | city | bracket | current | running | YES ask | NO ask | YES win | YES ROI | min since high | peak delta h | 1h trend F |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-31 | TelAviv | 28 | 27.222 | 27.778 | 0.900 | 0.250 | 1.000 | 0.111 | 100.883 | 1.000 | 0.000 |
| 2026-06-05 | Helsinki | 19 | 17.778 | 18.889 | 0.677 | 0.429 | 1.000 | 0.477 | 100.883 | -6.000 | 0.000 |
| 2026-06-07 | NYC | 80-81 | 72.000 | 80.000 | 0.679 | 0.350 | 1.000 | 0.473 | 99.883 | -1.000 | -2.000 |
| 2026-06-11 | TelAviv | 29 | 27.778 | 28.889 | 0.900 | 0.110 | 1.000 | 0.111 | 40.883 | 1.000 | 0.000 |
| 2026-06-13 | Helsinki | 15 | 13.889 | 15.000 | 0.893 | 0.373 | 1.000 | 0.120 | 70.883 | 1.000 | -1.800 |
| 2026-06-19 | Busan | 28 | 26.111 | 27.778 | 0.760 | 0.260 | 1.000 | 0.316 | 91.000 | 2.000 | -3.600 |
| 2026-06-21 | Jeddah | 35 | 33.889 | 35.000 | 0.570 | 0.440 | 1.000 | 0.754 | 90.883 | 0.000 | -1.800 |

## Interpretation

This is not the old broad `peak fade` rule revived as-is. The old family mixed several states: early false fades, plateau, mature fades, and clock-unknown rows. Here the candidate is narrower:

- `pullback_uncertain`: high was already printed; latest report has pulled back; the market still offers substantial NO because forecast/runway context says reheat is possible. In this sample, buying the high-bracket YES was correct.
- `plateau_near_high`: latest report is still near the high rather than a real pullback. This was mixed and negative for YES; do not merge it into the candidate.
- `mature_fade`: point estimate is noisy and not clearly positive for YES in this selected universe. It may belong to a different low-price NO tail or separate current-YES fade model, not this expression.
- `clock_unknown`: exclude from live/shadow scoring until the observation clock is fixed.

## Verdict

significance=FAIL, baseline=PARTIAL, forward=FAIL/TOO_THIN, conclusion=inconclusive_shadow_candidate.

Recommended next action: add a zero-notional shadow head named `current_high_yes_pullback_uncertain_v0` that logs these conditions but does not place live orders. It should use shared observation-cache features, require a known first-touch/running-max clock, and evaluate against the old current-YES peak/fade heads on a fixed denominator.

Artifacts:

- Summary JSON: `docs/analysis/2026-06/generated/current_high_yes_from_regime_no_misroutes_v0/summary.json`
- Slice summary CSV: `docs/analysis/2026-06/generated/current_high_yes_from_regime_no_misroutes_v0/slice_summary.csv`
- Details CSV: `docs/analysis/2026-06/generated/current_high_yes_from_regime_no_misroutes_v0/stale_no_inverse_yes_details.csv`
- Jeddah observation timeline: `docs/analysis/2026-06/generated/current_high_yes_from_regime_no_misroutes_v0/jeddah_2026_06_21_observation_timeline.csv`
- Jeddah price timeline: `docs/analysis/2026-06/generated/current_high_yes_from_regime_no_misroutes_v0/jeddah_2026_06_21_price_timeline.csv`
