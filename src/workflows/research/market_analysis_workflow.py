from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List

from src.strategies.rule_lawyer.models_research import EventAnalysisSummary, MarketIntelSummary, RiskFlag, RuleAuditSummary
from src.strategies.rule_lawyer.services.market_analysis import build_market_analysis_summary
from src.strategies.rule_lawyer.services.market_resolver import resolve_event, resolve_market, resolve_market_target
from src.strategies.rule_lawyer.services.reporting import (
    build_event_analysis_report,
    build_market_analysis_report,
    default_output_dir,
    write_json,
    write_text,
)
from src.workflows.research.market_intel_workflow import run_market_intel_workflow
from src.workflows.research.market_rule_audit_workflow import run_market_rule_audit_workflow


def _safe_copy(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def _run_single_market_analysis(
    target_market: str,
    output_dir: Path,
    comment_limit: int,
    comment_mode: str,
    holders_depth: int,
    top_wallets: int,
    wallet_score_mode: str,
    profile_audit_mode: str,
    require_rule_llm: bool = True,
    wallet_audit_max_trades: int = 800,
    max_candidate_wallets: int = 50,
) -> Dict[str, Any]:
    rule_stage_dir = output_dir / "_rule_audit"
    intel_stage_dir = output_dir / "_market_intel"
    rule_res = run_market_rule_audit_workflow(
        target_market=target_market,
        out_dir=str(rule_stage_dir),
        require_llm=require_rule_llm,
    )
    intel_res = run_market_intel_workflow(
        target_market=target_market,
        comment_limit=comment_limit,
        comment_mode=comment_mode,
        holders_depth=holders_depth,
        top_wallets=top_wallets,
        wallet_score_mode=wallet_score_mode,
        profile_audit_mode=profile_audit_mode,
        max_candidate_wallets=max_candidate_wallets,
        out_dir=str(intel_stage_dir),
        wallet_audit_max_trades=wallet_audit_max_trades,
    )
    rule_summary = RuleAuditSummary(**rule_res["summary"])
    market_intel = MarketIntelSummary(**intel_res["summary"])
    analysis = build_market_analysis_summary(rule_summary, market_intel)

    summary_path = output_dir / "summary.json"
    report_path = output_dir / "report.md"
    rule_path = output_dir / "rule_analysis.json"
    snapshot_path = output_dir / "market_snapshot.json"
    comments_path = output_dir / "comments.json"
    smart_wallets_path = output_dir / "smart_wallets.json"

    write_json(summary_path, analysis.to_dict())
    write_text(report_path, build_market_analysis_report(analysis.to_dict()))
    write_json(rule_path, rule_res["summary"])
    write_json(snapshot_path, rule_summary.market)
    if "comments_path" in intel_res:
        _safe_copy(Path(intel_res["comments_path"]), comments_path)
    if "smart_wallets_path" in intel_res:
        _safe_copy(Path(intel_res["smart_wallets_path"]), smart_wallets_path)
    src_wallet_audits = Path(intel_res["output_dir"]) / "wallet_audits"
    dst_wallet_audits = output_dir / "wallet_audits"
    if src_wallet_audits.exists():
        shutil.rmtree(dst_wallet_audits, ignore_errors=True)
        shutil.copytree(src_wallet_audits, dst_wallet_audits)
    return {
        "summary": analysis.to_dict(),
        "summary_path": str(summary_path),
        "report_path": str(report_path),
        "rule_path": str(rule_path),
        "snapshot_path": str(snapshot_path),
        "comments_path": str(comments_path),
        "smart_wallets_path": str(smart_wallets_path),
        "output_dir": str(output_dir),
    }


def _build_event_takeaways(event_payload: Dict[str, Any], market_summaries: List[Dict[str, Any]]) -> tuple[List[str], List[Dict[str, Any]], float, str]:
    takeaways: List[str] = []
    risk_flags: List[Dict[str, Any]] = []
    yes_sorted = sorted(market_summaries, key=lambda x: float(x.get("yes_price", 0.0)), reverse=True)
    if yes_sorted:
        top = yes_sorted[0]
        takeaways.append(
            f"当前 Yes 定价最高的子市场是 {top.get('group_title') or top.get('question')}，Yes 约 {float(top.get('yes_price', 0.0)):.1%}。"
        )
    comments_unavailable = [x for x in market_summaries if x.get("comment_status") != "ok"]
    rules_unavailable = [x for x in market_summaries if x.get("rule_status") != "ok"]
    if comments_unavailable:
        takeaways.append(f"{len(comments_unavailable)} 个子市场未抓到可用评论，相关结论更多依赖 holder / wallet 侧证据。")
        risk_flags.append(
            RiskFlag(
                provider_name="event_analysis",
                risk_type="comments_partially_unavailable",
                severity="medium",
                summary="部分子市场未抓到评论区数据，事件级分析存在信息盲区。",
                payload={"missing_comment_markets": [x.get("slug", "") for x in comments_unavailable]},
            ).to_dict()
        )
    if rules_unavailable:
        takeaways.append(f"{len(rules_unavailable)} 个子市场未完成结构化规则解析，这部分结论仍不能直接用于交易决策。")
        risk_flags.append(
            RiskFlag(
                provider_name="event_analysis",
                risk_type="rules_partially_unavailable",
                severity="high",
                summary="部分子市场没有完成结构化规则解析，事件级结论存在关键规则盲区。",
                payload={"missing_rule_markets": [x.get("slug", "") for x in rules_unavailable]},
            ).to_dict()
        )
    high_rule_risk = [x for x in market_summaries if x.get("verdict") == "high_rule_risk"]
    if high_rule_risk:
        takeaways.append(f"{len(high_rule_risk)} 个子市场被规则风险门控，需要优先人工审阅原始结算说明。")
        risk_flags.append(
            RiskFlag(
                provider_name="event_analysis",
                risk_type="high_rule_risk_markets",
                severity="high",
                summary="事件内存在规则风险较高的子市场。",
                payload={"markets": [x.get("slug", "") for x in high_rule_risk]},
            ).to_dict()
        )
    multi_yes = [x for x in market_summaries if float(x.get("yes_price", 0.0)) >= 0.30]
    if len(multi_yes) >= 2:
        takeaways.append("事件内有多个子市场的 Yes 定价同时不低，这组市场更像可多赢结构，而不是互斥单选题。")
    confidence = min(
        1.0,
        0.35
        + min(0.45, len(market_summaries) * 0.04)
        - min(0.2, len(comments_unavailable) * 0.03)
        - min(0.25, len(rules_unavailable) * 0.06)
    )
    verdict = "mixed"
    if rules_unavailable:
        verdict = "rule_unavailable"
    elif high_rule_risk:
        verdict = "high_rule_risk"
    elif yes_sorted and float(yes_sorted[0].get("yes_price", 0.0)) >= 0.60:
        verdict = "leans_yes"
    elif yes_sorted and float(yes_sorted[0].get("yes_price", 0.0)) <= 0.40:
        verdict = "leans_no"
    return takeaways, risk_flags, round(confidence, 6), verdict


def _run_event_analysis(
    target_market: str,
    output_dir: Path,
    comment_limit: int,
    comment_mode: str,
    holders_depth: int,
    top_wallets: int,
    wallet_score_mode: str,
    profile_audit_mode: str,
    require_rule_llm: bool = True,
    max_event_markets: int = 5,
) -> Dict[str, Any]:
    event = resolve_event(target_market)
    market_rows = sorted(
        list(event.markets),
        key=lambda row: float(row.get("volume", 0.0)),
        reverse=True,
    )[: max(1, max_event_markets)]
    market_summaries: List[Dict[str, Any]] = []
    markets_root = output_dir / "markets"
    for row in market_rows:
        slug = str(row.get("slug") or "").strip()
        if not slug:
            continue
        child_dir = markets_root / slug
        child = _run_single_market_analysis(
            target_market=slug,
            output_dir=child_dir,
            comment_limit=comment_limit,
            comment_mode=comment_mode,
            holders_depth=holders_depth,
            top_wallets=top_wallets,
            wallet_score_mode=wallet_score_mode,
            profile_audit_mode=profile_audit_mode,
            require_rule_llm=require_rule_llm,
            wallet_audit_max_trades=250,
            max_candidate_wallets=12,
        )
        child_summary = child["summary"]
        market_intel = child_summary.get("market_intel", {})
        raw_market = row.get("raw_market", {}) if isinstance(row.get("raw_market"), dict) else {}
        prices = row.get("outcome_prices") or []
        yes_price = float(prices[0]) if len(prices) >= 1 else 0.0
        no_price = float(prices[1]) if len(prices) >= 2 else max(0.0, 1.0 - yes_price)
        market_summaries.append(
            {
                "slug": row.get("slug", ""),
                "question": row.get("question", ""),
                "group_title": raw_market.get("groupItemTitle") or row.get("question", ""),
                "yes_price": yes_price,
                "no_price": no_price,
                "volume": float(row.get("volume", 0.0)),
                "liquidity": float(row.get("liquidity", 0.0)),
                "verdict": child_summary.get("verdict", ""),
                "confidence": float(child_summary.get("confidence", 0.0)),
                "completeness": child_summary.get("completeness", "partial"),
                "missing_sections": child_summary.get("missing_sections", []),
                "rule_status": child_summary.get("rule_audit", {}).get("rule_status", ""),
                "comment_status": market_intel.get("comment_status", "unavailable"),
                "smart_wallet_count": len(market_intel.get("smart_wallets", [])),
                "top_commentary_count": len(market_intel.get("top_commentary", [])),
                "report_path": child["report_path"],
                "summary_path": child["summary_path"],
            }
        )
    takeaways, risk_flags, confidence, verdict = _build_event_takeaways(event.to_dict(), market_summaries)
    event_summary = EventAnalysisSummary(
        report_kind="event_analysis",
        event=event.to_dict(),
        market_summaries=market_summaries,
        aggregate_takeaways=takeaways,
        completeness="partial" if any(x.get("verdict") == "rule_unavailable" for x in market_summaries) else "full",
        missing_sections=["rules"] if any(x.get("verdict") == "rule_unavailable" for x in market_summaries) else [],
        next_research_steps=[
            "逐个补跑缺失规则层的子市场，确认是否能形成完整三层证据。",
            "对价格最高的子市场补充最新外部并购新闻和基本面核查。",
        ],
        risk_flags=risk_flags,
        confidence=confidence,
        verdict=verdict,
        caveat="事件级分析会逐个拆解子市场。若 event 内存在可多赢结构，不应把各子市场 Yes 概率当作互斥分布。",
    )
    summary_path = output_dir / "summary.json"
    report_path = output_dir / "report.md"
    snapshot_path = output_dir / "event_snapshot.json"
    write_json(summary_path, event_summary.to_dict())
    write_text(report_path, build_event_analysis_report(event_summary.to_dict()))
    write_json(snapshot_path, event.to_dict())
    return {
        "summary": event_summary.to_dict(),
        "summary_path": str(summary_path),
        "report_path": str(report_path),
        "snapshot_path": str(snapshot_path),
        "output_dir": str(output_dir),
    }


def run_market_analysis_workflow(
    target_market: str,
    comment_limit: int = 30,
    comment_mode: str = "top_and_newest",
    holders_depth: int = 40,
    top_wallets: int = 8,
    wallet_score_mode: str = "pnl_proxy",
    profile_audit_mode: str = "normal",
    require_rule_llm: bool = True,
    out_dir: str = "",
) -> Dict[str, Any]:
    target = resolve_market_target(target_market)
    if target.kind == "event":
        output_dir = Path(out_dir) if out_dir else default_output_dir("runtime/events", target.slug or target_market)
        return _run_event_analysis(
            target_market=target_market,
            output_dir=output_dir,
            comment_limit=comment_limit,
            comment_mode=comment_mode,
            holders_depth=holders_depth,
            top_wallets=top_wallets,
            wallet_score_mode=wallet_score_mode,
            profile_audit_mode=profile_audit_mode,
            require_rule_llm=require_rule_llm,
        )
    if target.kind == "slug":
        try:
            event = resolve_event(target_market)
            if len(event.markets) > 1 and event.slug == target.slug:
                output_dir = Path(out_dir) if out_dir else default_output_dir("runtime/events", event.slug or target_market)
                return _run_event_analysis(
                    target_market=target_market,
                    output_dir=output_dir,
                    comment_limit=comment_limit,
                    comment_mode=comment_mode,
                    holders_depth=holders_depth,
                    top_wallets=top_wallets,
                    wallet_score_mode=wallet_score_mode,
                    profile_audit_mode=profile_audit_mode,
                    require_rule_llm=require_rule_llm,
                )
        except Exception:
            pass
    resolved_market = resolve_market(target_market)
    output_dir = Path(out_dir) if out_dir else default_output_dir("runtime/market_analysis", resolved_market.slug or target_market)
    return _run_single_market_analysis(
        target_market=resolved_market.slug or target_market,
        output_dir=output_dir,
        comment_limit=comment_limit,
        comment_mode=comment_mode,
        holders_depth=holders_depth,
        top_wallets=top_wallets,
        wallet_score_mode=wallet_score_mode,
        profile_audit_mode=profile_audit_mode,
        require_rule_llm=require_rule_llm,
        wallet_audit_max_trades=800,
        max_candidate_wallets=50,
    )
