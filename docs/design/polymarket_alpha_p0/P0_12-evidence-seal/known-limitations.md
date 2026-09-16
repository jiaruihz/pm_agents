# Known limitations

- Certified scope is fixture-only offline implementation.
- No Gamma/CLOB network call or arbitrary live token capture was attempted.
- No GPT Pro or other external model automation was attempted; research results
  are immutable manual fixtures passed through the real importer.
- The existing weather book owner was exercised through frozen owner artifacts,
  not changed or deployed.
- The repository used a temporary Alpha database. No current runtime or
  `research.db` database was migrated.
- Worker token usage telemetry was not exposed by the delegated execution
  environment; this is recorded as unavailable, not estimated.
- The working tree contained user-owned, uncommitted harness development. The
  Alpha adapter only uses the unchanged public `DependencyResolver`,
  `EvidenceRecord`, `WorkOrder`, and `WorkStatus` APIs. Exact harness dependency
  file hashes used by this run are sealed in `source_identity.json`; those
  user-owned changes were not staged into the Alpha commits.
- Read-only operational capacity, rate limits, paired-book coverage, storage
  growth, weather isolation, and operational rollback remain for the separate
  `READ_ONLY_OPERATIONAL_PILOT_GATE`.
