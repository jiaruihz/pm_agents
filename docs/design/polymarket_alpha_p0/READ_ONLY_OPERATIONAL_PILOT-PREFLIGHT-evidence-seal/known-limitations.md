# Known limitations

- All four market inputs are synthetic frozen fixtures, not live Gamma captures.
- No real source URL, DNS answer, HTTP response, latency, staleness, or schema
  compatibility observation exists yet.
- Existing-owner book demand hashes are budget reservations only; no demand or
  receipt was sent or received.
- Weather isolation and rollback types are implemented, but there are no real
  before/during/after observations.
- Artifact growth is budgeted but not measured against real payload bytes.
- OP-06 read-only replay and rollback rehearsal remain authorization-bound.
- Terra rollout exposed no token/model usage telemetry; status is
  `UNAVAILABLE_IN_WORKER_ROLLOUT`.
