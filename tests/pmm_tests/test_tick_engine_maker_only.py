from src.strategies.pmm.engine.tick_engine import _maker_only_price


def test_maker_only_price_moves_buy_below_best_ask():
    adjusted = _maker_only_price(
        side="BUY",
        price=0.96,
        best_bid=0.95,
        best_ask=0.96,
        tick_size=0.01,
        tick_mode="nearest",
    )
    assert adjusted == 0.95


def test_maker_only_price_moves_sell_above_best_bid():
    adjusted = _maker_only_price(
        side="SELL",
        price=0.50,
        best_bid=0.50,
        best_ask=0.51,
        tick_size=0.01,
        tick_mode="nearest",
    )
    assert adjusted == 0.51


def test_maker_only_price_keeps_resting_order_unchanged():
    adjusted = _maker_only_price(
        side="BUY",
        price=0.94,
        best_bid=0.93,
        best_ask=0.96,
        tick_size=0.01,
        tick_mode="nearest",
    )
    assert adjusted == 0.94
