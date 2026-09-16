# First-seen exact-bracket residual v1

Status: inconclusive; infrastructure replay only

## Action

Keep zero-notional collection. Do not create a live selector from this replay.

## Target

Estimate P(exact bracket | PIT event checkpoint) and test it against same-row market probability; this replay uses the existing paper-snapshot model as the initial model baseline.

## Signal funnel

- information_events: 4934
- material_events_with_checkpoint: 3200
- built_checkpoints: 1357
- mapped_candidate_expressions: 20074
- scored_candidate_expressions: 19388
- positive_mid_residual_expressions: 9737

## Evidence funnel

- collector_exact_events: 0
- archive_known_available_events: 4934
- fresh_post_event_book_expressions: 19958
- settled_expressions: 20074
- scored_and_settled_expressions: 19388

## Same-row probability baseline

- rows: 10037 across 2 target dates
- model Brier: 0.091024
- market Brier: 0.055489
- model − market Brier: +0.035535
- model logloss: 0.319832
- market logloss: 0.176557
- model − market logloss: +0.143275

## Pre/post event update

- rows with both pre/post books and model values: 9803
- target dates: 2
- mean absolute market move: 0.016059
- mean absolute model move: 0.007234
- market Brier before → after: 0.058831 → 0.056306
- model Brier before → after: 0.091979 → 0.091963

## Fee-adjusted taker expression diagnostics

| min edge | expressions | dates | win rate | avg entry | ROI | expressions/day |
|---:|---:|---:|---:|---:|---:|---:|
| 0% | 8680 | 2 | 39.40% | 38.18% | 2.33% | 4340.00 |
| 2% | 5145 | 2 | 29.83% | 27.91% | 5.05% | 2572.50 |
| 5% | 4068 | 2 | 30.26% | 28.03% | 5.90% | 2034.00 |

## Interpretation

- This is archive-known availability, not exact collector first-seen latency evidence.
- The replay has too few independent target dates for bootstrap or live promotion.
- Each row above is an event-condition expression, not a deployable trade count; no city-day lock or execution policy is applied.
- Threshold rows are diagnostics on a fixed denominator, not eligibility gates.
- Forward collection must retain every event/checkpoint/expression and missing-book row.

significance=NA: fewer than the required independent target dates
