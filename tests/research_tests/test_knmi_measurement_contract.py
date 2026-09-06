from dataclasses import replace
import json

import pytest

from weather_city_runtime.next_print_adapters import adapt_source_observation
from weather_city_runtime.next_print_contracts import CanonicalSourceObservation
from weather_data_feed.information_events import canonical_json_hash
from scripts.analysis.forecast_quality.wcir_amsterdam_pilot_v1_1 import write_static_contracts


def source(field="ta"):
    return {
        "target_date": "2026-09-06", "source": "knmi", "station_id": "06240",
        "payload_kind": field, "native_value": 21.4, "payload_hash": "raw-payload",
        "observation_time_utc": "2026-09-06T10:20:00Z",
        "first_seen_at_utc": "2026-09-06T10:25:00Z",
        "ingested_at_utc": "2026-09-06T10:25:02Z",
    }


@pytest.mark.parametrize("field,minutes", [("ta", 1), ("tx", 10), ("tn", 10)])
def test_individual_measurement_windows_preserve_availability(field, minutes):
    from datetime import datetime
    row = adapt_source_observation("Amsterdam", source(field))
    assert (datetime.fromisoformat(row.observed_end_utc.replace("Z", "+00:00"))
            - datetime.fromisoformat(row.observed_start_utc.replace("Z", "+00:00"))).total_seconds() == minutes * 60
    assert row.native_value == 21.4
    assert row.first_seen_at_utc.startswith("2026-09-06T10:25:00")
    assert row.ingested_at_utc.startswith("2026-09-06T10:25:02")
    assert row.payload_hash == "raw-payload"


def test_corrected_interpretation_has_new_id_and_legacy_roundtrips():
    corrected = adapt_source_observation("Amsterdam", source())
    legacy = corrected.to_dict()
    legacy.pop("measurement_contract_id")
    legacy["measurement_kind"] = "preceding_10m_average_ambient_temperature"
    legacy["observed_start_utc"] = legacy["observed_end_utc"]
    identity = {name: legacy[name] for name in (
        "schema_version", "city", "target_date", "source", "native_event_key", "payload_kind", "payload_hash"
    )}
    legacy["observation_id"] = canonical_json_hash(identity)
    original = CanonicalSourceObservation(**legacy)
    assert original.to_dict() == legacy
    assert original.observation_id != corrected.observation_id
    assert original.payload_hash == corrected.payload_hash
    with pytest.raises(ValueError, match="identity"):
        replace(corrected, measurement_kind="wrong-average")


def test_other_city_observation_identity_is_unchanged():
    row = source("temperature_point")
    row["source"] = "fmi"
    result = adapt_source_observation("Helsinki", row).to_dict()
    assert "measurement_contract_id" not in result
    identity = {name: result[name] for name in (
        "schema_version", "city", "target_date", "source", "native_event_key", "payload_kind", "payload_hash"
    )}
    assert canonical_json_hash(identity) == result["observation_id"]


def test_new_contract_generation_keeps_old_seals_immutable(tmp_path):
    write_static_contracts(tmp_path, "2026-09-06T00:00:00Z")
    data = json.loads((tmp_path / "AMSTERDAM_SOURCE_IDENTITY_AND_LABEL_CONTRACT_V3.json").read_text())
    assert data["measurement_window_seconds"] == {"ta": 60, "tx": 600, "tn": 600}
    assert data["source_cadence_seconds"] == 600
    assert "1-minute" in data["knmi_fields"]["ta"]
    (tmp_path / "EVIDENCE_MANIFEST.json").write_text("{}")
    with pytest.raises(ValueError, match="immutable"):
        write_static_contracts(tmp_path, "later")


def test_feature_audit_preserves_varying_temperature_path(monkeypatch):
    import pandas as pd
    import scripts.analysis.forecast_quality.wcir_amsterdam_pilot_v1_1 as pilot
    from scripts.analysis.forecast_quality.wcir_unified_amsterdam_pilot import CORE_FEATURES
    event = {name: 0.0 for name in CORE_FEATURES}
    event.update(event_id="e", decision_vintage_id="e", target_date="2026-09-06",
                 ts_utc="2026-09-06T10:25:00Z", source_obs_ts_utc="2026-09-06T10:20:00Z",
                 latest_fast_native_value=22.0, recent_slope=1.0)
    captured = pd.DataFrame([event])
    path = pd.DataFrame({
        "event_id": ["e"] * 3,
        "observed_at": pd.to_datetime(["2026-09-06T10:00Z", "2026-09-06T10:10Z", "2026-09-06T10:20Z"]),
        "available_at": pd.to_datetime(["2026-09-06T10:05Z", "2026-09-06T10:15Z", "2026-09-06T10:25Z"]),
        "latest_fast_native_value": [20.0, 21.0, 22.0],
        "raw_row_hash": ["a", "b", "c"], "payload_hash": ["a", "b", "c"],
    })
    monkeypatch.setattr(pilot, "make_captured_panel", lambda _: (captured.copy(), {}))
    rows, audit = pilot.build_feature_audit(captured, captured, path)
    features = rows.set_index("feature_name").feature_value
    assert features["recent_slope"] == 1.0
    assert features["path_volatility"] == 1.0
    assert features["latest_fast_native_value"] == 22.0
    assert audit["normalized_path_all_hashes_equal"]


def test_portable_b2_preserves_frozen_training_support():
    import numpy as np
    from weather_modeling.amsterdam_frozen_scorer_v2 import score_b2
    from scripts.analysis.forecast_quality.wcir_unified_amsterdam_pilot import baseline_pmf
    labels = np.array([-1, 0, 0, 1, 1, 2])
    parameters = {"support": list(range(-10, 11)), "training_residual_counts": {-1: 1, 0: 2, 1: 2, 2: 1}, "laplace_alpha_per_support_tick": 1}
    for shift in (-3, 0, 4):
        frozen = baseline_pmf(labels, np.zeros(len(labels)), np.array([shift]))[0]
        portable = score_b2(parameters, {"latest_fast_native_value": 20 + shift, "last_official_native_value": 20})
        np.testing.assert_allclose(portable, frozen, rtol=0, atol=1e-15)
