# HeadA Missed Winner Execution Audit v1

Generated: 2026-07-09T02:10:28Z

## Scope

This audits the 9 settled 2026-07-01..2026-07-07 opportunity winners from `low_price_yes_source_quality_score_v2` and asks why they were not all live filled tickets.

## Verdict

The gap is not one single fill filter. It is a mix of: pre-live/order-window rows, one token-resolution bug, post-freeze selector/execution blockers, and one actual filled winner.

## Reason Summary

| missed_reason | rows | cities | counterfactual_pnl |
| --- | --- | --- | --- |
| before_live_order_window | 1 | 1 | 8.0746125 |
| blocked_dist_le0_boundary | 3 | 3 | 21.0681925 |
| blocked_fresh_ask_exceeds_cushion | 2 | 2 | 12.49568 |
| blocked_missing_yes_token_id | 1 | 1 | 5.5917675 |
| filled_live | 1 | 1 | 7.1681601 |
| not_in_live_runner_matching_rows | 1 | 1 | 5.551083675 |

## Ticket Detail

| target_date | city | bracket | entry_price | counterfactual_pnl | missed_reason | runtime_blockers | first_runtime_ts | fresh_best_ask_min | max_taker_price | live_filled_ticket |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-01 | Chongqing | 30+ | 0.185 | 8.0746125 | before_live_order_window | {} |  | nan | nan | False |
| 2026-07-02 | Paris | 26 | 0.065 | 5.5917675 | blocked_missing_yes_token_id | {"decision_snapshot_too_stale": 270, "missing_yes_token_id": 14} | 2026-07-02T02:17:03Z | nan | nan | False |
| 2026-07-02 | Wuhan | 31 | 0.0715 | 5.551083675 | not_in_live_runner_matching_rows | {} |  | nan | nan | False |
| 2026-07-05 | Helsinki | 17 | 0.0995 | 7.1681601 | filled_live | {"duplicate_submitted_signal": 518, "lifecycle_book_fetch_failed": 108, "lifecycle_no_action": 180, "lifecycle_no_asks": 63, "planned": 10, "simulated_open": 1} | 2026-07-04T19:11:19Z | 0.1 | 0.1 | True |
| 2026-07-06 | Austin | 98-99 | 0.06 | 5.62308 | blocked_dist_le0_boundary | {"dist_lt0_cold_or_inside_forecast_tail_v1": 228} | 2026-07-06T07:23:33Z | nan | nan | False |
| 2026-07-06 | Dallas | 98-99 | 0.165 | 8.281112499999999 | blocked_dist_le0_boundary | {"dist_lt0_cold_or_inside_forecast_tail_v1": 228} | 2026-07-06T07:23:33Z | nan | nan | False |
| 2026-07-06 | Seattle | 84-85 | 0.1 | 7.164 | blocked_dist_le0_boundary | {"dist_lt0_cold_or_inside_forecast_tail_v1": 228} | 2026-07-06T07:23:33Z | nan | nan | False |
| 2026-07-06 | TelAviv | 33 | 0.08 | 5.49792 | blocked_fresh_ask_exceeds_cushion | {"decision_snapshot_too_stale": 358, "fresh_ask_exceeds_cushion_or_band": 4} | 2026-07-06T01:48:57Z | 0.108 | 0.09 | False |
| 2026-07-07 | Shanghai | 34 | 0.12 | 6.9977599999999995 | blocked_fresh_ask_exceeds_cushion | {"decision_snapshot_too_stale": 266, "fresh_ask_exceeds_cushion_or_band": 94} | 2026-07-06T17:04:35Z | 0.17 | 0.13 | False |

## Interpretation

- Opportunity replay answers: if we had bought every candidate in that denominator at the recorded decision price, which settled as winners?
- Live fills answer: which tickets actually passed the then-current runner, token/book checks, execution policy, and got filled?
- Therefore the 9 winners do not mean the live runner missed 8 fills due only to maker queue. Only one row is a direct infrastructure miss (`missing_yes_token_id`); several rows were not in the live runner denominator or were blocked by later-approved policy/execution checks.

## Files

- Detail CSV: `docs/analysis/2026-07/generated/low_price_yes_missed_winner_execution_audit_v1/missed_winner_execution_audit.csv`
- Summary CSV: `docs/analysis/2026-07/generated/low_price_yes_missed_winner_execution_audit_v1/missed_winner_reason_summary.csv`
