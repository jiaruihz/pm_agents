#!/usr/bin/env python3
"""Reproduce the fast-source live-chain impact counts for the 2026-07-14 audit."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.jsonl_partitions import iter_jsonl_lines, jsonl_family_paths


RUNTIME_ROOT = Path(os.environ.get("WEATHER_DATA_FEED_RUNTIME_ROOT", "/Volumes/jrs/weather_data_feed_service_runtime"))


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    paths = jsonl_family_paths(path, allow_missing=True)
    for line in iter_jsonl_lines(paths):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            yield row


def actual_fill_shares(row: dict[str, Any]) -> float:
    if row.get("actual_fill_shares") not in (None, ""):
        return float(row["actual_fill_shares"])
    place = (row.get("exchange_response") or {}).get("place") or {}
    return float(place.get("takingAmount") or row.get("size") or 0.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", default=str(RUNTIME_ROOT / "output/fast_source_prev_no_trial"))
    args = parser.parse_args()
    runtime_dir = Path(args.runtime_dir)
    submitted = [row for row in iter_jsonl(runtime_dir / "orders.jsonl") if row.get("live_submit_status") == "submitted"]
    intended = [float(row.get("desired_shares") or row.get("planned_shares") or row.get("size") or 0.0) for row in submitted]
    actual = [actual_fill_shares(row) for row in submitted]
    stale_candidates = 0
    for row in iter_jsonl(runtime_dir / "opportunities.jsonl"):
        age = row.get("source_age_min")
        if row.get("status") == "cross_candidate" and age not in (None, "") and float(age) > 15.0:
            stale_candidates += 1
    payload = {
        "schema_version": "fast_source_live_chain_audit_v1",
        "runtime_dir": str(runtime_dir),
        "submitted_orders": len(submitted),
        "intended_shares": round(sum(intended), 6),
        "actual_fill_shares": round(sum(actual), 6),
        "orders_over_intended_shares": sum(fill > plan + 1e-9 for plan, fill in zip(intended, actual)),
        "excess_fill_shares": round(sum(fill - plan for plan, fill in zip(intended, actual)), 6),
        "max_fill_to_plan_ratio": round(max((fill / plan for plan, fill in zip(intended, actual) if plan), default=0.0), 6),
        "stale_cross_candidate_rows_over_15m": stale_candidates,
        "stale_submitted_orders_over_15m": sum(
            row.get("source_age_min") not in (None, "") and float(row["source_age_min"]) > 15.0
            for row in submitted
        ),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
