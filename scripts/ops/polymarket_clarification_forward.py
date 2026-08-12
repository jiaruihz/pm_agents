#!/usr/bin/env python3
"""Run the zero-notional official-clarification adjudication worker."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any
import uuid


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.llm.codex_cli_client import run_codex_exec_json  # noqa: E402
from src.platform.storage.jsonl import (  # noqa: E402
    append_jsonl_row,
    rewrite_jsonl_atomic,
    write_json_atomic,
)
from src.strategies.rule_lawyer.clarification_adjudicator import (  # noqa: E402
    PROMPT_VERSION,
    PACKET_ONLY_EXECUTION_ISOLATION,
    PACKET_ONLY_EXECUTION_ISOLATIONS,
    ClarificationDirectionBatch,
    clarification_court_prompt,
)
from src.strategies.rule_lawyer.clarification_forward import (  # noqa: E402
    apply_update_cluster_cap,
    clarification_packets_from_snapshot,
    latest_by_case,
    score_clarification_card,
)
from src.strategies.rule_lawyer.dispute_forward import get_book_with_capture  # noqa: E402


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    append_jsonl_row(path, row)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    args.output_root.mkdir(parents=True, exist_ok=True)
    snapshots = latest_by_case(read_jsonl(args.output_root / "snapshots.jsonl"))
    snapshot_by_case = {str(row["case_id"]): row for row in snapshots}
    packets = [
        packet
        for snapshot in snapshots
        for packet in clarification_packets_from_snapshot(snapshot)
    ]
    packets.sort(key=lambda row: (int(row["update_timestamp"]), row["case_id"]))
    rewrite_jsonl_atomic(args.output_root / "clarification_packets.jsonl", packets)

    cards_path = args.output_root / "clarification_cards.jsonl"
    signals_path = args.output_root / "clarification_signals.jsonl"
    quotes_path = args.output_root / "clarification_quote_snapshots.jsonl"
    errors_path = args.output_root / "clarification_errors.jsonl"
    cards = {
        (str(row.get("case_id") or ""), str(row.get("input_sha256") or "")): row
        for row in read_jsonl(cards_path)
        if row.get("prompt_version") == PROMPT_VERSION
        and row.get("execution_isolation") in PACKET_ONLY_EXECUTION_ISOLATIONS
    }
    pending_all = [
        packet
        for packet in packets
        if (packet["case_id"], packet["input_sha256"]) not in cards
    ]
    pending = pending_all[: max(0, args.max_new_cards)]
    generated = 0
    failures = 0
    if args.codex and pending:
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
                    _, result = future.result()
                    returned = {
                        str(card.get("case_id") or ""): card
                        for card in result.get("cards") or []
                    }
                    if set(returned) != {str(packet["case_id"]) for packet in batch}:
                        raise ValueError("Codex batch case ids did not match packet ids")
                    for packet in batch:
                        artifact = {
                            "schema_version": "clarification_forward_card_v1",
                            "prompt_version": PROMPT_VERSION,
                            "case_id": packet["case_id"],
                            "input_sha256": packet["input_sha256"],
                            "created_at_utc": utc_now(),
                            "llm_backend": "codex_cli",
                            "llm_model": args.model,
                            "llm_reasoning_effort": args.reasoning_effort,
                            "execution_isolation": PACKET_ONLY_EXECUTION_ISOLATION,
                            "card": returned[packet["case_id"]],
                        }
                        append_jsonl(cards_path, artifact)
                        cards[(packet["case_id"], packet["input_sha256"])] = artifact
                        generated += 1
                except Exception as exc:
                    failures += len(batch)
                    append_jsonl(
                        errors_path,
                        {
                            "schema_version": "clarification_forward_error_v1",
                            "case_ids": [packet["case_id"] for packet in batch],
                            "created_at_utc": utc_now(),
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        },
                    )

    existing_signals = read_jsonl(signals_path)
    decided_inputs = {str(row.get("input_sha256") or "") for row in existing_signals}
    occupied_clusters = {
        str(row.get("update_cluster_id") or "")
        for row in existing_signals
        if row.get("eligible_shadow")
    }
    candidates: list[dict[str, Any]] = []
    for packet in packets:
        if packet["input_sha256"] in decided_inputs:
            continue
        artifact = cards.get((packet["case_id"], packet["input_sha256"]))
        if artifact is None:
            continue
        snapshot = deepcopy(snapshot_by_case.get(str(packet["source_snapshot_case_id"])) or {})
        card = artifact["card"]
        index = (
            0
            if card.get("determination") == "Outcome0"
            else 1
            if card.get("determination") == "Outcome1"
            else None
        )
        tokens = [str(value) for value in snapshot.get("tokens") or []]
        if index in (0, 1) and len(tokens) == 2:
            token = tokens[index]
            try:
                raw_book, capture = get_book_with_capture(
                    token,
                    request_batch_capture_id=f"clarification-{uuid.uuid4().hex}",
                )
                snapshot.setdefault("books", {})[token] = raw_book
                snapshot.setdefault("book_captures", {})[token] = capture
                append_jsonl(
                    quotes_path,
                    {
                        "schema_version": "clarification_quote_snapshot_v1",
                        "case_id": packet["source_snapshot_case_id"],
                        "clarification_packet_id": packet["case_id"],
                        "source_snapshot_case_id": packet["source_snapshot_case_id"],
                        "market_id": packet.get("market_id"),
                        "condition_id": snapshot.get("condition_id"),
                        "token_id": token,
                        "input_sha256": packet["input_sha256"],
                        "captured_at_utc": capture["available_at_utc"],
                        "book_capture": capture,
                        "raw_book": raw_book,
                    },
                )
            except Exception as exc:
                append_jsonl(
                    errors_path,
                    {
                        "schema_version": "clarification_forward_quote_error_v1",
                        "case_ids": [packet["case_id"]],
                        "created_at_utc": utc_now(),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
        observed_at_utc = str(
            ((snapshot.get("book_captures") or {}).get(tokens[index]) or {}).get(
                "available_at_utc"
            )
            if index in (0, 1) and len(tokens) == 2
            else ""
        ) or utc_now()
        candidates.append(
            score_clarification_card(
                packet,
                card,
                snapshot,
                observed_at_utc=observed_at_utc,
            )
        )
    apply_update_cluster_cap(candidates, occupied_clusters)
    for candidate in candidates:
        append_jsonl(signals_path, candidate)

    summary = {
        "schema_version": "clarification_forward_run_v1",
        "generated_at_utc": utc_now(),
        "zero_notional": True,
        "prompt_version": PROMPT_VERSION,
        "model": args.model,
        "latest_snapshots": len(snapshots),
        "official_clarification_packets": len(packets),
        "pending_cards_before_run": len(pending_all),
        "cards_selected_this_run": len(pending),
        "cards_deferred_by_cycle_cap": len(pending_all) - len(pending),
        "cards_generated": generated,
        "card_failures": failures,
        "signals_written": len(candidates),
        "eligible_shadow": sum(bool(row.get("eligible_shadow")) for row in candidates),
        "codex_enabled": args.codex,
    }
    write_json_atomic(args.output_root / "clarification_latest.json", summary)
    append_jsonl(args.output_root / "clarification_runs.jsonl", summary)
    return summary


def court_health(summary: dict[str, Any], *, status: str) -> dict[str, Any]:
    return {
        "schema_version": "clarification_court_health_v1",
        "status": status,
        "updated_at_utc": utc_now(),
        "strategy_key": "rule_lawyer.dispute_repricing",
        "execution_mode": "zero_notional_shadow",
        "live_authority": False,
        "actual_notional": 0.0,
        "worker": "official_clarification_court",
        "run": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root", type=Path, default=Path("runtime/dispute_repricing/forward_v1")
    )
    parser.add_argument("--codex", action="store_true")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--parallel-batches", type=int, default=2)
    parser.add_argument("--max-new-cards", type=int, default=8)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=60)
    parser.add_argument("--health-path", type=Path)
    args = parser.parse_args()
    health_path = args.health_path or args.output_root / "clarification_health.json"
    while True:
        write_json_atomic(health_path, court_health({}, status="running"))
        try:
            summary = run_once(args)
        except Exception as exc:
            write_json_atomic(
                health_path,
                court_health(
                    {"error_type": type(exc).__name__, "error": str(exc)},
                    status="error",
                ),
            )
            raise
        status = "degraded" if summary["card_failures"] else "ok"
        write_json_atomic(health_path, court_health(summary, status=status))
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
        if not args.loop:
            break
        time.sleep(max(10, args.interval_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
