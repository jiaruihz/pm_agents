import hashlib

import pytest

from src.strategies.weather_edge_v1.ids import (
    WeatherIdError,
    make_execution_id,
    make_fill_id,
    make_paper_order_id,
    make_plan_id,
    make_signal_id,
    make_signal_id_from_row,
    make_settlement_id,
)


def test_make_signal_id_is_producer_neutral_and_deterministic():
    row = {
        "target_date": "2026-05-17",
        "city": "LA",
        "bracket": "68-69",
        "signal_side": "NO",
        "model_version": "gfs",
        "forecast_source": "open_meteo_live_gfs",
        "snapshot_ts_utc": "2026-05-17T04:00:53Z",
        "condition_id": "0x9abc74973998194008feb80c40fca8ea21bed8e6878f8801395a3aeee6295c14",
    }

    expected = hashlib.sha256(
        (
            "weather_edge_v1|2026-05-17|LA|68-69|NO|gfs|"
            "open_meteo_live_gfs|2026-05-17T04:00:53Z|"
            "0x9abc74973998194008feb80c40fca8ea21bed8e6878f8801395a3aeee6295c14"
        ).encode("utf-8")
    ).hexdigest()

    assert make_signal_id_from_row({**row, "producer_system": "n100"}) == expected
    assert make_signal_id_from_row({**row, "producer_system": "pm_agent_local"}) == expected
    assert len(expected) == 64


def test_make_signal_id_requires_canonical_signal_side():
    with pytest.raises(WeatherIdError, match="signal_side"):
        make_signal_id(
            target_date="2026-05-17",
            city="LA",
            bracket="68-69",
            signal_side="BUY_NO",
            model_version="gfs",
            forecast_source="open_meteo_live_gfs",
            snapshot_ts_utc="2026-05-17T04:00:53Z",
            condition_id="0xabc",
        )


def test_plan_execution_and_paper_order_ids_are_stable():
    signal_id = "a" * 64
    plan_id = make_plan_id(
        run_id="run-20260517",
        signal_id=signal_id,
        order_side="BUY_NO",
        execution_policy="mid_price_core_v1",
    )
    execution_id = make_execution_id(
        run_id="run-20260517",
        plan_id=plan_id,
        venue="paper",
        attempt_index=0,
    )
    paper_order_id = make_paper_order_id(execution_id=execution_id)
    fill_id = make_fill_id(execution_id=execution_id)
    settlement_id = make_settlement_id(
        target_date="2026-05-17",
        condition_id="0x9abc",
        market_id="2266022",
        bracket="68-69",
    )

    assert plan_id == make_plan_id(
        run_id="run-20260517",
        signal_id=signal_id,
        order_side="BUY_NO",
        execution_policy="mid_price_core_v1",
    )
    assert execution_id == make_execution_id(
        run_id="run-20260517",
        plan_id=plan_id,
        venue="paper",
        attempt_index=0,
    )
    assert len(plan_id) == 64
    assert len(execution_id) == 64
    assert len(paper_order_id) == 64
    assert len(fill_id) == 64
    assert len(settlement_id) == 64


def test_make_plan_id_rejects_ambiguous_live_buy_side():
    with pytest.raises(WeatherIdError, match="order_side"):
        make_plan_id(
            run_id="run-1",
            signal_id="a" * 64,
            order_side="BUY",
            execution_policy="mid_price_core_v1",
        )
