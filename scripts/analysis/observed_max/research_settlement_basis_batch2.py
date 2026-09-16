#!/usr/bin/env python3
"""Batch-2 settlement basis diagnosis for Moscow/Seoul/Shenzhen/MexicoCity/HongKong/Jakarta.

These cities nominally use the same station as our pipeline (per market rules),
yet round(daily max) alignment vs pm_history winners is below 100%. Hypotheses
tested per city:

- iem_raw      : max of IEM METAR temps (report_type 3+4), local-day, arith round
- iem_fchain   : per-ob C->F integer round, daily max F, F->C integer round
                 (simulates weather.gov / WU Fahrenheit display chain)
- wu_feed      : the actual feed behind the Wunderground history page, fetched
                 from api.weather.com v1 observations/historical (units=m).
                 Note: for some ICAOs WU silently maps to a *different* physical
                 station (e.g. ZGSZ -> Lau Fau Shan / HKO 45035).
- hko_extract  : HongKong only — HKO Daily Extract (climat) absolute daily max
                 (one decimal), tested with floor and round mapping to the
                 integer brackets.
- Jakarta      : rules point at WIHH (Halim) not WIII; validated on IEM WIHH.

Settlement truth: pm_history files with exactly one bracket at final_price>=0.99.

Outputs: docs/analysis/2026-06/generated/settlement_basis_batch2_v0/
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts/analysis/observed_max"))
sys.path.insert(0, str(REPO))
from scripts.ops.weather_market_proxy import production_market_proxy_url  # noqa: E402

from research_official_station_alignment import (  # noqa: E402
    arith_round,
    fetch_iem,
    load_winner,
    local_day_max,
    parse_bracket,
)

PROXIES = [None, production_market_proxy_url()]
WU_API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"  # public key embedded in wunderground.com

# city -> (icao, tz, wu_location_suffix)
CITIES = {
    "Moscow": ("UUWW", "Europe/Moscow", "RU"),
    "Seoul": ("RKSI", "Asia/Seoul", "KR"),
    "Shenzhen": ("ZGSZ", "Asia/Shanghai", "CN"),
    "MexicoCity": ("MMMX", "America/Mexico_City", "MX"),
}

JAKARTA_OFFICIAL = ("WIHH", "Asia/Jakarta", "ID")
JAKARTA_OURS = ("WIII", "Asia/Jakarta", "ID")


def fetch_json_chain(url: str, max_rounds: int = 3):
    last_err = None
    for rnd in range(max_rounds):
        for proxy in PROXIES:
            try:
                r = httpx.get(url, proxy=proxy, timeout=60)
                r.raise_for_status()
                return r.json()
            except Exception as e:  # noqa: BLE001
                last_err = f"{proxy}: {type(e).__name__}"
                time.sleep(0.5 + rnd)
    raise RuntimeError(f"fetch failed {url}: {last_err}")


def fetch_wu_history(icao: str, country: str, months: list[tuple[int, int]], cache_dir: Path) -> list[dict]:
    """Fetch WU (api.weather.com) historical obs, one call per month. units=m -> temp in deg C."""
    obs: list[dict] = []
    for year, month in months:
        start = f"{year:04d}{month:02d}01"
        # end-of-month
        if month == 12:
            ney, nem = year + 1, 1
        else:
            ney, nem = year, month + 1
        from datetime import date, timedelta

        end_d = date(ney, nem, 1) - timedelta(days=1)
        end = end_d.strftime("%Y%m%d")
        cache = cache_dir / f"wu_api_{icao}_{start}_{end}.json"
        if cache.exists() and cache.stat().st_size > 500:
            data = json.loads(cache.read_text())
        else:
            url = (
                f"https://api.weather.com/v1/location/{icao}:9:{country}/observations/historical.json"
                f"?apiKey={WU_API_KEY}&units=m&startDate={start}&endDate={end}"
            )
            data = fetch_json_chain(url)
            cache.write_text(json.dumps(data, ensure_ascii=False))
        obs.extend(data.get("observations") or [])
    return obs


def wu_daily_max(obs: list[dict], tz: str) -> tuple[pd.Series, str]:
    tzinfo = ZoneInfo(tz)
    rows = []
    names = set()
    for o in obs:
        t = o.get("temp")
        ts = o.get("valid_time_gmt")
        if t is None or ts is None:
            continue
        names.add(str(o.get("obs_name")))
        local = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(tzinfo)
        rows.append({"local_date": local.date().isoformat(), "temp": float(t)})
    if not rows:
        return pd.Series(dtype=float), ""
    df = pd.DataFrame(rows)
    return df.groupby("local_date")["temp"].max(), "; ".join(sorted(names))


def iem_fchain_daily_max(df: pd.DataFrame, tz: str) -> pd.Series:
    """Per-ob C -> integer F, local-day max F, back to integer C."""
    d = df.copy()
    d["valid"] = pd.to_datetime(d["valid"], utc=True)
    d["tmpc"] = pd.to_numeric(d["tmpc"], errors="coerce")
    d = d.dropna(subset=["tmpc"])
    d["tmpf_int"] = d["tmpc"].map(lambda c: arith_round(c * 9 / 5 + 32))
    d["local_date"] = d["valid"].dt.tz_convert(ZoneInfo(tz)).dt.date.astype(str)
    fmax = d.groupby("local_date")["tmpf_int"].max()
    return fmax.map(lambda f: arith_round((f - 32) * 5 / 9))


def eval_hypothesis(
    city: str,
    hypothesis: str,
    day_values: pd.Series,
    pm_history_dir: Path,
    end: str,
    mapper=None,
) -> pd.DataFrame:
    """day_values: local_date -> raw max. mapper: raw -> settled integer (default arith_round)."""
    mapper = mapper or (lambda v: arith_round(float(v)))
    rows = []
    for day, mx in day_values.items():
        day = str(day)
        if day > end:
            continue
        winner = load_winner(pm_history_dir, city, day)
        if winner is None:
            continue
        label, question = winner
        bracket = parse_bracket(label, question)
        if bracket is None:
            continue
        mapped = mapper(mx)
        rows.append(
            {
                "city": city,
                "hypothesis": hypothesis,
                "target_date": day,
                "winner_label": label,
                "raw_max": float(mx),
                "mapped_value": mapped,
                "match": bracket.contains(float(mapped)),
            }
        )
    return pd.DataFrame(rows)


def fetch_hko_daily_max(cache_dir: Path, months: list[tuple[int, int]]) -> pd.Series:
    """HKO Daily Extract (https://www.hko.gov.hk/cis/dailyExtract/dailyExtract_YYYYMM.xml).

    Despite the .xml extension the payload is JSON:
    {"stn": {"data": [{"month": M, "dayData": [[day, pressure, max_temp, ...], ...]}]}}
    max_temp is the HKO-station absolute daily max, one decimal.
    """
    values: dict[str, float] = {}
    for year, month in months:
        cache = cache_dir / f"hko_dailyExtract_{year:04d}{month:02d}.json"
        if cache.exists() and cache.stat().st_size > 200:
            data = json.loads(cache.read_text())
        else:
            url = f"https://www.hko.gov.hk/cis/dailyExtract/dailyExtract_{year:04d}{month:02d}.xml"
            r = httpx.get(url, timeout=60)
            r.raise_for_status()
            data = r.json()
            cache.write_text(json.dumps(data, ensure_ascii=False))
        for block in data["stn"]["data"]:
            if int(block["month"]) != month:
                continue
            for row in block["dayData"]:
                day_s, max_t = row[0], row[2]
                try:
                    day_i = int(day_s)
                    mx = float(max_t)
                except (TypeError, ValueError):
                    continue
                values[f"{year:04d}-{month:02d}-{day_i:02d}"] = mx
    return pd.Series(values).sort_index()


def fetch_jakarta_rules(cache_dir: Path, dates: list[str]) -> pd.DataFrame:
    """Confirm the official station in Jakarta market rules for each market day."""
    from research_official_resolution_source import extract_rule_fields  # noqa: PLC0415

    months = [
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    ]
    rows = []
    for day in dates:
        y, m, d = day.split("-")
        slug = f"highest-temperature-in-jakarta-on-{months[int(m) - 1]}-{int(d)}-{int(y)}"
        cache = cache_dir / f"{slug}.json"
        if cache.exists() and cache.stat().st_size > 100:
            data = json.loads(cache.read_text())
        else:
            data = fetch_json_chain(f"https://gamma-api.polymarket.com/events?slug={slug}")
            cache.write_text(json.dumps(data, ensure_ascii=False))
        if not data:
            rows.append({"target_date": day, "slug": slug, "official_icao": None, "station_name": None})
            continue
        desc = (data[0].get("markets") or [{}])[0].get("description") or ""
        fields = extract_rule_fields(desc)
        rows.append(
            {
                "target_date": day,
                "slug": slug,
                "official_icao": fields["official_icao"],
                "station_name": fields["station_name"],
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-05-01")
    parser.add_argument("--end", default="2026-06-09")
    parser.add_argument(
        "--pm-history-dir",
        default=str(REPO / "runtime/weather_edge_v1/market_data/cache/pm_history"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(REPO / "docs/analysis/2026-06/generated/settlement_basis_batch2_v0"),
    )
    parser.add_argument(
        "--cache-dir",
        default=str(REPO / "runtime/rule_source_research/obs_cache"),
    )
    parser.add_argument(
        "--gamma-cache-dir",
        default=str(REPO / "runtime/rule_source_research/gamma_cache"),
    )
    args = parser.parse_args()

    pm_history_dir = Path(args.pm_history_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    gamma_cache_dir = Path(args.gamma_cache_dir)
    gamma_cache_dir.mkdir(parents=True, exist_ok=True)

    from datetime import date, timedelta

    fetch_end = (date.fromisoformat(args.end) + timedelta(days=2)).isoformat()
    months = [(2026, 5), (2026, 6)]
    frames: list[pd.DataFrame] = []

    # ---- METAR cities: iem_raw + iem_fchain + wu_feed -------------------
    for city, (icao, tz, country) in CITIES.items():
        iem = fetch_iem(icao, "tmpc", args.start, fetch_end, cache_dir)
        frames.append(
            eval_hypothesis(city, f"iem_raw_{icao}", local_day_max(iem, "tmpc", tz), pm_history_dir, args.end)
        )
        frames.append(
            eval_hypothesis(city, f"iem_fchain_{icao}", iem_fchain_daily_max(iem, tz), pm_history_dir, args.end)
        )
        try:
            obs = fetch_wu_history(icao, country, months, cache_dir)
            dm, names = wu_daily_max(obs, tz)
            print(f"{city}: wu_feed station(s) = {names}")
            frames.append(eval_hypothesis(city, f"wu_feed_{icao}", dm, pm_history_dir, args.end))
        except RuntimeError as e:
            print(f"{city}: wu_feed FETCH FAILED: {e}", file=sys.stderr)

    # ---- Jakarta: rules check + WIII baseline vs WIHH official ----------
    jak_days = sorted(
        p.stem.split("_")[1] for p in pm_history_dir.glob("Jakarta_*.json")
        if args.start <= p.stem.split("_")[1] <= args.end
    )
    jak_rules = fetch_jakarta_rules(gamma_cache_dir, jak_days)
    jak_rules.to_csv(out_dir / "jakarta_rules_stations.csv", index=False)
    print("Jakarta rules stations:")
    print(jak_rules.to_string(index=False))

    for tag, (icao, tz, country) in {"ours": JAKARTA_OURS, "official": JAKARTA_OFFICIAL}.items():
        iem = fetch_iem(icao, "tmpc", args.start, fetch_end, cache_dir)
        frames.append(
            eval_hypothesis("Jakarta", f"iem_raw_{icao}", local_day_max(iem, "tmpc", tz), pm_history_dir, args.end)
        )
    try:
        obs = fetch_wu_history(JAKARTA_OFFICIAL[0], JAKARTA_OFFICIAL[2], months, cache_dir)
        dm, names = wu_daily_max(obs, JAKARTA_OFFICIAL[1])
        print(f"Jakarta: wu_feed(WIHH) station(s) = {names}")
        frames.append(eval_hypothesis("Jakarta", "wu_feed_WIHH", dm, pm_history_dir, args.end))
    except RuntimeError as e:
        print(f"Jakarta: wu_feed FETCH FAILED: {e}", file=sys.stderr)

    # ---- HongKong: HKO Daily Extract, floor vs round mapping ------------
    hko = fetch_hko_daily_max(cache_dir, months)
    frames.append(
        eval_hypothesis(
            "HongKong", "hko_extract_floor", hko, pm_history_dir, args.end,
            mapper=lambda v: math.floor(float(v)),
        )
    )
    frames.append(
        eval_hypothesis(
            "HongKong", "hko_extract_round", hko, pm_history_dir, args.end,
            mapper=lambda v: arith_round(float(v)),
        )
    )

    detail = pd.concat([f for f in frames if len(f)], ignore_index=True)
    for city in detail["city"].unique():
        detail[detail["city"] == city].to_csv(out_dir / f"{city}_alignment_detail.csv", index=False)

    summary = (
        detail.groupby(["city", "hypothesis"])
        .agg(days=("match", "size"), matches=("match", "sum"))
        .reset_index()
    )
    summary["align_rate"] = (summary["matches"] / summary["days"]).round(4)
    summary.to_csv(out_dir / "hypothesis_summary.csv", index=False)
    print("\n" + summary.to_string(index=False))


if __name__ == "__main__":
    main()
