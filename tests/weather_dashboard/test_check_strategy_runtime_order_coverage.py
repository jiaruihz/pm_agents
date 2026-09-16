from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from weather_dashboard.cli.check_strategy_runtime_order_coverage import check_file


def _write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_check_file_only_blocks_on_missing_submitted_orders(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE orders (execution_id TEXT PRIMARY KEY, order_id TEXT)")
    conn.execute("INSERT INTO orders(execution_id, order_id) VALUES ('db-present', 'clob-present')")
    path = tmp_path / "live_orders.jsonl"
    _write_jsonl(
        path,
        [
            {"execution_id": "submitted-present", "status": "submitted", "order_id": "clob-present"},
            {"execution_id": "blocked-tail", "status": "blocked"},
            {"execution_id": "failed-tail", "live_submit_status": "submit_failed"},
        ],
    )

    report = check_file(conn, path)

    assert report["raw_exchange_order_ids"] == 1
    assert report["raw_submitted_rows"] == 1
    assert report["missing_orders"] == 0
    assert report["non_exchange_lifecycle_attempts"] == 2


def test_check_file_still_blocks_on_missing_submitted_order(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE orders (execution_id TEXT PRIMARY KEY, order_id TEXT)")
    path = tmp_path / "live_orders.jsonl"
    _write_jsonl(path, [{"execution_id": "submitted-missing", "status": "submitted", "order_id": "clob-missing"}])

    report = check_file(conn, path)

    assert report["missing_orders"] == 1
    assert report["sample_missing_execution_ids"] == ["clob-missing"]


def test_cancel_lifecycle_submitted_wrapper_is_not_an_exchange_order(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE orders (execution_id TEXT PRIMARY KEY, order_id TEXT)")
    path = tmp_path / "live_orders.jsonl"
    _write_jsonl(
        path,
        [{"execution_id": "cancel-attempt", "status": "submitted", "execution_action": "maker_cancel_ttl"}],
    )

    report = check_file(conn, path)

    assert report["raw_submitted_rows"] == 0
    assert report["missing_orders"] == 0
    assert report["non_exchange_lifecycle_attempts"] == 1
