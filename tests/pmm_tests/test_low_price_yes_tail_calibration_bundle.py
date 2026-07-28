from __future__ import annotations

from scripts.ops.low_price_yes_integrated_tail_shadow_v2 import (
    build_shadow_row,
    load_pcal_v2_resources,
)
from src.strategies.weather_edge_v1.tools.low_price_yes_tail_telemetry import (
    build_low_price_yes_tail_telemetry,
    load_tail_telemetry_resources,
)


def test_deployable_tail_calibration_bundle_loads_without_generated_docs() -> None:
    resources = load_tail_telemetry_resources()

    assert resources.load_error == ""
    assert resources.bias_index
    assert resources.forecast_calibration_index
    assert "low_price_yes_tail_calibration_v1.json.gz" in resources.bias_path

    row = {
        "city": "London",
        "event_date": "2026-07-20",
        "forecast_source": "ecmwf",
        "decision_entry_price": 0.10,
        "model_p_yes": 0.22,
        "edge": 0.12,
        "bracket": "27",
        "forecast_max_native": 25.0,
        "unit": "C",
        "decision_snapshot_ts_utc": "2026-07-19T20:00:00Z",
        "forecast_timezone": "Europe/London",
    }
    telemetry = build_low_price_yes_tail_telemetry(row, resources)

    assert telemetry["tail_telemetry_status"] == "ok"
    assert telemetry["bias_n_asof"] > 0
    assert telemetry["forecast_source_calibration_status"] in {
        "ok",
        "no_active_source_profile",
    }


def test_pcal_v2_uses_the_same_deployable_bias_index() -> None:
    resources = load_tail_telemetry_resources()
    pcal = load_pcal_v2_resources(resources)

    assert pcal is not None
    assert pcal["bias_index"] is resources.bias_index
    assert pcal["frozen"]["train_end"] == "2026-06-20"


def test_integrated_shadow_tags_rain_convective_candidate(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.ops.low_price_yes_integrated_tail_shadow_v2.build_low_price_yes_tail_telemetry",
        lambda _row, _resources: {
            "tail_telemetry_status": "ok",
            "weather_regime": "rain_convective",
        },
    )
    monkeypatch.setattr(
        "scripts.ops.low_price_yes_integrated_tail_shadow_v2.pcal_v2_tags",
        lambda _row, _resources: {"pcal_v2_status": "ok"},
    )
    monkeypatch.setattr(
        "scripts.ops.low_price_yes_integrated_tail_shadow_v2.classify_live_metar",
        lambda _row, _obs, _args: {"live_metar_regime_score": 0},
    )
    monkeypatch.setattr(
        "scripts.ops.low_price_yes_integrated_tail_shadow_v2.source_aware_v3",
        lambda _row: (False, "test"),
    )
    row = {
        "city": "London",
        "event_date": "2026-07-29",
        "bracket": "27",
        "side": "BUY_YES",
        "candidate_id": "candidate",
        "condition_id": "condition",
        "market_id": "market",
        "unit": "C",
        "decision_snapshot_ts_utc": "2026-07-28T20:00:00Z",
        "first_seen_ts_utc": "2026-07-28T20:00:00Z",
        "last_seen_ts_utc": "2026-07-28T20:00:00Z",
        "decision_hours_to_settle": 23.0,
        "decision_entry_price": 0.10,
        "model_p_yes": 0.25,
        "market_yes_price": 0.10,
        "edge": 0.15,
        "forecast_source": "open_meteo_live_ecmwf",
        "forecast_peak_source": "open_meteo_live_ecmwf",
        "forecast_max_native": 26.0,
        "forecast_max_f": 78.8,
        "forecast_peak_hour_local": 15,
        "forecast_peak_delta_hours_local": 8.0,
        "forecast_max_in_bracket": 0,
        "forecast_max_above_bracket_f": 0.0,
        "forecast_max_below_bracket_f": 1.8,
        "yes_spread": 0.01,
        "yes_depth_ask_5c": 20.0,
        "settlement_status": None,
        "final_yes": None,
        "bracket_hit": None,
        "fact_built_at_utc": "2026-07-28T20:00:00Z",
    }
    args = type("Args", (), {"max_obs_age_min": 30.0, "db": "runtime/weather.db"})()

    tagged = build_shadow_row(
        row,
        cycle_id="2026-07-28T20:00:00Z",
        args=args,
        tail_resources=None,
        obs_index={},
        obs_meta={},
        pcal_v2_resources=None,
    )

    assert tagged["heada_rain_convective_shadow_v1"] is True
    assert tagged["heada_rain_convective_shadow_policy"] == "diagnostic_only_not_live_selector"
