#!/usr/bin/env python3
"""Audit current-YES live readiness after forecast-peak scorecard work."""

from __future__ import annotations

import json
import math
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
V14_JSON = ROOT / "docs/analysis/2026-06/2026-06-18-theta-current-yes-forecast-peak-scorecard-v14.json"
TELEMETRY = ROOT / "runtime/weather_edge_v1/remote_pm_agent/theta_current_yes_tiny_live_v1/forward_telemetry.jsonl"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-18-theta-current-yes-live-readiness-v15.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-18-theta-current-yes-live-readiness-v15.md"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: Any) -> str:
    if value is None:
        return "NA"
    try:
        value_f = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(value_f):
        return "NA"
    return f"{100 * value_f:+.1f}%"


def git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return ""


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_telemetry() -> dict[str, Any]:
    if not TELEMETRY.exists():
        return {"exists": False, "rows": 0, "status_counts": {}, "planned_rows": 0}
    rows = [json.loads(line) for line in TELEMETRY.read_text(encoding="utf-8").splitlines() if line.strip()]
    statuses = Counter(str(row.get("decision_status", "")) for row in rows)
    created = [str(row.get("created_at_utc", "")) for row in rows if row.get("created_at_utc")]
    return {
        "exists": True,
        "path": str(TELEMETRY.relative_to(ROOT)),
        "rows": len(rows),
        "status_counts": dict(statuses),
        "planned_rows": statuses.get("planned", 0),
        "first_created_at_utc": min(created) if created else None,
        "last_created_at_utc": max(created) if created else None,
    }


def find_rule(v14: dict[str, Any], name: str) -> dict[str, Any]:
    for row in v14.get("holdout_rule_summary", []):
        if row.get("rule") == name:
            return row
    return {}


def gate_status(v14: dict[str, Any], telemetry: dict[str, Any]) -> dict[str, Any]:
    v9 = find_rule(v14, "v9_fixed_fade_confirmed")
    both_after = find_rule(v14, "v9_fixed_fade_both_peaks_passed")
    peak_forming = find_rule(v14, "gfs_peak_forming_shadow")
    v9_yes_ci = v9.get("yes_roi_ci95") or [None, None]
    v9_delta_ci = v9.get("yes_minus_d1_no_roi_ci95") or [None, None]

    historical_v9_pass = (
        int(v9.get("orders") or 0) >= 30
        and int(v9.get("active_dates") or 0) >= 10
        and v9_yes_ci[0] is not None
        and float(v9_yes_ci[0]) > 0
        and v9_delta_ci[0] is not None
        and float(v9_delta_ci[0]) > 0
    )
    forecast_upgrade_pass = (
        int(both_after.get("orders") or 0) >= 30
        and int(both_after.get("active_dates") or 0) >= 10
        and (both_after.get("yes_roi_ci95") or [None, None])[0] is not None
        and float((both_after.get("yes_roi_ci95") or [0, 0])[0]) > 0
    )
    peak_forming_pass = (
        int(peak_forming.get("orders") or 0) >= 30
        and int(peak_forming.get("active_dates") or 0) >= 10
        and (peak_forming.get("yes_roi_ci95") or [None, None])[0] is not None
        and float((peak_forming.get("yes_roi_ci95") or [0, 0])[0]) > 0
    )
    forward_pass = int(telemetry.get("planned_rows") or 0) >= 20

    return {
        "historical_v9_gate": {
            "status": "PASS" if historical_v9_pass else "FAIL",
            "evidence": {
                "orders": v9.get("orders"),
                "active_dates": v9.get("active_dates"),
                "yes_roi": v9.get("yes_roi"),
                "yes_roi_ci95": v9_yes_ci,
                "yes_minus_d1_no_roi": v9.get("yes_minus_d1_no_roi"),
                "yes_minus_d1_no_roi_ci95": v9_delta_ci,
            },
        },
        "forecast_clock_upgrade_gate": {
            "status": "FAIL" if not forecast_upgrade_pass else "PASS",
            "reason": "after-peak variants are profitable in backfill but below sample gate and not forward-native",
            "evidence": {
                "both_peaks_passed_orders": both_after.get("orders"),
                "both_peaks_passed_active_dates": both_after.get("active_dates"),
                "both_peaks_passed_yes_roi": both_after.get("yes_roi"),
                "peak_forming_orders": peak_forming.get("orders"),
                "peak_forming_yes_roi": peak_forming.get("yes_roi"),
                "peak_forming_yes_roi_ci95": peak_forming.get("yes_roi_ci95"),
                "peak_forming_pass": peak_forming_pass,
            },
        },
        "forward_telemetry_gate": {
            "status": "FAIL" if not forward_pass else "PASS",
            "reason": "need live/shadow forward would-order rows with planned/fresh-book states and later settlement",
            "evidence": telemetry,
        },
        "production_native_peak_fields_gate": {
            "status": "FAIL",
            "reason": "current scorecard uses historical forecast backfill; production snapshots still need native point-in-time peak fields",
        },
    }


def write_markdown(payload: dict[str, Any]) -> None:
    gates = payload["gates"]
    v9 = gates["historical_v9_gate"]["evidence"]
    telem = gates["forward_telemetry_gate"]["evidence"]
    lines = [
        "# Theta Current YES Live Readiness v15",
        "",
        "Status: not_live_ready_for_upgrade / v9 tiny-live telemetry only",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "Target metric: `current_yes_live_readiness` = decide whether forecast peak clock has enough historical, baseline, and forward evidence to promote current-YES beyond tiny-live telemetry.",
        "",
        "## Human Conclusion",
        "",
        "现在的结论很简单：v9 这个“已回落后买当前最高温 YES”的小仓位方向，历史回放仍然过得去；但 forecast peak clock 还不能让我们扩大 live，也不能把 peak-forming 早入场直接上线。",
        "",
        "原因不是 forecast clock 没用，而是证据层还差一层：它已经在历史 backfill 里显示出风险形状，但生产端还没有持续写入原生 forecast peak 字段，也没有足够 forward would-order 样本。",
        "",
        "## Gates",
        "",
        "| gate | status | evidence |",
        "|---|---|---|",
        f"| historical v9 | {gates['historical_v9_gate']['status']} | {int(v9.get('orders') or 0)} orders / {int(v9.get('active_dates') or 0)} days, YES ROI {pct(v9.get('yes_roi'))}, YES-NO {pct(v9.get('yes_minus_d1_no_roi'))} |",
        f"| forecast clock upgrade | {gates['forecast_clock_upgrade_gate']['status']} | profitable subsets are thin and backfilled, not forward-native |",
        f"| forward telemetry | {gates['forward_telemetry_gate']['status']} | {telem.get('rows')} telemetry rows, planned={telem.get('planned_rows')}, statuses={telem.get('status_counts')} |",
        f"| production native peak fields | {gates['production_native_peak_fields_gate']['status']} | historical backfill exists; native snapshot fields still required |",
        "",
        "## Next Work",
        "",
        "1. Restart or otherwise activate the N100 current-YES loop only after explicit user confirmation, so the already-deployed forward telemetry code actually runs in the persistent live process.",
        "2. Make `weather-predict` emit native point-in-time `forecast_peak_*` fields into snapshots, then sync + rebuild fact tables.",
        "3. After at least 20 planned/fresh-book forward rows and settlements, rerun this gate with real forward hit rate and taker ROI.",
        "",
        "## Outputs",
        "",
        f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
        f"- Script: `{Path(__file__).resolve().relative_to(ROOT)}`",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    v14 = load_json(V14_JSON)
    telemetry = load_telemetry()
    payload = {
        "generated_at_utc": now_utc(),
        "git_head": git_head(),
        "inputs": {
            "v14_json": str(V14_JSON.relative_to(ROOT)),
            "forward_telemetry": str(TELEMETRY.relative_to(ROOT)),
        },
        "gates": gate_status(v14, telemetry),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(payload)
    print(json.dumps({"json": str(OUT_JSON), "md": str(OUT_MD)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
