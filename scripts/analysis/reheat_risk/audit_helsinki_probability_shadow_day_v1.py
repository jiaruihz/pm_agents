#!/usr/bin/env python3
"""Audit one settled Helsinki probability-shadow day from immutable journals."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any


UTC = timezone.utc


def read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def logloss(probability: float, label: float) -> float:
    probability = min(max(probability, 1e-15), 1.0 - 1e-15)
    return -(label * math.log(probability) + (1.0 - label) * math.log(1.0 - probability))


def auc(probabilities: list[float], labels: list[float]) -> float | None:
    positives = [p for p, y in zip(probabilities, labels) if y == 1.0]
    negatives = [p for p, y in zip(probabilities, labels) if y == 0.0]
    if not positives or not negatives:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in positives for n in negatives)
    return wins / (len(positives) * len(negatives))


def probability_metrics(rows: list[dict[str, Any]], winning_bracket: int) -> dict[str, Any]:
    labels = [float(int(row["current_bracket"]) != winning_bracket) for row in rows]
    model = [float(row["model_probability"]) for row in rows]
    market = [float(row["market_probability"]) for row in rows]
    return {
        "rows": len(rows),
        "positive_labels": int(sum(labels)),
        "model_accuracy": mean([float((p >= 0.5) == bool(y)) for p, y in zip(model, labels)]),
        "market_accuracy": mean([float((p >= 0.5) == bool(y)) for p, y in zip(market, labels)]),
        "model_brier": mean([(p - y) ** 2 for p, y in zip(model, labels)]),
        "market_brier": mean([(p - y) ** 2 for p, y in zip(market, labels)]),
        "model_logloss": mean([logloss(p, y) for p, y in zip(model, labels)]),
        "market_logloss": mean([logloss(p, y) for p, y in zip(market, labels)]),
        "model_auc": auc(model, labels),
        "market_auc": auc(market, labels),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal-root", type=Path, required=True)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--winning-bracket", type=int, required=True)
    parser.add_argument("--shares", type=float, default=5.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    evaluations = [
        row for row in read_jsonl(args.journal_root / "evaluations.jsonl")
        if row.get("city") == "Helsinki" and row.get("target_date") == args.target_date
    ]
    intents = [
        row for row in read_jsonl(args.journal_root / "paper_intents.jsonl")
        if row.get("city") == "Helsinki" and row.get("target_date") == args.target_date
    ]
    decision_times = [datetime.fromisoformat(row["decision_ts_utc"]) for row in evaluations]
    first_decision = min(decision_times)
    last_decision = max(decision_times)
    errors = [
        row for row in read_jsonl(args.journal_root / "errors.jsonl")
        if row.get("city") == "Helsinki"
        and row.get("ts_utc")
        and first_decision <= datetime.fromisoformat(row["ts_utc"]) < datetime.fromisoformat(
            args.target_date + "T21:00:00+00:00"
        )
    ]

    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in evaluations:
        by_model[str(row["model_id"])].append(row)
    metrics: dict[str, Any] = {}
    slices: dict[str, Any] = {}
    paper: dict[str, Any] = {}
    intent_details: list[dict[str, Any]] = []
    for model_id, rows in sorted(by_model.items()):
        scored = [
            row for row in rows
            if row.get("evaluation_status") == "scored"
            and row.get("model_probability") is not None
            and row.get("market_probability") is not None
        ]
        metrics[model_id] = probability_metrics(scored, args.winning_bracket)
        model_slices: dict[str, Any] = {}
        for field, getter in (
            ("path_state", lambda row: row.get("lineage", {}).get("path_state")),
            ("bracket", lambda row: str(row.get("current_bracket"))),
        ):
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in scored:
                grouped[str(getter(row))].append(row)
            model_slices[field] = {
                key: probability_metrics(group, args.winning_bracket)
                for key, group in sorted(grouped.items())
            }
        slices[model_id] = model_slices

        model_intents = [row for row in intents if row.get("model_id") == model_id]
        cost = 0.0
        pnl = 0.0
        wins = 0
        for row in model_intents:
            won = int(row["current_bracket"]) != args.winning_bracket
            row_cost = args.shares * float(row["effective_cost_per_share"])
            row_pnl = args.shares * float(won) - row_cost
            cost += row_cost
            pnl += row_pnl
            wins += int(won)
            intent_details.append({
                "model_id": model_id,
                "decision_ts_utc": row["decision_ts_utc"],
                "source_obs_ts_utc": row["source_obs_ts_utc"],
                "bracket": int(row["current_bracket"]),
                "side": row["market_side"],
                "path_state": row["lineage"]["path_state"],
                "model_probability": row["model_probability"],
                "market_probability": row["market_probability"],
                "ask": row["market_entry_price"],
                "effective_cost_per_share": row["effective_cost_per_share"],
                "edge_after_fee": row["edge_after_fee"],
                "won": won,
                "five_share_cost_usd": row_cost,
                "five_share_pnl_usd": row_pnl,
            })
        paper[model_id] = {
            "intents": len(model_intents),
            "wins": wins,
            "win_rate": wins / len(model_intents) if model_intents else None,
            "five_share_cost_usd": cost,
            "five_share_pnl_usd": pnl,
            "five_share_roi": pnl / cost if cost else None,
        }

    unique_checkpoints = {
        (row["source_obs_ts_utc"], int(row["current_bracket"])) for row in evaluations
    }
    scored_checkpoints = {
        (row["source_obs_ts_utc"], int(row["current_bracket"]))
        for row in evaluations if row["evaluation_status"] == "scored"
    }
    result = {
        "schema_version": "helsinki_probability_shadow_day_audit_v1",
        "target_date": args.target_date,
        "winning_bracket": args.winning_bracket,
        "outcome_semantics": "NO wins iff current bracket differs from final exact bracket",
        "signal_funnel": {
            "expression_checkpoints": len(unique_checkpoints),
            "evaluation_rows": len(evaluations),
            "scored_two_sided_checkpoints": len(scored_checkpoints),
            "not_scorable_one_sided_checkpoints": len(unique_checkpoints - scored_checkpoints),
            "would_enter_rows": sum(bool(row.get("would_enter")) for row in evaluations),
            "first_positive_edge_intents": len(intents),
            "models": dict(Counter(str(row["model_id"]) for row in evaluations)),
        },
        "evidence_funnel": {
            "settlement_available": True,
            "same_rows_market_baseline": True,
            "actual_orders": 0,
            "actual_fills": 0,
            "paper_only": True,
            "errors_during_decision_window": sum(
                datetime.fromisoformat(row["ts_utc"]) <= last_decision for row in errors
            ),
            "post_last_decision_stale_book_errors": sum(
                datetime.fromisoformat(row["ts_utc"]) > last_decision for row in errors
            ),
            "target_date_block_bootstrap": "not_applicable_one_target_date",
        },
        "probability_by_model": metrics,
        "slices_by_model": slices,
        "paper_strategy_by_model": paper,
        "paper_intents": sorted(intent_details, key=lambda row: (row["decision_ts_utc"], row["model_id"])),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "slices_by_model"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
