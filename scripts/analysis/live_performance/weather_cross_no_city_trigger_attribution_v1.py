#!/usr/bin/env python3
"""Attribute CrossNO trigger and order volume by city.

The analysis keeps three denominators separate:

* first CrossNO expression per condition (weather/source trigger volume),
* raw entry order records and unique order ids (execution activity), and
* canonical live-real fill expressions (actual positions).

Repeated event rows are retained only to determine whether a triggered
expression ever obtained a usable book/price/depth state. Settlements and
actual fills remain canonical.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.live_performance.weather_cross_no_accuracy_attribution_v1 import (
    DEFAULT_DB,
    DEFAULT_EVENTS,
    STRATEGY_ID,
    attach_settlements_and_fills,
    connect_ro,
    db_identity,
    dedupe_event_rows,
    event_expression_key,
    iter_jsonl,
    load_cross_fills,
    load_settlements,
    number,
    parse_dt,
)


RUNTIME_DIR = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/fast_source_prev_no_trial"
)
DEFAULT_ORDERS = RUNTIME_DIR / "orders.jsonl"
DEFAULT_RUNTIME_SUMMARY = RUNTIME_DIR / "latest_summary.json"
DEFAULT_OBSERVATION_ROOT = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/live_cross_observations"
)
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-08/generated/cross_no_city_trigger_attribution_20260812_v1"
)

WINDOWS = {
    "prior_10d": ("2026-07-23", "2026-08-01"),
    "recent_10d": ("2026-08-02", "2026-08-11"),
    "preceding_5d": ("2026-08-02", "2026-08-06"),
    "latest_5d": ("2026-08-07", "2026-08-11"),
    "current_unsettled": ("2026-08-12", "2026-08-12"),
}


def expression_key_text(row: dict[str, Any]) -> str | None:
    key = event_expression_key(row)
    return "|".join(key) if key else None


def date_count(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1


def median(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return statistics.median(clean) if clean else None


def load_event_sequences(
    path: Path, start_date: str, end_date: str
) -> dict[str, dict[str, Any]]:
    sequences: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(path):
        if row.get("status") != "cross_candidate":
            continue
        target_date = str(row.get("target_date") or "")
        if not (start_date <= target_date <= end_date):
            continue
        key = expression_key_text(row)
        timestamp = parse_dt(row.get("ts_utc") or row.get("source_detect_ts_utc"))
        if key is None or timestamp is None:
            continue
        best_ask = number(row.get("best_ask"))
        ask_size = number(row.get("ask_size"))
        max_no_ask = number(row.get("max_no_ask"))
        min_taker = number(row.get("min_taker_shares"))
        blockers = [str(item) for item in (row.get("live_blockers") or [])]
        live_requested = bool(row.get("live_requested"))
        basic_price_eligible = (
            best_ask is not None
            and max_no_ask is not None
            and best_ask <= max_no_ask + 1e-12
        )
        depth_eligible = (
            ask_size is not None
            and min_taker is not None
            and ask_size + 1e-12 >= min_taker
        )
        execution_ready = live_requested and basic_price_eligible and not blockers
        state = sequences.setdefault(
            key,
            {
                "event_rows": 0,
                "first_event_ts_utc": timestamp.isoformat(),
                "last_event_ts_utc": timestamp.isoformat(),
                "ever_book_present": 0,
                "ever_basic_price_eligible": 0,
                "ever_depth_eligible": 0,
                "ever_execution_ready": 0,
                "first_book_ts_utc": None,
                "first_price_eligible_ts_utc": None,
                "first_execution_ready_ts_utc": None,
                "minimum_best_ask": None,
                "maximum_ask_size": None,
                "live_requested": 0,
                "modes": set(),
                "policies": set(),
                "book_statuses": set(),
                "blockers": set(),
                "book_fetch_failed_rows": 0,
                "book_ok_no_ask_rows": 0,
            },
        )
        state["event_rows"] += 1
        state["first_event_ts_utc"] = min(state["first_event_ts_utc"], timestamp.isoformat())
        state["last_event_ts_utc"] = max(state["last_event_ts_utc"], timestamp.isoformat())
        state["live_requested"] = max(state["live_requested"], int(live_requested))
        state["modes"].add(str(row.get("mode") or "unknown"))
        state["policies"].add(str(row.get("source_cross_policy") or "legacy_unlabeled"))
        fresh_book_status = str(row.get("fresh_book_status") or "unknown")
        state["book_statuses"].add(fresh_book_status)
        state["blockers"].update(blockers)
        state["book_fetch_failed_rows"] += int(fresh_book_status == "fetch_failed")
        state["book_ok_no_ask_rows"] += int(fresh_book_status == "ok" and best_ask is None)
        if best_ask is not None:
            state["ever_book_present"] = 1
            state["first_book_ts_utc"] = min(
                value
                for value in (state["first_book_ts_utc"], timestamp.isoformat())
                if value is not None
            )
            state["minimum_best_ask"] = (
                best_ask
                if state["minimum_best_ask"] is None
                else min(state["minimum_best_ask"], best_ask)
            )
        if basic_price_eligible:
            state["ever_basic_price_eligible"] = 1
            state["first_price_eligible_ts_utc"] = min(
                value
                for value in (state["first_price_eligible_ts_utc"], timestamp.isoformat())
                if value is not None
            )
        if depth_eligible:
            state["ever_depth_eligible"] = 1
        if execution_ready:
            state["ever_execution_ready"] = 1
            state["first_execution_ready_ts_utc"] = min(
                value
                for value in (state["first_execution_ready_ts_utc"], timestamp.isoformat())
                if value is not None
            )
        if ask_size is not None:
            state["maximum_ask_size"] = (
                ask_size
                if state["maximum_ask_size"] is None
                else max(state["maximum_ask_size"], ask_size)
            )
    for state in sequences.values():
        first = parse_dt(state["first_event_ts_utc"])
        for source, destination in (
            ("first_book_ts_utc", "seconds_to_first_book"),
            ("first_price_eligible_ts_utc", "seconds_to_first_price_eligible"),
            ("first_execution_ready_ts_utc", "seconds_to_first_execution_ready"),
        ):
            later = parse_dt(state[source])
            state[destination] = (
                (later - first).total_seconds() if first is not None and later is not None else None
            )
        state["modes"] = sorted(state["modes"])
        state["policies"] = sorted(state["policies"])
        state["book_statuses"] = sorted(state["book_statuses"])
        state["blockers"] = sorted(state["blockers"])
    return sequences


def raw_order_id(row: dict[str, Any]) -> str:
    response = row.get("exchange_response") or {}
    place = response.get("place") or {} if isinstance(response, dict) else {}
    return str(
        row.get("order_id")
        or (response.get("order_id") if isinstance(response, dict) else "")
        or (place.get("orderID") if isinstance(place, dict) else "")
        or ""
    )


def is_entry_order(row: dict[str, Any]) -> bool:
    role = str(row.get("child_order_role") or "")
    side = str(row.get("order_side") or row.get("side") or "").upper()
    return "exit" not in role and side != "SELL"


def load_order_sequences(
    path: Path, start_date: str, end_date: str
) -> dict[str, dict[str, Any]]:
    sequences: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(path):
        target_date = str(row.get("target_date") or "")
        if not (start_date <= target_date <= end_date):
            continue
        key = expression_key_text(row)
        if key is None:
            continue
        state = sequences.setdefault(
            key,
            {
                "raw_entry_order_records": 0,
                "submitted_entry_order_records": 0,
                "matched_entry_order_records": 0,
                "resting_entry_order_records": 0,
                "failed_entry_order_records": 0,
                "submitted_entry_order_ids": set(),
                "matched_entry_order_ids": set(),
                "entry_roles": Counter(),
                "execution_policies": Counter(),
                "submit_errors": Counter(),
                "exit_order_records": 0,
            },
        )
        if not is_entry_order(row):
            state["exit_order_records"] += 1
            continue
        state["raw_entry_order_records"] += 1
        role = str(row.get("child_order_role") or "legacy_unspecified")
        state["entry_roles"][role] += 1
        state["execution_policies"][str(row.get("execution_policy") or "legacy_unspecified")] += 1
        submit_status = str(row.get("live_submit_status") or "")
        response = row.get("exchange_response") or {}
        place = response.get("place") or {} if isinstance(response, dict) else {}
        place_status = str(place.get("status") or "") if isinstance(place, dict) else ""
        order_id = raw_order_id(row)
        if submit_status == "submitted":
            state["submitted_entry_order_records"] += 1
            if order_id:
                state["submitted_entry_order_ids"].add(order_id)
        elif submit_status == "submit_failed":
            state["failed_entry_order_records"] += 1
            state["submit_errors"][str(row.get("error") or "unknown")] += 1
        if place_status == "matched":
            state["matched_entry_order_records"] += 1
            if order_id:
                state["matched_entry_order_ids"].add(order_id)
        elif place_status == "live":
            state["resting_entry_order_records"] += 1
    for state in sequences.values():
        state["submitted_entry_order_ids"] = sorted(state["submitted_entry_order_ids"])
        state["matched_entry_order_ids"] = sorted(state["matched_entry_order_ids"])
        state["entry_roles"] = dict(sorted(state["entry_roles"].items()))
        state["execution_policies"] = dict(sorted(state["execution_policies"].items()))
        state["submit_errors"] = dict(sorted(state["submit_errors"].items()))
    return sequences


def observation_paths(root: Path, start_date: str, end_date: str) -> list[Path]:
    start = date.fromisoformat(start_date) - timedelta(days=1)
    end = date.fromisoformat(end_date) + timedelta(days=1)
    paths: list[Path] = []
    current = start
    while current <= end:
        candidate = root / current.isoformat() / "high_frequency_observations.jsonl"
        if candidate.exists():
            paths.append(candidate)
        current += timedelta(days=1)
    return paths


def load_observation_rows(
    root: Path, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path in observation_paths(root, start_date, end_date):
        for row in iter_jsonl(path):
            target_date = str(row.get("target_date") or "")
            city = str(row.get("city") or "")
            if not city or not (start_date <= target_date <= end_date):
                continue
            content_key = str(
                row.get("content_key")
                or row.get("information_event_id")
                or row.get("raw_row_hash")
                or row.get("payload_hash")
                or ""
            )
            if not content_key:
                continue
            key = (city, target_date, content_key)
            candidate = {
                "city": city,
                "target_date": target_date,
                "source": str(row.get("source") or ""),
                "content_key": content_key,
                "source_fetch_latency_sec": number(row.get("source_fetch_latency_sec")),
                "source_observation_ts_utc": str(
                    row.get("observation_time_utc") or row.get("source_event_ts_utc") or ""
                ),
                "source_first_seen_at_utc": str(
                    row.get("source_first_seen_at_utc") or row.get("first_seen_at_utc") or ""
                ),
                "collector_exact": int(row.get("pit_lineage_class") == "collector_exact"),
            }
            existing = deduped.get(key)
            if existing is None or candidate["source_first_seen_at_utc"] < existing["source_first_seen_at_utc"]:
                deduped[key] = candidate
    return sorted(
        deduped.values(),
        key=lambda row: (row["target_date"], row["city"], row["source_first_seen_at_utc"]),
    )


def no_submit_reason(row: dict[str, Any]) -> str:
    if row.get("submitted_expression"):
        return "submitted"
    modes = set(row.get("modes") or [])
    blockers = set(row.get("blockers") or [])
    if modes and modes <= {"shadow", "paper"}:
        return "shadow_or_paper"
    if row.get("failed_entry_order_records"):
        return "submit_failed"
    if not row.get("ever_book_present"):
        return "never_had_best_ask"
    if not row.get("ever_basic_price_eligible"):
        return "ask_above_cap"
    if "insufficient_top_ask_size" in blockers and not row.get("ever_depth_eligible"):
        return "insufficient_depth"
    if any("report_clock" in blocker or "metar_window" in blocker for blocker in blockers):
        return "report_clock"
    if any("share_cap" in blocker or "already" in blocker or "dedupe" in blocker for blocker in blockers):
        return "cap_or_dedupe"
    return "other_policy_or_execution"


def attach_sequences(
    detail: list[dict[str, Any]],
    event_sequences: dict[str, dict[str, Any]],
    order_sequences: dict[str, dict[str, Any]],
    any_fill_conditions: set[str],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for base in detail:
        row = dict(base)
        event_state = event_sequences.get(str(row["expression_key"]), {})
        order_state = order_sequences.get(str(row["expression_key"]), {})
        row.update(event_state)
        row.update(order_state)
        row.setdefault("event_rows", 1)
        for field in (
            "ever_book_present",
            "ever_basic_price_eligible",
            "ever_depth_eligible",
            "ever_execution_ready",
            "live_requested",
            "raw_entry_order_records",
            "submitted_entry_order_records",
            "matched_entry_order_records",
            "resting_entry_order_records",
            "failed_entry_order_records",
            "exit_order_records",
            "book_fetch_failed_rows",
            "book_ok_no_ask_rows",
        ):
            row.setdefault(field, 0)
        for field in (
            "submitted_entry_order_ids",
            "matched_entry_order_ids",
            "modes",
            "policies",
            "book_statuses",
            "blockers",
        ):
            row.setdefault(field, [])
        for field in ("entry_roles", "execution_policies", "submit_errors"):
            row.setdefault(field, {})
        row["submitted_expression"] = int(row["submitted_entry_order_records"] > 0)
        row["matched_expression"] = int(row["matched_entry_order_records"] > 0)
        row["canonical_fill_any_settlement"] = int(
            bool(row.get("condition_id")) and str(row["condition_id"]) in any_fill_conditions
        )
        row["unique_submitted_entry_orders"] = len(row["submitted_entry_order_ids"])
        row["unique_matched_entry_orders"] = len(row["matched_entry_order_ids"])
        row["no_submit_reason"] = no_submit_reason(row)
        output.append(row)
    return output


def observation_summary(
    observation_rows: list[dict[str, Any]], city: str, start: str, end: str
) -> dict[str, Any]:
    rows = [
        row
        for row in observation_rows
        if row["city"] == city and start <= row["target_date"] <= end
    ]
    return {
        "observation_rows": len(rows),
        "coverage_dates": len({row["target_date"] for row in rows}),
        "observations_per_calendar_date": len(rows) / date_count(start, end),
        "source_fetch_latency_sec_p50": median(row["source_fetch_latency_sec"] for row in rows),
        "collector_exact_share": (
            sum(row["collector_exact"] for row in rows) / len(rows) if rows else None
        ),
        "sources": dict(sorted(Counter(row["source"] for row in rows).items())),
    }


def city_summary(
    rows: list[dict[str, Any]],
    observation_rows: list[dict[str, Any]],
    city: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    city_rows = [
        row for row in rows if row["city"] == city and start <= row["target_date"] <= end
    ]
    settled = [row for row in city_rows if row.get("correct") is not None]
    city_days = Counter(row["target_date"] for row in city_rows)
    fills = [row for row in settled if row.get("actual_fill")]
    buy_cost = sum(float(row.get("fill_buy_cost_usd") or 0) for row in fills)
    net_pnl = sum(float(row.get("fill_net_pnl_usd") or 0) for row in fills)
    output = {
        "calendar_dates": date_count(start, end),
        "signals": len(city_rows),
        "active_signal_dates": len(city_days),
        "signals_per_calendar_date": len(city_rows) / date_count(start, end),
        "signals_per_active_date": len(city_rows) / len(city_days) if city_days else None,
        "multi_cross_active_date_share": (
            sum(count >= 2 for count in city_days.values()) / len(city_days)
            if city_days
            else None
        ),
        "settled_signals": len(settled),
        "wins": sum(int(row["correct"]) for row in settled),
        "accuracy": (
            sum(int(row["correct"]) for row in settled) / len(settled) if settled else None
        ),
        "terminal_false_city_days": len(
            {row["target_date"] for row in settled if int(row["correct"]) == 0}
        ),
        "ever_book_expressions": sum(int(row["ever_book_present"]) for row in city_rows),
        "ever_price_eligible_expressions": sum(
            int(row["ever_basic_price_eligible"]) for row in city_rows
        ),
        "ever_execution_ready_expressions": sum(
            int(row["ever_execution_ready"]) for row in city_rows
        ),
        "submitted_expressions": sum(int(row["submitted_expression"]) for row in city_rows),
        "matched_expressions": sum(int(row["matched_expression"]) for row in city_rows),
        "canonical_fill_expressions_any_settlement": sum(
            int(row["canonical_fill_any_settlement"]) for row in city_rows
        ),
        "actual_fill_expressions": len(fills),
        "signal_to_submit_conversion": (
            sum(int(row["submitted_expression"]) for row in city_rows) / len(city_rows)
            if city_rows
            else None
        ),
        "signal_to_fill_conversion": len(fills) / len(settled) if settled else None,
        "raw_entry_order_records": sum(
            int(row["raw_entry_order_records"]) for row in city_rows
        ),
        "submitted_entry_order_records": sum(
            int(row["submitted_entry_order_records"]) for row in city_rows
        ),
        "unique_submitted_entry_orders": sum(
            int(row["unique_submitted_entry_orders"]) for row in city_rows
        ),
        "matched_entry_order_records": sum(
            int(row["matched_entry_order_records"]) for row in city_rows
        ),
        "failed_entry_order_records": sum(
            int(row["failed_entry_order_records"]) for row in city_rows
        ),
        "expressions_with_any_book_fetch_failure": sum(
            int(row["book_fetch_failed_rows"] > 0) for row in city_rows
        ),
        "book_fetch_failed_event_rows": sum(
            int(row["book_fetch_failed_rows"]) for row in city_rows
        ),
        "book_ok_no_ask_event_rows": sum(
            int(row["book_ok_no_ask_rows"]) for row in city_rows
        ),
        "exit_order_records": sum(int(row["exit_order_records"]) for row in city_rows),
        "settled_buy_cost_usd": buy_cost,
        "settled_net_pnl_usd": net_pnl,
        "settled_net_roi": net_pnl / buy_cost if buy_cost else None,
        "source_detect_to_runner_sec_p50": median(
            row.get("source_detect_to_runner_sec") for row in city_rows
        ),
        "source_obs_lag_min_p50": median(row.get("source_obs_lag_min") for row in city_rows),
        "seconds_to_first_book_p50": median(
            row.get("seconds_to_first_book") for row in city_rows
        ),
        "seconds_to_first_execution_ready_p50": median(
            row.get("seconds_to_first_execution_ready") for row in city_rows
        ),
        "cross_margin_native_p50": median(row.get("cross_margin_native") for row in city_rows),
        "local_hour_p50": median(row.get("local_hour") for row in city_rows),
        "policies": dict(
            sorted(Counter(policy for row in city_rows for policy in row.get("policies", [])).items())
        ),
        "modes": dict(
            sorted(Counter(mode for row in city_rows for mode in row.get("modes", [])).items())
        ),
        "no_submit_reasons": dict(
            sorted(Counter(row["no_submit_reason"] for row in city_rows).items())
        ),
    }
    output.update(observation_summary(observation_rows, city, start, end))
    return output


def volume_conversion_decomposition(earlier: dict[str, Any], later: dict[str, Any]) -> dict[str, Any]:
    n0, n1 = float(earlier["settled_signals"]), float(later["settled_signals"])
    c0 = float(earlier["signal_to_fill_conversion"] or 0)
    c1 = float(later["signal_to_fill_conversion"] or 0)
    return {
        "fill_expression_delta": later["actual_fill_expressions"] - earlier["actual_fill_expressions"],
        "trigger_volume_component": (n1 - n0) * (c0 + c1) / 2,
        "fill_conversion_component": (c1 - c0) * (n0 + n1) / 2,
        "method": "two-factor Shapley decomposition of fills = settled signals * fill conversion",
    }


def rollout_summary(rows: list[dict[str, Any]], city: str) -> dict[str, Any]:
    city_rows = [row for row in rows if row["city"] == city]
    live_rows = [row for row in city_rows if "live" in set(row.get("modes") or [])]
    policy_dates: dict[str, list[str]] = defaultdict(list)
    for row in city_rows:
        for policy in row.get("policies", []):
            policy_dates[policy].append(row["target_date"])
    return {
        "first_signal_date": min((row["target_date"] for row in city_rows), default=None),
        "last_signal_date": max((row["target_date"] for row in city_rows), default=None),
        "first_observed_live_signal_date": min(
            (row["target_date"] for row in live_rows), default=None
        ),
        "policy_signal_date_ranges": {
            policy: [min(dates), max(dates)] for policy, dates in sorted(policy_dates.items())
        },
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--orders", type=Path, default=DEFAULT_ORDERS)
    parser.add_argument("--runtime-summary", type=Path, default=DEFAULT_RUNTIME_SUMMARY)
    parser.add_argument("--observation-root", type=Path, default=DEFAULT_OBSERVATION_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--start-date", default="2026-07-09")
    parser.add_argument("--end-date", default="2026-08-12")
    args = parser.parse_args()

    runtime_summary = json.loads(args.runtime_summary.read_text(encoding="utf-8"))
    live_cities = sorted(str(city) for city in runtime_summary.get("live_cities", []))
    shadow_cities = sorted(str(city) for city in runtime_summary.get("shadow_cities", []))
    configured_cities = set(live_cities) | set(shadow_cities)

    conn = connect_ro(args.db)
    first_events = dedupe_event_rows(iter_jsonl(args.events), args.start_date, args.end_date)
    settlements_by_condition, settlements_by_city_date_bracket = load_settlements(
        conn, first_events, args.start_date, args.end_date
    )
    fills = load_cross_fills(conn, args.start_date, args.end_date)
    any_fill_conditions = {
        str(row[0])
        for row in conn.execute(
            """
            SELECT DISTINCT condition_id
            FROM fact_trades
            WHERE strategy_id=? AND trade_class='live_real'
              AND side='BUY_NO' AND target_date BETWEEN ? AND ?
              AND condition_id IS NOT NULL
            """,
            (STRATEGY_ID, args.start_date, args.end_date),
        )
    }
    detail = attach_settlements_and_fills(
        first_events, settlements_by_condition, settlements_by_city_date_bracket, fills
    )
    event_sequences = load_event_sequences(args.events, args.start_date, args.end_date)
    order_sequences = load_order_sequences(args.orders, args.start_date, args.end_date)
    detail = attach_sequences(detail, event_sequences, order_sequences, any_fill_conditions)
    observation_rows = load_observation_rows(
        args.observation_root, args.start_date, args.end_date
    )

    all_cities = sorted(configured_cities | {str(row["city"]) for row in detail})
    window_summaries: dict[str, Any] = {}
    for name, (start, end) in WINDOWS.items():
        window_summaries[name] = {
            "window": [start, end],
            "cities": {
                city: city_summary(detail, observation_rows, city, start, end)
                for city in all_cities
            },
        }

    comparisons: dict[str, Any] = {}
    for city in all_cities:
        earlier = window_summaries["prior_10d"]["cities"][city]
        later = window_summaries["recent_10d"]["cities"][city]
        earlier5 = window_summaries["preceding_5d"]["cities"][city]
        later5 = window_summaries["latest_5d"]["cities"][city]
        comparisons[city] = {
            "recent_10d_minus_prior_10d": {
                "signal_delta": later["signals"] - earlier["signals"],
                "submitted_expression_delta": later["submitted_expressions"]
                - earlier["submitted_expressions"],
                "unique_submitted_entry_order_delta": later["unique_submitted_entry_orders"]
                - earlier["unique_submitted_entry_orders"],
                "fill_expression_delta": later["actual_fill_expressions"]
                - earlier["actual_fill_expressions"],
                "observation_coverage_date_delta": later["coverage_dates"]
                - earlier["coverage_dates"],
                "fill_decomposition": volume_conversion_decomposition(earlier, later),
            },
            "latest_5d_minus_preceding_5d": {
                "signal_delta": later5["signals"] - earlier5["signals"],
                "submitted_expression_delta": later5["submitted_expressions"]
                - earlier5["submitted_expressions"],
                "fill_expression_delta": later5["actual_fill_expressions"]
                - earlier5["actual_fill_expressions"],
            },
        }

    daily_city: dict[str, Any] = {}
    current_date = date.fromisoformat(args.start_date)
    final_date = date.fromisoformat(args.end_date)
    while current_date <= final_date:
        day = current_date.isoformat()
        day_rows = [row for row in detail if row["target_date"] == day]
        if day_rows:
            day_cities = sorted({str(row["city"]) for row in day_rows})
            daily_city[day] = {
                city: city_summary(detail, observation_rows, city, day, day)
                for city in day_cities
            }
        current_date += timedelta(days=1)

    payload = {
        "schema_version": "weather_cross_no_city_trigger_attribution_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "denominator_scope": (
            "first CrossNO expression per condition from raw runner events; repeated event rows only "
            "for post-trigger book/depth availability; raw entry orders for attempts/submissions; "
            "canonical fact_trades for actual live-real fills"
        ),
        "strategy_id": STRATEGY_ID,
        "candidate_grain": "first city-target_date-previous-bracket CrossNO expression",
        "configured_live_cities": live_cities,
        "configured_shadow_cities": shadow_cities,
        "runtime_summary_generated_at_utc": runtime_summary.get("generated_at_utc"),
        "runtime_city_policies": runtime_summary.get("city_policies", {}),
        "db_identity": db_identity(conn, args.db),
        "source_paths": {
            "events": str(args.events),
            "orders": str(args.orders),
            "observation_root": str(args.observation_root),
            "runtime_summary": str(args.runtime_summary),
        },
        "source_mtimes_utc": {
            "events": datetime.fromtimestamp(args.events.stat().st_mtime, timezone.utc).isoformat(),
            "orders": datetime.fromtimestamp(args.orders.stat().st_mtime, timezone.utc).isoformat(),
            "runtime_summary": datetime.fromtimestamp(
                args.runtime_summary.stat().st_mtime, timezone.utc
            ).isoformat(),
        },
        "rows": {
            "first_expressions": len(detail),
            "canonical_settled_fill_expressions": len(fills),
            "deduped_high_frequency_observations": len(observation_rows),
        },
        "rollout": {city: rollout_summary(detail, city) for city in all_cities},
        "windows": window_summaries,
        "daily_city": daily_city,
        "comparisons": comparisons,
        "multiple_city_slice_note": (
            f"K={len(all_cities)} descriptive city slices; no multiplicity-adjusted alpha claim"
        ),
    }
    conn.close()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    csv_rows: list[dict[str, Any]] = []
    for row in detail:
        csv_rows.append(
            {
                key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                if isinstance(value, (dict, list))
                else value
                for key, value in row.items()
            }
        )
    write_csv(args.output_dir / "expression_lineage.csv", csv_rows)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
