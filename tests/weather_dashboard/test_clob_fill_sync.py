import sqlite3

import weather_dashboard.ingest.clob_fill_sync as clob_fill_sync
from weather_dashboard.ingest.clob_fill_sync import (
    _cap_reported_fill_to_order,
    _exact_activity_fee_for_fill,
    _extract_trade_fill,
    _extract_immediate_place_fill,
    _fallback_fee_details,
    _index_public_activity_by_tx,
    _index_authenticated_trades_by_order_id,
    _insert_order_fill_top_up,
    _insert_fill,
    _public_trade_key,
    _public_buy_fee_details,
    _public_trade_matches_order,
    _resolve_fee_details,
    _select_public_partial_fills,
    _submitted_order_price_cap,
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


def test_authenticated_trade_index_uses_exact_nested_maker_leg():
    order_id = "our-maker-order"
    indexed = _index_authenticated_trades_by_order_id([
        {
            "id": "aggregate-match",
            "size": "147.34",
            "price": "0.76",
            "taker_order_id": "our-taker-order",
            "match_time": "1785036267",
            "transaction_hash": "0xtx",
            "maker_orders": [
                {"order_id": "other-order", "matched_amount": "8", "price": "0.76"},
                {"order_id": order_id, "matched_amount": "5", "price": "0.77"},
            ],
        }
    ])

    maker_leg = indexed[order_id][0]
    assert {key: maker_leg[key] for key in ("order_id", "size", "price", "match_time")} == {
        "order_id": order_id,
        "size": "5",
        "price": "0.77",
        "match_time": "1785036267",
    }
    assert indexed["our-taker-order"][0]["id"] == "aggregate-match"


def test_authenticated_trade_fill_uses_snake_case_match_time():
    fill = _extract_trade_fill(
        [{"size": "5", "price": "0.77", "match_time": "1785036267"}],
        row_shares=10.0,
        row_limit_price=0.77,
    )

    assert fill["filled_at"] == "2026-07-26T03:24:27+00:00"


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
    monkeypatch.setattr(clob_fill_sync, "import_fee_adjustments", lambda _conn, _path: 0)
    monkeypatch.setattr(clob_fill_sync, "import_price_adjustments", lambda _conn, _path: 0)
    monkeypatch.setattr(
        clob_fill_sync, "import_timestamp_adjustments", lambda _conn, _path: 0
    )
    monkeypatch.setattr(
        clob_fill_sync, "import_validity_adjustments", lambda _conn, _path: 0
    )

    def fail_if_scanned(_conn):
        raise AssertionError("cache-only mode should not scan submitted orders")

    monkeypatch.setattr(clob_fill_sync, "_get_submitted_orders", fail_if_scanned)

    result = sync_clob_fills(conn, cache_only=True)

    assert result["cached_imported"] == 7
    assert result["checked"] == 0
    assert result["cache_only"] is True


def test_sync_required_auth_refuses_public_fallback(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(
        clob_fill_sync,
        "_get_submitted_orders",
        lambda _conn, **_kwargs: [{"execution_id": "execution"}],
    )
    monkeypatch.setattr(clob_fill_sync, "_build_clob_client", lambda: None)

    def fail_if_public_fallback_runs(*_args, **_kwargs):
        raise AssertionError("required-auth mode must not fetch public activity")

    monkeypatch.setattr(
        clob_fill_sync, "_fetch_activity_public", fail_if_public_fallback_runs
    )

    result = sync_clob_fills(
        conn,
        dry_run=True,
        maker_address="maker",
        require_authenticated=True,
    )

    assert result["require_authenticated"] is True
    assert result["data_incomplete"] is True
    assert result["errors"] == 1
    assert result["external_fetch_errors"] == 1
    assert result["filled"] == 0


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


def test_submitted_order_price_cap_prefers_exchange_posted_tick_price():
    row = {"limit_price": 0.791, "posted_price": 0.80}

    assert _submitted_order_price_cap(row) == 0.80


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
            "takingAmount": "9.61",
            "transactionsHashes": ["0xabc"]
          }
        }
        """,
    }

    fill = _extract_immediate_place_fill(row)

    assert fill["filled_shares"] == 9.61
    assert fill["filled_price"] == 3.90166 / 9.61
    assert fill["transaction_hashes"] == ["0xabc"]
    assert fill["fee_source"] == "weather_fee_curve_estimate"
    assert fill["fees_usd"] > 0
    assert fill["filled_at_utc"] == "2026-06-04T07:37:10+00:00"


def test_exact_transaction_activity_fee_beats_estimate():
    activity = {
        "transactionHash": "0xc055",
        "conditionId": "cond",
        "asset": "token",
        "side": "BUY",
        "outcome": "No",
        "size": 5,
        "price": 0.67,
        "usdcSize": 3.40527,
    }
    exact = _exact_activity_fee_for_fill(
        transaction_hashes=["0xc055"],
        activity_by_tx=_index_public_activity_by_tx([activity]),
        condition_id="cond",
        token_id="token",
        order_side="BUY_NO",
        expected_shares=5.0,
    )

    assert exact is not None
    assert exact["fees_usd"] == 0.05527
    assert exact["fee_source"] == "public_activity_tx_exact"


def test_public_partial_buy_derives_exact_cash_delta_fee():
    details = _public_buy_fee_details(
        {"side": "BUY", "size": 2.5, "price": 0.40, "usdcSize": 1.03}
    )

    assert details is not None
    assert details["fees_usd"] == 0.03
    assert details["fee_source"] == "public_activity_cash_delta_exact"


def test_maker_fill_has_explicit_zero_fee_source():
    details = _fallback_fee_details(shares=5.0, price=0.67, maker_only=True)

    assert details["fees_usd"] == 0.0
    assert details["fee_source"] == "maker_zero"


def test_authenticated_nonzero_taker_fee_is_authoritative():
    details = _resolve_fee_details(
        authenticated_fee_usd=0.07,
        authenticated_fee_rate=0.05,
        authenticated_metadata={"takerFee": "70000"},
        transaction_hashes=["0xc055"],
        activity_by_tx=_index_public_activity_by_tx(
            [{
                "transactionHash": "0xc055", "conditionId": "cond", "asset": "token",
                "side": "BUY", "outcome": "No", "size": 5, "price": 0.67,
                "usdcSize": 3.40527,
            }]
        ),
        condition_id="cond",
        token_id="token",
        order_side="BUY_NO",
        shares=5.0,
        price=0.67,
        maker_only=False,
    )

    assert details["fees_usd"] == 0.07
    assert details["fee_source"] == "authenticated_taker_fee"
