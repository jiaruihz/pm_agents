#!/usr/bin/env python3
"""Evaluate label-blind clarification court cards against later settlements."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from scripts.analysis.dispute_repricing.analyze_clarification_repricing_v1 import (
    FEE_RATE_BY_TOPIC,
    load_market_trades,
)
from src.strategies.rule_lawyer.clarification_adjudicator import (
    PROMPT_VERSION,
    PACKET_ONLY_EXECUTION_ISOLATIONS,
    clarification_card_blockers,
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


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


def date_block_ci(rows: list[dict[str, Any]], field: str, samples: int = 5_000) -> list[float] | None:
    by_date: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        date = datetime.fromtimestamp(int(row["update_timestamp"]), timezone.utc).date().isoformat()
        by_date.setdefault(date, []).append(row)
    dates = sorted(by_date)
    if not dates:
        return None
    rng = random.Random(20260812)
    values = []
    for _ in range(samples):
        replay = [row for _ in dates for row in by_date[rng.choice(dates)]]
        values.append(sum(float(row[field]) for row in replay) / len(replay))
    values.sort()
    return [values[round((len(values) - 1) * q)] for q in (0.025, 0.5, 0.975)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--signals",
        type=Path,
        default=Path("runtime/dispute_repricing/dispute_repricing_v1/signals.jsonl"),
    )
    parser.add_argument(
        "--packets",
        type=Path,
        default=Path("runtime/dispute_repricing/clarification_judge_v1/packets.jsonl"),
    )
    parser.add_argument(
        "--cards",
        type=Path,
        default=Path("runtime/dispute_repricing/clarification_judge_v1/cards.jsonl"),
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path("runtime/dispute_repricing/dispute_repricing_v1/cache"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("runtime/dispute_repricing/clarification_judge_v1"),
    )
    parser.add_argument("--min-confidence", type=float, default=0.95)
    parser.add_argument("--payout-floor", type=float, default=0.95)
    parser.add_argument("--min-policy-edge", type=float, default=0.05)
    parser.add_argument("--max-public-print-lag-seconds", type=int, default=300)
    args = parser.parse_args()

    signals = first_signals(read_jsonl(args.signals))
    packets = {str(row["case_id"]): row for row in read_jsonl(args.packets)}
    cards = {}
    for row in read_jsonl(args.cards):
        if row.get("prompt_version") != PROMPT_VERSION:
            continue
        if row.get("execution_isolation") not in PACKET_ONLY_EXECUTION_ISOLATIONS:
            continue
        key = str(row.get("case_id") or "")
        if key in packets and row.get("input_sha256") == packets[key].get("input_sha256"):
            cards[key] = row

    ledger = []
    for case_id, packet in packets.items():
        market_id = str(packet["market_id"])
        signal = signals.get(market_id, {})
        artifact = cards.get(case_id)
        card = artifact.get("card", {}) if artifact else {}
        settlement_class = str(signal.get("request_settlement_class") or "")
        outcomes = [str(value) for value in signal.get("outcomes") or packet.get("outcomes") or []]
        final_binary = signal.get("final_binary")
        true_outcome = (
            outcomes[1 - int(final_binary)]
            if final_binary in (0, 1) and len(outcomes) == 2
            else None
        )
        decision_index = (
            0
            if card.get("determination") == "Outcome0"
            else 1
            if card.get("determination") == "Outcome1"
            else None
        )
        selected_outcome = outcomes[decision_index] if decision_index in (0, 1) and len(outcomes) == 2 else None
        blockers = (
            clarification_card_blockers(packet, card, min_confidence=args.min_confidence)
            if artifact
            else ["missing_card"]
        )
        price = None
        trade_ts = None
        tokens = [str(value) for value in signal.get("token_ids") or []]
        if decision_index in (0, 1) and len(tokens) == 2:
            settlement_ts = int(signal.get("final_settlement_ts") or 0)
            trades = [
                row
                for row in load_market_trades(args.cache_root, market_id)
                if str(row.get("asset") or "") == tokens[decision_index]
                and row.get("timestamp") is not None
                and int(row["timestamp"]) > int(packet["update_timestamp"])
                and (not settlement_ts or int(row["timestamp"]) <= settlement_ts)
            ]
            trades.sort(key=lambda row: (int(row["timestamp"]), str(row.get("transactionHash") or "")))
            if trades:
                price = float(trades[0]["price"])
                trade_ts = int(trades[0]["timestamp"])
        fee_rate = FEE_RATE_BY_TOPIC.get(str(signal.get("topic") or "other"), 0.05)
        fee = fee_rate * price * (1 - price) if price is not None else None
        confidence = float(card.get("confidence") or 0) if artifact else None
        policy_edge = (
            args.payout_floor - price - fee
            if price is not None and fee is not None
            else None
        )
        correct = selected_outcome == true_outcome if selected_outcome and true_outcome else None
        realized_edge = (1.0 if correct else 0.0) - price - fee if correct is not None and price is not None else None
        update_cluster = hashlib.sha256(str(packet.get("official_update") or "").encode()).hexdigest()[:16]
        print_lag = trade_ts - int(packet["update_timestamp"]) if trade_ts is not None else None
        ledger.append(
            {
                "schema_version": "clarification_judge_evaluation_case_v1",
                "case_id": case_id,
                "market_id": market_id,
                "title": packet.get("title"),
                "update_timestamp": int(packet["update_timestamp"]),
                "update_cluster": update_cluster,
                "settlement_class": settlement_class,
                "proposal_outcome": packet.get("proposal_outcome"),
                "true_outcome": true_outcome,
                "determination": card.get("determination") if artifact else None,
                "selected_outcome": selected_outcome,
                "confidence": confidence,
                "correct": correct,
                "blockers": blockers,
                "adjudication_eligible": not blockers,
                "post_update_public_print": price,
                "post_update_trade_ts": trade_ts,
                "post_update_print_lag_seconds": print_lag,
                "modeled_fee_per_share": fee,
                "conservative_policy_edge_per_share": policy_edge,
                "realized_fee_adjusted_edge_per_share": realized_edge,
                "trade_eligible": not blockers
                and policy_edge is not None
                and policy_edge >= args.min_policy_edge
                and print_lag is not None
                and print_lag <= args.max_public_print_lag_seconds,
                "card": card if artifact else None,
            }
        )

    args.output_root.mkdir(parents=True, exist_ok=True)
    ledger_path = args.output_root / "evaluation_cases.jsonl"
    with ledger_path.open("w") as handle:
        for row in ledger:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    final_market = [row for row in ledger if row["true_outcome"] is not None]
    clear = [row for row in ledger if row["settlement_class"] in {"upheld", "binary_flip"}]
    final_adjudicated = [row for row in final_market if row["adjudication_eligible"]]
    adjudicated = [row for row in clear if row["adjudication_eligible"]]
    trade_rows = [row for row in clear if row["trade_eligible"] and row["realized_fee_adjusted_edge_per_share"] is not None]
    cluster_selected = []
    for cluster in sorted({row["update_cluster"] for row in trade_rows}):
        group = [row for row in trade_rows if row["update_cluster"] == cluster]
        cluster_selected.append(
            max(group, key=lambda row: float(row["conservative_policy_edge_per_share"]))
        )

    confidence_table = {}
    for threshold in (0.90, 0.95, 0.97, 0.98, 0.99):
        selected = []
        for row in clear:
            artifact = cards.get(row["case_id"])
            if not artifact:
                continue
            blockers = clarification_card_blockers(
                packets[row["case_id"]], artifact["card"], min_confidence=threshold
            )
            if not blockers:
                selected.append(row)
        confidence_table[str(threshold)] = {
            "adjudicated": len(selected),
            "coverage_of_clear_binary": len(selected) / len(clear) if clear else None,
            "precision": sum(bool(row["correct"]) for row in selected) / len(selected) if selected else None,
            "update_clusters": len({row["update_cluster"] for row in selected}),
        }

    def mean(rows: list[dict[str, Any]], field: str) -> float | None:
        return sum(float(row[field]) for row in rows) / len(rows) if rows else None

    errors = [
        {
            "case_id": row["case_id"],
            "title": row["title"],
            "determination": row["determination"],
            "selected_outcome": row["selected_outcome"],
            "true_outcome": row["true_outcome"],
            "confidence": row["confidence"],
            "blockers": row["blockers"],
        }
        for row in final_market
        if row["determination"] in {"Outcome0", "Outcome1"} and row["correct"] is False
    ]
    summary = {
        "schema_version": "clarification_judge_evaluation_summary_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_status": "retrospective_development_not_untouched",
        "prompt_version": PROMPT_VERSION,
        "model": next((row.get("llm_model") for row in cards.values()), None),
        "packets": len(packets),
        "cards": len(cards),
        "final_market_cases": len(final_market),
        "final_market_proposal_baseline_accuracy": sum(
            row["proposal_outcome"] == row["true_outcome"] for row in final_market
        )
        / len(final_market)
        if final_market
        else None,
        "final_market_adjudicated": len(final_adjudicated),
        "final_market_adjudication_coverage": len(final_adjudicated) / len(final_market)
        if final_market
        else None,
        "final_market_adjudication_precision": sum(
            bool(row["correct"]) for row in final_adjudicated
        )
        / len(final_adjudicated)
        if final_adjudicated
        else None,
        "clear_binary_cases": len(clear),
        "proposal_baseline_accuracy": sum(row["proposal_outcome"] == row["true_outcome"] for row in clear)
        / len(clear)
        if clear
        else None,
        "adjudicated": len(adjudicated),
        "adjudication_coverage": len(adjudicated) / len(clear) if clear else None,
        "adjudication_precision": sum(bool(row["correct"]) for row in adjudicated) / len(adjudicated)
        if adjudicated
        else None,
        "adjudication_update_clusters": len({row["update_cluster"] for row in adjudicated}),
        "confidence_threshold_table": confidence_table,
        "trade_policy": {
            "min_confidence": args.min_confidence,
            "conservative_payout_floor": args.payout_floor,
            "min_public_print_policy_edge": args.min_policy_edge,
            "max_public_print_lag_seconds": args.max_public_print_lag_seconds,
            "expression": "first post-update selected-outcome public print; diagnostic, not executable ask",
        },
        "trade_rows": len(trade_rows),
        "trade_update_clusters": len({row["update_cluster"] for row in trade_rows}),
        "trade_win_rate": sum(bool(row["correct"]) for row in trade_rows) / len(trade_rows)
        if trade_rows
        else None,
        "mean_trade_fee_adjusted_edge_per_share": mean(
            trade_rows, "realized_fee_adjusted_edge_per_share"
        ),
        "trade_date_block_edge_ci": date_block_ci(
            trade_rows, "realized_fee_adjusted_edge_per_share"
        ),
        "cluster_capped_trade_rows": len(cluster_selected),
        "cluster_capped_win_rate": sum(bool(row["correct"]) for row in cluster_selected)
        / len(cluster_selected)
        if cluster_selected
        else None,
        "cluster_capped_mean_fee_adjusted_edge_per_share": mean(
            cluster_selected, "realized_fee_adjusted_edge_per_share"
        ),
        "cluster_capped_date_block_edge_ci": date_block_ci(
            cluster_selected, "realized_fee_adjusted_edge_per_share"
        ),
        "all_direction_errors_before_blockers": errors,
        "limitations": [
            "Prompt design followed prior manual case review, so historical results are development evidence.",
            "Public prints do not prove executable ask or size at the same timestamp.",
            "LLM confidence is only an adjudication-quality gate; edge uses the separate frozen payout floor.",
            "The 0.95 payout floor is a conservative policy assumption, not an empirically calibrated probability.",
            "Current topic fee rates are fallback estimates, not historical market fee parameters.",
        ],
    }
    (args.output_root / "evaluation_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
