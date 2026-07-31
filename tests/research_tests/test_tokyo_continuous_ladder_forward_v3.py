from __future__ import annotations

import json

from scripts.analysis.market_structure_edge import (
    research_tokyo_continuous_ladder_forward_v3 as forward,
)


def test_forward_window_is_completely_excluded_from_train() -> None:
    rows = [
        {"state_id": "train", "target_date": "2026-07-15"},
        {"state_id": "forward_1", "target_date": "2026-07-16"},
        {"state_id": "forward_2", "target_date": "2026-07-30"},
        {"state_id": "future", "target_date": "2026-07-31"},
    ]

    train, holdout = forward.split_train_forward(rows)

    assert [row["state_id"] for row in train] == ["train"]
    assert [row["state_id"] for row in holdout] == [
        "forward_1",
        "forward_2",
    ]


def test_official_fee_is_rounded_per_share_to_five_decimals() -> None:
    assert forward.official_fee_per_share(0.5) == 0.0125
    assert forward.official_fee_per_share(0.001) == 0.00005


def test_candidate_policy_only_expresses_current_and_next_brackets() -> None:
    row = {
        "state_id": "state",
        "target_date": "2026-07-20",
        "settlement_lower_bound_violation": 0,
        "current_bracket": 30,
        "winning_bracket": "31",
        "quotes_json": json.dumps(
            {
                "30": {"ask": 0.4, "bid": 0.35},
                "31": {"ask": 0.3, "bid": 0.25},
                "32": {"ask": 0.2, "bid": 0.15},
            }
        ),
        "model_distribution_json": json.dumps([0.3, 0.4, 0.2, 0.1]),
    }

    candidates = forward.current_next_candidates([row], ["model"])

    assert len(candidates) == 4
    assert {row["expression_delta"] for row in candidates} == {0, 1}
    assert {row["expression_bracket"] for row in candidates} == {"30", "31"}
    assert {row["side"] for row in candidates} == {"YES", "NO"}


def test_first_signal_policy_does_not_select_later_better_state(
    monkeypatch,
) -> None:
    candidates = [
        {
            "model": "model",
            "state_id": "early",
            "target_date": "2026-07-20",
            "availability_ts_utc": "2026-07-20T01:00:00+00:00",
            "snapshot_ts_utc": "2026-07-20T01:00:00+00:00",
            "expression_bracket": "30",
            "expression_delta": 0,
            "side": "YES",
            "fee_adjusted_edge": 0.03,
            "selected_side_ask": 0.4,
            "fee_per_share": 0.012,
            "winning_bracket": "30",
        },
        {
            "model": "model",
            "state_id": "later",
            "target_date": "2026-07-20",
            "availability_ts_utc": "2026-07-20T01:10:00+00:00",
            "snapshot_ts_utc": "2026-07-20T01:10:00+00:00",
            "expression_bracket": "31",
            "expression_delta": 1,
            "side": "YES",
            "fee_adjusted_edge": 0.20,
            "selected_side_ask": 0.2,
            "fee_per_share": 0.008,
            "winning_bracket": "31",
        },
    ]
    monkeypatch.setattr(
        forward.v1,
        "raw_ask_size",
        lambda *_args, **_kwargs: 10.0,
    )

    selected = forward.select_first_signal(
        candidates, raw_books=forward.Path(".")
    )

    assert len(selected) == 1
    assert selected[0]["state_id"] == "early"
    assert selected[0]["settled_win"] == 1
    assert selected[0]["fee_adjusted_pnl_usd"] > 0


def test_probability_bins_cover_full_unit_interval() -> None:
    assert forward.probability_bin(0.0) == "[0,.05)"
    assert forward.probability_bin(0.05) == "[.05,.10)"
    assert forward.probability_bin(0.9) == "[.90,1]"
    assert forward.probability_bin(1.0) == "[.90,1]"


def test_wilson_interval_keeps_zero_win_uncertainty_visible() -> None:
    low, high = forward.wilson_interval(0, 12)

    assert low == 0
    assert 0.24 < high < 0.25
