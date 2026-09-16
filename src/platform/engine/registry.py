"""Registry to manage active strategy instances and their lifecycles."""

import asyncio
import logging
from typing import Dict, List, Optional, Any

from src.platform.engine.base import IStrategy, StrategyContext
from src.platform.engine.models import OrderCommand, CancelCommand
from src.platform.engine.events import MarketTickEvent, OrderUpdateEvent, AlertEvent
from src.platform.engine.dispatcher import EventDispatcher

logger = logging.getLogger(__name__)


class StrategyRegistry:
    """
    Holds loaded strategy instances in memory and routes events to them.
    Also collects commands emitted by the strategies and forwards them.
    """

    def __init__(self, dispatcher: EventDispatcher, command_queue: asyncio.Queue[Any]) -> None:
        # instance_id -> (IStrategy, StrategyContext)
        self._instances: Dict[str, tuple[IStrategy, StrategyContext]] = {}
        # token_id -> set of instance_ids interested in this token
        self._subscriptions: Dict[str, set[str]] = {}
        
        self.dispatcher = dispatcher
        self.command_queue = command_queue

        # Bind to dispatcher
        self.dispatcher.subscribe(MarketTickEvent, self._handle_market_tick)
        self.dispatcher.subscribe(OrderUpdateEvent, self._handle_order_update)

    async def register(
        self,
        strategy: IStrategy,
        context: StrategyContext,
        subscribe_tokens: List[str]
    ) -> None:
        """Register a new strategy instance and initialize it."""
        instance_id = context.instance_id
        if instance_id in self._instances:
            logger.warning(f"Strategy instance {instance_id} is already registered.")
            return

        self._instances[instance_id] = (strategy, context)
        
        for token_id in subscribe_tokens:
            if token_id not in self._subscriptions:
                self._subscriptions[token_id] = set()
            self._subscriptions[token_id].add(instance_id)

        try:
            # Initialize strategy state
            await strategy.init(context)
            logger.info(f"Registered and initialized strategy {instance_id}")
        except Exception as e:
            logger.error(f"Failed to initialize strategy {instance_id}: {e}", exc_info=True)
            self.dispatcher.publish(AlertEvent(level="ERROR", message=f"Init failed: {e}", instance_id=instance_id))

    def unregister(self, instance_id: str) -> None:
        """Remove a strategy instance from the registry."""
        if instance_id in self._instances:
            del self._instances[instance_id]
            for subs in self._subscriptions.values():
                subs.discard(instance_id)
            logger.info(f"Unregistered strategy {instance_id}")

    async def _handle_market_tick(self, event: MarketTickEvent) -> None:
        """Route market data to interested strategies."""
        token_id = event.token_id
        interested_instances = self._subscriptions.get(token_id, set())

        tasks = []
        for instance_id in interested_instances:
            if instance_id in self._instances:
                strategy, context = self._instances[instance_id]
                tasks.append(self._invoke_tick(strategy, context, event))

        if tasks:
            await asyncio.gather(*tasks)

    async def _invoke_tick(self, strategy: IStrategy, context: StrategyContext, event: MarketTickEvent) -> None:
        try:
            commands = await strategy.on_market_tick(context, event)
            if commands:
                self._route_commands(commands)
        except Exception as e:
            logger.error(f"Error in strategy {context.instance_id} tick: {e}", exc_info=True)
            self.dispatcher.publish(AlertEvent(level="ERROR", message=f"Tick error: {e}", instance_id=context.instance_id))

    async def _handle_order_update(self, event: OrderUpdateEvent) -> None:
        """Route order update back to the exact strategy that placed it."""
        instance_id = event.instance_id
        if instance_id in self._instances:
            strategy, context = self._instances[instance_id]
            try:
                commands = await strategy.on_order_update(context, event)
                if commands:
                    self._route_commands(commands)
            except Exception as e:
                logger.error(f"Error in strategy {instance_id} order update: {e}", exc_info=True)

    def _route_commands(self, commands: List[Any]) -> None:
        """Push action commands from strategies to the execution queue."""
        for cmd in commands:
            if isinstance(cmd, (OrderCommand, CancelCommand)):
                self.command_queue.put_nowait(cmd)
