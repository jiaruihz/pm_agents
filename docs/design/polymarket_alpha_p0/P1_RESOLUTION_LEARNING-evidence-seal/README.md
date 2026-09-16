# P1 Resolution and Learning Closure — Evidence Seal

Disposition: `COMPLETE_OFFLINE_FOUNDATION`

This seal covers the first post-P0 learning block. It adds immutable resolution
assertions, append-only prediction-to-resolution links, frozen simulated entry
bases, versioned PredictionScore facts, and calibration reports. Existing P0
`PredictionRecord` contracts and rows are never updated.

## Delivered boundary

- `MarketResolution` binds a replayable `SourceArtifact`, exact RuleContract
  revision/hash, canonical market identity, outcome, dispute state, and clocks.
- `PredictionResolutionLink` binds the original prediction, decision, FINAL
  estimate, RuleContract, resolution, and frozen book. `SIMULATED` records use
  the exact decision target-depth VWAP; `NO_POSITION` records have no entry.
- `PredictionScore` records Decimal Brier/Log Loss, market baseline metrics, and
  simulated PnL under an explicit versioned epsilon/fee convention.
- `CalibrationReport` supports market type, Rule clarity, and entry-price
  slices, with all selected scores hash-bound and typed exclusions retained.
- Additive migration `alpha_p1_0001_resolution_learning` owns typed projections,
  FK/uniqueness constraints, replay repair, and atomic rollback. Migrations
  `0001`–`0005` and the P0 contract fingerprint remain unchanged.
- Repository projectors reconstruct links, scores, and calibration reports from
  their sealed parents; forged derived metrics or slice statistics fail closed.

## Verification

```text
focused_and_compatibility_tests=67 passed
full_alpha_tests=545 passed
learning_security_audit=PASS (0 violations)
git_diff_check=PASS
p0_contract_schema_sha256=25dc4cb3a82430b95cd557e5f632e3c2c48c7aaaa7d663ed8d94885b84edeed3
p1_contract_schema_sha256=ffeac91880e9efef9da0b834656a0de3b60738ec0d66c80533ae1a524e4b2f88
p1_migration_sql_sha256=5de4530fb0bd5c962bf235e64852c4d1731ba43a55c7c03938d5313580a7d4d9
```

The full suite was run with inherited proxy variables removed because GLM's
still-uncommitted operational CLI intentionally rejects ambient proxy state.
No network request was made by this P1 block.

## Review closure

The independent read-only review found one High issue: the repository accepted
structurally valid but forged calibration aggregates. The projector now rebuilds
the expected report from referenced PredictionScore contracts and compares the
entire deterministic contract. Adversarial tests cover forged mean, count,
policy hash, and exclusion. The same replay boundary was also applied to links
and scores.

## Explicit limitations

- No live resolution backfill source or scheduler is implemented here. A caller
  must provide an already captured, replayable resolution artifact.
- Gamma lifecycle state alone is not treated as settlement truth.
- No current or production database was migrated.
- No order, signing, key, wallet, production config, market-book owner, network,
  or operational-pilot capability was added.
- This does not approve `READ_ONLY_OPERATIONAL_PILOT` or production activation.

