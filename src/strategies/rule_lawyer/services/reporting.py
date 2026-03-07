from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from src.strategies.rule_lawyer.services.common import ensure_dir, slugify_target, utc_stamp


def default_output_dir(root: str, target: str) -> Path:
    return ensure_dir(Path(root) / f"{slugify_target(target)}-{utc_stamp()}")


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_profile_report(data: Dict[str, Any], focus: str, report_style: str) -> str:
    profile = data.get("profile", {})
    closed = data.get("closed_position_score", {})
    pnl = data.get("portfolio_pnl_analysis", {})
    lines = [
        f"# Polymarket Profile Audit: {profile.get('username') or data.get('target')}",
        "",
        f"- Target: `{data.get('target', '')}`",
        f"- Profile: `{profile.get('profile_url', '')}`",
        f"- Wallet: `{profile.get('proxy_wallet', '')}`",
        f"- Focus: `{focus}`",
        f"- Report style: `{report_style}`",
        "",
        "## Headline",
        "",
        data.get("verdict", ""),
        "",
        "## Core Stats",
        "",
        f"- Markets traded: `{profile.get('markets_traded', 0)}`",
        f"- Portfolio volume: `{profile.get('portfolio_volume', 0):.4f}`",
        f"- Portfolio pnl: `{profile.get('portfolio_pnl', 0):.4f}`",
        f"- Closed-position win rate: `{float(closed.get('closed_position_win_rate', 0.0)):.2%}`",
        f"- Max drawdown: `{pnl.get('max_drawdown_abs', 0)}`",
    ]
    return "\n".join(lines).strip() + "\n"


def build_rule_audit_report(data: Dict[str, Any]) -> str:
    lines = [
        f"# Market Rule Audit: {data.get('market', {}).get('slug') or data.get('market', {}).get('market_id')}",
        "",
        f"- Question: {data.get('market', {}).get('question', '')}",
        f"- Rule clarity: `{float(data.get('rule_clarity_score', 0.0)):.2f}`",
        f"- Resolution risk: `{float(data.get('resolution_risk', 0.0)):.2f}`",
        "",
        "## Rule Summary",
        "",
        data.get("rule_summary", ""),
        "",
        "## Settlement Summary",
        "",
        data.get("settlement_summary", ""),
        "",
        "## Ambiguity Flags",
        "",
    ]
    for flag in data.get("ambiguity_flags", []):
        lines.append(f"- {flag}")
    if not data.get("ambiguity_flags"):
        lines.append("- None")
    lines.extend(["", "## Caveat", "", data.get("caveat", "")])
    return "\n".join(lines).strip() + "\n"


def build_market_intel_report(data: Dict[str, Any]) -> str:
    lines = [
        f"# Market Intel: {data.get('market', {}).get('slug') or data.get('market', {}).get('market_id')}",
        "",
        f"- Question: {data.get('market', {}).get('question', '')}",
        f"- Comment status: `{data.get('comment_status', '')}`",
        f"- Observed bias: `{float(data.get('observed_bias', 0.0)):.4f}`",
        f"- Confidence: `{float(data.get('confidence', 0.0)):.2f}`",
        f"- Verdict: `{data.get('verdict', '')}`",
        "",
        "## Top Commentary",
        "",
    ]
    for row in data.get("top_commentary", [])[:10]:
        lines.append(
            f"- [{row.get('classification')}] score={row.get('value_score')} wallet={row.get('profile_wallet')} {row.get('body')}"
        )
    if not data.get("top_commentary"):
        lines.append("- No usable comments")
    lines.extend(["", "## Smart Wallets", ""])
    for row in data.get("smart_wallets", [])[:10]:
        lines.append(
            f"- {row.get('wallet')} win_rate={row.get('win_rate')} score={row.get('score')} style={row.get('style_label')}"
        )
    return "\n".join(lines).strip() + "\n"


def build_market_analysis_report(data: Dict[str, Any]) -> str:
    rule_audit = data.get("rule_audit", {})
    market_intel = data.get("market_intel", {})
    lines = [
        f"# Market Analysis: {data.get('market', {}).get('slug') or data.get('market', {}).get('market_id')}",
        "",
        f"- Question: {data.get('market', {}).get('question', '')}",
        f"- Verdict: `{data.get('verdict', '')}`",
        f"- Confidence: `{float(data.get('confidence', 0.0)):.2f}`",
        "",
        "## Rule Layer",
        "",
        f"- Rule clarity: `{float(rule_audit.get('rule_clarity_score', 0.0)):.2f}`",
        f"- Resolution risk: `{float(rule_audit.get('resolution_risk', 0.0)):.2f}`",
        f"- Rule summary: {rule_audit.get('rule_summary', '')}",
        "",
        "## Market Intel Layer",
        "",
        f"- Comment status: `{market_intel.get('comment_status', '')}`",
        f"- Observed bias: `{float(market_intel.get('observed_bias', 0.0)):.4f}`",
        "",
        "## Risk Flags",
        "",
    ]
    for row in data.get("risk_flags", []):
        lines.append(f"- [{row.get('severity')}] {row.get('risk_type')}: {row.get('summary')}")
    if not data.get("risk_flags"):
        lines.append("- None")
    lines.extend(["", "## Caveat", "", data.get("caveat", "")])
    return "\n".join(lines).strip() + "\n"
