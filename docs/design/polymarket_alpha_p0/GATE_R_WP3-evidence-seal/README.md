# Gate R WP3 Evidence Seal

Disposition: `COMPLETE`

Scope: offline atomic Blind QuestionSet/SourcePlan compilation and exact
UTF-8/LF work-order prompt sealing. No provider call, network, browser,
repository migration, scheduler, order, signing or private-key capability was
added or exercised.

## Delivered behavior

- The compiler rebuilds controlled question text/id from RuleContract templates
  and proves the Blind packet derives from the sealed Candidate and rule.
- Candidate rule-source artifact id and hash must both match; cross-Candidate,
  cross-rule, cross-packet and cross-plan combinations fail closed.
- QuestionSet, SourcePlan, leakage receipt, atomic plan seal and prompt seal each
  recompute their own complete content-derived id/hash in the shared model.
- SourcePlan freezes question membership, critical ids/types, primary-source
  policy, venue/mirror exclusions, budgets, stop/freshness/availability policy.
- Prompt bytes and all metadata bind Candidate, Rule, packet, QuestionSet,
  SourcePlan, atomic plan seal, output schema, provider policy, expiry, full
  hash, preview hash and byte length.

## Verification

- Focused WP1-WP3 and related Blind/contracts/research regression: `132 passed`.
- Full Alpha regression: `701 passed`, `3 failed`.
- The same three managed-environment canaries fail because nested
  `/usr/bin/sandbox-exec` returns `sandbox_apply: Operation not permitted`.
  This matches the pre-WP3 failure set and is unrelated to WP3 source.
- Python compilation, design-manifest verification, `git diff --check`, and the
  scoped capability-import scan pass.

The independent review initially returned `REQUEST_CHANGES`. All reviewer and
coordinator findings in `REVIEW.md` were closed before this seal. Worker and
reviewer usage telemetry were unavailable from the runtime and are recorded as
such.
