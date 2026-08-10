#!/usr/bin/env python3
"""PIT replay for an LMVM-style D-2/D-1 single-YES repricing strategy.

The strategy is intentionally defined before looking at its results:

* a signal clock is a first-seen change in ``forecast_values_hash`` (or the
  documented legacy fallback state key);
* the universe is every complete D-2/D-1 city-day ladder at that clock;
* the primary arm selects exactly one YES rung, the largest
  ``model_probability - executable_ask - entry_fee``;
* exits are fixed-horizon taker sells to the first observed bid after
  5/15/30/60/120 minutes, including entry and exit Weather fees;
* forecast-mode and market-favorite arms are retained on the same rows.

This is research-only.  It does not write canonical facts, plans, orders, or
production configuration.
"""

from __future__ import annotations

import argparse
import bisect
import gzip
import hashlib
import json
import math
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed import city_timezone_name  # noqa: E402
from weather_data_feed.production_paths import historical_strategy_snapshots  # noqa: E402


DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_SNAPSHOTS = historical_strategy_snapshots()
DEFAULT_OUTPUT = ROOT / "docs/analysis/2026-08/generated/lmvm_single_yes_repricing_v1"
DEFAULT_REPORT = ROOT / "docs/analysis/2026-08/2026-08-04-research-lmvm-single-yes-repricing-v1.md"
HORIZONS_MIN = (5, 15, 30, 60, 120)
FEE_RATE = 0.05
MIN_EXECUTABLE_SHARES = 5.0
BOOTSTRAP_DRAWS = 5000
BOOTSTRAP_SEED = 20260804


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--draws", type=int, default=BOOTSTRAP_DRAWS)
    parser.add_argument("--max-files", type=int)
    return parser.parse_args()


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def valid_price(value: Any) -> bool:
    result = finite(value)
    return result is not None and 0.001 <= result <= 0.999


def parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def weather_fee_per_share(price: float, fee_rate: float = FEE_RATE) -> float:
    """Official Weather fee curve, rounded at the per-trade reporting layer."""
    return float(price) * (1.0 - float(price)) * float(fee_rate)


def forecast_state_key(record: dict[str, Any]) -> tuple[str, str]:
    value_hash = str(record.get("forecast_values_hash") or "").strip()
    if value_hash:
        return f"hash:{value_hash}", "forecast_values_hash"
    fallback = "|".join(
        [
            f"init:{record.get('model_init_utc_estimated')}",
            f"max:{record.get('forecast_max_f')}",
            f"peak:{record.get('forecast_peak_time_utc')}",
        ]
    )
    return fallback, "legacy_init_max_peak_fallback"


def snapshot_identity(path: Path, city: str, target_date: str, event: str) -> str:
    raw = f"{path.name}|{city}|{target_date}|{event}".encode()
    return hashlib.sha256(raw).hexdigest()


def _first_present(records: list[dict[str, Any]], field: str) -> Any:
    for record in records:
        value = record.get(field)
        if value is not None and str(value).strip():
            return value
    return None


def _historical_orderbook_quotes(path: Path) -> tuple[dict[str, dict[str, Any]], datetime | None]:
    """Load effective YES quotes from a retired same-capture orderbook batch."""

    if not path.exists():
        return {}, None
    opener = gzip.open if path.suffix == ".gz" else open
    sides: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    latest_fetched: datetime | None = None
    try:
        with opener(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("status") != "ok":
                    continue
                condition = str(row.get("condition_id") or "").strip()
                outcome = str(row.get("outcome") or "yes").lower()
                if not condition or outcome not in {"yes", "no"}:
                    continue
                sides[condition][outcome] = dict(row.get("summary") or {})
                fetched = parse_utc(row.get("fetched_at_utc"))
                if fetched is not None and (latest_fetched is None or fetched > latest_fetched):
                    latest_fetched = fetched
    except (OSError, json.JSONDecodeError):
        return {}, None

    quotes: dict[str, dict[str, Any]] = {}
    for condition, books in sides.items():
        yes = books.get("yes") or {}
        no = books.get("no") or {}
        yes_bid = finite(yes.get("best_bid"))
        yes_ask = finite(yes.get("best_ask"))
        no_bid = finite(no.get("best_bid"))
        no_ask = finite(no.get("best_ask"))
        bid_candidates = [
            pair
            for pair in (
                (yes_bid, finite(yes.get("bid_size"))) if yes_bid is not None else None,
                (1.0 - no_ask, finite(no.get("ask_size"))) if no_ask is not None else None,
            )
            if pair is not None
        ]
        ask_candidates = [
            pair
            for pair in (
                (yes_ask, finite(yes.get("ask_size"))) if yes_ask is not None else None,
                (1.0 - no_bid, finite(no.get("bid_size"))) if no_bid is not None else None,
            )
            if pair is not None
        ]
        bid, bid_size = max(bid_candidates, default=(None, None), key=lambda pair: pair[0])
        ask, ask_size = min(ask_candidates, default=(None, None), key=lambda pair: pair[0])
        if bid is not None and ask is not None and ask >= bid:
            quotes[condition] = {
                "yes_best_bid": bid,
                "yes_best_ask": ask,
                "yes_bid_size": bid_size,
                "yes_ask_size": ask_size,
            }
    return quotes, latest_fetched


def _parse_snapshot_payload(
    path: Path,
    payload: dict[str, Any],
    *,
    book_quotes: dict[str, dict[str, Any]] | None = None,
    book_available_at: datetime | None = None,
    book_source_path: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    counts: Counter[str] = Counter(files_read=1)
    snapshot_ts = parse_utc(payload.get("ts_utc") or payload.get("snapshot_ts_utc"))
    records = payload.get("records")
    if snapshot_ts is None or not isinstance(records, list):
        return [], {"files_read": 1, "files_invalid": 1}
    decision_ts = max(snapshot_ts, book_available_at) if book_available_at else snapshot_ts
    evidence_class = (
        "historical_companion_orderbook_same_capture_v1"
        if book_quotes
        else "legacy_inline_snapshot_v1"
    )
    if book_quotes:
        counts["historical_companion_orderbook_files"] += 1
    counts["record_rows"] += len(records)
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if not isinstance(record, dict):
            continue
        city = str(record.get("city") or "").strip()
        target = str(record.get("target_date") or record.get("event_date") or "").strip()
        bracket = str(record.get("bracket") or "").strip()
        if not city or not target or not bracket:
            continue
        event = str(record.get("event_slug") or f"{city}|{target}")
        groups[(city, target, event)].append(record)

    output: list[dict[str, Any]] = []
    for (city, target, event), group in groups.items():
        counts["ladder_groups"] += 1
        tz_name = str(_first_present(group, "timezone_name") or city_timezone_name(city) or "")
        if not tz_name:
            counts["missing_timezone"] += 1
            continue
        try:
            local_ts = decision_ts.astimezone(ZoneInfo(tz_name))
            target_day = date.fromisoformat(target)
        except (ValueError, KeyError):
            counts["invalid_clock"] += 1
            continue
        lead_days = (target_day - local_ts.date()).days
        if lead_days not in {1, 2}:
            continue
        counts[f"d{lead_days}_groups"] += 1

        by_bracket: dict[str, dict[str, Any]] = {}
        duplicate = False
        for record in group:
            bracket = str(record.get("bracket") or "").strip()
            if bracket in by_bracket:
                duplicate = True
                break
            by_bracket[bracket] = record
        if duplicate or len(by_bracket) < 3:
            counts["invalid_ladder"] += 1
            continue

        rungs: list[dict[str, Any]] = []
        complete = True
        for bracket, record in by_bracket.items():
            model_p = finite(record.get("model_prob"))
            condition_id = str(record.get("condition_id") or "").strip()
            quote = (book_quotes or {}).get(condition_id) or {}
            bid = finite(quote.get("yes_best_bid", record.get("yes_best_bid")))
            ask = finite(quote.get("yes_best_ask", record.get("yes_best_ask")))
            bid_size = finite(quote.get("yes_bid_size", record.get("yes_bid_size")))
            ask_size = finite(quote.get("yes_ask_size", record.get("yes_ask_size")))
            if (
                not condition_id
                or model_p is None
                or not valid_price(bid)
                or not valid_price(ask)
                or ask < bid
            ):
                complete = False
            rungs.append(
                {
                    "bracket": bracket,
                    "question": record.get("question"),
                    "condition_id": condition_id,
                    "market_id": record.get("market_id"),
                    "yes_token_id": record.get("yes_token_id"),
                    "tick_size": finite(record.get("tick_size") or record.get("minimum_tick_size")),
                    "model_prob_raw": model_p,
                    "yes_bid": bid,
                    "yes_ask": ask,
                    "yes_bid_size": bid_size,
                    "yes_ask_size": ask_size,
                }
            )
        if not complete:
            counts["incomplete_quote_or_probability"] += 1
            continue
        probability_sum = sum(float(r["model_prob_raw"]) for r in rungs)
        if not 0.80 <= probability_sum <= 1.20:
            counts["invalid_model_probability_mass"] += 1
            continue
        mid_sum = sum((float(r["yes_bid"]) + float(r["yes_ask"])) / 2.0 for r in rungs)
        if not 0.50 <= mid_sum <= 1.50:
            counts["invalid_market_probability_mass"] += 1
            continue
        for rung in rungs:
            rung["model_prob"] = float(rung["model_prob_raw"]) / probability_sum
            rung["market_mid"] = (float(rung["yes_bid"]) + float(rung["yes_ask"])) / 2.0
            rung["market_prob"] = float(rung["market_mid"]) / mid_sum
        state_key, state_basis = forecast_state_key(group[0])
        output.append(
            {
                "snapshot_id": snapshot_identity(path, city, target, event),
                "source_path": str(path),
                "snapshot_ts_utc": iso_utc(decision_ts),
                "source_snapshot_ts_utc": iso_utc(snapshot_ts),
                "snapshot_epoch": decision_ts.timestamp(),
                "decision_local": decision_ts.astimezone(ZoneInfo(tz_name)).isoformat(),
                "decision_hour_local": (
                    decision_ts.astimezone(ZoneInfo(tz_name)).hour
                    + decision_ts.astimezone(ZoneInfo(tz_name)).minute / 60.0
                ),
                "clock_lineage_status": evidence_class,
                "book_source_path": book_source_path,
                "book_available_at_utc": iso_utc(book_available_at) if book_available_at else None,
                "lead_days": lead_days,
                "city": city,
                "target_date": target,
                "event_slug": event,
                "market_timezone": tz_name,
                "forecast_source": _first_present(group, "forecast_source"),
                "forecast_model": _first_present(group, "forecast_model") or _first_present(group, "model"),
                "model_version": _first_present(group, "model_version"),
                "forecast_state_key": state_key,
                "forecast_state_basis": state_basis,
                "model_init_utc_estimated": _first_present(group, "model_init_utc_estimated"),
                "forecast_max_f": finite(_first_present(group, "forecast_max_f")),
                "rung_count": len(rungs),
                "model_probability_sum_raw": probability_sum,
                "market_mid_sum_raw": mid_sum,
                "rungs": rungs,
            }
        )
        counts["complete_d2_d1_ladders"] += 1
    return output, dict(counts)


def parse_snapshot_file(path_text: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Return D-2/D-1 ladder states from one immutable snapshot file."""
    path = Path(path_text)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], {"files_read": 1, "files_invalid": 1}
    return _parse_snapshot_payload(path, payload)


def parse_snapshot_with_orderbook(
    item: tuple[str, str | None],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Parse a snapshot and, when present, its retired same-capture book batch."""

    path = Path(item[0])
    book_path = Path(item[1]) if item[1] else None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], {"files_read": 1, "files_invalid": 1}
    quotes, available_at = (
        _historical_orderbook_quotes(book_path) if book_path is not None else ({}, None)
    )
    return _parse_snapshot_payload(
        path,
        payload,
        book_quotes=quotes,
        book_available_at=available_at,
        book_source_path=str(book_path) if book_path is not None and book_path.exists() else None,
    )


def _stream_key(state: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(state["city"]),
        str(state["target_date"]),
        str(state["event_slug"]),
        str(state.get("forecast_source") or ""),
        str(state.get("forecast_model") or ""),
    )


def annotate_forecast_updates(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark first-seen forecast states; initial left-censored states stay explicit."""
    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for state in states:
        grouped[_stream_key(state)].append(state)
    output: list[dict[str, Any]] = []
    for rows in grouped.values():
        rows.sort(key=lambda row: (row["snapshot_epoch"], row["snapshot_id"]))
        previous: str | None = None
        sequence = 0
        for row in rows:
            current = str(row["forecast_state_key"])
            changed = current != previous
            if changed:
                sequence += 1
            annotated = dict(row)
            annotated["forecast_state_changed"] = changed
            annotated["forecast_state_seq"] = sequence
            annotated["left_censored_initial_state"] = previous is None
            output.append(annotated)
            previous = current
    return sorted(output, key=lambda row: (row["snapshot_epoch"], row["city"], row["target_date"]))


def select_rung(rungs: list[dict[str, Any]], policy: str) -> dict[str, Any]:
    if policy == "residual_argmax":
        key = lambda rung: (
            float(rung["model_prob"])
            - float(rung["yes_ask"])
            - weather_fee_per_share(float(rung["yes_ask"])),
            float(rung["model_prob"]),
            -float(rung["yes_ask"]),
        )
    elif policy == "forecast_mode":
        key = lambda rung: (float(rung["model_prob"]), -float(rung["yes_ask"]))
    elif policy == "market_favorite":
        key = lambda rung: (float(rung["market_prob"]), -float(rung["yes_ask"]))
    else:
        raise ValueError(f"unknown policy: {policy}")
    return max(rungs, key=key)


def build_candidates(states: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    candidates: list[dict[str, Any]] = []
    probability_rows: list[dict[str, Any]] = []
    for state in states:
        if not state["forecast_state_changed"] or state["left_censored_initial_state"]:
            continue
        rungs = state["rungs"]
        probability_rows.append(
            {
                **{key: state[key] for key in (
                    "snapshot_id", "snapshot_ts_utc", "city", "target_date", "lead_days",
                    "forecast_source", "forecast_model", "forecast_state_key", "rung_count",
                )},
                "brackets_json": json.dumps([r["bracket"] for r in rungs], ensure_ascii=False),
                "model_probs_json": json.dumps([r["model_prob"] for r in rungs]),
                "market_probs_json": json.dumps([r["market_prob"] for r in rungs]),
            }
        )
        for policy in ("residual_argmax", "forecast_mode", "market_favorite"):
            selected = select_rung(rungs, policy)
            ask = float(selected["yes_ask"])
            entry_fee = weather_fee_per_share(ask)
            edge = float(selected["model_prob"]) - ask - entry_fee
            candidates.append(
                {
                    **{key: state[key] for key in (
                        "snapshot_id", "source_path", "snapshot_ts_utc", "snapshot_epoch",
                        "decision_local", "decision_hour_local", "lead_days", "city", "target_date",
                        "event_slug", "market_timezone", "forecast_source", "forecast_model",
                        "model_version", "forecast_state_key", "forecast_state_basis",
                        "forecast_state_seq", "model_init_utc_estimated", "forecast_max_f",
                        "rung_count", "model_probability_sum_raw", "market_mid_sum_raw",
                    )},
                    "policy": policy,
                    "condition_id": selected["condition_id"],
                    "bracket": selected["bracket"],
                    "question": selected["question"],
                    "model_prob": selected["model_prob"],
                    "market_prob": selected["market_prob"],
                    "entry_bid": selected["yes_bid"],
                    "entry_ask": ask,
                    "entry_bid_size": selected["yes_bid_size"],
                    "entry_ask_size": selected["yes_ask_size"],
                    "entry_fee_per_share": entry_fee,
                    "model_edge_after_entry_fee": edge,
                    "policy_eligible": policy != "residual_argmax" or edge > 0.0,
                }
            )
    return pd.DataFrame(candidates), pd.DataFrame(probability_rows)


@dataclass(frozen=True)
class Quote:
    epoch: float
    bid: float
    ask: float
    bid_size: float | None
    ask_size: float | None


def quote_history(states: Iterable[dict[str, Any]]) -> dict[str, list[Quote]]:
    grouped: dict[str, dict[float, Quote]] = defaultdict(dict)
    for state in states:
        epoch = float(state["snapshot_epoch"])
        for rung in state["rungs"]:
            condition = str(rung.get("condition_id") or "")
            if not condition:
                continue
            grouped[condition][epoch] = Quote(
                epoch=epoch,
                bid=float(rung["yes_bid"]),
                ask=float(rung["yes_ask"]),
                bid_size=finite(rung.get("yes_bid_size")),
                ask_size=finite(rung.get("yes_ask_size")),
            )
    return {key: [rows[epoch] for epoch in sorted(rows)] for key, rows in grouped.items()}


def horizon_tolerance_min(horizon: int) -> float:
    return float(min(30, max(10, horizon / 2)))


def first_quote_after(
    quotes: list[Quote], entry_epoch: float, horizon_min: int
) -> tuple[Quote | None, float | None]:
    target = entry_epoch + horizon_min * 60.0
    epochs = [quote.epoch for quote in quotes]
    index = bisect.bisect_left(epochs, target)
    if index >= len(quotes):
        return None, None
    quote = quotes[index]
    gap_min = (quote.epoch - target) / 60.0
    if gap_min > horizon_tolerance_min(horizon_min):
        return None, gap_min
    return quote, gap_min


def attach_markouts(candidates: pd.DataFrame, histories: dict[str, list[Quote]]) -> pd.DataFrame:
    if candidates.empty:
        return candidates.copy()
    rows: list[dict[str, Any]] = []
    for candidate in candidates.to_dict("records"):
        output = dict(candidate)
        history = histories.get(str(candidate["condition_id"]), [])
        entry_cost = float(candidate["entry_ask"]) + float(candidate["entry_fee_per_share"])
        entry_epoch = float(candidate["snapshot_epoch"])
        entry_bid = finite(candidate.get("entry_bid"))
        for horizon in HORIZONS_MIN:
            quote, gap = first_quote_after(history, entry_epoch, horizon)
            prefix = f"h{horizon}"
            output[f"{prefix}_quote_gap_min"] = gap
            if quote is None:
                for suffix in (
                    "bid", "ask", "bid_size", "ask_size", "exit_fee_per_share",
                    "net_markout_per_share", "net_markout_roi", "executable_shares",
                    "net_pnl_usd", "window_min_bid", "window_max_bid",
                    "window_min_ask", "window_max_ask", "window_quote_count",
                    "maker_bid_touch", "maker_bid_touch_after_min",
                ):
                    output[f"{prefix}_{suffix}"] = math.nan
                continue
            window = [
                item for item in history
                if entry_epoch < item.epoch <= quote.epoch
            ]
            touched = [] if entry_bid is None else [
                item for item in window if item.ask <= entry_bid + 1e-12
            ]
            exit_fee = weather_fee_per_share(quote.bid)
            net = quote.bid - exit_fee - entry_cost
            sizes = [MIN_EXECUTABLE_SHARES]
            if finite(candidate.get("entry_ask_size")) is not None:
                sizes.append(float(candidate["entry_ask_size"]))
            if quote.bid_size is not None:
                sizes.append(float(quote.bid_size))
            executable = max(0.0, min(sizes))
            output[f"{prefix}_bid"] = quote.bid
            output[f"{prefix}_ask"] = quote.ask
            output[f"{prefix}_bid_size"] = quote.bid_size
            output[f"{prefix}_ask_size"] = quote.ask_size
            output[f"{prefix}_exit_fee_per_share"] = exit_fee
            output[f"{prefix}_net_markout_per_share"] = net
            output[f"{prefix}_net_markout_roi"] = net / entry_cost if entry_cost else math.nan
            output[f"{prefix}_executable_shares"] = executable
            output[f"{prefix}_net_pnl_usd"] = net * executable
            output[f"{prefix}_window_min_bid"] = min(item.bid for item in window)
            output[f"{prefix}_window_max_bid"] = max(item.bid for item in window)
            output[f"{prefix}_window_min_ask"] = min(item.ask for item in window)
            output[f"{prefix}_window_max_ask"] = max(item.ask for item in window)
            output[f"{prefix}_window_quote_count"] = len(window)
            output[f"{prefix}_maker_bid_touch"] = bool(touched)
            output[f"{prefix}_maker_bid_touch_after_min"] = (
                (touched[0].epoch - entry_epoch) / 60.0 if touched else math.nan
            )
        rows.append(output)
    return pd.DataFrame(rows)


def quote_at_epoch(
    quotes: list[Quote], epoch: float, *, tolerance_seconds: float = 1.0
) -> Quote | None:
    epochs = [quote.epoch for quote in quotes]
    index = bisect.bisect_left(epochs, epoch)
    if index >= len(quotes):
        return None
    quote = quotes[index]
    return quote if abs(quote.epoch - epoch) <= tolerance_seconds else None


def attach_full_ladder_completion(
    rungs: pd.DataFrame,
    histories: dict[str, list[Quote]],
    *,
    requested_shares: float = MIN_EXECUTABLE_SHARES,
    hedge_slippage_per_leg: float = 0.001,
) -> pd.DataFrame:
    """Price an exhaustive YES completion after one hypothetical maker fill.

    ``touch_completion_*`` is conditional on the own rung's ask trading down
    to the posted best bid within 60 minutes.  It remains a trade-through
    proxy, not an inferred fill.  The other rungs are priced at the exact same
    archived ladder epoch and pay taker fees plus one tick per hedge leg.
    """

    if rungs.empty:
        return rungs.copy()
    output = rungs.copy()
    result_rows: list[dict[str, Any]] = []
    for _, event in output.groupby("forecast_event_id", sort=False):
        event_rows = event.to_dict(orient="records")
        entry_ask_sum = sum(float(row["entry_ask"]) for row in event_rows)
        entry_fee_sum = sum(
            weather_fee_per_share(float(row["entry_ask"])) for row in event_rows
        )
        for row in event_rows:
            item = dict(row)
            own_ask = float(row["entry_ask"])
            own_bid = float(row["entry_bid"])
            entry_other_fee = entry_fee_sum - weather_fee_per_share(own_ask)
            item["entry_completion_cost"] = (
                own_bid + entry_ask_sum - own_ask + entry_other_fee
            )
            item["entry_completion_margin"] = 1.0 - item["entry_completion_cost"]
            touch_after = finite(row.get("h60_maker_bid_touch_after_min"))
            touch_fields = {
                "touch_completion_epoch": math.nan,
                "touch_completion_rungs": math.nan,
                "touch_completion_other_ask_sum": math.nan,
                "touch_completion_other_fee_sum": math.nan,
                "touch_completion_cost": math.nan,
                "touch_completion_margin": math.nan,
                "touch_completion_cost_1tick_per_hedge_leg": math.nan,
                "touch_completion_margin_1tick_per_hedge_leg": math.nan,
                "touch_completion_depth_shares": math.nan,
                "touch_completion_5share_executable": math.nan,
                "touch_completion_5share_locked_pnl": math.nan,
            }
            if touch_after is not None:
                touch_epoch = float(row["snapshot_epoch"]) + touch_after * 60.0
                other_quotes: list[Quote] = []
                complete = True
                for other in event_rows:
                    if str(other["condition_id"]) == str(row["condition_id"]):
                        continue
                    quote = quote_at_epoch(
                        histories.get(str(other["condition_id"]), []), touch_epoch
                    )
                    if quote is None:
                        complete = False
                        break
                    other_quotes.append(quote)
                if complete and len(other_quotes) == len(event_rows) - 1:
                    other_ask_sum = sum(quote.ask for quote in other_quotes)
                    other_fee_sum = sum(
                        weather_fee_per_share(quote.ask) for quote in other_quotes
                    )
                    cost = own_bid + other_ask_sum + other_fee_sum
                    stressed_cost = cost + hedge_slippage_per_leg * len(other_quotes)
                    depth_values = [
                        quote.ask_size for quote in other_quotes if quote.ask_size is not None
                    ]
                    depth = min(depth_values) if len(depth_values) == len(other_quotes) else 0.0
                    margin = 1.0 - cost
                    stressed_margin = 1.0 - stressed_cost
                    executable = depth >= requested_shares
                    touch_fields = {
                        "touch_completion_epoch": touch_epoch,
                        "touch_completion_rungs": len(event_rows),
                        "touch_completion_other_ask_sum": other_ask_sum,
                        "touch_completion_other_fee_sum": other_fee_sum,
                        "touch_completion_cost": cost,
                        "touch_completion_margin": margin,
                        "touch_completion_cost_1tick_per_hedge_leg": stressed_cost,
                        "touch_completion_margin_1tick_per_hedge_leg": stressed_margin,
                        "touch_completion_depth_shares": depth,
                        "touch_completion_5share_executable": executable,
                        "touch_completion_5share_locked_pnl": (
                            stressed_margin * requested_shares if executable else math.nan
                        ),
                    }
            item.update(touch_fields)
            result_rows.append(item)
    return pd.DataFrame(result_rows)


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def load_winners(path: Path) -> dict[tuple[str, str], str]:
    query = """
    SELECT city, target_date, bracket
    FROM settlement_outcomes
    WHERE source_system='pm_history'
      AND settlement_status='settled'
      AND final_price >= 0.999
    """
    with connect_ro(path) as conn:
        rows = conn.execute(query).fetchall()
    winners: dict[tuple[str, str], str] = {}
    duplicates: set[tuple[str, str]] = set()
    for city, target, bracket in rows:
        key = (str(city), str(target))
        if key in winners and winners[key] != str(bracket):
            duplicates.add(key)
        winners[key] = str(bracket)
    for key in duplicates:
        winners.pop(key, None)
    return winners


def score_probabilities(rows: pd.DataFrame, winners: dict[tuple[str, str], str]) -> pd.DataFrame:
    if rows.empty:
        return rows.copy()
    output = []
    for row in rows.to_dict("records"):
        winner = winners.get((str(row["city"]), str(row["target_date"])))
        brackets = json.loads(row["brackets_json"])
        model = np.asarray(json.loads(row["model_probs_json"]), dtype=float)
        market = np.asarray(json.loads(row["market_probs_json"]), dtype=float)
        scored = dict(row)
        scored["winner_bracket"] = winner
        scored["settlement_status"] = "settled" if winner in brackets else ("missing_winner" if winner else "unsettled")
        if winner not in brackets:
            for field in ("model_brier", "market_brier", "model_logloss", "market_logloss"):
                scored[field] = math.nan
        else:
            actual = brackets.index(winner)
            target = np.zeros(len(brackets), dtype=float)
            target[actual] = 1.0
            scored["model_brier"] = float(np.mean((model - target) ** 2))
            scored["market_brier"] = float(np.mean((market - target) ** 2))
            scored["model_logloss"] = float(-math.log(max(float(model[actual]), 1e-12)))
            scored["market_logloss"] = float(-math.log(max(float(market[actual]), 1e-12)))
        output.append(scored)
    return pd.DataFrame(output)


def block_bootstrap_ratio(
    rows: pd.DataFrame,
    numerator: str,
    denominator: str,
    draws: int,
    seed: int,
) -> tuple[float, float, float, int]:
    usable = rows[["target_date", numerator, denominator]].dropna()
    if usable.empty:
        return math.nan, math.nan, math.nan, 0
    by_date = usable.groupby("target_date", as_index=False).agg(
        numerator=(numerator, "sum"), denominator=(denominator, "sum")
    )
    total_denominator = float(by_date["denominator"].sum())
    point = float(by_date["numerator"].sum() / total_denominator) if total_denominator else math.nan
    if len(by_date) < 3:
        return point, math.nan, math.nan, len(by_date)
    values = by_date[["numerator", "denominator"]].to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    boot = []
    for _ in range(draws):
        sample = values[rng.integers(0, len(values), size=len(values))]
        denominator_sum = float(sample[:, 1].sum())
        if denominator_sum:
            boot.append(float(sample[:, 0].sum() / denominator_sum))
    return point, float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5)), len(by_date)


def summarize_markouts(rows: pd.DataFrame, draws: int) -> pd.DataFrame:
    summaries: list[dict[str, Any]] = []
    if rows.empty:
        return pd.DataFrame()
    for (policy, lead_days), group in rows.groupby(["policy", "lead_days"]):
        policy_group = group[group["policy_eligible"]].copy()
        for horizon in HORIZONS_MIN:
            prefix = f"h{horizon}"
            usable = policy_group[policy_group[f"{prefix}_net_pnl_usd"].notna()].copy()
            usable["entry_cost_usd"] = (
                usable["entry_ask"] + usable["entry_fee_per_share"]
            ) * usable[f"{prefix}_executable_shares"]
            point, low, high, dates = block_bootstrap_ratio(
                usable,
                f"{prefix}_net_pnl_usd",
                "entry_cost_usd",
                draws,
                BOOTSTRAP_SEED + horizon + int(lead_days),
            )
            summaries.append(
                {
                    "policy": policy,
                    "lead_days": int(lead_days),
                    "horizon_min": horizon,
                    "signals": len(policy_group),
                    "markout_rows": len(usable),
                    "markout_coverage": len(usable) / len(policy_group) if len(policy_group) else math.nan,
                    "target_dates": dates,
                    "positive_rate": float((usable[f"{prefix}_net_pnl_usd"] > 0).mean()) if len(usable) else math.nan,
                    "turnover_roi": point,
                    "ci_low": low,
                    "ci_high": high,
                }
            )
    return pd.DataFrame(summaries)


def probability_summary(rows: pd.DataFrame) -> dict[str, Any]:
    settled = rows[rows["settlement_status"].eq("settled")].copy() if not rows.empty else rows
    if settled.empty:
        return {"rows": 0, "target_dates": 0}
    rng = np.random.default_rng(BOOTSTRAP_SEED + 909)

    def paired_date_ci(column: str, draws: int = 2_000) -> tuple[float, float]:
        grouped = settled.assign(_delta=settled[column]).groupby("target_date")["_delta"].agg(["sum", "count"])
        if len(grouped) < 2:
            return math.nan, math.nan
        sums = grouped["sum"].to_numpy(dtype=float)
        counts = grouped["count"].to_numpy(dtype=float)
        sampled = rng.integers(0, len(grouped), size=(draws, len(grouped)))
        estimates = sums[sampled].sum(axis=1) / counts[sampled].sum(axis=1)
        return float(np.quantile(estimates, 0.025)), float(np.quantile(estimates, 0.975))

    settled["brier_delta"] = settled["model_brier"] - settled["market_brier"]
    settled["logloss_delta"] = settled["model_logloss"] - settled["market_logloss"]
    brier_low, brier_high = paired_date_ci("brier_delta")
    logloss_low, logloss_high = paired_date_ci("logloss_delta")
    return {
        "rows": len(settled),
        "target_dates": int(settled["target_date"].nunique()),
        "model_brier": float(settled["model_brier"].mean()),
        "market_brier": float(settled["market_brier"].mean()),
        "brier_delta_model_minus_market": float(settled["brier_delta"].mean()),
        "brier_delta_ci_low": brier_low,
        "brier_delta_ci_high": brier_high,
        "model_logloss": float(settled["model_logloss"].mean()),
        "market_logloss": float(settled["market_logloss"].mean()),
        "logloss_delta_model_minus_market": float(settled["logloss_delta"].mean()),
        "logloss_delta_ci_low": logloss_low,
        "logloss_delta_ci_high": logloss_high,
    }


def md_pct(value: Any) -> str:
    number = finite(value)
    return "NA" if number is None else f"{100 * number:+.2f}%"


def write_report(
    path: Path,
    snapshot_dir: Path,
    db_path: Path,
    counters: dict[str, int],
    states: list[dict[str, Any]],
    candidates: pd.DataFrame,
    probabilities: pd.DataFrame,
    markout_summary: pd.DataFrame,
    generated_at: str,
) -> None:
    primary = candidates[(candidates["policy"].eq("residual_argmax")) & candidates["policy_eligible"]] if not candidates.empty else candidates
    prob = probability_summary(probabilities)
    complete = counters.get("complete_d2_d1_ladders", 0)
    changed = (
        sum(1 for row in states if row.get("forecast_state_changed") and not row.get("left_censored_initial_state"))
        if states
        else counters.get("forecast_update_events", 0)
    )
    report_rows = []
    for row in markout_summary.to_dict("records"):
        report_rows.append(
            f"| {row['policy']} | D-{row['lead_days']} | {row['horizon_min']}m | {row['markout_rows']}/{row['signals']} | "
            f"{md_pct(row['turnover_roi'])} | [{md_pct(row['ci_low'])}, {md_pct(row['ci_high'])}] |"
        )
    diagnostics = []
    for lead_days in (1, 2):
        lead = primary[primary["lead_days"].eq(lead_days)].copy()
        covered = lead[lead["h60_bid"].notna()].copy()
        cost = (covered["entry_ask"] + covered["entry_fee_per_share"]) * covered["h60_executable_shares"]
        roi = covered["h60_net_pnl_usd"].sum() / cost.sum() if cost.sum() else math.nan
        diagnostics.append(
            f"| D-{lead_days} | {len(lead):,} | {lead['entry_ask'].median():.3f} | "
            f"{(lead['entry_ask'] - lead['entry_bid']).median():.3f} | {len(covered):,} | "
            f"{100 * ((covered['h60_bid'] - covered['entry_ask']) > 0).mean():.2f}% | {md_pct(roi)} |"
        )
    old_price_band = primary[
        primary["entry_ask"].between(0.20, 0.40, inclusive="left") & primary["h60_bid"].notna()
    ].copy()
    old_price_band_cost = (
        (old_price_band["entry_ask"] + old_price_band["entry_fee_per_share"])
        * old_price_band["h60_executable_shares"]
    )
    old_price_band_roi = (
        old_price_band["h60_net_pnl_usd"].sum() / old_price_band_cost.sum()
        if old_price_band_cost.sum()
        else math.nan
    )
    db_real = db_path.resolve()
    stat = db_real.stat()
    text = f"""# LMVM-style D-2/D-1 单档 YES repricing v1

## 数据快照

| 字段 | 值 |
|---|---|
| 数据源 | immutable paper snapshots `{snapshot_dir}` + canonical settlement `{db_real}` |
| 数据快照时间 | {generated_at} |
| DB identity | device={stat.st_dev}, inode={stat.st_ino}, mtime={datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()} |
| snapshot files | {counters.get('files_read', 0):,}（invalid={counters.get('files_invalid', 0):,}） |
| D-2/D-1 complete ladders | {complete:,} |
| forecast update events | {changed:,}（left-censored initial state 已剔除主分母） |
| single-rung candidates | {len(candidates):,} across 3 same-row arms；primary eligible={len(primary):,} |
| unsettled | {int((~probabilities['settlement_status'].eq('settled')).sum()) if not probabilities.empty else 0:,} / {len(probabilities):,} update states |
| missing_bracket | {int(probabilities['settlement_status'].eq('missing_winner').sum()) if not probabilities.empty else 0:,} |

## 冻结问题与动作

目标是在所有 D-2/D-1 forecast first-seen update 的固定 city-day ladder 分母上，检验
`P(final exact bracket)-market` residual 是否能预测未来固定时限的可执行 repricing。
当前动作固定为 **research / zero-notional**；本脚本不改 live、不会产生 order。

```text
signal funnel:
snapshot files -> D-2/D-1 complete ladders -> non-left-censored forecast update
-> one residual-argmax YES per city-day/update -> positive after-entry-fee residual

evidence funnel:
PIT model probability -> same-snapshot complete direct book -> executable taker ask/depth
-> 5/15/30/60/120m first observed bid -> double-sided Weather fee -> settlement label
```

## 概率层：同 rows market baseline

| metric | model | market | model-market delta | target-date 95% CI |
|---|---:|---:|---:|---:|
| Brier | {prob.get('model_brier', math.nan):.6f} | {prob.get('market_brier', math.nan):.6f} | {prob.get('brier_delta_model_minus_market', math.nan):+.6f} | [{prob.get('brier_delta_ci_low', math.nan):+.6f}, {prob.get('brier_delta_ci_high', math.nan):+.6f}] |
| logloss | {prob.get('model_logloss', math.nan):.6f} | {prob.get('market_logloss', math.nan):.6f} | {prob.get('logloss_delta_model_minus_market', math.nan):+.6f} | [{prob.get('logloss_delta_ci_low', math.nan):+.6f}, {prob.get('logloss_delta_ci_high', math.nan):+.6f}] |

settled update states={prob.get('rows', 0):,}，independent target dates={prob.get('target_dates', 0):,}。

## 固定 horizon 可执行 markout

Weather taker fee 在 entry ask 与 exit bid 两边都计入；每笔最多 5 shares，并受 entry ask size / exit bid size 限制。
未来 snapshot 必须在预注册 tolerance 内到达，否则保留为 coverage gap。

| policy | lead | horizon | covered/signals | turnover ROI | target-date 95% CI |
|---|---:|---:|---:|---:|---:|
{os.linesep.join(report_rows) if report_rows else '| NA | NA | NA | 0/0 | NA | NA |'}

## 为什么机械复制失败

| lead | signals | median ask | median spread | 60m covered | bid > entry ask | 60m net ROI |
|---|---:|---:|---:|---:|---:|---:|
{os.linesep.join(diagnostics)}

即使事后单列 LMVM 历史上常见的 `0.20–0.40` entry ask（不把它升级为 eligibility gate），
60m 仍只有 {len(old_price_band):,} 个 covered rows、含费 turnover ROI={md_pct(old_price_band_roi)}。
主要损耗不是“持有时间没选准”，而是模型概率校准弱于盘口，同时 taker ask→future bid 需要先跨过 spread 和双边 fee。
如果将来研究 maker 表达，必须另有 trade-through/queue-position/fill 证据；不能把挂单价被触碰当作已成交。

## 解释边界

- `residual_argmax` 每个 update 只选一档；不使用 LMVM 历史 0.20–0.40 价格带作 gate。
- `forecast_mode` 与 `market_favorite` 是同分母对照，不是额外调参候选。
- snapshot 的 `model_prob` 是当时系统概率 telemetry；forecast state 以 hash first-seen 为主，旧行 fallback 单列。
- fixed-horizon bid markout 衡量短期 repricing，不等同 settlement PnL；没有 bid/depth 就不假装可退出。
- public LMVM selected fills 只启发生命周期，不进入我们的候选选择、模型训练或绩效分母。

## 三门

当前历史同分母结果为：

```text
significance=FAIL（primary 30/60/120m markout CI 全部低于 0）
baseline=FAIL（model proper score 显著差于 same-row market；primary markout 也差于 market-favorite）
forward=NA
conclusion=historical FAIL_CURRENT_EVIDENCE / zero-notional research / no shadow or live promotion
```
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def merge_counts(target: Counter[str], incoming: dict[str, int]) -> None:
    target.update(incoming)


def main() -> int:
    args = parse_args()
    files = sorted(args.snapshot_dir.glob("snapshot_*.json"))
    if args.max_files is not None:
        files = files[: args.max_files]
    if not files:
        raise FileNotFoundError(f"no snapshot files under {args.snapshot_dir}")
    states: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as executor:
        for parsed, local_counts in executor.map(parse_snapshot_file, map(str, files), chunksize=16):
            states.extend(parsed)
            merge_counts(counters, local_counts)
    states = annotate_forecast_updates(states)
    candidates, probability_rows = build_candidates(states)
    histories = quote_history(states)
    candidates = attach_markouts(candidates, histories)
    winners = load_winners(args.db)
    probability_rows = score_probabilities(probability_rows, winners)
    markout_summary = summarize_markouts(candidates, args.draws)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(args.output_dir / "candidate_markouts.csv", index=False)
    probability_rows.to_csv(args.output_dir / "probability_rows.csv", index=False)
    markout_summary.to_csv(args.output_dir / "markout_summary.csv", index=False)
    generated_at = datetime.now(timezone.utc).isoformat()
    summary = {
        "generated_at_utc": generated_at,
        "target_metric": "lmvm_single_yes_repricing_v1",
        "counters": dict(counters),
        "states": len(states),
        "forecast_update_events": sum(
            1 for row in states if row.get("forecast_state_changed") and not row.get("left_censored_initial_state")
        ),
        "candidate_rows": len(candidates),
        "primary_eligible_rows": int(
            ((candidates["policy"] == "residual_argmax") & candidates["policy_eligible"]).sum()
        ) if not candidates.empty else 0,
        "probability": probability_summary(probability_rows),
        "markout_summary": markout_summary.to_dict("records"),
        "status": "research_zero_notional",
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=True) + "\n",
        encoding="utf-8",
    )
    write_report(
        args.report,
        args.snapshot_dir,
        args.db,
        dict(counters),
        states,
        candidates,
        probability_rows,
        markout_summary,
        generated_at,
    )
    print(json.dumps({"report": str(args.report), "output_dir": str(args.output_dir), **summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
