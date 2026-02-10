import json
import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class ArbPairConfig:
    name: str
    yes_token_id: str
    no_token_id: str
    condition_id: str
    partition: List[int] = field(default_factory=lambda: [1, 2])
    collateral_token: str = ""
    parent_collection_id: str = ""


@dataclass
class ArbConfig:
    api_base_url: str = "http://localhost:8000"
    api_key: str = ""

    # Loop
    tick_interval_sec: float = 1.0
    max_ticks: int = 0
    dry_run: bool = True

    # Market data
    market_data_source: str = "ws"  # ws | rest
    ws_market_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    ws_detail_level: str = "agg"
    ws_app_ping_interval_sec: float = 10.0
    ws_reconnect_delay_sec: float = 2.0
    ws_stale_after_sec: float = 3.0
    ws_level_limit: int = 100

    # Strategy thresholds
    fee_buffer: float = 0.003
    min_expected_profit_usdc: float = 0.20
    gas_estimate_usdc: float = 0.05
    gas_multiplier_guard: float = 2.0
    max_notional_usdc_per_trade: float = 100.0

    # Execution controls
    strict_fok_required: bool = True
    allow_degraded_execution: bool = False

    # Capital recycle
    auto_merge_every_ticks: int = 30
    auto_merge_min_shares: float = 1.0
    ctf_amount_scale: int = 1_000_000

    # Pairs
    pairs: List[ArbPairConfig] = field(default_factory=list)

    @staticmethod
    def from_env() -> "ArbConfig":
        pairs_raw = os.getenv("PM_ARB_PAIRS_JSON", "[]")
        parsed_pairs: List[ArbPairConfig] = []
        try:
            data = json.loads(pairs_raw)
            if isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    yes_token_id = str(item.get("yes_token_id", "")).strip()
                    no_token_id = str(item.get("no_token_id", "")).strip()
                    condition_id = str(item.get("condition_id", "")).strip()
                    name = str(item.get("name", f"{yes_token_id[:6]}_{no_token_id[:6]}"))
                    if not yes_token_id or not no_token_id or not condition_id:
                        continue
                    partition = item.get("partition", [1, 2])
                    if not isinstance(partition, list) or len(partition) < 2:
                        partition = [1, 2]
                    parsed_pairs.append(
                        ArbPairConfig(
                            name=name,
                            yes_token_id=yes_token_id,
                            no_token_id=no_token_id,
                            condition_id=condition_id,
                            partition=[int(x) for x in partition if int(x) > 0],
                            collateral_token=str(item.get("collateral_token", "")).strip(),
                            parent_collection_id=str(item.get("parent_collection_id", "")).strip(),
                        )
                    )
        except Exception:
            parsed_pairs = []

        return ArbConfig(
            api_base_url=os.getenv("PM_ARB_API_BASE_URL", "http://localhost:8000"),
            api_key=os.getenv("PM_ARB_API_KEY", ""),
            tick_interval_sec=float(os.getenv("PM_ARB_TICK_INTERVAL_SEC", "1.0")),
            max_ticks=int(os.getenv("PM_ARB_MAX_TICKS", "0")),
            dry_run=os.getenv("PM_ARB_DRY_RUN", "1") == "1",
            market_data_source=os.getenv("PM_ARB_MARKET_DATA_SOURCE", "ws"),
            ws_market_url=os.getenv(
                "PM_ARB_WS_MARKET_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/market"
            ),
            ws_detail_level=os.getenv("PM_ARB_WS_DETAIL_LEVEL", "agg"),
            ws_app_ping_interval_sec=float(os.getenv("PM_ARB_WS_APP_PING_INTERVAL_SEC", "10")),
            ws_reconnect_delay_sec=float(os.getenv("PM_ARB_WS_RECONNECT_DELAY_SEC", "2")),
            ws_stale_after_sec=float(os.getenv("PM_ARB_WS_STALE_AFTER_SEC", "3")),
            ws_level_limit=int(os.getenv("PM_ARB_WS_LEVEL_LIMIT", "100")),
            fee_buffer=float(os.getenv("PM_ARB_FEE_BUFFER", "0.003")),
            min_expected_profit_usdc=float(os.getenv("PM_ARB_MIN_EXPECTED_PROFIT_USDC", "0.20")),
            gas_estimate_usdc=float(os.getenv("PM_ARB_GAS_ESTIMATE_USDC", "0.05")),
            gas_multiplier_guard=float(os.getenv("PM_ARB_GAS_MULTIPLIER_GUARD", "2.0")),
            max_notional_usdc_per_trade=float(os.getenv("PM_ARB_MAX_NOTIONAL_USDC_PER_TRADE", "100")),
            strict_fok_required=os.getenv("PM_ARB_STRICT_FOK_REQUIRED", "1") == "1",
            allow_degraded_execution=os.getenv("PM_ARB_ALLOW_DEGRADED_EXECUTION", "0") == "1",
            auto_merge_every_ticks=int(os.getenv("PM_ARB_AUTO_MERGE_EVERY_TICKS", "30")),
            auto_merge_min_shares=float(os.getenv("PM_ARB_AUTO_MERGE_MIN_SHARES", "1.0")),
            ctf_amount_scale=int(os.getenv("PM_ARB_CTF_AMOUNT_SCALE", "1000000")),
            pairs=parsed_pairs,
        )
