from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "weather_data_feed_service/legacy_weather_predict/paper_snapshot.py"
SPEC = importlib.util.spec_from_file_location("paper_snapshot_curve_publish", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def _record() -> dict[str, str]:
    return {
        "city": "Wellington",
        "target_date": "2026-08-08",
        "forecast_model": "ecmwf",
        "forecast_values_hash": "curve-v1",
    }


def test_cached_durable_curve_can_satisfy_snapshot_publish_evidence(tmp_path) -> None:
    archive = tmp_path / "forecast_hourly_curves.jsonl"
    archive.write_text("{}\n", encoding="utf-8")
    record = _record()

    evidence = runner.forecast_curve_publish_evidence(
        [record],
        [],
        [{**record, "curve_archive_path": str(archive)}],
    )

    assert evidence["publishable"] is True
    assert evidence["fresh_curve_matches"] == 0
    assert evidence["cached_curve_matches"] == 1
    assert evidence["missing_count"] == 0


def test_missing_cached_curve_archive_still_fails_closed(tmp_path) -> None:
    record = _record()

    evidence = runner.forecast_curve_publish_evidence(
        [record],
        [],
        [{**record, "curve_archive_path": str(tmp_path / "missing.jsonl")}],
    )

    assert evidence["publishable"] is False
    assert evidence["cached_curve_matches"] == 0
    assert evidence["missing_count"] == 1


def test_fresh_curve_satisfies_snapshot_publish_evidence() -> None:
    record = _record()

    evidence = runner.forecast_curve_publish_evidence([record], [record], [])

    assert evidence["publishable"] is True
    assert evidence["fresh_curve_matches"] == 1
    assert evidence["cached_curve_matches"] == 0
