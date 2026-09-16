import json
import sqlite3

from scripts.etl.build_weather_fact_trades import build as build_fact_trades
from scripts.ops.materialize_weather_execution_evidence import materialize
from scripts.ops.reconcile_weather_order_execution_aliases import reconcile
from src.strategies.runtime.sync import sync_instance_specs
from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.db.connection import apply_pragmas
from weather_dashboard.legacy_migration.strategy_runtime_orders import (
    _enrich_runtime_order,
    migrate_strategy_runtime_orders,
    physical_exchange_order_id,
)


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _conn_with_fill(tmp_path):
    order_path = tmp_path / "runtime/weather_edge_v1/live/orders.jsonl"
    _write_jsonl(
        order_path,
        [
            {
                "record_type": "weather_edge_live_order",
                "strategy_id": "low_price_yes_lottery_tiny_live_v1",
                "strategy_instance": "low_price_yes_lottery_tiny_live_v1",
                "execution_id": "e" * 64,
                "order_id": "0xorder",
                "venue": "polymarket_clob",
                "status": "submitted",
                "created_at_utc": "2026-08-29T12:00:10Z",
                "decision_snapshot_ts_utc": "2026-08-29T12:00:00Z",
                "target_date": "2026-08-29",
                "city": "Tokyo",
                "city_pool": "t1_trading",
                "icao": "RJTT",
                "bracket": "35",
                "unit": "C",
                "signal_side": "BUY_YES",
                "order_side": "BUY",
                "model_version": "gfs",
                "model_p_yes": 0.8,
                "forecast_source": "open_meteo_live_gfs",
                "condition_id": "0xcondition",
                "market_id": "market",
                "token_id": "yes-token",
                "question": "Will Tokyo be 35C?",
                "market_price": 0.6,
                "limit_price": 0.62,
                "posted_price": 0.62,
                "size": 5.0,
                "posted_notional": 3.1,
                "exchange_response": {
                    "place": {"orderID": "0xorder", "success": True}
                },
            }
        ],
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_pragmas(conn)
    apply_schema_canonical(conn)
    sync_instance_specs(conn)
    report = migrate_strategy_runtime_orders(conn, order_path=order_path)
    assert report.orders == 1
    execution_id, order_id = conn.execute(
        "SELECT execution_id, order_id FROM orders"
    ).fetchone()
    conn.execute(
        """INSERT INTO fills (
             fill_id, execution_id, order_id, filled_shares, filled_price,
             fees_usd, fee_source, transaction_hash, status, filled_at_utc
           ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            "fill-1", execution_id, order_id, 5.0, 0.625, 0.01,
            "authenticated_activity", "0xtx", "filled",
            "2026-08-29T12:00:11Z",
        ),
    )
    conn.commit()
    return conn


def _public_book(root, observed_at):
    path = root / "2026-08-29/public_books_20260829_12_test.jsonl"
    _write_jsonl(
        path,
        [
            {
                "schema_version": "weather_public_book_evidence_v1",
                "evidence_id": "public-evidence-1",
                "evidence_class": "public_orderbook_state_not_fill_or_queue",
                "execution_claim_status": (
                    "unjoined_requires_decision_and_private_order_lifecycle"
                ),
                "token_id": "yes-token",
                "book_snapshot_id": "book-snapshot-1",
                "book_observed_at_utc": observed_at,
                "best_bid": 0.59,
                "best_ask": 0.61,
                "sweeps": [{"shares": 5.0, "buy_cost": 3.05}],
                "sequence_status": "exchange_sequence_unavailable",
                "gap_detection_status": (
                    "best_quote_parity_checked_sequence_unavailable"
                ),
                "subscription_epoch_id": "epoch-1",
                "baseline_raw_frame_ref": {
                    "archive_path": "/raw/ws.jsonl",
                    "line_number": 1,
                },
            }
        ],
    )


def test_cross_no_v2_legacy_order_maps_previous_bracket_and_market_unit():
    row = _enrich_runtime_order(
        {
            "strategy_id": "cross_no_v2_metar_v1",
            "created_at_utc": "2026-08-31T17:58:39Z",
            "source_event_ts_utc": "2026-08-31T17:45:00Z",
            "previous_official_bracket": "76-77",
            "market_unit": "F",
            "condition_id": "0xcondition",
            "question": "Will NYC reach 76-77F?",
        },
        None,
    )
    assert row["bracket"] == "76-77"
    assert row["unit"] == "F"
    assert row["market_id"] == "0xcondition"


def test_confirmed_cancel_is_appended_as_canonical_order_lifecycle(tmp_path):
    order_id = "0x" + "5" * 64
    order_path = tmp_path / "runtime/weather_edge_v1/cancel_probe/live_orders.jsonl"
    submitted = {
        "record_type": "weather_edge_live_order",
        "strategy_id": "cancel_probe",
        "strategy_instance": "cancel_probe",
        "execution_id": "a" * 64,
        "order_id": order_id,
        "venue": "polymarket_clob",
        "status": "submitted",
        "created_at_utc": "2026-09-03T21:33:44Z",
        "decision_snapshot_ts_utc": "2026-09-03T21:33:25Z",
        "target_date": "2026-09-03",
        "city": "LA",
        "city_pool": "t1_trading",
        "icao": "KLAX",
        "bracket": "76-77",
        "unit": "F",
        "signal_side": "BUY_YES",
        "order_side": "BUY",
        "model_version": "cancel-probe-v1",
        "model_p_yes": 0.9657,
        "forecast_source": "open_meteo_live_gfs",
        "condition_id": "0xcondition",
        "market_id": "4058776",
        "token_id": "yes-token",
        "question": "Will LA reach 76-77F?",
        "market_price": 0.97,
        "limit_price": 0.95,
        "posted_price": 0.94,
        "size": 5.0,
        "posted_notional": 4.7,
        "execution_policy": "core_carry_near_core_fixed_rest_maker_v1",
        "execution_profile": "near_core_fixed_rest_maker_ws1_v1",
        "order_lifecycle_policy": "near_core_fixed_rest_safety_cancel_only_v1",
        "child_order_role": "maker_staged",
        "maker_only": True,
        "exchange_response": {
            "place": {"orderID": order_id, "status": "live", "success": True},
            "quote_status": "accepted",
        },
    }
    cancelled = {
        **submitted,
        "execution_id": "b" * 64,
        "created_at_utc": "2026-09-03T21:48:09Z",
        "order_id": "",
        "status": "submitted",
        "source_order_id": order_id,
        "cancel_before_order_id": order_id,
        "source_execution_id": submitted["execution_id"],
        "source_posted_price": 0.94,
        "posted_price": 0.0,
        "posted_notional": 0.0,
        "notional": 0.0,
        "limit_price": 0.0,
        "execution_action": "near_core_maker_cancel_stale_market_state",
        "child_order_role": "near_core_maker_cancel_stale_market_state",
        "quote_status": "cancelled",
        "exchange_response": {
            "cancel": {
                "status": "cancelled",
                "raw_response": {
                    "status": "cancelled",
                    "canceled": [order_id],
                    "not_canceled": {},
                },
            },
            "quote_status": "cancelled",
        },
    }
    _write_jsonl(order_path, [submitted, cancelled])

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_pragmas(conn)
    apply_schema_canonical(conn)
    try:
        report = migrate_strategy_runtime_orders(conn, order_path=order_path)
        assert report.orders == 2
        rows = conn.execute(
            "SELECT execution_id, order_id, status, clob_status, quote_status, "
            "execution_action, source_order_id, entry_price, cost_usd "
            "FROM orders ORDER BY placed_at_utc, execution_id"
        ).fetchall()
        assert tuple(rows[0]) == (
            submitted["execution_id"], order_id, "submitted", "live", "accepted",
            None, None, 0.94, 4.7,
        )
        assert tuple(rows[1]) == (
            cancelled["execution_id"], order_id, "cancelled", "cancelled", "cancelled",
            "near_core_maker_cancel_stale_market_state", order_id, 0.94, 0.0,
        )
        assert physical_exchange_order_id(cancelled) == order_id

        aliases = reconcile(conn, apply=True, source_path="test")
        assert aliases["resolved_aliases"] == 1
        alias = conn.execute(
            "SELECT alias_execution_id, canonical_execution_id, reason "
            "FROM order_execution_aliases"
        ).fetchone()
        assert tuple(alias) == (
            cancelled["execution_id"],
            submitted["execution_id"],
            "physical_order_lifecycle_event",
        )

        repeated = migrate_strategy_runtime_orders(conn, order_path=order_path)
        assert repeated.orders == 0
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 2
    finally:
        conn.close()


def test_unconfirmed_cancel_attempt_remains_out_of_canonical_orders(tmp_path):
    order_id = "0x" + "6" * 64
    order_path = tmp_path / "runtime/weather_edge_v1/cancel_probe/live_orders.jsonl"
    attempted = {
        "record_type": "weather_edge_live_order",
        "strategy_instance": "cancel_probe",
        "execution_id": "c" * 64,
        "venue": "polymarket_clob",
        "status": "submitted",
        "created_at_utc": "2026-09-03T21:48:09Z",
        "decision_snapshot_ts_utc": "2026-09-03T21:33:25Z",
        "target_date": "2026-09-03",
        "city": "LA",
        "bracket": "76-77",
        "unit": "F",
        "signal_side": "BUY_YES",
        "order_side": "BUY",
        "model_version": "cancel-probe-v1",
        "model_p_yes": 0.9657,
        "forecast_source": "open_meteo_live_gfs",
        "condition_id": "0xcondition",
        "market_id": "4058776",
        "token_id": "yes-token",
        "market_price": 0.97,
        "size": 5.0,
        "source_order_id": order_id,
        "execution_action": "near_core_maker_cancel_stale_market_state",
        "exchange_response": {
            "cancel": {
                "status": "cancelled",
                "raw_response": {
                    "status": "cancelled",
                    "not_canceled": {order_id: "not found"},
                },
            }
        },
    }
    _write_jsonl(order_path, [attempted])

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_pragmas(conn)
    apply_schema_canonical(conn)
    try:
        report = migrate_strategy_runtime_orders(conn, order_path=order_path)
        assert report.orders == 0
        assert report.skipped_reasons == {"non_exchange_order_lifecycle_attempt": 1}
        assert physical_exchange_order_id(attempted) == ""
    finally:
        conn.close()


def test_cancel_status_without_raw_exchange_response_is_not_canonical_fact():
    order_id = "0x" + "7" * 64
    row = {
        "source_order_id": order_id,
        "execution_action": "near_core_maker_cancel_stale_market_state",
        "exchange_response": {"cancel": {"status": "cancelled"}},
    }
    assert physical_exchange_order_id(row) == ""


def test_materializer_combines_causal_public_book_with_private_fill(tmp_path):
    conn = _conn_with_fill(tmp_path)
    public_root = tmp_path / "public_books"
    _public_book(public_root, "2026-08-29T12:00:09Z")
    try:
        report = materialize(
            conn, public_books_root=public_root, apply=True, max_book_age_sec=10
        )

        assert report["matched_links"] == 1
        assert report["inserted_links"] == 1
        assert report["missing_links"] == 0
        link = conn.execute(
            "SELECT execution_book_snapshot_id, book_age_ms, quote_side, "
            "executable_quote_price, adverse_slippage, evidence_status "
            "FROM execution_evidence_links"
        ).fetchone()
        assert tuple(link) == (
            "book-snapshot-1", 1000.0, "ask", 0.61, 0.015,
            "private_fill_plus_causal_public_book",
        )
        facts, _ = build_fact_trades(conn, fill_ids=["fill-1"])
        assert facts[0]["execution_book_snapshot_id"] == "book-snapshot-1"
        assert facts[0]["execution_evidence_status"] == (
            "private_fill_plus_causal_public_book"
        )
    finally:
        conn.close()


def test_materializer_never_uses_post_order_book(tmp_path):
    conn = _conn_with_fill(tmp_path)
    public_root = tmp_path / "public_books"
    _public_book(public_root, "2026-08-29T12:00:10.001Z")
    try:
        report = materialize(
            conn, public_books_root=public_root, apply=True, max_book_age_sec=10
        )
        assert report["matched_links"] == 0
        assert report["missing_by_reason"] == {
            "no_causal_public_book_before_order": 1
        }
        assert conn.execute(
            "SELECT COUNT(*) FROM execution_evidence_links"
        ).fetchone()[0] == 0
    finally:
        conn.close()
