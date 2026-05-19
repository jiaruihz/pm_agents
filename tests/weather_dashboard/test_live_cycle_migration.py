import json
import sqlite3

from src.strategies.weather_edge_v1.ids import make_plan_id, make_signal_id
from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.db.connection import apply_pragmas
from weather_dashboard.legacy_migration.live_cycle import migrate_live_cycle


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_pragmas(conn)
    apply_schema_canonical(conn)
    return conn


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _cycle(tmp_path, stamp, *, raw_signal_id, raw_plan_id, execution_id):
    root = tmp_path / "remote_pm_agent"
    (root / "live_cycle").mkdir(parents=True, exist_ok=True)
    cycle_path = root / "live_cycle" / f"{stamp}.json"
    cycle_path.write_text(json.dumps({"run_id": stamp}), encoding="utf-8")

    signal = {
        "signal_id": raw_signal_id,
        "source_run_id": "snapshot_20260517_1000",
        "snapshot_fetched_at_utc": "2026-05-17T02:00:53Z",
        "target_date": "2026-05-17",
        "city": "Miami",
        "city_pool": "t1_trading",
        "bracket": "88-89",
        "unit": "F",
        "signal_side": "BUY_NO",
        "model_version": "gfs",
        "model_probability_yes": 0.0612,
        "profile": "open_meteo_live_gfs",
        "market_price": 0.645,
        "edge": 0.2938,
        "source_id": "0xba76|BUY_NO",
        "market_id": "2265769",
        "token_id": "token",
    }
    plan = {
        "plan_id": raw_plan_id,
        "signal_id": raw_signal_id,
        "signal_side": "BUY_NO",
        "execution_policy": "mid_price_core_v1",
        "limit_price": 0.645,
        "notional": 5.0,
        "size": 7.751938,
        "sizing_mode": "notional",
        "entry_price_window": "0.25-0.75",
        "status": "accepted",
    }
    order = {
        "execution_id": execution_id,
        "plan_id": raw_plan_id,
        "signal_id": raw_signal_id,
        "signal_side": "BUY_NO",
        "venue": "polymarket_clob",
        "limit_price": 0.645,
        "posted_price": 0.645,
        "notional": 5.0,
        "size": 7.751938,
        "status": "submitted",
        "created_at_utc": "2026-05-17T02:06:04+00:00",
        "exchange_response": {
            "place": {
                "orderID": f"0x{execution_id[:8]}",
                "status": "live",
                "success": True,
            }
        },
    }

    _write_jsonl(root / "signals" / f"live_{stamp}_signals.jsonl", [signal])
    _write_jsonl(root / "plans" / f"live_{stamp}_trade_plans.jsonl", [plan])
    _write_jsonl(root / "live" / f"live_{stamp}_orders.jsonl", [order])
    _write_jsonl(root / "paper" / f"live_{stamp}_paper_orders.jsonl", [])
    return cycle_path


def test_migrate_live_cycle_preserves_live_execution_and_response(tmp_path):
    cycle_path = _cycle(
        tmp_path,
        "20260517T020558Z",
        raw_signal_id="a" * 64,
        raw_plan_id="b" * 64,
        execution_id="c" * 64,
    )

    conn = _conn()
    try:
        report = migrate_live_cycle(conn, cycle_path=cycle_path)
        assert report.skipped_rows == 0
        assert report.signals == 1
        assert report.plans == 1
        assert report.orders == 1

        row = conn.execute(
            """
            SELECT sig.signal_id, p.plan_id, o.execution_id, o.order_id,
                   o.venue, o.exchange_response
            FROM orders o
            JOIN plans p ON p.plan_id = o.plan_id
            JOIN signals sig ON sig.signal_id = p.signal_id
            """
        ).fetchone()
        expected_signal_id = make_signal_id(
            target_date="2026-05-17",
            city="Miami",
            bracket="88-89",
            signal_side="NO",
            model_version="gfs",
            forecast_source="open_meteo_live_gfs",
            snapshot_ts_utc="2026-05-17T02:00:53Z",
            condition_id="0xba76",
        )
        expected_plan_id = make_plan_id(
            run_id="n100_live_20260517T020558Z",
            signal_id=expected_signal_id,
            order_side="BUY_NO",
            execution_policy="mid_price_core_v1",
        )

        assert row["signal_id"] == expected_signal_id
        assert row["plan_id"] == expected_plan_id
        assert row["execution_id"] == "c" * 64
        assert row["order_id"] == "0x" + ("c" * 8)
        assert row["venue"] == "polymarket_clob"
        assert json.loads(row["exchange_response"])["place"]["success"] is True
    finally:
        conn.close()


def test_migrate_live_cycle_regenerates_plan_ids_per_run(tmp_path):
    first = _cycle(
        tmp_path,
        "20260517T020558Z",
        raw_signal_id="a" * 64,
        raw_plan_id="b" * 64,
        execution_id="c" * 64,
    )
    second = _cycle(
        tmp_path,
        "20260517T030613Z",
        raw_signal_id="a" * 64,
        raw_plan_id="b" * 64,
        execution_id="d" * 64,
    )

    conn = _conn()
    try:
        migrate_live_cycle(conn, cycle_path=first)
        migrate_live_cycle(conn, cycle_path=second)

        rows = conn.execute("SELECT run_id, plan_id FROM plans ORDER BY run_id").fetchall()
        assert len(rows) == 2
        assert rows[0]["run_id"] != rows[1]["run_id"]
        assert rows[0]["plan_id"] != rows[1]["plan_id"]
        assert conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"] == 2
    finally:
        conn.close()
