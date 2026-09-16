#!/usr/bin/env python3
"""Scan WU-vs-MADIS/METAR source-basis divergence against live orderbooks.

This is research telemetry, not a trading script. It consumes the timing
monitor's sources.jsonl, fetches relevant market books, and writes one row per
city/market label with enough fields to judge whether the market is already
pricing the WU settlement basis or is still following a conflicting fast source.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
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
from weather_data_feed.source_policy import load_city_configs  # noqa: E402


DATA_ROOT = Path(os.environ.get("WU_BASIS_SCAN_DATA_ROOT") or os.environ.get("TIMING_MONITOR_DATA_ROOT") or ROOT)
TIMING_DIR = DATA_ROOT / "runtime/weather_edge_v1/source_orderbook_timing"
OUT_PATH = DATA_ROOT / "runtime/weather_edge_v1/source_orderbook_timing/source_basis_market_scan.jsonl"
MARKET_PROXY_MODE = os.environ.get("WU_BASIS_SCAN_MARKET_PROXY_MODE", os.environ.get("TIMING_MONITOR_MARKET_PROXY_MODE", "direct")).strip().lower()
EXPLICIT_MARKET_PROXY = os.environ.get("WU_BASIS_SCAN_MARKET_PROXY", os.environ.get("TIMING_MONITOR_MARKET_PROXY", "")).strip()
HTTP_TIMEOUT_SEC = float(os.environ.get("WU_BASIS_SCAN_HTTP_TIMEOUT_SEC", "6.0"))


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


def json_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        out.append(json.loads(line))
    return out


def latest_sources(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        city = row.get("city")
        source_name = row.get("source")
        if city and source_name:
            latest[(str(city), str(source_name))] = row
    return latest


def int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def label_range(label: str) -> tuple[int, int] | None:
    nums = [int(x) for x in re.findall(r"\d+", label or "")]
    text = (label or "").lower()
    if "or below" in text and nums:
        return -999, nums[0]
    if ("or higher" in text or "or above" in text) and nums:
        return nums[0], 999
    if len(nums) >= 2:
        return nums[0], nums[1]
    if nums:
        return nums[0], nums[0]
    return None


def range_contains(label: str, bracket: int) -> bool:
    rng = label_range(label)
    return bool(rng and rng[0] <= bracket <= rng[1])


def fetch_json(url: str, params: dict[str, Any]) -> Any:
    return source.fetch_json(url, params, max_rounds=1, timeout_sec=HTTP_TIMEOUT_SEC, proxy_candidates=MARKET_PROXY_CANDIDATES)


def book_summary(token_id: str) -> dict[str, Any]:
    try:
        return source.book_summary(fetch_json(f"{source.CLOB}/book", {"token_id": token_id}))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def classify_market(
    *,
    wu_max: int | None,
    fast_max: int | None,
    market_contains_wu_max: bool,
    yes_book: dict[str, Any],
) -> str:
    if wu_max is None:
        return "missing_source_basis"
    if fast_max is None:
        return "no_false_cross"
    if fast_max <= wu_max:
        return "no_false_cross"
    if not market_contains_wu_max:
        return "false_cross_context"
    yes_ask = yes_book.get("best_ask")
    if yes_ask is None:
        return "false_cross_yes_no_ask"
    try:
        ask = float(yes_ask)
    except (TypeError, ValueError):
        return "false_cross_yes_ask_unparseable"
    if ask <= 0.25:
        return "candidate_yes_cheap"
    if ask <= 0.75:
        return "candidate_yes_mid"
    return "false_cross_but_market_not_cheap"


def cycle(*, cities: set[str] | None) -> int:
    source_rows = json_rows(TIMING_DIR / "sources.jsonl")
    latest = latest_sources(source_rows)
    city_names = sorted({city for city, _source in latest})
    if cities:
        city_names = [city for city in city_names if city in cities]
    configs = {cfg.city: cfg for cfg in load_city_configs(include_station_diff=True, only_cities=set(city_names))}
    now = datetime.now(timezone.utc).isoformat()
    emitted = 0
    for city in city_names:
        cfg = configs.get(city)
        if not cfg:
            continue
        wu = latest.get((city, "weather_com_current"), {})
        if wu.get("status") != "ok":
            continue
        mad = latest.get((city, "iem_asos_madishf_latest"), {})
        routine = latest.get((city, "iem_asos_routine_latest"), {})
        target_date = str(wu.get("target_date") or routine.get("target_date") or "")
        if not target_date:
            continue
        wu_max = int_or_none(wu.get("max_temp_f_since_7am"))
        source_values = {
            "wu_current": int_or_none(wu.get("temp_round_f")),
            "madishf": int_or_none(mad.get("temp_round_f")),
            "routine_main": int_or_none(routine.get("main_round_f")),
            "routine_rmk": int_or_none(routine.get("rmk_round_f")),
            "routine_temp": int_or_none(routine.get("temp_round_f")),
        }
        watch_brackets = {value for value in source_values.values() if value is not None}
        if wu_max is not None:
            watch_brackets.update({wu_max - 1, wu_max, wu_max + 1})
        false_cross_sources = {
            name: value
            for name, value in source_values.items()
            if value is not None and wu_max is not None and value > wu_max
        }
        if false_cross_sources and wu_max is not None:
            watch_brackets.add(wu_max)
        event_slug = source.event_slug(cfg.slug, datetime.fromisoformat(target_date).date())
        try:
            events = fetch_json(f"{source.GAMMA}/events", {"slug": event_slug})
            markets = events[0].get("markets") if events else []
        except Exception as exc:  # noqa: BLE001
            append_jsonl(
                OUT_PATH,
                {
                    "ts_utc": now,
                    "city": city,
                    "target_date": target_date,
                    "status": "event_fetch_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            emitted += 1
            continue
        seen_labels: set[str] = set()
        for market in markets:
            label = str(market.get("groupItemTitle") or "")
            if label in seen_labels or not any(range_contains(label, bracket) for bracket in watch_brackets):
                continue
            seen_labels.add(label)
            token_ids = json.loads(market.get("clobTokenIds") or "[]")
            if len(token_ids) < 2:
                continue
            yes = book_summary(str(token_ids[0]))
            no = book_summary(str(token_ids[1]))
            label_rng = label_range(label)
            fast_max = max(false_cross_sources.values()) if false_cross_sources else None
            status = classify_market(
                wu_max=wu_max,
                fast_max=fast_max,
                market_contains_wu_max=bool(wu_max is not None and range_contains(label, wu_max)),
                yes_book=yes,
            )
            row = {
                "ts_utc": now,
                "city": city,
                "target_date": target_date,
                "event_slug": event_slug,
                "market_label": label,
                "market_range_low": None if not label_rng else label_rng[0],
                "market_range_high": None if not label_rng else label_rng[1],
                "status": status,
                "wu_report_ts_utc": wu.get("source_report_ts_utc"),
                "wu_detect_ts_utc": wu.get("local_detect_ts_utc"),
                "wu_detected_after_report_sec": wu.get("detected_after_report_sec"),
                "wu_temp_f": wu.get("temp_f"),
                "wu_current_round_f": source_values["wu_current"],
                "wu_max_f_since_7am": wu_max,
                "madishf_report_ts_utc": mad.get("source_report_ts_utc"),
                "madishf_round_f": source_values["madishf"],
                "routine_report_ts_utc": routine.get("source_report_ts_utc"),
                "routine_main_round_f": source_values["routine_main"],
                "routine_rmk_round_f": source_values["routine_rmk"],
                "false_cross_sources": false_cross_sources,
                "yes_best_bid": yes.get("best_bid"),
                "yes_best_ask": yes.get("best_ask"),
                "yes_best_ask_size": yes.get("best_ask_size"),
                "no_best_bid": no.get("best_bid"),
                "no_best_ask": no.get("best_ask"),
                "no_best_ask_size": no.get("best_ask_size"),
                "yes_error": yes.get("error"),
                "no_error": no.get("error"),
            }
            append_jsonl(OUT_PATH, row)
            print(json.dumps(row, ensure_ascii=False, sort_keys=True))
            emitted += 1
    return emitted


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan WU source-basis divergence against live orderbooks.")
    parser.add_argument("--cities", nargs="*", default=None)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-sec", type=float, default=30.0)
    args = parser.parse_args()
    cities = set(args.cities or []) or None
    if not args.loop:
        cycle(cities=cities)
        return 0
    import time

    while True:
        cycle(cities=cities)
        time.sleep(args.interval_sec)


if __name__ == "__main__":
    raise SystemExit(main())
