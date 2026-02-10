from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ManagedOrder:
    order_id: str
    token_id: str
    side: str
    price: float
    size: float
    raw: Dict[str, Any]


@dataclass
class DiffDecision:
    cancel_ids: List[str]
    create: bool
    reason: str
    matched_order_id: str = ""


class OrderManager:
    """Diffing + deadband manager on top of remote open orders."""

    def __init__(self, deadband: float) -> None:
        self.deadband = deadband

    def should_replace(self, old: ManagedOrder, new_price: float, new_size: float) -> bool:
        price_diff = abs(new_price - old.price)
        size_diff = abs(new_size - old.size)
        if price_diff < self.deadband and size_diff < max(0.01, old.size * 0.1):
            return False
        return True

    def _normalize_side(self, side: Any) -> str:
        value = str(side).upper()
        if value in {"0", "BUY"}:
            return "BUY"
        if value in {"1", "SELL"}:
            return "SELL"
        return value

    def _to_float(self, value: Any) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0

    def _parse_order(self, raw: Dict[str, Any]) -> Optional[ManagedOrder]:
        order_id = str(raw.get("id") or raw.get("orderID") or raw.get("order_id") or "")
        token_id = str(raw.get("asset_id") or raw.get("assetId") or raw.get("token_id") or "")
        side = self._normalize_side(raw.get("side", ""))
        price = self._to_float(raw.get("price", 0))
        size = self._to_float(
            raw.get("size")
            or raw.get("original_size")
            or raw.get("remaining_size")
            or raw.get("amount")
            or 0
        )
        if not order_id or not token_id or side not in {"BUY", "SELL"}:
            return None
        return ManagedOrder(
            order_id=order_id,
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            raw=raw,
        )

    def _filter_orders(
        self, open_orders: List[Dict[str, Any]], token_id: str, side: str
    ) -> List[ManagedOrder]:
        items: List[ManagedOrder] = []
        for raw in open_orders:
            parsed = self._parse_order(raw)
            if not parsed:
                continue
            if parsed.token_id == token_id and parsed.side == side:
                items.append(parsed)
        return items

    def side_order_ids(
        self, open_orders: List[Dict[str, Any]], token_id: str, side: str
    ) -> List[str]:
        return [x.order_id for x in self._filter_orders(open_orders, token_id, side)]

    def _pick_anchor(self, orders: List[ManagedOrder], target_price: float) -> Optional[ManagedOrder]:
        if not orders:
            return None
        return min(orders, key=lambda x: abs(x.price - target_price))

    def diff(
        self,
        open_orders: List[Dict[str, Any]],
        token_id: str,
        side: str,
        target_price: float,
        target_size: float,
    ) -> DiffDecision:
        same_side_orders = self._filter_orders(open_orders, token_id, side)
        if not same_side_orders:
            return DiffDecision(cancel_ids=[], create=True, reason="no_order")

        anchor = self._pick_anchor(same_side_orders, target_price)
        if not anchor:
            return DiffDecision(cancel_ids=[], create=True, reason="no_anchor")

        cancel_ids = [x.order_id for x in same_side_orders if x.order_id != anchor.order_id]
        replace_anchor = self.should_replace(anchor, target_price, target_size)
        if replace_anchor:
            cancel_ids.append(anchor.order_id)
            return DiffDecision(
                cancel_ids=cancel_ids,
                create=True,
                reason="replace",
                matched_order_id=anchor.order_id,
            )

        return DiffDecision(
            cancel_ids=cancel_ids,
            create=False,
            reason="keep_in_deadband",
            matched_order_id=anchor.order_id,
        )
