from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from weather_dashboard.cli.ingest_strategy_runtime_orders import resolve_active_live_order_paths
from weather_dashboard.legacy_migration.strategy_runtime_orders import physical_exchange_order_id


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _chunks(values: list[str], size: int = 500) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def _db_order_ids(conn: sqlite3.Connection, order_ids: list[str]) -> set[str]:
    found: set[str] = set()
    for chunk in _chunks(order_ids):
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT order_id FROM orders WHERE order_id IN ({placeholders})",
            chunk,
        ).fetchall()
        found.update(str(row[0]) for row in rows)
    return found


def check_file(conn: sqlite3.Connection, path: Path) -> dict[str, Any]:
    rows = _read_jsonl(path)
    unique_ids = sorted({physical_exchange_order_id(row) for row in rows if physical_exchange_order_id(row)})
    found = _db_order_ids(conn, unique_ids) if unique_ids else set()
    submitted_ids = sorted(
        {
            physical_exchange_order_id(row)
            for row in rows
            if physical_exchange_order_id(row)
        }
    )
    missing = [execution_id for execution_id in submitted_ids if execution_id not in found]
    lifecycle_attempts = [
        str(row.get("execution_id") or row.get("decision_id") or "").strip()
        for row in rows
        if not physical_exchange_order_id(row)
    ]
    return {
        "path": str(path),
        "raw_rows": len(rows),
        "raw_exchange_order_ids": len(unique_ids),
        "raw_submitted_rows": len(submitted_ids),
        "db_orders_found": len(found),
        "missing_orders": len(missing),
        "sample_missing_execution_ids": missing[:20],
        "non_exchange_lifecycle_attempts": len(lifecycle_attempts),
        "sample_non_exchange_lifecycle_ids": [value for value in lifecycle_attempts if value][:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check strategy-runtime order JSONL coverage in canonical orders table")
    parser.add_argument("--db-path", default="runtime/weather.db")
    parser.add_argument("--order-file", action="append")
    parser.add_argument(
        "--active-live-only",
        action="store_true",
        help="Check every enabled expected-live journal registered in strategy_instance.",
    )
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
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
            if not args.order_file:
                parser.error("--order-file is required unless --active-live-only is used")
            paths = [Path(path) for path in args.order_file]
        reports = [check_file(conn, path) for path in paths]
    finally:
        conn.close()

    failed = any(report["missing_orders"] for report in reports)
    print(json.dumps({"ok": not failed, "reports": reports}, ensure_ascii=False, indent=2, sort_keys=True))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
