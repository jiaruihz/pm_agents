"""Reconstruct maker-quote touch paths from append-only REST market books.

The historical collector did not persist our own order lifecycle.  This module
therefore never calls a displayed ask touch a fill.  It does preserve the
first observed touch clock and measures liquidation from that clock, which is
strictly stronger than the old ``window_min_ask`` / ``event+60m`` label.
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd
from weather_clock_contract import parse_utc_or_none


@dataclass(frozen=True)
class RestQuote:
    epoch: float
    bid: float
    ask: float
    bid_size: float | None
    ask_size: float | None
    available_at_utc: str
    clock_lineage_status: str
    event_time_pit_scorable: bool
    source_path: str
    capture_id: str | None


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_utc(value: Any) -> datetime | None:
    return parse_utc_or_none(value, field="rest_quote_clock")


def _utc_text(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _effective_yes_quote(rows: Sequence[Mapping[str, Any]], path: Path) -> RestQuote | None:
    books = {str(row.get("outcome") or "").lower(): row for row in rows}
    yes_row = books.get("yes") or {}
    no_row = books.get("no") or {}
    yes = dict(yes_row.get("summary") or {})
    no = dict(no_row.get("summary") or {})
    yes_bid, yes_ask = _finite(yes.get("best_bid")), _finite(yes.get("best_ask"))
    no_bid, no_ask = _finite(no.get("best_bid")), _finite(no.get("best_ask"))
    bids = [
        value
        for value in (
            (yes_bid, _finite(yes.get("bid_size"))) if yes_bid is not None else None,
            (1.0 - no_ask, _finite(no.get("ask_size"))) if no_ask is not None else None,
        )
        if value is not None
    ]
    asks = [
        value
        for value in (
            (yes_ask, _finite(yes.get("ask_size"))) if yes_ask is not None else None,
            (1.0 - no_bid, _finite(no.get("bid_size"))) if no_bid is not None else None,
        )
        if value is not None
    ]
    if not bids or not asks:
        return None
    bid, bid_size = max(bids, key=lambda value: value[0])
    ask, ask_size = min(asks, key=lambda value: value[0])
    if not (0.0 <= bid <= ask <= 1.0):
        return None

    exact = bool(rows) and all(
        row.get("event_time_pit_scorable") is True
        and row.get("available_at_utc")
        and row.get("request_started_at_utc")
        and row.get("response_received_at_utc")
        and row.get("parsed_at_utc")
        for row in rows
    )
    clock_field = "available_at_utc" if exact else "fetched_at_utc"
    clocks = [_parse_utc(row.get(clock_field)) for row in rows]
    observed = [clock for clock in clocks if clock is not None]
    if not observed:
        return None
    available = max(observed)
    return RestQuote(
        epoch=available.timestamp(),
        bid=float(bid),
        ask=float(ask),
        bid_size=bid_size,
        ask_size=ask_size,
        available_at_utc=available.isoformat().replace("+00:00", "Z"),
        clock_lineage_status=(
            "collector_exact_response_clock"
            if exact
            else "legacy_fetched_at_clock_not_request_response_separated"
        ),
        event_time_pit_scorable=exact,
        source_path=str(path),
        capture_id=next(
            (
                str(row.get("request_batch_capture_id") or row.get("book_capture_id"))
                for row in rows
                if row.get("request_batch_capture_id") or row.get("book_capture_id")
            ),
            None,
        ),
    )


def _parse_relevant_file(
    item: tuple[str, frozenset[str]],
) -> tuple[dict[str, list[RestQuote]], dict[str, int]]:
    path = Path(item[0])
    relevant = item[1]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    counters = {"files": 1, "rows": 0, "relevant_rows": 0, "quotes": 0}
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                counters["rows"] += 1
                condition = str(row.get("condition_id") or "")
                if (
                    condition in relevant
                    and row.get("status") == "ok"
                    and str(row.get("outcome") or "").lower() in {"yes", "no"}
                ):
                    grouped[condition].append(row)
                    counters["relevant_rows"] += 1
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        counters["invalid_files"] = 1
        return {}, counters
    output: dict[str, list[RestQuote]] = {}
    for condition, rows in grouped.items():
        quote = _effective_yes_quote(rows, path)
        if quote is not None:
            output[condition] = [quote]
            counters["quotes"] += 1
            key = "exact_quotes" if quote.event_time_pit_scorable else "legacy_clock_quotes"
            counters[key] = counters.get(key, 0) + 1
    return output, counters


def discover_market_book_paths(
    roots: Iterable[Path], *, start_date: str, end_date: str
) -> list[Path]:
    paths: list[Path] = []
    for root in roots:
        for day in sorted(root.glob("20??-??-??")):
            if start_date <= day.name <= end_date:
                paths.extend(sorted(day.glob("*.jsonl")))
                paths.extend(sorted(day.glob("*.jsonl.gz")))
    return sorted(set(paths))


def load_relevant_quote_history(
    paths: Sequence[Path],
    relevant_conditions: Iterable[str],
    *,
    workers: int = 4,
) -> tuple[dict[str, list[RestQuote]], dict[str, int]]:
    relevant = frozenset(str(value) for value in relevant_conditions if str(value))
    histories: dict[str, list[RestQuote]] = defaultdict(list)
    counters: dict[str, int] = defaultdict(int)
    items = [(str(path), relevant) for path in paths]
    if workers <= 1:
        parsed = map(_parse_relevant_file, items)
    else:
        executor = ProcessPoolExecutor(max_workers=workers)
        parsed = executor.map(_parse_relevant_file, items, chunksize=8)
    try:
        for local, local_counts in parsed:
            for key, value in local_counts.items():
                counters[key] += int(value)
            for condition, quotes in local.items():
                histories[condition].extend(quotes)
    finally:
        if workers > 1:
            executor.shutdown()
    for quotes in histories.values():
        quotes.sort(key=lambda quote: (quote.epoch, quote.source_path))
    counters["conditions"] = len(histories)
    return dict(histories), dict(counters)


def attach_first_touch_labels(
    actions: pd.DataFrame,
    histories: Mapping[str, Sequence[RestQuote]],
    *,
    touch_horizon_min: float = 30.0,
    liquidation_horizon_min: float = 60.0,
    diagnostic_liquidation_horizons_min: Sequence[float] = (5.0, 15.0, 30.0, 60.0, 120.0),
    endpoint_tolerance_min: float = 12.0,
    liquidation_tolerance_min: float = 15.0,
    fee_rate: float = 0.05,
) -> pd.DataFrame:
    """Attach first-observed-touch and fill-relative liquidation labels.

    A non-touch is scoreable only when the REST path brackets both ends of the
    30-minute posting window.  This is an evidence coverage rule, not a trade
    eligibility filter.  Touch remains a proxy because queue and own-order
    lifecycle are absent.
    """

    rows: list[dict[str, Any]] = []
    for source in actions.to_dict("records"):
        row = dict(source)
        entry = float(row["snapshot_epoch"])
        quote_price = float(row["quote_price"])
        history = list(histories.get(str(row["condition_id"]), ()))
        end = entry + touch_horizon_min * 60.0
        window = [quote for quote in history if entry < quote.epoch <= end]
        start_gap = (window[0].epoch - entry) / 60.0 if window else math.nan
        end_gap = (end - window[-1].epoch) / 60.0 if window else math.nan
        coverage_complete = bool(
            len(window) >= 3
            and start_gap <= endpoint_tolerance_min
            and end_gap <= endpoint_tolerance_min
        )
        touches = [quote for quote in window if quote.ask <= quote_price + 1e-12]
        touch = touches[0] if touches else None
        row.update(
            {
                "path_window_quote_count": len(window),
                "path_window_start_gap_min": start_gap,
                "path_window_end_gap_min": end_gap,
                "path_window_coverage_complete": coverage_complete,
                "first_touch_at_utc": touch.available_at_utc if touch else None,
                "first_touch_after_min": (
                    (touch.epoch - entry) / 60.0 if touch else math.nan
                ),
                "first_touch_clock_lineage_status": (
                    touch.clock_lineage_status if touch else None
                ),
                "first_touch_source_path": touch.source_path if touch else None,
                "first_touch_capture_id": touch.capture_id if touch else None,
                "path_touch_proxy": True if touch else (False if coverage_complete else None),
                "fill_relative_exit_at_utc": None,
                "fill_relative_exit_bid": math.nan,
                "fill_relative_exit_gap_min": math.nan,
                "fill_relative_exit_clock_lineage_status": None,
                "fill_relative_net_pnl_60": math.nan,
                "path_expected_pnl_label": math.nan,
                "path_label_status": "coverage_blocked",
                "path_clock_grade": (
                    "collector_exact"
                    if window and all(q.event_time_pit_scorable for q in window)
                    else "legacy_fetched_at"
                    if window
                    else "missing"
                ),
            }
        )
        if touch is None:
            if coverage_complete:
                row["path_expected_pnl_label"] = 0.0
                row["path_label_status"] = "scoreable_no_observed_touch"
            rows.append(row)
            continue

        diagnostic_exits: dict[float, tuple[RestQuote | None, float]] = {}
        for horizon in diagnostic_liquidation_horizons_min:
            target = touch.epoch + float(horizon) * 60.0
            candidates = [
                candidate
                for candidate in history
                if target <= candidate.epoch <= target + liquidation_tolerance_min * 60.0
            ]
            candidate = candidates[0] if candidates else None
            gap = (candidate.epoch - target) / 60.0 if candidate else math.nan
            diagnostic_exits[float(horizon)] = (candidate, gap)
            prefix = f"fill_relative_h{int(horizon)}"
            valid = candidate is not None and gap <= liquidation_tolerance_min
            net = (
                candidate.bid
                - fee_rate * candidate.bid * (1.0 - candidate.bid)
                - quote_price
                if valid
                else math.nan
            )
            row[f"{prefix}_exit_at_utc"] = candidate.available_at_utc if valid else None
            row[f"{prefix}_exit_bid"] = candidate.bid if valid else math.nan
            row[f"{prefix}_exit_gap_min"] = gap
            row[f"{prefix}_net_pnl"] = net
        exit_quote, exit_gap = diagnostic_exits[float(liquidation_horizon_min)]
        if exit_quote is None or exit_gap > liquidation_tolerance_min:
            row["path_label_status"] = "touch_without_fill_relative_exit"
            rows.append(row)
            continue
        net_bid = exit_quote.bid - fee_rate * exit_quote.bid * (1.0 - exit_quote.bid)
        pnl = net_bid - quote_price
        row.update(
            {
                "fill_relative_exit_at_utc": exit_quote.available_at_utc,
                "fill_relative_exit_bid": exit_quote.bid,
                "fill_relative_exit_gap_min": exit_gap,
                "fill_relative_exit_clock_lineage_status": exit_quote.clock_lineage_status,
                "fill_relative_net_pnl_60": pnl,
                "path_expected_pnl_label": pnl,
                "path_label_status": "scoreable_observed_touch_fill_relative_exit",
                "path_clock_grade": (
                    "collector_exact"
                    if touch.event_time_pit_scorable and exit_quote.event_time_pit_scorable
                    else "legacy_fetched_at"
                ),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)
