from __future__ import annotations

from src.strategies.rule_lawyer.adapters.rule_analysis_adapter import run_rule_analysis
from src.strategies.rule_lawyer.models_research import RuleAuditSummary
from src.strategies.rule_lawyer.services.market_resolver import resolve_market


def audit_market_rules(target_market: str, require_llm: bool = True) -> RuleAuditSummary:
    market = resolve_market(target_market)
    return run_rule_analysis(market, require_llm=require_llm)
