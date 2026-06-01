from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


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
    low_band_ceiling: float = 0.40
    high_band_floor: float = 0.55
    split_enabled: bool = True
    taker_fraction: float = 0.50
    split_min_edge: float = 0.10
    high_band_shade_narrow: int = 1
    high_band_shade_wide: int = 2
    high_band_min_edge: float = 0.15
    high_band_size_mult: float = 0.60


def _reject_quote(
    *,
    policy: str,
    reason: str,
    token_prob: float,
    bid: float,
    ask: float,
    spread: float,
    tick: float,
    quote_mode: str = "no_quote",
    required_edge: float = 0.0,
    limit_price: float = 0.0,
    order_role: str = "single",
    maker_only: bool = True,
) -> Dict[str, Any]:
    return {
        "execution_policy": policy,
        "quote_status": "rejected",
        "quote_reason": reason,
        "limit_price": round(limit_price, 6),
        "quote_edge": round(token_prob - limit_price, 6),
        "required_quote_edge": round(required_edge, 6),
        "model_token_probability": round(token_prob, 6),
        "quote_best_bid": round(bid, 6),
        "quote_best_ask": round(ask, 6),
        "quote_spread": round(spread, 6),
        "quote_tick_size": round(tick, 6),
        "quote_mode": quote_mode,
        "child_order_role": order_role,
        "maker_only": bool(maker_only),
        "notional_fraction": 1.0,
        "size_multiplier": 1.0,
    }


def _accepted_quote(
    *,
    policy: str,
    limit_price: float,
    token_prob: float,
    bid: float,
    ask: float,
    spread: float,
    tick: float,
    quote_mode: str,
    required_edge: float,
    order_role: str = "single",
    maker_only: bool = True,
    notional_fraction: float = 1.0,
    size_multiplier: float = 1.0,
) -> Dict[str, Any]:
    return {
        "execution_policy": policy,
        "quote_status": "accepted",
        "quote_reason": "",
        "limit_price": round(limit_price, 6),
        "quote_edge": round(token_prob - limit_price, 6),
        "required_quote_edge": round(required_edge, 6),
        "model_token_probability": round(token_prob, 6),
        "quote_best_bid": round(bid, 6),
        "quote_best_ask": round(ask, 6),
        "quote_spread": round(spread, 6),
        "quote_tick_size": round(tick, 6),
        "quote_mode": quote_mode,
        "child_order_role": order_role,
        "maker_only": bool(maker_only),
        "notional_fraction": round(max(0.0, min(1.0, notional_fraction)), 6),
        "size_multiplier": round(max(0.0, size_multiplier), 6),
    }


def _deferred_quote(
    *,
    policy: str,
    placeholder: float,
    token_prob: float,
    tick: float,
    required_edge: float,
    order_role: str = "single",
    maker_only: bool = True,
    notional_fraction: float = 1.0,
    size_multiplier: float = 1.0,
) -> Dict[str, Any]:
    """Accepted placeholder quote for the no-live-book (production planner) path.

    The split/band decision only needs ``market_price`` + ``token_prob`` (both
    present in the signal), so we decide the leg structure here and let the
    executor re-fetch the live orderbook and re-price each leg by role.
    """

    quote = _accepted_quote(
        policy=policy,
        limit_price=placeholder,
        token_prob=token_prob,
        bid=0.0,
        ask=0.0,
        spread=0.0,
        tick=tick,
        quote_mode="defer_to_executor",
        required_edge=required_edge,
        order_role=order_role,
        maker_only=maker_only,
        notional_fraction=notional_fraction,
        size_multiplier=size_multiplier,
    )
    quote["quote_reason"] = "defer_to_executor_missing_two_sided_book"
    return quote


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

    if policy == "mid_price_core_v2":
        quotes = build_execution_quotes(
            signal,
            config,
            best_bid=best_bid,
            best_ask=best_ask,
            tick_size=tick_size,
        )
        return quotes[0] if quotes else _reject_quote(
            policy=policy,
            reason="no_v2_quote",
            token_prob=token_prob,
            bid=bid,
            ask=ask,
            spread=spread,
            tick=tick,
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
            "child_order_role": "single",
            "maker_only": True,
            "notional_fraction": 1.0,
            "size_multiplier": 1.0,
        }

    if policy not in ("maker_queue_v2",):
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
            "child_order_role": "single",
            "maker_only": True,
            "notional_fraction": 1.0,
            "size_multiplier": 1.0,
        }

    if bid <= 0 or ask <= 0 or ask <= bid:
        # No live two-sided book in the signal (snapshot branch).
        # Accept with a placeholder price so the executor can re-evaluate
        # against the live orderbook at CLOB submission time.
        placeholder = max(price_floor, min(price_ceiling, market_price))
        placeholder = _round_down_to_tick(placeholder, tick)
        return {
            "execution_policy": policy,
            "quote_status": "accepted",
            "quote_reason": "defer_to_executor_missing_two_sided_book",
            "limit_price": round(placeholder, 6),
            "quote_edge": round(token_prob - placeholder, 6),
            "required_quote_edge": round(config.min_quote_edge, 6),
            "model_token_probability": round(token_prob, 6),
            "quote_best_bid": 0.0,
            "quote_best_ask": 0.0,
            "quote_spread": 0.0,
            "quote_tick_size": round(tick, 6),
            "quote_mode": "defer_to_executor",
            "child_order_role": "single",
            "maker_only": True,
            "notional_fraction": 1.0,
            "size_multiplier": 1.0,
        }

    mid = (bid + ask) / 2.0
    if market_price > 0 and abs(mid - market_price) > config.max_mid_drift:
        return {
            "execution_policy": policy,
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
            "child_order_role": "single",
            "maker_only": True,
            "notional_fraction": 1.0,
            "size_multiplier": 1.0,
        }

    required_edge = config.min_quote_edge + config.adverse_selection_spread_fraction * spread
    edge_cap = token_prob - required_edge
    ask_cap = ask - tick
    if policy == "maker_queue_v2":
        # V2: always try to top the book by 1 tick.
        # If spread <= 1 tick the ask_cap collapses queue_price back to bid (join bid).
        # Wide-spread adverse selection is handled by the edge check, not by shading.
        queue_price = bid + tick
        quote_mode = "improve_bid" if (ask_cap > bid) else "join_bid"
    elif spread > config.max_quote_spread:
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
            "execution_policy": policy,
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
            "child_order_role": "single",
            "maker_only": True,
            "notional_fraction": 1.0,
            "size_multiplier": 1.0,
        }

    quote_edge = token_prob - limit_price
    if quote_edge < required_edge:
        return {
            "execution_policy": policy,
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
            "child_order_role": "single",
            "maker_only": True,
            "notional_fraction": 1.0,
            "size_multiplier": 1.0,
        }

    return {
        "execution_policy": policy,
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
        "child_order_role": "single",
        "maker_only": True,
        "notional_fraction": 1.0,
        "size_multiplier": 1.0,
    }


def build_execution_quotes(
    signal: Dict[str, Any],
    config: ExecutionPolicyConfig,
    *,
    best_bid: Optional[float] = None,
    best_ask: Optional[float] = None,
    tick_size: Optional[float] = None,
) -> List[Dict[str, Any]]:
    policy = (_safe_str(config.policy_name) or "mid_price_core_v1").lower()
    if policy != "mid_price_core_v2":
        return [build_execution_quote(signal, config, best_bid=best_bid, best_ask=best_ask, tick_size=tick_size)]

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

    low_band_ceiling = max(price_floor, min(price_ceiling, config.low_band_ceiling))
    high_band_floor = max(price_floor, min(price_ceiling, config.high_band_floor))

    if bid <= 0 or ask <= 0 or ask <= bid:
        # Production signals carry no live two-sided book (bid=0/ask=0). The
        # split/band decision only needs market_price + token_prob, so decide
        # the leg structure here and emit deferred placeholder quotes with the
        # correct child_order_role. The executor re-fetches the live book at
        # submit time and re-prices each leg by role (selecting the matching
        # child_order_role), so this stays consistent with the with-book path.
        entry_price = market_price
        placeholder = _round_down_to_tick(max(price_floor, min(price_ceiling, market_price)), tick)

        if entry_price < low_band_ceiling:
            split_edge_ok = (token_prob - entry_price) >= config.split_min_edge
            should_split = bool(config.split_enabled) and split_edge_ok
            if not should_split:
                return [
                    _deferred_quote(
                        policy=policy,
                        placeholder=placeholder,
                        token_prob=token_prob,
                        tick=tick,
                        required_edge=config.min_quote_edge,
                        order_role="single",
                        maker_only=True,
                        notional_fraction=1.0,
                    )
                ]
            return [
                _deferred_quote(
                    policy=policy,
                    placeholder=placeholder,
                    token_prob=token_prob,
                    tick=tick,
                    required_edge=config.split_min_edge,
                    order_role="taker",
                    maker_only=False,
                    notional_fraction=config.taker_fraction,
                ),
                _deferred_quote(
                    policy=policy,
                    placeholder=placeholder,
                    token_prob=token_prob,
                    tick=tick,
                    required_edge=config.min_quote_edge,
                    order_role="maker",
                    maker_only=True,
                    notional_fraction=(1.0 - config.taker_fraction),
                ),
            ]

        if entry_price < high_band_floor:
            return [
                _deferred_quote(
                    policy=policy,
                    placeholder=placeholder,
                    token_prob=token_prob,
                    tick=tick,
                    required_edge=config.min_quote_edge,
                    order_role="single",
                    maker_only=True,
                )
            ]

        required_edge = max(config.min_quote_edge, config.high_band_min_edge)
        if (token_prob - entry_price) < required_edge:
            return [
                _reject_quote(
                    policy=policy,
                    reason="high_band_edge_below_min",
                    token_prob=token_prob,
                    bid=0.0,
                    ask=0.0,
                    spread=0.0,
                    tick=tick,
                    quote_mode="high_band_no_quote",
                    required_edge=required_edge,
                    limit_price=entry_price,
                )
            ]
        return [
            _deferred_quote(
                policy=policy,
                placeholder=placeholder,
                token_prob=token_prob,
                tick=tick,
                required_edge=required_edge,
                order_role="single",
                maker_only=True,
                size_multiplier=config.high_band_size_mult,
            )
        ]

    mid = (bid + ask) / 2.0
    if market_price > 0 and abs(mid - market_price) > config.max_mid_drift:
        return [
            _reject_quote(
                policy=policy,
                reason="mid_drift_too_large",
                token_prob=token_prob,
                bid=bid,
                ask=ask,
                spread=spread,
                tick=tick,
                required_edge=config.min_quote_edge,
            )
        ]

    entry_price = market_price

    if entry_price < low_band_ceiling:
        split_edge_ok = (token_prob - entry_price) >= config.split_min_edge
        should_split = bool(config.split_enabled) and split_edge_ok
        maker_raw = min(bid + tick, ask - tick, token_prob - config.min_quote_edge, price_ceiling)
        maker_price = max(price_floor, _round_down_to_tick(maker_raw, tick))
        maker_mode = "low_band_improve_bid"
        if maker_price <= bid:
            maker_mode = "low_band_join_bid"
        if maker_price >= ask or maker_price <= 0:
            maker_quote = _reject_quote(
                policy=policy,
                reason="no_valid_resting_price",
                token_prob=token_prob,
                bid=bid,
                ask=ask,
                spread=spread,
                tick=tick,
                quote_mode="low_band_no_quote",
                required_edge=config.min_quote_edge,
                order_role="maker" if should_split else "single",
            )
        else:
            maker_quote = _accepted_quote(
                policy=policy,
                limit_price=maker_price,
                token_prob=token_prob,
                bid=bid,
                ask=ask,
                spread=spread,
                tick=tick,
                quote_mode=maker_mode if not should_split else f"split_{maker_mode}",
                required_edge=config.min_quote_edge,
                order_role="maker" if should_split else "single",
                maker_only=True,
                notional_fraction=(1.0 - config.taker_fraction) if should_split else 1.0,
            )
        if not should_split:
            if not split_edge_ok and maker_quote.get("quote_status") == "accepted":
                maker_quote = {**maker_quote, "quote_mode": f"no_split_{maker_quote.get('quote_mode')}"}
            return [maker_quote]

        taker_price = min(price_ceiling, max(price_floor, _round_up_to_tick(ask, tick)))
        if taker_price <= 0 or taker_price > price_ceiling:
            taker_quote = _reject_quote(
                policy=policy,
                reason="no_valid_taker_price",
                token_prob=token_prob,
                bid=bid,
                ask=ask,
                spread=spread,
                tick=tick,
                quote_mode="split_taker_no_quote",
                required_edge=config.split_min_edge,
                limit_price=taker_price,
                order_role="taker",
                maker_only=False,
            )
        else:
            taker_quote = _accepted_quote(
                policy=policy,
                limit_price=taker_price,
                token_prob=token_prob,
                bid=bid,
                ask=ask,
                spread=spread,
                tick=tick,
                quote_mode="split_taker_cross_ask",
                required_edge=config.split_min_edge,
                order_role="taker",
                maker_only=False,
                notional_fraction=config.taker_fraction,
            )
        return [taker_quote, maker_quote]

    if entry_price < high_band_floor:
        limit_price = max(price_floor, min(price_ceiling, _round_down_to_tick(entry_price, tick)))
        if limit_price >= ask:
            limit_price = _round_down_to_tick(ask - tick, tick)
        if limit_price <= 0 or limit_price < price_floor:
            return [
                _reject_quote(
                    policy=policy,
                    reason="no_valid_resting_price",
                    token_prob=token_prob,
                    bid=bid,
                    ask=ask,
                    spread=spread,
                    tick=tick,
                    quote_mode="mid_band_no_quote",
                    required_edge=config.min_quote_edge,
                    limit_price=limit_price,
                )
            ]
        return [
            _accepted_quote(
                policy=policy,
                limit_price=limit_price,
                token_prob=token_prob,
                bid=bid,
                ask=ask,
                spread=spread,
                tick=tick,
                quote_mode="mid_band_passive",
                required_edge=config.min_quote_edge,
                maker_only=True,
            )
        ]

    required_edge = max(config.min_quote_edge, config.high_band_min_edge)
    if (token_prob - entry_price) < required_edge:
        return [
            _reject_quote(
                policy=policy,
                reason="high_band_edge_below_min",
                token_prob=token_prob,
                bid=bid,
                ask=ask,
                spread=spread,
                tick=tick,
                quote_mode="high_band_no_quote",
                required_edge=required_edge,
                limit_price=entry_price,
            )
        ]
    shade_ticks = config.high_band_shade_narrow if spread <= config.narrow_spread else config.high_band_shade_wide
    shade_ticks = max(0, int(shade_ticks))
    raw_price = min(entry_price, mid) - shade_ticks * tick
    raw_price = min(raw_price, ask - tick, token_prob - required_edge, price_ceiling)
    limit_price = max(price_floor, _round_down_to_tick(raw_price, tick))
    if limit_price >= ask or limit_price <= 0:
        return [
            _reject_quote(
                policy=policy,
                reason="no_valid_resting_price",
                token_prob=token_prob,
                bid=bid,
                ask=ask,
                spread=spread,
                tick=tick,
                quote_mode="high_band_no_quote",
                required_edge=required_edge,
                limit_price=limit_price,
            )
        ]
    return [
        _accepted_quote(
            policy=policy,
            limit_price=limit_price,
            token_prob=token_prob,
            bid=bid,
            ask=ask,
            spread=spread,
            tick=tick,
            quote_mode="high_band_shade_narrow" if spread <= config.narrow_spread else "high_band_shade_wide",
            required_edge=required_edge,
            maker_only=True,
            size_multiplier=config.high_band_size_mult,
        )
    ]


def price_to_tick(price: float, tick_size: float, *, side: str = "BUY") -> float:
    if side.upper().strip() == "SELL":
        return round(_round_up_to_tick(price, tick_size), 6)
    return round(_round_down_to_tick(price, tick_size), 6)
