from __future__ import annotations

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
    created_tick: int
    cancel_requested_at: Optional[float] = None
    cancel_effective_tick: Optional[int] = None

    def as_open_order(self, current_tick: int) -> Dict[str, Any]:
        status = "OPEN"
        if self.cancel_effective_tick is not None and current_tick < self.cancel_effective_tick:
            status = "CANCELING"
        return {
            "id": self.order_id,
            "asset_id": self.token_id,
            "side": self.side,
            "price": self.price,
            "size": self.remaining_size,
            "original_size": self.size,
            "remaining_size": self.remaining_size,
            "status": status,
            "created_at": self.created_at,
        }


class PaperBroker:
    """Local paper-trading broker.

    Real market data comes from live/synthetic orderbooks.
    Execution, balances and positions are maintained in memory.
    """

    def __init__(
        self,
        token_ids: List[str],
        initial_usdc: float = 1000.0,
        initial_positions: Optional[Dict[str, float]] = None,
        fill_model: str = "conservative",
        fill_epsilon: float = 0.001,
        queue_share: float = 0.25,
        maker_fee_bps: float = 0.0,
        taker_fee_bps: float = 0.0,
        min_fill_age_ticks: int = 1,
        cancel_delay_ticks: int = 1,
        require_trade_flow_for_at_bbo: bool = False,
        disable_at_bbo_in_conservative: bool = False,
        conservative_bbo_share_multiplier: float = 1.0,
    ) -> None:
        self.token_ids = list(token_ids)
        self.fill_model = str(fill_model).lower().strip()
        if self.fill_model not in {"conservative", "optimistic"}:
            self.fill_model = "conservative"

        self.fill_epsilon = max(0.0, float(fill_epsilon))
        self.queue_share = max(0.0, min(1.0, float(queue_share)))
        self.maker_fee_bps = max(0.0, float(maker_fee_bps))
        self.taker_fee_bps = max(0.0, float(taker_fee_bps))
        self.min_fill_age_ticks = max(0, int(min_fill_age_ticks))
        self.cancel_delay_ticks = max(0, int(cancel_delay_ticks))
        self.require_trade_flow_for_at_bbo = bool(require_trade_flow_for_at_bbo)
        self.disable_at_bbo_in_conservative = bool(disable_at_bbo_in_conservative)
        self.conservative_bbo_share_multiplier = max(
            0.0, float(conservative_bbo_share_multiplier)
        )

        self._cash_total = max(0.0, float(initial_usdc))
        self._cash_free = self._cash_total

        initial_positions = initial_positions or {}
        self._positions_total: Dict[str, float] = {
            token_id: float(initial_positions.get(token_id, 0.0))
            for token_id in self.token_ids
        }
        for token_id, value in initial_positions.items():
            if token_id not in self._positions_total:
                self._positions_total[token_id] = float(value)
        self._positions_free: Dict[str, float] = dict(self._positions_total)

        self._orders: Dict[str, PaperOrder] = {}
        self._recent_fills: List[Dict[str, Any]] = []
        self._seq = 0
        self._clock_tick = 0

    async def get_balance(self) -> Dict[str, Any]:
        return {
            "address": "paper",
            "usdc_balance": self._cash_free,
            "usdc_total": self._cash_total,
        }

    async def get_orders(self, token_id: str = "") -> List[Dict[str, Any]]:
        items = [
            x
            for x in self._orders.values()
            if x.remaining_size > 0
            and not (
                x.cancel_effective_tick is not None
                and self._clock_tick >= x.cancel_effective_tick
            )
        ]
        if token_id:
            items = [x for x in items if x.token_id == token_id]
        return [x.as_open_order(self._clock_tick) for x in items]

    async def get_positions(self, token_ids: List[str]) -> Dict[str, float]:
        return {
            token_id: float(self._positions_total.get(token_id, 0.0))
            for token_id in token_ids
        }

    async def place_limit_order(
        self, token_id: str, price: float, size: float, side: str
    ) -> Dict[str, Any]:
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
            created_tick=self._clock_tick,
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
        order = self._orders.get(oid)
        if order:
            self._mark_cancel(order)
        return {"canceled": [oid]}

    async def cancel_orders(self, order_ids: List[str]) -> Dict[str, Any]:
        canceled: List[str] = []
        for order_id in order_ids:
            oid = str(order_id)
            order = self._orders.get(oid)
            if order:
                self._mark_cancel(order)
                canceled.append(oid)
        return {"canceled": canceled}

    async def cancel_all_orders(self) -> Dict[str, Any]:
        ids = list(self._orders.keys())
        for order in list(self._orders.values()):
            self._mark_cancel(order)
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

    async def merge_pair(
        self, yes_token_id: str, no_token_id: str, amount: int
    ) -> Dict[str, Any]:
        amt = max(0, int(amount))
        yes_total = self._positions_total.get(yes_token_id, 0.0)
        no_total = self._positions_total.get(no_token_id, 0.0)
        yes_free = self._positions_free.get(yes_token_id, 0.0)
        no_free = self._positions_free.get(no_token_id, 0.0)
        usable = int(min(yes_total, no_total, yes_free, no_free))
        merged = min(amt, usable)
        if merged <= 0:
            return {"status": "skipped", "merged": 0, "reason": "insufficient_pair_position"}

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

    async def split_pair(
        self, yes_token_id: str, no_token_id: str, amount_usdc: float
    ) -> Dict[str, Any]:
        """Paper-mode split: USDC collateral -> YES + NO inventory."""
        amt = max(0.0, float(amount_usdc))
        if amt <= 0:
            return {"status": "skipped", "reason": "amount<=0", "split_usdc": 0.0}

        if amt > self._cash_free + 1e-9:
            raise ValueError("insufficient paper USDC for split")

        self._cash_free -= amt
        self._cash_total = max(0.0, self._cash_total - amt)

        for token_id in (str(yes_token_id), str(no_token_id)):
            self._positions_total[token_id] = (
                float(self._positions_total.get(token_id, 0.0)) + amt
            )
            self._positions_free[token_id] = (
                float(self._positions_free.get(token_id, 0.0)) + amt
            )

        return {
            "status": "ok",
            "mode": "paper",
            "split_usdc": amt,
            "yes_token_id": str(yes_token_id),
            "no_token_id": str(no_token_id),
        }

    async def on_market_data(
        self,
        orderbooks: Dict[str, Dict[str, Any]],
        trade_flow: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        self._clock_tick += 1
        self._flush_effective_cancels()

        fills: List[Dict[str, Any]] = []
        trade_flow = trade_flow or {}
        flow_budget: Dict[str, Dict[str, float]] = {}
        if isinstance(trade_flow, dict):
            for token_id, tf in trade_flow.items():
                if not isinstance(tf, dict):
                    continue
                flow_budget[str(token_id)] = {
                    "buy_taker_qty": max(0.0, self._to_float(tf.get("buy_taker_qty"))),
                    "sell_taker_qty": max(0.0, self._to_float(tf.get("sell_taker_qty"))),
                }
        for order in list(self._orders.values()):
            if order.remaining_size <= 0:
                self._orders.pop(order.order_id, None)
                continue
            if order.cancel_effective_tick is not None and self._clock_tick >= order.cancel_effective_tick:
                self._orders.pop(order.order_id, None)
                self._release_reserve(order, order.remaining_size)
                continue
            # Orders must age at least N ticks before matching.
            if (self._clock_tick - order.created_tick) < self.min_fill_age_ticks:
                continue
            orderbook = orderbooks.get(order.token_id, {})
            top = self._best_bid_ask_with_size(orderbook)
            token_flow = flow_budget.get(order.token_id, {"buy_taker_qty": 0.0, "sell_taker_qty": 0.0})
            fill_qty, match_reason = self._match_qty(order, top, token_flow)
            if fill_qty <= 0:
                continue
            fill_price = order.price
            # Resting-limit strategy flow is maker by construction in this simulator.
            liquidity = "maker"
            fee = self._apply_fill(order, fill_qty, liquidity=liquidity)
            # Consume flow budget so one tick's taker flow is not reused by many resting orders.
            if order.side == "BUY":
                token_flow["sell_taker_qty"] = max(0.0, token_flow.get("sell_taker_qty", 0.0) - fill_qty)
            else:
                token_flow["buy_taker_qty"] = max(0.0, token_flow.get("buy_taker_qty", 0.0) - fill_qty)
            flow_budget[order.token_id] = token_flow
            fills.append(
                {
                    "order_id": order.order_id,
                    "token_id": order.token_id,
                    "side": order.side,
                    "price": fill_price,
                    "size": fill_qty,
                    "remaining_size": order.remaining_size,
                    "liquidity": liquidity,
                    "fee": fee,
                    "match_reason": match_reason,
                    "ts": time.time(),
                }
            )
            if order.remaining_size <= 1e-9:
                self._orders.pop(order.order_id, None)

        if fills:
            self._recent_fills.extend(fills)
        return fills

    def _mark_cancel(self, order: PaperOrder) -> None:
        if order.cancel_effective_tick is not None:
            return
        if self.cancel_delay_ticks <= 0:
            self._orders.pop(order.order_id, None)
            self._release_reserve(order, order.remaining_size)
            return
        order.cancel_requested_at = time.time()
        order.cancel_effective_tick = self._clock_tick + self.cancel_delay_ticks + 1

    def _flush_effective_cancels(self) -> None:
        for order in list(self._orders.values()):
            if order.cancel_effective_tick is None:
                continue
            if self._clock_tick >= order.cancel_effective_tick:
                self._orders.pop(order.order_id, None)
                self._release_reserve(order, order.remaining_size)

    def pop_recent_fills(self) -> List[Dict[str, Any]]:
        items = list(self._recent_fills)
        self._recent_fills.clear()
        return items

    def _best_bid_ask_with_size(self, orderbook: Dict[str, Any]) -> Dict[str, float]:
        bids = orderbook.get("bids") or []
        asks = orderbook.get("asks") or []
        normalized_bids = [self._normalize_level(x) for x in bids]
        normalized_asks = [self._normalize_level(x) for x in asks]
        normalized_bids = [x for x in normalized_bids if x[0] > 0 and x[1] >= 0]
        normalized_asks = [x for x in normalized_asks if x[0] > 0 and x[1] >= 0]

        best_bid = 0.0
        best_bid_size = 0.0
        best_ask = 0.0
        best_ask_size = 0.0
        if normalized_bids:
            best_bid, best_bid_size = max(normalized_bids, key=lambda x: x[0])
        if normalized_asks:
            best_ask, best_ask_size = min(normalized_asks, key=lambda x: x[0])
        return {
            "best_bid": best_bid,
            "best_bid_size": best_bid_size,
            "best_ask": best_ask,
            "best_ask_size": best_ask_size,
        }

    def _normalize_level(self, level: Any) -> tuple[float, float]:
        if isinstance(level, dict):
            price = self._to_float(level.get("price"))
            size = self._to_float(
                level.get("size") or level.get("amount") or level.get("quantity")
            )
            return price, size
        if isinstance(level, (list, tuple)) and len(level) >= 2:
            return self._to_float(level[0]), self._to_float(level[1])
        return 0.0, 0.0

    def _to_float(self, value: Any) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0

    def _match_qty(
        self,
        order: PaperOrder,
        top: Dict[str, float],
        trade_flow: Dict[str, Any],
    ) -> tuple[float, str]:
        best_bid = top.get("best_bid", 0.0)
        best_ask = top.get("best_ask", 0.0)
        bid_size = max(0.0, top.get("best_bid_size", 0.0))
        ask_size = max(0.0, top.get("best_ask_size", 0.0))
        eps = self.fill_epsilon

        buy_taker_qty = max(0.0, self._to_float(trade_flow.get("buy_taker_qty")))
        sell_taker_qty = max(0.0, self._to_float(trade_flow.get("sell_taker_qty")))

        bbo_share_mult = 1.0
        if self.fill_model == "conservative":
            bbo_share_mult = self.conservative_bbo_share_multiplier

        # Float-safe price comparison around tick-sized boundaries.
        cmp_tol = max(1e-9, eps * 1e-3)

        def le(a: float, b: float) -> bool:
            return a <= (b + cmp_tol)

        def ge(a: float, b: float) -> bool:
            return a >= (b - cmp_tol)

        def at(a: float, b: float) -> bool:
            return abs(a - b) <= (eps + cmp_tol)

        def cap_by_queue(x: float) -> float:
            return max(0.0, x * self.queue_share * bbo_share_mult)

        if order.side == "BUY":
            cross_hit = best_ask > 0 and (
                le(best_ask, (order.price - eps))
                if self.fill_model == "conservative"
                else le(best_ask, (order.price + eps))
            )
            if cross_hit:
                marketable = ask_size if ask_size > 0 else order.remaining_size
                return min(order.remaining_size, cap_by_queue(marketable)), "cross"

            at_bbo = best_bid > 0 and at(order.price, best_bid)
            if at_bbo:
                if self.fill_model == "conservative" and self.disable_at_bbo_in_conservative:
                    return 0.0, ""
                if sell_taker_qty > 0:
                    return min(order.remaining_size, cap_by_queue(sell_taker_qty)), "at_bbo_trade_flow"
                if self.require_trade_flow_for_at_bbo:
                    return 0.0, ""
                return min(order.remaining_size, cap_by_queue(bid_size)), "at_bbo_book"
            return 0.0, ""

        cross_hit = best_bid > 0 and (
            ge(best_bid, (order.price + eps))
            if self.fill_model == "conservative"
            else ge(best_bid, (order.price - eps))
        )
        if cross_hit:
            marketable = bid_size if bid_size > 0 else order.remaining_size
            return min(order.remaining_size, cap_by_queue(marketable)), "cross"

        at_bbo = best_ask > 0 and at(order.price, best_ask)
        if at_bbo:
            if self.fill_model == "conservative" and self.disable_at_bbo_in_conservative:
                return 0.0, ""
            if buy_taker_qty > 0:
                return min(order.remaining_size, cap_by_queue(buy_taker_qty)), "at_bbo_trade_flow"
            if self.require_trade_flow_for_at_bbo:
                return 0.0, ""
            return min(order.remaining_size, cap_by_queue(ask_size)), "at_bbo_book"
        return 0.0, ""

    def _apply_fill(self, order: PaperOrder, qty: float, liquidity: str = "maker") -> float:
        fill_qty = max(0.0, min(order.remaining_size, qty))
        if fill_qty <= 0:
            return 0.0

        notional = order.price * fill_qty
        fee_bps = self.taker_fee_bps if str(liquidity).lower() == "taker" else self.maker_fee_bps
        fee = notional * (fee_bps / 10_000.0)

        if order.side == "BUY":
            cost = notional
            self._cash_total -= (cost + fee)
            self._cash_free -= fee
            self._positions_total[order.token_id] = (
                self._positions_total.get(order.token_id, 0.0) + fill_qty
            )
            self._positions_free[order.token_id] = (
                self._positions_free.get(order.token_id, 0.0) + fill_qty
            )
        else:
            revenue = notional
            net_revenue = revenue - fee
            self._cash_total += net_revenue
            self._cash_free += net_revenue
            self._positions_total[order.token_id] = (
                self._positions_total.get(order.token_id, 0.0) - fill_qty
            )

        order.remaining_size -= fill_qty
        if order.remaining_size < 1e-9:
            self._release_reserve(order, 0.0)
            order.remaining_size = 0.0
        return fee

    def _release_reserve(self, order: PaperOrder, remaining: float) -> None:
        qty = max(0.0, float(remaining))
        if qty <= 0:
            return
        if order.side == "BUY":
            self._cash_free += order.price * qty
        else:
            self._positions_free[order.token_id] = (
                self._positions_free.get(order.token_id, 0.0) + qty
            )
