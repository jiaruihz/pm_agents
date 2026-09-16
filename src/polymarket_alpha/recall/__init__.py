"""Provider-neutral recall aggregation surface."""

from .aggregate import (
    CandidateMergeResult,
    LateHitImpact,
    LateHitImpactType,
    ProviderBatch,
    RecallAggregationConfig,
    RecallAggregationOutcome,
    RecallAggregationRequest,
    RecallAggregator,
    RejectedRecall,
    recall_dedupe_key,
)
from .registry import ProviderDescriptor, ProviderRegistry

__all__ = [
    "CandidateMergeResult",
    "LateHitImpact",
    "LateHitImpactType",
    "ProviderBatch",
    "ProviderDescriptor",
    "ProviderRegistry",
    "RecallAggregationConfig",
    "RecallAggregationOutcome",
    "RecallAggregationRequest",
    "RecallAggregator",
    "RejectedRecall",
    "recall_dedupe_key",
]
