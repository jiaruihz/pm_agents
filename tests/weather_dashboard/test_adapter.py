"""Tests for real_ledger_adapter and ingest_run pipeline."""

import pytest
from weather_dashboard.ingest.real_ledger_adapter import adapt_row, adapt_rows


# ── adapt_row ──────────────────────────────────────────────────────────────────

def test_adapt_row_snapshot_replay_format():
    """Snapshot-replay CSV: missing mode/order_id/created_at_utc."""
    raw = {
        "snapshot_file": "snap1.json",
        "snapshot_ts_utc": "2026-05-05T15:27:41Z",
        "city": "Shanghai",
        "bracket": "22",
        "side": "BUY_YES",
        "model": "gfs",
        "model_prob": "0.138",
        "market_yes_price": "0.012",
        "edge": "0.126",
        "abs_edge": "0.126",
        "event_date": "2026-05-06",
        "shares": "10.0",
        "cost_usd": "0.12",
        "entry_price": "0.012",
        "settlement_status": "settled",
        "final_yes": "0.0",
        # no mode, order_id, created_at_utc
    }
    row = adapt_row(raw, default_mode="snapshot_replay")
    assert row["mode"] == "snapshot_replay"
    assert row["order_id"] == ""
    assert row["created_at_utc"] == "2026-05-05T15:27:41Z"  # falls back to snapshot_ts_utc
    assert row["final_yes"] == "0"


def test_adapt_row_ledger_format():
    """Ledger CSV: has mode/order_id/created_at_utc."""
    raw = {
        "snapshot_file": "snap1.json",
        "snapshot_ts_utc": "2026-05-05T15:27:41Z",
        "city": "Tokyo",
        "bracket": "23",
        "side": "BUY_NO",
        "model": "ecmwf",
        "model_prob": "0.65",
        "market_yes_price": "0.55",
        "edge": "0.10",
        "abs_edge": "0.10",
        "event_date": "2026-05-06",
        "shares": "100",
        "cost_usd": "55.00",
        "entry_price": "0.55",
        "mode": "paper",
        "order_id": "ord_abc",
        "created_at_utc": "2026-05-05T16:00:00Z",
        "settlement_status": "settled",
        "final_yes": "1.0",
    }
    row = adapt_row(raw)
    assert row["mode"] == "paper"
    assert row["order_id"] == "ord_abc"
    assert row["created_at_utc"] == "2026-05-05T16:00:00Z"
    assert row["final_yes"] == "1"


def test_adapt_row_final_yes_normalisation():
    for raw_val, expected in [
        ("0.0", "0"), ("1.0", "1"), ("0", "0"), ("1", "1"),
        ("False", "0"), ("True", "1"), ("", ""),
    ]:
        row = adapt_row({"final_yes": raw_val}, default_mode="paper")
        assert row["final_yes"] == expected, f"failed for {raw_val!r}"


def test_adapt_row_event_date_from_settle_utc():
    raw = {"settle_utc": "2026-05-06T14:00:00Z"}
    row = adapt_row(raw)
    assert row["event_date"] == "2026-05-06"


def test_adapt_rows_list():
    raw = [{"city": "Tokyo", "bracket": "22"}, {"city": "Warsaw", "bracket": "25"}]
    rows = adapt_rows(raw)
    assert len(rows) == 2
    assert rows[0]["city"] == "Tokyo"


# ── ingest_run end-to-end ─────────────────────────────────────────────────────

def test_ingest_run_dry_run(tmp_path):
    """dry_run=True should not create DB."""
    import csv as csv_mod
    from weather_dashboard.cli.ingest_run import run_ingest

    csv_file = tmp_path / "test.csv"
    rows = [
        {"snapshot_file": "s.json", "snapshot_ts_utc": "2026-05-09T10:00:00Z",
         "city": "Tokyo", "bracket": "23", "side": "BUY_YES", "model": "ecmwf",
         "model_prob": "0.65", "market_yes_price": "0.55", "edge": "0.10",
         "abs_edge": "0.10", "event_date": "2026-05-09", "shares": "100",
         "cost_usd": "55.00", "entry_price": "0.55", "settlement_status": "settled",
         "final_yes": "1.0"},
    ]
    with open(csv_file, "w", newline="") as fh:
        w = csv_mod.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    db_path = str(tmp_path / "test.db")
    result = run_ingest(
        csv_path=str(csv_file), db_path=db_path,
        run_name="test", execution_mode="snapshot_replay", dry_run=True,
    )
    assert result["dry_run"] is True
    import os
    assert not os.path.exists(db_path)


def test_ingest_run_full(tmp_path):
    """Full pipeline: CSV → DB → metrics."""
    import csv as csv_mod
    from weather_dashboard.cli.ingest_run import run_ingest

    csv_file = tmp_path / "test.csv"
    rows = [
        {"snapshot_file": "s1.json", "snapshot_ts_utc": "2026-05-09T10:00:00Z",
         "city": "Tokyo", "bracket": "23", "side": "BUY_YES", "model": "ecmwf",
         "model_prob": "0.65", "market_yes_price": "0.55", "edge": "0.10",
         "abs_edge": "0.10", "event_date": "2026-05-09", "shares": "100",
         "cost_usd": "55.00", "entry_price": "0.55", "settlement_status": "settled",
         "final_yes": "1.0"},
        {"snapshot_file": "s2.json", "snapshot_ts_utc": "2026-05-09T11:00:00Z",
         "city": "Warsaw", "bracket": "25", "side": "BUY_NO", "model": "gfs",
         "model_prob": "0.45", "market_yes_price": "0.60", "edge": "-0.05",
         "abs_edge": "0.05", "event_date": "2026-05-09", "shares": "50",
         "cost_usd": "20.00", "entry_price": "0.40", "settlement_status": "settled",
         "final_yes": "1.0"},
    ]
    with open(csv_file, "w", newline="") as fh:
        w = csv_mod.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    db_path = str(tmp_path / "test.db")
    result = run_ingest(
        csv_path=str(csv_file), db_path=db_path,
        run_name="test_run", execution_mode="snapshot_replay",
        date_range_start="2026-05-09", date_range_end="2026-05-09",
    )

    assert "run_id" in result
    # Legacy ingest path uses apply_schema (not canonical), so fact_trades can't be
    # populated by the canonical builder. Metrics are computed from an empty fact_trades.
    assert "num_trades" in result["metrics"]
    # Verify metrics were persisted to the runs table
    from weather_dashboard.db.connection import get_conn
    import json
    conn = get_conn(db_path)
    row = conn.execute("SELECT metrics FROM runs WHERE run_id=?", (result["run_id"],)).fetchone()
    conn.close()
    assert row is not None
    m = json.loads(row["metrics"])
    assert "num_trades" in m
