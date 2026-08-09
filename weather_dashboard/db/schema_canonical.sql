-- weather_dashboard canonical DB schema
-- Canonical weather lineage schema. Legacy field aliases are intentionally absent.

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    description TEXT
);

CREATE TABLE IF NOT EXISTS code_versions (
    code_version TEXT PRIMARY KEY,
    branch TEXT,
    commit_subject TEXT,
    commit_at_utc TEXT,
    deployed_at_utc TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS strategy_config (
    config_id TEXT PRIMARY KEY,
    strategy_key TEXT REFERENCES strategy_def(strategy_key),
    name TEXT NOT NULL,
    params TEXT NOT NULL,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS universes (
    universe_id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    cities TEXT NOT NULL,
    models TEXT NOT NULL,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    frozen_at_utc TEXT,
    deprecated_at_utc TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    producer_system TEXT NOT NULL CHECK (producer_system IN ('n100','pm_agent_local','legacy_migration')),
    producer_run_id TEXT,
    config_id TEXT NOT NULL REFERENCES strategy_config(config_id),
    universe_id TEXT REFERENCES universes(universe_id),
    code_version TEXT REFERENCES code_versions(code_version),
    execution_mode TEXT NOT NULL CHECK (execution_mode IN ('snapshot_replay','paper','live')),
    date_range_start TEXT,
    date_range_end TEXT,
    started_at_utc TEXT,
    ended_at_utc TEXT,
    state TEXT NOT NULL CHECK (state IN ('explore','paper','live','retired')),
    source_root TEXT,
    repro_key TEXT,
    parent_run_id TEXT REFERENCES runs(run_id),
    tags TEXT,
    metrics TEXT,
    metrics_at_utc TEXT,
    notes TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    producer_system TEXT NOT NULL CHECK (producer_system IN ('n100','pm_agent_local','legacy_migration')),
    producer_run_id TEXT NOT NULL,
    snapshot_ts_utc TEXT NOT NULL,
    snapshot_file TEXT,
    target_date TEXT NOT NULL,
    city TEXT NOT NULL,
    city_pool TEXT NOT NULL CHECK (city_pool IN ('t1_trading','t2_research')),
    icao TEXT NOT NULL,
    bracket TEXT NOT NULL,
    unit TEXT NOT NULL,
    signal_side TEXT NOT NULL CHECK (signal_side IN ('YES','NO')),
    model_version TEXT NOT NULL,
    model_p_yes REAL NOT NULL,
    forecast_source TEXT NOT NULL,
    forecast_max_f REAL,
    forecast_max_native REAL,
    forecast_peak_hour_local INTEGER,
    forecast_peak_time_local TEXT,
    forecast_peak_hour_utc INTEGER,
    forecast_peak_time_utc TEXT,
    forecast_hourly_count INTEGER,
    forecast_values_hash TEXT,
    forecast_peak_source TEXT,
    forecast_timezone TEXT,
    forecast_utc_offset_seconds INTEGER,
    forecast_peak_delta_hours_local REAL,
    forecast_max_in_bracket INTEGER,
    forecast_max_above_bracket_f REAL,
    forecast_max_below_bracket_f REAL,
    forecast_max_above_metar_max_f REAL,
    market_price REAL NOT NULL,
    edge REAL NOT NULL,
    abs_edge REAL NOT NULL,
    condition_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    token_id TEXT,
    hours_to_settle REAL NOT NULL,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS plans (
    plan_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    signal_id TEXT NOT NULL REFERENCES signals(signal_id),
    config_id TEXT NOT NULL REFERENCES strategy_config(config_id),
    order_side TEXT NOT NULL CHECK (order_side IN ('BUY_YES','BUY_NO','SELL_YES','SELL_NO')),
    notional REAL,
    desired_shares REAL,
    sizing_mode TEXT,
    entry_price_window TEXT,
    execution_profile TEXT,
    execution_policy TEXT NOT NULL,
    order_lifecycle_policy TEXT,
    child_order_role TEXT,
    comparison_group_id TEXT,
    maker_only INTEGER,
    limit_price REAL,
    skip_reason TEXT,
    status TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS orders (
    execution_id TEXT PRIMARY KEY,
    order_id TEXT,
    instance_id TEXT REFERENCES strategy_instance(instance_id),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    plan_id TEXT NOT NULL REFERENCES plans(plan_id),
    venue TEXT NOT NULL CHECK (venue IN ('paper','snapshot_replay','polymarket_clob')),
    order_side TEXT NOT NULL CHECK (order_side IN ('BUY_YES','BUY_NO','SELL_YES','SELL_NO')),
    limit_price REAL,
    entry_price REAL NOT NULL,
    shares REAL NOT NULL,
    cost_usd REAL NOT NULL,
    notional REAL,
    status TEXT NOT NULL,
    clob_status TEXT,
    quote_status TEXT,
    quote_mode TEXT,
    quote_reason TEXT,
    quote_tick_size REAL,
    risk_status TEXT,
    risk_reason TEXT,
    error_classification TEXT,
    error_reason TEXT,
    execution_action TEXT,
    child_order_role TEXT,
    source_order_id TEXT,
    cancel_before_order_id TEXT,
    maker_only INTEGER,
    sizing_policy TEXT,
    score_dist_sizing_model TEXT,
    score_dist_probability REAL,
    score_dist_tier TEXT,
    score_dist_multiplier REAL,
    requested_price REAL,
    posted_price REAL,
    posted_notional REAL,
    best_bid REAL,
    best_ask REAL,
    spread REAL,
    model_p_yes_used REAL,
    market_implied_p_yes REAL,
    quote_edge REAL,
    fee_adjusted_edge REAL,
    source_order_age_min REAL,
    exchange_response TEXT,
    order_payload TEXT,
    placed_at_utc TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES orders(execution_id),
    order_id TEXT,
    filled_shares REAL NOT NULL,
    filled_price REAL NOT NULL,
    fees_usd REAL NOT NULL DEFAULT 0,
    fee_source TEXT NOT NULL DEFAULT 'legacy_unknown',
    fee_rate REAL,
    fee_metadata_json TEXT,
    transaction_hash TEXT,
    status TEXT NOT NULL CHECK (status IN ('filled','partial','cancelled','expired','simulated')),
    filled_at_utc TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- A physical CLOB order id is globally unique, while legacy migrations could
-- derive a new execution_id when plan/config enrichment changed. Preserve the
-- raw duplicate rows and mark the later execution as an append-only alias.
CREATE TABLE IF NOT EXISTS order_execution_aliases (
    alias_execution_id TEXT PRIMARY KEY REFERENCES orders(execution_id),
    canonical_execution_id TEXT NOT NULL REFERENCES orders(execution_id),
    physical_order_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    source_path TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK (alias_execution_id <> canonical_execution_id)
);

CREATE INDEX IF NOT EXISTS idx_order_execution_aliases_physical
    ON order_execution_aliases(physical_order_id);

CREATE TABLE IF NOT EXISTS fill_validity_adjustments (
    adjustment_id TEXT PRIMARY KEY,
    fill_id TEXT NOT NULL UNIQUE REFERENCES fills(fill_id),
    effective_status TEXT NOT NULL CHECK (effective_status IN ('valid','excluded')),
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    source_path TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Existing fills are immutable. Fee corrections are append-only evidence rows
-- whose signed deltas are folded into fact_trades at build time.
CREATE TABLE IF NOT EXISTS fill_fee_adjustments (
    adjustment_id TEXT PRIMARY KEY,
    fill_id TEXT NOT NULL REFERENCES fills(fill_id),
    fee_delta_usd REAL NOT NULL,
    fee_source TEXT NOT NULL,
    fee_evidence_class TEXT NOT NULL CHECK (fee_evidence_class IN ('exact','estimate')),
    transaction_hash TEXT,
    fee_rate REAL,
    market_fee_metadata_json TEXT,
    evidence_json TEXT NOT NULL,
    source_path TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Existing fills are immutable. Exact price corrections preserve the raw row
-- and replace its price only in canonical derived facts.
CREATE TABLE IF NOT EXISTS fill_price_adjustments (
    adjustment_id TEXT PRIMARY KEY,
    fill_id TEXT NOT NULL UNIQUE REFERENCES fills(fill_id),
    corrected_filled_price REAL NOT NULL CHECK (corrected_filled_price > 0 AND corrected_filled_price <= 1),
    price_source TEXT NOT NULL,
    price_evidence_class TEXT NOT NULL CHECK (price_evidence_class IN ('exact','estimate')),
    evidence_json TEXT NOT NULL,
    source_path TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Existing fills are immutable. Timestamp corrections preserve the raw ingest
-- timestamp and replace it only in canonical derived facts.
CREATE TABLE IF NOT EXISTS fill_timestamp_adjustments (
    adjustment_id TEXT PRIMARY KEY,
    fill_id TEXT NOT NULL UNIQUE REFERENCES fills(fill_id),
    corrected_filled_at_utc TEXT NOT NULL,
    timestamp_source TEXT NOT NULL,
    timestamp_evidence_class TEXT NOT NULL CHECK (timestamp_evidence_class IN ('exact','estimate')),
    evidence_json TEXT NOT NULL,
    source_path TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS settlements (
    settlement_id TEXT PRIMARY KEY,
    target_date TEXT NOT NULL,
    condition_id TEXT,
    market_id TEXT,
    bracket TEXT NOT NULL,
    token_id TEXT,
    final_price REAL NOT NULL,
    settlement_status TEXT NOT NULL CHECK (settlement_status IN ('settled','missing_event','missing_bracket')),
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS settlement_outcomes (
    settlement_outcome_id TEXT PRIMARY KEY,
    source_system TEXT NOT NULL CHECK (source_system IN ('pm_history','polymarket_api','manual_backfill')),
    source_path TEXT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    bracket TEXT NOT NULL,
    unit TEXT,
    condition_id TEXT,
    market_id TEXT,
    token_id TEXT,
    raw_final_price REAL,
    final_price REAL NOT NULL,
    settlement_status TEXT NOT NULL CHECK (settlement_status IN ('settled','missing_event','missing_bracket')),
    question TEXT,
    payload TEXT,
    source_payload_hash TEXT,
    source_file_mtime_utc TEXT,
    first_seen_at_utc TEXT,
    available_at_utc TEXT,
    pit_lineage_class TEXT,
    producer_build_id TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS weather_observation_events (
    observation_id TEXT PRIMARY KEY,
    source_system TEXT NOT NULL,
    source_path TEXT,
    source_row_hash TEXT NOT NULL,
    city TEXT NOT NULL,
    icao TEXT NOT NULL,
    timezone TEXT,
    target_date TEXT NOT NULL,
    obs_ts_utc TEXT NOT NULL,
    fetched_at_utc TEXT,
    source_report_ts_utc TEXT,
    unit TEXT,
    temp_f REAL,
    temp_c REAL,
    dewpoint_f REAL,
    wind_speed_kt REAL,
    wind_dir_deg REAL,
    sky_cover TEXT,
    raw_payload TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(source_system, source_row_hash)
);

-- Tmax V2 keeps its point-in-time inputs separate from the legacy feature
-- tables.  A V2 state may reference one (and only one) captured ladder; it
-- must never be reconstructed by joining rungs from different captures.
CREATE TABLE IF NOT EXISTS tmax_v2_ladder_snapshots (
    ladder_snapshot_id TEXT PRIMARY KEY,
    source_system TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_snapshot_ts_utc TEXT NOT NULL,
    available_at_utc TEXT NOT NULL,
    source_payload_hash TEXT NOT NULL,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    event_slug TEXT,
    event_identity TEXT NOT NULL,
    market_unit TEXT,
    settlement_source_class TEXT,
    market_timezone TEXT,
    market_utc_offset_seconds INTEGER,
    market_metadata_source_json TEXT,
    market_metadata_missing_reason TEXT,
    absolute_ladder_signature TEXT NOT NULL,
    rung_count INTEGER NOT NULL,
    complete_rung_count INTEGER NOT NULL,
    completeness_status TEXT NOT NULL CHECK (
        completeness_status IN ('complete','incomplete','invalid')
    ),
    lineage_status TEXT NOT NULL CHECK (
        lineage_status IN ('pit_verified_capture','research_only_unknown_available_at')
    ),
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(source_payload_hash, city, target_date, event_identity)
);

CREATE TABLE IF NOT EXISTS tmax_v2_ladder_rung_quotes (
    rung_quote_id TEXT PRIMARY KEY,
    ladder_snapshot_id TEXT NOT NULL REFERENCES tmax_v2_ladder_snapshots(ladder_snapshot_id),
    absolute_bracket_identity TEXT NOT NULL,
    condition_id TEXT,
    market_id TEXT,
    question TEXT,
    yes_token_id TEXT,
    no_token_id TEXT,
    yes_direct_bid REAL,
    yes_direct_ask REAL,
    yes_direct_bid_size REAL,
    yes_direct_ask_size REAL,
    yes_direct_depth_bid_5c REAL,
    yes_direct_depth_ask_5c REAL,
    yes_direct_depth_bid_10c REAL,
    yes_direct_depth_ask_10c REAL,
    yes_book_status TEXT,
    yes_book_fetched_at_utc TEXT,
    no_direct_bid REAL,
    no_direct_ask REAL,
    no_direct_bid_size REAL,
    no_direct_ask_size REAL,
    no_direct_depth_bid_5c REAL,
    no_direct_depth_ask_5c REAL,
    no_direct_depth_bid_10c REAL,
    no_direct_depth_ask_10c REAL,
    no_book_status TEXT,
    no_book_fetched_at_utc TEXT,
    source_record_hash TEXT NOT NULL,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(ladder_snapshot_id, absolute_bracket_identity)
);

CREATE TABLE IF NOT EXISTS tmax_v2_forecast_captures (
    forecast_capture_id TEXT PRIMARY KEY,
    source_system TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_row_hash TEXT NOT NULL,
    snapshot_ts_utc TEXT,
    available_at_utc TEXT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    forecast_source TEXT,
    forecast_model TEXT,
    forecast_values_hash TEXT,
    hourly_curve_json TEXT NOT NULL,
    normalized_hourly_curve_json TEXT,
    forecast_run_at_utc TEXT,
    forecast_timezone TEXT,
    forecast_utc_offset_seconds INTEGER,
    available_at_basis TEXT,
    forecast_first_seen_at_utc TEXT,
    forecast_first_seen_basis TEXT,
    forecast_first_seen_source TEXT,
    curve_time_lineage_status TEXT,
    curve_time_missing_reason TEXT,
    lineage_status TEXT NOT NULL CHECK (
        lineage_status IN ('pit_verified_capture','research_only_unknown_available_at')
    ),
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(source_path, source_row_hash)
);

-- This is a compatibility lineage layer over weather_observation_events.  It
-- makes the absence of first-seen data explicit instead of treating a later
-- archive import time as evidence that an observation was usable then.
CREATE TABLE IF NOT EXISTS tmax_v2_observation_event_lineage (
    tmax_v2_observation_id TEXT PRIMARY KEY,
    source_observation_id TEXT NOT NULL,
    source_system TEXT NOT NULL,
    source_path TEXT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    obs_ts_utc TEXT NOT NULL,
    temp_f REAL,
    first_seen_at_utc TEXT,
    available_at_utc TEXT,
    source_kind TEXT NOT NULL,
    station_id TEXT,
    icao TEXT,
    feed_identity TEXT,
    identity_missing_reason TEXT,
    lineage_status TEXT NOT NULL CHECK (
        lineage_status IN ('pit_verified_first_seen','research_only_unknown_first_seen')
    ),
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    -- A later native first-seen record is new evidence, not an in-place
    -- upgrade of an archive-only record.
    UNIQUE(source_observation_id, lineage_status, first_seen_at_utc)
);

-- Immutable upstream information-event headers. This is intentionally
-- independent from weather_observation_events: a source-specific delivery
-- identity can be shared by several typed payload representations, and late
-- recovery must remain auditable without rewriting its historical clock.
CREATE TABLE IF NOT EXISTS weather_information_events (
    information_event_id TEXT PRIMARY KEY,
    event_kind TEXT NOT NULL CHECK (event_kind IN ('observation', 'taf', 'forecast_curve')),
    event_role TEXT NOT NULL CHECK (event_role IN ('new_content', 'revision')),
    source TEXT NOT NULL,
    city TEXT NOT NULL,
    station_id TEXT,
    provider_item_id TEXT,
    content_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    revision_of_event_id TEXT REFERENCES weather_information_events(information_event_id),
    source_event_ts_utc TEXT,
    issued_at_utc TEXT,
    valid_from_utc TEXT,
    valid_to_utc TEXT,
    detected_at_utc TEXT,
    first_seen_at_utc TEXT,
    available_at_utc TEXT,
    ingested_at_utc TEXT NOT NULL,
    pit_lineage_class TEXT NOT NULL CHECK (
        pit_lineage_class IN (
            'collector_exact',
            'archive_known_available',
            'late_backfill_first_seen_unknown'
        )
    ),
    original_first_seen_unknown INTEGER NOT NULL CHECK (original_first_seen_unknown IN (0, 1)),
    raw_source_path TEXT,
    raw_row_hash TEXT,
    CHECK (
        (pit_lineage_class <> 'late_backfill_first_seen_unknown')
        OR (first_seen_at_utc IS NULL AND original_first_seen_unknown = 1)
    ),
    UNIQUE(source, station_id, provider_item_id, content_key, payload_hash)
);

-- A checkpoint is only an index to a file-backed feature frame. The full
-- payload stays in weather_feature_layer; trigger_event_id prevents two
-- same-second source updates from collapsing into one city/date/time row.
CREATE TABLE IF NOT EXISTS weather_state_checkpoints (
    state_checkpoint_id TEXT PRIMARY KEY,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    trigger_event_id TEXT NOT NULL REFERENCES weather_information_events(information_event_id),
    as_of_ts_utc TEXT NOT NULL,
    input_event_set_hash TEXT NOT NULL,
    feature_store_frame_id TEXT,
    feature_row_id TEXT,
    feature_schema_version TEXT NOT NULL,
    feature_version_manifest TEXT NOT NULL,
    source_profile_id TEXT,
    pit_provenance TEXT NOT NULL,
    checkpoint_status TEXT NOT NULL CHECK (
        checkpoint_status IN (
            'built',
            'blocked_missing_required_identity',
            'blocked_no_target_date_scope',
            'build_error'
        )
    ),
    checkpoint_blocker TEXT,
    created_at_utc TEXT NOT NULL,
    UNIQUE(
        city, target_date, trigger_event_id, as_of_ts_utc,
        feature_schema_version, feature_version_manifest, input_event_set_hash
    )
);

-- Existing v15 facts are immutable. These additive metadata rows backfill
-- their newly introduced lineage columns without updating the original row.
CREATE TABLE IF NOT EXISTS tmax_v2_ladder_snapshot_metadata (
    metadata_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ladder_metadata_id TEXT NOT NULL UNIQUE,
    ladder_snapshot_id TEXT NOT NULL REFERENCES tmax_v2_ladder_snapshots(ladder_snapshot_id),
    metadata_fingerprint TEXT NOT NULL,
    market_unit TEXT,
    settlement_source_class TEXT,
    market_timezone TEXT,
    market_utc_offset_seconds INTEGER,
    market_metadata_source_json TEXT,
    market_metadata_missing_reason TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(ladder_snapshot_id, metadata_fingerprint)
);

CREATE TABLE IF NOT EXISTS tmax_v2_ladder_rung_metadata (
    metadata_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    rung_metadata_id TEXT NOT NULL UNIQUE,
    rung_quote_id TEXT NOT NULL REFERENCES tmax_v2_ladder_rung_quotes(rung_quote_id),
    metadata_fingerprint TEXT NOT NULL,
    question TEXT,
    question_source TEXT,
    question_missing_reason TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(rung_quote_id, metadata_fingerprint)
);

CREATE TABLE IF NOT EXISTS tmax_v2_forecast_capture_metadata (
    metadata_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_metadata_id TEXT NOT NULL UNIQUE,
    forecast_capture_id TEXT NOT NULL REFERENCES tmax_v2_forecast_captures(forecast_capture_id),
    metadata_fingerprint TEXT NOT NULL,
    normalized_hourly_curve_json TEXT,
    forecast_timezone TEXT,
    forecast_utc_offset_seconds INTEGER,
    available_at_basis TEXT,
    forecast_first_seen_at_utc TEXT,
    forecast_first_seen_basis TEXT,
    forecast_first_seen_source TEXT,
    curve_time_lineage_status TEXT,
    curve_time_missing_reason TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(forecast_capture_id, metadata_fingerprint)
);

CREATE TABLE IF NOT EXISTS tmax_v2_observation_identity_metadata (
    metadata_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_metadata_id TEXT NOT NULL UNIQUE,
    tmax_v2_observation_id TEXT NOT NULL REFERENCES tmax_v2_observation_event_lineage(tmax_v2_observation_id),
    metadata_fingerprint TEXT NOT NULL,
    station_id TEXT,
    icao TEXT,
    feed_identity TEXT,
    identity_missing_reason TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(tmax_v2_observation_id, metadata_fingerprint)
);

CREATE VIEW IF NOT EXISTS tmax_v2_ladder_snapshots_enriched AS
WITH latest AS (
    SELECT ladder_snapshot_id, MAX(metadata_seq) AS metadata_seq
    FROM tmax_v2_ladder_snapshot_metadata
    GROUP BY ladder_snapshot_id
)
SELECT
    base.*,
    COALESCE(metadata.market_unit, base.market_unit) AS effective_market_unit,
    COALESCE(metadata.settlement_source_class, base.settlement_source_class) AS effective_settlement_source_class,
    COALESCE(metadata.market_timezone, base.market_timezone) AS effective_market_timezone,
    COALESCE(metadata.market_utc_offset_seconds, base.market_utc_offset_seconds) AS effective_market_utc_offset_seconds,
    COALESCE(metadata.market_metadata_source_json, base.market_metadata_source_json) AS effective_market_metadata_source_json,
    COALESCE(metadata.market_metadata_missing_reason, base.market_metadata_missing_reason) AS effective_market_metadata_missing_reason
FROM tmax_v2_ladder_snapshots AS base
LEFT JOIN latest ON latest.ladder_snapshot_id = base.ladder_snapshot_id
LEFT JOIN tmax_v2_ladder_snapshot_metadata AS metadata ON metadata.metadata_seq = latest.metadata_seq;

CREATE VIEW IF NOT EXISTS tmax_v2_ladder_rung_quotes_enriched AS
WITH latest AS (
    SELECT rung_quote_id, MAX(metadata_seq) AS metadata_seq
    FROM tmax_v2_ladder_rung_metadata
    GROUP BY rung_quote_id
)
SELECT
    base.*,
    COALESCE(metadata.question, base.question) AS effective_question,
    metadata.question_source AS effective_question_source,
    metadata.question_missing_reason AS effective_question_missing_reason
FROM tmax_v2_ladder_rung_quotes AS base
LEFT JOIN latest ON latest.rung_quote_id = base.rung_quote_id
LEFT JOIN tmax_v2_ladder_rung_metadata AS metadata ON metadata.metadata_seq = latest.metadata_seq;

CREATE VIEW IF NOT EXISTS tmax_v2_forecast_captures_enriched AS
WITH latest AS (
    SELECT forecast_capture_id, MAX(metadata_seq) AS metadata_seq
    FROM tmax_v2_forecast_capture_metadata
    GROUP BY forecast_capture_id
)
SELECT
    base.*,
    COALESCE(metadata.normalized_hourly_curve_json, base.normalized_hourly_curve_json) AS effective_normalized_hourly_curve_json,
    COALESCE(metadata.forecast_timezone, base.forecast_timezone) AS effective_forecast_timezone,
    COALESCE(metadata.forecast_utc_offset_seconds, base.forecast_utc_offset_seconds) AS effective_forecast_utc_offset_seconds,
    COALESCE(metadata.available_at_basis, base.available_at_basis) AS effective_available_at_basis,
    COALESCE(metadata.forecast_first_seen_at_utc, base.forecast_first_seen_at_utc) AS effective_forecast_first_seen_at_utc,
    COALESCE(metadata.forecast_first_seen_basis, base.forecast_first_seen_basis) AS effective_forecast_first_seen_basis,
    COALESCE(metadata.forecast_first_seen_source, base.forecast_first_seen_source) AS effective_forecast_first_seen_source,
    COALESCE(metadata.curve_time_lineage_status, base.curve_time_lineage_status) AS effective_curve_time_lineage_status,
    COALESCE(metadata.curve_time_missing_reason, base.curve_time_missing_reason) AS effective_curve_time_missing_reason
FROM tmax_v2_forecast_captures AS base
LEFT JOIN latest ON latest.forecast_capture_id = base.forecast_capture_id
LEFT JOIN tmax_v2_forecast_capture_metadata AS metadata ON metadata.metadata_seq = latest.metadata_seq;

CREATE VIEW IF NOT EXISTS tmax_v2_observation_event_lineage_enriched AS
WITH latest AS (
    SELECT tmax_v2_observation_id, MAX(metadata_seq) AS metadata_seq
    FROM tmax_v2_observation_identity_metadata
    GROUP BY tmax_v2_observation_id
)
SELECT
    base.*,
    COALESCE(metadata.station_id, base.station_id) AS effective_station_id,
    COALESCE(metadata.icao, base.icao) AS effective_icao,
    COALESCE(metadata.feed_identity, base.feed_identity) AS effective_feed_identity,
    COALESCE(metadata.identity_missing_reason, base.identity_missing_reason) AS effective_identity_missing_reason
FROM tmax_v2_observation_event_lineage AS base
LEFT JOIN latest ON latest.tmax_v2_observation_id = base.tmax_v2_observation_id
LEFT JOIN tmax_v2_observation_identity_metadata AS metadata ON metadata.metadata_seq = latest.metadata_seq;

CREATE TABLE IF NOT EXISTS tmax_v2_canonical_states (
    tmax_state_id TEXT PRIMARY KEY,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    decision_ts_utc TEXT NOT NULL,
    ladder_snapshot_id TEXT NOT NULL REFERENCES tmax_v2_ladder_snapshots(ladder_snapshot_id),
    observation_event_id TEXT REFERENCES tmax_v2_observation_event_lineage(tmax_v2_observation_id),
    forecast_capture_id TEXT REFERENCES tmax_v2_forecast_captures(forecast_capture_id),
    observation_available_at_utc TEXT,
    forecast_available_at_utc TEXT,
    observation_lineage_status TEXT NOT NULL,
    forecast_lineage_status TEXT NOT NULL,
    pit_status TEXT NOT NULL CHECK (
        pit_status IN ('pit_verified','research_only_unknown_observation_first_seen',
                       'research_only_unknown_forecast_available_at','research_only_missing_inputs')
    ),
    lineage_summary_json TEXT NOT NULL DEFAULT '{}',
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(city, target_date, decision_ts_utc, ladder_snapshot_id)
);

-- v14 states remain immutable historical materializations.  A late-arriving
-- source can still be PIT-valid for an old decision, so V2 publishes later
-- input lineages as a new revision instead of mutating or replacing the base.
CREATE TABLE IF NOT EXISTS tmax_v2_canonical_state_revisions (
    state_revision_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    tmax_state_revision_id TEXT NOT NULL UNIQUE,
    tmax_state_id TEXT NOT NULL REFERENCES tmax_v2_canonical_states(tmax_state_id),
    lineage_fingerprint TEXT NOT NULL,
    observation_event_id TEXT REFERENCES tmax_v2_observation_event_lineage(tmax_v2_observation_id),
    forecast_capture_id TEXT REFERENCES tmax_v2_forecast_captures(forecast_capture_id),
    observation_available_at_utc TEXT,
    forecast_available_at_utc TEXT,
    observation_lineage_status TEXT NOT NULL,
    forecast_lineage_status TEXT NOT NULL,
    pit_status TEXT NOT NULL CHECK (
        pit_status IN ('pit_verified','research_only_unknown_observation_first_seen',
                       'research_only_unknown_forecast_available_at','research_only_missing_inputs')
    ),
    lineage_summary_json TEXT NOT NULL DEFAULT '{}',
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(tmax_state_id, lineage_fingerprint)
);

CREATE VIEW IF NOT EXISTS tmax_v2_canonical_state_effective AS
WITH latest_revision AS (
    SELECT tmax_state_id, MAX(state_revision_seq) AS state_revision_seq
    FROM tmax_v2_canonical_state_revisions
    GROUP BY tmax_state_id
)
SELECT
    base.tmax_state_id,
    revision.tmax_state_revision_id,
    revision.state_revision_seq,
    base.city,
    base.target_date,
    base.decision_ts_utc,
    base.ladder_snapshot_id,
    revision.observation_event_id,
    revision.forecast_capture_id,
    revision.observation_available_at_utc,
    revision.forecast_available_at_utc,
    revision.observation_lineage_status,
    revision.forecast_lineage_status,
    revision.pit_status,
    revision.lineage_summary_json,
    revision.created_at_utc
FROM tmax_v2_canonical_states AS base
JOIN latest_revision ON latest_revision.tmax_state_id = base.tmax_state_id
JOIN tmax_v2_canonical_state_revisions AS revision
  ON revision.state_revision_seq = latest_revision.state_revision_seq
UNION ALL
SELECT
    base.tmax_state_id,
    NULL AS tmax_state_revision_id,
    0 AS state_revision_seq,
    base.city,
    base.target_date,
    base.decision_ts_utc,
    base.ladder_snapshot_id,
    base.observation_event_id,
    base.forecast_capture_id,
    base.observation_available_at_utc,
    base.forecast_available_at_utc,
    base.observation_lineage_status,
    base.forecast_lineage_status,
    base.pit_status,
    base.lineage_summary_json,
    base.created_at_utc
FROM tmax_v2_canonical_states AS base
WHERE NOT EXISTS (
    SELECT 1
    FROM tmax_v2_canonical_state_revisions AS revision
    WHERE revision.tmax_state_id = base.tmax_state_id
);

CREATE TABLE IF NOT EXISTS weather_intraday_state_rows (
    state_row_id TEXT PRIMARY KEY,
    source_system TEXT NOT NULL,
    source_path TEXT,
    city TEXT NOT NULL,
    icao TEXT NOT NULL,
    timezone TEXT,
    target_date TEXT NOT NULL,
    decision_hour_local INTEGER NOT NULL,
    decision_cutoff_local TEXT,
    decision_last_obs_utc TEXT,
    obs_count_day INTEGER,
    obs_count_to_decision INTEGER,
    first_obs_utc TEXT,
    last_obs_utc TEXT,
    current_temp_f REAL,
    current_temp_c REAL,
    running_max_f REAL,
    running_max_c REAL,
    final_max_f REAL,
    final_max_c REAL,
    residual_c REAL,
    residual_ge_0_5c INTEGER,
    residual_ge_1_0c INTEGER,
    residual_ge_1_5c INTEGER,
    floor_c_bucket_delta INTEGER,
    temp_trend_1h_f REAL,
    temp_trend_3h_f REAL,
    minutes_since_running_max REAL,
    dewpoint_f REAL,
    wind_speed_kt REAL,
    wind_dir_deg REAL,
    sky_cover TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(source_system, city, target_date, decision_hour_local)
);

CREATE TABLE IF NOT EXISTS run_artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    artifact_kind TEXT NOT NULL,
    source_path TEXT NOT NULL,
    row_count INTEGER,
    payload TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS run_alerts (
    alert_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    alert_ts_utc TEXT,
    severity TEXT,
    kind TEXT,
    payload TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS ingestion_log (
    ingest_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT NOT NULL,
    source_row_hash TEXT NOT NULL,
    target_table TEXT NOT NULL,
    target_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(source_path, source_row_hash, target_table)
);

CREATE TABLE IF NOT EXISTS weather_strategy_runtime_registry (
    strategy_instance TEXT PRIMARY KEY,
    strategy_id TEXT,
    display_name TEXT NOT NULL,
    family TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL CHECK (
        lifecycle_status IN ('live','shadow','telemetry','paper','research','stale','shelved','blocked','monitor','pre_live','superseded-for-now','tiny_live_probe','deprecated')
    ),
    execution_mode TEXT NOT NULL CHECK (
        execution_mode IN ('live','zero_notional_shadow','telemetry','paper','research','monitor','historical','zero_notional_pre_live','tiny_live_split_taker_maker_probe','tiny_live','tiny_live_taker_probe')
    ),
    health_status TEXT NOT NULL CHECK (
        health_status IN ('healthy','idle','stale','blocked','shelved','unknown')
    ),
    source_layer TEXT NOT NULL CHECK (
        source_layer IN ('runtime_local','runtime_remote_mirror','fact_trades','docs','manual','legacy_read_only')
    ),
    runtime_dir TEXT,
    summary_path TEXT,
    primary_journal_path TEXT,
    latest_summary_ts_utc TEXT,
    latest_data_ts_utc TEXT,
    latest_artifact_mtime_utc TEXT,
    heartbeat_age_min REAL,
    candidate_rows INTEGER NOT NULL DEFAULT 0,
    plan_rows INTEGER NOT NULL DEFAULT 0,
    live_order_rows INTEGER NOT NULL DEFAULT 0,
    paper_order_rows INTEGER NOT NULL DEFAULT 0,
    shadow_rows INTEGER NOT NULL DEFAULT 0,
    telemetry_rows INTEGER NOT NULL DEFAULT 0,
    fact_trade_rows INTEGER NOT NULL DEFAULT 0,
    fact_live_real_rows INTEGER NOT NULL DEFAULT 0,
    fact_cost_usd REAL,
    first_target_date TEXT,
    last_target_date TEXT,
    latest_fill_ts_utc TEXT,
    cap_order_notional REAL,
    cap_city_day_notional REAL,
    cap_total_day_notional REAL,
    live_enabled INTEGER,
    process_status TEXT NOT NULL DEFAULT 'unknown',
    blocker_count INTEGER NOT NULL DEFAULT 0,
    blockers_json TEXT NOT NULL DEFAULT '[]',
    summary_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT,
    refreshed_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS weather_strategy_runtime_artifacts (
    artifact_key TEXT PRIMARY KEY,
    strategy_instance TEXT NOT NULL REFERENCES weather_strategy_runtime_registry(strategy_instance),
    artifact_kind TEXT NOT NULL,
    source_path TEXT NOT NULL,
    row_count INTEGER,
    size_bytes INTEGER,
    mtime_utc TEXT,
    latest_record_ts_utc TEXT,
    sample_json TEXT,
    refreshed_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS weather_strategy_shadow_queue (
    shadow_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    family TEXT NOT NULL,
    proposed_execution_mode TEXT NOT NULL DEFAULT 'zero_notional_shadow',
    priority TEXT NOT NULL CHECK (priority IN ('high','medium','low')),
    status TEXT NOT NULL CHECK (status IN ('proposed','runner_ready','active','blocked','superseded')),
    source_doc TEXT,
    target_runtime_dir TEXT,
    required_fields_json TEXT NOT NULL DEFAULT '[]',
    blockers_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    refreshed_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_runs_mode_state ON runs(execution_mode, state);
CREATE INDEX IF NOT EXISTS idx_runs_producer ON runs(producer_system, producer_run_id);
CREATE INDEX IF NOT EXISTS idx_signals_target_city ON signals(target_date, city);
CREATE INDEX IF NOT EXISTS idx_signals_pool_source ON signals(city_pool, forecast_source);
CREATE INDEX IF NOT EXISTS idx_signals_market ON signals(condition_id, market_id);
CREATE INDEX IF NOT EXISTS idx_plans_run_id ON plans(run_id);
CREATE INDEX IF NOT EXISTS idx_plans_signal_id ON plans(signal_id);
CREATE INDEX IF NOT EXISTS idx_orders_run_id ON orders(run_id);
CREATE INDEX IF NOT EXISTS idx_orders_order_id ON orders(order_id);
CREATE INDEX IF NOT EXISTS idx_orders_plan_id ON orders(plan_id);
CREATE INDEX IF NOT EXISTS idx_orders_status_quote ON orders(status, clob_status, quote_status);
CREATE INDEX IF NOT EXISTS idx_orders_sizing ON orders(sizing_policy, score_dist_tier);
-- Keep the live fill gate off the large orders table and exchange_response
-- overflow pages. The expressions must match the gate query exactly so this
-- remains a covering index on the external JRS canonical DB.
CREATE INDEX IF NOT EXISTS idx_orders_clob_gate_v2
    ON orders(
      venue, status, execution_id, order_id, shares, limit_price,
      posted_price, cost_usd, notional,
      json_extract(exchange_response, '$.place.makingAmount'),
      json_extract(exchange_response, '$.place.takingAmount'),
      json_extract(exchange_response, '$.maker_only'),
      lower(COALESCE(json_extract(exchange_response, '$.place.status'), ''))
    )
    WHERE venue='polymarket_clob' AND status='submitted';
CREATE INDEX IF NOT EXISTS idx_fills_execution_id ON fills(execution_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_settlements_market
    ON settlements(target_date, condition_id, bracket)
    WHERE condition_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_settlement_outcomes_city_bracket
    ON settlement_outcomes(source_system, city, target_date, bracket);
CREATE INDEX IF NOT EXISTS idx_settlement_outcomes_condition
    ON settlement_outcomes(condition_id);
CREATE INDEX IF NOT EXISTS idx_settlement_outcomes_date_city
    ON settlement_outcomes(target_date, city);
CREATE INDEX IF NOT EXISTS idx_weather_observation_events_city_date
    ON weather_observation_events(city, target_date, obs_ts_utc);
CREATE INDEX IF NOT EXISTS idx_weather_observation_events_icao_date
    ON weather_observation_events(icao, target_date, obs_ts_utc);
CREATE INDEX IF NOT EXISTS idx_fill_fee_adjustments_fill_id
    ON fill_fee_adjustments(fill_id);
CREATE INDEX IF NOT EXISTS idx_fill_price_adjustments_fill_id
    ON fill_price_adjustments(fill_id);
CREATE INDEX IF NOT EXISTS idx_fill_timestamp_adjustments_fill_id
    ON fill_timestamp_adjustments(fill_id);
CREATE INDEX IF NOT EXISTS idx_tmax_v2_ladder_snapshot_city_date_decision
    ON tmax_v2_ladder_snapshots(city, target_date, source_snapshot_ts_utc);
CREATE INDEX IF NOT EXISTS idx_tmax_v2_ladder_rung_snapshot
    ON tmax_v2_ladder_rung_quotes(ladder_snapshot_id, absolute_bracket_identity);
CREATE INDEX IF NOT EXISTS idx_tmax_v2_forecast_capture_asof
    ON tmax_v2_forecast_captures(city, target_date, available_at_utc);
CREATE INDEX IF NOT EXISTS idx_tmax_v2_observation_lineage_asof
    ON tmax_v2_observation_event_lineage(city, target_date, available_at_utc, obs_ts_utc);
CREATE INDEX IF NOT EXISTS idx_weather_information_events_city_available
    ON weather_information_events(city, available_at_utc, event_kind);
CREATE INDEX IF NOT EXISTS idx_weather_information_events_content_key
    ON weather_information_events(content_key, source, first_seen_at_utc);
CREATE INDEX IF NOT EXISTS idx_weather_state_checkpoints_city_date_asof
    ON weather_state_checkpoints(city, target_date, as_of_ts_utc);
CREATE INDEX IF NOT EXISTS idx_weather_state_checkpoints_trigger
    ON weather_state_checkpoints(trigger_event_id);
CREATE INDEX IF NOT EXISTS idx_tmax_v2_state_city_date_decision
    ON tmax_v2_canonical_states(city, target_date, decision_ts_utc);
CREATE INDEX IF NOT EXISTS idx_tmax_v2_state_revision_identity
    ON tmax_v2_canonical_state_revisions(tmax_state_id, state_revision_seq DESC);
CREATE INDEX IF NOT EXISTS idx_tmax_v2_ladder_metadata_identity
    ON tmax_v2_ladder_snapshot_metadata(ladder_snapshot_id, metadata_seq DESC);
CREATE INDEX IF NOT EXISTS idx_tmax_v2_rung_metadata_identity
    ON tmax_v2_ladder_rung_metadata(rung_quote_id, metadata_seq DESC);
CREATE INDEX IF NOT EXISTS idx_tmax_v2_forecast_metadata_identity
    ON tmax_v2_forecast_capture_metadata(forecast_capture_id, metadata_seq DESC);
CREATE INDEX IF NOT EXISTS idx_tmax_v2_observation_metadata_identity
    ON tmax_v2_observation_identity_metadata(tmax_v2_observation_id, metadata_seq DESC);
CREATE INDEX IF NOT EXISTS idx_weather_intraday_state_rows_city_date
    ON weather_intraday_state_rows(city, target_date, decision_hour_local);
CREATE INDEX IF NOT EXISTS idx_run_artifacts_run_id ON run_artifacts(run_id);
CREATE INDEX IF NOT EXISTS idx_run_alerts_run_id ON run_alerts(run_id);
CREATE INDEX IF NOT EXISTS idx_weather_strategy_runtime_status
    ON weather_strategy_runtime_registry(lifecycle_status, health_status);
CREATE INDEX IF NOT EXISTS idx_weather_strategy_runtime_family
    ON weather_strategy_runtime_registry(family);
CREATE INDEX IF NOT EXISTS idx_weather_strategy_artifacts_strategy
    ON weather_strategy_runtime_artifacts(strategy_instance);
CREATE INDEX IF NOT EXISTS idx_weather_strategy_shadow_queue_status
    ON weather_strategy_shadow_queue(status, priority);

-- ── Data-source management (Phase 2 minimal): profile + monitor instance ────
CREATE TABLE IF NOT EXISTS weather_data_source_profile (
    profile_id              TEXT PRIMARY KEY,
    feed_kind               TEXT NOT NULL,
    city                    TEXT NOT NULL,
    source_key              TEXT NOT NULL,
    source_kind             TEXT,
    station_or_feed         TEXT,
    icao                    TEXT,
    runway                  TEXT,
    source_role             TEXT NOT NULL DEFAULT 'primary',
    timezone_name           TEXT,
    expected_cadence_sec    REAL,
    staleness_max_age_sec   REAL,
    active_window_json      TEXT NOT NULL DEFAULT '{}',
    requires_auth           INTEGER NOT NULL DEFAULT 0,
    auth_ref                TEXT,
    strategy_eligible       INTEGER NOT NULL DEFAULT 0,
    live_eligible           INTEGER NOT NULL DEFAULT 0,
    observed_median_lag_sec REAL,
    observed_p95_lag_sec    REAL,
    notes                   TEXT NOT NULL DEFAULT '',
    updated_at_utc          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(city, feed_kind, source_key, station_or_feed, runway)
);
CREATE INDEX IF NOT EXISTS idx_weather_data_source_profile_city
    ON weather_data_source_profile(city, feed_kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_weather_data_source_profile_grain
    ON weather_data_source_profile(
        city,
        feed_kind,
        source_key,
        COALESCE(station_or_feed, ''),
        COALESCE(runway, '')
    );

CREATE TABLE IF NOT EXISTS weather_data_monitor_instance (
    monitor_instance_id     TEXT PRIMARY KEY,
    display_name            TEXT NOT NULL,
    feed_kind               TEXT NOT NULL,
    sources_json            TEXT NOT NULL DEFAULT '[]',
    cities_json             TEXT NOT NULL DEFAULT '[]',
    scan_interval_sec       REAL,
    active_window_json      TEXT NOT NULL DEFAULT '{}',
    output_dir              TEXT,
    latest_path             TEXT,
    journal_paths_json      TEXT NOT NULL DEFAULT '[]',
    state_path              TEXT,
    proxy_policy            TEXT,
    auth_refs_json          TEXT NOT NULL DEFAULT '[]',
    desired_status          TEXT NOT NULL DEFAULT 'enabled',
    host                    TEXT NOT NULL DEFAULT 'mac',
    tmux_session            TEXT,
    start_command           TEXT,
    summary_json            TEXT NOT NULL DEFAULT '{}',
    updated_at_utc          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_weather_data_monitor_instance_feed
    ON weather_data_monitor_instance(feed_kind);

-- ── Strategy runtime platform (B1/B2): definition + control plane ──────────
CREATE TABLE IF NOT EXISTS strategy_def (
    strategy_key     TEXT PRIMARY KEY,
    family           TEXT NOT NULL,
    strategy_group   TEXT NOT NULL DEFAULT 'weather',
    domain           TEXT NOT NULL DEFAULT 'weather',
    strategy_name    TEXT NOT NULL DEFAULT '',
    runner_module    TEXT NOT NULL DEFAULT '',
    strategy_module  TEXT NOT NULL DEFAULT '',
    meta_json        TEXT NOT NULL DEFAULT '{}',
    def_source       TEXT NOT NULL DEFAULT 'instance_family',  -- manifest | instance_family
    description      TEXT NOT NULL DEFAULT '',
    portfolio_status TEXT NOT NULL DEFAULT 'unclassified',
    portfolio_note   TEXT NOT NULL DEFAULT '',
    is_active        INTEGER NOT NULL DEFAULT 1,
    spec_commit      TEXT,
    updated_at_utc   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS strategy_instance (
    instance_id      TEXT PRIMARY KEY,
    strategy_key     TEXT NOT NULL,
    display_name     TEXT NOT NULL,
    family           TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL,
    execution_mode   TEXT NOT NULL,
    config_id        TEXT REFERENCES strategy_config(config_id),
    desired_status   TEXT NOT NULL DEFAULT 'enabled'
        CHECK (desired_status IN ('enabled','paused','shelved','blocked')),
    source_layer     TEXT NOT NULL DEFAULT 'runtime_local',
    runtime_dir      TEXT,
    start_script     TEXT,
    tmux_session     TEXT,
    expected_live    INTEGER,
    spec_commit      TEXT,
    params_hash      TEXT,
    notes            TEXT,
    updated_at_utc   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_strategy_instance_family
    ON strategy_instance(family);
CREATE INDEX IF NOT EXISTS idx_strategy_instance_config_id
    ON strategy_instance(config_id);

CREATE TABLE IF NOT EXISTS order_instance_lineage (
    execution_id  TEXT PRIMARY KEY REFERENCES orders(execution_id),
    instance_id   TEXT NOT NULL REFERENCES strategy_instance(instance_id),
    source        TEXT NOT NULL CHECK (source IN ('runtime_order_file','run_tag','unique_config')),
    evidence      TEXT,
    assigned_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_order_instance_lineage_instance
    ON order_instance_lineage(instance_id);

CREATE TABLE IF NOT EXISTS strategy_instance_runtime (
    instance_id                  TEXT PRIMARY KEY REFERENCES strategy_instance(instance_id),
    process_status              TEXT NOT NULL DEFAULT 'unknown'
        CHECK (process_status IN ('running','starting','stopped','crashed','stale','unknown')),
    pid                         INTEGER,
    supervisor_id               TEXT,
    heartbeat_at_utc            TEXT,
    last_tick_ts_utc            TEXT,
    last_data_ts_utc            TEXT,
    latest_summary_ts_utc       TEXT,
    latest_artifact_mtime_utc   TEXT,
    heartbeat_age_min           REAL,
    candidate_rows              INTEGER NOT NULL DEFAULT 0,
    plan_rows                   INTEGER NOT NULL DEFAULT 0,
    live_order_rows             INTEGER NOT NULL DEFAULT 0,
    paper_order_rows            INTEGER NOT NULL DEFAULT 0,
    shadow_rows                 INTEGER NOT NULL DEFAULT 0,
    telemetry_rows              INTEGER NOT NULL DEFAULT 0,
    fact_trade_rows             INTEGER NOT NULL DEFAULT 0,
    fact_live_real_rows         INTEGER NOT NULL DEFAULT 0,
    fact_cost_usd               REAL,
    first_target_date           TEXT,
    last_target_date            TEXT,
    latest_fill_ts_utc          TEXT,
    health_status               TEXT NOT NULL DEFAULT 'unknown'
        CHECK (health_status IN ('healthy','idle','stale','blocked','shelved','unknown')),
    live_enabled                INTEGER,
    summary_path                TEXT,
    primary_journal_path        TEXT,
    blocker_count               INTEGER NOT NULL DEFAULT 0,
    blockers_json               TEXT NOT NULL DEFAULT '[]',
    summary_json                TEXT NOT NULL DEFAULT '{}',
    refreshed_at_utc            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_strategy_instance_runtime_status
    ON strategy_instance_runtime(process_status, health_status);
CREATE INDEX IF NOT EXISTS idx_strategy_instance_runtime_heartbeat
    ON strategy_instance_runtime(heartbeat_at_utc DESC);

CREATE TABLE IF NOT EXISTS strategy_control_log (
    log_id       TEXT PRIMARY KEY,
    instance_id  TEXT NOT NULL,
    actor        TEXT NOT NULL,
    action       TEXT NOT NULL,
    from_state   TEXT,
    to_state     TEXT,
    reason       TEXT,
    spec_commit  TEXT,
    params_hash  TEXT,
    ts_utc       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_strategy_control_log_instance
    ON strategy_control_log(instance_id, ts_utc);

CREATE TRIGGER IF NOT EXISTS signals_canonical_before_update
BEFORE UPDATE ON signals
BEGIN
    SELECT RAISE(ABORT, 'signals is append-only');
END;

CREATE TRIGGER IF NOT EXISTS signals_canonical_before_delete
BEFORE DELETE ON signals
BEGIN
    SELECT RAISE(ABORT, 'signals is append-only');
END;

CREATE TRIGGER IF NOT EXISTS plans_canonical_before_update
BEFORE UPDATE ON plans
BEGIN
    SELECT RAISE(ABORT, 'plans is append-only');
END;

CREATE TRIGGER IF NOT EXISTS plans_canonical_before_delete
BEFORE DELETE ON plans
BEGIN
    SELECT RAISE(ABORT, 'plans is append-only');
END;

CREATE TRIGGER IF NOT EXISTS orders_canonical_before_update
BEFORE UPDATE ON orders
BEGIN
    SELECT RAISE(ABORT, 'orders is append-only');
END;

CREATE TRIGGER IF NOT EXISTS orders_canonical_before_delete
BEFORE DELETE ON orders
BEGIN
    SELECT RAISE(ABORT, 'orders is append-only');
END;

CREATE TRIGGER IF NOT EXISTS fills_canonical_before_update
BEFORE UPDATE ON fills
BEGIN
    SELECT RAISE(ABORT, 'fills is append-only');
END;

CREATE TRIGGER IF NOT EXISTS fills_canonical_before_delete
BEFORE DELETE ON fills
BEGIN
    SELECT RAISE(ABORT, 'fills is append-only');
END;

CREATE TRIGGER IF NOT EXISTS fill_fee_adjustments_canonical_before_update
BEFORE UPDATE ON fill_fee_adjustments
BEGIN
    SELECT RAISE(ABORT, 'fill_fee_adjustments is append-only');
END;

CREATE TRIGGER IF NOT EXISTS fill_fee_adjustments_canonical_before_delete
BEFORE DELETE ON fill_fee_adjustments
BEGIN
    SELECT RAISE(ABORT, 'fill_fee_adjustments is append-only');
END;

CREATE TRIGGER IF NOT EXISTS fill_price_adjustments_canonical_before_update
BEFORE UPDATE ON fill_price_adjustments
BEGIN
    SELECT RAISE(ABORT, 'fill_price_adjustments is append-only');
END;

CREATE TRIGGER IF NOT EXISTS fill_price_adjustments_canonical_before_delete
BEFORE DELETE ON fill_price_adjustments
BEGIN
    SELECT RAISE(ABORT, 'fill_price_adjustments is append-only');
END;

CREATE TRIGGER IF NOT EXISTS fill_timestamp_adjustments_canonical_before_update
BEFORE UPDATE ON fill_timestamp_adjustments
BEGIN
    SELECT RAISE(ABORT, 'fill_timestamp_adjustments is append-only');
END;

CREATE TRIGGER IF NOT EXISTS fill_timestamp_adjustments_canonical_before_delete
BEFORE DELETE ON fill_timestamp_adjustments
BEGIN
    SELECT RAISE(ABORT, 'fill_timestamp_adjustments is append-only');
END;

CREATE TRIGGER IF NOT EXISTS settlements_canonical_before_update
BEFORE UPDATE ON settlements
BEGIN
    SELECT RAISE(ABORT, 'settlements is append-only');
END;

CREATE TRIGGER IF NOT EXISTS settlements_canonical_before_delete
BEFORE DELETE ON settlements
BEGIN
    SELECT RAISE(ABORT, 'settlements is append-only');
END;

CREATE TRIGGER IF NOT EXISTS settlement_outcomes_canonical_before_update
BEFORE UPDATE ON settlement_outcomes
BEGIN
    SELECT RAISE(ABORT, 'settlement_outcomes is append-only');
END;

CREATE TRIGGER IF NOT EXISTS settlement_outcomes_canonical_before_delete
BEFORE DELETE ON settlement_outcomes
BEGIN
    SELECT RAISE(ABORT, 'settlement_outcomes is append-only');
END;

CREATE TRIGGER IF NOT EXISTS ingestion_log_canonical_before_update
BEFORE UPDATE ON ingestion_log
BEGIN
    SELECT RAISE(ABORT, 'ingestion_log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS ingestion_log_canonical_before_delete
BEFORE DELETE ON ingestion_log
BEGIN
    SELECT RAISE(ABORT, 'ingestion_log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS tmax_v2_ladder_snapshots_before_update
BEFORE UPDATE ON tmax_v2_ladder_snapshots
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_ladder_snapshots is append-only');
END;
CREATE TRIGGER IF NOT EXISTS tmax_v2_ladder_snapshots_before_delete
BEFORE DELETE ON tmax_v2_ladder_snapshots
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_ladder_snapshots is append-only');
END;
CREATE TRIGGER IF NOT EXISTS tmax_v2_ladder_rung_quotes_before_update
BEFORE UPDATE ON tmax_v2_ladder_rung_quotes
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_ladder_rung_quotes is append-only');
END;
CREATE TRIGGER IF NOT EXISTS tmax_v2_ladder_rung_quotes_before_delete
BEFORE DELETE ON tmax_v2_ladder_rung_quotes
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_ladder_rung_quotes is append-only');
END;
CREATE TRIGGER IF NOT EXISTS tmax_v2_forecast_captures_before_update
BEFORE UPDATE ON tmax_v2_forecast_captures
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_forecast_captures is append-only');
END;
CREATE TRIGGER IF NOT EXISTS tmax_v2_forecast_captures_before_delete
BEFORE DELETE ON tmax_v2_forecast_captures
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_forecast_captures is append-only');
END;
CREATE TRIGGER IF NOT EXISTS tmax_v2_observation_event_lineage_before_update
BEFORE UPDATE ON tmax_v2_observation_event_lineage
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_observation_event_lineage is append-only');
END;
CREATE TRIGGER IF NOT EXISTS tmax_v2_observation_event_lineage_before_delete
BEFORE DELETE ON tmax_v2_observation_event_lineage
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_observation_event_lineage is append-only');
END;
CREATE TRIGGER IF NOT EXISTS tmax_v2_canonical_states_before_update
BEFORE UPDATE ON tmax_v2_canonical_states
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_canonical_states is append-only');
END;
CREATE TRIGGER IF NOT EXISTS tmax_v2_canonical_states_before_delete
BEFORE DELETE ON tmax_v2_canonical_states
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_canonical_states is append-only');
END;

CREATE TRIGGER IF NOT EXISTS tmax_v2_canonical_state_revisions_before_update
BEFORE UPDATE ON tmax_v2_canonical_state_revisions
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_canonical_state_revisions is append-only');
END;
CREATE TRIGGER IF NOT EXISTS tmax_v2_canonical_state_revisions_before_delete
BEFORE DELETE ON tmax_v2_canonical_state_revisions
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_canonical_state_revisions is append-only');
END;

CREATE TRIGGER IF NOT EXISTS tmax_v2_ladder_snapshot_metadata_before_update
BEFORE UPDATE ON tmax_v2_ladder_snapshot_metadata
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_ladder_snapshot_metadata is append-only');
END;
CREATE TRIGGER IF NOT EXISTS tmax_v2_ladder_snapshot_metadata_before_delete
BEFORE DELETE ON tmax_v2_ladder_snapshot_metadata
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_ladder_snapshot_metadata is append-only');
END;
CREATE TRIGGER IF NOT EXISTS tmax_v2_ladder_rung_metadata_before_update
BEFORE UPDATE ON tmax_v2_ladder_rung_metadata
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_ladder_rung_metadata is append-only');
END;
CREATE TRIGGER IF NOT EXISTS tmax_v2_ladder_rung_metadata_before_delete
BEFORE DELETE ON tmax_v2_ladder_rung_metadata
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_ladder_rung_metadata is append-only');
END;
CREATE TRIGGER IF NOT EXISTS tmax_v2_forecast_capture_metadata_before_update
BEFORE UPDATE ON tmax_v2_forecast_capture_metadata
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_forecast_capture_metadata is append-only');
END;
CREATE TRIGGER IF NOT EXISTS tmax_v2_forecast_capture_metadata_before_delete
BEFORE DELETE ON tmax_v2_forecast_capture_metadata
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_forecast_capture_metadata is append-only');
END;
CREATE TRIGGER IF NOT EXISTS tmax_v2_observation_identity_metadata_before_update
BEFORE UPDATE ON tmax_v2_observation_identity_metadata
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_observation_identity_metadata is append-only');
END;
CREATE TRIGGER IF NOT EXISTS tmax_v2_observation_identity_metadata_before_delete
BEFORE DELETE ON tmax_v2_observation_identity_metadata
BEGIN
    SELECT RAISE(ABORT, 'tmax_v2_observation_identity_metadata is append-only');
END;
