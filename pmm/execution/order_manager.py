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


@dataclass
class MultiDiffDecision:
    cancel_ids: List[str]
    create_targets: List[Dict[str, Any]]
    kept_order_ids: List[str]
    reason: str


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

    def _side_sort_key(self, side: str):
        side_value = self._normalize_side(side)
        if side_value == "BUY":
            return lambda x: (-x.price, x.order_id)
        return lambda x: (x.price, x.order_id)

    def _pick_nearest_unmatched(
        self,
        pool: List[ManagedOrder],
        target_price: float,
    ) -> Optional[ManagedOrder]:
        if not pool:
            return None
        return min(pool, key=lambda x: abs(x.price - target_price))

    def diff_multi(
        self,
        open_orders: List[Dict[str, Any]],
        token_id: str,
        side: str,
        targets: List[Dict[str, Any]],
    ) -> MultiDiffDecision:
        """
        Multi-level diff for one (token_id, side).

        `targets` item format:
        {
          "price": float,
          "size": float,
          "level": int,
          "target_price": float (optional)
        }
        """
        side_value = self._normalize_side(side)
        same_side_orders = self._filter_orders(open_orders, token_id, side_value)

        valid_targets: List[Dict[str, Any]] = []
        for x in targets:
            px = self._to_float(x.get("price"))
            sz = self._to_float(x.get("size"))
            level = int(x.get("level", 0))
            tpx = self._to_float(x.get("target_price", px))
            if px <= 0:
                continue
            valid_targets.append(
                {
                    "price": px,
                    "size": sz,
                    "level": level,
                    "target_price": tpx,
                }
            )

        valid_targets.sort(
            key=lambda x: (
                x.get("level", 0),
                -x["price"] if side_value == "BUY" else x["price"],
            )
        )
        same_side_orders.sort(key=self._side_sort_key(side_value))

        if not valid_targets:
            return MultiDiffDecision(
                cancel_ids=[x.order_id for x in same_side_orders],
                create_targets=[],
                kept_order_ids=[],
                reason="no_targets",
            )

        unmatched_orders = list(same_side_orders)
        cancel_ids: List[str] = []
        create_targets: List[Dict[str, Any]] = []
        kept_order_ids: List[str] = []

        for t in valid_targets:
            anchor = self._pick_nearest_unmatched(unmatched_orders, t["price"])
            if anchor is None:
                create_targets.append(t)
                continue

            unmatched_orders = [x for x in unmatched_orders if x.order_id != anchor.order_id]
            if self.should_replace(anchor, t["price"], t["size"]):
                cancel_ids.append(anchor.order_id)
                create_targets.append(t)
            else:
                kept_order_ids.append(anchor.order_id)

        if unmatched_orders:
            cancel_ids.extend([x.order_id for x in unmatched_orders])

        # de-dup while preserving order
        seen = set()
        dedup_cancel_ids: List[str] = []
        for cid in cancel_ids:
            if cid not in seen:
                seen.add(cid)
                dedup_cancel_ids.append(cid)

        return MultiDiffDecision(
            cancel_ids=dedup_cancel_ids,
            create_targets=create_targets,
            kept_order_ids=kept_order_ids,
            reason="multi_diff",
        )

    def diff(
        self,
        open_orders: List[Dict[str, Any]],
        token_id: str,
        side: str,
        target_price: float,
        target_size: float,
    ) -> DiffDecision:
        multi = self.diff_multi(
            open_orders=open_orders,
            token_id=token_id,
            side=side,
            targets=[
                {
                    "price": target_price,
                    "size": target_size,
                    "level": 0,
                    "target_price": target_price,
                }
            ],
        )
        matched = multi.kept_order_ids[0] if multi.kept_order_ids else ""
        create = len(multi.create_targets) > 0
        reason = "keep_in_deadband" if (not create and not multi.cancel_ids) else multi.reason
        return DiffDecision(
            cancel_ids=multi.cancel_ids,
            create=create,
            reason=reason,
            matched_order_id=matched,
        )
