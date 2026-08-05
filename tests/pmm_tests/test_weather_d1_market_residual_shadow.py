from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.strategies.weather_d1_market_residual_shadow import (
    FrozenResidualArtifact,
    LinearMarketResidualModel,
    ShadowPolicy,
    ShadowRuntime,
    apply_market_residual,
    evaluate_settlements,
)
from src.strategies.weather_d1_market_residual_shadow.runtime import load_jsonl


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "src/strategies/weather_d1_market_residual_shadow/fixtures"


def _model() -> LinearMarketResidualModel:
    return LinearMarketResidualModel(FrozenResidualArtifact.load(FIXTURES / "demo_artifact.json"))


def test_zero_residual_is_exact_normalized_market() -> None:
    assert apply_market_residual([0.2, 0.3, 0.5], [0.0, 0.0, 0.0]) == [0.2, 0.3, 0.5]
    assert apply_market_residual([2.0, 3.0, 5.0], [0.0, 0.0, 0.0]) == [0.2, 0.3, 0.5]


def test_default_runtime_outputs_full_universe_without_candidate_or_intent(tmp_path: Path) -> None:
    checkpoints = load_jsonl(FIXTURES / "demo_checkpoints.jsonl")
    summary = ShadowRuntime(_model()).run(checkpoints, tmp_path / "runtime/research/run")
    predictions = load_jsonl(tmp_path / "runtime/research/run/predictions.jsonl")
    assert len(predictions) == 6
    assert {(row["checkpoint_id"], row["expression_id"]) for row in predictions} == {
        ("demo-cp-1", "demo_30"), ("demo-cp-1", "demo_31"), ("demo-cp-1", "demo_32"),
        ("demo-cp-2", "demo_30"), ("demo-cp-2", "demo_31"), ("demo-cp-2", "demo_32"),
    }
    assert {row["feature_book_snapshot_id"] for row in predictions} == {"feature-book-1", "feature-book-2"}
    assert {row["execution_book_snapshot_id"] for row in predictions} == {"execution-book-1", "execution-book-2"}
    assert summary["candidate_rows"] == summary["intent_rows"] == summary["orders_submitted"] == 0
    assert summary["no_order_placed"] is True
    assert (tmp_path / "runtime/research/run/signal_candidates.jsonl").read_text() == ""


def test_missing_feature_blocks_every_declared_rung_without_dropping_universe() -> None:
    checkpoint = load_jsonl(FIXTURES / "demo_checkpoints.jsonl")[0]
    checkpoint["features"] = {}
    result = ShadowRuntime(_model()).score_checkpoint(checkpoint)
    assert len(result["predictions"]) == 3
    assert {row["scorable_status"] for row in result["predictions"]} == {"not_scorable"}
    assert all("missing_required_features:revision_up" in row["blocker_reasons"] for row in result["predictions"])


def test_rung_level_features_are_used_for_distribution_residual() -> None:
    checkpoint = load_jsonl(FIXTURES / "demo_checkpoints.jsonl")[0]
    checkpoint["features"] = {}
    for value, rung in zip((-1.0, 0.0, 1.0), checkpoint["feature_book_snapshot"]["rungs"]):
        rung["features"] = {"revision_up": value}
    result = ShadowRuntime(_model()).score_checkpoint(checkpoint)
    assert {row["scorable_status"] for row in result["predictions"]} == {"scorable"}
    assert any(
        row["posterior_probability"] != row["market_probability"]
        for row in result["predictions"]
    )


def test_incomplete_ladder_and_future_artifact_block_all_declared_rungs() -> None:
    checkpoint = load_jsonl(FIXTURES / "demo_checkpoints.jsonl")[0]
    checkpoint["feature_book_snapshot"]["rung_completeness"] = "incomplete"
    checkpoint["decision_ts_utc"] = "2026-06-30T23:00:00Z"
    checkpoint["event_available_at_utc"] = "2026-06-30T22:59:00Z"
    checkpoint["feature_book_snapshot"]["observed_at_utc"] = "2026-06-30T22:58:00Z"
    checkpoint["execution_book_snapshot"]["observed_at_utc"] = "2026-06-30T22:59:30Z"
    result = ShadowRuntime(_model()).score_checkpoint(checkpoint)
    assert len(result["predictions"]) == 3
    assert all("native_ladder_incomplete" in row["blocker_reasons"] for row in result["predictions"])
    assert all("model_artifact_not_available_at_decision" in row["blocker_reasons"] for row in result["predictions"])
    assert len(result["blockers"]) == 1


def test_explicit_candidate_intent_mode_stays_zero_notional(tmp_path: Path) -> None:
    policy = ShadowPolicy(emit_candidates=True, emit_intents=True, minimum_edge=0.02)
    summary = ShadowRuntime(_model(), policy).run(
        load_jsonl(FIXTURES / "demo_checkpoints.jsonl"),
        tmp_path / "runtime/research/run",
    )
    candidates = load_jsonl(tmp_path / "runtime/research/run/signal_candidates.jsonl")
    intents = load_jsonl(tmp_path / "runtime/research/run/trade_intents.jsonl")
    assert len(candidates) == 6
    assert intents
    assert all(row["mode"] == "zero_notional" for row in intents)
    assert all(row["requested_size"] == 0.0 and row["no_order_placed"] is True for row in intents)
    assert summary["orders_submitted"] == summary["venue_calls"] == 0
    package_text = "\n".join(
        path.read_text() for path in (ROOT / "src/strategies/weather_d1_market_residual_shadow").glob("*.py")
    )
    assert "py_clob_client" not in package_text
    assert "submit_order" not in package_text


def test_raw_ask_without_fee_or_depth_is_not_executable() -> None:
    checkpoint = load_jsonl(FIXTURES / "demo_checkpoints.jsonl")[0]
    for rung in checkpoint["execution_book_snapshot"]["rungs"]:
        rung.pop("fee_adjusted_ask")
        rung.pop("depth_at_or_better")
    result = ShadowRuntime(_model(), ShadowPolicy(emit_candidates=True)).score_checkpoint(checkpoint)
    assert len(result["candidates"]) == 3
    assert {row["candidate_status"] for row in result["candidates"]} == {"blocked"}
    assert {row["blocker_reason"] for row in result["candidates"]} == {
        "official_fee_adjusted_cost_missing"
    }
    assert result["intents"] == []


def test_settlement_replay_uses_same_denominator_and_beats_demo_market() -> None:
    predictions = []
    runtime = ShadowRuntime(_model())
    for checkpoint in load_jsonl(FIXTURES / "demo_checkpoints.jsonl"):
        predictions.extend(runtime.score_checkpoint(checkpoint)["predictions"])
    report, labeled = evaluate_settlements(
        predictions, load_jsonl(FIXTURES / "demo_settlements.jsonl")
    )
    quality = report["same_denominator_probability_quality"]
    assert quality["checkpoint_rows"] == 2
    assert quality["target_dates"] == 2
    assert len(labeled) == 6
    assert quality["model_minus_market"]["logloss"] < 0
    assert report["execution"]["no_order_placed"] is True


def test_artifact_sha_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="sha256 mismatch"):
        FrozenResidualArtifact.load(FIXTURES / "demo_artifact.json", expected_sha256="0" * 64)


def test_output_path_rejects_non_research_location(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="shadow output"):
        ShadowRuntime(_model()).run([], tmp_path / "production")
