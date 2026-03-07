"""Executor service that consumes commands and interacts with Polymarket clients."""

import asyncio
import logging
from typing import Dict, Any, Optional

from src.platform.engine.base import OrderCommand, CancelCommand
from src.platform.engine.events import OrderUpdateEvent, AlertEvent
from src.platform.engine.models import OrderStatus, OrderType, OrderSide
from src.platform.engine.dispatcher import EventDispatcher
from src.platform.strategy_runtime.store import StrategyRuntimeStore

logger = logging.getLogger(__name__)


class ExecutionService:
    """
    Consumes OrderCommand and CancelCommand from the queue.
    Executes actual network requests (e.g. against Polymarket CLOB or Gamma).
    """

    def __init__(
        self,
        dispatcher: EventDispatcher,
        command_queue: asyncio.Queue,
        runtime_store: StrategyRuntimeStore,
        gamma_client: Any = None
    ) -> None:
        self.dispatcher = dispatcher
        self.command_queue = command_queue
        self.runtime_store = runtime_store
        self.gamma_client = gamma_client
        
        self._is_running = False
        self._execution_task: Optional[asyncio.Task[Any]] = None

    async def start(self) -> None:
        if self._is_running:
            return
        self._is_running = True
        self._execution_task = asyncio.create_task(self._process_commands())
        logger.info("ExecutionService started.")

    async def stop(self) -> None:
        self._is_running = False
        if self._execution_task:
            self._execution_task.cancel()
            try:
                await self._execution_task
            except asyncio.CancelledError:
                pass
            self._execution_task = None
        logger.info("ExecutionService stopped.")

    async def _process_commands(self) -> None:
        while self._is_running:
            try:
                cmd = await self.command_queue.get()
                
                if isinstance(cmd, OrderCommand):
                    await self._handle_order_command(cmd)
                elif isinstance(cmd, CancelCommand):
                    await self._handle_cancel_command(cmd)
                else:
                    logger.warning(f"Unknown command type: {type(cmd)}")

                self.command_queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Execution loop error: {e}", exc_info=True)
                self.dispatcher.publish(AlertEvent(level="ERROR", message=f"Execution error: {e}"))

    async def _handle_order_command(self, cmd: OrderCommand) -> None:
        """Place a new order via the API."""
        instance_id = cmd.instance_id
        token_id = cmd.token_id
        side = cmd.side.value
        size = cmd.size
        price = cmd.price
        
        # generate a local unique order_id and insert PENDING state into DB
        # PM CLOB will return a real order_id, we map it or overwrite it
        internal_id = f"local_{cmd.strategy_key}_{instance_id}_{token_id}_{side}_{price}_{size}"
        if cmd.strategy_order_id:
            internal_id = cmd.strategy_order_id

        self.runtime_store.upsert_trade_order(
            order_id=internal_id,
            instance_id=instance_id,
            strategy_key=cmd.strategy_key,
            token_id=token_id,
            side=side,
            size=size,
            price=price,
            order_type=cmd.order_type.value,
            status=OrderStatus.PENDING.value,
            metadata={"strategy_order_id": cmd.strategy_order_id}
        )

        try:
            # Here we would actually call the network API (e.g. self.clob_client.create_order)
            # For the architecture frame, we mock success for now unless you want real POST
            # simulated network delay
            await asyncio.sleep(0.1)

            final_status = OrderStatus.OPEN
            if cmd.order_type == OrderType.FOK or cmd.order_type == OrderType.MARKET:
                final_status = OrderStatus.FILLED

            # Update DB with OPEN or FILLED
            self.runtime_store.upsert_trade_order(
                order_id=internal_id,
                instance_id=instance_id,
                strategy_key=cmd.strategy_key,
                token_id=token_id,
                side=side,
                size=size,
                price=price,
                order_type=cmd.order_type.value,
                status=final_status.value,
            )

            # Emit Event back to Strategy
            update_event = OrderUpdateEvent(
                instance_id=instance_id,
                order_id=internal_id,
                token_id=token_id,
                status=final_status,
                filled_size=size if final_status == OrderStatus.FILLED else 0.0,
                average_price=price if final_status == OrderStatus.FILLED else 0.0,
            )
            self.dispatcher.publish(update_event)

        except Exception as e:
            logger.error(f"Failed to place order {internal_id}: {e}", exc_info=True)
            self.runtime_store.upsert_trade_order(
                order_id=internal_id,
                instance_id=instance_id,
                strategy_key=cmd.strategy_key,
                token_id=token_id,
                side=side,
                size=size,
                price=price,
                order_type=cmd.order_type.value,
                status=OrderStatus.ERROR.value,
            )
            self.dispatcher.publish(OrderUpdateEvent(
                instance_id=instance_id,
                order_id=internal_id,
                token_id=token_id,
                status=OrderStatus.ERROR,
                error_message=str(e)
            ))

    async def _handle_cancel_command(self, cmd: CancelCommand) -> None:
        """Cancel an existing order via the API."""
        instance_id = cmd.instance_id
        order_id = cmd.order_id

        try:
            # call self.clob_client.cancel(...)
            await asyncio.sleep(0.1)
            
            # get current state to rewrite to DB
            orders = self.runtime_store.get_trade_orders(instance_id, limit=200)
            target = next((o for o in set(orders) if getattr(o, "order_id", None) == order_id), None)
            
            if target:
                self.runtime_store.upsert_trade_order(
                    order_id=order_id,
                    instance_id=instance_id,
                    strategy_key=target.get("strategy_key", ""),  # type: ignore
                    token_id=target.get("token_id", ""),  # type: ignore
                    side=target.get("side", ""),          # type: ignore
                    size=float(target.get("size", 0.0)),         # type: ignore
                    price=float(target.get("price", 0.0)),       # type: ignore
                    order_type=target.get("order_type", "LIMIT"),# type: ignore
                    status=OrderStatus.CANCELED.value,
                )
                self.dispatcher.publish(OrderUpdateEvent(
                    instance_id=instance_id,
                    order_id=order_id,
                    token_id=target.get("token_id", ""),  # type: ignore
                    status=OrderStatus.CANCELED
                ))
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}", exc_info=True)
