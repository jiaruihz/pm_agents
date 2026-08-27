"""P1 scoring/calibration tests; no transport or storage is involved."""

from decimal import Decimal

import pytest

from src.polymarket_alpha.contracts import CalibrationDimension, ResolutionOutcome, ScoringEligibility
from src.polymarket_alpha.learning import CalibrationPolicy, ScoringPolicy, build_calibration_report, score_prediction
from src.polymarket_alpha.learning.scoring import LearningScoringError
from tests.polymarket_alpha.test_learning_resolution_p1 import NOW, _lineage


def test_score_has_binary_metrics_baseline_and_simulated_pnl() -> None:
    _, _, _, resolution, link = _lineage()
    score = score_prediction(link=link, resolution=resolution, policy=ScoringPolicy(version="score-v1"), scored_at=NOW, run_id="p1-run")
    assert score.brier_score == Decimal("0.04")
    assert score.market_baseline_brier_score == Decimal("0.2025")
    assert score.simulated_pnl == Decimal("3.40")
    assert score.entry_vwap == Decimal("0.65")


def test_scoring_clips_zero_and_one_for_log_loss_and_rejects_excluded() -> None:
    _, _, _, resolution, link = _lineage()
    zero = link.model_copy(update={"predicted_probability": Decimal("0")})
    score = score_prediction(link=zero, resolution=resolution, policy=ScoringPolicy(version="score-v1", probability_epsilon=Decimal("0.01")), scored_at=NOW, run_id="p1-run")
    assert score.log_loss > Decimal("4")
    excluded = link.model_copy(update={"scoring_eligibility": ScoringEligibility.EXCLUDED_INVALID, "exclusion_reason": "INVALID"})
    with pytest.raises(LearningScoringError, match="excluded"):
        score_prediction(link=excluded, resolution=resolution, policy=ScoringPolicy(version="score-v1"), scored_at=NOW, run_id="p1-run")


def test_calibration_dimensions_exclusions_duplicate_prediction_and_replay() -> None:
    _, _, _, resolution, link = _lineage()
    score = score_prediction(link=link, resolution=resolution, policy=ScoringPolicy(version="score-v1"), scored_at=NOW, run_id="p1-run")
    policy = CalibrationPolicy(version="cal-v1")
    market = build_calibration_report(scores=(score,), dimension=CalibrationDimension.MARKET_TYPE, policy=policy, reported_at=NOW, run_id="p1-run")
    assert market.slices[0].slice_key == "binary"
    clarity = build_calibration_report(scores=(score,), dimension=CalibrationDimension.RULE_CLARITY, policy=policy, reported_at=NOW, run_id="p1-run")
    assert clarity.slices[0].count == 1
    entry = build_calibration_report(scores=(score,), dimension=CalibrationDimension.ENTRY_PRICE, policy=policy, reported_at=NOW, run_id="p1-run")
    assert entry.slices[0].count == 1
    assert entry == build_calibration_report(scores=(score,), dimension=CalibrationDimension.ENTRY_PRICE, policy=policy, reported_at=NOW, run_id="p1-run")
    no_entry = score.model_copy(update={"score_id": "prediction_score:no_entry", "record_id": "prediction_score:no_entry", "entry_vwap": None, "simulated_pnl": None, "prediction_id": "prediction:no_entry"})
    mixed = build_calibration_report(scores=(score, no_entry), dimension=CalibrationDimension.ENTRY_PRICE, policy=policy, reported_at=NOW, run_id="p1-run")
    assert mixed.excluded_score_ids == (no_entry.record_id,)
    assert tuple(item.score_id for item in mixed.score_references) == tuple(sorted((score.record_id, no_entry.record_id)))
    with pytest.raises(LearningScoringError, match="duplicate predictions"):
        build_calibration_report(scores=(score, score.model_copy(update={"record_id": "prediction_score:duplicate", "score_id": "prediction_score:duplicate"})), dimension=CalibrationDimension.MARKET_TYPE, policy=policy, reported_at=NOW, run_id="p1-run")
