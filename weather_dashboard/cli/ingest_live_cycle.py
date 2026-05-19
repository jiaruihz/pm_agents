from __future__ import annotations

import argparse
import json
from pathlib import Path

from weather_dashboard.db.apply_schema_canonical import init_db_canonical
from weather_dashboard.db.connection import get_conn
from weather_dashboard.legacy_migration.live_cycle import (
    iter_live_cycle_paths,
    migrate_live_cycle,
    write_report,
)


DEFAULT_ROOTS = (
    "runtime/weather_edge_v1",
    "runtime/weather_edge_v1/remote_pm_agent",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate live-cycle JSONL lineage into canonical DB")
    parser.add_argument("--db-path", default="runtime/weather.db")
    parser.add_argument("--report-dir", default="runtime/weather_dashboard_migration/reports")
    parser.add_argument(
        "--root",
        action="append",
        help="Root containing live_cycle/signals/plans/live/paper. Can be repeated.",
    )
    parser.add_argument(
        "--cycle",
        action="append",
        help="Specific live_cycle summary JSON to ingest. Can be repeated.",
    )
    args = parser.parse_args()

    init_db_canonical(args.db_path)
    paths = [Path(p) for p in args.cycle] if args.cycle else iter_live_cycle_paths(args.root or DEFAULT_ROOTS)
    conn = get_conn(args.db_path)
    try:
        reports = [migrate_live_cycle(conn, cycle_path=path) for path in paths]
    finally:
        conn.close()

    report_path = write_report(reports, args.report_dir)
    print(json.dumps({
        "report_path": str(report_path),
        "reports": [report.as_dict() for report in reports],
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
