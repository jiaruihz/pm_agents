from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from src.strategies.rule_lawyer.services.profile_audit import audit_profile
from src.strategies.rule_lawyer.services.reporting import build_profile_report, default_output_dir, write_json, write_text


def run_profile_audit_workflow(
    target: str,
    fetch_all: bool = False,
    max_trades: int = 800,
    resolve_trade_outcomes: bool = False,
    max_resolve_markets: int = 80,
    focus: str = "all",
    report_style: str = "brief",
    out_file: str = "",
    raw_dir: str = "",
) -> Dict[str, Any]:
    summary = audit_profile(
        target=target,
        fetch_all=fetch_all,
        max_trades=max_trades,
        resolve_trade_outcomes=resolve_trade_outcomes,
        max_resolve_markets=max_resolve_markets,
    )
    if out_file:
        summary_path = Path(out_file)
        output_dir = summary_path.parent
    else:
        output_dir = default_output_dir("runtime/profile_audits", target)
        summary_path = output_dir / "summary.json"
    report_path = output_dir / "report.md"
    write_json(summary_path, summary.to_dict())
    write_text(report_path, build_profile_report(summary.to_dict(), focus=focus, report_style=report_style))
    if fetch_all or raw_dir:
        raw_path = Path(raw_dir) if raw_dir else output_dir / "raw"
        raw_path.mkdir(parents=True, exist_ok=True)
        for name, payload in summary.raw_payloads.items():
            write_json(raw_path / f"{name}.json", payload if isinstance(payload, dict) else {"items": payload})
    return {
        "summary": summary.to_dict(),
        "summary_path": str(summary_path),
        "report_path": str(report_path),
        "output_dir": str(output_dir),
    }
