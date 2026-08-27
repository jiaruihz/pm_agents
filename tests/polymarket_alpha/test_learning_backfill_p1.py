"""Offline P1 correction-head selection and prediction backfill planning tests."""

from datetime import timedelta
from decimal import Decimal

import pytest

from src.polymarket_alpha.contracts import ResolutionAdjudicationStatus, ResolutionOutcome
from src.polymarket_alpha.learning.backfill import (
    BackfillFailureCode,
    PredictionBackfillInput,
    PredictionBackfillSuccess,
    ResolutionSelectionError,
    ResolutionSelectionPolicy,
    contracts_for_backfill_persistence,
    plan_prediction_backfill,
    select_resolution_head,
)
from src.polymarket_alpha.learning.resolution import build_market_resolution
from src.polymarket_alpha.learning.scoring import ScoringPolicy
from src.polymarket_alpha.security import audit_source_tree
from tests.polymarket_alpha.test_learning_resolution_p1 import NOW, _lineage


def _correction_chain():
    inputs, ledger, artifact, root, _ = _lineage()
    correction = build_market_resolution(
        source_artifact=artifact, rule_contract=inputs["contract"], market_id="market-1",
        condition_id="condition-1", outcome=ResolutionOutcome.NO,
        adjudication_status=ResolutionAdjudicationStatus.FINAL,
        resolved_at=NOW - timedelta(minutes=4), source_observed_at=NOW - timedelta(minutes=2),
        created_at=NOW + timedelta(minutes=1), run_id="p1-correction", parser_version="fixture-v2",
        supersedes=root,
    )
    return inputs, ledger, artifact, root, correction


def _selection(*items):
    return select_resolution_head(
        items, market_id="market-1", policy=ResolutionSelectionPolicy(version="selection-v1"),
        selected_at=NOW + timedelta(minutes=2), run_id="selection-run",
    )


def _bundle(inputs, ledger) -> PredictionBackfillInput:
    return PredictionBackfillInput(
        prediction=ledger.prediction, decision=ledger.decision,
        probability_estimate=inputs["market_result"].probability_estimate,
        rule_contract=inputs["contract"], orderbook=inputs["book"], market_type="binary",
        fee_amount=Decimal("0.10"), fee_model_version="fixture_fee_v1",
    )


def test_selects_single_and_corrected_head_with_all_hash_references() -> None:
    _, _, _, root, correction = _correction_chain()
    receipt = _selection(root, correction)
    assert receipt.selected_resolution_id == correction.record_id
    assert receipt.selected_outcome == ResolutionOutcome.NO
    assert len(receipt.resolution_references) == 2
    assert receipt == _selection(correction, root)


@pytest.mark.parametrize("mutation, match", [
    ("fork", "fork"), ("dangling", "missing"), ("hash", "hash"), ("backward", "backward"),
])
def test_correction_chain_rejects_fork_dangling_hash_and_backward_clocks(mutation, match) -> None:
    _, _, _, root, correction = _correction_chain()
    if mutation == "fork":
        other = build_market_resolution(
            source_artifact=correction.model_copy().source_artifact_id and _lineage()[2],
            rule_contract=_lineage()[0]["contract"], market_id="market-1", condition_id="condition-1",
            outcome=ResolutionOutcome.YES, adjudication_status=ResolutionAdjudicationStatus.FINAL,
            resolved_at=NOW - timedelta(minutes=3), source_observed_at=NOW - timedelta(minutes=1),
            created_at=NOW + timedelta(minutes=1), run_id="other", parser_version="fixture-v3", supersedes=root,
        )
        values = (root, correction, other)
    elif mutation == "dangling":
        values = (root, correction.model_copy(update={"supersedes_resolution_id": "resolution:missing"}))
    elif mutation == "hash":
        values = (root, correction.model_copy(update={"supersedes_resolution_sha256": "b" * 64}))
    else:
        values = (root, correction.model_copy(update={"source_observed_at": root.source_observed_at - timedelta(seconds=1)}))
    with pytest.raises(ResolutionSelectionError, match=match):
        _selection(*values)


def test_cycle_model_copy_and_pending_head_fail_closed() -> None:
    _, _, _, root, correction = _correction_chain()
    cycle_root = root.model_copy(update={
        "supersedes_resolution_id": correction.record_id,
        "supersedes_resolution_sha256": correction.canonical_sha256,
        "source_observed_at": correction.source_observed_at,
        "created_at": correction.created_at,
    })
    # A forged cycle necessarily changes the predecessor hash first; the
    # content-addressed link rejects it before graph traversal.
    with pytest.raises(ResolutionSelectionError, match="hash"):
        _selection(cycle_root, correction)
    broken = correction.model_copy(update={"resolution_id": "resolution:broken"})
    with pytest.raises(ResolutionSelectionError, match="invalid frozen"):
        _selection(root, broken)
    inputs, _, artifact, _, _ = _correction_chain()
    pending = build_market_resolution(
        source_artifact=artifact, rule_contract=inputs["contract"], market_id="market-1",
        condition_id="condition-1", outcome=ResolutionOutcome.YES,
        adjudication_status=ResolutionAdjudicationStatus.PENDING_DISPUTE,
        resolved_at=NOW - timedelta(minutes=4), source_observed_at=NOW - timedelta(minutes=2),
        created_at=NOW + timedelta(minutes=1), run_id="pending", parser_version="fixture-pending", supersedes=root,
    )
    with pytest.raises(ResolutionSelectionError, match="PENDING_DISPUTE"):
        _selection(root, pending)


def test_backfill_partial_isolation_duplicates_and_deterministic_replay() -> None:
    inputs, ledger, _, root, _ = _correction_chain()
    selection = _selection(root)
    good = _bundle(inputs, ledger)
    bad = good.model_copy(update={"market_type": ""})
    plan = plan_prediction_backfill(
        selection, (bad, good), scoring_policy=ScoringPolicy(version="score-v1"),
        planned_at=NOW + timedelta(minutes=2), run_id="backfill-run",
    )
    assert plan.successes == ()
    assert [(item.prediction_id, item.code) for item in plan.failures] == [
        (ledger.prediction.record_id, BackfillFailureCode.DUPLICATE_PREDICTION),
        (ledger.prediction.record_id, BackfillFailureCode.DUPLICATE_PREDICTION),
    ]
    # A malformed bundle cannot suppress a distinct valid prediction.
    other_prediction = ledger.prediction.model_validate({
        **ledger.prediction.model_dump(mode="python"),
        "record_id": "prediction:other", "prediction_id": "prediction:other",
        "decision_id": "review_decision:other",
    })
    other_decision = ledger.decision.model_validate({
        **ledger.decision.model_dump(mode="python"), "record_id": "review_decision:other",
    })
    other = PredictionBackfillInput(
        prediction=other_prediction, decision=other_decision,
        probability_estimate=inputs["market_result"].probability_estimate,
        rule_contract=inputs["contract"], orderbook=inputs["book"], market_type="binary",
    )
    isolated = plan_prediction_backfill(
        selection, (bad, other), scoring_policy=ScoringPolicy(version="score-v1"),
        planned_at=NOW + timedelta(minutes=2), run_id="backfill-run",
    )
    assert [item.prediction_id for item in isolated.successes] == ["prediction:other"]
    assert isolated.failures[0].code == BackfillFailureCode.INVALID_BUNDLE
    assert isolated == plan_prediction_backfill(
        selection, (other, bad), scoring_policy=ScoringPolicy(version="score-v1"),
        planned_at=NOW + timedelta(minutes=2), run_id="backfill-run",
    )
    facts = contracts_for_backfill_persistence(selection, isolated)
    assert facts[0] == selection.selected_resolution
    assert facts[1] == selection
    assert facts[-1] == isolated
    assert len({item.record_id for item in facts}) == len(facts)


def test_selection_receipt_tampering_is_revalidated_before_backfill() -> None:
    inputs, ledger, _, root, _ = _correction_chain()
    selection = _selection(root)
    bundle = _bundle(inputs, ledger)
    tampered = selection.model_copy(update={"selected_outcome": ResolutionOutcome.NO})
    with pytest.raises(ResolutionSelectionError, match="invalid frozen"):
        plan_prediction_backfill(
            tampered,
            (bundle,),
            scoring_policy=ScoringPolicy(version="score-v1"),
            planned_at=NOW + timedelta(minutes=2),
            run_id="backfill-run",
        )


def test_persistence_rejects_score_bound_to_another_resolution() -> None:
    inputs, ledger, _, root, _ = _correction_chain()
    selection = _selection(root)
    plan = plan_prediction_backfill(
        selection,
        (_bundle(inputs, ledger),),
        scoring_policy=ScoringPolicy(version="score-v1"),
        planned_at=NOW + timedelta(minutes=2),
        run_id="backfill-run",
    )
    success = plan.successes[0]
    forged_score = success.score.model_copy(update={"resolution_id": "resolution:other"})
    with pytest.raises(ValueError, match="resolution ids"):
        PredictionBackfillSuccess(
            prediction_id=success.prediction_id,
            link=success.link,
            score=forged_score,
        )

    # Persistence revalidates the selected head even if an object was forged
    # after normal model construction.
    forged_success = success.model_copy(update={"score": forged_score})
    forged_plan = plan.model_copy(update={"successes": (forged_success,)})
    with pytest.raises(ResolutionSelectionError, match="invalid frozen"):
        contracts_for_backfill_persistence(selection, forged_plan)


def test_non_binary_or_pending_selected_head_produces_no_plan() -> None:
    inputs, ledger, _, root, correction = _correction_chain()
    artifact = _correction_chain()[2]
    invalid = build_market_resolution(
        source_artifact=artifact, rule_contract=inputs["contract"], market_id="market-1",
        condition_id="condition-1", outcome=ResolutionOutcome.INVALID,
        adjudication_status=ResolutionAdjudicationStatus.FINAL,
        resolved_at=NOW - timedelta(minutes=4), source_observed_at=NOW - timedelta(minutes=2),
        created_at=NOW + timedelta(minutes=1), run_id="invalid", parser_version="fixture-invalid", supersedes=root,
    )
    selection = _selection(root, invalid)
    with pytest.raises(ResolutionSelectionError, match="FINAL YES or NO"):
        plan_prediction_backfill(
            selection, (_bundle(inputs, ledger),), scoring_policy=ScoringPolicy(version="score-v1"),
            planned_at=NOW + timedelta(minutes=2), run_id="backfill-run",
        )


def test_backfill_planner_has_no_transport_or_storage_capability() -> None:
    assert audit_source_tree("src/polymarket_alpha/learning/backfill.py").violations == ()
