"""Live broker — production execution with safety checks.

Implements BrokerInterface for real Polymarket CLOB trading.
All orders pass through SafetyGuard before hitting the network.

STATUS: STUB — implementation pending API integration.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from src.domains.pmm.execution.broker_interface import BrokerInterface
from src.domains.pmm.risk.safety_guard import SafetyGuard


class LiveBroker(BrokerInterface):
    """Live execution broker with built-in safety guard.

    By default operates in dry_run mode — logs orders but does not
    send them to the exchange.
    """

    def __init__(
        self,
        http_client: Any,
        safety_guard: SafetyGuard,
        dry_run: bool = True,
    ) -> None:
        self.client = http_client
        self.guard = safety_guard
        self.dry_run = dry_run

    def get_balance(self) -> Dict[str, Any]:
        # TODO: implement via http_client
        raise NotImplementedError("LiveBroker.get_balance not yet implemented")

    def get_positions(self, token_ids: List[str]) -> Dict[str, float]:
        # TODO: implement via http_client
        raise NotImplementedError("LiveBroker.get_positions not yet implemented")

    def get_orders(self, token_id: str = "") -> List[Dict[str, Any]]:
        # TODO: implement via http_client
        raise NotImplementedError("LiveBroker.get_orders not yet implemented")

    def place_limit_order(
        self, token_id: str, price: float, size: float, side: str
    ) -> Dict[str, Any]:
        # Step 1: SafetyGuard pre-check
        self.guard.validate_order(token_id=token_id, price=price, size=size, side=side)

        # Step 2: Dry-run mode
        if self.dry_run:
            return {
                "order_id": f"dry_{int(time.time() * 1000)}",
                "status": "simulated",
                "token_id": token_id,
                "side": side,
                "price": price,
                "size": size,
            }

        # Step 3: Real execution (TODO)
        raise NotImplementedError("Live order placement not yet implemented")

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        if self.dry_run:
            return {"order_id": order_id, "status": "dry_cancel"}
        raise NotImplementedError("Live cancel not yet implemented")

    def cancel_orders(self, order_ids: List[str]) -> Dict[str, Any]:
        if self.dry_run:
            return {"cancelled": order_ids, "status": "dry_cancel"}
        raise NotImplementedError("Live bulk cancel not yet implemented")

    def merge_pair(
        self, yes_token_id: str, no_token_id: str, amount: int
    ) -> Dict[str, Any]:
        if self.dry_run:
            return {"status": "dry_merge", "amount": amount}
        raise NotImplementedError("Live merge not yet implemented")
