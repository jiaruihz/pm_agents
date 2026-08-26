# P0-02/P0-03/P0-07 Integration Evidence Seal

```text
source_commit=fe5598ca4a3bcd13b538dfb6e2cd550def2659dd
readiness_scope=OFFLINE_IMPLEMENTATION_ONLY
P0_02_CATALOG_INTEGRITY=ACCEPTED
P0_03_GAMMA_CATALOG=ACCEPTED
P0_07_RULE_GATES_REVISION=ACCEPTED
P0_03_DISPOSITION=COMPLETE
```

This seal supersedes the implementation disposition in
`P0_03_CODEX_INDEPENDENT_REVIEW.md`. The original GLM rework seal is retained
unchanged as handoff provenance; this directory records the coordinator-owned
repository, migration, RuleContract, and evidence integration against an exact
committed source identity.

## Blocking-finding closure

| Finding | Closure evidence |
|---|---|
| BF-P003-01 | Additive partial unique index on non-null `condition_id`; repository-native lookup; in-batch, cross-batch, and legacy-duplicate rollback tests. |
| BF-P003-02 | Deterministic primary event plus full sorted event set; every event-market join is projected; array-order replay is stable. |
| BF-P003-03 | Verified lookup requires at least one non-blank expected identity; missing, blank, mismatch, and ambiguous inputs fail closed. |
| BF-P003-04 | Gamma artifacts include every run/clock field in concrete identity. Rule compilation separates stable semantic revision from immutable per-run instance; receipts and Gate A/B IDs cover full canonical content. |
| BF-P003-05 | Typed raw projection, replay repair, alias interval persistence, DB-level single-active-alias constraint, historical replay, and run-artifact links are integrated into the catalog path. |
| BF-P003-06 | Finite default page budget and explicit termination receipt; captured offset/limit/page boundary is retained. |
| BF-P003-07 | Source is committed before seal generation; JUnit, migration hashes, source audit, and seal hashes are bound to the exact source commit above. |

## Storage and RuleContract decisions

- `alpha_p0_0003_catalog_integrity` is additive. It does not alter the sealed
  0001/0002 SQL and fails transactionally when legacy catalog identity or
  active-alias rows violate the new invariants.
- `contract_revision_id` is the stable semantic rule revision used by Rule A/B
  comparisons and invalidation logic.
- `rule_contract_id` is an immutable concrete compilation instance carrying
  run, clock, market-snapshot, and provenance fields.
- `alpha_p0_0004_rule_contract_instances` adds instance-grain v3 projections
  and backfills sealed v2 rows without dropping or rewriting v1/v2 objects.
- `SUPERSEDED_P0_POLICY=DEFERRED_UNREACHABLE` remains in force.

## Test evidence

| Evidence | Result |
|---|---:|
| `test-results/focused.xml` | 73 passed |
| `test-results/full-alpha.xml` | 166 passed |
| `test-results/no-live.xml` | 59 passed |
| `test-results/legacy-gamma.xml` | 8 passed |
| `source-audit.txt` | 4 owned source roots, 0 violations |
| `migration-manifests.json` | 0001–0004 exact SQL SHA-256 identities |

Focused coverage comprises P0-02 storage, P0-03 Gamma catalog, and P0-07 rule
compiler/gates. The full Alpha suite includes P0-01, P0-06A, P0-11, and all
adjacent offline fixtures.

## Safety and authority

No network call, production DB migration, weather runtime/config mutation,
order/signing path, private key, or live smoke test was used. This seal does
not approve:

```text
READ_ONLY_OPERATIONAL_PILOT
PRODUCTION_CAPTURE_EXPANSION
CURRENT_RESEARCH_DB_MIGRATION
LIVE_ORDER_OR_SIGNING
```

Rollback remains additive: pin readers before 0003/0004 and stop P0-03/P0-07
callers. Existing immutable records and v1/v2 projections remain readable; no
DROP or DELETE rollback is required.
