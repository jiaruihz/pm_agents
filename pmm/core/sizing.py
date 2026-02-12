"""Position sizing logic.

Extracted from tick_loop.py for independent testing and reuse.
"""
from __future__ import annotations

from pmm.config import PMMConfig


def target_sizes(
    config: PMMConfig,
    position: float,
    usdc_balance: float,
    bid_price: float,
) -> tuple[float, float]:
    """Compute (buy_size, sell_size) respecting balance, position limits, and inventory."""
    buy = min(config.base_size, usdc_balance / max(bid_price, 0.0001))
    if config.max_position > 0:
        buy = min(buy, max(0.0, config.max_position - position))

    if config.enforce_inventory_for_sell:
        sell = min(config.base_size, max(0.0, position))
    else:
        sell = config.base_size
    return max(0.0, buy), max(0.0, sell)
