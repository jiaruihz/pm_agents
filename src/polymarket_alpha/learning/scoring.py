"""Pure scoring and calibration for immutable Alpha P1 learning facts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, localcontext
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts import (
    CalibrationDimension, CalibrationReport, CalibrationSlice, MarketResolution,
    PredictionResolutionLink, PredictionScore, ResolutionAdjudicationStatus,
    ResolutionOutcome, ScoreReference, ScoringEligibility, content_sha256, stable_record_id,
)
from ..contracts.base import ensure_utc


LEARNING_SCORING_VERSION = "p1_learning_scoring_v1"
_ZERO, _ONE = Decimal("0"), Decimal("1")


class LearningScoringError(ValueError):
    """Raised when an append-only P1 score cannot be safely produced."""


class ScoringPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    probability_epsilon: Decimal = Field(default=Decimal("0.000001"), gt=0, lt=Decimal("0.5"))

    @model_validator(mode="after")
    def version_is_present(self) -> "ScoringPolicy":
        if not self.version.strip():
            raise ValueError("scoring policy version must not be blank")
        return self

    @property
    def policy_sha256(self) -> str:
        return content_sha256(
            {
                "scoring_policy_version": self.version,
                "log_loss_epsilon": self.probability_epsilon,
            }
        )


class CalibrationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    rule_clarity_boundaries: tuple[Decimal, ...] = (Decimal("0"), Decimal("0.5"), Decimal("0.8"), Decimal("1"))
    entry_price_boundaries: tuple[Decimal, ...] = (Decimal("0"), Decimal("0.25"), Decimal("0.5"), Decimal("0.75"), Decimal("1"))

    @model_validator(mode="after")
    def boundaries_are_closed_unit_intervals(self) -> "CalibrationPolicy":
        if not self.version.strip():
            raise ValueError("calibration policy version must not be blank")
        for values in (self.rule_clarity_boundaries, self.entry_price_boundaries):
            if len(values) < 2 or values[0] != _ZERO or values[-1] != _ONE or tuple(sorted(values)) != values or len(set(values)) != len(values):
                raise ValueError("calibration boundaries must be unique sorted [0, 1] endpoints")
        return self

    @property
    def policy_sha256(self) -> str:
        return content_sha256(
            {
                "calibration_policy_version": self.version,
                "rule_clarity_boundaries": self.rule_clarity_boundaries,
                "entry_price_boundaries": self.entry_price_boundaries,
            }
        )


def _frozen(value, cls):
    if not isinstance(value, cls):
        raise LearningScoringError(f"expected {cls.__name__}")
    try:
        rebuilt = cls.model_validate(value.model_dump(mode="python"))
    except ValueError as exc:
        raise LearningScoringError(f"invalid frozen {cls.__name__}: {exc}") from exc
    if rebuilt.canonical_sha256 != value.canonical_sha256:
        raise LearningScoringError(f"{cls.__name__} canonical replay mismatch")
    return rebuilt


def _clip(value: Decimal, epsilon: Decimal) -> Decimal:
    return min(max(value, epsilon), _ONE - epsilon)


def _log_loss(probability: Decimal, label: int, epsilon: Decimal) -> Decimal:
    value = _clip(probability, epsilon)
    with localcontext() as context:
        context.prec = 50
        return -(value.ln() if label == 1 else (_ONE - value).ln())


def score_prediction(
    *, link: PredictionResolutionLink, resolution: MarketResolution, policy: ScoringPolicy,
    scored_at: datetime, run_id: str,
) -> PredictionScore:
    """Score only a FINAL, eligible binary settlement; never mutate its inputs."""

    link, resolution = _frozen(link, PredictionResolutionLink), _frozen(resolution, MarketResolution)
    if not isinstance(policy, ScoringPolicy):
        raise LearningScoringError("expected ScoringPolicy")
    policy = ScoringPolicy.model_validate(policy.model_dump(mode="python"))
    scored_at = ensure_utc(scored_at)
    if link.scoring_eligibility != ScoringEligibility.ELIGIBLE:
        raise LearningScoringError("excluded link cannot receive a score")
    if resolution.adjudication_status != ResolutionAdjudicationStatus.FINAL or resolution.outcome not in {ResolutionOutcome.YES, ResolutionOutcome.NO}:
        raise LearningScoringError("only final YES/NO resolutions can receive a score")
    if link.resolution_id != resolution.record_id or link.resolution_sha256 != resolution.canonical_sha256:
        raise LearningScoringError("link/resolution id or hash mismatch")
    if resolution.resolved_at > link.linked_at or link.linked_at > scored_at:
        raise LearningScoringError("resolution/link/score clocks are not ordered")
    label = 1 if resolution.outcome == ResolutionOutcome.YES else 0
    brier = (link.predicted_probability - Decimal(label)) ** 2
    log_loss = _log_loss(link.predicted_probability, label, policy.probability_epsilon)
    baseline_brier = baseline_log = None
    if link.market_baseline_probability is not None:
        baseline_brier = (link.market_baseline_probability - Decimal(label)) ** 2
        baseline_log = _log_loss(link.market_baseline_probability, label, policy.probability_epsilon)
    pnl = entry = None
    if link.entry_basis is not None:
        basis = link.entry_basis
        entry = basis.entry_vwap
        won = (basis.direction == resolution.outcome.value)
        pnl = (basis.quantity if won else _ZERO) - basis.gross_cost - basis.fee_amount
    score_id = stable_record_id(
        "prediction_score", link.record_id, link.canonical_sha256, resolution.record_id,
        resolution.canonical_sha256, policy.version, policy.policy_sha256, scored_at,
    )
    return PredictionScore(
        record_id=score_id, score_id=score_id, run_id=run_id, created_at=scored_at,
        source="alpha_p1_learning_scorer", source_version=LEARNING_SCORING_VERSION,
        provenance=(), extensions={}, link_id=link.record_id, link_sha256=link.canonical_sha256,
        prediction_id=link.prediction_id, resolution_id=resolution.record_id,
        outcome=resolution.outcome.value, outcome_label=label,
        predicted_probability=link.predicted_probability,
        market_baseline_probability=link.market_baseline_probability, brier_score=brier,
        log_loss=log_loss, market_baseline_brier_score=baseline_brier,
        market_baseline_log_loss=baseline_log, simulated_pnl=pnl, market_type=link.market_type,
        rule_clarity=link.rule_clarity, entry_vwap=entry, scoring_policy_version=policy.version,
        log_loss_epsilon=policy.probability_epsilon, scoring_policy_sha256=policy.policy_sha256,
    )


def _bucket(value: Decimal, boundaries: tuple[Decimal, ...]) -> str:
    for lower, upper in zip(boundaries, boundaries[1:]):
        if lower <= value <= upper:
            opener = "[" if lower == _ZERO else "("
            return f"{opener}{lower},{upper}]"
    raise LearningScoringError("value lies outside calibration policy boundaries")


def _mean(values: Iterable[Decimal]) -> Decimal | None:
    values = tuple(values)
    return sum(values, _ZERO) / Decimal(len(values)) if values else None


def build_calibration_report(
    *, scores: Iterable[PredictionScore], dimension: CalibrationDimension,
    policy: CalibrationPolicy, reported_at: datetime, run_id: str,
) -> CalibrationReport:
    """Build one deterministic, explicitly selected calibration view from score facts."""

    if not isinstance(policy, CalibrationPolicy):
        raise LearningScoringError("expected CalibrationPolicy")
    policy = CalibrationPolicy.model_validate(policy.model_dump(mode="python"))
    reported_at = ensure_utc(reported_at)
    items = tuple(sorted((_frozen(item, PredictionScore) for item in scores), key=lambda item: item.record_id))
    if not items:
        raise LearningScoringError("calibration requires at least one selected score")
    if len({item.prediction_id for item in items}) != len(items):
        raise LearningScoringError("calibration selection cannot contain duplicate predictions")
    if any(item.created_at > reported_at for item in items):
        raise LearningScoringError("score cannot be reported before it was created")
    included: dict[str, list[PredictionScore]] = {}
    excluded: list[str] = []
    for score in items:
        if dimension == CalibrationDimension.MARKET_TYPE:
            key = score.market_type
        elif dimension == CalibrationDimension.RULE_CLARITY:
            key = _bucket(score.rule_clarity, policy.rule_clarity_boundaries)
        elif dimension == CalibrationDimension.ENTRY_PRICE:
            if score.entry_vwap is None:
                excluded.append(score.record_id)
                continue
            key = _bucket(score.entry_vwap, policy.entry_price_boundaries)
        else:
            raise LearningScoringError("unsupported calibration dimension")
        included.setdefault(key, []).append(score)
    if not included:
        raise LearningScoringError("calibration dimension excludes every selected score")
    slices = []
    for key in sorted(included):
        group = tuple(included[key])
        slices.append(CalibrationSlice(
            slice_key=key, count=len(group),
            mean_predicted_probability=_mean(item.predicted_probability for item in group),
            observed_yes_rate=_mean(Decimal(item.outcome_label) for item in group),
            mean_brier_score=_mean(item.brier_score for item in group),
            mean_log_loss=_mean(item.log_loss for item in group),
            mean_market_baseline_brier_score=_mean(item.market_baseline_brier_score for item in group if item.market_baseline_brier_score is not None),
            mean_market_baseline_log_loss=_mean(item.market_baseline_log_loss for item in group if item.market_baseline_log_loss is not None),
            mean_simulated_pnl=_mean(item.simulated_pnl for item in group if item.simulated_pnl is not None),
        ))
    # References cover the whole caller-selected set, including explicitly excluded
    # no-entry scores, so the report remains replayable and cannot hide selection.
    references = tuple(
        ScoreReference(score_id=item.record_id, score_sha256=item.canonical_sha256)
        for item in items
    )
    report_id = stable_record_id("calibration_report", dimension.value, tuple((item.score_id, item.score_sha256) for item in references), tuple(sorted(excluded)), policy.version, policy.policy_sha256, reported_at)
    return CalibrationReport(
        record_id=report_id, report_id=report_id, run_id=run_id, created_at=reported_at,
        source="alpha_p1_calibration", source_version=LEARNING_SCORING_VERSION,
        provenance=(), extensions={}, dimension=dimension, score_references=references,
        slices=tuple(slices), excluded_score_ids=tuple(sorted(excluded)),
        calibration_policy_version=policy.version,
        rule_clarity_boundaries=policy.rule_clarity_boundaries,
        entry_price_boundaries=policy.entry_price_boundaries,
        calibration_policy_sha256=policy.policy_sha256,
    )
