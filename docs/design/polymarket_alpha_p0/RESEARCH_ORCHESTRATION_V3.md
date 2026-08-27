# Polymarket Alpha Research Orchestration V3

Status: CANONICAL_DESIGN
Readiness: WP1_TO_WP4_COMPLETE_BLOCKED_PENDING_WP5
Pilot scope: CONTROLLED_MANUAL_GPT_PRO_BLIND_RESEARCH_ONLY

## Authority boundary

Deterministic code owns identity, lifecycle, routing, hashes, Rule A/B,
acceptance/quarantine, book comparison, and ledger transitions. Models only
produce untrusted typed proposals or research drafts. Humans may approve or
submit schema-bound patches but cannot override hash, identity, leakage,
freshness, or source-coverage failures. Execution remains NO_ORDER.

## Canonical DAG

    Recall providers
      -> Candidate aggregation / dedupe / refresh
      -> Candidate eligibility + CandidateSnapshotSeal
      -> allowlist-only SemanticTriageProjection
      -> GLM-4.7 semantic proposal
      -> deterministic triage importer
      -> rule compiler dry-run + risk/complexity routing
         -> direct canonical compile, or
         -> independent GLM-5.3 semantic/rule proposal
            -> deterministic disagreement comparator
            -> deterministic quote/segment binder
            -> canonical compile
      -> conditional HumanRuleExceptionCheckpoint
         -> structured patch -> recompile
      -> Rule A
      -> atomic BlindResearchPlanCompiler
         -> BlindResearchQuestionSet
         -> SourcePlan
      -> BlindWorkOrderPromptSeal
      -> mandatory Human ExportApprovalReceipt
      -> fresh isolated Web GPT Pro Blind research
      -> raw return + JSON appendix + source capture
      -> ResearchReturnCaptureSeal
      -> existing ResearchDraftCompiler + importer
         -> quarantine, or
         -> INSUFFICIENT_EVIDENCE non-advance, or
         -> BLIND_RESULT_ACCEPTED
      -> fresh FORMAL_REVIEW paired book demand
      -> MarketResearchPacket
      -> deterministic Blind-vs-Book MarketComparison
      -> DeterministicMarketAssessmentCompiler
      -> existing MARKET importer -> MARKET_RESULT_ACCEPTED
      -> Rule B -> ReviewDecision + PredictionRecord + NO_ORDER ledger
      -> caller-supplied authoritative resolution intake
      -> scoring / calibration / backfill

Optional MarketCritique is disabled in Gate R. If later authorized, it may only
emit challenge or refresh requests and cannot replace the Blind probability.

## Canonical transitions

Hard lifecycle facts are resolved before model calls. Closed, resolved,
superseded, duplicate, or explicitly elapsed Candidates receive append-only
transitions without model cost. Model-only defer is nonterminal and must have a
refresh time or explicit refresh trigger.

Required explicit events include:

- DEFER_NONTERMINAL
- HUMAN_RULE_REVIEW_REQUIRED
- RULE_REVISION_INVALIDATED
- RESEARCH_EXPORT_APPROVAL_REQUIRED
- RESEARCH_REFRESH_REQUIRED
- EVIDENCE_STALE
- BOOK_REFRESH_REQUIRED
- BLIND_RESEARCH_INSUFFICIENT
- RETURN_QUARANTINED
- MARKET_CLOSED
- RESOLVED
- SUPERSEDED

Any Candidate revision, rule source byte, policy version, PIT cutoff, accepted
source correction, or fresh-book expiry creates a new revision and invalidates
downstream authority. Historical artifacts remain immutable and replayable.

## Isolation invariants

Blind-facing artifacts must not contain venue identity, private market id,
slug/market URL, price, odds, bid/ask/book, wallet position or direction,
candidate/order side, RecallHit free text, price-derived features, GLM output,
or operator market commentary. Recursive scans include nested strings,
attachment names, origin metadata, questions, and source-policy rendering.

The Alpha domain process does not gain browser, generic HTTP, provider SDK,
shell, subprocess, signing, private-key, or order capability. External provider
and browser work stays behind immutable work orders and validated imports.

## Human checkpoints

HumanRuleExceptionCheckpoint is conditional and sees only rule sources,
compiler diagnostics, disagreement codes, and safe projections. It produces an
approval, defer, correction request, or span-bound patch.

Human ExportApproval is mandatory for every Gate R GPT Pro attempt. It binds the
exact sealed prompt hash. The operator copies the exact file into a fresh
session and captures the full return before editing.

WP4 implements this boundary as a local-only adapter. Approval lineage binds a
parent approval plus newly resealed prompt; a typed ResearchAttempt binds the
job and lease clocks; source metadata remains serializable while separately
revalidated against immutable artifact bytes. Raw transcript, response and JSON
appendix are captured before the existing ResearchDraftCompiler and importer.
Malformed or leaking returns quarantine, and weak critical evidence is
non-advancing. The Alpha process gained no browser, network or provider client.

## Pilot success ceiling

Passing Gate R may assert only:

CONTROLLED_MANUAL_GPT_PRO_BLIND_RESEARCH_PILOT=PASS

It may not assert daily operation, autonomous browser/provider execution,
production capture health, validated alpha, trading readiness, or live order
readiness.
