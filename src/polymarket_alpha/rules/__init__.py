"""Public P0-07 RuleContractCompiler and Gate A/B surface."""

from .compiler import (
    RuleContractCompiler,
    corpus_from_legacy,
    structured_parse_from_legacy,
)
from .gates import evaluate_gate_a, evaluate_gate_b
from .binding import (
    BoundRuleProposal,
    CanonicalRuleSegment,
    RuleBindingError,
    RuleBindingReceipt,
    apply_rule_review,
    bind_rule_parse_proposal,
    binding_source_evidence,
)
from .models import (
    CompilationStatus,
    CorpusSourceIdentity,
    ParseStatus,
    RuleCompilationOutcome,
    RuleCompilationReceipt,
    RuleCompilationRequest,
    RuleCorpusSnapshot,
    RuleGateDecision,
    RuleGateStage,
    RuleSourceEvidence,
    StructuredRuleParse,
)

__all__ = [
    "CompilationStatus",
    "CorpusSourceIdentity",
    "ParseStatus",
    "RuleCompilationOutcome",
    "RuleCompilationReceipt",
    "RuleCompilationRequest",
    "RuleContractCompiler",
    "RuleCorpusSnapshot",
    "RuleGateDecision",
    "RuleGateStage",
    "RuleSourceEvidence",
    "StructuredRuleParse",
    "corpus_from_legacy",
    "evaluate_gate_a",
    "evaluate_gate_b",
    "structured_parse_from_legacy",
    "BoundRuleProposal",
    "CanonicalRuleSegment",
    "RuleBindingError",
    "RuleBindingReceipt",
    "apply_rule_review",
    "bind_rule_parse_proposal",
    "binding_source_evidence",
]
