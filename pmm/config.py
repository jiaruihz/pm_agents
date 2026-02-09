import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class MarketConfig:
    """Per-market configuration.

    token_ids: list of CLOB token ids for outcomes (e.g. [YES, NO]).
    """

    token_ids: List[str]
    symbol: str = "PMM"


@dataclass
class PMMConfig:
    """Global PMM config with sane defaults."""

    api_base_url: str = "http://localhost:8000"
    api_key: str = ""
    tick_interval_sec: float = 2.0
    max_ticks: int = 0
    dry_run: bool = False

    # Pricing / inventory
    base_spread: float = 0.02
    deadband: float = 0.005
    skew_factor: float = 0.0
    base_size: float = 5.0

    # Depth / risk
    max_position: float = 100.0

    # Market selection (optional text filter)
    market_query: str = ""

    # Market config (manual token ids)
    market: MarketConfig = field(default_factory=lambda: MarketConfig(token_ids=[]))

    @staticmethod
    def from_env() -> "PMMConfig":
        token_ids = os.getenv("PMM_TOKEN_IDS", "").split(",")
        token_ids = [t.strip() for t in token_ids if t.strip()]
        return PMMConfig(
            api_base_url=os.getenv("PMM_API_BASE_URL", "http://localhost:8000"),
            api_key=os.getenv("PMM_API_KEY", ""),
            tick_interval_sec=float(os.getenv("PMM_TICK_INTERVAL_SEC", "2")),
            max_ticks=int(os.getenv("PMM_MAX_TICKS", "0")),
            dry_run=os.getenv("PMM_DRY_RUN", "0") == "1",
            base_spread=float(os.getenv("PMM_BASE_SPREAD", "0.02")),
            deadband=float(os.getenv("PMM_DEADBAND", "0.005")),
            skew_factor=float(os.getenv("PMM_SKEW_FACTOR", "0.0")),
            base_size=float(os.getenv("PMM_BASE_SIZE", "5")),
            max_position=float(os.getenv("PMM_MAX_POSITION", "100")),
            market_query=os.getenv("PMM_MARKET_QUERY", ""),
            market=MarketConfig(token_ids=token_ids, symbol=os.getenv("PMM_SYMBOL", "PMM")),
        )
