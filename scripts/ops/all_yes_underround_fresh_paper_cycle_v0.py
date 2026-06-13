#!/usr/bin/env python3
"""Run all-YES underround paper capture only on a fresh orderbook snapshot.

This script is paper-only. It is meant to be scheduled next to the snapshot
capture path so forward paper evidence is live-equivalent under the same TTL
that a future live executor must respect.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge.research_all_yes_underround_live_prep_v0 import (  # noqa: E402
    latest_snapshot,
    read_snapshot,
    snapshot_summary,
)
from scripts.ops.all_yes_underround_paper_exec_v0 import parse_utc, read_json  # noqa: E402


SNAPSHOT_ROOT_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "market_data" / "orderbook_snapshots"
SCAN_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-14-all-yes-underround-live-prep-v0.json"
SCAN_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-14-all-yes-underround-live-prep-v0.md"
RUN_DIR_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "all_yes_underround_paper_v0"
DB_DEFAULT = ROOT / "runtime" / "weather.db"
GATE_DEFAULT = ROOT / "runtime" / "_dashboard_logs" / "clob_fill_coverage_gate.json"
STATION_BASIS_GATE_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "station_basis_shadow_v1" / "live_prep_gate.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default=str(SNAPSHOT_ROOT_DEFAULT))
    parser.add_argument("--snapshot-path")
    parser.add_argument("--scan-json", default=str(SCAN_JSON_DEFAULT))
    parser.add_argument("--scan-md", default=str(SCAN_MD_DEFAULT))
    parser.add_argument("--run-dir", default=str(RUN_DIR_DEFAULT))
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--gate-path", default=str(GATE_DEFAULT))
    parser.add_argument("--station-basis-gate-path", default=str(STATION_BASIS_GATE_DEFAULT))
    parser.add_argument("--max-snapshot-age-seconds", type=float, default=180.0)
    parser.add_argument("--wait-seconds", type=float, default=0.0)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--python", default=sys.executable)
    return parser.parse_args()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def snapshot_freshness(snapshot_ts_utc: Any, decision_ts: datetime, max_age_seconds: float) -> dict[str, Any]:
    snapshot_ts = parse_utc(snapshot_ts_utc)
    if snapshot_ts is None:
        return {
            "fresh": False,
            "reason": "missing_snapshot_ts",
            "snapshot_age_seconds": None,
        }
    age = (decision_ts - snapshot_ts).total_seconds()
    if age < -1:
        return {
            "fresh": False,
            "reason": "snapshot_ts_in_future",
            "snapshot_age_seconds": round(age, 3),
        }
    if age > max_age_seconds:
        return {
            "fresh": False,
            "reason": "snapshot_too_old",
            "snapshot_age_seconds": round(age, 3),
        }
    return {
        "fresh": True,
        "reason": "fresh",
        "snapshot_age_seconds": round(age, 3),
    }


def load_latest_snapshot(args: argparse.Namespace) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    path = Path(args.snapshot_path) if args.snapshot_path else latest_snapshot(Path(args.snapshot_root))
    rows = read_snapshot(path)
    summary = snapshot_summary(rows)
    freshness = snapshot_freshness(summary.get("snapshot_ts_utc_max"), now_utc(), args.max_snapshot_age_seconds)
    return path, summary, freshness


def run_cmd(argv: list[str]) -> None:
    subprocess.run(argv, cwd=ROOT, check=True)


def write_result(run_dir: Path, result: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "fresh_cycle.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    deadline = time.monotonic() + max(args.wait_seconds, 0.0)
    path: Path
    summary: dict[str, Any]
    freshness: dict[str, Any]

    while True:
        path, summary, freshness = load_latest_snapshot(args)
        if freshness["fresh"] or time.monotonic() >= deadline:
            break
        time.sleep(max(args.poll_seconds, 1.0))

    run_dir = Path(args.run_dir)
    result: dict[str, Any] = {
        "command": "fresh_paper_cycle",
        "generated_at_utc": now_utc().isoformat(),
        "snapshot_path": str(path),
        "snapshot_summary": summary,
        "max_snapshot_age_seconds": args.max_snapshot_age_seconds,
        **freshness,
        "executed_cycle": False,
        "live_now": False,
    }

    if not freshness["fresh"]:
        result["verdict"] = "STALE_SNAPSHOT_SKIP_CYCLE"
        result["next_actions"] = [
            "Run this script immediately after orderbook snapshot capture on the same host.",
            "Do not count stale local sync observations as live-equivalent forward paper evidence.",
        ]
        write_result(run_dir, result)
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    py = args.python
    scanner = "scripts/analysis/market_structure_edge/research_all_yes_underround_live_prep_v0.py"
    executor = "scripts/ops/all_yes_underround_paper_exec_v0.py"
    scanner_args = [
        py,
        scanner,
        "--snapshot-path",
        str(path),
        "--db-path",
        str(Path(args.db_path)),
        "--gate-path",
        str(Path(args.gate_path)),
        "--station-basis-gate-path",
        str(Path(args.station_basis_gate_path)),
        "--paper-gate-path",
        str(run_dir / "live_prep_gate.json"),
        "--paper-monitor-path",
        str(run_dir / "monitor.json"),
        "--fresh-cycle-path",
        str(run_dir / "fresh_cycle.json"),
        "--out-json",
        str(Path(args.scan_json)),
        "--out-md",
        str(Path(args.scan_md)),
    ]
    run_cmd(scanner_args)
    run_cmd(
        [
            py,
            executor,
            "cycle",
            "--scan-json",
            str(Path(args.scan_json)),
            "--db-path",
            str(Path(args.db_path)),
            "--gate-path",
            str(Path(args.gate_path)),
            "--run-dir",
            str(run_dir),
            "--max-snapshot-age-seconds",
            str(args.max_snapshot_age_seconds),
        ]
    )
    run_cmd(
        [
            py,
            executor,
            "monitor",
            "--db-path",
            str(Path(args.db_path)),
            "--gate-path",
            str(Path(args.gate_path)),
            "--run-dir",
            str(run_dir),
            "--max-snapshot-age-seconds",
            str(args.max_snapshot_age_seconds),
        ]
    )
    run_cmd(scanner_args)

    result["executed_cycle"] = True
    result["verdict"] = "FRESH_SNAPSHOT_CYCLE_RAN"
    result["last_cycle"] = read_json(run_dir / "last_cycle.json")
    result["monitor"] = read_json(run_dir / "monitor.json")
    write_result(run_dir, result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
