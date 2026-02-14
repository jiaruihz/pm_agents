import asyncio
from typing import Any, Dict, List, Optional

from src.platform.market_data.http_client import ToolServiceClient
from src.platform.market_data.market_ws import MarketWsFeed

from src.domains.arb.config import ArbConfig
from src.domains.arb.mock_market_data import MockMarketDataFeed


class MarketDataManager:
    def __init__(self, config: ArbConfig, token_ids: List[str]) -> None:
        self.config = config
        self.token_ids = token_ids
        self._ws_feed: Optional[MarketWsFeed] = None
        self._mock_feed: Optional[MockMarketDataFeed] = None

    async def start(self) -> None:
        source = self.config.market_data_source.lower()
        if source == "mock":
            self._mock_feed = MockMarketDataFeed(self.config)
            return
        if source != "ws":
            return
        self._ws_feed = MarketWsFeed(
            ws_url=self.config.ws_market_url,
            token_ids=self.token_ids,
            detail_level=self.config.ws_detail_level,
            app_ping_interval_sec=self.config.ws_app_ping_interval_sec,
            reconnect_delay_sec=self.config.ws_reconnect_delay_sec,
            stale_after_sec=self.config.ws_stale_after_sec,
            level_limit=self.config.ws_level_limit,
        )
        await self._ws_feed.start()

    async def stop(self) -> None:
        if self._ws_feed:
            await self._ws_feed.stop()
        self._mock_feed = None

    async def get_orderbooks(self, client: ToolServiceClient) -> Dict[str, Dict[str, Any]]:
        results: Dict[str, Dict[str, Any]] = {}

        if self._mock_feed:
            return self._mock_feed.next_orderbooks()

        if self._ws_feed:
            missing: List[str] = []
            for token_id in self.token_ids:
                ob = self._ws_feed.get_orderbook(token_id)
                if ob and not self._ws_feed.is_stale(token_id):
                    results[token_id] = ob
                else:
                    missing.append(token_id)
            if missing:
                fallback = await asyncio.gather(
                    *[client.retry(client.get_orderbook, tid) for tid in missing],
                    return_exceptions=True,
                )
                for token_id, ob in zip(missing, fallback):
                    if isinstance(ob, dict):
                        results[token_id] = ob
                        self._ws_feed.books.set_snapshot(
                            token_id,
                            bids=ob.get("bids", []),
                            asks=ob.get("asks", []),
                        )
                    else:
                        results[token_id] = {}
            return results

        fallback = await asyncio.gather(
            *[client.retry(client.get_orderbook, tid) for tid in self.token_ids],
            return_exceptions=True,
        )
        for token_id, ob in zip(self.token_ids, fallback):
            results[token_id] = ob if isinstance(ob, dict) else {}
        return results
