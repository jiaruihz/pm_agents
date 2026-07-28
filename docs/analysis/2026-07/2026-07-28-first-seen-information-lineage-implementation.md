# First-seen information lineage — implementation log

Status: implementation complete; forward evidence collection pending by design

Started: 2026-07-28

Scope: immutable raw capture → information event → PIT checkpoint + feature-frame
reference → `fact_signal_candidates` v2 → zero-notional forward telemetry only.

## Phase A — contract, schema audit, and fixtures

### Frozen target

The authoritative design is
[`WEATHER_FIRST_SEEN_INFORMATION_LINEAGE.md`](../../WEATHER_FIRST_SEEN_INFORMATION_LINEAGE.md).
`first_seen_at_utc` is the collector information clock.  It is distinct from
weather observation time, provider issue time, detection time, canonical ingest
time and strategy decision time.  In particular, late reconciliation/backfill
may repair a path or label but cannot generate an historical exact-first-seen
candidate.

### Schema / implementation audit

- `weather_data_feed_service/source_events.py` publishes append-only observation
  deliveries, including `changed_since_last`; its AWC recovery path already
  identifies recovered rows as `first_seen_type=late_backfill` and
  `original_first_seen_unknown=true`.
- `weather_data_feed/forecast_hourly_curves.py` already writes immutable
  captures with exact `forecast_first_seen_utc` when collector evidence exists.
- `runtime/weather.db` currently has the compatibility lineage tables
  `tmax_v2_forecast_captures` and `tmax_v2_observation_event_lineage`, but has
  neither `weather_information_events` nor `weather_state_checkpoints`.
- The materialized `fact_signal_candidates` table is v1 daily only: its grain
  remains `(condition_id, side, event_date)` and it has no
  `candidate_grain_version`, trigger-event or checkpoint columns.
- `weather_feature_layer.store.DEFAULT_KEY_COLUMNS` is currently
  `(city, target_date, decision_snapshot_ts_utc)`; event-driven checkpoint
  storage must add `trigger_event_id` rather than overwriting that v1 key.

### Completed first change

Added `weather_data_feed.information_events`, a side-effect-free shared
contract used later by both the producer and canonical rebuild:

- full SHA-256 canonical JSON hashing with explicit nulls;
- source-specific `information_event_id` based only on frozen identity fields;
- explicit `collector_exact`, `archive_known_available`, and
  `late_backfill_first_seen_unknown` validation;
- no provider/report/issue timestamp can populate a late-backfill first-seen
  clock.

Tests cover duplicate/restart identity, revisions, cross-source same-content
delivery, late-backfill rejection and distinct same-second events.

Verification on 2026-07-28:

```text
.venv/bin/python -m pytest \
  tests/weather_data_feed/test_information_events.py \
  tests/pmm_tests/test_forecast_hourly_curves.py \
  tests/pmm_tests/test_weather_data_feed_service.py -q
33 passed in 0.36s
```

### Next implementation order

1. Apply the shared identity contract to source-event and TAF raw publication;
   preserve duplicate delivery evidence and revisions without emitting duplicate
   material events.
2. Add canonical information-event/checkpoint tables and build parity tests for
   incremental and full rebuilds.
3. Add trigger-aware feature-frame identity and PIT checkpoint build rules.
4. Materialize additive `fact_signal_candidates` v2 while preserving every
   `v1_legacy_daily` row and primary key.
5. Record the full zero-notional event/checkpoint/candidate denominator and
   evidence funnel; do not introduce plans, orders, fills or live promotion.

## Phase B/C progress — producer and canonical boundary

Completed after Phase A:

- `source_events` stamps `available_at_utc` at raw publication, persists the
  earliest source-specific first-seen across restarts, emits linked revisions,
  and leaves fetch failures non-material coverage rows.
- late AWC reconciliation retains the frozen `late_backfill_first_seen_unknown`
  class and null first-seen; it cannot become an exact event later.
- canonical schema v18 adds `weather_information_events` and
  `weather_state_checkpoints`; event ingest is append-only, so duplicate raw
  deliveries cannot rewrite historical timing.
- event-checkpoint feature refs use the additive key
  `(city, target_date, trigger_event_id, as_of_ts_utc)`. The checkpoint helper
  rejects any input whose `available_at_utc` is after its as-of cutoff.

Verification after this increment:

```text
.venv/bin/python -m pytest \
  tests/weather_dashboard/test_state_checkpoints.py \
  tests/pmm_tests/test_weather_feature_layer_contract.py \
  tests/weather_dashboard/test_db_schema_canonical.py \
  tests/pmm_tests/test_weather_data_feed_service.py -q
66 passed in 1.17s
```

## Research / action status

significance=NA

baseline=NA

forward=NA
conclusion=inconclusive

This is infrastructure work. It produces no alpha, no trade recommendation and
no live behavior change.

## Final implementation state

The frozen data/signal scope is now connected end to end:

```text
observation / TAF / forecast immutable raw capture
  -> weather_information_events
  -> weather_state_checkpoints + trigger-aware feature_frame_ref
  -> fact_signal_candidates(candidate_grain_version=v2_event_checkpoint)
  -> weather_first_seen_zero_notional_v1 telemetry
```

- Full dashboard rebuild invokes the information-event materializer from the
  current Mac/JRS data-feed runtime. Missing raw paths produce zero rows rather
  than inferred historical first-seen values.
- Candidate v2 IDs use the frozen event/checkpoint expression grain. Observed,
  scored, selected and blocked rows share one table; missing book/model evidence
  stays a blocker/coverage row.
- Legacy full rebuild preserves v2 rows and all v1 rows remain
  `v1_legacy_daily`; no v1 key or selector behavior was reinterpreted.
- Forward export is hard-coded zero-notional (`notional_usd=0`,
  `no_order_placed=true`) and has no plan/order/fill imports or side effects.

Final regression command covered identity, duplicate/restart/revision,
late-backfill rejection, schema constraints, incremental idempotency, feature
checkpoint PIT exclusion, v1 candidate compatibility, candidate v2 and
zero-notional telemetry:

```text
92 passed in 1.78s
```

No current runtime DB rebuild, process restart, deployment or live action was
performed as part of this implementation.
