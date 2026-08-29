"""PIT checkpoint identity and canonical append-only ingest."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any

from weather_data_feed.information_events import canonical_json_hash


def build_state_checkpoint(
    *,
    city: str,
    target_date: str,
    trigger_event: Mapping[str, Any],
    as_of_ts_utc: str,
    feature_frame_ref: Mapping[str, Any] | None,
    input_events: Iterable[Mapping[str, Any]],
    pit_provenance: str,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build one event-triggered checkpoint, rejecting future information."""
    trigger_id = str(trigger_event.get("information_event_id") or "")
    if not trigger_id:
        raise ValueError("trigger_event must have information_event_id")
    trigger_available = trigger_event.get("available_at_utc")
    if trigger_available and str(trigger_available) > as_of_ts_utc:
        raise ValueError("trigger_event is available after as_of_ts_utc")
    if str(trigger_event.get("pit_lineage_class") or "") == "late_backfill_first_seen_unknown":
        status, blocker = "blocked_missing_required_identity", "late_backfill_first_seen_unknown"
    elif trigger_event.get("material_state_change") in (False, 0):
        status, blocker = "build_error", "non_material_duplicate_state"
    elif not feature_frame_ref:
        status, blocker = "build_error", "missing_feature_frame"
    else:
        status, blocker = "built", None
    eligible_ids = []
    for event in input_events:
        available = event.get("available_at_utc")
        if available and str(available) > as_of_ts_utc:
            raise ValueError("PIT checkpoint input is available after as_of_ts_utc")
        if available:
            eligible_ids.append(str(event.get("information_event_id") or ""))
    input_hash = canonical_json_hash(sorted(ident for ident in eligible_ids if ident))
    ref = dict(feature_frame_ref or {})
    schema = str(ref.get("feature_schema_version") or "feature_frame_v1")
    manifest = ref.get("feature_version_manifest") or {}
    checkpoint_id = canonical_json_hash(
        {
            "city": city,
            "target_date": target_date,
            "trigger_event_id": trigger_id,
            "as_of_ts_utc": as_of_ts_utc,
            "feature_schema_version": schema,
            "feature_version_manifest": manifest,
            "input_event_set_hash": input_hash,
        }
    )
    return {
        "state_checkpoint_id": checkpoint_id,
        "city": city,
        "target_date": target_date,
        "trigger_event_id": trigger_id,
        "as_of_ts_utc": as_of_ts_utc,
        "input_event_set_hash": input_hash,
        "feature_store_frame_id": ref.get("store_frame_id"),
        "feature_row_id": ref.get("feature_row_id"),
        "feature_schema_version": schema,
        "feature_version_manifest": json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        "source_profile_id": ref.get("source_profile_id"),
        "pit_provenance": pit_provenance,
        "checkpoint_status": status,
        "checkpoint_blocker": blocker,
        "created_at_utc": created_at_utc
        or datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
    }


def ingest_state_checkpoints(
    conn: sqlite3.Connection,
    checkpoints: Iterable[Mapping[str, Any]],
    *,
    commit: bool = True,
) -> int:
    rows = list(checkpoints)
    if not rows:
        return 0
    columns = list(rows[0])
    placeholders = ", ".join("?" for _ in columns)
    inserted = 0
    for row in rows:
        cursor = conn.execute(
            f"INSERT OR IGNORE INTO weather_state_checkpoints ({', '.join(columns)}) VALUES ({placeholders})",
            [row.get(column) for column in columns],
        )
        inserted += int(cursor.rowcount or 0)
    if commit:
        conn.commit()
    return inserted
