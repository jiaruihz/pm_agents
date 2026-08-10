"""Settlement-native exact-bracket probability and head contracts.

This module keeps three predictions deliberately separate:

* the final settlement distribution over a complete native-unit ladder;
* short-horizon repricing transport over that same ladder; and
* execution/fill probability for one concrete expression and book snapshot.

The settlement stack is explicit: market prior, weather/path residual,
source-basis correction, then calibration. Every intermediate distribution is
immutable, carries its own identity, and must contain exactly one unit of mass.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import math
from typing import Any


PROBABILITY_STACK_SCHEMA_VERSION = "weather_exact_bracket_probability_stack_v1"
SETTLEMENT_HEAD_SCHEMA_VERSION = "weather_exact_bracket_settlement_head_v1"
REPRICING_HEAD_SCHEMA_VERSION = "weather_exact_bracket_repricing_head_v1"
EXECUTION_HEAD_SCHEMA_VERSION = "weather_exact_bracket_execution_head_v1"
PROBABILITY_TOLERANCE = 1e-9
SUPPORTED_REPRICING_HORIZONS_SEC = frozenset({15, 30, 60})


class ProbabilityHeadKind(StrEnum):
    FINAL_SETTLEMENT = "final_settlement"
    REPRICING = "repricing"
    EXECUTION_FILL = "execution_fill"


def _stable_id(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite_tuple(values: Sequence[float], *, field_name: str) -> tuple[float, ...]:
    parsed = tuple(float(value) for value in values)
    if not parsed or not all(math.isfinite(value) for value in parsed):
        raise ValueError(f"{field_name} must be a non-empty finite vector")
    return parsed


@dataclass(frozen=True)
class SettlementBracket:
    """One inclusive interval on the settlement source's native lattice."""

    bracket_id: str
    label: str
    lower_native: float | None
    upper_native: float | None

    def __post_init__(self) -> None:
        if not self.bracket_id.strip() or not self.label.strip():
            raise ValueError("settlement bracket identity and label are required")
        for value in (self.lower_native, self.upper_native):
            if value is not None and not math.isfinite(float(value)):
                raise ValueError("settlement bracket bounds must be finite")
        if (
            self.lower_native is not None
            and self.upper_native is not None
            and float(self.lower_native) > float(self.upper_native)
        ):
            raise ValueError("settlement bracket lower bound exceeds upper bound")


@dataclass(frozen=True)
class SettlementLadder:
    """A complete, ordered partition of a discrete settlement-native lattice."""

    city: str
    target_date: str
    settlement_source: str
    native_unit: str
    native_step: float
    brackets: tuple[SettlementBracket, ...]

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.city,
                self.target_date,
                self.settlement_source,
                self.native_unit,
            )
        ):
            raise ValueError("ladder city/date/source/native unit are required")
        if not math.isfinite(self.native_step) or self.native_step <= 0:
            raise ValueError("native_step must be finite and positive")
        if len(self.brackets) < 2:
            raise ValueError("a complete settlement ladder needs at least two brackets")
        ids = [bracket.bracket_id for bracket in self.brackets]
        if len(set(ids)) != len(ids):
            raise ValueError("settlement bracket IDs must be unique")
        if self.brackets[0].lower_native is not None:
            raise ValueError("the first settlement bracket must have an open lower tail")
        if self.brackets[-1].upper_native is not None:
            raise ValueError("the final settlement bracket must have an open upper tail")
        for index, bracket in enumerate(self.brackets):
            if index > 0 and bracket.lower_native is None:
                raise ValueError("only the first settlement bracket may have an open lower tail")
            if index < len(self.brackets) - 1 and bracket.upper_native is None:
                raise ValueError("only the final settlement bracket may have an open upper tail")
        for previous, current in zip(self.brackets, self.brackets[1:]):
            expected = float(previous.upper_native) + self.native_step
            if not math.isclose(
                float(current.lower_native),
                expected,
                rel_tol=0.0,
                abs_tol=PROBABILITY_TOLERANCE,
            ):
                raise ValueError(
                    "settlement ladder has a gap or overlap between "
                    f"{previous.bracket_id} and {current.bracket_id}"
                )

    @property
    def bracket_ids(self) -> tuple[str, ...]:
        return tuple(bracket.bracket_id for bracket in self.brackets)

    @property
    def ladder_id(self) -> str:
        return _stable_id(
            {
                "city": self.city,
                "target_date": self.target_date,
                "settlement_source": self.settlement_source,
                "native_unit": self.native_unit,
                "native_step": self.native_step,
                "brackets": [
                    {
                        "bracket_id": bracket.bracket_id,
                        "label": bracket.label,
                        "lower_native": bracket.lower_native,
                        "upper_native": bracket.upper_native,
                    }
                    for bracket in self.brackets
                ],
            }
        )


@dataclass(frozen=True)
class ExactBracketDistribution:
    ladder_id: str
    bracket_ids: tuple[str, ...]
    probabilities: tuple[float, ...]
    source_kind: str
    source_snapshot_id: str
    observed_at_utc: str

    def __post_init__(self) -> None:
        if not self.ladder_id or not self.source_kind or not self.source_snapshot_id:
            raise ValueError("distribution ladder/source identities are required")
        if not self.observed_at_utc:
            raise ValueError("distribution observed_at_utc is required")
        if len(set(self.bracket_ids)) != len(self.bracket_ids):
            raise ValueError("distribution bracket IDs must be unique")
        values = _finite_tuple(self.probabilities, field_name="probabilities")
        object.__setattr__(self, "probabilities", values)
        if len(values) != len(self.bracket_ids):
            raise ValueError("distribution bracket and probability lengths differ")
        if any(value < 0.0 or value > 1.0 for value in values):
            raise ValueError("probabilities must lie in [0, 1]")
        if not math.isclose(
            sum(values), 1.0, rel_tol=0.0, abs_tol=PROBABILITY_TOLERANCE
        ):
            raise ValueError("exact-bracket probabilities must sum to one")

    @classmethod
    def from_mapping(
        cls,
        ladder: SettlementLadder,
        probabilities: Mapping[str, float],
        *,
        source_kind: str,
        source_snapshot_id: str,
        observed_at_utc: str,
    ) -> "ExactBracketDistribution":
        if set(probabilities) != set(ladder.bracket_ids):
            missing = sorted(set(ladder.bracket_ids) - set(probabilities))
            extra = sorted(set(probabilities) - set(ladder.bracket_ids))
            raise ValueError(f"distribution does not match ladder: missing={missing} extra={extra}")
        return cls(
            ladder_id=ladder.ladder_id,
            bracket_ids=ladder.bracket_ids,
            probabilities=tuple(float(probabilities[key]) for key in ladder.bracket_ids),
            source_kind=source_kind,
            source_snapshot_id=source_snapshot_id,
            observed_at_utc=observed_at_utc,
        )

    @property
    def distribution_id(self) -> str:
        return _stable_id(
            {
                "ladder_id": self.ladder_id,
                "bracket_ids": self.bracket_ids,
                "probabilities": self.probabilities,
                "source_kind": self.source_kind,
                "source_snapshot_id": self.source_snapshot_id,
                "observed_at_utc": self.observed_at_utc,
            }
        )

    def as_mapping(self) -> dict[str, float]:
        return dict(zip(self.bracket_ids, self.probabilities, strict=True))


@dataclass(frozen=True)
class LadderLogAdjustment:
    """Per-rung additive log-weight for one named causal component."""

    ladder_id: str
    bracket_ids: tuple[str, ...]
    log_weights: tuple[float, ...]
    component_id: str
    observed_at_utc: str
    source_snapshot_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.ladder_id or not self.component_id or not self.observed_at_utc:
            raise ValueError(
                "adjustment ladder_id, component_id and observed_at_utc are required"
            )
        values = _finite_tuple(self.log_weights, field_name="log_weights")
        object.__setattr__(self, "log_weights", values)
        if len(values) != len(self.bracket_ids):
            raise ValueError("adjustment bracket and weight lengths differ")

    @property
    def adjustment_id(self) -> str:
        return _stable_id(
            {
                "ladder_id": self.ladder_id,
                "bracket_ids": self.bracket_ids,
                "log_weights": self.log_weights,
                "component_id": self.component_id,
                "observed_at_utc": self.observed_at_utc,
                "source_snapshot_ids": self.source_snapshot_ids,
            }
        )


CalibrationTransform = Callable[
    [ExactBracketDistribution], ExactBracketDistribution
]


def _validate_same_ladder(
    ladder: SettlementLadder,
    distribution: ExactBracketDistribution,
) -> None:
    if distribution.ladder_id != ladder.ladder_id:
        raise ValueError("probability distribution ladder identity mismatch")
    if distribution.bracket_ids != ladder.bracket_ids:
        raise ValueError("probability distribution bracket order mismatch")


def _apply_log_adjustment(
    distribution: ExactBracketDistribution,
    adjustment: LadderLogAdjustment,
    *,
    source_kind: str,
) -> ExactBracketDistribution:
    if adjustment.ladder_id != distribution.ladder_id:
        raise ValueError("probability adjustment ladder identity mismatch")
    if adjustment.bracket_ids != distribution.bracket_ids:
        raise ValueError("probability adjustment bracket order mismatch")
    log_values = [
        (-math.inf if probability == 0.0 else math.log(probability)) + weight
        for probability, weight in zip(
            distribution.probabilities, adjustment.log_weights, strict=True
        )
    ]
    maximum = max(log_values)
    if not math.isfinite(maximum):
        raise ValueError("probability adjustment has no supported prior mass")
    raw = [math.exp(value - maximum) for value in log_values]
    total = sum(raw)
    values = tuple(value / total for value in raw)
    return ExactBracketDistribution(
        ladder_id=distribution.ladder_id,
        bracket_ids=distribution.bracket_ids,
        probabilities=values,
        source_kind=source_kind,
        source_snapshot_id=adjustment.component_id,
        observed_at_utc=adjustment.observed_at_utc,
    )


@dataclass(frozen=True)
class ExactBracketProbabilityStackResult:
    schema_version: str
    ladder_id: str
    city: str
    target_date: str
    settlement_source: str
    native_unit: str
    market_prior: ExactBracketDistribution
    after_weather_path: ExactBracketDistribution
    after_source_basis: ExactBracketDistribution
    final_distribution: ExactBracketDistribution
    weather_path_residual: LadderLogAdjustment
    source_basis_correction: LadderLogAdjustment
    calibration_id: str

    def __post_init__(self) -> None:
        if self.schema_version != PROBABILITY_STACK_SCHEMA_VERSION:
            raise ValueError("unsupported probability stack schema_version")
        if not all(
            value
            for value in (
                self.ladder_id,
                self.city,
                self.target_date,
                self.settlement_source,
                self.native_unit,
                self.calibration_id,
            )
        ):
            raise ValueError("probability stack identity is incomplete")
        distributions = (
            self.market_prior,
            self.after_weather_path,
            self.after_source_basis,
            self.final_distribution,
        )
        if any(value.ladder_id != self.ladder_id for value in distributions):
            raise ValueError("probability stack mixes ladder identities")
        bracket_orders = {value.bracket_ids for value in distributions}
        if len(bracket_orders) != 1:
            raise ValueError("probability stack mixes bracket orders")
        if self.weather_path_residual.ladder_id != self.ladder_id:
            raise ValueError("weather/path residual ladder identity mismatch")
        if self.source_basis_correction.ladder_id != self.ladder_id:
            raise ValueError("source-basis correction ladder identity mismatch")

    @property
    def weather_path_component_id(self) -> str:
        return self.weather_path_residual.component_id

    @property
    def source_basis_component_id(self) -> str:
        return self.source_basis_correction.component_id

    @property
    def stack_snapshot_id(self) -> str:
        return _stable_id(
            {
                "schema_version": self.schema_version,
                "ladder_id": self.ladder_id,
                "market_prior": self.market_prior.distribution_id,
                "after_weather_path": self.after_weather_path.distribution_id,
                "after_source_basis": self.after_source_basis.distribution_id,
                "final_distribution": self.final_distribution.distribution_id,
                "weather_path_adjustment": self.weather_path_residual.adjustment_id,
                "source_basis_adjustment": self.source_basis_correction.adjustment_id,
                "calibration_id": self.calibration_id,
            }
        )


def compose_exact_bracket_probability(
    ladder: SettlementLadder,
    *,
    market_prior: ExactBracketDistribution,
    weather_path_residual: LadderLogAdjustment,
    source_basis_correction: LadderLogAdjustment,
    calibration_id: str = "identity",
    calibration: CalibrationTransform | None = None,
) -> ExactBracketProbabilityStackResult:
    """Compose the registered settlement stack without mixing other heads."""

    _validate_same_ladder(ladder, market_prior)
    if market_prior.source_kind != "market_prior":
        raise ValueError("the settlement stack requires an explicit market_prior")
    after_weather = _apply_log_adjustment(
        market_prior,
        weather_path_residual,
        source_kind="market_plus_weather_path",
    )
    after_source = _apply_log_adjustment(
        after_weather,
        source_basis_correction,
        source_kind="market_plus_weather_path_plus_source_basis",
    )
    if calibration is None:
        if calibration_id != "identity":
            raise ValueError("a non-identity calibration_id requires a transform")
        final = after_source
    else:
        if not calibration_id or calibration_id == "identity":
            raise ValueError("a calibration transform requires a non-identity calibration_id")
        final = calibration(after_source)
        _validate_same_ladder(ladder, final)
        if final.source_kind != "calibrated_final":
            raise ValueError("calibration output source_kind must be calibrated_final")
        if final.observed_at_utc != after_source.observed_at_utc:
            raise ValueError("calibration must not change the observation clock")
    return ExactBracketProbabilityStackResult(
        schema_version=PROBABILITY_STACK_SCHEMA_VERSION,
        ladder_id=ladder.ladder_id,
        city=ladder.city,
        target_date=ladder.target_date,
        settlement_source=ladder.settlement_source,
        native_unit=ladder.native_unit,
        market_prior=market_prior,
        after_weather_path=after_weather,
        after_source_basis=after_source,
        final_distribution=final,
        weather_path_residual=weather_path_residual,
        source_basis_correction=source_basis_correction,
        calibration_id=calibration_id,
    )


@dataclass(frozen=True)
class FinalSettlementHeadOutput:
    model_id: str
    decision_ts_utc: str
    target_date: str
    settlement_source: str
    probability_stack: ExactBracketProbabilityStackResult
    model_book_snapshot_id: str
    schema_version: str = field(default=SETTLEMENT_HEAD_SCHEMA_VERSION, init=False)
    head_kind: ProbabilityHeadKind = field(
        default=ProbabilityHeadKind.FINAL_SETTLEMENT, init=False
    )

    def __post_init__(self) -> None:
        if not all(
            value
            for value in (
                self.model_id,
                self.decision_ts_utc,
                self.target_date,
                self.settlement_source,
                self.model_book_snapshot_id,
            )
        ):
            raise ValueError("final settlement head lineage is incomplete")
        if self.target_date != self.probability_stack.target_date:
            raise ValueError("settlement head target_date differs from probability stack")
        if self.settlement_source != self.probability_stack.settlement_source:
            raise ValueError(
                "settlement head source differs from probability stack"
            )


@dataclass(frozen=True)
class RepricingHeadOutput:
    model_id: str
    decision_ts_utc: str
    horizon_sec: int
    ladder_id: str
    bracket_ids: tuple[str, ...]
    probability_transport: tuple[float, ...]
    model_book_snapshot_id: str
    schema_version: str = field(default=REPRICING_HEAD_SCHEMA_VERSION, init=False)
    head_kind: ProbabilityHeadKind = field(default=ProbabilityHeadKind.REPRICING, init=False)

    def __post_init__(self) -> None:
        if self.horizon_sec not in SUPPORTED_REPRICING_HORIZONS_SEC:
            raise ValueError("repricing horizon must be one of 15/30/60 seconds")
        values = _finite_tuple(
            self.probability_transport, field_name="probability_transport"
        )
        object.__setattr__(self, "probability_transport", values)
        if len(values) != len(self.bracket_ids):
            raise ValueError("repricing transport does not match bracket count")
        if not math.isclose(
            sum(values), 0.0, rel_tol=0.0, abs_tol=PROBABILITY_TOLERANCE
        ):
            raise ValueError("repricing transport must conserve full-ladder mass")
        if not all(
            value
            for value in (
                self.model_id,
                self.decision_ts_utc,
                self.ladder_id,
                self.model_book_snapshot_id,
            )
        ):
            raise ValueError("repricing head lineage is incomplete")


@dataclass(frozen=True)
class ExecutionFillHeadOutput:
    model_id: str
    decision_ts_utc: str
    expression_id: str
    side: str
    fill_window_sec: int
    fill_probability: float
    expected_fill_price: float
    execution_book_snapshot_id: str
    schema_version: str = field(default=EXECUTION_HEAD_SCHEMA_VERSION, init=False)
    head_kind: ProbabilityHeadKind = field(
        default=ProbabilityHeadKind.EXECUTION_FILL, init=False
    )

    def __post_init__(self) -> None:
        if not all(
            value
            for value in (
                self.model_id,
                self.decision_ts_utc,
                self.expression_id,
                self.side,
                self.execution_book_snapshot_id,
            )
        ):
            raise ValueError("execution/fill head lineage is incomplete")
        if self.fill_window_sec <= 0:
            raise ValueError("fill_window_sec must be positive")
        if not math.isfinite(self.fill_probability) or not 0.0 <= self.fill_probability <= 1.0:
            raise ValueError("fill_probability must lie in [0, 1]")
        if (
            not math.isfinite(self.expected_fill_price)
            or not 0.0 <= self.expected_fill_price <= 1.0
        ):
            raise ValueError("expected_fill_price must lie in [0, 1]")
