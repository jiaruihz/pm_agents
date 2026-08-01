#!/usr/bin/env python3
"""Materialize an explicit, non-destructive v1->v2 evaluation migration."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_city_probability_shadow.core import (  # noqa: E402
    OUTPUT_SCHEMA_FINGERPRINT,
    OUTPUT_SCHEMA_VERSION,
    migrate_evaluation_row,
)


DEFAULT_RUNTIME = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/city_probability_shadow_v1"
)


def parse_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def iter_jsonl(path: Path):
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def error_class(message: str) -> str:
    if "two-sided quote" in message:
        return "one_sided_market_probability_interval"
    if "No such file or directory" in message:
        return "missing_physical_shard"
    if "missing official observation journal" in message:
        return "missing_official_physical_shard"
    if "bracket mismatch" in message:
        return "anchor_capture_gap"
    if "KeyError" in message or "TypeError" in message:
        return "schema_or_state_shape"
    return "other"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--cutoff", default="2026-08-01T05:48:16Z")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/analysis/2026-08/generated/city_intraday_contract_repair_v1/evaluations_v2_migrated.jsonl",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "docs/analysis/2026-08/generated/city_intraday_contract_repair_v1/migration_summary.json",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    cutoff = parse_utc(args.cutoff)
    if cutoff is None:
        raise SystemExit("invalid --cutoff")
    if (args.output.exists() or args.report.exists()) and not args.force:
        raise SystemExit("output exists; pass --force only for this derived migration artifact")

    source = args.runtime_root / "evaluations.jsonl"
    migrated = []
    source_versions: Counter[str] = Counter()
    migration_statuses: Counter[str] = Counter()
    evaluation_statuses: Counter[str] = Counter()
    for row in iter_jsonl(source):
        decision = parse_utc(row.get("decision_ts_utc"))
        if decision is None or decision > cutoff:
            continue
        source_versions[str(row.get("schema_version") or "missing")] += 1
        normalized = migrate_evaluation_row(row)
        migrated.append(normalized)
        migration_statuses[str(normalized.get("migration_status") or "native_v2")] += 1
        evaluation_statuses[str(normalized.get("evaluation_status") or "missing")] += 1

    errors = []
    error_counts: Counter[str] = Counter()
    for row in iter_jsonl(args.runtime_root / "errors.jsonl"):
        timestamp = parse_utc(row.get("ts_utc"))
        if timestamp is None or timestamp > cutoff:
            continue
        errors.append(row)
        error_counts[error_class(str(row.get("error") or ""))] += 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in migrated),
        encoding="utf-8",
    )
    summary = {
        "schema_version": "weather_city_probability_shadow_v1_to_v2_migration_summary_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_cutoff_utc": cutoff.isoformat(),
        "source_path": str(source),
        "output_path": str(args.output),
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "output_schema_fingerprint": OUTPUT_SCHEMA_FINGERPRINT,
        "evaluation_rows": len(migrated),
        "source_schema_versions": dict(source_versions),
        "migration_statuses": dict(migration_statuses),
        "evaluation_statuses": dict(evaluation_statuses),
        "legacy_rows_with_unrecoverable_runtime_identity": sum(
            row.get("runtime_identity") is None for row in migrated
        ),
        "historical_error_poll_rows": len(errors),
        "historical_error_poll_rows_by_repair_class": dict(error_counts),
        "scope": "derived_output_only_source_runtime_read_only",
    }
    args.report.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
