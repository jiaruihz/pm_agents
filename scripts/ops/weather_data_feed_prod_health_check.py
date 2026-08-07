#!/usr/bin/env python3
"""Production health checks for the shared weather data feed.

This checks the data products consumed by live strategies. It does not place
orders and does not mutate runtime files.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops.weather_data_feed_parity_check import check_snapshot, latest_snapshot, load_snapshot
from src.strategies.runtime.production import load_production_spec


PRODUCTION_SPEC = load_production_spec()
MAC_DATA_FEED_RUNTIME = PRODUCTION_SPEC.data_feed_runtime_root
DEFAULT_SNAPSHOT_DIR = MAC_DATA_FEED_RUNTIME / "targeted_output/paper_snapshots"
DEFAULT_ORDERBOOK_DIR = MAC_DATA_FEED_RUNTIME / "targeted_output/orderbook_snapshots"
DEFAULT_FORECAST_CURVE_DIR = MAC_DATA_FEED_RUNTIME / "targeted_output/forecast_hourly_curves"
DEFAULT_FAST_OBSERVATION_STATE = MAC_DATA_FEED_RUNTIME / "output/high_frequency_observations/state.json"
DEFAULT_LIVE_CROSS_OBSERVATION_STATE = MAC_DATA_FEED_RUNTIME / "output/live_cross_observations/state.json"
DEFAULT_OBSERVATION_CACHE = MAC_DATA_FEED_RUNTIME / "output/observations/latest.json"
DEFAULT_OBSERVATION_HISTORY = MAC_DATA_FEED_RUNTIME / "output/observations/observations.jsonl"
DEFAULT_TELEMETRY_FILES: tuple[Path, ...] = ()
DEFAULT_SUMMARY_FILES = tuple(
    runtime.health_path
    for runtime in PRODUCTION_SPEC.managed_runtimes
    if runtime.role == "strategy"
    and runtime.health_format == "json"
    and runtime.health_path is not None
)
ACTIVE_RUNTIME_LIVE_ORDER_FILES = PRODUCTION_SPEC.active_live_order_paths()
SNAPSHOT_SCHEMA_VERSION = "weather_data_feed_snapshot_v1"


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def latest_existing_snapshot_dir() -> Path:
    """Return the configured current-production path without historical fallback."""

    return DEFAULT_SNAPSHOT_DIR


def latest_existing_orderbook_dir() -> Path:
    return DEFAULT_ORDERBOOK_DIR


def latest_existing_forecast_curve_dir() -> Path:
    return DEFAULT_FORECAST_CURVE_DIR


def latest_partitioned_file(root: Path, patterns: tuple[str, ...]) -> Path | None:
    """Find the newest capture without scanning every historical partition."""

    if not root.exists():
        return None
    partitions = sorted(
        (path for path in root.iterdir() if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    )
    for partition in partitions:
        files = [path for pattern in patterns for path in partition.glob(pattern)]
        if files:
            return max(files, key=lambda path: path.stat().st_mtime)
    files = [path for pattern in patterns for path in root.glob(pattern)]
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def latest_orderbook_snapshot(root: Path) -> Path | None:
    return latest_partitioned_file(
        root,
        ("orderbook_snapshot_*.jsonl.gz", "orderbook_snapshot_*.jsonl"),
    )


def latest_forecast_curve_capture(root: Path) -> Path | None:
    return latest_partitioned_file(root, ("forecast_hourly_curves_*.jsonl",))


def read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    wanted = max(1, limit)
    chunk_size = 64 * 1024
    with path.open("rb") as fh:
        fh.seek(0, 2)
        position = fh.tell()
        buffer = b""
        while position > 0 and buffer.count(b"\n") <= wanted:
            read_size = min(chunk_size, position)
            position -= read_size
            fh.seek(position)
            buffer = fh.read(read_size) + buffer
    lines = [line for line in buffer.splitlines() if line.strip()][-wanted:]
    rows: list[dict[str, Any]] = []
    for tail_index, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            rows.append(
                {
                    "_line_no": None,
                    "_tail_line_index": tail_index,
                    "_parse_error": "json_decode_error",
                }
            )
            continue
        if isinstance(row, dict):
            row["_line_no"] = None
            row["_tail_line_index"] = tail_index
            rows.append(row)
    return rows


def row_key(row: dict[str, Any], fields: Iterable[str]) -> tuple[str, ...]:
    return tuple(str(row.get(field) or "") for field in fields)


def parse_target_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw[:10]).date().isoformat()
    except ValueError:
        return ""


def effective_order_id(row: dict[str, Any]) -> str:
    if str(row.get("order_id") or "").strip():
        return str(row.get("order_id")).strip()
    if str(row.get("orderID") or "").strip():
        return str(row.get("orderID")).strip()
    exchange = row.get("exchange_response") if isinstance(row.get("exchange_response"), dict) else {}
    place = exchange.get("place") if isinstance(exchange.get("place"), dict) else {}
    return str(place.get("orderID") or "").strip()


def is_current_or_future_order(row: dict[str, Any], *, today_utc: str) -> bool:
    target_date = parse_target_date(row.get("target_date"))
    return bool(target_date and target_date >= today_utc)


def is_effective_live_order(row: dict[str, Any], *, today_utc: str) -> bool:
    if row.get("_parse_error") or not is_current_or_future_order(row, today_utc=today_utc):
        return False
    status = str(row.get("status") or "").lower()
    exchange = row.get("exchange_response") if isinstance(row.get("exchange_response"), dict) else {}
    place = exchange.get("place") if isinstance(exchange.get("place"), dict) else {}
    place_status = str(place.get("status") or "").lower()
    exchange_order_status = str(row.get("exchange_order_status") or "").lower()
    if status in {"failed", "error", "rejected", "cancelled", "canceled", "blocked"}:
        return False
    if place_status in {"failed", "error", "rejected", "cancelled", "canceled"}:
        return False
    try:
        actual_fill_shares = float(row.get("actual_fill_shares") or 0.0)
    except (TypeError, ValueError):
        actual_fill_shares = 0.0
    if actual_fill_shares <= 0 and (
        exchange_order_status in {"cancelled", "canceled"}
        or row.get("immediate_cancel_confirmed") is True
    ):
        return False
    if place.get("success") is False:
        return False
    return True


def replaced_order_ids(rows: list[dict[str, Any]]) -> set[str]:
    """Return order ids whose lifecycle successor/cancel is exchange-confirmed."""
    out: set[str] = set()
    for row in rows:
        source_order_id = str(row.get("source_order_id") or row.get("cancel_before_order_id") or "").strip()
        if not source_order_id:
            continue
        exchange = row.get("exchange_response") if isinstance(row.get("exchange_response"), dict) else {}
        cancel_status = str(exchange.get("pre_place_cancel_status") or "").lower()
        cancel_response = (
            exchange.get("pre_place_cancel_response")
            if isinstance(exchange.get("pre_place_cancel_response"), dict)
            else {}
        )
        cancel_payload = (
            cancel_response.get("cancel")
            if isinstance(cancel_response.get("cancel"), dict)
            else cancel_response
        )
        canceled = {
            str(value)
            for value in (cancel_payload.get("canceled") or [])
        } if isinstance(cancel_payload, dict) else set()
        confirmed = cancel_status in {
            "cancel_confirmed",
            "cancel_submitted",
        } or source_order_id in canceled
        if not confirmed:
            continue
        out.add(source_order_id)
    return out


def duplicate_examples(rows: list[dict[str, Any]], fields: tuple[str, ...], limit: int = 10) -> tuple[int, list[dict[str, Any]]]:
    counter = Counter(row_key(row, fields) for row in rows)
    duplicate_keys = {key for key, count in counter.items() if count > 1 and any(key)}
    examples = []
    for row in rows:
        key = row_key(row, fields)
        if key in duplicate_keys:
            examples.append({"key": dict(zip(fields, key)), "line_no": row.get("_line_no")})
            if len(examples) >= limit:
                break
    return sum(counter[key] - 1 for key in duplicate_keys), examples


def check_snapshot_duplicates(snapshot_path: Path, *, now_utc: datetime, max_age_min: float) -> dict[str, Any]:
    payload = load_snapshot(snapshot_path)
    rows = [row for row in payload.get("records", []) if isinstance(row, dict)]
    duplicate_count, examples = duplicate_examples(
        rows,
        ("city", "target_date", "token_id", "bracket"),
    )
    row_ts = [parse_utc(row.get("snapshot_ts_utc")) for row in rows]
    valid_ts = [dt for dt in row_ts if dt is not None]
    latest_ts = max(valid_ts) if valid_ts else parse_utc(payload.get("snapshot_ts_utc") or payload.get("ts_utc"))
    age_min = None
    if latest_ts is not None:
        age_min = round((now_utc - latest_ts).total_seconds() / 60.0, 3)
    city_target_counter = Counter((str(row.get("city") or ""), str(row.get("target_date") or "")) for row in rows)
    cities_with_many_targets = sorted(
        city
        for city in {city for city, _target in city_target_counter}
        if len({target for c, target in city_target_counter if c == city and target}) > 2
    )
    return {
        "path": str(snapshot_path),
        "duplicate_record_count": duplicate_count,
        "duplicate_examples": examples,
        "latest_snapshot_ts_utc": latest_ts.isoformat() if latest_ts else "",
        "snapshot_age_min": age_min,
        "snapshot_stale": bool(age_min is not None and age_min > max_age_min),
        "cities_with_more_than_two_target_dates": cities_with_many_targets[:20],
    }


def check_snapshot_source_model(snapshot_path: Path) -> dict[str, Any]:
    payload = load_snapshot(snapshot_path)
    rows = [row for row in payload.get("records", []) if isinstance(row, dict)]
    summary = payload.get("source_model_summary")
    forecast_source_counts = Counter(str(row.get("forecast_source") or "") for row in rows)
    forecast_source_counts.pop("", None)
    model_counts = Counter(str(row.get("model") or "") for row in rows)
    model_counts.pop("", None)

    if not isinstance(summary, dict):
        return {
            "path": str(snapshot_path),
            "status": "missing_source_model_summary",
            "source_model_summary": None,
            "forecast_source_counts": dict(sorted(forecast_source_counts.items())),
            "model_counts": dict(sorted(model_counts.items())),
        }

    assigned_counts = summary.get("assigned_model_counts") if isinstance(summary.get("assigned_model_counts"), dict) else {}
    actual_counts = summary.get("actual_model_counts") if isinstance(summary.get("actual_model_counts"), dict) else {}
    fallback_reason_counts = (
        summary.get("fallback_reason_counts") if isinstance(summary.get("fallback_reason_counts"), dict) else {}
    )
    expected_count = int(summary.get("expected_city_target_count") or 0)
    captured_count = int(summary.get("captured_city_target_count") or 0)
    cached_curve_count = int(summary.get("cached_curve_fallback_count") or 0)
    effective_count = int(
        summary.get("effective_city_target_count")
        if summary.get("effective_city_target_count") is not None
        else captured_count
    )
    fallback_count = int(summary.get("fallback_count") or 0)
    missing_count = int(summary.get("missing_count") or 0)
    lineage_errors = []
    if summary.get("grain") != "city_target_forecast":
        lineage_errors.append("unexpected_grain")
    if sum(int(value or 0) for value in assigned_counts.values()) != captured_count:
        lineage_errors.append("assigned_count_mismatch")
    if sum(int(value or 0) for value in actual_counts.values()) != captured_count:
        lineage_errors.append("actual_count_mismatch")
    if effective_count != captured_count + cached_curve_count:
        lineage_errors.append("effective_count_mismatch")
    if effective_count + missing_count != expected_count:
        lineage_errors.append("expected_count_mismatch")
    if fallback_count > 0 and not fallback_reason_counts:
        lineage_errors.append("fallback_reason_missing")
    if fallback_count == 0 and fallback_reason_counts:
        lineage_errors.append("fallback_reason_without_fallback")
    status = "invalid_source_model_lineage" if lineage_errors else "ok"

    return {
        "path": str(snapshot_path),
        "status": status,
        "source_model_summary": summary,
        "lineage_errors": lineage_errors,
        "fallback_detected": fallback_count > 0,
        "forecast_source_counts": dict(sorted(forecast_source_counts.items())),
        "model_counts": dict(sorted(model_counts.items())),
    }


def check_snapshot_city_state_coverage(snapshot_path: Path) -> dict[str, Any]:
    payload = load_snapshot(snapshot_path)
    rows = [row for row in payload.get("records", []) if isinstance(row, dict)]
    city_models = payload.get("city_models") if isinstance(payload.get("city_models"), dict) else {}
    city_pools = payload.get("city_pools") if isinstance(payload.get("city_pools"), dict) else {}
    registered_cities = set(city_pools) or set(city_models)
    record_cities = {str(row.get("city") or "") for row in rows if str(row.get("city") or "").strip()}
    declared_active_cities = payload.get("active_cities")
    if isinstance(declared_active_cities, list):
        expected_cities = {
            str(city).strip() for city in declared_active_cities if str(city).strip()
        }
        expectation_basis = "active_cities"
    else:
        # city_pools/city_models are the supported registry, not the current
        # market universe.  In snapshots without an explicit active-city list,
        # records define the active universe; source-model/orderbook checks
        # independently validate city-target completeness.
        expected_cities = set(record_cities)
        expectation_basis = "snapshot_records"
    same_local_day_rows = [
        row
        for row in rows
        if str(row.get("city") or "").strip()
        and str(row.get("target_date") or "").strip()
        and str(row.get("city_local_date_at_snapshot") or "") == str(row.get("target_date") or "")
    ]
    same_local_day_cities = {str(row.get("city") or "") for row in same_local_day_rows}
    required_fields = (
        "metar_current_max_f",
        "metar_latest_temp_f",
        "forecast_peak_delta_hours_local",
        "forecast_max_native",
    )
    field_city_counts: dict[str, int] = {}
    missing_required_by_field: dict[str, list[str]] = {}
    for field in required_fields:
        ok_cities = {
            str(row.get("city") or "")
            for row in same_local_day_rows
            if row.get(field) is not None and str(row.get(field)).strip() != ""
        }
        field_city_counts[field] = len(ok_cities)
        missing_required_by_field[field] = sorted(same_local_day_cities - ok_cities)

    missing_record_cities = sorted(expected_cities - record_cities)
    missing_required_total = sorted({city for cities in missing_required_by_field.values() for city in cities})
    trading_pools = {"t1_trading"}
    live_source_cities = {
        str(row.get("city") or "")
        for row in same_local_day_rows
        if str(row.get("live_observation_source") or "").strip()
    }
    missing_required_trading = sorted(
        city for city in missing_required_total if city_pools.get(city) in trading_pools and city in live_source_cities
    )
    missing_required_non_trading = sorted(city for city in missing_required_total if city not in missing_required_trading)
    status = "ok"
    if missing_required_trading:
        status = "missing_same_day_weather_state"
    elif missing_required_total:
        status = "missing_non_trading_weather_state"
    elif missing_record_cities:
        status = "missing_record_cities"

    return {
        "path": str(snapshot_path),
        "status": status,
        "expectation_basis": expectation_basis,
        "registered_city_count": len(registered_cities),
        "expected_city_count": len(expected_cities),
        "record_city_count": len(record_cities),
        "same_local_day_city_count": len(same_local_day_cities),
        "same_local_day_live_source_city_count": len(live_source_cities),
        "missing_record_cities": missing_record_cities,
        "required_fields": list(required_fields),
        "field_city_counts": field_city_counts,
        "missing_required_by_field": missing_required_by_field,
        "missing_required_cities": missing_required_total,
        "missing_required_trading_cities": missing_required_trading,
        "missing_required_non_trading_cities": missing_required_non_trading,
    }


def check_orderbook_snapshots(orderbook_dir: Path, *, now_utc: datetime, max_age_min: float) -> dict[str, Any]:
    latest = latest_orderbook_snapshot(orderbook_dir)
    if latest is None:
        return {
            "dir": str(orderbook_dir),
            "exists": orderbook_dir.exists(),
            "latest_path": "",
            "snapshot_age_min": None,
            "missing": True,
            "stale": False,
        }
    latest_mtime = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)
    age_min = round((now_utc - latest_mtime).total_seconds() / 60.0, 3)
    return {
        "dir": str(orderbook_dir),
        "exists": orderbook_dir.exists(),
        "latest_path": str(latest),
        "latest_mtime_utc": latest_mtime.isoformat(),
        "snapshot_age_min": age_min,
        "missing": False,
        "stale": bool(age_min > max_age_min),
    }


def check_snapshot_orderbook_coverage(snapshot_path: Path) -> dict[str, Any]:
    payload = load_snapshot(snapshot_path)
    summary = payload.get("orderbook_enrichment_summary")
    if not isinstance(summary, dict):
        return {
            "path": str(snapshot_path),
            "status": "missing_summary",
            "target_count": 0,
            "target_ok_count": 0,
            "target_incomplete_count": 0,
        }
    target_count = int(summary.get("target_count") or 0)
    target_ok_count = int(summary.get("target_ok_count") or 0)
    target_incomplete_count = int(summary.get("target_incomplete_count") or 0)
    consistent = target_count == target_ok_count + target_incomplete_count
    status = (
        "ok"
        if summary.get("status") == "ok" and target_count > 0 and target_incomplete_count == 0 and consistent
        else "incomplete"
    )
    return {
        "path": str(snapshot_path),
        "status": status,
        "scope": summary.get("scope"),
        "budget_sec": summary.get("budget_sec"),
        "spent_sec": summary.get("spent_sec"),
        "target_count": target_count,
        "target_ok_count": target_ok_count,
        "target_incomplete_count": target_incomplete_count,
        "target_status_counts": summary.get("target_status_counts") or {},
        "count_consistent": consistent,
    }


def check_fast_observation_state(path: Path, *, now_utc: datetime, max_age_min: float) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": str(path),
            "status": "missing",
            "updated_at_utc": "",
            "age_min": None,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "path": str(path),
            "status": "unreadable",
            "error": f"{type(exc).__name__}: {exc}",
            "updated_at_utc": "",
            "age_min": None,
        }
    updated = parse_utc(payload.get("updated_at_utc") or payload.get("generated_at_utc"))
    age_min = round((now_utc - updated).total_seconds() / 60.0, 3) if updated else None
    status = "ok" if age_min is not None and age_min <= max_age_min else "stale"
    return {
        "path": str(path),
        "status": status,
        "updated_at_utc": updated.isoformat() if updated else "",
        "age_min": age_min,
    }


def check_observation_cache(
    path: Path,
    *,
    history_path: Path,
    now_utc: datetime,
    max_cache_age_min: float,
    max_observation_age_min: float,
    history_window_min: float = 30.0,
    history_tail_rows: int = 1000,
) -> dict[str, Any]:
    """Validate the exact observation cache consumed by live strategies."""

    if not path.exists():
        return {"path": str(path), "history_path": str(history_path), "status": "fail", "error": "missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "path": str(path),
            "history_path": str(history_path),
            "status": "fail",
            "error": f"{type(exc).__name__}: {exc}",
        }

    generated = parse_utc(payload.get("generated_at_utc"))
    cache_age_min = round((now_utc - generated).total_seconds() / 60.0, 3) if generated else None
    rows = [row for row in payload.get("records", []) if isinstance(row, dict)]
    invalid_rows: list[dict[str, Any]] = []
    reused_rows: list[str] = []
    awaiting_first_rows: list[str] = []
    for row in rows:
        city = str(row.get("city") or "")
        status = str(row.get("status") or "")
        try:
            running_max = float(row.get("running_max_c"))
        except (TypeError, ValueError):
            running_max = math.nan
        try:
            current_temp = float(row.get("current_temp_c"))
        except (TypeError, ValueError):
            current_temp = math.nan
        try:
            observation_age = float(row.get("age_min"))
        except (TypeError, ValueError):
            observation_age = math.nan
        reasons = []
        awaiting_first = status == "awaiting_first_observation"
        try:
            local_day_elapsed_min = float(row.get("local_day_elapsed_min"))
            first_observation_grace_min = float(row.get("first_observation_grace_min"))
        except (TypeError, ValueError):
            local_day_elapsed_min = math.nan
            first_observation_grace_min = math.nan
        valid_awaiting_first = (
            awaiting_first
            and math.isfinite(local_day_elapsed_min)
            and math.isfinite(first_observation_grace_min)
            and 0 <= local_day_elapsed_min <= first_observation_grace_min
        )
        if status not in {"ok", "reused_after_fetch_error"} and not valid_awaiting_first:
            reasons.append(f"status:{status or 'missing'}")
        if not math.isfinite(running_max) and not valid_awaiting_first:
            reasons.append("missing_running_max")
        if math.isfinite(current_temp) and math.isfinite(running_max) and running_max + 1e-9 < current_temp:
            reasons.append("running_max_below_current")
        if (
            not valid_awaiting_first
            and (not math.isfinite(observation_age) or observation_age < 0 or observation_age > max_observation_age_min)
        ):
            reasons.append("observation_stale_or_invalid_age")
        if reasons:
            invalid_rows.append({"city": city, "reasons": reasons})
        if status == "reused_after_fetch_error":
            reused_rows.append(city)
        if valid_awaiting_first:
            awaiting_first_rows.append(city)

    recent_cutoff = now_utc.timestamp() - history_window_min * 60.0
    previous_by_station_day: dict[tuple[str, str, str], tuple[float, str]] = {}
    regressions: list[dict[str, Any]] = []
    for row in read_jsonl_tail(history_path, history_tail_rows):
        if row.get("_parse_error"):
            continue
        key = (
            str(row.get("city") or ""),
            str(row.get("target_date") or ""),
            str(row.get("station") or ""),
        )
        try:
            running_max = float(row.get("running_max_c"))
        except (TypeError, ValueError):
            continue
        if not all(key) or not math.isfinite(running_max):
            continue
        row_ts_raw = str(row.get("observation_cache_generated_at_utc") or "")
        row_ts = parse_utc(row_ts_raw)
        previous = previous_by_station_day.get(key)
        if previous and running_max + 1e-9 < previous[0] and row_ts and row_ts.timestamp() >= recent_cutoff:
            regressions.append(
                {
                    "city": key[0],
                    "target_date": key[1],
                    "station": key[2],
                    "previous_running_max_c": previous[0],
                    "running_max_c": running_max,
                    "generated_at_utc": row_ts_raw,
                }
            )
        if previous is None or running_max >= previous[0]:
            previous_by_station_day[key] = (running_max, row_ts_raw)

    fail = (
        cache_age_min is None
        or cache_age_min < 0
        or cache_age_min > max_cache_age_min
        or bool(invalid_rows)
        or bool(regressions)
    )
    status = "fail" if fail else ("warn" if reused_rows or awaiting_first_rows else "ok")
    return {
        "path": str(path),
        "history_path": str(history_path),
        "history_tail_rows": history_tail_rows,
        "status": status,
        "generated_at_utc": generated.isoformat() if generated else "",
        "cache_age_min": cache_age_min,
        "record_count": len(rows),
        "invalid_record_count": len(invalid_rows),
        "invalid_record_examples": invalid_rows[:10],
        "reused_record_count": len(reused_rows),
        "reused_cities": sorted(reused_rows),
        "awaiting_first_observation_count": len(awaiting_first_rows),
        "awaiting_first_observation_cities": sorted(awaiting_first_rows),
        "recent_running_max_regression_count": len(regressions),
        "recent_running_max_regressions": regressions[:10],
    }


def check_forecast_hourly_curves(
    curve_dir: Path,
    snapshot_path: Path,
    *,
    now_utc: datetime,
    max_age_min: float,
) -> dict[str, Any]:
    latest = latest_forecast_curve_capture(curve_dir)
    if latest is None:
        return {
            "dir": str(curve_dir),
            "exists": curve_dir.exists(),
            "latest_path": "",
            "status": "missing",
            "missing": True,
            "stale": False,
        }

    rows = read_jsonl_tail(latest, 10000)
    parse_errors = [row for row in rows if row.get("_parse_error")]
    valid_rows = [row for row in rows if not row.get("_parse_error")]
    required_fields = (
        "capture_id",
        "snapshot_ts_utc",
        "available_at_utc",
        "available_at_basis",
        "city",
        "target_date",
        "forecast_source",
        "forecast_model",
        "forecast_assigned_model",
        "forecast_values_hash",
        "forecast_first_seen_utc",
        "forecast_first_seen_basis",
        "forecast_first_seen_source",
        "forecast_run_lineage_status",
        "hourly_curve",
    )
    missing_fields = Counter()
    empty_curves = 0
    invalid_fallback_rows = 0
    early_first_seen_rows = 0
    future_first_seen_rows = 0
    invalid_detected_at_rows = 0
    invalid_available_at_rows = 0
    latest_mtime = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)
    for row in valid_rows:
        for field in required_fields:
            if field == "hourly_curve":
                if not isinstance(row.get(field), list) or not row[field]:
                    missing_fields[field] += 1
                continue
            if not str(row.get(field) or "").strip():
                missing_fields[field] += 1
        if not isinstance(row.get("hourly_curve"), list) or not row.get("hourly_curve"):
            empty_curves += 1
        fallback = row.get("forecast_model_fallback")
        assigned_model = str(row.get("forecast_assigned_model") or "")
        active_model = str(row.get("forecast_model") or "")
        if not isinstance(fallback, bool) or (active_model != assigned_model and not row.get("forecast_model_fallback_reason")):
            invalid_fallback_rows += 1
        snapshot_at = parse_utc(row.get("snapshot_ts_utc"))
        available_at = parse_utc(row.get("available_at_utc"))
        first_seen_at = parse_utc(row.get("forecast_first_seen_utc"))
        detected_at = parse_utc(row.get("forecast_detected_at_utc"))
        first_seen_source = str(row.get("forecast_first_seen_source") or "")
        if available_at is None or snapshot_at is None or available_at < snapshot_at or available_at > latest_mtime:
            invalid_available_at_rows += 1
        if first_seen_at is None or available_at is None or first_seen_at > available_at:
            future_first_seen_rows += 1
        if first_seen_source.startswith("current_capture_") and (
            first_seen_at is None or snapshot_at is None or first_seen_at < snapshot_at
        ):
            early_first_seen_rows += 1
        if detected_at is not None:
            if not row.get("forecast_detected_at_basis"):
                missing_fields["forecast_detected_at_basis"] += 1
            if (
                snapshot_at is None
                or available_at is None
                or detected_at < snapshot_at
                or detected_at > available_at
                or (first_seen_at is not None and first_seen_at > detected_at)
            ):
                invalid_detected_at_rows += 1

    available_times = [parse_utc(row.get("available_at_utc")) for row in valid_rows]
    available_times = [value for value in available_times if value is not None]
    latest_available = max(available_times) if available_times else None
    age_min = (
        round((now_utc - latest_available).total_seconds() / 60.0, 3)
        if latest_available is not None
        else None
    )
    capture_snapshot_times = {str(row.get("snapshot_ts_utc") or "") for row in valid_rows}
    snapshot_payload = load_snapshot(snapshot_path)
    snapshot_ts = str(snapshot_payload.get("snapshot_ts_utc") or snapshot_payload.get("ts_utc") or "")
    capture_keys = {
        (
            str(row.get("city") or ""),
            str(row.get("target_date") or ""),
            str(row.get("forecast_values_hash") or ""),
        )
        for row in valid_rows
    }
    cached_reuse_pairs: set[tuple[str, str]] = set()
    invalid_cached_reuse: list[dict[str, str]] = []
    latest_resolved = latest.resolve()
    snapshot_records = [
        row for row in snapshot_payload.get("records", []) if isinstance(row, dict)
    ]
    for row in snapshot_records:
        if row.get("forecast_curve_evidence") != "cached_durable_curve":
            continue
        city = str(row.get("city") or "")
        target_date = str(row.get("target_date") or "")
        values_hash = str(row.get("forecast_values_hash") or "")
        archive_raw = str(row.get("forecast_curve_archive_path") or "")
        pair = (city, target_date)
        if pair in cached_reuse_pairs:
            continue
        archive = Path(archive_raw).expanduser() if archive_raw else None
        valid = bool(
            city
            and target_date
            and values_hash
            and archive is not None
            and archive.exists()
            and archive.resolve() == latest_resolved
            and (city, target_date, values_hash) in capture_keys
        )
        if valid:
            cached_reuse_pairs.add(pair)
        else:
            invalid_cached_reuse.append(
                {
                    "city": city,
                    "target_date": target_date,
                    "forecast_values_hash": values_hash,
                    "forecast_curve_archive_path": archive_raw,
                }
            )
    expected_pairs = {
        (str(row.get("city") or ""), str(row.get("target_date") or ""))
        for row in snapshot_payload.get("records", [])
        if isinstance(row, dict) and row.get("city") and row.get("target_date")
    }
    captured_pairs = {(str(row.get("city") or ""), str(row.get("target_date") or "")) for row in valid_rows}
    missing_pairs = sorted(expected_pairs - captured_pairs)
    cached_reuse_complete = bool(expected_pairs) and expected_pairs <= cached_reuse_pairs

    status = "ok"
    if parse_errors or not valid_rows:
        status = "invalid_jsonl"
    elif (
        missing_fields
        or invalid_fallback_rows
        or early_first_seen_rows
        or future_first_seen_rows
        or invalid_detected_at_rows
        or invalid_available_at_rows
    ):
        status = "invalid_lineage"
    elif age_min is None or age_min > max_age_min:
        status = "stale"
    elif snapshot_ts not in capture_snapshot_times and not cached_reuse_complete:
        status = "snapshot_mismatch"
    elif missing_pairs:
        status = "incomplete_city_target_coverage"
    return {
        "dir": str(curve_dir),
        "exists": curve_dir.exists(),
        "latest_path": str(latest),
        "latest_capture_mtime_utc": latest_mtime.isoformat(),
        "latest_capture_available_at_utc": latest_available.isoformat() if latest_available else "",
        "latest_capture_age_min": age_min,
        "latest_capture_snapshot_ts_utc": sorted(capture_snapshot_times),
        "latest_snapshot_ts_utc": snapshot_ts,
        "cached_reuse_complete": cached_reuse_complete,
        "cached_reuse_city_target_count": len(cached_reuse_pairs),
        "invalid_cached_reuse_count": len(invalid_cached_reuse),
        "invalid_cached_reuse_examples": invalid_cached_reuse[:20],
        "capture_row_count": len(valid_rows),
        "capture_city_count": len({row.get("city") for row in valid_rows if row.get("city")}),
        "capture_city_target_count": len(captured_pairs),
        "expected_city_target_count": len(expected_pairs),
        "missing_city_target_count": len(missing_pairs),
        "missing_city_target_examples": [
            {"city": city, "target_date": target_date} for city, target_date in missing_pairs[:20]
        ],
        "parse_error_count": len(parse_errors),
        "missing_required_fields": dict(sorted(missing_fields.items())),
        "empty_hourly_curve_count": empty_curves,
        "invalid_fallback_lineage_count": invalid_fallback_rows,
        "early_first_seen_count": early_first_seen_rows,
        "future_first_seen_count": future_first_seen_rows,
        "invalid_detected_at_count": invalid_detected_at_rows,
        "invalid_available_at_count": invalid_available_at_rows,
        "missing": False,
        "stale": bool(age_min is None or age_min > max_age_min),
        "status": status,
    }


def check_telemetry(path: Path, *, tail_rows: int) -> dict[str, Any]:
    rows = read_jsonl_tail(path, tail_rows)
    parse_errors = [row for row in rows if row.get("_parse_error")]
    required = ("record_type", "created_at_utc", "strategy_instance", "city", "target_date", "decision_status")
    missing = Counter()
    for row in rows:
        if row.get("_parse_error"):
            continue
        for field in required:
            if not str(row.get(field) or "").strip():
                missing[field] += 1
    run_id_counts = Counter(str(row.get("telemetry_run_id") or "") for row in rows if row.get("telemetry_run_id"))
    duplicate_decisions, decision_examples = duplicate_examples(
        rows,
        ("strategy_instance", "created_at_utc", "city", "target_date", "market_id", "current_bracket", "decision_status"),
    )
    status_counts = Counter(str(row.get("decision_status") or "") for row in rows if not row.get("_parse_error"))
    return {
        "path": str(path),
        "exists": path.exists(),
        "checked_rows": len(rows),
        "parse_error_count": len(parse_errors),
        "missing_required_fields": dict(sorted(missing.items())),
        "telemetry_run_id_count": len(run_id_counts),
        "max_rows_per_telemetry_run_id": max(run_id_counts.values(), default=0),
        "duplicate_decision_count": duplicate_decisions,
        "duplicate_examples": decision_examples[:10],
        "decision_status_counts": dict(status_counts.most_common(20)),
    }


def check_live_orders(
    live_dir: Path,
    *,
    tail_rows: int,
    all_files: bool = False,
    extra_files: list[Path] | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    if not live_dir.exists():
        files = []
    elif all_files:
        files = sorted(live_dir.glob("*orders.jsonl"))
    else:
        files = []
    if extra_files:
        files.extend(path for path in extra_files if path.exists() and path not in files)
    rows: list[dict[str, Any]] = []
    for path in files:
        for row in read_jsonl_tail(path, tail_rows):
            row["_file"] = str(path)
            row["_effective_order_id"] = effective_order_id(row)
            rows.append(row)
    duplicate_orders, order_examples = duplicate_examples(
        [row for row in rows if row.get("_effective_order_id")],
        ("_effective_order_id",),
    )
    duplicate_intents, intent_examples = duplicate_examples(
        rows,
        ("strategy_instance", "city", "target_date", "token_id", "signal_side", "order_side"),
    )
    today_utc = (now_utc or datetime.now(timezone.utc)).date().isoformat()
    replaced_ids = replaced_order_ids(rows)
    effective_rows = [
        row
        for row in rows
        if is_effective_live_order(row, today_utc=today_utc)
        and (not row.get("_effective_order_id") or row.get("_effective_order_id") not in replaced_ids)
    ]
    duplicate_current_intents, current_intent_examples = duplicate_examples(
        effective_rows,
        (
            "strategy_instance",
            "city",
            "target_date",
            "token_id",
            "signal_side",
            "order_side",
            "execution_policy",
            "child_order_role",
        ),
    )
    market_groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in effective_rows:
        key = (
            str(row.get("city") or ""),
            str(row.get("target_date") or ""),
            str(row.get("market_id") or row.get("condition_id") or ""),
            str(row.get("bracket") or row.get("current_bracket") or ""),
        )
        if any(key):
            market_groups.setdefault(key, []).append(row)
    conflict_examples: list[dict[str, Any]] = []
    for key, group_rows in market_groups.items():
        sides = {str(row.get("signal_side") or "") for row in group_rows}
        if "BUY_YES" not in sides or "BUY_NO" not in sides:
            continue
        conflict_examples.append(
            {
                "key": {
                    "city": key[0],
                    "target_date": key[1],
                    "market_or_condition_id": key[2],
                    "bracket": key[3],
                },
                "rows": [
                    {
                        "file": row.get("_file"),
                        "line_no": row.get("_line_no"),
                        "strategy_instance": row.get("strategy_instance"),
                        "signal_side": row.get("signal_side"),
                        "order_id": row.get("_effective_order_id"),
                    }
                    for row in group_rows[:6]
                ],
            }
        )
    parse_errors = sum(1 for row in rows if row.get("_parse_error"))
    return {
        "live_dir": str(live_dir),
        "scope": "all_live_order_files" if all_files else "active_live_order_files",
        "files": [str(path) for path in files],
        "checked_rows": len(rows),
        "effective_current_or_future_rows": len(effective_rows),
        "replaced_order_id_count": len(replaced_ids),
        "current_or_future_cutoff_utc_date": today_utc,
        "parse_error_count": parse_errors,
        "duplicate_order_id_count": duplicate_orders,
        "duplicate_strategy_city_token_count": duplicate_intents,
        "duplicate_current_strategy_city_token_count": duplicate_current_intents,
        "current_yes_no_conflict_count": len(conflict_examples),
        "duplicate_examples": (order_examples + intent_examples)[:10],
        "current_duplicate_examples": current_intent_examples[:10],
        "current_yes_no_conflict_examples": conflict_examples[:10],
    }


def check_summaries(paths: list[Path]) -> list[dict[str, Any]]:
    out = []
    for path in paths:
        if not path.exists():
            out.append({"path": str(path), "exists": False})
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        out.append(
            {
                "path": str(path),
                "exists": True,
                "generated_at_utc": payload.get("generated_at_utc"),
                "status": payload.get("status"),
                "snapshot_ts_utc": payload.get("snapshot_ts_utc"),
                "snapshot_age_min": payload.get("snapshot_age_min"),
                "plans": payload.get("plans"),
                "live_enabled": payload.get("live_enabled"),
            }
        )
    return out


def overall_status(sections: dict[str, Any]) -> str:
    parity = sections["snapshot_parity"]
    snapshot = sections["snapshot_duplicates"]
    source_model = sections.get("snapshot_source_model", {})
    city_state = sections.get("snapshot_city_state_coverage", {})
    orderbook = sections.get("orderbook_snapshots", {})
    orderbook_coverage = sections.get("snapshot_orderbook_coverage", {})
    forecast_curves = sections.get("forecast_hourly_curves", {})
    fast_observations = sections.get("fast_observation_state", {})
    live_cross_observations = sections.get("live_cross_observation_state", {})
    active_fast_observations = (
        live_cross_observations if live_cross_observations else fast_observations
    )
    observation_cache = sections.get("observation_cache", {})
    telemetry = sections["telemetry"]
    live_orders = sections["live_orders"]
    hard_fail = (
        parity.get("status") != "ok"
        or (bool(source_model) and source_model.get("status") != "ok")
        or city_state.get("status") == "missing_same_day_weather_state"
        or orderbook.get("missing")
        or (bool(orderbook_coverage) and orderbook_coverage.get("status") != "ok")
        or (bool(forecast_curves) and forecast_curves.get("status") != "ok")
        or (
            bool(active_fast_observations)
            and active_fast_observations.get("status") != "ok"
        )
        or (bool(observation_cache) and observation_cache.get("status") == "fail")
        or snapshot.get("duplicate_record_count", 0) > 0
        or any(item.get("parse_error_count", 0) > 0 for item in telemetry)
        or live_orders.get("parse_error_count", 0) > 0
        or live_orders.get("duplicate_order_id_count", 0) > 0
        or live_orders.get("duplicate_current_strategy_city_token_count", 0) > 0
        or live_orders.get("current_yes_no_conflict_count", 0) > 0
    )
    if hard_fail:
        return "fail"
    warn = (
        snapshot.get("snapshot_stale")
        or city_state.get("status") == "missing_non_trading_weather_state"
        or city_state.get("status") == "missing_record_cities"
        or orderbook.get("stale")
        or (bool(observation_cache) and observation_cache.get("status") == "warn")
        or any(item.get("duplicate_decision_count", 0) > 0 for item in telemetry)
        or any(summary.get("status") == "stale_snapshot" for summary in sections["summaries"])
    )
    return "warn" if warn else "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check production weather data feed outputs for stale, bad, or duplicate data.")
    parser.add_argument("--snapshot", default="")
    parser.add_argument("--snapshot-dir", default=str(latest_existing_snapshot_dir()))
    parser.add_argument("--orderbook-dir", default=str(latest_existing_orderbook_dir()))
    parser.add_argument("--forecast-curve-dir", default=str(latest_existing_forecast_curve_dir()))
    parser.add_argument("--fast-observation-state", default=str(DEFAULT_FAST_OBSERVATION_STATE))
    parser.add_argument(
        "--live-cross-observation-state",
        default=str(DEFAULT_LIVE_CROSS_OBSERVATION_STATE),
    )
    parser.add_argument("--observation-cache", default=str(DEFAULT_OBSERVATION_CACHE))
    parser.add_argument("--observation-history", default=str(DEFAULT_OBSERVATION_HISTORY))
    parser.add_argument("--runtime-root", default=str(PRODUCTION_SPEC.pm_runtime_root / "weather_edge_v1"))
    parser.add_argument("--max-snapshot-age-min", type=float, default=45.0)
    parser.add_argument("--max-orderbook-age-min", type=float, default=75.0)
    # GFS/ECMWF model cycles update on an hours-scale; the collector refreshes
    # at 30 minutes but a six-hour durable curve remains valid during an
    # upstream outage. This is a source-cadence SLA, not a process heartbeat.
    parser.add_argument("--max-forecast-curve-age-min", type=float, default=390.0)
    parser.add_argument("--max-fast-observation-age-min", type=float, default=3.0)
    parser.add_argument("--max-observation-cache-age-min", type=float, default=10.0)
    parser.add_argument("--max-observation-age-min", type=float, default=120.0)
    parser.add_argument("--tail-telemetry-rows", type=int, default=5000)
    parser.add_argument("--tail-live-order-rows", type=int, default=2000)
    parser.add_argument("--tail-observation-history-rows", type=int, default=1000)
    parser.add_argument("--all-live-order-files", action="store_true")
    args = parser.parse_args()

    now_utc = datetime.now(timezone.utc)
    snapshot_path = Path(args.snapshot) if args.snapshot else latest_snapshot(Path(args.snapshot_dir))
    runtime_root = Path(args.runtime_root)
    telemetry_files = [path if path.is_absolute() else runtime_root / path for path in DEFAULT_TELEMETRY_FILES]
    summary_files = [path if path.is_absolute() else runtime_root / path for path in DEFAULT_SUMMARY_FILES]
    active_live_order_files = [
        path if path.is_absolute() else runtime_root / path
        for path in ACTIVE_RUNTIME_LIVE_ORDER_FILES
    ]
    sections = {
        "snapshot_parity": check_snapshot(snapshot_path),
        "snapshot_duplicates": check_snapshot_duplicates(snapshot_path, now_utc=now_utc, max_age_min=args.max_snapshot_age_min),
        "snapshot_source_model": check_snapshot_source_model(snapshot_path),
        "snapshot_city_state_coverage": check_snapshot_city_state_coverage(snapshot_path),
        "orderbook_snapshots": check_orderbook_snapshots(
            Path(args.orderbook_dir),
            now_utc=now_utc,
            max_age_min=args.max_orderbook_age_min,
        ),
        "snapshot_orderbook_coverage": check_snapshot_orderbook_coverage(snapshot_path),
        "forecast_hourly_curves": check_forecast_hourly_curves(
            Path(args.forecast_curve_dir),
            snapshot_path,
            now_utc=now_utc,
            max_age_min=args.max_forecast_curve_age_min,
        ),
        "fast_observation_state": check_fast_observation_state(
            Path(args.fast_observation_state),
            now_utc=now_utc,
            max_age_min=args.max_fast_observation_age_min,
        ),
        "live_cross_observation_state": check_fast_observation_state(
            Path(args.live_cross_observation_state),
            now_utc=now_utc,
            max_age_min=args.max_fast_observation_age_min,
        ),
        "observation_cache": check_observation_cache(
            Path(args.observation_cache),
            history_path=Path(args.observation_history),
            now_utc=now_utc,
            max_cache_age_min=args.max_observation_cache_age_min,
            max_observation_age_min=args.max_observation_age_min,
            history_tail_rows=args.tail_observation_history_rows,
        ),
        "telemetry": [check_telemetry(path, tail_rows=args.tail_telemetry_rows) for path in telemetry_files],
        "live_orders": check_live_orders(
            runtime_root / "live",
            tail_rows=args.tail_live_order_rows,
            all_files=args.all_live_order_files,
            extra_files=active_live_order_files,
            now_utc=now_utc,
        ),
        "summaries": check_summaries(summary_files),
    }
    report = {
        "status": overall_status(sections),
        "checked_at_utc": now_utc.isoformat(),
        "snapshot_schema_expected": SNAPSHOT_SCHEMA_VERSION,
        **sections,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
