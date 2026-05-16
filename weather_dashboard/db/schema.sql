-- weather_dashboard DB Schema v1

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    description TEXT
);

-- Universe configs
CREATE TABLE IF NOT EXISTS universes (
    universe_id       TEXT PRIMARY KEY,
    name              TEXT UNIQUE NOT NULL,
    description       TEXT,
    cities            TEXT NOT NULL,  -- JSON array
    models            TEXT NOT NULL,  -- JSON array
    created_at_utc    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    frozen_at_utc     TEXT,
    deprecated_at_utc TEXT
);

-- Code versions (git SHA)
CREATE TABLE IF NOT EXISTS code_versions (
    code_version    TEXT PRIMARY KEY,  -- git SHA
    branch          TEXT,
    commit_subject  TEXT,
    commit_at_utc   TEXT,
    deployed_at_utc TEXT,
    notes           TEXT
);

-- Strategy configs
CREATE TABLE IF NOT EXISTS strategy_config (
    config_id TEXT PRIMARY KEY,  -- hash of params
    name TEXT NOT NULL,
    params TEXT NOT NULL,  -- JSON
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Runs
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    config_id       TEXT NOT NULL REFERENCES strategy_config(config_id),
    universe_id     TEXT REFERENCES universes(universe_id),
    code_version    TEXT REFERENCES code_versions(code_version),
    execution_mode  TEXT NOT NULL CHECK (execution_mode IN ('snapshot_replay','paper','live')),
    date_range_start TEXT,
    date_range_end  TEXT,
    started_at_utc  TEXT,
    ended_at_utc    TEXT,
    state           TEXT NOT NULL CHECK (state IN ('explore','paper','live','retired')),
    repro_key       TEXT,
    parent_run_id   TEXT REFERENCES runs(run_id),
    tags            TEXT,  -- JSON array
    metrics         TEXT,  -- JSON object
    metrics_at_utc  TEXT,
    notes           TEXT,
    created_at_utc  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Signals
CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    snapshot_ts_utc TEXT,
    snapshot_file TEXT,
    target_date TEXT,
    city TEXT,
    bracket TEXT,
    side TEXT CHECK (side IN ('YES','NO')),
    model_version TEXT,
    model_p_yes TEXT,
    market_price TEXT,
    edge TEXT,
    abs_edge TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Plans
CREATE TABLE IF NOT EXISTS plans (
    plan_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    signal_id TEXT NOT NULL REFERENCES signals(signal_id),
    config_id TEXT NOT NULL REFERENCES strategy_config(config_id),
    desired_shares TEXT,
    skip_reason TEXT,  -- NULL = ordered, non-NULL = skip reason
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Orders
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    plan_id TEXT REFERENCES plans(plan_id),
    execution_mode TEXT,
    side TEXT CHECK (side IN ('BUY_YES','BUY_NO')),
    entry_price TEXT,
    shares TEXT,
    cost_usd TEXT,
    placed_at_utc TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Fills
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    filled_shares TEXT,
    filled_price TEXT,
    fees_usd TEXT DEFAULT '0',
    status TEXT CHECK (status IN ('filled','partial','cancelled','expired')),
    filled_at_utc TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Settlements
CREATE TABLE IF NOT EXISTS settlements (
    settlement_id TEXT PRIMARY KEY,
    target_date TEXT NOT NULL,
    bracket TEXT NOT NULL,
    final_yes INTEGER,  -- 0, 1, or NULL
    status TEXT CHECK (status IN ('settled','missing_event','missing_bracket')),
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(target_date, bracket)
);

-- Ingestion log for idempotency
CREATE TABLE IF NOT EXISTS ingestion_log (
    ingest_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT NOT NULL,
    source_row_hash TEXT NOT NULL,
    target_table TEXT NOT NULL,
    target_id TEXT NOT NULL,
    UNIQUE(source_path, source_row_hash, target_table)
);

-- === INDEXES ===
CREATE INDEX IF NOT EXISTS idx_orders_run_id ON orders(run_id);
CREATE INDEX IF NOT EXISTS idx_orders_run_mode ON orders(run_id, execution_mode);
CREATE INDEX IF NOT EXISTS idx_plans_run_id ON plans(run_id);
CREATE INDEX IF NOT EXISTS idx_plans_signal_id ON plans(signal_id);
CREATE INDEX IF NOT EXISTS idx_plans_config_id ON plans(config_id);
CREATE INDEX IF NOT EXISTS idx_fills_order_id ON fills(order_id);
CREATE INDEX IF NOT EXISTS idx_signals_target_date_city ON signals(target_date, city);
CREATE INDEX IF NOT EXISTS idx_signals_snapshot_ts ON signals(snapshot_ts_utc);
CREATE INDEX IF NOT EXISTS idx_settlements_target_bracket ON settlements(target_date, bracket);
CREATE INDEX IF NOT EXISTS idx_runs_config_mode ON runs(config_id, execution_mode);
CREATE INDEX IF NOT EXISTS idx_runs_state ON runs(state);
CREATE INDEX IF NOT EXISTS idx_runs_repro_key ON runs(repro_key);

-- === APPEND-ONLY TRIGGERS ===

-- signals: prevent UPDATE/DELETE
CREATE TRIGGER IF NOT EXISTS signals_before_update
BEFORE UPDATE ON signals
BEGIN
    SELECT RAISE(ABORT, 'signals is append-only');
END;

CREATE TRIGGER IF NOT EXISTS signals_before_delete
BEFORE DELETE ON signals
BEGIN
    SELECT RAISE(ABORT, 'signals is append-only');
END;

-- plans: prevent UPDATE/DELETE
CREATE TRIGGER IF NOT EXISTS plans_before_update
BEFORE UPDATE ON plans
BEGIN
    SELECT RAISE(ABORT, 'plans is append-only');
END;

CREATE TRIGGER IF NOT EXISTS plans_before_delete
BEFORE DELETE ON plans
BEGIN
    SELECT RAISE(ABORT, 'plans is append-only');
END;

-- orders: prevent UPDATE/DELETE
CREATE TRIGGER IF NOT EXISTS orders_before_update
BEFORE UPDATE ON orders
BEGIN
    SELECT RAISE(ABORT, 'orders is append-only');
END;

CREATE TRIGGER IF NOT EXISTS orders_before_delete
BEFORE DELETE ON orders
BEGIN
    SELECT RAISE(ABORT, 'orders is append-only');
END;

-- fills: prevent UPDATE/DELETE
CREATE TRIGGER IF NOT EXISTS fills_before_update
BEFORE UPDATE ON fills
BEGIN
    SELECT RAISE(ABORT, 'fills is append-only');
END;

CREATE TRIGGER IF NOT EXISTS fills_before_delete
BEFORE DELETE ON fills
BEGIN
    SELECT RAISE(ABORT, 'fills is append-only');
END;

-- strategy_config: prevent UPDATE/DELETE
CREATE TRIGGER IF NOT EXISTS strategy_config_before_update
BEFORE UPDATE ON strategy_config
BEGIN
    SELECT RAISE(ABORT, 'strategy_config is append-only');
END;

CREATE TRIGGER IF NOT EXISTS strategy_config_before_delete
BEFORE DELETE ON strategy_config
BEGIN
    SELECT RAISE(ABORT, 'strategy_config is append-only');
END;

-- ingestion_log: prevent UPDATE/DELETE
CREATE TRIGGER IF NOT EXISTS ingestion_log_before_update
BEFORE UPDATE ON ingestion_log
BEGIN
    SELECT RAISE(ABORT, 'ingestion_log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS ingestion_log_before_delete
BEFORE DELETE ON ingestion_log
BEGIN
    SELECT RAISE(ABORT, 'ingestion_log is append-only');
END;

-- universes: prevent UPDATE when frozen
CREATE TRIGGER IF NOT EXISTS universes_before_update
BEFORE UPDATE ON universes
WHEN OLD.frozen_at_utc IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'frozen universes cannot be updated');
END;