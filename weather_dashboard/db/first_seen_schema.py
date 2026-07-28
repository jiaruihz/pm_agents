"""Additive schema for the first-seen information lineage."""
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
 raw_source_path TEXT, raw_row_hash TEXT,
 CHECK (pit_lineage_class <> 'late_backfill_first_seen_unknown'
        OR (first_seen_at_utc IS NULL AND original_first_seen_unknown = 1))
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

def apply_first_seen_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()
