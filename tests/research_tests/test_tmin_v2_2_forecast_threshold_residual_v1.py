from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT / "scripts/analysis/tmin/tmin_v2_2_forecast_threshold_residual_v1.py"
)
SPEC = importlib.util.spec_from_file_location("tmin_v22", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _observation_frame() -> pd.DataFrame:
    rows = [
        {
            "available_at": "2026-08-20T00:01:00Z",
            "event_time": "2026-08-20T00:00:00Z",
            "value": 20,
            "revision": "base",
        },
        {
            "available_at": "2026-08-20T00:02:00Z",
            "event_time": "2026-08-20T00:00:00Z",
            "value": 19,
            "revision": "correction",
        },
    ]
    frame = pd.DataFrame(rows)
    frame["_available_at"] = pd.to_datetime(frame.pop("available_at"), utc=True)
    frame["_event_time"] = pd.to_datetime(frame.pop("event_time"), utc=True)
    frame["_canonical_json"] = frame.apply(
        lambda row: json.dumps(row.to_dict(), default=str, sort_keys=True), axis=1
    )
    return frame


def test_correction_duplicate_and_future_append_resolution_is_deterministic() -> None:
    checkpoint = pd.Timestamp("2026-08-20T00:03:00Z")
    base = _observation_frame()
    expected = MODULE.deterministic_observation_choice(base, checkpoint)
    assert expected["revision"] == "correction"

    duplicate = base.iloc[[1]].copy()
    shuffled = pd.concat([base, duplicate], ignore_index=True).sample(
        frac=1, random_state=7
    )
    chosen = MODULE.deterministic_observation_choice(shuffled, checkpoint)
    assert chosen["_canonical_json"] == expected["_canonical_json"]

    future = base.iloc[[0]].copy()
    future["_available_at"] = pd.Timestamp("2026-08-20T00:04:00Z")
    future["value"] = 1
    future["_canonical_json"] = "future"
    appended = MODULE.deterministic_observation_choice(
        pd.concat([base, future], ignore_index=True), checkpoint
    )
    assert appended["_canonical_json"] == expected["_canonical_json"]
    assert appended["_available_at"] <= checkpoint

    future_event = base.iloc[[0]].copy()
    future_event["_available_at"] = pd.Timestamp("2026-08-20T00:02:30Z")
    future_event["_event_time"] = pd.Timestamp("2026-08-20T00:04:00Z")
    future_event["value"] = 0
    future_event["_canonical_json"] = "future-event"
    event_safe = MODULE.deterministic_observation_choice(
        pd.concat([base, future_event], ignore_index=True), checkpoint
    )
    assert event_safe["_canonical_json"] == expected["_canonical_json"]
    assert event_safe["_event_time"] <= checkpoint


def test_latest_native_forecast_vintage_requires_complete_pit_lineage() -> None:
    checkpoint = pd.Timestamp("2026-08-20T00:03:00Z")
    frame = pd.DataFrame(
        [
            {
                "available_at": "2026-08-20T00:01:00Z",
                "issue_time": "2026-08-19T18:00:00Z",
                "model_run": "18Z",
                "full_remaining_path": "[20,19]",
                "_canonical_json": "older",
            },
            {
                "available_at": "2026-08-20T00:02:00Z",
                "issue_time": "2026-08-20T00:00:00Z",
                "model_run": "00Z",
                "full_remaining_path": "[19,18]",
                "_canonical_json": "latest",
            },
            {
                "available_at": "2026-08-20T00:04:00Z",
                "issue_time": "2026-08-20T00:00:00Z",
                "model_run": "00Z-late",
                "full_remaining_path": "[1]",
                "_canonical_json": "future",
            },
        ]
    )
    chosen = MODULE.latest_native_forecast_vintage(frame, checkpoint)
    assert chosen["_canonical_json"] == "latest"
    assert chosen["_available_at"] <= checkpoint
    incomplete = frame.copy()
    incomplete["model_run"] = None
    with pytest.raises(LookupError):
        MODULE.latest_native_forecast_vintage(incomplete, checkpoint)
    empty_path = frame.copy()
    empty_path["full_remaining_path"] = ""
    with pytest.raises(LookupError):
        MODULE.latest_native_forecast_vintage(empty_path, checkpoint)


def test_positive_native_forecast_coverage_reads_real_lineage_fields() -> None:
    sample = pd.DataFrame(
        [
            {
                "curve_id": "curve-1",
                "city": "Seoul",
                "target_date": "2026-08-20",
                "forecast_source": "native-source",
                "forecast_model": "model-a",
                "available_at_utc": "2026-08-19T20:00:00Z",
                "issue_time": "2026-08-19T18:00:00Z",
                "model_run": "18Z",
                "full_remaining_path": [20.0, 19.0],
                "target_valid_time": "2026-08-20T00:00:00+09:00",
                "ingested_at": "2026-08-19T20:01:00Z",
                "forecast_remaining_min": 19.0,
                "forecast_at_checkpoint": 20.0,
                "realized_official_remaining_min": 18.0,
                "next_colder_boundary_native": 19.5,
                "member_quantile_identity": "deterministic",
                "source_unit": "C",
                "normalization_version": "native_c_v1",
                "source_file": "native.jsonl",
                "snapshot_ts_utc": "2026-08-19T19:59:00Z",
            }
        ]
    )
    matrix, archive = MODULE.build_forecast_coverage(sample, [6])
    assert matrix.loc[0, "native_vintage_eligible"]
    assert archive.loc[0, "status"] == "PIT_NATIVE_VINTAGE_ELIGIBLE"
    assert archive.loc[0, "forecast_error"] == pytest.approx(-1.0)
    assert bool(archive.loc[0, "next_colder_event"])


@pytest.mark.parametrize(
    ("raw_min", "rung", "boundary"),
    [(20.49, 20, 19.5), (20.50, 21, 20.5), (-1.49, -1, -1.5), (-1.50, -2, -2.5)],
)
def test_raw_minimum_and_next_colder_lattice_contract(
    raw_min: float, rung: int, boundary: float
) -> None:
    assert MODULE.native_rung(raw_min) == rung
    assert MODULE.next_colder_boundary(rung) == boundary
    assert MODULE.native_rung(np.nextafter(boundary, -np.inf)) == rung - 1


def test_empirical_cdf_clock_and_alpha_posterior_contract() -> None:
    q_weather = MODULE.shrunk_weather_probability(
        np.array([-1.0, 0.0, 1.0]),
        np.array([-2.0, -1.0, 0.0, 1.0, 2.0]),
        0.0,
    )
    assert 0.01 <= q_weather <= 0.99
    assert MODULE.clock_probability(3, 5, 0.8) == pytest.approx(19 / 25)
    frame = pd.DataFrame(
        {
            "target_date": ["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"],
            "label": [1, 0, 1, 0],
            "p_market": [0.7, 0.3, 0.7, 0.3],
            "routing_indicator": [1, 1, 1, 1],
            "z_weather": [0.5, -0.5, 0.5, -0.5],
        }
    )
    posterior = MODULE.alpha_posterior_grid(frame, grid_size=301, maximum=1.0)
    assert posterior["posterior_mean"] >= 0
    assert posterior["interval_90"][0] >= 0
    gradient = MODULE.routed_score_gradient(frame, draws=200, seed=1)
    assert gradient["bootstrap_draws"] == 200
    with pytest.raises(ValueError):
        MODULE.alpha_posterior_grid(frame.iloc[0:0])
    with pytest.raises(ValueError):
        MODULE.routed_score_gradient(frame.iloc[0:0], draws=20, seed=1)


def test_positive_fold_keeps_supported_outside_route_byte_equal_market() -> None:
    forecast_rows = []
    for day in range(1, 15):
        target_date = f"2026-01-{day:02d}"
        for city in ("Seoul", "Tokyo"):
            forecast_rows.append(
                {
                    "city": city,
                    "target_date": target_date,
                    "checkpoint_hour": 6,
                    "status": "PIT_NATIVE_VINTAGE_ELIGIBLE",
                    "forecast_error": -1.0 if day % 2 else 1.0,
                    "label_no_further_cooling": int(day % 2 == 0),
                    "next_colder_boundary_native": 10.5,
                    "forecast_remaining_min": 10.0,
                }
            )
    prediction_rows = []
    for day in range(10, 15):
        target_date = f"2026-01-{day:02d}"
        for city in ("Seoul", "Tokyo"):
            prediction_rows.extend(
                [
                    {
                        "checkpoint_id": f"{city}-{target_date}-route",
                        "city": city,
                        "target_date": target_date,
                        "checkpoint_hour": 6,
                        "supported_city": True,
                        "routing_indicator": 1,
                        "label": int(day % 2 == 0),
                        "p_market": 0.6,
                    },
                    {
                        "checkpoint_id": f"{city}-{target_date}-outside",
                        "city": city,
                        "target_date": target_date,
                        "checkpoint_hour": 12,
                        "supported_city": True,
                        "routing_indicator": 0,
                        "label": 1,
                        "p_market": 0.8,
                    },
                ]
            )
    config = {
        "supported_cities": ["Seoul", "Tokyo"],
        "city_cdf_shrinkage_strength": 30.0,
        "clock_shrinkage_strength": 20.0,
        "alpha_prior": {"scale": 0.25},
        "fit_gate": {
            "prior_target_dates_min": 2,
            "prior_independent_negative_city_date_episodes_min": 1,
            "both_supported_cities_required": True,
        },
    }
    output, folds, _ = MODULE.fit_v22_predictions(
        pd.DataFrame(prediction_rows), pd.DataFrame(forecast_rows), config
    )
    outside = output["routing_indicator"].eq(0)
    assert output.loc[outside, "z_weather"].eq(0.0).all()
    assert output.loc[outside, "p_v2_2"].to_numpy().tobytes() == output.loc[
        outside, "p_market"
    ].to_numpy().tobytes()
    assert any(item["status"] == "FIT" for item in folds)


def test_evaluation_reports_real_single_date_contributions() -> None:
    frame = pd.DataFrame(
        {
            "target_date": ["2026-01-01", "2026-01-02"],
            "city": ["Seoul", "Tokyo"],
            "checkpoint_hour": [6, 6],
            "routing_indicator": [1, 1],
            "label": [1, 0],
            "p_market": [0.95, 0.5],
            "p_v2_2": [0.9, 0.4],
        }
    )
    result = MODULE.build_evaluation(frame)
    contribution = result["single_date_contribution"]
    assert any(abs(item["delta_logloss"]) > 0 for item in contribution)
    assert sum(item["share_of_absolute_delta_logloss"] for item in contribution) == pytest.approx(1.0)
    assert result["high_probability_calibration"]["status"] != "MARKET_EQUAL_FALLBACK"


def test_clean_room_build_and_all_pit_fallback_invariants(tmp_path: Path) -> None:
    output = tmp_path / "empty-output"
    args = MODULE.parse_args([])
    args.output_dir = output
    MODULE.build(args)

    p0 = pd.read_parquet(output / "P0_RAW_FEATURE_AUDIT.parquet")
    assert len(p0) == 168
    assert (p0["observation_available_at"] <= p0["checkpoint_time"]).all()
    assert p0["raw_min_reconstructs_current_rung"].all()
    assert "native_rung_safety_margin" not in " ".join(p0.columns)

    forecasts = pd.read_csv(output / "FORECAST_VINTAGE_COVERAGE_MATRIX.csv")
    eligible = forecasts["native_vintage_eligible"].astype(bool)
    assert not eligible.any()
    assert not forecasts["snapshot_used_as_native_available_at"].astype(bool).any()

    gate = json.loads((output / "DATA_AND_FIT_GATE.json").read_text())
    expected_failures = {
        "FORECAST_COVERED_CITY_DAYS_BELOW_120",
        "NEXT_COLDER_EVENT_CITY_DAYS_BELOW_40",
        "SEOUL_EVENT_CITY_DAYS_BELOW_15",
        "TOKYO_EVENT_CITY_DAYS_BELOW_15",
        "SETTLEMENT_TRUTH_EXACT_MATCH_BELOW_99_PERCENT",
    }
    assert expected_failures.issubset(set(gate["failures"]))
    assert gate["maximum_prior_target_dates"] >= 10
    assert gate["maximum_prior_independent_negative_city_date_episodes"] >= 5
    assert gate["both_supported_cities_present"]

    folds = pd.read_csv(output / "FOUNDATION_FOLD_COEFFICIENTS.csv")
    comparable = folds.dropna(subset=["training_max_target_date"])
    assert (
        comparable["training_max_target_date"].astype(str)
        < comparable["test_date"].astype(str)
    ).all()

    predictions = pd.read_parquet(output / "MODEL_PREDICTIONS.parquet")
    assert predictions["p_v2_2"].to_numpy().tobytes() == predictions[
        "p_market"
    ].to_numpy().tobytes()
    unsupported = ~predictions["supported_city"]
    assert unsupported.any()
    assert predictions.loc[unsupported, "blocker"].eq(
        "UNSUPPORTED_CITY_FOUNDATION"
    ).all()
    outside = predictions["routing_indicator"].eq(0)
    assert predictions.loc[outside, "p_v2_2"].to_numpy().tobytes() == predictions.loc[
        outside, "p_market"
    ].to_numpy().tobytes()

    with pytest.raises(FileExistsError):
        MODULE.build(args)


def test_sealed_source_rebuild_is_invariant_to_current_head(tmp_path: Path) -> None:
    original = tmp_path / "original"
    original_args = MODULE.parse_args([])
    original_args.output_dir = original
    MODULE.build(original_args)

    rebuilt = tmp_path / "rebuilt"
    rebuilt_args = MODULE.parse_args([])
    rebuilt_args.output_dir = rebuilt
    rebuilt_args.config = (
        original
        / "source_snapshot/config/research/tmin_v2_2_forecast_threshold_residual_v1.json"
    )
    rebuilt_args.p0 = original / "frozen_inputs/P0_ROW_LEVEL_PROBABILITY_AUDIT.parquet"
    rebuilt_args.observation_root = original / "frozen_inputs/pit_observations"
    rebuilt_args.forecast_sample = original / "frozen_inputs/forecast_archive_sample.parquet"
    rebuilt_args.forecast_inventory = original / "frozen_inputs/forecast_archive_inventory.json"
    rebuilt_args.reconciliation = original / "frozen_inputs/settlement_source_reconciliation.parquet"
    rebuilt_args.wu_disputes_root = original / "frozen_inputs/wu_disputes"
    rebuilt_args.prior_forward_manifest = original / "frozen_inputs/prior_forward_arm_manifest.json"
    rebuilt_args.knowledge_root = original / "source_snapshot/docs/knowledge/tmin"
    rebuilt_args.source_snapshot_root = original / "source_snapshot"
    rebuilt_args.source_patch_input = original / "SOURCE_PATCH.binary.diff"
    rebuilt_args.base_sha = (original / "BASE_SHA.txt").read_text().strip()
    MODULE.build(rebuilt_args)

    expected = json.loads((original / "EXPECTED_OUTPUT_HASHES.json").read_text())
    for item in expected["files"]:
        rebuilt_path = rebuilt / item["path"]
        assert hashlib.sha256(rebuilt_path.read_bytes()).hexdigest() == item["sha256"]
