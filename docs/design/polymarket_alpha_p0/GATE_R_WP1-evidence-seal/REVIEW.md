# WP1 Independent Review and Coordinator Closure

Reviewer: luna_verifier
Mode: read-only
Result before fixes: 3 P1 findings, 2 P2 findings

## Finding 1 — Cross-Candidate GLM binding

Original issue: route_triage checked eligibility but did not prove that the
decision and receipt belonged to the current CandidateSnapshot, projection and
attempt.

Fix: eligible routing now requires exact snapshot id/hash on both records,
matching projection id, matching nonblank attempt id and ordered clocks.
Cross-Candidate and cross-attempt fixtures fail closed.

## Finding 2 — Invalidated Candidate eligibility

Original issue: invalidated, refresh-required, inactive and archived lifecycle
projections could still become ELIGIBLE.

Fix: deterministic_eligibility emits REFRESH_REQUIRED, INVALIDATED or ARCHIVED
before deadline/model logic. The test matrix also covers closed, resolved,
superseded, elapsed and duplicate.

## Finding 3 — Future sampling contract

Original issue: D_MODEL_ONLY had no 20-percent audit and R1 could not guarantee
one per family per 50-Candidate cohort.

Fix: sampling now hashes snapshot, seed and saved family/cohort context.
D_MODEL_ONLY uses 20 percent. R1 uses 10 percent plus the cohort minimum. The
first Gate R pilot still routes every eligible Candidate to GLM-5.3.

## Finding 4 — sampled semantics

Original issue: pilot action could request GLM-5.3 while sampled was false.

Fix: sampled records the actual request. future_policy_sampled,
future_policy_action and future_sample_policy_id preserve the counterfactual
future decision separately.

## Finding 5 — Disagreement route binding

Original issue: comparator could combine a snapshot with another snapshot's
route and arbitrary attempt ids.

Fix: comparison requires exact route snapshot id/hash, REQUEST_GLM53 authority,
the routed GLM-4.7 attempt as first attempt, a distinct GLM-5.3 attempt and a
saved route hash. Unknown or incomplete comparison fields fail closed.

## Coordinator additional hardening

- New WP1 contract ids are verified as content-derived inside the models.
- CandidateSnapshot seal hash is mandatory.
- Snapshot, dry-run, GLM import and routing clocks must be ordered.
- Snapshot-bound semantic projections contain exactly one Candidate item.
- Snapshot-bound semantic decision/receipt ids include attempt and snapshot
  identity, so identical retry bytes remain distinct append-only attempts.
- Direct compile cannot be asserted for an incomplete dry-run.
