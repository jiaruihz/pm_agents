from __future__ import annotations

import numpy as np

from scripts.analysis.market_structure_edge import (
    research_tokyo_continuous_ladder_probability_v2 as model,
)


def _row(
    timestamp: str,
    *,
    target_date: str = "2026-07-01",
    bracket: int = 30,
    slope: float = 0.0,
    pullback: float = 0.0,
    age: float = 20.0,
    delta: float = 0.0,
) -> dict[str, object]:
    return {
        "state_id": f"{target_date}:{timestamp}:{bracket}",
        "target_date": target_date,
        "decision_ts_utc": timestamp,
        "current_bracket": bracket,
        "jma_temp_slope_30m_cph": slope,
        "jma_pullback_from_running_max_c": pullback,
        "minutes_since_jma_strict_high": age,
        "jma_temp_delta_10m": delta,
    }


def test_grains_are_prelabel_and_state_entry_is_once_per_bracket() -> None:
    rows = model.annotate_grains(
        [
            _row("2026-07-01T01:00:00+00:00"),
            _row("2026-07-01T01:10:00+00:00"),
            _row(
                "2026-07-01T01:20:00+00:00",
                slope=0.8,
                pullback=-0.1,
            ),
            _row(
                "2026-07-01T01:30:00+00:00",
                bracket=31,
                slope=0.8,
                pullback=-0.1,
            ),
        ]
    )

    assert [row["path_phase"] for row in rows] == [
        "plateau",
        "plateau",
        "warming",
        "warming",
    ]
    assert [row["is_transition"] for row in rows] == [1, 0, 1, 1]
    assert [row["is_state_entry"] for row in rows] == [1, 0, 0, 1]


def test_multigrain_weights_match_explicit_three_objective_sum() -> None:
    rows = model.annotate_grains(
        [
            _row("2026-07-01T01:00:00+00:00"),
            _row("2026-07-01T01:10:00+00:00"),
            _row(
                "2026-07-01T01:20:00+00:00",
                bracket=31,
                slope=0.8,
                pullback=-0.1,
            ),
        ]
    )
    weights = model.multigrain_weights(rows)

    # checkpoint count=3, transition count=2, state-entry count=2.
    raw = np.asarray(
        [
            1 / 9 + 1 / 6 + 1 / 6,
            1 / 9,
            1 / 9 + 1 / 6 + 1 / 6,
        ]
    )
    expected = raw / np.mean(raw)
    assert np.allclose(weights, expected)


def test_coherent_hurdle_distribution_uses_one_binary_head(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        model,
        "probability_for_class",
        lambda *_args, **_kwargs: np.asarray([0.25, 0.8]),
    )
    monkeypatch.setattr(
        model,
        "aligned_tail_probabilities",
        lambda *_args, **_kwargs: np.asarray(
            [[0.5, 0.3, 0.2], [0.1, 0.2, 0.7]]
        ),
    )

    probabilities = model.coherent_probabilities(
        {"leave": object(), "tail": object()}, [{}, {}]
    )

    assert np.allclose(probabilities[:, 0], [0.75, 0.2])
    assert np.allclose(probabilities[0, 1:], [0.125, 0.075, 0.05])
    assert np.allclose(probabilities.sum(axis=1), 1)


def test_wait_policy_moves_to_next_checkpoint_without_using_label() -> None:
    joined = [
        {
            "state_id": "s1",
            "target_date": "2026-07-01",
            "availability_ts_utc": "2026-07-01T01:00:00+00:00",
        },
        {
            "state_id": "s2",
            "target_date": "2026-07-01",
            "availability_ts_utc": "2026-07-01T01:10:00+00:00",
        },
    ]
    now = [
        {
            "model": "m",
            "state_id": "s1",
            "target_date": "2026-07-01",
            "availability_ts_utc": "2026-07-01T01:00:00+00:00",
            "expression_bracket": "30",
            "side": "YES",
            "fee_adjusted_edge": 0.04,
        }
    ]
    candidates = [
        {
            "model": "m",
            "state_id": "s2",
            "target_date": "2026-07-01",
            "fee_adjusted_edge": 0.03,
            "expression_bracket": "31",
            "side": "NO",
        },
        {
            "model": "m",
            "state_id": "s2",
            "target_date": "2026-07-01",
            "fee_adjusted_edge": 0.01,
            "expression_bracket": "30",
            "side": "YES",
        },
    ]

    waited, pairs = model.wait_one_candidates(joined, candidates, now)

    assert len(waited) == 1
    assert waited[0]["expression_bracket"] == "31"
    assert pairs[0]["wait_status"] == "eligible"
    assert pairs[0]["wait_minutes"] == 10
