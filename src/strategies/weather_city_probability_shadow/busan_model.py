"""Stable Busan exact-bracket model contract.

This module defines the probability factorization and feature ontology only.
It does not deploy an adapter, load an artifact, select a trade, or grant live
authority.  Busan remains coverage-only until a frozen artifact passes the
research admission contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from weather_model_evaluation import (
    FeatureDefinition,
    MechanismHypothesis,
    StableModelSpec,
    experiment_alias_manifest,
    validate_hypothesis_registry,
)


STABLE_MODEL_ID = "busan_intraday_exact_no"
TARGET_ID = "final_exact_current_routine_rung_no"
CONFIRMATION_STAGE = "next_routine_confirmation"
REHEAT_STAGE = "late_break_after_nonconfirmation"
TransitionState = Literal["pending", "confirmed_break", "nonconfirmed"]


FEATURES = (
    # Source -> routine/settlement basis.  These answer whether the already
    # observed fast-source cross will survive the next official report.
    FeatureDefinition(
        "source_cross_margin_c",
        "source_settlement_basis",
        CONFIRMATION_STAGE,
        "decision_current",
        "increasing",
        "Fast-source temperature above the current routine rung boundary.",
    ),
    FeatureDefinition(
        "source_peak_cross_margin_c",
        "source_settlement_basis",
        CONFIRMATION_STAGE,
        "decision_current",
        "increasing",
        "Largest PIT source cross margin in the current episode.",
    ),
    FeatureDefinition(
        "source_peak_giveback_c",
        "source_settlement_basis",
        CONFIRMATION_STAGE,
        "decision_current",
        "decreasing",
        "Continuous drawdown from the PIT source peak.",
    ),
    FeatureDefinition(
        "current_cross_retained",
        "source_settlement_basis",
        CONFIRMATION_STAGE,
        "decision_current",
        "increasing",
        (
            "Whether the current AMOS observation still clears the explicit "
            "routine lattice boundary; unlike peak-cross, this state can turn off."
        ),
    ),
    FeatureDefinition(
        "cross_persistence_minutes",
        "source_settlement_basis",
        CONFIRMATION_STAGE,
        "decision_current",
        "increasing",
        "Elapsed time with the source above the lattice boundary.",
    ),
    FeatureDefinition(
        "cross_observation_count",
        "source_settlement_basis",
        CONFIRMATION_STAGE,
        "decision_current",
        "increasing",
        "Independent PIT source observations supporting the cross.",
    ),
    FeatureDefinition(
        "runway_agreement_fraction",
        "source_settlement_basis",
        CONFIRMATION_STAGE,
        "decision_current",
        "increasing",
        "Fraction of AMOS runway observations supporting the cross.",
    ),
    FeatureDefinition(
        "minutes_to_next_routine",
        "source_settlement_basis",
        CONFIRMATION_STAGE,
        "decision_current",
        "unconstrained",
        "PIT time remaining until the next scheduled routine report.",
    ),
    # Conditional reheat head.  It is evaluated only if the next routine did
    # not confirm the source cross, so morning reheat and late fade are not
    # forced into one weak linear correction.
    FeatureDefinition(
        "remaining_heat_hours",
        "remaining_heat",
        REHEAT_STAGE,
        "decision_current",
        "increasing",
        "Positive hours remaining to the PIT forecast peak.",
    ),
    FeatureDefinition(
        "post_peak_hours",
        "remaining_heat",
        REHEAT_STAGE,
        "decision_current",
        "decreasing",
        "Hours elapsed after the PIT forecast peak.",
    ),
    FeatureDefinition(
        "forecast_ceiling_margin_c",
        "remaining_heat",
        REHEAT_STAGE,
        "decision_current",
        "increasing",
        "PIT forecast ceiling above the current routine boundary.",
    ),
    FeatureDefinition(
        "path_15m_slope_c_per_hour",
        "path_morphology",
        REHEAT_STAGE,
        "decision_current",
        "increasing",
        "Short-run thermal momentum.",
    ),
    FeatureDefinition(
        "path_60m_slope_c_per_hour",
        "path_morphology",
        REHEAT_STAGE,
        "decision_current",
        "increasing",
        "Hour-scale thermal momentum.",
    ),
    FeatureDefinition(
        "official_peak_age_minutes",
        "path_morphology",
        REHEAT_STAGE,
        "decision_current",
        "decreasing",
        "Age of the latest routine/official running maximum.",
    ),
    FeatureDefinition(
        "relative_humidity_pct",
        "atmospheric_heat_suppression",
        REHEAT_STAGE,
        "decision_current",
        "decreasing",
        "Humidity state available at the decision checkpoint.",
    ),
    FeatureDefinition(
        "dewpoint_depression_c",
        "atmospheric_heat_suppression",
        REHEAT_STAGE,
        "decision_current",
        "increasing",
        "Dryness proxy supporting additional sensible heating.",
    ),
    FeatureDefinition(
        "cloud_cover_remaining_3h_pct",
        "atmospheric_heat_suppression",
        REHEAT_STAGE,
        "decision_current",
        "decreasing",
        "PIT forecast cloud cover over the remaining three hours.",
    ),
    FeatureDefinition(
        "precip_probability_remaining_3h_pct",
        "atmospheric_heat_suppression",
        REHEAT_STAGE,
        "decision_current",
        "decreasing",
        "PIT precipitation probability over the remaining three hours.",
    ),
    FeatureDefinition(
        "wind_speed_remaining_3h_kt",
        "atmospheric_heat_suppression",
        REHEAT_STAGE,
        "decision_current",
        "unconstrained",
        "PIT forecast wind; sign is learned rather than assumed globally.",
    ),
)


MODEL_SPEC = StableModelSpec(
    model_id=STABLE_MODEL_ID,
    city="Busan",
    target_id=TARGET_ID,
    target_kind="settlement_outcome",
    state_schema_version="busan_intraday_physical_state_v1",
    stages=(CONFIRMATION_STAGE, REHEAT_STAGE),
    composition=(
        "p_no = p_next_routine_confirms + "
        "(1 - p_next_routine_confirms) * p_late_break_given_nonconfirmation"
    ),
    features=FEATURES,
    evaluation_grains=("checkpoint", "transition", "state_entry"),
    market_feature_role="none",
    market_feature_clock="none",
)


HYPOTHESES = (
    MechanismHypothesis(
        hypothesis_id="basis_confirmation_head",
        mechanism="source_settlement_basis",
        statement=(
            "Cross margin, persistence, runway agreement and peak giveback estimate "
            "whether the already observed AMOS cross survives the next routine report."
        ),
        changed_features=tuple(
            feature.name
            for feature in FEATURES
            if feature.mechanism == "source_settlement_basis"
        ),
        preregistered=True,
        inspired_by_cases=("busan_2026_08_01_terminal_false_cross",),
    ),
    MechanismHypothesis(
        hypothesis_id="conditional_remaining_heat_head",
        mechanism="remaining_heat",
        statement=(
            "After a routine nonconfirmation, remaining heating time and forecast "
            "ceiling determine whether a later official break is still possible."
        ),
        changed_features=tuple(
            feature.name
            for feature in FEATURES
            if feature.mechanism == "remaining_heat"
        ),
        preregistered=True,
        inspired_by_cases=(
            "busan_2026_07_20_late_reheat",
            "busan_2026_08_04_late_fade",
        ),
    ),
    MechanismHypothesis(
        hypothesis_id="conditional_path_morphology",
        mechanism="path_morphology",
        statement=(
            "Short and hour-scale momentum plus official peak age distinguish fresh "
            "runway from plateau, pullback and fade without case-specific thresholds."
        ),
        changed_features=tuple(
            feature.name
            for feature in FEATURES
            if feature.mechanism == "path_morphology"
        ),
        preregistered=True,
    ),
    MechanismHypothesis(
        hypothesis_id="conditional_atmospheric_suppression",
        mechanism="atmospheric_heat_suppression",
        statement=(
            "Humidity, dryness, cloud, precipitation and wind explain remaining heat "
            "rather than becoming independent entry filters."
        ),
        changed_features=tuple(
            feature.name
            for feature in FEATURES
            if feature.mechanism == "atmospheric_heat_suppression"
        ),
        preregistered=True,
    ),
)
validate_hypothesis_registry(MODEL_SPEC, HYPOTHESES)


LEGACY_EXPERIMENTS = experiment_alias_manifest(
    STABLE_MODEL_ID,
    {
        "busan_continuous_amos_residual_v9": {
            "experiment_id": "single_head_amos_context",
            "status": "historical_baseline",
        },
        "busan_confirmation_aware_residual_v10": {
            "experiment_id": "single_head_sequence_features",
            "status": "historical_challenger_not_accepted",
        },
        "diagnostic_v11_giveback_interaction": {
            "experiment_id": "single_head_giveback_interaction",
            "status": "historical_challenger_not_accepted",
        },
    },
)


@dataclass(frozen=True)
class SequentialProbability:
    p_no: float
    p_next_routine_confirms: float
    p_late_break_given_nonconfirmation: float
    transition_state: TransitionState


@dataclass(frozen=True)
class BusanTransitionTrainingExample:
    """Anchored cross-episode label contract for the two probability heads."""

    checkpoint_id: str
    target_date: str
    decision_ts_utc: str
    anchor_rung: int
    transition_state: TransitionState
    label_next_routine_confirms: int
    label_late_break_given_nonconfirmation: int | None
    label_final_exact_anchor_no: int
    pit_provenance: str

    def __post_init__(self) -> None:
        binary = {0, 1}
        if self.label_next_routine_confirms not in binary:
            raise ValueError("next-routine label must be binary")
        if self.label_final_exact_anchor_no not in binary:
            raise ValueError("final exact-NO label must be binary")
        if self.label_next_routine_confirms == 1:
            if self.label_late_break_given_nonconfirmation is not None:
                raise ValueError(
                    "conditional reheat label is undefined when next routine confirms"
                )
            if self.label_final_exact_anchor_no != 1:
                raise ValueError("confirmed higher routine must leave the anchor exact rung")
        else:
            if self.label_late_break_given_nonconfirmation not in binary:
                raise ValueError(
                    "nonconfirmation requires a binary conditional late-break label"
                )
            if (
                self.label_final_exact_anchor_no
                != self.label_late_break_given_nonconfirmation
            ):
                raise ValueError(
                    "after nonconfirmation final exact-NO must equal conditional late break"
                )


def compose_exact_no_probability(
    *,
    p_next_routine_confirms: float,
    p_late_break_given_nonconfirmation: float,
    transition_state: TransitionState = "pending",
) -> SequentialProbability:
    """Compose coherent exact-NO probability from the two physical heads."""

    p_confirm = float(p_next_routine_confirms)
    p_reheat = float(p_late_break_given_nonconfirmation)
    if not 0.0 <= p_confirm <= 1.0 or not 0.0 <= p_reheat <= 1.0:
        raise ValueError("head probabilities must be in [0, 1]")
    if transition_state == "confirmed_break":
        p_no = 1.0
    elif transition_state == "nonconfirmed":
        p_no = p_reheat
    elif transition_state == "pending":
        p_no = p_confirm + (1.0 - p_confirm) * p_reheat
    else:
        raise ValueError(f"unsupported transition state: {transition_state}")
    return SequentialProbability(
        p_no=p_no,
        p_next_routine_confirms=p_confirm,
        p_late_break_given_nonconfirmation=p_reheat,
        transition_state=transition_state,
    )


__all__ = [
    "FEATURES",
    "HYPOTHESES",
    "LEGACY_EXPERIMENTS",
    "MODEL_SPEC",
    "STABLE_MODEL_ID",
    "BusanTransitionTrainingExample",
    "SequentialProbability",
    "compose_exact_no_probability",
]
