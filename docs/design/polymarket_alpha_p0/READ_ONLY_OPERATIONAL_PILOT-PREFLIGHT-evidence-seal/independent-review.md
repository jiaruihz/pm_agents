# Independent read-only review

Reviewer: `luna_verifier` (`review_operational_preflight`)

Reviewer permissions: read-only; no file modifications; no child agents.

## Prompt scope

Review the owned pilot source, tests, frozen fixtures, Gate contract, existing
transport, and book adapter for correctness, boundary conditions,
idempotency/append-only behavior, budget enforcement, single market-book owner,
network/production/no-order boundaries, weather isolation/rollback semantics,
test coverage, performance, and readability. Run focused and full Alpha tests;
report exact findings and available model/usage telemetry.

## Findings

- `BLOCKING-1`: Alpha endpoint policy still authorized direct CLOB `/books`.
- `BLOCKING-2`: Gamma `limit/offset/closed` keys had no value constraints.
- `MEDIUM`: budget event kinds allowed unrelated fields.
- `MEDIUM`: fixture hash covered canonical JSON but not raw artifact bytes.
- `LOW`: a public builder could bypass the verified fixture-set entrypoint.

Initial reviewer verification: 21 focused tests and 392 Alpha tests passed, but
the two adversarial requests above were incorrectly allowed. Reviewer status:
`REWORK_REQUIRED`. Model/token telemetry was unavailable in the review rollout.

## Coordinator fixes

- Removed CLOB `/books` from the Alpha transport policy. CLOB remains reachable
  only behind the existing `weather_market_books` owner demand/receipt boundary.
- Added typed exact/integer-range query-value rules. First-pilot Gamma requests
  require `closed=false`, `limit=1..5`, and `offset=0`.
- Made budget event fields mutually exclusive by kind.
- Added raw-bytes SHA-256 beside semantic payload hashes and tested formatting-only changes.
- Made the direct manifest builder internal; the exported entrypoint now requires
  an accepted `FrozenFixtureSet`.
- Added direct adversarial tests for valid `/books`, huge/zero/non-numeric limit,
  nonzero offset, and wrong closed value.

## Final rerun

- focused: 64 passed;
- Alpha: 403 passed;
- legacy/harness regressions: 30 passed;
- source audit: 0 violations;
- direct attack rerun: CLOB `/books` denied; unbounded Gamma query denied.

Per the repository single-review rule, no reviewer cascade was started after the
coordinator fixed the findings; final verification was performed by the root
coordinator.
