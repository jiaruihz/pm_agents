import asyncio
import ast
from typing import Dict, List, Optional, Any

from pmm.config import PMMConfig
from pmm.http_client import ToolServiceClient
from pmm.orderbook import mid_price
from pmm.order_manager import OrderManager
from pmm.pricing import compute_quotes


def _split_sides(token_id: str) -> List[str]:
    # 简化：每个 token_id 同时挂 BUY/SELL
    return ["BUY", "SELL"]

def _mid_from_market(market: Dict[str, Any]) -> Optional[float]:
    raw = market.get("outcome_prices")
    if raw is None:
        return None
    try:
        if isinstance(raw, list):
            return float(raw[0])
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, list) and parsed:
            return float(parsed[0])
    except Exception:
        return None
    return None


async def tick_loop(config: PMMConfig) -> None:
    order_mgr = OrderManager(deadband=config.deadband)

    async with ToolServiceClient(config.api_base_url, config.api_key) as client:
        tick_count = 0
        while True:
            if not config.market.token_ids:
                print("PMM_TOKEN_IDS 未设置，无法运行。")
                return

            # 拉取 orderbook（并发）
            orderbooks: Dict[str, Dict] = {}
            tasks = [client.retry(client.get_orderbook, tid) for tid in config.market.token_ids]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for token_id, ob in zip(config.market.token_ids, results):
                if isinstance(ob, Exception):
                    orderbooks[token_id] = {}
                else:
                    orderbooks[token_id] = ob

            # 下单逻辑（使用 mid 估计 + spread）
            for token_id, ob in orderbooks.items():
                mid = mid_price(ob) if ob else 0.0
                if mid <= 0:
                    # fallback：用 Gamma market 的 outcome_prices 估 mid
                    try:
                        market = await client.retry(client.get_market, token_id)
                        mid = _mid_from_market(market) or 0.0
                        if mid > 0:
                            print(f"[FALLBACK] mid from market outcome_prices: {mid:.4f}")
                    except Exception:
                        mid = 0.0
                if mid <= 0:
                    continue
                quotes = compute_quotes(mid, config.base_spread, inventory=0.0, skew_factor=config.skew_factor)

                # BUY
                old_buy = order_mgr.get_order(token_id, "BUY")
                if old_buy is None or order_mgr.should_replace(old_buy, quotes.bid, config.base_size):
                    if config.dry_run:
                        print(f"[DRY_RUN] PLACE BUY {token_id} price={quotes.bid:.4f} size={config.base_size}")
                    else:
                        await client.retry(client.place_limit_order, token_id, quotes.bid, config.base_size, "BUY")
                    order_mgr.update_local(token_id, "BUY", quotes.bid, config.base_size)

                # SELL
                old_sell = order_mgr.get_order(token_id, "SELL")
                if old_sell is None or order_mgr.should_replace(old_sell, quotes.ask, config.base_size):
                    if config.dry_run:
                        print(f"[DRY_RUN] PLACE SELL {token_id} price={quotes.ask:.4f} size={config.base_size}")
                    else:
                        await client.retry(client.place_limit_order, token_id, quotes.ask, config.base_size, "SELL")
                    order_mgr.update_local(token_id, "SELL", quotes.ask, config.base_size)

            tick_count += 1
            if config.max_ticks > 0 and tick_count >= config.max_ticks:
                return

            await asyncio.sleep(config.tick_interval_sec)
