#!/usr/bin/env python3
"""M3 observed running-max residual experiment.

This script intentionally stops at the physical layer: it does not read market
prices, order books, fills, PnL, or live strategy outputs.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


DEFAULT_DECISION_HOURS = (18, 19, 20, 21)

DEFAULT_CORE_STATIONS = {
    "Boston": "KBOS",
    "LA": "KLAX",
    "London": "EGLL",
    "Miami": "KMIA",
    "NYC": "KLGA",
    "Phoenix": "KPHX",
    "Shanghai": "ZSPD",
    "Tokyo": "RJTT",
    "Warsaw": "EPWA",
}


# v0 timezone assumptions for cities currently present in fact_trades. These
# are only used to reconstruct local decision-time observations from UTC cache.
CITY_TIMEZONE = {
    "Amsterdam": "Europe/Amsterdam",
    "Ankara": "Europe/Istanbul",
    "Atlanta": "America/New_York",
    "Austin": "America/Chicago",
    "Beijing": "Asia/Shanghai",
    "Boston": "America/New_York",
    "BuenosAires": "America/Argentina/Buenos_Aires",
    "Busan": "Asia/Seoul",
    "CapeTown": "Africa/Johannesburg",
    "Chengdu": "Asia/Shanghai",
    "Chicago": "America/Chicago",
    "Chongqing": "Asia/Shanghai",
    "Dallas": "America/Chicago",
    "Denver": "America/Denver",
    "Guangzhou": "Asia/Shanghai",
    "Helsinki": "Europe/Helsinki",
    "HongKong": "Asia/Hong_Kong",
    "Houston": "America/Chicago",
    "Istanbul": "Europe/Istanbul",
    "Jakarta": "Asia/Jakarta",
    "Jeddah": "Asia/Riyadh",
    "Karachi": "Asia/Karachi",
    "KualaLumpur": "Asia/Kuala_Lumpur",
    "LA": "America/Los_Angeles",
    "Lagos": "Africa/Lagos",
    "London": "Europe/London",
    "Lucknow": "Asia/Kolkata",
    "Madrid": "Europe/Madrid",
    "Manila": "Asia/Manila",
    "MexicoCity": "America/Mexico_City",
    "Miami": "America/New_York",
    "Milan": "Europe/Rome",
    "Moscow": "Europe/Moscow",
    "Munich": "Europe/Berlin",
    "NYC": "America/New_York",
    "PanamaCity": "America/Panama",
    "Paris": "Europe/Paris",
    "Phoenix": "America/Phoenix",
    "SanFrancisco": "America/Los_Angeles",
    "SaoPaulo": "America/Sao_Paulo",
    "Seattle": "America/Los_Angeles",
    "Seoul": "Asia/Seoul",
    "Shanghai": "Asia/Shanghai",
    "Shenzhen": "Asia/Shanghai",
    "Singapore": "Asia/Singapore",
    "Taipei": "Asia/Taipei",
    "TelAviv": "Asia/Jerusalem",
    "Tokyo": "Asia/Tokyo",
    "Warsaw": "Europe/Warsaw",
    "Wellington": "Pacific/Auckland",
    "Wuhan": "Asia/Shanghai",
}


@dataclass(frozen=True)
class CityStation:
    city: str
    icao: str
    timezone: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="runtime/weather.db")
    parser.add_argument("--wu-dir", default="runtime/weather_edge_v1/market_data/cache/wu_obs")
    parser.add_argument(
        "--output-dir",
        default="docs/analysis/2026-06/generated/m3_observed_max_v0",
    )
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument(
        "--decision-hours",
        default=",".join(str(x) for x in DEFAULT_DECISION_HOURS),
        help="Comma-separated local decision hours, e.g. 18,19,20,21",
    )
    parser.add_argument("--min-obs-per-day", type=int, default=8)
    parser.add_argument("--core-only", action="store_true", help="Restrict to current legacy live core cities.")
    return parser.parse_args()


def load_city_stations(db_path: Path, core_only: bool) -> list[CityStation]:
    core_cities = set(DEFAULT_CORE_STATIONS)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT city, icao, SUM(n) AS n
            FROM (
              SELECT city, icao, COUNT(1) AS n
              FROM fact_trades
              WHERE city IS NOT NULL AND icao IS NOT NULL AND city <> '' AND icao <> ''
              GROUP BY city, icao
              UNION ALL
              SELECT city, icao, COUNT(1) AS n
              FROM fact_signal_candidates
              WHERE city IS NOT NULL AND icao IS NOT NULL AND city <> '' AND icao <> ''
              GROUP BY city, icao
            )
            GROUP BY city, icao
            ORDER BY city, n DESC
            """
        ).fetchall()

    seen: set[str] = set()
    out: list[CityStation] = []
    for city, icao, _n in rows:
        if city in seen:
            continue
        if core_only and city not in core_cities:
            continue
        tz = CITY_TIMEZONE.get(city)
        if not tz:
            continue
        out.append(CityStation(city=city, icao=icao, timezone=tz))
        seen.add(city)
    if core_only:
        for city, icao in DEFAULT_CORE_STATIONS.items():
            if city in seen:
                continue
            tz = CITY_TIMEZONE.get(city)
            if tz:
                out.append(CityStation(city=city, icao=icao, timezone=tz))
                seen.add(city)
    return out


def read_wu_cache(path: Path, timezone_name: str, min_obs_per_day: int) -> pd.DataFrame:
    df = pd.read_csv(path)
    if df.empty:
        return pd.DataFrame()
    required = {"valid_utc", "temp"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} missing columns: {sorted(missing)}")
    df = df[["valid_utc", "temp"]].copy()
    df["temp_f"] = pd.to_numeric(df["temp"], errors="coerce")
    df["valid_utc"] = pd.to_datetime(df["valid_utc"], utc=True, errors="coerce")
    df = df.dropna(subset=["valid_utc", "temp_f"])
    if df.empty:
        return pd.DataFrame()

    tz = ZoneInfo(timezone_name)
    df["local_dt"] = df["valid_utc"].dt.tz_convert(tz)
    df["target_date"] = df["local_dt"].dt.date.astype(str)
    df["obs_n_for_day"] = df.groupby("target_date")["temp_f"].transform("count")
    df = df[df["obs_n_for_day"] >= min_obs_per_day].copy()
    return df


def residual_rows_for_city(
    city_station: CityStation,
    wu_dir: Path,
    decision_hours: list[int],
    min_obs_per_day: int,
    start_date: str | None,
    end_date: str | None,
) -> tuple[list[dict], dict]:
    path = wu_dir / f"wu_obs_{city_station.icao}.csv"
    meta = {
        "city": city_station.city,
        "icao": city_station.icao,
        "timezone": city_station.timezone,
        "cache_path": str(path),
        "cache_exists": path.exists(),
        "input_rows": 0,
        "usable_days": 0,
        "output_rows": 0,
    }
    if not path.exists():
        return [], meta

    obs = read_wu_cache(path, city_station.timezone, min_obs_per_day)
    meta["input_rows"] = int(len(obs))
    if obs.empty:
        return [], meta

    if start_date:
        obs = obs[obs["target_date"] >= start_date]
    if end_date:
        obs = obs[obs["target_date"] <= end_date]
    if obs.empty:
        return [], meta

    rows: list[dict] = []
    for target_date, day in obs.groupby("target_date", sort=True):
        day = day.sort_values("local_dt")
        final_max_f = float(day["temp_f"].max())
        final_max_c = (final_max_f - 32.0) * 5.0 / 9.0
        meta["usable_days"] += 1
        for hour in decision_hours:
            cutoff = pd.Timestamp(f"{target_date} {hour:02d}:00:00", tz=city_station.timezone)
            prefix = day[day["local_dt"] <= cutoff]
            if prefix.empty:
                continue
            running_max_f = float(prefix["temp_f"].max())
            current_temp_f = float(prefix.iloc[-1]["temp_f"])
            running_max_c = (running_max_f - 32.0) * 5.0 / 9.0
            current_temp_c = (current_temp_f - 32.0) * 5.0 / 9.0
            residual_c = final_max_c - running_max_c
            if residual_c < -1e-9:
                # Should be impossible by construction; keep visible if cache has bad rows.
                residual_c = float("nan")
            rows.append(
                {
                    "city": city_station.city,
                    "icao": city_station.icao,
                    "timezone": city_station.timezone,
                    "target_date": target_date,
                    "decision_hour_local": hour,
                    "obs_count_day": int(day["temp_f"].count()),
                    "obs_count_to_decision": int(prefix["temp_f"].count()),
                    "first_obs_utc": day.iloc[0]["valid_utc"].isoformat(),
                    "last_obs_utc": day.iloc[-1]["valid_utc"].isoformat(),
                    "decision_last_obs_utc": prefix.iloc[-1]["valid_utc"].isoformat(),
                    "final_max_f": final_max_f,
                    "running_max_f": running_max_f,
                    "current_temp_f": current_temp_f,
                    "final_max_c": final_max_c,
                    "running_max_c": running_max_c,
                    "current_temp_c": current_temp_c,
                    "residual_c": residual_c,
                    "residual_ge_0_5c": int(residual_c >= 0.5),
                    "residual_ge_1_0c": int(residual_c >= 1.0),
                    "residual_ge_1_5c": int(residual_c >= 1.5),
                    # v0 proxy for 1C market bracket drift. Contract rounding rules
                    # need separate verification before using this for trading.
                    "floor_c_bucket_delta": int(math.floor(final_max_c) - math.floor(running_max_c)),
                }
            )
    meta["output_rows"] = len(rows)
    return rows, meta


def summarize(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if rows.empty:
        return pd.DataFrame(), pd.DataFrame()

    def quantile(p: float):
        return lambda s: float(s.quantile(p))

    summary = (
        rows.groupby(["city", "icao", "decision_hour_local"], dropna=False)
        .agg(
            city_days=("target_date", "nunique"),
            rows=("target_date", "count"),
            p50_residual_c=("residual_c", quantile(0.50)),
            p75_residual_c=("residual_c", quantile(0.75)),
            p90_residual_c=("residual_c", quantile(0.90)),
            p95_residual_c=("residual_c", quantile(0.95)),
            p99_residual_c=("residual_c", quantile(0.99)),
            max_residual_c=("residual_c", "max"),
            residual_ge_0_5c_rate=("residual_ge_0_5c", "mean"),
            residual_ge_1_0c_rate=("residual_ge_1_0c", "mean"),
            residual_ge_1_5c_rate=("residual_ge_1_5c", "mean"),
            bucket_delta_ge_1_rate=("floor_c_bucket_delta", lambda s: float((s >= 1).mean())),
            avg_obs_count_day=("obs_count_day", "mean"),
            avg_obs_count_to_decision=("obs_count_to_decision", "mean"),
        )
        .reset_index()
    )
    summary["physical_candidate_v0"] = (
        (summary["city_days"] >= 30)
        & (summary["p95_residual_c"] < 1.0)
        & (summary["bucket_delta_ge_1_rate"] <= 0.10)
    )
    summary = summary.sort_values(
        ["physical_candidate_v0", "p95_residual_c", "bucket_delta_ge_1_rate", "city_days"],
        ascending=[False, True, True, False],
    )

    hour_summary = (
        rows.groupby("decision_hour_local")
        .agg(
            city_days=("target_date", "count"),
            cities=("city", "nunique"),
            p50_residual_c=("residual_c", quantile(0.50)),
            p90_residual_c=("residual_c", quantile(0.90)),
            p95_residual_c=("residual_c", quantile(0.95)),
            residual_ge_1_0c_rate=("residual_ge_1_0c", "mean"),
            bucket_delta_ge_1_rate=("floor_c_bucket_delta", lambda s: float((s >= 1).mean())),
        )
        .reset_index()
    )
    return summary, hour_summary


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    wu_dir = Path(args.wu_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    decision_hours = [int(x) for x in args.decision_hours.split(",") if x.strip()]

    city_stations = load_city_stations(db_path, args.core_only)
    all_rows: list[dict] = []
    coverage: list[dict] = []
    for city_station in city_stations:
        rows, meta = residual_rows_for_city(
            city_station=city_station,
            wu_dir=wu_dir,
            decision_hours=decision_hours,
            min_obs_per_day=args.min_obs_per_day,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        all_rows.extend(rows)
        coverage.append(meta)

    detail = pd.DataFrame(all_rows)
    coverage_df = pd.DataFrame(coverage)
    summary, hour_summary = summarize(detail)

    detail_path = output_dir / "m3_observed_max_residual_detail.csv"
    summary_path = output_dir / "m3_observed_max_residual_by_city_hour.csv"
    hour_path = output_dir / "m3_observed_max_residual_by_hour.csv"
    coverage_path = output_dir / "m3_observed_max_cache_coverage.csv"
    manifest_path = output_dir / "manifest.json"

    detail.to_csv(detail_path, index=False)
    summary.to_csv(summary_path, index=False)
    hour_summary.to_csv(hour_path, index=False)
    coverage_df.to_csv(coverage_path, index=False)

    manifest = {
        "experiment": "m3_observed_max_residual_v0",
        "db": str(db_path),
        "wu_dir": str(wu_dir),
        "output_dir": str(output_dir),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "decision_hours": decision_hours,
        "min_obs_per_day": args.min_obs_per_day,
        "core_only": args.core_only,
        "cities_requested": len(city_stations),
        "cities_with_cache": int(coverage_df["cache_exists"].sum()) if not coverage_df.empty else 0,
        "cities_with_output": int((coverage_df["output_rows"] > 0).sum()) if not coverage_df.empty else 0,
        "detail_rows": int(len(detail)),
        "summary_rows": int(len(summary)),
        "outputs": {
            "detail": str(detail_path),
            "summary_by_city_hour": str(summary_path),
            "summary_by_hour": str(hour_path),
            "coverage": str(coverage_path),
        },
        "notes": [
            "Physical residual only; no market prices, PnL, orderbook, or live action.",
            "floor_c_bucket_delta is a v0 proxy and does not verify contract rounding rules.",
            "Timezone mapping is hardcoded in this script for v0 and should be promoted to a shared station profile if reused.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
