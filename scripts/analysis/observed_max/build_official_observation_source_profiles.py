#!/usr/bin/env python3
"""Build the runtime-facing official observation source profile sidecar."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.official_observation_feed.source_registry import (
    DEFAULT_RESEARCH_REGISTRY_JSON,
    DEFAULT_SOURCE_PROFILES_JSON,
    source_profile_from_registry_row,
)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def as_profile_row(row: dict[str, Any]) -> dict[str, Any]:
    profile = source_profile_from_registry_row(row)
    out = asdict(profile)
    out["fallback_sources"] = list(profile.fallback_sources)
    out["live_eligible"] = profile.live_eligible
    out["source_profile_note"] = source_profile_note(profile.settlement_source_class)
    return out


def source_profile_note(settlement_source_class: str) -> str:
    if settlement_source_class == "default_wu_station_by_rules":
        return "live-capable METAR mirror of market rules station"
    if settlement_source_class == "official_station_diff_confirmed":
        return "live-capable METAR mirror of confirmed official station; rules recheck required"
    if settlement_source_class == "default_source_watchlist":
        return "shadow-capable source, not live eligible until settlement watch clears"
    if settlement_source_class == "special_source_confirmed":
        return "special source requires dedicated realtime client before live eligibility"
    if settlement_source_class == "non_wu_source_by_rules":
        return "non-WU source requires dedicated realtime client before live eligibility"
    if settlement_source_class == "blocked_unresolved_settlement_basis":
        return "blocked until settlement basis mismatch is explained"
    if settlement_source_class == "no_recent_market_or_unknown_rules":
        return "blocked until market rules identify the official source"
    return "unknown source class; blocked by missing primary source unless explicitly implemented"


def load_research_registry(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("registry")
    if not isinstance(rows, list):
        raise ValueError(f"registry rows missing in {path}")
    return [row for row in rows if isinstance(row, dict)]


def build_payload(input_path: Path) -> dict[str, Any]:
    rows = sorted((as_profile_row(row) for row in load_research_registry(input_path)), key=lambda row: row["city"])
    class_counts: dict[str, int] = {}
    live_counts = {"live_eligible": 0, "not_live_eligible": 0}
    for row in rows:
        cls = row["settlement_source_class"]
        class_counts[cls] = class_counts.get(cls, 0) + 1
        live_counts["live_eligible" if row["live_eligible"] else "not_live_eligible"] += 1
    return {
        "profile_schema_version": 1,
        "generated_at_utc": now_utc(),
        "generated_from": str(input_path.resolve().relative_to(ROOT)),
        "source_profiles": rows,
        "summary": {
            "cities": len(rows),
            "class_counts": dict(sorted(class_counts.items())),
            **live_counts,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(DEFAULT_RESEARCH_REGISTRY_JSON))
    parser.add_argument("--output", default=str(DEFAULT_SOURCE_PROFILES_JSON))
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    payload = build_payload(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output_path), "summary": payload["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
