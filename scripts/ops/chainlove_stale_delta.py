#!/usr/bin/env python3
"""Deterministic stale-delta for Chain.Love runs (work order `stale_delta`).

Structured claim resolution against the baseline inventory: each finding lists
claiming PR numbers; every number resolves to open/merged/closed/unknown via
live gh. A seam opens ONLY on merged/closed; `unknown` (query failure) keeps
the seam closed — never treat an unreadable claim as "no claim".
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def gh_pr_state(repo: str, number: int) -> str:
    proc = subprocess.run(
        ["gh", "pr", "view", str(number), "--repo", repo,
         "--json", "state,mergedAt,closedAt"],
        text=True, capture_output=True, check=False, timeout=60,
    )
    if proc.returncode or not (proc.stdout or "").strip():
        return "unknown"
    try:
        info = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return "unknown"
    if info.get("mergedAt"):
        return "merged"
    if info.get("closedAt"):
        return "closed"
    return str(info.get("state", "unknown")).lower()


def claiming_numbers(item: dict[str, Any]) -> list[int]:
    structured = item.get("claiming_prs")
    if isinstance(structured, list):
        numbers = [int(n) for n in structured if isinstance(n, (int, str)) and str(n).isdigit()]
        if numbers:
            return numbers
        if structured == []:
            return []  # explicitly no claim
    text = json.dumps(item.get("collision", "")) + json.dumps(item.get("db_rows", ""))
    return [int(n) for n in re.findall(r"#(\d+)", text)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="Chain-Love/chain-love")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    findings: list[dict[str, Any]] = []

    groups = [
        ("stale", baseline.get("genuinely_stale", [])),
        ("redirect", baseline.get("redirects_confirmed_all_claimed", [])),
    ]
    for kind, items in groups:
        for item in items:
            numbers = claiming_numbers(item)
            states = {n: gh_pr_state(args.repo, n) for n in numbers}
            resolved = [n for n, s in states.items() if s in ("merged", "closed")]
            unknown = [n for n, s in states.items() if s == "unknown"]
            findings.append({
                "kind": kind,
                "name": item.get("name"),
                "claiming_prs": states,
                "seam_open": bool(numbers) and bool(resolved) and not unknown,
                "unknown_queries": unknown,
                "note": ("claim query failed — seam stays CLOSED (unknown != no claim)"
                         if unknown else ""),
            })

    delta = {
        "schema_version": "chainlove_stale_delta_v2",
        "baseline": str(args.baseline),
        "findings": findings,
        "seams_opened": [f for f in findings if f.get("seam_open")],
        "escalate_to_agent": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(delta, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({
        "baseline_findings": len(findings),
        "seams_opened": len(delta["seams_opened"]),
        "unknown_queries": sum(len(f["unknown_queries"]) for f in findings),
        "output": str(args.output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
