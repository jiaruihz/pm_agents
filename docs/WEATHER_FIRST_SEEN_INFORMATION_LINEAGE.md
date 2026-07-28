# Weather First-Seen Information Event Lineage

Status: current-reference
Updated: 2026-07-28 initial frozen design
Source of truth: yes for first-seen event semantics and target data/signal lineage
Implementation status: data/signal implementation complete and locally replayed; production forward start blocked by JRS write permission
Used by: WEATHER_SYSTEM_CONTRACT.md; WEATHER_ARCHITECTURE_SPINE.md; WEATHER_STRATEGY_QUANT_DESIGN.md; WEATHER_TEMPERATURE_CONTEXT_FEATURE_LAYER.md

## 0. Decision

`first_seen_at` is the system's information clock. It records when our collector
could first make a source item available to downstream research. It is not a
weather feature by itself and is not interchangeable with observation time,
provider issue time or strategy decision time.

The additive lineage is:

```text
immutable raw capture
  -> weather_information_event
  -> weather_state_checkpoint + feature_frame_ref
  -> fact_signal_candidates
  -> settlement / market markout
```

This is the upstream extension of the existing permanent lineage:

```text
fact_signal_candidates -> plan -> order -> fill -> settlement
```

The first implementation stops at `fact_signal_candidates`. It does not create
plans, orders, exits or live behavior.

The target research question is:

```text
At the first time a genuinely new item was available to us, did the PIT weather
state improve P(exact outcome) relative to the same-row market probability?
```

It is not:

```text
rain/cloud/TAF present -> trade
```

## 1. Boundaries

### 1.1 Reuse before adding

The design reuses:

- `weather_data_feed_service/output/source_events/*.jsonl` for observation
  deliveries;
- immutable `forecast_hourly_curves` captures and their exact
  `forecast_first_seen_utc`;
- `forecast_enrichment` raw captures for TAF payloads;
- existing typed canonical payloads such as `weather_observation_events` and
  `fact_forecast_hourly_curves`;
- `weather_feature_layer` and its file-backed `feature_frame_ref`;
- existing orderbook snapshots;
- `fact_signal_candidates` as the opportunity fact;
- existing settlement facts and builders.

It does not create a strategy-specific weather cache, another feature store,
another opportunity fact or another PnL implementation.

### 1.2 Intentionally not designed now

The first version does not add:

- a streaming broker or event bus; append-only JSONL plus incremental cursors
  is sufficient at current scale;
- one canonical payload table per possible future source;
- radar/satellite/lightning/upstream-station adapters that do not yet exist;
- an LLM decision node;
- an execution, exit or position state machine;
- source absence, rain, cloud or transition counts as eligibility gates;
- historical first-seen values inferred from provider timestamps.

Future sources enter through the same event header. A typed child table is only
added after a real reusable query needs structured fields from that source.

## 2. Four clocks, one availability boundary

These times have different meanings and must remain separate:

| Field | Meaning | May trigger PIT visibility? |
|---|---|---|
| `source_event_ts_utc` | when the observation/weather state applies | no |
| `issued_at_utc` | when the provider says it issued the item | no |
| `detected_at_utc` | when our collector parsed this delivery | not by itself |
| `first_seen_at_utc` | earliest collector detection for this immutable event identity | yes, after publication |
| `available_at_utc` | when the immutable raw record became available to downstream consumers | yes |
| `ingested_at_utc` | when the canonical builder imported it | no; operational lineage only |
| `as_of_ts_utc` | cutoff used to build a state checkpoint | yes |
| `decision_ts_utc` | when a signal candidate was evaluated | yes |

For the first delivery of an event:

```text
detected_at_utc = first_seen_at_utc
detected_at_utc <= available_at_utc <= as_of_ts_utc <= decision_ts_utc
```

For a repeated delivery, the stored `first_seen_at_utc` remains the earlier
value. `issued_at_utc` and `source_event_ts_utc` may be much earlier and must
never be substituted for it.

The market baseline uses a book that was available no earlier than the trigger
event and no later than the candidate decision:

```text
trigger.available_at_utc
  <= book.snapshot_ts_utc
  <= book.available_at_utc
  <= decision_ts_utc
```

This prevents a request started before the weather event but published
afterward from masquerading as a fresh post-event quote. An earlier book can be
retained for market-reaction analysis, but it is not an executable post-event
cost.

## 3. Canonical records and grains

Only three additive canonical records are required. Existing typed weather and
market payloads remain where they are.

### 3.1 `weather_information_events`: immutable event header

Grain:

```text
one unique source-specific content version of a provider item
```

Repeated polls remain in the append-only raw delivery journal but collapse to
the same canonical event. They update operational delivery counts, not the
event identity or its earliest first-seen time.

Minimum fields:

```text
information_event_id
event_kind
event_role
source
city
station_id
provider_item_id
content_key
payload_hash
revision_of_event_id

source_event_ts_utc
issued_at_utc
valid_from_utc
valid_to_utc
detected_at_utc
first_seen_at_utc
available_at_utc
ingested_at_utc

pit_lineage_class
original_first_seen_unknown
raw_source_path
raw_row_hash
```

`event_kind` initially needs only:

```text
observation
taf
forecast_curve
```

The enum may later add `radar`, `satellite`, `lightning` or
`upstream_station`; this does not imply those collectors or payload schemas
exist now.

`event_role` distinguishes:

```text
new_content
revision
```

Every raw delivery remains available for source-latency and coverage analysis.
Only unique `new_content` and semantically changed `revision` rows enter the
canonical event table and trigger a state checkpoint by default. Polling the
same content again does not create another event, checkpoint or candidate.

#### Two identities are necessary, not more

- `information_event_id` is source-specific. It preserves the earliest time
  AviationWeather, IEM, FMI or another source delivered its content version.
- `content_key` groups deliveries that represent the same meteorological item,
  such as the same station report time. It enables source-race comparisons.

`payload_hash` represents normalized provider content. Collector timestamps,
file paths, fetch status text and serialization order are excluded from it.

For TAF AMD/COR or a changed forecast run, the new version gets a new
`information_event_id` and points to the previous version through
`revision_of_event_id`. An identical payload delivered again remains only a
duplicate raw delivery, not a new canonical event or revision.

### 3.2 Existing typed payloads

The event header does not duplicate weather values:

```text
weather_information_events
  <- weather_observation_events.information_event_id
  <- fact_forecast_hourly_curves.information_event_id
  <- raw TAF capture reference
```

Phase 1 keeps TAF structured fields in the immutable enrichment capture and
references that raw row from the event header. A dedicated TAF payload table is
only justified if multiple consumers later require canonical SQL access to the
parsed bulletin or all transition windows.

The canonical event identity is not allowed to depend on `target_date`.
Observation/forecast validity and city calendar determine which target-date
state checkpoints may consume the event. This avoids breaking identity at
local midnight or for a TAF spanning two dates.

### 3.3 `weather_state_checkpoints`: PIT state index

Grain:

```text
city + target_date + trigger_event_id + as_of_ts_utc
```

Minimum fields:

```text
state_checkpoint_id
city
target_date
trigger_event_id
as_of_ts_utc
input_event_set_hash
feature_store_frame_id
feature_row_id
feature_schema_version
feature_version_manifest
source_profile_id
pit_provenance
checkpoint_status
checkpoint_blocker
created_at_utc
```

This is an index to the existing feature store, not a second feature payload.
`feature_frame_ref` remains the authoritative pointer to the full state row.
The feature-row key must include `trigger_event_id`; city/date/time alone is
not sufficient when two sources update in the same second.

The shared feature builder:

1. selects only inputs with `available_at_utc <= as_of_ts_utc`;
2. retains every valid transition window needed by downstream features;
3. stores occurrence/report time separately from collector first-seen time;
4. emits explicit missing, expired, left-censored and unknown states;
5. never reads settlement or later observations into the PIT feature row.

`checkpoint_status` records at least:

```text
built
blocked_missing_required_identity
blocked_no_target_date_scope
build_error
```

Source outages and fetch failures remain coverage evidence. They do not become
synthetic weather transitions.

### 3.4 `fact_signal_candidates`: event-checkpoint opportunity

The existing v1 daily opportunity grain remains valid for its original T-24
research. It cannot represent multiple first-seen decisions during one
city-day. Event-driven candidates therefore use an additive, versioned grain:

```text
strategy_key + model_artifact_id + condition_id + expression_side
+ trigger_event_id + state_checkpoint_id
```

Required additive fields:

```text
candidate_grain_version       # v1_legacy_daily / v2_event_checkpoint
strategy_key
model_artifact_id
trigger_event_id
state_checkpoint_id
feature_store_frame_id
feature_row_id

decision_ts_utc
book_snapshot_id
book_snapshot_ts_utc
book_available_at_utc
market_evidence_status

model_probability_before
model_probability_after
market_probability
probability_residual

candidate_status
candidate_blocker
policy_selected
first_city_day_selected
```

Expression identity remains explicit:

```text
condition_id + bracket + side/expression
```

A state checkpoint is the full information-event denominator. A candidate row
exists for every strategy expression mapped from that checkpoint, including
rows blocked by a missing/stale book or model input. A checkpoint with no
market mapping remains visible in `weather_state_checkpoints`; it does not need
a fake `condition_id`.

`candidate_status` distinguishes:

```text
observed
scored
selected
blocked
```

`policy_selected` and `first_city_day_selected` are derived policy outputs.
They never determine whether the base candidate row exists.

Old v1 rows remain identifiable and are not silently reinterpreted. The v2
builder must not replace the v1 primary key algorithm for historical rows.

### 3.5 Deterministic IDs

IDs use SHA256 over canonical JSON with sorted keys, UTF-8 encoding and explicit
nulls. Display order, file path and ingest time never enter identity.

```text
information_event_id = hash(
  event_kind, normalized_source, station_id, provider_item_id,
  content_key, payload_hash
)

state_checkpoint_id = hash(
  city, target_date, trigger_event_id, as_of_ts_utc,
  feature_schema_version, feature_version_manifest, input_event_set_hash
)

candidate_id[v2] = hash(
  candidate_grain_version, strategy_key, model_artifact_id,
  condition_id, bracket, expression_side,
  trigger_event_id, state_checkpoint_id
)
```

`provider_item_id` may be null when a source exposes no stable bulletin/report
ID; `content_key + payload_hash` then carries identity. A parser or feature
version change may create a new checkpoint/candidate identity, but it does not
rewrite the upstream information event.

## 4. Source-specific rules

### 4.1 Observation/METAR

An observation event identity uses the normalized source, station, provider
report identity/time and normalized observation payload. Multi-record AWC
responses are split into individual events.

If an older report first appears only during reconciliation:

```text
pit_lineage_class = late_backfill_first_seen_unknown
original_first_seen_unknown = true
```

It may repair a weather path or become a later label, but it may not create a
historical first-seen signal candidate.

Cross-source copies of the same station report share a `content_key` but retain
separate source-specific event IDs and first-seen times.

### 4.2 TAF

TAF identity must use bulletin identity, issue time, validity and normalized raw
bulletin content. Repeated capture of the same bulletin preserves the earliest
collector first-seen. AMD/COR or changed raw content creates a revision.

The feature layer must stop using the current enrichment
`snapshot_ts_utc` as a replacement for exact TAF first-seen after the event
contract is available.

All parsed transition windows remain reachable from the immutable TAF payload.
Selecting the maximum-exposure transition is a feature/model operation, not
event ingestion.

### 4.3 Forecast curves

The existing forecast-curve implementation is the reference behavior:

- immutable capture;
- `forecast_run_ts_utc` when the source exposes it;
- normalized values hash;
- exact collector first-seen preserved across repeated captures;
- `detected_at <= available_at`;
- no future capture in a PIT state.

A new provider run with identical target-date values is still a provider
publication for lineage, but it is not a material weather-state update unless
run metadata or another consumed field changes. This prevents unchanged model
runs from generating duplicate signal candidates.

## 5. Market evidence without execution

Phase 1 reuses the existing orderbook archive. For each event checkpoint the
candidate builder attempts to attach:

1. the last book available before the event, for reaction diagnostics;
2. the first fresh book available after the event, for the market baseline;
3. later fixed-horizon books, when available, for markouts.

The first implementation does not create a separate markout fact. Fixed-horizon
markouts are derived from immutable book snapshots during the candidate build
and may be materialized as additive candidate columns when the metric contract
is frozen.

If the existing orderbook cadence does not provide sufficient post-event
coverage, the next step is to let the existing market-data producer refresh the
affected city ladder and stamp `trigger_event_id`. It is not a reason to create
a second orderbook collector inside a strategy runner.

Missing book coverage is:

```text
market_evidence_status = missing_post_event_book
candidate_status = blocked
```

It remains in the evidence funnel and is not a strategy rejection.

## 6. PIT lineage classes

Only three initial values are needed:

| `pit_lineage_class` | Meaning | First-seen latency research | Ordinary PIT feature replay |
|---|---|---:|---:|
| `collector_exact` | collector preserved exact first-seen and immutable availability | yes | yes |
| `archive_known_available` | a trustworthy publication boundary exists, exact collector arrival does not | no | yes |
| `late_backfill_first_seen_unknown` | later recovery cannot establish when it was first knowable | no | label/path repair only |

No source timestamp may upgrade a row from either historical class to
`collector_exact`.

## 7. Funnels and labels

The signal funnel is:

```text
raw deliveries
-> unique information events
-> material events
-> state checkpoints
-> mapped strategy expressions
-> scored candidates
-> positive residual
-> policy selection
```

The evidence funnel is:

```text
exact/known PIT lineage
-> feature frame available
-> fresh post-event book
-> settlement
-> fixed-horizon market markouts
```

Every stage reports its unit and independent target dates. Source, feature,
book and settlement gaps are coverage; they are not signal filters.

Later observations may produce:

- settlement labels;
- transition-realization labels;
- source-to-official/settlement basis labels;
- market markouts.

They never mutate a prior checkpoint or candidate. A correction creates a new
event, checkpoint or downstream build version.

## 8. Rebuild and ownership

Ownership remains:

| Layer | Owner |
|---|---|
| source parsing, identity, first-seen preservation | `weather_data_feed/` |
| polling and immutable raw publication | `weather_data_feed_service/` |
| canonical event/checkpoint/candidate ingest | dashboard ETL/rebuild |
| strategy-neutral state features | `weather_feature_layer/` |
| strategy probability/residual | strategy research/model head |

Raw files remain the durable evidence; `runtime/weather.db` and feature frames
are reproducible derived layers.

Incremental ingest and full rebuild must produce the same:

```text
information_event_id
state_checkpoint_id
candidate_id
feature_row_id
```

The canonical builder never edits an event to improve its historical timing.
Corrections and richer parsing create new revisions/build versions while old
records remain auditable.

## 9. Implementation sequence

### Phase A — contract and fixtures

1. Freeze event/checkpoint/candidate v2 field and ID contracts.
2. Add fixtures for duplicates, revisions, late backfills, restarts, cross-day
   validity, source races and same-second events.
3. Add incremental-versus-full-rebuild identity tests.

### Phase B — producer identity and first-seen

1. Extract the forecast-curve exact-first-seen logic into a shared
   `weather_data_feed` helper.
2. Apply it to observation source events and TAF captures.
3. Preserve polling/fetch failures as coverage records without turning them
   into material weather events.

### Phase C — canonical information events

1. Add `weather_information_events`.
2. Link existing observation and forecast typed payloads.
3. Add raw-source mapping to incremental ingest and full rebuild.
4. Verify stable IDs and no history loss.

### Phase D — event-triggered state checkpoints

1. Build one checkpoint for each material event in the target-date universe.
2. Add `trigger_event_id` to feature-row identity.
3. Extend realized transition clocks beyond precipitation/thunderstorm while
   retaining occurrence versus first-seen time.

### Phase E — candidate v2

1. Extend `fact_signal_candidates` additively with
   `candidate_grain_version`.
2. Attach checkpoint, feature, model and first post-event book lineage.
3. Preserve every observed/scored/selected/blocked expression.
4. Keep v1 daily candidates unchanged and separately queryable.

### Phase F — forward collection and review

1. Run collector and signal scoring with zero notional.
2. Publish signal and evidence funnels, latency distributions, same-row market
   proper scores and settlement coverage.
3. Do not discuss execution or live promotion until frozen forward evidence is
   sufficient under `WEATHER_ANALYSIS_CONTRACT.md`.

## 10. Acceptance criteria

The data/signal implementation is complete only when:

1. one immutable provider item has stable identity across polls and restarts;
2. a real revision creates a new linked event;
3. cross-source deliveries are comparable without being collapsed;
4. late backfill cannot create a historical first-seen candidate;
5. two same-second events create distinct checkpoint/feature identities;
6. no input with `available_at_utc > as_of_ts_utc` enters a checkpoint;
7. repeated non-material polling creates no new checkpoint or candidate;
8. every candidate resolves to trigger event, checkpoint, feature frame, model
   artifact, market evidence and—when available—settlement;
9. missing feature/book/settlement coverage remains in the appropriate funnel;
10. incremental ingest and full rebuild produce identical IDs and counts;
11. existing strategy behavior and existing v1 candidate rows do not change;
12. no execution-layer file, plan, order or live process is touched.

## 11. Required regression cases

- the same METAR arrives through three sources at different times;
- collector restart and replay of the same raw line;
- AWC returns current plus older reports in one response;
- TAF AMD/COR with nearby issue times;
- a new forecast run whose consumed values are unchanged;
- payload formatting/precision changes without semantic change;
- provider issue/report time is early but collector arrival is late;
- observation or TAF validity crosses local midnight;
- two material events arrive in the same second;
- no fresh book exists after an exact first-seen event;
- a late backfill is present during full rebuild;
- a later observation would change the label but not the earlier feature row;
- Atlanta 2026-07-17 terminal false cross remains a negative control for
  source-to-settlement basis.
