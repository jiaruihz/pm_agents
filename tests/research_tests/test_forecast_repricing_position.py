from __future__ import annotations

import pandas as pd
from pathlib import Path
import pytest
from sklearn.dummy import DummyRegressor
from sklearn.dummy import DummyClassifier
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_model_evaluation import forecast_repricing_position as subject


def _event_rows() -> pd.DataFrame:
    rows = []
    for index, bracket in enumerate(("19 or below", "20", "21+")):
        rows.append(
            {
                "forecast_event_id": "event-1",
                "city": "London",
                "target_date": "2026-08-08",
                "condition_id": f"c{index}",
                "bracket": bracket,
                "lead_days": 1,
                "snapshot_epoch": 1.0,
                "model_probability_before": (0.2, 0.5, 0.3)[index],
                "model_probability_after": (0.1, 0.4, 0.5)[index],
                "market_probability_before": (0.25, 0.5, 0.25)[index],
                "market_probability_after": (0.20, 0.45, 0.35)[index],
                "entry_bid": (0.19, 0.44, 0.34)[index],
                "entry_ask": (0.21, 0.46, 0.36)[index],
                "entry_bid_size": 10.0,
                "entry_ask_size": 11.0,
                "h30_bid": (0.18, 0.43, 0.39)[index],
                "h60_bid": (0.17, 0.41, 0.44)[index],
            }
        )
    return pd.DataFrame(rows)


def test_full_ladder_features_are_relative_and_neighbor_aware() -> None:
    frame = subject.add_full_ladder_position_features(_event_rows())
    center = frame.loc[frame["condition_id"].eq("c1")].iloc[0]
    assert center["signed_mode_distance"] == pytest.approx(0.0)
    assert center["neighbor_propagation"] == pytest.approx(0.075)
    assert center["h60_common_bid_move"] == pytest.approx(-0.02)
    assert center["h60_relative_bid_move"] == pytest.approx(-0.01)
    assert frame["shock_x_mode_x_neighbor_propagation"].notna().sum() == 3


def test_fixed_signal_replay_keeps_identity_and_reports_strict_pit_slice() -> None:
    rows = _event_rows()
    rows["h30_ask"] = rows["h30_bid"] + 0.02
    rows["h60_ask"] = rows["h60_bid"] + 0.02
    rows["h60_window_min_ask"] = rows["h60_ask"]
    rows["h30_quote_event_time_pit_scorable"] = True
    rows["h60_quote_event_time_pit_scorable"] = True
    fixed = rows.loc[rows["condition_id"].eq("c1"), list(subject.IDENTITY_COLUMNS)]

    replay, summary = subject.replay_fixed_signal_execution(
        rows, fixed, _dummy_bundle(0.03, -0.01), draws=100
    )

    assert len(replay) == 1
    assert replay.iloc[0]["condition_id"] == "c1"
    assert replay.iloc[0]["position_action_at_30m"] == "EXIT"
    assert summary["fixed_signal_rows"] == 1
    assert summary["matched_signal_rows"] == 1
    assert summary["strict_pit_dynamic_rows"] == 1


def _dummy_bundle(entry_value: float, continuation_value: float) -> dict:
    entry = DummyRegressor(strategy="constant", constant=entry_value).fit(
        pd.DataFrame([[0.0] * len(subject.INTERACTION_FEATURES)]), [entry_value]
    )
    continuation = DummyRegressor(
        strategy="constant", constant=continuation_value
    ).fit(
        pd.DataFrame([[0.0] * len(subject.CONTINUATION_FEATURES)]),
        [continuation_value],
    )
    return {
        "schema_version": subject.SCHEMA_VERSION,
        "model_id": subject.MODEL_ID,
        "entry_features": list(subject.INTERACTION_FEATURES),
        "continuation_features": list(subject.CONTINUATION_FEATURES),
        "entry_model": entry,
        "continuation_model": continuation,
        "entry_threshold_net_value": 0.0,
        "continuation_threshold_net_value": 0.0,
        "continuation_checkpoint_min": 30,
        "hard_exit_min": 60,
    }


def _antitoxic_dummy_bundle(value: float = 0.02) -> dict:
    bundle = _dummy_bundle(0.03, 0.01)
    feature_count = len(subject.MICROSTRUCTURE_FEATURES)
    features = pd.DataFrame([[0.0] * feature_count, [1.0] * feature_count])
    touch = DummyClassifier(strategy="constant", constant=1).fit(features, [0, 1])
    touch_value = DummyRegressor(strategy="constant", constant=value).fit(
        features, [value, value]
    )
    bundle.update(
        {
            "touch_model": touch,
            "touch_value_model": touch_value,
            "touch_features": list(subject.MICROSTRUCTURE_FEATURES),
            "touch_probability_min": 0.5,
            "touch_conditional_pnl_min": 0.0,
            "maker_quote_ttl_min": 60,
        }
    )
    return bundle


def test_runtime_policy_selects_maker_then_exits_from_full_ladder_state() -> None:
    source = _event_rows()
    paired = []
    for row in source.to_dict(orient="records"):
        paired.append(
            {
                **row,
                "yes_bid": row["entry_bid"],
                "yes_ask": row["entry_ask"],
                "yes_bid_size": row["entry_bid_size"],
                "yes_ask_size": row["entry_ask_size"],
            }
        )
    bundle = _dummy_bundle(0.03, -0.01)
    scored, selected = subject.score_runtime_entry(
        paired,
        bundle,
        event_identity={
            "forecast_event_id": "runtime-event",
            "city": "London",
            "target_date": "2026-08-08",
            "lead_days": 1,
            "snapshot_epoch": 1.0,
        },
    )
    assert selected is not None
    assert selected["predicted_entry_net_value"] > 0.0
    current = [
        {
            "condition_id": row["condition_id"],
            "yes_bid": row["h30_bid"],
            "yes_ask": row["h30_bid"] + 0.02,
        }
        for row in source.to_dict(orient="records")
    ]
    decision = subject.score_runtime_position(
        {
            "condition_id": selected["condition_id"],
            "entry_ladder": scored,
        },
        current,
        bundle,
        elapsed_minutes=30.0,
    )
    assert decision.action == "EXIT"
    assert decision.reason == "continuation_exhausted"
    assert decision.predicted_incremental_exit_value == pytest.approx(-0.01)


def test_runtime_antitoxic_gate_and_pending_order_recheck() -> None:
    source = _event_rows()
    paired = [
        {
            **row,
            "yes_bid": row["entry_bid"],
            "yes_ask": row["entry_ask"],
            "yes_bid_size": row["entry_bid_size"],
            "yes_ask_size": row["entry_ask_size"],
        }
        for row in source.to_dict(orient="records")
    ]
    bundle = _antitoxic_dummy_bundle()
    scored, selected = subject.score_runtime_entry(
        paired,
        bundle,
        event_identity={
            "forecast_event_id": "runtime-event",
            "city": "London",
            "target_date": "2026-08-08",
            "lead_days": 1,
            "snapshot_epoch": 1.0,
        },
    )
    assert selected is not None
    assert selected["predicted_touch_probability"] == pytest.approx(1.0)
    pending = {
        "condition_id": selected["condition_id"],
        "decision_epoch": 1.0,
        "maker_limit_price": selected["entry_bid"],
        "entry_ladder": scored,
    }
    current = [
        {
            "condition_id": row["condition_id"],
            "yes_bid": row["entry_bid"],
            "yes_ask": row["entry_ask"],
            "yes_bid_size": row["entry_bid_size"],
            "yes_ask_size": row["entry_ask_size"],
        }
        for row in source.to_dict(orient="records")
    ]
    decision = subject.score_runtime_pending_order(
        pending, current, bundle, elapsed_minutes=10.0
    )
    assert decision.action == "KEEP_MAKER"
    expired = subject.score_runtime_pending_order(
        pending, current, bundle, elapsed_minutes=60.0
    )
    assert expired.action == "CANCEL_MAKER"


def test_runtime_completion_policy_locks_full_ladder_after_fill() -> None:
    source = _event_rows()
    source.loc[:, "entry_bid"] = (0.20, 0.25, 0.25)
    source.loc[:, "entry_ask"] = (0.30, 0.30, 0.30)
    source.loc[:, "entry_ask_size"] = 10.0
    paired = [
        {
            **row,
            "yes_bid": row["entry_bid"],
            "yes_ask": row["entry_ask"],
            "yes_bid_size": row["entry_bid_size"],
            "yes_ask_size": row["entry_ask_size"],
        }
        for row in source.to_dict(orient="records")
    ]
    bundle = {
        "schema_version": subject.SCHEMA_VERSION,
        "model_id": subject.MODEL_ID,
        "primary_policy": "full_ladder_completion_v1",
        "maker_quote_ttl_min": 60,
        "completion_policy": {
            "buffer_per_set": 0.01,
            "requested_shares": 5.0,
            "hedge_slippage_per_leg": 0.001,
        },
    }
    scored, selected = subject.score_runtime_entry(
        paired,
        bundle,
        event_identity={
            "forecast_event_id": "completion-event",
            "city": "London",
            "target_date": "2026-08-08",
            "lead_days": 1,
            "snapshot_epoch": 1.0,
        },
    )
    assert selected is not None
    pending = {
        "condition_id": selected["condition_id"],
        "decision_epoch": 1.0,
        "maker_limit_price": selected["entry_bid"],
        "entry_ladder": scored,
    }
    current = [
        {
            "condition_id": row["condition_id"],
            "yes_bid": row["entry_bid"],
            "yes_ask": row["entry_ask"],
            "yes_bid_size": 10.0,
            "yes_ask_size": 10.0,
        }
        for row in source.to_dict(orient="records")
    ]
    pending_decision = subject.score_runtime_pending_order(
        pending, current, bundle, elapsed_minutes=10.0
    )
    assert pending_decision.action == "KEEP_MAKER"
    filled = subject.score_runtime_position(
        pending, current, bundle, elapsed_minutes=10.0
    )
    assert filled.action == "HEDGE"
    assert filled.predicted_incremental_exit_value is not None
    assert filled.predicted_incremental_exit_value > 0.0


def test_exit_uplift_uses_identical_positions_and_date_blocks() -> None:
    frame = pd.DataFrame(
        [
            {"target_date": "2026-08-01", "entry_bid": 0.1, "dynamic": 0.02, "fixed": 0.01},
            {"target_date": "2026-08-01", "entry_bid": 0.2, "dynamic": 0.01, "fixed": 0.00},
            {"target_date": "2026-08-02", "entry_bid": 0.1, "dynamic": -0.01, "fixed": -0.02},
            {"target_date": "2026-08-03", "entry_bid": 0.1, "dynamic": 0.03, "fixed": None},
        ]
    )

    result = subject._paired_position_delta(
        frame,
        "dynamic",
        "fixed",
        cost_column="entry_bid",
        draws=100,
        seed=7,
    )

    assert result["positions"] == 3
    assert result["target_dates"] == 2
    assert result["candidate_roi"] == pytest.approx(0.05)
    assert result["baseline_roi"] == pytest.approx(-0.025)
    assert result["roi_delta"] == pytest.approx(0.075)


def test_mixed_execution_uses_native_tick_and_taker_when_quote_reaches_ask() -> None:
    frame = pd.DataFrame(
        [
            {
                "target_date": "2026-08-01",
                "entry_bid": 0.020,
                "entry_ask": 0.040,
                "entry_ask_size": 5.0,
                "h30_bid": 0.050,
                "h60_bid": 0.060,
                "h60_window_min_ask": 0.021,
                "dynamic_exit_bid": 0.060,
                "dynamic_exit_fee": subject.weather_fee(0.060),
            },
            {
                "target_date": "2026-08-01",
                "entry_bid": 0.001,
                "entry_ask": 0.002,
                "entry_ask_size": 5.0,
                "h30_bid": 0.003,
                "h60_bid": 0.004,
                "h60_window_min_ask": 0.002,
                "dynamic_exit_bid": 0.004,
                "dynamic_exit_fee": subject.weather_fee(0.004),
            },
            {
                "target_date": "2026-08-02",
                "entry_bid": 0.040,
                "entry_ask": 0.080,
                "entry_ask_size": 0.5,
                "h30_bid": 0.050,
                "h60_bid": 0.060,
                "h60_window_min_ask": 0.060,
                "dynamic_exit_bid": 0.060,
                "dynamic_exit_fee": subject.weather_fee(0.060),
            },
        ]
    )

    priced = subject.add_mixed_execution_expressions(frame)

    assert priced.loc[0, "native_entry_tick"] == pytest.approx(0.001)
    assert priced.loc[0, "maker_plus_tick_price"] == pytest.approx(0.021)
    assert priced.loc[0, "mixed_entry_route"] == "MAKER_BID_PLUS_TICK"
    assert priced.loc[0, "maker_plus_tick_touch_proxy"] == pytest.approx(1.0)

    assert priced.loc[1, "maker_plus_tick_price"] == pytest.approx(0.002)
    assert priced.loc[1, "mixed_entry_route"] == "TAKER_ASK"
    assert priced.loc[1, "mixed_entry_cost"] == pytest.approx(
        0.002 + subject.weather_fee(0.002)
    )

    assert priced.loc[2, "native_entry_tick"] == pytest.approx(0.01)
    assert priced.loc[2, "maker_plus_tick_price"] == pytest.approx(0.05)
    assert priced.loc[2, "mixed_entry_route"] == "MAKER_BID_PLUS_TICK"


def test_mixed_fill_sensitivity_keeps_taker_fills_immediate() -> None:
    frame = pd.DataFrame(
        [
            {
                "mixed_entry_route": "MAKER_BID_PLUS_TICK",
                "mixed_dynamic_pnl": 0.10,
                "mixed_entry_cost": 0.20,
            },
            {
                "mixed_entry_route": "MAKER_BID_PLUS_TICK",
                "mixed_dynamic_pnl": -0.02,
                "mixed_entry_cost": 0.10,
            },
            {
                "mixed_entry_route": "TAKER_ASK",
                "mixed_dynamic_pnl": -0.01,
                "mixed_entry_cost": 0.05,
            },
        ]
    )

    result = subject._mixed_fill_sensitivity(frame)

    assert result["winner_fill_rate_break_even_if_all_losers_fill"] == pytest.approx(
        0.30
    )
    assert result["winner_fill_rate_break_even_if_loser_rate_is_2x"] == pytest.approx(
        1.0 / 6.0
    )
    assert result["conditional_roi_without_top_winner"] == pytest.approx(-0.20)
