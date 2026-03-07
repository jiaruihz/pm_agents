from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from src.strategies.rule_lawyer.services.market_rule_audit import audit_market_rules
from src.strategies.rule_lawyer.services.reporting import build_rule_audit_report, default_output_dir, write_json, write_text


def run_market_rule_audit_workflow(target_market: str, out_dir: str = "") -> Dict[str, Any]:
    summary = audit_market_rules(target_market)
    output_dir = Path(out_dir) if out_dir else default_output_dir("runtime/market_rule_audits", target_market)
    summary_path = output_dir / "summary.json"
    report_path = output_dir / "report.md"
    snapshot_path = output_dir / "market_snapshot.json"
    write_json(summary_path, summary.to_dict())
    write_text(report_path, build_rule_audit_report(summary.to_dict()))
    write_json(snapshot_path, summary.market)
    return {
        "summary": summary.to_dict(),
        "summary_path": str(summary_path),
        "report_path": str(report_path),
        "snapshot_path": str(snapshot_path),
        "output_dir": str(output_dir),
    }
