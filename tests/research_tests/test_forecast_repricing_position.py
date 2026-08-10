from __future__ import annotations

import pandas as pd
from pathlib import Path
import pytest
from sklearn.dummy import DummyRegressor
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
