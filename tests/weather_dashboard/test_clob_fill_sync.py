from weather_dashboard.ingest.clob_fill_sync import (
    _public_trade_key,
    _public_trade_matches_order,
)


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
