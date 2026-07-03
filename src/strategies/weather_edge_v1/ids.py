from __future__ import annotations

import hashlib
from typing import Any, Mapping


STRATEGY_ID_NAMESPACE = "weather_edge_v1"


class WeatherIdError(ValueError):
    """Raised when a deterministic weather ID cannot be built."""


def _clean(value: Any, field_name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise WeatherIdError(f"missing required ID field: {field_name}")
    return text


def _sha256_joined(parts: list[str]) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def make_signal_id(
    *,
    target_date: Any,
    city: Any,
    bracket: Any,
    signal_side: Any,
    model_version: Any,
    forecast_source: Any,
    snapshot_ts_utc: Any,
    condition_id: Any,
) -> str:
    """Return the producer-neutral v2 signal_id.

    The ID intentionally excludes producer_system so N100 and local pm_agent
    can independently produce the same signal identifier for the same market
    signal.
    """
    side = _clean(signal_side, "signal_side").upper()
    if side not in {"YES", "NO"}:
        raise WeatherIdError("signal_side must be YES or NO")

    return _sha256_joined([
        STRATEGY_ID_NAMESPACE,
        _clean(target_date, "target_date"),
        _clean(city, "city"),
        _clean(bracket, "bracket"),
        side,
        _clean(model_version, "model_version"),
        _clean(forecast_source, "forecast_source"),
        _clean(snapshot_ts_utc, "snapshot_ts_utc"),
        _clean(condition_id, "condition_id"),
    ])


def make_signal_id_from_row(row: Mapping[str, Any]) -> str:
    return make_signal_id(
        target_date=row.get("target_date"),
        city=row.get("city"),
        bracket=row.get("bracket"),
        signal_side=row.get("signal_side"),
        model_version=row.get("model_version"),
        forecast_source=row.get("forecast_source"),
        snapshot_ts_utc=row.get("snapshot_ts_utc"),
        condition_id=row.get("condition_id"),
    )


def make_plan_id(
    *,
    run_id: Any,
    signal_id: Any,
    order_side: Any,
    execution_policy: Any,
) -> str:
    side = _clean(order_side, "order_side").upper()
    if side not in {"BUY_YES", "BUY_NO", "SELL_YES", "SELL_NO"}:
        raise WeatherIdError("order_side must be BUY_YES, BUY_NO, SELL_YES, or SELL_NO")

    return _sha256_joined([
        _clean(run_id, "run_id"),
        _clean(signal_id, "signal_id").lower(),
        side,
        _clean(execution_policy, "execution_policy"),
    ])


def make_plan_id_from_row(row: Mapping[str, Any]) -> str:
    return make_plan_id(
        run_id=row.get("run_id"),
        signal_id=row.get("signal_id"),
        order_side=row.get("order_side"),
        execution_policy=row.get("execution_policy"),
    )


def make_execution_id(
    *,
    run_id: Any,
    plan_id: Any,
    venue: Any,
    attempt_index: Any = 0,
) -> str:
    return _sha256_joined([
        _clean(run_id, "run_id"),
        _clean(plan_id, "plan_id").lower(),
        _clean(venue, "venue"),
        _clean(attempt_index, "attempt_index"),
    ])


def make_execution_id_from_row(row: Mapping[str, Any]) -> str:
    return make_execution_id(
        run_id=row.get("run_id"),
        plan_id=row.get("plan_id"),
        venue=row.get("venue"),
        attempt_index=row.get("attempt_index", 0),
    )


def make_paper_order_id(*, execution_id: Any) -> str:
    return _sha256_joined([
        _clean(execution_id, "execution_id").lower(),
        "paper_fill",
    ])


def make_fill_id(*, execution_id: Any) -> str:
    return _sha256_joined([
        _clean(execution_id, "execution_id").lower(),
        "fill",
    ])


def make_settlement_id(
    *,
    target_date: Any,
    condition_id: Any,
    market_id: Any,
    bracket: Any,
) -> str:
    return _sha256_joined([
        "settlement",
        _clean(target_date, "target_date"),
        _clean(condition_id, "condition_id"),
        _clean(market_id, "market_id"),
        _clean(bracket, "bracket"),
    ])
