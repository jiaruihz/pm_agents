"""Additive migrations for the first-seen information lineage.

The legacy candidate builder owns the original wide
``fact_signal_candidates`` table.  This module owns only the additive
event/checkpoint columns and tables, and is safe to run against both a fresh
database and an existing v1 database.
"""
from __future__ import annotations

import sqlite3

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
    if _table_exists(conn, "weather_observation_events"):
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_weather_observation_events_information_event
            ON weather_observation_events(information_event_id)
            """
        )
    conn.commit()
