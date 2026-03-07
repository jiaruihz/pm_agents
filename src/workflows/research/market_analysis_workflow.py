from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from src.strategies.rule_lawyer.services.market_analysis import build_market_analysis_summary
from src.strategies.rule_lawyer.services.reporting import build_market_analysis_report, default_output_dir, write_json, write_text
from src.workflows.research.market_intel_workflow import run_market_intel_workflow
from src.workflows.research.market_rule_audit_workflow import run_market_rule_audit_workflow


def run_market_analysis_workflow(
    target_market: str,
    comment_limit: int = 30,
    comment_mode: str = "top_and_newest",
    holders_depth: int = 40,
    top_wallets: int = 8,
    wallet_score_mode: str = "pnl_proxy",
    profile_audit_mode: str = "normal",
    out_dir: str = "",
) -> Dict[str, Any]:
    output_dir = Path(out_dir) if out_dir else default_output_dir("runtime/market_analysis", target_market)
    rule_res = run_market_rule_audit_workflow(target_market=target_market, out_dir=str(output_dir))
    intel_res = run_market_intel_workflow(
        target_market=target_market,
        comment_limit=comment_limit,
        comment_mode=comment_mode,
        holders_depth=holders_depth,
        top_wallets=top_wallets,
        wallet_score_mode=wallet_score_mode,
        profile_audit_mode=profile_audit_mode,
        out_dir=str(output_dir),
    )
    from src.strategies.rule_lawyer.models_research import MarketIntelSummary, RuleAuditSummary

    rule_summary = RuleAuditSummary(**rule_res["summary"])
    market_intel = MarketIntelSummary(**intel_res["summary"])
    analysis = build_market_analysis_summary(rule_summary, market_intel)
    summary_path = output_dir / "summary.json"
    report_path = output_dir / "report.md"
    rule_path = output_dir / "rule_analysis.json"
    write_json(summary_path, analysis.to_dict())
    write_text(report_path, build_market_analysis_report(analysis.to_dict()))
    write_json(rule_path, rule_res["summary"])
    return {
        "summary": analysis.to_dict(),
        "summary_path": str(summary_path),
        "report_path": str(report_path),
        "rule_path": str(rule_path),
        "output_dir": str(output_dir),
    }
