#!/usr/bin/env python3
"""Merge the short-lived v1 journal into v2 and remove the split runtime.

The command is intentionally explicit and idempotent.  It preserves evaluation
and paper-intent lineage in the canonical v2 journals, moves legacy error/data
quality evidence under v2/history, validates key counts, and only then removes
the obsolete v1 directory.  It never touches plans, orders, fills, or exits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_city_probability_shadow.core import migrate_evaluation_row


DEFAULT_ROOT = Path("/Volumes/jrs/weather_data_feed_service_runtime/output")


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def normalized(row: dict[str, Any], kind: str) -> dict[str, Any]:
    result = migrate_evaluation_row(row)
    result["record_kind"] = kind
    return result


def merge_rows(
    current: list[dict[str, Any]], legacy: list[dict[str, Any]], *, key: str, kind: str
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in legacy:
        item = normalized(row, kind)
        identity = item.get(key)
        if not identity:
            raise RuntimeError(f"legacy {kind} row is missing {key}")
        merged[str(identity)] = item
    for row in current:
        identity = row.get(key)
        if not identity:
            raise RuntimeError(f"current {kind} row is missing {key}")
        merged[str(identity)] = row
    return sorted(
        merged.values(),
        key=lambda row: (
            str(row.get("decision_ts_utc") or row.get("ts_utc") or ""),
            str(row.get(key) or ""),
        ),
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-output-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("refusing to mutate runtime without --apply")
    v1 = args.runtime_output_root / "city_probability_shadow_v1"
    v2 = args.runtime_output_root / "city_probability_shadow_v2"
    if not v1.is_dir():
        print(json.dumps({"status": "already_consolidated", "v2": str(v2)}))
        return 0
    if not v2.is_dir():
        raise SystemExit(f"canonical v2 runtime is missing: {v2}")

    source_hashes = {
        f"v1/{name}": digest(v1 / name)
        for name in ("evaluations.jsonl", "paper_intents.jsonl", "errors.jsonl", "data_quality_adjustments.jsonl")
    }
    source_hashes.update({
        f"v2/{name}": digest(v2 / name)
        for name in ("evaluations.jsonl", "paper_intents.jsonl")
    })
    evaluations = merge_rows(
        read_rows(v2 / "evaluations.jsonl"),
        read_rows(v1 / "evaluations.jsonl"),
        key="evaluation_id",
        kind="evaluation",
    )
    intents = merge_rows(
        read_rows(v2 / "paper_intents.jsonl"),
        read_rows(v1 / "paper_intents.jsonl"),
        key="position_key",
        kind="paper_intent",
    )
    write_jsonl(v2 / "evaluations.jsonl", evaluations)
    write_jsonl(v2 / "paper_intents.jsonl", intents)

    history = v2 / "history"
    for name in ("errors.jsonl", "data_quality_adjustments.jsonl"):
        source = v1 / name
        if source.is_file():
            history.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, history / f"legacy_v1_{name}")

    if len({str(row["evaluation_id"]) for row in evaluations}) != len(evaluations):
        raise RuntimeError("evaluation deduplication validation failed")
    if len({str(row["position_key"]) for row in intents}) != len(intents):
        raise RuntimeError("paper-intent deduplication validation failed")
    report = {
        "schema_version": "city_probability_shadow_runtime_consolidation_v2",
        "status": "consolidated",
        "canonical_runtime_dir": str(v2),
        "removed_runtime_dir": str(v1),
        "evaluation_rows": len(evaluations),
        "paper_intent_rows": len(intents),
        "legacy_evaluation_rows": sum(
            row.get("source_schema_version") == "weather_city_probability_shadow_v1"
            for row in evaluations
        ),
        "source_sha256": source_hashes,
        "orders_submitted": 0,
    }
    (v2 / "consolidation_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    shutil.rmtree(v1)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
