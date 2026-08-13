#!/usr/bin/env python3
"""Align high-frequency airport/reference observations with METAR/WU and settlement outcomes."""

from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.jsonl_partitions import dated_jsonl_paths  # noqa: E402
from scripts.analysis.forecast_quality.source_alignment_common import (  # noqa: E402
    parse_dt,
    write_csv,
)
from scripts.analysis.versioned_artifact_output import (  # noqa: E402
    prepare_new_run_output,
    resolve_run_output,
)
from src.strategies.runtime.production import load_production_spec  # noqa: E402

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


def safe_float(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def read_json_or_jsonl(
    path: Path,
    *,
    partition_filename: str = "high_frequency_observations.jsonl",
    shard_dates: set[str] | None = None,
    dedupe_observations: bool = False,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = payload.get("records") if isinstance(payload, dict) else payload
        return [row for row in records or [] if isinstance(row, dict)]
    paths = (
        dated_jsonl_paths(
            path,
            filename=partition_filename,
            allow_missing=True,
        )
        if path.is_dir()
        else (path,)
    )
    if shard_dates is not None:
        paths = tuple(shard for shard in paths if shard.parent.name in shard_dates)
    rows: list[dict[str, Any]] = []
    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for source_path in paths:
        with source_path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                if isinstance(row, dict):
                    if not dedupe_observations:
                        rows.append(row)
                        continue
                    temperature = row.get("temp_c")
                    if temperature is None:
                        temperature = row.get("point_temp_c")
                    event_time = (
                        row.get("observation_time_utc")
                        or row.get("source_report_ts_utc")
                        or row.get("last_obs_utc")
                    )
                    key = (
                        row.get("city"),
                        row.get("source"),
                        row.get("station") or row.get("icao"),
                        event_time,
                        temperature,
                    )
                    deduped.setdefault(key, row)
    return list(deduped.values()) if dedupe_observations else rows


def source_shard_dates(high_freq_rows: list[dict[str, Any]]) -> set[str]:
    """Return UTC partitions that can overlap the high-frequency events."""
    dates: set[str] = set()
    for row in high_freq_rows:
        event_dt = parse_dt(row.get("observation_time_utc"))
        if event_dt is None:
            continue
        for offset in (-1, 0, 1):
            dates.add((event_dt + timedelta(days=offset)).date().isoformat())
    return dates


def source_type(source: str) -> str:
    if source in METAR_LIKE_SOURCES:
        return "metar_like"
    if source in WU_LIKE_SOURCES:
        return "wu_like"
    return "other"


def source_event_time(row: dict[str, Any]) -> datetime | None:
    return parse_dt(row.get("source_report_ts_utc") or row.get("observation_time_utc") or row.get("last_obs_utc"))


SourceIndex = dict[tuple[str, str], tuple[list[datetime], list[dict[str, Any]]]]


def build_source_index(source_rows: list[dict[str, Any]]) -> SourceIndex:
    grouped: dict[tuple[str, str], list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
    for row in source_rows:
        kind = source_type(str(row.get("source") or ""))
        event_dt = source_event_time(row)
        if kind == "other" or event_dt is None or safe_float(row.get("temp_c")) is None:
            continue
        grouped[(str(row.get("city") or ""), kind)].append((event_dt, row))
    index: SourceIndex = {}
    for key, pairs in grouped.items():
        pairs.sort(key=lambda item: item[0])
        index[key] = ([item[0] for item in pairs], [item[1] for item in pairs])
    return index


def nearest_source_event(
    obs: dict[str, Any],
    source_rows: list[dict[str, Any]],
    *,
    wanted_type: str,
    max_abs_lag_sec: float,
    source_index: SourceIndex | None = None,
) -> dict[str, Any] | None:
    obs_dt = parse_dt(obs.get("observation_time_utc"))
    if obs_dt is None:
        return None
    city = str(obs.get("city") or "")
    station = str(obs.get("icao") or obs.get("station") or "").upper()
    index = source_index if source_index is not None else build_source_index(source_rows)
    times, rows = index.get((city, wanted_type), ([], []))
    lower = obs_dt - timedelta(seconds=max_abs_lag_sec)
    upper = obs_dt + timedelta(seconds=max_abs_lag_sec)
    start = bisect.bisect_left(times, lower)
    stop = bisect.bisect_right(times, upper)
    candidates: list[tuple[float, dict[str, Any]]] = []
    for event_dt, row in zip(times[start:stop], rows[start:stop], strict=True):
        row_station = str(row.get("station") or "").upper()
        if station and row_station and row_station not in {station, str(obs.get("station") or "").upper()}:
            continue
        lag_sec = (obs_dt - event_dt).total_seconds()
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
    source_index = build_source_index(source_rows)
    for obs in high_freq_rows:
        obs_temp_c = safe_float(obs.get("temp_c") or obs.get("point_temp_c"))
        obs_dt = parse_dt(obs.get("observation_time_utc"))
        if obs_temp_c is None or obs_dt is None:
            continue
        settlement = settlements.get((str(obs.get("city") or ""), str(obs.get("target_date") or "")), {})
        matched_any = False
        for wanted in ("metar_like", "wu_like"):
            source = nearest_source_event(
                obs,
                source_rows,
                wanted_type=wanted,
                max_abs_lag_sec=max_abs_lag_sec,
                source_index=source_index,
            )
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    spec = load_production_spec()
    runtime_root = spec.data_feed_runtime_root
    parser.add_argument(
        "--high-frequency-path",
        default=str(runtime_root / "output/high_frequency_observations"),
    )
    parser.add_argument("--source-events-path", default=str(runtime_root / "output/source_events"))
    parser.add_argument("--db-path", default=str(spec.canonical_db_path))
    parser.add_argument("--run-id", help="stable immutable artifact run identity")
    parser.add_argument("--output-dir", "--out-dir", dest="output_dir")
    parser.add_argument("--max-abs-lag-min", type=float, default=45.0)
    args = parser.parse_args(argv)

    high_freq_rows = read_json_or_jsonl(
        Path(args.high_frequency_path).expanduser(),
        dedupe_observations=True,
    )
    source_rows = read_json_or_jsonl(
        Path(args.source_events_path).expanduser(),
        partition_filename="sources.jsonl",
        shard_dates=source_shard_dates(high_freq_rows),
        dedupe_observations=True,
    )
    settlements = load_settlement_outcomes(Path(args.db_path).expanduser())
    alignment = build_alignment_rows(
        high_freq_rows,
        source_rows,
        settlements,
        max_abs_lag_sec=float(args.max_abs_lag_min) * 60.0,
    )
    summary = summarize(alignment)
    out_dir = resolve_run_output(
        "high_frequency_settlement_alignment_v1",
        run_id=args.run_id,
        explicit_output=Path(args.output_dir) if args.output_dir else None,
    )
    prepare_new_run_output(out_dir)
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
