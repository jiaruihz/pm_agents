#!/usr/bin/env python3
"""Build runway-vs-METAR/WU alignment rows for microclimate research."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
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

ARTIFACT_FAMILY = "runway_metar_wu_alignment_v1"

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


def default_runtime_root() -> Path:
    return load_production_spec().data_feed_runtime_root


def read_json_or_jsonl(
    path: Path,
    *,
    partition_filename: str = "runway_observations.jsonl",
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = payload.get("records") if isinstance(payload, dict) else payload
        return [row for row in records or [] if isinstance(row, dict)]
    rows: list[dict[str, Any]] = []
    paths = dated_jsonl_paths(
        path,
        filename=partition_filename,
        allow_missing=True,
    )
    for source_path in paths:
        with source_path.open(encoding="utf-8") as fh:
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


def observation_time(row: dict[str, Any]) -> datetime | None:
    return parse_dt(row.get("source_report_ts_utc") or row.get("observation_time_utc") or row.get("last_obs_utc"))


def nearest_source_event(
    runway: dict[str, Any],
    source_rows: list[dict[str, Any]],
    *,
    wanted_type: str,
    max_abs_lag_sec: float,
) -> dict[str, Any] | None:
    runway_dt = parse_dt(runway.get("observation_time_utc"))
    if runway_dt is None:
        return None
    city = str(runway.get("city") or "")
    station = str(runway.get("station") or "").upper()
    candidates: list[tuple[float, dict[str, Any]]] = []
    for row in source_rows:
        if str(row.get("city") or "") != city:
            continue
        if station and str(row.get("station") or "").upper() not in {"", station}:
            continue
        if source_type(str(row.get("source") or "")) != wanted_type:
            continue
        temp_c = safe_float(row.get("temp_c"))
        obs_dt = observation_time(row)
        if temp_c is None or obs_dt is None:
            continue
        lag_sec = (runway_dt - obs_dt).total_seconds()
        if abs(lag_sec) <= max_abs_lag_sec:
            candidates.append((abs(lag_sec), row))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[0][1]


def build_alignment_rows(
    runway_rows: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
    *,
    max_abs_lag_sec: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for runway in runway_rows:
        runway_temp_c = safe_float(runway.get("point_temp_c") or runway.get("target_runway_temp_c"))
        runway_dt = parse_dt(runway.get("observation_time_utc"))
        if runway_temp_c is None or runway_dt is None:
            continue
        for wanted in ("metar_like", "wu_like"):
            source = nearest_source_event(runway, source_rows, wanted_type=wanted, max_abs_lag_sec=max_abs_lag_sec)
            if source is None:
                continue
            source_temp_c = safe_float(source.get("temp_c"))
            source_dt = observation_time(source)
            if source_temp_c is None or source_dt is None:
                continue
            lag_sec = (runway_dt - source_dt).total_seconds()
            out.append(
                {
                    "city": runway.get("city"),
                    "station": runway.get("station"),
                    "target_date": runway.get("target_date"),
                    "runway_source": runway.get("source"),
                    "runway": runway.get("runway"),
                    "runway_obs_time_utc": runway_dt.isoformat(),
                    "runway_temp_c": runway_temp_c,
                    "runway_temp_min_c": runway.get("runway_temp_min_c"),
                    "runway_temp_max_c": runway.get("runway_temp_max_c"),
                    "is_configured_runway_target": runway.get("is_configured_runway_target"),
                    "reference_type": wanted,
                    "reference_source": source.get("source"),
                    "reference_obs_time_utc": source_dt.isoformat(),
                    "reference_temp_c": source_temp_c,
                    "runway_minus_reference_c": round(runway_temp_c - source_temp_c, 3),
                    "runway_minus_reference_abs_c": round(abs(runway_temp_c - source_temp_c), 3),
                    "runway_lag_vs_reference_sec": lag_sec,
                    "reference_payload_hash": source.get("payload_hash") or source.get("raw_payload_hash"),
                    "runway_payload_hash": runway.get("payload_hash") or runway.get("raw_payload_hash"),
                }
            )
    return out


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("city")), str(row.get("runway_source")), str(row.get("reference_type")))
        groups.setdefault(key, []).append(row)
    summary: list[dict[str, Any]] = []
    for (city, runway_source, reference_type), group in sorted(groups.items()):
        deltas = [float(row["runway_minus_reference_c"]) for row in group]
        abs_deltas = [abs(value) for value in deltas]
        lags = [float(row["runway_lag_vs_reference_sec"]) for row in group]
        summary.append(
            {
                "city": city,
                "runway_source": runway_source,
                "reference_type": reference_type,
                "n": len(group),
                "mean_delta_c": round(statistics.fmean(deltas), 3),
                "median_delta_c": round(statistics.median(deltas), 3),
                "mean_abs_delta_c": round(statistics.fmean(abs_deltas), 3),
                "median_abs_delta_c": round(statistics.median(abs_deltas), 3),
                "median_lag_sec": round(statistics.median(lags), 3),
            }
        )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    runtime_root = default_runtime_root()
    parser.add_argument("--runway-path", default=str(runtime_root / "output/runway_observations"))
    parser.add_argument("--source-events-path", default=str(runtime_root / "output/source_events"))
    parser.add_argument("--run-id")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--max-abs-lag-min", type=float, default=45.0)
    args = parser.parse_args(argv)
    output = resolve_run_output(
        ARTIFACT_FAMILY,
        run_id=args.run_id,
        explicit_output=args.out_dir,
    )

    runway_rows = read_json_or_jsonl(Path(args.runway_path).expanduser())
    source_rows = read_json_or_jsonl(
        Path(args.source_events_path).expanduser(),
        partition_filename="sources.jsonl",
    )
    alignment = build_alignment_rows(
        runway_rows,
        source_rows,
        max_abs_lag_sec=float(args.max_abs_lag_min) * 60.0,
    )
    summary = summarize(alignment)
    out_dir = prepare_new_run_output(output)
    write_csv(out_dir / "alignment_rows.csv", alignment)
    write_csv(out_dir / "summary_by_city_source.csv", summary)
    print(
        json.dumps(
            {
                "runway_rows": len(runway_rows),
                "source_rows": len(source_rows),
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
