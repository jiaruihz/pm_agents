import asyncio
import json
import time
from typing import Any, Dict, List, Optional

import websockets


class LocalOrderBookStore:
    def __init__(self, level_limit: int = 200) -> None:
        self._books: Dict[str, Dict[str, Dict[float, float]]] = {}
        self._updated_at: Dict[str, float] = {}
        self.level_limit = level_limit

    def _ensure(self, token_id: str) -> Dict[str, Dict[float, float]]:
        if token_id not in self._books:
            self._books[token_id] = {"bids": {}, "asks": {}}
        return self._books[token_id]

    def set_snapshot(self, token_id: str, bids: Any, asks: Any) -> None:
        book = self._ensure(token_id)
        book["bids"] = self._normalize_side(bids)
        book["asks"] = self._normalize_side(asks)
        self._updated_at[token_id] = time.time()

    def apply_updates(self, token_id: str, side: str, levels: Any) -> None:
        book = self._ensure(token_id)
        target = book["bids"] if side.lower() == "buy" else book["asks"]
        for price, size in self._normalize_levels(levels):
            if size <= 0:
                target.pop(price, None)
            else:
                target[price] = size
        self._updated_at[token_id] = time.time()

    def get_orderbook(self, token_id: str) -> Dict[str, Any]:
        if token_id not in self._books:
            return {}
        book = self._books[token_id]
        bids = sorted(book["bids"].items(), key=lambda x: x[0], reverse=True)[
            : self.level_limit
        ]
        asks = sorted(book["asks"].items(), key=lambda x: x[0])[: self.level_limit]
        return {
            "bids": [{"price": p, "size": s} for p, s in bids],
            "asks": [{"price": p, "size": s} for p, s in asks],
        }

    def is_stale(self, token_id: str, stale_after_sec: float) -> bool:
        ts = self._updated_at.get(token_id)
        if ts is None:
            return True
        return (time.time() - ts) > stale_after_sec

    def _normalize_levels(self, levels: Any) -> List[tuple[float, float]]:
        out: List[tuple[float, float]] = []
        if not levels:
            return out
        for item in levels:
            if isinstance(item, dict):
                price = self._to_float(item.get("price"))
                size = self._to_float(
                    item.get("size")
                    or item.get("amount")
                    or item.get("quantity")
                    or item.get("qty")
                )
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                price = self._to_float(item[0])
                size = self._to_float(item[1])
            else:
                continue
            if price > 0:
                out.append((price, max(0.0, size)))
        return out

    def _normalize_side(self, levels: Any) -> Dict[float, float]:
        data: Dict[float, float] = {}
        for price, size in self._normalize_levels(levels):
            if size > 0:
                data[price] = size
        return data

    def _to_float(self, value: Any) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0


class MarketWsFeed:
    def __init__(
        self,
        ws_url: str,
        token_ids: List[str],
        detail_level: str = "agg",
        app_ping_interval_sec: float = 20.0,
        reconnect_delay_sec: float = 2.0,
        stale_after_sec: float = 3.0,
        level_limit: int = 200,
    ) -> None:
        self.ws_url = ws_url
        self.token_ids = token_ids
        self.detail_level = detail_level
        self.app_ping_interval_sec = app_ping_interval_sec
        self.reconnect_delay_sec = reconnect_delay_sec
        self.stale_after_sec = stale_after_sec
        self.books = LocalOrderBookStore(level_limit=level_limit)
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass

    def get_orderbook(self, token_id: str) -> Dict[str, Any]:
        return self.books.get_orderbook(token_id)

    def is_stale(self, token_id: str) -> bool:
        return self.books.is_stale(token_id, self.stale_after_sec)

    async def _run_forever(self) -> None:
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(
                    self.ws_url, ping_interval=20, ping_timeout=20
                ) as ws:
                    await self._subscribe(ws)
                    heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))
                    async for raw in ws:
                        if self._stop_event.is_set():
                            break
                        self._handle_raw_message(raw)
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except Exception:
                        pass
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(max(0.2, self.reconnect_delay_sec))

    async def _subscribe(self, ws: Any) -> None:
        payload = {
            "type": "market",
            "assets_ids": self.token_ids,
            "detail_level": self.detail_level,
        }
        await ws.send(json.dumps(payload))

    async def _heartbeat_loop(self, ws: Any) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(max(5.0, self.app_ping_interval_sec))
            try:
                await ws.send("PING")
            except Exception:
                return

    def _handle_raw_message(self, raw: Any) -> None:
        if isinstance(raw, str) and raw.upper() == "PONG":
            return
        try:
            payload = json.loads(raw)
        except Exception:
            return
        if isinstance(payload, list):
            for event in payload:
                self._handle_event(event)
            return
        if isinstance(payload, dict):
            self._handle_event(payload)

    def _handle_event(self, event: Dict[str, Any]) -> None:
        token_id = str(
            event.get("asset_id")
            or event.get("assetId")
            or event.get("token_id")
            or event.get("tokenId")
            or ""
        )
        if not token_id:
            return

        event_type = str(event.get("event_type") or event.get("type") or "").lower()
        bids = event.get("bids") or event.get("buys")
        asks = event.get("asks") or event.get("sells")

        if bids is not None or asks is not None or event_type in {"book", "snapshot"}:
            self.books.set_snapshot(token_id, bids or [], asks or [])
            return

        changes = event.get("changes") or event.get("price_changes") or []
        for ch in changes:
            side = str(ch.get("side") or ch.get("type") or "").lower()
            if side in {"buy", "bid", "bids"}:
                self.books.apply_updates(token_id, "buy", [ch])
            elif side in {"sell", "ask", "asks"}:
                self.books.apply_updates(token_id, "sell", [ch])
