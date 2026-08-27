# Independent read-only review

Reviewer contract: one fresh read-only `luna_verifier`; no edits, no subagents.
Owned review scope was P1-A03 through P1-A06 runtime code/tests plus the
repository importer-receipt transition validation. It was asked to inspect
correctness, edge cases, idempotency, append-only/crash replay, Blind/Market
isolation, budgets/retry/TTL, resolution-source truth and all network/order/
signing capabilities.

## Findings

- CRITICAL: none.
- MAJOR: `scheduler.py` left a non-final attempt in `RETRY_PENDING` when the job
  TTL had already expired; a retry was then impossible and no terminal
  transition existed.
- MINOR: none.

## Coordinator fix

- Added `ResearchTransitionReason.JOB_TTL_EXPIRED`.
- Allowed only `QUEUED|LEASED|RETRY_PENDING -> FAILED` for that reason.
- Repository requires the cause to be the exact job id/hash and the effective
  clock to be at or after `job.expires_at`.
- Scheduler now emits the terminal transition instead of an impossible retry.
- Updated the contract-schema golden and acceptance test.
- Added an extra coordinator hardening test that `processed_at` cannot precede
  provider `received_at`, and fixed same-packet tick dedupe so a future request
  cannot suppress an already-due request.

Post-fix focused result: 40 passed. Post-fix full Alpha result: 631 passed.

Reviewer model identity and token/usage telemetry were not exposed by the
collaboration runtime. The reviewer reported tool use and no file mutation.
Worker telemetry for the earlier bounded Terra A03 task was likewise unavailable;
the coordinator reran all owned tests and the full Alpha suite independently.
