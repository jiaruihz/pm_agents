from __future__ import annotations

from scripts.ops.low_price_yes_integrated_tail_shadow_v2 import (
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
