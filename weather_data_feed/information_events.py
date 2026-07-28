"""Deterministic contracts for first-seen weather information events.

This module deliberately has no filesystem or database side effects.  Both the
data-feed producer and canonical rebuild use it so a poll/restart/rebuild
cannot change an immutable event identity or manufacture an exact first-seen
timestamp from a provider timestamp.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from weather_data_feed.observation_sources.aliases import normalize_source_name


PIT_LINEAGE_CLASSES = frozenset(
    {
        "collector_exact",
        "archive_known_available",
        "late_backfill_first_seen_unknown",
    }
)

OBSERVATION_DELIVERY_METADATA_FIELDS = frozenset(
    {
        "producer",
        "status",
        "error",
        "ts_utc",
        "local_detect_ts_utc",
        "fetched_at_utc",
        "payload_hash",
        "raw_payload_hash",
        "changed_since_last",
        "first_seen_type",
        "original_first_seen_unknown",
        "recovered_from_multi_record_payload",
        "information_event_id",
        "event_kind",
        "event_role",
        "content_key",
        "revision_of_event_id",
        "detected_at_utc",
        "first_seen_at_utc",
        "available_at_utc",
        "pit_lineage_class",
        "material_state_change",
        "raw_source_path",
        "raw_row_hash",
        "information_event_status",
        "source_fetch_start_utc",
        "source_fetch_end_utc",
        "source_fetch_latency_sec",
        "source_first_seen_at_utc",
        "source_published_at_utc",
        "observation_cache_generated_at_utc",
        "source_status",
        "source_kind",
        "source_note",
        "source_age_sec",
        "detected_after_report_sec",
        "estimated_cadence_min",
        "record_count",
        "target_date",
        "city",
        "source",
        "station",
        "live_observation_source",
        "mapping_rule",
        "registry_class",
        "settlement_source",
        "settlement_source_class",
    }
)


def normalized_observation_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return provider/weather content without collector delivery metadata."""
    return {
        str(key): value
        for key, value in row.items()
        if key not in OBSERVATION_DELIVERY_METADATA_FIELDS and not str(key).startswith("_")
    }


def canonical_json_hash(payload: Any) -> str:
    """Return the full SHA256 of canonical JSON, preserving explicit nulls."""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def information_event_id(
    *,
    event_kind: str,
    source: str,
    station_id: str | None,
    provider_item_id: str | None,
    content_key: str,
    payload_hash: str,
) -> str:
    """Build the source-specific immutable event ID from the frozen contract."""
    if not event_kind or not content_key or not payload_hash:
        raise ValueError("event_kind, content_key, and payload_hash are required")
    return canonical_json_hash(
        {
            "event_kind": str(event_kind),
            "source": normalize_source_name(source),
            "station_id": station_id,
            "provider_item_id": provider_item_id,
            "content_key": str(content_key),
            "payload_hash": str(payload_hash),
        }
    )


def validate_information_event_lineage(event: Mapping[str, Any]) -> None:
    """Reject clock substitutions that would violate the first-seen contract."""
    lineage_class = str(event.get("pit_lineage_class") or "")
    if lineage_class not in PIT_LINEAGE_CLASSES:
        raise ValueError(f"unknown pit_lineage_class: {lineage_class!r}")

    first_seen = event.get("first_seen_at_utc")
    detected = event.get("detected_at_utc")
    available = event.get("available_at_utc")
    original_unknown = bool(event.get("original_first_seen_unknown"))

    if lineage_class == "collector_exact":
        if not first_seen or not detected or not available:
            raise ValueError("collector_exact requires detected_at_utc, first_seen_at_utc, and available_at_utc")
        if original_unknown:
            raise ValueError("collector_exact cannot have original_first_seen_unknown=true")
        # The first raw delivery has detected_at == first_seen_at.  A replayed
        # delivery may be detected later while retaining the original event's
        # earlier first-seen clock, so only publication ordering is universal.
        if not str(first_seen) <= str(available) or not str(detected) <= str(available):
            raise ValueError("collector_exact requires first_seen_at_utc and detected_at_utc not after available_at_utc")
        return

    if lineage_class == "late_backfill_first_seen_unknown":
        if first_seen is not None or not original_unknown:
            raise ValueError("late backfill must keep first_seen_at_utc null and original_first_seen_unknown=true")
        return

    # archive_known_available may support ordinary PIT replay, but is not an
    # exact collector-arrival record.  Provider issue/report timestamps are
    # intentionally not accepted as a substitute for first_seen_at_utc.
    if not available:
        raise ValueError("archive_known_available requires available_at_utc")
    if first_seen is not None:
        raise ValueError("archive_known_available must not claim exact first_seen_at_utc")


def build_information_event(
    *,
    event_kind: str,
    event_role: str,
    source: str,
    city: str,
    station_id: str | None,
    provider_item_id: str | None,
    content_key: str,
    normalized_payload: Any,
    revision_of_event_id: str | None = None,
    source_event_ts_utc: str | None = None,
    issued_at_utc: str | None = None,
    valid_from_utc: str | None = None,
    valid_to_utc: str | None = None,
    detected_at_utc: str | None = None,
    first_seen_at_utc: str | None = None,
    available_at_utc: str | None = None,
    pit_lineage_class: str,
    original_first_seen_unknown: bool = False,
    material_state_change: bool = True,
    raw_source_path: str | None = None,
    raw_row_hash: str | None = None,
) -> dict[str, Any]:
    """Create a validated, immutable event header without publishing it."""
    if event_role not in {"new_content", "revision"}:
        raise ValueError("event_role must be 'new_content' or 'revision'")
    if event_role == "revision" and not revision_of_event_id:
        raise ValueError("revision requires revision_of_event_id")
    payload_hash = canonical_json_hash(normalized_payload)
    event = {
        "event_kind": str(event_kind),
        "event_role": event_role,
        "source": normalize_source_name(source),
        "city": str(city),
        "station_id": station_id,
        "provider_item_id": provider_item_id,
        "content_key": str(content_key),
        "payload_hash": payload_hash,
        "revision_of_event_id": revision_of_event_id,
        "source_event_ts_utc": source_event_ts_utc,
        "issued_at_utc": issued_at_utc,
        "valid_from_utc": valid_from_utc,
        "valid_to_utc": valid_to_utc,
        "detected_at_utc": detected_at_utc,
        "first_seen_at_utc": first_seen_at_utc,
        "available_at_utc": available_at_utc,
        "pit_lineage_class": pit_lineage_class,
        "original_first_seen_unknown": bool(original_first_seen_unknown),
        "material_state_change": bool(material_state_change),
        "raw_source_path": raw_source_path,
        "raw_row_hash": raw_row_hash,
    }
    event["information_event_id"] = information_event_id(
        event_kind=event["event_kind"],
        source=event["source"],
        station_id=station_id,
        provider_item_id=provider_item_id,
        content_key=event["content_key"],
        payload_hash=payload_hash,
    )
    validate_information_event_lineage(event)
    return event
