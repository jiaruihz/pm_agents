"""P1 resolution/linker contract tests; all fixtures are offline and deterministic."""

from datetime import timedelta
from decimal import Decimal

import pytest

from src.polymarket_alpha.contracts import (
    CalibrationDimension, CaptureScope, EstimateStage, ResolutionAdjudicationStatus,
    ResolutionOutcome, ScoringEligibility, PositionState, ReviewAction,
)
from src.polymarket_alpha.decision import build_ranked_ledger
from src.polymarket_alpha.learning import LearningResolutionError, build_market_resolution, build_prediction_resolution_link
from tests.polymarket_alpha.test_decision_ledger_p0_09b import NOW, _artifact_and_claim, _inputs


def _lineage():
    inputs = _inputs()
    outcome = build_ranked_ledger(**inputs)
    artifact, _ = _artifact_and_claim("learning-resolution")
    resolution = build_market_resolution(
        source_artifact=artifact, rule_contract=inputs["contract"], market_id="market-1",
        condition_id="condition-1",
        outcome=ResolutionOutcome.YES, adjudication_status=ResolutionAdjudicationStatus.FINAL,
        resolved_at=NOW - timedelta(minutes=5), source_observed_at=NOW - timedelta(minutes=3),
        created_at=NOW, run_id="p1-run", parser_version="fixture-resolution-v1",
    )
    link = build_prediction_resolution_link(
        prediction=outcome.prediction, decision=outcome.decision,
        probability_estimate=inputs["market_result"].probability_estimate,
        rule_contract=inputs["contract"], resolution=resolution, orderbook=inputs["book"],
        market_type="binary", linked_at=NOW, run_id="p1-run", fee_amount=Decimal("0.10"),
        fee_model_version="fixture_fee_v1",
    )
    return inputs, outcome, artifact, resolution, link


def test_resolution_and_simulated_link_are_deterministic_and_bound() -> None:
    inputs, outcome, artifact, resolution, link = _lineage()
    assert resolution.source_artifact_sha256 == artifact.canonical_sha256
    assert link.scoring_eligibility == ScoringEligibility.ELIGIBLE
    assert link.entry_basis is not None
    assert link.entry_basis.direction == "YES"
    assert link.entry_basis.gross_cost == Decimal("6.50")
    again = build_prediction_resolution_link(
        prediction=outcome.prediction, decision=outcome.decision,
        probability_estimate=inputs["market_result"].probability_estimate,
        rule_contract=inputs["contract"], resolution=resolution, orderbook=inputs["book"],
        market_type="binary", linked_at=NOW, run_id="p1-run", fee_amount=Decimal("0.10"), fee_model_version="fixture_fee_v1",
    )
    assert again == link


@pytest.mark.parametrize(
    ("outcome", "status", "expected"),
    [
        (ResolutionOutcome.INVALID, ResolutionAdjudicationStatus.FINAL, ScoringEligibility.EXCLUDED_INVALID),
        (ResolutionOutcome.YES, ResolutionAdjudicationStatus.PENDING_DISPUTE, ScoringEligibility.EXCLUDED_PENDING_DISPUTE),
    ],
)
def test_invalid_and_pending_resolution_are_typed_exclusions(outcome, status, expected) -> None:
    inputs, ledger, artifact, _, _ = _lineage()
    resolution = build_market_resolution(
        source_artifact=artifact, rule_contract=inputs["contract"], market_id="market-1", outcome=outcome,
        condition_id="condition-1",
        adjudication_status=status, resolved_at=NOW - timedelta(minutes=5),
        source_observed_at=NOW - timedelta(minutes=3), created_at=NOW, run_id="p1-run", parser_version="v1",
    )
    link = build_prediction_resolution_link(
        prediction=ledger.prediction, decision=ledger.decision, probability_estimate=inputs["market_result"].probability_estimate,
        rule_contract=inputs["contract"], resolution=resolution, orderbook=inputs["book"], market_type="binary",
        linked_at=NOW, run_id="p1-run",
    )
    assert link.scoring_eligibility == expected
    assert link.exclusion_reason == expected.value.removeprefix("EXCLUDED_")


def test_resolution_and_link_fail_closed_for_source_model_copy_nonfinal_and_book_mismatch() -> None:
    inputs, ledger, artifact, resolution, _ = _lineage()
    reference_only = artifact.model_copy(update={"capture_scope": CaptureScope.REFERENCE_ONLY, "content_sha256": None})
    with pytest.raises(LearningResolutionError, match="invalid frozen SourceArtifact"):
        build_market_resolution(
            source_artifact=reference_only, rule_contract=inputs["contract"], market_id="market-1",
            condition_id="condition-1",
            outcome=ResolutionOutcome.YES, adjudication_status=ResolutionAdjudicationStatus.FINAL,
            resolved_at=NOW - timedelta(minutes=5), source_observed_at=NOW - timedelta(minutes=3),
            created_at=NOW, run_id="p1-run", parser_version="v1",
        )
    nonfinal = inputs["market_result"].probability_estimate.model_copy(update={"estimate_stage": EstimateStage.POST_RED_TEAM})
    with pytest.raises(LearningResolutionError, match="FINAL"):
        build_prediction_resolution_link(
            prediction=ledger.prediction, decision=ledger.decision, probability_estimate=nonfinal,
            rule_contract=inputs["contract"], resolution=resolution, orderbook=inputs["book"], market_type="binary",
            linked_at=NOW, run_id="p1-run",
        )
    with pytest.raises(LearningResolutionError, match="frozen orderbook id"):
        build_prediction_resolution_link(
            prediction=ledger.prediction, decision=ledger.decision,
            probability_estimate=inputs["market_result"].probability_estimate, rule_contract=inputs["contract"],
            resolution=resolution, orderbook=inputs["book"].model_copy(update={"record_id": "orderbook_snapshot:other"}),
            market_type="binary", linked_at=NOW, run_id="p1-run",
        )


def test_insufficient_depth_and_cross_rule_resolution_are_rejected() -> None:
    inputs, ledger, _, resolution, _ = _lineage()
    depth = inputs["book"].yes_depth[0].model_copy(update={"buy_insufficient_depth": True, "buy_vwap": None})
    with pytest.raises(LearningResolutionError, match="sufficient directional"):
        build_prediction_resolution_link(
            prediction=ledger.prediction, decision=ledger.decision,
            probability_estimate=inputs["market_result"].probability_estimate, rule_contract=inputs["contract"],
            resolution=resolution, orderbook=inputs["book"].model_copy(update={"yes_depth": (depth,)}),
            market_type="binary", linked_at=NOW, run_id="p1-run",
        )
    with pytest.raises(LearningResolutionError, match="rule hash"):
        build_prediction_resolution_link(
            prediction=ledger.prediction, decision=ledger.decision,
            probability_estimate=inputs["market_result"].probability_estimate, rule_contract=inputs["contract"],
            resolution=resolution.model_copy(update={"rule_hash": "b" * 64}), orderbook=inputs["book"],
            market_type="binary", linked_at=NOW, run_id="p1-run",
        )


def test_no_position_has_no_simulated_entry_basis() -> None:
    inputs, ledger, _, resolution, _ = _lineage()
    decision = ledger.decision.model_copy(
        update={"action": ReviewAction.WATCH, "target_size": None}
    )
    link = build_prediction_resolution_link(
        prediction=ledger.prediction.model_copy(update={"position_state": PositionState.NO_POSITION}),
        decision=decision, probability_estimate=inputs["market_result"].probability_estimate,
        rule_contract=inputs["contract"], resolution=resolution, orderbook=inputs["book"],
        market_type="binary", linked_at=NOW, run_id="p1-run",
    )
    assert link.entry_basis is None
