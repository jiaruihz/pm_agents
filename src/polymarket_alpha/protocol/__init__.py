"""Pure, append-only protocol reducers for Polymarket Alpha P0."""

from .lifecycle import (
    CandidateLifecycleError,
    CandidateLifecycleProjection,
    NORMAL_TRANSITION_MATRIX,
    reduce_candidate_lifecycle,
)

__all__ = [
    "CandidateLifecycleError",
    "CandidateLifecycleProjection",
    "NORMAL_TRANSITION_MATRIX",
    "reduce_candidate_lifecycle",
]
