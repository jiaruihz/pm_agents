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
    order_side TEXT NOT NULL CHECK (order_side IN ('BUY_YES','BUY_NO')),
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
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    plan_id TEXT NOT NULL REFERENCES plans(plan_id),
    venue TEXT NOT NULL CHECK (venue IN ('paper','snapshot_replay','polymarket_clob')),
    order_side TEXT NOT NULL CHECK (order_side IN ('BUY_YES','BUY_NO')),
    limit_price REAL,
    entry_price REAL NOT NULL,
    shares REAL NOT NULL,
    cost_usd REAL NOT NULL,
    notional REAL,
    status TEXT NOT NULL,
    exchange_response TEXT,
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

CREATE INDEX IF NOT EXISTS idx_runs_mode_state ON runs(execution_mode, state);
CREATE INDEX IF NOT EXISTS idx_runs_producer ON runs(producer_system, producer_run_id);
CREATE INDEX IF NOT EXISTS idx_signals_target_city ON signals(target_date, city);
CREATE INDEX IF NOT EXISTS idx_signals_pool_source ON signals(city_pool, forecast_source);
CREATE INDEX IF NOT EXISTS idx_signals_market ON signals(condition_id, market_id);
CREATE INDEX IF NOT EXISTS idx_plans_run_id ON plans(run_id);
CREATE INDEX IF NOT EXISTS idx_plans_signal_id ON plans(signal_id);
CREATE INDEX IF NOT EXISTS idx_orders_run_id ON orders(run_id);
CREATE INDEX IF NOT EXISTS idx_orders_plan_id ON orders(plan_id);
CREATE INDEX IF NOT EXISTS idx_fills_execution_id ON fills(execution_id);
CREATE INDEX IF NOT EXISTS idx_settlements_market ON settlements(target_date, condition_id, bracket);
CREATE INDEX IF NOT EXISTS idx_run_artifacts_run_id ON run_artifacts(run_id);
CREATE INDEX IF NOT EXISTS idx_run_alerts_run_id ON run_alerts(run_id);

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
