#!/usr/bin/env python3
"""Build label-blind Codex adjudication cards for official clarifications."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.llm.codex_cli_client import run_codex_exec_json  # noqa: E402
from src.strategies.rule_lawyer.clarification_adjudicator import (  # noqa: E402
    PROMPT_VERSION,
    PACKET_ONLY_EXECUTION_ISOLATION,
    PACKET_ONLY_EXECUTION_ISOLATIONS,
    ClarificationDirectionBatch,
    build_clarification_packet,
    clarification_court_prompt,
)
from src.strategies.rule_lawyer.contract_corpus import (  # noqa: E402
    classify_official_update_text,
    extract_ancillary_description,
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def first_signals(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    first: dict[str, dict[str, Any]] = {}
    for row in rows:
        market_id = str(row.get("market_id") or "")
        if market_id and (
            market_id not in first
            or int(row.get("dispute_ts") or 0) < int(first[market_id].get("dispute_ts") or 0)
        ):
            first[market_id] = row
    return first


def selected_packets(
    signals_path: Path,
    clarification_cases_path: Path,
) -> list[dict[str, Any]]:
    signals = first_signals(read_jsonl(signals_path))
    latest_cases = {
        str(row.get("market_id") or ""): row
        for row in read_jsonl(clarification_cases_path)
    }
    packets = []
    for market_id, case in latest_cases.items():
        signal = signals.get(market_id)
        if case.get("status") != "ok" or signal is None:
            continue
        updates = [
            update
            for update in case.get("updates") or []
            if update.get("phase") == "after_dispute_before_settlement"
            and classify_official_update_text(str(update.get("text") or ""))
            != "operational_notice"
        ]
        if not updates:
            continue
        update = min(updates, key=lambda row: int(row.get("timestamp") or 0))
        proposed_binary = signal.get("proposed_binary")
        outcomes = [str(value) for value in signal.get("outcomes") or ["Yes", "No"]]
        proposal_outcome = (
            outcomes[1 - int(proposed_binary)]
            if proposed_binary in (0, 1) and len(outcomes) == 2
            else "Unknown"
        )
        packet = build_clarification_packet(
            case_id=f"market:{market_id}",
            market_id=market_id,
            title=str(signal.get("title") or ""),
            outcomes=outcomes,
            proposal_outcome=proposal_outcome,
            binding_rules=extract_ancillary_description(str(signal.get("ancillary_text") or "")),
            official_update=str(update.get("text") or ""),
            update_timestamp=int(update.get("timestamp") or 0),
        )
        packets.append(packet)
    return sorted(packets, key=lambda row: (int(row["update_timestamp"]), row["case_id"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--signals",
        type=Path,
        default=Path("runtime/dispute_repricing/dispute_repricing_v1/signals.jsonl"),
    )
    parser.add_argument(
        "--clarification-cases",
        type=Path,
        default=Path("runtime/dispute_repricing/official_clarifications_v1/cases.jsonl"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("runtime/dispute_repricing/clarification_judge_v1"),
    )
    parser.add_argument("--codex", action="store_true")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--parallel-batches", type=int, default=3)
    args = parser.parse_args()

    packets = selected_packets(args.signals, args.clarification_cases)
    if args.limit is not None:
        packets = packets[: args.limit]
    args.output_root.mkdir(parents=True, exist_ok=True)
    packet_path = args.output_root / "packets.jsonl"
    with packet_path.open("w") as handle:
        for packet in packets:
            handle.write(json.dumps(packet, ensure_ascii=False, sort_keys=True) + "\n")

    cards_path = args.output_root / "cards.jsonl"
    errors_path = args.output_root / "errors.jsonl"
    existing = {
        (str(row.get("case_id") or ""), str(row.get("input_sha256") or "")): row
        for row in read_jsonl(cards_path)
        if row.get("prompt_version") == PROMPT_VERSION
        and row.get("execution_isolation") in PACKET_ONLY_EXECUTION_ISOLATIONS
    }
    pending = [
        packet
        for packet in packets
        if (packet["case_id"], packet["input_sha256"]) not in existing
    ]
    generated = 0
    failures = 0
    if args.codex:
        batches = [
            pending[start : start + max(1, args.batch_size)]
            for start in range(0, len(pending), max(1, args.batch_size))
        ]

        def run_batch(batch: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            result = run_codex_exec_json(
                prompt=clarification_court_prompt(batch),
                output_model=ClarificationDirectionBatch,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                cwd=ROOT,
                timeout_seconds=300,
                isolated_context=True,
                forbid_tool_calls=True,
            )
            return batch, result

        with ThreadPoolExecutor(max_workers=max(1, args.parallel_batches)) as pool:
            futures = {pool.submit(run_batch, batch): batch for batch in batches}
            for future in as_completed(futures):
                batch = futures[future]
                try:
                    batch, result = future.result()
                    returned = {
                        str(card.get("case_id") or ""): card
                        for card in result.get("cards") or []
                    }
                    if set(returned) != {str(packet["case_id"]) for packet in batch}:
                        raise ValueError("Codex batch case ids did not match input packet ids")
                    for packet in batch:
                        case_id = str(packet["case_id"])
                        row = {
                            "schema_version": "clarification_adjudication_card_artifact_v1",
                            "prompt_version": PROMPT_VERSION,
                            "case_id": case_id,
                            "input_sha256": packet["input_sha256"],
                            "created_at_utc": datetime.now(timezone.utc).isoformat(),
                            "llm_backend": "codex_cli",
                            "llm_model": args.model,
                            "llm_reasoning_effort": args.reasoning_effort,
                            "execution_isolation": PACKET_ONLY_EXECUTION_ISOLATION,
                            "card": returned[case_id],
                        }
                        append_jsonl(cards_path, row)
                        generated += 1
                except Exception as exc:
                    failures += len(batch)
                    append_jsonl(
                        errors_path,
                        {
                            "schema_version": "clarification_adjudication_error_v1",
                            "case_ids": [packet["case_id"] for packet in batch],
                            "created_at_utc": datetime.now(timezone.utc).isoformat(),
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        },
                    )

    summary = {
        "schema_version": "clarification_adjudication_generation_summary_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "prompt_version": PROMPT_VERSION,
        "packets": len(packets),
        "existing_cards_before_run": len(existing),
        "pending_before_run": len(pending),
        "generated": generated,
        "failures": failures,
        "codex_enabled": args.codex,
    }
    (args.output_root / "generation_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
