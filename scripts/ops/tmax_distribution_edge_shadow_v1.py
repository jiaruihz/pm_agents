#!/usr/bin/env python3
"""Tmax distribution edge shadow v1.

Zero-notional shadow runner for the Tmax distribution-first strategy family.
It never submits orders. It consumes the current shadow-event source produced by
the research pipeline, appends selected and blocked rows to a runtime journal,
and writes latest_summary.json for dashboard/runtime registry visibility.

The strategy family is intentionally expressed without P-stage names:

  market/weather distribution -> expression selector -> zero-notional shadow

Usage:
  .venv/bin/python scripts/ops/tmax_distribution_edge_shadow_v1.py run
  .venv/bin/python scripts/ops/tmax_distribution_edge_shadow_v1.py loop --interval-seconds 900

Optional:
  --refresh-source reruns the current research materialization before reading
  the source CSV. This is useful after atlas/orderbook data has been updated,
  but is intentionally opt-in because it is heavier than a normal journal pass.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_feature_layer.runtime_refs import attach_runtime_feature_frame_ref  # noqa: E402

SOURCE_DEFAULT = (
    ROOT
    / "docs/analysis/2026-07/generated/tmax_distribution_p6_shadow_telemetry_v1/shadow_events.csv"
)
RUNTIME_DIR_DEFAULT = ROOT / os.environ.get(
    "TMAX_DISTRIBUTION_EDGE_SHADOW_RUNTIME_DIR",
    "runtime/weather_edge_v1/tmax_distribution_edge_shadow_v1",
)
JOURNAL_DEFAULT = RUNTIME_DIR_DEFAULT / "shadow_events.jsonl"
LATEST_DEFAULT = RUNTIME_DIR_DEFAULT / "latest_events.json"
SUMMARY_DEFAULT = RUNTIME_DIR_DEFAULT / "latest_summary.json"
HISTORY_DEFAULT = RUNTIME_DIR_DEFAULT / "summary_history.jsonl"
FEATURE_STORE_DEFAULT = ROOT / os.environ.get("WEATHER_FEATURE_STORE_DIR", "runtime/weather_feature_store")

STRATEGY_INSTANCE = "tmax_distribution_edge_shadow_v1"
STRATEGY_FAMILY = "reheat_risk.tmax_distribution_edge"
SOURCE_DOC = "docs/WEATHER_TMAX_DISTRIBUTION_EDGE_STRATEGY.md"
SOURCE_REFRESH_SCRIPTS = [
    "scripts/analysis/reheat_risk/research_tmax_distribution_p5_walk_forward_execution_replay_v1.py",
    "scripts/analysis/reheat_risk/research_tmax_distribution_p6_shadow_telemetry_v1.py",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "loop"], nargs="?", default="run")
    parser.add_argument("--source", default=str(SOURCE_DEFAULT))
    parser.add_argument("--runtime-dir", default=str(RUNTIME_DIR_DEFAULT))
    parser.add_argument("--journal", default=None)
    parser.add_argument("--latest", default=None)
    parser.add_argument("--summary", default=None)
    parser.add_argument("--history", default=None)
    parser.add_argument("--min-target-date", default=None, help="Default: max target_date in non-dev source rows.")
    parser.add_argument("--max-target-date", default=None)
    parser.add_argument("--scope", action="append", default=[], help="Repeatable. Default: all non-dev_cv scopes.")
    parser.add_argument("--include-dev-cv", action="store_true")
    parser.add_argument("--selected-only", action="store_true", help="Default keeps both selected and blocked.")
    parser.add_argument("--max-events-per-run", type=int, default=5000)
    parser.add_argument("--refresh-source", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=900.0)
    return parser.parse_args()


def to_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def json_ready(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    return value


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(json_ready(payload), ensure_ascii=False, sort_keys=True) + "\n")


def existing_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_id = row.get("shadow_event_id")
            if event_id:
                ids.add(str(event_id))
    return ids


def load_rows(source: Path) -> list[dict[str, str]]:
    if not source.exists():
        raise FileNotFoundError(f"shadow event source does not exist: {source}")
    with source.open("r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def refresh_source() -> list[dict[str, Any]]:
    runs = []
    py = ROOT / ".venv/bin/python"
    python = str(py) if py.exists() else sys.executable
    for script in SOURCE_REFRESH_SCRIPTS:
        started = utc_now()
        proc = subprocess.run(
            [python, script],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        runs.append(
            {
                "script": script,
                "started_at_utc": started,
                "finished_at_utc": utc_now(),
                "returncode": proc.returncode,
                "stdout_tail": proc.stdout[-2000:],
                "stderr_tail": proc.stderr[-2000:],
            }
        )
        if proc.returncode != 0:
            raise RuntimeError(f"source refresh failed for {script}: {proc.stderr[-1000:]}")
    return runs


def effective_min_target_date(rows: list[dict[str, str]], args: argparse.Namespace) -> str | None:
    if args.min_target_date:
        return str(args.min_target_date)
    candidates = [
        str(r.get("target_date"))
        for r in rows
        if r.get("target_date") and (args.include_dev_cv or r.get("scope") != "dev_cv")
    ]
    return max(candidates) if candidates else None


def filter_rows(rows: list[dict[str, str]], args: argparse.Namespace, min_target_date: str | None) -> list[dict[str, str]]:
    scopes = set(args.scope)
    out = []
    for row in rows:
        scope = str(row.get("scope") or "")
        target_date = str(row.get("target_date") or "")
        if not args.include_dev_cv and scope == "dev_cv":
            continue
        if scopes and scope not in scopes:
            continue
        if min_target_date and target_date < min_target_date:
            continue
        if args.max_target_date and target_date > str(args.max_target_date):
            continue
        if args.selected_only and row.get("selection_status") != "selected":
            continue
        out.append(row)
    return sorted(out, key=lambda r: (r.get("target_date") or "", r.get("city") or "", r.get("shadow_config_id") or ""))[
        : int(args.max_events_per_run)
    ]


def event_payload(row: dict[str, str], *, source: Path) -> dict[str, Any]:
    numeric_fields = {
        "edge_threshold",
        "decision_hour_local",
        "ask",
        "p_win",
        "model_edge",
        "model_roi",
        "win",
        "unit_pnl",
        "minutes_since_running_max",
        "forecast_peak_delta_hours_local",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "wind_speed_kt",
        "relative_humidity_pct",
    }
    bool_fields = {"zero_notional", "no_order_placed"}
    payload: dict[str, Any] = {}
    for key, value in row.items():
        if key in numeric_fields:
            payload[key] = to_float(value)
        elif key in bool_fields:
            payload[key] = str(value).lower() in {"true", "1", "yes"}
        elif value == "":
            payload[key] = None
        else:
            payload[key] = value
    payload.update(
        {
            "record_type": "tmax_distribution_edge_shadow_event",
            "strategy_instance": STRATEGY_INSTANCE,
            "strategy_family": STRATEGY_FAMILY,
            "execution_mode": "zero_notional_shadow",
            "no_order_placed": True,
            "zero_notional": True,
            "created_at_utc": utc_now(),
            "source_doc": SOURCE_DOC,
            "source_artifact": rel(source),
        }
    )
    return attach_runtime_feature_frame_ref(
        payload,
        store_root=FEATURE_STORE_DEFAULT,
        feature_grain="tmax_distribution_shadow_event",
        source_profile_id=STRATEGY_INSTANCE,
        builder_version="tmax_distribution_edge_shadow_feature_ref_v1",
        key_columns=(
            "strategy_instance",
            "city",
            "target_date",
            "decision_hour_local",
            "bracket",
            "shadow_config_id",
        ),
    )


def summarize(rows: list[dict[str, Any]], *, all_filtered_rows: list[dict[str, str]], appended: int, skipped_existing: int, source: Path, min_target_date: str | None, refresh_runs: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [r for r in rows if r.get("selection_status") == "selected"]
    blocked = [r for r in rows if r.get("selection_status") == "blocked"]
    by_config: dict[str, dict[str, Any]] = {}
    for row in rows:
        cfg = str(row.get("shadow_config_id"))
        bucket = by_config.setdefault(
            cfg,
            {
                "rows": 0,
                "selected": 0,
                "blocked": 0,
                "dates": set(),
                "cities": set(),
                "selected_cost": 0.0,
                "selected_pnl": 0.0,
            },
        )
        bucket["rows"] += 1
        bucket["dates"].add(row.get("target_date"))
        bucket["cities"].add(row.get("city"))
        if row.get("selection_status") == "selected":
            bucket["selected"] += 1
            bucket["selected_cost"] += to_float(row.get("ask"), 0.0)
            bucket["selected_pnl"] += to_float(row.get("unit_pnl"), 0.0)
        elif row.get("selection_status") == "blocked":
            bucket["blocked"] += 1
    configs = []
    for cfg, bucket in sorted(by_config.items()):
        cost = float(bucket["selected_cost"])
        pnl = float(bucket["selected_pnl"])
        configs.append(
            {
                "shadow_config_id": cfg,
                "rows": bucket["rows"],
                "selected": bucket["selected"],
                "blocked": bucket["blocked"],
                "dates": len([d for d in bucket["dates"] if d]),
                "cities": len([c for c in bucket["cities"] if c]),
                "selected_cost": cost,
                "selected_pnl": pnl,
                "selected_roi": pnl / cost if cost else None,
            }
        )
    source_stat = source.stat() if source.exists() else None
    return {
        "generated_at_utc": utc_now(),
        "strategy_instance": STRATEGY_INSTANCE,
        "strategy_family": STRATEGY_FAMILY,
        "execution_mode": "zero_notional_shadow",
        "no_order_placed": True,
        "source_artifact": rel(source),
        "source_mtime_utc": datetime.fromtimestamp(source_stat.st_mtime, timezone.utc).isoformat(timespec="seconds") if source_stat else None,
        "source_rows_filtered": len(all_filtered_rows),
        "effective_min_target_date": min_target_date,
        "target_dates": sorted({str(r.get("target_date")) for r in rows if r.get("target_date")}),
        "scopes": sorted({str(r.get("scope")) for r in rows if r.get("scope")}),
        "rows_written_this_cycle": len(rows),
        "feature_frame_ref_stored_count": sum(1 for row in rows if str(row.get("feature_frame_ref_status") or "") == "stored"),
        "feature_frame_ref_error_count": sum(1 for row in rows if str(row.get("feature_frame_ref_status") or "") == "error"),
        "selected_rows_this_cycle": len(selected),
        "blocked_rows_this_cycle": len(blocked),
        "appended": appended,
        "skipped_existing": skipped_existing,
        "config_summary": configs,
        "refresh_source": bool(refresh_runs),
        "refresh_runs": refresh_runs,
        "journal": rel(JOURNAL_DEFAULT),
        "latest_events": rel(LATEST_DEFAULT),
        "source_doc": SOURCE_DOC,
    }


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    runtime_dir = Path(args.runtime_dir)
    journal = Path(args.journal) if args.journal else runtime_dir / "shadow_events.jsonl"
    latest = Path(args.latest) if args.latest else runtime_dir / "latest_events.json"
    summary_path = Path(args.summary) if args.summary else runtime_dir / "latest_summary.json"
    history = Path(args.history) if args.history else runtime_dir / "summary_history.jsonl"
    source = Path(args.source)

    refresh_runs = refresh_source() if args.refresh_source else []
    source_rows = load_rows(source)
    min_target_date = effective_min_target_date(source_rows, args)
    filtered = filter_rows(source_rows, args, min_target_date)
    seen = existing_ids(journal)
    appended = 0
    skipped_existing = 0
    written_payloads = []
    for row in filtered:
        payload = event_payload(row, source=source)
        event_id = str(payload.get("shadow_event_id") or "")
        written_payloads.append(payload)
        if event_id in seen:
            skipped_existing += 1
            continue
        append_jsonl(journal, payload)
        seen.add(event_id)
        appended += 1

    latest_payload = {
        "generated_at_utc": utc_now(),
        "strategy_instance": STRATEGY_INSTANCE,
        "events": written_payloads,
    }
    write_json(latest, latest_payload)
    summary = summarize(
        written_payloads,
        all_filtered_rows=filtered,
        appended=appended,
        skipped_existing=skipped_existing,
        source=source,
        min_target_date=min_target_date,
        refresh_runs=refresh_runs,
    )
    summary["journal"] = rel(journal)
    summary["latest_events"] = rel(latest)
    write_json(summary_path, summary)
    append_jsonl(history, summary)
    print(json.dumps(json_ready(summary), ensure_ascii=False, sort_keys=True))
    return summary


def main() -> int:
    args = parse_args()
    if args.command == "run":
        run_once(args)
        return 0
    while True:
        try:
            run_once(args)
        except Exception as exc:  # noqa: BLE001
            runtime_dir = Path(args.runtime_dir)
            history = Path(args.history) if args.history else runtime_dir / "summary_history.jsonl"
            append_jsonl(
                history,
                {
                    "generated_at_utc": utc_now(),
                    "strategy_instance": STRATEGY_INSTANCE,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "no_order_placed": True,
                },
            )
            print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        time.sleep(float(args.interval_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
