"""Smart Money Copy Trading strategy for the unified engine."""

import logging
from typing import List, Union, Dict, Any

from src.platform.engine.base import IStrategy, StrategyContext
from src.platform.engine.events import MarketTickEvent, OrderUpdateEvent, AlertEvent
from src.platform.engine.models import OrderCommand, CancelCommand, OrderSide, OrderType, OrderStatus

logger = logging.getLogger(__name__)


class UnifiedCopyTradingStrategy(IStrategy):
    """
    Listens to a specific subset of "Smart Money" wallets or external signals
    and replicates their trades into our own account proportionally.
    """

    key = "smart_money_copy"

    async def init(self, context: StrategyContext) -> None:
        logger.info(f"Initialized {self.key} for instance {context.instance_id}")
        context.state["target_wallets"] = context.run_params.get("target_wallets", [])
        context.state["trade_multiplier"] = context.run_params.get("trade_multiplier", 1.0)
        context.state["max_position_per_token"] = context.run_params.get("max_position_per_token", 500.0)

    async def on_market_tick(
        self, context: StrategyContext, event: MarketTickEvent
    ) -> List[Union[OrderCommand, CancelCommand]]:
        """
        In a unified engine, a Copy Trade strategy would likely also listen to an `ExternalTradeEvent`
        or evaluate custom indicators piped through `event.context`.
        For now, we check if the feeder injected a 'smart_money_signal' in the tick context.
        """
        if not event.context or "smart_money_signal" not in event.context:
            return []

        signal: Dict[str, Any] = event.context["smart_money_signal"]
        wallet = signal.get("wallet_address")
        
        target_wallets = context.state.get("target_wallets", [])
        if wallet not in target_wallets:
            return []

        side_str = str(signal.get("side", "")).upper()
        if side_str not in {"BUY", "SELL"}:
            return []
            
        side = OrderSide.BUY if side_str == "BUY" else OrderSide.SELL
        
        # Calculate proportional size
        base_size = float(signal.get("size", 0.0))
        multiplier = float(context.state.get("trade_multiplier", 1.0))
        target_size = base_size * multiplier
        
        if target_size <= 0:
            return []

        token_id = event.token_id
        current_position = context.positions.get(token_id, 0.0)
        max_pos = float(context.state.get("max_position_per_token", 500.0))
        
        if side == OrderSide.BUY and (current_position + target_size) > max_pos:
            target_size = max(0.0, max_pos - current_position)
            if target_size <= 0:
                logger.debug(f"[{self.key}] Skipping copy buy: max position reached for {token_id}")
                return []
                
        if side == OrderSide.SELL and current_position < target_size:
            target_size = current_position
            if target_size <= 0:
                return []

        # Execute order at market price logic (simplistic)
        price = event.best_ask if side == OrderSide.BUY else event.best_bid
        if not price:
            price = event.mid_price
            
        if not price:
            logger.warning(f"[{self.key}] Cannot copy trade without orderbook pricing for {token_id}")
            return []

        logger.info(f"[{self.key}] Copying trade from {wallet} on {token_id}: {side.value} {target_size} @ {price}")
        return [
            OrderCommand(
                instance_id=context.instance_id,
                strategy_key=self.key,
                token_id=token_id,
                side=side,
                size=target_size,
                price=price,
                order_type=OrderType.MARKET
            )
        ]

    async def on_order_update(
        self, context: StrategyContext, event: OrderUpdateEvent
    ) -> List[Union[OrderCommand, CancelCommand]]:
        """Log the result of our copied trade."""
        if event.status == OrderStatus.FILLED:
            logger.info(f"[{self.key}] Copied trade successfully filled for {event.token_id}")
        return []
