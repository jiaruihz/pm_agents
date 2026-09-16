#!/usr/bin/env python3
"""Frozen OOF and first shadow-day audit for Tokyo routed v7."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


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


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def metrics(rows: list[tuple[float, float]]) -> dict[str, float | int | None]:
    return {
        "rows": len(rows),
        "brier": average([(probability - label) ** 2 for probability, label in rows]),
        "accuracy": average([
            float((probability >= 0.5) == bool(label)) for probability, label in rows
        ]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluations", type=Path, required=True)
    parser.add_argument("--oof", type=Path, required=True)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--winning-bracket", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with gzip.open(args.oof, "rt", encoding="utf-8", newline="") as handle:
        oof = list(csv.DictReader(handle))
    oof_pairs: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in oof:
        label = float(row["y_stay"])
        market = float(row["market_p_stay"])
        residual = float(row["p_offset_physical_ridge1_v6"])
        routed = market if int(row["is_state_entry"]) else residual
        grain = (
            "state_entry" if int(row["is_state_entry"])
            else "transition" if int(row["is_transition"])
            else "ordinary"
        )
        for name, value in (("market", market), ("v6", residual), ("v7", routed)):
            oof_pairs[f"{grain}:{name}"].append((value, label))
            oof_pairs[f"all:{name}"].append((value, label))

    deduped: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(args.evaluations):
        if row.get("city") != "Tokyo" or row.get("target_date") != args.target_date:
            continue
        identity = str(row.get("evaluation_id") or "|".join((
            str(row.get("source_obs_ts_utc")), str(row.get("current_bracket")),
            str(row.get("market_side")), str(row.get("model_id")),
        )))
        deduped.setdefault(identity, row)
    day_rows = sorted(deduped.values(), key=lambda row: (
        str(row.get("source_obs_ts_utc")), str(row.get("market_side"))
    ))
    first_brackets: set[int] = set()
    routed_probabilities: dict[tuple[str, int], float] = {}
    day_pairs: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in day_rows:
        if row.get("market_side") != "YES" or row.get("evaluation_status") != "scored":
            continue
        bracket = int(row["current_bracket"])
        state_entry = bracket not in first_brackets
        first_brackets.add(bracket)
        market = float(row["market_probability"])
        residual = float(row["model_probability"])
        routed = market if state_entry else residual
        label = float(bracket == args.winning_bracket)
        routed_probabilities[(str(row["source_obs_ts_utc"]), bracket)] = routed
        for name, value in (("market", market), ("v6", residual), ("v7", routed)):
            day_pairs[name].append((value, label))

    best_by_checkpoint: list[dict[str, Any]] = []
    by_checkpoint: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in day_rows:
        if row.get("evaluation_status") != "scored" or row.get("market_entry_price") is None:
            continue
        by_checkpoint[(str(row["source_obs_ts_utc"]), int(row["current_bracket"]))].append(row)
    for (source_obs, bracket), rows in sorted(by_checkpoint.items()):
        p_yes = routed_probabilities.get((source_obs, bracket))
        if p_yes is None:
            continue
        candidates = []
        for row in rows:
            probability = p_yes if row["market_side"] == "YES" else 1.0 - p_yes
            ask = float(row["market_entry_price"])
            fee = 0.05 * ask * (1.0 - ask)
            candidates.append({
                "source_obs_ts_utc": source_obs,
                "decision_ts_utc": row["decision_ts_utc"],
                "current_bracket": bracket,
                "side": row["market_side"],
                "probability": probability,
                "ask": ask,
                "edge_after_fee": probability - ask - fee,
            })
        best_by_checkpoint.append(max(candidates, key=lambda item: item["edge_after_fee"]))
    first_candidate = next(
        (row for row in best_by_checkpoint if row["edge_after_fee"] >= 0.02), None
    )
    if first_candidate is not None:
        won = (
            first_candidate["current_bracket"] == args.winning_bracket
            if first_candidate["side"] == "YES"
            else first_candidate["current_bracket"] != args.winning_bracket
        )
        first_candidate["settled_win"] = won

    report = {
        "schema_version": "tokyo_state_entry_routed_market_residual_v7_audit",
        "target": "final exact maximum stays in current bracket",
        "training_or_selection_uses_shadow_day": False,
        "oof_window": sorted({row["target_date"] for row in oof}),
        "oof_target_dates": len({row["target_date"] for row in oof}),
        "oof": {key: metrics(value) for key, value in sorted(oof_pairs.items())},
        "frozen_shadow_day": {
            "target_date": args.target_date,
            "winning_bracket": args.winning_bracket,
            "unique_yes_checkpoints": len(day_pairs["v7"]),
            "market": metrics(day_pairs["market"]),
            "v6": metrics(day_pairs["v6"]),
            "v7": metrics(day_pairs["v7"]),
            "first_city_day_candidate_at_2pct": first_candidate,
            "deployed_paper_intent_emission": False,
            "deployed_orders_submitted": 0,
        },
        "policy": {
            "state_entry_probability": "PIT market midpoint",
            "ordinary_probability": "v6 weather-market residual",
            "side_selection": "best fee-adjusted edge across YES and NO",
            "position_scope": "one city-day-model position",
            "paper_intents": "disabled until independent exact forward evidence",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
