#!/usr/bin/env python3
"""Materialize WCIR intents at the existing shared-execution boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.execution.wcir import (
    WCIRExecutionCompatibilityError,
    build_wcir_execution_handoff,
)
from weather_city_runtime.contracts import SignalCandidate, TradeIntent


def _rows(path: Path) -> Iterable[Mapping[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if isinstance(row, dict):
                    yield row


def materialize(
    *,
    bundle_path: Path,
    intent_path: Path,
    output_path: Path,
    strategy_instance: str,
    config_id: str,
) -> dict[str, Any]:
    candidates = {
        str(row["signal_candidate"]["candidate_id"]): SignalCandidate.from_dict(
            row["signal_candidate"]
        )
        for row in _rows(bundle_path)
        if isinstance(row.get("signal_candidate"), dict)
    }
    existing: set[str] = set()
    if output_path.is_file():
        existing = {
            str(row.get("source_intent_id"))
            for row in _rows(output_path)
            if row.get("source_intent_id")
        }
    summary = {
        "input_intents": 0,
        "written": 0,
        "deduped": 0,
        "record_only": 0,
        "ready_for_paper_runtime": 0,
        "blocked": 0,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as output:
        for row in _rows(intent_path):
            summary["input_intents"] += 1
            intent = TradeIntent.from_dict(row)
            if intent.intent_id in existing:
                summary["deduped"] += 1
                continue
            candidate = candidates.get(intent.candidate_id)
            try:
                if candidate is None:
                    raise WCIRExecutionCompatibilityError(
                        "source candidate not found in decision bundles"
                    )
                handoff = build_wcir_execution_handoff(
                    trade_intent=intent,
                    signal_candidate=candidate,
                    strategy_instance=strategy_instance,
                    config_id=config_id,
                )
                payload = handoff.to_json()
            except (ValueError, WCIRExecutionCompatibilityError) as exc:
                payload = {
                    "schema_version": "weather_wcir_execution_handoff_v1",
                    "source_intent_id": intent.intent_id,
                    "source_candidate_id": intent.candidate_id,
                    "status": "blocked",
                    "reason": str(exc),
                    "execution_intent": None,
                }
            output.write(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            )
            existing.add(intent.intent_id)
            summary["written"] += 1
            summary[payload["status"]] += 1
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundles", type=Path, required=True)
    parser.add_argument("--intents", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--strategy-instance", required=True)
    parser.add_argument("--config-id", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            materialize(
                bundle_path=args.bundles,
                intent_path=args.intents,
                output_path=args.output,
                strategy_instance=args.strategy_instance,
                config_id=args.config_id,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
