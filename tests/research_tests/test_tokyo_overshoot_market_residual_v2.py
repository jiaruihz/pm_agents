from pathlib import Path

import joblib
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
GENERATED = (
    ROOT / "docs/analysis/2026-08/generated/tokyo_overshoot_market_residual_v2"
)


def test_historical_proxy_clock_is_after_assumed_availability_and_before_holdout() -> None:
    rows = pd.read_csv(
        ROOT
        / "docs/analysis/2026-07/generated/tokyo_clob_price_history_coverage_v8"
        / "historical_checkpoint_price_proxy.csv.gz"
    )
    usable = rows[rows["after_price"].notna() & rows["after_delay_min"].between(0, 15)]
    point = pd.to_datetime(usable["after_point_ts"], unit="s", utc=True)
    available = pd.to_datetime(usable["assumed_availability_ts_utc"], utc=True)
    assert (point >= available).all()
    assert usable["target_date"].max() == "2026-07-15"


def test_frozen_dates_never_enter_fitted_artifact() -> None:
    artifact = joblib.load(GENERATED / "tokyo_overshoot_market_residual_v2.joblib")
    assert artifact["training_end"] == "2026-07-15"
    assert artifact["clean_frozen_start"] == "2026-07-16"
    assert max(artifact["train_dates"]) == "2026-07-15"
    assert all(date < artifact["clean_frozen_start"] for date in artifact["train_dates"])
    assert artifact["training_clock_class"] == "archive_reconstructed_plus_15m_price_proxy"
