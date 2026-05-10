#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import List


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_theta_no_v1.tools.weather_edge_market_data import (
    DEFAULT_DATA_ROOT,
    WEATHER_CITIES,
    WeatherEdgeMarketData,
    capture_live_orderbooks,
    collect_market_data_status,
    parse_date,
    safe_json_write,
    token_ids_from_event,
)


def _cities(raw: str) -> List[str]:
    if not raw.strip():
        return list(WEATHER_CITIES.keys())
    out = [x.strip() for x in raw.split(",") if x.strip()]
    unknown = [x for x in out if x not in WEATHER_CITIES]
    if unknown:
        raise ValueError(f"unknown cities: {unknown}; allowed={sorted(WEATHER_CITIES)}")
    return out


def _dates(raw: str, *, days_forward: int) -> List[date]:
    if raw.strip():
        return [parse_date(x.strip()) for x in raw.split(",") if x.strip()]
    today = date.today()
    return [today + timedelta(days=i) for i in range(max(1, days_forward))]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Polymarket market data for weather_edge_v1.")
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show cached history counts and live capture status.")

    backfill = sub.add_parser("backfill-30d", help="Fetch Gamma events and CLOB price history for recent weather markets.")
    backfill.add_argument("--end-date", default=date.today().strftime("%Y-%m-%d"))
    backfill.add_argument("--days", type=int, default=30)
    backfill.add_argument("--cities", default="")
    backfill.add_argument("--overwrite", action="store_true")
    backfill.add_argument("--fidelity", type=int, default=60)
    backfill.add_argument("--history-concurrency", type=int, default=8)

    discover = sub.add_parser("discover", help="Fetch/cache Gamma weather events for selected dates.")
    discover.add_argument("--dates", default="", help="Comma separated YYYY-MM-DD dates. Default: today..days-forward.")
    discover.add_argument("--days-forward", type=int, default=2)
    discover.add_argument("--cities", default="")
    discover.add_argument("--no-cache", action="store_true")
    discover.add_argument("--out", default="")

    live = sub.add_parser("capture-live", help="Continuously append live orderbook snapshots for active weather events.")
    live.add_argument("--dates", default="", help="Comma separated YYYY-MM-DD dates. Default: today..days-forward.")
    live.add_argument("--days-forward", type=int, default=2)
    live.add_argument("--cities", default="")
    live.add_argument("--duration", type=int, default=0, help="Seconds to run. 0 means run until stopped.")
    live.add_argument("--interval", type=float, default=30.0)
    live.add_argument("--top-n", type=int, default=20)
    live.add_argument("--capture-concurrency", type=int, default=25)
    live.add_argument("--out", default="")

    return parser


def _event_token_meta(events: list[dict]) -> dict:
    token_meta = {}
    for item in events:
        summary = item.get("summary") or {}
        event = item.get("event") or {}
        for token_id in token_ids_from_event(event):
            token_meta[token_id] = {
                "city": summary.get("city"),
                "target_date": summary.get("target_date"),
                "event_slug": summary.get("event_slug") or summary.get("slug"),
                "event_id": summary.get("event_id"),
            }
    return token_meta


def main() -> int:
    args = _parser().parse_args()
    os.chdir(ROOT)
    manager = WeatherEdgeMarketData(data_root=Path(args.data_root))

    if args.cmd == "backfill-30d":
        summary = asyncio.run(
            manager.backfill_30d(
                end_date=parse_date(args.end_date),
                days=args.days,
                cities=_cities(args.cities),
                overwrite=args.overwrite,
                fidelity=args.fidelity,
                history_concurrency=args.history_concurrency,
            )
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.cmd == "status":
        status = collect_market_data_status(data_root=Path(args.data_root))
        print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.cmd == "discover":
        events = manager.discover_events(
            dates=_dates(args.dates, days_forward=args.days_forward),
            cities=_cities(args.cities),
            use_cache=not args.no_cache,
        )
        payload = {
            "events_found": len(events),
            "events": [{"summary": item["summary"]} for item in events],
        }
        if args.out.strip():
            safe_json_write(Path(args.out), payload)
            print(str(Path(args.out)))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.cmd == "capture-live":
        events = manager.discover_events(
            dates=_dates(args.dates, days_forward=args.days_forward),
            cities=_cities(args.cities),
            use_cache=True,
        )
        token_meta = _event_token_meta(events)
        token_ids = sorted(token_meta)
        if not token_ids:
            print(json.dumps({"ok": False, "error": "no tokens discovered"}, ensure_ascii=False, indent=2))
            return 2
        if args.out.strip():
            out = Path(args.out)
        else:
            run_date = date.today().strftime("%Y-%m-%d")
            out = Path(args.data_root) / "live_orderbook" / run_date / f"weather_edge_orderbooks_{run_date}.jsonl.gz"
        summary = asyncio.run(
            capture_live_orderbooks(
                token_ids=token_ids,
                token_meta=token_meta,
                out_jsonl_gz=out,
                duration_sec=args.duration,
                interval_sec=args.interval,
                top_n=args.top_n,
                concurrency=args.capture_concurrency,
            )
        )
        print(json.dumps({"ok": True, **summary}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    raise ValueError(f"unsupported command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
