# Blind Research Plan Contract V1

## Atomic compilation

One deterministic BlindResearchPlanCompiler atomically emits:

- BlindResearchQuestionSet;
- SourcePlan;
- provenance and recursive leakage receipt;
- one plan seal binding both artifacts.

The compiler accepts only an accepted RuleContract, neutral proposition, safe
Blind projection, allowlisted evidence-gap taxonomy, PIT policy, source policy,
and budget policy. It must not consume RecallHit text, wallet facts, direction,
price/book, market probability, GLM output, operator market commentary, or
search results.

## BlindResearchQuestionSet

The artifact binds Candidate snapshot, Blind projection, RuleContract/hash,
compiler policy/version, research-as-of, PIT cutoff, allowed input artifact
ids/hashes, and leakage receipt. Every question has id, claim type, neutral
text, required answer type, evidence target, time scope, required flag, and
question dependencies.

Question text must be generated only from RuleContract, neutral proposition,
and allowlisted non-market evidence-gap templates.

## SourcePlan

The artifact binds the question set and RuleContract, source policy/version,
PIT cutoff, allowed and forbidden source classes/domains, primary requirements,
fallback and source-independence policy, capture preference, claim coverage,
critical-claim policy, freshness rules, budgets and stop conditions.

SourcePlan defines a research strategy, not future findings. It cannot contain
searched URLs, conclusions, probabilities, or market-aware hints. A
RuleContract-declared authoritative resolution source identity may be included
only with explicit rule-derived provenance.

## Invalidation and acceptance

Same inputs and versions produce byte-identical artifacts and hashes. Any
change in rule, projection, policy, cutoff, questions, or source plan creates a
new plan and invalidates the old prompt approval. Cross-hash mismatch,
forbidden origin, non-neutral question, missing PIT/budget, or embedded search
result fails closed.
