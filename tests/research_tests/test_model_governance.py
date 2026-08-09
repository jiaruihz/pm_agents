from __future__ import annotations

import pytest

from weather_model_evaluation import (
    AdmissionEvidence,
    FeatureDefinition,
    MechanismHypothesis,
    OrderedThresholdClassifier,
    StableModelSpec,
    decide_hypothesis_admission,
)


def _spec() -> StableModelSpec:
    return StableModelSpec(
        model_id="busan_intraday_probability",
        city="Busan",
        target_id="final_exact_tmax",
        target_kind="ordinal",
        state_schema_version="weather_context_v1",
        stages=("path", "terminal"),
        composition="path_then_terminal",
        features=(
            FeatureDefinition(
                name="remaining_heat",
                mechanism="heating_window",
                stage="path",
                available_clock="decision_current",
            ),
        ),
        evaluation_grains=("checkpoint", "transition"),
    )


def _hypothesis() -> MechanismHypothesis:
    return MechanismHypothesis(
        hypothesis_id="heating-window-1",
        mechanism="heating_window",
        statement="Remaining heat improves the terminal distribution.",
        changed_features=("remaining_heat",),
        preregistered=True,
    )


def test_stable_model_identity_rejects_version_suffix_and_duplicate_stages() -> None:
    values = _spec().__dict__
    with pytest.raises(ValueError, match="numeric version"):
        StableModelSpec(**{**values, "model_id": "busan_intraday_probability_v2"})
    with pytest.raises(ValueError, match="stages must be unique"):
        StableModelSpec(**{**values, "stages": ("path", "path")})


def test_frozen_zero_delta_is_not_an_improvement() -> None:
    evidence = AdmissionEvidence(
        fixed_rows=True,
        fixed_labels=True,
        fixed_quotes=True,
        development_dates=20,
        development_loss_delta=-0.01,
        frozen_dates=10,
        frozen_loss_delta=0.0,
        market_delta_ci95=(-0.02, -0.001),
        case_regressions_passed=True,
    )
    decision = decide_hypothesis_admission(_hypothesis(), _spec(), evidence)
    assert decision.accepted is False
    assert decision.reasons == ("frozen_forward_not_improved",)


def test_top_level_package_exports_shared_model_primitives() -> None:
    assert OrderedThresholdClassifier.__name__ == "OrderedThresholdClassifier"
