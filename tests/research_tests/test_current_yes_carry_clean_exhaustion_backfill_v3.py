from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "research_current_yes_carry_clean_exhaustion_backfill_v3",
    ROOT / "scripts/analysis/reheat_risk/research_current_yes_carry_clean_exhaustion_backfill_v3.py",
)
assert SPEC is not None and SPEC.loader is not None
study = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(study)


def test_equal_high_does_not_reset_strict_high_clock() -> None:
    day = pd.DataFrame(
        {
            "ts": pd.to_datetime(
                [
                    "2026-07-16T10:00:00Z",
                    "2026-07-16T11:00:00Z",
                    "2026-07-16T12:00:00Z",
                    "2026-07-16T13:00:00Z",
                ],
                utc=True,
            ),
            "tmpf": [73.4, 75.2, 73.4, 75.2],
            "skyc1": ["CLR", "CLR", "FEW", "CLR"],
        }
    )

    features = study.observation_path_asof(
        day, pd.Timestamp("2026-07-16T13:30:00Z")
    )

    assert features["minutes_since_last_strict_new_high"] == 150
    assert features["same_running_max_obs_count"] == 2


def test_forecast_interpolation_is_linear() -> None:
    assert study.interpolate([(13.0, 80.0), (14.0, 82.0)], 13.5) == 81.0


def test_clean_feature_sets_are_frozen_without_taf() -> None:
    all_features = {feature for features in study.FEATURE_SETS.values() for feature in features}

    assert "heat_exhaustion_strict_high_age_v2" in all_features
    assert "forecast_remaining_gap_to_running_native" in all_features
    assert not any("taf" in feature.lower() for feature in all_features)
