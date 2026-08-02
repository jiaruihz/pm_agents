import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from weather_model_evaluation import (
    binary_calibration_table,
    binary_score,
    build_checkpoint_grain,
    build_state_entry_grain,
    build_transition_grain,
    composite_grain_date_bootstrap_delta,
    composite_grain_weights,
    date_block_bootstrap_delta,
    event_bin_from_cumulative_labels,
    event_probabilities_to_cumulative,
    hazards_to_event_probabilities,
    integrated_horizon_score,
    ordinal_score,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "city": ["A"] * 5 + ["B"] * 2,
            "target_date": ["2026-01-01"] * 5 + ["2026-01-02"] * 2,
            "target_id": ["break"] * 7,
            "decision_ts_utc": pd.to_datetime(
                [
                    "2026-01-01T09:00Z",
                    "2026-01-01T09:10Z",
                    "2026-01-01T09:20Z",
                    "2026-01-01T09:30Z",
                    "2026-01-01T09:40Z",
                    "2026-01-02T09:00Z",
                    "2026-01-02T09:10Z",
                ],
                utc=True,
            ),
            "current_x": [10, 10, 10, 11, 11, 20, 20],
            "path_state": [
                "runway",
                "runway",
                "plateau",
                "runway",
                "runway",
                "runway",
                "fade",
            ],
            "label": [1, 1, 1, 0, 0, 0, 0],
        }
    )


def test_grain_builders_are_price_and_outcome_independent() -> None:
    frame = _frame()
    group = ["city", "target_date", "target_id"]
    checkpoint = build_checkpoint_grain(frame, group_columns=group)
    transition = build_transition_grain(
        frame,
        group_columns=group,
        state_columns=["current_x", "path_state"],
    )
    state_entry = build_state_entry_grain(
        frame,
        group_columns=group,
        state_columns=["current_x"],
    )

    assert len(checkpoint) == 7
    assert len(transition) == 5
    assert len(state_entry) == 3
    assert set(state_entry["evaluation_grain"]) == {"state_entry"}


def test_checkpoint_duplicate_is_explicit_failure() -> None:
    frame = pd.concat([_frame(), _frame().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate checkpoint"):
        build_checkpoint_grain(
            frame,
            group_columns=["city", "target_date", "target_id"],
        )


def test_date_equal_binary_score_does_not_overweight_long_day() -> None:
    frame = _frame()
    probability = np.array([1, 1, 1, 0, 0, 1, 1], dtype=float)
    score = binary_score(
        frame, probability, label_column="label"
    )
    # First day perfect, second day entirely wrong: dates receive equal weight.
    assert score["brier"] == pytest.approx(0.5, abs=1e-7)


def test_composite_grain_weights_equalize_each_component_by_date() -> None:
    frame = _frame()
    frame["is_transition"] = [1, 0, 1, 1, 0, 1, 1]
    frame["is_state_entry"] = [1, 0, 0, 1, 0, 1, 0]
    weight = composite_grain_weights(
        frame,
        membership_columns=["is_transition", "is_state_entry"],
    )
    assert weight.mean() == pytest.approx(1.0)
    assert weight[0] > weight[1]
    # A member of both sparse decision grains receives more training mass.
    assert weight[0] > weight[2]


def test_ordinal_rps_respects_class_distance() -> None:
    frame = pd.DataFrame(
        {"target_date": ["d1", "d2"], "label": [0, 0]}
    )
    near = np.array([[0, 1, 0, 0], [0, 1, 0, 0]], dtype=float)
    far = np.array([[0, 0, 0, 1], [0, 0, 0, 1]], dtype=float)
    near_score = ordinal_score(
        frame, near, label_column="label"
    )
    far_score = ordinal_score(frame, far, label_column="label")
    assert (
        near_score["ranked_probability_score"]
        < far_score["ranked_probability_score"]
    )


def test_hazard_simplex_and_cumulative_round_trip() -> None:
    hazards = np.array([[0.2, 0.25, 0.5, 0.1]])
    event = hazards_to_event_probabilities(hazards)
    cumulative = event_probabilities_to_cumulative(event)

    assert event.shape == (1, 5)
    assert event.sum(axis=1) == pytest.approx([1.0])
    assert np.diff(cumulative, axis=1).min() >= 0
    assert cumulative[0, 0] == pytest.approx(0.2)
    assert cumulative[0, 1] == pytest.approx(0.4)


def test_event_bin_from_monotone_labels_and_integrated_score() -> None:
    labels = np.array(
        [[1, 1, 1, 1], [0, 0, 1, 1], [0, 0, 0, 0]]
    )
    assert event_bin_from_cumulative_labels(labels).tolist() == [0, 2, 4]
    frame = pd.DataFrame(
        {
            "target_date": ["d1", "d1", "d2"],
            "h30": labels[:, 0],
            "h60": labels[:, 1],
            "h120": labels[:, 2],
            "heod": labels[:, 3],
        }
    )
    score = integrated_horizon_score(
        frame,
        labels.astype(float),
        label_columns=["h30", "h60", "h120", "heod"],
    )
    assert score["integrated_brier"] == pytest.approx(0.0)


def test_date_block_bootstrap_uses_paired_date_means() -> None:
    frame = pd.DataFrame(
        {"target_date": ["d1", "d1", "d2", "d2"]}
    )
    result = date_block_bootstrap_delta(
        frame,
        np.array([0.0, 0.0, 1.0, 1.0]),
        np.ones(4),
        draws=200,
        seed=1,
    )
    assert result["delta"] == pytest.approx(-0.5)
    assert result["target_dates"] == 2


def test_binary_calibration_and_auc_use_date_equal_weights() -> None:
    frame = pd.DataFrame(
        {
            "target_date": ["d1", "d1", "d2"],
            "label": [0, 1, 1],
        }
    )
    probability = np.array([0.1, 0.9, 0.8])
    table = binary_calibration_table(
        frame, probability, label_column="label"
    )
    assert table["rows"].sum() == 3
    assert table["date_equal_weight"].sum() == pytest.approx(2.0)
    score = binary_score(
        frame, probability, label_column="label"
    )
    assert score["auc"] == pytest.approx(1.0)
    assert score["calibration_ece_10"] == pytest.approx(0.15)


def test_composite_grain_bootstrap_equalizes_grains_and_dates() -> None:
    checkpoint = pd.DataFrame(
        {"target_date": ["d1", "d1", "d2", "d2"]}
    )
    transition = pd.DataFrame({"target_date": ["d1", "d2"]})
    result = composite_grain_date_bootstrap_delta(
        {
            "checkpoint": (
                checkpoint,
                np.array([0.0, 0.0, 2.0, 2.0]),
                np.zeros(4),
            ),
            "transition": (
                transition,
                np.array([2.0, 0.0]),
                np.zeros(2),
            ),
        },
        draws=200,
        seed=1,
    )
    assert result["delta"] == pytest.approx(1.0)
    assert result["target_dates"] == 2
    assert result["grains"] == 2


def test_composite_grain_bootstrap_rejects_date_mismatch() -> None:
    with pytest.raises(ValueError, match="identical target dates"):
        composite_grain_date_bootstrap_delta(
            {
                "a": (
                    pd.DataFrame({"target_date": ["d1", "d2"]}),
                    np.zeros(2),
                    np.zeros(2),
                ),
                "b": (
                    pd.DataFrame({"target_date": ["d1"]}),
                    np.zeros(1),
                    np.zeros(1),
                ),
            }
        )


def test_non_monotone_cumulative_label_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-decreasing"):
        event_bin_from_cumulative_labels(
            np.array([[0, 1, 0, 1]])
        )
