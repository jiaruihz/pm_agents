#!/usr/bin/env python3
"""Fast paper-only orderbook snapshot for all-YES underround baskets.

The broad weather-predict snapshot captures the whole weather universe
serially. That is useful for research, but too slow for live-equivalent
all-leg basket evidence. This script fetches just weather temperature event
orderbooks concurrently, writes a complete gzip sidecar atomically, and then
the existing all-YES scanner/executor can consume that sidecar.

It never signs or places orders.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.clients import clob as clob_client  # noqa: E402
from src.platform.clients.polymarket_gamma import PolymarketGammaClient  # noqa: E402
from src.strategies.rule_lawyer.services.common import normalize_json_list  # noqa: E402
from src.strategies.weather_edge_v1.tools.weather_edge_market_data import WEATHER_CITIES, weather_event_slug  # noqa: E402


RUN_DIR_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "all_yes_underround_paper_v0"
SNAPSHOT_DIR_DEFAULT = RUN_DIR_DEFAULT / "micro_orderbook_snapshots"
WEATHER_PREDICT_DIR_DEFAULT = Path(os.environ.get("WEATHER_PREDICT_DIR", "/home/jiarui/projects/weather-predict"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weather-predict-dir", default=str(WEATHER_PREDICT_DIR_DEFAULT))
    parser.add_argument("--snapshot-dir", default=str(SNAPSHOT_DIR_DEFAULT))
    parser.add_argument("--dates", default="")
    parser.add_argument("--days-forward", type=int, default=2)
    parser.add_argument("--cities", default="")
    parser.add_argument("--city-pool", choices=["all", "t1_trading", "t2_research"], default="all")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--event-concurrency", type=int, default=12)
    parser.add_argument("--orderbook-concurrency", type=int, default=80)
    parser.add_argument("--gamma-use-cache", action="store_true")
    parser.add_argument("--out", default="")
    parser.add_argument("--summary-out", default="")
    return parser.parse_args()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_utc_iso() -> str:
    return now_utc().isoformat(timespec="seconds")


def normalize_book_levels(entries: list[dict[str, Any]], side: str, top_n: int) -> list[dict[str, float]]:
    rows = []
    for entry in entries or []:
        try:
            price = float(entry.get("price"))
            size = float(entry.get("size"))
            level = int(entry.get("level") or len(rows) + 1)
        except Exception:
            continue
        if price > 0 and size > 0:
            rows.append({"level": level, "price": price, "size": size})
    rows.sort(key=lambda row: row["price"], reverse=(side == "bid"))
    return rows[:top_n]


def summarize_from_levels(level_rows: list[dict[str, Any]], top_n: int) -> dict[str, Any]:
    bids = normalize_book_levels([row for row in level_rows if row.get("side") == "bid"], "bid", top_n)
    asks = normalize_book_levels([row for row in level_rows if row.get("side") == "ask"], "ask", top_n)
    best_bid = bids[0]["price"] if bids else None
    best_ask = asks[0]["price"] if asks else None
    bid_size = bids[0]["size"] if bids else None
    ask_size = asks[0]["size"] if asks else None
    spread = round(best_ask - best_bid, 6) if best_bid is not None and best_ask is not None else None
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "bid_size": bid_size,
        "ask_size": ask_size,
        "depth_bid_5c": depth_within(bids, lambda p: best_bid is not None and p >= best_bid - 0.05),
        "depth_ask_5c": depth_within(asks, lambda p: best_ask is not None and p <= best_ask + 0.05),
        "depth_bid_10c": depth_within(bids, lambda p: best_bid is not None and p >= best_bid - 0.10),
        "depth_ask_10c": depth_within(asks, lambda p: best_ask is not None and p <= best_ask + 0.10),
        "bids": bids,
        "asks": asks,
    }


def depth_within(levels: list[dict[str, float]], threshold_fn) -> float:
    return round(sum(row["size"] for row in levels if threshold_fn(row["price"])), 6)


def load_weather_predict_city_configs(path: Path) -> tuple[dict[str, dict[str, Any]], set[str], set[str]]:
    city_pools = path / "city_pools.py"
    if not city_pools.exists():
        fallback = {city: {"slug": cfg["slug"]} for city, cfg in WEATHER_CITIES.items()}
        return fallback, set(), set()

    spec = importlib.util.spec_from_file_location("weather_predict_city_pools_for_all_yes", city_pools)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {city_pools}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    configs = getattr(module, "FULL_CITY_CONFIGS")
    t1 = set(getattr(module, "TRADING_T1_CITIES", set()))
    t2 = set(getattr(module, "RESEARCH_T2_CITIES", set()))
    return dict(configs), t1, t2


def selected_cities(args: argparse.Namespace, configs: dict[str, dict[str, Any]], t1: set[str], t2: set[str]) -> list[str]:
    if args.cities.strip():
        requested = [item.strip() for item in args.cities.split(",") if item.strip()]
    elif args.city_pool == "t1_trading":
        requested = sorted(t1)
    elif args.city_pool == "t2_research":
        requested = sorted(t2)
    else:
        requested = sorted(configs)
    unknown = [city for city in requested if city not in configs]
    if unknown:
        raise ValueError(f"unknown cities: {unknown}")
    return requested


def selected_dates(args: argparse.Namespace) -> list[date]:
    if args.dates.strip():
        return [datetime.strptime(item.strip(), "%Y-%m-%d").date() for item in args.dates.split(",") if item.strip()]
    today = date.today()
    return [today + timedelta(days=i) for i in range(max(1, args.days_forward))]


def extract_market_tokens(market: dict[str, Any]) -> dict[str, str]:
    outcomes = [str(x).strip().lower() for x in normalize_json_list(market.get("outcomes"))]
    token_ids = [str(x).strip() for x in normalize_json_list(market.get("clobTokenIds")) if str(x).strip()]
    out = {"yes": "", "no": ""}
    for idx, outcome in enumerate(outcomes):
        if idx >= len(token_ids):
            continue
        if outcome == "yes":
            out["yes"] = token_ids[idx]
        elif outcome == "no":
            out["no"] = token_ids[idx]
    if token_ids:
        out["yes"] = out["yes"] or token_ids[0]
        if len(token_ids) >= 2:
            out["no"] = out["no"] or token_ids[1]
    return out


BRACKET_RE = re.compile(r"\b(?:be|reach|at)\s+([+-]?\d+(?:\.\d+)?)\s*(?:°| degrees| degree|f|c)?", re.I)


def extract_bracket_label(question: str) -> str | None:
    text = str(question or "")
    match = BRACKET_RE.search(text)
    if match:
        value = match.group(1)
        return value[:-2] if value.endswith(".0") else value
    numbers = re.findall(r"[+-]?\d+(?:\.\d+)?", text)
    if numbers:
        value = numbers[-1]
        return value[:-2] if value.endswith(".0") else value
    return None


async def fetch_token_books(token_ids: list[str], *, top_n: int, concurrency: int) -> dict[str, dict[str, Any]]:
    sem = asyncio.Semaphore(max(1, concurrency))

    async def fetch_one(token_id: str) -> tuple[str, dict[str, Any]]:
        async with sem:
            try:
                price_row, level_rows, _archive = await clob_client.fetch_price_and_book(token_id, top_n=top_n, archive_books=False)
            except Exception as exc:
                return token_id, {
                    "status": "error",
                    "token_id": token_id,
                    "fetched_at_utc": now_utc_iso(),
                    "error": f"{type(exc).__name__}: {exc}",
                    "summary": {},
                    "raw": {},
                }
        price_row = dict(price_row or {})
        fetched_at = str(price_row.get("fetched_at_utc") or now_utc_iso())
        summary = summarize_from_levels(list(level_rows or []), top_n)
        status = "ok" if summary.get("best_bid") is not None or summary.get("best_ask") is not None else "empty_book"
        return token_id, {
            "status": status,
            "token_id": token_id,
            "fetched_at_utc": fetched_at,
            "summary": summary,
            "raw": {"bids": summary["bids"], "asks": summary["asks"]},
        }

    pairs = await asyncio.gather(*(fetch_one(token_id) for token_id in token_ids))
    return dict(pairs)


def discover_event(gamma: PolymarketGammaClient, *, city: str, city_slug: str, target_date: date) -> dict[str, Any] | None:
    slug = weather_event_slug(city_slug, target_date)
    event = gamma.fetch_event_by_id_or_slug(slug=slug)
    if not isinstance(event, dict) or not event:
        return None
    return event


async def main_async(args: argparse.Namespace) -> int:
    configs, t1, t2 = load_weather_predict_city_configs(Path(args.weather_predict_dir))
    cities = selected_cities(args, configs, t1, t2)
    dates = selected_dates(args)
    gamma = PolymarketGammaClient()
    snapshot_ts = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    started = time.monotonic()
    event_sem = asyncio.Semaphore(max(1, args.event_concurrency))
    discovered: list[dict[str, Any]] = []
    missing_events: list[dict[str, str]] = []

    async def discover_one(city: str, target_date: date) -> None:
        async with event_sem:
            slug = weather_event_slug(str(configs[city]["slug"]), target_date)
            try:
                event = await asyncio.to_thread(discover_event, gamma, city=city, city_slug=str(configs[city]["slug"]), target_date=target_date)
            except Exception as exc:
                missing_events.append({"city": city, "event_date": target_date.isoformat(), "slug": slug, "error": f"{type(exc).__name__}: {exc}"})
                return
            if not event:
                missing_events.append({"city": city, "event_date": target_date.isoformat(), "slug": slug, "error": "event_not_found"})
                return
            discovered.append({"city": city, "event_date": target_date.isoformat(), "slug": slug, "event": event})

    await asyncio.gather(*(discover_one(city, d) for d in dates for city in cities))

    token_meta: dict[str, dict[str, Any]] = {}
    market_rows: list[dict[str, Any]] = []
    for item in discovered:
        event = item["event"]
        markets = event.get("markets") if isinstance(event.get("markets"), list) else []
        for market in markets:
            if not isinstance(market, dict):
                continue
            label = extract_bracket_label(str(market.get("question") or ""))
            if label is None:
                continue
            tokens = extract_market_tokens(market)
            for outcome in ("yes", "no"):
                token_id = tokens.get(outcome)
                if not token_id:
                    continue
                token_meta[token_id] = {
                    "city": item["city"],
                    "event_date": item["event_date"],
                    "event_slug": item["slug"],
                    "event_id": str(event.get("id") or ""),
                    "market_id": str(market.get("id") or ""),
                    "condition_id": str(market.get("conditionId") or ""),
                    "bracket": label,
                    "outcome": outcome,
                }
                market_rows.append({"token_id": token_id, **token_meta[token_id]})

    books = await fetch_token_books(sorted(token_meta), top_n=args.top_n, concurrency=args.orderbook_concurrency)
    rows: list[dict[str, Any]] = []
    for meta in market_rows:
        book = books.get(meta["token_id"], {"status": "missing_book", "summary": {}, "raw": {}, "fetched_at_utc": None})
        rows.append(
            {
                "type": "weather_paper_snapshot_orderbook",
                "source": "pm_agent_all_yes_micro_snapshot_v0",
                "snapshot_ts_utc": snapshot_ts,
                "city": meta["city"],
                "event_date": meta["event_date"],
                "event_slug": meta["event_slug"],
                "event_id": meta["event_id"],
                "market_id": meta["market_id"],
                "condition_id": meta["condition_id"],
                "bracket": meta["bracket"],
                "outcome": meta["outcome"],
                "token_id": meta["token_id"],
                "top_n": args.top_n,
                **book,
            }
        )

    out = Path(args.out) if args.out.strip() else build_output_path(Path(args.snapshot_dir), snapshot_ts)
    write_gzip_jsonl_atomic(out, rows)
    finished = time.monotonic()
    fetched_times = sorted(row.get("fetched_at_utc") for row in rows if row.get("fetched_at_utc"))
    summary = {
        "command": "all_yes_underround_micro_snapshot_v0",
        "generated_at_utc": now_utc_iso(),
        "snapshot_ts_utc": snapshot_ts,
        "out": str(out),
        "elapsed_seconds": round(finished - started, 3),
        "cities": len(cities),
        "dates": [d.isoformat() for d in dates],
        "events_found": len(discovered),
        "events_missing": len(missing_events),
        "missing_events": missing_events[:20],
        "tokens": len(token_meta),
        "rows": len(rows),
        "status_counts": count_by(row.get("status") for row in rows),
        "orderbook_fetched_at_utc_min": fetched_times[0] if fetched_times else None,
        "orderbook_fetched_at_utc_max": fetched_times[-1] if fetched_times else None,
    }
    summary_out = Path(args.summary_out) if args.summary_out.strip() else out.with_suffix(out.suffix + ".summary.json")
    write_json_atomic(summary_out, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def count_by(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        key = str(value)
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def build_output_path(root: Path, snapshot_ts: str) -> Path:
    stamp = snapshot_ts.replace("-", "").replace(":", "").replace("T", "_").replace("Z", "")
    date_part = snapshot_ts[:10]
    return root / date_part / f"all_yes_micro_orderbook_snapshot_{stamp}.jsonl.gz"


def write_gzip_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
