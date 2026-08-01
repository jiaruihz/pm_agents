#!/usr/bin/env python3
"""Replay one Tokyo shadow day through the deployed adapter without orders."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

from src.strategies.weather_city_probability_shadow import tokyo as tokyo_module
from src.strategies.weather_city_probability_shadow.core import (
    InputNotReady,
    migrate_evaluation_row,
    resolve_journal_catalog,
)


UTC = timezone.utc


def parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--winning-bracket", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shares", type=float, default=5.0)
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    journal_catalog = resolve_journal_catalog(config)
    profile = next(
        row
        for row in config["profiles"]
        if row.get("city") == "Tokyo" and row.get("enabled", True)
    )
    book_path = Path(profile["book_dir"]) / f"{args.target_date}.jsonl"
    raw = [
        row
        for row in read_jsonl(book_path)
        if row.get("city") == "Tokyo"
        and row.get("source") == "jma_amedas"
        and row.get("outcome") == "no"
        and row.get("book_status") == "ok"
    ]
    cycles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in raw:
        key = str(
            row.get("capture_cycle_id")
            or "|".join(
                (
                    str(row.get("source_obs_ts_utc")),
                    str(row.get("reference_market_value")),
                    str(row.get("book_fetched_at_utc"))[:16],
                )
            )
        )
        cycles[key].append(row)
    ordered = sorted(
        cycles.values(),
        key=lambda rows: max(parse_ts(str(row["book_fetched_at_utc"])) for row in rows),
    )

    original_capture = tokyo_module._latest_book_capture
    original_previous = tokyo_module._previous_weather_probability
    previous: dict[tuple[str, int], list[tuple[datetime, float]]] = defaultdict(list)
    active_cycle: list[dict[str, Any]] = []

    def capture_override(_: dict[str, Any]) -> list[dict[str, Any]]:
        return active_cycle

    def previous_override(
        _paths: list[Path], target_date: str, bracket: int, source_obs: datetime
    ) -> float | None:
        eligible = [
            item
            for item in previous[(target_date, bracket)]
            if item[0] < source_obs
        ]
        return max(eligible, default=(None, None), key=lambda item: item[0])[1]

    tokyo_module._latest_book_capture = capture_override
    tokyo_module._previous_weather_probability = previous_override
    adapter = tokyo_module.TokyoMarketAnchorAdapter()
    evaluations: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str, str]] = set()
    first_positions: set[tuple[str, int, str]] = set()
    intents: list[dict[str, Any]] = []
    try:
        for cycle in ordered:
            active_cycle = cycle
            decision = max(parse_ts(str(row["book_fetched_at_utc"])) for row in cycle)
            try:
                scores = adapter.score(profile, decision)
            except InputNotReady as exc:
                blockers.append(
                    {
                        "decision_ts_utc": decision.isoformat(),
                        "reason": exc.reason,
                        "details": exc.details,
                    }
                )
                continue
            except Exception as exc:
                errors.append(
                    {
                        "decision_ts_utc": decision.isoformat(),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            for score in scores:
                key = (
                    score.source_obs_ts_utc,
                    score.current_bracket,
                    score.market_side,
                    score.model_id,
                )
                if key in seen:
                    continue
                seen.add(key)
                fee = (
                    0.05 * score.market_entry_price * (1.0 - score.market_entry_price)
                    if score.market_entry_price is not None
                    else None
                )
                effective_cost = (
                    score.market_entry_price + fee
                    if score.market_entry_price is not None and fee is not None
                    else None
                )
                edge = (
                    score.model_probability - effective_cost
                    if score.evaluation_status == "scored"
                    and score.model_probability is not None
                    and effective_cost is not None
                    else None
                )
                would_enter = edge is not None and edge >= float(profile["edge_threshold"])
                row = {
                    **asdict(score),
                    "fee_per_share": fee,
                    "effective_cost_per_share": effective_cost,
                    "edge_after_fee": edge,
                    "would_enter": would_enter,
                }
                evaluations.append(row)
                if (
                    score.market_side == "YES"
                    and score.lineage.get("weather_probability_stay") is not None
                ):
                    previous[(score.target_date, score.current_bracket)].append(
                        (parse_ts(score.source_obs_ts_utc), float(score.lineage["weather_probability_stay"]))
                    )
                position_key = (score.target_date, score.current_bracket, score.model_id)
                if would_enter and position_key not in first_positions:
                    first_positions.add(position_key)
                    won = (
                        args.winning_bracket == score.current_bracket
                        if score.market_side == "YES"
                        else args.winning_bracket != score.current_bracket
                    )
                    pnl_per_share = (1.0 if won else 0.0) - float(effective_cost)
                    intents.append(
                        {
                            **row,
                            "won": won,
                            "shares": args.shares,
                            "cost_usd": args.shares * float(effective_cost),
                            "pnl_usd": args.shares * pnl_per_share,
                        }
                    )
    finally:
        tokyo_module._latest_book_capture = original_capture
        tokyo_module._previous_weather_probability = original_previous

    yes_rows = [
        row
        for row in evaluations
        if row["market_side"] == "YES"
        and row["evaluation_status"] == "scored"
        and row["model_probability"] is not None
        and row["market_probability"] is not None
    ]
    model_correct: list[float] = []
    market_correct: list[float] = []
    model_brier: list[float] = []
    market_brier: list[float] = []
    for row in yes_rows:
        label = float(args.winning_bracket == int(row["current_bracket"]))
        model_p = float(row["model_probability"])
        market_p = float(row["market_probability"])
        model_correct.append(float((model_p >= 0.5) == bool(label)))
        market_correct.append(float((market_p >= 0.5) == bool(label)))
        model_brier.append((model_p - label) ** 2)
        market_brier.append((market_p - label) ** 2)

    intent_cost = sum(float(row["cost_usd"]) for row in intents)
    intent_pnl = sum(float(row["pnl_usd"]) for row in intents)

    actual_evaluations: dict[str, dict[str, Any]] = {}
    for path in journal_catalog["evaluations"]:
        if not path.is_file():
            continue
        for raw_row in read_jsonl(path):
            if raw_row.get("city") != "Tokyo" or raw_row.get("target_date") != args.target_date:
                continue
            row = migrate_evaluation_row(raw_row)
            identity = str(row.get("evaluation_id") or "|".join((
                str(row.get("source_obs_ts_utc")),
                str(row.get("current_bracket")),
                str(row.get("market_side")),
                str(row.get("model_id")),
            )))
            actual_evaluations.setdefault(identity, row)
    actual_yes = [
        row for row in actual_evaluations.values()
        if row.get("market_side") == "YES"
        and row.get("evaluation_status") == "scored"
        and row.get("model_probability") is not None
        and row.get("market_probability") is not None
    ]
    actual_model_brier = [
        (float(row["model_probability"]) - float(int(row["current_bracket"]) == args.winning_bracket)) ** 2
        for row in actual_yes
    ]
    actual_market_brier = [
        (float(row["market_probability"]) - float(int(row["current_bracket"]) == args.winning_bracket)) ** 2
        for row in actual_yes
    ]
    actual_model_accuracy = [
        float((float(row["model_probability"]) >= 0.5) == (int(row["current_bracket"]) == args.winning_bracket))
        for row in actual_yes
    ]
    actual_market_accuracy = [
        float((float(row["market_probability"]) >= 0.5) == (int(row["current_bracket"]) == args.winning_bracket))
        for row in actual_yes
    ]
    actual_intents: dict[str, dict[str, Any]] = {}
    for path in journal_catalog["paper_intents"]:
        if not path.is_file():
            continue
        for row in read_jsonl(path):
            if row.get("city") != "Tokyo" or row.get("target_date") != args.target_date:
                continue
            identity = str(row.get("position_key") or "|".join((
                str(row.get("current_bracket")), str(row.get("market_side")), str(row.get("model_id"))
            )))
            actual_intents.setdefault(identity, row)
    actual_cost = 0.0
    actual_pnl = 0.0
    actual_wins = 0
    for row in actual_intents.values():
        cost = float(row["effective_cost_per_share"]) * args.shares
        won = (
            int(row["current_bracket"]) == args.winning_bracket
            if row["market_side"] == "YES"
            else int(row["current_bracket"]) != args.winning_bracket
        )
        actual_cost += cost
        actual_pnl += (float(won) * args.shares) - cost
        actual_wins += int(won)
    result = {
        "schema_version": "tokyo_probability_shadow_day_audit_v1",
        "target_date": args.target_date,
        "winning_bracket": args.winning_bracket,
        "runtime_config": str(args.config),
        "runtime_adapter_path": str(Path(tokyo_module.__file__).resolve()),
        "actual_forward": {
            "journal_catalog": {
                kind: [str(path) for path in paths]
                for kind, paths in journal_catalog.items()
            },
            "evaluation_rows_yes_no": len(actual_evaluations),
            "unique_scored_checkpoints": len(actual_yes),
            "model_accuracy": mean(actual_model_accuracy),
            "market_accuracy": mean(actual_market_accuracy),
            "model_brier": mean(actual_model_brier),
            "market_brier": mean(actual_market_brier),
            "brier_delta_vs_market": (
                float(mean(actual_model_brier)) - float(mean(actual_market_brier))
                if actual_model_brier else None
            ),
            "paper_intents": len(actual_intents),
            "wins": actual_wins,
            "win_rate": actual_wins / len(actual_intents) if actual_intents else None,
            "five_share_cost_usd": actual_cost,
            "five_share_pnl_usd": actual_pnl,
            "five_share_roi": actual_pnl / actual_cost if actual_cost else None,
        },
        "signal_funnel": {
            "raw_no_book_rows": len(raw),
            "capture_cycles": len(ordered),
            "unique_scored_checkpoints": len(yes_rows),
            "evaluation_rows_yes_no": len(evaluations),
            "first_position_intents": len(intents),
        },
        "evidence_funnel": {
            "blocker_cycles": len(blockers),
            "error_cycles": len(errors),
            "settlement_available": True,
            "actual_orders": 0,
            "actual_fills": 0,
        },
        "probability": {
            "model_accuracy": mean(model_correct),
            "market_accuracy": mean(market_correct),
            "model_brier": mean(model_brier),
            "market_brier": mean(market_brier),
            "brier_delta_vs_market": (
                None
                if not model_brier
                else float(mean(model_brier)) - float(mean(market_brier))
            ),
        },
        "paper_strategy": {
            "intents": len(intents),
            "wins": sum(int(row["won"]) for row in intents),
            "win_rate": mean([float(row["won"]) for row in intents]),
            "five_share_cost_usd": intent_cost,
            "five_share_pnl_usd": intent_pnl,
            "five_share_roi": intent_pnl / intent_cost if intent_cost else None,
        },
        "blockers": blockers,
        "errors": errors,
        "evaluations": evaluations,
        "paper_intents": intents,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {key: value for key, value in result.items() if key not in {"evaluations", "paper_intents", "blockers", "errors"}},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
