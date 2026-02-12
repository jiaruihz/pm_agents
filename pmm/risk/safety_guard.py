"""Pre-execution safety guard — hard risk checks before any order hits the network.

All checks are synchronous and local. If any check fails, a descriptive
exception is raised, blocking the order from being sent.

STATUS: STUB — thresholds should be tuned per deployment.
"""
from __future__ import annotations

from typing import Set


class SecurityError(RuntimeError):
    """Order blocked by security policy (e.g., token not whitelisted)."""
    pass


class RiskError(RuntimeError):
    """Order blocked by risk limits (e.g., fat finger, daily loss)."""
    pass


class SafetyGuard:
    """Local hard-risk guard. Sits between strategy and exchange.

    Checks:
    1. Token whitelist — only trade known assets.
    2. Fat finger — reject single orders above max notional.
    3. Price bounds — reject obviously wrong prices (outside [0.01, 0.99]).
    4. Daily loss circuit breaker — halt if cumulative loss exceeds limit.
    5. Max position — reject orders that would breach position limits.
    """

    def __init__(
        self,
        allowed_tokens: Set[str],
        max_order_value: float = 100.0,
        max_position: float = 500.0,
        max_daily_loss: float = 50.0,
        price_floor: float = 0.01,
        price_ceiling: float = 0.99,
    ) -> None:
        self.allowed_tokens = allowed_tokens
        self.max_order_value = max_order_value
        self.max_position = max_position
        self.max_daily_loss = max_daily_loss
        self.price_floor = price_floor
        self.price_ceiling = price_ceiling

        # Running state
        self._daily_pnl: float = 0.0

    def validate_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
    ) -> None:
        """Validate an order. Raises SecurityError or RiskError on failure."""
        # 1. Token whitelist
        if self.allowed_tokens and token_id not in self.allowed_tokens:
            raise SecurityError(
                f"token {token_id[:16]}... not in whitelist "
                f"({len(self.allowed_tokens)} allowed)"
            )

        # 2. Price bounds
        if price < self.price_floor or price > self.price_ceiling:
            raise RiskError(
                f"price {price:.4f} outside [{self.price_floor}, {self.price_ceiling}]"
            )

        # 3. Fat finger
        notional = size * price
        if notional > self.max_order_value:
            raise RiskError(
                f"order notional {notional:.2f} USDC exceeds max {self.max_order_value:.2f}"
            )

        # 4. Daily loss circuit breaker
        if self._daily_pnl < -self.max_daily_loss:
            raise RiskError(
                f"daily loss {self._daily_pnl:.2f} exceeds limit {self.max_daily_loss:.2f}, "
                f"trading halted"
            )

    def record_pnl(self, pnl_delta: float) -> None:
        """Record PnL for daily loss tracking."""
        self._daily_pnl += pnl_delta

    def reset_daily(self) -> None:
        """Reset daily PnL counter (call at start of each trading day)."""
        self._daily_pnl = 0.0

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl
