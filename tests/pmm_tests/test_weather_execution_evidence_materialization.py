import json
import sqlite3

from scripts.etl.build_weather_fact_trades import build as build_fact_trades
from scripts.ops.materialize_weather_execution_evidence import materialize
from src.strategies.runtime.sync import sync_instance_specs
from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.db.connection import apply_pragmas
from weather_dashboard.legacy_migration.strategy_runtime_orders import (
    migrate_strategy_runtime_orders,
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
