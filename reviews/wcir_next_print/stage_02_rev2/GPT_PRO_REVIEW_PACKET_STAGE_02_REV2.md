# WCIR Stage 2 rev2 — GPT Pro review packet

## Requested disposition

Recommended:

```text
ACCEPT_STAGE2_REV2_RECONSTRUCTION_AND_DIAGNOSIS
AUTHORIZE_SEPARATE_COLLECTOR_CLOCK_CONTRACT_AMENDMENT
```

Alternatives:

```text
CONTINUE_UNCHANGED_COLLECTION_UNTIL_10_CLEAN_FORWARD_DATES
REWORK_STAGE2_REV2_EVIDENCE
STOP_DUE_TO_UNCONTROLLED_LIVE_OR_DATA_RISK
```

This packet does **not** request Stage 4 authorization or any live change.

## What was completed

- Replayed 100% of available cutoff archive: 465 WS files, 19 transport days,
  15,856,707 frames, 841 frozen causal events and 84,240 event/checkpoint/token
  rows.
- Froze event, market/token, forecast, latency, epoch, WS and REST identities.
- Assigned every row exactly one primary reason; `UNKNOWN=0`.
- Separated reconstruction validity from side availability and 1/5/10-share
  executable depth.
- Replayed forward, reverse and chunked file order. All full identity checks
  passed with coverage hash
  `2e0351bac634d4847aebc77786472dc986001938525b79cad9d90c55d36b9b09`.
- Independent read-only code review findings were fixed; focused tests are
  37/37 pass, including missing/extra/hash-drift package fail-closed tests.
  Production manifest strict exited 0. Zero notional remains
  0 orders / 0 fills / $0.

## Result: algorithm closes, archive availability does not

Source-t0 exact prior-NO denominator:

| Status | Rows |
|---|---:|
| `ARCHIVE_MISSING` | 278 |
| `STALE_BOOK` | 419 |
| `NO_BASELINE` | 44 |
| `OPEN_GAP` | 34 |
| valid but one-sided | 18 |
| valid but insufficient 5-share depth | 4 |
| valid two-sided 5-share depth | 44 |
| **Total / book-valid** | **841 / 66 (7.85%)** |

This is not mainly a thin-book problem. The dominant causes are selective
non-subscription and capture windows that are stale by event time. Seoul is
fully missing (268/268); stale capture dominates Amsterdam, Busan, Helsinki and
Tokyo.

Archive regime gates:

- transport days: 19 — pass;
- reconnect/gap days: 19 — pass minimum;
- normal days: 0 — fail (minimum 3);
- source-t0 book-valid: 7.85% — fail (minimum 90%);
- unknown reason: 0 — pass.

There were 2,767,013 frames without an activatable epoch and 3 within-file
receive-clock regressions; regressed frames are excluded and labelled
`CLOCK_UNCERTAINTY`. The runner did not change the collector or repair WS with
REST. Of 66 valid WS books, all had a nearest REST record; only 6 were within
the parity clock tolerance and all 6 had depth-prefix parity.

## Reviewer question

Unchanged collection is unlikely to close this boundary because the exact event
tokens are often absent or too old at source-t0. Please decide whether the
evidence is sufficient to accept the reconstruction/diagnosis contract and
authorize a **separate, review-gated Stage 2 collector-clock contract amendment**.
That amendment should target epoch completeness, source-event token demand and
freshness coverage; it must not alter model, selector, threshold, position or
execution policy.
