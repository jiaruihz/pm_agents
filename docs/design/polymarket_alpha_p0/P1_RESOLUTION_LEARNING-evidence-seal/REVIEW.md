# Independent Review and Fix Closure

## Review prompt

Perform a read-only review of the new P1 contracts, pure learning engine,
additive migration/repository projections, and tests. Check append-only
semantics; model-copy and parent-hash bypasses; market/rule/book/source lineage;
resolution/dispute eligibility; simulated PnL and Decimal scoring math;
calibration boundaries/exclusions/replay; migration idempotence/repair; P0 schema
seal preservation; no-network/no-order safety; and test gaps. Do not edit files.

## Finding

`HIGH`: `CalibrationReport` projection verified referenced score hashes but did
not recompute policy hash, bucket membership, exclusions, counts, or aggregate
statistics. A canonical yet fabricated report could therefore be stored.

## Fix

- Reconstruct every referenced `PredictionScore` from canonical repository
  bytes and verified hashes.
- Rebuild the report with the explicit version/boundaries and original clock/run.
- Require full deterministic contract equality before projection.
- Add adversarial tests for forged mean, count, policy hash, and exclusion.
- Apply the same full parent replay check to `PredictionResolutionLink` and
  `PredictionScore`; preserve existing FK/hash checks as defense in depth.
- Add decision↔position and condition-identity enforcement.

## Rerun

```text
focused_and_compatibility_tests=67 passed
full_alpha_tests=545 passed
learning_security_audit=PASS (0 violations)
```

Reviewer model/usage telemetry: `unavailable`.

