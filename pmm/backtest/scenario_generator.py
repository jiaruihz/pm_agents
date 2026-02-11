from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


def _clip_price(x: float) -> float:
    return max(0.01, min(0.99, x))


def _make_level(price: float, size: float) -> Dict[str, float]:
    return {"price": round(price, 4), "size": round(max(0.0, size), 4)}


def _book_from_mid(
    mid: float,
    spread: float,
    depth: float,
) -> Dict[str, List[Dict[str, float]]]:
    spread = max(0.002, spread)
    bid = _clip_price(mid - spread / 2.0)
    ask = _clip_price(mid + spread / 2.0)
    if bid >= ask:
        ask = min(0.99, bid + 0.001)
    # Two levels are enough for current paper fill model and diagnostics.
    return {
        "bids": [
            _make_level(bid, depth),
            _make_level(max(0.01, bid - 0.01), depth * 0.6),
        ],
        "asks": [
            _make_level(ask, depth),
            _make_level(min(0.99, ask + 0.01), depth * 0.6),
        ],
    }


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
    return _clip_price(x)


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
        no_mid = _clip_price(1.0 - yes_mid)

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

        yes_book = _book_from_mid(yes_mid, spread, depth)
        # Keep NO correlated but not identical to avoid trivial symmetry.
        no_spread = max(0.002, spread * (0.9 + rng.random() * 0.2))
        no_depth = max(20.0, depth * (0.9 + rng.random() * 0.25))
        no_book = _book_from_mid(no_mid, no_spread, no_depth)

        ticks.append(
            {
                "t": t,
                "event": event_label,
                "orderbooks": {
                    yes_id: yes_book,
                    no_id: no_book,
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
            # Small initial split inventory (must stay <= max_position, default 100).
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
