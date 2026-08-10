"""Incremental state for append-only weather source-event ledgers.

The live strategy loop must not reparse a growing JSONL file on every cycle.
This module persists a byte cursor plus the small per-city/day aggregates needed
for METAR running extremes and routine-report cadence.  It also retains the
previous local day so a new market day can warm-start its report clock.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

from weather_data_feed.fast_event_source_policy import FastEventSourceProfile, market_value_from_temp_c
from weather_data_feed.source_policy import canonical_city_name


STATE_SCHEMA = "weather_source_event_incremental_state_v1"

METAR_LIKE_SOURCES = {
    "aviationweather_metar",
    "aviationweather_cache_csv",
    "synopticdata_timeseries",
    "noaa_tgftp_station_txt",
    "iem_asos",
    "iem_asos_madishf_latest",
}


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def retained_target_dates(target_dates_by_city: dict[str, str]) -> dict[str, set[str]]:
    retained: dict[str, set[str]] = {}
    for city, target_text in target_dates_by_city.items():
        try:
            target = date.fromisoformat(target_text)
        except ValueError:
            continue
        retained[canonical_city_name(city)] = {target.isoformat(), (target - timedelta(days=1)).isoformat()}
    return retained


def _empty_state(path: Path) -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA,
        "source_path": str(path),
        "file_identity": None,
        "byte_offset": 0,
        "daily": {},
    }


def _source_event_shards(root: Path) -> list[Path]:
    return sorted(root.glob("????-??-??/sources.jsonl"))


def _relevant_shard_dates(retained_dates: dict[str, set[str]]) -> tuple[date, date] | None:
    parsed = [
        parsed_date
        for values in retained_dates.values()
        for value in values
        if (parsed_date := date.fromisoformat(value))
    ]
    if not parsed:
        return None
    return min(parsed) - timedelta(days=2), max(parsed) + timedelta(days=1)


def _migrate_aggregate_offset_to_shards(
    root: Path,
    shards: list[Path],
    aggregate_offset: int,
) -> dict[str, dict[str, Any]]:
    remaining = aggregate_offset
    cursors: dict[str, dict[str, Any]] = {}
    for shard in shards:
        stat = shard.stat()
        offset = min(int(stat.st_size), remaining)
        cursors[str(shard)] = {
            "file_identity": [int(stat.st_dev), int(stat.st_ino)],
            "byte_offset": offset,
        }
        remaining -= offset
    if remaining:
        raise ValueError(
            f"aggregate cursor exceeds dated shard bytes: root={root} remaining={remaining}"
        )
    return cursors


def _refresh_partitioned_source_event_state(
    root: Path,
    existing_state: dict[str, Any] | None,
    *,
    retained_dates: dict[str, set[str]],
    city_profiles: dict[str, FastEventSourceProfile],
) -> tuple[dict[str, Any], dict[str, Any]]:
    shards = _source_event_shards(root)
    if not shards:
        empty = _empty_state(root)
        empty["shard_cursors"] = {}
        return empty, {
            "status": "source_events_missing",
            "full_rebuild": True,
            "reset_reason": "dated_shards_missing",
            "lines_read": 0,
            "bytes_read": 0,
        }

    state = dict(existing_state or {})
    source_path = Path(str(state.get("source_path") or ""))
    migrated_from_aggregate = (
        state.get("schema_version") == STATE_SCHEMA
        and source_path == root / "sources.jsonl"
    )
    if migrated_from_aggregate:
        shard_cursors = _migrate_aggregate_offset_to_shards(
            root,
            shards,
            int(state.get("byte_offset") or 0),
        )
        reset_reason = "aggregate_cursor_migrated"
    elif (
        state.get("schema_version") == STATE_SCHEMA
        and source_path == root
        and isinstance(state.get("shard_cursors"), dict)
    ):
        shard_cursors = {
            str(path): dict(cursor)
            for path, cursor in dict(state.get("shard_cursors") or {}).items()
        }
        reset_reason = ""
    else:
        shard_cursors = {}
        reset_reason = "partition_state_initialized"

    daily = {
        key: dict(value)
        for key, value in dict(state.get("daily") or {}).items()
        if str(value.get("target_date") or "")
        in retained_dates.get(str(value.get("city") or ""), set())
    }
    fresh_partition_state = not migrated_from_aggregate and not shard_cursors
    relevant_window = _relevant_shard_dates(retained_dates)
    lines_read = 0
    bytes_read = 0
    for shard in shards:
        stat = shard.stat()
        identity = [int(stat.st_dev), int(stat.st_ino)]
        cursor = dict(shard_cursors.get(str(shard)) or {})
        if fresh_partition_state and relevant_window is not None:
            try:
                shard_date = date.fromisoformat(shard.parent.name)
            except ValueError:
                shard_date = relevant_window[0]
            if not (relevant_window[0] <= shard_date <= relevant_window[1]):
                shard_cursors[str(shard)] = {
                    "file_identity": identity,
                    "byte_offset": int(stat.st_size),
                }
                continue
        offset = int(cursor.get("byte_offset") or 0)
        if cursor and (
            cursor.get("file_identity") != identity or int(stat.st_size) < offset
        ):
            offset = 0
        start_offset = offset
        with shard.open("rb") as handle:
            handle.seek(offset)
            while True:
                line_start = handle.tell()
                raw_line = handle.readline()
                if not raw_line:
                    break
                if not raw_line.endswith(b"\n"):
                    offset = line_start
                    break
                offset = handle.tell()
                lines_read += 1
                try:
                    row = json.loads(raw_line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(row, dict):
                    _update_daily_row(
                        daily,
                        row,
                        retained_dates=retained_dates,
                        city_profiles=city_profiles,
                    )
        bytes_read += max(0, offset - start_offset)
        shard_cursors[str(shard)] = {
            "file_identity": identity,
            "byte_offset": offset,
        }

    refreshed = {
        "schema_version": STATE_SCHEMA,
        "source_path": str(root),
        "file_identity": None,
        "byte_offset": sum(
            int(cursor.get("byte_offset") or 0)
            for cursor in shard_cursors.values()
        ),
        "shard_cursors": shard_cursors,
        "daily": daily,
    }
    return refreshed, {
        "status": "ok",
        "full_rebuild": bool(fresh_partition_state),
        "reset_reason": reset_reason,
        "migrated_from_aggregate": migrated_from_aggregate,
        "lines_read": lines_read,
        "bytes_read": bytes_read,
        "byte_offset": refreshed["byte_offset"],
        "shard_count": len(shards),
        "daily_rows": len(daily),
    }


def _update_daily_row(
    daily: dict[str, dict[str, Any]],
    raw: dict[str, Any],
    *,
    retained_dates: dict[str, set[str]],
    city_profiles: dict[str, FastEventSourceProfile],
) -> None:
    if str(raw.get("source") or "") not in METAR_LIKE_SOURCES:
        return
    city = canonical_city_name(str(raw.get("city") or ""))
    target_date = str(raw.get("target_date") or "")
    if city not in city_profiles or target_date not in retained_dates.get(city, set()):
        return
    temp_c = finite_float(raw.get("temp_c"))
    if temp_c is None:
        return
    report_dt = parse_utc(raw.get("source_report_ts_utc"))
    detect_dt = parse_utc(raw.get("local_detect_ts_utc") or raw.get("ts_utc"))
    market_value = market_value_from_temp_c(temp_c, city_profiles[city])
    key = f"{city}|{target_date}"
    current = daily.get(key)
    if current is None:
        current = {
            "city": city,
            "target_date": target_date,
            "market_unit": city_profiles[city].market_unit,
            "metar_running_max_market_value": market_value,
            "metar_running_max_round_c": market_value,
            "metar_running_max_temp_c": temp_c,
            "latest_metar_temp_c": temp_c,
            "latest_metar_round_c": market_value,
            "latest_report_ts_utc": report_dt.isoformat() if report_dt else "",
            "latest_detect_ts_utc": detect_dt.isoformat() if detect_dt else "",
            "raw_metar": raw.get("raw_metar") or "",
            "routine_report_ts_utc": [],
        }
        daily[key] = current
    else:
        if market_value > int(current["metar_running_max_market_value"]):
            current["metar_running_max_market_value"] = market_value
            current["metar_running_max_round_c"] = market_value
            current["metar_running_max_temp_c"] = temp_c
        prior_report = parse_utc(current.get("latest_report_ts_utc"))
        if report_dt and (prior_report is None or report_dt >= prior_report):
            current.update(
                {
                    "latest_metar_temp_c": temp_c,
                    "latest_metar_round_c": market_value,
                    "latest_report_ts_utc": report_dt.isoformat(),
                    "latest_detect_ts_utc": detect_dt.isoformat() if detect_dt else "",
                    "raw_metar": raw.get("raw_metar") or "",
                }
            )

    raw_metar = str(raw.get("raw_metar") or "").strip().upper()
    if report_dt is not None and raw_metar.startswith("METAR "):
        reports = set(str(value) for value in current.get("routine_report_ts_utc") or [])
        reports.add(report_dt.isoformat())
        current["routine_report_ts_utc"] = sorted(reports)[-24:]


def refresh_source_event_state(
    path: Path,
    existing_state: dict[str, Any] | None,
    *,
    target_dates_by_city: dict[str, str],
    city_profiles: dict[str, FastEventSourceProfile],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Consume only complete lines appended since the persisted byte cursor."""
    retained_dates = retained_target_dates(target_dates_by_city)
    if path.is_dir():
        return _refresh_partitioned_source_event_state(
            path,
            existing_state,
            retained_dates=retained_dates,
            city_profiles=city_profiles,
        )
    state = dict(existing_state or {})
    reset_reason = ""
    try:
        stat = path.stat()
    except FileNotFoundError:
        empty = _empty_state(path)
        return empty, {
            "status": "source_events_missing",
            "full_rebuild": True,
            "reset_reason": "file_missing",
            "lines_read": 0,
            "bytes_read": 0,
        }

    identity = [int(stat.st_dev), int(stat.st_ino)]
    offset = int(state.get("byte_offset") or 0)
    if state.get("schema_version") != STATE_SCHEMA:
        reset_reason = "schema_changed"
    elif str(state.get("source_path") or "") != str(path):
        reset_reason = "path_changed"
    elif state.get("file_identity") != identity:
        reset_reason = "file_rotated"
    elif stat.st_size < offset:
        reset_reason = "file_truncated"
    if reset_reason:
        state = _empty_state(path)
        offset = 0

    daily = {
        key: dict(value)
        for key, value in dict(state.get("daily") or {}).items()
        if str(value.get("target_date") or "") in retained_dates.get(str(value.get("city") or ""), set())
    }
    lines_read = 0
    start_offset = offset
    with path.open("rb") as handle:
        handle.seek(offset)
        while True:
            line_start = handle.tell()
            raw_line = handle.readline()
            if not raw_line:
                break
            if not raw_line.endswith(b"\n"):
                offset = line_start
                break
            offset = handle.tell()
            lines_read += 1
            try:
                row = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(row, dict):
                _update_daily_row(
                    daily,
                    row,
                    retained_dates=retained_dates,
                    city_profiles=city_profiles,
                )

    refreshed = {
        "schema_version": STATE_SCHEMA,
        "source_path": str(path),
        "file_identity": identity,
        "byte_offset": offset,
        "daily": daily,
    }
    return refreshed, {
        "status": "ok",
        "full_rebuild": bool(start_offset == 0),
        "reset_reason": reset_reason,
        "lines_read": lines_read,
        "bytes_read": max(0, offset - start_offset),
        "byte_offset": offset,
        "file_size": int(stat.st_size),
        "daily_rows": len(daily),
    }


def metar_running_max_from_state(
    state: dict[str, Any], target_dates_by_city: dict[str, str]
) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    daily = dict(state.get("daily") or {})
    for city, target_date in target_dates_by_city.items():
        canonical_city = canonical_city_name(city)
        row = daily.get(f"{canonical_city}|{target_date}")
        if row is not None:
            out[(canonical_city, target_date)] = dict(row)
    return out


def _cadence_gaps(reports: list[datetime]) -> list[float]:
    return [
        (current - previous).total_seconds() / 60.0
        for previous, current in zip(reports, reports[1:])
        if 15.0 <= (current - previous).total_seconds() / 60.0 <= 90.0
    ]


def _nearest_expected_report(latest: datetime, cadence_min: float, now: datetime | None) -> datetime:
    if now is None or now <= latest:
        return latest + timedelta(minutes=cadence_min)
    elapsed_periods = (now - latest).total_seconds() / 60.0 / cadence_min
    steps = max(1, int(round(elapsed_periods)))
    return latest + timedelta(minutes=cadence_min * steps)


def metar_report_clocks_from_state(
    state: dict[str, Any],
    target_dates_by_city: dict[str, str],
    *,
    now: datetime | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Infer current-day clocks, warm-starting from the previous local day."""
    daily = dict(state.get("daily") or {})
    clocks: dict[tuple[str, str], dict[str, Any]] = {}
    for city, target_text in target_dates_by_city.items():
        canonical_city = canonical_city_name(city)
        try:
            target = date.fromisoformat(target_text)
        except ValueError:
            continue
        current = daily.get(f"{canonical_city}|{target_text}") or {}
        previous = daily.get(f"{canonical_city}|{(target - timedelta(days=1)).isoformat()}") or {}
        current_reports = sorted(
            report for value in current.get("routine_report_ts_utc") or [] if (report := parse_utc(value)) is not None
        )
        previous_reports = sorted(
            report for value in previous.get("routine_report_ts_utc") or [] if (report := parse_utc(value)) is not None
        )
        current_gaps = _cadence_gaps(current_reports)
        if len(current_reports) >= 3 and len(current_gaps) >= 2:
            basis_reports = current_reports
            gaps = current_gaps
            clock_source = "current_day"
        else:
            basis_reports = sorted(previous_reports[-8:] + current_reports)
            gaps = _cadence_gaps(basis_reports)
            clock_source = "cross_day_warm_start"
        if len(basis_reports) < 3 or len(gaps) < 2:
            continue
        cadence_min = float(median(gaps[-8:]))
        latest_report = current_reports[-1] if current_reports else basis_reports[-1]
        next_report = _nearest_expected_report(latest_report, cadence_min, now)
        clocks[(canonical_city, target_text)] = {
            "routine_metar_cadence_min": round(cadence_min, 3),
            "latest_routine_metar_report_ts_utc": latest_report.isoformat(),
            "next_expected_metar_report_ts_utc": next_report.isoformat(),
            "routine_metar_report_count": len(current_reports),
            "routine_metar_report_count_basis": len(basis_reports),
            "routine_metar_clock_source": clock_source,
            "routine_metar_warm_start_report_count": len(previous_reports) if clock_source == "cross_day_warm_start" else 0,
        }
    return clocks
