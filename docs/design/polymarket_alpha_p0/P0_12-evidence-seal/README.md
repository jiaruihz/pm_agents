# P0-12 Offline Integration Evidence Seal

Disposition: `READY_FOR_P0_IMPLEMENTATION`

Readiness scope: `OFFLINE_IMPLEMENTATION_ONLY`

This seal certifies the deterministic fixture implementation of P0-01 through
P0-12. The fixture reaches `SIMULATION_RECORDED`, writes an atomic
decision/prediction pair, and can only emit `execution=NO_ORDER`. A01-A18,
targeted legacy regressions, the existing harness regression, source audit,
schema integrity, replay, and rollback tests pass.

This seal does not approve a read-only operational pilot, production capture
expansion, migration of any current runtime database, external model
automation, live orders, signing, credentials, or private keys.

Primary evidence:

- `manifest.json`: machine-readable gate disposition and test totals.
- `acceptance-A01-A18.json`: executed test-module mapping for every scenario.
- `test-results/`: JUnit results, source audit, and import graph.
- `sample-artifacts/`: deterministic output, decision, prediction, WorkOrders,
  coordinator certification, evidence chain, and verified completion receipt.
- `migrations/`: five applied fixture migrations, integrity evidence, and
  rollback rehearsal mapping.
- `hashes.sha256`: every file in this seal except the hash list itself.

Regeneration order is recorded in `commands.log`. The generator is offline and
does not invoke a subprocess; pytest is run explicitly by the coordinator.
