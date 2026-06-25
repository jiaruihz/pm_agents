#!/usr/bin/env python3
"""Build a city -> fastest observation source map from timing monitor logs."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SUPPORTED_SOURCES = {"aviationweather_metar", "synopticdata_timeseries", "noaa_tgftp_station_txt"}


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def p50(values: list[float]) -> float:
    return float(statistics.median(values))


def iter_recent_rows(path: Path, *, tail_bytes: int, since_utc: datetime) -> Any:
    proc = subprocess.Popen(["tail", "-c", str(tail_bytes), str(path)], stdout=subprocess.PIPE, text=True, errors="replace")
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = parse_ts(row.get("ts_utc") or row.get("local_detect_ts_utc"))
        if ts and ts >= since_utc:
            yield row
    proc.wait()


def build_winners(path: Path, *, hours: float, min_changed_rows: int, tail_bytes: int) -> dict[str, str]:
    since_utc = datetime.now(timezone.utc) - timedelta(hours=hours)
    by_city_source: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in iter_recent_rows(path, tail_bytes=tail_bytes, since_utc=since_utc):
        if row.get("status") != "ok" or row.get("changed_since_last") is not True:
            continue
        city = str(row.get("city") or "")
        source = str(row.get("source") or "")
        if not city or source not in SUPPORTED_SOURCES:
            continue
        age = row.get("source_age_sec")
        if age is None:
            age = row.get("detected_after_report_sec")
        if isinstance(age, (int, float)):
            by_city_source[(city, source)].append(float(age))

    by_city: dict[str, list[tuple[float, int, str]]] = defaultdict(list)
    for (city, source), values in by_city_source.items():
        if len(values) >= min_changed_rows:
            by_city[city].append((p50(values), len(values), source))
    return {city: sorted(candidates)[0][2] for city, candidates in sorted(by_city.items()) if candidates}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build source-by-city JSON for the METAR crossing bot.")
    parser.add_argument("--input", type=Path, default=Path("runtime/weather_edge_v1/source_orderbook_timing/sources.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("runtime/weather_edge_v1/metar_cross_prev_no_shadow/source_by_city.json"))
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--min-changed-rows", type=int, default=3)
    parser.add_argument("--tail-bytes", type=int, default=900_000_000)
    args = parser.parse_args()

    winners = build_winners(args.input, hours=args.hours, min_changed_rows=args.min_changed_rows, tail_bytes=args.tail_bytes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(winners, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "cities": len(winners), "winners": winners}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
