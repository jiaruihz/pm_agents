from src.strategies.pmm.config import PMMConfig
from src.strategies.pmm.core.pricing import compute_quotes_pro
from src.strategies.pmm.core.sizing import target_sizes


def test_asymmetric_inventory_skew_only_moves_buy_side_when_long():
    neutral = compute_quotes_pro(
        mid=0.50,
        spread_ticks=4,
        tick_size=0.01,
        position=0,
        max_position=100,
    )
    long_inventory = compute_quotes_pro(
        mid=0.50,
        spread_ticks=4,
        tick_size=0.01,
        position=60,
        max_position=100,
    )

    assert long_inventory.bid < neutral.bid
    assert long_inventory.ask == neutral.ask


def test_asymmetric_inventory_skew_only_moves_sell_side_when_short():
    neutral = compute_quotes_pro(
        mid=0.50,
        spread_ticks=4,
        tick_size=0.01,
        position=0,
        max_position=100,
    )
    short_inventory = compute_quotes_pro(
        mid=0.50,
        spread_ticks=4,
        tick_size=0.01,
        position=-60,
        max_position=100,
    )

    assert short_inventory.bid == neutral.bid
    assert short_inventory.ask > neutral.ask


def test_symmetric_inventory_skew_remains_available():
    neutral = compute_quotes_pro(
        mid=0.50,
        spread_ticks=4,
        tick_size=0.01,
        position=0,
        max_position=100,
        inventory_skew_mode="symmetric",
    )
    long_inventory = compute_quotes_pro(
        mid=0.50,
        spread_ticks=4,
        tick_size=0.01,
        position=60,
        max_position=100,
        inventory_skew_mode="symmetric",
    )

    assert long_inventory.bid < neutral.bid
    assert long_inventory.ask < neutral.ask


def test_target_sizes_deducts_open_buy_exposure_from_position_limit():
    config = PMMConfig(base_size=50, max_position=100)

    buy_size, sell_size = target_sizes(
        config=config,
        position=80,
        usdc_balance=1000,
        bid_price=0.50,
        open_buy_qty=15,
        open_sell_qty=0,
    )

    assert buy_size == 5
    assert sell_size == 50


def test_target_sizes_deducts_open_sell_exposure_when_inventory_enforced():
    config = PMMConfig(base_size=50, max_position=100, enforce_inventory_for_sell=True)

    buy_size, sell_size = target_sizes(
        config=config,
        position=20,
        usdc_balance=1000,
        bid_price=0.50,
        open_buy_qty=0,
        open_sell_qty=15,
    )

    assert buy_size == 50
    assert sell_size == 5
