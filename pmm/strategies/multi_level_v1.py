from __future__ import annotations

from typing import Callable, List, Tuple

from pmm.config import PMMConfig
from pmm.core.pricing import compute_quotes_pro
from pmm.core.strategy_base import QuoteTarget, StrategyQuoteInput

AnchorFn = Callable[[float, float, float, float, float, float, float], Tuple[float, float]]
QuantizeFn = Callable[[float, float, float, str], Tuple[float, float]]
TargetSizesFn = Callable[[PMMConfig, float, float, float], Tuple[float, float]]


class MultiLevelV1Strategy:
    key = "multi_level_v1"

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
        base_bid, base_ask = self._anchor_quotes(
            target_bid=target_bid,
            target_ask=target_ask,
            best_bid=quote_input.best_bid,
            best_ask=quote_input.best_ask,
            join_epsilon=config.join_epsilon,
            fair_value=quote_input.mid,
            min_edge=config.min_edge,
        )
        base_bid, base_ask = self._quantize_pair(
            base_bid,
            base_ask,
            tick=config.price_tick,
            mode=config.price_tick_mode,
        )

        base_buy_size, base_sell_size = self._target_sizes(
            config=config,
            position=quote_input.position,
            usdc_balance=quote_input.effective_usdc_balance,
            bid_price=base_bid,
        )
        if quote.allow_buy:
            base_buy_size *= max(0.0, quote.bid_size_adj)
        else:
            base_buy_size = 0.0
        if quote.allow_sell:
            base_sell_size *= max(0.0, quote.ask_size_adj)
        else:
            base_sell_size = 0.0

        levels = max(1, int(config.effective_quote_levels()))
        step = max(0.0, float(config.level_spread_step))
        decay = float(config.level_size_decay)
        if decay <= 0:
            decay = 1.0

        quotes: List[QuoteTarget] = []
        for level in range(levels):
            price_offset = step * level
            size_factor = decay ** level

            lvl_target_bid = max(0.0001, target_bid - price_offset)
            lvl_target_ask = min(0.9999, target_ask + price_offset)
            lvl_bid = max(0.0001, base_bid - price_offset)
            lvl_ask = min(0.9999, base_ask + price_offset)

            lvl_bid, lvl_ask = self._quantize_pair(
                lvl_bid,
                lvl_ask,
                tick=config.price_tick,
                mode=config.price_tick_mode,
            )

            buy_size = max(0.0, base_buy_size * size_factor)
            sell_size = max(0.0, base_sell_size * size_factor)

            if buy_size > 0:
                quotes.append(
                    QuoteTarget(
                        token_id=quote_input.token_id,
                        side="BUY",
                        price=lvl_bid,
                        size=buy_size,
                        level=level,
                        target_price=lvl_target_bid,
                    )
                )
            if sell_size > 0:
                quotes.append(
                    QuoteTarget(
                        token_id=quote_input.token_id,
                        side="SELL",
                        price=lvl_ask,
                        size=sell_size,
                        level=level,
                        target_price=lvl_target_ask,
                    )
                )
        return quotes
