"""Pre-execution safety guard — hard risk checks before any order hits the network."""

from __future__ import annotations

from typing import Optional, Set


class SecurityError(RuntimeError):
    """Order blocked by security policy, for example token not whitelisted."""


class RiskError(RuntimeError):
    """Order blocked by risk limits, for example fat finger or daily loss."""


class SafetyGuard:
    """Local hard-risk guard. Sits between strategy and exchange.

    Checks:
    1. Token whitelist — only trade known assets.
    2. Fat finger — reject single orders above max notional.
    3. Price bounds — reject obviously wrong prices.
    4. Daily loss circuit breaker — halt if cumulative loss exceeds limit.
    5. Max position — reject orders that would breach position limits.
    """

    def __init__(
        self,
        allowed_tokens: Set[str],
        max_order_value: float = 100.0,
        max_position: float = 500.0,
        max_long_position: Optional[float] = None,
        max_short_position: Optional[float] = None,
        max_buy_order_value: Optional[float] = None,
        max_sell_order_value: Optional[float] = None,
        max_daily_loss: float = 50.0,
        price_floor: float = 0.01,
        price_ceiling: float = 0.99,
    ) -> None:
        self.allowed_tokens = allowed_tokens
        self.max_order_value = max_order_value
        self.max_position = max_position
        self.max_long_position = (
            max_long_position if max_long_position is not None else max_position
        )
        self.max_short_position = (
            max_short_position if max_short_position is not None else max_position
        )
        self.max_buy_order_value = (
            max_buy_order_value if max_buy_order_value is not None else max_order_value
        )
        self.max_sell_order_value = (
            max_sell_order_value if max_sell_order_value is not None else max_order_value
        )
        self.max_daily_loss = max_daily_loss
        self.price_floor = price_floor
        self.price_ceiling = price_ceiling
        self._daily_pnl: float = 0.0

    @staticmethod
    def predict_position_after_order(
        current_position: float,
        size: float,
        side: str,
    ) -> float:
        side_u = str(side).strip().upper()
        if side_u == "BUY":
            return current_position + size
        if side_u == "SELL":
            return current_position - size
        raise RiskError(f"invalid side '{side}' (expected BUY/SELL)")

    def validate_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        current_position: Optional[float] = None,
    ) -> None:
        """Validate an order. Raises SecurityError or RiskError on failure."""
        side_u = str(side).strip().upper()
        if side_u not in {"BUY", "SELL"}:
            raise RiskError(f"invalid side '{side}' (expected BUY/SELL)")

        if size <= 0:
            raise RiskError(f"size must be > 0, got {size}")

        if self.allowed_tokens and token_id not in self.allowed_tokens:
            raise SecurityError(
                f"token {token_id[:16]}... not in whitelist "
                f"({len(self.allowed_tokens)} allowed)"
            )

        if price < self.price_floor or price > self.price_ceiling:
            raise RiskError(
                f"price {price:.4f} outside [{self.price_floor}, {self.price_ceiling}]"
            )

        notional = size * price
        if notional > self.max_order_value:
            raise RiskError(
                f"order notional {notional:.2f} USDC exceeds max {self.max_order_value:.2f}"
            )
        if side_u == "BUY" and notional > self.max_buy_order_value:
            raise RiskError(
                f"BUY notional {notional:.2f} USDC exceeds BUY cap {self.max_buy_order_value:.2f}"
            )
        if side_u == "SELL" and notional > self.max_sell_order_value:
            raise RiskError(
                f"SELL notional {notional:.2f} USDC exceeds SELL cap {self.max_sell_order_value:.2f}"
            )

        if self._daily_pnl <= -self.max_daily_loss:
            raise RiskError(
                f"daily loss {self._daily_pnl:.2f} exceeds limit {self.max_daily_loss:.2f}, "
                f"trading halted"
            )

        base_position = float(current_position or 0.0)
        projected = self.predict_position_after_order(
            current_position=base_position,
            size=size,
            side=side_u,
        )
        if abs(projected) > self.max_position:
            raise RiskError(
                f"projected abs position {abs(projected):.4f} exceeds max_position {self.max_position:.4f}"
            )
        if projected > self.max_long_position:
            raise RiskError(
                f"projected long position {projected:.4f} exceeds max_long_position {self.max_long_position:.4f}"
            )
        if projected < -self.max_short_position:
            raise RiskError(
                f"projected short position {projected:.4f} exceeds max_short_position {self.max_short_position:.4f}"
            )

    def record_pnl(self, pnl_delta: float) -> None:
        """Record PnL for daily loss tracking."""
        self._daily_pnl += pnl_delta

    def reset_daily(self) -> None:
        """Reset daily PnL counter."""
        self._daily_pnl = 0.0

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl
