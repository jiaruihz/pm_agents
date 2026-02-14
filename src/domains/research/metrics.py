"""Metrics computation for liquidity and depth."""

from typing import Dict, Iterable, List, Tuple


def compute_spread(best_bid: float, best_ask: float, mid: float) -> Tuple[float, float]:
    spread = best_ask - best_bid
    spread_pct_mid = spread / mid if mid and mid > 0 else None
    return spread, spread_pct_mid


def compute_depth(levels: Iterable[Dict[str, float]], mid: float, pct: float, side: str) -> float:
    if mid is None or mid <= 0:
        return 0.0
    lower = mid * (1 - pct)
    upper = mid * (1 + pct)
    total = 0.0
    for lvl in levels:
        price = lvl.get("price")
        size = lvl.get("size") or 0
        if price is None:
            continue
        if side == "bid" and price >= lower:
            total += size
        if side == "ask" and price <= upper:
            total += size
    return total


def compute_depths(mid: float, bids: List[Dict[str, float]], asks: List[Dict[str, float]]) -> Dict[str, float]:
    return {
        "depth_1pct_bid": compute_depth(bids, mid, 0.01, "bid"),
        "depth_1pct_ask": compute_depth(asks, mid, 0.01, "ask"),
        "depth_2pct_bid": compute_depth(bids, mid, 0.02, "bid"),
        "depth_2pct_ask": compute_depth(asks, mid, 0.02, "ask"),
    }


def compute_metrics(price_row: Dict[str, float], levels: List[Dict[str, float]]) -> Dict[str, float]:
    mid = price_row.get("mid")
    best_bid = price_row.get("best_bid")
    best_ask = price_row.get("best_ask")
    bids = [l for l in levels if l.get("side") == "bid"]
    asks = [l for l in levels if l.get("side") == "ask"]
    depth_vals = compute_depths(mid or 0.0, bids, asks)
    spread = price_row.get("spread")
    spread_pct_mid = price_row.get("spread_pct_mid")
    metrics = {
        "mid": mid,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "spread_pct_mid": spread_pct_mid,
        **depth_vals,
    }
    return metrics

