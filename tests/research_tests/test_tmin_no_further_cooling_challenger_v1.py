from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from scripts.analysis.tmin.evaluate_tmin_no_further_cooling_shadow_v1 import (
    CHALLENGER_POLICY_ID,
    _pit_probability_identity_legal,
    _window_routed_probability,
    evaluate,
)
from scripts.analysis.tmin.tmin_model_layer_v2_v3_v1 import (
    _calibration_oof,
    _gradient_diagnostics,
    _model_summary,
    _prior_date_oof,
)
from scripts.analysis.tmin.build_tmin_settlement_source_path_truth_v1 import (
    build as build_truth,
    load_iem_path,
    rung_contains,
)
from scripts.analysis.tmin.package_tmin_model_layer_v2_1_v3_research_v1 import (
    CITY_TIMEZONES,
    build_event_truth,
    weather_panel_manifest,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_window_routed_probability_is_market_only_outside_cooling_windows() -> None:
    market = pd.Series([0.5, 0.5, 0.5])
    innovation = pd.Series([2.0, 2.0, 2.0])
    windows = pd.Series(
        ["morning_cooling", "post_sunrise_provisional_low", "daytime_warming"]
    )

    result = _window_routed_probability(market, innovation, windows)

    assert np.isclose(result.iloc[0], 1 / (1 + np.exp(-0.2)))
    assert np.isclose(result.iloc[1], 1 / (1 + np.exp(-0.2)))
    assert result.iloc[2] == 0.5

    with pytest.raises(ValueError, match=r"within \[0, 1\]"):
        _window_routed_probability(
            pd.Series([1.2]),
            pd.Series([1.0]),
            pd.Series(["morning_cooling"]),
        )


def test_evaluator_qualifies_only_prospective_zero_notional_challenger(
    tmp_path: Path,
) -> None:
    artifact_id = "artifact-alpha-half"
    candidates_path = tmp_path / "candidates.jsonl"
    model_outputs_path = tmp_path / "model_outputs.jsonl"
    db_path = tmp_path / "weather.db"
    candidate_rows = []
    model_rows = []
    for index, target_date in enumerate(("2026-08-12", "2026-08-21")):
        checkpoint_id = f"checkpoint-{index}"
        condition_id = f"condition-{index}"
        decision_ts = f"{target_date}T00:00:00Z"
        candidate_rows.append(
            {
                "candidate_id": f"candidate-{index}",
                "checkpoint_id": checkpoint_id,
                "city": "Seoul",
                "target_date": target_date,
                "decision_ts_utc": decision_ts,
                "condition_id": condition_id,
                "market_id": f"market-{index}",
                "token_id": f"token-{index}",
                "bracket": "24",
                "feature_book_snapshot_id": f"feature-book-{index}",
                "candidate_status": "scored",
                "blocker_reason": None,
                "p_model": 0.70,
                "market_p": 0.60,
                "selected": False,
                "executable_cost": None,
                "execution_book_snapshot_id": None,
                "market_evidence_status": "available",
                "model_artifact_id": artifact_id,
                "model_id": "incumbent-model",
                "policy_id": "incumbent-policy",
                "input_refs": [
                    {"kind": "observation_archive", "path": "/tmp/observations", "available_at_utc": decision_ts},
                    {"kind": "forecast_curve", "path": "/tmp/forecast", "available_at_utc": decision_ts},
                ],
            }
        )
        model_rows.append(
            {
                "checkpoint_id": checkpoint_id,
                "model_artifact_id": artifact_id,
                "p_model": 0.70,
                "metadata": {
                    "physical_innovation_logit": 1.0,
                    "cooling_window_state": "morning_cooling",
                },
            }
        )
    candidate_rows.append(
        {
            **candidate_rows[-1],
            "candidate_id": "candidate-forward-missing-book",
            "checkpoint_id": "checkpoint-forward-missing-book",
            "target_date": "2026-08-28",
            "decision_ts_utc": "2026-08-28T00:00:00Z",
            "condition_id": "condition-forward-missing-book",
            "executable_cost": 0.50,
        }
    )
    model_rows.append(
        {
            "checkpoint_id": "checkpoint-forward-missing-book",
            "model_artifact_id": artifact_id,
            "p_model": 0.70,
            "metadata": {
                "physical_innovation_logit": 1.0,
                "cooling_window_state": "morning_cooling",
            },
        }
    )
    _write_jsonl(candidates_path, candidate_rows)
    _write_jsonl(model_outputs_path, model_rows)

    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE fact_trades (fact_built_at_utc TEXT)")
    connection.execute(
        """CREATE TABLE settlements (
               condition_id TEXT,
               target_date TEXT,
               bracket TEXT,
               final_price REAL,
               settlement_status TEXT,
               created_at_utc TEXT
           )"""
    )
    connection.executemany(
        "INSERT INTO settlements VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                f"condition-{index}",
                target_date,
                "24",
                1.0,
                "settled",
                f"{target_date}T12:00:00Z",
            )
            for index, target_date in enumerate(("2026-08-12", "2026-08-21"))
        ],
    )
    connection.commit()
    connection.close()

    summary = evaluate(
        candidates_path=candidates_path,
        db_path=db_path,
        artifact_id=artifact_id,
        model_outputs_path=model_outputs_path,
    )

    challenger = summary["prospective_challenger"]
    assert summary["denominator_lineage"]["P0_CANONICAL_PROBABILITY"]["rows"] == 2
    assert summary["denominator_lineage"]["LEGACY_111_HEADLINE"]["rows"] == 2
    assert challenger["policy_id"] == CHALLENGER_POLICY_ID
    assert challenger["freeze_qualification"]["eligible"] is True
    assert challenger["status"] == (
        "eligible_for_prospective_zero_notional_frozen_forward"
    )
    assert challenger["forward_funnel"]["status"] == "running"
    assert challenger["forward_funnel"]["positive_edge_signal_rows"] == 1
    assert challenger["forward_funnel"]["static_book_executable_selected_rows"] == 0
    assert summary["decision"] == (
        "inconclusive_keep_zero_notional_shadow_do_not_promote_live"
    )


def test_probability_denominator_does_not_require_execution_status(tmp_path: Path) -> None:
    artifact_id = "artifact-denominator"
    candidates_path = tmp_path / "candidates.jsonl"
    model_outputs_path = tmp_path / "model_outputs.jsonl"
    db_path = tmp_path / "weather.db"
    candidates = []
    outputs = []
    for index, status in enumerate(("scored", "blocked")):
        checkpoint_id = f"checkpoint-{index}"
        candidates.append(
            {
                "candidate_id": f"candidate-{index}",
                "checkpoint_id": checkpoint_id,
                "city": "Tokyo",
                "target_date": "2026-08-12",
                "decision_ts_utc": f"2026-08-12T0{index}:00:00Z",
                "condition_id": f"condition-{index}",
                "market_id": f"market-{index}",
                "token_id": f"token-{index}",
                "bracket": "24",
                "feature_book_snapshot_id": f"feature-book-{index}",
                "candidate_status": status,
                "blocker_reason": None if status == "scored" else "missing_direct_yes_ask",
                "p_model": 0.7,
                "market_p": 0.6,
                "selected": False,
                "executable_cost": 0.5 if status == "scored" else None,
                "execution_book_snapshot_id": None,
                "market_evidence_status": "available",
                "model_artifact_id": artifact_id,
                "model_id": "incumbent-model",
                "policy_id": "incumbent-policy",
                "input_refs": [
                    {"kind": "observation_archive", "path": "/tmp/observations", "available_at_utc": f"2026-08-12T0{index}:00:00Z"},
                    {"kind": "forecast_curve", "path": "/tmp/forecast", "available_at_utc": f"2026-08-12T0{index}:00:00Z"},
                ],
            }
        )
        outputs.append(
            {
                "checkpoint_id": checkpoint_id,
                "model_artifact_id": artifact_id,
                "p_model": 0.7,
                "metadata": {
                    "physical_innovation_logit": 1.0,
                    "cooling_window_state": "morning_cooling",
                },
            }
        )
    _write_jsonl(candidates_path, candidates)
    _write_jsonl(model_outputs_path, outputs)
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE fact_trades (fact_built_at_utc TEXT)")
    connection.execute(
        "CREATE TABLE settlements (condition_id TEXT, target_date TEXT, bracket TEXT, final_price REAL, settlement_status TEXT, created_at_utc TEXT)"
    )
    connection.executemany(
        "INSERT INTO settlements VALUES (?, '2026-08-12', '24', 1.0, 'settled', '2026-08-13T00:00:00Z')",
        [("condition-0",), ("condition-1",)],
    )
    connection.commit()
    connection.close()

    summary = evaluate(
        candidates_path=candidates_path,
        db_path=db_path,
        artifact_id=artifact_id,
        model_outputs_path=model_outputs_path,
    )

    lineage = summary["denominator_lineage"]
    assert lineage["P0_CANONICAL_PROBABILITY"]["rows"] == 2
    assert lineage["P1_ACTIVE_WINDOW_PROBABILITY"]["rows"] == 2
    assert lineage["LEGACY_111_HEADLINE"]["rows"] == 1
    assert lineage["E0_EXECUTION_CLEAN"]["rows"] == 1


def test_probability_pit_contract_rejects_future_or_missing_availability() -> None:
    base = {
        "candidate_id": "candidate",
        "checkpoint_id": "checkpoint",
        "condition_id": "condition",
        "market_id": "market",
        "token_id": "token",
        "bracket": "24",
        "feature_book_snapshot_id": "feature-book",
        "decision_ts_utc": "2026-08-12T03:00:00Z",
    }
    frame = pd.DataFrame(
        [
            {
                **base,
                "checkpoint_id": "legal",
                "input_refs": [
                    {"kind": "observation_archive", "available_at_utc": "2026-08-12T02:59:00Z"},
                    {"kind": "forecast_curve", "available_at_utc": "2026-08-12T03:00:00Z"},
                ],
            },
            {
                **base,
                "checkpoint_id": "future",
                "input_refs": [
                    {"kind": "observation_archive", "available_at_utc": "2026-08-12T02:59:00Z"},
                    {"kind": "forecast_curve", "available_at_utc": "2026-08-12T03:01:00Z"},
                ],
            },
            {
                **base,
                "checkpoint_id": "missing",
                "input_refs": [
                    {"kind": "observation_archive", "available_at_utc": "2026-08-12T02:59:00Z"},
                    {"kind": "forecast_curve"},
                ],
            },
        ]
    )

    assert _pit_probability_identity_legal(frame).tolist() == [True, False, False]


def test_research_oof_is_prior_date_only_and_calibration_fails_closed() -> None:
    rows = []
    for date_index in range(7):
        for row_index, label in enumerate((0, 1)):
            rows.append(
                {
                    "target_date": f"2026-08-{date_index + 1:02d}",
                    "city": "Tokyo",
                    "label": label,
                    "p_market": 0.25 if label == 0 else 0.75,
                    "feature": float(label),
                }
            )
    frame = pd.DataFrame(rows)

    calibrated, calibration_folds = _calibration_oof(frame)
    residual, residual_folds = _prior_date_oof(
        frame,
        ["feature"],
        offset_column="p_market",
        fallback_column="p_market",
    )

    assert np.isfinite(calibrated).all()
    assert np.isfinite(residual).all()
    assert all(
        fold["status"] == "fail_closed_identity"
        for fold in calibration_folds[:5]
    )
    assert all(fold["status"] == "fail_closed" for fold in residual_folds[:5])
    assert calibration_folds[5]["prior_dates"] == 5
    assert residual_folds[5]["prior_dates"] == 5
    assert calibration_folds[5]["prior_rows"] == 10
    assert residual_folds[5]["prior_rows"] == 10


def test_date_equal_metric_does_not_overweight_checkpoint_dense_date() -> None:
    frame = pd.DataFrame(
        {
            "target_date": ["2026-08-01"] * 10 + ["2026-08-02"],
            "city": ["Tokyo"] * 11,
            "label": [1] * 11,
            "p_market": [0.5] * 11,
            "p_candidate": [0.9] * 10 + [0.1],
        }
    )

    summary = _model_summary(frame, "p_candidate")

    expected = (-np.log(0.9) - np.log(0.1)) / 2
    assert np.isclose(summary["date"]["logloss"], expected)
    assert not np.isclose(summary["row"]["logloss"], expected)


def test_corrected_gradient_is_exactly_zero_outside_route() -> None:
    frame = pd.DataFrame(
        {
            "target_date": ["2026-08-01", "2026-08-02"],
            "city": ["Seoul", "Tokyo"],
            "label": [1, 0],
            "p_market": [0.7, 0.7],
            "physical_innovation": [2.0, 2.0],
            "routing_indicator": [1, 0],
            "P1_ACTIVE_WINDOW_PROBABILITY": [True, False],
        }
    )
    frame["score_gradient_alpha0_routed"] = (
        frame["physical_innovation"]
        * frame["routing_indicator"]
        * (frame["label"] - frame["p_market"])
    )

    result = _gradient_diagnostics(frame)

    assert frame.loc[1, "score_gradient_alpha0_routed"] == 0.0
    assert result["outside_p1_rows"] == 1
    assert result["outside_p1_nonzero_rows"] == 0


def test_iem_target_date_uses_positive_utc9_local_conversion(tmp_path: Path) -> None:
    source = tmp_path / "RKSI.csv"
    source.write_text(
        "station,valid,tmpc,metar\n"
        "RKSI,2026-04-24 14:30,10.0,RKSI TEST\n"
        "RKSI,2026-04-24 15:00,9.0,RKSI TEST\n",
        encoding="utf-8",
    )

    frame = load_iem_path("Seoul", source, "2026-04-24", "2026-04-25")

    assert frame["target_date"].tolist() == ["2026-04-24", "2026-04-25"]
    assert rung_contains("9", 9)
    assert rung_contains("9 or below", 8)
    assert not rung_contains("9", 8)


def test_event_truth_uses_city_timezone_and_size_gate_is_computed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(CITY_TIMEZONES, "Tokyo", "UTC")
    path = pd.DataFrame(
        {
            "city": ["Tokyo", "Tokyo"],
            "target_date": ["2026-01-01", "2026-01-01"],
            "observation_event_time_utc": pd.to_datetime(
                ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"], utc=True
            ),
            "published_available_time_utc": pd.to_datetime([None, None], utc=True),
            "normalized_native_rung": [5, 4],
            "source": ["test", "test"],
            "source_version": ["v1", "v1"],
        }
    )
    reconciliation = pd.DataFrame(
        {
            "city": ["Tokyo"],
            "target_date": ["2026-01-01"],
            "reconciliation_status": ["EXACT_MATCH_IEM_MIRROR"],
            "exchange_resolved_rung": ["4"],
        }
    )

    event = build_event_truth(path, reconciliation)

    assert event.loc[0, "right_censor_time"] == pd.Timestamp(
        "2026-01-01T23:59:59.999999Z"
    )
    assert bool(event.loc[0, "crossed_before_day_end"])
    small = weather_panel_manifest(event)
    assert small["provisional_size_gate"] == "FAIL"

    panel = pd.DataFrame(
        [
            {
                "city": city,
                "target_date": f"2026-01-{day + 1:02d}",
                "crossed_before_day_end": True,
            }
            for city in ("Seoul", "Tokyo")
            for day in range(60)
        ]
    )
    assert weather_panel_manifest(panel)["provisional_size_gate"] == "PASS"


def test_truth_builder_refuses_nonempty_output_by_default(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    (output / "evidence.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        build_truth(
            SimpleNamespace(
                output_dir=output,
                allow_existing_output=False,
            )
        )
