#!/usr/bin/env python3
"""Fetch Polymarket weather market rules and extract official resolution station per city.

For each city in weather-predict city_pools, fetch recent daily-high-temperature
events from Gamma API, parse the market description for the official resolution
station (name + Wunderground history URL + ICAO), and compare against our
pipeline's assumed ICAO.

Network goes through local proxy (mihomo). Results are cached so reruns are cheap.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[3]
WEATHER_PREDICT = Path("/Users/deepsleep/projects/weather-predict")
sys.path.insert(0, str(WEATHER_PREDICT))

from city_pools import FULL_CITY_CONFIGS  # noqa: E402

PROXIES = ["http://127.0.0.1:7897", "http://127.0.0.1:7890"]
GAMMA = "https://gamma-api.polymarket.com"

MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]

STATION_RE = re.compile(
    r"recorded (?:at|by) the (.+?)(?: Station)? in degrees", re.IGNORECASE
)
WU_URL_RE = re.compile(r"https://www\.wunderground\.com/history/daily/(\S+?)(?:[).,\s]|$)")
ANY_URL_RE = re.compile(r"https?://\S+")
PRECISION_RE = re.compile(
    r"measures temperatures (?:in \w+ )?to (whole degrees|one decimal)", re.IGNORECASE
)


def event_slug(city_slug: str, d: date) -> str:
    return f"highest-temperature-in-{city_slug}-on-{MONTHS[d.month - 1]}-{d.day}-{d.year}"


def fetch_json(url: str, max_rounds: int = 4):
    last_err = None
    for rnd in range(max_rounds):
        for proxy in PROXIES:
            try:
                r = httpx.get(url, proxy=proxy, timeout=30)
                r.raise_for_status()
                return r.json()
            except Exception as e:  # noqa: BLE001
                last_err = f"{proxy}: {type(e).__name__}"
                time.sleep(0.5 + rnd)
    raise RuntimeError(f"all proxies failed for {url}: {last_err}")


def extract_rule_fields(description: str) -> dict:
    station_name = None
    m = STATION_RE.search(description)
    if m:
        station_name = m.group(1).strip()
    wu_url = None
    icao = None
    m = WU_URL_RE.search(description)
    if m:
        wu_url = m.group(1).rstrip(".")
        tail = wu_url.rstrip("/").split("/")[-1]
        if re.fullmatch(r"[A-Z0-9]{4}", tail):
            icao = tail
    source_url = None
    m = ANY_URL_RE.search(description)
    if m:
        source_url = m.group(0).rstrip(".,)")
    is_wu = source_url is not None and "wunderground.com" in source_url
    precision = None
    m = PRECISION_RE.search(description)
    if m:
        precision = "decimal" if "decimal" in m.group(1).lower() else "whole"
    return {
        "station_name": station_name,
        "wu_url": wu_url,
        "official_icao": icao,
        "source_url": source_url,
        "source_is_wu": is_wu,
        "precision": precision,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=6, help="recent days to probe per city")
    parser.add_argument("--end-date", default=None, help="last event date to probe (YYYY-MM-DD)")
    parser.add_argument(
        "--output-dir",
        default=str(REPO / "docs/analysis/2026-06/generated/official_resolution_source_v0"),
    )
    parser.add_argument(
        "--cache-dir",
        default=str(REPO / "runtime/rule_source_research/gamma_cache"),
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    end = date.fromisoformat(args.end_date) if args.end_date else date.today() - timedelta(days=1)
    probe_dates = [end - timedelta(days=i) for i in range(args.days)]

    rows = []
    for city, cfg in sorted(FULL_CITY_CONFIGS.items()):
        slug = cfg["slug"]
        our_icao = cfg["icao"]
        found = None
        tried = []
        for d in probe_dates:
            es = event_slug(slug, d)
            cache_file = cache_dir / f"{es}.json"
            try:
                if cache_file.exists():
                    data = json.loads(cache_file.read_text())
                else:
                    data = fetch_json(f"{GAMMA}/events?slug={es}")
                    cache_file.write_text(json.dumps(data, ensure_ascii=False))
                tried.append(es)
            except RuntimeError as e:
                print(f"  ! {city} {es}: {e}", file=sys.stderr)
                continue
            if not data:
                continue
            ev = data[0]
            markets = ev.get("markets") or []
            if not markets:
                continue
            desc = markets[0].get("description") or ""
            fields = extract_rule_fields(desc)
            if fields["official_icao"] or fields["station_name"] or fields["source_url"]:
                found = {
                    "event_slug": es,
                    "event_date": d.isoformat(),
                    **fields,
                    "description_head": desc[:300].replace("\n", " "),
                }
                break
        row = {
            "city": city,
            "unit": cfg["unit"],
            "our_icao": our_icao,
            "official_icao": found["official_icao"] if found else None,
            "icao_match": (found["official_icao"] == our_icao) if found and found["official_icao"] else None,
            "station_name": found["station_name"] if found else None,
            "source_is_wu": found["source_is_wu"] if found else None,
            "precision": found["precision"] if found else None,
            "source_url": found["source_url"] if found else None,
            "wu_url": found["wu_url"] if found else None,
            "event_slug": found["event_slug"] if found else None,
            "probed": len(tried),
        }
        rows.append(row)
        if row["icao_match"]:
            status = "MATCH"
        elif row["icao_match"] is False:
            status = "DIFF"
        elif found and not found["source_is_wu"]:
            status = "NONWU"
        else:
            status = "??"
        print(
            f"{status:5} {city:14} ours={our_icao} official={row['official_icao']} "
            f"prec={row['precision']} name={row['station_name']} url={row['source_url']}"
        )

    out_csv = out_dir / "official_resolution_source.csv"
    with out_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    n_diff = sum(1 for r in rows if r["icao_match"] is False)
    n_unknown = sum(1 for r in rows if r["icao_match"] is None)
    print(f"\nsaved {out_csv}  cities={len(rows)} diff={n_diff} unknown={n_unknown}")


if __name__ == "__main__":
    main()
