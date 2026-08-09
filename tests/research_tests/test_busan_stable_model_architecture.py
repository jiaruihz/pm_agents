from __future__ import annotations

from pathlib import Path
import sys

import pytest
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.strategies.weather_city_probability_shadow.busan_model import (
    FEATURES,
    HYPOTHESES,
    LEGACY_EXPERIMENTS,
    MODEL_SPEC,
    STABLE_MODEL_ID,
    BusanTransitionTrainingExample,
    compose_exact_no_probability,
)
from weather_model_evaluation import (
    AdmissionEvidence,
    ExperimentRun,
    StableModelSpec,
    decide_hypothesis_admission,
)


def test_model_identity_is_stable_and_legacy_versions_are_experiments() -> None:
    assert STABLE_MODEL_ID == "busan_intraday_exact_no"
    assert MODEL_SPEC.spec_id
    assert MODEL_SPEC.feature_set_id
    assert set(LEGACY_EXPERIMENTS["legacy_aliases"]) == {
        "busan_continuous_amos_residual_v9",
        "busan_confirmation_aware_residual_v10",
        "diagnostic_v11_giveback_interaction",
    }
    assert all(
        row["status"] != "accepted"
        for row in LEGACY_EXPERIMENTS["legacy_aliases"].values()
    )


def test_numeric_version_suffix_is_rejected_for_stable_model_identity() -> None:
    with pytest.raises(ValueError, match="numeric version"):
        StableModelSpec(
            model_id="busan_patch_v100",
            city="Busan",
            target_id="x",
            target_kind="settlement_outcome",
            state_schema_version="state_v1",
            stages=("a", "b"),
            composition="x",
            features=(),
            evaluation_grains=("checkpoint",),
        )


def test_sequential_composition_switches_branch_on_routine_observation() -> None:
    pending = compose_exact_no_probability(
        p_next_routine_confirms=0.6,
        p_late_break_given_nonconfirmation=0.25,
        transition_state="pending",
    )
    nonconfirmed = compose_exact_no_probability(
        p_next_routine_confirms=0.6,
        p_late_break_given_nonconfirmation=0.25,
        transition_state="nonconfirmed",
    )
    confirmed = compose_exact_no_probability(
        p_next_routine_confirms=0.6,
        p_late_break_given_nonconfirmation=0.25,
        transition_state="confirmed_break",
    )

    assert pending.p_no == pytest.approx(0.7)
    assert nonconfirmed.p_no == pytest.approx(0.25)
    assert confirmed.p_no == pytest.approx(1.0)
    assert pending.p_no > nonconfirmed.p_no


def test_cases_do_not_create_features_or_cross_mechanism_experiments() -> None:
    feature_names = {feature.name for feature in FEATURES}
    assert all("2026" not in name and "case" not in name for name in feature_names)
    for hypothesis in HYPOTHESES:
        assert set(hypothesis.changed_features) <= feature_names
        hypothesis.validate_against(MODEL_SPEC)


def test_retraining_changes_run_not_model_identity() -> None:
    first = ExperimentRun(
        stable_model_id=STABLE_MODEL_ID,
        training_cutoff="2026-08-02",
        code_sha="abc",
        data_content_hash="data",
        hypothesis_ids=("basis_confirmation_head",),
    )
    second = ExperimentRun(
        stable_model_id=STABLE_MODEL_ID,
        training_cutoff="2026-08-10",
        code_sha="abc",
        data_content_hash="data2",
        hypothesis_ids=("basis_confirmation_head",),
        parent_run_id=first.run_id,
    )
    assert first.stable_model_id == second.stable_model_id
    assert first.run_id != second.run_id


def test_old_giveback_experiment_is_rejected_by_architecture_gate() -> None:
    evidence = AdmissionEvidence(
        fixed_rows=True,
        fixed_labels=True,
        fixed_quotes=True,
        development_dates=7,
        development_loss_delta=0.5583700187 - 0.5576494176,
        frozen_dates=1,
        frozen_loss_delta=0.2777342064 - 0.2803809924,
        market_delta_ci95=(-0.2211125277, 0.1399463793),
        case_regressions_passed=True,
    )
    decision = decide_hypothesis_admission(
        HYPOTHESES[0], MODEL_SPEC, evidence
    )
    assert decision.accepted is False
    assert set(decision.reasons) == {
        "insufficient_development_dates",
        "no_development_improvement",
        "insufficient_frozen_dates",
        "market_baseline_not_beaten",
    }


def test_transition_training_labels_follow_probability_factorization() -> None:
    confirmed = BusanTransitionTrainingExample(
        checkpoint_id="confirmed",
        target_date="2026-08-01",
        decision_ts_utc="2026-08-01T03:00:00Z",
        anchor_rung=38,
        transition_state="confirmed_break",
        label_next_routine_confirms=1,
        label_late_break_given_nonconfirmation=None,
        label_final_exact_anchor_no=1,
        pit_provenance="live_capture",
    )
    reheat = BusanTransitionTrainingExample(
        checkpoint_id="reheat",
        target_date="2026-07-20",
        decision_ts_utc="2026-07-20T03:00:00Z",
        anchor_rung=31,
        transition_state="nonconfirmed",
        label_next_routine_confirms=0,
        label_late_break_given_nonconfirmation=1,
        label_final_exact_anchor_no=1,
        pit_provenance="live_capture",
    )
    assert confirmed.label_late_break_given_nonconfirmation is None
    assert reheat.label_late_break_given_nonconfirmation == 1

    with pytest.raises(ValueError, match="must equal conditional late break"):
        BusanTransitionTrainingExample(
            checkpoint_id="bad",
            target_date="2026-08-04",
            decision_ts_utc="2026-08-04T03:15:00Z",
            anchor_rung=35,
            transition_state="nonconfirmed",
            label_next_routine_confirms=0,
            label_late_break_given_nonconfirmation=0,
            label_final_exact_anchor_no=1,
            pit_provenance="live_capture",
        )


def test_case_library_is_regression_evidence_not_feature_definition() -> None:
    cases = pd.read_csv(
        ROOT
        / "docs/analysis/2026-08/2026-08-04-busan-model-regression-cases.csv"
    )
    assert len(cases) == 3
    assert set(cases.allowed_use) == {"diagnostic_and_regression_only"}
    feature_names = {feature.name for feature in FEATURES}
    assert not feature_names.intersection(cases.case_id)
