"""Feeder that consumes from the MarketWsFeed and triggers MarketTickEvents."""

import asyncio
import logging
from typing import List, Dict, Any, Optional

from src.platform.market_data.market_ws import MarketWsFeed
from src.platform.engine.events import MarketTickEvent, AlertEvent
from src.platform.engine.dispatcher import EventDispatcher

logger = logging.getLogger(__name__)


class MarketDataFeeder:
    """
    Bridges the existing WebSocket feed with the central EventDispatcher.
    It periodically polls the local orderbook store maintained by MarketWsFeed
    and fires MarketTickEvents when prices change.
    """

    def __init__(
        self,
        dispatcher: EventDispatcher,
        ws_url: str,
        token_ids: List[str],
        poll_interval_sec: float = 1.0,
    ) -> None:
        self.dispatcher = dispatcher
        self.token_ids = token_ids
        self.poll_interval_sec = poll_interval_sec
        self.ws_feed = MarketWsFeed(ws_url=ws_url, token_ids=token_ids)
        self._is_running = False
        self._poll_task: Optional[asyncio.Task[Any]] = None
        self._last_event_ts: Dict[str, float] = {}

    async def start(self) -> None:
        """Start the underlying WS feed and the polling loop."""
        if self._is_running:
            return
        
        self._is_running = True
        logger.info(f"Starting MarketDataFeeder for {len(self.token_ids)} tokens.")
        await self.ws_feed.start()
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop polling and close the WS feed."""
        self._is_running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        await self.ws_feed.stop()
        logger.info("MarketDataFeeder stopped.")

    async def _poll_loop(self) -> None:
        """Continuously check for updates in the WS orderbook and dispatch events."""
        while self._is_running:
            try:
                for token_id in self.token_ids:
                    # Check if there is a new event since last tick
                    current_ts = self.ws_feed.get_event_ts(token_id)
                    last_ts = self._last_event_ts.get(token_id, 0.0)
                    
                    if current_ts > last_ts:
                        self._last_event_ts[token_id] = current_ts
                        book = self.ws_feed.get_orderbook(token_id)
                        
                        if book:
                            bids = book.get("bids", [])
                            asks = book.get("asks", [])
                            best_bid = bids[0]["price"] if bids else None
                            best_ask = asks[0]["price"] if asks else None
                            mid_price = None
                            
                            if best_bid is not None and best_ask is not None:
                                mid_price = (best_bid + best_ask) / 2.0
                                
                            event = MarketTickEvent(
                                token_id=token_id,
                                mid_price=mid_price,
                                best_bid=best_bid,
                                best_ask=best_ask,
                                orderbook=book
                            )
                            self.dispatcher.publish(event)

                await asyncio.sleep(self.poll_interval_sec)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in MarketDataFeeder polling loop: {e}", exc_info=True)
                self.dispatcher.publish(
                    AlertEvent(level="ERROR", message=f"Feeder poll error: {e}")
                )
                await asyncio.sleep(self.poll_interval_sec * 2)
