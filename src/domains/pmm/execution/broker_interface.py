"""Abstract broker interface.

All brokers (Paper, Live) implement this interface so the engine
is agnostic to execution mode.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BrokerInterface(ABC):
    """Unified interface for paper and live brokers."""

    @abstractmethod
    async def get_balance(self) -> Dict[str, Any]:
        """Return current account balance."""
        ...

    @abstractmethod
    async def get_positions(self, token_ids: List[str]) -> Dict[str, float]:
        """Return position sizes for given token IDs."""
        ...

    @abstractmethod
    async def get_orders(self, token_id: str = "") -> List[Dict[str, Any]]:
        """Return open orders, optionally filtered by token_id."""
        ...

    @abstractmethod
    async def place_limit_order(
        self, token_id: str, price: float, size: float, side: str
    ) -> Dict[str, Any]:
        """Place a limit order. Returns order details."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel a single order by ID."""
        ...

    @abstractmethod
    async def cancel_orders(self, order_ids: List[str]) -> Dict[str, Any]:
        """Cancel multiple orders."""
        ...

    @abstractmethod
    async def cancel_all_orders(self) -> Dict[str, Any]:
        """Cancel all open orders."""
        ...

    @abstractmethod
    async def merge_positions(
        self,
        condition_id: str,
        partition: List[int],
        amount: int,
        collateral_token: str = "",
        parent_collection_id: str = "",
    ) -> Dict[str, Any]:
        """Merge paired positions by condition/partition."""
        ...

    @abstractmethod
    async def merge_pair(
        self, yes_token_id: str, no_token_id: str, amount: int
    ) -> Dict[str, Any]:
        """Merge YES + NO token positions to reclaim USDC."""
        ...
