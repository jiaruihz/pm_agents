#!/usr/bin/env python3
"""Inspect raw paper snapshot evolution for selected weather brackets."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.production_paths import historical_strategy_snapshots


def pick(row: dict[str, Any], name: str) -> Any:
    value = row.get(name)
    if value is None:
        return ""
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--snapshots-dir",
        default=historical_strategy_snapshots(),
        type=Path,
    )
    parser.add_argument("--city", required=True)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--bracket", action="append", required=True)
    parser.add_argument("--start-name", default="")
    parser.add_argument("--end-name", default="")
    args = parser.parse_args()

    brackets = set(args.bracket)
    rows: list[dict[str, Any]] = []
    for path in sorted(args.snapshots_dir.glob("snapshot_*.json")):
        if args.start_name and path.name < args.start_name:
            continue
        if args.end_name and path.name > args.end_name:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for record in payload.get("records", []):
            if not isinstance(record, dict):
                continue
            if record.get("city") != args.city:
                continue
            if record.get("event_date") != args.target_date:
                continue
            if record.get("bracket") not in brackets:
                continue
            rows.append(
                {
                    "snapshot_file": path.name,
                    "ts_utc": pick(record, "ts_utc"),
                    "ts_bj": pick(record, "ts_beijing"),
                    "ts_local": pick(record, "ts_local"),
                    "bracket": pick(record, "bracket"),
                    "side": pick(record, "side"),
                    "model": pick(record, "model"),
                    "forecast_source": pick(record, "forecast_source"),
                    "model_init_utc_estimated": pick(record, "model_init_utc_estimated"),
                    "model_run_age_hours_estimated": pick(record, "model_run_age_hours_estimated"),
                    "forecast_target_lead_hours_estimated": pick(record, "forecast_target_lead_hours_estimated"),
                    "forecast_max_f": pick(record, "forecast_max_f"),
                    "gfs_forecast_f": pick(record, "gfs_forecast_f"),
                    "model_prob": pick(record, "model_prob"),
                    "market_yes_price": pick(record, "market_yes_price"),
                    "entry_price": pick(record, "entry_price"),
                    "edge": pick(record, "edge"),
                    "abs_edge": pick(record, "abs_edge"),
                    "yes_best_bid": pick(record, "yes_best_bid"),
                    "yes_best_ask": pick(record, "yes_best_ask"),
                    "no_best_bid": pick(record, "no_best_bid"),
                    "no_best_ask": pick(record, "no_best_ask"),
                    "metar_current_max_f": pick(record, "metar_current_max_f"),
                    "metar_latest_temp_f": pick(record, "metar_latest_temp_f"),
                    "metar_latest_ts_utc": pick(record, "metar_latest_ts_utc"),
                    "metar_obs_count_today": pick(record, "metar_obs_count_today"),
                    "probability_status": pick(record, "probability_status"),
                    "window": pick(record, "window"),
                    "hours_to_settle": pick(record, "hours_to_settle"),
                }
            )

    fieldnames = [
        "snapshot_file",
        "ts_utc",
        "ts_bj",
        "ts_local",
        "bracket",
        "side",
        "model",
        "forecast_source",
        "model_init_utc_estimated",
        "model_run_age_hours_estimated",
        "forecast_target_lead_hours_estimated",
        "forecast_max_f",
        "gfs_forecast_f",
        "model_prob",
        "market_yes_price",
        "entry_price",
        "edge",
        "abs_edge",
        "yes_best_bid",
        "yes_best_ask",
        "no_best_bid",
        "no_best_ask",
        "metar_current_max_f",
        "metar_latest_temp_f",
        "metar_latest_ts_utc",
        "metar_obs_count_today",
        "probability_status",
        "window",
        "hours_to_settle",
    ]
    import sys

    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
