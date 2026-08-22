#!/usr/bin/env python3
"""Deterministic stale-delta for Chain.Love runs (work order `stale_delta`).

Diff the prior verified stale inventory against current claim-resolution state:
which claiming PRs merged/closed (seam opens), which still block, and any
mechanically-checkable new events. No agent needed for these judgments; only
genuinely unjudgeable anomalies escalate upstream. Read-only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def gh_pr_state(repo: str, number: int) -> dict[str, Any] | None:
    proc = subprocess.run(
        ["gh", "pr", "view", str(number), "--repo", repo,
         "--json", "state,mergedAt,closedAt"],
        text=True, capture_output=True, check=False, timeout=60,
    )
    if proc.returncode:
        return None
    return json.loads(proc.stdout or "{}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="Chain-Love/chain-love")
    parser.add_argument("--baseline", type=Path, required=True,
                        help="state/stale_inventory.json from the prior sweep")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    pr_cache: dict[int, dict[str, Any] | None] = {}

    def state_of(number: int | None) -> str:
        if number is None:
            return "unknown"
        if number not in pr_cache:
            pr_cache[number] = gh_pr_state(args.repo, number)
        info = pr_cache[number]
        if info is None:
            return "unknown"
        if info.get("mergedAt"):
            return "merged"
        if info.get("closedAt"):
            return "closed"
        return info.get("state", "unknown").lower()

    import re

    def pr_numbers(text: str) -> list[int]:
        return [int(n) for n in re.findall(r"#(\d+)", text)]

    findings: list[dict[str, Any]] = []
    for item in baseline.get("genuinely_stale", []):
        numbers = pr_numbers(json.dumps(item))
        states = {n: state_of(n) for n in numbers}
        resolved = [n for n, s in states.items() if s in ("merged", "closed")]
        findings.append({
            "name": item.get("name"),
            "baseline_collision": item.get("collision"),
            "claiming_prs": states,
            "seam_open": bool(resolved),
            "actionable_now": bool(resolved),
        })
    for item in baseline.get("redirects_confirmed_all_claimed", []):
        numbers = pr_numbers(json.dumps(item))
        states = {n: state_of(n) for n in numbers}
        resolved = [n for n, s in states.items() if s in ("merged", "closed")]
        findings.append({
            "name": item.get("name"),
            "redirect": f"{item.get('from')} -> {item.get('to')}",
            "claiming_prs": states,
            "seam_open": bool(resolved),
        })

    delta = {
        "schema_version": "chainlove_stale_delta_v1",
        "baseline": str(args.baseline),
        "findings": findings,
        "seams_opened": [f for f in findings if f.get("seam_open")],
        "escalate_to_agent": [],  # deterministic-only by design
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(delta, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({
        "baseline_findings": len(findings),
        "seams_opened": len(delta["seams_opened"]),
        "output": str(args.output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
