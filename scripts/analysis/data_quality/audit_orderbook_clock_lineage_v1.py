#!/usr/bin/env python3
"""Audit historical order-book clocks without mutating append-only raw data."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
from statistics import median
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.market_book_contract import classify_orderbook_clock


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * probability))
    return ordered[index]


def audit(orderbook_root: Path, snapshot_root: Path) -> dict[str, Any]:
    schemas: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    raw_rows = 0
    first_observed: str | None = None
    last_observed: str | None = None
    for path in sorted(orderbook_root.rglob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                raw_rows += 1
                schemas[str(row.get("schema_version") or "missing")] += 1
                status = classify_orderbook_clock(row)["clock_lineage_status"]
                statuses[str(status)] += 1
                observed = str(
                    row.get("response_received_at_utc")
                    or row.get("fetched_at_utc")
                    or row.get("snapshot_ts_utc")
                    or ""
                )
                if observed:
                    first_observed = min(first_observed, observed) if first_observed else observed
                    last_observed = max(last_observed, observed) if last_observed else observed

    snapshot_count = 0
    durations: list[float] = []
    snapshot_clock_statuses: Counter[str] = Counter()
    for path in sorted(snapshot_root.glob("snapshot_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        snapshot_count += 1
        started = _parse(
            payload.get("collection_started_at_utc") or payload.get("ts_utc")
        )
        published = _parse(
            payload.get("published_at_utc") or payload.get("available_at_utc")
        )
        if started and published:
            durations.append((published - started).total_seconds())
        records = [row for row in payload.get("records") or [] if isinstance(row, dict)]
        exact = bool(records) and all(
            row.get("event_time_pit_scorable") is True
            and row.get("ladder_available_at_utc")
            for row in records
        )
        snapshot_clock_statuses[
            "collector_exact_full_ladder_clock"
            if exact
            else "legacy_or_incomplete_full_ladder_clock"
        ] += 1

    return {
        "schema_version": "weather_orderbook_clock_lineage_audit_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        ),
        "denominator_scope": {
            "orderbook_root": str(orderbook_root),
            "snapshot_root": str(snapshot_root),
            "raw_orderbook_rows": raw_rows,
            "paper_snapshots": snapshot_count,
            "observed_window_start_utc": first_observed,
            "observed_window_end_utc": last_observed,
        },
        "orderbook_schema_counts": dict(sorted(schemas.items())),
        "orderbook_clock_lineage_counts": dict(sorted(statuses.items())),
        "snapshot_clock_lineage_counts": dict(sorted(snapshot_clock_statuses.items())),
        "snapshot_cycle_duration_seconds": {
            "count": len(durations),
            "median": median(durations) if durations else None,
            "p95": _percentile(durations, 0.95),
            "max": max(durations) if durations else None,
        },
        "historical_null_policy": {
            "request_started_at_utc": None,
            "response_received_at_utc": None,
            "parsed_at_utc": None,
            "clock_lineage_status": "legacy_missing_response_clock",
            "event_time_pit_scorable": False,
            "rule": "do_not_infer_from_snapshot_ts_filename_or_legacy_fetched_at",
        },
        "impact": {
            "terminal_probability_use": "retained_when_other_PIT_contracts_pass",
            "event_time_repricing_use": "blocked_without_collector_exact_response_clock",
            "execution_markout_use": "blocked_without_collector_exact_response_clock",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orderbook-root", type=Path, required=True)
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.orderbook_root, args.snapshot_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
