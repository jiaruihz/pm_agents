import json
import gzip
from datetime import date, timedelta

import pandas as pd

from weather_model_evaluation.daily_minimum_next_colder import (
    attach_same_checkpoint_market,
    build_next_colder_panel,
    run_daily_minimum_next_colder_development,
)


def _write_fixture(tmp_path, *, dates=12):
    forecast_root = tmp_path / "forecast"
    observation_root = tmp_path / "observations"
    ladder_root = tmp_path / "market_ladder_snapshots"
    start = date(2026, 7, 1)
    for offset in range(dates):
        target = start + timedelta(days=offset)
        forecast_dir = forecast_root / target.isoformat()
        forecast_dir.mkdir(parents=True, exist_ok=True)
        forecast = {
            "city": "Tokyo",
            "target_date": target.isoformat(),
            "forecast_timezone": "Asia/Tokyo",
            "available_at_utc": f"{target.isoformat()}T00:00:00Z",
            "forecast_model": "ecmwf",
            "forecast_values_hash": f"forecast-{offset}",
            "hourly_curve": [
                {
                    "time_local": f"{target.isoformat()}T06:00",
                    "temperature_f": 68.0,
                },
                {
                    "time_local": f"{target.isoformat()}T21:00",
                    "temperature_f": 66.2 if offset % 2 else 69.8,
                },
                {
                    "time_local": f"{target.isoformat()}T23:00",
                    "temperature_f": 64.4 if offset % 3 == 0 else 68.0,
                },
            ],
        }
        (forecast_dir / "curves.jsonl").write_text(json.dumps(forecast) + "\n")
        observation_dir = observation_root / target.isoformat()
        observation_dir.mkdir(parents=True, exist_ok=True)
        observations = []
        temperatures = [20.0, 22.0, 23.0, 21.0, 20.0]
        if offset % 3 == 0:
            temperatures[-1] = 18.0  # two-rung overshoot: no-touch false, exact-next NO true
        elif offset % 2:
            temperatures[-1] = 19.0  # exactly next colder: both labels false
        for hour, temperature in zip((5, 9, 12, 18, 23), temperatures):
            observed_utc_hour = (hour - 9) % 24
            observed_utc_date = target if hour >= 9 else target - timedelta(days=1)
            available_hour = observed_utc_hour
            available_date = observed_utc_date
            row = {
                "city": "Tokyo",
                "target_date": target.isoformat(),
                "timezone_name": "Asia/Tokyo",
                "source": "fixture_point_source",
                "station": "FIXTURE",
                "last_obs_utc": f"{observed_utc_date.isoformat()}T{observed_utc_hour:02d}:00:00+00:00",
                "available_at_utc": f"{available_date.isoformat()}T{available_hour:02d}:05:00+00:00",
                "ingested_at_utc": f"{available_date.isoformat()}T{available_hour:02d}:05:01+00:00",
                "current_temp_c": temperature,
                "relative_humidity_pct": 70,
                "wind_speed_kt": 3,
            }
            observations.append(row)
            # Later duplicate polling row must not replace first-seen identity.
            observations.append({**row, "available_at_utc": f"{available_date.isoformat()}T{available_hour:02d}:10:00+00:00"})
        (observation_dir / "observations.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in observations)
        )
    return forecast_root, observation_root, ladder_root


def test_next_colder_panel_separates_touch_from_exact_no_semantics(tmp_path):
    forecast_root, observation_root, _ladder_root = _write_fixture(tmp_path, dates=3)
    panel = build_next_colder_panel(
        forecast_root=forecast_root,
        observation_root=observation_root,
        cities=["Tokyo"],
        checkpoint_hours_local=(6,),
    )

    first = panel[panel["target_date"].eq("2026-07-01")].iloc[0]
    assert first["running_min_native"] == 20
    assert first["next_colder_native"] == 19
    assert first["final_min_proxy_native"] == 18
    assert first["label_no_next_colder_touch"] == 0
    assert first["label_next_colder_exact_no_proxy"] == 1
    assert first["first_seen_ts_utc"].endswith("20:05:00+00:00")


def test_next_colder_runner_emits_oof_and_blocks_production(tmp_path):
    forecast_root, observation_root, ladder_root = _write_fixture(tmp_path)
    output = tmp_path / "output"
    summary = run_daily_minimum_next_colder_development(
        forecast_root=forecast_root,
        observation_root=observation_root,
        ladder_root=ladder_root,
        output_dir=output,
        cities=["Tokyo"],
        checkpoint_hours_local=(6, 12, 18, 23),
        min_train_dates=4,
        code_revision="fixture",
    )

    assert summary["status"] == "research_only_blocked_for_settlement_and_market_forward"
    assert summary["development_model"]["status"] == "development_proxy_only"
    assert summary["development_model"]["target_dates"] == 8
    assert summary["same_row_market_baseline"]["status"].startswith("blocked_")
    assert summary["production"] == {
        "probability_artifact_emitted": False,
        "signal_candidates": 0,
        "trade_intents": 0,
        "orders_submitted": 0,
        "fills": 0,
        "execution_mode": "research_only",
    }
    prediction = pd.read_csv(output / "prediction_table.csv")
    assert set(prediction["target_kind"]) == {"physical_path", "market_expression"}
    assert prediction["market_p"].isna().all()
    assert (output / "next_colder_checkpoint_panel.csv").exists()
    assert (output / "next_colder_oof.csv").exists()
    assert (output / "summary.json").exists()


def test_market_attachment_uses_complete_archive_not_transport_subbatch(tmp_path):
    ladder_root = tmp_path / "market_ladder_snapshots"
    batch_dir = tmp_path / "market_books" / "batches" / "2026-07-01"
    batch_dir.mkdir(parents=True)
    rows = []
    for bracket, condition in (("19", "tail-low"), ("20", "exact-20"), ("21+", "tail-high")):
        for outcome, bid, ask in (("yes", 0.19, 0.21), ("no", 0.79, 0.81)):
            rows.append(
                {
                    "city": "Tokyo",
                    "event_date": "2026-07-01",
                    "extreme_kind": "min",
                    "snapshot_ts_utc": "2026-07-01T09:00:00Z",
                    "available_at_utc": "2026-07-01T09:00:05Z",
                    "request_batch_capture_id": f"different-{condition}-{outcome}",
                    "condition_id": condition,
                    "bracket": bracket,
                    "outcome": outcome,
                    "summary": {"best_bid": bid, "best_ask": ask},
                    "raw": {"bids": [{"price": bid}], "asks": [{"price": ask}]},
                }
            )
    with gzip.open(batch_dir / "market_books_fixture.jsonl.gz", "wt") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    panel = pd.DataFrame(
        [
            {
                "city": "Tokyo",
                "target_date": "2026-07-01",
                "decision_ts_utc": "2026-07-01T10:00:00Z",
                "next_colder_native": 20,
            }
        ]
    )

    attached = attach_same_checkpoint_market(
        panel, ladder_root=ladder_root, cities=["Tokyo"]
    )

    assert attached.loc[0, "market_scorable_status"] == "scorable"
    assert 0 < attached.loc[0, "market_p_next_colder_exact_no"] < 1
    assert attached.loc[0, "market_next_colder_no_best_ask"] == 0.81
