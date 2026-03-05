import os
import json
import warnings
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
    execution_mode: str = "live"  # live | paper
    strategy_key: str = "single_level_v1"
    strategy_params: dict = field(default_factory=dict)

    # Paper trading
    paper_initial_usdc: float = 1000.0
    paper_initial_positions: dict = field(default_factory=dict)
    paper_fill_model: str = "conservative"  # conservative | optimistic
    paper_fill_epsilon: float = 0.001
    paper_queue_share: float = 0.25
    paper_maker_fee_bps: float = 0.0
    paper_taker_fee_bps: float = 0.0
    paper_min_fill_age_ticks: int = 1
    paper_cancel_delay_ticks: int = 0
    paper_require_trade_flow_for_at_bbo: bool = True
    paper_disable_at_bbo_in_conservative: bool = False
    paper_conservative_bbo_share_multiplier: float = 0.35
    paper_bootstrap_split_usdc: float = 0.0
    inflight_order_ttl_sec: float = 10.0

    # Market data source
    market_data_source: str = "rest"  # rest | ws
    ws_market_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    ws_detail_level: str = "agg"
    ws_app_ping_interval_sec: float = 10.0
    ws_reconnect_delay_sec: float = 2.0
    ws_stale_after_sec: float = 3.0
    ws_level_limit: int = 200

    # Pricing / inventory
    base_spread: float = 0.04
    deadband: float = 0.01
    skew_factor: float = 0.05
    join_epsilon: float = 0.001
    min_edge: float = 0.002
    price_tick: float = 0.001
    price_tick_mode: str = "nearest"  # nearest | floor | ceil
    inventory_sigmoid_k: float = 4.0
    mid_price_mode: str = "weighted"  # midpoint | weighted
    base_size: float = 5.0
    min_size: float = 1.0
    enforce_inventory_for_sell: bool = False
    # Quote ladder configuration. Used by strategy_key=multi_level_v1.
    # For strategy_key=single_level_v1, only one level is used.
    quote_levels: int = 1
    level_spread_step: float = 0.005
    level_size_decay: float = 0.6
    multi_level_quote_enabled: bool = False

    # Alpha / adverse-selection protection
    alpha_enabled: bool = True
    alpha_reference_token_ids: List[str] = field(default_factory=list)
    alpha_window_sec: int = 30
    alpha_min_points: int = 5
    alpha_ref_momentum_threshold: float = 0.03
    alpha_ofi_enabled: bool = True
    alpha_ofi_delta: float = 0.01
    alpha_ofi_imbalance_threshold: float = 0.60

    # Depth / risk
    max_position: float = 100.0
    guard_max_order_value: float = 100.0
    guard_max_buy_order_value: float = 100.0
    guard_max_sell_order_value: float = 100.0
    guard_max_long_position: float = 100.0
    guard_max_short_position: float = 100.0
    guard_max_daily_loss: float = 50.0
    guard_price_floor: float = 0.0001
    guard_price_ceiling: float = 0.9999
    circuit_breaker_enabled: bool = True
    circuit_breaker_window_sec: int = 60
    circuit_breaker_threshold: float = 0.10
    circuit_breaker_min_points: int = 5
    circuit_breaker_halt_on_trigger: bool = True

    # Profitability guard
    min_profitability_spread: float = 0.03
    fee_spread_floor: float = 0.002
    target_profit_spread: float = 0.002
    volatility_spread_coeff: float = 2.0
    inventory_risk_spread_coeff: float = 0.01

    # Capital efficiency
    auto_merge_enabled: bool = False
    auto_merge_every_ticks: int = 30
    auto_merge_default_min_amount: int = 1_000_000
    auto_merge_plans: list = field(default_factory=list)
    merge_pending_credit_enabled: bool = True
    merge_pending_credit_ttl_sec: int = 20
    merge_pending_credit_ratio: float = 1.0
    merge_amount_scale: int = 1_000_000

    # Market selection (optional text filter)
    market_query: str = ""

    # Market config (manual token ids)
    market: MarketConfig = field(default_factory=lambda: MarketConfig(token_ids=[]))
    metrics_path: str = "src/strategies/pmm/backtest/.artifacts/logs/metrics.jsonl"
    instance_id: str = ""
    instance_label: str = ""
    strategy_runtime_db_path: str = "runtime/strategy_runtime.db"
    # Deprecated alias, kept for compatibility with old call sites.
    instance_db_path: str = "runtime/strategy_runtime.db"
    instance_heartbeat_sec: int = 10
    instance_snapshot_interval_sec: int = 60

    # External notification (Telegram)
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_api_base_url: str = "https://api.telegram.org"
    telegram_report_interval_sec: int = 1800
    telegram_alert_cooldown_sec: int = 120
    telegram_send_startup: bool = True

    def effective_quote_levels(self) -> int:
        requested = max(1, int(self.quote_levels))
        if self.strategy_key == "multi_level_v1":
            return requested
        if self.multi_level_quote_enabled:
            # Backward compatibility: allow explicit multi-level experiments
            # even before switching strategy_key.
            return requested
        return 1

    def quote_runtime_meta(self) -> dict:
        requested = max(1, int(self.quote_levels))
        effective = self.effective_quote_levels()
        multi_active = self.strategy_key == "multi_level_v1"
        return {
            "quote_levels_requested": requested,
            "quote_levels_effective": effective,
            "multi_level_quote_enabled": multi_active,
            "multi_level_placeholder_active": (requested > 1 and not multi_active),
            "level_spread_step": float(self.level_spread_step),
            "level_size_decay": float(self.level_size_decay),
        }

    @staticmethod
    def from_env() -> "PMMConfig":
        token_ids = os.getenv("PMM_TOKEN_IDS", "").split(",")
        token_ids = [t.strip() for t in token_ids if t.strip()]
        alpha_reference_token_ids = os.getenv("PMM_ALPHA_REFERENCE_TOKEN_IDS", "").split(",")
        alpha_reference_token_ids = [
            t.strip() for t in alpha_reference_token_ids if t.strip()
        ]
        merge_plans_raw = os.getenv("PMM_MERGE_PLANS_JSON", "[]")
        paper_positions_raw = os.getenv("PMM_PAPER_INITIAL_POSITIONS_JSON", "{}")
        strategy_params_raw = os.getenv("PMM_STRATEGY_PARAMS_JSON", "{}")
        try:
            auto_merge_plans = json.loads(merge_plans_raw)
            if not isinstance(auto_merge_plans, list):
                auto_merge_plans = []
        except Exception:
            auto_merge_plans = []
        try:
            paper_initial_positions = json.loads(paper_positions_raw)
            if not isinstance(paper_initial_positions, dict):
                paper_initial_positions = {}
        except Exception:
            paper_initial_positions = {}
        try:
            strategy_params = json.loads(strategy_params_raw)
            if not isinstance(strategy_params, dict):
                strategy_params = {}
        except Exception:
            strategy_params = {}
        legacy_instance_db_path = os.getenv("PMM_INSTANCE_DB_PATH", "").strip()
        strategy_runtime_db_path = (
            os.getenv("STRATEGY_RUNTIME_DB_PATH", "").strip()
            or legacy_instance_db_path
            or "runtime/strategy_runtime.db"
        )
        if legacy_instance_db_path and not os.getenv("STRATEGY_RUNTIME_DB_PATH", "").strip():
            warnings.warn(
                "PMM_INSTANCE_DB_PATH is deprecated; please use STRATEGY_RUNTIME_DB_PATH instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        return PMMConfig(
            api_base_url=os.getenv("PM_API_BASE_URL", "http://localhost:8000"),
            api_key=os.getenv("PM_API_KEY", ""),
            tick_interval_sec=float(os.getenv("PMM_TICK_INTERVAL_SEC", "2")),
            max_ticks=int(os.getenv("PMM_MAX_TICKS", "0")),
            dry_run=os.getenv("PMM_DRY_RUN", "0") == "1",
            execution_mode=os.getenv("PMM_EXECUTION_MODE", "live"),
            strategy_key=os.getenv("PMM_STRATEGY_KEY", "single_level_v1"),
            strategy_params=strategy_params,
            paper_initial_usdc=float(os.getenv("PMM_PAPER_INITIAL_USDC", "1000")),
            paper_initial_positions=paper_initial_positions,
            paper_fill_model=os.getenv("PMM_PAPER_FILL_MODEL", "conservative"),
            paper_fill_epsilon=float(os.getenv("PMM_PAPER_FILL_EPSILON", "0.001")),
            paper_queue_share=float(os.getenv("PMM_PAPER_QUEUE_SHARE", "0.25")),
            paper_maker_fee_bps=float(os.getenv("PMM_PAPER_MAKER_FEE_BPS", "0")),
            paper_taker_fee_bps=float(os.getenv("PMM_PAPER_TAKER_FEE_BPS", "0")),
            paper_min_fill_age_ticks=int(os.getenv("PMM_PAPER_MIN_FILL_AGE_TICKS", "1")),
            paper_cancel_delay_ticks=int(os.getenv("PMM_PAPER_CANCEL_DELAY_TICKS", "0")),
            paper_require_trade_flow_for_at_bbo=os.getenv(
                "PMM_PAPER_REQUIRE_TRADE_FLOW_BBO", "1"
            )
            == "1",
            paper_disable_at_bbo_in_conservative=os.getenv(
                "PMM_PAPER_DISABLE_AT_BBO_CONSERVATIVE", "0"
            )
            == "1",
            paper_conservative_bbo_share_multiplier=float(
                os.getenv("PMM_PAPER_CONSERVATIVE_BBO_SHARE_MULTIPLIER", "0.35")
            ),
            paper_bootstrap_split_usdc=float(
                os.getenv("PMM_PAPER_BOOTSTRAP_SPLIT_USDC", "0")
            ),
            inflight_order_ttl_sec=float(os.getenv("PMM_INFLIGHT_ORDER_TTL_SEC", "10")),
            market_data_source=os.getenv("PMM_MARKET_DATA_SOURCE", "rest"),
            ws_market_url=os.getenv(
                "PMM_WS_MARKET_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/market"
            ),
            ws_detail_level=os.getenv("PMM_WS_DETAIL_LEVEL", "agg"),
            ws_app_ping_interval_sec=float(
                os.getenv("PMM_WS_APP_PING_INTERVAL_SEC", "10")
            ),
            ws_reconnect_delay_sec=float(os.getenv("PMM_WS_RECONNECT_DELAY_SEC", "2")),
            ws_stale_after_sec=float(os.getenv("PMM_WS_STALE_AFTER_SEC", "3")),
            ws_level_limit=int(os.getenv("PMM_WS_LEVEL_LIMIT", "200")),
            base_spread=float(os.getenv("PMM_BASE_SPREAD", "0.04")),
            deadband=float(os.getenv("PMM_DEADBAND", "0.01")),
            skew_factor=float(os.getenv("PMM_SKEW_FACTOR", "0.05")),
            join_epsilon=float(os.getenv("PMM_JOIN_EPSILON", "0.001")),
            min_edge=float(os.getenv("PMM_MIN_EDGE", "0.002")),
            price_tick=float(os.getenv("PMM_PRICE_TICK", "0.001")),
            price_tick_mode=os.getenv("PMM_PRICE_TICK_MODE", "nearest"),
            inventory_sigmoid_k=float(os.getenv("PMM_INVENTORY_SIGMOID_K", "4.0")),
            mid_price_mode=os.getenv("PMM_MID_PRICE_MODE", "weighted"),
            base_size=float(os.getenv("PMM_BASE_SIZE", "5")),
            min_size=float(os.getenv("PMM_MIN_SIZE", "1")),
            enforce_inventory_for_sell=os.getenv("PMM_ENFORCE_INV_SELL", "0") == "1",
            quote_levels=int(os.getenv("PMM_QUOTE_LEVELS", "1")),
            level_spread_step=float(os.getenv("PMM_LEVEL_SPREAD_STEP", "0.005")),
            level_size_decay=float(os.getenv("PMM_LEVEL_SIZE_DECAY", "0.6")),
            multi_level_quote_enabled=os.getenv("PMM_MULTI_LEVEL_ENABLED", "0") == "1",
            alpha_enabled=os.getenv("PMM_ALPHA_ENABLED", "1") == "1",
            alpha_reference_token_ids=alpha_reference_token_ids,
            alpha_window_sec=int(os.getenv("PMM_ALPHA_WINDOW_SEC", "30")),
            alpha_min_points=int(os.getenv("PMM_ALPHA_MIN_POINTS", "5")),
            alpha_ref_momentum_threshold=float(
                os.getenv("PMM_ALPHA_REF_MOMENTUM_THRESHOLD", "0.03")
            ),
            alpha_ofi_enabled=os.getenv("PMM_ALPHA_OFI_ENABLED", "1") == "1",
            alpha_ofi_delta=float(os.getenv("PMM_ALPHA_OFI_DELTA", "0.01")),
            alpha_ofi_imbalance_threshold=float(
                os.getenv("PMM_ALPHA_OFI_IMBALANCE_THRESHOLD", "0.60")
            ),
            max_position=float(os.getenv("PMM_MAX_POSITION", "100")),
            guard_max_order_value=float(
                os.getenv("PMM_GUARD_MAX_ORDER_VALUE", os.getenv("PMM_MAX_POSITION", "100"))
            ),
            guard_max_buy_order_value=float(
                os.getenv(
                    "PMM_GUARD_MAX_BUY_ORDER_VALUE",
                    os.getenv("PMM_GUARD_MAX_ORDER_VALUE", os.getenv("PMM_MAX_POSITION", "100")),
                )
            ),
            guard_max_sell_order_value=float(
                os.getenv(
                    "PMM_GUARD_MAX_SELL_ORDER_VALUE",
                    os.getenv("PMM_GUARD_MAX_ORDER_VALUE", os.getenv("PMM_MAX_POSITION", "100")),
                )
            ),
            guard_max_long_position=float(
                os.getenv("PMM_GUARD_MAX_LONG_POSITION", os.getenv("PMM_MAX_POSITION", "100"))
            ),
            guard_max_short_position=float(
                os.getenv("PMM_GUARD_MAX_SHORT_POSITION", os.getenv("PMM_MAX_POSITION", "100"))
            ),
            guard_max_daily_loss=float(os.getenv("PMM_GUARD_MAX_DAILY_LOSS", "50")),
            guard_price_floor=float(os.getenv("PMM_GUARD_PRICE_FLOOR", "0.0001")),
            guard_price_ceiling=float(os.getenv("PMM_GUARD_PRICE_CEILING", "0.9999")),
            circuit_breaker_enabled=os.getenv("PMM_CB_ENABLED", "1") == "1",
            circuit_breaker_window_sec=int(os.getenv("PMM_CB_WINDOW_SEC", "60")),
            circuit_breaker_threshold=float(os.getenv("PMM_CB_THRESHOLD", "0.10")),
            circuit_breaker_min_points=int(os.getenv("PMM_CB_MIN_POINTS", "5")),
            circuit_breaker_halt_on_trigger=os.getenv("PMM_CB_HALT", "1") == "1",
            min_profitability_spread=float(os.getenv("PMM_MIN_PROFITABILITY_SPREAD", "0.03")),
            fee_spread_floor=float(os.getenv("PMM_FEE_SPREAD_FLOOR", "0.002")),
            target_profit_spread=float(os.getenv("PMM_TARGET_PROFIT_SPREAD", "0.002")),
            volatility_spread_coeff=float(os.getenv("PMM_VOLATILITY_SPREAD_COEFF", "2.0")),
            inventory_risk_spread_coeff=float(
                os.getenv("PMM_INVENTORY_RISK_SPREAD_COEFF", "0.01")
            ),
            auto_merge_enabled=os.getenv("PMM_AUTO_MERGE_ENABLED", "0") == "1",
            auto_merge_every_ticks=int(os.getenv("PMM_AUTO_MERGE_EVERY_TICKS", "30")),
            auto_merge_default_min_amount=int(
                os.getenv("PMM_AUTO_MERGE_DEFAULT_MIN_AMOUNT", "1000000")
            ),
            auto_merge_plans=auto_merge_plans,
            merge_pending_credit_enabled=os.getenv("PMM_MERGE_PENDING_CREDIT_ENABLED", "1")
            == "1",
            merge_pending_credit_ttl_sec=int(
                os.getenv("PMM_MERGE_PENDING_CREDIT_TTL_SEC", "20")
            ),
            merge_pending_credit_ratio=float(
                os.getenv("PMM_MERGE_PENDING_CREDIT_RATIO", "1.0")
            ),
            merge_amount_scale=int(os.getenv("PMM_MERGE_AMOUNT_SCALE", "1000000")),
            market_query=os.getenv("PMM_MARKET_QUERY", ""),
            metrics_path=os.getenv("PMM_METRICS_PATH", "src/strategies/pmm/backtest/.artifacts/logs/metrics.jsonl"),
            instance_id=os.getenv("PMM_INSTANCE_ID", "").strip(),
            instance_label=os.getenv("PMM_INSTANCE_LABEL", "").strip(),
            strategy_runtime_db_path=strategy_runtime_db_path,
            instance_db_path=strategy_runtime_db_path,
            instance_heartbeat_sec=max(
                1,
                int(os.getenv("PMM_INSTANCE_HEARTBEAT_SEC", "10")),
            ),
            instance_snapshot_interval_sec=max(
                1,
                int(os.getenv("PMM_INSTANCE_SNAPSHOT_INTERVAL_SEC", "60")),
            ),
            telegram_enabled=os.getenv("PMM_TELEGRAM_ENABLED", "0") == "1",
            telegram_bot_token=os.getenv("PMM_TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("PMM_TELEGRAM_CHAT_ID", ""),
            telegram_api_base_url=os.getenv("PMM_TELEGRAM_API_BASE_URL", "https://api.telegram.org"),
            telegram_report_interval_sec=int(
                os.getenv("PMM_TELEGRAM_REPORT_INTERVAL_SEC", "1800")
            ),
            telegram_alert_cooldown_sec=int(
                os.getenv("PMM_TELEGRAM_ALERT_COOLDOWN_SEC", "120")
            ),
            telegram_send_startup=os.getenv("PMM_TELEGRAM_SEND_STARTUP", "1") == "1",
            market=MarketConfig(token_ids=token_ids, symbol=os.getenv("PMM_SYMBOL", "PMM")),
        )
