#!/usr/bin/env python3
"""Fetch immutable ECMWF day-1 Amsterdam forecast paths for model parity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


API = "https://previous-runs-api.open-meteo.com/v1/forecast"
DEFAULT_OUTPUT = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/research/"
    "amsterdam_ecmwf_previous_day1_path_v1"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def fetch(start: str, end: str) -> tuple[dict, str]:
    params = {
        "latitude": 52.3105,
        "longitude": 4.7683,
        "start_date": start,
        "end_date": end,
        "timezone": "Europe/Amsterdam",
        "models": "ecmwf_ifs025",
        "hourly": "temperature_2m_previous_day1",
    }
    url = f"{API}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "pm-agents-weather-research/1"})
    with urlopen(request, timeout=120) as response:
        text = response.read().decode("utf-8")
    return json.loads(text), url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2024-03-01")
    parser.add_argument("--end", default="2026-08-11")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    raw_root = args.output / "raw"
    raw_root.mkdir(exist_ok=True)
    chunks = []
    for year in range(int(args.start[:4]), int(args.end[:4]) + 1):
        start = max(args.start, f"{year}-01-01")
        end = min(args.end, f"{year}-12-31")
        raw = raw_root / f"ecmwf_ifs025_previous_day1_{start}_{end}.json"
        if args.refresh or not raw.is_file():
            payload, url = fetch(start, end)
            atomic_text(raw, json.dumps(payload, separators=(",", ":")) + "\n")
        else:
            payload = json.loads(raw.read_text())
            url = None
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        values = hourly.get("temperature_2m_previous_day1") or []
        if len(times) != len(values):
            raise ValueError(f"hourly shape mismatch for {start}..{end}")
        chunks.extend(
            {"target_date": str(time)[:10], "forecast_time_local": time,
             "forecast_temperature_c": value,
             "forecast_model": "ecmwf_ifs025", "forecast_lead_days": 1,
             "forecast_source": "open_meteo_previous_runs",
             "forecast_lineage_class": "fixed_lead_previous_day1"}
            for time, value in zip(times, values) if value is not None
        )
    frame = pd.DataFrame(chunks).sort_values(["target_date", "forecast_time_local"])
    duplicate = int(frame.duplicated(["target_date", "forecast_time_local"]).sum())
    if duplicate:
        raise ValueError(f"duplicate forecast hours: {duplicate}")
    dataset = args.output / "forecast_hourly.csv.gz"
    frame.to_csv(dataset, index=False, compression="gzip")
    counts = frame.groupby("target_date").size()
    manifest = {
        "schema_version": "open_meteo_ecmwf_previous_day1_curve_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "api": API, "model": "ecmwf_ifs025", "lead_days": 1,
        "start_date": args.start, "end_date": args.end,
        "rows": int(len(frame)), "target_dates": int(frame.target_date.nunique()),
        "complete_target_dates": int(counts.eq(24).sum()),
        "incomplete_target_dates": [str(value) for value in counts[counts.ne(24)].index],
        "dataset_path": str(dataset), "dataset_sha256": sha256(dataset),
        "raw_files": [{"path": str(path), "sha256": sha256(path)} for path in sorted(raw_root.glob("*.json"))],
        "pit_semantics": "each valid hour was predicted 24 hours earlier; target-day observations and later forecast runs are excluded",
    }
    atomic_text(args.output / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
