from dataclasses import dataclass


@dataclass
class Quote:
    bid: float
    ask: float


def compute_quotes(mid: float, spread: float, inventory: float = 0.0, skew_factor: float = 0.0) -> Quote:
    # 价格围绕 mid 展开，库存倾斜向 inventory 方向偏移
    skew = skew_factor * inventory
    bid = max(0.0001, min(0.9999, mid - spread / 2 - skew))
    ask = max(0.0001, min(0.9999, mid + spread / 2 - skew))
    if bid >= ask:
        bid = max(0.0001, ask - 0.0001)
    return Quote(bid=bid, ask=ask)
