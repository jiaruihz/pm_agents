from typing import Any, Dict, Tuple


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def best_bid_ask_with_size(orderbook: Dict[str, Any]) -> Dict[str, float]:
    bids = orderbook.get("bids") or []
    asks = orderbook.get("asks") or []

    def _level(level: Any) -> Tuple[float, float]:
        if isinstance(level, dict):
            return safe_float(level.get("price"), 0.0), safe_float(level.get("size"), 0.0)
        if isinstance(level, (list, tuple)) and len(level) >= 2:
            return safe_float(level[0], 0.0), safe_float(level[1], 0.0)
        return 0.0, 0.0

    best_bid, best_bid_size = (0.0, 0.0)
    best_ask, best_ask_size = (0.0, 0.0)
    if bids:
        best_bid, best_bid_size = _level(bids[0])
    if asks:
        best_ask, best_ask_size = _level(asks[0])

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "best_bid_size": max(0.0, best_bid_size),
        "best_ask_size": max(0.0, best_ask_size),
    }


def merge_arb_edge(ask_yes: float, ask_no: float, fee_buffer: float) -> float:
    # Positive edge means candidate profit before gas on 1 USDC notional.
    return 1.0 - (ask_yes + ask_no + fee_buffer)


def split_arb_edge(bid_yes: float, bid_no: float, fee_buffer: float) -> float:
    # Positive edge means candidate profit before gas on 1 USDC notional.
    return (bid_yes + bid_no) - 1.0 - fee_buffer


def expected_profit_usdc(edge: float, notional: float, gas_estimate_usdc: float) -> float:
    return max(0.0, edge) * max(0.0, notional) - max(0.0, gas_estimate_usdc)


def min_fill_size_for_notional(price_a: float, price_b: float, max_notional_usdc: float) -> float:
    denom = max(1e-9, price_a + price_b)
    return max(0.0, max_notional_usdc / denom)


def to_ctf_units(usdc_amount: float, scale: int) -> int:
    return int(max(0.0, usdc_amount) * max(1, int(scale)))


def from_ctf_units(units: int, scale: int) -> float:
    return max(0, int(units)) / float(max(1, int(scale)))
