from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


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


def _db_execution_ids(conn: sqlite3.Connection, execution_ids: list[str]) -> set[str]:
    found: set[str] = set()
    for chunk in _chunks(execution_ids):
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT execution_id FROM orders WHERE execution_id IN ({placeholders})",
            chunk,
        ).fetchall()
        found.update(str(row[0]) for row in rows)
    return found


def check_file(conn: sqlite3.Connection, path: Path) -> dict[str, Any]:
    rows = _read_jsonl(path)
    raw_execution_ids = [
        str(row.get("execution_id") or "").strip()
        for row in rows
        if str(row.get("execution_id") or "").strip()
    ]
    unique_ids = sorted(set(raw_execution_ids))
    found = _db_execution_ids(conn, unique_ids) if unique_ids else set()
    submitted_ids = sorted(
        {
            str(row.get("execution_id") or "").strip()
            for row in rows
            if str(row.get("execution_id") or "").strip()
            and str(row.get("live_submit_status") or row.get("order_status") or row.get("status") or "").strip()
            == "submitted"
        }
    )
    missing = [execution_id for execution_id in submitted_ids if execution_id not in found]
    missing_non_submitted = [
        execution_id
        for execution_id in unique_ids
        if execution_id not in found and execution_id not in submitted_ids
    ]
    return {
        "path": str(path),
        "raw_rows": len(rows),
        "raw_execution_ids": len(unique_ids),
        "raw_submitted_rows": len(submitted_ids),
        "db_orders_found": len(found),
        "missing_orders": len(missing),
        "sample_missing_execution_ids": missing[:20],
        "missing_non_submitted_attempts": len(missing_non_submitted),
        "sample_missing_non_submitted_execution_ids": missing_non_submitted[:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check strategy-runtime order JSONL coverage in canonical orders table")
    parser.add_argument("--db-path", default="runtime/weather.db")
    parser.add_argument("--order-file", action="append", required=True)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    try:
        reports = [check_file(conn, Path(path)) for path in args.order_file]
    finally:
        conn.close()

    failed = any(report["missing_orders"] for report in reports)
    print(json.dumps({"ok": not failed, "reports": reports}, ensure_ascii=False, indent=2, sort_keys=True))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
