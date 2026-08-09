#!/usr/bin/env python3
"""Materialize the shared all-event first-seen/full-ladder research panel."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_model_evaluation.first_seen_event_ladder_panel import (  # noqa: E402
    PANEL_SCHEMA_VERSION,
    load_canonical_ladder_snapshots,
    load_direct_event_snapshots,
    load_source_events,
    materialize_panel,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--event-path", type=Path, action="append", required=True)
    parser.add_argument("--ladder-root", type=Path, required=True)
    parser.add_argument("--books-root", type=Path, required=True)
    parser.add_argument("--direct-capture-root", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    events, event_coverage = load_source_events(
        args.event_path,
        city=args.city,
        source=args.source,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    canonical, canonical_coverage = load_canonical_ladder_snapshots(
        ladder_root=args.ladder_root,
        books_root=args.books_root,
        city=args.city,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    direct = []
    direct_coverage: dict[str, int] = {}
    if args.direct_capture_root:
        direct, direct_coverage = load_direct_event_snapshots(
            args.direct_capture_root,
            city=args.city,
            start_date=args.start_date,
            end_date=args.end_date,
        )
    snapshots = sorted(
        [*canonical, *direct],
        key=lambda item: (item["available_at_utc"], item["snapshot_id"]),
    )
    panel, event_panel, coverage = materialize_panel(events, snapshots)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    panel_path = args.output_dir / "event_rung_panel.csv.gz"
    events_path = args.output_dir / "event_slot_coverage.csv.gz"
    panel.to_csv(panel_path, index=False, compression="gzip")
    event_panel.to_csv(events_path, index=False, compression="gzip")
    output = {
        **coverage,
        "city": args.city,
        "source": args.source,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "source_event_input_coverage": event_coverage,
        "canonical_snapshot_input_coverage": canonical_coverage,
        "direct_snapshot_input_coverage": direct_coverage,
        "input_paths": {
            "events": [str(path) for path in args.event_path],
            "ladder_root": str(args.ladder_root),
            "books_root": str(args.books_root),
            "direct_capture_root": str(args.direct_capture_root) if args.direct_capture_root else None,
        },
        "outputs": {
            "event_rung_panel": str(panel_path),
            "event_slot_coverage": str(events_path),
        },
        "producer": {
            "schema_version": PANEL_SCHEMA_VERSION,
            "source_path": str(Path(__file__).resolve().relative_to(ROOT)),
            "source_sha256": sha256_file(Path(__file__).resolve()),
        },
    }
    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
