"""Event definitions for the core event-driven engine."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from src.platform.engine.models import OrderStatus


@dataclass
class Event:
    """Base class for all system events."""
    timestamp_utc: datetime = field(init=False)
    
    def __post_init__(self):
        self.timestamp_utc = datetime.now(timezone.utc)


@dataclass
class MarketTickEvent(Event):
    """Fired when new market data (price or orderbook) arrives."""
    token_id: str
    mid_price: Optional[float] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    orderbook: Dict[str, Any] = field(default_factory=dict)
    
    # Allows strategies to pass pre-computed indicators along with the tick if desired
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderUpdateEvent(Event):
    """Fired when an order state changes (placed, filled, canceled, error)."""
    instance_id: str
    order_id: str
    token_id: str
    status: OrderStatus
    filled_size: float = 0.0
    average_price: float = 0.0
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AlertEvent(Event):
    """Fired for system-level or strategy-level alerts (e.g. to Telegram)."""
    level: str  # "INFO", "WARNING", "ERROR", "CRITICAL"
    message: str
    instance_id: Optional[str] = None
