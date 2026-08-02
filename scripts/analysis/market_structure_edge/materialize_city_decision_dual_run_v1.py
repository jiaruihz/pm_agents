#!/usr/bin/env python3
"""Migrate deprecated city journals into the authoritative runtime-v3 contracts."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from weather_city_runtime import DecisionContractJournalSink  # noqa: E402
from weather_data_feed.information_events import canonical_json_hash  # noqa: E402


PRODUCTION_RUNTIME_V3 = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/city_probability_runtime_v3"
).resolve()


def _validate_output_dir(path: Path, *, confirm_production_write: bool = False) -> Path:
    resolved = path.resolve()
    allowed_roots = [
        Path("/tmp").resolve(),
        (ROOT / "runtime/research").resolve(),
        (ROOT / "research_outputs").resolve(),
    ]
    if resolved == PRODUCTION_RUNTIME_V3 and confirm_production_write:
        pass
    elif not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise ValueError(
            "dual-run materialization may only write /tmp, runtime/research, "
            "or research_outputs; the exact production v3 path additionally requires "
            "--confirm-production-write"
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


def _token_outcomes(book_dirs: Iterable[Path]) -> dict[str, str]:
    values: dict[str, str] = {}
    for root in book_dirs:
        for path in sorted(root.rglob("*.jsonl")):
            for row in _rows([path]):
                token_id = str(row.get("token_id") or "")
                outcome = str(row.get("outcome") or "").upper()
                if not token_id or outcome not in {"YES", "NO"}:
                    continue
                previous = values.get(token_id)
                if previous is not None and previous != outcome:
                    raise ValueError(
                        f"token has conflicting raw outcomes: {token_id}"
                    )
                values[token_id] = outcome
    return values


def _enrich_market_outcome(
    row: dict[str, Any], token_outcomes: Mapping[str, str]
) -> tuple[dict[str, Any], bool]:
    market = dict(row.get("market") or {})
    if market.get("outcome"):
        return row, False
    token_id = str(market.get("token_id") or "")
    outcome = token_outcomes.get(token_id)
    if outcome is None:
        return row, False
    enriched = dict(row)
    market["outcome"] = outcome
    market["outcome_lineage"] = "recovered_from_token_matched_book_journal"
    enriched["market"] = market
    return enriched, True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-evaluations", action="append", type=Path, default=[])
    parser.add_argument("--legacy-paper-intents", action="append", type=Path, default=[])
    parser.add_argument("--legacy-checkpoint-blockers", action="append", type=Path, default=[])
    parser.add_argument(
        "--market-book-dir",
        action="append",
        type=Path,
        default=[],
        help="raw book roots used only to recover a missing token outcome by exact token match",
    )
    parser.add_argument("--city", action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--confirm-production-write", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = _validate_output_dir(
        args.output_dir,
        confirm_production_write=args.confirm_production_write,
    )
    sink = DecisionContractJournalSink(output_dir, cities=args.city)
    token_outcomes = _token_outcomes(args.market_book_dir)
    source_counts: Counter[str] = Counter()
    result_counts: Counter[str] = Counter()

    for row in _rows(args.legacy_evaluations):
        if row.get("city") not in args.city:
            continue
        row, recovered = _enrich_market_outcome(row, token_outcomes)
        source_counts["evaluations"] += 1
        source_counts["market_outcomes_recovered"] += int(recovered)
        result_counts.update(sink.record_evaluation(row).to_dict())
    for row in _rows(args.legacy_paper_intents):
        if row.get("city") not in args.city:
            continue
        row, recovered = _enrich_market_outcome(row, token_outcomes)
        source_counts["paper_intents"] += 1
        source_counts["market_outcomes_recovered"] += int(recovered)
        result_counts.update(sink.record_paper_intent(row).to_dict())
    for row in _rows(args.legacy_checkpoint_blockers):
        if row.get("city") not in args.city:
            continue
        source_counts["checkpoint_blockers"] += 1
        result_counts.update(sink.record_checkpoint_blocker(row).to_dict())

    summary = {
        "schema_version": "weather_city_probability_runtime_v3_migration_v1",
        "cities": sorted(set(args.city)),
        "source_rows": dict(sorted(source_counts.items())),
        "sink_results": dict(sorted(result_counts.items())),
        "output_rows": {
            "decision_bundles": _line_count(sink.bundle_path),
            "trade_intents": _line_count(sink.intent_path),
            "checkpoint_blockers": _line_count(sink.blocker_path),
            "trade_intent_blockers": _line_count(sink.intent_blocker_path),
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
