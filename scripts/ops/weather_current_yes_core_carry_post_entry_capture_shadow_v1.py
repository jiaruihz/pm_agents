#!/usr/bin/env python3
"""Zero-notional post-entry carry-capture shadow for Core Carry.

The runner consumes the existing Core Carry entry/state journals.  For every
new official report after an entry it fetches a fresh book for the originally
held YES token and records whether a marketable sell would lock the frozen
gain floor.  It has no execution adapter and cannot place or cancel orders.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
CORE_RUNTIME = Path(
    "/Volumes/jrs/pm_agents/runtime/weather_edge_v1/"
    "current_yes_core_carry_tiny_live_v2"
)
OUTPUT_DIR = Path(
    "/Volumes/jrs/pm_agents/runtime/weather_edge_v1/"
    "current_yes_core_carry_post_entry_capture_shadow_v1"
)
STRATEGY_ID = "current_yes_core_carry_post_entry_capture_v1"
STRATEGY_INSTANCE = "current_yes_core_carry_post_entry_capture_shadow_v1"

if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk.research_core_carry_post_entry_capture_v1 import (  # noqa: E402
    entry_cost,
    parse_utc,
    walk_sell_ladder,
)
from scripts.ops import weather_current_yes_core_carry_pre_live_v1 as core_signal  # noqa: E402
from scripts.ops.weather_market_proxy import market_httpx_client, market_proxy_url  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def append_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"processed_event_keys": [], "captured_conditions": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"processed_event_keys": [], "captured_conditions": []}
    payload.setdefault("processed_event_keys", [])
    payload.setdefault("captured_conditions", [])
    return payload


def entry_is_active(entry: Mapping[str, Any], now_utc: datetime) -> bool:
    timezone_name = str(entry.get("timezone") or "UTC")
    try:
        local_date = now_utc.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    except (KeyError, ValueError):
        local_date = now_utc.date().isoformat()
    return str(entry.get("target_date") or "") == local_date


def selected_entries(
    core_runtime: Path, *, active_only: bool = True, now_utc: datetime | None = None
) -> dict[tuple[str, str], dict[str, Any]]:
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    now = now_utc or datetime.now(timezone.utc)
    for row in iter_jsonl(core_runtime / "pre_live_scores.jsonl"):
        if row.get("would_submit_after_family_dedupe") is True and (
            not active_only or entry_is_active(row, now)
        ):
            selected[(str(row.get("city") or ""), str(row.get("target_date") or ""))] = row
    return selected


def latest_new_report_events(
    core_runtime: Path,
    entries: Mapping[tuple[str, str], Mapping[str, Any]],
    processed: set[str],
    captured: set[str],
) -> list[tuple[Mapping[str, Any], Mapping[str, Any], str]]:
    latest_state_by_city_day: dict[tuple[str, str], Mapping[str, Any]] = {}
    state_rows = list(iter_jsonl(core_runtime / "state_decisions.jsonl"))
    for row in state_rows:
        key = (str(row.get("city") or ""), str(row.get("target_date") or ""))
        if key not in entries or parse_utc(row.get("as_of_ts_utc")) is None:
            continue
        prior = latest_state_by_city_day.get(key)
        if prior is None or parse_utc(row.get("as_of_ts_utc")) > parse_utc(prior.get("as_of_ts_utc")):
            latest_state_by_city_day[key] = row
    latest: dict[str, tuple[Mapping[str, Any], Mapping[str, Any], str]] = {}
    for row in state_rows:
        key = (str(row.get("city") or ""), str(row.get("target_date") or ""))
        entry = entries.get(key)
        if entry is None:
            continue
        latest_state = latest_state_by_city_day.get(key)
        if latest_state is None or str(latest_state.get("current_bracket") or "") != str(
            entry.get("current_bracket") or ""
        ):
            continue
        condition = str(entry.get("current_condition_id") or "")
        if not condition or condition in captured:
            continue
        report = parse_utc(row.get("source_report_ts_utc"))
        entry_report = parse_utc(entry.get("source_report_ts_utc"))
        available = parse_utc(row.get("as_of_ts_utc"))
        if report is None or entry_report is None or available is None or report <= entry_report:
            continue
        if str(row.get("current_bracket") or "") != str(entry.get("current_bracket") or ""):
            continue
        event_key = f"{condition}|{row.get('source_report_ts_utc')}"
        if event_key in processed:
            continue
        prior = latest.get(condition)
        if prior is None or available > parse_utc(prior[1].get("as_of_ts_utc")):
            latest[condition] = (entry, row, event_key)
    return list(latest.values())


def evaluate_event(
    entry: Mapping[str, Any],
    event: Mapping[str, Any],
    event_key: str,
    book: Mapping[str, Any],
    *,
    quantity: float,
    gain_floor: float,
) -> dict[str, Any]:
    cost = entry_cost(entry)
    ladder = walk_sell_ladder(book.get("bids") or [], quantity)
    proceeds = ladder.get("net_proceeds_per_share")
    would_exit = (
        cost is not None
        and proceeds is not None
        and ladder.get("executable") is True
        and float(proceeds) >= float(cost) + float(gain_floor)
    )
    return {
        "record_type": "current_yes_core_carry_post_entry_capture_shadow_decision",
        "schema_version": STRATEGY_ID,
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_INSTANCE,
        "created_at_utc": utc_now(),
        "event_key": event_key,
        "city": entry.get("city"),
        "target_date": entry.get("target_date"),
        "held_bracket": entry.get("current_bracket"),
        "held_condition_id": entry.get("current_condition_id"),
        "held_yes_token_id": entry.get("current_yes_token_id"),
        "entry_created_at_utc": entry.get("created_at_utc"),
        "entry_source_report_ts_utc": entry.get("source_report_ts_utc"),
        "entry_effective_cost_per_share": cost,
        "event_source_report_ts_utc": event.get("source_report_ts_utc"),
        "event_available_at_utc": event.get("as_of_ts_utc"),
        "raw_metar": event.get("raw_metar"),
        "quantity": float(quantity),
        "gain_floor_per_share": float(gain_floor),
        "book_status": book.get("status"),
        "book_fetched_at_utc": book.get("fetched_at_utc"),
        "best_bid": book.get("bid"),
        "best_ask": book.get("ask"),
        "sell_ladder": ladder,
        "net_gain_per_share": (
            None if cost is None or proceeds is None else float(proceeds) - float(cost)
        ),
        "would_exit": bool(would_exit),
        "zero_notional": True,
        "no_order_placed": True,
        "execution_calls": 0,
    }


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    core_runtime = Path(args.core_runtime)
    output_dir = Path(args.output_dir)
    state_path = output_dir / "state.json"
    persistent = load_state(state_path)
    processed = set(map(str, persistent["processed_event_keys"]))
    captured = set(map(str, persistent["captured_conditions"]))
    entries = selected_entries(core_runtime, active_only=not bool(args.include_history))
    candidates = latest_new_report_events(core_runtime, entries, processed, captured)
    decisions: list[dict[str, Any]] = []
    with market_httpx_client(args.book_proxy, timeout=float(args.book_timeout_sec)) as client:
        for entry, event, event_key in candidates[: max(0, int(args.max_books_per_run))]:
            book = core_signal.fetch_full_book(client, str(entry.get("current_yes_token_id") or ""))
            decision = evaluate_event(
                entry,
                event,
                event_key,
                book,
                quantity=float(args.quantity),
                gain_floor=float(args.gain_floor),
            )
            decisions.append(decision)
            processed.add(event_key)
            if decision["would_exit"]:
                captured.add(str(entry.get("current_condition_id") or ""))
    append_jsonl(output_dir / "decisions.jsonl", decisions)
    write_json(
        state_path,
        {
            "schema_version": STRATEGY_ID,
            "processed_event_keys": sorted(processed),
            "captured_conditions": sorted(captured),
            "updated_at_utc": utc_now(),
        },
    )
    summary = {
        "schema_version": STRATEGY_ID,
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_INSTANCE,
        "generated_at_utc": utc_now(),
        "status": "ok",
        "mode": "zero_notional_shadow",
        "core_entries": len(entries),
        "new_report_candidates": len(candidates),
        "books_fetched": len(decisions),
        "would_exit": sum(bool(row["would_exit"]) for row in decisions),
        "processed_event_keys_total": len(processed),
        "captured_conditions_total": len(captured),
        "quantity": float(args.quantity),
        "gain_floor_per_share": float(args.gain_floor),
        "no_order_placed": True,
        "real_live_action": "none",
    }
    write_json(output_dir / "latest_summary.json", summary)
    return summary


def parser() -> argparse.ArgumentParser:
    out = argparse.ArgumentParser()
    sub = out.add_subparsers(dest="command", required=True)
    for command in ("once", "loop"):
        p = sub.add_parser(command)
        p.add_argument("--core-runtime", default=str(CORE_RUNTIME))
        p.add_argument("--output-dir", default=str(OUTPUT_DIR))
        p.add_argument("--book-proxy", default=market_proxy_url(None))
        p.add_argument("--book-timeout-sec", type=float, default=5.0)
        p.add_argument("--quantity", type=float, default=10.0)
        p.add_argument("--gain-floor", type=float, default=0.03)
        p.add_argument("--max-books-per-run", type=int, default=20)
        p.add_argument("--include-history", action="store_true")
        if command == "loop":
            p.add_argument("--interval-seconds", type=float, default=60.0)
    return out


def main() -> int:
    args = parser().parse_args()
    if args.command == "once":
        print(json.dumps(run_once(args), ensure_ascii=False, indent=2))
        return 0
    while True:
        print(json.dumps(run_once(args), ensure_ascii=False, sort_keys=True), flush=True)
        time.sleep(max(1.0, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
