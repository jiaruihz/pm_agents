# GPT Pro Pipeline Review V3 — Coordinator Acceptance

Review date: 2026-08-28

## Disposition

ACCEPT_WITH_REQUIRED_CHANGES

Target readiness after implementation and Gate R evidence:

READY_FOR_CONTROLLED_MANUAL_GPT_PRO_BLIND_RESEARCH_PILOT

This does not authorize external research automation, daily read-only operation,
production capture expansion, trading, signing, or private-key access.

## Coordinator ruling

The existing offline substrate is retained. The review does not justify a new
collector, repository, rule compiler, artifact store, scheduler, or order path.
The following nine findings are accepted as blocking before the manual pilot:

1. Bind semantic triage to a sealed Candidate revision and canonical lifecycle.
2. Make GLM-5.3 independent: its first review must not see GLM-4.7 output.
3. Replace free-form Coordinator Synthesis with deterministic routing and
   disagreement comparison; model majority has zero authority.
4. Move rule dry-run before optional GLM-5.3; bind model proposals to exact
   source quotes and let the sole RuleContractCompiler retain Rule authority.
5. Atomically compile BlindResearchQuestionSet and SourcePlan from allowlisted
   inputs under one deterministic plan seal.
6. Separate deterministic PromptSeal from human ExportApprovalReceipt; any
   human patch requires recompile, reseal, and reapproval.
7. Capture raw GPT Pro return bytes, JSON appendix, actual source artifacts and
   capture semantics before deterministic import.
8. Make the required market-aware step a deterministic Blind-vs-Book
   comparator. A second GPT/LLM critique is disabled for the first pilot.
9. Require one coordinator-owned Gate R evidence seal with failures, replay,
   leakage, source coverage, cost/effort, rollback, and scope limitations.

## Accepted architecture changes

- GLM-4.7 remains cheap semantic triage, not a decision authority.
- GLM-5.3 is conditional in future operation and 100% shadowed for eligible
  cases in the first 8-case pilot to measure disagreement and false defers.
- TRY_RULE_A is only a routing action. RULE_A_PASSED can only be issued by the
  shared deterministic RuleContractCompiler/RuleGate.
- Human review is split into bounded rule exception review and mandatory prompt
  export approval.
- GPT Pro performs Blind web research in a fresh isolated session and receives
  no market price, venue, wallet, recall reason, or preceding model output.
- A valid but weak return becomes INSUFFICIENT_EVIDENCE and does not advance;
  corrupt, leaking, identity-mismatched, or tampered returns are quarantined.
- Fresh paired book demand is emitted only after Blind result acceptance.
- Rule B writes only NO_ORDER decisions and must preserve the Blind estimate.

## Canonical successors

- RESEARCH_ORCHESTRATION_V3.md
- GATE_R_SHARED_CONTRACTS_V1.md
- DETERMINISTIC_TRIAGE_ROUTING_POLICY_V1.md
- GLM_5_3_INDEPENDENT_REVIEW_AND_RULE_PARSE_CONTRACT_V1.md
- BLIND_RESEARCH_PLAN_CONTRACT_V1.md
- GPT_PRO_MANUAL_HANDOFF_PROTOCOL_V1.md
- MARKET_AWARE_REVIEW_POLICY_V1.md
- GATE_R_CONTROLLED_MANUAL_PILOT_PLAN_V1.md

The reviewed GPT_PRO_PIPELINE_REVIEW_V3 directory and ZIP remain immutable
historical inputs. Their proposal document is superseded by the canonical files
above; their PACK_MANIFEST is not rewritten after review.
