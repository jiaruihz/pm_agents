from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from scripts.ops import weather_current_yes_core_carry_event_rescore_shadow_v1 as runner


def weather_row(*, observation_epoch: str, curve_temp: float = 20.0) -> dict:
    return {
        "city": "London",
        "target_date": "2026-08-12",
        "snapshot_file": f"snapshot-{observation_epoch}.json",
        "decision_snapshot_ts_utc": "2026-08-12T14:40:00Z",
        "decision_hour_local": 15.0,
        "current_bracket": "20",
        "current_question": "Will the highest temperature in London be 20°C?",
        "current_yes_token_id": "token-20",
        "source_report_ts_utc": observation_epoch,
        "obs_source": "aviationweather_metar",
        "obs_status": "ok",
        "station_gap_state": "within_expected_cadence",
        "obs_age_min": 10.0,
        "expected_report_cadence": 30.0,
        "dewpoint_depression_f": 6.0,
        "wind_speed_kt": 8.0,
        "forecast_source": "open_meteo_live_ecmwf",
        "forecast_peak_delta_hours_local": 0.5,
        "hourly_curve": [
            {
                "time_local": "2026-08-12T16:00",
                "temperature_f": curve_temp,
            }
        ],
    }


def full_book() -> dict:
    return {
        "status": "ok",
        "fetched_at_utc": "2026-08-12T14:40:01Z",
        "bid": 0.70,
        "bid_size": 20.0,
        "ask": 0.72,
        "ask_size": 20.0,
        "tick_size": 0.01,
        "bids": [{"price": "0.70", "size": "20"}],
        "asks": [{"price": "0.72", "size": "20"}],
    }


def test_transition_types_separate_metar_forecast_and_rebracket() -> None:
    prior = runner.state_signature(weather_row(observation_epoch="2026-08-12T14:00:00Z"))
    changed = weather_row(
        observation_epoch="2026-08-12T14:30:00Z",
        curve_temp=21.0,
    )
    changed["current_bracket"] = "21"
    changed["current_yes_token_id"] = "token-21"
    current = runner.state_signature(changed)
    assert runner.transition_types(prior, current) == [
        "new_metar",
        "forecast_revision",
        "exact_bracket_transition",
    ]


def test_read_appended_jsonl_leaves_partial_suffix(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"city":"London"}\n{"city":"Paris"')
    rows, offset, status = runner.read_appended_jsonl(path, 0)
    assert status == "ok"
    assert rows == [{"city": "London"}]
    assert offset == len(b'{"city":"London"}\n')


def test_run_once_bootstraps_then_scores_first_new_event(
    tmp_path: Path, monkeypatch
) -> None:
    core_runtime = tmp_path / "core"
    output_dir = tmp_path / "shadow"
    core_runtime.mkdir()
    source = core_runtime / "state_decisions.jsonl"
    source.write_text(
        json.dumps(weather_row(observation_epoch="2026-08-12T13:30:00Z")) + "\n",
        encoding="utf-8",
    )
    (core_runtime / "live_orders.jsonl").write_text("", encoding="utf-8")

    @contextmanager
    def fake_client(*_args, **_kwargs):
        yield object()

    monkeypatch.setattr(runner, "market_httpx_client", fake_client)
    monkeypatch.setattr(
        runner.core_signal,
        "fetch_full_book",
        lambda _client, _token: full_book(),
    )
    args = SimpleNamespace(
        core_runtime=str(core_runtime),
        output_dir=str(output_dir),
        artifact=str(runner.DEFAULT_ARTIFACT),
        market_proxy="http://127.0.0.1:7896",
        book_timeout_sec=5.0,
        max_books_per_run=40,
        max_books_per_utc_day=2000,
    )

    bootstrap = runner.run_once(args)
    assert bootstrap["source_status"] == "bootstrap_at_end"
    assert bootstrap["real_orders"] == 0

    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(weather_row(observation_epoch="2026-08-12T14:00:00Z"))
            + "\n"
        )
    seeded = runner.run_once(args)
    assert seeded["event_transitions"] == 0
    assert seeded["event_scores_written"] == 0

    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(weather_row(observation_epoch="2026-08-12T14:30:00Z"))
            + "\n"
        )
    scored = runner.run_once(args)
    assert scored["event_transitions"] == 1
    assert scored["event_scores_written"] == 1
    assert scored["books_used"] == 1
    score = json.loads((output_dir / "event_scores.jsonl").read_text().splitlines()[0])
    assert score["event_types"] == ["new_metar"]
    assert score["model_probability_hold"] is not None
    assert score["market_mid_band"] == "mid_0_50_to_0_80"
    assert score["zero_notional"] is True
    assert score["trade_intent_created"] is False
    assert score["order_created"] is False
