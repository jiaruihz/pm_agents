from __future__ import annotations

import pandas as pd
import pytest

from weather_model_evaluation.selection_increment import paired_policy_increment


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "opportunity_id": "a-early",
                "city": "A",
                "target_date": "2026-08-01",
                "decision_ts_utc": "2026-08-01T01:00:00Z",
                "label": 1,
                "cost": 0.80,
                "challenger": 0.85,
                "baseline": 0.79,
            },
            {
                "opportunity_id": "a-late",
                "city": "A",
                "target_date": "2026-08-01",
                "decision_ts_utc": "2026-08-01T02:00:00Z",
                "label": 1,
                "cost": 0.70,
                "challenger": 0.90,
                "baseline": 0.90,
            },
            {
                "opportunity_id": "b",
                "city": "B",
                "target_date": "2026-08-02",
                "decision_ts_utc": "2026-08-02T01:00:00Z",
                "label": 0,
                "cost": 0.60,
                "challenger": 0.55,
                "baseline": 0.65,
            },
            {
                "opportunity_id": "c",
                "city": "C",
                "target_date": "2026-08-03",
                "decision_ts_utc": "2026-08-03T01:00:00Z",
                "label": 1,
                "cost": 0.75,
                "challenger": 0.70,
                "baseline": 0.70,
            },
        ]
    )


def test_paired_increment_keeps_no_trade_dates_and_first_policy_action() -> None:
    report, ledger = paired_policy_increment(
        _frame(),
        challenger_probability_column="challenger",
        baseline_probability_column="baseline",
        cost_column="cost",
        bootstrap_draws=1_000,
        bootstrap_seed=7,
    )

    assert report["fixed_universe"] == {
        "opportunities": 4,
        "policy_groups": 3,
        "target_dates": 3,
    }
    assert report["challenger"]["entries"] == 1
    assert report["baseline"]["entries"] == 2
    assert report["selection_relation"] == {
        "same": 0,
        "switched": 1,
        "challenger_only": 0,
        "baseline_only": 1,
    }
    switched = ledger[ledger["city"].eq("A")].iloc[0]
    assert switched["opportunity_id_challenger"] == "a-early"
    assert switched["opportunity_id_baseline"] == "a-late"
    # Challenger earns +0.20 instead of +0.30 on A, while avoiding baseline's
    # -0.60 loss on B: paired total increment is +0.50/share.
    assert report["paired_increment"]["pnl_per_share"] == pytest.approx(0.50)
    assert len(report["daily"]) == 3
    multi_opportunity_date = next(
        row for row in report["daily"] if row["target_date"] == "2026-08-01"
    )
    assert multi_opportunity_date["fixed_opportunities"] == 2
    assert multi_opportunity_date["fixed_groups"] == 1
    assert multi_opportunity_date["delta_fixed_universe_return"] == pytest.approx(
        -0.05
    )
    no_trade = next(
        row for row in report["daily"] if row["target_date"] == "2026-08-03"
    )
    assert no_trade["delta_pnl_per_share"] == 0.0


def test_duplicate_opportunity_identity_fails_closed() -> None:
    frame = _frame()
    frame.loc[1, "opportunity_id"] = "a-early"
    with pytest.raises(ValueError, match="duplicate opportunity_id"):
        paired_policy_increment(
            frame,
            challenger_probability_column="challenger",
            baseline_probability_column="baseline",
            cost_column="cost",
        )
