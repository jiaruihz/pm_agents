# GLM-5.3 Independent Review and Rule Parse Contract V1

## Purpose

One external GLM-5.3 call may return two separately validated typed sections:
IndependentSemanticReview and StructuredRuleParseProposal. This controls cost
without merging their authority. Both remain untrusted proposals.

## Allowed input

- exact safe SemanticTriageProjection bytes and hash;
- safe canonical rule segments and segment ids;
- output schema and budget/model policy;
- deterministic missing-field or compiler error codes when applicable.

## Forbidden input

- any GLM-4.7 output, disposition, rationale, confidence, or receipt;
- price, odds, book, market probability, wallet facts or direction;
- RecallHit reason text or price-derived feature;
- GPT Pro output;
- private market ids, venue slug, market URL, or private rebind map.

## Output

IndependentSemanticReview contains topic, entities, clocks, source-type hints,
researchability, ambiguities, and an independent non-authoritative disposition.

StructuredRuleParseProposal is a list of field name, proposed value,
source_segment_id, exact_quote, confidence, and unresolved flag.

Provider, displayed model, prompt/schema versions, attempt/work-order/Candidate/
route identities and hashes, rule-source identity, exact wrapper/return hashes,
usage, cost and duration are receipted. Extra fields are forbidden. The sealed
work order includes hard token/cost/duration ceilings.

The model must not produce authoritative offset, source hash, RuleContract id,
RULE_A_PASS, probability, fair value, edge, or trading action. The deterministic
binder locates exact quotes in canonical source bytes and recomputes offsets and
hashes. Candidate snapshot, rule artifact and source hashes must agree across
the proposal and every segment. Missing, fabricated, duplicate, cross-Candidate,
cross-source, partial or extra bindings fail closed.

A PATCH is valid only when its quote occurs exactly once in the declared frozen
segment and its artifact id, segment hash and offsets recompute. The approval
contract replays the ordered unique-field patches from the embedded before
draft and verifies the sealed after draft, hashes and identities.

## Failure and rollback

Timeout, budget, schema, leakage, coverage, or binding failures are append-only
typed attempt receipts or quarantine. A different return for the same logical
attempt keeps the same record id and therefore fails as a repository content
conflict. Disabling the adapter routes flagged cases to
human review or the existing direct compiler path; no attempt is deleted and no
second Rule compiler is introduced.
