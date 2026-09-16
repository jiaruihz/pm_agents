# Known limitations

- `DeterministicFakeExecutor` is fixture-only. No real GPT, browser, provider SDK
  or network transport exists in the Alpha process.
- The scheduler is a caller-driven synthetic tick function. It is not a daemon,
  cron, launchd task or daily scanner. Prior immutable receipts are supplied by
  the authorized caller when enforcing cross-tick daily budgets.
- Official resolution adapters consume already captured caller-supplied source
  bytes; they do not fetch authoritative sources.
- A crash after an immutable artifact write but before the DB transaction may
  leave an unreferenced immutable file. Exact replay consumes the same locator
  and bytes; conflicting replacement bytes fail closed.
- No current/production database migration, real Gamma scan, arbitrary token
  book expansion, weather production mutation or operational load test occurred.
- Read-only operational and external-network pilots still require separate
  authorization, credentials/egress design and operational evidence.
