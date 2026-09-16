#!/usr/bin/env python3
"""Materialize canonical weather observation and intraday state rows.

This is the bridge away from dated research patch CSVs.  It ingests normalized
WU-like/IEM observation caches into ``runtime/weather.db`` and then builds
city/date/hour physical state rows from the DB.  Optional legacy CSV output is
only a compatibility adapter for older research scripts.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.observed_max.research_m3_observed_max_residual import CITY_TIMEZONE  # noqa: E402
from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical  # noqa: E402
from weather_clock_contract import (  # noqa: E402
    local_wall_time_to_utc,
    parse_utc_or_none,
)


DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_STATION_SUMMARY = ROOT / "docs/analysis/2026-06/generated/theta_no_wu_obs_patch_v1/summary.json"
DEFAULT_HOURS = tuple(range(10, 22))


@dataclass(frozen=True)
class Station:
    city: str
    icao: str
    unit: str
    timezone_name: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--station-summary", default=str(DEFAULT_STATION_SUMMARY))
    parser.add_argument("--wu-dir", required=True)
    parser.add_argument("--source-system", default="iem_asos_wu_like_cache")
    parser.add_argument("--source-path", default="")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--decision-hours", default=",".join(str(h) for h in DEFAULT_HOURS))
    parser.add_argument("--min-obs-per-day", type=int, default=8)
    parser.add_argument("--legacy-observed-detail-out", default="")
    parser.add_argument("--summary-json", default="")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: Any) -> datetime | None:
    return parse_utc_or_none(value, field="observation_state_clock")


def as_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def f_to_c(value: float) -> float:
    return (value - 32.0) * 5.0 / 9.0


def c_to_f(value: float) -> float:
    return value * 9.0 / 5.0 + 32.0


def sha256_json(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_stations(path: Path) -> list[Station]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("stations") or []
    out: list[Station] = []
    for row in rows:
        city = str(row.get("city") or "")
        icao = str(row.get("icao") or "").upper()
        if not city or not icao:
            continue
        tz = CITY_TIMEZONE.get(city)
        if not tz:
            continue
        out.append(Station(city=city, icao=icao, unit=str(row.get("unit") or "").upper(), timezone_name=tz))
    return out


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    apply_schema_canonical(conn)
    return conn


def read_cache_rows(path: Path, station: Station, start: str, end: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    tz = ZoneInfo(station.timezone_name)
    with path.open("r", encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            valid = parse_utc(raw.get("valid_utc") or raw.get("valid"))
            temp_f = as_float(raw.get("temp") or raw.get("tmpf"))
            if valid is None or temp_f is None:
                continue
            target_date = str(raw.get("date_local") or valid.astimezone(tz).date().isoformat())
            if target_date < start or target_date > end:
                continue
            dewpoint_f = as_float(raw.get("dewpt") or raw.get("dwpf"))
            row = {
                "city": station.city,
                "icao": station.icao,
                "timezone": station.timezone_name,
                "target_date": target_date,
                "obs_ts_utc": valid.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "unit": station.unit,
                "temp_f": float(temp_f),
                "temp_c": f_to_c(float(temp_f)),
                "dewpoint_f": dewpoint_f,
                "wind_speed_kt": as_float(raw.get("wspd") or raw.get("sknt")),
                "wind_dir_deg": as_float(raw.get("wdir") or raw.get("drct")),
                "sky_cover": str(raw.get("wx_phrase") or raw.get("skyc1") or "").strip(),
                "raw_payload": raw,
            }
            out.append(row)
    return sorted(out, key=lambda item: item["obs_ts_utc"])


def ingest_observations(
    conn: sqlite3.Connection,
    *,
    stations: list[Station],
    wu_dir: Path,
    source_system: str,
    source_path: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    inserted = 0
    station_rows = []
    for station in stations:
        path = wu_dir / f"wu_obs_{station.icao}.csv"
        rows = read_cache_rows(path, station, start, end)
        station_rows.append({"city": station.city, "icao": station.icao, "rows": len(rows), "cache_exists": path.exists()})
        for row in rows:
            hash_payload = {
                "source_system": source_system,
                "city": row["city"],
                "icao": row["icao"],
                "obs_ts_utc": row["obs_ts_utc"],
                "temp_f": row["temp_f"],
                "source_path": source_path or str(path),
            }
            row_hash = sha256_json(hash_payload)
            observation_id = sha256_json({"table": "weather_observation_events", "source_row_hash": row_hash})
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO weather_observation_events (
                    observation_id, source_system, source_path, source_row_hash,
                    city, icao, timezone, target_date, obs_ts_utc, fetched_at_utc,
                    source_report_ts_utc, unit, temp_f, temp_c, dewpoint_f,
                    wind_speed_kt, wind_dir_deg, sky_cover, raw_payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    source_system,
                    source_path or str(path),
                    row_hash,
                    row["city"],
                    row["icao"],
                    row["timezone"],
                    row["target_date"],
                    row["obs_ts_utc"],
                    utc_now(),
                    row["obs_ts_utc"],
                    row["unit"],
                    row["temp_f"],
                    row["temp_c"],
                    row["dewpoint_f"],
                    row["wind_speed_kt"],
                    row["wind_dir_deg"],
                    row["sky_cover"],
                    json.dumps(row["raw_payload"], sort_keys=True, ensure_ascii=False),
                ),
            )
            inserted += int(cur.rowcount or 0)
    conn.commit()
    return {"inserted_observation_events": inserted, "station_rows": station_rows}


def query_observations(conn: sqlite3.Connection, source_system: str, start: str, end: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM weather_observation_events
        WHERE source_system = ?
          AND target_date BETWEEN ? AND ?
        ORDER BY city, target_date, obs_ts_utc
        """,
        (source_system, start, end),
    ).fetchall()


def latest_at_or_before(rows: list[sqlite3.Row], cutoff: datetime) -> sqlite3.Row | None:
    best = None
    for row in rows:
        ts = parse_utc(row["obs_ts_utc"])
        if ts is None or ts > cutoff:
            continue
        best = row
    return best


def trend_f(rows: list[sqlite3.Row], current_row: sqlite3.Row, cutoff: datetime, hours: int) -> float | None:
    baseline = latest_at_or_before(rows, cutoff.replace(tzinfo=cutoff.tzinfo) - pd_timedelta(hours=hours))
    if baseline is None:
        return None
    cur = as_float(current_row["temp_f"])
    base = as_float(baseline["temp_f"])
    if cur is None or base is None:
        return None
    return cur - base


def pd_timedelta(*, hours: int) -> Any:
    from datetime import timedelta

    return timedelta(hours=hours)


def build_state_rows(
    conn: sqlite3.Connection,
    *,
    source_system: str,
    source_path: str,
    start: str,
    end: str,
    hours: list[int],
    min_obs_per_day: int,
) -> list[dict[str, Any]]:
    rows = query_observations(conn, source_system, start, end)
    by_key: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in rows:
        by_key.setdefault((str(row["city"]), str(row["target_date"])), []).append(row)

    state_rows: list[dict[str, Any]] = []
    for (city, target_date), day_rows in sorted(by_key.items()):
        if len(day_rows) < min_obs_per_day:
            continue
        station = str(day_rows[0]["icao"])
        timezone_name = str(day_rows[0]["timezone"] or CITY_TIMEZONE.get(city) or "UTC")
        final_max_f = max(float(row["temp_f"]) for row in day_rows if row["temp_f"] is not None)
        final_max_c = f_to_c(final_max_f)
        first_obs = str(day_rows[0]["obs_ts_utc"])
        last_obs = str(day_rows[-1]["obs_ts_utc"])
        for hour in hours:
            cutoff_utc = local_wall_time_to_utc(
                f"{target_date}T{hour:02d}:00:00",
                timezone_name=timezone_name,
                field="decision_hour_local",
            )
            cutoff_local = cutoff_utc.astimezone(ZoneInfo(timezone_name))
            prefix = [row for row in day_rows if (parse_utc(row["obs_ts_utc"]) or datetime.max.replace(tzinfo=timezone.utc)) <= cutoff_utc]
            if not prefix:
                continue
            current = prefix[-1]
            current_f = float(current["temp_f"])
            current_c = f_to_c(current_f)
            running_max_f = max(float(row["temp_f"]) for row in prefix if row["temp_f"] is not None)
            running_max_c = f_to_c(running_max_f)
            max_rows = [row for row in prefix if abs(float(row["temp_f"]) - running_max_f) < 1e-9]
            max_ts = parse_utc(max_rows[-1]["obs_ts_utc"]) if max_rows else None
            minutes_since_max = (cutoff_utc - max_ts).total_seconds() / 60.0 if max_ts else None
            residual_c = final_max_c - running_max_c
            state = {
                "source_system": source_system,
                "source_path": source_path,
                "city": city,
                "icao": station,
                "timezone": timezone_name,
                "target_date": target_date,
                "decision_hour_local": hour,
                "decision_cutoff_local": cutoff_local.isoformat(timespec="seconds"),
                "decision_last_obs_utc": current["obs_ts_utc"],
                "obs_count_day": len(day_rows),
                "obs_count_to_decision": len(prefix),
                "first_obs_utc": first_obs,
                "last_obs_utc": last_obs,
                "current_temp_f": current_f,
                "current_temp_c": current_c,
                "running_max_f": running_max_f,
                "running_max_c": running_max_c,
                "final_max_f": final_max_f,
                "final_max_c": final_max_c,
                "residual_c": residual_c,
                "residual_ge_0_5c": int(residual_c >= 0.5),
                "residual_ge_1_0c": int(residual_c >= 1.0),
                "residual_ge_1_5c": int(residual_c >= 1.5),
                "floor_c_bucket_delta": int(math.floor(final_max_c) - math.floor(running_max_c)),
                "temp_trend_1h_f": trend_f(day_rows, current, cutoff_utc, 1),
                "temp_trend_3h_f": trend_f(day_rows, current, cutoff_utc, 3),
                "minutes_since_running_max": minutes_since_max,
                "dewpoint_f": as_float(current["dewpoint_f"]),
                "wind_speed_kt": as_float(current["wind_speed_kt"]),
                "wind_dir_deg": as_float(current["wind_dir_deg"]),
                "sky_cover": str(current["sky_cover"] or ""),
            }
            state["state_row_id"] = sha256_json(
                {
                    "table": "weather_intraday_state_rows",
                    "source_system": source_system,
                    "city": city,
                    "target_date": target_date,
                    "decision_hour_local": hour,
                }
            )
            state_rows.append(state)
    return state_rows


def write_state_rows(conn: sqlite3.Connection, state_rows: list[dict[str, Any]]) -> None:
    cols = [
        "state_row_id",
        "source_system",
        "source_path",
        "city",
        "icao",
        "timezone",
        "target_date",
        "decision_hour_local",
        "decision_cutoff_local",
        "decision_last_obs_utc",
        "obs_count_day",
        "obs_count_to_decision",
        "first_obs_utc",
        "last_obs_utc",
        "current_temp_f",
        "current_temp_c",
        "running_max_f",
        "running_max_c",
        "final_max_f",
        "final_max_c",
        "residual_c",
        "residual_ge_0_5c",
        "residual_ge_1_0c",
        "residual_ge_1_5c",
        "floor_c_bucket_delta",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "minutes_since_running_max",
        "dewpoint_f",
        "wind_speed_kt",
        "wind_dir_deg",
        "sky_cover",
    ]
    placeholders = ",".join("?" for _ in cols)
    conn.executemany(
        f"INSERT OR REPLACE INTO weather_intraday_state_rows ({','.join(cols)}) VALUES ({placeholders})",
        [[row.get(col) for col in cols] for row in state_rows],
    )
    conn.commit()


def write_legacy_observed_detail(path: Path, state_rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "city",
        "icao",
        "timezone",
        "target_date",
        "decision_hour_local",
        "obs_count_day",
        "obs_count_to_decision",
        "first_obs_utc",
        "last_obs_utc",
        "decision_last_obs_utc",
        "final_max_f",
        "running_max_f",
        "current_temp_f",
        "final_max_c",
        "running_max_c",
        "current_temp_c",
        "residual_c",
        "residual_ge_0_5c",
        "residual_ge_1_0c",
        "residual_ge_1_5c",
        "floor_c_bucket_delta",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=cols)
        writer.writeheader()
        for row in state_rows:
            writer.writerow({col: row.get(col) for col in cols})


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    stations = load_stations(Path(args.station_summary))
    hours = [int(x) for x in str(args.decision_hours).split(",") if x.strip()]
    source_path = args.source_path or str(Path(args.wu_dir))
    with connect(db_path) as conn:
        ingest_summary = ingest_observations(
            conn,
            stations=stations,
            wu_dir=Path(args.wu_dir),
            source_system=str(args.source_system),
            source_path=source_path,
            start=str(args.start_date),
            end=str(args.end_date),
        )
        state_rows = build_state_rows(
            conn,
            source_system=str(args.source_system),
            source_path=source_path,
            start=str(args.start_date),
            end=str(args.end_date),
            hours=hours,
            min_obs_per_day=int(args.min_obs_per_day),
        )
        write_state_rows(conn, state_rows)

    if args.legacy_observed_detail_out:
        write_legacy_observed_detail(Path(args.legacy_observed_detail_out), state_rows)

    payload = {
        "generated_at_utc": utc_now(),
        "db": str(db_path),
        "source_system": str(args.source_system),
        "source_path": source_path,
        "range": {"start": str(args.start_date), "end": str(args.end_date)},
        "stations_requested": len(stations),
        **ingest_summary,
        "state_rows": len(state_rows),
        "legacy_observed_detail_out": args.legacy_observed_detail_out or None,
    }
    if args.summary_json:
        out = Path(args.summary_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
