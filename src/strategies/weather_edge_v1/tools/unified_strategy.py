"""Weather carry strategy ported to the unified engine."""

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Union

from src.platform.engine.base import IStrategy, StrategyContext
from src.platform.engine.events import AlertEvent, MarketTickEvent, OrderUpdateEvent
from src.platform.engine.models import CancelCommand, OrderCommand, OrderSide, OrderStatus, OrderType

logger = logging.getLogger(__name__)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class TokenState:
    last_position: float = 0.0
    entry_mid: float = 0.0
    entry_ts: float = 0.0
    last_exit_ts: float = 0.0


class UnifiedWeatherEdgeStrategy(IStrategy):
    """
    Weather carry strategy for NO-side accumulation and timed exit.
    Adapted for the asyncio Unified Engine.
    """

    key = "weather_edge_v1"

    async def init(self, context: StrategyContext) -> None:
        logger.info("Initialized %s for instance %s", self.key, context.instance_id)
        context.state["token_states"] = {}

    async def on_market_tick(
        self, context: StrategyContext, event: MarketTickEvent
    ) -> List[Union[OrderCommand, CancelCommand]]:
        params = context.run_params
        token_id = event.token_id

        allowed_tokens = params.get("weather_no_token_ids", [])
        if allowed_tokens and token_id not in allowed_tokens:
            return []

        mid = event.mid_price
        if mid is None or mid <= 0:
            return []

        position = max(0.0, context.positions.get(token_id, 0.0))
        now = time.time()

        state_dict = context.state["token_states"]
        if token_id not in state_dict:
            state_dict[token_id] = TokenState()
        token_state: TokenState = state_dict[token_id]

        self._sync_state(token_state, position, mid, now)

        entry_min = _clamp(_to_float(params.get("weather_entry_min_price"), 0.78), 0.0001, 0.9999)
        entry_max = _clamp(_to_float(params.get("weather_entry_max_price"), 0.96), entry_min, 0.9999)
        max_entry_spread = max(0.0, _to_float(params.get("weather_entry_max_spread"), 0.06))
        position_pct = _clamp(_to_float(params.get("weather_position_pct"), 0.05), 0.0, 1.0)
        min_order_notional = max(0.0, _to_float(params.get("weather_min_order_notional"), 1.0))

        take_profit_abs = max(0.0, _to_float(params.get("weather_take_profit_abs"), 0.01))
        stop_loss_abs = max(0.0, _to_float(params.get("weather_stop_loss_abs"), 0.03))
        min_hold_sec = max(0.0, _to_float(params.get("weather_min_hold_hours"), 0.0)) * 3600.0
        max_hold_sec = max(0.0, _to_float(params.get("weather_max_hold_hours"), 48.0)) * 3600.0
        exit_before_sec = max(0.0, _to_float(params.get("weather_exit_before_hours"), 6.0)) * 3600.0
        cooldown_sec = max(0.0, _to_float(params.get("weather_reentry_cooldown_hours"), 12.0)) * 3600.0
        allow_reentry = _to_bool(params.get("weather_allow_reentry"), True)
        force_flat_on_range_break = _to_bool(params.get("weather_force_flat_on_range_break"), True)

        token_end_ts_map = params.get("weather_token_end_ts", {})
        token_end_ts = _to_float(token_end_ts_map.get(token_id, 0.0))

        spread_now = 0.0
        if event.best_bid and event.best_ask:
            spread_now = max(0.0, event.best_ask - event.best_bid)

        if position <= 1e-9:
            if not allow_reentry and token_state.last_exit_ts > 0:
                return []
            if token_state.last_exit_ts > 0 and cooldown_sec > 0 and (now - token_state.last_exit_ts) < cooldown_sec:
                return []
            if spread_now > 0 and spread_now > max_entry_spread:
                return []
            if mid < entry_min or mid > entry_max:
                return []

            usdc_balance = max(0.0, _to_float(context.run_params.get("usdc_balance", 0.0)))
            entry_price = min(mid, (event.best_bid or mid) + 0.001)
            entry_price = _clamp(entry_price, entry_min, entry_max)

            budget = usdc_balance * position_pct
            if budget <= 0 or entry_price <= 0:
                return []

            buy_size = budget / max(entry_price, 1e-6)

            max_pos = max(0.0, _to_float(params.get("max_position", 0.0)))
            if max_pos > 0:
                buy_size = min(buy_size, max_pos)

            if (buy_size * entry_price) < min_order_notional:
                return []

            logger.info("[%s] Entry conditions met for %s. Issuing BUY command.", self.key, token_id)
            return [
                OrderCommand(
                    instance_id=context.instance_id,
                    strategy_key=self.key,
                    token_id=token_id,
                    side=OrderSide.BUY,
                    size=buy_size,
                    price=round(entry_price, 3),
                    order_type=OrderType.LIMIT,
                )
            ]

        entry_mid = token_state.entry_mid if token_state.entry_mid > 0 else mid
        hold_sec = (now - token_state.entry_ts) if token_state.entry_ts > 0 else 0.0

        stop_loss_hit = stop_loss_abs > 0 and mid <= (entry_mid - stop_loss_abs)
        take_profit_hit = (
            take_profit_abs > 0 and mid >= (entry_mid + take_profit_abs) and hold_sec >= min_hold_sec
        )
        max_hold_hit = max_hold_sec > 0 and hold_sec >= max_hold_sec
        pre_settlement_exit = token_end_ts > 0 and now >= max(0.0, token_end_ts - exit_before_sec)
        range_break_hit = force_flat_on_range_break and mid < entry_min

        if stop_loss_hit or take_profit_hit or max_hold_hit or pre_settlement_exit or range_break_hit:
            aggressive = stop_loss_hit or range_break_hit
            exit_price = (event.best_bid or mid) - 0.001 if aggressive else mid

            logger.info("[%s] Exit conditions met for %s. Issuing SELL command.", self.key, token_id)
            return [
                OrderCommand(
                    instance_id=context.instance_id,
                    strategy_key=self.key,
                    token_id=token_id,
                    side=OrderSide.SELL,
                    size=position,
                    price=round(max(0.001, exit_price), 3),
                    order_type=OrderType.LIMIT,
                )
            ]

        return []

    def _sync_state(self, state: TokenState, position: float, mid: float, now: float) -> None:
        prev = max(0.0, state.last_position)
        curr = max(0.0, position)

        if curr <= 1e-9:
            if prev > 1e-9:
                state.last_exit_ts = now
            state.last_position = 0.0
            state.entry_mid = 0.0
            state.entry_ts = 0.0
            return

        if prev <= 1e-9:
            state.entry_mid = max(0.0, mid)
            state.entry_ts = now
        elif curr > prev + 1e-9:
            added = curr - prev
            if state.entry_mid <= 0:
                state.entry_mid = max(0.0, mid)
            else:
                state.entry_mid = ((state.entry_mid * prev) + (mid * added)) / max(curr, 1e-9)
        state.last_position = curr

    async def on_order_update(
        self, context: StrategyContext, event: OrderUpdateEvent
    ) -> List[Union[OrderCommand, CancelCommand]]:
        if event.status == OrderStatus.FILLED:
            logger.info("[%s] Order %s filled for %s", self.key, event.order_id, event.token_id)
        return []

    async def on_alert(
        self, context: StrategyContext, event: AlertEvent
    ) -> List[Union[OrderCommand, CancelCommand]]:
        logger.warning("[%s] Alert received: %s", self.key, event.message)
        return []
