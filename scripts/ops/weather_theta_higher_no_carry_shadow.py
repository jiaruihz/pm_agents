#!/usr/bin/env python3
"""Shadow runner for the fade-confirmed higher-NO carry route.

Route split:
- current YES live route: buy the current running-max bracket YES while it is
  still the running max.
- higher-NO carry route: after the official temperature has faded from the
  running max, watch the next-higher bracket NO ask as a separate expression.

This script is shadow-only. It never places orders.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

OPS = Path(__file__).resolve().parent
ROOT = OPS.parents[1]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import weather_theta_current_yes_tiny_live as current_yes  # noqa: E402


STRATEGY_INSTANCE = "theta_higher_no_carry_shadow_v1"
RUNTIME_DIR = ROOT / "runtime/weather_edge_v1/theta_higher_no_carry_shadow_v1"
SUMMARY_OUT = RUNTIME_DIR / "latest_summary.json"
HISTORY_OUT = RUNTIME_DIR / "summary_history.jsonl"
SHADOW_OUT = RUNTIME_DIR / "shadow_candidates.jsonl"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(current_yes.json_ready(row), ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current_yes.json_ready(row), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def select_higher_no_candidates(rows: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    if rows.empty:
        return rows.copy()
    work = rows.copy()
    work["d1_no_available_notional"] = work["d1_no_ask"].astype(float) * work["d1_no_size"].astype(float)
    mask = (
        work["decline_c"].astype(float).ge(float(args.min_decline_c))
        & work["d1_no_ask"].astype(float).ge(float(args.min_no_ask))
        & work["d1_no_ask"].astype(float).le(float(args.max_no_ask))
        & work["d1_no_available_notional"].ge(float(args.min_available_notional))
        & work["d1_no_bracket"].astype(str).ne("")
    )
    if args.max_current_yes_ask is not None:
        mask &= work["yes_current_ask"].astype(float).le(float(args.max_current_yes_ask))
    return work[mask].sort_values(["d1_no_ask", "decline_c", "d1_no_available_notional"], ascending=[False, False, False]).copy()


def candidate_row(row: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    cost = float(row["d1_no_ask"])
    size = float(row["d1_no_size"])
    notional = min(float(args.shadow_notional), cost * size)
    return {
        "record_type": "theta_higher_no_carry_shadow_candidate",
        "strategy_instance": STRATEGY_INSTANCE,
        "created_at_utc": now_utc(),
        "city": row["city"],
        "target_date": row["target_date"],
        "unit": row["unit"],
        "decision_local_time": row.get("local_time"),
        "decision_timezone": row.get("timezone"),
        "decision_hour_local": int(row["decision_hour_local"]),
        "route": "fade_confirmed_higher_no_carry",
        "expression": "BUY_NO_D1_ABOVE_RUNNING_MAX",
        "current_bracket": row["current_bracket"],
        "d1_no_bracket": row["d1_no_bracket"],
        "running_value": int(row["running_value"]),
        "current_native": round(float(row["current_native"]), 6),
        "running_native": round(float(row["running_native"]), 6),
        "decline_c": round(float(row["decline_c"]), 6),
        "gap_running_to_d1_low_c": round(float(row["gap_running_to_d1_low_c"]), 6),
        "yes_current_ask": round(float(row["yes_current_ask"]), 6),
        "d1_no_ask": round(cost, 6),
        "d1_no_size": round(size, 6),
        "d1_no_available_notional": round(cost * size, 6),
        "shadow_notional": round(notional, 6),
        "snapshot_ts_utc": row.get("snapshot_ts_utc"),
        "source_snapshot_path": row.get("snapshot_path"),
        "obs_source": (row.get("obs") or {}).get("source"),
        "obs_age_min": round(float((row.get("obs") or {}).get("age_min") or 0.0), 6),
        "minutes_to_next_obs": round(float((row.get("obs") or {}).get("minutes_to_next_obs") or -1.0), 6),
        "config": {
            "min_decline_c": float(args.min_decline_c),
            "min_no_ask": float(args.min_no_ask),
            "max_no_ask": float(args.max_no_ask),
            "max_current_yes_ask": args.max_current_yes_ask,
            "min_available_notional": float(args.min_available_notional),
            "min_gap_to_next_bracket_c": float(args.min_gap_to_next_bracket_c),
            "max_obs_age_min": float(args.max_obs_age_min),
            "pre_metar_update_blackout_min": float(args.pre_metar_update_blackout_min),
        },
    }


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    snap_path = Path(args.snapshot) if args.snapshot else current_yes.latest_snapshot()
    if snap_path is None:
        raise RuntimeError("no paper snapshot found")
    snapshot, records = current_yes.snapshot_records(snap_path)
    snapshot_ts = current_yes.parse_utc(snapshot.get("ts_utc")) or datetime.now(timezone.utc)
    age_min = (datetime.now(timezone.utc) - snapshot_ts).total_seconds() / 60.0
    if age_min > args.max_snapshot_age_min:
        result = {
            "generated_at_utc": now_utc(),
            "status": "stale_snapshot",
            "strategy_instance": STRATEGY_INSTANCE,
            "snapshot": str(snap_path),
            "snapshot_ts_utc": snapshot.get("ts_utc"),
            "snapshot_age_min": round(age_min, 1),
            "max_snapshot_age_min": float(args.max_snapshot_age_min),
        }
        write_json(SUMMARY_OUT, result)
        append_jsonl(HISTORY_OUT, result)
        return result

    current_rows, audits = current_yes.build_current_rows(
        snapshot,
        records,
        current_yes.load_stations(),
        snapshot_ts,
        max_obs_age_min=args.max_obs_age_min,
        pre_update_blackout_min=args.pre_metar_update_blackout_min,
        min_gap_to_next_bracket_c=args.min_gap_to_next_bracket_c,
        min_local_hour=args.min_local_hour,
        max_local_hour=args.max_local_hour,
    )
    selected = select_higher_no_candidates(current_rows, args)
    candidates = [candidate_row(row.to_dict(), args) for _, row in selected.iterrows()]
    for row in candidates:
        append_jsonl(SHADOW_OUT, row)
    audit_counts = dict(pd.Series([current_yes.safe_str(a.get("status")) for a in audits]).value_counts()) if audits else {}
    result = {
        "generated_at_utc": now_utc(),
        "status": "shadow_planned",
        "strategy_instance": STRATEGY_INSTANCE,
        "route": "fade_confirmed_higher_no_carry",
        "live_enabled": False,
        "snapshot": str(snap_path),
        "snapshot_dir": str(current_yes.snapshot_dir()),
        "snapshot_ts_utc": snapshot.get("ts_utc"),
        "snapshot_age_min": round(age_min, 1),
        "current_rows": int(0 if current_rows.empty else len(current_rows)),
        "candidate_rows": len(candidates),
        "shadow_out": str(SHADOW_OUT),
        "audit_counts": audit_counts,
        "candidates": candidates[:20],
        "config": {
            "min_decline_c": float(args.min_decline_c),
            "min_no_ask": float(args.min_no_ask),
            "max_no_ask": float(args.max_no_ask),
            "max_current_yes_ask": args.max_current_yes_ask,
            "min_available_notional": float(args.min_available_notional),
            "min_gap_to_next_bracket_c": float(args.min_gap_to_next_bracket_c),
            "max_snapshot_age_min": float(args.max_snapshot_age_min),
            "max_obs_age_min": float(args.max_obs_age_min),
            "pre_metar_update_blackout_min": float(args.pre_metar_update_blackout_min),
            "min_local_hour": int(args.min_local_hour),
            "max_local_hour": int(args.max_local_hour),
            "shadow_notional": float(args.shadow_notional),
        },
    }
    write_json(SUMMARY_OUT, result)
    append_jsonl(HISTORY_OUT, result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "loop"])
    parser.add_argument("--snapshot")
    parser.add_argument("--min-decline-c", type=float, default=0.5)
    parser.add_argument("--min-no-ask", type=float, default=0.75)
    parser.add_argument("--max-no-ask", type=float, default=0.97)
    parser.add_argument("--max-current-yes-ask", type=float, default=None)
    parser.add_argument("--min-available-notional", type=float, default=5.0)
    parser.add_argument("--min-gap-to-next-bracket-c", type=float, default=0.0)
    parser.add_argument("--max-snapshot-age-min", type=float, default=45.0)
    parser.add_argument("--max-obs-age-min", type=float, default=20.0)
    parser.add_argument("--pre-metar-update-blackout-min", type=float, default=6.0)
    parser.add_argument("--min-local-hour", type=int, default=13)
    parser.add_argument("--max-local-hour", type=int, default=17)
    parser.add_argument("--shadow-notional", type=float, default=5.0)
    parser.add_argument("--interval-seconds", type=float, default=900.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    while True:
        try:
            result = run_once(args)
            print(json.dumps(current_yes.json_ready(result), ensure_ascii=False, sort_keys=True), flush=True)
        except Exception as exc:  # noqa: BLE001
            err = {"generated_at_utc": now_utc(), "status": "error", "error": f"{type(exc).__name__}: {exc}"}
            print(json.dumps(err, ensure_ascii=False, sort_keys=True), flush=True)
            append_jsonl(HISTORY_OUT, err)
        if args.command == "run":
            return 0
        time.sleep(max(30.0, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
