from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from src.strategies.rule_lawyer.services.comment_intel import collect_market_comments
from src.strategies.rule_lawyer.services.market_analysis import build_market_intel_summary
from src.strategies.rule_lawyer.services.market_resolver import resolve_market
from src.strategies.rule_lawyer.services.profile_audit import audit_profile
from src.strategies.rule_lawyer.services.reporting import build_market_intel_report, default_output_dir, write_json, write_text
from src.strategies.rule_lawyer.services.smart_wallets import discover_market_wallets


def run_market_intel_workflow(
    target_market: str,
    comment_limit: int = 30,
    comment_mode: str = "top_and_newest",
    holders_depth: int = 40,
    top_wallets: int = 8,
    wallet_score_mode: str = "pnl_proxy",
    profile_audit_mode: str = "normal",
    out_dir: str = "",
) -> Dict[str, Any]:
    market = resolve_market(target_market)
    smart_wallets = discover_market_wallets(
        market=market,
        holders_depth=holders_depth,
        score_mode=wallet_score_mode,
        top_wallets=max(8, top_wallets),
    )
    wallet_score_lookup = {
        str(row.get("wallet") or ""): float(row.get("score") or 0.0)
        for row in smart_wallets.get("wallets", [])
    }
    comments_result = collect_market_comments(
        market=market,
        comment_limit=comment_limit,
        mode=comment_mode,
        wallet_score_lookup=wallet_score_lookup,
    )
    selected_wallet_rows = smart_wallets.get("wallets", [])[: max(1, top_wallets)]
    wallet_audits: List[Dict[str, Any]] = []
    for row in selected_wallet_rows:
        wallet = str(row.get("wallet") or "").strip()
        if not wallet:
            continue
        audited = audit_profile(target=wallet, fetch_all=(profile_audit_mode == "full"), max_trades=800)
        wallet_audits.append(audited.to_dict())
    summary = build_market_intel_summary(
        market=market.to_dict(),
        comments_result=comments_result,
        smart_wallets_result=smart_wallets,
        wallet_audits=wallet_audits,
    )
    output_dir = Path(out_dir) if out_dir else default_output_dir("runtime/market_intel", target_market)
    summary_path = output_dir / "summary.json"
    report_path = output_dir / "report.md"
    comments_path = output_dir / "comments.json"
    smart_wallets_path = output_dir / "smart_wallets.json"
    write_json(summary_path, summary.to_dict())
    write_text(report_path, build_market_intel_report(summary.to_dict()))
    write_json(comments_path, comments_result)
    write_json(smart_wallets_path, smart_wallets)
    audits_dir = output_dir / "wallet_audits"
    for audit in wallet_audits:
        wallet = str(((audit.get("profile") or {}).get("proxy_wallet")) or "wallet")
        write_json(audits_dir / wallet / "summary.json", audit)
    return {
        "summary": summary.to_dict(),
        "summary_path": str(summary_path),
        "report_path": str(report_path),
        "comments_path": str(comments_path),
        "smart_wallets_path": str(smart_wallets_path),
        "output_dir": str(output_dir),
    }
