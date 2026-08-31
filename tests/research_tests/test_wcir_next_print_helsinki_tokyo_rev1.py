from __future__ import annotations

import json
import numpy as np
import pandas as pd

from scripts.analysis.forecast_quality.wcir_next_print_helsinki_tokyo_rev1 import (
    CONTRACTS,
    build_captured_panel,
    build_historical_panel,
    compact_member_paths,
    _b2_pmf,
    fit_dependency_weights,
    fit_temperature_dependency_weighted,
    load_captured_official,
    reconcile_market,
    seal_compact_manifest,
)
from weather_modeling.next_print_feature_builder_v1 import NextPrintFeatureBuilderV1, stable_hash


FEATURES = [
    "recent_slope", "recent_acceleration", "path_volatility", "running_fast_max",
    "time_since_high_minutes", "pullback_depth", "reheat_strength",
]


def path() -> pd.DataFrame:
    return pd.DataFrame({
        "target_date": ["2026-08-29"] * 4,
        "observed_at": pd.to_datetime([
            "2026-08-29T08:00:00Z", "2026-08-29T08:10:00Z",
            "2026-08-29T08:20:00Z", "2026-08-29T08:30:00Z",
        ]),
        "available_at": pd.to_datetime([
            "2026-08-29T08:01:00Z", "2026-08-29T08:11:00Z",
            "2026-08-29T08:21:00Z", "2026-08-29T08:31:00Z",
        ]),
        "latest_fast_native_value": [18.0, 18.4, 18.1, 18.7],
        "source_observation_id": ["a", "b", "c", "d"],
    })


def test_city_contracts_do_not_reuse_amsterdam_station_semantics() -> None:
    assert CONTRACTS["Helsinki"].fast_station == "100968"
    assert CONTRACTS["Helsinki"].official_station == "EFHK"
    assert CONTRACTS["Helsinki"].routine_minutes == (20, 50)
    assert CONTRACTS["Tokyo"].fast_station == "44166"
    assert CONTRACTS["Tokyo"].official_station == "RJTT"
    assert CONTRACTS["Tokyo"].routine_minutes == (0, 30)


def test_historical_and_captured_share_identical_feature_builder() -> None:
    source = path()
    historical = NextPrintFeatureBuilderV1.add_path_features(
        source, group_column="target_date", require_available_at=False
    ).iloc[-1]
    captured = NextPrintFeatureBuilderV1.build_vintage(
        source,
        decision_vintage_id="v1",
        feature_cutoff_at="2026-08-29T08:31:00Z",
        decision_ready_at="2026-08-29T08:31:00Z",
        observation_cutoff_at="2026-08-29T08:30:00Z",
        availability_class="CAPTURED_PIT_ARCHIVE",
        feature_names=FEATURES,
        source_path_complete=True,
    )
    assert captured.status == "OK"
    for name in FEATURES:
        expected = historical[name]
        actual = captured.feature_vector[name]
        if pd.isna(expected):
            assert actual is None
        else:
            assert actual == expected


def test_captured_vintage_fails_closed_on_path_gap() -> None:
    source = path().drop(index=2)
    result = NextPrintFeatureBuilderV1.build_vintage(
        source,
        decision_vintage_id="gap",
        feature_cutoff_at="2026-08-29T08:31:00Z",
        decision_ready_at="2026-08-29T08:31:00Z",
        observation_cutoff_at="2026-08-29T08:30:00Z",
        availability_class="CAPTURED_PIT_ARCHIVE",
        feature_names=FEATURES,
        source_path_complete=True,
    )
    assert result.status == "INCOMPLETE_CAPTURED_SOURCE_PATH"
    assert result.observed_gap_count == 1


def test_future_available_revision_is_not_used() -> None:
    source = path()
    source.loc[len(source)] = {
        "target_date": "2026-08-29",
        "observed_at": pd.Timestamp("2026-08-29T08:30:00Z"),
        "available_at": pd.Timestamp("2026-08-29T08:40:00Z"),
        "latest_fast_native_value": 99.0,
        "source_observation_id": "future-revision",
    }
    result = NextPrintFeatureBuilderV1.build_vintage(
        source,
        decision_vintage_id="pit",
        feature_cutoff_at="2026-08-29T08:31:00Z",
        decision_ready_at="2026-08-29T08:31:00Z",
        observation_cutoff_at="2026-08-29T08:30:00Z",
        availability_class="CAPTURED_PIT_ARCHIVE",
        feature_names=["running_fast_max"],
        source_path_complete=True,
    )
    assert result.status == "OK"
    assert result.feature_vector["running_fast_max"] == 18.7
    assert result.max_feature_available_at == "2026-08-29T08:31:00+00:00"


def test_historical_path_features_are_built_before_label_window_filter() -> None:
    fast = pd.DataFrame({
        "target_date": ["2026-08-29"] * 5,
        "observed_at": pd.to_datetime([
            "2026-08-29T00:10:00Z", "2026-08-29T00:20:00Z", "2026-08-29T00:30:00Z",
            "2026-08-29T00:40:00Z", "2026-08-29T00:50:00Z",
        ]),
        "latest_fast_native_value": [10.0, 11.0, 12.0, 13.0, 14.0],
        "source_observation_id": ["a", "b", "c", "d", "e"],
    })
    official = pd.DataFrame({
        "target_date": ["2026-08-29", "2026-08-29"],
        "official_report_at": pd.to_datetime(["2026-08-29T00:00:00Z", "2026-08-29T01:00:00Z"]),
        "official_native_value": [10.0, 11.0],
        "official_print_id": ["p0", "p1"],
    })
    panel, _ = build_historical_panel(fast, official, CONTRACTS["Tokyo"])
    assert str(panel["next_official_observed_at"].dtype) == "datetime64[ns, UTC]"
    row = panel.loc[panel["observed_at"].eq(pd.Timestamp("2026-08-29T00:40:00Z"))].iloc[0]
    assert row["recent_slope"] == 1.0


def test_feature_context_is_in_vector_hash_and_lineage() -> None:
    result = NextPrintFeatureBuilderV1.build_vintage(
        path(),
        decision_vintage_id="context",
        feature_cutoff_at="2026-08-29T08:31:00Z",
        decision_ready_at="2026-08-29T08:31:00Z",
        observation_cutoff_at="2026-08-29T08:30:00Z",
        availability_class="CAPTURED_PIT_ARCHIVE",
        feature_names=["recent_slope", "last_official_native_value"],
        source_path_complete=True,
        feature_context={
            "last_official_native_value": {
                "value": 18.0,
                "available_at": "2026-08-29T08:25:00Z",
                "source_observation_ids": ["official:p0"],
            }
        },
    )
    assert result.feature_vector["last_official_native_value"] == 18.0
    assert result.feature_vector_hash == stable_hash(result.feature_vector)
    lineage = result.lineage.set_index("feature_name")
    assert lineage.loc["last_official_native_value", "missing_reason"] is None
    assert lineage.loc["last_official_native_value", "source_observation_ids"] == '["official:p0"]'


def test_compact_manifest_exactly_describes_compact_member_set(tmp_path) -> None:
    (tmp_path / "inputs").mkdir()
    (tmp_path / "evidence").mkdir()
    (tmp_path / "inputs/raw.csv").write_text("raw", encoding="utf-8")
    (tmp_path / "evidence/ROW_LEVEL_PANEL.csv").write_text("rows", encoding="utf-8")
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    seal_compact_manifest(tmp_path)
    manifest = json.loads((tmp_path / "COMPACT_EVIDENCE_MANIFEST.json").read_text(encoding="utf-8"))
    assert [str(path.relative_to(tmp_path)) for path in compact_member_paths(tmp_path)] == ["summary.json"]
    assert manifest["entries"][0]["path"] == "summary.json"


def test_captured_target_already_available_at_decision_fails_closed() -> None:
    opportunities = pd.DataFrame([{
        "event_id": "e1", "event_key": "k1", "target_date": "2026-08-29",
        "source_obs_ts_utc": pd.Timestamp("2026-08-29T08:00:00Z"),
        "source_detect_ts_utc": pd.Timestamp("2026-08-29T08:05:00Z"),
        "ts_utc": "2026-08-29T08:20:00Z", "latest_metar_report_ts_utc": "2026-08-29T07:50:00Z",
        "source_temp_c": 18.0, "latest_metar_round_c": 17.0, "metar_running_max_round_c": 17.0,
    }])
    raw = pd.DataFrame({
        "target_date": ["2026-08-29"], "observed_at": pd.to_datetime(["2026-08-29T08:00:00Z"]),
        "available_at": pd.to_datetime(["2026-08-29T08:05:00Z"]),
        "latest_fast_native_value": [18.0], "source_observation_id": ["s1"],
    })
    official = pd.DataFrame([{
        "target_date": "2026-08-29", "official_report_at": pd.Timestamp("2026-08-29T08:10:00Z"),
        "official_available_at": pd.Timestamp("2026-08-29T08:10:30Z"),
        "official_native_value": 18.0, "official_print_id": "p1",
    }])
    captured, audit, _, _ = build_captured_panel(opportunities, raw, official, CONTRACTS["Tokyo"])
    assert captured.empty
    assert audit.iloc[0]["eligibility_status"] == "DATA_FAIL_CLOSED_TARGET_ALREADY_AVAILABLE_AT_DECISION"


def test_ws_missing_native_market_identity_is_not_full_exact(monkeypatch, tmp_path) -> None:
    import scripts.analysis.forecast_quality.wcir_next_print_helsinki_tokyo_rev1 as runner

    aligned = {"e1": [{
        "event_id": "e1", "token_id": "t1", "book_valid": True, "book_truth_id": "b1",
        "checkpoint_at_utc": "2099-01-01T00:00:00Z", "market_id": "WRONG_M", "condition_id": "WRONG_C",
    }]}
    truths = {"b1": {"token_id": "t1", "market_id": "WRONG_M", "condition_id": "WRONG_C"}}
    monkeypatch.setattr(runner, "load_stage2_ws", lambda: (aligned, truths))
    monkeypatch.setattr(runner, "scan_rest_books", lambda *_: {})
    monkeypatch.setattr(runner, "scan_fixed_full_ladder", lambda *_: {})
    monkeypatch.setattr(runner, "scan_legacy_quotes", lambda *_: {})
    events = pd.DataFrame([{
        "event_id": "e1", "city": "Helsinki", "target_date": "2026-08-29",
        "source_obs_ts_utc": pd.Timestamp("2026-08-29T08:00:00Z"),
        "source_detect_ts_utc": pd.Timestamp("2026-08-29T08:05:00Z"),
        "official_first_seen_at_utc": pd.Timestamp("2026-08-29T08:20:00Z"),
        "market_id": "m1", "condition_id": "c1", "token_id": "t1",
    }])
    evidence, _, _, _ = reconcile_market(tmp_path, events, "Helsinki")
    row = evidence.iloc[0]
    assert row["evidence_tier"] == "TIER_A_STRICT_RECONSTRUCTED_WS"
    assert not row["market_id_archive_exact"]
    assert not row["condition_id_archive_exact"]
    assert not row["full_archive_native_identity_reconciled"]
    assert row["checkpoint_temporal_relation"] == "POST_OFFICIAL_NAMED_CHECKPOINT"


def test_captured_path_missing_preregistered_local_start_fails_closed() -> None:
    opportunities = pd.DataFrame([{
        "event_id": "e2", "event_key": "k2", "target_date": "2026-08-29",
        "source_obs_ts_utc": pd.Timestamp("2026-08-29T08:00:00Z"),
        "source_detect_ts_utc": pd.Timestamp("2026-08-29T08:05:00Z"),
        "ts_utc": "2026-08-29T08:06:00Z", "latest_metar_report_ts_utc": "2026-08-29T07:50:00Z",
        "source_temp_c": 18.0, "latest_metar_round_c": 17.0, "metar_running_max_round_c": 17.0,
    }])
    raw = pd.DataFrame({
        "target_date": ["2026-08-29"], "observed_at": pd.to_datetime(["2026-08-29T08:00:00Z"]),
        "available_at": pd.to_datetime(["2026-08-29T08:05:00Z"]),
        "latest_fast_native_value": [18.0], "source_observation_id": ["s2"],
    })
    official = pd.DataFrame([{
        "target_date": "2026-08-29", "official_report_at": pd.Timestamp("2026-08-29T08:20:00Z"),
        "official_available_at": pd.Timestamp("2026-08-29T08:21:00Z"),
        "official_native_value": 18.0, "official_print_id": "p2", "official_station": "RJTT",
        "official_source": "aviationweather_metar", "official_raw_row_hash": "r", "official_raw_payload_hash": "p",
        "official_raw_source_path": "/raw",
    }])
    captured, audit, _, _ = build_captured_panel(opportunities, raw, official, CONTRACTS["Tokyo"])
    assert captured.empty
    assert audit.iloc[0]["eligibility_status"] == "DATA_FAIL_CLOSED_INCOMPLETE_SOURCE_PATH"
    assert not audit.iloc[0]["frozen_archive_prefix_complete"]


def test_captured_official_requires_and_preserves_exact_station(tmp_path) -> None:
    root = tmp_path / "output/source_events/2026-08-29"
    root.mkdir(parents=True)
    base = {
        "city": "Helsinki", "source": "aviationweather_metar", "target_date": "2026-08-29",
        "source_report_ts_utc": "2026-08-29T08:20:00Z", "first_seen_at_utc": "2026-08-29T08:21:00Z",
        "temp_c": 18.0, "raw_row_hash": "row", "raw_payload_hash": "payload", "raw_source_path": "/raw",
    }
    rows = [{**base, "station_id": "WRONG", "information_event_id": "bad"}, {**base, "station_id": "EFHK", "information_event_id": "good"}]
    (root / "sources.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    result = load_captured_official(tmp_path, CONTRACTS["Helsinki"])
    assert len(result) == 1
    assert result.iloc[0]["official_station"] == "EFHK"
    assert result.iloc[0]["official_raw_row_hash"] == "row"


def test_b2_fit_is_invariant_to_within_group_row_duplication() -> None:
    train = pd.DataFrame({
        "target_date": ["2026-01-01", "2026-01-02"],
        "official_print_group_id": ["g1", "g2"], "decision_vintage_id": ["a", "b"],
        "latest_fast_native_value": [10.0, 10.0], "last_official_native_value": [10.0, 10.0],
        "next_official_delta_native_tick": [0, 2],
    })
    duplicated = pd.concat([train, pd.concat([train.iloc[[0]].assign(decision_vintage_id=f"a{i}") for i in range(100)])], ignore_index=True)
    test = train.iloc[[0]].copy()
    assert pd.Series(fit_dependency_weights(train)).sum() == pd.Series(fit_dependency_weights(duplicated)).sum()
    assert np.allclose(_b2_pmf(train, test), _b2_pmf(duplicated, test), atol=1e-15, rtol=0)
    pmf = np.full((2, 21), 0.01)
    pmf[0, 10], pmf[1, 12] = 0.8, 0.8
    pmf /= pmf.sum(axis=1, keepdims=True)
    duplicated_pmf = np.vstack([pmf, np.repeat(pmf[[0]], 100, axis=0)])
    labels = np.array([0, 2])
    duplicated_labels = np.concatenate([labels, np.zeros(100, dtype=int)])
    first = fit_temperature_dependency_weighted(pmf, labels, fit_dependency_weights(train))
    second = fit_temperature_dependency_weighted(duplicated_pmf, duplicated_labels, fit_dependency_weights(duplicated))
    assert np.isclose(first, second, atol=1e-10, rtol=0)
