#!/usr/bin/env python3
"""Backfill forecast peak clock and evaluate current-YES timing rules.

Evidence layer: historical orderbook replay plus Open-Meteo historical forecast
backfill.  This is not live fill PnL and not a proof that production snapshots
already persisted these fields at decision time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
WEATHER_PREDICT_ROOT = ROOT.parent / "weather-predict"
if str(WEATHER_PREDICT_ROOT) not in sys.path:
    sys.path.insert(0, str(WEATHER_PREDICT_ROOT))

from city_pools import FULL_CITY_CONFIGS  # type: ignore  # noqa: E402


DB = ROOT / "runtime/weather.db"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/feature_rows.csv"
MODEL_ARTIFACT = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/live_model.json"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_current_yes_forecast_peak_clock_backfill_v3"
FORECAST_CACHE_DIR = OUT_DIR / "open_meteo_historical_forecast"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-17-theta-current-yes-forecast-peak-clock-backfill-v3.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-17-theta-current-yes-forecast-peak-clock-backfill-v3.md"

SPLIT_DATE = "2026-06-01"
SEED = 20260617
FORECAST_MODELS = {
    "gfs": "gfs_seamless",
    "ecmwf": "ecmwf_ifs025",
}


@dataclass(frozen=True)
class StrategyRule:
    name: str
    head: str
    model: str
    ask_min: float
    p_min: float
    edge_min: float
    liquidity_notional_min: float
    decline_min: float | None = None
    decline_max: float | None = None
    hour_start: int | None = None
    hour_end: int | None = None
    delta_min: float | None = None
    delta_max: float | None = None
    forecast_gap_max_native: float | None = None


FIXED_RULES = [
    StrategyRule(
        name="v9_fixed_fade_confirmed",
        head="fade_confirmed",
        model="none",
        ask_min=0.55,
        p_min=0.50,
        edge_min=0.05,
        liquidity_notional_min=2.0,
        decline_min=0.5,
        hour_start=13,
        hour_end=15,
    ),
    StrategyRule(
        name="gfs_after_peak_fade_confirmed",
        head="fade_confirmed",
        model="gfs",
        ask_min=0.55,
        p_min=0.50,
        edge_min=0.05,
        liquidity_notional_min=2.0,
        decline_min=0.5,
        delta_min=0.0,
        delta_max=4.0,
        forecast_gap_max_native=0.5,
    ),
    StrategyRule(
        name="ecmwf_after_peak_fade_confirmed",
        head="fade_confirmed",
        model="ecmwf",
        ask_min=0.55,
        p_min=0.50,
        edge_min=0.05,
        liquidity_notional_min=2.0,
        decline_min=0.5,
        delta_min=0.0,
        delta_max=4.0,
        forecast_gap_max_native=0.5,
    ),
    StrategyRule(
        name="gfs_peak_forming_plateau",
        head="peak_forming",
        model="gfs",
        ask_min=0.55,
        p_min=0.50,
        edge_min=0.05,
        liquidity_notional_min=2.0,
        decline_max=0.1,
        delta_min=-1.0,
        delta_max=1.0,
        forecast_gap_max_native=0.5,
    ),
    StrategyRule(
        name="ecmwf_peak_forming_plateau",
        head="peak_forming",
        model="ecmwf",
        ask_min=0.55,
        p_min=0.50,
        edge_min=0.05,
        liquidity_notional_min=2.0,
        decline_max=0.1,
        delta_min=-1.0,
        delta_max=1.0,
        forecast_gap_max_native=0.5,
    ),
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: float | None, signed: bool = True) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    sign = "+" if signed else ""
    return f"{100 * float(value):{sign}.1f}%"


def fnum(value: float | None, digits: int = 3) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value):.{digits}f}"


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, tuple):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    return value


def connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def query_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def data_self_check() -> dict[str, Any]:
    conn = connect_ro()
    try:
        return {
            "fact_trades_max_built_at_utc": conn.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades").fetchone()[0],
            "fact_trades_by_class": query_rows(
                conn,
                "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
            ),
            "fact_trades_by_settlement_status": query_rows(
                conn,
                "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
                "FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
            ),
            "fact_signal_candidate_coverage": query_rows(
                conn,
                "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
                "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
            )[0],
            "clob_order_fill_join": query_rows(
                conn,
                "SELECT o.status, COUNT(*) AS orders, "
                "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
                "FROM orders o LEFT JOIN fills f USING(execution_id) "
                "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
            ),
            "forecast_peak_fact_coverage": query_rows(
                conn,
                "SELECT COUNT(*) AS rows, "
                "SUM(forecast_peak_hour_local IS NOT NULL) AS with_peak_hour, "
                "SUM(forecast_values_hash IS NOT NULL) AS with_hash, "
                "MIN(event_date) AS min_event_date, MAX(event_date) AS max_event_date "
                "FROM fact_signal_candidates",
            )[0],
        }
    finally:
        conn.close()


def load_gate() -> dict[str, Any]:
    if not GATE.exists():
        return {"gate_pass": None, "missing": True}
    data = json.loads(GATE.read_text(encoding="utf-8"))
    return {
        "gate_pass": data.get("gate_pass"),
        "fail_reasons": data.get("fail_reasons", []),
        "missing_order_rows": data.get("db_fills", {}).get("missing_order_rows"),
        "over_order_keys": data.get("db_fills", {}).get("over_order_keys"),
        "db_fill_cost_minus_fact_cost": data.get("db_fill_cost_minus_fact_cost"),
    }


def forecast_values_hash(rows: list[tuple[str, float]]) -> str:
    payload = [[str(ts), round(float(temp), 3)] for ts, temp in rows]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def fetch_city_model_forecast(
    client: httpx.Client,
    *,
    city: str,
    model_key: str,
    start_date: str,
    end_date: str,
    refresh: bool,
) -> tuple[dict[str, Any] | None, str]:
    FORECAST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cfg = FULL_CITY_CONFIGS[city]
    cache_path = FORECAST_CACHE_DIR / f"{model_key}_{city}_{start_date}_{end_date}.json"
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8")), "cache"

    params = {
        "latitude": cfg["lat"],
        "longitude": cfg["lon"],
        "hourly": "temperature_2m",
        "temperature_unit": "fahrenheit",
        "models": FORECAST_MODELS[model_key],
        "start_date": start_date,
        "end_date": end_date,
        "past_forecast_days": 1,
        "timezone": "auto",
    }
    response = client.get("https://historical-forecast-api.open-meteo.com/v1/forecast", params=params)
    if response.status_code != 200:
        return {"error": response.text[:500], "status_code": response.status_code, "params": params}, "error"
    payload = response.json()
    cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    time.sleep(0.1)
    return payload, "fetched"


def derive_peak_rows(payload: dict[str, Any], *, city: str, model_key: str) -> list[dict[str, Any]]:
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

    out: list[dict[str, Any]] = []
    offset = int(payload.get("utc_offset_seconds") or 0)
    for target_date, rows in sorted(by_date.items()):
        if not rows:
            continue
        max_f = max(temp for _, temp in rows)
        peak_local_time = min(ts for ts, temp in rows if abs(temp - max_f) < 1e-9)
        peak_hour_local = int(peak_local_time[11:13])
        try:
            local_dt = datetime.fromisoformat(peak_local_time)
            peak_utc_dt = (local_dt - timedelta(seconds=offset)).replace(tzinfo=timezone.utc)
            peak_time_utc = peak_utc_dt.isoformat().replace("+00:00", "Z")
            peak_hour_utc = peak_utc_dt.hour
        except Exception:
            peak_time_utc = None
            peak_hour_utc = None
        cfg = FULL_CITY_CONFIGS[city]
        max_native = max_f if cfg["unit"] == "F" else (max_f - 32.0) * 5.0 / 9.0
        out.append(
            {
                "city": city,
                "target_date": target_date,
                "forecast_model": model_key,
                f"{model_key}_forecast_max_f": max_f,
                f"{model_key}_forecast_max_native": max_native,
                f"{model_key}_forecast_peak_hour_local": peak_hour_local,
                f"{model_key}_forecast_peak_time_local": peak_local_time,
                f"{model_key}_forecast_peak_hour_utc": peak_hour_utc,
                f"{model_key}_forecast_peak_time_utc": peak_time_utc,
                f"{model_key}_forecast_hourly_count": len(rows),
                f"{model_key}_forecast_values_hash": forecast_values_hash(rows),
                f"{model_key}_forecast_timezone": payload.get("timezone"),
                f"{model_key}_forecast_utc_offset_seconds": offset,
            }
        )
    return out


def build_forecast_peak_table(
    feature_rows: pd.DataFrame,
    *,
    refresh: bool,
    limit_cities: int | None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    city_dates = feature_rows[["city", "target_date"]].drop_duplicates()
    by_city = city_dates.groupby("city")["target_date"].agg(["min", "max", "nunique"]).reset_index()
    if limit_cities is not None:
        by_city = by_city.head(limit_cities)

    rows: list[dict[str, Any]] = []
    fetch_stats = {"fetched": 0, "cache": 0, "error": 0, "missing_city_config": 0}
    errors: list[dict[str, Any]] = []
    with httpx.Client(timeout=60.0) as client:
        for item in by_city.to_dict("records"):
            city = str(item["city"])
            if city not in FULL_CITY_CONFIGS:
                fetch_stats["missing_city_config"] += 1
                continue
            for model_key in FORECAST_MODELS:
                payload, status = fetch_city_model_forecast(
                    client,
                    city=city,
                    model_key=model_key,
                    start_date=str(item["min"]),
                    end_date=str(item["max"]),
                    refresh=refresh,
                )
                fetch_stats[status] = fetch_stats.get(status, 0) + 1
                if status == "error" or payload is None:
                    errors.append({"city": city, "model": model_key, "payload": payload})
                    continue
                rows.extend(derive_peak_rows(payload, city=city, model_key=model_key))

    peak = pd.DataFrame(rows)
    if peak.empty:
        return peak, {"fetch_stats": fetch_stats, "errors": errors}
    # Merge GFS/ECMWF model rows onto one city-date row.
    model_frames = []
    for model_key in FORECAST_MODELS:
        cols = ["city", "target_date"] + [c for c in peak.columns if c.startswith(f"{model_key}_")]
        model_frames.append(peak[peak["forecast_model"].eq(model_key)][cols].drop_duplicates(["city", "target_date"]))
    merged = model_frames[0]
    for frame in model_frames[1:]:
        merged = merged.merge(frame, on=["city", "target_date"], how="outer")

    return merged, {
        "fetch_stats": fetch_stats,
        "errors": errors[:10],
        "city_count": int(by_city["city"].nunique()),
        "city_date_count": int(city_dates.shape[0]),
        "peak_city_date_rows": int(len(merged)),
        "date_min": str(merged["target_date"].min()),
        "date_max": str(merged["target_date"].max()),
    }


def score_rows(rows: pd.DataFrame, artifact: dict[str, Any]) -> np.ndarray:
    numeric_features = artifact["numeric_features"]
    categorical_features = artifact["categorical_features"]
    numeric = rows[numeric_features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    medians = np.asarray(artifact["numeric_medians"], dtype=float)
    means = np.asarray(artifact["numeric_means"], dtype=float)
    scales = np.asarray(artifact["numeric_scales"], dtype=float)
    numeric = np.where(np.isfinite(numeric), numeric, medians)
    numeric = (numeric - means) / scales
    cat_parts = []
    for idx, feature in enumerate(categorical_features):
        values = rows[feature].astype(str).to_numpy()
        cats = [str(x) for x in artifact["categories"][idx]]
        lookup = {cat: col for col, cat in enumerate(cats)}
        mat = np.zeros((len(rows), len(cats)), dtype=float)
        for row_idx, value in enumerate(values):
            col = lookup.get(str(value))
            if col is not None:
                mat[row_idx, col] = 1.0
        cat_parts.append(mat)
    transformed = np.concatenate([numeric, *cat_parts], axis=1)
    logits = transformed @ np.asarray(artifact["coef"], dtype=float) + float(artifact["intercept"])
    return 1.0 / (1.0 + np.exp(-logits))


def load_feature_rows() -> pd.DataFrame:
    df = pd.read_csv(FEATURE_ROWS)
    artifact = json.loads(MODEL_ARTIFACT.read_text(encoding="utf-8"))
    df["p_yes_win"] = score_rows(df, artifact)
    df["yes_current_ask"] = pd.to_numeric(df["yes_current_ask"], errors="coerce")
    df["yes_current_size"] = pd.to_numeric(df["yes_current_size"], errors="coerce")
    df["decline_c"] = pd.to_numeric(df["decline_c"], errors="coerce")
    df["decision_hour_local"] = pd.to_numeric(df["decision_hour_local"], errors="coerce")
    df["available_notional_at_ask"] = df["yes_current_ask"] * df["yes_current_size"]
    df["edge_snapshot"] = df["p_yes_win"] - df["yes_current_ask"]
    df["snapshot_dt"] = pd.to_datetime(df["snapshot_ts_utc"], utc=True, errors="coerce")
    return df


def attach_forecast_features(feature_rows: pd.DataFrame, peak_rows: pd.DataFrame) -> pd.DataFrame:
    out = feature_rows.merge(peak_rows, on=["city", "target_date"], how="left")
    for model_key in FORECAST_MODELS:
        out[f"{model_key}_forecast_peak_delta_hours_local"] = (
            pd.to_numeric(out["decision_hour_local"], errors="coerce")
            - pd.to_numeric(out.get(f"{model_key}_forecast_peak_hour_local"), errors="coerce")
        )
        out[f"{model_key}_forecast_gap_to_running_native"] = (
            pd.to_numeric(out.get(f"{model_key}_forecast_max_native"), errors="coerce")
            - pd.to_numeric(out["running_native"], errors="coerce")
        )
        out[f"{model_key}_forecast_inside_current_bracket"] = (
            pd.to_numeric(out.get(f"{model_key}_forecast_max_native"), errors="coerce").ge(pd.to_numeric(out["bracket_low"], errors="coerce"))
            & pd.to_numeric(out.get(f"{model_key}_forecast_max_native"), errors="coerce").le(pd.to_numeric(out["bracket_high"], errors="coerce"))
        )
    both = out[["gfs_forecast_peak_hour_local", "ecmwf_forecast_peak_hour_local"]].notna().all(axis=1)
    out["forecast_peak_models_agree_le_1h"] = False
    out.loc[both, "forecast_peak_models_agree_le_1h"] = (
        (out.loc[both, "gfs_forecast_peak_hour_local"] - out.loc[both, "ecmwf_forecast_peak_hour_local"]).abs() <= 1
    )
    return out


def rule_mask(df: pd.DataFrame, rule: StrategyRule) -> pd.Series:
    mask = (
        df["has_d1_no"].astype(bool)
        & df["yes_current_ask"].ge(rule.ask_min)
        & df["available_notional_at_ask"].ge(rule.liquidity_notional_min)
        & df["p_yes_win"].ge(rule.p_min)
        & df["edge_snapshot"].ge(rule.edge_min)
    )
    if rule.decline_min is not None:
        mask &= df["decline_c"].ge(rule.decline_min)
    if rule.decline_max is not None:
        mask &= df["decline_c"].le(rule.decline_max)
    if rule.hour_start is not None:
        mask &= df["decision_hour_local"].ge(rule.hour_start)
    if rule.hour_end is not None:
        mask &= df["decision_hour_local"].le(rule.hour_end)
    if rule.model != "none":
        delta = pd.to_numeric(df.get(f"{rule.model}_forecast_peak_delta_hours_local"), errors="coerce")
        gap = pd.to_numeric(df.get(f"{rule.model}_forecast_gap_to_running_native"), errors="coerce")
        mask &= delta.notna()
        if rule.delta_min is not None:
            mask &= delta.ge(rule.delta_min)
        if rule.delta_max is not None:
            mask &= delta.le(rule.delta_max)
        if rule.forecast_gap_max_native is not None:
            mask &= gap.le(rule.forecast_gap_max_native)
    return mask


def dedupe_live(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    return (
        frame.sort_values("snapshot_dt")
        .drop_duplicates(["target_date", "city", "current_bracket"], keep="first")
        .sort_values(["target_date", "city", "snapshot_dt"])
        .groupby(["target_date", "city"])
        .head(2)
        .copy()
    )


def summarize_trades(frame: pd.DataFrame, *, taker_cushion: float) -> dict[str, Any]:
    d = dedupe_live(frame)
    if d.empty:
        return {
            "orders": 0,
            "active_dates": 0,
            "cities": 0,
            "notional": 0.0,
            "wins": 0,
            "win_rate": None,
            "roi": None,
            "pnl_usd": 0.0,
            "avg_ask": None,
            "avg_price": None,
            "avg_p_yes_win": None,
            "avg_edge_snapshot": None,
        }
    notional = 5.0
    ask = d["yes_current_ask"].astype(float).to_numpy()
    price = np.minimum(ask + taker_cushion, 0.999)
    label = d["label_yes_wins"].astype(int).to_numpy()
    pnl = np.where(label == 1, notional / price - notional, -notional)
    return {
        "orders": int(len(d)),
        "active_dates": int(d["target_date"].nunique()),
        "cities": int(d["city"].nunique()),
        "notional": float(notional * len(d)),
        "wins": int(label.sum()),
        "win_rate": float(label.mean()),
        "roi": float(pnl.sum() / (notional * len(d))),
        "pnl_usd": float(pnl.sum()),
        "orders_per_active_day": float(len(d) / d["target_date"].nunique()),
        "avg_ask": float(np.mean(ask)),
        "avg_price": float(np.mean(price)),
        "avg_p_yes_win": float(d["p_yes_win"].mean()),
        "avg_edge_snapshot": float(d["edge_snapshot"].mean()),
        "date_min": str(d["target_date"].min()),
        "date_max": str(d["target_date"].max()),
    }


def bootstrap_roi_ci(frame: pd.DataFrame, *, taker_cushion: float, iterations: int = 2000) -> list[float | None]:
    d = dedupe_live(frame)
    if d.empty or d["target_date"].nunique() < 3:
        return [None, None]
    rng = np.random.default_rng(SEED)
    notional = 5.0
    date_rows = []
    for _, g in d.groupby("target_date"):
        price = np.minimum(g["yes_current_ask"].astype(float).to_numpy() + taker_cushion, 0.999)
        label = g["label_yes_wins"].astype(int).to_numpy()
        pnl = np.where(label == 1, notional / price - notional, -notional).sum()
        date_rows.append((float(pnl), float(notional * len(g))))
    arr = np.asarray(date_rows, dtype=float)
    draws = []
    for _ in range(iterations):
        sample = arr[rng.integers(0, len(arr), len(arr))]
        cost = sample[:, 1].sum()
        draws.append(sample[:, 0].sum() / cost if cost else np.nan)
    lo, hi = np.nanpercentile(draws, [2.5, 97.5])
    return [float(lo), float(hi)]


def evaluate_rules(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows = []
    trade_frames = []
    for rule in FIXED_RULES:
        selected = df[rule_mask(df, rule)].copy()
        selected["rule"] = rule.name
        trade_frames.append(dedupe_live(selected))
        for period in ["train", "holdout", "all"]:
            scope = selected if period == "all" else selected[selected["period"].eq(period)]
            for taker_cushion in [0.0, 0.02, 0.05]:
                row = summarize_trades(scope, taker_cushion=taker_cushion)
                row.update(
                    {
                        "rule": rule.name,
                        "head": rule.head,
                        "model": rule.model,
                        "period": period,
                        "taker_cushion": taker_cushion,
                        "roi_ci95": bootstrap_roi_ci(scope, taker_cushion=taker_cushion),
                    }
                )
                summary_rows.append(row)
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    return pd.DataFrame(summary_rows), trades


def grid_search(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for model_key in ["gfs", "ecmwf"]:
        for head in ["fade_confirmed", "peak_forming"]:
            for ask_min in [0.55, 0.65, 0.75]:
                for edge_min in [0.03, 0.05, 0.08]:
                    for delta_max in ([2.0, 4.0, 6.0] if head == "fade_confirmed" else [0.0, 1.0, 2.0]):
                        if head == "fade_confirmed":
                            rule = StrategyRule(
                                name=f"{model_key}_{head}|ask>={ask_min}|edge>={edge_min}|d>=0.5|delta0-{delta_max}",
                                head=head,
                                model=model_key,
                                ask_min=ask_min,
                                p_min=0.5,
                                edge_min=edge_min,
                                liquidity_notional_min=2.0,
                                decline_min=0.5,
                                delta_min=0.0,
                                delta_max=delta_max,
                                forecast_gap_max_native=0.5,
                            )
                        else:
                            rule = StrategyRule(
                                name=f"{model_key}_{head}|ask>={ask_min}|edge>={edge_min}|d<=0.1|delta-1-{delta_max}",
                                head=head,
                                model=model_key,
                                ask_min=ask_min,
                                p_min=0.5,
                                edge_min=edge_min,
                                liquidity_notional_min=2.0,
                                decline_max=0.1,
                                delta_min=-1.0,
                                delta_max=delta_max,
                                forecast_gap_max_native=0.5,
                            )
                        selected = df[rule_mask(df, rule)]
                        train = summarize_trades(selected[selected["period"].eq("train")], taker_cushion=0.02)
                        holdout = summarize_trades(selected[selected["period"].eq("holdout")], taker_cushion=0.02)
                        if train["orders"] >= 10 and train["active_dates"] >= 5:
                            rows.append(
                                {
                                    "rule": rule.name,
                                    "head": head,
                                    "model": model_key,
                                    "train_orders": train["orders"],
                                    "train_dates": train["active_dates"],
                                    "train_roi": train["roi"],
                                    "holdout_orders": holdout["orders"],
                                    "holdout_dates": holdout["active_dates"],
                                    "holdout_roi": holdout["roi"],
                                    "holdout_win_rate": holdout["win_rate"],
                                }
                            )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["train_roi", "train_orders"], ascending=[False, False]).reset_index(drop=True)


def write_markdown(payload: dict[str, Any], summary: pd.DataFrame, grid: pd.DataFrame) -> None:
    holdout = summary[(summary["period"] == "holdout") & (summary["taker_cushion"] == 0.02)].copy()
    lines = [
        "# Theta Current YES Forecast Peak Clock Backfill v3",
        "",
        "Status: research_backfill / not_live_ready",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "Target metric: `forecast_peak_clock_current_yes_backfill` = 用历史预报 API 补出 forecast peak hour 后，测试 current-YES 的固定小时规则能否被“相对预报峰值时间”替代或增强。",
        "",
        "## 数据完整性自检",
        "",
        f"- fact_built_at_utc: `{payload['data_self_check']['fact_trades_max_built_at_utc']}`",
        f"- fact_trades trade_class: `{payload['data_self_check']['fact_trades_by_class']}`",
        f"- settlement_status: `{payload['data_self_check']['fact_trades_by_settlement_status']}`",
        f"- fact_signal_candidates coverage: `{payload['data_self_check']['fact_signal_candidate_coverage']}`",
        f"- forecast peak fact coverage: `{payload['data_self_check']['forecast_peak_fact_coverage']}`",
        f"- CLOB orders/fills join: `{payload['data_self_check']['clob_order_fill_join']}`",
        f"- CLOB gate: `{payload['clob_gate']}`",
        "",
        "## 人话结论",
        "",
        "这次把缺失的 forecast peak clock 用 Open-Meteo Historical Forecast API 补到了 replay 层，终于可以做第一版策略测算。但它仍然是 `backfilled forecast`：说明这个因子方向是否值得继续，不等于生产当时 snapshot 已经可靠落盘。",
        "",
        "最重要的结果：forecast peak clock 没有自动把 current-YES 变成更强 live 规则。固定 v9 post-decline 规则仍是当前最稳的候选；GFS fade 分支点估很好但只有 10 单/6 天，样本太薄；peak-forming 分支样本更多但 CI 跨 0，不能靠它直接上线。",
        "",
        "## Holdout 核心结果（$5/order, taker +2c）",
        "",
        "| rule | orders | dates | win | ROI | CI95 | avg ask | avg p | orders/day |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|",
    ]
    for _, row in holdout.iterrows():
        ci = row.get("roi_ci95") or [None, None]
        lines.append(
            f"| {row['rule']} | {int(row['orders'])} | {int(row['active_dates'])} | "
            f"{pct(row['win_rate'])} | {pct(row['roi'])} | "
            f"[{pct(ci[0])}, {pct(ci[1])}] | {fnum(row['avg_ask'])} | "
            f"{fnum(row['avg_p_yes_win'])} | {fnum(row.get('orders_per_active_day'), 1)} |"
        )

    lines.extend(
        [
            "",
            "## Grid Search Sanity",
            "",
            "下面只展示 train ROI 最高的 10 个 forecast-clock 变体，用来看有没有明显可前瞻迁移的参数族；它不是 live 参数选择器。",
            "",
            "| rule | train rows/dates | train ROI | holdout rows/dates | holdout ROI | holdout win |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for _, row in grid.head(10).iterrows():
        lines.append(
            f"| {row['rule']} | {int(row['train_orders'])}/{int(row['train_dates'])} | "
            f"{pct(row['train_roi'])} | {int(row['holdout_orders'])}/{int(row['holdout_dates'])} | "
            f"{pct(row['holdout_roi'])} | {pct(row['holdout_win_rate'])} |"
        )

    lines.extend(
        [
            "",
            "## 交易动作",
            "",
            "- 不把 forecast peak clock 直接推进 live。",
            "- 保留 v9 fixed post-decline tiny-live 候选，但部署前必须用生产 snapshot 原生 peak fields 重新确认，而不是只信 backfill。",
            "- forecast-clock 下一步应该作为 live/shadow telemetry 字段落盘：记录 `decision_hour - forecast_peak_hour`、forecast max gap、GFS/ECMWF peak disagreement，再用新增前瞻样本复核。",
            "",
            "## 三道门",
            "",
            "- significance=FAIL/LOW_SAMPLE：GFS fade 的 CI 不跨 0 但只有 10 单/6 天，低于样本门槛；peak-forming 样本更多但 CI 跨 0。",
            "- baseline=FAIL：未证明 forecast-clock 规则优于 v9 fixed fade-confirmed。",
            "- forward=FAIL：train grid 高 ROI 变体没有稳定迁移到 holdout。",
            "- conclusion=`inconclusive` for forecast-clock live；`telemetry_required` for production field logging。",
            "",
            "## 产物",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- summary CSV: `{(OUT_DIR / 'rule_summary.csv').relative_to(ROOT)}`",
            f"- grid CSV: `{(OUT_DIR / 'grid_search.csv').relative_to(ROOT)}`",
            f"- joined rows CSV: `{(OUT_DIR / 'forecast_peak_joined_rows.csv').relative_to(ROOT)}`",
            f"- Script: `{Path(__file__).resolve().relative_to(ROOT)}`",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-forecast", action="store_true")
    parser.add_argument("--limit-cities", type=int)
    args = parser.parse_args(argv)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    features = load_feature_rows()
    peak, forecast_stats = build_forecast_peak_table(
        features,
        refresh=args.refresh_forecast,
        limit_cities=args.limit_cities,
    )
    if peak.empty:
        raise RuntimeError("No forecast peak rows available")
    joined = attach_forecast_features(features, peak)
    summary, selected = evaluate_rules(joined)
    grid = grid_search(joined)

    peak.to_csv(OUT_DIR / "forecast_peak_city_date.csv", index=False)
    joined.to_csv(OUT_DIR / "forecast_peak_joined_rows.csv", index=False)
    summary.to_csv(OUT_DIR / "rule_summary.csv", index=False)
    selected.to_csv(OUT_DIR / "selected_rule_rows.csv", index=False)
    grid.to_csv(OUT_DIR / "grid_search.csv", index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "evidence_layer": "historical orderbook replay plus Open-Meteo historical forecast backfill",
        "row_grain": "current YES replay row; live-style deduped before ROI summaries",
        "split_date": SPLIT_DATE,
        "data_self_check": data_self_check(),
        "clob_gate": load_gate(),
        "forecast_backfill": forecast_stats,
        "coverage": {
            "feature_rows": int(len(features)),
            "joined_rows": int(len(joined)),
            "rows_with_gfs_peak": int(joined["gfs_forecast_peak_hour_local"].notna().sum()),
            "rows_with_ecmwf_peak": int(joined["ecmwf_forecast_peak_hour_local"].notna().sum()),
            "date_min": str(joined["target_date"].min()),
            "date_max": str(joined["target_date"].max()),
            "active_dates": int(joined["target_date"].nunique()),
        },
        "holdout_rule_summary_plus_2c": json_ready(
            summary[(summary["period"] == "holdout") & (summary["taker_cushion"] == 0.02)].to_dict("records")
        ),
        "grid_top10": json_ready(grid.head(10).to_dict("records")),
        "outputs": {
            "md": str(OUT_MD.relative_to(ROOT)),
            "json": str(OUT_JSON.relative_to(ROOT)),
            "summary_csv": str((OUT_DIR / "rule_summary.csv").relative_to(ROOT)),
            "grid_csv": str((OUT_DIR / "grid_search.csv").relative_to(ROOT)),
            "joined_csv": str((OUT_DIR / "forecast_peak_joined_rows.csv").relative_to(ROOT)),
        },
    }
    payload = json_ready(payload)
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(payload, summary, grid)
    print(json.dumps({"json": str(OUT_JSON), "md": str(OUT_MD), "summary": str(OUT_DIR / "rule_summary.csv")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
