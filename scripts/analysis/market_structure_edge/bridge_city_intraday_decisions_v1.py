#!/usr/bin/env python3
"""Bridge legacy/vNext city decisions into an isolated canonical candidate DB."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from weather_city_runtime import (  # noqa: E402
    DecisionBundle,
    ModelOutput,
    SignalCandidate,
    TemporaryCanonicalBridge,
    TradeIntent,
    legacy_bundle_from_evaluation,
    legacy_trade_intent_from_paper_intent,
)
from weather_data_feed.information_events import canonical_json_hash  # noqa: E402


def _rows(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
                if not isinstance(value, dict):
                    raise ValueError(f"JSONL row must be an object at {path}:{line_number}")
                yield value


def _vnext_bundle(row: Mapping[str, Any]) -> DecisionBundle:
    required = {
        "information_event",
        "state_checkpoint",
        "model_output",
        "signal_candidate",
    }
    missing = sorted(required - set(row))
    if missing:
        raise ValueError(f"vNext bundle missing fields: {missing}")
    return DecisionBundle(
        information_event=dict(row["information_event"]),
        state_checkpoint=dict(row["state_checkpoint"]),
        model_output=ModelOutput.from_dict(row["model_output"]),
        signal_candidate=SignalCandidate.from_dict(row["signal_candidate"]),
    )


def _unique(values: Iterable[Any], identity_field: str) -> list[Any]:
    output: dict[str, Any] = {}
    for value in values:
        row = value.to_dict()
        identity = str(row[identity_field])
        previous = output.get(identity)
        if previous is not None and canonical_json_hash(previous.to_dict()) != canonical_json_hash(row):
            raise ValueError(f"conflicting {identity_field}: {identity}")
        output[identity] = value
    return [output[key] for key in sorted(output)]


def _unique_candidates(values: Iterable[SignalCandidate]) -> list[SignalCandidate]:
    output: dict[str, SignalCandidate] = {}
    for value in values:
        previous = output.get(value.candidate_id)
        if previous is None:
            output[value.candidate_id] = value
            continue
        left = {**previous.to_dict(), "selected": False}
        right = {**value.to_dict(), "selected": False}
        if canonical_json_hash(left) != canonical_json_hash(right):
            raise ValueError(f"conflicting candidate_id: {value.candidate_id}")
        output[value.candidate_id] = replace(
            previous, selected=previous.selected or value.selected
        )
    return [output[key] for key in sorted(output)]


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-evaluations", action="append", type=Path, default=[])
    parser.add_argument("--legacy-paper-intents", action="append", type=Path, default=[])
    parser.add_argument("--vnext-bundles", action="append", type=Path, default=[])
    parser.add_argument("--vnext-intents", action="append", type=Path, default=[])
    parser.add_argument(
        "--city", action="append", default=[],
        help="optional city filter for legacy rows and vNext bundles",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not any(
        (
            args.legacy_evaluations,
            args.legacy_paper_intents,
            args.vnext_bundles,
            args.vnext_intents,
        )
    ):
        raise ValueError("at least one legacy or vNext input is required")
    bridge = TemporaryCanonicalBridge(args.output_dir / "canonical_weather.db")
    city_filter = set(args.city)
    legacy_evaluations = [
        row for row in _rows(args.legacy_evaluations)
        if not city_filter or row.get("city") in city_filter
    ]
    legacy_paper_intents = [
        row for row in _rows(args.legacy_paper_intents)
        if not city_filter or row.get("city") in city_filter
    ]
    vnext_bundle_rows = [
        row for row in _rows(args.vnext_bundles)
        if not city_filter
        or (row.get("signal_candidate") or {}).get("city") in city_filter
    ]
    vnext_intent_rows = list(_rows(args.vnext_intents))
    bundles = [
        *(legacy_bundle_from_evaluation(row) for row in legacy_evaluations),
        *(legacy_bundle_from_evaluation(row) for row in legacy_paper_intents),
        *(_vnext_bundle(row) for row in vnext_bundle_rows),
    ]
    intents = []
    intent_blockers = []
    for row in legacy_paper_intents:
        try:
            intents.append(legacy_trade_intent_from_paper_intent(row))
        except ValueError as exc:
            intent_blockers.append(
                {
                    "schema_version": "weather_city_trade_intent_blocker_v1",
                    "legacy_evaluation_id": row.get("evaluation_id"),
                    "position_key": row.get("position_key"),
                    "blocker_reason": str(exc),
                    "row_hash": canonical_json_hash(row),
                }
            )
    intents.extend(TradeIntent.from_dict(row) for row in vnext_intent_rows)
    canonical = bridge.append(bundles)
    unique_outputs = _unique(
        (bundle.model_output for bundle in bundles), "output_id"
    )
    unique_candidates = _unique_candidates(
        bundle.signal_candidate for bundle in bundles
    )
    unique_intents = _unique(intents, "intent_id")
    candidates_by_id = {
        value.candidate_id: value for value in unique_candidates
    }
    for intent in unique_intents:
        candidate = candidates_by_id.get(intent.candidate_id)
        if candidate is None:
            raise ValueError(
                f"TradeIntent references missing candidate: {intent.candidate_id}"
            )
        if not candidate.selected:
            raise ValueError(
                f"TradeIntent references unselected candidate: {intent.candidate_id}"
            )
        if (
            intent.condition_id != candidate.condition_id
            or intent.token_id != candidate.token_id
        ):
            raise ValueError("TradeIntent market identity differs from candidate")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        args.output_dir / "model_outputs.jsonl",
        (value.to_dict() for value in unique_outputs),
    )
    _write_jsonl(
        args.output_dir / "signal_candidates.jsonl",
        (value.to_dict() for value in unique_candidates),
    )
    _write_jsonl(
        args.output_dir / "trade_intents.jsonl",
        (value.to_dict() for value in unique_intents),
    )
    _write_jsonl(
        args.output_dir / "trade_intent_blockers.jsonl",
        intent_blockers,
    )
    summary = {
        "schema_version": "weather_city_decision_bridge_summary_v1",
        "input_modes": sorted(
            mode
            for mode, present in {
                "legacy": bool(legacy_evaluations or legacy_paper_intents),
                "vnext": bool(vnext_bundle_rows or vnext_intent_rows),
            }.items()
            if present
        ),
        "input_rows": {
            "legacy_evaluations": len(legacy_evaluations),
            "legacy_paper_intents": len(legacy_paper_intents),
            "vnext_bundles": len(vnext_bundle_rows),
            "vnext_intents": len(vnext_intent_rows),
        },
        "outputs": {
            "model_outputs": len(unique_outputs),
            "signal_candidates": len(unique_candidates),
            "trade_intents": len(unique_intents),
            "trade_intent_blockers": len(intent_blockers),
            "candidate_status": dict(
                sorted(Counter(value.candidate_status for value in unique_candidates).items())
            ),
            "candidate_blockers": dict(
                sorted(
                    Counter(
                        value.blocker_reason
                        for value in unique_candidates
                        if value.blocker_reason
                    ).items()
                )
            ),
            "selected": sum(value.selected for value in unique_candidates),
        },
        "canonical_reconciliation": canonical,
        **bridge.candidate_funnels(),
    }
    summary["summary_hash"] = canonical_json_hash(summary)
    (args.output_dir / "bridge_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
