import math
from dataclasses import dataclass


@dataclass
class Quote:
    bid: float
    ask: float
    bid_size_adj: float = 1.0
    ask_size_adj: float = 1.0
    allow_buy: bool = True
    allow_sell: bool = True


def compute_quotes_pro(
    mid: float,
    spread_ticks: int = 2,
    tick_size: float = 0.01,
    position: float = 0.0,
    open_buy_qty: float = 0.0,
    open_sell_qty: float = 0.0,
    max_position: float = 1000.0,
    size_decay_power: float = 2.0,
) -> Quote:
    # 1) Effective net exposure: position + potential buys - potential sells.
    eff_net = position + open_buy_qty - open_sell_qty

    # 2) Risk ratio in [-1, 1].
    ratio = eff_net / max(1.0, max_position)
    ratio = max(-1.0, min(1.0, ratio))

    # 3) Tick-based skewing to reduce inventory risk.
    skew_ticks = 0
    sign = 1 if ratio > 0 else -1
    if abs(ratio) > 0.2:
        skew_ticks = 1 * sign
    if abs(ratio) > 0.5:
        skew_ticks = 2 * sign
    if abs(ratio) > 0.8:
        skew_ticks = 4 * sign

    # 4) Continuous size decay by risk level.
    decay_power = max(1.0, float(size_decay_power))
    decay_factor = max(0.0, 1.0 - (abs(ratio) ** decay_power))

    bid_adj = 1.0
    ask_adj = 1.0
    if ratio > 0:
        bid_adj = decay_factor
    else:
        ask_adj = decay_factor

    # 5) Side gating at extreme risk.
    allow_buy = ratio <= 0.95
    allow_sell = ratio >= -0.95

    # 6) Raw quote around mid.
    safe_tick = max(1e-6, float(tick_size))
    spread_ticks = max(1, int(spread_ticks))
    half_spread = (spread_ticks / 2.0) * safe_tick
    skew_val = skew_ticks * safe_tick
    bid_raw = mid - half_spread - skew_val
    ask_raw = mid + half_spread - skew_val

    # 7) Conservative quantization.
    epsilon = 1e-9
    bid = math.floor((bid_raw + epsilon) / safe_tick) * safe_tick
    ask = math.ceil((ask_raw - epsilon) / safe_tick) * safe_tick

    # 8) Bounds and anti-cross.
    bid = max(safe_tick, min(1.0 - safe_tick, bid))
    ask = max(safe_tick, min(1.0 - safe_tick, ask))
    if bid >= ask:
        if ratio > 0:
            bid = max(safe_tick, ask - safe_tick)
        else:
            ask = min(1.0 - safe_tick, bid + safe_tick)

    return Quote(
        bid=bid,
        ask=ask,
        bid_size_adj=bid_adj,
        ask_size_adj=ask_adj,
        allow_buy=allow_buy,
        allow_sell=allow_sell,
    )


def compute_quotes(mid: float, spread: float, inventory: float = 0.0, skew_factor: float = 0.0) -> Quote:
    # Compatibility wrapper for legacy call sites.
    skew = skew_factor * inventory
    bid = max(0.0001, min(0.9999, mid - spread / 2 - skew))
    ask = max(0.0001, min(0.9999, mid + spread / 2 - skew))
    if bid >= ask:
        bid = max(0.0001, ask - 0.0001)
    return Quote(bid=bid, ask=ask)
