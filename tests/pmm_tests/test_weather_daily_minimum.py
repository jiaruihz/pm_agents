import json
from datetime import date, timedelta

from weather_model_evaluation.daily_minimum import run_daily_minimum_development


def test_daily_minimum_builds_pit_panel_and_blocks_production_artifact(tmp_path):
    forecast_root = tmp_path / "forecast"
    observation_root = tmp_path / "observations"
    ladder_root = tmp_path / "ladders"
    start = date(2026, 7, 1)
    for offset in range(10):
        target = start + timedelta(days=offset)
        capture_day = target - timedelta(days=1)
        curve_dir = forecast_root / capture_day.isoformat()
        curve_dir.mkdir(parents=True, exist_ok=True)
        row = {
            "city": "Seoul",
            "target_date": target.isoformat(),
            "forecast_timezone": "Asia/Seoul",
            "available_at_utc": f"{capture_day.isoformat()}T08:00:00Z",
            "forecast_model": "ecmwf",
            "forecast_source": "fixture",
            "forecast_values_hash": f"hash-{offset}",
            "forecast_model_fallback": False,
            "hourly_curve": [
                {
                    "time_local": f"{target.isoformat()}T06:00",
                    "temperature_f": 68.0 + offset,
                    "cloud_cover_pct": 20,
                    "precipitation_probability_pct": 0,
                    "wind_speed_10m_kt": 4,
                },
                {
                    "time_local": f"{target.isoformat()}T21:00",
                    "temperature_f": 72.0 + offset,
                    "cloud_cover_pct": 30,
                    "precipitation_probability_pct": 10,
                    "wind_speed_10m_kt": 5,
                },
            ],
        }
        (curve_dir / f"curve-{offset}.jsonl").write_text(json.dumps(row) + "\n")
        obs_dir = observation_root / target.isoformat()
        obs_dir.mkdir(parents=True, exist_ok=True)
        obs = {
            "city": "Seoul",
            "target_date": target.isoformat(),
            "current_temp_c": 20.0 + offset * 5.0 / 9.0,
        }
        (obs_dir / "observations.jsonl").write_text(json.dumps(obs) + "\n")

    ladder_dir = ladder_root / "2026-07-10"
    ladder_dir.mkdir(parents=True)
    (ladder_dir / "market_ladder_snapshot_fixture.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "city": "Seoul",
                        "target_date": "2026-07-10",
                        "extreme_kind": "min",
                    }
                ]
            }
        )
    )

    output = tmp_path / "output"
    summary = run_daily_minimum_development(
        forecast_root=forecast_root,
        observation_root=observation_root,
        ladder_root=ladder_root,
        output_dir=output,
        cities=["Seoul"],
        min_train_dates=3,
    )

    assert summary["forecast_rows"] == 10
    assert summary["proxy_label_dates_by_city"] == {"Seoul": 10}
    assert summary["minimum_full_ladder_dates_by_city"] == {"Seoul": 1}
    assert summary["w0_weather_only_development"]["status"] == "development_proxy_only"
    assert summary["production"]["probability_artifact_emitted"] is False
    assert summary["production"]["orders_submitted"] == 0
    assert (output / "daily_minimum_checkpoint_panel.csv").exists()
    assert (output / "daily_minimum_w0_walk_forward.csv").exists()
    assert (output / "summary.json").exists()
