"""Additive migrations for the first-seen information lineage.

The legacy candidate builder owns the original wide
``fact_signal_candidates`` table.  This module owns only the additive
event/checkpoint columns and tables, and is safe to run against both a fresh
database and an existing v1 database.
"""
from __future__ import annotations

import sqlite3

from weather_dashboard.db.replay_case_schema import apply_replay_case_schema

DDL = """
CREATE TABLE IF NOT EXISTS weather_information_events (
 information_event_id TEXT PRIMARY KEY, event_kind TEXT NOT NULL, event_role TEXT NOT NULL,
 source TEXT NOT NULL, city TEXT NOT NULL, station_id TEXT, provider_item_id TEXT,
 content_key TEXT NOT NULL, payload_hash TEXT NOT NULL, revision_of_event_id TEXT,
 source_event_ts_utc TEXT, issued_at_utc TEXT, valid_from_utc TEXT, valid_to_utc TEXT,
 detected_at_utc TEXT, first_seen_at_utc TEXT, available_at_utc TEXT, ingested_at_utc TEXT NOT NULL,
 pit_lineage_class TEXT NOT NULL, original_first_seen_unknown INTEGER NOT NULL,
 raw_source_path TEXT, raw_row_hash TEXT, material_state_change INTEGER NOT NULL DEFAULT 1,
 CHECK (event_kind IN ('observation','taf','forecast_curve')),
 CHECK (event_role IN ('new_content','revision')),
 CHECK (pit_lineage_class IN (
   'collector_exact','archive_known_available','late_backfill_first_seen_unknown'
 )),
 CHECK (pit_lineage_class <> 'late_backfill_first_seen_unknown'
        OR (first_seen_at_utc IS NULL AND original_first_seen_unknown = 1)),
 CHECK (material_state_change IN (0,1)),
 FOREIGN KEY (revision_of_event_id) REFERENCES weather_information_events(information_event_id)
);
CREATE TABLE IF NOT EXISTS weather_state_checkpoints (
 state_checkpoint_id TEXT PRIMARY KEY, city TEXT NOT NULL, target_date TEXT NOT NULL,
 trigger_event_id TEXT NOT NULL REFERENCES weather_information_events(information_event_id),
 as_of_ts_utc TEXT NOT NULL, input_event_set_hash TEXT NOT NULL,
 feature_store_frame_id TEXT, feature_row_id TEXT, feature_schema_version TEXT NOT NULL,
 feature_version_manifest TEXT NOT NULL, source_profile_id TEXT, pit_provenance TEXT NOT NULL,
 checkpoint_status TEXT NOT NULL, checkpoint_blocker TEXT, created_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weather_model_outputs (
 output_id TEXT PRIMARY KEY,
 checkpoint_id TEXT NOT NULL REFERENCES weather_state_checkpoints(state_checkpoint_id),
 trigger_event_id TEXT NOT NULL REFERENCES weather_information_events(information_event_id),
 city TEXT NOT NULL, target_date TEXT NOT NULL, decision_ts_utc TEXT NOT NULL,
 target_id TEXT NOT NULL, target_kind TEXT NOT NULL,
 p_model REAL, model_id TEXT NOT NULL, model_artifact_id TEXT NOT NULL,
 feature_set_id TEXT NOT NULL, input_refs_json TEXT NOT NULL,
 scorable_status TEXT NOT NULL, blocker_reason TEXT,
 market_feature_role TEXT NOT NULL, market_feature_clock TEXT NOT NULL,
 feature_book_snapshot_id TEXT, metadata_json TEXT NOT NULL,
 schema_version TEXT NOT NULL, payload_hash TEXT NOT NULL,
 ingested_at_utc TEXT NOT NULL,
 CHECK (target_kind IN ('physical_path','settlement_outcome','market_expression')),
 CHECK (scorable_status IN ('scorable','not_scorable')),
 CHECK ((scorable_status = 'scorable' AND p_model IS NOT NULL)
        OR (scorable_status = 'not_scorable' AND blocker_reason IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS weather_trade_intents (
 intent_id TEXT PRIMARY KEY,
 candidate_id TEXT NOT NULL,
 condition_id TEXT NOT NULL, token_id TEXT NOT NULL, side TEXT NOT NULL,
 requested_size REAL NOT NULL, sizing_profile TEXT NOT NULL,
 execution_profile TEXT NOT NULL, max_cost REAL, ttl_seconds INTEGER,
 dedupe_key TEXT NOT NULL, exposure_bucket TEXT NOT NULL, mode TEXT NOT NULL,
 metadata_json TEXT NOT NULL, schema_version TEXT NOT NULL,
 payload_hash TEXT NOT NULL, ingested_at_utc TEXT NOT NULL,
 CHECK (side IN ('BUY','SELL')),
 CHECK (mode IN ('research','shadow','zero_notional')),
 CHECK (requested_size >= 0),
 CHECK (mode <> 'zero_notional' OR requested_size = 0),
 CHECK (max_cost IS NULL OR (max_cost >= 0 AND max_cost <= 1))
);
CREATE INDEX IF NOT EXISTS idx_weather_model_outputs_checkpoint
 ON weather_model_outputs(checkpoint_id, decision_ts_utc);
CREATE INDEX IF NOT EXISTS idx_weather_trade_intents_candidate
 ON weather_trade_intents(candidate_id, execution_profile, mode);
CREATE TRIGGER IF NOT EXISTS weather_model_outputs_no_update
 BEFORE UPDATE ON weather_model_outputs BEGIN
   SELECT RAISE(ABORT, 'weather_model_outputs is append-only');
 END;
CREATE TRIGGER IF NOT EXISTS weather_model_outputs_no_delete
 BEFORE DELETE ON weather_model_outputs BEGIN
   SELECT RAISE(ABORT, 'weather_model_outputs is append-only');
 END;
CREATE TRIGGER IF NOT EXISTS weather_trade_intents_no_update
 BEFORE UPDATE ON weather_trade_intents BEGIN
   SELECT RAISE(ABORT, 'weather_trade_intents is append-only');
 END;
CREATE TRIGGER IF NOT EXISTS weather_trade_intents_no_delete
 BEFORE DELETE ON weather_trade_intents BEGIN
   SELECT RAISE(ABORT, 'weather_trade_intents is append-only');
 END;
CREATE INDEX IF NOT EXISTS idx_weather_information_events_city_available
 ON weather_information_events(city, available_at_utc, event_kind);
CREATE INDEX IF NOT EXISTS idx_weather_state_checkpoints_trigger
 ON weather_state_checkpoints(trigger_event_id);
"""


_EVENT_COLUMNS = {
    "material_state_change": "INTEGER NOT NULL DEFAULT 1",
}

_CANDIDATE_V2_COLUMNS = {
    "candidate_grain_version": "TEXT NOT NULL DEFAULT 'v1_legacy_daily'",
    "strategy_key": "TEXT",
    "model_artifact_id": "TEXT",
    "trigger_event_id": "TEXT",
    "state_checkpoint_id": "TEXT",
    "feature_store_frame_id": "TEXT",
    "feature_row_id": "TEXT",
    "decision_ts_utc": "TEXT",
    "book_snapshot_id": "TEXT",
    "book_snapshot_ts_utc": "TEXT",
    "book_available_at_utc": "TEXT",
    "pre_event_book_snapshot_id": "TEXT",
    "pre_event_book_available_at_utc": "TEXT",
    "market_evidence_status": "TEXT",
    "model_probability_before": "REAL",
    "model_probability_after": "REAL",
    "market_probability": "REAL",
    "market_probability_before": "REAL",
    "market_probability_change": "REAL",
    "probability_residual": "REAL",
    "candidate_status": "TEXT",
    "candidate_blocker": "TEXT",
    "policy_selected": "INTEGER",
    "first_city_day_selected": "INTEGER",
    "target_id": "TEXT",
    "target_kind": "TEXT",
    "expression_id": "TEXT",
    "token_id": "TEXT",
    "feature_set_id": "TEXT",
    "feature_book_snapshot_id": "TEXT",
    "execution_book_snapshot_id": "TEXT",
    "policy_id": "TEXT",
    "candidate_schema_version": "TEXT",
    "input_refs_json": "TEXT",
    "candidate_metadata_json": "TEXT",
}

_OBSERVATION_LINEAGE_COLUMNS = {
    "information_event_id": "TEXT",
    "available_at_utc": "TEXT",
    "pit_lineage_class": "TEXT",
}

_FORECAST_LINEAGE_COLUMNS = {
    "information_event_id": "TEXT",
    "available_at_utc": "TEXT",
}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    if not _table_exists(conn, table):
        return
    existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, declaration in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def apply_first_seen_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    _ensure_columns(conn, "weather_information_events", _EVENT_COLUMNS)
    _ensure_columns(conn, "fact_signal_candidates", _CANDIDATE_V2_COLUMNS)
    _ensure_columns(conn, "weather_observation_events", _OBSERVATION_LINEAGE_COLUMNS)
    _ensure_columns(conn, "fact_forecast_hourly_curves", _FORECAST_LINEAGE_COLUMNS)
    if _table_exists(conn, "fact_signal_candidates"):
        candidate_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(fact_signal_candidates)")
        }
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_fact_signal_candidates_v2_trigger
            ON fact_signal_candidates(candidate_grain_version, trigger_event_id, state_checkpoint_id)
            """
        )
        if {"condition_id", "candidate_grain_version", "final_yes"} <= candidate_columns:
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_fact_signal_candidates_condition_grain
                ON fact_signal_candidates(condition_id, candidate_grain_version, final_yes)
                """
            )
        if {
            "side",
            "event_date",
            "decision_entry_price",
            "edge",
            "final_yes",
        } <= candidate_columns:
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_fact_signal_candidates_active_selector
                ON fact_signal_candidates(side, event_date DESC, decision_entry_price, edge)
                WHERE final_yes IS NULL
                """
            )
        if {"event_date", "candidate_grain_version"} <= candidate_columns:
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_fact_signal_candidates_event_grain
                ON fact_signal_candidates(event_date, candidate_grain_version)
                """
            )
        if {
            "candidate_id",
            "candidate_grain_version",
            "condition_id",
            "event_date",
            "final_yes",
        } <= candidate_columns:
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_fact_signal_candidates_v2_pending_settlement
                ON fact_signal_candidates(condition_id, event_date, candidate_id)
                WHERE candidate_grain_version = 'v2_event_checkpoint'
                  AND final_yes IS NULL
                """
            )
    if _table_exists(conn, "weather_observation_events"):
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_weather_observation_events_information_event
            ON weather_observation_events(information_event_id)
            """
        )
    apply_replay_case_schema(conn)
    conn.commit()
