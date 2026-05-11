"""Weather Edge strategy adapter for the unified engine."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from src.platform.engine.base import IStrategy, StrategyContext
from src.platform.engine.events import AlertEvent, MarketTickEvent, OrderUpdateEvent
from src.platform.engine.models import CancelCommand, OrderCommand, OrderSide, OrderStatus, OrderType
from src.strategies.weather_edge_v1.core import (
    WeatherDecisionInput,
    WeatherDecisionParams,
    WeatherRuntimeConfig,
    WeatherTokenState,
    clamp,
    forecast_distance_from_bucket,
    make_decision,
    sync_state,
    to_float,
    token_set,
)
from src.strategies.weather_edge_v1.tools.provider import AsyncOpenMeteoClient

logger = logging.getLogger(__name__)


class UnifiedWeatherEdgeStrategy(IStrategy):
    """
    Weather Edge adapter for the asyncio unified engine.

    The trading rules live in `src.strategies.weather_edge_v1.core`; this class
    only adapts unified-engine ticks and commands.
    """

    key = "weather_edge_v1"

    def __init__(self, time_fn=None) -> None:
        self._time_fn = time_fn or time.time
        self._weather_provider = AsyncOpenMeteoClient(update_interval_sec=300)
        self._weather_target_meta: Dict[str, Dict[str, Any]] = {}

    async def init(self, context: StrategyContext) -> None:
        logger.info("Initialized %s for instance %s", self.key, context.instance_id)
        context.state["token_states"] = {}
        self._load_weather_targets()
        self._sync_dynamic_weather_targets(context.run_params)
        await self._start_provider_if_needed()

    async def _start_provider_if_needed(self) -> None:
        try:
            await self._weather_provider.start_polling()
        except RuntimeError:
            logger.debug("Weather provider could not start because no running loop is available.")

    def _load_weather_targets(self) -> None:
        config_path = Path("src/strategies/weather_edge_v1/config/weather_targets.json")
        if not config_path.exists():
            logger.warning("Weather targets config not found at %s", config_path)
            return
        try:
            data = json.loads(config_path.read_text("utf-8"))
            if isinstance(data, list):
                for item in data:
                    self._apply_weather_target_item(item)
        except Exception as exc:
            logger.error("Failed to load weather targets config: %s", exc)

    def _apply_weather_target_item(self, item: Dict[str, Any]) -> None:
        lat = item.get("lat")
        lon = item.get("lon")
        token_id = str(item.get("token_id") or item.get("no_token") or "").strip()
        if lat is None or lon is None or not token_id:
            return
        target_date = str(item.get("target_date") or "").strip()
        self._weather_provider.register_target(token_id, float(lat), float(lon), target_date=target_date or None)
        self._weather_target_meta[token_id] = {
            "unit": str(item.get("unit") or "C").strip().upper(),
            "bucket_min": item.get("bucket_min"),
            "bucket_max": item.get("bucket_max"),
            "bucket_value": item.get("bucket_value"),
            "target_date": target_date,
            "city": str(item.get("city") or "").strip(),
        }

    def _sync_dynamic_weather_targets(self, params: Dict[str, Any]) -> None:
        raw = params.get("weather_token_forecast_map")
        if not isinstance(raw, dict):
            return
        for token_id, item in raw.items():
            if not isinstance(item, dict):
                continue
            payload = dict(item)
            payload.setdefault("token_id", str(token_id))
            self._apply_weather_target_item(payload)

    async def on_market_tick(
        self, context: StrategyContext, event: MarketTickEvent
    ) -> List[Union[OrderCommand, CancelCommand]]:
        params = context.run_params
        self._sync_dynamic_weather_targets(params)
        token_id = event.token_id

        allowed_tokens = token_set(params.get("weather_no_token_ids"))
        if allowed_tokens and token_id not in allowed_tokens:
            return []

        mid = event.mid_price
        if mid is None or mid <= 0:
            return []

        position = max(0.0, context.positions.get(token_id, 0.0))
        now = self._time_fn()
        state_dict = context.state.setdefault("token_states", {})
        token_state = state_dict.setdefault(token_id, WeatherTokenState())
        sync_state(token_state, position=position, mid=mid, now=now)

        forecast_distance = self._forecast_distance_from_bucket(token_id)
        event_context = event.context if isinstance(event.context, dict) else {}
        price_tick = max(0.0001, to_float(params.get("price_tick"), 0.001))
        decision = make_decision(
            quote=WeatherDecisionInput(
                token_id=token_id,
                mid=mid,
                adaptive_spread=to_float(event_context.get("adaptive_spread"), to_float(params.get("adaptive_spread"), 0.04)),
                best_bid=to_float(event.best_bid, 0.0),
                best_ask=to_float(event.best_ask, 0.0),
                position=position,
                effective_usdc_balance=max(0.0, to_float(params.get("usdc_balance"), 0.0)),
                open_buy_qty=to_float(event_context.get("open_buy_qty"), 0.0),
                open_sell_qty=to_float(event_context.get("open_sell_qty"), 0.0),
            ),
            state=token_state,
            params=WeatherDecisionParams.from_mapping(params),
            runtime=WeatherRuntimeConfig(
                max_position=max(0.0, to_float(params.get("max_position"), 0.0)),
                min_size=max(0.0, to_float(params.get("min_size"), 0.01)),
                price_tick=price_tick,
                join_epsilon=max(0.0, to_float(params.get("join_epsilon"), 0.001)),
            ),
            now=now,
            forecast_distance=forecast_distance,
            quantize_price=lambda price: self._quantize_price(price, price_tick=price_tick),
        )
        if decision is None:
            return []

        logger.info("[%s] %s conditions met for %s. Issuing %s command.", self.key, decision.reason, token_id, decision.side)
        return [
            OrderCommand(
                instance_id=context.instance_id,
                strategy_key=self.key,
                token_id=token_id,
                side=OrderSide(decision.side),
                size=decision.size,
                price=decision.price,
                order_type=OrderType.LIMIT,
            )
        ]

    def _forecast_distance_from_bucket(self, token_id: str) -> Optional[float]:
        forecast_c = self._weather_provider.get_daily_max_temperature(token_id)
        meta = self._weather_target_meta.get(token_id) or {}
        return forecast_distance_from_bucket(forecast_c, meta)

    def _quantize_price(self, price: float, *, price_tick: float) -> float:
        tick = max(0.0001, price_tick)
        quantized = round(round(price / tick) * tick, 3)
        return clamp(quantized, 0.0001, 0.9999)

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
