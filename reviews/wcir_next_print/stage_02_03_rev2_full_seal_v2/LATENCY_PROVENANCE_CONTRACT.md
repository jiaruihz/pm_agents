# WCIR latency provenance contract

Each historical event has one row in
`evidence/LATENCY_POLICY_PROVENANCE_ROWS.jsonl.gz` with:

- `latency_policy_id`, `slo_seconds`;
- city, source, and collector epoch;
- estimation-window start/end and estimation cutoff;
- freeze clock and fallback policy;
- historical/forward disposition.

The frozen 49.773-second global pooled p95 is explicitly
`LEGACY_DIAGNOSTIC_ONLY` and `NOT_FORWARD_PRIMARY`.  Its rows preserve
`collector_epoch = null` because the historical epoch was not frozen; the seal
does not invent it.

Any future primary latency policy must be frozen before its evaluation window,
estimated from pre-cutoff observations, and keyed by city + source +
collector_epoch with an explicit fallback.  This is a future Stage 1/forward
input, not an authorization in this package.
