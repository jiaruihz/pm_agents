from __future__ import annotations

import pandas as pd

from scripts.analysis.reheat_risk import (
    research_current_yes_core_carry_physical_semantic_audit_v1 as subject,
)


def _row(minutes_since_strict_high: float = 360.0) -> pd.Series:
    return pd.Series(
        {
            "minutes_since_last_strict_new_high": minutes_since_strict_high,
            "solar_elevation_deg": -5.0,
        }
    )


def test_warm_moist_advection_requires_wind_moistening_and_nonfalling_temp() -> None:
    ts = pd.Timestamp("2026-07-29T09:30:00Z")
    day = pd.DataFrame(
        {
            "ts": pd.to_datetime(
                [
                    "2026-07-29T06:30:00Z",
                    "2026-07-29T07:30:00Z",
                    "2026-07-29T08:30:00Z",
                    "2026-07-29T09:30:00Z",
                ],
                utc=True,
            ),
            "tmpf": [53.6, 53.6, 53.6, 53.6],
            "dwpf": [46.4, 46.4, 48.2, 48.2],
            "relh": [75.0, 75.0, 82.0, 82.0],
            "drct": [350.0, 360.0, 360.0, 360.0],
            "sknt": [20.0, 20.0, 22.0, 21.0],
            "sky_level": [2.0, 2.0, 4.0, 3.0],
        }
    )
    features = subject.physical_features(day, ts, _row())
    assert features["temp_tendency_3h_f"] == 0.0
    assert features["dewpoint_tendency_3h_f"] > 0.0
    assert features["wind_direction_persistence_3h"] > 0.95
    assert features["warm_moist_advection_score"] > 0.0
    assert features["mechanical_mixing_plateau_score"] > 0.0


def test_strong_wind_alone_does_not_create_warm_advection_score() -> None:
    ts = pd.Timestamp("2026-07-29T09:30:00Z")
    day = pd.DataFrame(
        {
            "ts": pd.to_datetime(
                [
                    "2026-07-29T06:30:00Z",
                    "2026-07-29T07:30:00Z",
                    "2026-07-29T08:30:00Z",
                    "2026-07-29T09:30:00Z",
                ],
                utc=True,
            ),
            "tmpf": [55.4, 55.4, 53.6, 53.6],
            "dwpf": [48.2, 48.2, 46.4, 46.4],
            "relh": [75.0, 75.0, 75.0, 75.0],
            "drct": [360.0, 360.0, 360.0, 360.0],
            "sknt": [21.0, 21.0, 21.0, 21.0],
            "sky_level": [2.0, 2.0, 2.0, 2.0],
        }
    )
    features = subject.physical_features(day, ts, _row())
    assert features["warm_moist_advection_score"] == 0.0
    assert features["cold_advection_score"] > 0.0


def test_circular_direction_shift_handles_north_crossing() -> None:
    assert subject.circular_difference(350.0, 10.0) == 20.0
    assert subject.circular_difference(10.0, 350.0) == 20.0


def test_daytime_fade_is_not_mislabeled_as_radiative_cooling() -> None:
    ts = pd.Timestamp("2026-07-29T03:00:00Z")
    day = pd.DataFrame(
        {
            "ts": pd.to_datetime(
                [
                    "2026-07-29T00:00:00Z",
                    "2026-07-29T01:00:00Z",
                    "2026-07-29T02:00:00Z",
                    "2026-07-29T03:00:00Z",
                ],
                utc=True,
            ),
            "tmpf": [80.0, 78.0, 77.0, 76.0],
            "dwpf": [55.0, 55.0, 55.0, 55.0],
            "relh": [40.0, 42.0, 43.0, 45.0],
            "drct": [180.0, 180.0, 180.0, 180.0],
            "sknt": [3.0, 3.0, 3.0, 3.0],
            "sky_level": [0.0, 0.0, 0.0, 0.0],
        }
    )
    row = _row()
    row["solar_elevation_deg"] = 35.0
    features = subject.physical_features(day, ts, row)
    assert features["temp_tendency_3h_f"] < 0
    assert features["radiative_cooling_score"] == 0.0
