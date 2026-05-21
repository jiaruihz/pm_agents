from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional


def _safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _side_token_probability(*, signal_side: str, model_probability_yes: float) -> float:
    side = signal_side.upper().strip()
    p_yes = max(0.0, min(1.0, model_probability_yes))
    if side in {"BUY_NO", "NO"}:
        return 1.0 - p_yes
    return p_yes


def _round_down_to_tick(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        return price
    return math.floor((price + 1e-12) / tick_size) * tick_size


def _round_up_to_tick(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        return price
    return math.ceil((price - 1e-12) / tick_size) * tick_size


@dataclass(frozen=True)
class ExecutionPolicyConfig:
    policy_name: str = "mid_price_core_v1"
    price_offset: float = 0.0
    price_floor: float = 0.01
    price_ceiling: float = 0.99
    tick_size: float = 0.01
    min_quote_edge: float = 0.03
    max_quote_spread: float = 0.12
    max_mid_drift: float = 0.10
    quote_improvement_ticks: int = 1
    wide_spread_shade_ticks: int = 1
    narrow_spread: float = 0.03
    adverse_selection_spread_fraction: float = 0.50


def build_execution_quote(
    signal: Dict[str, Any],
    config: ExecutionPolicyConfig,
    *,
    best_bid: Optional[float] = None,
    best_ask: Optional[float] = None,
    tick_size: Optional[float] = None,
) -> Dict[str, Any]:
    """Return a deterministic quote decision for the configured execution policy.

    The returned dict is intentionally JSON-friendly and can be copied directly
    into plan/order records for later policy comparison.
    """

    policy = (_safe_str(config.policy_name) or "mid_price_core_v1").lower()
    market_price = _to_float(signal.get("market_price"), 0.0)
    bid = _to_float(best_bid, _to_float(signal.get("best_bid"), 0.0))
    ask = _to_float(best_ask, _to_float(signal.get("best_ask"), 0.0))
    spread = max(0.0, ask - bid) if bid > 0 and ask > 0 else _to_float(signal.get("spread"), 0.0)
    signal_tick = _to_float(signal.get("minimum_tick_size"), _to_float(signal.get("tick_size"), config.tick_size))
    tick = float(tick_size if tick_size is not None else signal_tick)
    tick = tick if tick > 0 else 0.001
    price_floor = max(0.0, min(1.0, config.price_floor))
    price_ceiling = max(price_floor, min(1.0, config.price_ceiling))
    existing_token_prob = signal.get("model_token_probability")
    if existing_token_prob is not None:
        token_prob = max(0.0, min(1.0, _to_float(existing_token_prob, 0.0)))
    else:
        model_p_yes = _to_float(signal.get("model_probability_yes"), _to_float(signal.get("model_p_yes"), 0.0))
        token_prob = _side_token_probability(
            signal_side=_safe_str(signal.get("signal_side")),
            model_probability_yes=model_p_yes,
        )

    if policy == "mid_price_core_v1":
        limit_price = max(price_floor, min(price_ceiling, market_price + config.price_offset))
        limit_price = _round_down_to_tick(limit_price, tick)
        return {
            "execution_policy": "mid_price_core_v1",
            "quote_status": "accepted",
            "quote_reason": "",
            "limit_price": round(limit_price, 6),
            "quote_edge": round(token_prob - limit_price, 6),
            "required_quote_edge": 0.0,
            "model_token_probability": round(token_prob, 6),
            "quote_best_bid": round(bid, 6),
            "quote_best_ask": round(ask, 6),
            "quote_spread": round(spread, 6),
            "quote_tick_size": round(tick, 6),
            "quote_mode": "legacy_snapshot_price",
        }

    if policy != "maker_queue_v1":
        return {
            "execution_policy": policy,
            "quote_status": "rejected",
            "quote_reason": "unknown_execution_policy",
            "limit_price": 0.0,
            "quote_edge": round(token_prob, 6),
            "required_quote_edge": 0.0,
            "model_token_probability": round(token_prob, 6),
            "quote_best_bid": round(bid, 6),
            "quote_best_ask": round(ask, 6),
            "quote_spread": round(spread, 6),
            "quote_tick_size": round(tick, 6),
            "quote_mode": "unknown",
        }

    if bid <= 0 or ask <= 0 or ask <= bid:
        return {
            "execution_policy": "maker_queue_v1",
            "quote_status": "rejected",
            "quote_reason": "missing_two_sided_book",
            "limit_price": 0.0,
            "quote_edge": round(token_prob, 6),
            "required_quote_edge": round(config.min_quote_edge, 6),
            "model_token_probability": round(token_prob, 6),
            "quote_best_bid": round(bid, 6),
            "quote_best_ask": round(ask, 6),
            "quote_spread": round(spread, 6),
            "quote_tick_size": round(tick, 6),
            "quote_mode": "no_quote",
        }

    mid = (bid + ask) / 2.0
    if market_price > 0 and abs(mid - market_price) > config.max_mid_drift:
        return {
            "execution_policy": "maker_queue_v1",
            "quote_status": "rejected",
            "quote_reason": "mid_drift_too_large",
            "limit_price": 0.0,
            "quote_edge": round(token_prob - bid, 6),
            "required_quote_edge": round(config.min_quote_edge + config.adverse_selection_spread_fraction * spread, 6),
            "model_token_probability": round(token_prob, 6),
            "quote_best_bid": round(bid, 6),
            "quote_best_ask": round(ask, 6),
            "quote_spread": round(spread, 6),
            "quote_tick_size": round(tick, 6),
            "quote_mode": "no_quote",
        }

    required_edge = config.min_quote_edge + config.adverse_selection_spread_fraction * spread
    edge_cap = token_prob - required_edge
    ask_cap = ask - tick
    if spread > config.max_quote_spread:
        queue_price = bid - max(0, int(config.wide_spread_shade_ticks)) * tick
        quote_mode = "shade_below_bid_wide_spread"
    elif spread <= config.narrow_spread:
        queue_price = bid + max(0, int(config.quote_improvement_ticks)) * tick
        quote_mode = "improve_bid"
    else:
        queue_price = bid
        quote_mode = "join_bid"
    raw_price = min(queue_price, ask_cap, edge_cap, price_ceiling)
    limit_price = _round_down_to_tick(raw_price, tick)
    limit_price = max(price_floor, min(price_ceiling, limit_price))

    if limit_price >= ask:
        limit_price = _round_down_to_tick(ask - tick, tick)
    if limit_price < price_floor or limit_price <= 0:
        quote_edge = token_prob - max(0.0, limit_price)
        return {
            "execution_policy": "maker_queue_v1",
            "quote_status": "rejected",
            "quote_reason": "no_valid_resting_price",
            "limit_price": 0.0,
            "quote_edge": round(quote_edge, 6),
            "required_quote_edge": round(required_edge, 6),
            "model_token_probability": round(token_prob, 6),
            "quote_best_bid": round(bid, 6),
            "quote_best_ask": round(ask, 6),
            "quote_spread": round(spread, 6),
            "quote_tick_size": round(tick, 6),
            "quote_mode": "no_quote",
        }

    quote_edge = token_prob - limit_price
    if quote_edge < required_edge:
        return {
            "execution_policy": "maker_queue_v1",
            "quote_status": "rejected",
            "quote_reason": "quote_edge_below_required",
            "limit_price": round(limit_price, 6),
            "quote_edge": round(quote_edge, 6),
            "required_quote_edge": round(required_edge, 6),
            "model_token_probability": round(token_prob, 6),
            "quote_best_bid": round(bid, 6),
            "quote_best_ask": round(ask, 6),
            "quote_spread": round(spread, 6),
            "quote_tick_size": round(tick, 6),
            "quote_mode": quote_mode,
        }

    return {
        "execution_policy": "maker_queue_v1",
        "quote_status": "accepted",
        "quote_reason": "",
        "limit_price": round(limit_price, 6),
        "quote_edge": round(quote_edge, 6),
        "required_quote_edge": round(required_edge, 6),
        "model_token_probability": round(token_prob, 6),
        "quote_best_bid": round(bid, 6),
        "quote_best_ask": round(ask, 6),
        "quote_spread": round(spread, 6),
        "quote_tick_size": round(tick, 6),
        "quote_mode": quote_mode,
    }


def price_to_tick(price: float, tick_size: float, *, side: str = "BUY") -> float:
    if side.upper().strip() == "SELL":
        return round(_round_up_to_tick(price, tick_size), 6)
    return round(_round_down_to_tick(price, tick_size), 6)
