"""
Polymarket Weather Edge Comparison: GFS Model vs Market Prices
Fetches live GFS forecasts (Open-Meteo) and Polymarket bracket prices,
computes model probabilities from historical error distribution, and highlights edges.
"""

from __future__ import annotations
import json
import os
import re
import httpx
import numpy as np
from pathlib import Path
from datetime import datetime, date, timedelta, timezone
from typing import Optional

from city_pools import FULL_CITY_CONFIGS

BASE_DIR = Path(__file__).parent
DEFAULT_RUNTIME_DIR = BASE_DIR.parent / "runtime"
CACHE_DIR = Path(os.environ.get("WEATHER_DATA_FEED_CACHE_ROOT", DEFAULT_RUNTIME_DIR / "cache"))
OUTPUT_DIR = Path(os.environ.get("WEATHER_DATA_FEED_OUTPUT_ROOT", DEFAULT_RUNTIME_DIR / "output"))

# City configs: name, lat, lon, ICAO, utc_offset, unit (F or C for PM brackets), PM slug name.
CITIES = FULL_CITY_CONFIGS

# Backward-compatible market proxy. Do not reuse it for weather/forecast APIs:
# those are normally reachable direct and can burn paid proxy traffic quickly.
PROXY = os.getenv("WEATHER_PREDICT_MARKET_PROXY") or os.getenv("WEATHER_DATA_FEED_MARKET_PROXY") or os.getenv("WEATHER_PREDICT_PROXY") or None
WEATHER_PROXY = os.getenv("WEATHER_DATA_FEED_WEATHER_PROXY") or os.getenv("WEATHER_PREDICT_WEATHER_PROXY") or None
PM_GAMMA_URL = "https://gamma-api.polymarket.com"
TARGET_DATE = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")


def fetch_weather_url(url: str, *, params: dict, direct_timeout: float = 10.0, proxy_timeout: float = 15.0) -> httpx.Response:
    """Fetch non-Polymarket weather/forecast data without using the market proxy."""
    try:
        resp = httpx.get(url, params=params, timeout=direct_timeout, trust_env=False)
        resp.raise_for_status()
        return resp
    except Exception:
        if not WEATHER_PROXY:
            raise
    client = httpx.Client(proxy=WEATHER_PROXY, timeout=proxy_timeout, trust_env=False)
    try:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        return resp
    finally:
        client.close()


def _forecast_cache_score(path: Path) -> tuple[int, float, str]:
    """Prefer forecast caches with more hourly rows, then newer mtime."""
    try:
        raw = json.loads(path.read_text())
        n = len(raw.get("hourly", {}).get("time", []))
    except Exception:
        n = 0
    return (n, path.stat().st_mtime, path.name)


def select_forecast_cache(patterns: list[str]) -> Path | None:
    """Select the most complete matching forecast cache file."""
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(CACHE_DIR.glob(pattern))
    if not matches:
        return None
    return max(matches, key=_forecast_cache_score)


def fetch_gfs_forecasts() -> dict:
    """Fetch GFS forecasts from Open-Meteo for all cities for TARGET_DATE."""
    results = {}
    for city, cfg in CITIES.items():
        url = "https://api.open-meteo.com/v1/gfs"
        params = {
            "latitude": cfg["lat"],
            "longitude": cfg["lon"],
            "hourly": "temperature_2m",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "start_date": TARGET_DATE,
            "end_date": TARGET_DATE,
        }
        try:
            resp = fetch_weather_url(url, params=params)
            data = resp.json()
            temps = data["hourly"]["temperature_2m"]
            valid_temps = [t for t in temps if t is not None]
            if not valid_temps:
                print(f"  [WARN] {city}: No valid temperatures from GFS")
                continue
            max_f = max(valid_temps)
            results[city] = {
                "max_f": max_f,
                "max_c": (max_f - 32) * 5 / 9,
                "hourly_temps": temps,
            }
            unit = cfg["unit"]
            if unit == "F":
                print(f"  {city}: GFS max = {max_f:.1f} F")
            else:
                print(f"  {city}: GFS max = {(max_f-32)*5/9:.1f} C ({max_f:.1f} F)")
        except Exception as e:
            print(f"  [ERROR] {city}: Failed to fetch GFS - {e}")
    return results


def compute_error_distribution(city: str, cfg: dict) -> np.ndarray | None:
    """Compute historical GFS forecast errors (WU_actual - GFS_forecast) from cache."""
    # Load GFS historical cache. File naming is "gfs_365d_*" but actual coverage
    # is ~735 days (~2 years, e.g. cache/gfs_v4_<city>_2024-05-01_2026-05-06.json).
    # The "365d" prefix is legacy; select_forecast_cache picks the most-complete file.
    # (try city name and aliases like LosAngeles for LA)
    aliases = [city]
    if city == "LA":
        aliases.append("LosAngeles")
    gfs_file = None
    for alias in aliases:
        gfs_file = select_forecast_cache([
            f"gfs_365d_{alias}_*.json",
            f"gfs_v4_{alias}_*.json",
        ])
        if gfs_file:
            break
    if not gfs_file:
        print(f"  [WARN] {city}: No GFS historical cache found (gfs_365d_*/gfs_v4_*)")
        return None
    gfs_data = json.loads(gfs_file.read_text())
    gfs_hourly = gfs_data["hourly"]
    gfs_times = gfs_hourly["time"]  # local time strings
    gfs_temps = gfs_hourly["temperature_2m"]  # in Fahrenheit

    # Build GFS daily max (by local date)
    gfs_daily_max = {}
    for t, temp in zip(gfs_times, gfs_temps):
        if temp is None:
            continue
        date = t[:10]
        gfs_daily_max[date] = max(gfs_daily_max.get(date, -999), temp)

    # Load WU observations
    wu_file = CACHE_DIR / "wu_obs" / f"wu_obs_{cfg['icao']}.csv"
    if not wu_file.exists():
        print(f"  [WARN] {city}: No WU obs file found")
        return None

    wu_daily_max = {}
    with open(wu_file) as f:
        header = f.readline().strip().split(",")
        temp_idx = header.index("temp")
        date_idx = header.index("date_local")
        for line in f:
            parts = line.strip().split(",")
            if len(parts) <= max(temp_idx, date_idx):
                continue
            try:
                temp = int(parts[temp_idx])
                date = parts[date_idx]
                wu_daily_max[date] = max(wu_daily_max.get(date, -999), temp)
            except (ValueError, IndexError):
                continue

    # Compute errors on common dates
    errors = []
    for date in sorted(gfs_daily_max.keys()):
        if date in wu_daily_max:
            error = wu_daily_max[date] - gfs_daily_max[date]
            errors.append(error)

    if len(errors) < 30:
        print(f"  [WARN] {city}: Only {len(errors)} common dates (need >=30)")
        return None

    return np.array(errors)


def compute_bracket_probs(gfs_max: float, errors: np.ndarray, brackets: list,
                          unit: str) -> list:
    """
    Compute model probability for each bracket using empirical error distribution.

    gfs_max: GFS forecast max temperature in Fahrenheit (raw float)
    errors: historical (WU_°F_int - GFS_°F) per day, all in °F (verified:
            wu_obs CSVs store integer °F for all cities, US and international)
    brackets: list of (label, market_price) from Polymarket
    unit: 'F' or 'C' - the unit of the bracket labels

    Note on °F → °C scaling: the two paths
       (a) round((gfs_°F + err_°F - 32) * 5/9)   ← snap in °F first then convert
       (b) round((gfs_°F - 32)*5/9 + err_°F * 5/9)  ← scale errors then snap
    are algebraically identical because 5/9 distributes; verified empirically
    on Tokyo 326 days (0 divergent points). We use (b) for clarity.
    """
    # Convert GFS max to bracket unit
    if unit == "C":
        gfs_val = (gfs_max - 32) * 5 / 9
        # Errors are in °F; converting to °C is exact (1°F = 5/9°C)
        errors_in_unit = errors * 5 / 9
    else:
        gfs_val = gfs_max
        errors_in_unit = errors

    # Simulated outcomes = gfs + each historical error, snapped to integer grid
    # of the bracket unit (WU reports integer °F; PM °C brackets are integer °C)
    simulated = np.round(gfs_val + errors_in_unit).astype(int)
    n = len(simulated)

    result = []
    for label, mkt_price in brackets:
        label_str = str(label)
        if label_str.endswith("+"):
            threshold = int(label_str[:-1])
            prob = np.sum(simulated >= threshold) / n
        elif label_str.endswith("-"):
            threshold = int(label_str[:-1])
            prob = np.sum(simulated <= threshold) / n
        elif "-" in label_str and not label_str.startswith("-"):
            parts = label_str.split("-")
            lo, hi = int(parts[0]), int(parts[1])
            prob = np.sum((simulated >= lo) & (simulated <= hi)) / n
        else:
            val = int(label_str)
            prob = np.sum(simulated == val) / n

        edge = prob - mkt_price
        result.append({
            "bracket": label_str,
            "model_pct": prob,
            "market_pct": mkt_price,
            "edge": edge,
        })
    return result


def fetch_polymarket_brackets(city: str, cfg: dict) -> dict | None:
    """
    Fetch Polymarket bracket markets for a city on TARGET_DATE.
    Uses Gamma API events endpoint with slug search.
    """
    date_str = TARGET_DATE
    city_slug = cfg.get("slug", city.lower())
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    date_slug = dt.strftime("%B-%-d-%Y").lower()

    slug = f"highest-temperature-in-{city_slug}-on-{date_slug}"

    client = httpx.Client(proxy=PROXY, timeout=20, trust_env=False)
    try:
        resp = client.get(f"{PM_GAMMA_URL}/events", params={"slug": slug})
        if resp.status_code == 200:
            data = resp.json()
            if data:
                return _parse_pm_event(data[0] if isinstance(data, list) else data)
    except Exception:
        pass
    finally:
        client.close()
    return None


def _parse_pm_event(event: dict) -> dict:
    """Parse a Polymarket event into bracket format."""
    markets = event.get("markets", [])
    if not markets:
        return None

    unit = "C"  # default
    buckets = []
    for m in markets:
        question = m.get("question", "")
        prices = m.get("outcomePrices", "[]")
        if isinstance(prices, str):
            prices = json.loads(prices)

        # Extract bracket label from question
        # e.g. "Will the highest temperature be 73°F?"
        label = _extract_bracket_label(question)
        if label is None:
            continue

        if "°F" in question or "°f" in question:
            unit = "F"
        elif "°C" in question or "°c" in question:
            unit = "C"

        # outcomePrices[0] = Yes price
        yes_price = float(prices[0]) if prices else 0
        buckets.append((label, yes_price))

    return {"unit": unit, "buckets": buckets} if buckets else None


def _parse_pm_markets(markets: list) -> dict:
    """Parse individual market objects into bracket format."""
    unit = "F"
    buckets = []
    for m in markets:
        question = m.get("question", "")
        prices = m.get("outcomePrices", "[]")
        if isinstance(prices, str):
            prices = json.loads(prices)

        label = _extract_bracket_label(question)
        if label is None:
            continue

        if "°C" in question:
            unit = "C"

        yes_price = float(prices[0]) if prices else 0
        buckets.append((label, yes_price))

    return {"unit": unit, "buckets": buckets} if buckets else None


def _extract_bracket_label(question: str) -> str | None:
    """Extract bracket label from Polymarket question text."""
    q = question.lower()

    # "72°f or lower" / "72 or lower"
    m = re.search(r'(\d+)\s*°?[fc]?\s+or\s+(lower|less)', q)
    if m:
        return f"{m.group(1)}-"

    # "75°f or higher" / "75 or higher"
    m = re.search(r'(\d+)\s*°?[fc]?\s+or\s+(higher|more|above)', q)
    if m:
        return f"{m.group(1)}+"

    # "73°f" exact
    m = re.search(r'be\s+(\d+)\s*°[fc]', q)
    if m:
        return m.group(1)

    # Range "68-69"
    m = re.search(r'(\d+)\s*[-–]\s*(\d+)', q)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    # Fallback: any standalone number near degree
    m = re.search(r'(\d+)\s*°', q)
    if m:
        return m.group(1)

    return None


def main():
    print("=" * 80)
    print(f"Polymarket Weather Edge Comparison — {TARGET_DATE}")
    print("=" * 80)

    # Step 1: Fetch GFS forecasts
    print("\n[1/3] Fetching GFS forecasts from Open-Meteo...")
    gfs_forecasts = fetch_gfs_forecasts()
    if not gfs_forecasts:
        print("FATAL: No GFS forecasts retrieved. Aborting.")
        return

    # Step 2: Compute error distributions
    print("\n[2/3] Computing historical error distributions from cache...")
    error_dists = {}
    for city, cfg in CITIES.items():
        errors = compute_error_distribution(city, cfg)
        if errors is not None:
            error_dists[city] = errors
            print(f"  {city}: {len(errors)} days, mean_error={np.mean(errors):.2f}F, "
                  f"std={np.std(errors):.2f}F")

    # Step 3: Fetch Polymarket prices
    print("\n[3/3] Fetching Polymarket bracket prices...")
    pm_data = {}
    for city in CITIES:
        print(f"  Trying {city}...")
        brackets = fetch_polymarket_brackets(city, CITIES[city])
        if brackets and brackets.get("buckets"):
            pm_data[city] = brackets
            n = len(brackets["buckets"])
            print(f"  {city}: {n} brackets found ({brackets['unit']})")
        else:
            print(f"  {city}: No active markets found")

    # Step 4: Compare
    print("\n" + "=" * 80)
    print("RESULTS")
    print("=" * 80)

    cities_with_data = 0
    all_edges = []
    calib = json.loads((OUTPUT_DIR / "calibration_backtest_results.json").read_text())

    for city, cfg in CITIES.items():
        if city not in gfs_forecasts:
            print(f"\n{city}: SKIPPED (no GFS forecast)")
            continue

        gfs = gfs_forecasts[city]
        brier = None
        if city in calib["by_city"]:
            brier = calib["by_city"][city]["brier_score"]

        unit = cfg["unit"]
        if unit == "F":
            gfs_display = f"{gfs['max_f']:.1f} F"
        else:
            gfs_display = f"{gfs['max_c']:.1f} C"

        brier_str = f" | Model Brier: {brier:.4f}" if brier else ""

        if city not in pm_data:
            print(f"\nCity: {city} | GFS: {gfs_display}{brier_str}")
            print("  No Polymarket data available")

            # Still show model probabilities if we have error dist
            if city in error_dists:
                # Show hypothetical bracket probs
                gfs_val = gfs["max_f"] if unit == "F" else gfs["max_c"]
                rounded = round(gfs_val)
                print(f"  Model probability distribution (hypothetical brackets around {rounded}{unit}):")
                errors = error_dists[city]
                errors_u = errors * (5/9) if unit == "C" else errors
                sim = np.round(gfs_val + errors_u).astype(int)
                for offset in [-2, -1, 0, 1, 2]:
                    t = rounded + offset
                    p = np.sum(sim == t) / len(sim)
                    print(f"    {t}{unit}: {p*100:.1f}%")
                p_low = np.sum(sim < rounded - 2) / len(sim)
                p_high = np.sum(sim >= rounded + 3) / len(sim)
                print(f"    {rounded-3}{unit} or lower: {p_low*100:.1f}%")
                print(f"    {rounded+3}{unit} or higher: {p_high*100:.1f}%")
            continue

        cities_with_data += 1
        brackets = pm_data[city]

        if city not in error_dists:
            print(f"\nCity: {city} | GFS: {gfs_display}{brier_str}")
            print("  No historical error distribution available")
            print(f"  Market brackets: {brackets['buckets']}")
            continue

        # Compute model probs
        probs = compute_bracket_probs(
            gfs["max_f"], error_dists[city],
            brackets["buckets"], brackets["unit"]
        )

        print(f"\nCity: {city} | GFS: {gfs_display}{brier_str}")
        print(f"{'Bracket':<30} {'Model%':>8} {'Market%':>8} {'Edge':>8}  Signal")
        print("-" * 75)

        for p in sorted(probs, key=lambda x: -x["model_pct"]):
            signal = ""
            if abs(p["edge"]) > 0.05:
                if p["edge"] > 0:
                    signal = " <-- BUY"
                else:
                    signal = " <-- SELL/AVOID"
                all_edges.append({
                    "city": city, "bracket": p["bracket"],
                    "edge": p["edge"], "model": p["model_pct"],
                    "market": p["market_pct"],
                })

            print(f"{p['bracket']:<30} {p['model_pct']*100:>7.1f}% {p['market_pct']*100:>7.1f}% "
                  f"{p['edge']*100:>+7.1f}%{signal}")

    # Summary
    print("\n" + "=" * 80)
    print("EDGE SUMMARY (|edge| > 5%)")
    print("=" * 80)
    if all_edges:
        all_edges.sort(key=lambda x: -abs(x["edge"]))
        for e in all_edges:
            direction = "BUY" if e["edge"] > 0 else "SELL"
            print(f"  {e['city']:>10} | {e['bracket']:<15} | "
                  f"Model {e['model']*100:.1f}% vs Market {e['market']*100:.1f}% | "
                  f"Edge {e['edge']*100:+.1f}% | {direction}")
    else:
        print("  No edges > 5% found (or no Polymarket data available)")

    print(f"\nCities with both GFS + PM data: {cities_with_data}/{len(CITIES)}")
    print(f"Cities with error distributions: {len(error_dists)}/{len(CITIES)}")


if __name__ == "__main__":
    main()
