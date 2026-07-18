from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from weather_dashboard.cli.check_strategy_runtime_order_coverage import check_file


def _write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_check_file_only_blocks_on_missing_submitted_orders(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE orders (execution_id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO orders(execution_id) VALUES ('submitted-present')")
    path = tmp_path / "live_orders.jsonl"
    _write_jsonl(
        path,
        [
            {"execution_id": "submitted-present", "status": "submitted"},
            {"execution_id": "blocked-tail", "status": "blocked"},
            {"execution_id": "failed-tail", "live_submit_status": "submit_failed"},
        ],
    )

    report = check_file(conn, path)

    assert report["raw_execution_ids"] == 3
    assert report["raw_submitted_rows"] == 1
    assert report["missing_orders"] == 0
    assert report["missing_non_submitted_attempts"] == 2


def test_check_file_still_blocks_on_missing_submitted_order(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE orders (execution_id TEXT PRIMARY KEY)")
    path = tmp_path / "live_orders.jsonl"
    _write_jsonl(path, [{"execution_id": "submitted-missing", "status": "submitted"}])

    report = check_file(conn, path)

    assert report["missing_orders"] == 1
    assert report["sample_missing_execution_ids"] == ["submitted-missing"]
