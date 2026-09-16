# 2026-06-29 Pullback-Uncertain Current-High YES Mechanism v0

Generated UTC: `2026-06-29T07:44:24+00:00`

## Question

The prior inverse test found `pullback_uncertain` current-high YES had 7 wins in 7 historical rows. This report checks whether that is a real mechanism or a coincidence by replaying each row's observation path and same-bracket orderbook path where available.

Key distinction: current-bracket NO after a pullback does **not** only need "no break above". It needs the final winning bracket to be outside the current bracket. A later equal-bracket revisit is enough to kill NO and make current-high YES win.

## Case Summary

| date | city | bracket | first hit | latest obs | latest temp | after same | after above | after max | YES ask | NO ask | YES ROI | mechanism |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-31 | TelAviv | 28 | 2026-05-31 12:50 | 2026-05-31 14:20 | 27.0 |  |  | 27.0 | 0.9 | 0.25 | +11.1% | already_printed_high_holds |
| 2026-06-05 | Helsinki | 19 | 2026-06-05 08:20 | 2026-06-05 11:20 | 18.0 | 2026-06-05 14:50 |  | 19.0 | 0.677 | 0.429 | +47.7% | same_bracket_revisit_risk |
| 2026-06-07 | NYC | 80-81 | 2026-06-07 12:51 | 2026-06-07 14:18 | 78.0 | 2026-06-07 14:51 |  | 81.0 | 0.679 | 0.35 | +47.3% | same_bracket_revisit_risk |
| 2026-06-11 | TelAviv | 29 |  | 2026-06-11 02:50 | 23.0 |  |  | NA | 0.9 | 0.11 | +11.1% | source_gap_observation_path |
| 2026-06-13 | Helsinki | 15 | 2026-06-13 08:50 | 2026-06-13 13:20 | 14.0 |  |  | 13.0 | 0.893 | 0.373 | +12.0% | already_printed_high_holds |
| 2026-06-19 | Busan | 28 | 2026-06-19 11:00 | 2026-06-19 12:05 | 26.0 |  |  | 27.0 | 0.76 | 0.26 | +31.6% | already_printed_high_holds |
| 2026-06-21 | Jeddah | 35 | 2026-06-21 12:00 | 2026-06-21 13:00 | 34.0 | 2026-06-21 14:00 |  | 35.0 | 0.57 | 0.44 | +75.4% | same_bracket_revisit_risk |

## Pattern Summary

| mechanism | rows | avg YES ask | avg NO ask | avg YES ROI |
| --- | --- | --- | --- | --- |
| already_printed_high_holds | 3 | 0.851 | 0.294 | +18.2% |
| same_bracket_revisit_risk | 3 | 0.642 | 0.406 | +56.8% |
| source_gap_observation_path | 1 | 0.9 | 0.11 | +11.1% |

## Compact Observation Paths

`temp(bucket)` uses the market's native unit. `latest` marks the observation available near the decision snapshot; `hit` means the current bracket was touched; `above` means the day later moved to a higher bracket.

| date | city | bracket | obs path local: temp(bucket) |
| --- | --- | --- | --- |
| 2026-05-31 | TelAviv | 28 | 09:50 27.0(27) -> 10:20 26.0(26) -> 10:50 27.0(27) -> 11:20 27.0(27) -> 11:50 27.0(27) -> 12:20 27.0(27) -> 12:50 28.0(28) hit -> 13:20 27.0(27) -> 13:50 27.0(27) latest -> 14:20 27.0(27) latest -> 14:50 27.0(27) -> 15:20 27.0(27) -> 15:50 27.0(27) -> 16:20 27.0(27) -> 16:50 27.0(27) -> 17:20 26.0(26) -> 17:50 26.0(26) -> 18:20 26.0(26) |
| 2026-06-05 | Helsinki | 19 | 06:50 17.0(17) -> 07:20 18.0(18) -> 07:50 18.0(18) -> 08:20 19.0(19) hit -> 08:50 19.0(19) hit -> 09:20 19.0(19) hit -> 09:50 19.0(19) hit -> 10:20 18.0(18) -> 10:50 18.0(18) latest -> 11:20 18.0(18) latest -> 11:50 18.0(18) -> 12:20 18.0(18) -> 12:50 18.0(18) -> 13:20 18.0(18) -> 13:50 18.0(18) -> 14:20 18.0(18) -> 14:50 19.0(19) hit -> 15:20 19.0(19) hit -> 15:50 19.0(19) hit -> 16:20 19.0(19) hit -> 16:50 18.0(18) -> 17:20 18.0(18) -> 17:50 17.0(17) -> 18:20 18.0(18) -> 18:50 18.0(18) -> 19:20 18.0(18) -> 19:50 18.0(18) -> 20:20 18.0(18) -> 20:50 18.0(18) -> 21:20 17.0(17) -> 21:50 17.0(17) -> 22:20 17.0(17) -> 22:50 17.0(17) |
| 2026-06-07 | NYC | 80-81 | 09:51 78.0(78) -> 10:51 79.0(79) -> 11:51 79.0(79) -> 12:51 80.0(80) hit -> 13:51 72.0(72) latest -> 14:18 78.0(78) latest -> 14:51 80.0(80) hit -> 15:51 81.0(81) hit -> 16:51 81.0(81) hit -> 17:51 80.0(80) hit -> 18:51 78.0(78) |
| 2026-06-11 | TelAviv | 29 |  |
| 2026-06-13 | Helsinki | 15 | 00:20 13.0(13) -> 07:20 13.0(13) -> 07:50 14.0(14) -> 08:20 14.0(14) -> 08:50 15.0(15) hit -> 09:20 14.0(14) -> 09:50 15.0(15) hit -> 10:20 15.0(15) hit -> 10:50 15.0(15) hit -> 11:20 15.0(15) hit -> 11:50 15.0(15) hit -> 12:20 15.0(15) hit -> 12:50 14.0(14) latest -> 13:20 14.0(14) latest -> 13:50 13.0(13) -> 14:20 13.0(13) -> 14:50 12.0(12) -> 15:20 13.0(13) -> 15:50 12.0(12) -> 16:20 12.0(12) |
| 2026-06-19 | Busan | 28 | 09:00 27.0(27) -> 10:00 27.0(27) -> 11:00 28.0(28) hit -> 12:00 26.0(26) latest -> 12:05 26.0(26) latest -> 13:00 27.0(27) -> 13:16 26.0(26) -> 13:20 26.0(26) -> 13:23 26.0(26) -> 14:00 27.0(27) -> 14:40 26.0(26) -> 15:00 26.0(26) -> 15:12 26.0(26) -> 15:39 26.0(26) -> 16:00 26.0(26) -> 16:02 26.0(26) -> 16:18 26.0(26) |
| 2026-06-21 | Jeddah | 35 | 10:00 34.0(34) -> 11:00 34.0(34) -> 12:00 35.0(35) hit -> 13:00 34.0(34) latest -> 14:00 35.0(35) hit -> 15:00 34.0(34) -> 16:00 34.0(34) -> 17:00 33.0(33) -> 18:00 33.0(33) |

## Mechanism Read

- `already_printed_high_holds` means the current bracket had already been officially printed before decision; no later same-bracket revisit was required. YES wins if no higher bracket appears.
- `same_bracket_revisit_risk` is the Jeddah-style version: the day had already printed the high bracket, then pulled back, and later revisited the same bracket without breaking above. That is enough for current-high YES and fatal for NO.
- In the 6 rows with usable observation path, none required a higher-bracket break for YES to win. The real risk to YES was only a later higher bracket, not the absence of reheat. TelAviv 2026-06-11 has settlement/feature evidence but incomplete IEM path in this replay, so it stays a source-gap row.
- This means the 35 NO at 0.44 in Jeddah was not obviously rich. It was priced against a high probability that 35C remained/revisited; forecast peak near 13:00 did not remove equal-bracket revisit risk.
- The expression is different from broad peak fade: it should target "already printed high bracket + observed pullback + no evidence of higher-bracket break", not all stalled/fade states.

## Data Caveats

- Observation timelines are research IEM/METAR replays, not proof that every row was live-visible at that exact second. A live shadow head must use the shared observation cache and mark missing fields as unknown.
- Same-bracket price timelines are only available for a subset of these rows in the current paper-snapshot mirror. Decision-time YES/NO asks come from the selected trade-details evidence layer for all 7 rows.
- Sample size is 7 rows. This is a mechanism candidate, not a live-ready strategy.

Artifacts:

- Case summary: `docs/analysis/2026-06/generated/pullback_uncertain_current_high_yes_mechanism_v0/case_summary.csv`
- Observation timelines: `docs/analysis/2026-06/generated/pullback_uncertain_current_high_yes_mechanism_v0/observation_timelines.csv`
- Price timelines: `docs/analysis/2026-06/generated/pullback_uncertain_current_high_yes_mechanism_v0/price_timelines.csv`
- Compact timelines: `docs/analysis/2026-06/generated/pullback_uncertain_current_high_yes_mechanism_v0/compact_observation_timelines.csv`
