import json
import sqlite3

import pytest

from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.ingest.signal_clock_adjustments import (
    append_signal_clock_adjustment,
    import_signal_clock_adjustments,
)


def _row(**updates):
    row = {
        "adjustment_id": "clock-adjustment-1",
        "signal_id": "signal-1",
        "corrected_snapshot_ts_utc": "2026-07-04T14:48:08Z",
        "timestamp_source": "strategy_snapshot_record",
        "timestamp_evidence_class": "reconstructed",
        "lineage_status": "reconstructed_causal",
        "source_snapshot_ref": "/snapshots/snapshot_20260704_2248.json",
        "evidence": {"original_snapshot_ts_utc": "2026-07-05T00:51:16Z"},
        "created_at_utc": "2026-08-30T00:00:00Z",
    }
    row.update(updates)
    return row


def _conn():
    conn = sqlite3.connect(":memory:")
    apply_schema_canonical(conn)
    conn.execute(
        """INSERT INTO signals (
          signal_id, producer_system, producer_run_id, snapshot_ts_utc,
          target_date, city, city_pool, icao, bracket, unit, signal_side,
          model_version, model_p_yes, forecast_source, market_price, edge,
          abs_edge, condition_id, market_id, hours_to_settle
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "signal-1", "pm_agent_local", "run-1", "2026-07-05T00:51:16Z",
            "2026-07-05", "Shanghai", "t1_trading", "ZSPD", "35", "C",
            "YES", "gfs", 0.4, "open_meteo_live_gfs", 0.3, 0.1, 0.1,
            "condition", "market", 12.0,
        ),
    )
    conn.commit()
    return conn


def test_append_and_import_signal_clock_adjustment_is_idempotent(tmp_path):
    journal = tmp_path / "signal_clock_adjustments.jsonl"
    assert append_signal_clock_adjustment(_row(), journal) is True
    assert append_signal_clock_adjustment(_row(), journal) is False

    conn = _conn()
    try:
        assert import_signal_clock_adjustments(conn, journal) == 1
        assert import_signal_clock_adjustments(conn, journal) == 0
        stored = conn.execute(
            "SELECT corrected_snapshot_ts_utc, timestamp_evidence_class, "
            "lineage_status, evidence_json FROM signal_clock_adjustments"
        ).fetchone()
        assert stored[:3] == (
            "2026-07-04T14:48:08Z",
            "reconstructed",
            "reconstructed_causal",
        )
        assert json.loads(stored[3])["original_snapshot_ts_utc"].endswith("Z")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE signal_clock_adjustments SET timestamp_source='changed'"
            )
    finally:
        conn.close()


def test_signal_clock_adjustment_rejects_naive_or_conflicting_rows(tmp_path):
    journal = tmp_path / "signal_clock_adjustments.jsonl"
    with pytest.raises(ValueError, match="timezone"):
        append_signal_clock_adjustment(
            _row(corrected_snapshot_ts_utc="2026-07-04T14:48:08"), journal
        )
    assert append_signal_clock_adjustment(_row(), journal) is True
    with pytest.raises(ValueError, match="conflicting"):
        append_signal_clock_adjustment(
            _row(adjustment_id="clock-adjustment-2", timestamp_source="other"),
            journal,
        )
