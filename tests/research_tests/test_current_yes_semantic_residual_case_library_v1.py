import pandas as pd

from scripts.analysis.reheat_risk import (
    build_current_yes_semantic_residual_case_library_v1 as subject,
)


def test_classify_tail_loss_uses_relative_market_confidence() -> None:
    base = {
        "current_exact_hold": 0,
        "core_minus_market_brier": 0.0,
    }
    assert (
        subject.classify_case(
            pd.Series(
                {
                    **base,
                    "core_hold_probability": 0.94,
                    "market_hold_mid": 0.86,
                }
            )
        )
        == "core_more_overconfident_tail_loss"
    )
    assert (
        subject.classify_case(
            pd.Series(
                {
                    **base,
                    "core_hold_probability": 0.90,
                    "market_hold_mid": 0.89,
                }
            )
        )
        == "shared_high_confidence_tail_loss"
    )
    assert (
        subject.classify_case(
            pd.Series(
                {
                    **base,
                    "core_hold_probability": 0.84,
                    "market_hold_mid": 0.93,
                }
            )
        )
        == "market_more_overconfident_tail_loss"
    )


def test_first_mechanism_case_is_chronological_not_hindsight_selected() -> None:
    frame = pd.DataFrame(
        [
            {
                "city": "Example",
                "target_date": "2026-07-01",
                "decision_snapshot_ts_utc": "2026-07-01T10:00:00Z",
                "is_warm_moist_advection": True,
                "p_core": 0.85,
                "p_market_raw": 0.84,
                "current_yes_bid": 0.83,
                "current_yes_ask": 0.85,
                "overshoot": 1,
            },
            {
                "city": "Example",
                "target_date": "2026-07-01",
                "decision_snapshot_ts_utc": "2026-07-01T11:00:00Z",
                "is_warm_moist_advection": True,
                "p_core": 0.99,
                "p_market_raw": 0.90,
                "current_yes_bid": 0.89,
                "current_yes_ask": 0.91,
                "overshoot": 1,
            },
        ]
    )
    cases = subject.first_mechanism_cases(frame)
    assert len(cases) == 1
    assert (
        cases.iloc[0]["decision_snapshot_ts_utc"]
        == "2026-07-01T10:00:00Z"
    )
