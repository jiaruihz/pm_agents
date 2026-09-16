import pytest

from weather_dashboard.contract import (
    CanonicalValidationError,
    validate_canonical_order,
    validate_canonical_plan,
    validate_canonical_settlement,
    validate_canonical_signal,
)


def _signal_row():
    return {
        "signal_id": "a" * 64,
        "producer_system": "pm_agent_local",
        "producer_run_id": "snapshot_20260517_1200",
        "snapshot_ts_utc": "2026-05-17T04:00:53Z",
        "snapshot_file": "snapshot_20260517_1200.json",
        "target_date": "2026-05-17",
        "city": "LA",
        "city_pool": "t1_trading",
        "icao": "KLAX",
        "bracket": "68-69",
        "unit": "F",
        "signal_side": "NO",
        "model_version": "gfs",
        "model_p_yes": "0.3633",
        "forecast_source": "open_meteo_live_gfs",
        "market_price": "0.485",
        "edge": "0.1517",
        "abs_edge": "0.1517",
        "condition_id": "0x9abc",
        "market_id": "2266022",
        "token_id": "238644",
        "hours_to_settle": "12.5",
    }


def test_validate_canonical_signal_accepts_complete_row():
    validate_canonical_signal(_signal_row())


def test_validate_canonical_signal_rejects_legacy_field_shape():
    row = _signal_row()
    row.pop("target_date")
    row["event_date"] = "2026-05-17"

    with pytest.raises(CanonicalValidationError, match="target_date"):
        validate_canonical_signal(row)


def test_validate_canonical_signal_rejects_buy_no_signal_side():
    row = {**_signal_row(), "signal_side": "BUY_NO"}

    with pytest.raises(CanonicalValidationError, match="signal_side"):
        validate_canonical_signal(row)


def test_validate_canonical_signal_rejects_profile_forecast_alias():
    row = _signal_row()
    row.pop("forecast_source")
    row["profile"] = "open_meteo_live_gfs"

    with pytest.raises(CanonicalValidationError, match="forecast_source"):
        validate_canonical_signal(row)


def test_validate_canonical_plan_order_and_settlement():
    plan = {
        "plan_id": "b" * 64,
        "run_id": "run-canonical",
        "signal_id": "a" * 64,
        "config_id": "cfg-canonical",
        "order_side": "BUY_NO",
        "notional": "5.0",
        "desired_shares": "10.309278",
        "sizing_mode": "notional",
        "entry_price_window": "0.25-0.75",
        "execution_policy": "mid_price_core_v1",
        "limit_price": "0.485",
    }
    order = {
        "execution_id": "c" * 64,
        "order_id": "0x19a400",
        "run_id": "run-canonical",
        "plan_id": "b" * 64,
        "venue": "polymarket_clob",
        "order_side": "BUY_NO",
        "limit_price": "0.485",
        "entry_price": "0.45",
        "shares": "10.309278",
        "cost_usd": "4.639175",
        "notional": "5.0",
        "status": "submitted",
        "exchange_response": "{}",
    }
    settlement = {
        "settlement_id": "d" * 64,
        "target_date": "2026-05-17",
        "bracket": "68-69",
        "condition_id": "0x9abc",
        "market_id": "2266022",
        "final_price": "0.0",
        "settlement_status": "settled",
    }

    validate_canonical_plan(plan)
    validate_canonical_order(order)
    validate_canonical_settlement(settlement)


def test_validate_canonical_order_rejects_ambiguous_buy():
    order = {
        "execution_id": "c" * 64,
        "order_id": "ord",
        "run_id": "run-canonical",
        "plan_id": "b" * 64,
        "venue": "polymarket_clob",
        "order_side": "BUY",
        "entry_price": "0.45",
        "shares": "10.0",
        "cost_usd": "4.5",
        "status": "submitted",
    }

    with pytest.raises(CanonicalValidationError, match="order_side"):
        validate_canonical_order(order)
