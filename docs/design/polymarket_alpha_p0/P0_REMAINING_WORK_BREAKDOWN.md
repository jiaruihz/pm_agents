# Polymarket Alpha P0 — Remaining Work Breakdown

Status date: 2026-08-27

```text
PROGRAM_PHASE=P0_OFFLINE_IMPLEMENTATION
P0_COMPLETE=YES
P0_COMPLETION_STATUS=CERTIFIED_OFFLINE
SOURCE_COMMIT=3335cbaa3196adf46e7ef284fcd094515ca89e37
UNIFIED_PIPELINE_SOURCE_COMMIT=c3a578c4ec4f98974c7f130b9dfd02b019460049
UNIFIED_MULTI_RECALL_PIPELINE=COMPLETE
IMMUTABLE_RESEARCH_HANDOFF=COMPLETE
REPEATED_SCAN_REFRESH=COMPLETE
NEXT_GATE=READ_ONLY_OPERATIONAL_PILOT_GATE
NEXT_GATE_STATUS=PREPARED_NOT_AUTHORIZED
NEXT_GATE_OFFLINE_PREFLIGHT=IMPLEMENTED_AND_TESTED
P1_RESOLUTION_LEARNING_OFFLINE_FOUNDATION=COMPLETE
P1_CORRECTION_BACKFILL_OFFLINE_PLANNER=COMPLETE
P1_RESOLUTION_SOURCE_INTAKE_OFFLINE=COMPLETE
P1_LIVE_RESOLUTION_BACKFILL=NOT_IMPLEMENTED
RESEARCH_BRIEF_RENDERER=COMPLETE
BLIND_RESEARCH_EXECUTION=MANUAL_IMMUTABLE_HANDOFF_ONLY
MARKET_RESEARCH_EXECUTION=MANUAL_IMMUTABLE_HANDOFF_ONLY
DAILY_SCAN_SCHEDULER=NOT_IMPLEMENTED
READ_ONLY_OPERATIONAL_PILOT=NOT_APPROVED
PRODUCTION_CAPTURE_EXPANSION=NOT_AUTHORIZED
```

## 1. Current implementation truth

| Workstream | Status | Evidence / remaining boundary |
|---|---|---|
| P0-01 core contracts | COMPLETE | Base and R2 additive contracts, Blind question provenance, source-artifact semantics and golden schemas pass. |
| P0-02 storage | COMPLETE | Additive migrations 0001–0005, atomic research/ledger writes, copy/replay and rollback pass. No current DB was migrated. |
| P0-03 Gamma catalog | COMPLETE | Multi-market identity, YES/NO mapping, revisions, aliases, drift receipts and idempotence pass. |
| P0-04 Change Events | COMPLETE | Append-only NEW/rule/lifecycle/closed/metadata/family changes and cross-run identity pass. |
| P0-05 Book adapter | COMPLETE | Existing-owner SENSING/FORMAL_REVIEW demand, paired receipt, staleness and depth contracts pass offline. |
| P0-06A aggregator | COMPLETE | Provider-neutral dedupe, Candidate aggregation, late-hit refresh and no-book isolation pass. |
| P0-06B structural recall | COMPLETE | New/changed and structural metadata recall pass without book dependency. |
| P0-06C controversy recall | COMPLETE | Frozen source identity, PIT revision/hash/offset and provider isolation tests pass. |
| P0-06D wallet recall | COMPLETE | Staleness, address/entity separation, private direction and Blind exclusion tests pass. |
| P0-06E book anomaly recall | COMPLETE | Optional structural book route and shared provider-registry integration pass. |
| P0-07 Rule A/B | COMPLETE | Single compiler and exact same-hash/revision/compiler Gate A/B invariant pass. |
| P0-08 research roundtrip | COMPLETE | Blind allowlist, manual result import, source hash replay, formal book demand and Market packet pass. |
| P0-09 decision ledger | COMPLETE | Append-only lifecycle reaches SIMULATION_RECORDED; ranker emits only NO_ORDER and atomic ledger passes. |
| P0-10 harness adapter | COMPLETE | One coordinator, unique owners, complete evidence and hash-bound offline completion receipt pass. |
| P0-11 security | COMPLETE | Static, transport, dynamic canary and macOS process/network capability proofs pass for offline scope. |
| P0-12 offline E2E | COMPLETE | A01-A18, 371 Alpha tests, 12 legacy tests and 18 harness tests are sealed under `P0_12-evidence-seal`. |
| Unified offline integration closure | COMPLETE | Four released pre-book providers, staged immutable handoff, formal book gating, Rule A/B, no-order ledger, partial failure and second-scan refresh pass; 430 Alpha tests are sealed under `P0_UNIFIED_OFFLINE_PIPELINE-evidence-seal`. |
| P1 resolution/learning offline foundation | COMPLETE | Append-only resolution/link/score/calibration contracts, migration `alpha_p1_0001_resolution_learning`, deterministic replay and forged-aggregate rejection are sealed under `P1_RESOLUTION_LEARNING-evidence-seal`. Live resolution ingestion/backfill remains unimplemented. |
| Offline operational bridge | COMPLETE_AFTER_CODEX_REVIEW | Captured Gamma ingest, bounded paired-demand outbox, existing-owner book bridge and resumable single-market coordinator are sealed under `GLM_OPERATIONAL_REMAINDER_CODEX_REVIEW-evidence-seal`; this is not authorization to run the network pilot. |
| P1 correction/backfill planner | COMPLETE | Correction-chain head selection, policy/hash receipt, isolated prediction backfill and atomic persistence grouping are sealed under `P1_BACKFILL_PLANNER-evidence-seal`. No authoritative source adapter or scheduler exists. |
| P1 captured resolution intake | COMPLETE_OFFLINE | Caller-supplied source bytes, exact RuleContract source/precedence labels and parser assertion are hash-bound into replayable resolution facts under `P1_INTAKE_AND_RESEARCH_BRIEF-evidence-seal`. No online source fetcher exists. |
| Blind/Market research execution | MANUAL_HANDOFF_ONLY | Packet export, immutable result intake and validation are complete; no model/API/browser executor is owned by Alpha. A human or separately authorized agent must currently return the structured result artifact. |
| Research brief renderer | COMPLETE | Deterministic Blind/Market briefs are copyable to GPT Pro or another authorized provider; Market briefs include the exact accepted Blind baseline, and Blind briefs keep venue data out. This improves the manual seam but is not autonomous execution. |
| Daily scan scheduler | NOT_IMPLEMENTED | The coordinator is resumable and stage-bounded but intentionally has no polling loop or autonomous scheduler. Operational activation requires its own owner, budgets and rollback gate. |

P0 now has a complete deterministic offline path from multi-route Recall →
Candidate → Rule A → immutable Blind handoff/import → formal paired book →
immutable Market handoff/import → Rule B → rank/ledger →
`SIMULATION_RECORDED`, plus append-only second-scan refresh. The original P0-12
single-route fixture remains preserved; the unified closure is separately
sealed so component completion is not confused with orchestration completion.
This is not evidence that daily network operation, production capture
expansion, or live execution is ready.

The first post-P0 learning foundation and its pure backfill planner are complete
offline. They can validate an append-only correction chain and close sealed
predictions only when a caller supplies replayable resolution artifacts, then
produce versioned simulated PnL, Brier/Log Loss and calibration slices without
updating the P0 PredictionRecord. They do not fetch official outcomes, schedule
jobs, or treat Gamma lifecycle state as settlement truth.

The research pipeline is structurally complete but not autonomous: Blind and
Market packets are exported immutably, then wait for a caller-supplied result.
There is currently no Alpha-owned GPT/API/browser executor and no daily polling
scheduler. Those are real remaining engineering workstreams, separate from the
already complete packet/importer and decision logic.

The next-gate offline preflight is also implemented at source commit
`ef82da8844562c63eacc5d16a73b17fe5b98abbb`: four synthetic Gamma-shaped binary fixtures, exact canonical
identity bindings, a bounded Gamma-only Alpha transport policy, existing-owner
book-demand reservations, budget accounting, and caller-supplied weather
isolation/rollback contracts. Alpha cannot authorize CLOB `/books`; no network,
owner demand, runtime DB write, production observation, deploy, or restart was
performed. Actual OP-02/OP-03/OP-06 execution remains unapproved.

## 2. Implemented sequence (historical work breakdown)

The executable next-gate contract is
`READ_ONLY_OPERATIONAL_PILOT_GATE.md`. Preparation is complete; execution still
requires explicit authorization because it introduces real network traffic and
touches the current market-book owner boundary.

```text
Immediate, independent:
  C-P0-01R2 Shared contract addendum ------------------------+
  G-P0-06C Controversy Recall -------------------------------+----+
  G-P0-06D Wallet Recall ------------------------------------+    |
  T-P0-08A Blind projection/packet builder ------------------+    |
  T-P0-09A Candidate lifecycle reducer ----------------------+    |
                                                               |
After P0-01R2:                                                |
  C-P0-02R2 repository/migration projections                  |
       ├── G-P0-04 Change detector ──> G-P0-06B Structural ---+
       ├── C-P0-05 Book demand/receipt adapter ──> G-P0-06E --+
       └── T-P0-08B Result importer --------------------------+
                                  C-P0-05 + T-P0-08A/B
                                             |
                                      C/T-P0-08C Market stage
                                             |
                                      C/T-P0-09B protocol
                                             |
                                      C/T-P0-09C rank/ledger
                                             |
                                      G-P0-10 harness adapter
                                             |
                                      C-P0-11 final proof
                                             |
                                      C-P0-12 offline E2E
```

`G` = GLM bounded worker, `T` = Terra bounded implementation worker under
Codex ownership, `C` = Codex coordinator/sole owner.

## 3. Work packages

### C-P0-01R2 — Shared contract addendum

Owner: Codex

Owned files: `src/polymarket_alpha/contracts/**`, contract tests/fixtures and
the P0-01R2 evidence seal only.

Required additive types:

1. `MarketChangeEvent` with typed changes, before/after snapshot identity,
   effective clock, source lineage and deterministic event identity.
2. `BookCaptureDemand` and `BookCaptureReceipt`, including purpose
   `SENSING | FORMAL_REVIEW`, trigger artifact, TTL/freshness, requested target
   sizes, owner receipt state and fail-closed error reason.
3. Immutable `SourceArtifact` plus `ResearchResultEnvelope` and
   `ResearchImportReceipt`, binding packet id/hash, stage, ProbabilityEstimate,
   claim evidence, actual captured artifact hashes and quarantine/reject state.

Constraints: additive models only; do not change existing serialized model
fields. Keep `alpha_p0_v1.0` for a purely additive bundle release unless a
compatibility test proves an existing type must change. Publish a new schema
bundle fingerprint and backward-reader evidence.

Acceptance: golden schemas and IDs; retry/cross-run identity; wrong stage,
packet/hash mismatch, missing artifact and REFERENCE_ONLY semantics fail closed;
no market/price/wallet semantics become reachable from Blind types.

### C-P0-02R2 — Storage projections for the addendum

Owner: Codex migration owner; a Terra worker may implement only after the exact
contract release.

Owned files: `src/polymarket_alpha/storage/**` and storage tests.

Required output: one new additive migration; ChangeEvent, demand/receipt,
SourceArtifact, result/import receipt projections; replay repair; FK/CHECK and
transactional rollback. No rewrite of migrations 0001–0004 and no current DB
migration.

Acceptance: empty/legacy/repeat/interruption/copy rehearsal, exact source DB
hash unchanged, cross-run and duplicate importer writes deterministic.

### G-P0-04 — Pure revision change detector

Owner: GLM, after P0-01R2/P0-02R2 release.

Owned files: `src/polymarket_alpha/change/**` and
`tests/polymarket_alpha/test_change_events_p0_04.py`.

Inputs: previous/current MarketSnapshot or first-observation marker. Outputs:
only released MarketChangeEvent values. Detect NEW, RULE_CHANGED,
LIFECYCLE_CHANGED, CLOSED, RESOLVED, METADATA_CHANGED and FAMILY_CHANGED;
`SUPERSEDED` remains unreachable.

Acceptance: one-character rule change, whitespace-only rule revision, status
changes, no-change, retry, cross-run, missing previous snapshot and deterministic
ordering. No book, network, probability or fair-value logic.

### C-P0-05 — Existing-owner book demand/receipt adapter

Owner: Codex.

Owned scope: Alpha-side demand/receipt builder, normalized paired-book adapter
and offline fixtures. It consumes the existing market-book owner; it must not
open sockets or create a collector.

Split:

- P0-05A: pure demand/receipt and existing artifact normalization.
- P0-05B: route triggers—SENSING from ChangeEvent and FORMAL_REVIEW only after
  accepted blind result.

Acceptance: paired YES/NO identity, stale/one-sided/insufficient-depth states,
purpose/trigger validation, no direct socket, missing book skips only dependent
route, no production config change.

### G-P0-06B — Pre-book structural recall

Owner: GLM after P0-04.

Owned files: `src/polymarket_alpha/recall/new_changed.py`,
`structural_metadata.py` and one focused test module.

Allowed inputs: ChangeEvent, catalog metadata, family/threshold relationships.
Allowed claims: new/changed, deadline proximity, monotonic/family inconsistency,
metadata completeness. Forbidden: fair value, mispricing, book dependency.

### G-P0-06C — Controversy recall

Owner: GLM; ready now. Exact executable prompt:
`handoffs/GLM_P0_06C_CONTROVERSY_RECALL.md`.

Only read frozen dispute/corpus fixtures or caller-provided source data. Every
hit binds absolute source identity, PIT cutoff, corpus revision/hash and source
offset. Missing/mismatched source skips this provider without blocking others.

### G-P0-06D — Wallet recall

Owner: GLM; ready now. Exact executable prompt:
`handoffs/GLM_P0_06D_WALLET_RECALL.md`.

Only caller-provided/frozen wallet facts. Address is never Entity. Current stale
stores must not create current hits. Wallet payload and trade direction remain
private recall inputs and never enter Blind output.

### G-P0-06E — Optional book structural anomaly recall

Owner: GLM after P0-05A.

Consumes normalized paired books only. Allowed output is structural price range,
spread/depth, paired consistency and family monotonicity. Missing/stale book
skips this provider only. No capture, transport or fair-value estimate.

### T-P0-08A — Blind projection and packet builder

Owner: Codex contract/integration owner; Terra may implement the bounded module.

Owned files: new `src/polymarket_alpha/research/blind.py` and focused tests.

Inputs: CandidateCard, RecallHits, accepted RuleContract/Gate A and explicitly
allowed non-market ClaimEvidence. Output: released BlindCandidateProjection and
BlindResearchPacket only.

Acceptance: recursive adversarial strings for price, probability, slug, URL,
YES/NO direction, wallet hint and Polymarket sources; controlled question
templates/provenance; input reordering deterministic; private recall fields
never serialized.

### T-P0-08B/C — Result importer and Market stage

Owner: Codex; Terra implementation after P0-01R2/P0-02R2.

- P0-08B imports a manually supplied blind result, recomputes source/excerpt
  hashes, quarantines wrong version/hash/stage and never trusts model-reported
  hashes.
- P0-08C, after P0-05, creates FORMAL_REVIEW demand only from an accepted blind
  receipt, accepts fresh paired book, freezes MarketResearchPacket and imports
  the market-aware result.

No external model/API automation is part of P0.

### T-P0-09A/B/C — Candidate protocol, rank and ledger

Owner: Codex; Terra may implement bounded reducers after contract freeze.

- P0-09A: pure allowed-transition/invalidation reducer and projection rebuild.
- P0-09B: integrate Blind, book, Market and Rule B transitions; stale inputs and
  mismatches fail closed.
- P0-09C: deterministic ranker, ReviewDecision, watchlist query and
  PredictionRecord writer; position remains `NO_POSITION | SIMULATED`.

Acceptance: complete transition matrix, invalid edges, late RecallHit,
RECALL_EXPIRED/EVIDENCE_STALE/RULE_REVISION_INVALIDATED/BOOK_REFRESH_REQUIRED,
closed/resolved, concurrent replay, golden ranking and event/projection
reconciliation.

### G-P0-10, C-P0-11 final, C-P0-12

- P0-10 adapts existing WorkOrder/lease/evidence-chain APIs after P0-09. It may
  not own Candidate state or create a second scheduler.
- P0-11 final reruns static, generic transport, OS network-deny, env/endpoint,
  dynamic import/process/shell and signing/order sentinel tests over the complete
  Alpha entrypoint.
- P0-12 is coordinator-only: A01–A18 offline fixture run, rollback rehearsal,
  exact commit/source identities and final seal.

## 4. Immediate wave and concurrency

Start now:

```text
Codex: C-P0-01R2
GLM window 1: G-P0-06C
GLM window 2: G-P0-06D
Terra: either T-P0-08A or T-P0-09A, one bounded worker at a time
```

Do not start P0-04 until P0-01R2 publishes the exact ChangeEvent schema. Do not
start P0-05 implementation against production owner state; P0-05 begins with
offline demand/receipt and normalized artifact fixtures. Do not combine 06B/C/D/E
into one worker.

## 5. Merge and evidence rule

Every worker handback must contain exact changed files, test commands/results,
golden hashes, limitations, out-of-scope confirmation and available usage
telemetry. Codex independently reviews the diff and reruns critical tests.

Only coordinator commits can move a task from `READY_TO_REVIEW` to `ACCEPTED`.
Passing P0 offline tests never upgrades operational or production readiness.
