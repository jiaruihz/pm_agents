from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.strategies.pmm.config import PMMConfig
from src.platform.quote_runtime.strategy_base import QuoteTarget, StrategyQuoteInput
from src.strategies.weather_edge_v1.core import (
    WeatherDecisionInput,
    WeatherDecisionParams,
    WeatherRuntimeConfig,
    WeatherTokenState,
    clamp,
    forecast_distance_from_bucket,
    make_decision,
    sync_state,
    token_set,
)
from src.strategies.weather_edge_v1.tools.provider import AsyncOpenMeteoClient

logger = logging.getLogger("weather_edge_v1.pmm_adapter")

QuantizeFn = Callable[[float, float, float, str], Tuple[float, float]]


class WeatherEdgeV1Strategy:
    """
    Weather edge strategy for probability-vs-market mispricing.

    Core idea:
    - Entry: act only when the weather model implies a clear edge vs market price.
    - Execution: keep the legacy NO-side carry controls as one supported mode.
    - Risk: immediate stop-loss, allowlisted tokens, and timed exit before settlement.
    """

    key = "weather_edge_v1"

    def __init__(
        self,
        quantize_pair_fn: QuantizeFn,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        self._quantize_pair = quantize_pair_fn
        self._time_fn = time_fn or time.time
        self._state: Dict[str, WeatherTokenState] = {}
        self._weather_target_meta: Dict[str, Dict[str, Any]] = {}

        self._weather_provider = AsyncOpenMeteoClient(update_interval_sec=300)
        self._load_weather_targets()

        # We start the provider polling immediately in background
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._weather_provider.start_polling())
        except RuntimeError:
            pass  # No running loop yet, it's fine, we aren't in tick engine yet.

    def _load_weather_targets(self) -> None:
        """Load weather targets mapping from local config."""
        config_path = Path("src/strategies/weather_edge_v1/config/weather_targets.json")
        if not config_path.exists():
            logger.warning(f"Weather targets config not found at {config_path}")
            return

        try:
            data = json.loads(config_path.read_text("utf-8"))
            if isinstance(data, list):
                for item in data:
                    self._apply_weather_target_item(item)
        except Exception as e:
            logger.error(f"Failed to load weather targets config: {e}")

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
        logger.info("Registered weather target for token %s at (%s, %s)", token_id, lat, lon)

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

    def generate_quotes(
        self,
        quote_input: StrategyQuoteInput,
        config: PMMConfig,
    ) -> List[QuoteTarget]:
        params = config.strategy_params or {}
        self._sync_dynamic_weather_targets(params)
        token_id = quote_input.token_id
        allow_tokens = token_set(params.get("weather_no_token_ids"))
        if allow_tokens and token_id not in allow_tokens:
            return []

        mid = max(0.0, float(quote_input.mid))
        if mid <= 0:
            return []

        now = self._time_fn()
        position = max(0.0, float(quote_input.position))
        state = self._sync_state(token_id=token_id, position=position, mid=mid, now=now)

        decision = make_decision(
            quote=WeatherDecisionInput(
                token_id=token_id,
                mid=quote_input.mid,
                adaptive_spread=quote_input.adaptive_spread,
                best_bid=quote_input.best_bid,
                best_ask=quote_input.best_ask,
                position=quote_input.position,
                effective_usdc_balance=quote_input.effective_usdc_balance,
                open_buy_qty=quote_input.open_buy_qty,
                open_sell_qty=quote_input.open_sell_qty,
            ),
            state=state,
            params=WeatherDecisionParams.from_mapping(params),
            runtime=WeatherRuntimeConfig(
                max_position=float(config.max_position),
                min_size=float(config.min_size),
                price_tick=float(config.price_tick),
                join_epsilon=float(config.join_epsilon),
            ),
            now=now,
            forecast_distance=self._forecast_distance_from_bucket(token_id),
            quantize_price=lambda price: self._quantize_price(price, config),
        )
        if decision is None:
            return []
        return [
            QuoteTarget(
                token_id=token_id,
                side=decision.side,
                price=decision.price,
                size=decision.size,
                level=0,
                target_price=decision.price,
            )
        ]

    def _sync_state(self, token_id: str, position: float, mid: float, now: float) -> WeatherTokenState:
        state = self._state.setdefault(token_id, WeatherTokenState())
        return sync_state(state, position=position, mid=mid, now=now)

    def _forecast_distance_from_bucket(self, token_id: str) -> Optional[float]:
        forecast_c = self._weather_provider.get_daily_max_temperature(token_id)
        meta = self._weather_target_meta.get(token_id) or {}
        return forecast_distance_from_bucket(forecast_c, meta)

    def _quantize_price(self, price: float, config: PMMConfig) -> float:
        bid, _ = self._quantize_pair(
            price,
            price,
            tick=config.price_tick,
            mode=config.price_tick_mode,
        )
        return clamp(float(bid), 0.0001, 0.9999)
