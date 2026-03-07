"""Base interfaces for strategies running in the unified engine."""

import abc
from dataclasses import dataclass, field
from typing import Dict, Any, List, Union

from src.platform.engine.models import OrderCommand, CancelCommand
from src.platform.engine.events import MarketTickEvent, OrderUpdateEvent


@dataclass
class StrategyContext:
    """Runtime context for a strategy instance."""
    instance_id: str
    strategy_key: str
    run_params: Dict[str, Any] = field(default_factory=dict)
    
    # Track the current held position (shares) per token if applicable
    positions: Dict[str, float] = field(default_factory=dict)
    
    # A generic dictionary for strategies to keep state across ticks
    state: Dict[str, Any] = field(default_factory=dict)
    
    # The current running mode (e.g. DRY_RUN, LIVE)
    execution_mode: str = "DRY_RUN"


class IStrategy(abc.ABC):
    """
    The base class that all unified strategies must implement.
    Methods can return a list of Action Commands to be processed by Execution.
    """

    @abc.abstractmethod
    async def init(self, context: StrategyContext) -> None:
        """Called once when the strategy instance is loaded."""
        pass

    @abc.abstractmethod
    async def on_market_tick(
        self, context: StrategyContext, event: MarketTickEvent
    ) -> List[Union[OrderCommand, CancelCommand]]:
        """Called when new market data arrives. Return a list of commands to execute."""
        return []

    # Optional methods to override
    async def on_order_update(
        self, context: StrategyContext, event: OrderUpdateEvent
    ) -> List[Union[OrderCommand, CancelCommand]]:
        """Called when an order status changes."""
        return []
