"""仓位与下单量计算逻辑。"""
from __future__ import annotations

from src.strategies.pmm.config import PMMConfig


def target_sizes(
    config: PMMConfig,
    position: float,
    usdc_balance: float,
    bid_price: float,
    open_buy_qty: float = 0.0,
    open_sell_qty: float = 0.0,
) -> tuple[float, float]:
    """计算 (buy_size, sell_size)。

    约束：
    - 买单受可用 USDC 和 max_position 限制；
    - 卖单可按开关要求受库存限制；
    - 返回值始终非负。
    """
    pending_buy = max(0.0, float(open_buy_qty))
    pending_sell = max(0.0, float(open_sell_qty))

    buy = min(config.base_size, usdc_balance / max(bid_price, 0.0001))
    if config.max_position > 0:
        buy = min(buy, max(0.0, config.max_position - position - pending_buy))

    if config.enforce_inventory_for_sell:
        sell = min(config.base_size, max(0.0, position - pending_sell))
    else:
        sell = config.base_size
    return max(0.0, buy), max(0.0, sell)
