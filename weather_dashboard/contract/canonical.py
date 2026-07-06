from __future__ import annotations

from typing import Any, Iterable, Mapping


ALLOWED_PRODUCER_SYSTEMS = {"n100", "pm_agent_local", "legacy_migration"}
ALLOWED_CITY_POOLS = {"t1_trading", "t2_research"}
ALLOWED_SIGNAL_SIDES = {"YES", "NO"}
ALLOWED_ORDER_SIDES = {"BUY_YES", "BUY_NO", "SELL_YES", "SELL_NO"}
ALLOWED_FORECAST_SOURCES = {"open_meteo_live_gfs", "open_meteo_live_ecmwf"}
ALLOWED_EXECUTION_MODES = {"snapshot_replay", "paper", "live"}
ALLOWED_VENUES = {"paper", "snapshot_replay", "polymarket_clob"}
ALLOWED_SETTLEMENT_STATUSES = {"settled", "missing_event", "missing_bracket"}
ALLOWED_FILL_STATUSES = {"filled", "partial", "cancelled", "expired", "simulated"}


SIGNAL_REQUIRED_FIELDS = (
    "signal_id",
    "producer_system",
    "producer_run_id",
    "snapshot_ts_utc",
    "target_date",
    "city",
    "city_pool",
    "icao",
    "bracket",
    "unit",
    "signal_side",
    "model_version",
    "model_p_yes",
    "forecast_source",
    "market_price",
    "edge",
    "abs_edge",
    "condition_id",
    "market_id",
    "hours_to_settle",
)

PLAN_REQUIRED_FIELDS = (
    "plan_id",
    "run_id",
    "signal_id",
    "config_id",
    "order_side",
    "execution_policy",
)

ORDER_REQUIRED_FIELDS = (
    "execution_id",
    "order_id",
    "run_id",
    "plan_id",
    "venue",
    "order_side",
    "entry_price",
    "shares",
    "cost_usd",
    "status",
)

FILL_REQUIRED_FIELDS = (
    "fill_id",
    "execution_id",
    "filled_shares",
    "filled_price",
    "fees_usd",
    "status",
)

SETTLEMENT_REQUIRED_FIELDS = (
    "settlement_id",
    "target_date",
    "bracket",
    "final_price",
    "settlement_status",
)


class CanonicalValidationError(ValueError):
    """Raised when a canonical weather dashboard record is malformed."""


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _require(row: Mapping[str, Any], fields: Iterable[str], record_name: str) -> None:
    missing = [field for field in fields if _is_blank(row.get(field))]
    if missing:
        raise CanonicalValidationError(
            f"{record_name} missing required field(s): {', '.join(missing)}"
        )


def _require_enum(row: Mapping[str, Any], field: str, allowed: set[str], record_name: str) -> None:
    value = str(row.get(field, "")).strip()
    if value not in allowed:
        raise CanonicalValidationError(
            f"{record_name}.{field} must be one of {sorted(allowed)}, got {value!r}"
        )


def _require_float(row: Mapping[str, Any], field: str, record_name: str) -> None:
    try:
        float(row[field])
    except Exception as exc:
        raise CanonicalValidationError(f"{record_name}.{field} must be numeric") from exc


def _require_hex64(row: Mapping[str, Any], field: str, record_name: str) -> None:
    value = str(row.get(field, "")).strip()
    if len(value) != 64:
        raise CanonicalValidationError(f"{record_name}.{field} must be 64 hex chars")
    try:
        int(value, 16)
    except ValueError as exc:
        raise CanonicalValidationError(f"{record_name}.{field} must be 64 hex chars") from exc


def validate_canonical_signal(row: Mapping[str, Any]) -> None:
    _require(row, SIGNAL_REQUIRED_FIELDS, "signal")
    _require_hex64(row, "signal_id", "signal")
    _require_enum(row, "producer_system", ALLOWED_PRODUCER_SYSTEMS, "signal")
    _require_enum(row, "city_pool", ALLOWED_CITY_POOLS, "signal")
    _require_enum(row, "signal_side", ALLOWED_SIGNAL_SIDES, "signal")
    _require_enum(row, "forecast_source", ALLOWED_FORECAST_SOURCES, "signal")
    for field in ("model_p_yes", "market_price", "edge", "abs_edge", "hours_to_settle"):
        _require_float(row, field, "signal")


def validate_canonical_plan(row: Mapping[str, Any]) -> None:
    _require(row, PLAN_REQUIRED_FIELDS, "plan")
    _require_hex64(row, "plan_id", "plan")
    _require_hex64(row, "signal_id", "plan")
    _require_enum(row, "order_side", ALLOWED_ORDER_SIDES, "plan")
    for field in ("notional", "desired_shares", "limit_price"):
        if not _is_blank(row.get(field)):
            _require_float(row, field, "plan")


def validate_canonical_order(row: Mapping[str, Any]) -> None:
    _require(row, ORDER_REQUIRED_FIELDS, "order")
    _require_hex64(row, "execution_id", "order")
    _require_hex64(row, "plan_id", "order")
    _require_enum(row, "venue", ALLOWED_VENUES, "order")
    _require_enum(row, "order_side", ALLOWED_ORDER_SIDES, "order")
    for field in ("entry_price", "shares", "cost_usd"):
        _require_float(row, field, "order")
    for field in (
        "limit_price",
        "notional",
        "quote_tick_size",
        "score_dist_probability",
        "score_dist_multiplier",
        "requested_price",
        "posted_price",
        "posted_notional",
        "best_bid",
        "best_ask",
        "spread",
        "model_p_yes_used",
        "market_implied_p_yes",
        "quote_edge",
        "fee_adjusted_edge",
        "source_order_age_min",
    ):
        if not _is_blank(row.get(field)):
            _require_float(row, field, "order")


def validate_canonical_fill(row: Mapping[str, Any]) -> None:
    _require(row, FILL_REQUIRED_FIELDS, "fill")
    _require_hex64(row, "execution_id", "fill")
    _require_enum(row, "status", ALLOWED_FILL_STATUSES, "fill")
    for field in ("filled_shares", "filled_price", "fees_usd"):
        _require_float(row, field, "fill")


def validate_canonical_settlement(row: Mapping[str, Any]) -> None:
    _require(row, SETTLEMENT_REQUIRED_FIELDS, "settlement")
    _require_hex64(row, "settlement_id", "settlement")
    _require_enum(row, "settlement_status", ALLOWED_SETTLEMENT_STATUSES, "settlement")
    _require_float(row, "final_price", "settlement")
