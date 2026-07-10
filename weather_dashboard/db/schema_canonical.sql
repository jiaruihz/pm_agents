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
    execution_policy TEXT NOT NULL,
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
    status TEXT NOT NULL CHECK (status IN ('filled','partial','cancelled','expired','simulated')),
    filled_at_utc TEXT,
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
        lifecycle_status IN ('live','shadow','telemetry','paper','research','stale','shelved','blocked','monitor')
    ),
    execution_mode TEXT NOT NULL CHECK (
        execution_mode IN ('live','zero_notional_shadow','telemetry','paper','research','monitor','historical')
    ),
    health_status TEXT NOT NULL CHECK (
        health_status IN ('healthy','idle','stale','blocked','shelved','unknown')
    ),
    source_layer TEXT NOT NULL CHECK (
        source_layer IN ('runtime_local','runtime_remote_mirror','fact_trades','docs','manual')
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
CREATE INDEX IF NOT EXISTS idx_orders_plan_id ON orders(plan_id);
CREATE INDEX IF NOT EXISTS idx_orders_status_quote ON orders(status, clob_status, quote_status);
CREATE INDEX IF NOT EXISTS idx_orders_sizing ON orders(sizing_policy, score_dist_tier);
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
