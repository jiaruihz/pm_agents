import sqlite3

import weather_dashboard.ingest.clob_fill_sync as clob_fill_sync
from weather_dashboard.ingest.clob_fill_sync import (
    _cap_reported_fill_to_order,
    _extract_immediate_place_fill,
    _insert_order_fill_top_up,
    _insert_fill,
    _public_trade_key,
    _public_trade_matches_order,
    _select_public_partial_fills,
    sync_clob_fills,
)


def test_insert_fill_dedupes_same_physical_fill_with_different_id(monkeypatch):
    monkeypatch.setattr(clob_fill_sync, "append_cached_fill", lambda row: None)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE fills (
          fill_id TEXT PRIMARY KEY, execution_id TEXT, order_id TEXT,
          filled_shares REAL, filled_price REAL, fees_usd REAL,
          status TEXT, filled_at_utc TEXT, created_at_utc TEXT
        )
        """
    )
    kwargs = dict(
        execution_id="exec",
        order_id="order",
        filled_shares=5.0,
        filled_price=0.4,
        fees_usd=0.0,
        filled_at_utc="2026-07-10T01:00:00Z",
        dry_run=False,
    )

    assert _insert_fill(conn, fill_id="first", **kwargs)
    assert not _insert_fill(conn, fill_id="second", **kwargs)
    assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1


def test_public_trade_fallback_requires_exact_token():
    trade = {
        "conditionId": "cond",
        "asset": "other-token",
        "timestamp": 1779436328,
        "side": "BUY",
        "outcome": "No",
        "price": 0.58,
    }

    assert not _public_trade_matches_order(
        trade,
        condition_id="cond",
        token_id="wanted-token",
        order_side="BUY_NO",
        limit_price=0.60,
        placed_ts=1779436000,
    )


def test_public_trade_fallback_rejects_price_worse_than_buy_limit():
    trade = {
        "conditionId": "cond",
        "asset": "token",
        "timestamp": 1779436328,
        "side": "BUY",
        "outcome": "No",
        "price": 0.65,
    }

    assert not _public_trade_matches_order(
        trade,
        condition_id="cond",
        token_id="token",
        order_side="BUY_NO",
        limit_price=0.59,
        placed_ts=1779436000,
    )


def test_public_trade_key_distinguishes_same_market_trades():
    first = {
        "transactionHash": "0xaaa",
        "asset": "token",
        "timestamp": 1779436328,
        "side": "BUY",
        "outcome": "No",
        "size": 1,
        "price": 0.59,
    }
    second = {**first, "transactionHash": "0xbbb"}

    assert _public_trade_key(first) != _public_trade_key(second)


def test_public_partial_fill_selection_keeps_multiple_fills_for_one_order():
    trades = [
        {
            "transactionHash": "0xaaa",
            "conditionId": "cond",
            "asset": "token",
            "timestamp": 1779436328,
            "side": "BUY",
            "outcome": "No",
            "size": 2.0,
            "price": 0.50,
        },
        {
            "transactionHash": "0xbbb",
            "conditionId": "cond",
            "asset": "token",
            "timestamp": 1779436330,
            "side": "BUY",
            "outcome": "No",
            "size": 3.0,
            "price": 0.50,
        },
    ]

    selected = _select_public_partial_fills(
        trades,
        used_public_trade_keys=set(),
        condition_id="cond",
        token_id="token",
        order_side="BUY_NO",
        limit_price=0.50,
        row_shares=5.0,
        placed_ts=1779436000,
    )

    assert [row["transactionHash"] for row in selected] == ["0xaaa", "0xbbb"]


def test_order_fill_top_up_appends_missing_partial_without_reinserting_existing(monkeypatch):
    monkeypatch.setattr(clob_fill_sync, "append_cached_fill", lambda row: None)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE fills (
          fill_id TEXT PRIMARY KEY,
          execution_id TEXT NOT NULL,
          order_id TEXT NOT NULL,
          filled_shares REAL NOT NULL,
          filled_price REAL NOT NULL,
          fees_usd REAL NOT NULL,
          status TEXT NOT NULL,
          filled_at_utc TEXT,
          created_at_utc TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO fills (
          fill_id, execution_id, order_id, filled_shares, filled_price,
          fees_usd, status, filled_at_utc, created_at_utc
        ) VALUES ('base', 'exec', 'order', 2.0, 0.50, 0.0, 'filled', '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:00+00:00')
        """
    )
    conn.commit()

    inserted = _insert_order_fill_top_up(
        conn,
        base_fill_id="base",
        execution_id="exec",
        order_id="order",
        target_shares=5.0,
        target_price=0.50,
        fees_usd=0.0,
        filled_at_utc="2026-06-01T00:01:00+00:00",
        dry_run=False,
    )

    assert inserted
    rows = conn.execute(
        """
        SELECT COUNT(*) fills,
               ROUND(SUM(filled_shares), 6) shares,
               ROUND(SUM(filled_shares * filled_price), 6) cost
        FROM fills
        """
    ).fetchone()
    assert dict(rows) == {"fills": 2, "shares": 5.0, "cost": 2.5}

    inserted_again = _insert_order_fill_top_up(
        conn,
        base_fill_id="base",
        execution_id="exec",
        order_id="order",
        target_shares=5.0,
        target_price=0.50,
        fees_usd=0.0,
        filled_at_utc="2026-06-01T00:01:00+00:00",
        dry_run=False,
    )

    assert not inserted_again
    assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 2


def test_sync_cache_only_imports_cache_without_external_scan(monkeypatch):
    conn = sqlite3.connect(":memory:")
    monkeypatch.setattr(clob_fill_sync, "import_cached_fills", lambda _conn, _path: 7)

    def fail_if_scanned(_conn):
        raise AssertionError("cache-only mode should not scan submitted orders")

    monkeypatch.setattr(clob_fill_sync, "_get_submitted_orders", fail_if_scanned)

    result = sync_clob_fills(conn, cache_only=True)

    assert result["cached_imported"] == 7
    assert result["checked"] == 0
    assert result["cache_only"] is True


def test_cap_reported_fill_to_order_keeps_recovered_fill_inside_order_cap():
    shares, price = _cap_reported_fill_to_order(
        execution_id="execution",
        order_id="order",
        reported_shares=12.0,
        reported_price=0.62,
        row_shares=5.0,
        row_limit_price=0.40,
    )

    assert shares == 5.0
    assert price == 0.40


def test_extract_immediate_place_fill_uses_matched_order_making_taking_amounts():
    row = {
        "order_side": "BUY_NO",
        "placed_at_utc": "2026-06-04T07:37:10+00:00",
        "exchange_response": """
        {
          "place": {
            "status": "matched",
            "success": true,
            "makingAmount": "3.90166",
            "takingAmount": "9.61"
          }
        }
        """,
    }

    fill = _extract_immediate_place_fill(row)

    assert fill == {
        "filled_shares": 9.61,
        "filled_price": 3.90166 / 9.61,
        "fees_usd": 0.0,
        "filled_at_utc": "2026-06-04T07:37:10+00:00",
    }
