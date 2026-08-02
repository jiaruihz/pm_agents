"""Shared decision contracts for city intraday weather strategies."""

from .canonical_bridge import TemporaryCanonicalBridge
from .contracts import (
    MODEL_OUTPUT_SCHEMA_VERSION,
    SIGNAL_CANDIDATE_SCHEMA_VERSION,
    TRADE_INTENT_SCHEMA_VERSION,
    ModelOutput,
    SignalCandidate,
    TradeIntent,
)
from .legacy_adapters import (
    DecisionBundle,
    LegacyDecisionBundle,
    legacy_bundle_from_evaluation,
    legacy_trade_intent_from_paper_intent,
)
from .decision_sink import (
    DUAL_WRITE_SCHEMA_VERSION,
    DecisionContractJournalSink,
    SinkResult,
)

__all__ = [
    "LegacyDecisionBundle",
    "DecisionBundle",
    "MODEL_OUTPUT_SCHEMA_VERSION",
    "ModelOutput",
    "SIGNAL_CANDIDATE_SCHEMA_VERSION",
    "SignalCandidate",
    "TRADE_INTENT_SCHEMA_VERSION",
    "TemporaryCanonicalBridge",
    "TradeIntent",
    "legacy_bundle_from_evaluation",
    "legacy_trade_intent_from_paper_intent",
    "DUAL_WRITE_SCHEMA_VERSION",
    "DecisionContractJournalSink",
    "SinkResult",
]
