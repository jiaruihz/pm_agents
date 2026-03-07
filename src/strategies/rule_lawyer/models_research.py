from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class BaseResearchModel:
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MarketTarget(BaseResearchModel):
    raw_input: str
    kind: str
    url: str = ""
    slug: str = ""
    condition_id: str = ""


@dataclass
class ResolvedMarket(BaseResearchModel):
    market_id: str = ""
    event_id: str = ""
    slug: str = ""
    condition_id: str = ""
    question: str = ""
    description: str = ""
    rules: str = ""
    category: str = ""
    outcomes: List[str] = field(default_factory=list)
    token_ids: List[str] = field(default_factory=list)
    outcome_prices: List[float] = field(default_factory=list)
    volume: float = 0.0
    liquidity: float = 0.0
    active: bool = True
    closed: bool = False
    end_date: str = ""
    market_url: str = ""
    event_title: str = ""
    event_description: str = ""
    raw_market: Dict[str, Any] = field(default_factory=dict)
    raw_event: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProfileTarget(BaseResearchModel):
    raw_input: str
    kind: str
    username: str = ""
    profile_url: str = ""
    wallet: str = ""


@dataclass
class ProfileSummary(BaseResearchModel):
    target: str
    profile: Dict[str, Any] = field(default_factory=dict)
    trade_summary: Dict[str, Any] = field(default_factory=dict)
    position_score: Dict[str, Any] = field(default_factory=dict)
    closed_position_score: Dict[str, Any] = field(default_factory=dict)
    position_summary: Dict[str, Any] = field(default_factory=dict)
    portfolio_pnl_analysis: Dict[str, Any] = field(default_factory=dict)
    portfolio_pnl_windows: Dict[str, int] = field(default_factory=dict)
    api_status: Dict[str, str] = field(default_factory=dict)
    verdict: str = ""
    caveat: str = ""
    raw_payloads: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CommentRecord(BaseResearchModel):
    comment_id: str
    body: str
    created_at: str = ""
    reaction_count: int = 0
    reply_count: int = 0
    report_count: int = 0
    profile_wallet: str = ""
    profile_name: str = ""
    profile_pseudonym: str = ""
    profile_verified: bool = False
    position_context: Dict[str, Any] = field(default_factory=dict)
    classification: str = "noise"
    bias_direction: str = "neutral"
    value_score: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WalletSignal(BaseResearchModel):
    wallet: str
    name: str = ""
    pseudonym: str = ""
    win_rate: float = 0.0
    wins: int = 0
    losses: int = 0
    resolved_trades: int = 0
    resolved_markets: int = 0
    resolved_notional: float = 0.0
    confidence: float = 0.0
    score: float = 0.0
    discovery_score: float = 0.0
    discovery_sources: List[str] = field(default_factory=list)
    discovery_markets: List[str] = field(default_factory=list)
    style_label: str = ""
    style_features: Dict[str, Any] = field(default_factory=dict)
    style_explanation: str = ""
    token_convictions: Dict[str, float] = field(default_factory=dict)
    llm_explanation: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceRecord(BaseResearchModel):
    provider_name: str
    evidence_type: str
    direction: str
    confidence: float
    summary: str
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskFlag(BaseResearchModel):
    provider_name: str
    risk_type: str
    severity: str
    summary: str
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RuleAuditSummary(BaseResearchModel):
    market: Dict[str, Any] = field(default_factory=dict)
    rule_summary: str = ""
    settlement_summary: str = ""
    rule_clarity_score: float = 0.0
    ambiguity_flags: List[str] = field(default_factory=list)
    resolution_risk: float = 0.0
    evidence_records: List[Dict[str, Any]] = field(default_factory=list)
    risk_flags: List[Dict[str, Any]] = field(default_factory=list)
    source_trace: Dict[str, Any] = field(default_factory=dict)
    caveat: str = ""


@dataclass
class MarketIntelSummary(BaseResearchModel):
    market: Dict[str, Any] = field(default_factory=dict)
    comment_status: str = "unavailable"
    comment_fetch_method: str = ""
    comment_stats: Dict[str, Any] = field(default_factory=dict)
    top_commentary: List[Dict[str, Any]] = field(default_factory=list)
    smart_wallets: List[Dict[str, Any]] = field(default_factory=list)
    selected_wallets: List[str] = field(default_factory=list)
    wallet_audit_summaries: List[Dict[str, Any]] = field(default_factory=list)
    observed_bias: float = 0.0
    risk_flags: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    verdict: str = ""
    caveat: str = ""
    provider_traces: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketAnalysisSummary(BaseResearchModel):
    market: Dict[str, Any] = field(default_factory=dict)
    rule_audit: Dict[str, Any] = field(default_factory=dict)
    market_intel: Dict[str, Any] = field(default_factory=dict)
    observed_bias: float = 0.0
    risk_flags: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    verdict: str = ""
    caveat: str = ""
    provider_traces: Dict[str, Any] = field(default_factory=dict)
