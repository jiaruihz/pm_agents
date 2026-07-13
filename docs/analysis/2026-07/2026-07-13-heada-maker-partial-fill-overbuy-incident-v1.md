# HeadA Maker Partial-Fill Overbuy Incident v1

Date: 2026-07-13
Strategy: `low_price_yes_lottery_tiny_live_v1`
Status: root cause fixed locally; deployment verification required.

## Summary

Chongqing 2026-07-13 bracket 36 was planned as fixed 5 shares but filled 8 shares. The first maker order filled 3/5 shares. The dynamic maker lifecycle read a delayed `clob_fills.jsonl`, saw zero fills, canceled the residual order, and submitted another full 5-share order. Both fills were real: 3 shares at 0.08 and 5 shares at 0.09.

The sizing policy was correctly `fixed_5_shares`; the failure was cancel/replace quantity accounting, not selector sizing.

## Impact

Audit denominator: all submitted `low_price_yes_lottery_tiny_live_v1` orders joined to canonical CLOB fills, grouped by `signal_id`; cumulative fills were compared with the root order's planned shares.

| city | target date | bracket | planned | filled | excess | excess cost | settlement |
|---|---|---:|---:|---:|---:|---:|---|
| Shanghai | 2026-07-05 | 28 | 6.000 | 36.000 | 30.000 | $2.688 | loss |
| Helsinki | 2026-07-05 | 17 | 6.000 | 11.000 | 5.000 | $0.625 | win |
| Munich | 2026-07-06 | 24 | 6.000 | 11.000 | 5.000 | $0.300 | loss |
| Helsinki | 2026-07-06 | 18 | 8.000 | 11.000 | 3.000 | $0.390 | loss |
| Istanbul | 2026-07-06 | 25 | 6.000 | 12.254 | 6.254 | $0.281 | loss |
| Busan | 2026-07-07 | 32 | 5.000 | 9.353 | 4.353 | $0.361 | loss |
| Austin | 2026-07-09 | 100-101 | 9.600 | 11.248 | 1.648 | $0.214 | loss |
| Chongqing | 2026-07-13 | 36 | 5.000 | 8.000 | 3.000 | $0.270 | open |

Total: 8 opportunities, 58.255 excess shares, $5.129 excess cost. The seven settled excess sleeves happened to net approximately +$0.141 because Helsinki 7/5 won; this is accidental outcome PnL, not acceptable sizing behavior. Chongqing's excess $0.270 remains open.

## Root Cause

`lifecycle_plans()` calculated `remaining_shares = posted_shares - filled_shares` from `runtime/weather_edge_v1/clob_fills.jsonl`. That cache was not refreshed until many hours after the live partial fill. The cancel/replace executor confirmed cancellation but did not query the authenticated order's final `size_matched`, so the stale full quantity reached the replacement order.

This was also a time-of-check/time-of-use race: even a fresher local cache cannot prove how much filled between planning and cancellation.

## Fix

The executor now:

1. Reads authenticated order state before cancellation for evidence.
2. Cancels the source order.
3. Reads authenticated order state again after cancellation.
4. Computes `remaining = original_size - size_matched` from the post-cancel state.
5. Caps the replacement at the smaller of planned and authoritative remaining shares.
6. Blocks replacement when authoritative state is missing/invalid or remaining shares are below the exchange 5-share minimum.

The HeadA lifecycle plan explicitly marks cancel/replace orders as requiring authoritative order state. Local tests cover a 3/5 partial fill being canceled without replacement and an 8-share source with 3 matched being resized to 5.

## Verification

- Focused tests: `41 passed` in `test_weather_execution_pipeline.py` and `test_low_price_yes_lottery_sizing.py`.
- Required live verification after restart: process uses the committed SHA; a lifecycle record contains post-cancel order state; no signal's cumulative fills exceed its root planned shares.
