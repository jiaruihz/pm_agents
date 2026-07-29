from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "scripts"
    / "analysis"
    / "market_structure_edge"
    / "research_three_city_first_seen_path_v1.py"
)
SPEC = importlib.util.spec_from_file_location("three_city_first_seen_path_v1", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _fast_rows() -> dict[tuple[str, str], list[dict]]:
    base = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)
    temps = [20.0, 20.0, 20.4, 20.3, 20.6, 20.6, 20.7, 20.7, 20.8]
    rows = []
    for index, temp in enumerate(temps):
        seen = base + timedelta(minutes=10 * index)
        rows.append(
            {
                "city": "Helsinki",
                "source": "fmi",
                "station": "100968",
                "target_date": "2026-07-20",
                "obs_ts": seen - timedelta(minutes=2),
                "first_seen_ts": seen,
                "first_seen_age_min": 2.0,
                "temp_c": temp,
                "wind_speed_kt": 5.0,
                "pressure_hpa": 1010.0,
                "payload_hash": str(index),
                "pit_lineage_class": "collector_exact",
            }
        )
    return {("Helsinki", "2026-07-20"): rows}


def test_future_high_labels_require_complete_first_seen_cadence() -> None:
    states = MODULE.build_states(_fast_rows(), {}, {})
    first = states[0]
    assert first["coverage_complete_30m"] == 1
    assert first["new_high_within_30m"] == 1
    assert first["coverage_complete_60m"] == 1
    assert first["new_high_within_60m"] == 1
    assert first["coverage_complete_120m"] == 0
    assert first["new_high_within_120m"] is None


def test_metar_join_is_strictly_point_in_time() -> None:
    fast = _fast_rows()
    base = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)
    metar = {
        ("Helsinki", "2026-07-20"): [
            {
                "city": "Helsinki",
                "target_date": "2026-07-20",
                "source": "aviationweather_metar",
                "priority": 0,
                "obs_ts": base - timedelta(minutes=30),
                "available_ts": base - timedelta(minutes=20),
                "temp_c": 19.0,
                "dewpoint_c": 10.0,
                "relative_humidity_pct": 50.0,
                "wind_speed_kt": 4.0,
                "wind_dir_deg": 180.0,
                "ceiling_ft_agl": 5000.0,
                "cloud_layer_count": 2.0,
                "precip_observed": 0,
            },
            {
                "city": "Helsinki",
                "target_date": "2026-07-20",
                "source": "aviationweather_metar",
                "priority": 0,
                "obs_ts": base,
                "available_ts": base + timedelta(minutes=5),
                "temp_c": 21.0,
                "dewpoint_c": 11.0,
                "relative_humidity_pct": 55.0,
                "wind_speed_kt": 6.0,
                "wind_dir_deg": 200.0,
                "ceiling_ft_agl": 4000.0,
                "cloud_layer_count": 3.0,
                "precip_observed": 0,
            },
        ]
    }
    states = MODULE.build_states(fast, metar, {})
    assert states[0]["metar_observation_ts_utc"] == (
        base - timedelta(minutes=30)
    ).isoformat()
    assert states[0]["source_to_metar_temp_gap_c"] == 1.0
    assert states[1]["metar_observation_ts_utc"] == base.isoformat()


def test_exact_bracket_bounds_and_overshoot_label() -> None:
    assert MODULE.bracket_bounds("24 or below") == (-float("inf"), 24.0)
    assert MODULE.bracket_bounds("26 or higher") == (26.0, float("inf"))
    assert MODULE.binary_final_above("26", 25) == 1
    assert MODULE.binary_final_above("25", 25) == 0
    assert MODULE.binary_final_above("24-26", 25) is None


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_loader_routes_dedicated_tokyo_and_knmi_exact_journals(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path
        / "output/live_cross_observations/2026-07-20/high_frequency_observations.jsonl",
        [
            {
                "city": "Tokyo",
                "source": "jma_amedas",
                "source_status": "ok",
                "station": "44166",
                "observation_time_utc": "2026-07-20T01:00:00+00:00",
                "source_first_seen_at_utc": "2026-07-20T01:06:00+00:00",
                "temp_c": 29.1,
            }
        ],
    )
    _write_jsonl(
        tmp_path / "output/knmi_open_data/2026-07-20/knmi_observations.jsonl",
        [
            {
                "city": "Amsterdam",
                "source": "knmi",
                "station": "0-20000-0-06240",
                "observation_time_utc": "2026-07-20T10:00:00+00:00",
                "knmi_first_seen_at_utc": "2026-07-20T10:13:00+00:00",
                "local_detect_ts_utc": "2026-07-20T10:13:00+00:00",
                "temp_c": 21.2,
            }
        ],
    )
    grouped, audit = MODULE.load_fast_events(
        tmp_path,
        MODULE.date.fromisoformat("2026-07-20"),
        MODULE.date.fromisoformat("2026-07-20"),
    )
    assert grouped[("Tokyo", "2026-07-20")][0]["collector_journal"] == (
        "live_cross_observations"
    )
    amsterdam = grouped[("Amsterdam", "2026-07-20")][0]
    assert amsterdam["collector_journal"] == "knmi_open_data"
    assert amsterdam["first_seen_field"] == "knmi_first_seen_at_utc"
    coverage = {row["city"]: row for row in audit}
    assert coverage["Tokyo"]["distinct_collector_exact_events"] == 1
    assert coverage["Amsterdam"]["distinct_collector_exact_events"] == 1


def test_loader_does_not_promote_local_detect_to_exact(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path
        / "output/jma_hot_observations/2026-07-20/high_frequency_observations.jsonl",
        [
            {
                "city": "Tokyo",
                "source": "jma_amedas",
                "source_status": "ok",
                "observation_time_utc": "2026-07-20T01:00:00+00:00",
                "local_detect_ts_utc": "2026-07-20T01:06:00+00:00",
                "temp_c": 29.1,
            }
        ],
    )
    grouped, audit = MODULE.load_fast_events(
        tmp_path,
        MODULE.date.fromisoformat("2026-07-20"),
        MODULE.date.fromisoformat("2026-07-20"),
    )
    assert not grouped
    coverage = {row["city"]: row for row in audit}
    assert coverage["Tokyo"]["missing_exact_first_seen"] == 1
