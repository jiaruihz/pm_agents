#!/usr/bin/env python3
"""Counterfactual pullback-maker replay for frozen Core Carry entries.

The entry selector and ten-share taker leg are unchanged.  The candidate uses
the existing five-share maker risk budget as a static pullback bid for a
bounded TTL.  A conservative fill requires a later PIT-available book to show
at least five shares of asks at or below the resting limit.  A separate touch
benchmark is retained for diagnosing depth coverage; it is never called a
fill.

Research only: no order is submitted, replaced, or cancelled.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk.research_core_carry_post_entry_capture_v1 import (
    BOOK_ROOTS,
    DB,
    RUNTIME,
    finite,
    load_books,
    parse_utc,
    selected_entries,
    settlement_map,
    top_of_book,
)
from scripts.analysis.versioned_artifact_output import (
    prepare_new_run_output,
    resolve_run_output,
)


SCHEMA_VERSION = "current_yes_core_carry_pullback_add_maker_v1"
FAMILY = SCHEMA_VERSION
SEED = 20260811
BOOTSTRAP_REPS = 5000
DISCOUNTS = (0.02, 0.03, 0.05, 0.07, 0.10)
TTLS_MIN = (15, 30, 60, 120)
PRIMARY_DISCOUNT = 0.07
PRIMARY_TTL_MIN = 15


def normalize_payoff(value: float) -> float:
    if value >= 0.999:
        return 1.0
    if value <= 0.001:
        return 0.0
    return float(value)


def levels(
    raw_levels: Sequence[Mapping[str, Any] | Sequence[Any]],
) -> list[tuple[float, float]]:
    parsed: list[tuple[float, float]] = []
    for raw in raw_levels:
        if isinstance(raw, Mapping):
            price, size = finite(raw.get("price")), finite(raw.get("size"))
        else:
            try:
                price, size = finite(raw[0]), finite(raw[1])
            except (IndexError, TypeError):
                continue
        if price is not None and size is not None and 0 < price < 1 and size > 0:
            parsed.append((price, size))
    return parsed


def visible_ask_depth_at_or_below(book: Mapping[str, Any], limit: float) -> float:
    return sum(size for price, size in levels(book.get("asks") or []) if price <= limit + 1e-9)


def entry_ask(entry: Mapping[str, Any]) -> float | None:
    ask = finite(entry.get("current_yes_ask"))
    if ask is not None:
        return ask
    ladder = entry.get("taker_ladder")
    return finite(ladder.get("best_ask")) if isinstance(ladder, Mapping) else None


def entry_cost(entry: Mapping[str, Any]) -> float | None:
    ladder = entry.get("taker_ladder")
    return finite(ladder.get("effective_cost_per_share")) if isinstance(ladder, Mapping) else None


def post_entry_books(
    entry: Mapping[str, Any], books: Mapping[str, Sequence[Mapping[str, Any]]]
) -> list[Mapping[str, Any]]:
    created = parse_utc(entry.get("created_at_utc"))
    if created is None:
        return []
    token = str(entry.get("current_yes_token_id") or "")
    return [
        book
        for book in books.get(token, [])
        if (parse_utc(book.get("available_at_utc") or book.get("snapshot_ts_utc")) or created)
        >= created
    ]


def replay_one(
    entry: Mapping[str, Any],
    path: Sequence[Mapping[str, Any]],
    payoff: float | None,
    *,
    discount: float,
    ttl_min: int,
    taker_quantity: float,
    maker_quantity: float,
) -> dict[str, Any]:
    created = parse_utc(entry.get("created_at_utc"))
    ask = entry_ask(entry)
    cost = entry_cost(entry)
    limit = None if ask is None else max(0.01, round(ask - discount + 1e-9, 2))
    cutoff = None if created is None else created + timedelta(minutes=ttl_min)
    eligible = []
    if created is not None and cutoff is not None:
        eligible = [
            book
            for book in path
            if created
            <= (parse_utc(book.get("available_at_utc") or book.get("snapshot_ts_utc")) or created)
            <= cutoff
        ]

    touch_book = None
    fill_book = None
    if limit is not None:
        for book in eligible:
            _bid, best_ask = top_of_book(book)
            if best_ask is not None and best_ask <= limit + 1e-9 and touch_book is None:
                touch_book = book
            if visible_ask_depth_at_or_below(book, limit) + 1e-9 >= maker_quantity:
                fill_book = book
                break

    fill_ts = None if fill_book is None else parse_utc(
        fill_book.get("available_at_utc") or fill_book.get("snapshot_ts_utc")
    )
    recovered = False
    recovery_ts = None
    if fill_ts is not None and ask is not None:
        for book in path:
            book_ts = parse_utc(book.get("available_at_utc") or book.get("snapshot_ts_utc"))
            bid, _best_ask = top_of_book(book)
            if book_ts is not None and book_ts >= fill_ts and bid is not None and bid >= ask - 1e-9:
                recovered = True
                recovery_ts = book_ts
                break

    settled = payoff is not None and cost is not None
    baseline_cost = None if cost is None else taker_quantity * cost
    baseline_pnl = None if not settled else taker_quantity * (float(payoff) - cost)
    incremental_cost = maker_quantity * limit if fill_book is not None and limit is not None else 0.0
    incremental_pnl = (
        maker_quantity * (float(payoff) - limit)
        if settled and fill_book is not None and limit is not None
        else (0.0 if settled else None)
    )
    candidate_cost = None if baseline_cost is None else baseline_cost + incremental_cost
    candidate_pnl = (
        None if baseline_pnl is None or incremental_pnl is None else baseline_pnl + incremental_pnl
    )

    if not settled:
        structure = "unsettled"
    elif fill_book is not None and float(payoff) == 0.0:
        structure = "qualifying_pullback_failed"
    elif fill_book is not None and recovered:
        structure = "qualifying_pullback_recovered"
    elif fill_book is not None:
        structure = "qualifying_pullback_winner_no_observed_recovery"
    elif float(payoff) == 1.0:
        structure = "no_qualifying_pullback_winner"
    else:
        structure = "no_qualifying_pullback_loss"

    path_bids = [top_of_book(book)[0] for book in path]
    path_asks = [top_of_book(book)[1] for book in path]
    path_bids = [value for value in path_bids if value is not None]
    path_asks = [value for value in path_asks if value is not None]
    return {
        "city": entry.get("city"),
        "target_date": entry.get("target_date"),
        "bracket": entry.get("current_bracket"),
        "condition_id": entry.get("current_condition_id"),
        "token_id": entry.get("current_yes_token_id"),
        "entry_created_at_utc": entry.get("created_at_utc"),
        "entry_ask": ask,
        "entry_taker_cost_per_share": cost,
        "settlement_payoff": payoff,
        "settled": settled,
        "post_entry_book_rows": len(path),
        "discount_per_share": discount,
        "maker_limit": limit,
        "ttl_min": ttl_min,
        "touch_observed": touch_book is not None,
        "conservative_fill": fill_book is not None,
        "fill_available_at_utc": None if fill_ts is None else fill_ts.isoformat(),
        "fill_visible_ask_depth": (
            None if fill_book is None or limit is None else visible_ask_depth_at_or_below(fill_book, limit)
        ),
        "market_recovered_to_entry_ask": recovered,
        "recovery_available_at_utc": None if recovery_ts is None else recovery_ts.isoformat(),
        "structure": structure,
        "min_post_entry_ask": min(path_asks, default=None),
        "max_post_entry_bid": max(path_bids, default=None),
        "baseline_cost_usd": baseline_cost,
        "baseline_pnl_usd": baseline_pnl,
        "incremental_maker_cost_usd": incremental_cost if settled else None,
        "incremental_maker_pnl_usd": incremental_pnl,
        "candidate_cost_usd": candidate_cost if settled else None,
        "candidate_pnl_usd": candidate_pnl,
    }


def bootstrap_total_delta(rows: Sequence[Mapping[str, Any]]) -> list[float | None]:
    settled = [row for row in rows if row["settled"]]
    daily: dict[str, float] = defaultdict(float)
    for row in settled:
        daily[str(row["target_date"])] += float(row["incremental_maker_pnl_usd"])
    if len(daily) < 2:
        return [None, None]
    values = np.array(list(daily.values()), dtype=float)
    rng = np.random.default_rng(SEED)
    indices = rng.integers(0, len(values), size=(BOOTSTRAP_REPS, len(values)))
    draws = values[indices].sum(axis=1)
    return [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))]


def summarize(rows: Sequence[Mapping[str, Any]], scope: str) -> dict[str, Any]:
    settled = [row for row in rows if row["settled"]]
    fills = [row for row in settled if row["conservative_fill"]]
    baseline_cost = sum(float(row["baseline_cost_usd"]) for row in settled)
    baseline_pnl = sum(float(row["baseline_pnl_usd"]) for row in settled)
    candidate_cost = sum(float(row["candidate_cost_usd"]) for row in settled)
    candidate_pnl = sum(float(row["candidate_pnl_usd"]) for row in settled)
    structure_counts = Counter(str(row["structure"]) for row in settled)
    city_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in settled:
        city_counts[str(row["city"])][str(row["structure"])] += 1
    return {
        "scope": scope,
        "entries": len(rows),
        "settled_entries": len(settled),
        "target_dates": len({str(row["target_date"]) for row in settled}),
        "entries_with_post_entry_books": sum(int(row["post_entry_book_rows"]) > 0 for row in settled),
        "touches": sum(bool(row["touch_observed"]) for row in settled),
        "conservative_fills": len(fills),
        "fill_rate_per_settled": len(fills) / len(settled) if settled else None,
        "filled_final_winners": sum(float(row["settlement_payoff"]) == 1.0 for row in fills),
        "filled_final_losses": sum(float(row["settlement_payoff"]) == 0.0 for row in fills),
        "baseline_cost_usd": baseline_cost,
        "baseline_pnl_usd": baseline_pnl,
        "baseline_roi": baseline_pnl / baseline_cost if baseline_cost else None,
        "candidate_cost_usd": candidate_cost,
        "candidate_pnl_usd": candidate_pnl,
        "candidate_roi": candidate_pnl / candidate_cost if candidate_cost else None,
        "incremental_maker_cost_usd": candidate_cost - baseline_cost,
        "incremental_maker_pnl_usd": candidate_pnl - baseline_pnl,
        "incremental_maker_roi": (
            (candidate_pnl - baseline_pnl) / (candidate_cost - baseline_cost)
            if candidate_cost > baseline_cost
            else None
        ),
        "pnl_delta_target_date_bootstrap_ci95": bootstrap_total_delta(rows),
        "structure_counts": dict(sorted(structure_counts.items())),
        "structure_city_counts": {
            city: dict(sorted(counts.items())) for city, counts in sorted(city_counts.items())
        },
    }


def scope_rows(rows: Sequence[Mapping[str, Any]], dates: set[str]) -> list[Mapping[str, Any]]:
    return [row for row in rows if str(row["target_date"]) in dates]


def run(args: argparse.Namespace) -> dict[str, Any]:
    runtime = Path(args.runtime)
    entries = selected_entries(runtime)
    if not entries:
        raise RuntimeError(f"no selected Core Carry entries in {runtime}")
    settlements, db_identity = settlement_map(Path(args.db), entries)
    normalized = {condition: normalize_payoff(value) for condition, value in settlements.items()}
    dates = sorted({str(entry["target_date"]) for entry in entries})
    development_dates = set(dates[:10])
    secondary_dates = set(dates[10:])
    date_min = (datetime.fromisoformat(dates[0]) - timedelta(days=1)).date().isoformat()
    date_max = (datetime.fromisoformat(dates[-1]) + timedelta(days=1)).date().isoformat()
    books, book_coverage = load_books(
        tuple(Path(path) for path in args.book_root),
        {str(entry.get("current_yes_token_id") or "") for entry in entries},
        date_min,
        date_max,
    )
    paths = {
        str(entry.get("current_condition_id") or ""): post_entry_books(entry, books)
        for entry in entries
    }
    grid: list[dict[str, Any]] = []
    primary_rows: list[dict[str, Any]] = []
    for ttl in TTLS_MIN:
        for discount in DISCOUNTS:
            rows = [
                replay_one(
                    entry,
                    paths[str(entry.get("current_condition_id") or "")],
                    normalized.get(str(entry.get("current_condition_id") or "")),
                    discount=discount,
                    ttl_min=ttl,
                    taker_quantity=float(args.taker_quantity),
                    maker_quantity=float(args.maker_quantity),
                )
                for entry in entries
            ]
            summaries = {
                "all_settled": summarize(rows, "all_settled"),
                "development_first10_dates": summarize(
                    scope_rows(rows, development_dates), "development_first10_dates"
                ),
                "secondary_last7_dates": summarize(
                    scope_rows(rows, secondary_dates), "secondary_last7_dates"
                ),
            }
            grid.append(
                {
                    "discount_per_share": discount,
                    "ttl_min": ttl,
                    "summaries": summaries,
                }
            )
            if math.isclose(discount, PRIMARY_DISCOUNT) and ttl == PRIMARY_TTL_MIN:
                primary_rows = rows

    primary = {
        "all_settled": summarize(primary_rows, "all_settled"),
        "development_first10_dates": summarize(
            scope_rows(primary_rows, development_dates), "development_first10_dates"
        ),
        "secondary_last7_dates": summarize(
            scope_rows(primary_rows, secondary_dates), "secondary_last7_dates"
        ),
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_status": "gross_maker_opportunity_incremental_attribution_invalid",
        "policy": {
            "baseline": f"{args.taker_quantity:g}-share original taker hold to settlement",
            "candidate": f"baseline plus {args.maker_quantity:g}-share static pullback maker",
            "primary_discount_per_share": PRIMARY_DISCOUNT,
            "primary_ttl_min": PRIMARY_TTL_MIN,
            "fill_contract": "first PIT-available full book with >= maker quantity visible asks at prices <= resting limit",
            "fill_price": "resting maker limit (conservative cost assumption)",
            "maker_fee": 0.0,
            "no_reprice": True,
        },
        "denominator": {
            "signal_entries": len(entries),
            "date_min": dates[0],
            "date_max": dates[-1],
            "target_dates": len(dates),
            "cities": len({str(entry.get("city")) for entry in entries}),
            "canonical_settled_entries": len(normalized),
            "settlement_coverage_gaps": len(entries) - len(normalized),
            "development_dates": sorted(development_dates),
            "secondary_dates": sorted(secondary_dates),
        },
        "db_identity": db_identity,
        "book_coverage": book_coverage,
        "primary": primary,
        "grid": grid,
        "limitations": [
            "This is a counterfactual quote replay, not actual maker order/fill PnL.",
            "The baseline is ten-share taker-only and omits the existing five-share production maker; the delta is gross maker opportunity, not incremental alpha versus current execution.",
            "An active production maker normally rests near ask minus one tick, so a later deeper pullback would cross that existing order before it could be attributed to an additional maker sleeve.",
            "Visible ask depth crossing is stronger than future touch but still cannot reconstruct queue priority or intervening trade prints.",
            "The secondary window contains Amsterdam, which motivated the hypothesis; it is not untouched forward evidence.",
            "The older frozen 2026-06-02..2026-07-08 Core universe lacks comparable five-minute PIT full-book coverage and is excluded from maker-fill PnL rather than filled with hourly future-touch proxies.",
        ],
        "decision": {
            "live_change": "none",
            "conclusion": "incremental_strategy_attribution_invalid",
            "next_valid_comparison": "paired current-v3 maker lifecycle versus same-budget deeper-static replacement versus post-update thesis-revalidated re-arm",
        },
    }
    return {"payload": payload, "primary_rows": primary_rows}


def parser() -> argparse.ArgumentParser:
    out = argparse.ArgumentParser()
    out.add_argument("--runtime", default=str(RUNTIME))
    out.add_argument("--db", default=str(DB))
    out.add_argument("--book-root", action="append", default=None)
    out.add_argument("--taker-quantity", type=float, default=10.0)
    out.add_argument("--maker-quantity", type=float, default=5.0)
    out.add_argument("--run-id", required=True)
    out.add_argument("--output-dir", default=None)
    return out


def main() -> int:
    args = parser().parse_args()
    if args.book_root is None:
        args.book_root = [str(path) for path in BOOK_ROOTS]
    result = run(args)
    run_dir = prepare_new_run_output(
        resolve_run_output(
            FAMILY,
            run_id=args.run_id,
            explicit_output=Path(args.output_dir) if args.output_dir else None,
        )
    )
    pd.DataFrame(result["primary_rows"]).to_csv(run_dir / "primary_entry_replay.csv", index=False)
    (run_dir / "result.json").write_text(
        json.dumps(result["payload"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["payload"]["primary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
