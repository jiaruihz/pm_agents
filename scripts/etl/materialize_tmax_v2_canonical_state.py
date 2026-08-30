#!/usr/bin/env python3
"""Build append-only, point-in-time Tmax V2 canonical state.

The materializer deliberately treats a paper snapshot as the boundary of an
absolute market ladder.  It never fills a missing rung from another snapshot,
and it never selects an observation or forecast whose recorded availability is
after the ladder's decision timestamp.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from src.strategies.runtime.production import load_production_spec
from weather_clock_contract import local_wall_time_to_utc, parse_utc_or_none


_PRODUCTION = load_production_spec()
DEFAULT_DB = _PRODUCTION.canonical_db_path
DEFAULT_SNAPSHOT_DIR = _PRODUCTION.strategy_paper_snapshot_dir()
DEFAULT_FORECAST_CURVE_DIR = _PRODUCTION.forecast_hourly_curve_dir()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--snapshot-dir", default=str(DEFAULT_SNAPSHOT_DIR))
    parser.add_argument("--forecast-curve-dir", default=str(DEFAULT_FORECAST_CURVE_DIR))
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument(
        "--source-capture-date",
        default="",
        help="Optional YYYY-MM-DD filter for timestamped snapshot/curve filenames; useful for a current mirror pass.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--summary-json", default="")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: Any) -> datetime | None:
    return parse_utc_or_none(value, field="tmax_v2_clock")


def canonical_ts(value: Any) -> str | None:
    parsed = parse_utc(value)
    if parsed is None:
        return None
    timespec = "microseconds" if parsed.microsecond else "seconds"
    return parsed.isoformat(timespec=timespec).replace("+00:00", "Z")


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def text_value(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def integer(value: Any) -> int | None:
    parsed = number(value)
    return int(parsed) if parsed is not None else None


def consistent_text(records: list[dict[str, Any]], field: str) -> tuple[str | None, str | None]:
    values = {value for record in records if (value := text_value(record.get(field))) is not None}
    if len(values) == 1:
        return next(iter(values)), None
    if not values:
        return None, f"missing_{field}"
    return None, f"inconsistent_{field}"


def ladder_market_metadata(records: list[dict[str, Any]]) -> dict[str, Any]:
    unit, unit_reason = consistent_text(records, "unit")
    if unit is not None:
        unit = unit.upper()
        if unit not in {"C", "F"}:
            unit_reason, unit = "invalid_unit", None
    settlement_source, settlement_reason = consistent_text(records, "settlement_source_class")
    market_timezone, timezone_reason = consistent_text(records, "timezone_name")
    offsets = {
        value
        for record in records
        if (value := integer(record.get("forecast_utc_offset_seconds"))) is not None
    }
    if len(offsets) == 1:
        utc_offset, offset_reason = next(iter(offsets)), None
    elif not offsets:
        utc_offset, offset_reason = None, "missing_forecast_utc_offset_seconds"
    else:
        utc_offset, offset_reason = None, "inconsistent_forecast_utc_offset_seconds"
    reasons = [reason for reason in (unit_reason, settlement_reason, timezone_reason, offset_reason) if reason]
    return {
        "market_unit": unit,
        "settlement_source_class": settlement_source,
        "market_timezone": market_timezone,
        "market_utc_offset_seconds": utc_offset,
        "market_metadata_source_json": json.dumps(
            {
                "market_unit": "snapshot_record.unit",
                "settlement_source_class": "snapshot_record.settlement_source_class",
                "market_timezone": "snapshot_record.timezone_name",
                "market_utc_offset_seconds": "snapshot_record.forecast_utc_offset_seconds",
            },
            sort_keys=True,
        ),
        "market_metadata_missing_reason": ";".join(reasons) or None,
    }


def normalize_hourly_curve_times(
    curve: list[dict[str, Any]], forecast_timezone: str | None, utc_offset_seconds: int | None
) -> tuple[list[dict[str, Any]], str, str | None]:
    timezone_name = None
    basis = None
    missing_reasons: list[str] = []
    if forecast_timezone:
        try:
            ZoneInfo(forecast_timezone)
            timezone_name = forecast_timezone
            basis = "source_time_local_plus_iana_forecast_timezone"
        except ZoneInfoNotFoundError:
            missing_reasons.append("invalid_forecast_timezone")
    if timezone_name is None:
        missing_reasons.append("missing_iana_forecast_timezone")
        missing_reasons.append(
            "missing_timezone_and_utc_offset"
            if utc_offset_seconds is None
            else "fixed_offset_not_accepted_without_iana_timezone"
        )

    normalized: list[dict[str, Any]] = []
    utc_count = 0
    for point in curve:
        output = dict(point)
        local_value = text_value(point.get("valid_time_local") or point.get("time_local"))
        output["valid_time_local"] = local_value
        output["valid_time_utc"] = None
        if local_value and timezone_name is not None:
            try:
                parsed_utc = parse_utc_or_none(
                    local_value, field="forecast_curve_valid_time"
                )
                if parsed_utc is None:
                    parsed_utc = local_wall_time_to_utc(
                        local_value,
                        timezone_name=timezone_name,
                        field="forecast_curve_valid_time_local",
                    )
                output["valid_time_utc"] = canonical_ts(parsed_utc)
                utc_count += int(output["valid_time_utc"] is not None)
            except ValueError:
                missing_reasons.append("invalid_curve_local_time")
        elif not local_value:
            missing_reasons.append("missing_curve_local_time")
        output["valid_time_basis"] = basis
        normalized.append(output)
    if normalized and utc_count == len(normalized):
        status = "local_and_utc_verified"
    elif utc_count:
        status = "partial_utc_conversion"
    else:
        status = "local_only_missing_timezone_lineage"
    return normalized, status, ";".join(sorted(set(missing_reasons))) or None


def in_date_range(target_date: str, start: str, end: str) -> bool:
    return (not start or target_date >= start) and (not end or target_date <= end)


def path_matches_capture_date(path: Path, capture_date: str) -> bool:
    if not capture_date:
        return True
    match = re.search(r"(20\d{6})", path.name)
    if match is None:
        return True
    return match.group(1) == capture_date.replace("-", "")


def snapshot_paths(root: Path, capture_date: str = "") -> Iterable[Path]:
    if not root.exists():
        return []
    return (
        path
        for path in sorted(root.rglob("*.json"))
        if "rejected_incomplete" not in path.parts
        and "paper_snapshots_partial" not in path.parts
        and path_matches_capture_date(path, capture_date)
    )


def quote_values(record: dict[str, Any], side: str) -> dict[str, Any]:
    prefix = side.lower()
    return {
        f"{prefix}_token_id": text_value(record.get(f"{prefix}_token_id")),
        f"{prefix}_direct_bid": number(record.get(f"{prefix}_best_bid")),
        f"{prefix}_direct_ask": number(record.get(f"{prefix}_best_ask")),
        f"{prefix}_direct_bid_size": number(record.get(f"{prefix}_bid_size")),
        f"{prefix}_direct_ask_size": number(record.get(f"{prefix}_ask_size")),
        f"{prefix}_direct_depth_bid_5c": number(record.get(f"{prefix}_depth_bid_5c")),
        f"{prefix}_direct_depth_ask_5c": number(record.get(f"{prefix}_depth_ask_5c")),
        f"{prefix}_direct_depth_bid_10c": number(record.get(f"{prefix}_depth_bid_10c")),
        f"{prefix}_direct_depth_ask_10c": number(record.get(f"{prefix}_depth_ask_10c")),
        f"{prefix}_book_status": text_value(record.get(f"{prefix}_book_status")),
        f"{prefix}_book_fetched_at_utc": canonical_ts(record.get(f"{prefix}_book_fetched_at_utc")),
    }


def direct_rung_complete(record: dict[str, Any]) -> bool:
    # Ladder completeness is about identity and capture boundary, not whether
    # every market was executable.  A direct field may be null because the
    # captured book was empty; replacing it with a later quote would violate
    # PIT.  Preserve that null in the rung table and let downstream research
    # reason about quote quality separately.
    identity_ok = all(text_value(record.get(field)) for field in ("bracket", "condition_id", "market_id"))
    direct_fields = (
        "token_id", "best_bid", "best_ask", "bid_size", "ask_size",
        "depth_bid_5c", "depth_ask_5c", "depth_bid_10c", "depth_ask_10c", "book_status",
    )
    return identity_ok and all(
        f"{side}_{field}" in record
        for side in ("yes", "no")
        for field in direct_fields
    )


def iter_ladder_rows(
    snapshot_dir: Path, start: str, end: str, capture_date: str = ""
) -> Iterable[tuple[dict[str, Any], list[dict[str, Any]]]]:
    for path in snapshot_paths(snapshot_dir, capture_date):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        snapshot_ts = canonical_ts(payload.get("ts_utc"))
        records = payload.get("records")
        if snapshot_ts is None or not isinstance(records, list):
            continue
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            if not isinstance(record, dict):
                continue
            city = text_value(record.get("city"))
            target_date = text_value(record.get("target_date") or record.get("event_date"))
            bracket = text_value(record.get("bracket"))
            if not city or not target_date or not bracket or not in_date_range(target_date, start, end):
                continue
            event_identity = text_value(record.get("event_slug")) or f"{city}|{target_date}"
            groups[(city, target_date, event_identity)].append(record)
        for (city, target_date, event_identity), group in groups.items():
            yield (
                {
                    "source_system": "weather_data_feed_paper_snapshot",
                    "source_path": str(path),
                    "source_snapshot_ts_utc": snapshot_ts,
                    "available_at_utc": snapshot_ts,
                    "city": city,
                    "target_date": target_date,
                    "event_slug": text_value(group[0].get("event_slug")),
                    "event_identity": event_identity,
                },
                group,
            )


def make_ladder_payload(meta: dict[str, Any], records: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_bracket: dict[str, dict[str, Any]] = {}
    duplicate = False
    for record in records:
        bracket = str(record["bracket"]).strip()
        if bracket in by_bracket:
            duplicate = True
        else:
            by_bracket[bracket] = record
    rung_records = [by_bracket[key] for key in sorted(by_bracket)]
    signature_rows = [
        {
            "absolute_bracket_identity": str(record["bracket"]).strip(),
            "condition_id": text_value(record.get("condition_id")),
            "market_id": text_value(record.get("market_id")),
        }
        for record in rung_records
    ]
    source_payload_hash = sha256_json(
        {"meta": meta, "rungs": [{"identity": row, "record": record} for row, record in zip(signature_rows, rung_records)]}
    )
    complete_rungs = sum(direct_rung_complete(record) for record in rung_records)
    status = "invalid" if duplicate or not rung_records else (
        "complete" if complete_rungs == len(rung_records) else "incomplete"
    )
    snapshot_id = sha256_json(
        {
            "table": "tmax_v2_ladder_snapshots",
            "source_payload_hash": source_payload_hash,
            "city": meta["city"],
            "target_date": meta["target_date"],
            "event_identity": meta["event_identity"],
        }
    )
    snapshot = {
        "ladder_snapshot_id": snapshot_id,
        **meta,
        **ladder_market_metadata(rung_records),
        "source_payload_hash": source_payload_hash,
        "absolute_ladder_signature": sha256_json(signature_rows),
        "rung_count": len(rung_records),
        "complete_rung_count": complete_rungs,
        "completeness_status": status,
        "lineage_status": "pit_verified_capture",
    }
    rungs = []
    for record in rung_records:
        bracket = str(record["bracket"]).strip()
        source_record_hash = sha256_json(record)
        rungs.append(
            {
                "rung_quote_id": sha256_json(
                    {"table": "tmax_v2_ladder_rung_quotes", "snapshot": snapshot_id, "bracket": bracket}
                ),
                "ladder_snapshot_id": snapshot_id,
                "absolute_bracket_identity": bracket,
                "condition_id": text_value(record.get("condition_id")),
                "market_id": text_value(record.get("market_id")),
                "question": text_value(record.get("question")),
                **quote_values(record, "yes"),
                **quote_values(record, "no"),
                "source_record_hash": source_record_hash,
            }
        )
    return snapshot, rungs


def embedded_snapshot_observation_rows(
    ladder_snapshot: dict[str, Any], records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Emit observations proven usable by this exact paper-snapshot capture.

    These are not archive observations and they do not infer a forecast curve.
    The producer included the METAR value in the same snapshot whose timestamp
    is the decision boundary, so that capture time is the requested local
    first-seen and availability time for this lineage record.
    """

    snapshot_ts = str(ladder_snapshot["source_snapshot_ts_utc"])
    snapshot_dt = parse_utc(snapshot_ts)
    seen: set[tuple[str, float, str, str]] = set()
    rows: list[dict[str, Any]] = []
    for record in records:
        obs_ts = canonical_ts(record.get("metar_latest_ts_utc"))
        temp_f = number(record.get("metar_latest_temp_f"))
        icao = text_value(record.get("metar_icao"))
        source = text_value(record.get("live_observation_source"))
        obs_dt = parse_utc(obs_ts)
        if obs_ts is None or temp_f is None or icao is None or source is None or obs_dt is None:
            continue
        if snapshot_dt is None or obs_dt > snapshot_dt:
            continue
        key = (obs_ts, temp_f, icao, source)
        if key in seen:
            continue
        seen.add(key)
        source_observation_id = sha256_json(
            {
                "kind": "embedded_snapshot_capture",
                "city": ladder_snapshot["city"],
                "metar_latest_ts_utc": obs_ts,
                "metar_latest_temp_f": temp_f,
                "metar_icao": icao,
                "live_observation_source": source,
            }
        )
        rows.append(
            {
                "tmax_v2_observation_id": sha256_json(
                    {
                        "table": "tmax_v2_observation_event_lineage",
                        "source": source_observation_id,
                        "status": "pit_verified_first_seen",
                        "first_seen": snapshot_ts,
                    }
                ),
                "source_observation_id": source_observation_id,
                "source_system": source,
                "source_path": str(ladder_snapshot["source_path"]),
                "city": str(ladder_snapshot["city"]),
                "target_date": str(ladder_snapshot["target_date"]),
                "obs_ts_utc": obs_ts,
                "temp_f": temp_f,
                "first_seen_at_utc": snapshot_ts,
                "available_at_utc": snapshot_ts,
                "source_kind": "embedded_snapshot_capture",
                "station_id": icao,
                "icao": icao,
                "feed_identity": source,
                "identity_missing_reason": None,
                "lineage_status": "pit_verified_first_seen",
            }
        )
    return rows


def dedupe_embedded_snapshot_observations(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the earliest captured first-seen for each exact observation value."""

    earliest: dict[str, dict[str, Any]] = {}
    for row in rows:
        source_id = str(row["source_observation_id"])
        current = earliest.get(source_id)
        if current is None or str(row["available_at_utc"]) < str(current["available_at_utc"]):
            earliest[source_id] = row
    return [earliest[source_id] for source_id in sorted(earliest)]


def iter_forecast_captures(
    root: Path, start: str, end: str, capture_date: str = ""
) -> Iterable[dict[str, Any]]:
    if not root.exists():
        return []
    for path in sorted(root.rglob("*.jsonl")):
        if not path_matches_capture_date(path, capture_date):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict) or not isinstance(raw.get("hourly_curve"), list) or not raw["hourly_curve"]:
                continue
            city = text_value(raw.get("city"))
            target_date = text_value(raw.get("target_date"))
            if not city or not target_date or not in_date_range(target_date, start, end):
                continue
            snapshot_ts = canonical_ts(raw.get("snapshot_ts_utc"))
            available_at = canonical_ts(raw.get("available_at_utc"))
            forecast_timezone = text_value(raw.get("forecast_timezone"))
            forecast_utc_offset_seconds = integer(raw.get("forecast_utc_offset_seconds"))
            normalized_curve, curve_time_status, curve_time_reason = normalize_hourly_curve_times(
                raw["hourly_curve"], forecast_timezone, forecast_utc_offset_seconds
            )
            row_hash = sha256_json(raw)
            lineage_status = "pit_verified_capture" if available_at else "research_only_unknown_available_at"
            yield {
                "forecast_capture_id": sha256_json(
                    {"table": "tmax_v2_forecast_captures", "source_path": str(path), "source_row_hash": row_hash}
                ),
                "source_system": "weather_data_feed_forecast_hourly_curve",
                "source_path": str(path),
                "source_row_hash": row_hash,
                "snapshot_ts_utc": snapshot_ts,
                "available_at_utc": available_at,
                "city": city,
                "target_date": target_date,
                "forecast_source": text_value(raw.get("forecast_source")),
                "forecast_model": text_value(raw.get("forecast_model")),
                "forecast_values_hash": text_value(raw.get("forecast_values_hash")),
                "hourly_curve_json": json.dumps(raw["hourly_curve"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "normalized_hourly_curve_json": json.dumps(
                    normalized_curve, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
                # Only explicit source fields count. Estimated init/run fields
                # and the snapshot timestamp are not forecast-run lineage.
                "forecast_run_at_utc": (
                    canonical_ts(raw.get("forecast_run_at_utc"))
                    or canonical_ts(raw.get("forecast_run_ts_utc"))
                ),
                "forecast_timezone": forecast_timezone,
                "forecast_utc_offset_seconds": forecast_utc_offset_seconds,
                "available_at_basis": text_value(raw.get("available_at_basis")),
                "forecast_first_seen_at_utc": canonical_ts(raw.get("forecast_first_seen_utc")),
                "forecast_first_seen_basis": text_value(raw.get("forecast_first_seen_basis")),
                "forecast_first_seen_source": text_value(raw.get("forecast_first_seen_source")),
                "curve_time_lineage_status": curve_time_status,
                "curve_time_missing_reason": curve_time_reason,
                "lineage_status": lineage_status,
            }


def observation_lineage_rows(conn: sqlite3.Connection, start: str, end: str) -> list[dict[str, Any]]:
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(weather_observation_events)")}
    optional = {name: name in columns for name in ("first_seen_at_utc", "available_at_utc", "source_kind")}
    selected = [
        "observation_id", "source_system", "source_path", "city", "target_date", "obs_ts_utc", "temp_f", "icao",
        *(name for name, exists in optional.items() if exists),
    ]
    query = f"SELECT {', '.join(selected)} FROM weather_observation_events"
    raw_rows = conn.execute(query).fetchall()
    out = []
    for raw in raw_rows:
        row = dict(raw)
        target_date = str(row["target_date"])
        if not in_date_range(target_date, start, end):
            continue
        first_seen = canonical_ts(row.get("first_seen_at_utc"))
        available = canonical_ts(row.get("available_at_utc")) or first_seen
        verified = first_seen is not None and available is not None
        status = "pit_verified_first_seen" if verified else "research_only_unknown_first_seen"
        source_id = str(row["observation_id"])
        out.append(
            {
                "tmax_v2_observation_id": sha256_json(
                    {"table": "tmax_v2_observation_event_lineage", "source": source_id, "status": status, "first_seen": first_seen}
                ),
                "source_observation_id": source_id,
                "source_system": str(row["source_system"]),
                "source_path": text_value(row.get("source_path")),
                "city": str(row["city"]),
                "target_date": target_date,
                "obs_ts_utc": canonical_ts(row["obs_ts_utc"]) or str(row["obs_ts_utc"]),
                "temp_f": number(row.get("temp_f")),
                "first_seen_at_utc": first_seen,
                "available_at_utc": available,
                "source_kind": text_value(row.get("source_kind")) or "archive_compat",
                "station_id": text_value(row.get("icao")),
                "icao": text_value(row.get("icao")),
                "feed_identity": str(row["source_system"]),
                "identity_missing_reason": None if text_value(row.get("icao")) else "missing_icao",
                "lineage_status": status,
            }
        )
    return out


def ladder_metadata_rows(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    fields = (
        "market_unit",
        "settlement_source_class",
        "market_timezone",
        "market_utc_offset_seconds",
        "market_metadata_source_json",
        "market_metadata_missing_reason",
    )
    for snapshot in snapshots:
        values = {field: snapshot.get(field) for field in fields}
        fingerprint = sha256_json(values)
        rows.append(
            {
                "ladder_metadata_id": sha256_json(
                    {"table": "tmax_v2_ladder_snapshot_metadata", "snapshot": snapshot["ladder_snapshot_id"], "fingerprint": fingerprint}
                ),
                "ladder_snapshot_id": snapshot["ladder_snapshot_id"],
                "metadata_fingerprint": fingerprint,
                **values,
            }
        )
    return rows


def rung_metadata_rows(rungs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for rung in rungs:
        values = {
            "question": rung.get("question"),
            "question_source": "snapshot_record.question" if rung.get("question") else None,
            "question_missing_reason": None if rung.get("question") else "missing_snapshot_question",
        }
        fingerprint = sha256_json(values)
        rows.append(
            {
                "rung_metadata_id": sha256_json(
                    {"table": "tmax_v2_ladder_rung_metadata", "rung": rung["rung_quote_id"], "fingerprint": fingerprint}
                ),
                "rung_quote_id": rung["rung_quote_id"],
                "metadata_fingerprint": fingerprint,
                **values,
            }
        )
    return rows


def forecast_metadata_rows(forecasts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    fields = (
        "normalized_hourly_curve_json",
        "forecast_timezone",
        "forecast_utc_offset_seconds",
        "available_at_basis",
        "forecast_first_seen_at_utc",
        "forecast_first_seen_basis",
        "forecast_first_seen_source",
        "curve_time_lineage_status",
        "curve_time_missing_reason",
    )
    for forecast in forecasts:
        values = {field: forecast.get(field) for field in fields}
        fingerprint = sha256_json(values)
        rows.append(
            {
                "forecast_metadata_id": sha256_json(
                    {"table": "tmax_v2_forecast_capture_metadata", "forecast": forecast["forecast_capture_id"], "fingerprint": fingerprint}
                ),
                "forecast_capture_id": forecast["forecast_capture_id"],
                "metadata_fingerprint": fingerprint,
                **values,
            }
        )
    return rows


def observation_metadata_rows(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    fields = ("station_id", "icao", "feed_identity", "identity_missing_reason")
    for observation in observations:
        values = {field: observation.get(field) for field in fields}
        fingerprint = sha256_json(values)
        rows.append(
            {
                "observation_metadata_id": sha256_json(
                    {"table": "tmax_v2_observation_identity_metadata", "observation": observation["tmax_v2_observation_id"], "fingerprint": fingerprint}
                ),
                "tmax_v2_observation_id": observation["tmax_v2_observation_id"],
                "metadata_fingerprint": fingerprint,
                **values,
            }
        )
    return rows


def observation_metadata_backfill_inputs(
    conn: sqlite3.Connection,
    observations: list[dict[str, Any]],
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    """Resolve immutable pre-metadata observation IDs from exact captured values."""

    by_id = {str(row["tmax_v2_observation_id"]): dict(row) for row in observations}
    identities: dict[tuple[str, str, str, float | None], set[tuple[str | None, str | None, str | None]]] = defaultdict(set)
    for row in observations:
        key = (
            str(row["city"]),
            str(row["target_date"]),
            str(row["obs_ts_utc"]),
            number(row.get("temp_f")),
        )
        identities[key].add((row.get("station_id"), row.get("icao"), row.get("feed_identity")))

    existing = conn.execute(
        """
        SELECT tmax_v2_observation_id, city, target_date, obs_ts_utc, temp_f
        FROM tmax_v2_observation_event_lineage
        WHERE (? = '' OR target_date >= ?) AND (? = '' OR target_date <= ?)
        """,
        (start, start, end, end),
    ).fetchall()
    for raw in existing:
        row = dict(raw)
        observation_id = str(row["tmax_v2_observation_id"])
        if observation_id in by_id:
            continue
        key = (str(row["city"]), str(row["target_date"]), str(row["obs_ts_utc"]), number(row.get("temp_f")))
        candidates = identities.get(key, set())
        resolved = dict(row)
        if len(candidates) == 1:
            station_id, icao, feed_identity = next(iter(candidates))
            resolved.update(
                {
                    "station_id": station_id,
                    "icao": icao,
                    "feed_identity": feed_identity,
                    "identity_missing_reason": None,
                }
            )
        else:
            resolved.update(
                {
                    "station_id": None,
                    "icao": None,
                    "feed_identity": None,
                    "identity_missing_reason": (
                        "no_exact_snapshot_identity_match" if not candidates else "ambiguous_exact_snapshot_identity_match"
                    ),
                }
            )
        by_id[observation_id] = resolved
    return [by_id[observation_id] for observation_id in sorted(by_id)]


def insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]], dry_run: bool) -> int:
    if not rows:
        return 0
    cols = list(rows[0])
    placeholders = ",".join("?" for _ in cols)
    statement = f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    if dry_run:
        existing_ids = {
            str(row[0]) for row in conn.execute(f"SELECT {cols[0]} FROM {table}")
        }
        return sum(str(row[cols[0]]) not in existing_ids for row in rows)
    inserted = 0
    for row in rows:
        cur = conn.execute(statement, [row.get(column) for column in cols])
        inserted += int(cur.rowcount or 0)
    return inserted


def selected_observation(conn: sqlite3.Connection, city: str, target_date: str, decision_ts: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        WITH exact_value_first_seen AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY source_observation_id
                ORDER BY available_at_utc ASC, tmax_v2_observation_id ASC
            ) AS exact_value_seen_rank
            FROM tmax_v2_observation_event_lineage
            WHERE city = ? AND target_date = ?
              AND lineage_status = 'pit_verified_first_seen'
              AND available_at_utc <= ? AND obs_ts_utc <= ?
        )
        SELECT * FROM exact_value_first_seen
        WHERE exact_value_seen_rank = 1
        ORDER BY
            obs_ts_utc DESC,
            CASE WHEN source_kind = 'embedded_snapshot_capture' THEN 1 ELSE 0 END DESC,
            available_at_utc DESC,
            tmax_v2_observation_id DESC
        LIMIT 1
        """,
        (city, target_date, decision_ts, decision_ts),
    ).fetchone()


def selected_forecast(conn: sqlite3.Connection, city: str, target_date: str, decision_ts: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM tmax_v2_forecast_captures
        WHERE city = ? AND target_date = ?
          AND lineage_status = 'pit_verified_capture'
          AND available_at_utc <= ?
        ORDER BY available_at_utc DESC, snapshot_ts_utc DESC, forecast_capture_id DESC
        LIMIT 1
        """,
        (city, target_date, decision_ts),
    ).fetchone()


def has_unknown_observation(conn: sqlite3.Connection, city: str, target_date: str, decision_ts: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM tmax_v2_observation_event_lineage
        WHERE city = ? AND target_date = ? AND obs_ts_utc <= ?
          AND lineage_status = 'research_only_unknown_first_seen'
        LIMIT 1
        """,
        (city, target_date, decision_ts),
    ).fetchone()
    return row is not None


def materialize_states(
    conn: sqlite3.Connection, start: str, end: str, dry_run: bool
) -> tuple[dict[str, int], Counter[str]]:
    snapshots = conn.execute(
        """
        SELECT * FROM tmax_v2_ladder_snapshots
        WHERE completeness_status = 'complete' AND lineage_status = 'pit_verified_capture'
        ORDER BY source_snapshot_ts_utc, ladder_snapshot_id
        """
    ).fetchall()
    states: list[dict[str, Any]] = []
    revisions: list[dict[str, Any]] = []
    statuses: Counter[str] = Counter()
    for snapshot in snapshots:
        target_date = str(snapshot["target_date"])
        if not in_date_range(target_date, start, end):
            continue
        decision_ts = str(snapshot["source_snapshot_ts_utc"])
        city = str(snapshot["city"])
        obs = selected_observation(conn, city, target_date, decision_ts)
        forecast = selected_forecast(conn, city, target_date, decision_ts)
        unknown_obs = has_unknown_observation(conn, city, target_date, decision_ts)
        if obs is not None and forecast is not None:
            pit_status = "pit_verified"
        elif obs is None and forecast is None:
            pit_status = "research_only_missing_inputs"
        elif unknown_obs:
            pit_status = "research_only_unknown_observation_first_seen"
        elif forecast is None:
            pit_status = "research_only_unknown_forecast_available_at"
        else:
            pit_status = "research_only_missing_inputs"
        statuses[pit_status] += 1
        state_id = sha256_json(
            {
                "city": city,
                "target_date": target_date,
                "decision_ts_utc": decision_ts,
                "ladder_snapshot_id": snapshot["ladder_snapshot_id"],
            }
        )
        state = {
            "tmax_state_id": state_id,
            "city": city,
            "target_date": target_date,
            "decision_ts_utc": decision_ts,
            "ladder_snapshot_id": snapshot["ladder_snapshot_id"],
            "observation_event_id": obs["tmax_v2_observation_id"] if obs else None,
            "forecast_capture_id": forecast["forecast_capture_id"] if forecast else None,
            "observation_available_at_utc": obs["available_at_utc"] if obs else None,
            "forecast_available_at_utc": forecast["available_at_utc"] if forecast else None,
            "observation_lineage_status": obs["lineage_status"] if obs else (
                "research_only_unknown_first_seen" if unknown_obs else "missing"
            ),
            "forecast_lineage_status": forecast["lineage_status"] if forecast else "missing",
            "pit_status": pit_status,
            "lineage_summary_json": json.dumps(
                {
                    "decision_ts_utc": decision_ts,
                    "ladder_snapshot_id": snapshot["ladder_snapshot_id"],
                    "selection_rule": "available_at_utc <= decision_ts_utc",
                    "unknown_archive_observation_used": False,
                },
                sort_keys=True,
            ),
        }
        states.append(state)
        fingerprint = sha256_json(
            {
                "tmax_state_id": state_id,
                "observation_event_id": state["observation_event_id"],
                "forecast_capture_id": state["forecast_capture_id"],
                "observation_available_at_utc": state["observation_available_at_utc"],
                "forecast_available_at_utc": state["forecast_available_at_utc"],
                "observation_lineage_status": state["observation_lineage_status"],
                "forecast_lineage_status": state["forecast_lineage_status"],
                "pit_status": state["pit_status"],
            }
        )
        revisions.append(
            {
                "tmax_state_revision_id": sha256_json(
                    {"table": "tmax_v2_canonical_state_revisions", "state": state_id, "fingerprint": fingerprint}
                ),
                "tmax_state_id": state_id,
                "lineage_fingerprint": fingerprint,
                **{
                    column: state[column]
                    for column in (
                        "observation_event_id",
                        "forecast_capture_id",
                        "observation_available_at_utc",
                        "forecast_available_at_utc",
                        "observation_lineage_status",
                        "forecast_lineage_status",
                        "pit_status",
                        "lineage_summary_json",
                    )
                },
            }
        )
    # Keep the original v14 state identity materialized for compatibility; all
    # changing input lineages append to the revision table below.
    inserted = {
        "base_states": insert_rows(conn, "tmax_v2_canonical_states", states, dry_run),
        "state_revisions": insert_rows(conn, "tmax_v2_canonical_state_revisions", revisions, dry_run),
    }
    return inserted, statuses


def db_coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS rows, MIN(target_date) AS first_date, MAX(target_date) AS last_date,
               COUNT(DISTINCT city) AS cities
        FROM tmax_v2_canonical_state_effective
        """
    ).fetchone()
    pit = {
        str(item["pit_status"]): int(item["rows"])
        for item in conn.execute(
            "SELECT pit_status, COUNT(*) AS rows FROM tmax_v2_canonical_state_effective GROUP BY pit_status"
        )
    }
    revisions = int(conn.execute("SELECT COUNT(*) FROM tmax_v2_canonical_state_revisions").fetchone()[0])
    return {
        "rows": int(row["rows"]),
        "first_date": row["first_date"],
        "last_date": row["last_date"],
        "cities": int(row["cities"]),
        "pit_status": pit,
        "state_revisions": revisions,
    }


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if args.dry_run:
        source_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        source_conn = sqlite3.connect(db_path)
    source_conn.row_factory = sqlite3.Row
    if args.dry_run:
        # A dry run must not even add schema objects to the target DB.  Work on
        # a backup so its counts include the same staged rows as a real run.
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        source_conn.backup(conn)
        apply_schema_canonical(conn)
    else:
        conn = source_conn
        apply_schema_canonical(conn)
    start, end = str(args.start_date), str(args.end_date)
    snapshots: list[dict[str, Any]] = []
    rungs: list[dict[str, Any]] = []
    embedded_observations: list[dict[str, Any]] = []
    for meta, records in iter_ladder_rows(Path(args.snapshot_dir), start, end, str(args.source_capture_date)):
        snapshot, snapshot_rungs = make_ladder_payload(meta, records)
        snapshots.append(snapshot)
        rungs.extend(snapshot_rungs)
        embedded_observations.extend(embedded_snapshot_observation_rows(snapshot, records))
    forecasts = list(iter_forecast_captures(Path(args.forecast_curve_dir), start, end, str(args.source_capture_date)))
    observations = [
        *observation_lineage_rows(conn, start, end),
        *dedupe_embedded_snapshot_observations(embedded_observations),
    ]
    ladder_metadata = ladder_metadata_rows(snapshots)
    rung_metadata = rung_metadata_rows(rungs)
    forecast_metadata = forecast_metadata_rows(forecasts)
    observation_metadata_inputs = observation_metadata_backfill_inputs(conn, observations, start, end)
    observation_metadata = observation_metadata_rows(observation_metadata_inputs)
    inserted = {
        "ladder_snapshots": insert_rows(conn, "tmax_v2_ladder_snapshots", snapshots, False),
        "ladder_rung_quotes": insert_rows(conn, "tmax_v2_ladder_rung_quotes", rungs, False),
        "forecast_captures": insert_rows(conn, "tmax_v2_forecast_captures", forecasts, False),
        "observation_lineage": insert_rows(conn, "tmax_v2_observation_event_lineage", observations, False),
        "ladder_metadata": insert_rows(conn, "tmax_v2_ladder_snapshot_metadata", ladder_metadata, False),
        "rung_metadata": insert_rows(conn, "tmax_v2_ladder_rung_metadata", rung_metadata, False),
        "forecast_metadata": insert_rows(conn, "tmax_v2_forecast_capture_metadata", forecast_metadata, False),
        "observation_metadata": insert_rows(conn, "tmax_v2_observation_identity_metadata", observation_metadata, False),
    }
    if not args.dry_run:
        conn.commit()
    state_inserted, state_statuses = materialize_states(conn, start, end, False)
    inserted.update(state_inserted)
    if not args.dry_run:
        conn.commit()
    payload = {
        "generated_at_utc": utc_now(),
        "dry_run": bool(args.dry_run),
        "db": str(db_path),
        "range": {"start": start or None, "end": end or None},
        "source_capture_date": str(args.source_capture_date) or None,
        "source_rows": {
            "ladder_snapshots": len(snapshots),
            "ladder_rung_quotes": len(rungs),
            "forecast_captures": len(forecasts),
            "observation_lineage": len(observations),
            "ladder_metadata": len(ladder_metadata),
            "rung_metadata": len(rung_metadata),
            "forecast_metadata": len(forecast_metadata),
            "observation_metadata": len(observation_metadata),
        },
        "inserted_or_would_insert": inserted,
        "candidate_state_pit_status": dict(sorted(state_statuses.items())),
        "coverage": db_coverage(conn),
    }
    if args.summary_json:
        output = Path(args.summary_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    conn.close()
    source_conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
