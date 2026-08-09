"""Shared probability evaluation primitives for city weather models."""

from .calibration import (
    fit_simplex_logit_calibrator,
    predict_simplex_logit_calibrator,
    simplex_log_ratio_features,
)
from .lineage import gzip_content_sha256
from .ordinal_model import OrderedThresholdClassifier
from .prior_model import EmpiricalPriorModel
from .model_governance import (
    AdmissionDecision,
    AdmissionEvidence,
    ExperimentRun,
    FeatureDefinition,
    MechanismHypothesis,
    StableModelSpec,
    decide_hypothesis_admission,
    experiment_alias_manifest,
    validate_hypothesis_registry,
)
from .contracts import (
    EVENT_SCHEMA_VERSION,
    PREDICTION_SCHEMA_VERSION,
    EventEnvelope,
    stable_json,
    stable_sha256,
    validate_prediction_row,
)
from .probability import (
    binary_calibration_table,
    binary_loss_values,
    binary_score,
    build_checkpoint_grain,
    build_state_entry_grain,
    build_transition_grain,
    composite_grain_date_bootstrap_delta,
    composite_grain_weights,
    date_block_bootstrap_delta,
    event_bin_from_cumulative_labels,
    event_probabilities_to_cumulative,
    hazards_to_event_probabilities,
    integrated_horizon_score,
    ordinal_loss_values,
    ordinal_score,
)
from .replay import (
    FixtureCheckpointBuilder,
    FixtureInputCatalog,
    ReplayResult,
    ReplayRunner,
    VirtualClock,
)
from .reporting import build_evaluation_report

__all__ = [
    "AdmissionDecision",
    "AdmissionEvidence",
    "binary_calibration_table",
    "binary_loss_values",
    "binary_score",
    "build_evaluation_report",
    "build_checkpoint_grain",
    "build_state_entry_grain",
    "build_transition_grain",
    "composite_grain_date_bootstrap_delta",
    "composite_grain_weights",
    "date_block_bootstrap_delta",
    "EVENT_SCHEMA_VERSION",
    "EmpiricalPriorModel",
    "EventEnvelope",
    "ExperimentRun",
    "event_bin_from_cumulative_labels",
    "event_probabilities_to_cumulative",
    "fit_simplex_logit_calibrator",
    "FixtureCheckpointBuilder",
    "FixtureInputCatalog",
    "FeatureDefinition",
    "gzip_content_sha256",
    "hazards_to_event_probabilities",
    "integrated_horizon_score",
    "MechanismHypothesis",
    "ordinal_loss_values",
    "ordinal_score",
    "OrderedThresholdClassifier",
    "PREDICTION_SCHEMA_VERSION",
    "predict_simplex_logit_calibrator",
    "ReplayResult",
    "ReplayRunner",
    "simplex_log_ratio_features",
    "stable_json",
    "stable_sha256",
    "StableModelSpec",
    "decide_hypothesis_admission",
    "experiment_alias_manifest",
    "validate_prediction_row",
    "validate_hypothesis_registry",
    "VirtualClock",
]
