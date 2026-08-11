from __future__ import annotations

import json
import sqlite3

from weather_dashboard.ingest.clob_fill_validity_adjustments import (
    append_validity_adjustment,
    import_validity_adjustments,
)


def test_validity_adjustment_is_append_only_and_idempotent(tmp_path) -> None:
    path = tmp_path / "validity.jsonl"
    row = {
        "adjustment_id": "adjustment-1",
        "fill_id": "dust-fill",
        "effective_status": "excluded",
        "reason": "share_unit_undercount",
        "evidence": {"matched_shares": 5.0},
        "source_path": "orders.jsonl",
        "created_at_utc": "2026-08-11T00:00:00Z",
    }

    assert append_validity_adjustment(row, path) is True
    assert append_validity_adjustment(row, path) is False
    assert len(path.read_text().splitlines()) == 1


def test_validity_adjustment_import_is_idempotent(tmp_path) -> None:
    path = tmp_path / "validity.jsonl"
    path.write_text(
        json.dumps(
            {
                "adjustment_id": "adjustment-1",
                "fill_id": "dust-fill",
                "effective_status": "excluded",
                "reason": "share_unit_undercount",
                "evidence": {"matched_shares": 5.0},
                "source_path": "orders.jsonl",
                "created_at_utc": "2026-08-11T00:00:00Z",
            }
        )
        + "\n"
    )
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE fill_validity_adjustments (
          adjustment_id TEXT PRIMARY KEY,
          fill_id TEXT,
          effective_status TEXT,
          reason TEXT,
          evidence_json TEXT,
          source_path TEXT,
          created_at_utc TEXT
        )
        """
    )

    assert import_validity_adjustments(conn, path) == 1
    assert import_validity_adjustments(conn, path) == 0
    assert conn.execute(
        "SELECT effective_status FROM fill_validity_adjustments"
    ).fetchone() == ("excluded",)
