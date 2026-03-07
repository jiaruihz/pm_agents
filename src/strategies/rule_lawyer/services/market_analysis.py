from __future__ import annotations

from typing import Any, Dict, List

from src.strategies.rule_lawyer.models_research import MarketAnalysisSummary, MarketIntelSummary, RiskFlag, RuleAuditSummary
from src.strategies.rule_lawyer.services.common import to_float


def build_market_intel_summary(
    market: Dict[str, Any],
    comments_result: Dict[str, Any],
    smart_wallets_result: Dict[str, Any],
    wallet_audits: List[Dict[str, Any]],
) -> MarketIntelSummary:
    wallets = smart_wallets_result.get("wallets", [])
    token_ids = market.get("token_ids") or []
    primary_token = token_ids[0] if token_ids else ""
    wallet_bias_values = []
    for wallet in wallets:
        conviction = (wallet.get("token_convictions") or {}).get(primary_token)
        if conviction is None:
            continue
        wallet_bias_values.append(float(conviction) * max(0.1, float(wallet.get("confidence", 0.0) or 0.0)))
    wallet_bias = sum(wallet_bias_values) / len(wallet_bias_values) if wallet_bias_values else 0.0
    commentary = comments_result.get("top_commentary", [])
    comment_weight = 0.0
    comment_total = 0.0
    for row in commentary:
        direction = row.get("bias_direction")
        score = to_float(row.get("value_score"), 0.0)
        if direction == "yes":
            comment_weight += score
            comment_total += score
        elif direction == "no":
            comment_weight -= score
            comment_total += score
    comment_bias = (comment_weight / comment_total) if comment_total > 0 else 0.0
    comment_status = comments_result.get("comment_status", "unavailable")
    observed_bias = (0.6 * wallet_bias + 0.4 * comment_bias) if comment_status == "ok" else wallet_bias
    risk_flags: List[Dict[str, Any]] = []
    if comment_status != "ok":
        risk_flags.append(
            RiskFlag(
                provider_name="comment_intel",
                risk_type="comments_unavailable",
                severity="medium",
                summary="评论区数据不可用，市场情报已自动降级为 holder/wallet 视角。",
            ).to_dict()
        )
    if len(wallets) < 3:
        risk_flags.append(
            RiskFlag(
                provider_name="smart_wallets",
                risk_type="sparse_wallet_sample",
                severity="medium",
                summary="可用于判断的关键钱包样本较少，市场偏向结论置信度有限。",
            ).to_dict()
        )
    confidence = min(
        1.0,
        0.35
        + min(0.35, len(wallets) * 0.05)
        + (0.2 if comment_status == "ok" else 0.0)
        + min(0.1, abs(observed_bias) * 0.2),
    )
    verdict = "comments_unavailable" if comment_status != "ok" else "mixed"
    if comment_status == "ok":
        if abs(observed_bias) < 0.15:
            verdict = "insufficient_edge"
        elif observed_bias > 0:
            verdict = "leans_yes"
        else:
            verdict = "leans_no"
    return MarketIntelSummary(
        market=market,
        comment_status=comment_status,
        comment_fetch_method=str(comments_result.get("comment_fetch_method") or ""),
        comment_stats=comments_result.get("comment_stats", {}),
        top_commentary=commentary,
        smart_wallets=wallets,
        selected_wallets=[str(x.get("wallet") or "") for x in wallets[:8]],
        wallet_audit_summaries=wallet_audits,
        observed_bias=round(observed_bias, 6),
        risk_flags=risk_flags,
        confidence=round(confidence, 6),
        verdict=verdict,
        caveat="市场情报层主要基于评论、holder 和钱包历史表现，不能替代对原始规则和事件事实的人工核查。",
        provider_traces={
            "comments": {"status": comment_status, "top_commentary_count": len(commentary)},
            "wallets": smart_wallets_result.get("stats", {}),
        },
    )


def build_market_analysis_summary(rule_audit: RuleAuditSummary, market_intel: MarketIntelSummary) -> MarketAnalysisSummary:
    clarity = to_float(rule_audit.rule_clarity_score, 0.0)
    risk = to_float(rule_audit.resolution_risk, 0.0)
    base_confidence = to_float(market_intel.confidence, 0.0)
    if risk >= 0.75 or clarity <= 0.35:
        verdict = "high_rule_risk"
        confidence = round(base_confidence * 0.45, 6)
    else:
        verdict = market_intel.verdict or "insufficient_edge"
        multiplier = 1.0
        if risk >= 0.55 or clarity <= 0.55:
            multiplier = 0.7
        confidence = round(base_confidence * multiplier, 6)
    risk_flags = list(rule_audit.risk_flags) + list(market_intel.risk_flags)
    if verdict == "high_rule_risk":
        risk_flags.append(
            RiskFlag(
                provider_name="market_analysis",
                risk_type="high_rule_risk",
                severity="high",
                summary="规则清晰度或争议风险过高，最终结论已被规则风险门控。",
                payload={"rule_clarity_score": clarity, "resolution_risk": risk},
            ).to_dict()
        )
    caveat = (
        "最终结论由规则风险门控后再结合评论与钱包偏向生成；"
        "如果市场规则本身存在高歧义，应优先人工审阅原始规则。"
    )
    return MarketAnalysisSummary(
        market=rule_audit.market,
        rule_audit=rule_audit.to_dict(),
        market_intel=market_intel.to_dict(),
        observed_bias=market_intel.observed_bias,
        risk_flags=risk_flags,
        confidence=confidence,
        verdict=verdict,
        caveat=caveat,
        provider_traces={
            "rule_audit": rule_audit.source_trace,
            "market_intel": market_intel.provider_traces,
        },
    )
