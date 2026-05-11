from __future__ import annotations

from typing import Callable, List, Tuple

from src.strategies.pmm.config import PMMConfig
from src.strategies.pmm.core.pricing import compute_quotes_pro
from src.platform.quote_runtime.strategy_base import QuoteTarget, StrategyQuoteInput

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
        tick = max(1e-6, float(config.price_tick))
        spread_ticks = max(1, int(round(quote_input.adaptive_spread / tick)))
        size_decay_power = float(config.strategy_params.get("size_decay_power", 2.0))
        quote = compute_quotes_pro(
            mid=quote_input.mid,
            spread_ticks=spread_ticks,
            tick_size=tick,
            position=quote_input.position,
            open_buy_qty=quote_input.open_buy_qty,
            open_sell_qty=quote_input.open_sell_qty,
            max_position=max(1.0, float(config.max_position)),
            size_decay_power=size_decay_power,
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

        base_buy_size, base_sell_size = self._target_sizes(
            config=config,
            position=quote_input.position,
            usdc_balance=quote_input.effective_usdc_balance,
            bid_price=bid_price,
        )
        buy_size = base_buy_size * max(0.0, quote.bid_size_adj) if quote.allow_buy else 0.0
        sell_size = base_sell_size * max(0.0, quote.ask_size_adj) if quote.allow_sell else 0.0

        quotes: List[QuoteTarget] = []
        if buy_size > 0:
            quotes.append(QuoteTarget(
                token_id=quote_input.token_id,
                side="BUY",
                price=bid_price,
                size=buy_size,
                level=0,
                target_price=target_bid,
            ))
        if sell_size > 0:
            quotes.append(QuoteTarget(
                token_id=quote_input.token_id,
                side="SELL",
                price=ask_price,
                size=sell_size,
                level=0,
                target_price=target_ask,
            ))
        return quotes
