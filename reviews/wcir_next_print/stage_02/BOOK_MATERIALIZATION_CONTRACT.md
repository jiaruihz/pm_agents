# Stage 2 Book Materialization Contract

## Boundary

Transport truth is the raw Polymarket market-channel frame plus its declared subscription epoch. REST is independent parity evidence only; it never repairs a WS gap. A reconnect starts a new chain and cannot inherit a prior book unless the epoch explicitly declares selector reconciliation inside the same chain.

## Valid state

A usable state requires a full `book` baseline followed by receive-ordered deltas. Detectable sequence gaps, receive/exchange clock regressions, delta-before-baseline, and best-quote parity failures invalidate the token until a verified state recovers it. Downstream rows expose `book_valid` and `gap_reason`; unavailable rows are not dropped or imputed.

Each state freezes the subscription epoch, token, baseline raw frame, first/last delta frame, delta-chain hash, exchange timestamp/hash, receive clocks, producer build, selector, capture policy, token map, subscription set, and stable `book_snapshot_id`.

## Executability

The materializer calculates both sides at 1, 5, and 10 shares across full visible depth. Buy cost adds the official Weather taker fee `shares × 0.05 × price × (1-price)` at each consumed level; sell proceeds subtract it. Insufficient depth yields `fully_executable=false` and no partial value.

`queue_truth=false` always. Public quote updates and reconstructed depth do not establish own fill, queue position, cancellation ordering, or maker fill probability. Any maker result is a proxy only.

## Event alignment

An event checkpoint uses the latest valid state received no later than the checkpoint, with a predeclared maximum age of 120 seconds. Missing and stale states become explicit invalid rows. Checkpoints are pre-source, source t0, pre-official, and official +1/+3/+5/+15/+30/+60/+120/+300 seconds.

