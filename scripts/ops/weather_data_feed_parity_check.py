#!/usr/bin/env python3
"""Validate weather-predict snapshots against weather_data_feed protocol fields."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.snapshot_protocol import REQUIRED_SNAPSHOT_FIELDS, normalize_snapshot_record


def load_snapshot(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"snapshot payload must be an object: {path}")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError(f"snapshot.records must be a list: {path}")
    return payload


def check_snapshot(path: Path) -> dict[str, Any]:
    payload = load_snapshot(path)
    rows = payload["records"]
    missing: Counter[str] = Counter()
    invalid: list[dict[str, Any]] = []
    by_city_dates: dict[str, set[str]] = defaultdict(set)
    local_target_delta: Counter[str] = Counter()
    normalized_rows = 0
    snapshot_ts = payload.get("ts_utc") or payload.get("snapshot_ts_utc")

    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            invalid.append({"idx": idx, "reason": "row_not_object"})
            continue
        for field in REQUIRED_SNAPSHOT_FIELDS:
            if field == "snapshot_ts_utc" and str(snapshot_ts or "").strip():
                continue
            if not str(row.get(field) or "").strip():
                missing[field] += 1
        try:
            normalized = normalize_snapshot_record(row, snapshot_ts_utc=snapshot_ts)
        except Exception as exc:
            invalid.append({"idx": idx, "city": row.get("city"), "target_date": row.get("target_date") or row.get("event_date"), "reason": str(exc)})
            continue
        normalized_rows += 1
        by_city_dates[normalized["city"]].add(normalized["target_date"])
        local = normalized["city_local_date_at_snapshot"]
        target = normalized["target_date"]
        local_target_delta[f"{local}->{target}"] += 1

    status = "ok" if not invalid and not missing else "fail"
    return {
        "status": status,
        "snapshot": str(path),
        "schema_version": payload.get("schema_version"),
        "data_feed_schema_version": payload.get("data_feed_schema_version"),
        "total_records": len(rows),
        "normalized_records": normalized_rows,
        "missing_required_fields": dict(sorted(missing.items())),
        "invalid_count": len(invalid),
        "invalid_examples": invalid[:10],
        "city_count": len(by_city_dates),
        "target_dates_by_city_examples": {city: sorted(dates) for city, dates in sorted(by_city_dates.items())[:10]},
        "local_to_target_date_counts": dict(sorted(local_target_delta.items())),
    }


def latest_snapshot(root: Path) -> Path:
    files = sorted(root.glob("snapshot_*.json"), key=lambda path: path.stat().st_mtime)
    if not files:
        raise FileNotFoundError(f"no snapshot_*.json found under {root}")
    return files[-1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default="")
    parser.add_argument(
        "--snapshot-dir",
        default=str(ROOT / "runtime/weather_edge_v1/market_data/paper_snapshots"),
    )
    args = parser.parse_args()

    path = Path(args.snapshot) if args.snapshot else latest_snapshot(Path(args.snapshot_dir))
    report = check_snapshot(path)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
