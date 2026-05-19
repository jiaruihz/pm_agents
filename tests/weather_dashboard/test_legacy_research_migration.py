import csv
import sqlite3

from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.db.connection import apply_pragmas
from weather_dashboard.legacy_migration.research_csv import migrate_research_csv


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_pragmas(conn)
    apply_schema_canonical(conn)
    return conn


def test_migrate_legacy_research_csv_writes_canonical_lineage(tmp_path):
    csv_path = tmp_path / "legacy.csv"
    rows = [
        {
            "snapshot_file": "snapshot_20260517_1200.json",
            "snapshot_ts_utc": "2026-05-17T04:00:53Z",
            "city": "LA",
            "city_pool": "t1_trading",
            "icao": "KLAX",
            "bracket": "68-69",
            "unit": "F",
            "side": "BUY_NO",
            "model": "gfs",
            "model_prob": "0.3633",
            "market_yes_price": "0.485",
            "forecast_source": "open_meteo_live_gfs",
            "condition_id": "0x9abc",
            "market_id": "2266022",
            "hours_to_settle": "12.5",
            "event_date": "2026-05-17",
            "shares": "10.309278",
            "cost_usd": "4.639175",
            "entry_price": "0.45",
            "settlement_status": "settled",
            "final_yes": "0.0",
            "edge": "0.1517",
            "abs_edge": "0.1517",
        }
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    conn = _conn()
    try:
        report = migrate_research_csv(
            conn,
            csv_path=csv_path,
            execution_mode="paper",
            run_name="test",
        )

        assert report.input_rows == 1
        assert report.skipped_rows == 0
        assert report.signals == 1
        assert report.plans == 1
        assert report.orders == 1
        assert report.fills == 1
        assert report.settlements == 1
        assert report.legacy_field_uses["event_date"] == 1
        assert report.legacy_field_uses["model"] == 1
        assert report.legacy_field_uses["model_prob"] == 1
        assert report.legacy_field_uses["market_yes_price"] == 1
        assert report.generated_ids["signal_id"] == 1

        row = conn.execute(
            """
            SELECT sig.target_date, sig.model_version, sig.model_p_yes,
                   sig.market_price, sig.signal_side, p.order_side,
                   o.venue, f.status, s.final_price
            FROM signals sig
            JOIN plans p ON p.signal_id = sig.signal_id
            JOIN orders o ON o.plan_id = p.plan_id
            JOIN fills f ON f.execution_id = o.execution_id
            JOIN settlements s
              ON s.target_date = sig.target_date
             AND s.condition_id = sig.condition_id
             AND s.bracket = sig.bracket
            """
        ).fetchone()

        assert dict(row) == {
            "target_date": "2026-05-17",
            "model_version": "gfs",
            "model_p_yes": 0.3633,
            "market_price": 0.485,
            "signal_side": "NO",
            "order_side": "BUY_NO",
            "venue": "paper",
            "status": "filled",
            "final_price": 0.0,
        }
    finally:
        conn.close()


def test_migrate_legacy_research_csv_reports_skipped_missing_condition_id(tmp_path):
    csv_path = tmp_path / "bad.csv"
    row = {
        "snapshot_ts_utc": "2026-05-17T04:00:53Z",
        "city": "LA",
        "city_pool": "t1_trading",
        "icao": "KLAX",
        "bracket": "68-69",
        "unit": "F",
        "side": "BUY_NO",
        "model": "gfs",
        "model_prob": "0.3633",
        "market_yes_price": "0.485",
        "forecast_source": "open_meteo_live_gfs",
        "condition_id": "",
        "market_id": "2266022",
        "hours_to_settle": "12.5",
        "event_date": "2026-05-17",
        "shares": "10.309278",
        "cost_usd": "4.639175",
        "entry_price": "0.45",
        "settlement_status": "settled",
        "final_yes": "0.0",
        "edge": "0.1517",
        "abs_edge": "0.1517",
    }
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)

    conn = _conn()
    try:
        report = migrate_research_csv(
            conn,
            csv_path=csv_path,
            execution_mode="paper",
            run_name="test",
        )

        assert report.input_rows == 1
        assert report.skipped_rows == 1
        assert report.signals == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"] == 0
        assert any("condition_id" in reason for reason in report.skipped_reasons)
    finally:
        conn.close()
