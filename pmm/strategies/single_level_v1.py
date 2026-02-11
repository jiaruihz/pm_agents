from __future__ import annotations

from typing import Callable, List, Tuple

from pmm.config import PMMConfig
from pmm.pricing import compute_quotes
from pmm.strategy_base import QuoteTarget, StrategyQuoteInput

AnchorFn = Callable[[float, float, float, float, float, float, float], Tuple[float, float]]
QuantizeFn = Callable[[float, float, float, str], Tuple[float, float]]
TargetSizesFn = Callable[[PMMConfig, float, float, float], Tuple[float, float]]


class SingleLevelV1Strategy:
    key = "single_level_v1"

    def __init__(
        self,
        anchor_quotes_fn: AnchorFn,
        quantize_pair_fn: QuantizeFn,
        target_sizes_fn: TargetSizesFn,
    ) -> None:
        self._anchor_quotes = anchor_quotes_fn
        self._quantize_pair = quantize_pair_fn
        self._target_sizes = target_sizes_fn

    def generate_quotes(
        self,
        quote_input: StrategyQuoteInput,
        config: PMMConfig,
    ) -> List[QuoteTarget]:
        quote = compute_quotes(
            mid=quote_input.mid,
            spread=quote_input.adaptive_spread,
            inventory=quote_input.inventory_signal,
            skew_factor=config.skew_factor,
        )
        target_bid = quote.bid
        target_ask = quote.ask
        bid_price, ask_price = self._anchor_quotes(
            target_bid=target_bid,
            target_ask=target_ask,
            best_bid=quote_input.best_bid,
            best_ask=quote_input.best_ask,
            join_epsilon=config.join_epsilon,
            fair_value=quote_input.mid,
            min_edge=config.min_edge,
        )
        bid_price, ask_price = self._quantize_pair(
            bid_price,
            ask_price,
            tick=config.price_tick,
            mode=config.price_tick_mode,
        )

        buy_size, sell_size = self._target_sizes(
            config=config,
            position=quote_input.position,
            usdc_balance=quote_input.effective_usdc_balance,
            bid_price=bid_price,
        )
        return [
            QuoteTarget(
                token_id=quote_input.token_id,
                side="BUY",
                price=bid_price,
                size=buy_size,
                level=0,
                target_price=target_bid,
            ),
            QuoteTarget(
                token_id=quote_input.token_id,
                side="SELL",
                price=ask_price,
                size=sell_size,
                level=0,
                target_price=target_ask,
            ),
        ]

