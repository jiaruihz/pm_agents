from __future__ import annotations

import json
import sqlite3

from weather_dashboard.ingest.clob_fill_price_adjustments import import_price_adjustments


def test_import_price_adjustment_is_idempotent(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE fills (fill_id TEXT PRIMARY KEY);
        INSERT INTO fills VALUES ('fill-1');
        CREATE TABLE fill_price_adjustments (
            adjustment_id TEXT PRIMARY KEY,
            fill_id TEXT NOT NULL UNIQUE,
            corrected_filled_price REAL NOT NULL,
            price_source TEXT NOT NULL,
            price_evidence_class TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            source_path TEXT,
            created_at_utc TEXT NOT NULL
        );
        """
    )
    path = tmp_path / "prices.jsonl"
    path.write_text(
        json.dumps(
            {
                "adjustment_id": "adj-1",
                "fill_id": "fill-1",
                "corrected_filled_price": 0.8,
                "price_source": "authenticated_clob_order_state",
                "price_evidence_class": "exact",
                "evidence": {"raw_price": 0.791},
                "created_at_utc": "2026-07-18T15:30:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert import_price_adjustments(conn, path) == 1
    assert import_price_adjustments(conn, path) == 0
    assert conn.execute(
        "SELECT corrected_filled_price FROM fill_price_adjustments"
    ).fetchone()[0] == 0.8
