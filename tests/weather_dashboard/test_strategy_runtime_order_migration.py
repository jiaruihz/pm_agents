import json
import sqlite3

from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.db.connection import apply_pragmas
from weather_dashboard.legacy_migration.strategy_runtime_orders import (
    iter_strategy_order_paths,
    migrate_strategy_runtime_orders,
)
from scripts.etl.build_weather_fact_trades import build as build_fact_trades


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_pragmas(conn)
    apply_schema_canonical(conn)
    return conn


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_migrate_mac_live_strategy_order_file_as_live(tmp_path):
    root = tmp_path / "runtime" / "weather_edge_v1"
    order_path = root / "live" / "low_price_yes_lottery_tiny_live_v1_orders.jsonl"
    _write_jsonl(
        order_path,
        [
            {
                "record_type": "weather_edge_live_order",
                "strategy_id": "low_price_yes_lottery_tiny_live_v1",
                "strategy_instance": "low_price_yes_lottery_tiny_live_v1",
                "execution_id": "a" * 64,
                "order_id": "0xabc",
                "venue": "polymarket_clob",
                "status": "submitted",
                "created_at_utc": "2026-07-03T20:21:59+00:00",
                "target_date": "2026-07-04",
                "city": "Paris",
                "city_pool": "t2_research",
                "icao": "LFPG",
                "bracket": "32",
                "unit": "C",
                "side": "BUY_YES",
                "order_side": "BUY_YES",
                "model_version": "gfs",
                "model_p_yes": 0.3709,
                "forecast_source": "open_meteo_live_gfs",
                "condition_id": "0xcondition",
                "market_id": "2765777",
                "token_id": "token-yes",
                "market_price": 0.061,
                "limit_price": 0.061,
                "posted_price": 0.061,
                "shares": 13.0,
                "notional": 0.8,
                "edge": 0.3054,
                "execution_policy": "maker_first_taker_fallback",
                "exchange_response": {"place": {"orderID": "0xabc", "success": True}},
            }
        ],
    )

    paths = iter_strategy_order_paths([root])
    assert order_path in paths

    conn = _conn()
    try:
        report = migrate_strategy_runtime_orders(conn, order_path=order_path)
        assert report.skipped_rows == 0
        assert report.orders == 1

        run = conn.execute("SELECT execution_mode, state, tags FROM runs").fetchone()
        assert run["execution_mode"] == "live"
        assert run["state"] == "live"
        assert "low_price_yes_lottery_tiny_live_v1" in json.loads(run["tags"])

        order = conn.execute("SELECT venue, status, order_id FROM orders").fetchone()
        assert dict(order) == {
            "venue": "polymarket_clob",
            "status": "submitted",
            "order_id": "0xabc",
        }
    finally:
        conn.close()


def test_migrate_mac_live_sell_yes_exit_order(tmp_path):
    root = tmp_path / "runtime" / "weather_edge_v1"
    order_path = root / "live" / "low_price_yes_take_profit_exit_v1_orders.jsonl"
    _write_jsonl(
        order_path,
        [
            {
                "record_type": "weather_edge_live_order",
                "strategy_id": "low_price_yes_take_profit_exit_v1",
                "strategy_instance": "low_price_yes_take_profit_exit_v1",
                "execution_id": "b" * 64,
                "order_id": "0xexit",
                "venue": "polymarket_clob",
                "status": "submitted",
                "created_at_utc": "2026-07-03T20:21:59+00:00",
                "target_date": "2026-07-03",
                "city": "London",
                "city_pool": "t1_trading",
                "icao": "EGLL",
                "bracket": "28",
                "unit": "C",
                "signal_side": "SELL_YES",
                "order_side": "SELL",
                "model_version": "gfs",
                "forecast_source": "open_meteo_live_gfs",
                "condition_id": "0xcondition",
                "market_id": "0xmarket",
                "token_id": "token-yes",
                "limit_price": 0.2,
                "posted_price": 0.2,
                "size": 12.5,
                "posted_notional": 2.5,
                "exchange_response": {"place": {"orderID": "0xexit", "success": True}},
            }
        ],
    )

    conn = _conn()
    try:
        report = migrate_strategy_runtime_orders(conn, order_path=order_path)
        assert report.skipped_rows == 0
        assert report.orders == 1

        row = conn.execute(
            """
            SELECT s.signal_side, p.order_side AS plan_order_side, o.order_side, o.cost_usd
            FROM orders o
            JOIN plans p ON p.plan_id = o.plan_id
            JOIN signals s ON s.signal_id = p.signal_id
            """
        ).fetchone()
        assert dict(row) == {
            "signal_side": "YES",
            "plan_order_side": "SELL_YES",
            "order_side": "SELL_YES",
            "cost_usd": 2.5,
        }
    finally:
        conn.close()


def test_fact_trades_uses_sell_side_cashflow_and_pnl(tmp_path):
    root = tmp_path / "runtime" / "weather_edge_v1"
    order_path = root / "live" / "low_price_yes_take_profit_exit_v1_orders.jsonl"
    _write_jsonl(
        order_path,
        [
            {
                "record_type": "weather_edge_live_order",
                "strategy_id": "low_price_yes_take_profit_exit_v1",
                "strategy_instance": "low_price_yes_take_profit_exit_v1",
                "execution_id": "c" * 64,
                "order_id": "0xexit-filled",
                "venue": "polymarket_clob",
                "status": "filled",
                "created_at_utc": "2026-07-03T20:21:59+00:00",
                "target_date": "2026-07-03",
                "city": "London",
                "city_pool": "t1_trading",
                "icao": "EGLL",
                "bracket": "28",
                "unit": "C",
                "signal_side": "SELL_YES",
                "order_side": "SELL",
                "model_version": "gfs",
                "forecast_source": "open_meteo_live_gfs",
                "condition_id": "0xcondition",
                "market_id": "0xmarket",
                "token_id": "token-yes",
                "limit_price": 0.2,
                "posted_price": 0.2,
                "size": 10.0,
                "posted_notional": 2.0,
            }
        ],
    )

    conn = _conn()
    try:
        migrate_strategy_runtime_orders(conn, order_path=order_path)
        order = conn.execute("SELECT execution_id, order_id FROM orders").fetchone()
        conn.execute(
            """
            INSERT INTO fills (
                fill_id, execution_id, order_id, filled_shares, filled_price,
                fees_usd, status, filled_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("fill-sell", order["execution_id"], order["order_id"], 10.0, 0.2, 0.0, "filled", "2026-07-03T20:22:00Z"),
        )
        conn.execute(
            """
            INSERT INTO settlements (
                settlement_id, target_date, condition_id, market_id, bracket,
                token_id, final_price, settlement_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("settle-sell", "2026-07-03", "0xcondition", "0xmarket", "28", "token-yes", 0.0, "settled"),
        )

        rows, alerts = build_fact_trades(conn)
        assert not [alert for alert in alerts if alert.startswith("SIDE_MISMATCH")]
        assert len(rows) == 1
        assert rows[0]["side"] == "SELL_YES"
        assert rows[0]["signal_side"] == "YES"
        assert rows[0]["cost_usd"] == 2.0
        assert rows[0]["pnl_usd_at_fill"] == 2.0
    finally:
        conn.close()
