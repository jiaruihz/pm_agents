# Gate R Controlled Manual GPT Pro Pilot Plan V1

Status: IMPLEMENTATION_IN_PROGRESS_WP1_WP2_COMPLETE
Entry: WP1 through WP5 must pass before WP6 pilot authorization.

## Work packages

WP1 — Canonical lifecycle and deterministic routing

Owner: existing Candidate lifecycle/orchestrator owner.
Deliver: CandidateSnapshotSeal, eligibility transitions, Rule dry-run receipt,
RiskTier, deterministic routing and disagreement framework; connect GLM-4.7 to
the sole canonical path.
Verify: transition matrix, duplicate/restart/replay, elapsed/closed/superseded,
policy/seed determinism, full Alpha regression.
Rollback: feature-disable triage routing and retain append-only records.

WP2 — Independent GLM-5.3 and rule binding

Owner: sole Rule/shared-contract owner.
Status: COMPLETE; evidence: `GATE_R_WP2-evidence-seal/`.
Deliver: typed independent review and rule proposal, sidecar adapter,
no-GLM-4.7-input policy, quote binder, bounded human rule patch/approval.
Verify: prompt isolation, fabricated/cross-source quote, stale patch, risk-tier,
timeout/budget/quarantine and Rule A/B same-hash tests.
Rollback: disable adapter; route to human/direct compiler.

WP3 — Blind plan and prompt seal

Owner: existing Research Packet/work-order owner.
Deliver: atomic QuestionSet/SourcePlan compiler, provenance/leakage receipt,
PromptSeal and identity-bound work order.
Verify: byte determinism, forbidden-origin corpus, plan cross-hash, cutoff/rule/
policy invalidation and golden prompt.
Rollback: pin the prior reader; retain old artifacts as incompatible.

WP4 — Human handoff and return/source capture

Owner: existing research handoff/ArtifactStore owner.
Deliver: ExportApprovalReceipt, structured patch/reseal, operator runbook, raw
return/JSON/source capture, ReturnCaptureSeal and retry/quarantine rules.
Verify: exact-file copy rehearsal, prompt mutation, raw preservation,
FULL/EXCERPT/REFERENCE, PIT/tamper, confinement and real importer roundtrip.
Rollback: disable external handoff and retain fixture/manual fallback.

WP5 — Deterministic market comparison

Owner: existing Rule B/Decision owner.
Deliver: MarketComparison, cost/depth/staleness bindings,
DeterministicMarketAssessmentCompiler, disabled optional critique contract and
refresh-on-new-info semantics.
Verify: Blind/book/rule bindings, stale/one-sided/depth cases, probability
overwrite rejection, existing importer acceptance and NO_ORDER DB checks.
Rollback: disable comparator version and retain artifacts.

WP6 — Gate R execution and evidence seal

Owner: Integration Gatekeeper only, after explicit pilot authorization.
Deliver: preregistered 8-case selection, budgets, operator checklist, real
GLM-5.3 and GPT Pro Blind attempts, source captures, comparator, ledger,
rollback rehearsal and immutable evidence seal.

No work package may create a second collector, repository, migration owner,
Rule compiler, artifact store, scheduler, browser-capable Alpha process, order
path, signing path, or private-key dependency.

## Preregistered sample

- S1_SIMPLE_OFFICIAL
- S2_NUMERIC_THRESHOLD
- S3_SOURCE_PRECEDENCE
- S4_CORRECTION_FINALITY
- S5_SUBJECTIVE_AMBIGUOUS
- S6_MODEL_DISAGREEMENT
- S7_DETERMINISTIC_INELIGIBLE
- S8_SOURCE_INSUFFICIENT

Selection, expected risk tier, PIT cutoff, budget and expected failure path are
frozen before any GLM-5.3 or GPT Pro output is viewed.

## Gate metrics

- 8/8 end in explicit audited states; no dangling state.
- Accepted replay is byte/hash deterministic; duplicate imports create no
  duplicate logical rows.
- Blind leakage and GLM-4.7-to-GLM-5.3 leakage are zero; adversarial canaries
  fail closed.
- Rule A PASS cases have complete source span, deadline, timezone, precedence
  and condition coverage.
- Every human edit is a structured patch followed by reseal/reapproval.
- Advancing required-claim coverage is at least 95 percent and critical claims
  have captured primary evidence; REFERENCE_ONLY cannot support them.
- Material sources record access time and PIT availability; critical NO or
  UNVERIFIED evidence cannot advance.
- Malformed/tampered/leaking fixtures quarantine; honest evidence insufficiency
  remains a valid non-advancing result.
- Every external attempt has budget/usage or operator-effort telemetry.
- Fresh book occurs only after Blind acceptance; Market result goes through the
  existing importer; all ledger execution values are NO_ORDER.
- Sidecar/export kill leaves offline fixture replay and repository reads intact.

## Disposition enum

- GATE_R_PASS_CONTROLLED_MANUAL_ONLY
- GATE_R_PASS_WITH_LIMITATIONS
- GATE_R_BLOCKED
- GATE_R_REWORK_REQUIRED

Even a pass does not authorize daily operation, production capture expansion,
automated browser execution, validated alpha, trading, signing, or live orders.
