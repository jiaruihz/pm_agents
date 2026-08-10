from __future__ import annotations

import math

import pytest

from weather_model_evaluation.exact_bracket_probability import (
    EXECUTION_HEAD_SCHEMA_VERSION,
    PROBABILITY_STACK_SCHEMA_VERSION,
    ExactBracketDistribution,
    ExecutionFillHeadOutput,
    FinalSettlementHeadOutput,
    LadderLogAdjustment,
    ProbabilityHeadKind,
    RepricingHeadOutput,
    SettlementBracket,
    SettlementLadder,
    compose_exact_bracket_probability,
)


def _ladder() -> SettlementLadder:
    return SettlementLadder(
        city="Helsinki",
        target_date="2026-08-10",
        settlement_source="fmi_daily_summary",
        native_unit="degC_integer",
        native_step=1.0,
        brackets=(
            SettlementBracket("le21", "21 or below", None, 21.0),
            SettlementBracket("22", "22", 22.0, 22.0),
            SettlementBracket("23", "23", 23.0, 23.0),
            SettlementBracket("ge24", "24 or above", 24.0, None),
        ),
    )


def _prior(ladder: SettlementLadder) -> ExactBracketDistribution:
    return ExactBracketDistribution.from_mapping(
        ladder,
        {"le21": 0.10, "22": 0.30, "23": 0.40, "ge24": 0.20},
        source_kind="market_prior",
        source_snapshot_id="book-pre-event",
        observed_at_utc="2026-08-10T10:00:00Z",
    )


def test_settlement_ladder_requires_complete_native_partition() -> None:
    with pytest.raises(ValueError, match="gap or overlap"):
        SettlementLadder(
            city="Helsinki",
            target_date="2026-08-10",
            settlement_source="fmi_daily_summary",
            native_unit="degC_integer",
            native_step=1.0,
            brackets=(
                SettlementBracket("le21", "21 or below", None, 21.0),
                SettlementBracket("23", "23", 23.0, 23.0),
                SettlementBracket("ge24", "24 or above", 24.0, None),
            ),
        )


def test_distribution_refuses_partial_or_non_normalized_ladder() -> None:
    ladder = _ladder()
    with pytest.raises(ValueError, match="does not match ladder"):
        ExactBracketDistribution.from_mapping(
            ladder,
            {"le21": 0.5, "22": 0.5},
            source_kind="market_prior",
            source_snapshot_id="partial",
            observed_at_utc="2026-08-10T10:00:00Z",
        )
    with pytest.raises(ValueError, match="sum to one"):
        ExactBracketDistribution(
            ladder_id=ladder.ladder_id,
            bracket_ids=ladder.bracket_ids,
            probabilities=(0.1, 0.2, 0.3, 0.3),
            source_kind="market_prior",
            source_snapshot_id="bad-mass",
            observed_at_utc="2026-08-10T10:00:00Z",
        )


def test_probability_stack_preserves_full_mass_and_stage_lineage() -> None:
    ladder = _ladder()
    weather = LadderLogAdjustment(
        ladder_id=ladder.ladder_id,
        bracket_ids=ladder.bracket_ids,
        log_weights=(-0.3, -0.1, 0.2, 0.4),
        component_id="weather-path-v1",
        observed_at_utc="2026-08-10T10:00:01Z",
        source_snapshot_ids=("fmi-path-1",),
    )
    basis = LadderLogAdjustment(
        ladder_id=ladder.ladder_id,
        bracket_ids=ladder.bracket_ids,
        log_weights=(0.1, 0.0, -0.1, -0.2),
        component_id="source-basis-v1",
        observed_at_utc="2026-08-10T10:00:01Z",
        source_snapshot_ids=("fmi-basis-train",),
    )

    def calibrate(value: ExactBracketDistribution) -> ExactBracketDistribution:
        sharpened = [probability**1.1 for probability in value.probabilities]
        total = sum(sharpened)
        return ExactBracketDistribution(
            ladder_id=value.ladder_id,
            bracket_ids=value.bracket_ids,
            probabilities=tuple(probability / total for probability in sharpened),
            source_kind="calibrated_final",
            source_snapshot_id="simplex-calibrator-v1",
            observed_at_utc=value.observed_at_utc,
        )

    result = compose_exact_bracket_probability(
        ladder,
        market_prior=_prior(ladder),
        weather_path_residual=weather,
        source_basis_correction=basis,
        calibration_id="simplex-calibrator-v1",
        calibration=calibrate,
    )

    assert result.schema_version == PROBABILITY_STACK_SCHEMA_VERSION
    assert result.weather_path_component_id == "weather-path-v1"
    assert result.source_basis_component_id == "source-basis-v1"
    assert result.calibration_id == "simplex-calibrator-v1"
    for distribution in (
        result.market_prior,
        result.after_weather_path,
        result.after_source_basis,
        result.final_distribution,
    ):
        assert math.isclose(sum(distribution.probabilities), 1.0)
        assert distribution.bracket_ids == ladder.bracket_ids
    assert result.stack_snapshot_id

    head = FinalSettlementHeadOutput(
        model_id="helsinki-settlement-v1",
        decision_ts_utc="2026-08-10T10:00:01Z",
        target_date=ladder.target_date,
        settlement_source=ladder.settlement_source,
        probability_stack=result,
        model_book_snapshot_id="model-book-1",
    )
    assert head.head_kind == ProbabilityHeadKind.FINAL_SETTLEMENT


def test_market_prior_is_required_instead_of_silent_uniform_fallback() -> None:
    ladder = _ladder()
    prior = ExactBracketDistribution(
        ladder_id=ladder.ladder_id,
        bracket_ids=ladder.bracket_ids,
        probabilities=(0.1, 0.3, 0.4, 0.2),
        source_kind="weather_only",
        source_snapshot_id="weather",
        observed_at_utc="2026-08-10T10:00:00Z",
    )
    zero = LadderLogAdjustment(
        ladder_id=ladder.ladder_id,
        bracket_ids=ladder.bracket_ids,
        log_weights=(0.0, 0.0, 0.0, 0.0),
        component_id="zero",
        observed_at_utc="2026-08-10T10:00:01Z",
    )
    with pytest.raises(ValueError, match="explicit market_prior"):
        compose_exact_bracket_probability(
            ladder,
            market_prior=prior,
            weather_path_residual=zero,
            source_basis_correction=zero,
        )


def test_repricing_and_execution_heads_are_distinct_contracts() -> None:
    ladder = _ladder()
    repricing = RepricingHeadOutput(
        model_id="repricing-v1",
        decision_ts_utc="2026-08-10T10:00:01Z",
        horizon_sec=30,
        ladder_id=ladder.ladder_id,
        bracket_ids=ladder.bracket_ids,
        probability_transport=(-0.03, -0.01, 0.02, 0.02),
        model_book_snapshot_id="model-book-1",
    )
    execution = ExecutionFillHeadOutput(
        model_id="fill-v1",
        decision_ts_utc="2026-08-10T10:00:01Z",
        expression_id="condition-23:YES",
        side="BUY",
        fill_window_sec=30,
        fill_probability=0.65,
        expected_fill_price=0.42,
        execution_book_snapshot_id="execution-book-2",
    )

    assert repricing.head_kind == ProbabilityHeadKind.REPRICING
    assert execution.head_kind == ProbabilityHeadKind.EXECUTION_FILL
    assert execution.schema_version == EXECUTION_HEAD_SCHEMA_VERSION
    assert repricing.model_book_snapshot_id != execution.execution_book_snapshot_id
    with pytest.raises(ValueError, match="15/30/60"):
        RepricingHeadOutput(
            model_id="repricing-v1",
            decision_ts_utc="2026-08-10T10:00:01Z",
            horizon_sec=10,
            ladder_id=ladder.ladder_id,
            bracket_ids=ladder.bracket_ids,
            probability_transport=(-0.03, -0.01, 0.02, 0.02),
            model_book_snapshot_id="model-book-1",
        )
