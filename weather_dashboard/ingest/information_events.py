"""Canonical ingest for immutable first-seen information-event headers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import sqlite3
from typing import Any

from weather_data_feed.information_events import information_event_id, validate_information_event_lineage


_EVENT_COLUMNS = (
    "information_event_id",
    "event_kind",
    "event_role",
    "source",
    "city",
    "station_id",
    "provider_item_id",
    "content_key",
    "payload_hash",
    "revision_of_event_id",
    "source_event_ts_utc",
    "issued_at_utc",
    "valid_from_utc",
    "valid_to_utc",
    "detected_at_utc",
    "first_seen_at_utc",
    "available_at_utc",
    "ingested_at_utc",
    "pit_lineage_class",
    "original_first_seen_unknown",
    "material_state_change",
    "raw_source_path",
    "raw_row_hash",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_event(raw: Mapping[str, Any], *, ingested_at_utc: str) -> dict[str, Any]:
    event = {column: raw.get(column) for column in _EVENT_COLUMNS}
    event["ingested_at_utc"] = str(event.get("ingested_at_utc") or ingested_at_utc)
    event["original_first_seen_unknown"] = int(bool(event.get("original_first_seen_unknown")))
    event["material_state_change"] = int(event.get("material_state_change") is not False)
    expected_id = information_event_id(
        event_kind=str(event.get("event_kind") or ""),
        source=str(event.get("source") or ""),
        station_id=event.get("station_id"),
        provider_item_id=event.get("provider_item_id"),
        content_key=str(event.get("content_key") or ""),
        payload_hash=str(event.get("payload_hash") or ""),
    )
    if event.get("information_event_id") != expected_id:
        raise ValueError("information_event_id does not match the frozen identity contract")
    validate_information_event_lineage(event)
    return event


def ingest_information_events(
    conn: sqlite3.Connection,
    events: Iterable[Mapping[str, Any]],
    *,
    ingested_at_utc: str | None = None,
    commit: bool = True,
) -> dict[str, int]:
    """Append immutable event headers without rewriting their historical clocks.

    Duplicate raw deliveries are expected.  The first canonical event wins; a
    later delivery cannot update first_seen, availability or any other event
    header field. Revisions must already have a distinct event identity and an
    explicit ``revision_of_event_id``.
    """
    ingested_at = ingested_at_utc or _utc_now()
    inserted = 0
    duplicates = 0
    columns = ", ".join(_EVENT_COLUMNS)
    placeholders = ", ".join("?" for _ in _EVENT_COLUMNS)
    for raw in events:
        event = _canonical_event(raw, ingested_at_utc=ingested_at)
        cursor = conn.execute(
            f"INSERT OR IGNORE INTO weather_information_events ({columns}) VALUES ({placeholders})",
            [event[column] for column in _EVENT_COLUMNS],
        )
        if cursor.rowcount:
            inserted += 1
        else:
            duplicates += 1
    if commit:
        conn.commit()
    return {"inserted": inserted, "duplicates": duplicates}
