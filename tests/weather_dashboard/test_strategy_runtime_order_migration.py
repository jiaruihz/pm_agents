import json
import sqlite3

import weather_dashboard.legacy_migration.strategy_runtime_orders as strategy_runtime_orders
from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.db.connection import apply_pragmas
from weather_dashboard.legacy_migration.strategy_runtime_orders import (
    iter_strategy_order_paths,
    migrate_strategy_runtime_orders,
)
from scripts.etl.build_weather_fact_trades import build as build_fact_trades
from src.strategies.runtime.sync import sync_instance_specs


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_pragmas(conn)
    apply_schema_canonical(conn)
    sync_instance_specs(conn)
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


def test_snapshot_lookup_skips_rows_with_complete_market_lineage(monkeypatch):
    def fail_snapshot_scan(_target_date):
        raise AssertionError("snapshot scan should not run")

    monkeypatch.setattr(strategy_runtime_orders, "_snapshot_files_for_date", fail_snapshot_scan)
    lookup = strategy_runtime_orders._build_snapshot_lookup(
        [
            {
                "target_date": "2026-07-09",
                "condition_id": "0xcondition",
                "token_id": "token-no",
                "question": "Will the highest temperature in Helsinki be 17°C on July 9?",
                "t_minus_1_no_bracket_c": 17,
            }
        ]
    )
    assert lookup == {}


def test_migrate_fast_source_prev_no_matched_fok_order_defers_fill_to_clob_sync(tmp_path):
    order_path = tmp_path / "output" / "fast_source_prev_no_trial" / "orders.jsonl"
    _write_jsonl(
        order_path,
        [
            {
                "schema_version": "fast_source_prev_no_trial_v1",
                "strategy_id": "fast_source_prev_no_trial_v1",
                "strategy_instance": "fast_source_prev_no_trial_v1",
                "city": "Helsinki",
                "target_date": "2026-07-09",
                "condition_id": "0xb1b1e78205c2ea08a92cd1248d68f3ae55ba06d9e827729454987f1c0b70b47d",
                "market_id": "2826049",
                "token_id": "98617391282593622490977003288012573295810667097836660592616146148184755841068",
                "question": "Will the highest temperature in Helsinki be 17°C on July 9?",
                "event_key": "Helsinki|2026-07-09|fmi|2026-07-09T13:40:00+00:00|18|17|17",
                "order_side": "BUY",
                "size": 5.0,
                "best_ask": 0.82,
                "ask_size": 8.76,
                "limit_price": 0.92,
                "submitted_notional_usd": 4.6,
                "live_submit_status": "submitted",
                "order_id": "0x847bf2533bd18fdf08a2ff7771be59068b7d5eb6a33d9ffe21d52e374ba7fe17",
                "live_attempt_ts_utc": "2026-07-09T13:46:08.750690+00:00",
                "ts_utc": "2026-07-09T13:46:07.986599+00:00",
                "t_minus_1_no_bracket_c": 17,
                "source": "fmi",
                "source_obs_ts_utc": "2026-07-09T13:40:00+00:00",
                "source_detect_ts_utc": "2026-07-09T13:45:48.414916+00:00",
                "source_temp_c": 17.5,
                "source_round_c": 18,
                "latest_metar_report_ts_utc": "2026-07-09T13:20:00+00:00",
                "latest_metar_temp_c": 16.0,
                "metar_running_max_round_c": 17,
                "exchange_response": {
                    "place": {
                        "status": "matched",
                        "success": True,
                        "orderID": "0x847bf2533bd18fdf08a2ff7771be59068b7d5eb6a33d9ffe21d52e374ba7fe17",
                        "makingAmount": "4.599999",
                        "takingAmount": "5.609755",
                    }
                },
            }
        ],
    )

    conn = _conn()
    try:
        report = migrate_strategy_runtime_orders(conn, order_path=order_path)
        assert report.skipped_rows == 0
        assert report.orders == 1
        assert report.fills == 0

        row = conn.execute(
            """
            SELECT s.city, s.bracket, s.unit, s.signal_side,
                   o.venue, o.order_side, o.status, o.clob_status
            FROM orders o
            JOIN plans p ON p.plan_id = o.plan_id
            JOIN signals s ON s.signal_id = p.signal_id
            """
        ).fetchone()
        assert dict(row) == {
            "city": "Helsinki",
            "bracket": "17",
            "unit": "C",
            "signal_side": "NO",
            "venue": "polymarket_clob",
            "order_side": "BUY_NO",
            "status": "submitted",
            "clob_status": "matched",
        }
        assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0
    finally:
        conn.close()


def test_enrich_runtime_order_recovers_condition_hash_from_market_id(monkeypatch):
    condition_id = "0x" + "a" * 64
    monkeypatch.setattr(strategy_runtime_orders, "_enrich_from_gamma_market", lambda row: None)

    row = strategy_runtime_orders._enrich_runtime_order(
        {
            "market_id": condition_id,
            "target_date": "2026-07-11",
            "city": "Seoul",
            "token_id": "token",
        },
        None,
    )

    assert row["condition_id"] == condition_id


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
