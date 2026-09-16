import json
import sqlite3

from weather_dashboard.ingest.clob_fill_timestamp_adjustments import (
    append_timestamp_adjustment,
    import_timestamp_adjustments,
)


def test_timestamp_adjustment_journal_and_import_are_idempotent(tmp_path):
    journal = tmp_path / "timestamp_adjustments.jsonl"
    row = {
        "adjustment_id": "adjustment-1",
        "fill_id": "fill-1",
        "corrected_filled_at_utc": "2026-07-26T03:24:27+00:00",
        "timestamp_source": "clob_authenticated_trade_match_time",
        "timestamp_evidence_class": "exact",
        "evidence": {"match_time_epoch": 1785036267},
        "created_at_utc": "2026-07-26T08:00:00+00:00",
    }

    assert append_timestamp_adjustment(row, journal)
    assert not append_timestamp_adjustment(row, journal)

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE fill_timestamp_adjustments (
            adjustment_id TEXT PRIMARY KEY,
            fill_id TEXT NOT NULL UNIQUE,
            corrected_filled_at_utc TEXT NOT NULL,
            timestamp_source TEXT NOT NULL,
            timestamp_evidence_class TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            source_path TEXT,
            created_at_utc TEXT NOT NULL
        )
        """
    )
    assert import_timestamp_adjustments(conn, journal) == 1
    assert import_timestamp_adjustments(conn, journal) == 0
    assert conn.execute(
        "SELECT corrected_filled_at_utc FROM fill_timestamp_adjustments"
    ).fetchone()[0] == "2026-07-26T03:24:27+00:00"


def test_onchain_evidence_class_is_normalized_without_losing_raw_lineage(tmp_path):
    journal = tmp_path / "timestamp_adjustments.jsonl"
    row = {
        "adjustment_id": "polygon-block-1-fill-1",
        "fill_id": "fill-1",
        "corrected_filled_at_utc": "2026-09-01T13:02:22+00:00",
        "timestamp_source": "polygon_block_timestamp",
        "timestamp_evidence_class": "exact_onchain",
        "evidence": {"polygon_block_number": 93041806},
        "created_at_utc": "2026-09-01T14:49:22+00:00",
    }
    assert append_timestamp_adjustment(row, journal)

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE fill_timestamp_adjustments (
            adjustment_id TEXT PRIMARY KEY,
            fill_id TEXT NOT NULL UNIQUE,
            corrected_filled_at_utc TEXT NOT NULL,
            timestamp_source TEXT NOT NULL,
            timestamp_evidence_class TEXT NOT NULL CHECK (
                timestamp_evidence_class IN ('exact', 'estimate')
            ),
            evidence_json TEXT NOT NULL,
            source_path TEXT,
            created_at_utc TEXT NOT NULL
        )
        """
    )

    assert import_timestamp_adjustments(conn, journal) == 1
    evidence_class, evidence_json = conn.execute(
        "SELECT timestamp_evidence_class, evidence_json "
        "FROM fill_timestamp_adjustments"
    ).fetchone()
    assert evidence_class == "exact"
    assert json.loads(evidence_json) == {
        "journal_timestamp_evidence_class": "exact_onchain",
        "polygon_block_number": 93041806,
    }
