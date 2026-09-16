"""Stable model identity and anti-patch research governance.

Model identity describes a target and a physical factorization.  Retraining,
feature ablations and case-inspired challengers are experiment runs under that
identity; they do not create a new model version by themselves.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Iterable, Mapping

from .contracts import stable_sha256


_VERSION_SUFFIX = re.compile(r"(?:^|_)[vV]\d+$")
_MONOTONIC_EFFECTS = {"increasing", "decreasing", "unconstrained"}
_FEATURE_CLOCKS = {"decision_current", "pre_event", "first_post_event"}


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    mechanism: str
    stage: str
    available_clock: str
    monotonic_effect: str = "unconstrained"
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.mechanism or not self.stage:
            raise ValueError("feature name, mechanism and stage are required")
        if self.available_clock not in _FEATURE_CLOCKS:
            raise ValueError(f"unsupported feature clock: {self.available_clock}")
        if self.monotonic_effect not in _MONOTONIC_EFFECTS:
            raise ValueError(
                f"unsupported monotonic effect: {self.monotonic_effect}"
            )


@dataclass(frozen=True)
class StableModelSpec:
    model_id: str
    city: str
    target_id: str
    target_kind: str
    state_schema_version: str
    stages: tuple[str, ...]
    composition: str
    features: tuple[FeatureDefinition, ...]
    evaluation_grains: tuple[str, ...]
    market_feature_role: str = "none"
    market_feature_clock: str = "none"

    def __post_init__(self) -> None:
        if not all(
            (
                self.model_id,
                self.city,
                self.target_id,
                self.target_kind,
                self.state_schema_version,
                self.composition,
            )
        ):
            raise ValueError("stable model identity fields must not be empty")
        if _VERSION_SUFFIX.search(self.model_id):
            raise ValueError(
                "stable model_id cannot end in a numeric version; use artifact/run identity"
            )
        if len(self.stages) < 2:
            raise ValueError("structured model requires at least two stages")
        if len(self.stages) != len(set(self.stages)):
            raise ValueError("model stages must be unique")
        names = [feature.name for feature in self.features]
        if len(names) != len(set(names)):
            raise ValueError("feature names must be unique")
        unknown = {feature.stage for feature in self.features} - set(self.stages)
        if unknown:
            raise ValueError(f"features reference unknown stages: {sorted(unknown)}")
        if not self.evaluation_grains:
            raise ValueError("at least one evaluation grain is required")
        if set(self.evaluation_grains) - {"checkpoint", "transition", "state_entry"}:
            raise ValueError("unsupported evaluation grain")

    @property
    def spec_id(self) -> str:
        return stable_sha256(asdict(self))

    @property
    def feature_set_id(self) -> str:
        return stable_sha256([asdict(feature) for feature in self.features])


@dataclass(frozen=True)
class MechanismHypothesis:
    hypothesis_id: str
    mechanism: str
    statement: str
    changed_features: tuple[str, ...]
    preregistered: bool
    inspired_by_cases: tuple[str, ...] = ()

    def validate_against(self, spec: StableModelSpec) -> None:
        if not self.hypothesis_id or not self.mechanism or not self.statement:
            raise ValueError("hypothesis identity, mechanism and statement are required")
        if not self.changed_features:
            raise ValueError("hypothesis must change at least one registered feature")
        known = {feature.name for feature in spec.features}
        unknown = set(self.changed_features) - known
        if unknown:
            raise ValueError(
                f"hypothesis introduces features outside stable ontology: {sorted(unknown)}"
            )
        if len({
            feature.mechanism
            for feature in spec.features
            if feature.name in self.changed_features
        }) > 1:
            raise ValueError("one experiment may change only one physical mechanism")


@dataclass(frozen=True)
class ExperimentRun:
    stable_model_id: str
    training_cutoff: str
    code_sha: str
    data_content_hash: str
    hypothesis_ids: tuple[str, ...]
    parent_run_id: str | None = None

    @property
    def run_id(self) -> str:
        return stable_sha256(asdict(self))


@dataclass(frozen=True)
class AdmissionEvidence:
    fixed_rows: bool
    fixed_labels: bool
    fixed_quotes: bool
    development_dates: int
    development_loss_delta: float
    frozen_dates: int
    frozen_loss_delta: float | None
    market_delta_ci95: tuple[float, float] | None
    case_regressions_passed: bool


@dataclass(frozen=True)
class AdmissionDecision:
    accepted: bool
    reasons: tuple[str, ...]


def decide_hypothesis_admission(
    hypothesis: MechanismHypothesis,
    spec: StableModelSpec,
    evidence: AdmissionEvidence,
    *,
    minimum_development_dates: int = 20,
    minimum_frozen_dates: int = 10,
) -> AdmissionDecision:
    """Conservative admission into a frozen artifact, never into live."""

    if minimum_development_dates <= 0 or minimum_frozen_dates <= 0:
        raise ValueError("admission date thresholds must be positive")
    hypothesis.validate_against(spec)
    reasons: list[str] = []
    if not hypothesis.preregistered:
        reasons.append("hypothesis_not_preregistered")
    if not evidence.fixed_rows:
        reasons.append("denominator_changed")
    if not evidence.fixed_labels:
        reasons.append("labels_changed")
    if not evidence.fixed_quotes:
        reasons.append("quotes_changed")
    if evidence.development_dates < minimum_development_dates:
        reasons.append("insufficient_development_dates")
    if evidence.development_loss_delta >= 0:
        reasons.append("no_development_improvement")
    if evidence.frozen_dates < minimum_frozen_dates:
        reasons.append("insufficient_frozen_dates")
    if evidence.frozen_loss_delta is None or evidence.frozen_loss_delta >= 0:
        reasons.append("frozen_forward_not_improved")
    if evidence.market_delta_ci95 is None or evidence.market_delta_ci95[1] >= 0:
        reasons.append("market_baseline_not_beaten")
    if not evidence.case_regressions_passed:
        reasons.append("case_regression_failed")
    return AdmissionDecision(accepted=not reasons, reasons=tuple(reasons))


def validate_hypothesis_registry(
    spec: StableModelSpec, hypotheses: Iterable[MechanismHypothesis]
) -> None:
    seen: set[str] = set()
    for hypothesis in hypotheses:
        if hypothesis.hypothesis_id in seen:
            raise ValueError(f"duplicate hypothesis_id: {hypothesis.hypothesis_id}")
        seen.add(hypothesis.hypothesis_id)
        hypothesis.validate_against(spec)


def experiment_alias_manifest(
    stable_model_id: str, aliases: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """Map legacy v-number names to immutable experiments without blessing them."""

    return {
        "stable_model_id": stable_model_id,
        "legacy_aliases": dict(aliases),
        "identity_rule": (
            "model_id stays stable; algorithm/data/cutoff changes create run_id and artifact_id"
        ),
    }


__all__ = [
    "AdmissionDecision",
    "AdmissionEvidence",
    "ExperimentRun",
    "FeatureDefinition",
    "MechanismHypothesis",
    "StableModelSpec",
    "decide_hypothesis_admission",
    "experiment_alias_manifest",
    "validate_hypothesis_registry",
]
