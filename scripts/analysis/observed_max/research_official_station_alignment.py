#!/usr/bin/env python3
"""Validate official resolution stations against pm_history winners.

For the cities where the Polymarket rules point at a different station than our
pipeline (Paris->LFPB, London->EGLC, Milan->LIMC, Chicago->KORD,
KualaLumpur->WMKK, PanamaCity->MPMG), fetch the official station's METAR
temperature history from IEM, compute the local-day max, and check alignment
with the official settlement winner from pm_history.

HongKong is validated separately against the HKO open-data daily max (decimal
precision, "Absolute Daily Max").
"""

from __future__ import annotations

import argparse
import io
import json
import math
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

REPO = Path(__file__).resolve().parents[3]

PROXIES = [None, "http://127.0.0.1:7897", "http://127.0.0.1:7890"]

# city -> (official_icao, market_unit, iem_data_col, tz)
DIFF_CITIES = {
    "Paris": ("LFPB", "C", "tmpc", "Europe/Paris"),
    "London": ("EGLC", "C", "tmpc", "Europe/London"),
    "Milan": ("LIMC", "C", "tmpc", "Europe/Rome"),
    "Chicago": ("KORD", "F", "tmpf", "America/Chicago"),
    "KualaLumpur": ("WMKK", "C", "tmpc", "Asia/Kuala_Lumpur"),
    "PanamaCity": ("MPMG", "C", "tmpc", "America/Panama"),
}

WIN_THRESHOLD = 0.99


@dataclass(frozen=True)
class Bracket:
    label: str
    low: float | None
    high: float | None

    def contains(self, value: float) -> bool:
        if self.low is not None and value < self.low:
            return False
        if self.high is not None and value > self.high:
            return False
        return True


def parse_bracket(label_value: object, question_value: object) -> Bracket | None:
    label = str(label_value or "").replace("°", "").strip()
    question = str(question_value or "").lower()
    if not label:
        return None
    nums: list[float] = []
    if "-" in label:
        parts = label.replace("+", "").split("-", 1)
        try:
            nums = [float(parts[0]), float(parts[1])]
        except (TypeError, ValueError):
            nums = []
    if not nums:
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", label)]
    if not nums:
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", question)]
    if not nums:
        return None
    if "or below" in question or "or lower" in question:
        return Bracket(label=label, low=None, high=nums[0])
    if "or higher" in question or "or above" in question or label.endswith("+"):
        return Bracket(label=label, low=nums[0], high=None)
    if len(nums) >= 2:
        return Bracket(label=label, low=nums[0], high=nums[1])
    return Bracket(label=label, low=nums[0], high=nums[0])


def fetch_text(url: str, params: list | dict | None = None, max_rounds: int = 3) -> str:
    last_err = None
    for rnd in range(max_rounds):
        for proxy in PROXIES:
            try:
                r = httpx.get(url, params=params, proxy=proxy, timeout=90)
                r.raise_for_status()
                return r.text
            except Exception as e:  # noqa: BLE001
                last_err = f"{proxy}: {type(e).__name__}"
                time.sleep(1 + rnd)
    raise RuntimeError(f"fetch failed {url}: {last_err}")


def fetch_iem(icao: str, data_col: str, start: str, end: str, cache_dir: Path) -> pd.DataFrame:
    cache = cache_dir / f"iem_{icao}_{start}_{end}.csv"
    if cache.exists() and cache.stat().st_size > 1000:
        return pd.read_csv(cache)
    y1, m1, d1 = start.split("-")
    y2, m2, d2 = end.split("-")
    params = [
        ("station", icao),
        ("data", data_col),
        ("year1", y1), ("month1", m1), ("day1", d1),
        ("year2", y2), ("month2", m2), ("day2", d2),
        ("tz", "UTC"),
        ("format", "comma"),
        ("latlon", "no"),
        ("missing", "M"),
        ("trace", "T"),
        ("direct", "no"),
        ("report_type", "3"),
        ("report_type", "4"),
    ]
    text = fetch_text("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py", params)
    lines = [l for l in text.splitlines() if not l.startswith("#") and l.strip()]
    if len(lines) < 2:
        raise RuntimeError(f"IEM returned no data for {icao}")
    cache.write_text("\n".join(lines) + "\n")
    return pd.read_csv(cache)


def local_day_max(df: pd.DataFrame, data_col: str, tz: str) -> pd.Series:
    df = df.copy()
    df["valid"] = pd.to_datetime(df["valid"], utc=True)
    df[data_col] = pd.to_numeric(df[data_col], errors="coerce")
    df = df.dropna(subset=[data_col])
    df["local_date"] = df["valid"].dt.tz_convert(ZoneInfo(tz)).dt.date.astype(str)
    return df.groupby("local_date")[data_col].max()


def arith_round(x: float) -> int:
    return math.floor(x + 0.5)


def load_winner(pm_history_dir: Path, city: str, day: str) -> tuple[str, str] | None:
    f = pm_history_dir / f"{city}_{day}.json"
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    brackets = data.get("brackets") or []
    winners = [b for b in brackets if (b.get("final_price") or 0) >= WIN_THRESHOLD]
    if len(winners) != 1:
        return None
    w = winners[0]
    return str(w.get("label") or ""), str(w.get("question") or "")


def validate_city(
    city: str,
    icao: str,
    unit: str,
    data_col: str,
    tz: str,
    pm_history_dir: Path,
    cache_dir: Path,
    start: str,
    end: str,
) -> pd.DataFrame:
    # IEM asos.py treats the end date as exclusive; fetch two extra days so the
    # last local day is complete, then only evaluate local days <= end.
    from datetime import date, timedelta

    fetch_end = (date.fromisoformat(end) + timedelta(days=2)).isoformat()
    obs = fetch_iem(icao, data_col, start, fetch_end, cache_dir)
    day_max = local_day_max(obs, data_col, tz)
    rows = []
    for day, mx in day_max.items():
        if str(day) > end:
            continue
        winner = load_winner(pm_history_dir, city, day)
        if winner is None:
            continue
        label, question = winner
        bracket = parse_bracket(label, question)
        if bracket is None:
            continue
        rounded = arith_round(float(mx))
        rows.append(
            {
                "city": city,
                "official_icao": icao,
                "target_date": day,
                "winner_label": label,
                "official_station_max": float(mx),
                "official_station_round": rounded,
                "match": bracket.contains(float(rounded)),
            }
        )
    return pd.DataFrame(rows)


def validate_hongkong(pm_history_dir: Path, cache_dir: Path) -> pd.DataFrame:
    """HKO open data: daily max temp (decimal) at HKO station."""
    cache = cache_dir / "hko_clmmaxt_2026.json"
    if cache.exists():
        data = json.loads(cache.read_text())
    else:
        text = fetch_text(
            "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php",
            {"dataType": "CLMMAXT", "station": "HKO", "rformat": "json", "year": "2026"},
        )
        data = json.loads(text)
        cache.write_text(json.dumps(data, ensure_ascii=False))
    rows = []
    for entry in data.get("data", []):
        year, month, day, value = entry[0], entry[1], entry[2], entry[3]
        try:
            mx = float(value)
        except (TypeError, ValueError):
            continue
        date_str = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        winner = load_winner(pm_history_dir, "HongKong", date_str)
        if winner is None:
            continue
        label, question = winner
        bracket = parse_bracket(label, question)
        if bracket is None:
            continue
        rows.append(
            {
                "city": "HongKong",
                "official_icao": "HKO",
                "target_date": date_str,
                "winner_label": label,
                "official_station_max": mx,
                "official_station_round": mx,
                "match": bracket.contains(mx),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-04-01")
    parser.add_argument("--end", default="2026-06-10")
    parser.add_argument(
        "--pm-history-dir",
        default=str(REPO / "runtime/weather_edge_v1/market_data/cache/pm_history"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(REPO / "docs/analysis/2026-06/generated/official_resolution_source_v0"),
    )
    parser.add_argument(
        "--cache-dir",
        default=str(REPO / "runtime/rule_source_research/obs_cache"),
    )
    args = parser.parse_args()

    pm_history_dir = Path(args.pm_history_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    frames = []
    for city, (icao, unit, data_col, tz) in DIFF_CITIES.items():
        try:
            df = validate_city(
                city, icao, unit, data_col, tz, pm_history_dir, cache_dir, args.start, args.end
            )
            frames.append(df)
            rate = df["match"].mean() if len(df) else float("nan")
            print(f"{city:14} {icao} days={len(df)} align={rate:.0%}" if len(df) else f"{city:14} {icao} days=0")
        except RuntimeError as e:
            print(f"{city:14} {icao} FETCH FAILED: {e}", file=sys.stderr)

    try:
        hk = validate_hongkong(pm_history_dir, cache_dir)
        frames.append(hk)
        rate = hk["match"].mean() if len(hk) else float("nan")
        print(f"{'HongKong':14} HKO  days={len(hk)} align={rate:.0%}" if len(hk) else "HongKong       HKO days=0")
    except RuntimeError as e:
        print(f"HongKong HKO FETCH FAILED: {e}", file=sys.stderr)

    detail = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    detail_path = out_dir / "official_station_alignment_detail.csv"
    detail.to_csv(detail_path, index=False)
    if len(detail):
        summary = (
            detail.groupby(["city", "official_icao"])
            .agg(days=("match", "size"), matches=("match", "sum"))
            .reset_index()
        )
        summary["align_rate"] = summary["matches"] / summary["days"]
        summary_path = out_dir / "official_station_alignment_summary.csv"
        summary.to_csv(summary_path, index=False)
        print(f"\nsaved {detail_path}\nsaved {summary_path}")
        print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
