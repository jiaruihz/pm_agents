from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LEGACY_DIR = ROOT / "weather_data_feed_service" / "legacy_weather_predict"


def load_paper_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("WEATHER_DATA_FEED_OUTPUT_ROOT", str(tmp_path / "out"))
    monkeypatch.setenv("WEATHER_DATA_FEED_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("WEATHER_DATA_FEED_ROOT", str(ROOT))
    monkeypatch.syspath_prepend(str(LEGACY_DIR))
    monkeypatch.syspath_prepend(str(ROOT))
    for name in ("paper_snapshot", "pm_edge_compare", "calibration_backtest", "city_pools"):
        sys.modules.pop(name, None)
    return importlib.import_module("paper_snapshot")


def test_strategy_snapshot_uses_canonical_observation_values(monkeypatch, tmp_path):
    module = load_paper_snapshot(monkeypatch, tmp_path)
    index = {
        ("London", "2026-08-08"): {
            "status": "ok",
            "source": "aviationweather_metar",
            "running_max_c": 28.0,
            "current_temp_c": 26.0,
            "last_obs_utc": "2026-08-08T18:50:00+00:00",
            "n_obs": 40,
        }
    }

    state = module.metar_state_from_observation_cache(index, "London", "2026-08-08")

    assert state == {
        "metar_current_max_f": 82,
        "metar_latest_temp_f": 79,
        "metar_latest_ts_utc": "2026-08-08T18:50:00+00:00",
        "metar_obs_count_today": 40,
        "metar_source": "observation_cache:aviationweather_metar",
    }


def test_strategy_snapshot_rejects_stale_observation_cache(monkeypatch, tmp_path):
    module = load_paper_snapshot(monkeypatch, tmp_path)
    cache_path = tmp_path / "observations.json"
    cache_path.write_text(
        '{"schema_version":"weather_data_feed_observation_cache_v1",'
        '"generated_at_utc":"2026-08-08T18:00:00Z","records":[]}',
        encoding="utf-8",
    )

    try:
        module.load_strategy_observation_index(
            cache_path,
            datetime(2026, 8, 8, 19, 0, tzinfo=timezone.utc),
            900,
        )
    except RuntimeError as exc:
        assert "observation cache stale" in str(exc)
    else:
        raise AssertionError("stale observation cache must fail closed")
