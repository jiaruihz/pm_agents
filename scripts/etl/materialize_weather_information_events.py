#!/usr/bin/env python3
"""Ingest immutable first-seen event headers from data-feed raw captures."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.db.first_seen_schema import apply_first_seen_schema
from weather_dashboard.ingest.information_events import ingest_information_events


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return []
    def rows() -> Iterable[dict[str, Any]]:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    return rows()


def raw_information_events(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Extract only material headers; raw coverage failures stay out of this grain."""
    events: list[dict[str, Any]] = []
    for path in paths:
        files = sorted(path.rglob("*.jsonl")) if path.is_dir() else [path]
        for file_path in files:
            for row in iter_jsonl(file_path):
              if row.get("information_event_id") and (
                  row.get("information_event_status") == "material" or row.get("event_kind") == "forecast_curve"
              ):
                events.append(row)
              taf = row.get("taf")
              if isinstance(taf, dict) and taf.get("information_event_status") == "material":
                  event = taf.get("information_event")
                  if isinstance(event, dict) and event.get("information_event_id"):
                      events.append(event)
    return events


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(ROOT / "runtime/weather.db"))
    parser.add_argument("--source-events", action="append", default=[])
    parser.add_argument("--forecast-curves", action="append", default=[])
    parser.add_argument("--forecast-enrichment", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = [Path(value) for value in [*args.source_events, *args.forecast_curves, *args.forecast_enrichment]]
    events = raw_information_events(paths)
    conn = sqlite3.connect(args.db)
    try:
        apply_schema_canonical(conn)
        apply_first_seen_schema(conn)
        result = ingest_information_events(conn, events)
    finally:
        conn.close()
    print(json.dumps({"paths": [str(path) for path in paths], "raw_material_events": len(events), **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
