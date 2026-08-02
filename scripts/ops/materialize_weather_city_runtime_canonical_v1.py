#!/usr/bin/env python3
"""Incrementally materialize WCIR shadow candidates into canonical facts."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_city_runtime import (  # noqa: E402
    CanonicalCandidateBridge,
    DecisionBundle,
    TemporaryCanonicalBridge,
)
from weather_data_feed.information_events import canonical_json_hash  # noqa: E402


def load_bundles(paths: Iterable[Path]) -> list[DecisionBundle]:
    bundles: list[DecisionBundle] = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
                if not isinstance(row, dict):
                    raise ValueError(f"JSONL row must be an object at {path}:{line_number}")
                bundles.append(DecisionBundle.from_dict(row))
    return bundles


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles", action="append", required=True, type=Path)
    parser.add_argument("--db", type=Path, default=ROOT / "runtime/weather.db")
    parser.add_argument(
        "--expected-db",
        type=Path,
        default=Path("/Volumes/jrs/pm_agents/runtime/weather.db"),
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bundles = load_bundles(args.bundles)
    if not bundles:
        raise ValueError("no decision bundles found")
    if args.apply:
        bridge = CanonicalCandidateBridge(
            args.db, expected_db_path=args.expected_db
        )
        mode = "canonical_incremental_apply"
        result = bridge.append(bundles)
        db_path = bridge.db_path
        funnels = bridge.candidate_funnels()
    else:
        with tempfile.TemporaryDirectory(prefix="wcir-canonical-dry-run-") as tmp:
            bridge = TemporaryCanonicalBridge(Path(tmp) / "weather.db")
            result = bridge.append(bundles)
            funnels = bridge.candidate_funnels()
        mode = "isolated_dry_run"
        db_path = None
    summary: dict[str, Any] = {
        "schema_version": "weather_city_canonical_materialization_v1",
        "mode": mode,
        "input_paths": [str(path.resolve()) for path in args.bundles],
        "input_bundle_rows": len(bundles),
        "cities": dict(sorted(Counter(
            bundle.signal_candidate.city for bundle in bundles
        ).items())),
        "canonical_reconciliation": result,
        **funnels,
        "execution_projection": {
            "plan": "not_created_shadow",
            "order": "not_created_shadow",
            "fill": "not_created_shadow",
            "pnl": "not_computed_without_fill",
        },
    }
    if db_path is not None:
        stat = db_path.stat()
        summary["canonical_db"] = {
            "requested_path": str(args.db),
            "resolved_path": str(db_path),
            "device": stat.st_dev,
            "inode": stat.st_ino,
        }
    summary["summary_hash"] = canonical_json_hash(summary)
    payload = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
