# P1 Controlled Research Automation — Evidence Seal

```text
DISPOSITION=READY_FOR_CONTROLLED_EXTERNAL_RESEARCH_PILOT
READINESS_SCOPE=OFFLINE_AUTOMATION_BASELINE_ONLY
CODE_COMMIT=b11ba6953508ad4eeeb6b2a500b851b1c65a4924
EXTERNAL_RESEARCH_NETWORK_PILOT=NOT_APPROVED
READ_ONLY_OPERATIONAL_PILOT=NOT_APPROVED
LIVE_ORDER_SIGNING_PRIVATE_KEY=STRICTLY_OUT_OF_SCOPE
```

## Delivered

- P1-A03: provider-neutral immutable work orders, stable per-work-order return
  locators/manifests and deterministic fake executor.
- P1-A04: replayable `packet -> job/lease -> returned draft -> compiler ->
  importer -> COMPLETED/QUARANTINED` orchestration. Market work requires the
  exact accepted Blind result; provider locators are replaced by Alpha-owned
  sealed source locators.
- P1-A05: caller-driven synthetic scheduler with dedupe, priority, stale and
  clock-skew handling, per-tick/per-day budgets, bounded retry, explicit
  `JOB_TTL_EXPIRED -> FAILED`, disable behavior and immutable run receipts.
- P1-A06: two source-specific official-resolution fixture adapters that consume
  caller-supplied bytes and preserve ambiguity, dispute, correction and
  supersession semantics. Gamma lifecycle is never settlement truth.
- Repository result transitions may use a hash-bound importer receipt only when
  the same active attempt also has a RETURNED provider receipt and packet
  identity matches the job.

The existing manual handoff remains available. No provider SDK, browser,
generic HTTP, socket, subprocess, shell, signing, order or private-key path was
added.

## Verification

- Focused P1 automation suite: **40 passed**.
- Full `tests/polymarket_alpha`: **631 passed**.
- Source capability audit: four new runtime modules passed with **0 violations**.
- Independent read-only review: no CRITICAL; one MAJOR TTL terminal-state defect
  found and closed; no remaining findings.
- `git diff --check`: passed after the review fix.

Machine-readable JUnit evidence is under `test-results/`. The exact commands,
review report, limitations, rollback and file hashes are adjacent to this file.

## Ownership and state

- AlphaRepository remains the sole job/transition projection owner.
- Scheduler owns timing policy only and stores no competing job state.
- ArtifactStore remains the sole confined immutable filesystem owner.
- Rule A/B and Candidate lifecycle owners are unchanged.
- Current runtime databases and weather production configuration were not
  touched.
