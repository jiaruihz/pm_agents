from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_model_evaluation import pooled_ladder_transport as subject


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_conservative_weather_transport_projects_to_full_probability_mass() -> None:
    before, after, delta = subject.conservative_weather_transport(
        [0.10, 0.55, 0.30],
        [0.02, 0.38, 0.72],
    )

    assert np.isclose(before.sum(), 1.0)
    assert np.isclose(after.sum(), 1.0)
    assert np.isclose(delta.sum(), 0.0)
    assert np.allclose(delta, after - before)
    assert (before >= 0).all() and (after >= 0).all()


def test_readiness_accepts_registered_15s_but_refuses_hot_strip_binary_adapter(
    tmp_path: Path,
) -> None:
    knmi_events = tmp_path / "knmi_events.jsonl"
    knmi_captures = tmp_path / "knmi_captures.jsonl"
    high_frequency = tmp_path / "high_frequency.jsonl"
    ws_features = tmp_path / "ws_features"
    ws_epochs = tmp_path / "ws_epochs"
    weather_delta = tmp_path / "missing_weather_delta.jsonl"
    output_dir = tmp_path / "output"
    _write_jsonl(
        knmi_events,
        [
            {
                "city": "Amsterdam",
                "source": "knmi",
                "information_event_id": "a1",
                "event_role": "new_content",
                "material_state_change": True,
                "first_seen_at_utc": "2026-08-09T10:00:00Z",
            }
        ],
    )
    _write_jsonl(
        knmi_captures,
        [
            {
                "source_event_id": "a1",
                "target_date": "2026-08-09",
                "scheduled_offset_seconds": horizon,
                "ladder_market_count": 9,
            }
            for horizon in subject.HORIZONS_SEC
        ],
    )
    _write_jsonl(
        high_frequency,
        [
            {
                "city": city,
                "source": source,
                "information_event_id": city,
                "event_role": "new_content",
                "information_event_status": "material",
                "target_date": "2026-08-09",
            }
            for city, source in (
                ("Helsinki", "fmi"),
                ("Tokyo", "jma_amedas"),
                ("Busan", "amos_runway"),
                ("Seoul", "amos_runway"),
            )
        ],
    )
    _write_jsonl(
        ws_features / "events.jsonl",
        [
            {
                "city": "Helsinki",
                "information_event_id": "Helsinki",
                "target_date": "2026-08-09",
                "slot": slot,
                "ladder_scope": "subscription_hot_strip",
                "rung_count": 3,
                "scorable_status": "scorable",
            }
            for slot in subject.REQUIRED_SLOTS
        ],
    )
    _write_jsonl(ws_epochs / "epochs.jsonl", [])

    result = subject.run(
        argparse.Namespace(
            run_id="fixture",
            knmi_events=knmi_events,
            knmi_captures=knmi_captures,
            high_frequency_observations=high_frequency,
            ws_feature_root=ws_features,
            ws_epoch_root=ws_epochs,
            weather_delta=weather_delta,
            output_dir=output_dir,
            entrypoint_path=Path("fixture.py"),
        )
    )

    assert result["status"] == "BLOCKED_DATA"
    assert not any("Amsterdam:REST_event_capture_missing" in value for value in result["blockers"])
    assert (
        "Helsinki:full_ladder_pre_t0_15_30_60s_market_panel_missing"
        in result["blockers"]
    )
    assert result["model_contract"]["pooled_universe"] == [
        "Amsterdam",
        "Helsinki",
        "Tokyo",
    ]
    assert result["model_contract"]["negative_controls"] == ["Busan", "Seoul"]
    assert result["orders_submitted"] == 0
    assert (output_dir / "readiness.json").exists()
    assert (output_dir / "run_manifest.json").exists()


def test_weather_delta_census_requires_mass_conservation(tmp_path: Path) -> None:
    path = tmp_path / "weather_delta.jsonl"
    rows = []
    for city in subject.POOLED_CITIES:
        for bracket, before, after in (("20", 0.4, 0.3), ("21", 0.6, 0.7)):
            rows.append(
                {
                    "city": city,
                    "information_event_id": f"{city}-event",
                    "target_date": "2026-08-09",
                    "bracket": bracket,
                    "p_weather_before": before,
                    "p_weather_after": after,
                    "delta_p_weather": after - before,
                }
            )
    _write_jsonl(path, rows)

    census = subject._weather_delta_census(path)

    assert all(
        census["by_city"][city]["conserved_full_ladder_events"] == 1
        for city in subject.POOLED_CITIES
    )
