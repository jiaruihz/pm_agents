# 2026-07-09 Tokyo fast-source prev-NO live first day

## Scope

Strategy: `fast_source_prev_no_trial_v1`.

Runtime journal:

- `/Volumes/jrs/weather_data_feed_service_runtime/output/fast_source_prev_no_trial/opportunities.jsonl`
- `/Volumes/jrs/weather_data_feed_service_runtime/output/fast_source_prev_no_trial/events.jsonl`
- `/Volumes/jrs/weather_data_feed_service_runtime/output/fast_source_prev_no_trial/orders.jsonl`

Configuration intent:

- Tokyo live, 5 shares per trade, 5 shares per market cap.
- Buy previous METAR bracket `NO` when the fast airport source has already rounded above the current METAR running max.
- `max_no_ask = 0.92`.

## Tokyo 2026-07-09 result

Tokyo had 579 opportunity rows for target date `2026-07-09`; 58 were `cross_candidate`.
All 58 candidates were the same ladder:

- fast source: JMA AMeDAS `44166`
- source rounded temperature: `30C`
- METAR running max / target previous bracket: `29C`
- intended market: Tokyo `29C NO`

Cross source-observation windows:

| source obs UTC | detected UTC | first decision UTC | source temp | METAR report in force | target | observed NO ask range |
|---|---:|---:|---:|---:|---:|---:|
| 2026-07-09 04:10 | 04:18:11 | 04:18:53 | 29.7C / round 30 | 04:00, 29C | 29 NO | 0.64-0.90 |
| 2026-07-09 04:20 | 04:28:11 | 04:28:23 | 29.5C / round 30 | 04:00, 29C | 29 NO | 0.58-0.87 |
| 2026-07-09 04:40 | 04:47:14 | 04:47:24 | 29.6C / round 30 | 04:30, 29C | 29 NO | 0.73-0.83 |
| 2026-07-09 05:00 | 05:07:17 | 05:07:24 | 29.9C / round 30 | 04:30, 29C | 29 NO | missing book ask |

One live order was attempted:

- decision UTC: `2026-07-09T04:18:53.546699+00:00`
- submit UTC: `2026-07-09T04:18:54.783380+00:00`
- source obs UTC: `2026-07-09T04:10:00+00:00`
- latest METAR in force: `2026-07-09T04:00:00+00:00`, `29C`
- target: Tokyo `29C NO`
- fresh book: NO ask `0.87`, ask size `64.38`
- submitted: `BUY 5 @ 0.87 FOK`
- result: `submit_failed`
- CLOB error: `order couldn't be fully filled. FOK orders are fully filled or killed`

## Interpretation

This supports the trading interpretation discussed after the run: the expected policy would have entered at the first valid cross, around NO ask `0.87`. The later cheaper quotes are not a clean "we should have waited" counterfactual, because under the intended policy the position would already have been entered.

The price path also makes sense mechanically. The 04:10 and 04:20 fast source observations rounded to 30 while the official 04:00 METAR still showed 29. The 04:30 METAR then still printed 29, so the market got a reason to discount the immediate cross before the later 04:40 fast source again showed round 30.

## Follow-up already fixed

The first live attempt exposed an execution bug in the trial runner:

- Failed FOK attempts were incorrectly counted against the per-market share cap.
- The live FOK limit used the exact current ask rather than the configured cap, leaving no room for tiny book movement.

This was fixed in commit `a34c78b` (`Fix fast-source FOK retry cap`): failed FOK no longer consumes the per-market cap, and FOK limit price uses `max_no_ask = 0.92`.

Remaining infra caveat: this trial runner still writes JSONL journals directly and is not yet routed through the unified order executor / canonical `orders -> fills -> fact_trades` lineage.
