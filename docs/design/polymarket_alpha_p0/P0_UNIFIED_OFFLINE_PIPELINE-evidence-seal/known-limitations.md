# Known limitations

- No real network request, production owner demand, runtime migration, deploy
  or restart was performed.
- The handoff is a manual filesystem protocol; it does not invoke GPT Pro or
  any other model and does not own queue/scheduler state.
- Filesystem and SQLite cannot share one atomic transaction. Immutable files may
  exist before a failed DB commit; exact retry is the recovery contract and no
  Candidate state advances without the DB transaction.
- The dirfd/no-follow implementation targets the current Unix/macOS runtime.
- Operational budgets, weather-owner capacity and rollback still require the
  separately authorized `READ_ONLY_OPERATIONAL_PILOT_GATE`.
- No execution, order, signing, authenticated CLOB or private-key capability is
  part of this pipeline.
