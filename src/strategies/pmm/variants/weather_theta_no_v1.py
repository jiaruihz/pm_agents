from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from src.strategies.pmm.config import PMMConfig
from src.strategies.pmm.core.strategy_base import QuoteTarget, StrategyQuoteInput
from src.strategies.weather_theta_no_v1.tools.provider import AsyncOpenMeteoClient

logger = logging.getLogger("pmm.strategy.weather_theta")

QuantizeFn = Callable[[float, float, float, str], Tuple[float, float]]


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


def _token_set(raw: Any) -> Set[str]:
    if isinstance(raw, list):
        return {str(x).strip() for x in raw if str(x).strip()}
    if isinstance(raw, str):
        return {x.strip() for x in raw.split(",") if x.strip()}
    return set()


def _token_ts_map(raw: Any) -> Dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, float] = {}
    for k, v in raw.items():
        key = str(k).strip()
        if not key:
            continue
        ts = _to_float(v, 0.0)
        if ts > 0:
            out[key] = ts
    return out


@dataclass
class _TokenState:
    last_position: float = 0.0
    entry_mid: float = 0.0
    entry_ts: float = 0.0
    last_exit_ts: float = 0.0


class WeatherThetaNoV1Strategy:
    """
    Weather carry strategy for NO-side accumulation and timed exit.

    Core idea:
    - Entry: buy NO when probability still carries uncertainty premium.
    - Exit: flatten before settlement / after holding window / at take-profit.
    - Risk: immediate stop-loss and optional "safety-range break" flattening.
    """

    key = "weather_theta_no_v1"

    def __init__(
        self,
        quantize_pair_fn: QuantizeFn,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        self._quantize_pair = quantize_pair_fn
        self._time_fn = time_fn or time.time
        self._state: Dict[str, _TokenState] = {}
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
        config_path = Path("src/strategies/weather_theta_no_v1/config/weather_targets.json")
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
        allow_tokens = _token_set(params.get("weather_no_token_ids"))
        if allow_tokens and token_id not in allow_tokens:
            return []

        mid = max(0.0, float(quote_input.mid))
        if mid <= 0:
            return []

        now = self._time_fn()
        position = max(0.0, float(quote_input.position))
        state = self._sync_state(token_id=token_id, position=position, mid=mid, now=now)

        entry_min = _clamp(_to_float(params.get("weather_entry_min_price"), 0.78), 0.0001, 0.9999)
        entry_max = _clamp(_to_float(params.get("weather_entry_max_price"), 0.97), entry_min, 0.9999)
        max_entry_spread = max(0.0, _to_float(params.get("weather_entry_max_spread"), 0.06))
        position_pct = _clamp(_to_float(params.get("weather_position_pct"), 0.05), 0.0, 1.0)
        min_order_notional = max(0.0, _to_float(params.get("weather_min_order_notional"), 1.0))

        take_profit_abs = max(0.0, _to_float(params.get("weather_take_profit_abs"), 0.02))
        stop_loss_abs = max(0.0, _to_float(params.get("weather_stop_loss_abs"), 0.03))
        min_hold_sec = max(0.0, _to_float(params.get("weather_min_hold_hours"), 0.0)) * 3600.0
        max_hold_sec = max(0.0, _to_float(params.get("weather_max_hold_hours"), 48.0)) * 3600.0
        exit_before_sec = max(0.0, _to_float(params.get("weather_exit_before_hours"), 6.0)) * 3600.0
        cooldown_sec = max(0.0, _to_float(params.get("weather_reentry_cooldown_hours"), 12.0)) * 3600.0
        allow_reentry = _to_bool(params.get("weather_allow_reentry"), True)
        force_flat_on_range_break = _to_bool(params.get("weather_force_flat_on_range_break"), True)
        token_end_ts_map = _token_ts_map(params.get("weather_token_end_ts"))
        forecast_entry_edge = max(0.0, _to_float(params.get("weather_min_forecast_edge"), 2.0))
        forecast_exit_edge = max(0.0, _to_float(params.get("weather_exit_forecast_edge"), 1.0))

        spread_now = 0.0
        if quote_input.best_bid > 0 and quote_input.best_ask > 0:
            spread_now = max(0.0, quote_input.best_ask - quote_input.best_bid)

        if position <= 1e-9:
            if not allow_reentry and state.last_exit_ts > 0:
                return []
            if state.last_exit_ts > 0 and cooldown_sec > 0 and (now - state.last_exit_ts) < cooldown_sec:
                return []
            if spread_now > 0 and spread_now > max_entry_spread:
                return []
            if mid < entry_min or mid > entry_max:
                return []

            forecast_distance = self._forecast_distance_from_bucket(token_id)
            if forecast_distance is None or forecast_distance < forecast_entry_edge:
                return []

            entry_price = self._entry_price(quote_input, config, entry_min=entry_min, entry_max=entry_max)
            buy_size = self._entry_size(
                quote_input=quote_input,
                config=config,
                entry_price=entry_price,
                position_pct=position_pct,
            )
            if buy_size <= 0:
                return []
            if (buy_size * entry_price) < min_order_notional or buy_size < max(0.01, float(config.min_size)):
                return []
            return [
                QuoteTarget(
                    token_id=token_id,
                    side="BUY",
                    price=entry_price,
                    size=buy_size,
                    level=0,
                    target_price=entry_price,
                )
            ]

        entry_mid = state.entry_mid if state.entry_mid > 0 else mid
        hold_sec = (now - state.entry_ts) if state.entry_ts > 0 else 0.0
        token_end_ts = token_end_ts_map.get(token_id, 0.0)

        stop_loss_hit = stop_loss_abs > 0 and mid <= (entry_mid - stop_loss_abs)
        take_profit_hit = (
            take_profit_abs > 0
            and mid >= (entry_mid + take_profit_abs)
            and hold_sec >= min_hold_sec
        )
        max_hold_hit = max_hold_sec > 0 and hold_sec >= max_hold_sec
        pre_settlement_exit = token_end_ts > 0 and now >= max(0.0, token_end_ts - exit_before_sec)
        range_break_hit = force_flat_on_range_break and mid < entry_min
        forecast_distance = self._forecast_distance_from_bucket(token_id)
        weather_hit = forecast_distance is not None and forecast_distance <= forecast_exit_edge

        if not (stop_loss_hit or take_profit_hit or max_hold_hit or pre_settlement_exit or range_break_hit or weather_hit):
            return []

        exit_size = max(0.0, position - max(0.0, float(quote_input.open_sell_qty)))
        if exit_size <= 0:
            return []

        aggressive = stop_loss_hit or range_break_hit or weather_hit
        exit_price = self._exit_price(quote_input, config, aggressive=aggressive)
        return [
            QuoteTarget(
                token_id=token_id,
                side="SELL",
                price=exit_price,
                size=exit_size,
                level=0,
                target_price=exit_price,
            )
        ]

    def _sync_state(self, token_id: str, position: float, mid: float, now: float) -> _TokenState:
        state = self._state.setdefault(token_id, _TokenState())
        prev = max(0.0, state.last_position)
        curr = max(0.0, position)

        if curr <= 1e-9:
            if prev > 1e-9:
                state.last_exit_ts = now
            state.last_position = 0.0
            state.entry_mid = 0.0
            state.entry_ts = 0.0
            return state

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
        return state

    def _entry_price(
        self,
        quote_input: StrategyQuoteInput,
        config: PMMConfig,
        entry_min: float,
        entry_max: float,
    ) -> float:
        tick = max(1e-6, float(config.price_tick))
        if quote_input.best_bid > 0:
            base = min(quote_input.mid, quote_input.best_bid + max(0.0, float(config.join_epsilon)))
        else:
            base = quote_input.mid - max(tick, quote_input.adaptive_spread * 0.25)
        raw_price = _clamp(base, entry_min, entry_max)
        return self._quantize_price(raw_price, config)

    def _forecast_distance_from_bucket(self, token_id: str) -> Optional[float]:
        forecast_c = self._weather_provider.get_daily_max_temperature(token_id)
        meta = self._weather_target_meta.get(token_id) or {}
        if forecast_c is None or not meta:
            return None
        unit = str(meta.get("unit") or "C").strip().upper()
        forecast = self._convert_celsius(forecast_c, unit)
        bucket_min = self._bucket_bound(meta.get("bucket_min"), meta.get("bucket_value"))
        bucket_max = self._bucket_bound(meta.get("bucket_max"), meta.get("bucket_value"))
        if bucket_min is None and bucket_max is None:
            return None
        lo = bucket_min if bucket_min is not None else bucket_max
        hi = bucket_max if bucket_max is not None else bucket_min
        if lo is None or hi is None:
            return None
        if lo > hi:
            lo, hi = hi, lo
        if lo <= forecast <= hi:
            return 0.0
        return min(abs(forecast - lo), abs(forecast - hi))

    def _bucket_bound(self, raw: Any, fallback: Any) -> Optional[float]:
        if raw is not None:
            return _to_float(raw, 0.0)
        if fallback is not None:
            return _to_float(fallback, 0.0)
        return None

    def _convert_celsius(self, value_c: float, unit: str) -> float:
        if unit == "F":
            return (value_c * 9.0 / 5.0) + 32.0
        return value_c

    def _entry_size(
        self,
        quote_input: StrategyQuoteInput,
        config: PMMConfig,
        entry_price: float,
        position_pct: float,
    ) -> float:
        if entry_price <= 0 or position_pct <= 0:
            return 0.0
        usdc_balance = max(0.0, float(quote_input.effective_usdc_balance))
        budget = usdc_balance * position_pct
        if budget <= 0:
            return 0.0
        size = budget / max(entry_price, 1e-6)
        size = min(size, usdc_balance / max(entry_price, 1e-6))

        max_pos = max(0.0, float(config.max_position))
        if max_pos > 0:
            room = max(0.0, max_pos - (max(0.0, float(quote_input.position)) + max(0.0, float(quote_input.open_buy_qty))))
            size = min(size, room)
        return max(0.0, size)

    def _exit_price(self, quote_input: StrategyQuoteInput, config: PMMConfig, aggressive: bool) -> float:
        tick = max(1e-6, float(config.price_tick))
        best_bid = max(0.0, float(quote_input.best_bid))
        best_ask = max(0.0, float(quote_input.best_ask))
        mid = max(0.0, float(quote_input.mid))

        if aggressive:
            if best_bid > 0:
                raw = max(0.0001, best_bid - tick)
            else:
                raw = max(0.0001, mid - tick)
        else:
            if best_ask > 0:
                raw = max(best_bid + tick, best_ask - tick)
            elif best_bid > 0:
                raw = best_bid + tick
            else:
                raw = mid
        return self._quantize_price(_clamp(raw, 0.0001, 0.9999), config)

    def _quantize_price(self, price: float, config: PMMConfig) -> float:
        bid, _ = self._quantize_pair(
            price,
            price,
            tick=config.price_tick,
            mode=config.price_tick_mode,
        )
        return _clamp(float(bid), 0.0001, 0.9999)
