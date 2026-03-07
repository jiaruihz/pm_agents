from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from src.strategies.rule_lawyer.models_research import EvidenceRecord, ResolvedMarket, RiskFlag, RuleAuditSummary
from src.strategies.rule_lawyer.services.common import to_float


AMBIGUITY_PHRASES = [
    ("sole discretion", "contains sole discretion language"),
    ("ambigu", "explicit ambiguity language"),
    ("variation", "contains variation-style wording"),
    ("consensus", "uses consensus-based resolution wording"),
    ("committee", "mentions committee-based resolution"),
    ("credible reporting", "depends on credible reporting"),
    ("significant", "contains significant/material threshold wording"),
]


def _llm_ready() -> bool:
    return bool(
        (
            os.getenv("LLM_API_KEY")
            or os.getenv("IFLOW_API_KEY")
        )
        and (
            os.getenv("LLM_MODEL")
            or os.getenv("IFLOW_MODEL")
        )
    )


def _heuristic_rule_analysis(market: ResolvedMarket) -> RuleAuditSummary:
    text = "\n".join([market.question, market.description, market.rules]).lower()
    ambiguity_flags: List[str] = []
    for needle, label in AMBIGUITY_PHRASES:
        if needle in text:
            ambiguity_flags.append(label)
    clarity = 0.2
    if market.rules.strip():
        clarity += 0.3
    if market.description.strip():
        clarity += 0.2
    if market.end_date:
        clarity += 0.1
    if any(word in text for word in ["resolve", "resolves", "will resolve", "this market"]):
        clarity += 0.15
    if any(word in text for word in ["except", "unless", "does not", "won't resolve"]):
        clarity += 0.1
    clarity = max(0.0, min(1.0, clarity - min(0.3, len(ambiguity_flags) * 0.05)))
    resolution_risk = max(0.0, min(1.0, 0.15 + len(ambiguity_flags) * 0.12))
    rule_summary = market.rules.strip() or market.description.strip() or market.question.strip()
    settlement_summary = (
        "基于市场描述和规则字段做启发式抽取；"
        "本次未获得结构化 LLM 规则解析，需人工复核关键结算条件。"
    )
    evidence = [
        EvidenceRecord(
            provider_name="rule_lawyer_heuristic",
            evidence_type="rule_text",
            direction="neutral",
            confidence=max(0.2, clarity),
            summary="市场规则文本已提取并完成启发式清晰度评估。",
            payload={"has_rules": bool(market.rules.strip()), "has_description": bool(market.description.strip())},
        ).to_dict()
    ]
    risk_flags = []
    if ambiguity_flags:
        risk_flags.append(
            RiskFlag(
                provider_name="rule_lawyer_heuristic",
                risk_type="ambiguity",
                severity="high" if resolution_risk >= 0.65 else "medium",
                summary="规则文本中存在潜在歧义或主观裁定空间。",
                payload={"ambiguity_flags": ambiguity_flags},
            ).to_dict()
        )
    return RuleAuditSummary(
        market=market.to_dict(),
        rule_summary=rule_summary[:600],
        settlement_summary=settlement_summary,
        rule_clarity_score=round(clarity, 6),
        ambiguity_flags=ambiguity_flags,
        resolution_risk=round(resolution_risk, 6),
        evidence_records=evidence,
        risk_flags=risk_flags,
        source_trace={"mode": "heuristic"},
        caveat="规则分析在未启用 LLM 时使用启发式规则提取，不能替代人工审阅原始规则文本。",
    )


def run_rule_analysis(market: ResolvedMarket) -> RuleAuditSummary:
    market_payload: Dict[str, Any] = {
        "market_id": market.market_id,
        "slug": market.slug,
        "question": market.question,
        "description": market.description,
        "rules": market.rules,
        "category": market.category,
        "end_at_utc": market.end_date,
    }
    if not _llm_ready():
        return _heuristic_rule_analysis(market)
    try:
        from src.strategies.rule_lawyer.parser import compute_rule_score, parse_market_with_llm
    except Exception:
        return _heuristic_rule_analysis(market)
    try:
        import asyncio

        parsed = asyncio.run(parse_market_with_llm(market_payload, retry_on_fail=True))
    except Exception:
        parsed = None
    if not parsed:
        return _heuristic_rule_analysis(market)
    score_payload = compute_rule_score(parsed)
    evidence = [
        EvidenceRecord(
            provider_name="rule_lawyer_llm",
            evidence_type="parsed_rule",
            direction="neutral",
            confidence=to_float(parsed.llm_confidence, 0.0),
            summary=parsed.notes_for_humans or "已完成结构化规则解析。",
            payload={
                "trigger_type": parsed.trigger_type,
                "minimum_conditions": parsed.trigger_minimum_conditions,
                "explicit_exclusions": parsed.explicit_exclusions,
                "entity_definitions": [x.dict() for x in parsed.entity_definitions],
            },
        ).to_dict()
    ]
    risk_flags = []
    if parsed.ambiguity_flags:
        risk_flags.append(
            RiskFlag(
                provider_name="rule_lawyer_llm",
                risk_type="ambiguity",
                severity="high" if parsed.dispute_risk_score >= 0.65 else "medium",
                summary="LLM 规则解析识别到潜在歧义或争议风险。",
                payload={"ambiguity_flags": parsed.ambiguity_flags},
            ).to_dict()
        )
    settlement_summary = (
        f"结算来源类型={parsed.settlement_source_type}；"
        f"触发类型={parsed.trigger_type}；"
        f"最小触发条件数={len(parsed.trigger_minimum_conditions)}。"
    )
    rule_summary = parsed.notes_for_humans or market.rules or market.description or market.question
    return RuleAuditSummary(
        market=market.to_dict(),
        rule_summary=rule_summary[:800],
        settlement_summary=settlement_summary,
        rule_clarity_score=round(to_float(parsed.clarity_score, 0.0), 6),
        ambiguity_flags=list(parsed.ambiguity_flags or []),
        resolution_risk=round(to_float(parsed.dispute_risk_score, 0.0), 6),
        evidence_records=evidence,
        risk_flags=risk_flags,
        source_trace={"mode": "llm", "rule_score": score_payload},
        caveat="LLM 规则解析是研究辅助，不应替代人工核对原市场规则与结算说明。",
    )
