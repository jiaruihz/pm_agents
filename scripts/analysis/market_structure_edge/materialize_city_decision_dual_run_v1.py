#!/usr/bin/env python3
"""Materialize legacy city journals through the Phase-3 decision dual-write sink."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from weather_city_runtime import DecisionContractJournalSink  # noqa: E402
from weather_data_feed.information_events import canonical_json_hash  # noqa: E402


def _validate_output_dir(path: Path) -> Path:
    resolved = path.resolve()
    allowed_roots = [
        Path("/tmp").resolve(),
        (ROOT / "runtime/research").resolve(),
        (ROOT / "research_outputs").resolve(),
    ]
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise ValueError(
            "dual-run materialization may only write /tmp, runtime/research, "
            "or research_outputs"
        )
    if resolved.exists() and any(resolved.iterdir()):
        raise ValueError(f"output directory must be empty: {resolved}")
    return resolved


def _rows(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
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
                yield row


def _line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-evaluations", action="append", type=Path, default=[])
    parser.add_argument("--legacy-paper-intents", action="append", type=Path, default=[])
    parser.add_argument("--legacy-checkpoint-blockers", action="append", type=Path, default=[])
    parser.add_argument("--city", action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = _validate_output_dir(args.output_dir)
    sink = DecisionContractJournalSink(output_dir, cities=args.city)
    source_counts: Counter[str] = Counter()
    result_counts: Counter[str] = Counter()

    for row in _rows(args.legacy_evaluations):
        if row.get("city") not in args.city:
            continue
        source_counts["evaluations"] += 1
        result_counts.update(sink.record_evaluation(row).to_dict())
    for row in _rows(args.legacy_paper_intents):
        if row.get("city") not in args.city:
            continue
        source_counts["paper_intents"] += 1
        result_counts.update(sink.record_paper_intent(row).to_dict())
    for row in _rows(args.legacy_checkpoint_blockers):
        if row.get("city") not in args.city:
            continue
        source_counts["checkpoint_blockers"] += 1
        result_counts.update(sink.record_checkpoint_blocker(row).to_dict())

    summary = {
        "schema_version": "weather_city_decision_dual_run_materialization_v1",
        "cities": sorted(set(args.city)),
        "source_rows": dict(sorted(source_counts.items())),
        "sink_results": dict(sorted(result_counts.items())),
        "output_rows": {
            "decision_bundles": _line_count(sink.bundle_path),
            "trade_intents": _line_count(sink.intent_path),
            "checkpoint_blockers": _line_count(sink.blocker_path),
            "conversion_errors": _line_count(sink.error_path),
        },
        "orders_submitted": 0,
        "notional_usd": 0.0,
    }
    summary["summary_hash"] = canonical_json_hash(summary)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "materialization_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
