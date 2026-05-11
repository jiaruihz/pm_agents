"""Live broker — production execution with safety checks."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from src.strategies.pmm.execution.broker_interface import BrokerInterface
from src.platform.quote_runtime.risk.safety_guard import SafetyGuard

logger = logging.getLogger("pmm.live_broker")


class LiveBroker(BrokerInterface):
    """Live execution broker with built-in safety guard.

    By default operates in dry_run mode — logs orders but does not
    send them to the exchange.
    """

    def __init__(
        self,
        http_client: Any,
        safety_guard: Optional[SafetyGuard] = None,
        dry_run: bool = True,
    ) -> None:
        self.client = http_client
        self.guard = safety_guard
        self.dry_run = dry_run

    async def get_balance(self) -> Dict[str, Any]:
        return await self.client.retry(self.client.get_balance)

    async def get_positions(self, token_ids: List[str]) -> Dict[str, float]:
        return await self.client.retry(self.client.get_positions, token_ids)

    async def get_orders(self, token_id: str = "") -> List[Dict[str, Any]]:
        return await self.client.retry(self.client.get_orders, token_id)

    async def place_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        current_position: Optional[float] = None,
    ) -> Dict[str, Any]:
        if self.guard is not None:
            position_for_check = current_position
            if position_for_check is None:
                # Fallback path for direct broker calls outside tick_loop.
                pos_map = await self.client.retry(self.client.get_positions, [token_id])
                position_for_check = float((pos_map or {}).get(token_id, 0.0))
            self.guard.validate_order(
                token_id=token_id,
                price=price,
                size=size,
                side=side,
                current_position=position_for_check,
            )

        if self.dry_run:
            return {
                "order_id": f"dry_{int(time.time() * 1000)}",
                "status": "simulated",
                "token_id": token_id,
                "side": side,
                "price": price,
                "size": size,
            }

        return await self.client.retry(
            self.client.place_limit_order,
            token_id,
            price,
            size,
            side,
        )

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        if self.dry_run:
            return {"order_id": order_id, "status": "dry_cancel"}
        return await self.client.retry(self.client.cancel_order, order_id)

    async def cancel_orders(self, order_ids: List[str]) -> Dict[str, Any]:
        if self.dry_run:
            return {"cancelled": order_ids, "status": "dry_cancel"}
        return await self.client.retry(self.client.cancel_orders, order_ids)

    async def cancel_all_orders(self) -> Dict[str, Any]:
        if self.dry_run:
            return {"status": "dry_cancel_all"}
        return await self.client.retry(self.client.cancel_all_orders)

    async def merge_positions(
        self,
        condition_id: str,
        partition: List[int],
        amount: int,
        collateral_token: str = "",
        parent_collection_id: str = "",
    ) -> Dict[str, Any]:
        if self.dry_run:
            return {
                "status": "dry_merge_positions",
                "condition_id": condition_id,
                "partition": partition,
                "amount": amount,
                "collateral_token": collateral_token,
                "parent_collection_id": parent_collection_id,
            }
        return await self.client.retry(
            self.client.merge_positions,
            condition_id,
            partition,
            amount,
            collateral_token,
            parent_collection_id,
        )

    async def merge_pair(
        self, yes_token_id: str, no_token_id: str, amount: int
    ) -> Dict[str, Any]:
        if self.dry_run:
            return {
                "status": "dry_merge",
                "yes_token_id": yes_token_id,
                "no_token_id": no_token_id,
                "amount": amount,
            }
        if hasattr(self.client, "merge_pair"):
            return await self.client.retry(
                self.client.merge_pair,
                yes_token_id,
                no_token_id,
                amount,
            )
        logger.warning(
            "merge_pair endpoint unavailable on client; returning unsupported response",
            extra={
                "yes_token_id": yes_token_id,
                "no_token_id": no_token_id,
                "amount": amount,
            },
        )
        return {
            "status": "unsupported",
            "reason": "client.merge_pair endpoint unavailable",
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
            "amount": amount,
        }
