# Independent read-only review

Reviewer contract: one fresh `luna_verifier`, read-only, no edits, no network,
no subagents. Owned scope was the new triage core, external sidecar, tests and
the real pilot artifacts.

## Findings

1. Medium: duplicate `market_id` values were not explicitly rejected while
   item ids were unique.
2. Low/medium: the durable receipt read only wrapper top-level `model`; the
   real CLI wrapper reports the actual model under `modelUsage`, leaving the
   first receipt's `reported_model` null.

## Coordinator fixes

1. Added duplicate market identity fail-closed validation and a regression test.
2. Added exact single-model extraction from `modelUsage`, multi-model ambiguity
   rejection and regression tests. Final receipt reports `glm-4.7`.
3. From repeated real calls, found that GLM inconsistently handled an elapsed
   deadline. Added deterministic eligibility/effective disposition and a test
   proving model `ADVANCE` becomes effective `DEFER` after deadline.
4. Hardened SHA validation, exact disposition counts and recursive free-text
   rejection for probability/fair-value/odds/trading-action leakage.

Reviewer verification before fixes: 36 passed. Coordinator verification after
fixes: focused + Wave-0 40 passed; full Alpha regression 655 passed.

Reviewer usage/token telemetry was not exposed to the reviewer and is recorded
as `unavailable`, not inferred.
