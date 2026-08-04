from __future__ import annotations

import argparse
import json
from pathlib import Path

from weather_dashboard.db.apply_schema_canonical import init_db_canonical
from weather_dashboard.db.connection import get_conn
from src.strategies.runtime.sync import sync_instance_specs
from src.strategies.runtime.production import WeatherProductionSpec, load_production_spec
from weather_dashboard.legacy_migration.strategy_runtime_orders import (
    DEFAULT_ROOTS,
    iter_strategy_order_paths,
    migrate_strategy_runtime_orders,
    write_report,
)


def resolve_active_live_order_paths(
    project_root: str | Path,
    *,
    production_spec: WeatherProductionSpec | None = None,
) -> list[Path]:
    """Resolve current live journals only from production desired state."""

    root = Path(project_root)
    spec = production_spec or load_production_spec()
    paths: list[Path] = []
    seen: set[str] = set()
    for runtime in spec.managed_runtimes:
        if not runtime.expected_live or runtime.live_order_path is None:
            continue
        path = runtime.live_order_path
        if not path.is_absolute():
            path = root / path
        if not path.is_file() or str(path) in seen:
            continue
        paths.append(path)
        seen.add(str(path))
    return paths


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
    parser.add_argument(
        "--active-live-only",
        action="store_true",
        help="Discover enabled expected-live instance journals from strategy_instance.",
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="Base directory for relative strategy_instance.runtime_dir values.",
    )
    args = parser.parse_args()

    init_db_canonical(args.db_path)
    conn = get_conn(args.db_path)
    try:
        if args.active_live_only:
            paths = resolve_active_live_order_paths(args.project_root)
            seen = {str(path) for path in paths}
            for raw_path in args.order_file or []:
                path = Path(raw_path)
                if str(path) not in seen:
                    paths.append(path)
                    seen.add(str(path))
        else:
            sync_instance_specs(conn)
            paths = resolve_order_paths(args.root, args.order_file)
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
