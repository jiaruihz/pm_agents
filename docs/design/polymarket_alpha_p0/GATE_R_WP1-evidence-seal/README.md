# Gate R WP1 Evidence Seal

Disposition: WP1_COMPLETE_READY_FOR_WP2_WP3
Scope: OFFLINE_DETERMINISTIC_CONTRACTS_ONLY
Execution: NO_ORDER

## Delivered

- CandidateSnapshotSeal with content-derived id, rule-source byte/hash binding,
  Candidate/lifecycle identity, UTC ordering, eligibility policy and allowlisted
  projection inputs.
- Deterministic pre-model eligibility for closed, resolved, superseded,
  duplicate, elapsed, refresh-required, invalidated and archived Candidates.
- RuleDryRunReceipt, RiskTier, TriageRoutingDecision and DisagreementReceipt.
- R1/R2/R3/D_MODEL_ONLY/D_DETERMINISTIC routing.
- Gate R pilot 100-percent GLM-5.3 route with separately sealed future-policy
  counterfactual.
- Future R1 deterministic 10-percent sampling plus one-per-family-per-50
  minimum; model-only defer 20-percent audit sampling.
- GLM-4.7 projection, decision and receipt binding to exact CandidateSnapshot,
  projection policy, attempt identity and invalidation parent.
- Generic Alpha contract ledger replay and conflict behavior; no new migration
  or repository owner.

## Changed files

- src/polymarket_alpha/contracts/models.py
- src/polymarket_alpha/contracts/__init__.py
- src/polymarket_alpha/triage/semantic.py
- src/polymarket_alpha/triage/routing.py
- src/polymarket_alpha/triage/__init__.py
- tests/polymarket_alpha/test_gate_r_routing_wp1.py
- tests/polymarket_alpha/test_glm_semantic_triage.py
- docs/design/polymarket_alpha_p0/GATE_R_SHARED_CONTRACTS_V1.md
- docs/design/polymarket_alpha_p0/GATE_R_DESIGN_MANIFEST.sha256
- docs/design/polymarket_alpha_p0/ALPHA_PROGRAM_INTEGRATION_AND_NEXT_PHASE.md

## Verification

Focused command:

    PYTHONPATH=. .venv/bin/pytest -q \
      tests/polymarket_alpha/test_contracts_p0_01.py \
      tests/polymarket_alpha/test_contracts_p0_01r2.py \
      tests/polymarket_alpha/test_gate_r_routing_wp1.py \
      tests/polymarket_alpha/test_glm_semantic_triage.py \
      tests/polymarket_alpha/test_candidate_lifecycle_p0_09a.py \
      tests/polymarket_alpha/test_storage_p0_02r2.py \
      --junitxml=.../test-results/focused.xml

Result: 99 passed in 1.24s.

Full command:

    PYTHONPATH=. .venv/bin/pytest -q tests/polymarket_alpha \
      --junitxml=.../test-results/full-alpha.xml

Result: 672 passed, 3 failed in 30.40s. The three failures are the same
pre-existing managed-environment failures observed before WP1: nested macOS
sandbox-exec returns sandbox_apply: Operation not permitted. Baseline before
WP1 was 652 passed and the same 3 failures. WP1 introduced 20 passing tests and
no new failure.

Additional checks:

- git diff --check: PASS
- Python byte compilation of changed source: PASS
- source capability audit: no network/provider/browser/subprocess/order/signing
  implementation; the only matches are prohibition text in module docstrings.
- GATE_R_DESIGN_MANIFEST.sha256: all entries verified.

## Review closure

One fresh luna_verifier performed the required read-only review. It found three
P1 and two P2 issues. All were fixed by the coordinator:

1. Cross-Candidate GLM decision/receipt binding now fails closed.
2. invalidated, refresh-required, inactive and archived Candidates are resolved
   before model routing.
3. D_MODEL_ONLY 20-percent sampling and R1 family/cohort minimum are implemented
   deterministically.
4. Actual pilot selection and future counterfactual sampling are distinct
   contract fields.
5. Disagreement comparison binds snapshot, route hash and the GLM-4.7 attempt.

See REVIEW.md for the full finding-to-fix map.

## Limitations and rollback

- WP1 freezes and validates routing contracts but does not yet wire them into
  the operational coordinator. That integration is part of the following
  implementation wave.
- GLM-5.3 provider execution, Blind plan/prompt seal, GPT Pro handoff and market
  comparison are not implemented by WP1.
- Disable callers of triage.routing to roll back. New contracts are append-only
  and inert; no current DB, production config, collector or scheduler changed.
- No external network call, production access, model call or order action was
  performed.

## Agent telemetry

- Implementation worker: terra_worker, one bounded turn, no child agents.
- Independent reviewer: luna_verifier, one read-only turn, no child agents.
- Exact worker/reviewer token telemetry: unavailable from the collaboration
  runtime.
