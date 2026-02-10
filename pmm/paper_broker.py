import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class PaperOrder:
    order_id: str
    token_id: str
    side: str
    price: float
    size: float
    remaining_size: float
    created_at: float

    def as_open_order(self) -> Dict[str, Any]:
        return {
            "id": self.order_id,
            "asset_id": self.token_id,
            "side": self.side,
            "price": self.price,
            "size": self.remaining_size,
            "original_size": self.size,
            "remaining_size": self.remaining_size,
            "status": "OPEN",
            "created_at": self.created_at,
        }


class PaperBroker:
    """Local paper-trading broker.

    Real market data comes from live orderbook feed (WS/REST), while execution,
    balances and positions are maintained in local memory.
    """

    def __init__(
        self,
        token_ids: List[str],
        initial_usdc: float = 1000.0,
        initial_positions: Optional[Dict[str, float]] = None,
        fill_model: str = "conservative",
        fill_epsilon: float = 0.001,
        queue_share: float = 0.25,
    ) -> None:
        self.token_ids = list(token_ids)
        self.fill_model = fill_model.lower()
        if self.fill_model not in {"conservative", "optimistic"}:
            self.fill_model = "conservative"

        self.fill_epsilon = max(0.0, float(fill_epsilon))
        self.queue_share = max(0.01, min(1.0, float(queue_share)))

        self._cash_total = max(0.0, float(initial_usdc))
        self._cash_free = self._cash_total

        initial_positions = initial_positions or {}
        self._positions_total: Dict[str, float] = {
            token_id: float(initial_positions.get(token_id, 0.0)) for token_id in self.token_ids
        }
        for token_id, value in initial_positions.items():
            if token_id not in self._positions_total:
                self._positions_total[token_id] = float(value)
        self._positions_free: Dict[str, float] = dict(self._positions_total)

        self._orders: Dict[str, PaperOrder] = {}
        self._recent_fills: List[Dict[str, Any]] = []
        self._seq = 0

    async def get_balance(self) -> Dict[str, Any]:
        return {
            "address": "paper",
            "usdc_balance": self._cash_free,
            "usdc_total": self._cash_total,
        }

    async def get_orders(self, token_id: str = "") -> List[Dict[str, Any]]:
        items = [x for x in self._orders.values() if x.remaining_size > 0]
        if token_id:
            items = [x for x in items if x.token_id == token_id]
        return [x.as_open_order() for x in items]

    async def get_positions(self, token_ids: List[str]) -> Dict[str, float]:
        return {token_id: float(self._positions_total.get(token_id, 0.0)) for token_id in token_ids}

    async def place_limit_order(self, token_id: str, price: float, size: float, side: str) -> Dict[str, Any]:
        token = str(token_id)
        px = max(0.0001, min(0.9999, float(price)))
        qty = max(0.0, float(size))
        s = str(side).upper()
        if qty <= 0:
            raise ValueError("size must be > 0")
        if s not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")

        if s == "BUY":
            reserve = px * qty
            if reserve > self._cash_free + 1e-9:
                raise ValueError("insufficient paper USDC")
            self._cash_free -= reserve
        else:
            free_pos = self._positions_free.get(token, 0.0)
            if qty > free_pos + 1e-9:
                raise ValueError("insufficient paper position")
            self._positions_free[token] = free_pos - qty

        self._seq += 1
        order_id = f"paper_{self._seq}"
        order = PaperOrder(
            order_id=order_id,
            token_id=token,
            side=s,
            price=px,
            size=qty,
            remaining_size=qty,
            created_at=time.time(),
        )
        self._orders[order_id] = order
        return {
            "status": "accepted",
            "id": order_id,
            "token_id": token,
            "side": s,
            "price": px,
            "size": qty,
        }

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        oid = str(order_id)
        order = self._orders.pop(oid, None)
        if order:
            self._release_reserve(order, order.remaining_size)
        return {"canceled": [oid]}

    async def cancel_orders(self, order_ids: List[str]) -> Dict[str, Any]:
        canceled: List[str] = []
        for order_id in order_ids:
            oid = str(order_id)
            order = self._orders.pop(oid, None)
            if order:
                self._release_reserve(order, order.remaining_size)
                canceled.append(oid)
        return {"canceled": canceled}

    async def cancel_all_orders(self) -> Dict[str, Any]:
        ids = list(self._orders.keys())
        for order in list(self._orders.values()):
            self._release_reserve(order, order.remaining_size)
        self._orders.clear()
        return {"canceled": ids}

    async def merge_positions(
        self,
        condition_id: str,
        partition: List[int],
        amount: int,
        collateral_token: str = "",
        parent_collection_id: str = "",
    ) -> Dict[str, Any]:
        # No-op for paper mode unless merge_pair() is used with concrete token ids.
        return {
            "status": "paper_noop",
            "condition_id": condition_id,
            "partition": partition,
            "amount": amount,
            "collateral_token": collateral_token,
            "parent_collection_id": parent_collection_id,
        }

    async def merge_pair(self, yes_token_id: str, no_token_id: str, amount: int) -> Dict[str, Any]:
        amt = max(0, int(amount))
        yes_total = self._positions_total.get(yes_token_id, 0.0)
        no_total = self._positions_total.get(no_token_id, 0.0)
        yes_free = self._positions_free.get(yes_token_id, 0.0)
        no_free = self._positions_free.get(no_token_id, 0.0)
        usable = int(min(yes_total, no_total, yes_free, no_free))
        merged = min(amt, usable)
        if merged <= 0:
            return {
                "status": "skipped",
                "merged": 0,
                "reason": "insufficient_pair_position",
            }

        self._positions_total[yes_token_id] = yes_total - merged
        self._positions_total[no_token_id] = no_total - merged
        self._positions_free[yes_token_id] = yes_free - merged
        self._positions_free[no_token_id] = no_free - merged
        self._cash_total += float(merged)
        self._cash_free += float(merged)
        return {
            "status": "ok",
            "mode": "paper",
            "merged": merged,
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
        }

    async def split_pair(self, yes_token_id: str, no_token_id: str, amount_usdc: float) -> Dict[str, Any]:
        """Paper-mode split: USDC collateral -> YES + NO inventory.

        In real CTF, splitting `amount` collateral mints `amount` YES shares and `amount` NO shares.
        For paper mode we model 1 USDC collateral -> +1 YES share and +1 NO share.
        """
        amt = max(0.0, float(amount_usdc))
        if amt <= 0:
            return {"status": "skipped", "reason": "amount<=0", "split_usdc": 0.0}

        if amt > self._cash_free + 1e-9:
            raise ValueError("insufficient paper USDC for split")

        # Consume USDC collateral.
        self._cash_free -= amt
        self._cash_total = max(0.0, self._cash_total - amt)

        # Mint YES/NO shares (paper model).
        for token_id in (str(yes_token_id), str(no_token_id)):
            self._positions_total[token_id] = float(self._positions_total.get(token_id, 0.0)) + amt
            self._positions_free[token_id] = float(self._positions_free.get(token_id, 0.0)) + amt

        return {
            "status": "ok",
            "mode": "paper",
            "split_usdc": amt,
            "yes_token_id": str(yes_token_id),
            "no_token_id": str(no_token_id),
        }

    async def on_market_data(self, orderbooks: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        fills: List[Dict[str, Any]] = []
        for order in list(self._orders.values()):
            if order.remaining_size <= 0:
                self._orders.pop(order.order_id, None)
                continue
            orderbook = orderbooks.get(order.token_id, {})
            top = self._best_bid_ask_with_size(orderbook)
            fill_qty = self._match_qty(order, top)
            if fill_qty <= 0:
                continue
            fill_price = order.price
            self._apply_fill(order, fill_qty)
            fills.append(
                {
                    "order_id": order.order_id,
                    "token_id": order.token_id,
                    "side": order.side,
                    "price": fill_price,
                    "size": fill_qty,
                    "remaining_size": order.remaining_size,
                    "ts": time.time(),
                }
            )
            if order.remaining_size <= 1e-9:
                self._orders.pop(order.order_id, None)

        if fills:
            self._recent_fills.extend(fills)
        return fills

    def pop_recent_fills(self) -> List[Dict[str, Any]]:
        items = list(self._recent_fills)
        self._recent_fills.clear()
        return items

    def _best_bid_ask_with_size(self, orderbook: Dict[str, Any]) -> Dict[str, float]:
        bids = orderbook.get("bids") or []
        asks = orderbook.get("asks") or []
        best_bid = 0.0
        best_bid_size = 0.0
        best_ask = 0.0
        best_ask_size = 0.0

        if bids:
            px, sz = self._normalize_level(bids[0])
            best_bid, best_bid_size = px, sz
        if asks:
            px, sz = self._normalize_level(asks[0])
            best_ask, best_ask_size = px, sz

        return {
            "best_bid": best_bid,
            "best_bid_size": best_bid_size,
            "best_ask": best_ask,
            "best_ask_size": best_ask_size,
        }

    def _normalize_level(self, level: Any) -> tuple[float, float]:
        if isinstance(level, dict):
            price = self._to_float(level.get("price"))
            size = self._to_float(level.get("size") or level.get("amount") or level.get("quantity"))
            return price, size
        if isinstance(level, (list, tuple)) and len(level) >= 2:
            return self._to_float(level[0]), self._to_float(level[1])
        return 0.0, 0.0

    def _to_float(self, value: Any) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0

    def _match_qty(self, order: PaperOrder, top: Dict[str, float]) -> float:
        best_bid = top.get("best_bid", 0.0)
        best_ask = top.get("best_ask", 0.0)
        bid_size = max(0.0, top.get("best_bid_size", 0.0))
        ask_size = max(0.0, top.get("best_ask_size", 0.0))

        if order.side == "BUY":
            if best_ask <= 0:
                return 0.0
            if self.fill_model == "conservative":
                touched = best_ask <= (order.price - self.fill_epsilon)
            else:
                touched = best_ask <= (order.price + self.fill_epsilon)
            if not touched:
                return 0.0
            marketable = ask_size if ask_size > 0 else order.remaining_size
        else:
            if best_bid <= 0:
                return 0.0
            if self.fill_model == "conservative":
                touched = best_bid >= (order.price + self.fill_epsilon)
            else:
                touched = best_bid >= (order.price - self.fill_epsilon)
            if not touched:
                return 0.0
            marketable = bid_size if bid_size > 0 else order.remaining_size

        max_fill = max(0.0, marketable * self.queue_share)
        if max_fill <= 0:
            return 0.0
        return min(order.remaining_size, max_fill)

    def _apply_fill(self, order: PaperOrder, qty: float) -> None:
        fill_qty = max(0.0, min(order.remaining_size, qty))
        if fill_qty <= 0:
            return

        if order.side == "BUY":
            cost = order.price * fill_qty
            self._cash_total -= cost
            self._positions_total[order.token_id] = self._positions_total.get(order.token_id, 0.0) + fill_qty
            self._positions_free[order.token_id] = self._positions_free.get(order.token_id, 0.0) + fill_qty
        else:
            revenue = order.price * fill_qty
            self._cash_total += revenue
            self._cash_free += revenue
            self._positions_total[order.token_id] = self._positions_total.get(order.token_id, 0.0) - fill_qty

        order.remaining_size -= fill_qty
        if order.remaining_size < 1e-9:
            self._release_reserve(order, max(0.0, order.remaining_size))
            order.remaining_size = 0.0

    def _release_reserve(self, order: PaperOrder, remaining: float) -> None:
        qty = max(0.0, float(remaining))
        if qty <= 0:
            return
        if order.side == "BUY":
            self._cash_free += order.price * qty
        else:
            self._positions_free[order.token_id] = self._positions_free.get(order.token_id, 0.0) + qty
