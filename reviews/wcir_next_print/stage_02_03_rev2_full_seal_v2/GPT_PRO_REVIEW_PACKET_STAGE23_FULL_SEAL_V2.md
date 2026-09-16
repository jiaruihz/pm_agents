# GPT Pro review packet — Stage 2/3 full evidence seal v2

## Requested disposition

Please return exactly one of:

```text
ACCEPT_STAGE23_REV2_EVIDENCE_CLOSURE
ACCEPT_WITH_BLOCKING_FIXES
REWORK_STAGE23_REV2_EVIDENCE_CLOSURE
STOP_DUE_TO_UNCONTROLLED_LIVE_OR_DATA_RISK
```

## Scope

This is the full evidence package, not the prior 16 KB compact brief.  It embeds
the immutable Stage 2 rev2 and Stage 3 rev2 evidence roots, the prior full closure,
the amended row-level evidence, code, tests, environment freeze, strict manifest,
and offline reproduction entrypoint.  The 15.8m raw-frame replay was not repeated;
its frozen manifests and deterministic replay identities are verified unchanged.

No model was trained.  No Stage 4 work, production configuration change,
deployment, order, fill, or notional occurred.

## Blocking fixes closed

- Strict causal gate is materialized on all 50,460 policy × size × horizon rows.
  Equality and missing/uncomparable clocks fail closed; action construction cannot
  read post-official market data.
- All 841 events carry latency provenance.  The 49.773-second pooled p95 is marked
  `LEGACY_DIAGNOSTIC_ONLY` and `NOT_FORWARD_PRIMARY`; missing collector epoch is
  preserved rather than invented.
- Denominators are split into 841 event-universe, 841 operational fail-closed, 89
  research-market-data eligible, and 1 strict pairwise baseline-comparable row.
  `POLICY_ABSTAIN`, `DATA_FAIL_CLOSED`, and `RESEARCH_INELIGIBLE` are distinct.
- The immutable historical headlines remain 89 for primary/reaction/concentration/
  bootstrap/city-gate and 56 for Busan.  The single legacy mismatch remains excluded
  because its effective entry was 46.443 seconds after official first_seen.
- Package verification rejects missing, extra, duplicate, size-drift, and hash-drift
  entries.

## Start here

1. `FULL_EVIDENCE_SEAL_V2_AUDIT.json`
2. `CAUSAL_ENTRY_AND_ORACLE_FAMILY_CONTRACT.md`
3. `LATENCY_POLICY_PROVENANCE_SUMMARY.json`
4. `DENOMINATOR_LAYER_SUMMARY.json`
5. `ORACLE_FAMILY_MEASUREMENT_HARNESS.json`
6. `BOOK_VALIDITY_CORRIGENDUM_RESULTS.json`
7. `TEST_COMMANDS_AND_RAW_OUTPUT.txt`
8. `FINAL_CODE_AND_ENVIRONMENT_FREEZE.json`
9. `REPRODUCE_STAGE23_FULL_SEAL_V2.sh`

Stage 3 modeling remains `CONTINUE_COLLECTION_WITHOUT_MODELING`.  Stage 4 remains
unauthorized.
