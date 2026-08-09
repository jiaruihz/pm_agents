from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from scripts.ops import weather_current_yes_heat_death_shadow_v1 as shadow


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, payloads: dict[str, dict]) -> None:
        self.payloads = payloads

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, _url: str, *, params: dict[str, str]) -> _FakeResponse:
        return _FakeResponse(self.payloads[params["token_id"]])


def _snapshot() -> dict:
    common = {
        "city": "Busan",
        "target_date": "2026-07-14",
        "event_date": "2026-07-14",
        "city_local_date_at_snapshot": "2026-07-14",
        "snapshot_ts_utc": "2026-07-14T04:08:00Z",
        "ts_local": "2026-07-14 13:08:00",
        "timezone_name": "Asia/Seoul",
        "unit": "C",
        "forecast_max_native": 31.4,
        "forecast_max_f": 88.52,
        "forecast_peak_hour_local": 12,
        "forecast_peak_delta_hours_local": 1.13,
        "market_id": "m1",
        "condition_id": "c1",
    }
    return {
        "ts_utc": "2026-07-14T04:08:00Z",
        "records": [
            {
                **common,
                "bracket": "30",
                "question": "Will the highest temperature in Busan be 30°C on July 14?",
                "yes_best_ask": 0.84,
                "yes_best_bid": 0.82,
                "yes_ask_size": 20,
                "yes_bid_size": 15,
                "yes_book_status": "ok",
                "yes_token_id": "yes30",
                "market_yes_price": 0.83,
            },
            {
                **common,
                "bracket": "31",
                "question": "Will the highest temperature in Busan be 31°C on July 14?",
                "no_best_ask": 0.83,
                "no_best_bid": 0.81,
                "no_ask_size": 25,
                "no_bid_size": 12,
                "no_book_status": "ok",
                "no_token_id": "no31",
                "market_yes_price": 0.17,
            },
            {
                **common,
                "bracket": "32+",
                "question": "Will the highest temperature in Busan be 32°C or above on July 14?",
                "market_yes_price": 0.01,
            },
        ],
    }


def _observations() -> dict:
    return {
        "schema_version": "weather_data_feed_observation_cache_v1",
        "generated_at_utc": "2026-07-14T04:07:30Z",
        "records": [
            {
                "city": "Busan",
                "target_date": "2026-07-14",
                "status": "ok",
                "source": "aviationweather_metar",
                "station": "RKPK",
                "timezone_name": "Asia/Seoul",
                "unit": "C",
                "fetched_at_utc": "2026-07-14T04:07:30Z",
                "last_obs_utc": "2026-07-14T04:00:00Z",
                "running_max_obs_utc": "2026-07-14T02:00:00Z",
                "minutes_since_running_max": 120,
                "age_min": 7.5,
                "cadence_min": 30,
                "current_temp_c": 29,
                "running_max_c": 30,
                "tmpf_now": 84.2,
                "dwpf_now": 80.6,
                "dewpoint_depression_f": 3.6,
                "relh_now": 88,
                "sknt_now": 10,
                "wind_dir_deg": 200,
                "sky_code_now": "BKN",
                "d_tmpf_1h": -1.8,
                "d_tmpf_3h": 0,
                "raw_metar": "METAR RKPK 140400Z 20010KT -RA BKN020 29/27 Q1006",
            }
        ],
    }


def _curves() -> list[dict]:
    return [
        {
            "city": "Busan",
            "target_date": "2026-07-14",
            "available_at_utc": "2026-07-14T04:00:00Z",
            "latitude": 35.18,
            "longitude": 128.94,
            "hourly_curve": [
                {
                    "time_local": f"2026-07-14T{hour:02d}:00",
                    "temperature_f": temp,
                    "precipitation_probability_pct": precip,
                    "cloud_cover_pct": cloud,
                    "wind_speed_10m_kt": 10,
                    "wind_direction_10m_deg": 205,
                }
                for hour, temp, precip, cloud in [
                    (13, 87, 58, 72),
                    (14, 86, 72, 78),
                    (15, 85, 84, 84),
                    (16, 84, 91, 89),
                ]
            ],
        }
    ]


def test_busan_like_state_is_strong_shadow_candidate_with_two_expressions(tmp_path: Path) -> None:
    snapshot = _snapshot()
    observations, counts = shadow.pit_observation_cache(_observations(), snapshot["ts_utc"])
    assert counts == {"pit_rows": 1}

    from weather_feature_layer.builders import build_weather_state_frame_with_audits
    from weather_feature_layer.contracts import PIT_PROVENANCE_LIVE_CAPTURE
    from weather_feature_layer.store import write_feature_frame_store

    frame, audits = build_weather_state_frame_with_audits(
        snapshot["records"],
        observations,
        forecast_curve_rows=_curves(),
        as_of_ts_utc=snapshot["ts_utc"],
        source_profile_id="fixture",
        input_snapshot_id="fixture_snapshot.json",
        pit_provenance=PIT_PROVENANCE_LIVE_CAPTURE,
        builder_version=shadow.BUILDER_VERSION,
    )
    stored = write_feature_frame_store(frame, tmp_path / "store")
    decisions = shadow.build_decisions(
        snapshot,
        frame.to_dict("records"),
        stored.row_refs,
        snapshot_file="fixture_snapshot.json",
    )

    assert [audit.reason for audit in audits] == ["ok"]
    assert len(decisions) == 1
    row = decisions[0]
    assert row["physical_confirmation_profile"] == "physical_confirmed_strong"
    assert row["decision_window_status"] == "in_research_window_13_17_local"
    assert row["precip_state"] == "rain_or_drizzle"
    assert row["forecast_precip_probability_remaining_3h_max_pct"] == 91
    assert row["current_bracket"] == "30"
    assert row["current_yes_ask"] == 0.84
    assert row["current_yes_bid_size"] == 15
    assert row["fact_signal_candidate_id"] == "c1|BUY_YES"
    assert row["fact_signal_candidate_side"] == "BUY_YES"
    assert row["d1_bracket"] == "31"
    assert row["d1_no_ask"] == 0.83
    assert row["direct_quote_pair_available"] is True
    assert row["zero_notional"] is True
    assert row["no_order_placed"] is True
    assert row["probability_status"] == "not_fitted_forward_collection"
    assert row["late_carry_action"] == "shadow_measure_only"
    assert row["late_carry_running_to_upper_boundary_native"] == 0.5


def test_mechanism_confirmed_after_window_remains_in_denominator_not_candidate() -> None:
    row = shadow._physical_profile(
        {
            "decision_hour_local": 20.75,
            "running_max_c": 30,
            "current_temp_c": 26,
            "forecast_peak_delta_hours_local": 8,
            "minutes_since_running_max": 300,
            "warming_state": "cooling",
            "forecast_cloud_cover_remaining_3h_mean_pct": 100,
            "forecast_precip_probability_remaining_3h_max_pct": 100,
        }
    )

    assert row["mechanism_confirmation_without_time_window"] is True
    assert row["physical_confirmation_base"] is False
    assert row["physical_confirmation_profile"] == "outside_research_window"


def test_missing_candidate_quotes_are_refreshed_read_only(monkeypatch) -> None:
    decisions = [
        {
            "physical_confirmation_base": True,
            "direct_quote_pair_available": False,
            "current_yes_token_id": "yes30",
            "d1_no_token_id": "no31",
        }
    ]
    client = _FakeClient(
        {
            "yes30": {
                "bids": [{"price": "0.80", "size": "11"}, {"price": "0.82", "size": "7"}],
                "asks": [{"price": "0.86", "size": "4"}, {"price": "0.84", "size": "9"}],
                "tick_size": "0.001",
            },
            "no31": {
                "bids": [{"price": "0.79", "size": "8"}],
                "asks": [{"price": "0.83", "size": "12"}],
            },
        }
    )
    monkeypatch.setattr(shadow, "market_httpx_client", lambda *_args, **_kwargs: client)

    counts = shadow.refresh_candidate_quotes(decisions, proxy=None, timeout_sec=1, max_pairs=2)

    assert counts == {"pair_attempts": 1, "pair_successes": 1}
    assert decisions[0]["current_yes_ask"] == 0.84
    assert decisions[0]["current_yes_bid"] == 0.82
    assert decisions[0]["current_yes_bid_size"] == 7
    assert decisions[0]["current_yes_tick_size"] == 0.001
    assert decisions[0]["d1_no_ask"] == 0.83
    assert decisions[0]["direct_quote_pair_available"] is True
    assert decisions[0]["direct_quote_refresh_attempted"] is True


def test_transition_carry_profile_keeps_h1_microstructure_as_diagnostic() -> None:
    profile = shadow._transition_carry_profile(
        {
            "physical_confirmation_strong": True,
            "decision_hour_local": 15.5,
            "running_native": 78.98,
            "expected_report_cadence": 60,
            "daylight_remaining_minutes": 240,
            "taf_transition_available": True,
            "temperature_transition_risk_score": 0.01,
        },
        {
            "current_bracket": "78-79",
            "current_yes_ask": 0.975,
            "current_yes_ask_size": 9,
            "current_yes_bid": 0.941,
            "current_yes_bid_size": 22,
            "current_yes_tick_size": 0.001,
            "current_yes_effective_cost": 0.976,
        },
    )

    assert profile["baseline_h1_late_carry_candidate"] is True
    assert profile["late_carry_timing_bucket"] == "15_to_17_local"
    assert profile["late_carry_timing_progress_13_17"] == 0.625
    assert abs(profile["late_carry_running_to_upper_boundary_native"] - 0.52) < 1e-9
    assert profile["late_carry_remaining_daylight_expected_reports"] == 4
    assert abs(profile["late_carry_current_yes_spread"] - 0.034) < 1e-9
    assert profile["late_carry_fresh_maker_price_proxy"] == 0.942
    assert abs(profile["late_carry_maker_headroom_vs_ask"] - 0.033) < 1e-9
    assert profile["late_carry_maker_queue_ahead_proxy"] == 0.0
    assert profile["late_carry_action"] == "shadow_measure_only"
    assert profile["transition_aware_is_hard_gate"] is False


def test_run_once_rejects_observation_cache_fetched_after_snapshot(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshots"
    curve_dir = tmp_path / "curves" / "2026-07-14"
    output_dir = tmp_path / "output"
    snapshot_dir.mkdir()
    curve_dir.mkdir(parents=True)
    snapshot_path = snapshot_dir / "snapshot_20260714_1308.json"
    snapshot_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
    obs = _observations()
    obs["records"][0]["fetched_at_utc"] = "2026-07-14T04:09:00Z"
    obs_path = tmp_path / "observations.json"
    obs_path.write_text(json.dumps(obs), encoding="utf-8")
    curve_path = curve_dir / "forecast_hourly_curves_fixture.jsonl"
    curve_path.write_text("\n".join(json.dumps(row) for row in _curves()) + "\n", encoding="utf-8")

    summary = shadow.run_once(
        Namespace(
            snapshot_dir=str(snapshot_dir),
            observation_cache=str(obs_path),
            forecast_curve_dir=str(tmp_path / "curves"),
            output_dir=str(output_dir),
            feature_store=str(tmp_path / "store"),
            curve_file_limit=4,
            force=True,
        )
    )

    assert summary["feature_rows"] == 0
    assert summary["decision_rows"] == 0
    assert summary["pit_observation_counts"] == {"cache_fetched_after_snapshot": 1}


def test_run_once_uses_snapshot_availability_as_decision_clock(tmp_path: Path) -> None:
    snapshot = _snapshot()
    snapshot["collection_started_at_utc"] = snapshot["ts_utc"]
    snapshot["available_at_utc"] = "2026-07-14T04:10:00Z"
    assert shadow.snapshot_decision_asof(snapshot) == "2026-07-14T04:10:00Z"


def test_immutable_observation_history_recovers_snapshot_asof_row(tmp_path: Path) -> None:
    latest = _observations()
    latest["records"][0]["fetched_at_utc"] = "2026-07-14T04:09:00Z"
    path = tmp_path / "observations" / "latest.json"
    path.parent.mkdir()
    path.write_text(json.dumps(latest), encoding="utf-8")
    history_dir = path.parent / "2026-07-14"
    history_dir.mkdir()
    historical = {**latest["records"][0], "fetched_at_utc": "2026-07-14T04:07:00Z"}
    (history_dir / "observations.jsonl").write_text(json.dumps(historical) + "\n", encoding="utf-8")

    evidence, counts = shadow.observation_evidence_asof(path, "2026-07-14T04:08:00Z")

    assert len(evidence["records"]) == 1
    assert evidence["records"][0]["fetched_at_utc"] == "2026-07-14T04:07:00Z"
    assert counts["immutable_history_rows_loaded"] == 1
    assert counts["cache_fetched_after_snapshot"] == 1
