# Independent review

Reviewer: fresh read-only `luna_verifier` worker, one turn, no edits, no network.

## Findings

1. Medium: `execute_gamma_events()` accepted caller-supplied expanded policy and
   budget without binding their hashes to the sealed authorization; cumulative
   request/artifact budgets were not enforced by the executor.
2. Low-medium: CLI exposed only the old offset-0/default-policy path, so an
   offset-20 page could only be run through the Python API.
3. Low observation: Gamma response `[{}]` could receive a success receipt even
   though the event had no identity.

Reviewer independently verified 126 focused tests, 50 catalog snapshots, both
Gamma page receipts and the `FINALIZED` chain state. Reviewer-reported model and
usage telemetry were unavailable in its runtime.

## Coordinator fixes

- `OperationalPilotAuthorization` now requires exact `endpoint_policy_sha256`
  and `budget_sha256`; authorization identity includes both values.
- Executor rejects missing/mismatched policy or budget before network I/O.
- A private, flock-protected append-only `gamma_budget.jsonl` ledger enforces
  cross-process request count, cumulative success artifact bytes and runtime.
- CLI now requires sealed authorization/policy/budget files and exposes offset,
  minimum limit and response cap.
- Gamma event entries now require a non-blank event id.
- Added adversarial tests for policy/budget mismatch, second-request budget
  denial, CLI offset replay and missing event identity behavior.

## Post-fix verification

- Focused suite: `155 passed`.
- Full `tests/polymarket_alpha`: `639 passed`.
- `git diff --check`: PASS.
- `py_compile` on changed executor/contracts/research modules: PASS.
