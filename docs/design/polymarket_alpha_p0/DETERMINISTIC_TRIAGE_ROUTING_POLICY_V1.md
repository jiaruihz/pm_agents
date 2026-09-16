# Deterministic Triage Routing Policy V1

## Inputs and outputs

Inputs are a sealed Candidate snapshot, validated GLM-4.7 receipt, canonical
rule source, deterministic compiler dry-run diagnostics, policy version, and a
saved sample seed. Price, book, wallet facts, GPT output, and free-form model
synthesis are forbidden.

The only routing actions are:

- TRY_DIRECT_COMPILE
- REQUEST_GLM53
- HUMAN_RULE_REVIEW
- DEFER_NONTERMINAL

TRY_DIRECT_COMPILE is not Rule A approval.

## Risk tiers

R3_CRITICAL includes direct compile failure/partial coverage, missing source
identity or precedence, subjective adjudication, correction/supersession or
dispute semantics, multiple timezone/deadline conventions, or conflict between
GLM-4.7 and deterministic facts. It always requests GLM-5.3 and normally human
rule review.

R2_REVIEW includes GLM-4.7 REVIEW, confidence below 0.80, explicit ambiguity,
complexity score at least 2, composite conditions, or complex source policy. It
always requests GLM-5.3; unresolved disagreement routes human.

R1_SIMPLE requires complete direct compile, one authoritative source, exact
deadline/timezone, no ambiguity, and GLM-4.7 ADVANCE. Future operation samples
10 percent, with at least one per market family per 50 Candidates.

D_MODEL_ONLY is a GLM-4.7-only defer with no deterministic blocker. Future
operation samples 20 percent to detect a false-negative sink.

D_DETERMINISTIC covers closed, resolved, superseded, elapsed, or duplicate
Candidates. No model is called.

For the first 8-case Gate R pilot, every eligible non-D_DETERMINISTIC Candidate
runs GLM-5.3. The future-policy counterfactual route is recorded separately.

## Independence and disagreement

GLM-5.3 receives the same safe projection and safe rule segments but never the
GLM-4.7 proposal, disposition, rationale, confidence, or receipt. A
deterministic comparator evaluates typed fields only after both returns are
validated. Agreement has no evidentiary weight and cannot bypass source,
compiler, or human gates.

Disagreement receipts bind both attempt ids/hashes, field-level difference
codes, compiler diagnostics, route decision, policy version, and sample seed.

## Determinism and replay

The same snapshot, receipts, compiler version, routing policy and seed must
produce byte-identical RiskTier and TriageRoutingDecision. Policy or input
changes create a new decision; old decisions remain append-only but cannot
advance the new Candidate revision.
