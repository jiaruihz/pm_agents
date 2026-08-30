#!/usr/bin/env python3
"""Build a reusable forecast-peak-clock backfill dataset.

The current-YES/no-reheat family needs a point-in-time feature that answers:
"according to the forecast, when should today's high temperature occur?"

Historical paper snapshots mostly saved only forecast max, not the hourly
forecast curve.  This script materializes a shared research dataset from
Open-Meteo Single Runs fixed model runs by default, using a deterministic
D-1 12:00 UTC run for each target city-date.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
WEATHER_PREDICT_ROOT = ROOT.parent / "weather-predict"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(WEATHER_PREDICT_ROOT) not in sys.path:
    sys.path.insert(0, str(WEATHER_PREDICT_ROOT))

from city_pools import FULL_CITY_CONFIGS  # type: ignore  # noqa: E402
from weather_clock_contract import local_wall_time_to_utc  # noqa: E402
from weather_data_feed.city_calendar import city_timezone_name  # noqa: E402


DB = ROOT / "runtime/weather.db"
FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/feature_rows.csv"
RUNTIME_CACHE_DIR = ROOT / "runtime/weather_edge_v1/market_data/cache/open_meteo_historical_forecast"
SINGLE_RUN_CACHE_DIR = ROOT / "runtime/weather_edge_v1/market_data/cache/open_meteo_single_runs_forecast"
LEGACY_CACHE_DIR = (
    ROOT
    / "docs/analysis/2026-06/generated/theta_current_yes_forecast_peak_clock_backfill_v3/open_meteo_historical_forecast"
)
OUT_CSV = ROOT / "runtime/weather_edge_v1/market_data/research/forecast_peak_clock_backfill_v1.csv"
OUT_JSON = ROOT / "runtime/weather_edge_v1/market_data/research/forecast_peak_clock_backfill_v1_summary.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-18-forecast-peak-clock-backfill-dataset-v1.md"

FORECAST_MODELS = {
    "gfs": "gfs_seamless",
    "ecmwf": "ecmwf_ifs025",
}
SINGLE_RUN_API = "https://single-runs-api.open-meteo.com/v1/forecast"
HISTORICAL_FORECAST_API = "https://historical-forecast-api.open-meteo.com/v1/forecast"


@dataclass(frozen=True)
class CacheHit:
    path: Path
    source: str
    start_date: str
    end_date: str


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def forecast_values_hash(rows: list[tuple[str, float]]) -> str:
    payload = [[str(ts), round(float(temp), 3)] for ts, temp in rows]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def parse_cache_name(path: Path, model: str, city: str) -> CacheHit | None:
    prefix = f"{model}_{city}_"
    if not path.name.startswith(prefix) or path.suffix != ".json":
        return None
    stem = path.stem[len(prefix) :]
    parts = stem.split("_")
    if len(parts) < 2:
        return None
    start_date, end_date = parts[-2], parts[-1]
    if len(start_date) != 10 or len(end_date) != 10:
        return None
    source = "runtime_cache" if RUNTIME_CACHE_DIR in path.parents else "legacy_generated_cache"
    return CacheHit(path=path, source=source, start_date=start_date, end_date=end_date)


def find_cache(model: str, city: str, start_date: str, end_date: str) -> CacheHit | None:
    hits: list[CacheHit] = []
    for cache_dir in [RUNTIME_CACHE_DIR, LEGACY_CACHE_DIR]:
        if not cache_dir.exists():
            continue
        for path in cache_dir.glob(f"{model}_{city}_*.json"):
            hit = parse_cache_name(path, model, city)
            if hit and hit.start_date <= start_date and hit.end_date >= end_date:
                hits.append(hit)
    if not hits:
        return None
    hits.sort(key=lambda h: (h.source != "runtime_cache", h.start_date, h.end_date))
    return hits[0]


def fetch_forecast(
    client: httpx.Client,
    *,
    city: str,
    model: str,
    start_date: str,
    end_date: str,
) -> tuple[dict[str, Any] | None, str]:
    cfg = FULL_CITY_CONFIGS[city]
    params = {
        "latitude": cfg["lat"],
        "longitude": cfg["lon"],
        "hourly": "temperature_2m",
        "temperature_unit": "fahrenheit",
        "models": FORECAST_MODELS[model],
        "start_date": start_date,
        "end_date": end_date,
        "past_forecast_days": 1,
        "timezone": "auto",
    }
    response = client.get(HISTORICAL_FORECAST_API, params=params)
    if response.status_code != 200:
        return {"status_code": response.status_code, "body": response.text[:500], "params": params}, "error"
    payload = response.json()
    RUNTIME_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNTIME_CACHE_DIR / f"{model}_{city}_{start_date}_{end_date}.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    time.sleep(0.1)
    return payload, "fetched"


def load_payload(
    client: httpx.Client,
    *,
    city: str,
    model: str,
    start_date: str,
    end_date: str,
    fetch_missing: bool,
    promote_cache: bool,
) -> tuple[dict[str, Any] | None, str, str | None]:
    hit = find_cache(model, city, start_date, end_date)
    if hit:
        if promote_cache and hit.source != "runtime_cache":
            RUNTIME_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            target = RUNTIME_CACHE_DIR / hit.path.name
            if not target.exists():
                shutil.copy2(hit.path, target)
            return json.loads(target.read_text(encoding="utf-8")), "promoted_cache", str(target)
        return json.loads(hit.path.read_text(encoding="utf-8")), hit.source, str(hit.path)
    if fetch_missing:
        payload, status = fetch_forecast(
            client,
            city=city,
            model=model,
            start_date=start_date,
            end_date=end_date,
        )
        cache_path = RUNTIME_CACHE_DIR / f"{model}_{city}_{start_date}_{end_date}.json"
        return payload, status, str(cache_path) if status == "fetched" else None
    return None, "missing_cache", None


def single_run_time_utc(target_date: str, *, run_day_offset: int, run_hour_utc: int) -> str:
    target_day = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    run_day = target_day - timedelta(days=run_day_offset)
    run_time = run_day.replace(hour=run_hour_utc, minute=0, second=0, microsecond=0)
    return run_time.strftime("%Y-%m-%dT%H:%M")


def single_run_cache_path(city: str, model: str, target_date: str, run_time_utc: str) -> Path:
    run_key = run_time_utc.replace("-", "").replace(":", "").replace("T", "T") + "Z"
    return SINGLE_RUN_CACHE_DIR / f"{model}_{city}_{target_date}_run_{run_key}.json"


def fetch_single_run_forecast(
    client: httpx.Client,
    *,
    city: str,
    model: str,
    target_date: str,
    run_time_utc: str,
    forecast_days: int,
) -> tuple[dict[str, Any] | None, str]:
    cfg = FULL_CITY_CONFIGS[city]
    params = {
        "latitude": cfg["lat"],
        "longitude": cfg["lon"],
        "hourly": "temperature_2m",
        "temperature_unit": "fahrenheit",
        "models": FORECAST_MODELS[model],
        "run": run_time_utc,
        "forecast_days": forecast_days,
        "timezone": "auto",
    }
    response = None
    for attempt in range(5):
        response = client.get(SINGLE_RUN_API, params=params)
        if response.status_code not in {429, 500, 502, 503, 504}:
            break
        time.sleep(0.75 * (attempt + 1))
    assert response is not None
    if response.status_code != 200:
        return {
            "status_code": response.status_code,
            "body": response.text[:500],
            "params": params,
            "target_date": target_date,
        }, "single_runs_error"
    payload = response.json()
    SINGLE_RUN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    single_run_cache_path(city, model, target_date, run_time_utc).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    time.sleep(0.1)
    return payload, "single_runs_fetched"


def payload_has_target_day_hourly(payload: dict[str, Any] | None, target_date: str) -> bool:
    hourly = payload.get("hourly", {}) if isinstance(payload, dict) else {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    for ts, temp in zip(times, temps, strict=False):
        if str(ts).startswith(target_date) and temp is not None:
            return True
    return False


def single_run_unavailable(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("status_code") != 400:
        return False
    body = str(payload.get("body") or "").lower()
    return "requested model run is not available" in body


def load_single_run_payload(
    client: httpx.Client,
    *,
    city: str,
    model: str,
    target_date: str,
    fetch_missing: bool,
    run_day_offset: int,
    run_hour_utc: int,
    forecast_days: int,
) -> tuple[dict[str, Any] | None, str, str | None, str]:
    run_time_utc = single_run_time_utc(
        target_date,
        run_day_offset=run_day_offset,
        run_hour_utc=run_hour_utc,
    )
    cache_path = single_run_cache_path(city, model, target_date, run_time_utc)
    if cache_path.exists():
        return (
            json.loads(cache_path.read_text(encoding="utf-8")),
            "single_runs_runtime_cache",
            str(cache_path),
            run_time_utc,
        )
    if fetch_missing:
        payload, status = fetch_single_run_forecast(
            client,
            city=city,
            model=model,
            target_date=target_date,
            run_time_utc=run_time_utc,
            forecast_days=forecast_days,
        )
        return payload, status, str(cache_path) if status == "single_runs_fetched" else None, run_time_utc
    return None, "single_runs_missing_cache", None, run_time_utc


def build_single_run_task(
    *,
    city: str,
    target_date: str,
    model: str,
    fetch_missing: bool,
    run_day_offsets: list[int],
    run_hour_utc: int,
    forecast_days: int,
) -> dict[str, Any]:
    last_result: dict[str, Any] | None = None
    with httpx.Client(timeout=60.0) as client:
        for run_day_offset in run_day_offsets:
            payload, status, cache_path, run_time_utc = load_single_run_payload(
                client,
                city=city,
                model=model,
                target_date=target_date,
                fetch_missing=fetch_missing,
                run_day_offset=run_day_offset,
                run_hour_utc=run_hour_utc,
                forecast_days=forecast_days,
            )
            last_result = {
                "city": city,
                "target_date": target_date,
                "model": model,
                "payload": payload,
                "status": status,
                "cache_path": cache_path,
                "run_time_utc": run_time_utc,
                "run_policy": f"d_minus_{run_day_offset}_{run_hour_utc:02d}z",
            }
            if status == "single_runs_error" and single_run_unavailable(payload):
                continue
            if payload is None or status == "single_runs_error":
                return last_result
            if payload_has_target_day_hourly(payload, target_date):
                return last_result
    assert last_result is not None
    return last_result


def derive_peak_rows(
    payload: dict[str, Any],
    *,
    city: str,
    model: str,
    target_dates: set[str] | None = None,
    api_source: str | None = None,
    run_time_utc: str | None = None,
    run_policy: str | None = None,
) -> list[dict[str, Any]]:
    hourly = payload.get("hourly", {}) if isinstance(payload, dict) else {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    if not times or not temps or len(times) != len(temps):
        return []

    by_date: dict[str, list[tuple[str, float]]] = {}
    for ts, temp in zip(times, temps, strict=False):
        if temp is None:
            continue
        try:
            value = float(temp)
        except Exception:
            continue
        by_date.setdefault(str(ts)[:10], []).append((str(ts), value))

    cfg = FULL_CITY_CONFIGS[city]
    offset = int(payload.get("utc_offset_seconds") or 0)
    timezone_name = str(payload.get("timezone") or city_timezone_name(city) or "")
    rows: list[dict[str, Any]] = []
    for target_date, day_rows in sorted(by_date.items()):
        if target_dates is not None and target_date not in target_dates:
            continue
        if not day_rows:
            continue
        max_f = max(value for _, value in day_rows)
        peak_local_time = min(ts for ts, value in day_rows if abs(value - max_f) < 1e-9)
        peak_utc = local_wall_time_to_utc(
            peak_local_time,
            timezone_name=timezone_name,
            field="forecast_peak_time_local",
        )
        max_native = max_f if cfg["unit"] == "F" else (max_f - 32.0) * 5.0 / 9.0
        row = {
            "city": city,
            "target_date": target_date,
            f"{model}_forecast_model_name": FORECAST_MODELS[model],
            f"{model}_forecast_max_f": max_f,
            f"{model}_forecast_max_native": max_native,
            f"{model}_forecast_peak_hour_local": int(peak_local_time[11:13]),
            f"{model}_forecast_peak_time_local": peak_local_time,
            f"{model}_forecast_peak_hour_utc": int(peak_utc.hour),
            f"{model}_forecast_peak_time_utc": peak_utc.isoformat().replace("+00:00", "Z"),
            f"{model}_forecast_hourly_count": len(day_rows),
            f"{model}_forecast_values_hash": forecast_values_hash(day_rows),
            f"{model}_forecast_timezone": payload.get("timezone"),
            f"{model}_forecast_utc_offset_seconds": offset,
        }
        if api_source:
            row[f"{model}_forecast_api_source"] = api_source
        if run_time_utc:
            row[f"{model}_forecast_run_time_utc"] = run_time_utc
        if run_policy:
            row[f"{model}_forecast_run_policy"] = run_policy
        rows.append(row)
    return rows


def load_universe(
    kind: str,
    start_date: str | None,
    end_date: str | None,
    universe_csv: str | None = None,
) -> pd.DataFrame:
    if kind == "current_yes_replay":
        df = pd.read_csv(FEATURE_ROWS, usecols=["city", "target_date"])
    elif kind == "csv":
        if not universe_csv:
            raise ValueError("--universe-csv is required when --universe csv")
        df = pd.read_csv(universe_csv, usecols=lambda col: col in {"city", "target_date", "event_date"})
        if "target_date" not in df.columns and "event_date" in df.columns:
            df = df.rename(columns={"event_date": "target_date"})
        if "city" not in df.columns or "target_date" not in df.columns:
            raise ValueError("universe CSV must contain city and target_date/event_date columns")
    elif kind == "fact_signal_candidates":
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=1000")
        try:
            df = pd.read_sql_query(
                "SELECT DISTINCT city, event_date AS target_date FROM fact_signal_candidates",
                conn,
            )
        finally:
            conn.close()
    else:
        raise ValueError(f"unknown universe: {kind}")

    df = df.dropna().drop_duplicates()
    df["target_date"] = df["target_date"].astype(str)
    if start_date:
        df = df[df["target_date"].ge(start_date)]
    if end_date:
        df = df[df["target_date"].le(end_date)]
    return df.sort_values(["city", "target_date"]).reset_index(drop=True)


def build_dataset(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    universe = load_universe(args.universe, args.start_date, args.end_date, args.universe_csv)
    ranges = universe.groupby("city")["target_date"].agg(["min", "max", "nunique"]).reset_index()

    model_frames: dict[str, pd.DataFrame] = {}
    fetch_stats: dict[str, int] = {}
    errors: list[dict[str, Any]] = []
    with httpx.Client(timeout=60.0) as client:
        if args.api_source == "single_runs":
            records = universe[["city", "target_date"]].drop_duplicates().to_dict("records")
            run_day_offsets = [
                int(item.strip())
                for item in str(args.run_day_offsets or args.run_day_offset).split(",")
                if item.strip()
            ]
            tasks: list[dict[str, str]] = []
            for model in FORECAST_MODELS:
                for item in records:
                    city = str(item["city"])
                    if city not in FULL_CITY_CONFIGS:
                        fetch_stats["missing_city_config"] = fetch_stats.get("missing_city_config", 0) + 1
                        continue
                    tasks.append({"model": model, "city": city, "target_date": str(item["target_date"])})

            rows_by_model: dict[str, list[dict[str, Any]]] = {model: [] for model in FORECAST_MODELS}

            def handle_result(result: dict[str, Any]) -> None:
                city = str(result["city"])
                target_date = str(result["target_date"])
                model = str(result["model"])
                payload = result["payload"]
                status = str(result["status"])
                cache_path = result["cache_path"]
                run_time_utc = str(result["run_time_utc"])
                run_policy = str(result["run_policy"])
                fetch_stats[status] = fetch_stats.get(status, 0) + 1
                if payload is None or status == "single_runs_error":
                    errors.append(
                        {
                            "city": city,
                            "target_date": target_date,
                            "model": model,
                            "status": status,
                            "cache_path": cache_path,
                            "run_time_utc": run_time_utc,
                            "payload": payload,
                        }
                    )
                    return
                derived = derive_peak_rows(
                    payload,
                    city=city,
                    model=model,
                    target_dates={target_date},
                    api_source="open_meteo_single_runs",
                    run_time_utc=run_time_utc,
                    run_policy=run_policy,
                )
                if not derived:
                    fetch_stats["single_runs_no_target_day_hourly"] = (
                        fetch_stats.get("single_runs_no_target_day_hourly", 0) + 1
                    )
                    errors.append(
                        {
                            "city": city,
                            "target_date": target_date,
                            "model": model,
                            "status": "single_runs_no_target_day_hourly",
                            "cache_path": cache_path,
                            "run_time_utc": run_time_utc,
                        }
                    )
                    return
                for row in derived:
                    row[f"{model}_forecast_cache_status"] = status
                    row[f"{model}_forecast_cache_path"] = cache_path
                rows_by_model[model].extend(derived)

            if args.workers <= 1:
                for task in tasks:
                    handle_result(
                        build_single_run_task(
                            city=task["city"],
                            target_date=task["target_date"],
                            model=task["model"],
                            fetch_missing=args.fetch_missing,
                            run_day_offsets=run_day_offsets,
                            run_hour_utc=args.run_hour_utc,
                            forecast_days=args.forecast_days,
                        )
                    )
            else:
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    futures = [
                        pool.submit(
                            build_single_run_task,
                            city=task["city"],
                            target_date=task["target_date"],
                            model=task["model"],
                            fetch_missing=args.fetch_missing,
                            run_day_offsets=run_day_offsets,
                            run_hour_utc=args.run_hour_utc,
                            forecast_days=args.forecast_days,
                        )
                        for task in tasks
                    ]
                    for future in as_completed(futures):
                        try:
                            handle_result(future.result())
                        except Exception as exc:
                            fetch_stats["single_runs_task_exception"] = fetch_stats.get("single_runs_task_exception", 0) + 1
                            errors.append({"status": "single_runs_task_exception", "error": repr(exc)})

            for model, rows in rows_by_model.items():
                if rows:
                    frame = pd.DataFrame(rows).sort_values(["city", "target_date"])
                    keep = ["city", "target_date"] + [c for c in frame.columns if c.startswith(f"{model}_")]
                    model_frames[model] = frame[keep].drop_duplicates(["city", "target_date"])
                else:
                    model_frames[model] = pd.DataFrame(columns=["city", "target_date"])
        else:
            for model in FORECAST_MODELS:
                rows: list[dict[str, Any]] = []
                for item in ranges.to_dict("records"):
                    city = str(item["city"])
                    if city not in FULL_CITY_CONFIGS:
                        fetch_stats["missing_city_config"] = fetch_stats.get("missing_city_config", 0) + 1
                        continue
                    payload, status, cache_path = load_payload(
                        client,
                        city=city,
                        model=model,
                        start_date=str(item["min"]),
                        end_date=str(item["max"]),
                        fetch_missing=args.fetch_missing,
                        promote_cache=args.promote_cache,
                    )
                    fetch_stats[status] = fetch_stats.get(status, 0) + 1
                    if payload is None or status == "error":
                        errors.append(
                            {"city": city, "model": model, "status": status, "cache_path": cache_path, "payload": payload}
                        )
                        continue
                    derived = derive_peak_rows(payload, city=city, model=model, api_source="open_meteo_historical_forecast")
                    for row in derived:
                        row[f"{model}_forecast_cache_status"] = status
                        row[f"{model}_forecast_cache_path"] = cache_path
                    rows.extend(derived)
                if rows:
                    frame = pd.DataFrame(rows)
                    keep = ["city", "target_date"] + [c for c in frame.columns if c.startswith(f"{model}_")]
                    model_frames[model] = frame[keep].drop_duplicates(["city", "target_date"])
                else:
                    model_frames[model] = pd.DataFrame(columns=["city", "target_date"])

    out = universe.copy()
    for model, frame in model_frames.items():
        out = out.merge(frame, on=["city", "target_date"], how="left")

    for model in FORECAST_MODELS:
        peak_col = f"{model}_forecast_peak_hour_local"
        if peak_col not in out.columns:
            out[peak_col] = pd.NA
        out[f"{model}_forecast_peak_present"] = out[f"{model}_forecast_peak_hour_local"].notna()

    both = out[["gfs_forecast_peak_hour_local", "ecmwf_forecast_peak_hour_local"]].notna().all(axis=1)
    out["forecast_peak_models_agree_le_1h"] = False
    out.loc[both, "forecast_peak_models_agree_le_1h"] = (
        (out.loc[both, "gfs_forecast_peak_hour_local"] - out.loc[both, "ecmwf_forecast_peak_hour_local"]).abs() <= 1
    )
    out["forecast_peak_hour_spread"] = (
        pd.to_numeric(out["gfs_forecast_peak_hour_local"], errors="coerce")
        - pd.to_numeric(out["ecmwf_forecast_peak_hour_local"], errors="coerce")
    ).abs()

    summary = {
        "generated_at_utc": now_utc(),
        "script": str(Path(__file__).relative_to(ROOT)),
        "universe": args.universe,
        "universe_csv": args.universe_csv,
        "api_source": args.api_source,
        "run_day_offset": args.run_day_offset if args.api_source == "single_runs" else None,
        "run_day_offsets": args.run_day_offsets if args.api_source == "single_runs" else None,
        "run_hour_utc": args.run_hour_utc if args.api_source == "single_runs" else None,
        "forecast_days": args.forecast_days if args.api_source == "single_runs" else None,
        "workers": args.workers if args.api_source == "single_runs" else None,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "fetch_missing": bool(args.fetch_missing),
        "promote_cache": bool(args.promote_cache),
        "universe_rows": int(len(universe)),
        "universe_city_dates": int(universe.drop_duplicates(["city", "target_date"]).shape[0]),
        "universe_cities": int(universe["city"].nunique()),
        "date_min": str(universe["target_date"].min()) if len(universe) else None,
        "date_max": str(universe["target_date"].max()) if len(universe) else None,
        "output_rows": int(len(out)),
        "coverage": {
            "gfs_peak_rows": int(out["gfs_forecast_peak_present"].sum()),
            "gfs_peak_rate": float(out["gfs_forecast_peak_present"].mean()) if len(out) else None,
            "ecmwf_peak_rows": int(out["ecmwf_forecast_peak_present"].sum()),
            "ecmwf_peak_rate": float(out["ecmwf_forecast_peak_present"].mean()) if len(out) else None,
            "both_models_rows": int(both.sum()),
            "both_models_rate": float(both.mean()) if len(out) else None,
            "models_agree_le_1h_rows": int(out["forecast_peak_models_agree_le_1h"].sum()),
            "models_agree_le_1h_rate": float(out["forecast_peak_models_agree_le_1h"].mean()) if len(out) else None,
        },
        "fetch_stats": fetch_stats,
        "errors": errors[:20],
        "output_csv": display_path(Path(args.out_csv)),
        "output_json": display_path(Path(args.out_json)),
        "output_md": display_path(Path(args.out_md)),
    }
    return out, summary


def pct(value: Any) -> str:
    if value is None:
        return "NA"
    try:
        if not math.isfinite(float(value)):
            return "NA"
        return f"{100 * float(value):.1f}%"
    except Exception:
        return "NA"


def write_report(summary: dict[str, Any], out_md: Path) -> None:
    cov = summary["coverage"]
    api_line = (
        f"Open-Meteo Single Runs fixed run: D-{summary['run_day_offset']} "
        f"{int(summary['run_hour_utc']):02d}:00 UTC"
        if summary.get("api_source") == "single_runs"
        else "Open-Meteo historical forecast legacy mode"
    )
    lines = [
        "# Forecast Peak Clock Backfill Dataset v1",
        "",
        "Status: research_data_layer / not_live_ready_by_itself",
        f"Generated: {summary['generated_at_utc']}",
        f"Forecast source: {api_line}",
        "",
        "Target metric: `forecast_peak_clock_backfill_v1` = one reusable city-date table with GFS/ECMWF expected daily high time, expected high temperature, hourly-vector hash, and timezone metadata.",
        "",
        "## Human Conclusion",
        "",
        "这次补的是数据层，不是又调一个交易规则。结果是：current-YES 研究窗口里的 forecast peak clock 已经从一次性回测缓存，升级成 `runtime/weather_edge_v1/market_data/research/forecast_peak_clock_backfill_v1.csv` 这张共享表。",
        "",
        "它能解决模型层最大的口径缺口：以后判断“现在是不是接近当天预报最高温出现时间”，不必写死本地 13-15 点，也不必每个策略脚本各自去抓一次历史预报。",
        "",
        "默认口径已改成 Open-Meteo Single Runs 的固定 D-1 12:00 UTC model run；这比 stitched historical forecast 更接近 PIT，因为每个 city-date 都绑定到目标日前已经发布的完整模型 run。",
        "",
        "但它仍然是研究 backfill，不等于生产当时 snapshot 已经原生落盘；所以它能支持研究和 shadow telemetry，不能单独把策略推到 live 放大。",
        "",
        "## Coverage",
        "",
        f"- universe: `{summary['universe']}`",
        f"- city-date rows: `{summary['universe_city_dates']}` across `{summary['universe_cities']}` cities",
        f"- date range: `{summary['date_min']}` .. `{summary['date_max']}`",
        f"- GFS peak coverage: `{cov['gfs_peak_rows']}` / `{summary['output_rows']}` = `{pct(cov['gfs_peak_rate'])}`",
        f"- ECMWF peak coverage: `{cov['ecmwf_peak_rows']}` / `{summary['output_rows']}` = `{pct(cov['ecmwf_peak_rate'])}`",
        f"- both-model coverage: `{cov['both_models_rows']}` / `{summary['output_rows']}` = `{pct(cov['both_models_rate'])}`",
        f"- GFS/ECMWF peak agree <= 1h: `{cov['models_agree_le_1h_rows']}` / `{summary['output_rows']}` = `{pct(cov['models_agree_le_1h_rate'])}`",
        "",
        "## Cache / Fetch",
        "",
        f"- api_source: `{summary['api_source']}`",
        f"- run_day_offset: `{summary['run_day_offset']}`",
        f"- run_day_offsets: `{summary['run_day_offsets']}`",
        f"- run_hour_utc: `{summary['run_hour_utc']}`",
        f"- fetch_missing: `{summary['fetch_missing']}`",
        f"- promote_cache: `{summary['promote_cache']}`",
        f"- fetch_stats: `{summary['fetch_stats']}`",
        f"- errors_kept: `{len(summary['errors'])}`",
        "",
        "## Outputs",
        "",
        f"- CSV: `{summary['output_csv']}`",
        f"- JSON: `{summary['output_json']}`",
        f"- Script: `{summary['script']}`",
        "",
        "## Trading Meaning",
        "",
        "- current-YES: 可以把 `decision_hour_local - forecast_peak_hour_local` 当作模型特征，而不是硬写本地时间。",
        "- higher-NO carry: 可以检查 NO carry 是否只在“预报峰值已过且预报最高温没有越过下一档”时成立。",
        "- YES reversal: 可以把低价 YES 的反转条件改成“预报峰值尚未到/模型分歧大/forecast max 高于 running max”。",
        "",
        "三道门：significance=NA, baseline=NA, forward=NA, conclusion=`research_data_layer`。这张表只是补数据口径，不直接给 live 动作。",
    ]
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", choices=["current_yes_replay", "fact_signal_candidates", "csv"], default="current_yes_replay")
    parser.add_argument("--universe-csv")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--fetch-missing", action="store_true")
    parser.add_argument("--promote-cache", action="store_true")
    parser.add_argument("--api-source", choices=["single_runs", "historical_forecast"], default="single_runs")
    parser.add_argument("--run-day-offset", type=int, default=1)
    parser.add_argument("--run-day-offsets", default="1,2,3")
    parser.add_argument("--run-hour-utc", type=int, default=12)
    parser.add_argument("--forecast-days", type=int, default=3)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out-csv", default=str(OUT_CSV))
    parser.add_argument("--out-json", default=str(OUT_JSON))
    parser.add_argument("--out-md", default=str(OUT_MD))
    args = parser.parse_args()

    out, summary = build_dataset(args)
    out_csv = Path(args.out_csv)
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_csv, index=False)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(summary, out_md)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
