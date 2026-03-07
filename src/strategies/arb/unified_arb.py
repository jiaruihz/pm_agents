"""Pilot Arb Strategy running on the unified engine."""

import logging
from typing import List, Union

from src.platform.engine.base import IStrategy, StrategyContext
from src.platform.engine.models import OrderCommand, CancelCommand, OrderSide, OrderType, OrderStatus
from src.platform.engine.events import MarketTickEvent, OrderUpdateEvent, AlertEvent

logger = logging.getLogger(__name__)


class UnifiedArbStrategy(IStrategy):
    """
    A simple mockup Arbitrage strategy to validate the engine flow.
    It buys YES if probability is below a threshold and NO is also cheap.
    """

    async def init(self, context: StrategyContext) -> None:
        logger.info(f"UnifiedArbStrategy {context.instance_id} initialized.")
        # Setup initial state
        context.state["trades_count"] = 0
        context.state["max_trades"] = context.run_params.get("max_trades", 5)

    async def on_market_tick(
        self, context: StrategyContext, event: MarketTickEvent
    ) -> List[Union[OrderCommand, CancelCommand]]:
        token_id = event.token_id
        mid = event.mid_price
        
        logger.info(f"[ArbTick] {token_id} MidPrice={mid}")

        # Dummy Arb Logic: if we haven't hit max trades and price is strangely low
        if mid and mid < 0.3 and context.state["trades_count"] < context.state["max_trades"]:
            # Let's say we spotted an arb opportunity
            logger.info(f"[ArbTick] Opportunity found! Buying 10 shares of {token_id}")
            context.state["trades_count"] += 1
            
            return [
                OrderCommand(
                    instance_id=context.instance_id,
                    strategy_key=self.key,
                    token_id=token_id,
                    side=OrderSide.BUY,
                    size=10.0,
                    price=mid + 0.01,  # Buy slightly above mid
                    order_type=OrderType.LIMIT
                )
            ]
            
        return []

    async def on_order_update(
        self, context: StrategyContext, event: OrderUpdateEvent
    ) -> List[Union[OrderCommand, CancelCommand]]:
        logger.info(f"[ArbOrderUpdate] Order {event.order_id} is now {event.status}")
        
        if event.status == OrderStatus.FILLED:
            logger.info("Yippee! Arb order filled.")
            
        return []
