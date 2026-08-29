"""Shared decision contracts for city intraday weather strategies."""

from .canonical_bridge import CanonicalCandidateBridge, TemporaryCanonicalBridge
from .contracts import (
    MODEL_OUTPUT_SCHEMA_VERSION,
    SIGNAL_CANDIDATE_SCHEMA_VERSION,
    TRADE_INTENT_SCHEMA_VERSION,
    ModelOutput,
    SignalCandidate,
    TradeIntent,
)
from .next_print_adapters import (
    adapt_amos_group,
    adapt_legacy_information_event,
    adapt_official_print,
    adapt_source_observation,
)
from .next_print_contracts import (
    CITY_CONTRACTS,
    CanonicalMarketIdentity,
    CanonicalOfficialPrint,
    CanonicalSourceObservation,
    CausalExclusionReason,
    ExperimentEpoch,
    LegacyModelRegistryEntry,
    NextPrintLink,
    audit_causal_clocks,
    contract_schema_fingerprints,
    link_next_official_print,
)
from .next_print_journal import NextPrintResearchJournal
from .legacy_adapters import (
    DecisionBundle,
    LegacyDecisionBundle,
    legacy_bundle_from_evaluation,
    legacy_trade_intent_from_paper_intent,
)
from .decision_sink import (
    DECISION_JOURNAL_SCHEMA_VERSION,
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
    "CanonicalCandidateBridge",
    "TradeIntent",
    "legacy_bundle_from_evaluation",
    "legacy_trade_intent_from_paper_intent",
    "DUAL_WRITE_SCHEMA_VERSION",
    "DECISION_JOURNAL_SCHEMA_VERSION",
    "DecisionContractJournalSink",
    "SinkResult",
    "CITY_CONTRACTS",
    "CanonicalMarketIdentity",
    "CanonicalOfficialPrint",
    "CanonicalSourceObservation",
    "CausalExclusionReason",
    "ExperimentEpoch",
    "LegacyModelRegistryEntry",
    "NextPrintLink",
    "adapt_amos_group",
    "adapt_legacy_information_event",
    "adapt_official_print",
    "adapt_source_observation",
    "audit_causal_clocks",
    "contract_schema_fingerprints",
    "link_next_official_print",
    "NextPrintResearchJournal",
]
