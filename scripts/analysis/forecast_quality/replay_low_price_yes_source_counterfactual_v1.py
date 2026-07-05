#!/usr/bin/env python3
"""Replay HeadA low-price YES source counterfactuals.

This script answers the narrow incident question:

  If assigned ECMWF/GFS source policy had been applied in the probability layer,
  how would the low-price YES candidate set and settled hit rate have changed?

It intentionally does not claim to reconstruct missing PIT ECMWF forecast curves.
For rows produced during the outage with GFS forecast_max_f for an ECMWF-assigned
city, the counterfactual recomputes model_p_yes with the assigned ECMWF empirical
error distribution while keeping the stored forecast_max_f fixed. The report
labels this as `assigned_error_dist_only`.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT.parent / "weather_data_feed_service"
SERVICE_LEGACY = SERVICE_ROOT / "weather_data_feed_service" / "legacy_weather_predict"
if str(SERVICE_LEGACY) not in sys.path:
    sys.path.insert(0, str(SERVICE_LEGACY))

try:
    from paper_snapshot import CITIES, CITY_MODEL  # type: ignore
except Exception as exc:  # pragma: no cover - explicit failure is better here.
    raise RuntimeError(f"failed to import CITY_MODEL from {SERVICE_LEGACY}: {exc}") from exc


DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_CACHE = ROOT.parent / "weather_data_feed_service_runtime" / "cache"
DEFAULT_OUT = ROOT / "docs/analysis/2026-07/generated/low_price_yes_source_counterfactual_v1"


@dataclass(frozen=True)
class ErrorKey:
    city: str
    icao: str
    model: str


def safe_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def source_model(forecast_source: str) -> str:
    text = (forecast_source or "").lower()
    if "ecmwf" in text:
        return "ecmwf"
    if "gfs" in text:
        return "gfs"
    return ""


def city_aliases(city: str) -> list[str]:
    aliases = [city]
    if city == "LA":
        aliases.append("LosAngeles")
    return aliases


def load_model_daily_max(cache_dir: Path, city: str, model: str) -> dict[str, float]:
    for alias in city_aliases(city):
        files = sorted(cache_dir.glob(f"{model}_v4_{alias}_*.json"))
        if not files:
            continue
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        hourly = payload.get("hourly", {})
        times = hourly.get("time", []) or []
        temps = hourly.get("temperature_2m", []) or []
        daily: dict[str, float] = {}
        for ts, temp in zip(times, temps):
            val = safe_float(temp)
            if not math.isfinite(val):
                continue
            day = str(ts)[:10]
            daily[day] = max(daily.get(day, -9999.0), val)
        return daily
    return {}


def load_wu_daily_max(cache_dir: Path, icao: str) -> dict[str, float]:
    path = cache_dir / "wu_obs" / f"wu_obs_{icao}.csv"
    if not path.exists():
        return {}
    daily: dict[str, float] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            day = (row.get("date_local") or "").strip()
            temp = safe_float(row.get("temp"))
            if not day or not math.isfinite(temp):
                continue
            daily[day] = max(daily.get(day, -9999.0), temp)
    return daily


def load_errors(cache_dir: Path, key: ErrorKey) -> list[float]:
    model_daily = load_model_daily_max(cache_dir, key.city, key.model)
    wu_daily = load_wu_daily_max(cache_dir, key.icao)
    errors = [
        wu_daily[day] - model_daily[day]
        for day in sorted(model_daily)
        if day in wu_daily
    ]
    return errors if len(errors) >= 30 else []


def bracket_probability(*, forecast_max_f: float, errors_f: list[float], bracket: str, unit: str) -> float:
    if not errors_f or not math.isfinite(forecast_max_f):
        return math.nan
    unit = (unit or "F").upper()
    if unit == "C":
        base = (forecast_max_f - 32.0) * 5.0 / 9.0
        errors = [err * 5.0 / 9.0 for err in errors_f]
    else:
        base = forecast_max_f
        errors = errors_f
    simulated = [int(math.floor(base + err + 0.5)) for err in errors]
    label = str(bracket).strip()
    if label.endswith("+"):
        threshold = int(label[:-1])
        return sum(1 for x in simulated if x >= threshold) / len(simulated)
    if label.endswith("-"):
        threshold = int(label[:-1])
        return sum(1 for x in simulated if x <= threshold) / len(simulated)
    if "-" in label and not label.startswith("-"):
        lo_s, hi_s = label.split("-", 1)
        lo, hi = int(lo_s), int(hi_s)
        return sum(1 for x in simulated if lo <= x <= hi) / len(simulated)
    val = int(label)
    return sum(1 for x in simulated if x == val) / len(simulated)


def native_from_f(forecast_max_f: float, unit: str) -> float:
    if not math.isfinite(forecast_max_f):
        return math.nan
    return (forecast_max_f - 32.0) * 5.0 / 9.0 if (unit or "F").upper() == "C" else forecast_max_f


def bracket_low_native(bracket: str) -> float:
    label = str(bracket).strip()
    m = re.match(r"^(-?\d+)", label)
    return float(m.group(1)) if m else math.nan


def is_settled(row: dict[str, Any]) -> bool:
    return str(row.get("settlement_status") or "").lower() == "settled" and row.get("final_yes") is not None


def summarize(rows: list[dict[str, Any]], *, price_field: str = "decision_entry_price") -> dict[str, Any]:
    settled = [r for r in rows if is_settled(r)]
    wins = [r for r in settled if safe_float(r.get("final_yes"), 0.0) >= 0.5]
    cost = sum(safe_float(r.get(price_field), 0.0) for r in settled)
    pnl = sum(safe_float(r.get("final_yes"), 0.0) - safe_float(r.get(price_field), 0.0) for r in settled)
    return {
        "rows": len(rows),
        "dates": len({r.get("event_date") or r.get("target_date") for r in rows}),
        "cities": len({r.get("city") for r in rows}),
        "settled": len(settled),
        "wins": len(wins),
        "win_rate_settled": None if not settled else len(wins) / len(settled),
        "avg_ask": None if not rows else mean(safe_float(r.get(price_field), 0.0) for r in rows),
        "pnl_1share": pnl,
        "roi_1share": None if cost <= 0 else pnl / cost,
    }


def summarize_fills(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [r for r in rows if str(r.get("settlement_status") or "").lower() == "settled"]
    wins = [r for r in settled if safe_float(r.get("final_yes"), 0.0) >= 0.5]
    cost = sum(safe_float(r.get("cost_usd"), 0.0) for r in settled)
    pnl = sum(safe_float(r.get("pnl_usd_at_fill"), 0.0) for r in settled)
    return {
        "fills": len(rows),
        "settled": len(settled),
        "wins": len(wins),
        "win_rate_settled": None if not settled else len(wins) / len(settled),
        "cost_usd_settled": cost,
        "pnl_usd_settled": pnl,
        "roi_settled_cost": None if cost <= 0 else pnl / cost,
    }


def row_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            str(row.get("event_date") or row.get("target_date") or ""),
            str(row.get("city") or ""),
            str(row.get("bracket") or ""),
            str(row.get("condition_id") or ""),
            str(row.get("candidate_id") or ""),
        ]
    )


def fetch_current_api_forecast_max_f(
    *,
    cache: dict[str, Any],
    cache_path: Path,
    model: str,
    city: str,
    target_date: str,
    cfg: dict[str, Any],
) -> float:
    key = f"{model}|{city}|{target_date}"
    if key in cache:
        return safe_float(cache[key])
    url = f"https://api.open-meteo.com/v1/{model}"
    params = {
        "latitude": cfg["lat"],
        "longitude": cfg["lon"],
        "hourly": "temperature_2m",
        "temperature_unit": "fahrenheit",
        "timezone": "auto",
        "start_date": target_date,
        "end_date": target_date,
    }
    value = math.nan
    try:
        response = httpx.get(url, params=params, timeout=10.0)
        if response.status_code == 200:
            payload = response.json()
            temps = payload.get("hourly", {}).get("temperature_2m", []) or []
            parsed = [safe_float(x) for x in temps]
            parsed = [x for x in parsed if math.isfinite(x)]
            if parsed:
                value = max(parsed)
    except Exception:
        value = math.nan
    cache[key] = value if math.isfinite(value) else None
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return value


def select_one_per_city_date(rows: list[dict[str, Any]], *, edge_field: str, require_hot_dist: bool) -> list[dict[str, Any]]:
    eligible: list[dict[str, Any]] = []
    for row in rows:
        ask = safe_float(row.get("decision_entry_price"))
        edge = safe_float(row.get(edge_field))
        if str(row.get("side") or "") != "BUY_YES":
            continue
        if not (0.05 <= ask <= 0.20 and edge >= 0.20):
            continue
        if require_hot_dist:
            dist = safe_float(row.get("dist_native"))
            if not (math.isfinite(dist) and dist > 0.0):
                continue
        eligible.append(row)

    def sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            str(row.get("decision_snapshot_ts_utc") or ""),
            safe_float(row.get("decision_entry_price"), 999.0),
            -safe_float(row.get(edge_field), -999.0),
            str(row.get("bracket") or ""),
            str(row.get("candidate_id") or ""),
        )

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in sorted(eligible, key=lambda r: (str(r.get("event_date") or ""), str(r.get("city") or ""), *sort_key(r))):
        key = (str(row.get("event_date") or ""), str(row.get("city") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def by_date(rows: list[dict[str, Any]], *, price_field: str = "decision_entry_price") -> list[dict[str, Any]]:
    out = []
    for day in sorted({str(r.get("event_date") or r.get("target_date") or "") for r in rows}):
        day_rows = [r for r in rows if str(r.get("event_date") or r.get("target_date") or "") == day]
        out.append({"date": day, **summarize(day_rows, price_field=price_field)})
    return out


def load_fact_rows(conn: sqlite3.Connection, start: str, end: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM fact_signal_candidates
        WHERE event_date BETWEEN :start AND :end
        ORDER BY event_date, city, decision_snapshot_ts_utc, bracket, candidate_id
        """,
        {"start": start, "end": end},
    ).fetchall()
    return [dict(r) for r in rows]


def load_live_fills(conn: sqlite3.Connection, start: str, end: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM fact_trades
        WHERE target_date BETWEEN :start AND :end
          AND trade_class = 'live_real'
          AND run_id LIKE '%low_price_yes_lottery%'
        ORDER BY target_date, fill_ts_utc, city, bracket, fill_id
        """,
        {"start": start, "end": end},
    ).fetchall()
    return [dict(r) for r in rows]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--start", default="2026-07-02")
    parser.add_argument("--end", default="2026-07-05")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--include-current-api-forecast-approx",
        action="store_true",
        help="Also recompute ECMWF-assigned rows with current Open-Meteo ECMWF max. This is not PIT.",
    )
    args = parser.parse_args()

    conn = connect(args.db)
    fact_rows = load_fact_rows(conn, args.start, args.end)
    live_fills = load_live_fills(conn, args.start, args.end)

    error_cache: dict[ErrorKey, list[float]] = {}
    enriched: list[dict[str, Any]] = []
    api_cache_path = args.out_dir / "current_api_forecast_cache.json"
    api_cache: dict[str, Any] = {}
    if api_cache_path.exists():
        try:
            api_cache = json.loads(api_cache_path.read_text(encoding="utf-8"))
        except Exception:
            api_cache = {}
    city_configs: dict[str, dict[str, Any]] = CITIES if args.include_current_api_forecast_approx else {}
    for row in fact_rows:
        city = str(row.get("city") or "")
        assigned = CITY_MODEL.get(city, "gfs")
        observed = source_model(str(row.get("forecast_source") or ""))
        key = ErrorKey(city=city, icao=str(row.get("icao") or ""), model=assigned)
        if key not in error_cache:
            error_cache[key] = load_errors(args.cache_dir, key)
        p_cf = bracket_probability(
            forecast_max_f=safe_float(row.get("forecast_max_f")),
            errors_f=error_cache[key],
            bracket=str(row.get("bracket") or ""),
            unit=str(row.get("unit") or "F"),
        )
        ask = safe_float(row.get("decision_entry_price"))
        lo = bracket_low_native(str(row.get("bracket") or ""))
        forecast_native = safe_float(row.get("forecast_max_native"))
        out = {
            **row,
            "assigned_model": assigned,
            "observed_model": observed,
            "source_aligned": bool(observed and observed == assigned),
            "cf_scope": "assigned_error_dist_only",
            "cf_model_p_yes": p_cf,
            "cf_edge": p_cf - ask if math.isfinite(p_cf) and math.isfinite(ask) else math.nan,
            "dist_native": lo - forecast_native if math.isfinite(lo) and math.isfinite(forecast_native) else math.nan,
            "error_sample_n": len(error_cache[key]),
        }
        if args.include_current_api_forecast_approx:
            approx_max_f = safe_float(row.get("forecast_max_f"))
            approx_scope = "stored_forecast_max_f"
            cfg = city_configs.get(city)
            if assigned == "ecmwf" and isinstance(cfg, dict):
                fetched = fetch_current_api_forecast_max_f(
                    cache=api_cache,
                    cache_path=api_cache_path,
                    model="ecmwf",
                    city=city,
                    target_date=str(row.get("event_date") or ""),
                    cfg=cfg,
                )
                if math.isfinite(fetched):
                    approx_max_f = fetched
                    approx_scope = "current_api_ecmwf_not_pit"
            approx_p = bracket_probability(
                forecast_max_f=approx_max_f,
                errors_f=error_cache[key],
                bracket=str(row.get("bracket") or ""),
                unit=str(row.get("unit") or "F"),
            )
            approx_native = native_from_f(approx_max_f, str(row.get("unit") or "F"))
            out.update(
                {
                    "api_approx_scope": approx_scope,
                    "api_approx_forecast_max_f": approx_max_f,
                    "api_approx_model_p_yes": approx_p,
                    "api_approx_edge": approx_p - ask if math.isfinite(approx_p) and math.isfinite(ask) else math.nan,
                    "api_approx_dist_native": lo - approx_native if math.isfinite(lo) and math.isfinite(approx_native) else math.nan,
                }
            )
        enriched.append(out)

    for row in live_fills:
        city = str(row.get("city") or "")
        row["assigned_model"] = CITY_MODEL.get(city, "gfs")
        row["observed_model"] = source_model(str(row.get("forecast_source") or ""))
        row["source_aligned"] = bool(row["observed_model"] and row["observed_model"] == row["assigned_model"])

    observed_then = select_one_per_city_date(enriched, edge_field="edge", require_hot_dist=False)
    cf_then = select_one_per_city_date(enriched, edge_field="cf_edge", require_hot_dist=False)
    observed_hot = select_one_per_city_date(enriched, edge_field="edge", require_hot_dist=True)
    cf_hot = select_one_per_city_date(enriched, edge_field="cf_edge", require_hot_dist=True)
    api_approx_then: list[dict[str, Any]] = []
    api_approx_hot: list[dict[str, Any]] = []
    if args.include_current_api_forecast_approx:
        api_approx_rows = []
        for row in enriched:
            copied = dict(row)
            copied["dist_native"] = copied.get("api_approx_dist_native")
            api_approx_rows.append(copied)
        api_approx_then = select_one_per_city_date(api_approx_rows, edge_field="api_approx_edge", require_hot_dist=False)
        api_approx_hot = select_one_per_city_date(api_approx_rows, edge_field="api_approx_edge", require_hot_dist=True)

    def set_delta(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> dict[str, Any]:
        ak = {row_key(r): r for r in a}
        bk = {row_key(r): r for r in b}
        dropped = [ak[k] for k in sorted(set(ak) - set(bk))]
        added = [bk[k] for k in sorted(set(bk) - set(ak))]
        overlap = [bk[k] for k in sorted(set(ak) & set(bk))]
        return {
            "dropped_count": len(dropped),
            "added_count": len(added),
            "overlap_count": len(overlap),
            "dropped_summary": summarize(dropped),
            "added_summary": summarize(added),
            "overlap_summary": summarize(overlap),
        }

    summary = {
        "meta": {
            "db": str(args.db),
            "cache_dir": str(args.cache_dir),
            "start": args.start,
            "end": args.end,
            "counterfactual_scope": "assigned_error_dist_only; stored forecast_max_f is not changed",
            "current_api_forecast_approx_enabled": bool(args.include_current_api_forecast_approx),
            "current_api_forecast_approx_warning": "not point-in-time; use only to size likely forecast_max effect",
            "pit_ecmwf_forecast_curve_reconstructed": False,
        },
        "source_coverage_fact_rows": {},
        "live_fills": {
            "all": summarize_fills(live_fills),
            "aligned": summarize_fills([r for r in live_fills if r.get("source_aligned")]),
            "mismatch": summarize_fills([r for r in live_fills if not r.get("source_aligned")]),
        },
        "selector_then_no_dist_filter": {
            "observed_dirty": summarize(observed_then),
            "source_corrected_partial": summarize(cf_then),
            "delta_observed_to_source_corrected": set_delta(observed_then, cf_then),
            "by_date_observed_dirty": by_date(observed_then),
            "by_date_source_corrected_partial": by_date(cf_then),
        },
        "selector_current_hot_dist_gt0": {
            "observed_dirty": summarize(observed_hot),
            "source_corrected_partial": summarize(cf_hot),
            "delta_observed_to_source_corrected": set_delta(observed_hot, cf_hot),
            "by_date_observed_dirty": by_date(observed_hot),
            "by_date_source_corrected_partial": by_date(cf_hot),
        },
    }

    if args.include_current_api_forecast_approx:
        summary["selector_current_api_ecmwf_forecast_approx_no_dist_filter"] = {
            "observed_dirty": summarize(observed_then),
            "api_forecast_approx": summarize(api_approx_then),
            "delta_observed_to_api_forecast_approx": set_delta(observed_then, api_approx_then),
            "by_date_api_forecast_approx": by_date(api_approx_then),
        }
        summary["selector_current_api_ecmwf_forecast_approx_hot_dist_gt0"] = {
            "observed_dirty": summarize(observed_hot),
            "api_forecast_approx": summarize(api_approx_hot),
            "delta_observed_to_api_forecast_approx": set_delta(observed_hot, api_approx_hot),
            "by_date_api_forecast_approx": by_date(api_approx_hot),
        }

    cov: dict[str, dict[str, int]] = {}
    for row in enriched:
        day = str(row.get("event_date") or "")
        key = f"{row.get('observed_model') or 'unknown'}->{row.get('assigned_model')}"
        cov.setdefault(day, {})
        cov[day][key] = cov[day].get(key, 0) + 1
    summary["source_coverage_fact_rows"] = cov

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.out_dir / "summary.json", summary)
    write_csv(args.out_dir / "selector_observed_then.csv", observed_then)
    write_csv(args.out_dir / "selector_source_corrected_partial_then.csv", cf_then)
    write_csv(args.out_dir / "selector_observed_hot_dist_gt0.csv", observed_hot)
    write_csv(args.out_dir / "selector_source_corrected_partial_hot_dist_gt0.csv", cf_hot)
    if args.include_current_api_forecast_approx:
        write_csv(args.out_dir / "selector_api_forecast_approx_then.csv", api_approx_then)
        write_csv(args.out_dir / "selector_api_forecast_approx_hot_dist_gt0.csv", api_approx_hot)
    write_csv(args.out_dir / "live_fills.csv", live_fills)

    print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
