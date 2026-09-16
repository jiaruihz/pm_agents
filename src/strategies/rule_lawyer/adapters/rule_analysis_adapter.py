from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from src.agents.llm.codex_cli_client import codex_cli_available
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


def _codex_cli_ready() -> bool:
    return codex_cli_available()


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
        rule_status="ok",
        llm_required=False,
        llm_used=False,
        trigger_conditions=[],
        explicit_exclusions=[],
        entity_definitions=[],
        ambiguity_explanations=[],
        decision_boundary_notes=[],
        yes_case_examples=[],
        no_case_examples=[],
        rule_summary=rule_summary[:600],
        settlement_summary=settlement_summary,
        rule_clarity_score=round(clarity, 6),
        ambiguity_flags=ambiguity_flags,
        resolution_risk=round(resolution_risk, 6),
        evidence_records=evidence,
        risk_flags=risk_flags,
        source_trace={
            "mode": "heuristic",
            "methodology": "未启用 LLM 时，使用规则文本可见性、截止时间、结算措辞和歧义词命中做启发式评分。",
            "clarity_components": {
                "base": 0.2,
                "has_rules_bonus": 0.3 if market.rules.strip() else 0.0,
                "has_description_bonus": 0.2 if market.description.strip() else 0.0,
                "has_end_date_bonus": 0.1 if market.end_date else 0.0,
                "has_resolution_language_bonus": 0.15
                if any(word in text for word in ["resolve", "resolves", "will resolve", "this market"])
                else 0.0,
                "has_exclusion_language_bonus": 0.1
                if any(word in text for word in ["except", "unless", "does not", "won't resolve"])
                else 0.0,
                "ambiguity_penalty": min(0.3, len(ambiguity_flags) * 0.05),
            },
            "resolution_risk_formula": {
                "base": 0.15,
                "ambiguity_flag_count": len(ambiguity_flags),
                "ambiguity_flag_penalty_per_hit": 0.12,
            },
            "matched_ambiguity_flags": ambiguity_flags,
        },
        caveat="规则分析在未启用 LLM 时使用启发式规则提取，不能替代人工审阅原始规则文本。",
    )


def _rule_unavailable(
    market: ResolvedMarket,
    reason: str,
    *,
    mode: str,
    llm_required: bool,
) -> RuleAuditSummary:
    return RuleAuditSummary(
        market=market.to_dict(),
        rule_status="unavailable",
        rule_failure_reason=reason,
        llm_required=llm_required,
        llm_used=False,
        source_trace={
            "mode": mode,
            "methodology": "规则层被配置为强制依赖结构化 LLM 解析；当前未能得到可用结果。",
        },
        caveat="规则层本次没有拿到结构化 LLM 解析结果，不提供正式规则评分；需人工补审原始规则。",
    )


def _rule_failed(
    market: ResolvedMarket,
    reason: str,
    *,
    mode: str,
) -> RuleAuditSummary:
    return RuleAuditSummary(
        market=market.to_dict(),
        rule_status="failed",
        rule_failure_reason=reason,
        llm_required=True,
        llm_used=False,
        source_trace={
            "mode": mode,
            "methodology": "规则层尝试执行结构化 LLM 解析，但运行失败或返回了不可校验结果。",
        },
        caveat="规则层执行失败，不提供正式规则评分；需检查 LLM 配置或人工复核规则文本。",
    )


def run_rule_analysis(market: ResolvedMarket, require_llm: bool = True) -> RuleAuditSummary:
    market_payload: Dict[str, Any] = {
        "market_id": market.market_id,
        "slug": market.slug,
        "question": market.question,
        "description": market.description,
        "rules": market.rules,
        "category": market.category,
        "end_at_utc": market.end_date,
    }
    backend = "api" if _llm_ready() else "codex_cli" if _codex_cli_ready() else "none"
    if backend == "none":
        if require_llm:
            return _rule_unavailable(
                market,
                "No rule parser backend available: configure API LLM or install/authenticate codex CLI.",
                mode="llm_required",
                llm_required=True,
            )
        return _heuristic_rule_analysis(market)
    try:
        from src.strategies.rule_lawyer.parser import compute_rule_score, parse_market_with_codex_cli, parse_market_with_llm
    except Exception as exc:
        if require_llm:
            return _rule_failed(market, f"LLM parser import failed: {exc}", mode="llm")
        return _heuristic_rule_analysis(market)
    if backend == "api":
        try:
            import asyncio

            parsed = asyncio.run(parse_market_with_llm(market_payload, retry_on_fail=True))
        except Exception as exc:
            if require_llm:
                return _rule_failed(market, f"API LLM parse execution failed: {exc}", mode="llm")
            parsed = None
    else:
        try:
            parsed = parse_market_with_codex_cli(market_payload)
        except Exception as exc:
            if require_llm:
                return _rule_failed(market, f"Codex CLI parse execution failed: {exc}", mode="codex_cli")
            parsed = None
    if not parsed:
        if require_llm:
            return _rule_failed(
                market,
                f"{'API LLM' if backend == 'api' else 'Codex CLI'} parse returned empty or invalid structured output.",
                mode="llm" if backend == "api" else "codex_cli",
            )
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
                "ambiguity_explanations": list(parsed.ambiguity_explanations or []),
                "decision_boundary_notes": list(parsed.decision_boundary_notes or []),
                "yes_case_examples": list(parsed.yes_case_examples or []),
                "no_case_examples": list(parsed.no_case_examples or []),
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
        rule_status="ok",
        llm_required=require_llm,
        llm_used=True,
        llm_confidence=round(to_float(parsed.llm_confidence, 0.0), 6),
        rule_score=score_payload,
        trigger_type=parsed.trigger_type,
        settlement_source_type=parsed.settlement_source_type,
        trigger_conditions=list(parsed.trigger_minimum_conditions or []),
        explicit_exclusions=list(parsed.explicit_exclusions or []),
        entity_definitions=[x.dict() for x in parsed.entity_definitions],
        ambiguity_explanations=list(parsed.ambiguity_explanations or []),
        decision_boundary_notes=list(parsed.decision_boundary_notes or []),
        yes_case_examples=list(parsed.yes_case_examples or []),
        no_case_examples=list(parsed.no_case_examples or []),
        rule_summary=rule_summary[:800],
        settlement_summary=settlement_summary,
        rule_clarity_score=round(to_float(parsed.clarity_score, 0.0), 6),
        ambiguity_flags=list(parsed.ambiguity_flags or []),
        resolution_risk=round(to_float(parsed.dispute_risk_score, 0.0), 6),
        evidence_records=evidence,
        risk_flags=risk_flags,
        source_trace={
            "mode": "llm" if backend == "api" else "codex_cli",
            "methodology": (
                "LLM 先按 schema 抽取规则结构，再由代码进行字段校验与 rule_score 计算。"
                if backend == "api"
                else "Codex CLI 先按 schema 输出结构化 JSON，再由代码进行字段校验与 rule_score 计算。"
            ),
            "prompt_schema_fields": [
                "time_window",
                "settlement_source_type",
                "trigger_type",
                "trigger_minimum_conditions",
                "explicit_exclusions",
                "entity_definitions",
                "ambiguity_flags",
                "ambiguity_explanations",
                "decision_boundary_notes",
                "yes_case_examples",
                "no_case_examples",
                "clarity_score",
                "dispute_risk_score",
                "notes_for_humans",
                "llm_confidence",
            ],
            "backend": backend,
            "llm_confidence": to_float(parsed.llm_confidence, 0.0),
            "rule_score": score_payload,
            "parsed_structure": {
                "settlement_source_type": parsed.settlement_source_type,
                "trigger_type": parsed.trigger_type,
                "trigger_minimum_conditions": list(parsed.trigger_minimum_conditions or []),
                "explicit_exclusions": list(parsed.explicit_exclusions or []),
                "entity_definitions": [x.dict() for x in parsed.entity_definitions],
                "ambiguity_flags": list(parsed.ambiguity_flags or []),
                "ambiguity_explanations": list(parsed.ambiguity_explanations or []),
                "decision_boundary_notes": list(parsed.decision_boundary_notes or []),
                "yes_case_examples": list(parsed.yes_case_examples or []),
                "no_case_examples": list(parsed.no_case_examples or []),
            },
        },
        caveat="LLM 规则解析是研究辅助，不应替代人工核对原市场规则与结算说明。",
    )
