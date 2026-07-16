#!/usr/bin/env python3
"""Report analysis-mirror and canonical materialization freshness."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_dashboard.analysis_freshness import summary  # noqa: E402


def write_result(runtime_dir: Path, payload: dict) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "latest_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with (runtime_dir / "summary_history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=ROOT / "runtime/weather.db")
    parser.add_argument(
        "--orderbook-dir",
        type=Path,
        default=ROOT / "runtime/weather_edge_v1/market_data/orderbook_snapshots",
    )
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        default=ROOT / "runtime/weather_edge_v1/analysis_freshness_monitor",
    )
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=900.0)
    parser.add_argument("--exit-nonzero-on-warning", action="store_true")
    args = parser.parse_args()

    while True:
        payload = summary(args.db_path, args.orderbook_dir)
        write_result(args.runtime_dir, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
        if not args.loop:
            return 2 if args.exit_nonzero_on_warning and payload["status"] != "healthy" else 0
        time.sleep(max(1.0, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
