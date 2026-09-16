#!/usr/bin/env python3
"""Public-print diagnostic around first post-dispute official clarification."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import random
from statistics import median
from typing import Any

from scripts.analysis.dispute_repricing.scan_official_clarifications_v1 import (
    classify_update_text,
)


FEE_RATE_BY_TOPIC = {
    "sports_esports": 0.05,
    "crypto": 0.07,
    "economics_finance": 0.05,
    "politics": 0.04,
    "awards_media": 0.05,
    "other": 0.05,
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def first_by_market(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    first: dict[str, dict[str, Any]] = {}
    for row in rows:
        market_id = str(row.get("market_id") or "")
        if market_id and (
            market_id not in first
            or int(row.get("dispute_ts") or 0) < int(first[market_id].get("dispute_ts") or 0)
        ):
            first[market_id] = row
    return first


def load_market_trades(cache_root: Path, market_id: str) -> list[dict[str, Any]]:
    base = cache_root / "trades" / f"{market_id}.json"
    if not base.exists():
        return []
    rows = json.loads(base.read_text())
    if not isinstance(rows, list):
        return []
    if len(rows) < 10_000:
        return rows
    windows = sorted((cache_root / "trades_window").glob(f"{market_id}-*.json"))
    merged: list[dict[str, Any]] = []
    for path in windows:
        value = json.loads(path.read_text())
        if isinstance(value, list):
            merged.extend(value)
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in merged:
        key = (
            row.get("transactionHash"),
            row.get("asset"),
            row.get("timestamp"),
            row.get("price"),
            row.get("side"),
            row.get("size"),
        )
        unique[key] = row
    return list(unique.values())


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * q)]


def date_block_ci(
    rows: list[dict[str, Any]],
    samples: int = 5_000,
    field: str = "fee_adjusted_edge_per_share",
) -> list[float] | None:
    by_date: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        date = datetime.fromtimestamp(int(row["update_ts"]), timezone.utc).date().isoformat()
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
    parser = argparse.ArgumentParser()
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
        "--cache-root",
        type=Path,
        default=Path("runtime/dispute_repricing/dispute_repricing_v1/cache"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("runtime/dispute_repricing/clarification_repricing_v1"),
    )
    parser.add_argument(
        "--selection",
        choices=("substantive", "any"),
        default="substantive",
        help="substantive excludes boilerplate dispute/orderbook notices",
    )
    args = parser.parse_args()

    signals = first_by_market(read_jsonl(args.signals))
    clarification_latest = {
        str(row.get("market_id") or ""): row for row in read_jsonl(args.clarification_cases)
    }
    candidates = []
    for market_id, case in clarification_latest.items():
        if case.get("status") != "ok" or market_id not in signals:
            continue
        eligible_updates = [
            update
            for update in case.get("updates") or []
            if update.get("phase") == "after_dispute_before_settlement"
            and (
                args.selection == "any"
                or classify_update_text(str(update.get("text") or ""))
                != "operational_notice"
            )
        ]
        if not eligible_updates:
            continue
        update = min(eligible_updates, key=lambda row: int(row.get("timestamp") or 0))
        signal = signals[market_id]
        candidates.append((market_id, case, signal, update))

    rows = []
    for market_id, case, signal, update in candidates:
        token = str(signal.get("reverse_token_id") or "")
        update_ts = int(update.get("timestamp") or 0)
        settlement_ts = int(signal.get("final_settlement_ts") or 0)
        trades = [
            row
            for row in load_market_trades(args.cache_root, market_id)
            if str(row.get("asset") or "") == token and row.get("timestamp") is not None
        ]
        trades.sort(key=lambda row: (int(row["timestamp"]), str(row.get("transactionHash") or "")))
        before = [row for row in trades if int(row["timestamp"]) <= update_ts]
        after = [
            row
            for row in trades
            if int(row["timestamp"]) > update_ts
            and (not settlement_ts or int(row["timestamp"]) <= settlement_ts)
        ]
        entry = after[0] if after else None
        price = float(entry["price"]) if entry else None
        token_ids = [str(value) for value in signal.get("token_ids") or []]
        reverse_index = signal.get("reverse_outcome_index")
        winner_index = (
            int(reverse_index)
            if signal.get("request_settlement_class") == "binary_flip"
            else 1 - int(reverse_index)
            if signal.get("request_settlement_class") == "upheld" and reverse_index in (0, 1)
            else None
        )
        winner_token = (
            token_ids[winner_index]
            if winner_index in (0, 1) and len(token_ids) == 2
            else ""
        )
        winner_trades = [
            row
            for row in load_market_trades(args.cache_root, market_id)
            if str(row.get("asset") or "") == winner_token
            and row.get("timestamp") is not None
            and int(row["timestamp"]) > update_ts
            and (not settlement_ts or int(row["timestamp"]) <= settlement_ts)
        ]
        winner_trades.sort(
            key=lambda row: (int(row["timestamp"]), str(row.get("transactionHash") or ""))
        )
        winner_entry = winner_trades[0] if winner_trades else None
        winner_price = float(winner_entry["price"]) if winner_entry else None
        payout = (
            1.0
            if signal.get("request_settlement_class") == "binary_flip"
            else 0.0
            if signal.get("request_settlement_class") == "upheld"
            else None
        )
        topic = str(signal.get("topic") or "other")
        fee_rate = FEE_RATE_BY_TOPIC.get(topic, 0.05)
        fee = fee_rate * price * (1 - price) if price is not None else None
        winner_fee = (
            fee_rate * winner_price * (1 - winner_price)
            if winner_price is not None
            else None
        )
        rows.append(
            {
                "schema_version": "clarification_public_print_case_v1",
                "market_id": market_id,
                "title": signal.get("title"),
                "theme": case.get("market_theme_v1"),
                "topic": topic,
                "update_ts": update_ts,
                "update_phase": update.get("phase"),
                "update_text_class_v1": update.get("update_text_class_v1")
                or classify_update_text(str(update.get("text") or "")),
                "update_text": update.get("text"),
                "request_settlement_class": signal.get("request_settlement_class"),
                "reverse_won": signal.get("request_settlement_class") == "binary_flip",
                "pre_update_reverse_price": float(before[-1]["price"]) if before else None,
                "post_update_reverse_price": price,
                "post_update_trade_ts": int(entry["timestamp"]) if entry else None,
                "post_update_trade_delay_seconds": int(entry["timestamp"]) - update_ts if entry else None,
                "post_update_trade_size": float(entry.get("size") or 0) if entry else None,
                "post_update_trade_side": str(entry.get("side") or "") if entry else None,
                "gross_edge_per_share": payout - price if payout is not None and price is not None else None,
                "modeled_fee_per_share": fee,
                "fee_adjusted_edge_per_share": payout - price - fee
                if payout is not None and price is not None and fee is not None
                else None,
                "first_post_dispute_reverse_price": signal.get("entry_price"),
                "hindsight_winning_relation": (
                    "reverse"
                    if signal.get("request_settlement_class") == "binary_flip"
                    else "proposal"
                    if signal.get("request_settlement_class") == "upheld"
                    else None
                ),
                "hindsight_winner_post_update_price": winner_price,
                "hindsight_winner_post_update_trade_ts": int(winner_entry["timestamp"])
                if winner_entry
                else None,
                "hindsight_winner_fee_adjusted_edge_per_share": 1 - winner_price - winner_fee
                if winner_price is not None and winner_fee is not None
                else None,
            }
        )

    args.output_root.mkdir(parents=True, exist_ok=True)
    cases_path = args.output_root / "cases.jsonl"
    with cases_path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    priced = [row for row in rows if row.get("post_update_reverse_price") is not None]
    clear_priced = [row for row in priced if row.get("fee_adjusted_edge_per_share") is not None]
    clear_candidates = [
        row
        for row in rows
        if row.get("request_settlement_class") in {"binary_flip", "upheld"}
    ]
    paired = [
        row
        for row in clear_priced
        if row.get("pre_update_reverse_price") is not None
    ]
    delays = [float(row["post_update_trade_delay_seconds"]) for row in priced]
    winner_priced = [
        row
        for row in rows
        if row.get("hindsight_winner_fee_adjusted_edge_per_share") is not None
    ]
    price_changes = [
        float(row["post_update_reverse_price"]) - float(row["pre_update_reverse_price"])
        for row in paired
    ]
    summary = {
        "schema_version": "clarification_repricing_summary_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "denominator_scope": (
            f"first {args.selection} post-dispute-before-settlement creator update "
            "per first-dispute unique market"
        ),
        "candidate_markets": len(candidates),
        "markets_with_post_update_public_print": len(priced),
        "post_update_public_print_coverage": len(priced) / len(candidates) if candidates else None,
        "clear_binary_priced_markets": len(clear_priced),
        "clear_binary_candidate_markets": len(clear_candidates),
        "binary_flip_rate": sum(row["reverse_won"] for row in clear_priced) / len(clear_priced)
        if clear_priced
        else None,
        "mean_post_update_reverse_price": sum(float(row["post_update_reverse_price"]) for row in clear_priced)
        / len(clear_priced)
        if clear_priced
        else None,
        "mean_gross_edge_per_share": sum(float(row["gross_edge_per_share"]) for row in clear_priced)
        / len(clear_priced)
        if clear_priced
        else None,
        "mean_fee_adjusted_edge_per_share": sum(
            float(row["fee_adjusted_edge_per_share"]) for row in clear_priced
        )
        / len(clear_priced)
        if clear_priced
        else None,
        "fee_adjusted_date_block_edge_ci": date_block_ci(clear_priced),
        "independent_update_utc_dates": len(
            {datetime.fromtimestamp(int(row["update_ts"]), timezone.utc).date() for row in clear_priced}
        ),
        "hindsight_winner_public_print_markets": len(winner_priced),
        "hindsight_winner_public_print_coverage": len(winner_priced) / len(candidates)
        if candidates
        else None,
        "hindsight_winner_public_print_coverage_of_clear_binary": len(winner_priced)
        / len(clear_candidates)
        if clear_candidates
        else None,
        "mean_hindsight_winner_fee_adjusted_edge_per_share": sum(
            float(row["hindsight_winner_fee_adjusted_edge_per_share"])
            for row in winner_priced
        )
        / len(winner_priced)
        if winner_priced
        else None,
        "hindsight_winner_date_block_edge_ci": date_block_ci(
            winner_priced,
            field="hindsight_winner_fee_adjusted_edge_per_share",
        ),
        "paired_pre_post_update_prices": len(paired),
        "median_reverse_price_change_at_update": median(price_changes) if price_changes else None,
        "post_update_trade_delay_seconds": {
            "p25": quantile(delays, 0.25),
            "median": quantile(delays, 0.5),
            "p75": quantile(delays, 0.75),
        },
        "by_update_text_class_v1": {},
        "limitations": [
            "Data API public prints are not executable asks and may represent tiny trades.",
            "Topic fee rates are current fallback rates, not historical per-market fee parameters.",
            "The update text classifier is a transparent regex split, not a validated semantic gold label.",
            "Markets with no cached post-update print remain evidence-coverage gaps, not strategy exclusions.",
        ],
    }
    for kind in sorted({str(row.get("update_text_class_v1")) for row in rows}):
        group_all = [row for row in rows if row.get("update_text_class_v1") == kind]
        group_priced = [row for row in priced if row.get("update_text_class_v1") == kind]
        group_clear = [row for row in clear_priced if row.get("update_text_class_v1") == kind]
        summary["by_update_text_class_v1"][kind] = {
            "candidate_markets": len(group_all),
            "priced_markets": len(group_priced),
            "clear_binary_priced": len(group_clear),
            "binary_flip_rate": sum(row["reverse_won"] for row in group_clear) / len(group_clear)
            if group_clear
            else None,
            "mean_post_update_reverse_price": sum(
                float(row["post_update_reverse_price"]) for row in group_clear
            )
            / len(group_clear)
            if group_clear
            else None,
            "mean_fee_adjusted_edge_per_share": sum(
                float(row["fee_adjusted_edge_per_share"]) for row in group_clear
            )
            / len(group_clear)
            if group_clear
            else None,
            "fee_adjusted_date_block_edge_ci": date_block_ci(group_clear),
        }
    (args.output_root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
