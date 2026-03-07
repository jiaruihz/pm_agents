"""Domain models for the core trading engine."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    FOK = "FOK"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    ERROR = "ERROR"
    REJECTED = "REJECTED"


@dataclass
class OrderCommand:
    """A command issued by a strategy to place an order."""
    instance_id: str
    token_id: str
    side: OrderSide
    size: float
    price: float
    strategy_key: str = ""
    order_type: OrderType = OrderType.LIMIT
    strategy_order_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CancelCommand:
    """A command issued by a strategy to cancel an existing order."""
    instance_id: str
    order_id: str
