#!/usr/bin/env python3
"""Materialize lab evidence into immutable weather information/source events.

This bridge is deliberately one-way: the lab SQLite database and its raw
payloads remain the evidence of record, while this module only appends
normalized views for zero-notional data-feed consumers.  It never reads a
credential, starts a collector, or calls an exchange.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from weather_data_feed.information_events import (
    build_information_event,
    canonical_json_hash,
    information_event_id,
)
from weather_data_feed.source_registry import DEFAULT_SOURCE_PROFILES_JSON, load_source_profiles


SOURCE_CLASSES: dict[str, tuple[str, str]] = {
    "METAR_WS_METAR": ("metar_ws_metar", "official_metar"),
    "METAR_WS_HFMETAR": ("metar_ws_hfmetar", "high_frequency_metar"),
    "METAR_WS_DATIS": ("metar_ws_datis", "datis_derived"),
    "AWC_API": ("aviationweather_metar", "official_metar"),
    "AWC_CACHE": ("aviationweather_cache_csv", "official_metar_cache"),
    "WIS2_ORIGIN": ("wis2_origin_metar", "official_metar"),
    "WIS2_CACHE": ("wis2_cache_metar", "official_metar_cache"),
}


def _utc_from_ns(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value) / 1_000_000_000, tz=timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        decoded = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _station_cities(profiles_path: Path) -> dict[str, tuple[tuple[str, str], ...]]:
    """Map configured/official ICAO values to all known city profiles."""
    by_station: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for city, profile in load_source_profiles(profiles_path).items():
        for station in {profile.configured_icao, profile.official_station_or_feed}:
            normalized = str(station or "").strip().upper()
            if normalized and (city, profile.timezone_name) not in by_station[normalized]:
                by_station[normalized].append((city, profile.timezone_name))
    return {station: tuple(rows) for station, rows in by_station.items()}


def _read_existing_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, Mapping) and row.get("information_event_id"):
                ids.add(str(row["information_event_id"]))
    return ids


def _append_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    prepared = [json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in rows]
    if not prepared:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for line in prepared:
            handle.write(line + "\n")
        handle.flush()
    return len(prepared)


def _source_for(source_id: str, report_kind: str) -> tuple[str, str]:
    configured = SOURCE_CLASSES.get(str(source_id or "").upper())
    if configured is not None:
        return configured
    # Preserve a source we do not yet classify rather than silently merging it
    # into official METAR.  The report class remains explicit downstream.
    return (f"lab_{str(source_id or 'unknown').strip().lower()}", str(report_kind or "unknown").lower())


def _content_key(row: Mapping[str, Any], *, city: str, report_class: str) -> str:
    return "|".join(
        str(value or "")
        for value in (
            city,
            row["event_family_id"],
            row["semantic_version_id"],
            row.get("raw_report_id"),
            report_class,
        )
    )


def _load_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    query = """
        SELECT
            s.source_seen_id, s.source_id AS seen_source_id, s.vantage_id,
            s.notification_seen_at_ns, s.payload_fetch_started_at_ns,
            s.payload_fetch_finished_at_ns, s.station_decoded_at_ns,
            s.first_actionable_seen_at_ns, s.clock_offset_ms AS seen_clock_offset_ms,
            s.clock_valid AS seen_clock_valid, s.raw_payload_sha256 AS seen_raw_payload_sha256,
            s.evidence_status,
            e.observation_version_id, e.event_family_id, e.raw_report_id,
            e.semantic_version_id, e.station_id, e.report_kind, e.observation_time,
            e.is_correction, e.correction_marker, e.air_temperature_c,
            e.dewpoint_c, e.wind_direction_deg, e.wind_speed_kt, e.visibility_m,
            e.altimeter_hpa, e.normalized_raw_text, e.normalized_fields_json,
            t.transport_message_id, t.source_id AS transport_source_id,
            t.received_wall_ns, t.received_monotonic_ns,
            t.clock_offset_ms AS transport_clock_offset_ms,
            t.clock_valid AS transport_clock_valid, t.raw_payload_sha256,
            t.raw_payload_path
        FROM source_observation_seen AS s
        JOIN observation_event AS e ON e.observation_version_id = s.observation_version_id
        JOIN transport_message AS t ON t.transport_message_id = s.transport_message_id
        WHERE s.evidence_status = 'actionable'
        ORDER BY s.first_actionable_seen_at_ns, s.source_seen_id
    """
    return [dict(row) for row in conn.execute(query)]


def _event_payload(row: Mapping[str, Any], *, source: str, report_class: str) -> dict[str, Any]:
    """Stable observation content only; delivery clocks do not change identity."""
    return {
        "source": source,
        "report_class": report_class,
        "event_family_id": row["event_family_id"],
        "raw_report_id": row.get("raw_report_id"),
        "semantic_version_id": row["semantic_version_id"],
        "station_id": row["station_id"],
        "report_kind": row["report_kind"],
        "observation_time": row["observation_time"],
        "is_correction": bool(row["is_correction"]),
        "correction_marker": row.get("correction_marker"),
        "air_temperature_c": row.get("air_temperature_c"),
        "dewpoint_c": row.get("dewpoint_c"),
        "wind_direction_deg": row.get("wind_direction_deg"),
        "wind_speed_kt": row.get("wind_speed_kt"),
        "visibility_m": row.get("visibility_m"),
        "altimeter_hpa": row.get("altimeter_hpa"),
        "normalized_raw_text": row.get("normalized_raw_text"),
        "normalized_fields": _json_object(row.get("normalized_fields_json")),
    }


def materialize_lab_events(
    evidence_db: Path,
    output_root: Path,
    *,
    profiles_path: Path = DEFAULT_SOURCE_PROFILES_JSON,
) -> dict[str, int]:
    """Append actionable lab observations as typed information/source events.

    Cached replay rows are intentionally not queried.  Clock-invalid records
    are retained in the output ledger with a non-actionable status and no
    trusted first-seen clock, so generic PIT/ranking consumers fail closed.
    """
    output_root = output_root.resolve()
    events_path = output_root / "information_events.jsonl"
    existing_ids = _read_existing_ids(events_path)
    cities = _station_cities(profiles_path)
    conn = sqlite3.connect(f"file:{Path(evidence_db).resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = _load_rows(conn)
    finally:
        conn.close()

    # A COR/update is a revision of the original version in the same source
    # family.  Pre-compute deterministic base IDs before emitting: a correction
    # can reach the collector before the uncorrected report on another path.
    first_by_family: dict[tuple[str, str], str] = {}
    for row in rows:
        if bool(row["is_correction"]):
            continue
        station = str(row["station_id"] or "").upper()
        source, report_class = _source_for(str(row["seen_source_id"]), str(row["report_kind"]))
        payload = _event_payload(row, source=source, report_class=report_class)
        for city, _timezone_name in cities.get(station, ()):
            content_key = _content_key(row, city=city, report_class=report_class)
            family_key = (source, city, str(row["event_family_id"]))
            first_by_family.setdefault(
                family_key,
                information_event_id(
                    event_kind="observation",
                    source=source,
                    station_id=station,
                    provider_item_id=str(row["observation_version_id"]),
                    content_key=content_key,
                    payload_hash=canonical_json_hash(payload),
                ),
            )

    prepared_events: list[dict[str, Any]] = []
    prepared_sources: list[tuple[dict[str, Any], str]] = []
    existing_source_ids_by_day: dict[str, set[str]] = {}
    skipped_unmapped = 0
    skipped_duplicate = 0
    clock_invalid = 0
    for row in rows:
        station = str(row["station_id"] or "").upper()
        mapped_cities = cities.get(station, ())
        if not mapped_cities:
            skipped_unmapped += 1
            continue
        source, report_class = _source_for(str(row["seen_source_id"]), str(row["report_kind"]))
        payload = _event_payload(row, source=source, report_class=report_class)
        valid_clock = bool(row["seen_clock_valid"]) and bool(row["transport_clock_valid"])
        first_seen = _utc_from_ns(row["first_actionable_seen_at_ns"])
        if valid_clock:
            lineage_class = "collector_exact"
            detected_at = first_seen
            first_seen_at = first_seen
            available_at = first_seen
        else:
            # Arrival evidence remains queryable in SQLite, but emitting it as
            # an exact external clock would manufacture a ranking-quality PIT
            # timestamp.  Mark it non-actionable in the source journal.
            lineage_class = "late_backfill_first_seen_unknown"
            detected_at = _utc_from_ns(row["station_decoded_at_ns"])
            first_seen_at = None
            available_at = detected_at
            clock_invalid += 1

        for city, timezone_name in mapped_cities:
            # City is part of the content key because a station can legitimately
            # serve more than one configured market city.
            content_key = _content_key(row, city=city, report_class=report_class)
            family_key = (source, city, str(row["event_family_id"]))
            prior = first_by_family.get(family_key)
            is_correction = bool(row["is_correction"])
            event = build_information_event(
                event_kind="observation",
                event_role="revision" if is_correction and prior else "new_content",
                source=source,
                city=city,
                station_id=station,
                provider_item_id=str(row["observation_version_id"]),
                content_key=content_key,
                normalized_payload=payload,
                revision_of_event_id=prior if is_correction else None,
                source_event_ts_utc=str(row["observation_time"]),
                detected_at_utc=detected_at,
                first_seen_at_utc=first_seen_at,
                available_at_utc=available_at,
                pit_lineage_class=lineage_class,
                original_first_seen_unknown=not valid_clock,
                raw_source_path=str(row["raw_payload_path"]),
                raw_row_hash=str(row["raw_payload_sha256"]),
            )
            try:
                target_date = datetime.fromisoformat(str(row["observation_time"]).replace("Z", "+00:00")).astimezone(
                    ZoneInfo(timezone_name)
                ).date().isoformat()
            except (ValueError, TypeError):
                # The lab parser has already accepted the observation clock;
                # this is a defensive fallback for an invalid profile timezone.
                target_date = str(row["observation_time"])[:10]
            source_row = {
                **event,
                "status": "ok" if valid_clock else "clock_invalid_non_actionable",
                "source": source,
                "source_class": source,
                "report_class": report_class,
                "report_kind": row["report_kind"],
                "is_correction": bool(row["is_correction"]),
                "correction_marker": row.get("correction_marker"),
                "city": city,
                "station": station,
                "target_date": target_date,
                "temp_c": row.get("air_temperature_c"),
                "source_report_ts_utc": row["observation_time"],
                "local_detect_ts_utc": detected_at if valid_clock else None,
                "source_first_seen_at_utc": first_seen_at,
                "transport_received_at_utc": _utc_from_ns(row["received_wall_ns"]),
                "transport_received_monotonic_ns": row["received_monotonic_ns"],
                "clock_valid": valid_clock,
                "clock_offset_ms": row.get("seen_clock_offset_ms"),
                "pit_eligible": valid_clock,
                "pit_exclusion_reason": "clock_invalid" if not valid_clock else "",
                "observation_version_id": row["observation_version_id"],
                "event_family_id": row["event_family_id"],
                "semantic_version_id": row["semantic_version_id"],
                "raw_report_id": row.get("raw_report_id"),
                "source_observation_seen_id": row["source_seen_id"],
                "transport_message_id": row["transport_message_id"],
                "raw_payload_hash": row["raw_payload_sha256"],
                "raw_source_path": row["raw_payload_path"],
                "source_event_materializer": "us_fast_weather_lab.lab_to_source_events.v1",
            }
            event_id = str(event["information_event_id"])
            source_path = output_root / target_date / "sources.jsonl"
            source_ids = existing_source_ids_by_day.setdefault(
                target_date, _read_existing_ids(source_path)
            )
            event_exists = event_id in existing_ids
            source_exists = event_id in source_ids
            if event_exists and source_exists:
                skipped_duplicate += 1
                continue
            if not event_exists:
                prepared_events.append(event)
                existing_ids.add(event_id)
            if not source_exists:
                prepared_sources.append((source_row, target_date))
                source_ids.add(event_id)

    appended_events = _append_jsonl(events_path, prepared_events)
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source_row, target_date in prepared_sources:
        by_day[target_date].append(source_row)
    appended_sources = sum(
        _append_jsonl(output_root / day / "sources.jsonl", source_rows)
        for day, source_rows in by_day.items()
    )
    return {
        "input_actionable_rows": len(rows),
        "appended_information_events": appended_events,
        "appended_source_events": appended_sources,
        "skipped_duplicate_events": skipped_duplicate,
        "skipped_unmapped_station_rows": skipped_unmapped,
        "clock_invalid_rows": clock_invalid,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-db", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--profiles", type=Path, default=DEFAULT_SOURCE_PROFILES_JSON)
    args = parser.parse_args()
    print(json.dumps(materialize_lab_events(args.evidence_db, args.output_root, profiles_path=args.profiles), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
