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

The compiler re-renders each template from the supplied RuleContract and
recomputes the upstream Blind question id. It also reconstructs the opaque
Blind Candidate id from the sealed Candidate id, rule revision, question ids
and allowed evidence ids; a cross-Candidate packet therefore cannot be rebound.
The canonical rule-source artifact bytes/hash in CandidateSnapshot must exactly
match the supplied frozen artifact binding, not merely share an artifact id.

## SourcePlan

The artifact binds the question set and RuleContract, source policy/version,
PIT cutoff, allowed and forbidden source classes/domains, primary requirements,
fallback and source-independence policy, capture preference, claim coverage,
critical-claim policy, freshness rules, budgets and stop conditions.

The shared SourcePlan validator freezes QuestionSet membership and critical
claim types, requires their primary-source policies, rejects allowed/forbidden
overlap and independently denies venue/Gamma/CLOB/mirror sources. Builder-only
validation is not considered sufficient.

SourcePlan defines a research strategy, not future findings. It cannot contain
searched URLs, conclusions, probabilities, or market-aware hints. A
RuleContract-declared authoritative resolution source identity may be included
only with explicit rule-derived provenance.

The exact UTF-8/LF work-order prompt is followed by one LF byte and binds full
and preview hashes, byte length, output schema, provider policy, Candidate,
Rule, Blind packet, QuestionSet, SourcePlan and atomic plan seal. The prompt-seal
model recomputes its logical id and complete seal hash; verification rejects a
forged typed object before comparing bytes or expiry.

## Invalidation and acceptance

Same inputs and versions produce byte-identical artifacts and hashes. Any
change in rule, projection, policy, cutoff, questions, or source plan creates a
new plan and invalidates the old prompt approval. Cross-hash mismatch,
forbidden origin, non-neutral question, missing PIT/budget, or embedded search
result fails closed.
