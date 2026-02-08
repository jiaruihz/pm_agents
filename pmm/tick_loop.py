import asyncio
from typing import Dict, List

from pmm.config import PMMConfig
from pmm.http_client import ToolServiceClient
from pmm.orderbook import mid_price
from pmm.order_manager import OrderManager
from pmm.pricing import compute_quotes


def _split_sides(token_id: str) -> List[str]:
    # 简化：每个 token_id 同时挂 BUY/SELL
    return ["BUY", "SELL"]


async def tick_loop(config: PMMConfig) -> None:
    order_mgr = OrderManager(deadband=config.deadband)

    async with ToolServiceClient(config.api_base_url, config.api_key) as client:
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
                    continue
                orderbooks[token_id] = ob

            # 下单逻辑（使用 mid 估计 + spread）
            for token_id, ob in orderbooks.items():
                mid = mid_price(ob)
                if mid <= 0:
                    continue
                quotes = compute_quotes(mid, config.base_spread, inventory=0.0, skew_factor=config.skew_factor)

                # BUY
                old_buy = order_mgr.get_order(token_id, "BUY")
                if old_buy is None or order_mgr.should_replace(old_buy, quotes.bid, config.base_size):
                    await client.retry(client.place_limit_order, token_id, quotes.bid, config.base_size, "BUY")
                    order_mgr.update_local(token_id, "BUY", quotes.bid, config.base_size)

                # SELL
                old_sell = order_mgr.get_order(token_id, "SELL")
                if old_sell is None or order_mgr.should_replace(old_sell, quotes.ask, config.base_size):
                    await client.retry(client.place_limit_order, token_id, quotes.ask, config.base_size, "SELL")
                    order_mgr.update_local(token_id, "SELL", quotes.ask, config.base_size)

            await asyncio.sleep(config.tick_interval_sec)
