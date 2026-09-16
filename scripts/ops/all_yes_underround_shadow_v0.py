#!/usr/bin/env python3
"""Append current all-YES underround candidates to a zero-notional journal.

This is a local shadow recorder. It does not submit, sign, or place CLOB
orders; it only persists would-trade basket rows from the live-prep scanner.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SCAN_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-14-all-yes-underround-live-prep-v0.json"
JOURNAL_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "all_yes_underround_shadow_v0" / "shadow_journal.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-json", default=str(SCAN_JSON_DEFAULT))
    parser.add_argument("--journal", default=str(JOURNAL_DEFAULT))
    parser.add_argument("--max-candidates", type=int, default=2)
    return parser.parse_args()


def read_existing_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("candidate_id"):
                ids.add(str(row["candidate_id"]))
    return ids


def candidate_id(scan: dict[str, Any], candidate: dict[str, Any]) -> str:
    snapshot_ts = scan.get("snapshot_summary", {}).get("snapshot_ts_utc_max")
    return "|".join(
        [
            "all_yes_underround_basket_v0",
            str(snapshot_ts),
            str(candidate.get("event_date")),
            str(candidate.get("city")),
            str(candidate.get("event_slug")),
        ]
    )


def make_entry(scan: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy_id": "all_yes_underround_basket_v0",
        "execution_mode": "zero_notional_shadow",
        "no_order_placed": True,
        "candidate_id": candidate_id(scan, candidate),
        "source_scan": scan.get("snapshot_path"),
        "source_report_generated_at_utc": scan.get("generated_at_utc"),
        "snapshot_ts_utc": scan.get("snapshot_summary", {}).get("snapshot_ts_utc_max"),
        "event_date": candidate.get("event_date"),
        "city": candidate.get("city"),
        "event_slug": candidate.get("event_slug"),
        "legs": candidate.get("legs"),
        "total_yes_ask_cost": candidate.get("total_yes_ask_cost"),
        "underround": candidate.get("underround"),
        "shares_per_leg": candidate.get("min_share_basket", {}).get("shares_per_leg"),
        "basket_cost_usd": candidate.get("min_share_basket", {}).get("cost_usd"),
        "gross_profit_if_complete_usd": candidate.get("min_share_basket", {}).get("gross_profit_usd"),
        "top_of_book_capacity": candidate.get("top_of_book_capacity"),
        "risk_notes": [
            "equal-share all-YES basket requires all legs to fill",
            "partial-fill handling is not implemented in this shadow recorder",
            "any live deployment must use weather-strategy-deploy",
        ],
        "legs_detail": candidate.get("legs_detail", []),
    }


def main() -> None:
    args = parse_args()
    scan_path = Path(args.scan_json)
    journal_path = Path(args.journal)
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    candidates = list(scan.get("paper_shadow_candidates") or [])[: args.max_candidates]
    existing = read_existing_ids(journal_path)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    appended = 0
    with journal_path.open("a", encoding="utf-8") as fh:
        for candidate in candidates:
            entry = make_entry(scan, candidate)
            if entry["candidate_id"] in existing:
                continue
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
            existing.add(entry["candidate_id"])
            appended += 1
    print(
        json.dumps(
            {
                "journal": str(journal_path),
                "source_scan": str(scan_path),
                "candidates": len(candidates),
                "appended": appended,
                "total_known_ids": len(existing),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
