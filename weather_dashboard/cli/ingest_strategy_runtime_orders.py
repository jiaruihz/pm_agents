from __future__ import annotations

import argparse
import json
from pathlib import Path

from weather_dashboard.db.apply_schema_canonical import init_db_canonical
from weather_dashboard.db.connection import get_conn
from src.strategies.runtime.sync import sync_instance_specs
from weather_dashboard.legacy_migration.strategy_runtime_orders import (
    DEFAULT_ROOTS,
    iter_strategy_order_paths,
    migrate_strategy_runtime_orders,
    write_report,
)


def resolve_order_paths(
    root_args: list[str] | None,
    order_files: list[str] | None,
) -> list[Path]:
    # Explicit order files supplement normal runtime discovery.  Previously
    # their presence silently disabled DEFAULT_ROOTS, so any local strategy not
    # repeated in the shell wrapper vanished from canonical lineage.
    roots = root_args if root_args is not None else DEFAULT_ROOTS
    paths = iter_strategy_order_paths(roots)
    seen = {str(path) for path in paths}
    for raw_path in order_files or []:
        path = Path(raw_path)
        if str(path) not in seen:
            paths.append(path)
            seen.add(str(path))
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate strategy-local runtime order JSONL into canonical DB")
    parser.add_argument("--db-path", default="runtime/weather.db")
    parser.add_argument("--report-dir", default="runtime/weather_dashboard_migration/reports")
    parser.add_argument(
        "--root",
        action="append",
        help="Root containing strategy runtime directories. Can be repeated.",
    )
    parser.add_argument(
        "--order-file",
        action="append",
        help="Specific live_orders.jsonl/paper_orders.jsonl to ingest. Can be repeated.",
    )
    args = parser.parse_args()

    init_db_canonical(args.db_path)
    paths = resolve_order_paths(args.root, args.order_file)
    conn = get_conn(args.db_path)
    try:
        sync_instance_specs(conn)
        reports = [migrate_strategy_runtime_orders(conn, order_path=path) for path in paths]
    finally:
        conn.close()

    report_path = write_report(reports, args.report_dir)
    print(json.dumps({
        "report_path": str(report_path),
        "reports": [report.as_dict() for report in reports],
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
