from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "scripts/analysis/market_structure_edge"
    / "research_three_city_pre_cross_path_pretrain_v2.py"
)
SPEC = importlib.util.spec_from_file_location("three_city_pre_cross_path_pretrain_v2", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _events(clock_class: str) -> dict[tuple[str, str], list[dict]]:
    base = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)
    temps = [20.0, 20.1, 20.3, 20.6, 20.8, 21.0, 21.4, 21.5, 21.6]
    rows = []
    for index, temp in enumerate(temps):
        obs = base + timedelta(minutes=10 * index)
        clock = obs + (timedelta(minutes=5) if clock_class == "collector_exact" else timedelta())
        rows.append(
            {
                "city": "Amsterdam",
                "source": "knmi",
                "station": "0-20000-0-06240",
                "target_date": "2026-07-20",
                "obs_ts": obs,
                "clock_ts": clock,
                "first_seen_ts": clock if clock_class == "collector_exact" else None,
                "first_seen_age_min": 5.0 if clock_class == "collector_exact" else None,
                "temp_c": temp,
                "wind_speed_kt": None,
                "pressure_hpa": None,
                "training_clock_class": clock_class,
            }
        )
    return {("Amsterdam", "2026-07-20"): rows}


def test_next_lattice_label_is_pre_cross_not_any_strict_high() -> None:
    states = MODULE.build_states(_events("collector_exact"))
    first = states[0]
    assert first["next_lattice_threshold_c"] == 20.5
    assert first["cross_next_lattice_within_30m"] == 1
    # The second print is a strict high, but still below the 20.5 lattice boundary.
    second = states[1]
    assert second["new_source_high"] == 1
    assert second["distance_to_next_lattice_c"] == pytest.approx(0.4)


def test_archive_clock_is_not_promoted_to_first_seen() -> None:
    states = MODULE.build_states(_events("observation_clock_archive_not_pit"))
    assert all(
        row["training_clock_class"] == "observation_clock_archive_not_pit"
        for row in states
    )
    assert all(row["source_first_seen_age_min"] is None for row in states)


def test_hourly_archive_uses_declared_cadence_for_coverage() -> None:
    base = datetime(2026, 7, 20, 6, 0, tzinfo=timezone.utc)
    rows = []
    for index, temp in enumerate((18.0, 18.2, 18.7, 19.0)):
        obs = base + timedelta(hours=index)
        rows.append(
            {
                "city": "Amsterdam",
                "source": "knmi",
                "station": "240",
                "target_date": "2026-07-20",
                "obs_ts": obs,
                "clock_ts": obs,
                "first_seen_ts": None,
                "first_seen_age_min": None,
                "temp_c": temp,
                "wind_speed_kt": None,
                "pressure_hpa": None,
                "cadence_minutes": 60,
                "archive_source": "knmi_hourly_climate_station_240",
                "training_clock_class": "observation_clock_archive_not_pit",
            }
        )
    states = MODULE.build_states({("Amsterdam", "2026-07-20"): rows})
    assert states[0]["coverage_complete_60m"] == 1
    assert states[0]["cross_next_lattice_within_60m"] == 0
