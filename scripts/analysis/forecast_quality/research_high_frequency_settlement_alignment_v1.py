#!/usr/bin/env python3
"""Align high-frequency airport/reference observations with METAR/WU and settlement outcomes."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weather_data_feed.jsonl_partitions import dated_jsonl_paths


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RUNTIME_ROOTS = [
    Path("/Volumes/jrs/weather_data_feed_service_runtime"),
    Path("~/projects/weather_data_feed_service_runtime").expanduser(),
]
METAR_LIKE_SOURCES = {
    "aviationweather_metar",
    "aviationweather_cache_csv",
    "iem_asos",
    "iem_asos_latest_raw",
    "iem_asos_routine_latest",
    "iem_asos_madishf_latest",
    "noaa_tgftp_station_txt",
    "weather_gov_latest",
    "synopticdata_timeseries",
}
WU_LIKE_SOURCES = {"weather_com_current", "weather_com_history_hourly"}


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def safe_float(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def default_runtime_root() -> Path:
    for root in DEFAULT_RUNTIME_ROOTS:
        if root.exists():
            return root
    return DEFAULT_RUNTIME_ROOTS[-1]


def read_json_or_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.is_dir():
        return [
            row
            for shard in dated_jsonl_paths(
                path,
                filename="high_frequency_observations.jsonl",
                allow_missing=True,
            )
            for row in read_json_or_jsonl(shard)
        ]
    if path.suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = payload.get("records") if isinstance(payload, dict) else payload
        return [row for row in records or [] if isinstance(row, dict)]
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def source_type(source: str) -> str:
    if source in METAR_LIKE_SOURCES:
        return "metar_like"
    if source in WU_LIKE_SOURCES:
        return "wu_like"
    return "other"


def source_event_time(row: dict[str, Any]) -> datetime | None:
    return parse_dt(row.get("source_report_ts_utc") or row.get("observation_time_utc") or row.get("last_obs_utc"))


def nearest_source_event(
    obs: dict[str, Any],
    source_rows: list[dict[str, Any]],
    *,
    wanted_type: str,
    max_abs_lag_sec: float,
) -> dict[str, Any] | None:
    obs_dt = parse_dt(obs.get("observation_time_utc"))
    if obs_dt is None:
        return None
    city = str(obs.get("city") or "")
    station = str(obs.get("icao") or obs.get("station") or "").upper()
    candidates: list[tuple[float, dict[str, Any]]] = []
    for row in source_rows:
        if str(row.get("city") or "") != city:
            continue
        row_station = str(row.get("station") or "").upper()
        if station and row_station and row_station not in {station, str(obs.get("station") or "").upper()}:
            continue
        if source_type(str(row.get("source") or "")) != wanted_type:
            continue
        temp_c = safe_float(row.get("temp_c"))
        event_dt = source_event_time(row)
        if temp_c is None or event_dt is None:
            continue
        lag_sec = (obs_dt - event_dt).total_seconds()
        if abs(lag_sec) <= max_abs_lag_sec:
            candidates.append((abs(lag_sec), row))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[0][1]


def load_settlement_outcomes(db_path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    try:
        rows = conn.execute(
            """
            SELECT city, target_date, unit,
                   GROUP_CONCAT(CASE WHEN final_price >= 0.999 THEN bracket END, '|') AS winning_brackets,
                   COUNT(*) AS market_rows,
                   SUM(CASE WHEN settlement_status = 'settled' THEN 1 ELSE 0 END) AS settled_rows
            FROM settlement_outcomes
            GROUP BY city, target_date, unit
            """
        ).fetchall()
    finally:
        conn.close()
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for city, target_date, unit, winning_brackets, market_rows, settled_rows in rows:
        out[(str(city), str(target_date))] = {
            "settlement_unit": unit,
            "winning_brackets": winning_brackets or "",
            "settlement_market_rows": market_rows,
            "settled_market_rows": settled_rows,
        }
    return out


def build_alignment_rows(
    high_freq_rows: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
    settlements: dict[tuple[str, str], dict[str, Any]],
    *,
    max_abs_lag_sec: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for obs in high_freq_rows:
        obs_temp_c = safe_float(obs.get("temp_c") or obs.get("point_temp_c"))
        obs_dt = parse_dt(obs.get("observation_time_utc"))
        if obs_temp_c is None or obs_dt is None:
            continue
        settlement = settlements.get((str(obs.get("city") or ""), str(obs.get("target_date") or "")), {})
        matched_any = False
        for wanted in ("metar_like", "wu_like"):
            source = nearest_source_event(obs, source_rows, wanted_type=wanted, max_abs_lag_sec=max_abs_lag_sec)
            if source is None:
                continue
            source_temp_c = safe_float(source.get("temp_c"))
            source_dt = source_event_time(source)
            if source_temp_c is None or source_dt is None:
                continue
            matched_any = True
            lag_sec = (obs_dt - source_dt).total_seconds()
            out.append(
                {
                    "city": obs.get("city"),
                    "target_date": obs.get("target_date"),
                    "high_freq_source": obs.get("source"),
                    "source_kind": obs.get("source_kind"),
                    "station": obs.get("station"),
                    "icao": obs.get("icao"),
                    "observation_time_utc": obs_dt.isoformat(),
                    "high_freq_temp_c": obs_temp_c,
                    "high_freq_temp_round_f": obs.get("temp_round_f"),
                    "reference_type": wanted,
                    "reference_source": source.get("source"),
                    "reference_obs_time_utc": source_dt.isoformat(),
                    "reference_temp_c": source_temp_c,
                    "high_freq_minus_reference_c": round(obs_temp_c - source_temp_c, 3),
                    "high_freq_minus_reference_abs_c": round(abs(obs_temp_c - source_temp_c), 3),
                    "high_freq_lag_vs_reference_sec": lag_sec,
                    "winning_brackets": settlement.get("winning_brackets", ""),
                    "settlement_unit": settlement.get("settlement_unit", ""),
                    "settlement_market_rows": settlement.get("settlement_market_rows", 0),
                    "settled_market_rows": settlement.get("settled_market_rows", 0),
                    "reference_payload_hash": source.get("payload_hash") or source.get("raw_payload_hash"),
                    "high_freq_payload_hash": obs.get("payload_hash") or obs.get("raw_payload_hash"),
                }
            )
        if not matched_any and settlement:
            out.append(
                {
                    "city": obs.get("city"),
                    "target_date": obs.get("target_date"),
                    "high_freq_source": obs.get("source"),
                    "source_kind": obs.get("source_kind"),
                    "station": obs.get("station"),
                    "icao": obs.get("icao"),
                    "observation_time_utc": obs_dt.isoformat(),
                    "high_freq_temp_c": obs_temp_c,
                    "high_freq_temp_round_f": obs.get("temp_round_f"),
                    "reference_type": "settlement_only",
                    "reference_source": "",
                    "reference_obs_time_utc": "",
                    "reference_temp_c": "",
                    "high_freq_minus_reference_c": "",
                    "high_freq_minus_reference_abs_c": "",
                    "high_freq_lag_vs_reference_sec": "",
                    "winning_brackets": settlement.get("winning_brackets", ""),
                    "settlement_unit": settlement.get("settlement_unit", ""),
                    "settlement_market_rows": settlement.get("settlement_market_rows", 0),
                    "settled_market_rows": settlement.get("settled_market_rows", 0),
                    "reference_payload_hash": "",
                    "high_freq_payload_hash": obs.get("payload_hash") or obs.get("raw_payload_hash"),
                }
            )
    return out


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("reference_type") == "settlement_only":
            continue
        key = (str(row.get("city")), str(row.get("high_freq_source")), str(row.get("reference_type")))
        groups.setdefault(key, []).append(row)
    summary: list[dict[str, Any]] = []
    for (city, high_freq_source, reference_type), group in sorted(groups.items()):
        deltas = [float(row["high_freq_minus_reference_c"]) for row in group if row.get("high_freq_minus_reference_c") != ""]
        abs_deltas = [abs(value) for value in deltas]
        lags = [float(row["high_freq_lag_vs_reference_sec"]) for row in group if row.get("high_freq_lag_vs_reference_sec") != ""]
        if not deltas:
            continue
        summary.append(
            {
                "city": city,
                "high_freq_source": high_freq_source,
                "reference_type": reference_type,
                "n": len(group),
                "mean_delta_c": round(statistics.fmean(deltas), 3),
                "median_delta_c": round(statistics.median(deltas), 3),
                "mean_abs_delta_c": round(statistics.fmean(abs_deltas), 3),
                "median_abs_delta_c": round(statistics.median(abs_deltas), 3),
                "median_lag_sec": round(statistics.median(lags), 3) if lags else "",
            }
        )
    return summary


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    runtime_root = default_runtime_root()
    parser.add_argument(
        "--high-frequency-path",
        default=str(runtime_root / "output/high_frequency_observations"),
    )
    parser.add_argument("--source-events-path", default=str(runtime_root / "output/source_events/sources.jsonl"))
    parser.add_argument("--db-path", default=str(ROOT / "runtime/weather.db"))
    parser.add_argument("--out-dir", default=str(ROOT / "docs/analysis/2026-07/generated/high_frequency_settlement_alignment_v1"))
    parser.add_argument("--max-abs-lag-min", type=float, default=45.0)
    args = parser.parse_args()

    high_freq_rows = read_json_or_jsonl(Path(args.high_frequency_path).expanduser())
    source_rows = read_json_or_jsonl(Path(args.source_events_path).expanduser())
    settlements = load_settlement_outcomes(Path(args.db_path).expanduser())
    alignment = build_alignment_rows(
        high_freq_rows,
        source_rows,
        settlements,
        max_abs_lag_sec=float(args.max_abs_lag_min) * 60.0,
    )
    summary = summarize(alignment)
    out_dir = Path(args.out_dir)
    write_csv(out_dir / "alignment_rows.csv", alignment)
    write_csv(out_dir / "summary_by_city_source.csv", summary)
    print(
        json.dumps(
            {
                "high_frequency_rows": len(high_freq_rows),
                "source_rows": len(source_rows),
                "settlement_city_dates": len(settlements),
                "alignment_rows": len(alignment),
                "summary_rows": len(summary),
                "out_dir": str(out_dir),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
