#!/usr/bin/env python3
"""Materialize immutable weather information events and typed payload links."""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Iterator, Mapping
from datetime import datetime, timezone
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.etl.build_weather_signal_candidates import FORECAST_CURVE_DDL
from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.db.first_seen_schema import apply_first_seen_schema
from weather_dashboard.ingest.information_events import ingest_information_events
from weather_data_feed.information_events import (
    build_information_event,
    canonical_json_hash,
    normalized_observation_payload,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _files(paths: Iterable[Path], *, allow_missing: bool) -> Iterator[Path]:
    for path in paths:
        if path.is_file():
            yield path
            continue
        if path.is_dir():
            files = sorted(path.rglob("*.jsonl"))
            if not files and not allow_missing:
                raise FileNotFoundError(f"no JSONL files under required path: {path}")
            yield from files
            continue
        if not allow_missing:
            raise FileNotFoundError(f"required raw path does not exist: {path}")


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if isinstance(value, dict):
                yield value


def _legacy_observation_event(row: Mapping[str, Any], path: Path) -> dict[str, Any] | None:
    if row.get("status") != "ok" or not row.get("source_report_ts_utc"):
        return None
    available = (
        row.get("available_at_utc")
        or row.get("local_detect_ts_utc")
        or row.get("source_fetch_end_utc")
        or row.get("ts_utc")
    )
    if not available:
        return None
    station = str(row.get("station") or "")
    report_ts = str(row.get("source_report_ts_utc") or "")
    content_key = "|".join(str(value or "") for value in (row.get("city"), station, report_ts))
    return build_information_event(
        event_kind="observation",
        event_role="new_content",
        source=str(row.get("source") or ""),
        city=str(row.get("city") or ""),
        station_id=station or None,
        provider_item_id=str(row.get("provider_item_id") or report_ts) or None,
        content_key=content_key,
        normalized_payload=normalized_observation_payload(row),
        source_event_ts_utc=report_ts,
        detected_at_utc=str(row.get("local_detect_ts_utc") or available),
        first_seen_at_utc=None,
        available_at_utc=str(available),
        pit_lineage_class="archive_known_available",
        raw_source_path=str(path),
        raw_row_hash=str(row.get("payload_hash") or canonical_json_hash(row)),
    )


def _legacy_forecast_event(row: Mapping[str, Any], path: Path) -> dict[str, Any] | None:
    if not row.get("forecast_values_hash") or not isinstance(row.get("hourly_curve"), list):
        return None
    available = row.get("available_at_utc") or row.get("forecast_first_seen_utc")
    if not available:
        return None
    content_key = "|".join(
        str(row.get(field) or "")
        for field in (
            "city",
            "target_date",
            "forecast_source",
            "forecast_model",
            "forecast_values_hash",
        )
    )
    return build_information_event(
        event_kind="forecast_curve",
        event_role="new_content",
        source=str(row.get("forecast_source") or ""),
        city=str(row.get("city") or ""),
        station_id=None,
        provider_item_id=str(row.get("forecast_run_ts_utc") or "") or None,
        content_key=content_key,
        normalized_payload={
            "forecast_values_hash": row["forecast_values_hash"],
            "hourly_curve": row["hourly_curve"],
        },
        source_event_ts_utc=row.get("forecast_run_ts_utc"),
        detected_at_utc=str(row.get("forecast_detected_at_utc") or available),
        first_seen_at_utc=None,
        available_at_utc=str(available),
        pit_lineage_class="archive_known_available",
        raw_source_path=str(path),
        raw_row_hash=canonical_json_hash(row),
    )


def _legacy_high_frequency_event(
    row: Mapping[str, Any], path: Path
) -> dict[str, Any] | None:
    if row.get("source_status") not in {"ok", "cadence_preserved"}:
        return None
    observation_time = str(row.get("observation_time_utc") or "")
    city = str(row.get("city") or "")
    station = str(row.get("station") or "")
    if not observation_time or not city or not station:
        return None
    exact_first_seen = str(row.get("source_first_seen_at_utc") or "")
    detected = str(
        exact_first_seen
        or row.get("local_detect_ts_utc")
        or row.get("fetched_at_utc")
        or ""
    )
    available = str(
        row.get("source_published_at_utc")
        or row.get("fetched_at_utc")
        or detected
        or ""
    )
    if not available:
        return None
    is_late = not bool(exact_first_seen)
    content_key = "|".join((city, station, observation_time))
    return build_information_event(
        event_kind="observation",
        event_role="new_content",
        source=str(row.get("source") or ""),
        city=city,
        station_id=station,
        provider_item_id=observation_time,
        content_key=content_key,
        normalized_payload=normalized_observation_payload(row),
        source_event_ts_utc=observation_time,
        detected_at_utc=detected or available,
        first_seen_at_utc=None if is_late else exact_first_seen,
        available_at_utc=available,
        pit_lineage_class=(
            "late_backfill_first_seen_unknown" if is_late else "collector_exact"
        ),
        original_first_seen_unknown=is_late,
        raw_source_path=str(path),
        raw_row_hash=str(row.get("payload_hash") or canonical_json_hash(row)),
    )


def _events_from_row(row: Mapping[str, Any], path: Path) -> Iterator[tuple[dict[str, Any], Mapping[str, Any]]]:
    if row.get("information_event_id"):
        event = dict(row)
        event.setdefault("material_state_change", row.get("information_event_status") == "material")
        yield event, row
    else:
        legacy = (
            _legacy_forecast_event(row, path)
            or _legacy_observation_event(row, path)
            or _legacy_high_frequency_event(row, path)
        )
        if legacy is not None:
            yield legacy, row

    taf = row.get("taf")
    if isinstance(taf, Mapping):
        event = taf.get("information_event")
        if isinstance(event, Mapping) and event.get("information_event_id"):
            typed = dict(event)
            typed.setdefault("material_state_change", taf.get("information_event_status") == "material")
            yield typed, taf


def _f_to_c(value: Any) -> float | None:
    try:
        return (float(value) - 32.0) * 5.0 / 9.0
    except (TypeError, ValueError):
        return None


def _c_to_f(value: Any) -> float | None:
    try:
        return float(value) * 9.0 / 5.0 + 32.0
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _insert_observation_payload(
    conn: sqlite3.Connection,
    event: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> int:
    if event.get("event_kind") != "observation":
        return 0
    city = str(event.get("city") or raw.get("city") or "")
    station = str(event.get("station_id") or raw.get("station") or "")
    target_date = str(raw.get("target_date") or "")
    obs_ts = str(event.get("source_event_ts_utc") or raw.get("source_report_ts_utc") or "")
    if not city or not station or not target_date or not obs_ts:
        return 0
    temp_c = _float(raw.get("temp_c") or raw.get("current_temp_c"))
    temp_f = _float(raw.get("temp_f") or raw.get("tmpf"))
    if temp_c is None:
        temp_c = _f_to_c(temp_f)
    if temp_f is None:
        temp_f = _c_to_f(temp_c)
    dewpoint_f = _float(raw.get("dewpoint_f") or raw.get("dwpf"))
    if dewpoint_f is None:
        dewpoint_f = _c_to_f(_float(raw.get("dewpoint_c")))
    row_hash = str(event.get("raw_row_hash") or event["payload_hash"])
    observation_id = canonical_json_hash(
        {"table": "weather_observation_events", "information_event_id": event["information_event_id"]}
    )
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO weather_observation_events (
            observation_id, source_system, source_path, source_row_hash,
            city, icao, timezone, target_date, obs_ts_utc, fetched_at_utc,
            source_report_ts_utc, unit, temp_f, temp_c, dewpoint_f,
            wind_speed_kt, wind_dir_deg, sky_cover, raw_payload,
            information_event_id, available_at_utc, pit_lineage_class
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            observation_id,
            event.get("source"),
            event.get("raw_source_path"),
            row_hash,
            city,
            station,
            raw.get("timezone"),
            target_date,
            obs_ts,
            event.get("detected_at_utc"),
            obs_ts,
            raw.get("unit"),
            temp_f,
            temp_c,
            dewpoint_f,
            _float(raw.get("wind_speed_kt") or raw.get("sknt")),
            _float(raw.get("wind_dir_deg") or raw.get("drct")),
            raw.get("sky_cover") or raw.get("sky_cover_code"),
            json.dumps(dict(raw), ensure_ascii=False, sort_keys=True),
            event.get("information_event_id"),
            event.get("available_at_utc"),
            event.get("pit_lineage_class"),
        ),
    )
    return int(cursor.rowcount or 0)


def _insert_forecast_payload(
    conn: sqlite3.Connection,
    event: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> int:
    if event.get("event_kind") != "forecast_curve":
        return 0
    curve_id = canonical_json_hash(
        {"table": "fact_forecast_hourly_curves", "information_event_id": event["information_event_id"]}
    )
    curve = raw.get("hourly_curve")
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO fact_forecast_hourly_curves (
            curve_id, snapshot_ts_utc, city, target_date, forecast_source,
            forecast_model, forecast_values_hash, forecast_max_f,
            forecast_peak_hour_local, forecast_peak_time_local,
            forecast_peak_hour_utc, forecast_peak_time_utc,
            forecast_hourly_count, forecast_timezone,
            forecast_timezone_abbreviation, forecast_utc_offset_seconds,
            forecast_generationtime_ms, hourly_curve_json, source_file,
            fact_built_at_utc, information_event_id, available_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            curve_id,
            raw.get("snapshot_ts_utc"),
            raw.get("city"),
            raw.get("target_date"),
            raw.get("forecast_source"),
            raw.get("forecast_model"),
            raw.get("forecast_values_hash"),
            _float(raw.get("forecast_max_f")),
            raw.get("forecast_peak_hour_local"),
            raw.get("forecast_peak_time_local"),
            raw.get("forecast_peak_hour_utc"),
            raw.get("forecast_peak_time_utc"),
            raw.get("forecast_hourly_count"),
            raw.get("forecast_timezone"),
            raw.get("forecast_timezone_abbreviation"),
            raw.get("forecast_utc_offset_seconds"),
            raw.get("forecast_generationtime_ms"),
            json.dumps(curve or [], ensure_ascii=False, sort_keys=True),
            event.get("raw_source_path"),
            _utc_now(),
            event.get("information_event_id"),
            event.get("available_at_utc"),
        ),
    )
    return int(cursor.rowcount or 0)


def materialize(
    conn: sqlite3.Connection,
    paths: Iterable[Path],
    *,
    allow_missing: bool,
    batch_size: int = 1_000,
) -> dict[str, int]:
    rows = (
        (row, path)
        for path in _files(paths, allow_missing=allow_missing)
        for row in iter_jsonl(path)
    )
    return materialize_rows(conn, rows, batch_size=batch_size)


def materialize_rows(
    conn: sqlite3.Connection,
    rows: Iterable[tuple[Mapping[str, Any], Path]],
    *,
    batch_size: int = 1_000,
) -> dict[str, int]:
    counters = {
        "raw_rows": 0,
        "events_seen": 0,
        "events_inserted": 0,
        "event_duplicates": 0,
        "observation_payloads_inserted": 0,
        "forecast_payloads_inserted": 0,
    }
    batch: list[tuple[dict[str, Any], Mapping[str, Any]]] = []

    def flush() -> None:
        if not batch:
            return
        result = ingest_information_events(conn, [event for event, _raw in batch])
        counters["events_inserted"] += result["inserted"]
        counters["event_duplicates"] += result["duplicates"]
        for event, raw in batch:
            counters["observation_payloads_inserted"] += _insert_observation_payload(conn, event, raw)
            counters["forecast_payloads_inserted"] += _insert_forecast_payload(conn, event, raw)
        conn.commit()
        batch.clear()

    for row, path in rows:
        counters["raw_rows"] += 1
        for event, raw in _events_from_row(row, path):
            counters["events_seen"] += 1
            batch.append((event, raw))
            if len(batch) >= batch_size:
                flush()
    flush()
    return counters


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(ROOT / "runtime/weather.db"))
    parser.add_argument("--source-events", action="append", default=[])
    parser.add_argument("--high-frequency-observations", action="append", default=[])
    parser.add_argument("--forecast-curves", action="append", default=[])
    parser.add_argument("--forecast-enrichment", action="append", default=[])
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--batch-size", type=int, default=1_000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = [
        Path(value)
        for value in [
            *args.source_events,
            *args.high_frequency_observations,
            *args.forecast_curves,
            *args.forecast_enrichment,
        ]
    ]
    if not paths:
        raise ValueError("at least one raw input path is required")
    conn = sqlite3.connect(args.db)
    try:
        apply_schema_canonical(conn)
        conn.execute(FORECAST_CURVE_DDL)
        apply_first_seen_schema(conn)
        result = materialize(
            conn,
            paths,
            allow_missing=bool(args.allow_missing),
            batch_size=max(1, int(args.batch_size)),
        )
    finally:
        conn.close()
    print(json.dumps({"paths": [str(path) for path in paths], **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
