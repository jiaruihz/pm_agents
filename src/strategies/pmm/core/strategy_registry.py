from __future__ import annotations

from typing import Dict, Optional

from src.strategies.pmm.core.strategy_base import MarketMakingStrategy


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: Dict[str, MarketMakingStrategy] = {}

    def register(self, strategy: MarketMakingStrategy) -> None:
        self._strategies[strategy.key] = strategy

    def get(self, key: str) -> Optional[MarketMakingStrategy]:
        return self._strategies.get(key)

    def available_keys(self) -> list[str]:
        return sorted(self._strategies.keys())

