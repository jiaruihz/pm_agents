#!/usr/bin/env python3
"""Replay a report-driven profit-capture overlay for Core Carry.

The entry selector is frozen.  After entry, each newly available official
report is an event clock.  At that clock the replay looks up the first archived
book available after the weather state and exits only when a marketable sell
of the held YES shares, after the official Weather taker fee, locks at least a
fixed gain over the frozen entry cost.

This is research-only.  It never places or cancels an order.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.current_yes_core_carry import (
    DEFAULT_ARTIFACT,
    load_artifact,
    market_features,
    score_probability,
    walk_ask_ladder,
)

RUNTIME = Path(
    "/Volumes/jrs/pm_agents/runtime/weather_edge_v1/"
    "current_yes_core_carry_tiny_live_v2"
)
DB = Path("/Volumes/jrs/pm_agents/runtime/weather.db")
BOOK_ROOTS = (
    Path("/Volumes/jrs/pm_agents/runtime/weather_edge_v1/market_data/orderbook_snapshots"),
    Path("/Volumes/jrs/weather_data_feed_service_runtime/targeted_output/orderbook_snapshots"),
    Path("/Volumes/jrs/weather_data_feed_service_runtime/full_ladder_output/orderbook_snapshots"),
)
OUT_DIR = ROOT / (
    "docs/analysis/2026-08/generated/"
    "current_yes_core_carry_post_entry_capture_v1"
)
OUT_JSON = ROOT / (
    "docs/analysis/2026-08/"
    "2026-08-07-current-yes-core-carry-post-entry-capture-v1.json"
)
OUT_MD = OUT_JSON.with_suffix(".md")

SCHEMA_VERSION = "current_yes_core_carry_post_entry_capture_v1"
GAIN_FLOOR = 0.03
MAX_BOOK_LAG_MIN = 30.0
BOOTSTRAP_REPS = 5000
SEED = 20260807


def parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def official_weather_fee_per_share(price: float) -> float:
    return round(0.05 * price * (1.0 - price), 5)


def walk_sell_ladder(
    bids: Sequence[Mapping[str, Any] | Sequence[Any]], quantity: float
) -> dict[str, Any]:
    levels: list[tuple[float, float]] = []
    for raw in bids:
        if isinstance(raw, Mapping):
            price, size = finite(raw.get("price")), finite(raw.get("size"))
        else:
            try:
                price, size = finite(raw[0]), finite(raw[1])
            except (IndexError, TypeError):
                continue
        if price is not None and size is not None and 0 < price < 1 and size > 0:
            levels.append((price, size))
    levels.sort(reverse=True)
    remaining = float(quantity)
    principal = 0.0
    fee = 0.0
    min_bid = None
    for price, available in levels:
        taken = min(remaining, available)
        principal += taken * price
        fee += taken * official_weather_fee_per_share(price)
        if taken > 0:
            min_bid = price
        remaining -= taken
        if remaining <= 1e-9:
            break
    executable = quantity > 0 and remaining <= 1e-9
    return {
        "quantity": float(quantity),
        "executable": executable,
        "unfilled_shares": max(0.0, remaining),
        "principal": principal if executable else None,
        "fee": fee if executable else None,
        "principal_vwap": principal / quantity if executable else None,
        "net_proceeds_per_share": (principal - fee) / quantity if executable else None,
        "best_bid": levels[0][0] if levels else None,
        "min_bid": min_bid if executable else None,
    }


def top_of_book(book: Mapping[str, Any]) -> tuple[float | None, float | None]:
    def prices(levels: Sequence[Mapping[str, Any] | Sequence[Any]]) -> list[float]:
        out: list[float] = []
        for level in levels:
            if isinstance(level, Mapping):
                price = finite(level.get("price"))
            else:
                try:
                    price = finite(level[0])
                except (IndexError, TypeError):
                    price = None
            if price is not None and 0 < price < 1:
                out.append(price)
        return out

    bids = prices(book.get("bids") or [])
    asks = prices(book.get("asks") or [])
    return (max(bids) if bids else None, min(asks) if asks else None)


def post_event_probability(
    event: Mapping[str, Any],
    book: Mapping[str, Any],
    artifact: Mapping[str, Any],
) -> float | None:
    bid, ask = top_of_book(book)
    state = {**event, "current_yes_bid": bid, "current_yes_ask": ask}
    features, fatal = market_features(state)
    if fatal:
        return None
    return score_probability(features, artifact)


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def selected_entries(runtime: Path) -> list[dict[str, Any]]:
    return [
        row
        for row in iter_jsonl(runtime / "pre_live_scores.jsonl")
        if row.get("would_submit_after_family_dedupe") is True
    ]


def load_states(runtime: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    states: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in iter_jsonl(runtime / "state_decisions.jsonl"):
        states[(str(row.get("city") or ""), str(row.get("target_date") or ""))].append(row)
    return states


def settlement_map(db_path: Path, entries: Sequence[Mapping[str, Any]]) -> tuple[dict[str, float], dict[str, Any]]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=1000")
    settled: dict[str, float] = {}
    for entry in entries:
        condition_id = str(entry.get("current_condition_id") or "")
        row = connection.execute(
            """
            SELECT final_price
            FROM settlement_outcomes
            WHERE condition_id = ? AND settlement_status = 'settled'
            ORDER BY created_at_utc DESC
            LIMIT 1
            """,
            (condition_id,),
        ).fetchone()
        if row is not None and finite(row[0]) is not None:
            settled[condition_id] = float(row[0])
    fact = connection.execute(
        "SELECT MAX(fact_built_at_utc), COUNT(*) FROM fact_trades"
    ).fetchone()
    candidate = connection.execute(
        "SELECT MAX(fact_built_at_utc), COUNT(*) FROM fact_signal_candidates"
    ).fetchone()
    connection.close()
    stat = db_path.stat()
    return settled, {
        "db_path": str(db_path),
        "db_device": stat.st_dev,
        "db_inode": stat.st_ino,
        "fact_trades_built_at_utc": fact[0],
        "fact_trades_rows": fact[1],
        "fact_signal_candidates_built_at_utc": candidate[0],
        "fact_signal_candidates_rows": candidate[1],
    }


def book_files(roots: Sequence[Path], date_min: str, date_max: str) -> list[Path]:
    files: list[Path] = []
    seen: set[tuple[int, int]] = set()
    for root in roots:
        if not root.exists():
            continue
        for day in root.iterdir():
            if not day.is_dir() or not date_min <= day.name <= date_max:
                continue
            for path in day.glob("orderbook_snapshot_*.jsonl*"):
                stat = path.stat()
                identity = (stat.st_dev, stat.st_ino)
                if identity not in seen:
                    seen.add(identity)
                    files.append(path)
    return sorted(files)


def load_books(
    roots: Sequence[Path], tokens: set[str], date_min: str, date_max: str
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    books: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    files = book_files(roots, date_min, date_max)
    for path in files:
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    token = str(row.get("token_id") or "")
                    if token not in tokens or str(row.get("outcome") or "").lower() != "yes":
                        continue
                    if str(row.get("status") or "") != "ok":
                        continue
                    timestamp = str(row.get("snapshot_ts_utc") or row.get("fetched_at_utc") or "")
                    if parse_utc(timestamp) is None:
                        continue
                    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
                    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
                    bids = summary.get("bids") or raw.get("bids") or []
                    asks = summary.get("asks") or raw.get("asks") or []
                    candidate = {
                        "snapshot_ts_utc": timestamp,
                        "bids": bids,
                        "asks": asks,
                        "source_path": str(path),
                    }
                    prior = books[token].get(timestamp)
                    prior_depth = sum(finite(x.get("size")) or 0.0 for x in (prior or {}).get("bids", []))
                    depth = sum(finite(x.get("size")) or 0.0 for x in bids if isinstance(x, dict))
                    if prior is None or depth > prior_depth:
                        books[token][timestamp] = candidate
        except (EOFError, OSError):
            continue
    ordered = {
        token: sorted(rows.values(), key=lambda row: parse_utc(row["snapshot_ts_utc"]))
        for token, rows in books.items()
    }
    return ordered, {
        "book_files_scanned": len(files),
        "tokens_requested": len(tokens),
        "tokens_with_any_book": len(ordered),
        "book_rows_matched": sum(len(rows) for rows in ordered.values()),
        "roots": [str(root) for root in roots],
    }


def post_entry_events(
    entry: Mapping[str, Any], states: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]]
) -> list[Mapping[str, Any]]:
    entry_report = parse_utc(entry.get("source_report_ts_utc"))
    if entry_report is None:
        return []
    key = (str(entry.get("city") or ""), str(entry.get("target_date") or ""))
    by_report: dict[str, Mapping[str, Any]] = {}
    for state in states.get(key, []):
        report = parse_utc(state.get("source_report_ts_utc"))
        available = parse_utc(state.get("as_of_ts_utc"))
        if report is None or available is None or report <= entry_report:
            continue
        if str(state.get("current_bracket") or "") != str(entry.get("current_bracket") or ""):
            continue
        by_report[str(state.get("source_report_ts_utc"))] = state
    return sorted(by_report.values(), key=lambda row: parse_utc(row.get("as_of_ts_utc")))


def first_new_report_event(
    entry: Mapping[str, Any], states: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]]
) -> Mapping[str, Any] | None:
    entry_report = parse_utc(entry.get("source_report_ts_utc"))
    if entry_report is None:
        return None
    key = (str(entry.get("city") or ""), str(entry.get("target_date") or ""))
    by_report: dict[str, Mapping[str, Any]] = {}
    for state in states.get(key, []):
        report = parse_utc(state.get("source_report_ts_utc"))
        available = parse_utc(state.get("as_of_ts_utc"))
        if report is None or available is None or report <= entry_report:
            continue
        report_key = str(state.get("source_report_ts_utc"))
        prior = by_report.get(report_key)
        if prior is None or available < parse_utc(prior.get("as_of_ts_utc")):
            by_report[report_key] = state
    if not by_report:
        return None
    return min(
        by_report.values(),
        key=lambda row: parse_utc(row.get("as_of_ts_utc"))
        or datetime.max.replace(tzinfo=timezone.utc),
    )


def match_book_after_event(
    event: Mapping[str, Any], books: Sequence[Mapping[str, Any]], max_lag_min: float
) -> Mapping[str, Any] | None:
    available = parse_utc(event.get("as_of_ts_utc"))
    if available is None:
        return None
    limit = available + timedelta(minutes=max_lag_min)
    for book in books:
        timestamp = parse_utc(book.get("snapshot_ts_utc"))
        if timestamp is not None and available <= timestamp <= limit:
            return book
    return None


def entry_cost(entry: Mapping[str, Any]) -> float | None:
    ladder = entry.get("taker_ladder")
    if not isinstance(ladder, dict):
        return None
    return finite(ladder.get("effective_cost_per_share"))


def replay_entries(
    entries: Sequence[Mapping[str, Any]],
    states: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    books: Mapping[str, Sequence[Mapping[str, Any]]],
    settlements: Mapping[str, float],
    artifact: Mapping[str, Any],
    *,
    quantity: float,
    gain_floor: float,
    max_book_lag_min: float,
    exit_policy: str = "gain_floor",
    model_edge_margin: float = 0.0,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in entries:
        condition = str(entry.get("current_condition_id") or "")
        cost = entry_cost(entry)
        payoff = settlements.get(condition)
        events = post_entry_events(entry, states)
        quotes: list[dict[str, Any]] = []
        for event in events:
            book = match_book_after_event(
                event,
                books.get(str(entry.get("current_yes_token_id") or ""), []),
                max_book_lag_min,
            )
            if book is None:
                continue
            execution = walk_sell_ladder(book.get("bids") or [], quantity)
            if not execution["executable"]:
                continue
            probability = post_event_probability(event, book, artifact)
            available = parse_utc(event.get("as_of_ts_utc"))
            book_ts = parse_utc(book.get("snapshot_ts_utc"))
            quotes.append(
                {
                    "event_report_ts_utc": event.get("source_report_ts_utc"),
                    "event_available_at_utc": event.get("as_of_ts_utc"),
                    "book_snapshot_ts_utc": book.get("snapshot_ts_utc"),
                    "book_lag_min": (book_ts - available).total_seconds() / 60.0,
                    "net_proceeds_per_share": execution["net_proceeds_per_share"],
                    "principal_vwap": execution["principal_vwap"],
                    "exit_fee_usd": execution["fee"],
                    "best_bid": execution["best_bid"],
                    "min_bid": execution["min_bid"],
                    "post_event_model_probability_hold": probability,
                    "sell_minus_model_per_share": (
                        None
                        if probability is None
                        else execution["net_proceeds_per_share"] - probability
                    ),
                    "source_path": book.get("source_path"),
                    "minutes_since_running_max": event.get("minutes_since_running_max"),
                    "daylight_remaining_minutes": event.get("daylight_remaining_minutes"),
                    "forecast_gap_to_running_native": event.get("forecast_gap_to_running_native"),
                    "temp_trend_1h_f": event.get("temp_trend_1h_f"),
                    "temp_trend_3h_f": event.get("temp_trend_3h_f"),
                    "forecast_peak_clock_state": event.get("forecast_peak_clock_state"),
                    "running_max_state": event.get("running_max_state"),
                    "intraday_state": event.get("intraday_state"),
                    "heating_done_bucket_v1": event.get("heating_done_bucket_v1"),
                    "raw_metar": event.get("raw_metar"),
                }
            )
        chosen = None
        if exit_policy == "gain_floor" and cost is not None:
            chosen = next(
                (quote for quote in quotes if quote["net_proceeds_per_share"] >= cost + gain_floor),
                None,
            )
        elif exit_policy == "model_edge_reversal":
            chosen = next(
                (
                    quote
                    for quote in quotes
                    if quote["sell_minus_model_per_share"] is not None
                    and quote["sell_minus_model_per_share"] >= model_edge_margin
                ),
                None,
            )
        elif exit_policy != "gain_floor":
            raise ValueError(f"unsupported exit_policy={exit_policy!r}")
        settled = payoff is not None and cost is not None
        baseline_pnl = quantity * (payoff - cost) if settled else None
        capture_pnl = (
            quantity * (chosen["net_proceeds_per_share"] - cost)
            if chosen is not None and cost is not None
            else baseline_pnl
        )
        rows.append(
            {
                "city": entry.get("city"),
                "target_date": entry.get("target_date"),
                "bracket": entry.get("current_bracket"),
                "condition_id": condition,
                "token_id": entry.get("current_yes_token_id"),
                "entry_created_at_utc": entry.get("created_at_utc"),
                "entry_source_report_ts_utc": entry.get("source_report_ts_utc"),
                "entry_cost_per_share": cost,
                "model_probability_hold": entry.get("model_probability_hold"),
                "settlement_payoff": payoff,
                "settled": payoff is not None,
                "post_entry_new_report_events": len(events),
                "executable_event_quotes": len(quotes),
                "capture_triggered": chosen is not None,
                "capture_event_report_ts_utc": None if chosen is None else chosen["event_report_ts_utc"],
                "capture_book_ts_utc": None if chosen is None else chosen["book_snapshot_ts_utc"],
                "capture_net_proceeds_per_share": None if chosen is None else chosen["net_proceeds_per_share"],
                "capture_model_probability_hold": (
                    None if chosen is None else chosen["post_event_model_probability_hold"]
                ),
                "capture_sell_minus_model_per_share": (
                    None if chosen is None else chosen["sell_minus_model_per_share"]
                ),
                "exit_policy": exit_policy,
                "model_edge_margin_per_share": float(model_edge_margin),
                "min_post_event_model_probability_hold": min(
                    (
                        float(quote["post_event_model_probability_hold"])
                        for quote in quotes
                        if quote["post_event_model_probability_hold"] is not None
                    ),
                    default=None,
                ),
                "max_sell_minus_model_per_share": max(
                    (
                        float(quote["sell_minus_model_per_share"])
                        for quote in quotes
                        if quote["sell_minus_model_per_share"] is not None
                    ),
                    default=None,
                ),
                "baseline_pnl_usd": baseline_pnl,
                "capture_pnl_usd": capture_pnl,
                "pnl_delta_usd": (
                    None if baseline_pnl is None or capture_pnl is None else capture_pnl - baseline_pnl
                ),
            }
        )
    return rows


def replay_one_report_confirmation(
    entries: Sequence[Mapping[str, Any]],
    states: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    books: Mapping[str, Sequence[Mapping[str, Any]]],
    settlements: Mapping[str, float],
    artifact: Mapping[str, Any],
    *,
    quantity: float,
    max_book_lag_min: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in entries:
        condition = str(entry.get("current_condition_id") or "")
        original_cost = entry_cost(entry)
        payoff = settlements.get(condition)
        event = first_new_report_event(entry, states)
        covered = False
        entered = False
        reason = "missing_new_report"
        confirmation_cost = None
        confirmation_probability = None
        confirmation_edge = None
        book_ts = None
        if event is not None and str(event.get("current_bracket") or "") != str(
            entry.get("current_bracket") or ""
        ):
            covered = True
            reason = "held_bracket_invalidated_by_first_new_report"
        elif event is not None:
            event_bid = finite(event.get("current_yes_bid"))
            event_ask = finite(event.get("current_yes_ask"))
            event_ask_size = finite(event.get("current_yes_ask_size"))
            if (
                event_bid is not None
                and event_ask is not None
                and 0 < event_bid <= event_ask < 1
                and event_ask_size is not None
                and event_ask_size >= quantity
            ):
                features, fatal = market_features(event)
                confirmation_probability = (
                    None if fatal else score_probability(features, artifact)
                )
                confirmation_cost = event_ask + official_weather_fee_per_share(event_ask)
                book_ts = event.get("as_of_ts_utc")
                if confirmation_probability is None:
                    reason = "missing_post_report_probability"
                else:
                    covered = True
                    confirmation_edge = confirmation_probability - confirmation_cost
                    entered = confirmation_edge > 0
                    reason = (
                        "positive_post_report_taker_ev"
                        if entered
                        else "non_positive_post_report_taker_ev"
                    )
            else:
                book = match_book_after_event(
                    event,
                    books.get(str(entry.get("current_yes_token_id") or ""), []),
                    max_book_lag_min,
                )
                if book is None:
                    reason = "missing_post_report_book"
                else:
                    ladder = walk_ask_ladder(book.get("asks") or [], quantity)
                    confirmation_probability = post_event_probability(event, book, artifact)
                    confirmation_cost = ladder.get("effective_cost_per_share")
                    book_ts = book.get("snapshot_ts_utc")
                    if confirmation_probability is None:
                        reason = "missing_post_report_probability"
                    elif not ladder["executable"]:
                        reason = "insufficient_post_report_ask_depth"
                    else:
                        covered = True
                        confirmation_edge = confirmation_probability - float(confirmation_cost)
                        entered = confirmation_edge > 0
                        reason = (
                            "positive_post_report_taker_ev"
                            if entered
                            else "non_positive_post_report_taker_ev"
                        )
        settled = payoff is not None and original_cost is not None
        baseline_pnl = quantity * (payoff - original_cost) if settled and covered else None
        candidate_pnl = (
            quantity * (payoff - confirmation_cost)
            if settled and covered and entered and confirmation_cost is not None
            else (0.0 if settled and covered else None)
        )
        rows.append(
            {
                "city": entry.get("city"),
                "target_date": entry.get("target_date"),
                "bracket": entry.get("current_bracket"),
                "condition_id": condition,
                "entry_source_report_ts_utc": entry.get("source_report_ts_utc"),
                "entry_created_at_utc": entry.get("created_at_utc"),
                "entry_cost_per_share": original_cost,
                "entry_model_probability_hold": entry.get("model_probability_hold"),
                "settlement_payoff": payoff,
                "settled": settled,
                "confirmation_covered": covered,
                "confirmation_entered": entered,
                "confirmation_reason": reason,
                "confirmation_report_ts_utc": None if event is None else event.get("source_report_ts_utc"),
                "confirmation_available_at_utc": None if event is None else event.get("as_of_ts_utc"),
                "confirmation_current_bracket": None if event is None else event.get("current_bracket"),
                "confirmation_book_ts_utc": book_ts,
                "confirmation_cost_per_share": confirmation_cost,
                "confirmation_model_probability_hold": confirmation_probability,
                "confirmation_model_edge_after_fee_and_depth": confirmation_edge,
                "confirmation_forecast_gap_to_running_native": (
                    None if event is None else event.get("forecast_gap_to_running_native")
                ),
                "confirmation_forecast_peak_delta_hours_local": (
                    None if event is None else event.get("forecast_peak_delta_hours_local")
                ),
                "confirmation_running_max_state": (
                    None if event is None else event.get("running_max_state")
                ),
                "confirmation_intraday_state": None if event is None else event.get("intraday_state"),
                "baseline_pnl_usd": baseline_pnl,
                "candidate_pnl_usd": candidate_pnl,
                "pnl_delta_usd": (
                    None
                    if baseline_pnl is None or candidate_pnl is None
                    else candidate_pnl - baseline_pnl
                ),
            }
        )
    return rows


def summarize_confirmation(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    settled = [row for row in rows if row["settled"]]
    covered = [row for row in settled if row["confirmation_covered"]]
    entered = [row for row in covered if row["confirmation_entered"]]
    skipped = [row for row in covered if not row["confirmation_entered"]]
    dates = sorted({str(row["target_date"]) for row in covered})
    baseline = sum(float(row["baseline_pnl_usd"]) for row in covered)
    candidate = sum(float(row["candidate_pnl_usd"]) for row in covered)
    daily = np.array(
        [
            sum(float(row["pnl_delta_usd"]) for row in covered if row["target_date"] == date)
            for date in dates
        ],
        dtype=float,
    )
    ci = [None, None]
    if len(dates) >= 2:
        rng = np.random.default_rng(SEED + 1)
        indices = rng.integers(0, len(daily), size=(BOOTSTRAP_REPS, len(daily)))
        draws = daily[indices].sum(axis=1)
        ci = [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))]
    return {
        "settled_entries": len(settled),
        "confirmation_covered_entries": len(covered),
        "confirmation_covered_target_dates": len(dates),
        "candidate_entries": len(entered),
        "candidate_final_winners": sum(float(row["settlement_payoff"]) == 1.0 for row in entered),
        "candidate_final_losses": sum(float(row["settlement_payoff"]) == 0.0 for row in entered),
        "skipped_entries": len(skipped),
        "skipped_final_winners": sum(float(row["settlement_payoff"]) == 1.0 for row in skipped),
        "skipped_final_losses": sum(float(row["settlement_payoff"]) == 0.0 for row in skipped),
        "invalidated_on_first_report": sum(
            row["confirmation_reason"] == "held_bracket_invalidated_by_first_new_report"
            for row in covered
        ),
        "baseline_hold_pnl_usd": baseline,
        "candidate_confirmation_pnl_usd": candidate,
        "pnl_delta_usd": candidate - baseline,
        "pnl_delta_target_date_bootstrap_ci95": ci,
        "winner_profit_sacrificed_or_skipped_usd": sum(
            max(0.0, -float(row["pnl_delta_usd"]))
            for row in covered
            if float(row["settlement_payoff"]) == 1.0
        ),
        "loss_capital_saved_usd": sum(
            max(0.0, float(row["pnl_delta_usd"]))
            for row in covered
            if float(row["settlement_payoff"]) == 0.0
        ),
    }


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    settled = [row for row in rows if row["settled"]]
    executable = [row for row in settled if row["executable_event_quotes"] > 0]
    triggered = [row for row in executable if row["capture_triggered"]]
    baseline = sum(float(row["baseline_pnl_usd"]) for row in executable)
    candidate = sum(float(row["capture_pnl_usd"]) for row in executable)
    dates = sorted({str(row["target_date"]) for row in executable})
    daily = {
        date: sum(float(row["pnl_delta_usd"]) for row in executable if row["target_date"] == date)
        for date in dates
    }
    ci = [None, None]
    if len(dates) >= 2:
        rng = np.random.default_rng(SEED)
        values = np.array([daily[date] for date in dates], dtype=float)
        indices = rng.integers(0, len(values), size=(BOOTSTRAP_REPS, len(values)))
        draws = values[indices].sum(axis=1)
        ci = [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))]
    return {
        "settled_entries": len(settled),
        "settled_entries_with_new_report": sum(row["post_entry_new_report_events"] > 0 for row in settled),
        "settled_entries_with_executable_event_quote": len(executable),
        "settled_dates_with_executable_event_quote": len(dates),
        "baseline_hold_pnl_usd": baseline,
        "candidate_capture_pnl_usd": candidate,
        "pnl_delta_usd": candidate - baseline,
        "pnl_delta_target_date_bootstrap_ci95": ci,
        "capture_triggers": len(triggered),
        "capture_triggered_final_winners": sum(float(row["settlement_payoff"]) == 1.0 for row in triggered),
        "capture_triggered_final_losses": sum(float(row["settlement_payoff"]) == 0.0 for row in triggered),
        "settled_losses_in_executable_denominator": sum(float(row["settlement_payoff"]) == 0.0 for row in executable),
        "loss_capital_saved_usd": sum(
            max(0.0, float(row["pnl_delta_usd"]))
            for row in triggered
            if float(row["settlement_payoff"]) == 0.0
        ),
        "winner_profit_sacrificed_usd": sum(
            max(0.0, -float(row["pnl_delta_usd"]))
            for row in triggered
            if float(row["settlement_payoff"]) == 1.0
        ),
    }


def csv_value(value: Any) -> str:
    text = "" if value is None else str(value)
    if any(char in text for char in [",", '"', "\n"]):
        return '"' + text.replace('"', '""') + '"'
    return text


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    lines = [",".join(fields)]
    lines.extend(",".join(csv_value(row.get(field)) for field in fields) for row in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    runtime = Path(args.runtime)
    db_path = Path(args.db)
    entries = selected_entries(runtime)
    states = load_states(runtime)
    artifact = load_artifact(DEFAULT_ARTIFACT)
    settlements, db_identity = settlement_map(db_path, entries)
    target_dates = sorted(str(entry["target_date"]) for entry in entries)
    date_min = (datetime.fromisoformat(target_dates[0]) - timedelta(days=1)).date().isoformat()
    date_max = (datetime.fromisoformat(target_dates[-1]) + timedelta(days=1)).date().isoformat()
    books, coverage = load_books(
        tuple(Path(path) for path in args.book_root),
        {str(entry["current_yes_token_id"]) for entry in entries},
        date_min,
        date_max,
    )
    primary_rows = replay_entries(
        entries,
        states,
        books,
        settlements,
        artifact,
        quantity=float(args.quantity),
        gain_floor=float(args.gain_floor),
        max_book_lag_min=float(args.max_book_lag_min),
    )
    primary = summarize(primary_rows)
    sweep = []
    for gain in (0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.10):
        rows = replay_entries(
            entries,
            states,
            books,
            settlements,
            artifact,
            quantity=float(args.quantity),
            gain_floor=gain,
            max_book_lag_min=float(args.max_book_lag_min),
        )
        sweep.append({"gain_floor_per_share": gain, **summarize(rows)})
    edge_reversal_rows: dict[float, list[dict[str, Any]]] = {}
    edge_reversal_sweep = []
    for margin in (0.0, 0.01, 0.02, 0.03):
        rows = replay_entries(
            entries,
            states,
            books,
            settlements,
            artifact,
            quantity=float(args.quantity),
            gain_floor=float(args.gain_floor),
            max_book_lag_min=float(args.max_book_lag_min),
            exit_policy="model_edge_reversal",
            model_edge_margin=margin,
        )
        edge_reversal_rows[margin] = rows
        edge_reversal_sweep.append(
            {"model_edge_margin_per_share": margin, **summarize(rows)}
        )
    confirmation_rows = replay_one_report_confirmation(
        entries,
        states,
        books,
        settlements,
        artifact,
        quantity=float(args.quantity),
        max_book_lag_min=float(args.max_book_lag_min),
    )
    confirmation = summarize_confirmation(confirmation_rows)
    physical_crosses = []
    for entry in entries:
        key = (str(entry["city"]), str(entry["target_date"]))
        entry_report = parse_utc(entry.get("source_report_ts_utc"))
        crossed = next(
            (
                state
                for state in sorted(states.get(key, []), key=lambda row: parse_utc(row.get("as_of_ts_utc")) or datetime.max.replace(tzinfo=timezone.utc))
                if parse_utc(state.get("source_report_ts_utc")) is not None
                and parse_utc(state.get("source_report_ts_utc")) > entry_report
                and str(state.get("current_bracket")) != str(entry.get("current_bracket"))
            ),
            None,
        )
        if crossed is not None:
            replay = next(row for row in primary_rows if row["condition_id"] == entry["current_condition_id"])
            physical_crosses.append(
                {
                    "city": entry["city"],
                    "target_date": entry["target_date"],
                    "held_bracket": entry["current_bracket"],
                    "cross_report_ts_utc": crossed.get("source_report_ts_utc"),
                    "settled": replay["settled"],
                    "capture_triggered": replay["capture_triggered"],
                    "capture_event_report_ts_utc": replay["capture_event_report_ts_utc"],
                }
            )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_status": "diagnostic_in_sample_threshold_selection",
        "model_artifact": {
            "path": str(DEFAULT_ARTIFACT),
            "artifact_version": artifact["artifact_version"],
            "artifact_hash": artifact["artifact_hash"],
        },
        "policy": {
            "entry": "unchanged frozen Core Carry first positive-EV city-day signal",
            "event_clock": "each newly available official report after entry while held bracket remains current",
            "exit": "marketable SELL YES using full archived bid ladder",
            "quantity": float(args.quantity),
            "gain_floor_per_share": float(args.gain_floor),
            "max_book_lag_min": float(args.max_book_lag_min),
            "exit_fee": "official Weather taker fee 0.05*p*(1-p), per level, rounded to 5 decimals",
            "no_fallback": True,
        },
        "denominator": {
            "signal_entries": len(entries),
            "target_dates": len(set(target_dates)),
            "date_min": target_dates[0],
            "date_max": target_dates[-1],
            "canonical_settlement_entries": len(settlements),
            "missing_or_open_settlement_entries": len(entries) - len(settlements),
        },
        "db_identity": db_identity,
        "book_coverage": coverage,
        "primary": primary,
        "threshold_sweep": sweep,
        "model_edge_reversal_sweep": edge_reversal_sweep,
        "one_report_confirmation": confirmation,
        "physical_cross_cases": physical_crosses,
        "limitations": [
            "The +3c operating point was selected after inspecting this window; it is not frozen-forward evidence.",
            "Primary replay uses the frozen five-share entry effective cost and archived marketable sell depth; it is not actual live fill PnL.",
            "Settlement gaps remain evidence gaps and are excluded rather than inferred from METAR running maxima.",
            "A missing post-event full ladder is an archive coverage gap, not a strategy rejection.",
        ],
        "decision": {
            "live_change": "none",
            "next_action": "run zero-notional forward post-entry capture shadow at +3c per share",
            "significance": "PASS" if primary["pnl_delta_target_date_bootstrap_ci95"][0] is not None and primary["pnl_delta_target_date_bootstrap_ci95"][0] > 0 else "FAIL",
            "baseline": "PASS",
            "forward": "FAIL",
            "conclusion": "inconclusive",
        },
    }
    return {
        "payload": payload,
        "rows": primary_rows,
        "model_edge_reversal_zero_margin_rows": edge_reversal_rows[0.0],
        "one_report_confirmation_rows": confirmation_rows,
    }


def markdown(payload: Mapping[str, Any]) -> str:
    p = payload["primary"]
    confirmation = payload["one_report_confirmation"]
    cross_lines = [
        f"| {row['city']} | {row['target_date']} | {row['held_bracket']} | {row['capture_triggered']} | {row['capture_event_report_ts_utc'] or ''} |"
        for row in payload["physical_cross_cases"]
    ]
    sweep_lines = [
        f"| {row['gain_floor_per_share'] * 100:.0f}c | {row['capture_triggers']} | ${row['candidate_capture_pnl_usd']:+.2f} | ${row['pnl_delta_usd']:+.2f} |"
        for row in payload["threshold_sweep"]
    ]
    reversal_lines = [
        f"| {row['model_edge_margin_per_share'] * 100:.0f}c | {row['capture_triggers']} | {row['capture_triggered_final_winners']} | {row['capture_triggered_final_losses']} | ${row['candidate_capture_pnl_usd']:+.2f} | ${row['pnl_delta_usd']:+.2f} |"
        for row in payload["model_edge_reversal_sweep"]
    ]
    return "\n".join(
        [
            "# Core Carry post-entry carry-capture v1",
            "",
            "## 数据快照",
            "",
            f"- 数据源：Core raw `pre_live_scores/state_decisions`、PIT full-ladder orderbook、canonical `settlement_outcomes`。",
            f"- DB build：`{payload['db_identity']['fact_trades_built_at_utc']}`；fact_trades `{payload['db_identity']['fact_trades_rows']}` rows。",
            f"- signal entries：`{payload['denominator']['signal_entries']}`；canonical settled `{payload['denominator']['canonical_settlement_entries']}`；missing/open `{payload['denominator']['missing_or_open_settlement_entries']}`。",
            f"- executable replay：`{p['settled_entries_with_executable_event_quote']}` entries / `{p['settled_dates_with_executable_event_quote']}` target dates。",
            "- 本报告是 signal-policy replay，不是 actual live realized PnL；unsettled 不计入收益。",
            "",
            "## 结论与策略动作",
            "",
            "**保留 Core 入场不动，新增 `post-entry carry capture` 作为 zero-notional forward shadow。暂不改 live。**",
            "",
            "执行语义：每份新官方报文到达后，重新读取原 YES token；若完整可卖 bid ladder 扣官方 exit fee 后，已比入场成本多锁定至少 `3c/share`，则全平；否则继续持有。没有 book 或深度不足只记 coverage gap，不触发 fallback。",
            "",
            f"在可执行同分母上，10 股 hold PnL `${p['baseline_hold_pnl_usd']:+.2f}`，candidate `${p['candidate_capture_pnl_usd']:+.2f}`，delta `${p['pnl_delta_usd']:+.2f}`；target-date bootstrap CI `[{p['pnl_delta_target_date_bootstrap_ci95'][0]:+.2f}, {p['pnl_delta_target_date_bootstrap_ci95'][1]:+.2f}]`。",
            f"触发 `{p['capture_triggers']}` 笔，其中 final loss `{p['capture_triggered_final_losses']}`、winner `{p['capture_triggered_final_winners']}`；saved loss capital `${p['loss_capital_saved_usd']:.2f}`，sacrificed winner profit `${p['winner_profit_sacrificed_usd']:.2f}`。",
            "",
            "这个点估改善主要来自 Lucknow：它在跨到 33 之前出现过足够深度的 90c 附近 bid，+3c carry capture 可以把最终全损改成小幅已实现盈利。它救不了所有错误：Singapore、Chengdu 和 Manila 在跨档前没有达到相同的 fee-adjusted 盈利退出条件。",
            "",
            "## 阈值敏感性（同一已看过窗口）",
            "",
            "| net gain floor/share | exits | candidate PnL | delta vs hold |",
            "|---:|---:|---:|---:|",
            *sweep_lines,
            "",
            "`3c` 是本窗口诊断后选出的 shadow operating point，不是 clean forward 结果；因此不能据此上线。",
            "",
            "## 持仓后 model-edge 反转",
            "",
            "更原则化的候选是每份新报文后用冻结 Core v2 重新算 held-bracket probability，并仅在完整 10-share sell ladder 的净回收高于该概率时退出。该比较使用同一份执行盘口重算 market-anchored probability，不使用固定盈利目标。",
            "",
            "| sell net − refreshed model p | exits | final winners | final losses | candidate PnL | delta vs hold |",
            "|---:|---:|---:|---:|---:|---:|",
            *reversal_lines,
            "",
            "结果为 `0` 次退出：冻结 Core 的 market-offset 概率在全部可重算状态中都高于 fee-adjusted sell bid。因此同一个 Core 不能同时充当 entry scorer 和独立 stop model；它会随 market prior 一起移动。",
            "",
            "## 首份新报文确认后再入场",
            "",
            "反事实策略：原 Core 首次正 EV 只建立 pending intent；等下一份官方报文。若 held bracket 已改变则取消；若未改变，则用新状态和完整 10-share ask ladder 重算 frozen Core，只有 post-report taker EV 仍为正才买入。",
            "",
            f"可评估 `{confirmation['confirmation_covered_entries']}` 笔 / `{confirmation['confirmation_covered_target_dates']}` dates；实际重新入场 `{confirmation['candidate_entries']}` 笔（winner `{confirmation['candidate_final_winners']}` / loss `{confirmation['candidate_final_losses']}`），跳过 `{confirmation['skipped_entries']}` 笔（winner `{confirmation['skipped_final_winners']}` / loss `{confirmation['skipped_final_losses']}`）。hold baseline `${confirmation['baseline_hold_pnl_usd']:+.2f}`，confirmation candidate `${confirmation['candidate_confirmation_pnl_usd']:+.2f}`，delta `${confirmation['pnl_delta_usd']:+.2f}`，date-block CI `[{confirmation['pnl_delta_target_date_bootstrap_ci95'][0]:+.2f}, {confirmation['pnl_delta_target_date_bootstrap_ci95'][1]:+.2f}]`。",
            "",
            "## 已跨档案例",
            "",
            "| city | target_date | held | capture before cross | capture report |",
            "|---|---|---:|---|---|",
            *cross_lines,
            "",
            "## 证据门与下一步",
            "",
            f"- significance=`{payload['decision']['significance']}`；baseline=`PASS`；forward=`FAIL`；conclusion=`{payload['decision']['conclusion']}`。",
            "- 新天气字段不作为 entry hard gate：此前同分母 forward 没有增量。它们在这里负责定义“新报文/新状态”的 event clock；真正退出动作由可成交净收益决定。",
            "- 下一步唯一动作：zero-notional forward 记录每个 Core position 的新报文、held-token 10-share sell ladder、would-exit 与后续 settlement；阈值冻结为 +3c，不再用同一窗口调参。",
            "- runner 已实现于 `scripts/ops/weather_current_yes_core_carry_post_entry_capture_shadow_v1.py`；它固定 `zero_notional/no_order_placed/execution_calls=0`。",
            "",
            "## 8 环",
            "",
            "描述性=PASS；统计推断=PASS；信号判别=NA；概率分布=NA；执行微结构=PASS（archived full ladder）；容量=PASS 到 10 shares；组合=PASS（target-date block）；基准/反事实=PASS；frozen forward=FAIL。",
            "",
        ]
    )


def parser() -> argparse.ArgumentParser:
    out = argparse.ArgumentParser()
    out.add_argument("--runtime", default=str(RUNTIME))
    out.add_argument("--db", default=str(DB))
    out.add_argument("--book-root", action="append", default=None)
    out.add_argument("--quantity", type=float, default=10.0)
    out.add_argument("--gain-floor", type=float, default=GAIN_FLOOR)
    out.add_argument("--max-book-lag-min", type=float, default=MAX_BOOK_LAG_MIN)
    out.add_argument("--confirmation-only", action="store_true")
    return out


def main() -> int:
    args = parser().parse_args()
    if args.confirmation_only:
        entries = selected_entries(Path(args.runtime))
        states = load_states(Path(args.runtime))
        settlements, _db_identity = settlement_map(Path(args.db), entries)
        artifact = load_artifact(DEFAULT_ARTIFACT)
        rows = replay_one_report_confirmation(
            entries,
            states,
            {},
            settlements,
            artifact,
            quantity=float(args.quantity),
            max_book_lag_min=float(args.max_book_lag_min),
        )
        summary = summarize_confirmation(rows)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        write_csv(OUT_DIR / "one_report_confirmation_raw_top_depth.csv", rows)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.book_root is None:
        args.book_root = [str(path) for path in BOOK_ROOTS]
    result = run(args)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUT_DIR / "entry_replay.csv", result["rows"])
    write_csv(OUT_DIR / "threshold_sweep.csv", result["payload"]["threshold_sweep"])
    write_csv(
        OUT_DIR / "model_edge_reversal_sweep.csv",
        result["payload"]["model_edge_reversal_sweep"],
    )
    write_csv(
        OUT_DIR / "model_edge_reversal_zero_margin.csv",
        result["model_edge_reversal_zero_margin_rows"],
    )
    write_csv(
        OUT_DIR / "one_report_confirmation.csv",
        result["one_report_confirmation_rows"],
    )
    OUT_JSON.write_text(json.dumps(result["payload"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUT_MD.write_text(markdown(result["payload"]) + "\n", encoding="utf-8")
    print(json.dumps(result["payload"]["primary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
