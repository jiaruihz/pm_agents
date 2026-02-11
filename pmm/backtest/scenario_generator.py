from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


def _clip_price(x: float, tick: float = 0.01) -> float:
    """Clip price to [0.01, 0.99] and snap to Polymarket tick grid."""
    clamped = max(0.01, min(0.99, x))
    return _snap_to_tick(clamped, tick)


def _snap_to_tick(price: float, tick: float = 0.01) -> float:
    """Round price to nearest tick (Polymarket uses 0.01 or 0.001)."""
    if tick <= 0:
        tick = 0.01
    decimals = max(0, -int(round(math.log10(tick))))
    return round(round(price / tick) * tick, decimals)


def _make_level(price: float, size: float, tick: float = 0.01) -> Dict[str, float]:
    return {"price": _snap_to_tick(price, tick), "size": round(max(0.0, size), 2)}


def _book_from_mid(
    mid: float,
    spread: float,
    depth: float,
    tick: float = 0.01,
) -> Dict[str, List[Dict[str, float]]]:
    spread = max(tick * 2, spread)
    bid = _clip_price(mid - spread / 2.0, tick)
    ask = _clip_price(mid + spread / 2.0, tick)
    if bid >= ask:
        ask = min(0.99, bid + tick)
    # Two levels: top-of-book + one deeper level.
    return {
        "bids": [
            _make_level(bid, depth, tick),
            _make_level(max(0.01, bid - tick), depth * 0.6, tick),
        ],
        "asks": [
            _make_level(ask, depth, tick),
            _make_level(min(0.99, ask + tick), depth * 0.6, tick),
        ],
    }


def _top_size(orderbook: Dict[str, List[Dict[str, float]]]) -> float:
    bids = orderbook.get("bids") or []
    asks = orderbook.get("asks") or []
    bid_size = float(bids[0].get("size", 0.0)) if bids else 0.0
    ask_size = float(asks[0].get("size", 0.0)) if asks else 0.0
    return max(1.0, (bid_size + ask_size) / 2.0)


def _flow_scale(fillability: str) -> float:
    mode = fillability.lower()
    if mode == "high":
        return 0.09
    if mode == "medium":
        return 0.04
    return 0.015


def _trend_bias(spec: CaseSpec, t: int) -> float:
    # Positive => buy taker pressure, negative => sell taker pressure.
    p = spec.pattern
    if p in {"trend_up", "shock_up", "spike_revert"}:
        return 0.35
    if p in {"trend_down", "shock_down", "drop_revert"}:
        return -0.35
    if p == "whipsaw":
        return 0.28 if (t // 20) % 2 == 0 else -0.28
    return 0.0


def _trade_flow_for_token(
    spec: CaseSpec,
    t: int,
    event_label: str,
    top_depth: float,
    rng: random.Random,
    bias: float,
) -> Dict[str, float]:
    base = top_depth * _flow_scale(spec.fillability) * (0.75 + 0.6 * rng.random())
    if event_label in {"shock_window", "event_window"}:
        base *= 2.2
    elif event_label == "liquidity_dryup":
        base *= 0.35
    elif event_label == "reversion_window":
        base *= 1.2

    # Clamp and normalize directional split.
    pressure = _clip_price(0.5 + bias * 0.35 + rng.uniform(-0.08, 0.08), tick=0.0001)
    buy_frac = max(0.05, min(0.95, pressure))
    buy_qty = round(max(0.0, base * buy_frac), 2)
    sell_qty = round(max(0.0, base * (1.0 - buy_frac)), 2)
    return {"buy_taker_qty": buy_qty, "sell_taker_qty": sell_qty}


@dataclass
class CaseSpec:
    name: str
    description: str
    base_mid: float
    ticks: int
    pattern: str
    volatility: float
    drift_per_tick: float
    spread_base: float
    spread_jitter: float
    depth_base: float
    fillability: str
    osc_amp: float = 0.02
    osc_period: int = 48
    shock_tick: int = 0
    shock_jump: float = 0.12
    shock_len: int = 40
    shock_spread_mult: float = 2.5
    shock_depth_mult: float = 0.4
    dryup_start: int = 0
    dryup_spread_mult: float = 2.5
    dryup_depth_mult: float = 0.25
    price_tick: float = 0.01  # Polymarket tick size: 0.01 (99% markets) or 0.001

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "CaseSpec":
        return CaseSpec(
            name=str(data["name"]),
            description=str(data.get("description", "")),
            base_mid=float(data["base_mid"]),
            ticks=int(data["ticks"]),
            pattern=str(data["pattern"]),
            volatility=float(data.get("volatility", 0.002)),
            drift_per_tick=float(data.get("drift_per_tick", 0.0)),
            spread_base=float(data.get("spread_base", 0.02)),
            spread_jitter=float(data.get("spread_jitter", 0.003)),
            depth_base=float(data.get("depth_base", 1500.0)),
            fillability=str(data.get("fillability", "medium")),
            osc_amp=float(data.get("osc_amp", 0.02)),
            osc_period=int(data.get("osc_period", 48)),
            shock_tick=int(data.get("shock_tick", 0)),
            shock_jump=float(data.get("shock_jump", 0.12)),
            shock_len=int(data.get("shock_len", 40)),
            shock_spread_mult=float(data.get("shock_spread_mult", 2.5)),
            shock_depth_mult=float(data.get("shock_depth_mult", 0.4)),
            dryup_start=int(data.get("dryup_start", 0)),
            dryup_spread_mult=float(data.get("dryup_spread_mult", 2.5)),
            dryup_depth_mult=float(data.get("dryup_depth_mult", 0.25)),
            price_tick=float(data.get("price_tick", 0.01)),
        )


def _fill_cross_adjustment(
    t: int,
    fillability: str,
    spread: float,
) -> float:
    """
    Returns signed compression to force occasional book crossings.

    Negative values narrow spread (easier fills), positive widen spread.
    """
    mode = fillability.lower()
    if mode == "low":
        return spread * 0.45 if (t % 19 == 0) else spread * 0.1
    if mode == "high":
        if t % 13 == 0:
            return -spread * 0.6
        if t % 7 == 0:
            return -spread * 0.35
        return -spread * 0.12
    # medium
    if t % 17 == 0:
        return -spread * 0.25
    return -spread * 0.05


def _yes_mid_at_tick(spec: CaseSpec, t: int, prev: float, rng: random.Random) -> float:
    noise = rng.gauss(0.0, spec.volatility)
    p = spec.pattern
    x = prev
    if p == "stable":
        x = prev + noise * 0.6
    elif p == "trend_up":
        x = prev + spec.drift_per_tick + noise
    elif p == "trend_down":
        x = prev - spec.drift_per_tick + noise
    elif p == "oscillating":
        phase = (2.0 * math.pi * t) / max(8, spec.osc_period)
        x = spec.base_mid + spec.osc_amp * math.sin(phase) + noise
    elif p in {"shock_up", "shock_down"}:
        x = prev + noise + (spec.drift_per_tick if p == "shock_up" else -spec.drift_per_tick)
        if spec.shock_tick <= t < spec.shock_tick + max(1, spec.shock_len):
            decay = 1.0 - ((t - spec.shock_tick) / max(1, spec.shock_len))
            jump = spec.shock_jump * decay
            x += jump if p == "shock_up" else -jump
    elif p == "regime_switch":
        q = t / max(1, spec.ticks)
        if q < 0.25:
            x = prev + noise * 0.4
        elif q < 0.5:
            x = prev + spec.drift_per_tick + noise
        elif q < 0.75:
            phase = (2.0 * math.pi * t) / 42.0
            x = prev + 0.018 * math.sin(phase) + noise
        else:
            x = prev - spec.drift_per_tick + noise
    elif p == "whipsaw":
        direction = 1.0 if (t // 20) % 2 == 0 else -1.0
        x = prev + direction * spec.drift_per_tick + noise * 1.1
    elif p == "liquidity_dryup":
        x = prev + noise + spec.drift_per_tick
    elif p == "spike_revert":
        x = prev + noise * 0.9
        if spec.shock_tick <= t < spec.shock_tick + max(1, spec.shock_len):
            decay = 1.0 - ((t - spec.shock_tick) / max(1, spec.shock_len))
            x += spec.shock_jump * decay
        elif t >= spec.shock_tick + spec.shock_len:
            # Pull back toward baseline after event.
            x += (spec.base_mid - prev) * 0.08
    elif p == "drop_revert":
        x = prev + noise * 0.9
        if spec.shock_tick <= t < spec.shock_tick + max(1, spec.shock_len):
            decay = 1.0 - ((t - spec.shock_tick) / max(1, spec.shock_len))
            x -= spec.shock_jump * decay
        elif t >= spec.shock_tick + spec.shock_len:
            x += (spec.base_mid - prev) * 0.08
    else:
        x = prev + noise
    return _clip_price(x, spec.price_tick)


def _tick_event_label(spec: CaseSpec, t: int) -> str:
    if spec.pattern in {"shock_up", "shock_down"}:
        if spec.shock_tick <= t < spec.shock_tick + max(1, spec.shock_len):
            return "shock_window"
    if spec.pattern in {"spike_revert", "drop_revert"}:
        if spec.shock_tick <= t < spec.shock_tick + max(1, spec.shock_len):
            return "event_window"
        if t >= spec.shock_tick + spec.shock_len and t < spec.shock_tick + spec.shock_len + 80:
            return "reversion_window"
    if spec.pattern == "liquidity_dryup" and t >= spec.dryup_start:
        return "liquidity_dryup"
    return "normal"


def generate_case(spec: CaseSpec, token_ids: List[str], seed: int = 42) -> Dict[str, Any]:
    rng = random.Random(seed)
    yes_id, no_id = token_ids[0], token_ids[1]
    yes_mid = _clip_price(spec.base_mid)
    ticks: List[Dict[str, Any]] = []

    for t in range(spec.ticks):
        prev_yes = yes_mid
        yes_mid = _yes_mid_at_tick(spec, t, prev_yes, rng)
        no_mid = _clip_price(1.0 - yes_mid, spec.price_tick)

        event_label = _tick_event_label(spec, t)

        spread = max(0.004, spec.spread_base + rng.gauss(0.0, spec.spread_jitter))
        depth = max(30.0, spec.depth_base * (1.0 + rng.gauss(0.0, 0.12)))

        if event_label in {"shock_window", "event_window"}:
            spread *= spec.shock_spread_mult
            depth *= spec.shock_depth_mult
        if event_label == "liquidity_dryup":
            spread *= spec.dryup_spread_mult
            depth *= spec.dryup_depth_mult

        spread += _fill_cross_adjustment(t, spec.fillability, spread)
        spread = max(0.002, spread)

        yes_book = _book_from_mid(yes_mid, spread, depth, spec.price_tick)
        # Keep NO correlated but not identical to avoid trivial symmetry.
        no_spread = max(spec.price_tick * 2, spread * (0.9 + rng.random() * 0.2))
        no_depth = max(20.0, depth * (0.9 + rng.random() * 0.25))
        no_book = _book_from_mid(no_mid, no_spread, no_depth, spec.price_tick)

        top_depth_yes = _top_size(yes_book)
        top_depth_no = _top_size(no_book)
        delta = yes_mid - prev_yes
        dyn_bias = max(-0.5, min(0.5, delta / max(0.001, spec.volatility * 4.0)))
        bias_yes = max(-0.7, min(0.7, _trend_bias(spec, t) + dyn_bias))
        bias_no = -bias_yes
        flow_yes = _trade_flow_for_token(spec, t, event_label, top_depth_yes, rng, bias_yes)
        flow_no = _trade_flow_for_token(spec, t, event_label, top_depth_no, rng, bias_no)

        ticks.append(
            {
                "t": t,
                "event": event_label,
                "orderbooks": {
                    yes_id: yes_book,
                    no_id: no_book,
                },
                "trade_flow": {
                    yes_id: flow_yes,
                    no_id: flow_no,
                },
            }
        )

    if spec.fillability.lower() == "high":
        strategy_overrides = {
            "base_spread": 0.025,
            "min_profitability_spread": 0.006,
            "deadband": 0.01,
            "skew_factor": 0.05,
            "join_epsilon": 0.001,
            "min_edge": 0.001,
            "inventory_sigmoid_k": 4.0,
            "mid_price_mode": "weighted",
            "alpha_enabled": False,
            "enforce_inventory_for_sell": True,
            "fee_spread_floor": 0.0005,
            "target_profit_spread": 0.0005,
            "volatility_spread_coeff": 0.5,
            "inventory_risk_spread_coeff": 0.002,
            "paper_fill_model": "optimistic",
            "paper_fill_epsilon": 0.001,
            "paper_queue_share": 0.35,
        }
    elif spec.fillability.lower() == "medium":
        strategy_overrides = {
            "base_spread": 0.03,
            "min_profitability_spread": 0.01,
            "deadband": 0.01,
            "skew_factor": 0.05,
            "join_epsilon": 0.001,
            "min_edge": 0.002,
            "inventory_sigmoid_k": 4.0,
            "mid_price_mode": "weighted",
            "alpha_enabled": False,
            "enforce_inventory_for_sell": True,
            "fee_spread_floor": 0.001,
            "target_profit_spread": 0.001,
            "volatility_spread_coeff": 0.8,
            "inventory_risk_spread_coeff": 0.003,
            "paper_fill_model": "conservative",
            "paper_fill_epsilon": 0.001,
            "paper_queue_share": 0.25,
        }
    else:
        strategy_overrides = {
            "base_spread": 0.04,
            "min_profitability_spread": 0.03,
            "deadband": 0.01,
            "skew_factor": 0.05,
            "join_epsilon": 0.001,
            "min_edge": 0.002,
            "inventory_sigmoid_k": 4.0,
            "mid_price_mode": "weighted",
            "alpha_enabled": False,
            "enforce_inventory_for_sell": True,
            "paper_fill_model": "conservative",
            "paper_fill_epsilon": 0.001,
            "paper_queue_share": 0.2,
        }

    return {
        "scenario_id": spec.name,
        "description": spec.description,
        "token_ids": [yes_id, no_id],
        "initial_state": {
            # Small initial split inventory (must stay ≤ max_position, default 100).
            "usdc": 100.0,
            "positions": {yes_id: 50.0, no_id: 50.0},
        },
        "strategy_overrides": strategy_overrides,
        "ticks": ticks,
    }


def generate_all_from_catalog(catalog_path: str, out_dir: str, seed: int = 42) -> Dict[str, Any]:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    token_ids = [str(x) for x in catalog.get("default_token_ids", ["YES", "NO"])]
    cases_raw = catalog.get("cases", [])

    created: List[str] = []
    for idx, item in enumerate(cases_raw):
        spec = CaseSpec.from_dict(item)
        case_seed = seed + (idx * 9973)
        payload = generate_case(spec=spec, token_ids=token_ids, seed=case_seed)
        file_path = out_path / f"{spec.name}.json"
        file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        created.append(str(file_path))

    return {
        "catalog": catalog_path,
        "out_dir": out_dir,
        "count": len(created),
        "files": created,
    }
