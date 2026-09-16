from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from scripts.analysis.market_structure_edge import (
    research_tokyo_overshoot_market_residual_v2 as model,
)


ROOT = Path(__file__).resolve().parents[2]
GENERATED = (
    ROOT / "docs/analysis/2026-08/generated/tokyo_overshoot_market_residual_v2"
)


def test_historical_proxy_clock_is_after_assumed_availability_and_before_holdout(
    tmp_path,
) -> None:
    timestamps = pd.to_datetime(
        [
            "2026-07-15T01:00:00Z",
            "2026-07-15T02:00:00Z",
            "2026-07-16T01:00:00Z",
        ],
        utc=True,
    )
    proxy = pd.DataFrame({
        "target_date": ["2026-07-15", "2026-07-15", "2026-07-16"],
        "decision_ts_utc": timestamps.astype(str),
        "current_bracket": [30, 30, 30],
        "after_price": [0.4, 0.5, 0.6],
        "after_delay_min": [5.0, 5.0, 5.0],
        "after_point_ts": [
            int(timestamps[0].timestamp()),
            int((timestamps[1] - pd.Timedelta(minutes=1)).timestamp()),
            int(timestamps[2].timestamp()),
        ],
        "assumed_availability_ts_utc": timestamps.astype(str),
    })
    path = tmp_path / "proxy.csv"
    proxy.to_csv(path, index=False)
    features = pd.DataFrame({
        "target_date": proxy["target_date"],
        "decision_ts_utc": [
            value.isoformat() for value in timestamps.to_pydatetime()
        ],
        "current_bracket": proxy["current_bracket"],
        "binary_leave_current": [1, 0, 1],
    })

    usable = model.historical_frame(features, path)

    assert usable["target_date"].tolist() == ["2026-07-15"]
    assert usable["decision_ts_utc"].tolist() == ["2026-07-15T01:00:00+00:00"]


def test_frozen_dates_never_enter_fitted_artifact() -> None:
    artifact = joblib.load(GENERATED / "tokyo_overshoot_market_residual_v2.joblib")
    assert artifact["training_end"] == "2026-07-15"
    assert artifact["clean_frozen_start"] == "2026-07-16"
    assert max(artifact["train_dates"]) == "2026-07-15"
    assert all(date < artifact["clean_frozen_start"] for date in artifact["train_dates"])
    assert artifact["training_clock_class"] == "archive_reconstructed_plus_15m_price_proxy"


def test_market_calibrator_is_probability_bounded_and_records_dates() -> None:
    frame = pd.DataFrame({
        "target_date": ["2026-01-01"] * 2 + ["2026-01-02"] * 2,
        "market_no_logit": [model.logit(value) for value in (0.2, 0.8, 0.3, 0.7)],
        "y_no": [0, 1, 0, 1],
    })
    artifact = model.fit_market_calibrator(frame)
    probability = model.predict_market_calibrator(artifact, frame)
    assert artifact["train_dates"] == ["2026-01-01", "2026-01-02"]
    assert np.isfinite(probability).all()
    assert ((probability > 0) & (probability < 1)).all()


def test_confirmation_is_feature_not_a_hard_gate() -> None:
    assert "stacked_confirmation" in model.FEATURE_FAMILIES
    assert "q_confirm_30m_logit" in model.FEATURE_FAMILIES["stacked_confirmation"]
    assert model.FEATURE_FAMILIES["compact"]
