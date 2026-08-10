from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/analysis/market_structure_edge/research_lmvm_single_yes_repricing_v1.py"
SPEC = importlib.util.spec_from_file_location("lmvm_repricing", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def rung(bracket: str, probability: float, bid: float, ask: float) -> dict:
    return {
        "bracket": bracket,
        "question": bracket,
        "condition_id": f"condition-{bracket}",
        "model_prob": probability,
        "market_prob": (bid + ask) / 2,
        "yes_bid": bid,
        "yes_ask": ask,
        "yes_bid_size": 10.0,
        "yes_ask_size": 10.0,
    }


def test_residual_selector_is_single_rung_and_fee_adjusted() -> None:
    rungs = [
        rung("29", 0.20, 0.17, 0.19),
        rung("30", 0.50, 0.30, 0.34),
        rung("31", 0.30, 0.19, 0.22),
    ]
    selected = module.select_rung(rungs, "residual_argmax")
    assert selected["bracket"] == "30"
    raw_edge = 0.50 - 0.34
    assert raw_edge - module.weather_fee_per_share(0.34) > 0


def test_forecast_update_excludes_left_censored_initial_state() -> None:
    base = {
        "city": "Shanghai",
        "target_date": "2026-08-06",
        "event_slug": "event",
        "forecast_source": "source",
        "forecast_model": "ecmwf",
        "rungs": [rung("30", 1.0, 0.2, 0.3)],
    }
    states = [
        {**base, "snapshot_epoch": 1.0, "snapshot_id": "a", "forecast_state_key": "hash:old"},
        {**base, "snapshot_epoch": 2.0, "snapshot_id": "b", "forecast_state_key": "hash:old"},
        {**base, "snapshot_epoch": 3.0, "snapshot_id": "c", "forecast_state_key": "hash:new"},
    ]
    annotated = module.annotate_forecast_updates(states)
    assert annotated[0]["left_censored_initial_state"] is True
    assert annotated[1]["forecast_state_changed"] is False
    assert annotated[2]["forecast_state_changed"] is True
    assert annotated[2]["forecast_state_seq"] == 2


def test_markout_uses_future_bid_and_both_taker_fees() -> None:
    entry = 1_000.0
    histories = {
        "condition-30": [
            module.Quote(entry, 0.30, 0.34, 10.0, 10.0),
            module.Quote(entry + 5 * 60, 0.39, 0.41, 8.0, 9.0),
        ]
    }
    import pandas as pd

    candidates = pd.DataFrame(
        [
            {
                "condition_id": "condition-30",
                "snapshot_epoch": entry,
                "entry_ask": 0.34,
                "entry_fee_per_share": module.weather_fee_per_share(0.34),
                "entry_ask_size": 10.0,
            }
        ]
    )
    result = module.attach_markouts(candidates, histories).iloc[0]
    expected = (
        0.39
        - module.weather_fee_per_share(0.39)
        - 0.34
        - module.weather_fee_per_share(0.34)
    )
    assert math.isclose(result["h5_net_markout_per_share"], expected)
    assert result["h5_executable_shares"] == 5.0
    assert math.isclose(result["h5_net_pnl_usd"], 5.0 * expected)


def test_horizon_quote_outside_tolerance_is_coverage_gap() -> None:
    quote, gap = module.first_quote_after(
        [module.Quote(30 * 60, 0.4, 0.5, 10.0, 10.0)],
        entry_epoch=0.0,
        horizon_min=5,
    )
    assert quote is None
    assert gap == 25.0


def test_markout_window_records_future_ask_touch_without_claiming_fill() -> None:
    entry = 1_000.0
    histories = {
        "condition-30": [
            module.Quote(entry, 0.20, 0.24, 10.0, 10.0),
            module.Quote(entry + 10 * 60, 0.18, 0.20, 8.0, 9.0),
            module.Quote(entry + 30 * 60, 0.17, 0.19, 7.0, 8.0),
        ]
    }
    candidates = pd.DataFrame(
        [
            {
                "condition_id": "condition-30",
                "snapshot_epoch": entry,
                "entry_bid": 0.20,
                "entry_ask": 0.24,
                "entry_fee_per_share": module.weather_fee_per_share(0.24),
                "entry_ask_size": 10.0,
            }
        ]
    )

    result = module.attach_markouts(candidates, histories).iloc[0]

    assert result["h30_ask"] == pytest.approx(0.19)
    assert result["h30_window_min_ask"] == pytest.approx(0.19)
    assert bool(result["h30_maker_bid_touch"])
    assert result["h30_maker_bid_touch_after_min"] == pytest.approx(10.0)
    assert result["h30_window_quote_count"] == 2


def test_full_ladder_completion_prices_other_rungs_at_touch_epoch() -> None:
    entry = 1_000.0
    histories = {
        "c0": [
            module.Quote(entry, 0.20, 0.30, 10.0, 10.0),
            module.Quote(entry + 30 * 60, 0.18, 0.20, 8.0, 9.0),
        ],
        "c1": [
            module.Quote(entry, 0.30, 0.35, 10.0, 10.0),
            module.Quote(entry + 30 * 60, 0.31, 0.34, 8.0, 9.0),
        ],
        "c2": [
            module.Quote(entry, 0.30, 0.35, 10.0, 10.0),
            module.Quote(entry + 30 * 60, 0.32, 0.34, 8.0, 9.0),
        ],
    }
    rows = pd.DataFrame(
        [
            {
                "forecast_event_id": "event-1",
                "condition_id": condition,
                "snapshot_epoch": entry,
                "entry_bid": bid,
                "entry_ask": ask,
                "h60_maker_bid_touch_after_min": 30.0 if condition == "c0" else math.nan,
            }
            for condition, bid, ask in (
                ("c0", 0.20, 0.30),
                ("c1", 0.30, 0.35),
                ("c2", 0.30, 0.35),
            )
        ]
    )

    result = module.attach_full_ladder_completion(rows, histories)
    first = result.loc[result["condition_id"].eq("c0")].iloc[0]
    expected_cost = (
        0.20
        + 0.34
        + 0.34
        + module.weather_fee_per_share(0.34) * 2
    )
    assert first["touch_completion_cost"] == pytest.approx(expected_cost)
    assert first["touch_completion_margin_1tick_per_hedge_leg"] == pytest.approx(
        1.0 - expected_cost - 0.002
    )
    assert bool(first["touch_completion_5share_executable"])


def test_probability_summary_bootstraps_paired_target_date_delta() -> None:
    rows = pd.DataFrame(
        [
            {"target_date": "2026-08-01", "settlement_status": "settled", "model_brier": 0.30, "market_brier": 0.10, "model_logloss": 0.80, "market_logloss": 0.40},
            {"target_date": "2026-08-01", "settlement_status": "settled", "model_brier": 0.20, "market_brier": 0.10, "model_logloss": 0.70, "market_logloss": 0.40},
            {"target_date": "2026-08-02", "settlement_status": "settled", "model_brier": 0.40, "market_brier": 0.20, "model_logloss": 0.90, "market_logloss": 0.50},
        ]
    )
    summary = module.probability_summary(rows)
    assert summary["target_dates"] == 2
    assert summary["brier_delta_ci_low"] > 0
    assert summary["logloss_delta_ci_low"] > 0
