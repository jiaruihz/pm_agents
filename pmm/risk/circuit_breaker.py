"""Circuit breaker — detects abnormal price jumps and halts quoting.

Extracted from tick_loop.py inline logic. Provides a clean interface
for the engine to check before entering the quoting path.

STATUS: STUB — detection logic should be expanded.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class CBResult:
    """Result of a circuit breaker check."""
    triggered: bool
    reason: str = ""
    token_id: str = ""
    jump_pct: float = 0.0


class CircuitBreaker:
    """Detects abnormal price jumps and signals the engine to halt.

    A jump is detected when the mid price moves more than `threshold`
    (as a fraction) between consecutive observations.
    """

    def __init__(
        self,
        threshold: float = 0.10,
        halt_on_trigger: bool = True,
    ) -> None:
        self.threshold = threshold
        self.halt_on_trigger = halt_on_trigger
        self._last_mids: Dict[str, float] = {}

    def check(self, current_mids: Dict[str, float]) -> CBResult:
        """Check current mids against previous observation.

        Returns CBResult with triggered=True if any token jumped
        more than threshold.
        """
        for token_id, mid in current_mids.items():
            if mid <= 0:
                continue
            prev = self._last_mids.get(token_id, 0.0)
            if prev <= 0:
                continue
            jump = abs(mid - prev) / prev
            if jump > self.threshold:
                return CBResult(
                    triggered=True,
                    reason=f"price jump {jump:.4f} > {self.threshold:.4f}",
                    token_id=token_id,
                    jump_pct=jump,
                )

        # Update stored mids after check.
        for token_id, mid in current_mids.items():
            if mid > 0:
                self._last_mids[token_id] = mid

        return CBResult(triggered=False)

    def update(self, mids: Dict[str, float]) -> None:
        """Manually update stored mids (e.g., after initialization)."""
        for token_id, mid in mids.items():
            if mid > 0:
                self._last_mids[token_id] = mid

    def reset(self) -> None:
        """Clear all stored mids."""
        self._last_mids.clear()
