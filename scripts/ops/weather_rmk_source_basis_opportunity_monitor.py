#!/usr/bin/env python3
"""Monitor RMK-vs-fast-source basis traps against live orderbooks.

Research telemetry only. This script consumes the standard timing monitor
outputs produced from weather_data_feed observation adapters. It does not fetch
weather sources directly and it never submits orders.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OPS = Path(__file__).resolve().parent
ROOT = OPS.parents[1]
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import weather_station_basis_shadow as source  # noqa: E402
from weather_data_feed import bracket_contains, load_city_configs, parse_label_dict  # noqa: E402
from weather_data_feed.source_basis import latest_sources, rmk_source_basis_state  # noqa: E402


DATA_ROOT = Path(os.environ.get("RMK_BASIS_MONITOR_DATA_ROOT") or os.environ.get("TIMING_MONITOR_DATA_ROOT") or ROOT)
TIMING_DIR = DATA_ROOT / "runtime/weather_edge_v1/source_orderbook_timing"
OUT_PATH = TIMING_DIR / "source_basis_rmk_proxy_opportunities.jsonl"
STATE_PATH = TIMING_DIR / "source_basis_rmk_proxy_state.jsonl"
MARKET_PROXY_MODE = os.environ.get("RMK_BASIS_MONITOR_MARKET_PROXY_MODE", os.environ.get("TIMING_MONITOR_MARKET_PROXY_MODE", "direct")).strip().lower()
EXPLICIT_MARKET_PROXY = os.environ.get("RMK_BASIS_MONITOR_MARKET_PROXY", os.environ.get("TIMING_MONITOR_MARKET_PROXY", "")).strip()
HTTP_TIMEOUT_SEC = float(os.environ.get("RMK_BASIS_MONITOR_HTTP_TIMEOUT_SEC", "6.0"))


def proxy_candidates(mode: str, explicit_proxy: str = "") -> list[str | None]:
    if explicit_proxy:
        return [explicit_proxy]
    if mode == "all":
        return source.PROXIES
    if mode == "first":
        return source.PROXIES[:1]
    if mode in {"none", "direct", ""}:
        return [None]
    raise ValueError(f"unknown proxy mode {mode}")


MARKET_PROXY_CANDIDATES = proxy_candidates(MARKET_PROXY_MODE, EXPLICIT_MARKET_PROXY)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def fetch_json(url: str, params: dict[str, Any]) -> Any:
    return source.fetch_json(url, params, max_rounds=1, timeout_sec=HTTP_TIMEOUT_SEC, proxy_candidates=MARKET_PROXY_CANDIDATES)


def book_summary(token_id: str) -> dict[str, Any]:
    try:
        return source.book_summary(fetch_json(f"{source.CLOB}/book", {"token_id": token_id}))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def market_contains(parsed: dict[str, Any] | None, value: int | None) -> bool:
    return bool(parsed is not None and value is not None and bracket_contains(parsed, value))


def classify_opportunity(
    *,
    has_false_cross: bool,
    wu_current_contradicts_proxy: bool,
    market_contains_proxy: bool,
    market_contains_fast: bool,
    yes_book: dict[str, Any],
) -> str:
    if not has_false_cross:
        return "no_false_cross"
    if wu_current_contradicts_proxy:
        return "wu_current_contradicts_proxy"
    if not market_contains_proxy:
        return "false_cross_context"
    if market_contains_fast:
        return "ambiguous_market_contains_fast"
    yes_ask = yes_book.get("best_ask")
    if yes_ask is None:
        return "candidate_yes_no_ask"
    try:
        ask = float(yes_ask)
    except (TypeError, ValueError):
        return "candidate_yes_ask_unparseable"
    if ask <= 0.25:
        return "candidate_yes_cheap"
    if ask <= 0.75:
        return "candidate_yes_mid"
    return "false_cross_but_market_not_cheap"


def trade_intent(status: str) -> dict[str, Any]:
    if status == "candidate_yes_cheap":
        return {
            "clean_rmk_basis_candidate": True,
            "candidate_tier": "cheap",
            "paper_action": "BUY_YES_PROXY_BRACKET",
        }
    if status == "candidate_yes_mid":
        return {
            "clean_rmk_basis_candidate": True,
            "candidate_tier": "mid",
            "paper_action": "BUY_YES_PROXY_BRACKET",
        }
    return {
        "clean_rmk_basis_candidate": False,
        "candidate_tier": "",
        "paper_action": "NONE",
    }


def cycle(*, cities: set[str] | None) -> int:
    source_rows = read_jsonl(TIMING_DIR / "sources.jsonl")
    latest_by_source = latest_sources(source_rows)
    city_names = sorted({city for city, _source in latest_by_source})
    if cities:
        city_names = [city for city in city_names if city in cities]
    configs = {cfg.city: cfg for cfg in load_city_configs(include_station_diff=True, only_cities=set(city_names))}
    now = datetime.now(timezone.utc).isoformat()
    emitted = 0
    for city in city_names:
        cfg = configs.get(city)
        if not cfg or cfg.unit != "F" or "wu" not in cfg.settlement_source_class.lower():
            continue
        basis = rmk_source_basis_state(city, latest_by_source)
        basis_row = basis.as_dict()
        basis_status = str(basis_row.pop("status"))
        state_row = {"ts_utc": now, "status": basis_status, **basis_row}
        append_jsonl(STATE_PATH, state_row)
        if basis_status == "missing_routine_rmk" or not basis.target_date:
            emitted += 1
            continue
        event_slug = source.event_slug(cfg.slug, datetime.fromisoformat(basis.target_date).date())
        try:
            events = fetch_json(f"{source.GAMMA}/events", {"slug": event_slug})
            markets = events[0].get("markets") if events else []
        except Exception as exc:  # noqa: BLE001
            append_jsonl(
                OUT_PATH,
                {
                    "ts_utc": now,
                    "city": city,
                    "target_date": basis.target_date,
                    "event_slug": event_slug,
                    "status": "event_fetch_failed",
                    "basis_status": basis_status,
                    "error": f"{type(exc).__name__}: {exc}",
                    **basis_row,
                },
            )
            emitted += 1
            continue

        seen_labels: set[str] = set()
        watch_values = {basis.proxy_round_f, basis.fast_round_f, basis.routine_main_round_f, basis.wu_current_round_f}
        watch_values = {value for value in watch_values if value is not None}
        for market in markets:
            label = str(market.get("groupItemTitle") or "")
            question = str(market.get("question") or "")
            parsed = parse_label_dict(label, question, include_label=False)
            if label in seen_labels or not any(market_contains(parsed, value) for value in watch_values):
                continue
            seen_labels.add(label)
            token_ids = json.loads(market.get("clobTokenIds") or "[]")
            if len(token_ids) < 2:
                continue
            yes = book_summary(str(token_ids[0]))
            no = book_summary(str(token_ids[1]))
            market_contains_proxy = market_contains(parsed, basis.proxy_round_f)
            market_contains_fast = market_contains(parsed, basis.fast_round_f)
            status = classify_opportunity(
                has_false_cross=basis.has_false_cross,
                wu_current_contradicts_proxy=basis.wu_current_contradicts_proxy,
                market_contains_proxy=market_contains_proxy,
                market_contains_fast=market_contains_fast,
                yes_book=yes,
            )
            intent = trade_intent(status)
            row = {
                "ts_utc": now,
                "city": city,
                "target_date": basis.target_date,
                "event_slug": event_slug,
                "market_label": label,
                "market_low": None if parsed is None else parsed.get("low"),
                "market_high": None if parsed is None else parsed.get("high"),
                "market_bottom": None if parsed is None else parsed.get("bottom"),
                "market_top": None if parsed is None else parsed.get("top"),
                "market_contains_proxy": market_contains_proxy,
                "market_contains_fast": market_contains_fast,
                "status": status,
                "basis_status": basis_status,
                **intent,
                "yes_best_bid": yes.get("best_bid"),
                "yes_best_ask": yes.get("best_ask"),
                "yes_best_ask_size": yes.get("best_ask_size"),
                "no_best_bid": no.get("best_bid"),
                "no_best_ask": no.get("best_ask"),
                "no_best_ask_size": no.get("best_ask_size"),
                "yes_error": yes.get("error"),
                "no_error": no.get("error"),
                **basis_row,
            }
            append_jsonl(OUT_PATH, row)
            print(json.dumps(row, ensure_ascii=False, sort_keys=True))
            emitted += 1
    return emitted


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor RMK proxy source-basis traps against live orderbooks.")
    parser.add_argument("--cities", nargs="*", default=None)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-sec", type=float, default=30.0)
    args = parser.parse_args()
    cities = set(args.cities or []) or None
    if not args.loop:
        cycle(cities=cities)
        return 0
    while True:
        cycle(cities=cities)
        time.sleep(args.interval_sec)


if __name__ == "__main__":
    raise SystemExit(main())
