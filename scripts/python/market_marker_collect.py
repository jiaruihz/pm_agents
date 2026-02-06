import ast
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# 将项目根目录加入 sys.path，避免每次手动设置 PYTHONPATH
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 加载根目录的 .env（配置一次即可复用）
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from agents.polymarket.polymarket import Polymarket


# 统一的时间戳格式，方便批次落盘
def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


# 确保输出目录存在（不会删除旧数据）
def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


# 保存 JSON（保留中文）
def _write_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 解析 market.clob_token_ids（字符串形式的 list）
def _parse_token_ids(raw: str) -> List[str]:
    if not raw:
        return []
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except Exception:
        pass
    return []


# 拉取全部事件
def collect_events(polymarket: Polymarket) -> List[Any]:
    return polymarket.get_all_events()


# 事件粗过滤（沿用项目内已有逻辑）
def coarse_filter_events(polymarket: Polymarket, events: List[Any]) -> List[Any]:
    return polymarket.filter_events_for_trading(events)


# 拉取全部市场
def collect_markets(polymarket: Polymarket) -> List[Any]:
    return polymarket.get_all_markets()


def _parse_end_time(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        # 兼容 ISO 时间格式（常见为 2024-07-15T17:12:48.601056Z）
        if value.endswith("Z"):
            value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _extract_mid_price(outcome_prices: str) -> Optional[float]:
    if not outcome_prices:
        return None
    try:
        parsed = ast.literal_eval(outcome_prices)
        if isinstance(parsed, list) and parsed:
            return float(parsed[0])
    except Exception:
        return None
    return None


def _rule_is_clear(description: str) -> bool:
    # 规则清晰性（启发式）：至少包含“如何结算”的关键词，且避免明显主观词
    text = (description or "").lower()
    required_keywords = [
        "resolve",
        "resolution",
        "resolves",
        "resolution source",
        "will resolve",
        "resolve to",
    ]
    if not any(k in text for k in required_keywords):
        return False

    # 主观/模糊词（可调整）
    ambiguous_terms = [
        "successful",
        "success",
        "improve",
        "better",
        "worse",
        "good",
        "bad",
        "significant",
        "major",
        "meaningful",
        "strong",
        "weak",
        "likely",
        "unlikely",
    ]
    if any(k in text for k in ambiguous_terms):
        return False
    return True


# ---------- Step 2 打分相关的可调参数 ----------
# 盘口价差阈值（越小越好）
MAX_SPREAD = 0.05
# 深度统计半径：mid ± delta
DEPTH_DELTA = 0.01
# 深度阈值（越大越好）
MIN_DEPTH = 25.0
# 短窗采样次数与间隔（做波动与跳变率）
MID_SAMPLES = 4
MID_SAMPLE_INTERVAL_SEC = 0.6
# 波动与跳变阈值（可调）
MAX_VOL = 0.02
JUMP_THRESHOLD = 0.02
MAX_JUMP_RATE = 0.35


def _normalize_levels(levels: Any) -> List[Dict[str, float]]:
    normalized: List[Dict[str, float]] = []
    if not levels:
        return normalized
    for level in levels:
        if isinstance(level, dict):
            price = float(level.get("price", 0))
            size = float(level.get("size", 0))
        elif isinstance(level, (list, tuple)) and len(level) >= 2:
            price = float(level[0])
            size = float(level[1])
        else:
            continue
        if price > 0 and size > 0:
            normalized.append({"price": price, "size": size})
    return normalized


def _best_bid_ask(orderbook: Dict[str, Any]) -> Optional[Dict[str, float]]:
    bids = _normalize_levels(orderbook.get("bids"))
    asks = _normalize_levels(orderbook.get("asks"))
    if not bids or not asks:
        return None
    best_bid = max(bids, key=lambda x: x["price"])["price"]
    best_ask = min(asks, key=lambda x: x["price"])["price"]
    return {"best_bid": best_bid, "best_ask": best_ask}


def _depth_within_delta(levels: List[Dict[str, float]], mid: float, delta: float, side: str) -> float:
    if side == "bid":
        return sum(l["size"] for l in levels if l["price"] >= (mid - delta))
    return sum(l["size"] for l in levels if l["price"] <= (mid + delta))


def _sample_midpoints(polymarket: Polymarket, token_id: str) -> List[float]:
    midpoints: List[float] = []
    for i in range(MID_SAMPLES):
        try:
            ob = polymarket.get_orderbook(token_id)
            best = _best_bid_ask(ob if isinstance(ob, dict) else ob.__dict__)
            if best:
                midpoints.append((best["best_bid"] + best["best_ask"]) / 2)
        except Exception:
            pass
        if i < MID_SAMPLES - 1:
            time.sleep(MID_SAMPLE_INTERVAL_SEC)
    return midpoints


def _calc_vol_and_jump(midpoints: List[float]) -> Dict[str, float]:
    if len(midpoints) < 2:
        return {"vol": 0.0, "jump_rate": 0.0}
    deltas = [midpoints[i] - midpoints[i - 1] for i in range(1, len(midpoints))]
    mean = sum(deltas) / len(deltas)
    var = sum((d - mean) ** 2 for d in deltas) / len(deltas)
    vol = var ** 0.5
    jump_rate = sum(1 for d in deltas if abs(d) > JUMP_THRESHOLD) / len(deltas)
    return {"vol": vol, "jump_rate": jump_rate}


def coarse_filter_markets(polymarket: Polymarket, markets: List[Any]) -> List[Any]:
    # Step 1：硬过滤（先排掉“天坑/不好做”的）
    # 目标：能稳定挂单、成交频繁、条款不拧巴

    # Step 1.1：必须是活跃市场 & 可程序化拉到元数据（Gamma active=true 的市场池）
    # Step 1.2：时间窗口适中（距截止 >= 24h 且 <= 30~45 天，避免太近/太远）
    # Step 1.3：避开“价格贴边”的市场（0.04/0.96 附近），建议 mid ∈ [0.08, 0.92]
    # Step 1.4：规则必须清晰可判定（标题仅描述，规则决定结算）

    min_hours_to_end = 24
    max_days_to_end = 45
    price_low = 0.08
    price_high = 0.92

    now = datetime.now(timezone.utc)
    filtered: List[Any] = []

    for market in markets:
        # Step 1.1：必须活跃
        if not getattr(market, "active", False):
            continue

        # Step 1.2：时间窗口过滤
        end_time = _parse_end_time(getattr(market, "end", ""))
        if not end_time:
            continue
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        delta = end_time - now
        if delta < timedelta(hours=min_hours_to_end):
            continue
        if delta > timedelta(days=max_days_to_end):
            continue

        # Step 1.3：避开价格贴边（用 outcome_prices 的第一个价格近似 mid）
        mid_price = _extract_mid_price(getattr(market, "outcome_prices", ""))
        if mid_price is None:
            continue
        if not (price_low <= mid_price <= price_high):
            continue

        # Step 1.4：规则清晰性
        description = getattr(market, "description", "") or ""
        if not _rule_is_clear(description):
            continue

        filtered.append(market)

    # Step 2：流动性/稳定性打分（从“看起来能做”到“值得做”）
    # 这里直接调用 CLOB 订单簿数据做快速打分过滤（可调整阈值）
    scored: List[Any] = []

    for market in filtered:
        token_ids = _parse_token_ids(getattr(market, "clob_token_ids", ""))
        if not token_ids:
            continue

        # 2.1(a) Top-of-book 价差（越小越好）
        # 2.1(b) 深度 depth@δ（越厚越好）
        spreads: List[float] = []
        depths: List[float] = []

        for token_id in token_ids:
            try:
                orderbook = polymarket.get_orderbook(token_id)
                ob = orderbook if isinstance(orderbook, dict) else orderbook.__dict__
                best = _best_bid_ask(ob)
                if not best:
                    continue
                best_bid = best["best_bid"]
                best_ask = best["best_ask"]
                spread = best_ask - best_bid
                spreads.append(spread)

                bids = _normalize_levels(ob.get("bids"))
                asks = _normalize_levels(ob.get("asks"))
                mid = (best_bid + best_ask) / 2
                depth = _depth_within_delta(bids, mid, DEPTH_DELTA, "bid") + _depth_within_delta(
                    asks, mid, DEPTH_DELTA, "ask"
                )
                depths.append(depth)
            except Exception:
                continue

        if not spreads or not depths:
            continue

        # 2.1(c) 短窗波动与跳变率（越低越好）
        # 使用第一个 token 做快速采样
        midpoints = _sample_midpoints(polymarket, token_ids[0])
        vol_jump = _calc_vol_and_jump(midpoints)

        max_spread = max(spreads)
        min_depth = min(depths)
        vol = vol_jump["vol"]
        jump_rate = vol_jump["jump_rate"]

        # 过滤条件（可调）
        if max_spread > MAX_SPREAD:
            continue
        if min_depth < MIN_DEPTH:
            continue
        if vol > MAX_VOL:
            continue
        if jump_rate > MAX_JUMP_RATE:
            continue

        scored.append(market)

    return scored


# 拉取 CLOB 订单簿数据
def collect_clob_data(polymarket: Polymarket, markets: List[Any]) -> List[Dict[str, Any]]:
    clob_data: List[Dict[str, Any]] = []
    for market in markets:
        try:
            token_ids = _parse_token_ids(getattr(market, "clob_token_ids", ""))
            for token_id in token_ids:
                try:
                    orderbook = polymarket.get_orderbook(token_id)
                    clob_data.append(
                        {
                            "market_id": getattr(market, "id", None),
                            "token_id": token_id,
                            "orderbook": orderbook,
                        }
                    )
                except Exception as exc:
                    clob_data.append(
                        {
                            "market_id": getattr(market, "id", None),
                            "token_id": token_id,
                            "error": str(exc),
                        }
                    )
        except Exception as exc:
            clob_data.append({"market_id": getattr(market, "id", None), "error": str(exc)})
    return clob_data


# 端到端流程：事件 -> 市场 -> 订单簿 -> 本地落盘
def collect_and_persist(output_dir: str = "local_market_collection") -> Dict[str, str]:
    _ensure_dir(output_dir)
    polymarket = Polymarket()

    events = collect_events(polymarket)
    filtered_events = coarse_filter_events(polymarket, events)

    markets = collect_markets(polymarket)
    filtered_markets = coarse_filter_markets(polymarket, markets)

    clob_data = collect_clob_data(polymarket, filtered_markets)

    ts = _timestamp()
    events_path = os.path.join(output_dir, f"events_{ts}.json")
    filtered_events_path = os.path.join(output_dir, f"events_filtered_{ts}.json")
    markets_path = os.path.join(output_dir, f"markets_{ts}.json")
    filtered_markets_path = os.path.join(output_dir, f"markets_filtered_{ts}.json")
    clob_path = os.path.join(output_dir, f"clob_{ts}.json")

    _write_json(events_path, events)
    _write_json(filtered_events_path, filtered_events)
    _write_json(markets_path, markets)
    _write_json(filtered_markets_path, filtered_markets)
    _write_json(clob_path, clob_data)

    return {
        "events": events_path,
        "events_filtered": filtered_events_path,
        "markets": markets_path,
        "markets_filtered": filtered_markets_path,
        "clob": clob_path,
    }


if __name__ == "__main__":
    paths = collect_and_persist()
    print("Saved:")
    for k, v in paths.items():
        print(f"- {k}: {v}")
