# GPT Pro Review Packet — WCIR Stage 2

## Requested review boundary

Review deterministic book reconstruction and executable depth truth only. Do not authorize Stage 4, deployment, collector changes, production config changes, or live behavior.

## Headline evidence

- Frozen real transport sample: 2026-08-26, five-city event-token scope.
- 608 declared epochs across 4 reconnect chains; 12,429 raw relevant frames.
- 6,250 reconstructed states; forward/reversed run ID identical: `2c33076e11ab85e03cfa21c748718d196460dc59815e437bae9db4136b94ad79`.
- 399 blocker intervals: 378 parity-pending, 16 parity mismatch, 5 delta-before-baseline; all recovered, zero open.
- 4,032 REST/WS comparisons: 18 full-depth parity, 369 REST-prefix parity, 1 best-quote-only, 3,644 not comparable because exchange clock skew exceeded 2 seconds.
- Two-sided executable states: 1 share 5,355; 5 shares 5,292; 10 shares 5,256.
- `queue_truth=false`; maker fill remains proxy-only.

## Known limitation

This freezes one full transport day, not the full 19-day archive. Four reconnect chains declare predecessors outside the day slice; this truncation is explicit, no external state is carried, and each token stays invalid until an in-slice verified baseline. Event alignment is sparse: only 2/72 source-t0 rows are valid under the 120-second as-of policy. This does not invalidate the reconstruction contract, but it blocks a Stage 3 alpha conclusion.

## Reproduce

Run `reviews/wcir_next_print/stage_02/REPRODUCE_STAGE_02.sh`. It verifies frozen hashes before dedupe/replay, uses no runtime root, writes only to a temporary replay directory, byte-compares 11 derived artifacts, and executes the contract tests.

## Requested disposition

Choose exactly one:

```text
ACCEPT_STAGE_02_BOOK_TRUTH_AND_PROCEED
ACCEPT_WITH_BLOCKING_FIXES
REWORK_BOOK_RECONSTRUCTION_BOUNDARY
COLLECT_MORE_TRANSPORT_BEFORE_PROCEEDING
```
