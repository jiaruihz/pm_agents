# City intraday Phase 2 decision bridge v1

Status: completed-offline
Date: 2026-08-02
Scope: `ModelOutput -> SignalCandidate -> TradeIntent -> temporary fact_signal_candidates`; no production DB write, deployment, order or fill mutation

## Result

Phase 2 now has versioned shared contracts, explicit legacy `CityScore/evaluation/paper_intent` adapters, and an incremental bridge into an isolated canonical DB. `SignalCandidate` is the opportunity fact boundary; selected, unselected, one-sided, missing-expression and non-executable rows remain in the denominator. `TradeIntent` contains only execution declarations and the Phase 2 constructor rejects `live` mode.

The bridge reuses `fact_signal_candidates` with `candidate_grain_version=v2_event_checkpoint`. Additive columns retain target, expression, token, feature set, feature/execution book snapshots, policy, schema and input/metadata lineage. No city-private settlement or PnL table was introduced.

## Deployed-journal census

The deterministic replay used these immutable input snapshots:

- `evaluations.jsonl`: 348 rows, SHA256 `3921709eafae68c05b68bcb530b80941c82e65b804579680b002430a73d5ce48`
- `paper_intents.jsonl`: 10 rows, SHA256 `6d426c6769dbcd3ef0189e0ce3488db3cc39a3e29f2586e12349b2aae6cb92dc`

Result: 358 raw decision rows collapsed to 346 unique candidates, 346/346 were present in the temporary canonical DB (`candidate_delta=0`), with 100 scored, 246 blocked, 6 policy-selected candidates, 6 valid zero-notional intents and 4 intent blockers. Two independent runs produced byte-identical JSON artifacts; summary hash was `cb65a6c88f4fbdd1ff5d5f87f7789a9de66291f13c49626714c3a2fd57edd28b`.

Signal funnel, unit `expression_checkpoint`: 346 raw candidates -> 100 scored / 246 blocked -> 6 policy selected. Evidence funnel: 260 PIT midpoint rows, 346 mapped conditions, 307 executable quotes in this snapshot; fill remains `not_available_phase2` because Phase 2 does not infer fills or PnL from shadow journals.

## Tokyo token-outcome pollution window

Root cause: Tokyo legacy evaluation consumed a NO-token book and derived YES/NO probabilities from it, but its compact `market` payload omitted `outcome`. A compatibility layer therefore could not prove whether `token_id` represented the candidate expression; treating it as a YES token would create a wrong executable identity.

Affected retained window: `2026-08-01T00:03:04.990845Z..2026-08-02T01:13:50.594325Z`, target dates 2026-08-01 and 2026-08-02. There are 168 affected evaluation rows (84 YES, 84 NO), collapsing to 166 candidate identities in the final snapshot. Twenty-four rows had `would_enter=true`; position dedupe produced four paper intents, all Tokyo YES:

- `e7680de20e7c0be57465a9389e90761533d617833b1515342f950ed3f313015a` — Tokyo 32
- `e9dc7d1060c57cbebb7d3a183d66687c674c495a1fc6e3f19894fbbb3c152b14` — Tokyo 33
- `45a15f54521bf17665c8e1479abee5d67020831c9aee1c24777d60633cb2d214` — Tokyo 34
- `5ed253107f528b649b7d61bf26bb22ff2ad5ca09393635fb05a90d66d4d9ab85` — Tokyo 35

Counterfactual execution result: all four standard intents are blocked as `token_outcome_unknown`; the historical rows had `notional_usd=0`, `shares=0`, `orders_submitted=0`, so order/fill/notional/fee/PnL impact is exactly zero. The raw rows remain preserved and must not be interpreted as executable-token evidence.

Root fix: new Tokyo compact market telemetry now records `outcome`. This prevents future NO-token rows from losing their expression identity after that code is adopted. A YES candidate backed only by a NO token remains blocked until Phase 3B maps/captures the actual YES expression token; the adapter does not infer the complement token.

## Verification

- legacy-only, vNext-only and mixed journals converge on the same candidate identity.
- execution profile A/B shares one candidate and produces distinct intent IDs.
- cross-object event/checkpoint/model/candidate clock and lineage mismatches fail explicitly.
- Helsinki/Tokyo one-sided fixtures remain blocked candidates; Amsterdam interval-only physical output cannot manufacture a market candidate.
- focused Phase 2/canonical/Tokyo/Phase 1 regression suite: 53 passed with two pre-existing pandas fragmentation warnings.
