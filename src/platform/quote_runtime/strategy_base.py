from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Protocol


@dataclass
class StrategyQuoteInput:
    token_id: str
    mid: float
    adaptive_spread: float
    inventory_signal: float
    best_bid: float
    best_ask: float
    position: float
    effective_usdc_balance: float
    open_buy_qty: float = 0.0
    open_sell_qty: float = 0.0


@dataclass
class QuoteTarget:
    token_id: str
    side: str
    price: float
    size: float
    level: int = 0
    target_price: float = 0.0


class QuoteStrategy(Protocol):
    key: str

    def generate_quotes(
        self,
        quote_input: StrategyQuoteInput,
        config: Any,
    ) -> List[QuoteTarget]:
        ...


# Compatibility name while the PMM runner is still being migrated.
MarketMakingStrategy = QuoteStrategy
