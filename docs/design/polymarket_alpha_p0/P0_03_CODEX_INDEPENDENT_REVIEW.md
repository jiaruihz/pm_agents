# P0-03 Gamma Catalog — Codex Independent Review

Review date: 2026-08-26

Reviewer: Codex integration coordinator

Reviewed delivery: `P0-03-evidence-seal/`, `src/polymarket_alpha/adapters/`, `src/polymarket_alpha/census/`, and `tests/polymarket_alpha/test_gamma_catalog_p0_03.py`

## 1. Disposition

```text
P0_03_DISPOSITION=REWORK_REQUIRED
ARCHITECTURE_REWORK=NO
FIX_AND_RESEAL=YES
MERGE_CURRENT_GLM_DELIVERY=NO
```

The implementation has a sound base and all declared test suites reproduce successfully. It is not accepted yet because required fail-closed properties are bypassable outside the current happy-path fixtures, one required event relationship is silently discarded, typed raw/alias projections are not persisted, and the evidence seal is not self-consistent.

This is a bounded P0-02/P0-03 integration correction. It does not authorize a second Gamma collector, new migration owner, network access, production capture expansion, or live execution.

## 2. Independently reproduced evidence

| Check | Result | Review conclusion |
|---|---:|---|
| `tests/polymarket_alpha/test_gamma_catalog_p0_03.py` | 37 passed | Reproduced |
| Entire `tests/polymarket_alpha` suite | 146 passed | Reproduced |
| Declared legacy Gamma regression set | 8 passed | Reproduced |
| Listed SHA-256 entries | 14/14 valid | Reproduced |
| SQLite `PRAGMA integrity_check` | `ok` | Reproduced |
| Import/capability audit | 0 violations | Reproduced |
| Files physically present in evidence seal | 16 | README/hand-off claim of 17 is incorrect |
| `manifest.json` covered by `hashes.sha256` | No | Seal does not seal its machine-readable disposition |
| Rows in `alpha_raw_artifact` | 0 | Required typed raw projection is not persisted |
| Rows in `alpha_market_alias` | 0 | Required alias projection is not persisted |

The passing suites prove that the implemented fixtures work. They do not close the findings below because the missing paths are not represented by those fixtures.

## 3. Blocking findings

### BF-P003-01 — Cross-batch `condition_id` uniqueness is fail-open

Severity: P0 contract blocker

Owners: P0-02 repository/migration owner + P0-03 adapter owner

`GammaCatalogIngestor` accepts an optional `market_identity_reader`. When it is absent, cross-batch identity lookup is skipped. `alpha_market.condition_id` also has no unique constraint. Two separate ingestions can therefore persist two market IDs with the same non-null `condition_id`, with zero errors.

Independent adversarial result:

```text
first_errors=0
second_errors=0
persisted=[("a", "dup"), ("b", "dup")]
```

Required correction:

1. P0-02 adds a partial unique constraint/index for non-null `condition_id` and exposes a repository identity lookup method.
2. P0-03 requires that repository capability; it must not be an optional correctness callback.
3. Add same-run and cross-run fixtures proving conflicts fail closed without direct SQL in P0-03.

Acceptance evidence: duplicate `condition_id` cannot produce two canonical markets under any public ingestion constructor.

### BF-P003-02 — A market linked to multiple events silently loses relationships

Severity: P0 integration blocker

Owners: P0-01 contract coordinator, P0-02 repository owner, P0-03 adapter owner

The identity adapter selects the first event, and the repository writes only that event-market join. For a market containing events `E1` and `E2`, only `E1 ↔ M1` survives. `E2 ↔ M1` is silently discarded. This violates the G01 requirement for bidirectional event-market joins and makes identity depend on upstream array order.

Required correction:

1. Keep one deterministic primary `event_id` for the existing singular `MarketIdentity`; selection must not depend on response array order.
2. Preserve the full deduplicated event-ID set in the normalized snapshot using the coordinator-approved additive contract representation.
3. P0-02 projects every event-market relationship into `alpha_event_market`.
4. Add a fixture with one market, at least two events, reordered arrays, and a replay proving stable identity plus complete joins.

Acceptance evidence: both joins exist, reordering event arrays changes neither canonical identity nor hashes, and no relationship is silently omitted.

### BF-P003-03 — F-05 verified-response identity can be bypassed

Severity: F-05 reopened

Owner: P0-03 adapter owner

`verify_market_response_identity()` accepts a payload when all expected identity arguments are absent. `ingest_verified_market()` exposes those arguments with `None` defaults, so a caller can enter the supposedly verified path without stating any expected identity.

Independent adversarial result:

```text
accepted_without_expected_identity=True
```

Required correction:

1. Require at least one non-blank expected identity value.
2. Reject all-`None`, empty-string, and whitespace-only expectations.
3. Ensure the public ingestion method returns a fail-closed error receipt rather than accepting or crashing.

Acceptance evidence: positive ID/condition/slug checks pass; missing, blank, mismatched, and ambiguous expectations all fail closed.

### BF-P003-04 — Retry under a new run conflicts with deterministic artifact IDs

Severity: idempotency/restart blocker

Owners: P0-01 contract coordinator + P0-03 adapter owner; P0-07 must reuse the same decision

Page and market-payload record IDs exclude `run_id` and ingestion time, while their canonical envelope content includes those values. Reingesting the identical captured source under a new work order therefore reuses the record ID with different canonical content and raises `ContractConflictError`. The page save occurs outside an error-receipt boundary, so the run aborts.

Independent adversarial result:

```text
ContractConflictError: record_id gamma_page:... has different canonical content
```

Required correction:

1. Freeze a single invariant for immutable source-artifact identity versus run association.
2. Preferred design: artifact canonical content contains only source-stable fields; `alpha_run_artifact_link` carries per-run association and observation metadata.
3. If the contract retains run metadata inside the immutable content, the record ID must include all content-changing fields and a separate logical-deduplication key must be explicit.
4. Add restart/retry fixtures with new `run_id` and later ingestion time; no uncaught conflict is allowed.

Acceptance evidence: same captured bytes can be linked to multiple runs without mutation or conflict, while changed bytes produce a distinct artifact.

### BF-P003-05 — Required raw and alias projections are only returned, not persisted

Severity: required-output/integration blocker

Owners: P0-02 repository owner + P0-03 adapter owner

The adapter saves generic `alpha_contract_record` envelopes and returns `MarketAlias` values, but the evidence DB contains zero `alpha_raw_artifact` and zero `alpha_market_alias` rows. Current alias builders also create open-ended intervals only; persisting a slug change for one market would leave overlapping active aliases unless the repository closes the prior interval.

Required correction:

1. P0-02 exposes repository methods for typed raw-artifact projection and alias interval persistence.
2. P0-03 uses those methods; it must not write migration-owned tables directly.
3. Add fixtures for identical alias replay, the same slug on different markets, and one market changing slug with non-overlapping effective intervals.
4. Validate that stored artifact hashes are recomputed from stored content rather than trusted from caller-supplied strings.

Acceptance evidence: evidence DB contains typed raw and alias rows; lineage resolves from snapshot to immutable raw content; alias intervals are non-overlapping and replay-idempotent.

### BF-P003-06 — Pagination is not generally bounded

Severity: ingestion termination blocker

Owner: P0-03 adapter owner

Exact duplicate-page detection closes the original repeated-page case, but `max_pages=None` permits an upstream that returns an endless sequence of unique full pages to run forever. The report's broader claim that termination is guaranteed by construction is therefore not established. In addition, captured page boundaries do not carry the authoritative request offset/limit; the ingestor reconstructs offsets from item counts.

Required correction:

1. Require a finite page budget or provide a finite fail-closed default.
2. Preserve requested offset, requested limit, page index, and termination reason in the page capture/receipt contract.
3. Add endless-unique-full-page and malformed-offset fixtures.

Acceptance evidence: every collection has a deterministic finite bound and a recorded termination reason.

### BF-P003-07 — Evidence seal is not self-consistent or reproducible from its declared base

Severity: evidence-gate blocker

Owners: P0-03 owner + integration coordinator

The directory contains 16 files, not the reported 17. `hashes.sha256` validates all 14 entries it lists, but omits `manifest.json`; its timestamp precedes the manifest. The manifest declares base commit `40d98649`, while the generated DB contains later P0-02/P0-07 schema and the 146-test result includes later Codex work. Source hashes help identify the actual files, but the declared Git dependency snapshot cannot reproduce the seal by itself.

Required correction:

1. Commit accepted P0-02/P0-03 dependencies first or record exact dirty-worktree source hashes and dependency commits without claiming a clean base.
2. Generate the manifest before the final hash list, or use a detached seal index that covers the manifest without a circular hash claim.
3. Record the exact test node list and current integration commit.
4. Recount files and verify the generator from a clean temporary checkout/worktree.

Acceptance evidence: all disposition-bearing evidence is covered, the file count is correct, and a clean checkout at the declared integration identity reproduces the tests and sample artifacts.

## 4. Non-blocking hardening required within the same rework

1. Reject or explicitly classify contradictory lifecycle flags instead of silently applying precedence such as `active=true, closed=true`.
2. Add model-level validation tying `page_sha256`/`payload_sha256` to canonical stored content; builder-only validation is insufficient.
3. Replace “verbatim raw response” wording unless actual response bytes are captured. The current model preserves canonicalized parsed JSON, not byte-for-byte HTTP response data.
4. Make the evidence generator deterministic or document which outputs are intentionally time-varying and excluded from golden comparison.

## 5. Coordinator decision on `SUPERSEDED`

The open F-04 decision is closed for P0 offline implementation as follows:

```text
SUPERSEDED_P0_POLICY=DEFERRED_UNREACHABLE
```

P0-03 and P0-04 must preserve relevant raw Gamma fields and drift evidence but must not infer or emit `SUPERSEDED`. No inference may be made from UMA state, `negRisk`, event membership, slug changes, or disappearance alone. Reopen the lifecycle transition only in the read-only operational pilot or P1 after an authoritative source/transition rule is demonstrated.

This decision avoids inventing lifecycle truth while keeping all source evidence available for a later migration.

## 6. P0-07 integration consequence

P0-03 supplies the rule text, rule hash, record ID, and provenance required by the P0-07 compiler request. That interface remains viable; no parallel Rule Lawyer is needed.

However, P0-07's final seal must wait for BF-P003-04 because its deterministic RuleContract identity must obey the same cross-run retry invariant. After P0-03 is accepted, the coordinator must rerun the combined P0-03/P0-07 fixture suite and reseal P0-07 against the accepted integration commit.

## 7. Bounded GLM rework contract

GLM may modify only:

- `src/polymarket_alpha/adapters/`
- `src/polymarket_alpha/census/`
- `tests/polymarket_alpha/test_gamma_catalog_p0_03.py`
- P0-03 evidence-seal generator and regenerated seal

GLM must not modify migration-owned files or shared contracts directly. It must instead provide a precise requested interface/schema delta for the coordinator to implement in P0-01/P0-02. The coordinator owns integration, shared-contract decisions, migration changes, final regression, and Git acceptance.

Required GLM handback:

1. Mapping from BF-P003-01 through BF-P003-07 to changed files and tests.
2. Exact P0-02 repository API/schema requests for condition identity, full event joins, raw projection, and alias intervals.
3. Adversarial fixtures named in this review.
4. Fresh test results and a new self-consistent evidence seal.
5. Explicit confirmation that no network, signing, private key, production DB, or live-order capability was introduced.

## 8. Acceptance gate after rework

The coordinator will accept P0-03 only after independently confirming:

```text
all seven blocking findings closed
P0-03 focused tests pass
entire polymarket_alpha suite passes
legacy Gamma regression passes
P0-11 no-live subset passes
evidence seal verifies from its declared integration identity
```

Until then, downstream work may inspect the branch but must not treat the current P0-03 outputs as canonical catalog truth.
