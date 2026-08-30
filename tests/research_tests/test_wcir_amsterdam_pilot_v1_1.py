from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import numpy as np
import pandas as pd
import pytest

from scripts.analysis.forecast_quality.wcir_amsterdam_pilot_rev2 import _exact_record
from scripts.analysis.forecast_quality.wcir_unified_amsterdam_pilot import make_historical_panel
from scripts.analysis.forecast_quality.wcir_amsterdam_pilot_v1_1 import (
    ARTIFACTS,
    DEFAULT_OUTPUT,
    append_shadow_prediction,
    normalized_weights,
    verify,
    verify_zip,
)
from weather_city_runtime import (
    CITY_CONTRACTS,
    adapt_official_print,
    adapt_source_observation,
    link_next_official_print,
)
from weather_modeling.amsterdam_feature_builder_v2 import AmsterdamFeatureBuilderV2
from weather_modeling.amsterdam_frozen_scorer_v2 import score_frozen_models


def _json(name: str) -> dict:
    return json.loads((DEFAULT_OUTPUT / name).read_text(encoding="utf-8"))


def _path() -> pd.DataFrame:
    return pd.DataFrame({
        "observed_at": pd.to_datetime([
            "2026-08-20T09:30Z", "2026-08-20T09:40Z",
            "2026-08-20T09:50Z", "2026-08-20T10:00Z",
        ], utc=True),
        "available_at": pd.to_datetime([
            "2026-08-20T09:30:02Z", "2026-08-20T09:40:02Z",
            "2026-08-20T09:50:02Z", "2026-08-20T10:00:02Z",
        ], utc=True),
        "latest_fast_native_value": [10.0, 12.0, 9.0, 11.0],
        "source_observation_id": ["o1", "o2", "o3", "o4"],
    })


def _build(frame: pd.DataFrame, availability_class: str = "CAPTURED_PIT_ARCHIVE", *, complete: bool = True):
    return AmsterdamFeatureBuilderV2.build_vintage(
        frame,
        decision_vintage_id="fixture",
        feature_cutoff_at="2026-08-20T10:00:05Z",
        decision_ready_at="2026-08-20T10:00:06Z",
        observation_cutoff_at="2026-08-20T10:00:00Z",
        availability_class=availability_class,
        feature_names=AmsterdamFeatureBuilderV2.path_features,
        source_path_complete=complete,
    )


def test_01_ta_tx_tn_semantic_contract() -> None:
    semantics = CITY_CONTRACTS["Amsterdam"].source_measurement_semantics
    assert semantics["ta"] == "preceding_10m_average_ambient_temperature"
    assert semantics["tx"] == "preceding_10m_maximum_ambient_temperature"
    assert semantics["tn"].endswith("UNAVAILABLE_IN_FROZEN_INPUT")


def test_02_three_source_identities_are_distinct() -> None:
    contract = CITY_CONTRACTS["Amsterdam"]
    assert len({contract.fast_source_id, contract.prediction_target_source_id, contract.settlement_source_id}) == 3


def test_03_next_report_matching_and_equal_rank_conflict_fail_closed() -> None:
    contract = CITY_CONTRACTS["Amsterdam"]
    source = adapt_source_observation("Amsterdam", {
        "target_date": "2026-08-20", "source": contract.fast_source,
        "station": "EHAM", "observation_time_utc": "2026-08-20T10:00:00Z",
        "source_first_seen_at_utc": "2026-08-20T10:00:02Z",
        "available_at_utc": "2026-08-20T10:00:02Z", "ingested_at_utc": "2026-08-20T10:00:02Z",
        "temp_c": 24.1, "payload_kind": "ta", "payload_hash": "source", "pit_lineage_class": "collector_exact",
    })
    def official(observed: str, seen: str, payload: str):
        return adapt_official_print("Amsterdam", {
            "target_date": "2026-08-20", "official_source": contract.official_source,
            "station": "EHAM", "observation_time_utc": observed,
            "first_seen_at_utc": seen, "available_at_utc": seen,
            "temp_c": 24.0, "payload_hash": payload,
        })
    later = official("2026-08-20T10:20:00Z", "2026-08-20T10:20:15Z", "later")
    earlier = official("2026-08-20T10:10:00Z", "2026-08-20T10:10:15Z", "earlier")
    assert link_next_official_print(source, [later, earlier], contract).official_print_id == earlier.official_print_id
    conflict = official("2026-08-20T10:10:00Z", "2026-08-20T10:10:15Z", "conflict")
    link = link_next_official_print(source, [earlier, conflict], contract)
    assert link.status == "excluded" and link.exclusion_reason == "ambiguous_official_print"


def test_04_historical_captured_shadow_share_one_builder_identity() -> None:
    hashes = {_build(_path(), archive).feature_vector_hash for archive in (
        "HISTORICAL_FINAL_ARCHIVE", "CAPTURED_PIT_ARCHIVE", "PROSPECTIVE_SHADOW"
    )}
    assert len(hashes) == 1


def test_05_full_path_vs_sparse_opportunity_path_fails_closed() -> None:
    full = _build(_path())
    dropped = _build(_path().drop(index=2).reset_index(drop=True))
    sparse = _build(_path().iloc[[0, 3]].copy(), complete=False)
    assert full.feature_vector_hash != dropped.feature_vector_hash
    assert dropped.status == "INCOMPLETE_CAPTURED_SOURCE_PATH"
    assert sparse.status == "INCOMPLETE_CAPTURED_SOURCE_PATH"


def test_06_future_observation_injection_is_invariant() -> None:
    frame = _path()
    future = pd.DataFrame({
        "observed_at": [pd.Timestamp("2026-08-20T10:10Z")],
        "available_at": [pd.Timestamp("2026-08-20T10:10:02Z")],
        "latest_fast_native_value": [99.0], "source_observation_id": ["future"],
    })
    assert _build(frame).feature_vector_hash == _build(pd.concat([frame, future], ignore_index=True)).feature_vector_hash


def test_07_feature_available_cutoff_decision_clock_order() -> None:
    result = _build(_path())
    assert result.status == "OK"
    assert pd.to_datetime(result.lineage["feature_available_at"], utc=True).max() <= pd.Timestamp("2026-08-20T10:00:05Z")
    assert pd.Timestamp("2026-08-20T10:00:05Z") <= pd.Timestamp("2026-08-20T10:00:06Z")


def test_08_official_print_group_never_crosses_folds() -> None:
    for suffix in ("FULL_CHECKPOINT", "OPPORTUNITY_MATCHED", "CAPTURED_PIT"):
        frame = pd.read_parquet(DEFAULT_OUTPUT / f"B2_PREDICTIONS_{suffix}.parquet")
        assert frame.groupby("official_print_group_id")["fold"].nunique().max() == 1


def test_09_official_group_and_date_weighting() -> None:
    frame = pd.DataFrame({
        "target_date": ["a", "a", "a", "b"],
        "official_print_group_id": ["g1", "g1", "g2", "g3"],
    })
    weighted = frame.assign(weight=normalized_weights(frame, "primary"))
    assert np.allclose(weighted.groupby("target_date").weight.sum(), [0.5, 0.5])
    assert np.allclose(weighted.loc[weighted.target_date.eq("a")].groupby("official_print_group_id").weight.sum(), [0.25, 0.25])
    summary = _json("AMSTERDAM_COHORT_SUMMARY.json")
    assert summary["P0_FULL_CHECKPOINT_WEATHER"]["raw_rows"] == 249650
    assert summary["P0_FULL_CHECKPOINT_WEATHER"]["model_scored_rows"] < 249650


def test_10_b2_m1_m2_share_identical_denominators() -> None:
    summary = _json("AMSTERDAM_COHORT_SUMMARY.json")
    comparison = _json("AMSTERDAM_MODEL_COMPARISON_V2.json")
    for suffix in ("FULL_CHECKPOINT", "OPPORTUNITY_MATCHED", "CAPTURED_PIT"):
        frames = [pd.read_parquet(DEFAULT_OUTPUT / f"{model}_PREDICTIONS_{suffix}.parquet") for model in ("B2", "M1", "M2")]
        identifiers = [frame.decision_vintage_id.tolist() for frame in frames]
        assert identifiers[0] == identifiers[1] == identifiers[2]
        cohort_id = frames[0]["cohort_id"].iloc[0]
        assert len(frames[0]) == summary[cohort_id]["model_scored_rows"]
        assert all(comparison["cohorts"][cohort_id]["models"][model]["primary"]["raw_rows"] == len(frames[0]) for model in ("B2", "M1", "M2"))


def test_11_m2_captured_pit_evaluation_is_present() -> None:
    frame = pd.read_parquet(DEFAULT_OUTPUT / "M2_PREDICTIONS_CAPTURED_PIT.parquet")
    audit = _json("AMSTERDAM_FEATURE_PARITY_AUDIT.json")
    assert len(frame) == frame.decision_vintage_id.nunique() == audit["captured_complete_path"]
    assert audit["captured_complete_path"] + audit["captured_fail_closed_incomplete_path"] == 87


def test_12_market_archive_exact_identity_join() -> None:
    snapshot = {"records": [{"market_id": "m", "condition_id": "c", "yes_token_id": "y", "no_token_id": "n"}]}
    record, reason = _exact_record(snapshot, {"market_id": "m", "condition_id": "c", "token_id": "n"})
    assert record is not None and reason is None


def test_13_fuzzy_city_date_bracket_join_fails() -> None:
    snapshot = {"records": [{"city": "Amsterdam", "target_date": "2026-08-20", "bracket": "25", "market_id": "m", "condition_id": "c", "yes_token_id": "y", "no_token_id": "n"}]}
    record, reason = _exact_record(snapshot, {"city": "Amsterdam", "target_date": "2026-08-20", "bracket": "25", "market_id": "", "condition_id": "", "token_id": ""})
    assert record is None and reason in {"market_id_not_matched", "condition_id_not_matched", "token_id_not_matched"}


def test_14_tier_a_b_c_cannot_silently_promote() -> None:
    identities = pd.read_parquet(DEFAULT_OUTPUT / "AMSTERDAM_EVENT_MARKET_IDENTITY_RECONCILIATION.parquet")
    assert not identities.loc[identities.evidence_tier.str.contains("TIER_B|TIER_C", na=False), "tier_a_entry_1share"].any()
    inventory = _json("AMSTERDAM_MARKET_ARCHIVE_INVENTORY.json")["archives"]
    assert any(row["usable_evidence_tier"] == "TIER_C_LEGACY_EXACT_QUOTE" for row in inventory)
    coverage = _json("AMSTERDAM_EXECUTABLE_COVERAGE_REASON_HISTOGRAM.json")
    assert coverage["reason_histogram_total"] == 87
    assert sum(coverage["reason_histogram"].values()) == 87


def test_15_missing_book_is_operational_zero_and_research_null() -> None:
    result = _json("MARKET_REACTION_MODEL_RESULTS.json")
    assert result["policy_arms"]["P0_always_abstain"]["operational_result"] == 0
    assert result["policy_arms"]["P1_MR0"] is None
    assert result["data_failure_is_null_not_policy_abstain"] is True


def test_16_midpoint_and_synthetic_opposite_token_are_prohibited() -> None:
    schema = _json("AMSTERDAM_ACTION_MAPPING_SCHEMA_V1.json")
    assert "midpoint" in schema["forbidden_inputs"]
    assert "opposite_token_synthetic_parity" in schema["forbidden_inputs"]


def test_17_new_running_max_probability_is_not_settlement_fair() -> None:
    schema = _json("AMSTERDAM_ACTION_MAPPING_SCHEMA_V1.json")
    assert "p_new_running_max_as_settlement_probability" in schema["forbidden_inputs"]


def test_18_layer_b_inputs_are_oof_outer_or_frozen_only() -> None:
    schema = _json("AMSTERDAM_ACTION_MAPPING_SCHEMA_V1.json")
    assert "in_sample_layer_a" in schema["forbidden_inputs"]
    for model in ("B2", "M1", "M2"):
        manifest = _json(f"{model}_MODEL_ARTIFACT_MANIFEST.json")
        assert manifest["captured_results_used_for_tuning"] is False
        assert manifest["frozen_scoring_parameters"]


def test_19_runner_has_no_order_client_or_private_credential_import() -> None:
    path = Path("scripts/analysis/forecast_quality/wcir_amsterdam_pilot_v1_1.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    assert not any(any(token in name.lower() for token in ("order_client", "private_key", "credential", "trade_intent")) for name in imports)


def test_20_orders_fills_notional_remain_zero() -> None:
    epoch = _json("FORWARD_EPOCH_MANIFEST_V2.json")
    audit = _json("SHADOW_RUNTIME_AND_ISOLATION_AUDIT.json")
    assert epoch["orders_fills_notional"] == [0, 0, 0]
    assert [audit["orders"], audit["fills"], audit["notional"]] == [0, 0, 0]
    counts = audit["runtime_preflight"]["wcir_v1_1_canonical_counts"]
    assert [counts["orders"], counts["fills"], counts["order_notional"], counts["fill_notional"]] == [0, 0, 0.0, 0.0]


def test_21_append_only_shadow_prediction_is_idempotent(tmp_path: Path) -> None:
    journal = tmp_path / "predictions.jsonl"
    row = {
        "forward_epoch_id": "e", "event_id": "event", "decision_vintage_id": "d",
        "raw_source_lineage": {}, "full_path_lineage": [], "feature_vector": {},
        "feature_vector_hash": "hash", "B2_PMF": [0.2, 0.8], "M1_PMF": [0.3, 0.7],
        "M2_PMF": [0.4, 0.6], "market_identity": None, "decision_book": None,
        "submit_proxy_book": None, "layer_b_prediction": None, "abstain_reasons": [],
        "data_failure_reasons": [], "future_next_print_label": None, "future_markouts": None,
        "orders": 0, "fills": 0, "notional": 0,
    }
    assert append_shadow_prediction(journal, row) == "APPENDED"
    assert append_shadow_prediction(journal, row) == "ALREADY_PRESENT_IDENTICAL"
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 1
    with pytest.raises(ValueError, match="does not match"):
        append_shadow_prediction(journal, {**row, "prediction_row_id": "wrong"})
    with pytest.raises(RuntimeError, match="epoch/event"):
        append_shadow_prediction(journal, {**row, "feature_vector_hash": "changed"})


def test_21b_concurrent_shadow_prediction_append_has_one_physical_row(tmp_path: Path) -> None:
    journal = tmp_path / "predictions.jsonl"
    row = {
        "forward_epoch_id": "e", "event_id": "event", "decision_vintage_id": "d",
        "raw_source_lineage": {}, "full_path_lineage": [], "feature_vector": {},
        "feature_vector_hash": "hash", "B2_PMF": [0.2, 0.8], "M1_PMF": [0.3, 0.7],
        "M2_PMF": [0.4, 0.6], "market_identity": None, "decision_book": None,
        "submit_proxy_book": None, "layer_b_prediction": None, "abstain_reasons": [],
        "data_failure_reasons": [], "future_next_print_label": None, "future_markouts": None,
        "orders": 0, "fills": 0, "notional": 0,
    }
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: append_shadow_prediction(journal, row), range(32)))

    assert results.count("APPENDED") == 1
    assert results.count("ALREADY_PRESENT_IDENTICAL") == 31
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 1


def test_22_package_missing_extra_hash_and_duplicate_fail_closed(tmp_path: Path) -> None:
    destination = tmp_path / "package"
    destination.mkdir()
    for name in ARTIFACTS:
        shutil.copy2(DEFAULT_OUTPUT / name, destination / name)
    verify(destination)
    (destination / "extra").write_text("extra")
    with pytest.raises(RuntimeError, match="non-whitelisted"):
        verify(destination)
    (destination / "extra").unlink()
    (destination / "wcir-amsterdam-pilot-v1-1-evil.txt").write_text("prefix bypass")
    with pytest.raises(RuntimeError, match="non-whitelisted"):
        verify(destination)
    (destination / "wcir-amsterdam-pilot-v1-1-evil.txt").unlink()
    nested = destination / "subdir"
    nested.mkdir()
    (nested / "evil").write_text("nested bypass")
    with pytest.raises(RuntimeError, match="nested package entries"):
        verify(destination)
    (nested / "evil").unlink()
    nested.rmdir()
    (destination / "AMSTERDAM_ACTION_MAPPING_SCHEMA_V1.json").write_text("drift")
    with pytest.raises(RuntimeError, match="size/hash"):
        verify(destination)
    duplicate = tmp_path / "duplicate.zip"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(duplicate, "w") as archive:
            for name in ARTIFACTS:
                archive.write(DEFAULT_OUTPUT / name, arcname=name)
            archive.writestr(ARTIFACTS[0], b"duplicate")
    with pytest.raises(RuntimeError, match="duplicate"):
        verify_zip(duplicate)


def test_23_offline_reproduction_from_extracted_root(tmp_path: Path) -> None:
    extracted_root = tmp_path / "extracted-package-root"
    extracted_root.mkdir()
    for name in ARTIFACTS:
        shutil.copy2(DEFAULT_OUTPUT / name, extracted_root / name)
    completed = subprocess.run(
        ["sh", str(extracted_root / "REPRODUCE_AMSTERDAM_PILOT_V1_1.sh")],
        text=True, capture_output=True, check=False,
        env={**os.environ, "PYTHON_BIN": sys.executable},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["status"] == "PASS"


def test_24_model_verdict_matches_frozen_primary_and_cluster_sensitivity() -> None:
    comparison = _json("AMSTERDAM_MODEL_COMPARISON_V2.json")
    p2 = comparison["cohorts"]["P2_CAPTURED_PIT_OPPORTUNITY"]["comparisons"]
    date_worse = all(
        p2[f"{model}_minus_B2"]["target_date_block_bootstrap"]["ci95"][0] > 0
        for model in ("M1", "M2")
    )
    group_crosses = all(
        low <= 0 <= high
        for model in ("M1", "M2")
        for low, high in [p2[f"{model}_minus_B2"]["official_print_group_cluster_sensitivity"]["ci95"]]
    )
    assert comparison["M1_M2_significantly_worse_than_B2_on_corrected_P2"] is date_worse
    assert comparison["P2_official_print_group_cluster_CIs_cross_zero"] is group_crosses
    assert comparison["evidence_ranking"] == "B2_PRIMARY_REFERENCE_M1_M2_CHALLENGERS"


def test_25_frozen_portable_scorer_returns_deterministic_normalized_pmfs() -> None:
    manifests = {
        name: _json(f"{name}_MODEL_ARTIFACT_MANIFEST.json")
        for name in ("B2", "M1", "M2")
    }
    features = {name: 0.0 for name in manifests["M1"]["frozen_scoring_parameters"]["features"]}
    features.update({
        "latest_fast_native_value": 21.6,
        "last_official_native_value": 22.0,
        "official_running_max": 22.0,
    })
    first = score_frozen_models(manifests, features)
    second = score_frozen_models(manifests, features)
    assert first == second
    for pmf in first.values():
        assert len(pmf) == 21
        assert sum(pmf) == pytest.approx(1.0, abs=1e-12)
        assert all(value > 0 for value in pmf)


def test_26_historical_entrypoint_calls_shared_builder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    columns = [
        "target_date", "observed_at_utc", "ta_c", "tx_c", "solar_w_m2",
        "cloud_okta", "precip_mm_h", "humidity_pct", "wind_speed_mps",
        "official_report_time_utc", "latest_official_temp_c",
        "official_running_max_c", "collection_mode", "feature_collection_mode",
        "decision_ts_utc", "weather_checkpoint_key", "market_join_status",
        "d1_bracket_c", "decision_minute_local",
    ]
    rows = []
    for index, observed in enumerate(pd.date_range("2026-08-20T09:30Z", periods=4, freq="10min")):
        first_state = index < 2
        rows.append(dict(zip(columns, [
            "2026-08-20", observed.isoformat(), 20.0 + index / 10, 20.2 + index / 10,
            100.0, 4.0, 0.0, 60.0, 5.0,
            "2026-08-20T09:00:00Z" if first_state else "2026-08-20T09:30:00Z",
            20.0 if first_state else 21.0, 20.0 if first_state else 21.0,
            "historical", "historical", None, f"checkpoint-{index}", "missing",
            "20", 690 + index * 10,
        ])))
    fixture = tmp_path / "historical.csv"
    pd.DataFrame(rows, columns=columns).to_csv(fixture, index=False)
    original = AmsterdamFeatureBuilderV2.add_path_features.__func__
    calls: list[tuple[str, bool]] = []

    def spy(cls, frame: pd.DataFrame, *, group_column: str, require_available_at: bool):
        calls.append((group_column, require_available_at))
        return original(cls, frame, group_column=group_column, require_available_at=require_available_at)

    monkeypatch.setattr(AmsterdamFeatureBuilderV2, "add_path_features", classmethod(spy))
    panel, _ = make_historical_panel(fixture)
    assert not panel.empty
    assert calls == [("target_date", False)]


def test_27_knmi_producer_note_uses_frozen_ta_tx_interval_semantics() -> None:
    source = Path("weather_data_feed/knmi_open_data.py").read_text(encoding="utf-8")
    assert "ta is the final 1-minute mean" not in source
    assert "ta is the preceding 10-minute average ambient temperature" in source
