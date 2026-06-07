#!/usr/bin/env python3
"""Inspect side flip transitions directly from raw paper snapshots."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def f(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def delta(after: Any, before: Any) -> float | str:
    a = f(after)
    b = f(before)
    if a is None or b is None:
        return ""
    return round(a - b, 4)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--snapshots-dir",
        default="runtime/weather_edge_v1/market_data/paper_snapshots",
        type=Path,
    )
    parser.add_argument("--target-date", action="append", default=[])
    parser.add_argument("--start-name", default="")
    parser.add_argument("--end-name", default="")
    parser.add_argument("--min-abs-edge", type=float, default=0.0)
    parser.add_argument("--min-entry-price", type=float, default=0.0)
    parser.add_argument("--max-entry-price", type=float, default=1.0)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    target_dates = set(args.target_date) if args.target_date else None
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    total_rows = 0
    for path in sorted(args.snapshots_dir.glob("snapshot_*.json")):
        if args.start_name and path.name < args.start_name:
            continue
        if args.end_name and path.name > args.end_name:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("records", []):
            if not isinstance(row, dict):
                continue
            if row.get("record_type") != "edge_signal":
                continue
            target_date = str(row.get("event_date") or "")
            if target_dates and target_date not in target_dates:
                continue
            side = str(row.get("side") or "")
            if side not in {"BUY_YES", "BUY_NO"}:
                continue
            total_rows += 1
            key = (
                target_date,
                str(row.get("city") or ""),
                str(row.get("condition_id") or row.get("market_id") or ""),
                str(row.get("bracket") or ""),
            )
            groups[key].append(
                {
                    "snapshot_file": path.name,
                    "ts_utc": row.get("ts_utc") or payload.get("ts_utc") or "",
                    "city": row.get("city") or "",
                    "target_date": target_date,
                    "bracket": row.get("bracket") or "",
                    "side": side,
                    "model": row.get("model") or "",
                    "forecast_source": row.get("forecast_source") or "",
                    "forecast_max_f": row.get("forecast_max_f") or row.get("gfs_forecast_f"),
                    "model_prob": row.get("model_prob"),
                    "market_yes_price": row.get("market_yes_price"),
                    "entry_price": row.get("entry_price"),
                    "edge": row.get("edge"),
                    "abs_edge": row.get("abs_edge"),
                    "model_init_utc_estimated": row.get("model_init_utc_estimated") or "",
                    "model_run_age_hours_estimated": row.get("model_run_age_hours_estimated") or "",
                    "forecast_target_lead_hours_estimated": row.get("forecast_target_lead_hours_estimated") or "",
                    "metar_current_max_f": row.get("metar_current_max_f") or "",
                    "metar_latest_temp_f": row.get("metar_latest_temp_f") or "",
                    "metar_obs_count_today": row.get("metar_obs_count_today") or "",
                }
            )

    transitions: list[dict[str, Any]] = []
    for key, rows in groups.items():
        rows.sort(key=lambda r: (r["ts_utc"], r["snapshot_file"]))
        compressed: list[dict[str, Any]] = []
        for row in rows:
            if compressed and row["ts_utc"] == compressed[-1]["ts_utc"] and row["side"] == compressed[-1]["side"]:
                continue
            compressed.append(row)
        for before, after in zip(compressed, compressed[1:]):
            if before["side"] == after["side"]:
                continue
            before_abs_edge = f(before["abs_edge"])
            after_abs_edge = f(after["abs_edge"])
            before_entry = f(before["entry_price"])
            after_entry = f(after["entry_price"])
            if before_abs_edge is None or after_abs_edge is None:
                continue
            if before_abs_edge < args.min_abs_edge or after_abs_edge < args.min_abs_edge:
                continue
            if before_entry is None or after_entry is None:
                continue
            if before_entry < args.min_entry_price or after_entry < args.min_entry_price:
                continue
            if before_entry >= args.max_entry_price or after_entry >= args.max_entry_price:
                continue
            transitions.append(
                {
                    "target_date": key[0],
                    "city": key[1],
                    "bracket": key[3],
                    "from_ts": before["ts_utc"],
                    "to_ts": after["ts_utc"],
                    "from_side": before["side"].replace("BUY_", ""),
                    "to_side": after["side"].replace("BUY_", ""),
                    "from_forecast": before["forecast_max_f"],
                    "to_forecast": after["forecast_max_f"],
                    "d_forecast": delta(after["forecast_max_f"], before["forecast_max_f"]),
                    "from_p": before["model_prob"],
                    "to_p": after["model_prob"],
                    "d_p": delta(after["model_prob"], before["model_prob"]),
                    "from_yes_px": before["market_yes_price"],
                    "to_yes_px": after["market_yes_price"],
                    "d_yes_px": delta(after["market_yes_price"], before["market_yes_price"]),
                    "from_edge": before["edge"],
                    "to_edge": after["edge"],
                    "model": after["model"],
                    "forecast_source": after["forecast_source"],
                    "from_model_init": before["model_init_utc_estimated"],
                    "to_model_init": after["model_init_utc_estimated"],
                    "from_run_age": before["model_run_age_hours_estimated"],
                    "to_run_age": after["model_run_age_hours_estimated"],
                    "from_metar_max": before["metar_current_max_f"],
                    "to_metar_max": after["metar_current_max_f"],
                    "from_metar_latest": before["metar_latest_temp_f"],
                    "to_metar_latest": after["metar_latest_temp_f"],
                    "from_metar_n": before["metar_obs_count_today"],
                    "to_metar_n": after["metar_obs_count_today"],
                }
            )

    transitions.sort(key=lambda r: (r["target_date"], r["city"], r["bracket"], r["from_ts"]))
    print(f"total_edge_rows={total_rows}")
    print(f"group_count={len(groups)}")
    print(f"transition_count={len(transitions)}")
    abs_d_forecast = [abs(float(r["d_forecast"])) for r in transitions if r["d_forecast"] != ""]
    abs_d_p = [abs(float(r["d_p"])) for r in transitions if r["d_p"] != ""]
    abs_d_px = [abs(float(r["d_yes_px"])) for r in transitions if r["d_yes_px"] != ""]
    model_init_changed = sum(1 for r in transitions if r["from_model_init"] != r["to_model_init"])
    if transitions:
        print(
            "summary="
            + json.dumps(
                {
                    "median_abs_d_forecast": round(statistics.median(abs_d_forecast), 4) if abs_d_forecast else None,
                    "mean_abs_d_forecast": round(statistics.mean(abs_d_forecast), 4) if abs_d_forecast else None,
                    "median_abs_d_p": round(statistics.median(abs_d_p), 4) if abs_d_p else None,
                    "mean_abs_d_p": round(statistics.mean(abs_d_p), 4) if abs_d_p else None,
                    "median_abs_d_yes_px": round(statistics.median(abs_d_px), 4) if abs_d_px else None,
                    "mean_abs_d_yes_px": round(statistics.mean(abs_d_px), 4) if abs_d_px else None,
                    "abs_d_forecast_ge_1f": sum(1 for x in abs_d_forecast if x >= 1.0),
                    "abs_d_forecast_ge_2f": sum(1 for x in abs_d_forecast if x >= 2.0),
                    "model_init_changed": model_init_changed,
                },
                sort_keys=True,
            )
        )
    fieldnames = [
        "target_date",
        "city",
        "bracket",
        "from_ts",
        "to_ts",
        "from_side",
        "to_side",
        "from_forecast",
        "to_forecast",
        "d_forecast",
        "from_p",
        "to_p",
        "d_p",
        "from_yes_px",
        "to_yes_px",
        "d_yes_px",
        "from_edge",
        "to_edge",
        "model",
        "forecast_source",
        "from_model_init",
        "to_model_init",
        "from_run_age",
        "to_run_age",
        "from_metar_max",
        "to_metar_max",
        "from_metar_latest",
        "to_metar_latest",
        "from_metar_n",
        "to_metar_n",
    ]
    import sys

    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
    writer.writeheader()
    for row in transitions[: args.limit]:
        writer.writerow({k: row[k] for k in fieldnames})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
