"""Circuit breaker for abnormal price jumps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass
class CBResult:
    """Result of a circuit breaker check."""

    triggered: bool
    reason: str = ""
    token_id: str = ""
    jump_pct: float = 0.0


class CircuitBreaker:
    """Detects abnormal price jumps and signals the engine to halt quoting."""

    def __init__(
        self,
        threshold: float = 0.10,
        halt_on_trigger: bool = True,
    ) -> None:
        self.threshold = threshold
        self.halt_on_trigger = halt_on_trigger
        self._last_mids: Dict[str, float] = {}

    def check(self, current_mids: Dict[str, float]) -> CBResult:
        """Check current mids against previous observation."""
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

        for token_id, mid in current_mids.items():
            if mid > 0:
                self._last_mids[token_id] = mid

        return CBResult(triggered=False)

    def update(self, mids: Dict[str, float]) -> None:
        """Manually update stored mids."""
        for token_id, mid in mids.items():
            if mid > 0:
                self._last_mids[token_id] = mid

    def reset(self) -> None:
        """Clear all stored mids."""
        self._last_mids.clear()
