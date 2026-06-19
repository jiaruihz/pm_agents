"""
Daily data accumulation pipeline for Polymarket weather prediction strategy.

Runs three jobs:
  1. Fetch yesterday's settled PM events for all cities (Gamma API)
  2. Fetch CLOB price history for all tokens from those events
  3. Fetch today's GFS forecast via Open-Meteo and cache it

Usage:
  python3 daily_pipeline.py              # full run
  python3 daily_pipeline.py --dry-run    # print plan, no network calls

All data lands in cache/pm_history/ (PM events + prices) and
cache/gfs_daily/ (GFS forecasts). Idempotent: skips if already cached.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import httpx
from datetime import date, timedelta
from pathlib import Path
import os

BASE_DIR = Path(__file__).parent
DEFAULT_RUNTIME_DIR = BASE_DIR.parent / "runtime"
CACHE_DIR = Path(os.environ.get("WEATHER_DATA_FEED_CACHE_ROOT", DEFAULT_RUNTIME_DIR / "cache"))
CACHE_PM = CACHE_DIR / "pm_history"
CACHE_GFS = CACHE_DIR / "gfs_daily"

from pm_edge_compare import CITIES, PROXY
from edge_backtest import fetch_settled_event, fetch_price_at_t_minus, TIMEPOINT_HOURS

# ────────────────────────────────────────────────────────────
# 1. Fetch yesterday's settled PM events
# ────────────────────────────────────────────────────────────

def fetch_settled_events(target_date: str, dry_run: bool) -> dict:
    """Fetch settled PM events for all cities on target_date.
    Returns {city: event_dict or None}."""
    results = {}
    for city, cfg in CITIES.items():
        cache_file = CACHE_PM / f"{city}_{target_date}.json"
        if cache_file.exists():
            raw = cache_file.read_text()
            results[city] = None if raw == "null" else json.loads(raw)
            ev = results[city]
            if ev is None:
                print(f"  {city}: CACHED (null)")
            else:
                n_br = len(ev.get("brackets", []))
                print(f"  {city}: CACHED ({n_br} brackets)")
            continue
        if dry_run:
            print(f"  {city}: WOULD FETCH (no cache)")
            results[city] = None
            continue
        event = fetch_settled_event(city, cfg, target_date)
        if event and event.get("brackets"):
            results[city] = event
            n = len(event["brackets"])
            print(f"  {city}: FETCHED ({n} brackets)")
        else:
            results[city] = None
            print(f"  {city}: no market found")
    return results


# ────────────────────────────────────────────────────────────
# 2. Fetch CLOB price history for tokens
# ────────────────────────────────────────────────────────────

def fetch_price_histories(events: dict, target_date: str, dry_run: bool) -> dict:
    """For each event with brackets, fetch CLOB price history for each token.
    Returns counts: {fetched, cached, skipped, errors}."""
    stats = {"fetched": 0, "cached": 0, "skipped": 0, "errors": 0}

    all_tokens = []
    for city, event in events.items():
        if event is None:
            continue
        for b in event.get("brackets", []):
            token_id = b.get("token_id")
            if not token_id:
                stats["skipped"] += 1
                continue
            cache_file = CACHE_PM / f"prices_{token_id[-20:]}.json"
            if cache_file.exists():
                stats["cached"] += 1
                continue
            all_tokens.append((city, b["label"], token_id))

    if not all_tokens:
        print(f"  All token prices already cached (cached={stats['cached']}, skipped={stats['skipped']})")
        return stats

    if dry_run:
        print(f"  WOULD FETCH {len(all_tokens)} token price histories (cached={stats['cached']})")
        stats["skipped"] += len(all_tokens)
        return stats

    print(f"  Fetching {len(all_tokens)} token price histories...")
    client = httpx.Client(proxy=PROXY, timeout=20)
    try:
        for city, label, token_id in all_tokens:
            for tp, hours in TIMEPOINT_HOURS.items():
                price = fetch_price_at_t_minus(token_id, target_date,
                                               hours_before=hours, client=client)
                # fetch_price_at_t_minus already caches the full history
            stats["fetched"] += 1
            time.sleep(0.2)  # rate limit
    except Exception as e:
        print(f"  [ERROR] CLOB fetch failed: {e}")
        stats["errors"] += 1
    finally:
        client.close()

    print(f"  Prices: fetched={stats['fetched']}, cached={stats['cached']}, "
          f"skipped={stats['skipped']}, errors={stats['errors']}")
    return stats


# ────────────────────────────────────────────────────────────
# 3. Fetch today's GFS forecast via Open-Meteo
# ────────────────────────────────────────────────────────────

def fetch_gfs_forecast(target_date: str, dry_run: bool) -> dict:
    """Fetch GFS hourly forecasts for all cities for target_date.
    Cache to cache/gfs_daily/{city}_{date}.json.
    Returns {city: max_temp_f} for cities successfully fetched."""
    CACHE_GFS.mkdir(parents=True, exist_ok=True)
    results = {}

    for city, cfg in CITIES.items():
        cache_file = CACHE_GFS / f"{city}_{target_date}.json"
        if cache_file.exists():
            data = json.loads(cache_file.read_text())
            results[city] = data.get("max_f")
            unit = cfg["unit"]
            if unit == "F":
                print(f"  {city}: CACHED max={data.get('max_f', '?'):.1f} F")
            else:
                max_c = (data.get("max_f", 0) - 32) * 5 / 9
                print(f"  {city}: CACHED max={max_c:.1f} C")
            continue

        if dry_run:
            print(f"  {city}: WOULD FETCH GFS forecast")
            continue

        url = "https://api.open-meteo.com/v1/gfs"
        params = {
            "latitude": cfg["lat"],
            "longitude": cfg["lon"],
            "hourly": "temperature_2m",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "start_date": target_date,
            "end_date": target_date,
        }
        try:
            try:
                resp = httpx.get(url, params=params, timeout=10)
                resp.raise_for_status()
            except Exception:
                client = httpx.Client(proxy=PROXY, timeout=15)
                try:
                    resp = client.get(url, params=params)
                    resp.raise_for_status()
                finally:
                    client.close()

            data = resp.json()
            temps = data["hourly"]["temperature_2m"]
            valid_temps = [t for t in temps if t is not None]
            if not valid_temps:
                print(f"  {city}: [WARN] No valid temperatures from GFS")
                continue

            max_f = max(valid_temps)
            cache_data = {
                "city": city,
                "date": target_date,
                "max_f": max_f,
                "max_c": round((max_f - 32) * 5 / 9, 2),
                "hourly_temps": temps,
                "fetched_at": str(date.today()),
            }
            cache_file.write_text(json.dumps(cache_data, indent=2))
            results[city] = max_f

            unit = cfg["unit"]
            if unit == "F":
                print(f"  {city}: FETCHED max={max_f:.1f} F")
            else:
                print(f"  {city}: FETCHED max={(max_f-32)*5/9:.1f} C")

        except Exception as e:
            print(f"  {city}: [ERROR] {e}")

        time.sleep(0.3)

    return results


# ────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Daily data pipeline for PM weather strategy")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be done without making network calls")
    parser.add_argument("--settle-date", type=str, default=None,
                        help="Override settle date (YYYY-MM-DD). Default: yesterday")
    parser.add_argument("--forecast-date", type=str, default=None,
                        help="Override forecast date (YYYY-MM-DD). Default: tomorrow")
    args = parser.parse_args()

    today = date.today()
    settle_date = args.settle_date or (today - timedelta(days=1)).strftime("%Y-%m-%d")
    forecast_date = args.forecast_date or (today + timedelta(days=1)).strftime("%Y-%m-%d")

    CACHE_PM.mkdir(parents=True, exist_ok=True)

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print("=" * 70)
    print(f"Daily Pipeline [{mode}] — {today}")
    print(f"  Settle date (yesterday):  {settle_date}")
    print(f"  Forecast date (tomorrow): {forecast_date}")
    print(f"  Cities: {len(CITIES)}")
    print("=" * 70)

    # Step 1: Settled events
    print(f"\n[1/3] Fetching settled PM events for {settle_date}...")
    events = fetch_settled_events(settle_date, args.dry_run)
    n_found = sum(1 for v in events.values() if v is not None)
    n_total = len(events)
    print(f"  => {n_found}/{n_total} cities have settled events")

    # Step 2: CLOB price histories
    print(f"\n[2/3] Fetching CLOB price histories for {settle_date}...")
    price_stats = fetch_price_histories(events, settle_date, args.dry_run)

    # Step 3: GFS forecast
    print(f"\n[3/3] Fetching GFS forecast for {forecast_date}...")
    gfs_results = fetch_gfs_forecast(forecast_date, args.dry_run)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Settle date:     {settle_date}")
    print(f"  PM events:       {n_found}/{n_total} cities")
    print(f"  CLOB prices:     fetched={price_stats['fetched']}, "
          f"cached={price_stats['cached']}, "
          f"skipped={price_stats['skipped']}, "
          f"errors={price_stats['errors']}")
    print(f"  Forecast date:   {forecast_date}")
    print(f"  GFS forecasts:   {len(gfs_results)}/{n_total} cities")

    if not args.dry_run:
        # Count total cache files
        pm_events = len(list(CACHE_PM.glob("*_2026-*.json")))
        pm_prices = len(list(CACHE_PM.glob("prices_*.json")))
        gfs_daily = len(list(CACHE_GFS.glob("*.json"))) if CACHE_GFS.exists() else 0
        print(f"\n  Cache totals:")
        print(f"    PM events:  {pm_events} files")
        print(f"    PM prices:  {pm_prices} files")
        print(f"    GFS daily:  {gfs_daily} files")

    print()


if __name__ == "__main__":
    main()
