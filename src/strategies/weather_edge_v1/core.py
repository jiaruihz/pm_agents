from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Set


DEFAULT_PARAMS: Dict[str, Any] = {
    "weather_entry_min_price": 0.78,
    "weather_entry_max_price": 0.97,
    "weather_entry_max_spread": 0.06,
    "weather_position_pct": 0.05,
    "weather_min_order_notional": 1.0,
    "weather_take_profit_abs": 0.02,
    "weather_stop_loss_abs": 0.03,
    "weather_min_hold_hours": 0.0,
    "weather_max_hold_hours": 48.0,
    "weather_exit_before_hours": 6.0,
    "weather_reentry_cooldown_hours": 12.0,
    "weather_allow_reentry": True,
    "weather_force_flat_on_range_break": True,
    "weather_min_forecast_edge": 2.0,
    "weather_exit_forecast_edge": 1.0,
}


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def token_set(raw: Any) -> Set[str]:
    if isinstance(raw, list):
        return {str(x).strip() for x in raw if str(x).strip()}
    if isinstance(raw, str):
        return {x.strip() for x in raw.split(",") if x.strip()}
    return set()


def token_ts_map(raw: Any) -> Dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, float] = {}
    for k, v in raw.items():
        key = str(k).strip()
        if not key:
            continue
        ts = to_float(v, 0.0)
        if ts > 0:
            out[key] = ts
    return out


@dataclass
class WeatherTokenState:
    last_position: float = 0.0
    entry_mid: float = 0.0
    entry_ts: float = 0.0
    last_exit_ts: float = 0.0


@dataclass(frozen=True)
class WeatherDecisionParams:
    entry_min: float
    entry_max: float
    max_entry_spread: float
    position_pct: float
    min_order_notional: float
    take_profit_abs: float
    stop_loss_abs: float
    min_hold_sec: float
    max_hold_sec: float
    exit_before_sec: float
    cooldown_sec: float
    allow_reentry: bool
    force_flat_on_range_break: bool
    token_end_ts_map: Dict[str, float]
    forecast_entry_edge: float
    forecast_exit_edge: float

    @classmethod
    def from_mapping(cls, params: Dict[str, Any]) -> "WeatherDecisionParams":
        entry_min = clamp(to_float(params.get("weather_entry_min_price"), DEFAULT_PARAMS["weather_entry_min_price"]), 0.0001, 0.9999)
        entry_max = clamp(to_float(params.get("weather_entry_max_price"), DEFAULT_PARAMS["weather_entry_max_price"]), entry_min, 0.9999)
        return cls(
            entry_min=entry_min,
            entry_max=entry_max,
            max_entry_spread=max(0.0, to_float(params.get("weather_entry_max_spread"), DEFAULT_PARAMS["weather_entry_max_spread"])),
            position_pct=clamp(to_float(params.get("weather_position_pct"), DEFAULT_PARAMS["weather_position_pct"]), 0.0, 1.0),
            min_order_notional=max(0.0, to_float(params.get("weather_min_order_notional"), DEFAULT_PARAMS["weather_min_order_notional"])),
            take_profit_abs=max(0.0, to_float(params.get("weather_take_profit_abs"), DEFAULT_PARAMS["weather_take_profit_abs"])),
            stop_loss_abs=max(0.0, to_float(params.get("weather_stop_loss_abs"), DEFAULT_PARAMS["weather_stop_loss_abs"])),
            min_hold_sec=max(0.0, to_float(params.get("weather_min_hold_hours"), DEFAULT_PARAMS["weather_min_hold_hours"])) * 3600.0,
            max_hold_sec=max(0.0, to_float(params.get("weather_max_hold_hours"), DEFAULT_PARAMS["weather_max_hold_hours"])) * 3600.0,
            exit_before_sec=max(0.0, to_float(params.get("weather_exit_before_hours"), DEFAULT_PARAMS["weather_exit_before_hours"])) * 3600.0,
            cooldown_sec=max(0.0, to_float(params.get("weather_reentry_cooldown_hours"), DEFAULT_PARAMS["weather_reentry_cooldown_hours"])) * 3600.0,
            allow_reentry=to_bool(params.get("weather_allow_reentry"), DEFAULT_PARAMS["weather_allow_reentry"]),
            force_flat_on_range_break=to_bool(
                params.get("weather_force_flat_on_range_break"),
                DEFAULT_PARAMS["weather_force_flat_on_range_break"],
            ),
            token_end_ts_map=token_ts_map(params.get("weather_token_end_ts")),
            forecast_entry_edge=max(0.0, to_float(params.get("weather_min_forecast_edge"), DEFAULT_PARAMS["weather_min_forecast_edge"])),
            forecast_exit_edge=max(0.0, to_float(params.get("weather_exit_forecast_edge"), DEFAULT_PARAMS["weather_exit_forecast_edge"])),
        )


@dataclass(frozen=True)
class WeatherDecisionInput:
    token_id: str
    mid: float
    adaptive_spread: float
    best_bid: float
    best_ask: float
    position: float
    effective_usdc_balance: float
    open_buy_qty: float = 0.0
    open_sell_qty: float = 0.0


@dataclass(frozen=True)
class WeatherRuntimeConfig:
    max_position: float
    min_size: float
    price_tick: float
    join_epsilon: float


@dataclass(frozen=True)
class WeatherDecision:
    side: str
    price: float
    size: float
    aggressive: bool = False
    reason: str = ""


QuantizePriceFn = Callable[[float], float]


def sync_state(state: WeatherTokenState, position: float, mid: float, now: float) -> WeatherTokenState:
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


def forecast_distance_from_bucket(forecast_c: Optional[float], meta: Dict[str, Any]) -> Optional[float]:
    if forecast_c is None or not meta:
        return None
    unit = str(meta.get("unit") or "C").strip().upper()
    forecast = convert_celsius(forecast_c, unit)
    bucket_min = bucket_bound(meta.get("bucket_min"), meta.get("bucket_value"))
    bucket_max = bucket_bound(meta.get("bucket_max"), meta.get("bucket_value"))
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


def bucket_bound(raw: Any, fallback: Any) -> Optional[float]:
    if raw is not None:
        return to_float(raw, 0.0)
    if fallback is not None:
        return to_float(fallback, 0.0)
    return None


def convert_celsius(value_c: float, unit: str) -> float:
    if unit == "F":
        return (value_c * 9.0 / 5.0) + 32.0
    return value_c


def make_decision(
    *,
    quote: WeatherDecisionInput,
    state: WeatherTokenState,
    params: WeatherDecisionParams,
    runtime: WeatherRuntimeConfig,
    now: float,
    forecast_distance: Optional[float],
    quantize_price: QuantizePriceFn,
) -> Optional[WeatherDecision]:
    mid = max(0.0, float(quote.mid))
    if mid <= 0:
        return None

    position = max(0.0, float(quote.position))
    spread_now = 0.0
    if quote.best_bid > 0 and quote.best_ask > 0:
        spread_now = max(0.0, quote.best_ask - quote.best_bid)

    if position <= 1e-9:
        if not params.allow_reentry and state.last_exit_ts > 0:
            return None
        if state.last_exit_ts > 0 and params.cooldown_sec > 0 and (now - state.last_exit_ts) < params.cooldown_sec:
            return None
        if spread_now > 0 and spread_now > params.max_entry_spread:
            return None
        if mid < params.entry_min or mid > params.entry_max:
            return None
        if forecast_distance is None or forecast_distance < params.forecast_entry_edge:
            return None

        entry_price = entry_price_for(quote, runtime, params, quantize_price)
        buy_size = entry_size_for(quote, runtime, entry_price, params.position_pct)
        if buy_size <= 0:
            return None
        if (buy_size * entry_price) < params.min_order_notional or buy_size < max(0.01, float(runtime.min_size)):
            return None
        return WeatherDecision(side="BUY", price=entry_price, size=buy_size, reason="entry")

    entry_mid = state.entry_mid if state.entry_mid > 0 else mid
    hold_sec = (now - state.entry_ts) if state.entry_ts > 0 else 0.0
    token_end_ts = params.token_end_ts_map.get(quote.token_id, 0.0)

    stop_loss_hit = params.stop_loss_abs > 0 and mid <= (entry_mid - params.stop_loss_abs)
    take_profit_hit = (
        params.take_profit_abs > 0
        and mid >= (entry_mid + params.take_profit_abs)
        and hold_sec >= params.min_hold_sec
    )
    max_hold_hit = params.max_hold_sec > 0 and hold_sec >= params.max_hold_sec
    pre_settlement_exit = token_end_ts > 0 and now >= max(0.0, token_end_ts - params.exit_before_sec)
    range_break_hit = params.force_flat_on_range_break and mid < params.entry_min
    weather_hit = forecast_distance is not None and forecast_distance <= params.forecast_exit_edge

    if not (stop_loss_hit or take_profit_hit or max_hold_hit or pre_settlement_exit or range_break_hit or weather_hit):
        return None

    exit_size = max(0.0, position - max(0.0, float(quote.open_sell_qty)))
    if exit_size <= 0:
        return None

    aggressive = stop_loss_hit or range_break_hit or weather_hit
    exit_price = exit_price_for(quote, runtime, aggressive=aggressive, quantize_price=quantize_price)
    reason = (
        "stop_loss" if stop_loss_hit else
        "weather" if weather_hit else
        "range_break" if range_break_hit else
        "take_profit" if take_profit_hit else
        "max_hold" if max_hold_hit else
        "pre_settlement"
    )
    return WeatherDecision(side="SELL", price=exit_price, size=exit_size, aggressive=aggressive, reason=reason)


def entry_price_for(
    quote: WeatherDecisionInput,
    runtime: WeatherRuntimeConfig,
    params: WeatherDecisionParams,
    quantize_price: QuantizePriceFn,
) -> float:
    tick = max(1e-6, float(runtime.price_tick))
    if quote.best_bid > 0:
        base = min(quote.mid, quote.best_bid + max(0.0, float(runtime.join_epsilon)))
    else:
        base = quote.mid - max(tick, quote.adaptive_spread * 0.25)
    raw_price = clamp(base, params.entry_min, params.entry_max)
    return quantize_price(raw_price)


def entry_size_for(
    quote: WeatherDecisionInput,
    runtime: WeatherRuntimeConfig,
    entry_price: float,
    position_pct: float,
) -> float:
    if entry_price <= 0 or position_pct <= 0:
        return 0.0
    usdc_balance = max(0.0, float(quote.effective_usdc_balance))
    budget = usdc_balance * position_pct
    if budget <= 0:
        return 0.0
    size = budget / max(entry_price, 1e-6)
    size = min(size, usdc_balance / max(entry_price, 1e-6))

    max_pos = max(0.0, float(runtime.max_position))
    if max_pos > 0:
        room = max(0.0, max_pos - (max(0.0, float(quote.position)) + max(0.0, float(quote.open_buy_qty))))
        size = min(size, room)
    return max(0.0, size)


def exit_price_for(
    quote: WeatherDecisionInput,
    runtime: WeatherRuntimeConfig,
    *,
    aggressive: bool,
    quantize_price: QuantizePriceFn,
) -> float:
    tick = max(1e-6, float(runtime.price_tick))
    best_bid = max(0.0, float(quote.best_bid))
    best_ask = max(0.0, float(quote.best_ask))
    mid = max(0.0, float(quote.mid))

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
    return quantize_price(clamp(raw, 0.0001, 0.9999))
