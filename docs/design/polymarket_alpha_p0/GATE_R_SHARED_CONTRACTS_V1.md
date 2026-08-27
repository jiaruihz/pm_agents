# Gate R Shared Contracts V1

Status: CONTRACT_DESIGN_FROZEN
Owner: existing P0-01 shared-contract owner

All contracts use canonical serialization, extra-field rejection, immutable
content-derived identity where applicable, UTC clocks, append-only revisions,
and repository idempotence with conflicting duplicates quarantined.

## CandidateSnapshotSeal

Required fields: schema_version, candidate_id, candidate_revision_id,
canonical_market_revision_id, canonical_rule_source_artifact_id,
rule_source_sha256, rule_source_byte_length, lifecycle_state,
eligibility_as_of_utc, eligibility_policy_id/version,
allowed_projection_input_ids, created_at_utc, seal_id and seal_sha256.

Eligibility is one of ELIGIBLE, MARKET_CLOSED, RESOLVED, SUPERSEDED,
DEADLINE_ELAPSED, DUPLICATE, REFRESH_REQUIRED, INVALIDATED or ARCHIVED.

## TriageRoutingDecision

Required fields: routing_decision_id, candidate_snapshot_id/hash,
triage_receipt_id/hash, rule_dry_run_receipt_id/hash, risk_tier,
risk_reason_codes, sample_policy_id, sample_seed, sample_context_id,
sampled, future_policy_sampled, future_sample_policy_id,
future_policy_action, triage_attempt_id, action, nullable refresh_after_utc and
created_at_utc. Sampled describes the actual GLM-5.3 route; the future fields
preserve the counterfactual production sampling decision during the 100-percent
Gate R calibration pilot.

Action is one of TRY_DIRECT_COMPILE, REQUEST_GLM53, HUMAN_RULE_REVIEW or
DEFER_NONTERMINAL.

## IndependentSemanticReview and StructuredRuleParseProposal

Both bind review/proposal id, attempt id, Candidate snapshot id/hash,
projection id/hash, routing-decision id/hash, work-order id/hash, canonical rule
source artifact/hash, provider/requested/reported model, prompt/schema versions,
prompt hash, raw wrapper hash, provider-return hash and usage/cost/duration.
The logical record id deliberately excludes provider-return bytes so a second,
different return for the same sealed attempt becomes a repository content
conflict instead of a second logical result.

Semantic review contains topic, entities, relevant_clocks, source_type_hints,
researchability, ambiguities and independent_disposition_proposal.

Every rule proposal contains field_name, proposed_value, source_segment_id,
exact_quote, proposal_confidence and unresolved. Model-provided offset, source
hash, RuleContract id and Rule A status are forbidden.

## RuleReviewApproval and RuleInterpretationPatch

Required fields: review_receipt_id, rule_contract_draft_id, before_hash,
reviewer_id, reviewed_at_utc, action, reason_codes, audit-only review_note and
nullable after_draft_id/hash.

Patch operations bind field_path, old_value_hash, new_value, source segment and
artifact identity, source-content hash, exact quote and recomputed offsets.
Approval contains the before draft and, for PATCH, the ordered after draft;
model validation replays unique field patches and verifies before/after hashes,
after-draft identity and receipt identity. Action is APPROVE, PATCH, DEFER or
REQUEST_CORRECTION.

## IndependentReviewAttemptReceipt

Every external attempt binds the logical work order, Candidate snapshot,
provider/model, prompt hash, exact wrapper/return hashes when present,
usage/cost/duration and one status: ACCEPTED, QUARANTINED or BUDGET_EXCEEDED.
Schema, leakage and budget failures remain append-only receipts attached to the
typed quarantine; they cannot advance.

## BlindResearchQuestionSet

Required identity: question_set_id/hash/schema_version, candidate_snapshot_id,
blind_projection_id, rule_contract_id/hash, compiler_policy_id/version,
research_as_of_utc, pit_cutoff_utc, allowed input artifact ids/hashes,
leakage_scan_receipt_id and created_at_utc.

Each question contains question_id, claim_type, neutral_question_text,
required_answer_type, evidence_target, time_scope, required, dependency ids,
allowlisted provenance template and Blind evidence ids. Its id is derived from
that complete payload. The QuestionSet model recomputes its complete id/hash and
orders `PIT cutoff <= research as-of <= creation`.

## SourcePlan

Required identity: source_plan_id/hash/schema_version, question_set_id/hash,
rule_contract_id/hash, source_policy_id/version, pit_cutoff_utc and
created_at_utc.

Policy fields: allowed/forbidden source classes and domains, primary source
requirements by claim type, fallback policy, source-independence policy,
capture preference, minimum claim coverage, critical-claim policy,
max_sources/searches/elapsed_minutes/attempts, stop conditions and PIT
freshness/availability rules.

SourcePlan also freezes all QuestionSet question ids, critical claim ids/types,
forbidden source classes/domains and complete plan budgets. Critical ids must be
QuestionSet members; critical types require primary-source policies; allowed
and forbidden classes/domains cannot overlap. Venue, Gamma, CLOB and mirror
sources fail in the model validator even if a caller bypasses the compiler.

## BlindWorkOrderPromptSeal and ExportApprovalReceipt

Prompt seal binds work_order_id, research_job_id, attempt_policy_id,
Candidate/Rule/Blind packet/QuestionSet/SourcePlan identities and hashes,
atomic plan-seal identity/hash, output_schema_id/hash, provider policy/version,
content type, encoding, newline
mode, byte length, prompt hash, preview hash, created/expires UTC and seal hash.
The model recomputes the work-order id and full metadata seal; byte verification
revalidates the typed seal before checking UTF-8/LF, full hash, preview, length
and expiry.

Approval binds approval_receipt_id, work_order_id, prompt hash, approver,
approved/expires UTC, APPROVE or REJECT, review check codes, exact-file-copy
policy, nullable patch id and receipt hash.

## ResearchReturnCaptureSeal and SourceCaptureManifest

Return seal binds research job, attempt and work order ids, prompt hash,
approval receipt, provider UI/displayed model/session mode, operator,
start/completion/capture clocks, raw transcript/response/JSON appendix
locators/hashes/lengths, JSON parse status, source manifest id/hash,
copy-attestation status, observed tool usage and return seal hash.

Every source capture contains source_capture_id, canonical URL, title,
publisher, source class, primary/secondary flag, published/updated/effective/
accessed clocks, PIT availability, capture scope, representation, content type,
byte length, locally recomputed hash, artifact locator, claim ids, quote locator
or excerpt, redirect chain and archive/version identity.

Capture scope is FULL_DOCUMENT, EXCERPT_ONLY or REFERENCE_ONLY. Representation
is ORIGINAL_BYTES, SAVED_HTML, RENDERED_PDF, TEXT_EXPORT, SCREENSHOT or NONE.

## MarketComparison

Required fields: comparison_id, accepted Blind result id/hash, Blind
central/low/high/as-of, book receipt and snapshot ids/hashes/capture clock,
executable YES/NO bid/ask and depth at policy size, fee/slippage/cost policy
id/version, YES/NO edge intervals, staleness/liquidity/cross-outcome flags,
status, reason codes, created_at_utc and hash.

## Decision bindings

ResearchResultEnvelope and ProbabilityEstimate add QuestionSet, SourcePlan,
work-order and return-seal identities plus conditioning statement, as-of/cutoff
and low/central/high. Blind market-aware fields remain null.

ReviewDecision and PredictionRecord bind accepted deterministic Market result,
MarketComparison, Rule B receipt and accepted Blind baseline. Execution must be
NO_ORDER; any other value fails closed.
