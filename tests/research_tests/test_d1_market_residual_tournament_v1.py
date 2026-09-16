from __future__ import annotations

import numpy as np
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import research_d1_market_residual_tournament_v1 as target


def test_zero_residual_returns_market_exactly() -> None:
    market = np.asarray([0.05, 0.25, 0.50, 0.20], dtype=float)
    design = np.asarray(
        [[-2.0, 1.0], [-1.0, -1.0], [0.5, 2.0], [3.0, -4.0]], dtype=float
    )

    actual = target.softmax_offset(market, design, np.zeros(2))

    assert np.array_equal(actual, market)


def test_prepared_labels_preserve_open_native_tails() -> None:
    brackets = target._brackets_from_labels(["77", "78-79", "80-81", "82+"])

    assert brackets[0].bottom is True
    assert brackets[0].high == 77.0
    assert brackets[1].low == 78.0
    assert brackets[1].high == 79.0
    assert brackets[-1].top is True
    assert brackets[-1].low == 82.0


def test_version_budget_includes_nonlinear_challengers() -> None:
    assert list(target.FEATURE_SETS) == [
        "V01_weather_logratio",
        "V02_ordinal_location_scale",
        "V03_structured_weather",
        "V04_revision_spread_bias",
        "V05_piecewise_surprise_gam",
        "V06_revision_gated_nonlinear",
        "V07_random_feature_network",
        "V08_hybrid_nonlinear",
    ]
    assert len(target.FEATURE_SETS["V07_random_feature_network"]) == 16
    assert set(target.FEATURE_SETS["V07_random_feature_network"]).issubset(
        target.FEATURE_SETS["V08_hybrid_nonlinear"]
    )


def test_d1_market_challenger_emits_shared_stack_without_probability_drift() -> None:
    brackets = tuple(target._brackets_from_labels(["21", "22", "23+"]))
    market = np.asarray([0.2, 0.5, 0.3], dtype=float)
    posterior = np.asarray([0.1, 0.35, 0.55], dtype=float)
    state = target.PreparedState(
        snapshot_key="d1-book-1",
        city="Tokyo",
        target_date="2026-08-10",
        decision_ts_utc="2026-08-09T12:00:00Z",
        market_unit="C",
        labels=("21", "22", "23+"),
        brackets=brackets,
        model_key="gfs_global",
        winner_index=2,
        market=market,
        weather=posterior,
        features={},
        reconstructed_revision_f=0.0,
        model_spread_f=1.0,
        assigned_minus_consensus_f=0.0,
    )

    fields = target._probability_stack_fields(
        state, posterior, arm="V01_weather_logratio"
    )

    assert fields["probability_stack_status"] == "scorable"
    assert fields["probability_stack_snapshot_id"]
    assert fields["model_book_snapshot_id"] == "d1-book-1"
    assert fields["probability_head_kind"] == "final_settlement"
