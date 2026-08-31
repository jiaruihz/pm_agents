#!/usr/bin/env python3
"""Backfill missing Polymarket weather pm_history city-date files from Gamma."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[2]
WEATHER_PREDICT_ROOT = ROOT.parent / "weather-predict"
if str(WEATHER_PREDICT_ROOT) not in sys.path:
    sys.path.insert(0, str(WEATHER_PREDICT_ROOT))

from city_pools import FULL_CITY_CONFIGS  # type: ignore  # noqa: E402
from pm_edge_compare import _extract_bracket_label  # type: ignore  # noqa: E402


DEFAULT_OUT_DIR = ROOT / "runtime/weather_edge_v1/market_data/cache/pm_history"
DEFAULT_LOWEST_OUT_DIR = ROOT / "runtime/weather_edge_v1/market_data/cache/pm_history_lowest"
PM_GAMMA_URL = "https://gamma-api.polymarket.com"


def iter_dates(start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        raise ValueError("--end-date must be >= --start-date")
    days: list[str] = []
    cur = start
    while cur <= end:
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


def event_slug(city_cfg: dict[str, Any], target_date: str, extreme: str = "max") -> str:
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    date_slug = f"{dt.strftime('%B')}-{dt.day}-{dt.year}".lower()
    kind = "lowest" if extreme == "min" else "highest"
    return f"{kind}-temperature-in-{city_cfg['slug']}-on-{date_slug}"


def parse_jsonish(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return value


def parse_event(city: str, target_date: str, event: dict[str, Any]) -> dict[str, Any] | None:
    markets = event.get("markets", [])
    if not markets:
        return None

    unit = "C"
    brackets: list[dict[str, Any]] = []
    for market in markets:
        question = str(market.get("question") or "")
        label = _extract_bracket_label(question)
        if label is None:
            continue

        question_lower = question.lower()
        if "°f" in question_lower:
            unit = "F"
        elif "°c" in question_lower:
            unit = "C"

        prices = parse_jsonish(market.get("outcomePrices", [])) or []
        token_ids = parse_jsonish(market.get("clobTokenIds", [])) or []
        try:
            yes_price = float(prices[0]) if prices else 0.0
        except (TypeError, ValueError):
            yes_price = 0.0

        brackets.append(
            {
                "label": label,
                "final_price": yes_price,
                "token_id": token_ids[0] if token_ids else None,
                "condition_id": str(market.get("conditionId") or "") or None,
                "market_id": str(market.get("id") or "") or None,
                "closed": bool(market.get("closed", False)),
                "question": question,
            }
        )

    if not brackets:
        return None
    return {"unit": unit, "brackets": brackets, "date": target_date, "city": city}


def fetch_event(
    client: httpx.Client,
    city: str,
    target_date: str,
    extreme: str = "max",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    slug = event_slug(FULL_CITY_CONFIGS[city], target_date, extreme)
    try:
        response = client.get(f"{PM_GAMMA_URL}/events", params={"slug": slug})
    except Exception as exc:
        return None, {"city": city, "target_date": target_date, "slug": slug, "error": repr(exc)}
    if response.status_code != 200:
        return None, {
            "city": city,
            "target_date": target_date,
            "slug": slug,
            "status_code": response.status_code,
            "body": response.text[:500],
        }
    data = response.json()
    if not data:
        return None, {"city": city, "target_date": target_date, "slug": slug, "status": "not_found"}
    event = data[0] if isinstance(data, list) else data
    parsed = parse_event(city, target_date, event)
    if parsed is None:
        return None, {"city": city, "target_date": target_date, "slug": slug, "status": "no_parseable_markets"}
    return parsed, None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--city", action="append", help="Limit to one city; repeatable.")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--extreme", choices=["max", "min"], default="max",
                        help="max=highest-temperature events (Tmax, default); min=lowest-temperature events (Tmin)")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--write-null", action="store_true")
    parser.add_argument("--sleep-sec", type=float, default=0.1)
    parser.add_argument("--proxy", default=os.getenv("WEATHER_PREDICT_PROXY") or None)
    args = parser.parse_args()

    if args.out_dir is not None:
        out_dir = Path(args.out_dir)
    else:
        out_dir = DEFAULT_LOWEST_OUT_DIR if args.extreme == "min" else DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    cities = args.city or sorted(FULL_CITY_CONFIGS)

    stats = {"fetched": 0, "cached": 0, "not_found": 0, "errors": 0}
    written: list[str] = []
    errors: list[dict[str, Any]] = []
    with httpx.Client(proxy=args.proxy, timeout=20.0) as client:
        for target_date in iter_dates(args.start_date, args.end_date):
            for city in cities:
                if city not in FULL_CITY_CONFIGS:
                    errors.append({"city": city, "target_date": target_date, "status": "missing_city_config"})
                    stats["errors"] += 1
                    continue
                out = out_dir / f"{city}_{target_date}.json"
                if out.exists() and not args.overwrite:
                    stats["cached"] += 1
                    continue
                event, error = fetch_event(client, city, target_date, extreme=args.extreme)
                if event is None:
                    errors.append(error or {"city": city, "target_date": target_date, "status": "unknown_error"})
                    if args.write_null:
                        out.write_text("null\n", encoding="utf-8")
                        written.append(str(out))
                    stats["not_found" if (error or {}).get("status") == "not_found" else "errors"] += 1
                    time.sleep(args.sleep_sec)
                    continue
                out.write_text(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
                written.append(str(out))
                stats["fetched"] += 1
                time.sleep(args.sleep_sec)

    print(
        json.dumps(
            {
                "start_date": args.start_date,
                "end_date": args.end_date,
                "extreme": args.extreme,
                "out_dir": str(out_dir),
                "stats": stats,
                "written_count": len(written),
                "written": written[:20],
                "errors": errors[:20],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
