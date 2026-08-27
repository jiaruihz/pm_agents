# Polymarket Alpha — Current Engineering Baseline and Next Phase

Status date: 2026-08-28

```text
COORDINATOR_DISPOSITION=ACCEPT_WITH_REQUIRED_CHANGES
P0_OFFLINE_PIPELINE=COMPLETE
OFFLINE_OPERATIONAL_BRIDGE=ACCEPTED_AFTER_CODEX_REVIEW_AND_GLM_SELF_REVIEW
P1_OFFLINE_RESOLUTION_LEARNING=COMPLETE
P1_RESEARCH_BRIEF_AND_DRAFT_COMPILATION=COMPLETE
P1-A01=COMPLETE
P1-A02=COMPLETE
P1-A03=COMPLETE
P1-A04=COMPLETE
P1-A05=COMPLETE
P1-A06=COMPLETE
P1-A07=COMPLETE
GLM_SEMANTIC_TRIAGE=IMPLEMENTED_AS_ADDITIVE_SEAM
CONTROLLED_50_MARKET_E2E=COMPLETE_WITH_LIMITATIONS
NEXT_ENGINEERING_PHASE=GATE_R_CONTROLLED_MANUAL_GPT_PRO_BLIND_PILOT
NEXT_ENGINEERING_PHASE_STATUS=WP1_COMPLETE_BLOCKED_PENDING_WP2_TO_WP5
GATE_R_WP1=COMPLETE
GATE_R_WP1_EVIDENCE=GATE_R_WP1-evidence-seal
TARGET_READINESS=READY_FOR_CONTROLLED_MANUAL_GPT_PRO_BLIND_RESEARCH_PILOT
TARGET_READINESS_SCOPE=CONTROLLED_MANUAL_NO_ORDER_ONLY
READ_ONLY_OPERATIONAL_PILOT=NOT_APPROVED
EXTERNAL_RESEARCH_NETWORK_PILOT=NOT_APPROVED
DAILY_READ_ONLY_SHADOW=NOT_APPROVED
PRODUCTION_CAPTURE_EXPANSION=NOT_AUTHORIZED
LIVE_ORDER_SIGNING_PRIVATE_KEY=STRICTLY_OUT_OF_SCOPE
```

The offline substrate is accepted and retained. WP1 has closed the canonical
lifecycle and deterministic routing portion. The next phase is not yet
authorized to call GPT Pro: WP2 through WP5 must close the remaining accepted
orchestration blockers. WP6 then requires a separate explicit controlled-pilot
authorization. A Gate R pass still does not authorize daily operation,
production capture expansion, automated browser execution, or trading.

## 0. Current canonical design

The current program truth is:

- GPT_PRO_PIPELINE_REVIEW_RESULT_V3.md
- RESEARCH_ORCHESTRATION_V3.md
- GATE_R_SHARED_CONTRACTS_V1.md
- DETERMINISTIC_TRIAGE_ROUTING_POLICY_V1.md
- GLM_5_3_INDEPENDENT_REVIEW_AND_RULE_PARSE_CONTRACT_V1.md
- BLIND_RESEARCH_PLAN_CONTRACT_V1.md
- GPT_PRO_MANUAL_HANDOFF_PROTOCOL_V1.md
- MARKET_AWARE_REVIEW_POLICY_V1.md
- GATE_R_CONTROLLED_MANUAL_PILOT_PLAN_V1.md

The reviewed GPT_PRO_PIPELINE_REVIEW_V3 package and all evidence-seal READMEs
remain immutable historical inputs. Sections 4 and 5 below describe the
completed P1-A01 through P1-A07 offline wave and are retained as historical
implementation lineage; they are no longer the current next-phase plan.

## 1. Coordinator integration decision

GLM-OP-01 through GLM-OP-04 are accepted into the current offline engineering
baseline. The producer report remains a historical self-report with disposition
`COMPLETE_FOR_CODEX_REVIEW`; the coordinator disposition above is the current
program truth.

The accepted implementation provides:

- captured Gamma response ingest from caller-supplied bytes;
- bounded paired book-demand outbox writing;
- import of paired artifacts produced by the existing `weather_market_books`
  owner;
- a resumable single-market coordinator from Gamma ingest through `NO_ORDER`
  finalization.

Codex's first independent review closed symlink confinement, multi-market
zero-write, and future-clock budget findings. GLM's later self-review then
closed typed-failure gaps around state sealing, result replacement, malformed
manifests, and book/result resume paths. The latter commit is newer than the
historical Codex seal, so this document records a fresh current-HEAD rerun.

Current verification:

```text
tests/polymarket_alpha=587 passed in 9.12s
operational_focused=50 passed in 1.31s
operational_source_audit=0 violations
operational_cli_audit=0 violations
NETWORK_USE=NONE
PRODUCTION_ACCESS=NONE
ORDER_CAPABILITY=NONE
```

Evidence lineage:

| Layer | Evidence |
|---|---|
| GLM implementation and final self-review | `glm_handoff/GLM_OPERATIONAL_REMAINDER_RESULT.md`; commits `b7a811d0`, `401a636c`, `673767a1` |
| Codex independent review and fixes | `GLM_OPERATIONAL_REMAINDER_CODEX_REVIEW-evidence-seal/`; commit `855ce750` |
| P0 unified offline pipeline | `P0_UNIFIED_OFFLINE_PIPELINE-evidence-seal/` |
| P1 resolution and learning | `P1_RESOLUTION_LEARNING-evidence-seal/`, `P1_BACKFILL_PLANNER-evidence-seal/` |
| P1 source intake and research preparation | `P1_INTAKE_AND_RESEARCH_BRIEF-evidence-seal/`, `P1_RESEARCH_DRAFT_COMPILER-evidence-seal/` |

## 2. What the system can and cannot do now

The implemented path is:

```text
caller-supplied captured Gamma bytes
  -> catalog/change events
  -> pre-book recall + optional book anomaly recall
  -> Candidate -> Rule A
  -> immutable Blind brief/draft/result import
  -> existing-owner paired book demand/artifact bridge
  -> immutable Market brief/draft/result import
  -> Rule B -> NO_ORDER decision ledger
  -> caller-supplied resolution intake
  -> append-only scoring/calibration/backfill
```

The data, recall, rule, research packet/import, formal book, decision, and
learning layers are implemented offline. Three operating seams remain external:

1. a human or separately authorized agent still performs Blind and Market
   research and returns the draft/result artifact;
2. Gamma, official resolution sources, and book capture are not autonomously
   fetched by Alpha;
3. there is no daily polling/scheduling loop.

Therefore the system is an accepted offline engineering baseline, not a live
daily scanner. No existing weather collector, scheduler, database, production
configuration, or order path was replaced or activated.

## 3. Integration decisions carried forward

1. `accept_formal_book()` is the canonical market-book acceptance API. Do not
   add a second alias only to match an obsolete handoff name.
2. `GammaResponseReceipt` remains local to the operational seam until a second
   real producer exists. Before a live Gamma transport is introduced, P0-01's
   contract owner must promote it additively or explicitly seal the local type
   as the transport contract.
3. The immutable filesystem primitives currently imported from private
   `research.handoff` symbols must become one public artifact-store API before
   a scheduler or external research work queue is added. Existing callers must
   migrate to that API; no second filesystem implementation may be created.
4. Alpha must not embed provider SDKs, browser control, generic HTTP, shell, or
   subprocess execution in its domain process. It emits immutable work orders;
   an external harness executes authorized research; Alpha validates and imports
   the returned artifact.
5. Wallet-derived data remains recall-only and never enters Blind work orders.
   Blind work orders also exclude venue identity, slug/URL, direction, price,
   book, market probability, and price-derived reason text.
6. Gamma lifecycle state is not settlement truth. Resolution learning accepts
   only source-specific, hash-bound artifacts under an explicit RuleContract
   source/precedence policy.

## 4. Historical completed phase objective

The completed historical phase was `P1_CONTROLLED_RESEARCH_AUTOMATION_OFFLINE`.

Its objective is to replace the manual copy/paste seam with a replayable work
order and result-return protocol while retaining the current no-network Alpha
process, immutable artifacts, Blind isolation, manual fallback, and `NO_ORDER`
boundary. It does not perform a real GPT/browser call and does not start a
daily scanner.

### Dependency graph

```text
P1-A01 public immutable artifact API ----+----> P1-A06 resolution adapter fixtures -----+
                                         |                                           |
P1-A02 research job contracts + storage -+--> P1-A03 work-order/fake executor           |
                                                  |                                    |
                         P1-A01 + P1-A02 + P1-A03 +--> P1-A04 orchestrator              |
                                                                  |                    |
                                                                  +--> P1-A05 scheduler +
                                                                                       |
                                                                                       +--> P1-A07 E2E seal
```

Wave 1 starts P1-A01 and P1-A02 in parallel. P1-A03 and P1-A06 start only after
their upstream public contracts are released. P1-A04 owns orchestration state;
P1-A05 owns scheduling policy and may not duplicate job state. P1-A07 is
coordinator-owned and integrates every workstream.

## 5. Historical P1-A01 through P1-A07 work packages

### P1-A01 — Public immutable artifact API

Owner: Codex shared integration owner. A single Terra worker may implement the
bounded refactor after the exact owned files are frozen.

Scope:

- introduce one public API for confined immutable read/write, locator
  normalization, hash verification, and idempotent conflict handling;
- migrate `research` and `operational` callers away from private cross-package
  imports;
- preserve serialized bytes, locators, hashes, typed failures, and existing
  manual handoff behavior.

Acceptance: all current 587 Alpha tests remain green; adversarial symlink,
traversal, replacement, replay, and conflict fixtures pass; no duplicate
artifact-store implementation remains.

Rollback: compatibility wrappers delegate to the new owner until all callers
are migrated. Do not delete historical artifacts or old evidence seals.

### P1-A02 — Research job and attempt contracts

Owner: Codex shared-contract and migration owner.

Scope:

- additive `ResearchJob`, `ResearchAttempt`, `ResearchWorkOrder`,
  `ResearchReturnReceipt`, lease, retry, cancellation, quarantine, and terminal
  state contracts;
- bind stage, packet id/hash, brief locator/hash, rule hash, provider policy id,
  attempt number, input/output artifact hashes, and effective clocks;
- add one additive offline migration and repository methods. No current DB is
  migrated in this task.

Acceptance: deterministic IDs, append-only transitions, expired lease reclaim,
duplicate result idempotence, conflicting result quarantine, stale rule/packet
invalidation, and copy/replay rehearsal.

### P1-A03 — Provider-neutral work-order adapter and fake executor

Owner: bounded Terra or GLM worker; no shared schema ownership.

Scope:

- render released job contracts into an immutable provider-neutral work order;
- provide a deterministic fake executor for fixtures only;
- validate returned provider draft/source artifacts before passing them to the
  existing draft compiler and result importer;
- keep the external execution port outside the Alpha domain process.

Acceptance: Blind adversarial strings, recursive nested content scanning,
Polymarket source exclusion, source-byte hash recomputation, malformed return,
timeout, retry, and duplicate-return fixtures. Any wallet payload in Blind is a
hard failure.

### P1-A04 — Resumable research job orchestrator

Owner: bounded Terra worker after P1-A01 through P1-A03 release.

Scope:

- drive `packet frozen -> job emitted -> result returned -> draft compiled ->
  result imported` for Blind and Market stages;
- require an accepted Blind baseline before a Market job is emitted;
- resume from immutable state without duplicate work orders or ledger effects;
- leave the current manual handoff path available as a fallback.

Acceptance: crash at every handoff, replaced result, stale evidence, RuleContract
revision, retry exhaustion, quarantine, and exact replay. The final decision is
still `NO_ORDER`.

### P1-A05 — Synthetic scheduler and backpressure

Owner: bounded Terra worker after P1-A04. It owns timing policy only.

Scope:

- synthetic-clock scan scheduling, job leases, bounded retry, TTL, dedupe,
  per-run/per-day budgets, and append-only run receipts;
- no daemon, cron, launchd, network request, production DB, or weather owner
  mutation in this phase.

Acceptance: restart/replay, overlapping tick, clock skew, budget exhaustion,
partial provider failure, stale Candidate, and graceful disable/rollback.

### P1-A06 — Authoritative resolution adapter port and fixtures

Owner: bounded GLM or Terra worker. Codex retains RuleContract and storage
ownership.

Scope:

- define source-specific adapters that consume caller-supplied official source
  bytes and emit the already released resolution intake model;
- implement frozen fixture adapters for at least two source-policy shapes;
- make ambiguity, dispute, supersession, correction, and unsupported source
  explicit.

Acceptance: no Gamma-as-truth fallback; exact source bytes and parser assertion
are hash-bound; correction-chain replay and affected prediction counts are
deterministic.

### P1-A07 — Coordinator integration and evidence seal

Owner: Codex coordinator only.

Scope:

- independently review all changed code, close findings, and rerun relevant plus
  full Alpha suites;
- prove manual fallback, Blind isolation, append-only state, no-network Alpha
  capability, `NO_ORDER`, and deterministic restart;
- publish one consolidated evidence seal with commands, hashes, test results,
  limitations, rollback rehearsal, and task usage telemetry.

Historical P1-A07 result: the provider-neutral offline automation baseline
completed. That result did not authorize an external research call and has
been superseded as a readiness statement by the Gate R V3 contracts.

## 6. Gates after the current offline baseline

The following gates are separate and require explicit authorization. Passing
one does not imply that another passed.

### Gate R — Controlled manual GPT Pro Blind research pilot

WP1 is complete; WP2 through WP5 must implement the remaining canonical V3
contracts before any pilot call. After separate authorization, WP6 uses eight
preregistered Candidates,
independent GLM-5.3 review, human exact-file prompt export, raw GPT Pro return
and actual source capture, deterministic import, fresh paired books,
Blind-vs-Book comparison and NO_ORDER ledger. The Integration Gatekeeper owns
the single evidence seal. A pass means controlled manual research only; it
does not authorize automated provider/browser execution or daily scans.

### Gate O — Read-only operational market-data pilot

Uses the existing `READ_ONLY_OPERATIONAL_PILOT_GATE.md`: bounded Gamma reads,
arbitrary binary token identity, existing-owner paired books, rate/storage
budgets, weather isolation, staleness, and rollback. It does not automate
research and does not authorize capture expansion.

### Gate S — Daily read-only shadow

May start only after both Gate R and Gate O pass. It combines the approved
research executor and market-data paths under bounded scheduling, observability,
kill switch, stale-run detection, and rollback. Outputs remain simulation and
watchlist artifacts only.

Platform-level extraction of the market-book owner is a later P1/P2 refactor
after Gate O produces real load and isolation evidence. Live order, signing,
private-key access, and production capture expansion remain outside this plan.
