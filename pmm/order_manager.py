from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class ManagedOrder:
    token_id: str
    side: str
    price: float
    size: float


class OrderManager:
    """轻量订单管理器：只做 diffing 与 deadband 控制。

    说明：当前工具服务未提供 GET /orders 与撤单接口，
    这里以“本地缓存”方式模拟已挂订单。
    """

    def __init__(self, deadband: float) -> None:
        self.deadband = deadband
        self._orders: Dict[str, ManagedOrder] = {}

    def _key(self, token_id: str, side: str) -> str:
        return f"{token_id}:{side}"

    def get_order(self, token_id: str, side: str) -> Optional[ManagedOrder]:
        return self._orders.get(self._key(token_id, side))

    def should_replace(self, old: ManagedOrder, new_price: float, new_size: float) -> bool:
        price_diff = abs(new_price - old.price)
        size_diff = abs(new_size - old.size)
        if price_diff < self.deadband and size_diff < (old.size * 0.1):
            return False
        return True

    def update_local(self, token_id: str, side: str, price: float, size: float) -> None:
        self._orders[self._key(token_id, side)] = ManagedOrder(
            token_id=token_id, side=side, price=price, size=size
        )
