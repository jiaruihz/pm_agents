"""市场微观结构信号函数（纯计算模块）。

特性：
- 无 I/O、无副作用，便于单测与回放复用；
- 输入是订单簿/价格历史，输出是信号与风险约束量；
- 被 tick_loop 与 replay_runner 共同调用。
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any, Dict, List

from src.strategies.pmm.config import PMMConfig
from src.strategies.pmm.utils.converters import normalize_levels, best_level


def weighted_mid(orderbook: Dict[str, Any]) -> float:
    """加权中间价（micro-price 近似），用于减轻薄盘口中点价偏差。"""
    bid_price, bid_size = best_level(orderbook.get("bids"), is_bid=True)
    ask_price, ask_size = best_level(orderbook.get("asks"), is_bid=False)
    if bid_price <= 0 or ask_price <= 0:
        return 0.0
    denom = bid_size + ask_size
    if denom <= 0:
        return 0.0
    return (bid_price * ask_size + ask_price * bid_size) / denom


def fair_mid(orderbook: Dict[str, Any], mode: str) -> float:
    """公平价选择器：weighted 优先，失败回退到普通 midpoint。"""
    from src.platform.market_data.orderbook import mid_price

    mode_value = (mode or "").strip().lower()
    if mode_value == "weighted":
        price = weighted_mid(orderbook)
        if price > 0:
            return price
    return mid_price(orderbook)


def depth_near_mid(orderbook: Dict[str, Any], delta: float) -> tuple[float, float]:
    """计算中间价附近 delta 范围内的 bid/ask 深度。"""
    from src.platform.market_data.orderbook import best_bid_ask

    top = best_bid_ask(orderbook)
    best_bid = top.get("best_bid", 0.0)
    best_ask = top.get("best_ask", 0.0)
    if best_bid <= 0 or best_ask <= 0:
        return 0.0, 0.0
    mid = (best_bid + best_ask) / 2.0
    bid_depth = 0.0
    ask_depth = 0.0
    for price, size in normalize_levels(orderbook.get("bids")):
        if price >= mid - delta:
            bid_depth += size
    for price, size in normalize_levels(orderbook.get("asks")):
        if price <= mid + delta:
            ask_depth += size
    return bid_depth, ask_depth


def order_flow_imbalance(orderbook: Dict[str, Any], delta: float) -> float:
    """订单流不平衡度（[-1, 1]）：越大表示买盘压力越强。"""
    bid_depth, ask_depth = depth_near_mid(orderbook, delta)
    denom = bid_depth + ask_depth
    if denom <= 0:
        return 0.0
    return (bid_depth - ask_depth) / denom


def realized_vol(hist: deque[float]) -> float:
    """基于中间价历史计算实现波动率。"""
    if len(hist) < 3:
        return 0.0
    items = list(hist)
    returns: List[float] = []
    for prev, cur in zip(items[:-1], items[1:]):
        if prev <= 0:
            continue
        returns.append((cur - prev) / prev)
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((x - mean) ** 2 for x in returns) / len(returns)
    return math.sqrt(max(0.0, variance))


def momentum(hist: deque[float], min_points: int) -> float:
    """简单动量：价格序列首尾变化率。"""
    if len(hist) < max(2, min_points):
        return 0.0
    first = hist[0]
    last = hist[-1]
    if first <= 0:
        return 0.0
    return (last - first) / first


def required_spread(config: PMMConfig, rv: float, inventory_signal: float) -> float:
    """最小自适应 spread：手续费 + 目标利润 + 波动补偿 + 库存风险补偿。"""
    return (
        config.fee_spread_floor
        + config.target_profit_spread
        + config.volatility_spread_coeff * max(0.0, rv)
        + config.inventory_risk_spread_coeff * abs(inventory_signal)
    )


def inventory_signal(
    token_id: str,
    token_ids: List[str],
    positions: Dict[str, float],
    max_position: float,
    sigmoid_k: float,
) -> float:
    """非线性库存偏移信号：接近仓位上限时，调整强度会更快增大。"""
    current = positions.get(token_id, 0.0)
    if len(token_ids) >= 2:
        if token_id == token_ids[0]:
            net = current - positions.get(token_ids[1], 0.0)
        elif token_id == token_ids[1]:
            net = current - positions.get(token_ids[0], 0.0)
        else:
            net = current
    else:
        net = current
    denom = max(1.0, max_position)
    raw_signal = max(-1.0, min(1.0, net / denom))
    signal = (2.0 / (1.0 + math.exp(-sigmoid_k * raw_signal))) - 1.0
    return max(-1.0, min(1.0, signal))
