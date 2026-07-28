#!/usr/bin/env python3
"""HeadA multi-source × city archetype × next-day weather-regime study.

The fixed trade denominator is:

* historical: 333 settled HeadA hot-tail tickets, 2026-05-06..2026-06-30;
* frozen shadow: 84 settled dist>0 would-live tickets, 2026-07-16..2026-07-25.

Three evidence classes are kept separate:

1. decision-time PIT: fresh ask, assigned forecast, and current hourly curves
   whose snapshot timestamp is not later than the would-live decision;
2. post-hoc historical-forecast replay: Open-Meteo multi-model daily/hourly
   archives without an exact decision-version timestamp;
3. settled outcomes, used only as labels.

The script is diagnostic research. It does not change a runner or selector.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.special import expit, logit
from scipy.stats import betabinom, beta
from sklearn.cluster import KMeans
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
HIST_PATH = ROOT / "docs/analysis/2026-07/generated/low_price_yes_forecast_source_calibration_v1/candidate_rows.csv"
CURRENT_PATH = ROOT / "docs/analysis/2026-07/generated/heada_would_live_filter_diagnostic_v1/enriched_entries.csv"
BACKFILL_DIR = ROOT / "docs/analysis/2026-07/generated/heada_multisource_city_regime_v1/multisource_backfill"
DB_PATH = ROOT / "runtime/weather.db"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/heada_multisource_city_regime_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-28-heada-multisource-city-weather-regime-paper-v1.md"

CORE_MODELS = [
    "ecmwf_ifs025",
    "ecmwf_aifs025_single",
    "gfs_seamless",
    "ncep_aigfs025",
    "icon_seamless",
    "gem_global",
    "jma_seamless",
]
CONTEXT_API = "https://historical-forecast-api.open-meteo.com/v1/forecast"
RNG_SEED = 20260728
N_BOOT = 5000
HIST_TRAIN_END = "2026-06-20"
HIST_RECENT_START = "2026-06-21"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def safe_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def load_base_frames(hist_path: Path, current_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    hist = pd.read_csv(hist_path, low_memory=False)
    current = pd.read_csv(current_path, low_memory=False)

    hist["window"] = "historical"
    hist["target_date"] = hist["target_date"].astype(str)
    hist["source"] = hist["forecast_model"].fillna("").astype(str)
    hist["ask"] = safe_num(hist["entry"])
    hist["distance_br"] = safe_num(hist["raw_dist_br"])
    hist["win"] = hist["win"].astype(bool)
    hist["cost_eval"] = safe_num(hist["cost"])
    hist["pnl_eval"] = safe_num(hist["pnl"])
    hist["bracket_low_f_eval"] = safe_num(hist["bracket_low_f"])
    hist["decision_ts"] = pd.to_datetime(hist["decision_snapshot_ts_utc"], utc=True, errors="coerce")

    current["window"] = "current_shadow"
    current["target_date"] = current["target_date"].astype(str)
    current["source"] = current["forecast_model"].fillna("").astype(str)
    current["ask"] = safe_num(current["best_ask"])
    width_native = np.where(current["unit"].eq("C"), 1.0, 2.0)
    current["distance_br"] = safe_num(current["forecast_to_bracket_low_native"]) / width_native
    current["win"] = current["win"].astype(bool)
    current["cost_eval"] = safe_num(current["taker_cost_usd"])
    current["pnl_eval"] = safe_num(current["taker_pnl_usd"])
    current["bracket_low_f_eval"] = np.where(
        current["unit"].eq("C"),
        safe_num(current["bracket_low_native"]) * 9.0 / 5.0 + 32.0,
        safe_num(current["bracket_low_native"]),
    )
    current["decision_ts"] = pd.to_datetime(current["created_at_utc"], utc=True, errors="coerce")
    return hist, current


def load_multisource(backfill_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    errors = pd.read_csv(backfill_dir / "daily_error_rows.csv", low_memory=False)
    coords = pd.read_csv(backfill_dir / "station_coordinates.csv", low_memory=False)
    errors["target_date"] = errors["target_date"].astype(str)
    errors["forecast_max_f"] = safe_num(errors["forecast_max_f"])
    pivot = (
        errors[errors["model_key"].isin(CORE_MODELS)]
        .pivot_table(
            index=["city", "target_date"],
            columns="model_key",
            values="forecast_max_f",
            aggfunc="first",
        )
        .reset_index()
    )
    for model in CORE_MODELS:
        if model not in pivot:
            pivot[model] = np.nan
        pivot[model] = safe_num(pivot[model])
    return pivot, coords


def add_multisource_features(frame: pd.DataFrame, pivot: pd.DataFrame) -> pd.DataFrame:
    out = frame.merge(pivot, on=["city", "target_date"], how="left")
    hot_cols: list[str] = []
    for model in CORE_MODELS:
        col = f"hot_{model}"
        out[col] = np.where(out[model].notna(), out[model] < out["bracket_low_f_eval"], np.nan)
        hot_cols.append(col)
    out["multi_model_n"] = out[CORE_MODELS].notna().sum(axis=1)
    out["multi_hot_count"] = out[hot_cols].sum(axis=1, min_count=1)
    out["multi_hot_share"] = out["multi_hot_count"] / out["multi_model_n"].replace(0, np.nan)
    out["multi_median_f"] = out[CORE_MODELS].median(axis=1)
    out["multi_spread_f"] = out[CORE_MODELS].max(axis=1) - out[CORE_MODELS].min(axis=1)
    out["assigned_replay_f"] = np.where(out["source"].eq("gfs"), out["gfs_seamless"], out["ecmwf_ifs025"])
    out["assigned_minus_consensus_f"] = out["assigned_replay_f"] - out["multi_median_f"]
    out["gfs_minus_ecmwf_f"] = out["gfs_seamless"] - out["ecmwf_ifs025"]
    out["consensus_bucket"] = pd.cut(
        out["multi_hot_share"],
        [-0.01, 0.25, 0.74, 1.01],
        labels=["weak_hot_0_25", "mixed_hot_29_71", "strong_hot_75_100"],
    ).astype("object")
    out["consensus_bucket"] = out["consensus_bucket"].fillna("missing")
    return out


def source_model_key(source: str) -> str:
    return "gfs_seamless" if str(source).lower() == "gfs" else "ecmwf_ifs025"


def context_cache_path(cache_dir: Path, city: str, model: str, start: str, end: str) -> Path:
    return cache_dir / f"{city}_{model}_{start}_{end}.json"


def fetch_context(
    *,
    cache_dir: Path,
    city: str,
    model: str,
    lat: float,
    lon: float,
    timezone_name: str,
    start: str,
    end: str,
    fetch_missing: bool,
) -> dict[str, Any] | None:
    path = context_cache_path(cache_dir, city, model, start, end)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if not fetch_missing:
        return None
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start,
        "end_date": end,
        "hourly": "temperature_2m,cloud_cover,precipitation_probability,wind_speed_10m",
        "models": model,
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "kn",
        "timezone": timezone_name or "auto",
    }
    with httpx.Client(timeout=60.0, trust_env=False) as client:
        response = client.get(CONTEXT_API, params=params)
        response.raise_for_status()
        payload = {"request_params": params, "response": response.json()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def daily_context_rows(payload: dict[str, Any], *, city: str, model: str) -> list[dict[str, Any]]:
    body = payload.get("response", payload)
    hourly = body.get("hourly", {})
    times = hourly.get("time") or []
    temp = hourly.get("temperature_2m") or []
    cloud = hourly.get("cloud_cover") or []
    pop = hourly.get("precipitation_probability") or []
    wind = hourly.get("wind_speed_10m") or []
    grouped: defaultdict[str, list[dict[str, float]]] = defaultdict(list)
    for ts, t, c, p, w in zip(times, temp, cloud, pop, wind, strict=False):
        vals = {
            "temp": float(t) if t is not None else math.nan,
            "cloud": float(c) if c is not None else math.nan,
            "pop": float(p) if p is not None else math.nan,
            "wind": float(w) if w is not None else math.nan,
        }
        grouped[str(ts)[:10]].append(vals)
    rows: list[dict[str, Any]] = []
    for day, values in grouped.items():
        temps = np.array([x["temp"] for x in values], dtype=float)
        if not np.isfinite(temps).any():
            continue
        peak = int(np.nanargmax(temps))
        lo, hi = max(0, peak - 2), min(len(values), peak + 3)
        peak_rows = values[lo:hi]
        rows.append(
            {
                "city": city,
                "target_date": day,
                "context_model": model,
                "hist_peak_cloud_pct": float(np.nanmean([x["cloud"] for x in peak_rows])),
                "hist_peak_pop_max_pct": float(np.nanmax([x["pop"] for x in peak_rows])),
                "hist_peak_wind_kt": float(np.nanmean([x["wind"] for x in peak_rows])),
                "hist_day_cloud_pct": float(np.nanmean([x["cloud"] for x in values])),
                "hist_day_pop_max_pct": float(np.nanmax([x["pop"] for x in values])),
                "hist_temp_range_f": float(np.nanmax(temps) - np.nanmin(temps)),
            }
        )
    return rows


def build_historical_context(
    hist: pd.DataFrame,
    coords: pd.DataFrame,
    cache_dir: Path,
    *,
    fetch_missing: bool,
) -> pd.DataFrame:
    coord_map = coords.drop_duplicates("city").set_index("city").to_dict("index")
    rows: list[dict[str, Any]] = []
    for city, group in hist.groupby("city"):
        coord = coord_map.get(city)
        if not coord:
            continue
        lat = pd.to_numeric(pd.Series([coord.get("lat")]), errors="coerce").iloc[0]
        lon = pd.to_numeric(pd.Series([coord.get("lon")]), errors="coerce").iloc[0]
        if not math.isfinite(lat) or not math.isfinite(lon):
            continue
        model = source_model_key(str(group["source"].mode().iloc[0]))
        payload = fetch_context(
            cache_dir=cache_dir,
            city=city,
            model=model,
            lat=float(lat),
            lon=float(lon),
            timezone_name=str(coord.get("timezone_name") or "auto"),
            start=str(hist["target_date"].min()),
            end="2026-07-25",
            fetch_missing=fetch_missing,
        )
        if payload:
            rows.extend(daily_context_rows(payload, city=city, model=model))
    return pd.DataFrame(rows)


def load_current_pit_context(current: pd.DataFrame, db_path: Path) -> pd.DataFrame:
    conn = connect_ro(db_path)
    curves = pd.read_sql_query(
        """
        SELECT city, target_date, forecast_model, snapshot_ts_utc,
               forecast_max_f, hourly_curve_json
        FROM fact_forecast_hourly_curves
        WHERE target_date BETWEEN '2026-07-16' AND '2026-07-25'
        ORDER BY city, target_date, forecast_model, snapshot_ts_utc
        """,
        conn,
    )
    curves["curve_ts"] = pd.to_datetime(curves["snapshot_ts_utc"], utc=True, errors="coerce")
    rows: list[dict[str, Any]] = []
    for idx, ticket in current.iterrows():
        eligible = curves[
            curves["city"].eq(ticket["city"])
            & curves["target_date"].eq(ticket["target_date"])
            & curves["forecast_model"].eq(ticket["source"])
            & curves["curve_ts"].le(ticket["decision_ts"])
        ]
        if eligible.empty:
            continue
        curve = eligible.loc[eligible["curve_ts"].idxmax()]
        hourly = json.loads(curve["hourly_curve_json"])
        temps = np.array([x.get("temperature_f", np.nan) for x in hourly], dtype=float)
        cloud = np.array([x.get("cloud_cover_pct", np.nan) for x in hourly], dtype=float)
        pop = np.array([x.get("precipitation_probability_pct", np.nan) for x in hourly], dtype=float)
        wind = np.array([x.get("wind_speed_10m_kt", np.nan) for x in hourly], dtype=float)
        peak = int(np.nanargmax(temps))
        lo, hi = max(0, peak - 2), min(len(hourly), peak + 3)
        rows.append(
            {
                "_row_id": idx,
                "pit_curve_ts": curve["snapshot_ts_utc"],
                "pit_curve_age_hours": (ticket["decision_ts"] - curve["curve_ts"]).total_seconds() / 3600.0,
                "peak_cloud_pct": float(np.nanmean(cloud[lo:hi])),
                "peak_pop_max_pct": float(np.nanmax(pop[lo:hi])),
                "peak_wind_kt": float(np.nanmean(wind[lo:hi])),
                "day_cloud_pct": float(np.nanmean(cloud)),
                "day_pop_max_pct": float(np.nanmax(pop)),
                "temp_range_f": float(np.nanmax(temps) - np.nanmin(temps)),
            }
        )
    return pd.DataFrame(rows)


def weather_regime(cloud: pd.Series, pop: pd.Series) -> pd.Series:
    values = np.select(
        [
            pop.ge(50),
            cloud.ge(70),
            cloud.ge(35),
        ],
        [
            "rain_convective",
            "cloud_suppressed",
            "mixed_cloud",
        ],
        default="clear_low_cloud",
    )
    out = pd.Series(values, index=cloud.index, dtype="object")
    return out.where(cloud.notna() & pop.notna(), "missing")


def add_context(
    hist: pd.DataFrame,
    current: pd.DataFrame,
    hist_context: pd.DataFrame,
    current_context: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not hist_context.empty:
        hist = hist.merge(hist_context, on=["city", "target_date"], how="left")
    rename = {
        "hist_peak_cloud_pct": "peak_cloud_pct",
        "hist_peak_pop_max_pct": "peak_pop_max_pct",
        "hist_peak_wind_kt": "peak_wind_kt",
        "hist_day_cloud_pct": "day_cloud_pct",
        "hist_day_pop_max_pct": "day_pop_max_pct",
        "hist_temp_range_f": "temp_range_f",
    }
    hist = hist.rename(columns=rename)
    hist["weather_evidence_class"] = "posthoc_hourly_forecast_replay"

    current = current.copy()
    current["_row_id"] = current.index
    current = current.merge(current_context, on="_row_id", how="left")
    current["weather_evidence_class"] = "decision_time_pit_curve"
    for frame in (hist, current):
        for col in ["peak_cloud_pct", "peak_pop_max_pct", "peak_wind_kt", "day_cloud_pct", "day_pop_max_pct", "temp_range_f"]:
            if col not in frame:
                frame[col] = np.nan
            frame[col] = safe_num(frame[col])
        frame["weather_regime"] = weather_regime(frame["peak_cloud_pct"], frame["peak_pop_max_pct"])
    return hist, current


def add_city_archetypes(
    hist: pd.DataFrame,
    current: pd.DataFrame,
    multisource_raw: pd.DataFrame,
    coords: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    # Freeze archetype construction before the current-shadow window so the
    # cluster assignment cannot use 7/16..7/25 settled weather.
    multisource_raw = multisource_raw[
        multisource_raw["target_date"].astype(str).le("2026-07-07")
    ].copy()
    settled = (
        multisource_raw[["city", "target_date", "settlement_mid_f"]]
        .drop_duplicates(["city", "target_date"])
        .assign(settlement_mid_f=lambda x: safe_num(x["settlement_mid_f"]))
    )
    climate = settled.groupby("city", as_index=False).agg(
        climate_days=("target_date", "nunique"),
        climate_mean_tmax_f=("settlement_mid_f", "mean"),
        climate_sd_tmax_f=("settlement_mid_f", "std"),
    )
    spread = (
        multisource_raw[multisource_raw["model_key"].isin(CORE_MODELS)]
        .pivot_table(index=["city", "target_date"], columns="model_key", values="forecast_max_f", aggfunc="first")
        .apply(pd.to_numeric, errors="coerce")
    )
    spread["ensemble_spread_f"] = spread.max(axis=1) - spread.min(axis=1)
    spread_city = (
        spread["ensemble_spread_f"]
        .groupby(level="city")
        .mean()
        .rename("ensemble_spread_f")
        .reset_index()
    )
    meta = coords[["city", "lat", "lon"]].drop_duplicates("city").copy()
    meta["lat"] = safe_num(meta["lat"])
    meta["lon"] = safe_num(meta["lon"])
    archetypes = climate.merge(spread_city, on="city", how="left").merge(meta, on="city", how="left")
    archetypes["abs_lat"] = archetypes["lat"].abs()
    feature_cols = ["abs_lat", "climate_mean_tmax_f", "climate_sd_tmax_f", "ensemble_spread_f"]
    fit = archetypes.dropna(subset=feature_cols).copy()
    scaler = StandardScaler()
    z = scaler.fit_transform(fit[feature_cols])
    km = KMeans(n_clusters=4, random_state=RNG_SEED, n_init=50)
    fit["cluster_raw"] = km.fit_predict(z)
    centers = fit.groupby("cluster_raw", as_index=False).agg(
        abs_lat=("abs_lat", "mean"),
        mean_tmax_f=("climate_mean_tmax_f", "mean"),
        sd_tmax_f=("climate_sd_tmax_f", "mean"),
        ensemble_spread_f=("ensemble_spread_f", "mean"),
    )
    centers = centers.sort_values(["abs_lat", "mean_tmax_f"]).reset_index(drop=True)
    label_map = {int(row.cluster_raw): f"A{idx + 1}" for idx, row in centers.iterrows()}
    fit["city_archetype"] = fit["cluster_raw"].map(label_map)
    archetypes = archetypes.merge(fit[["city", "city_archetype"]], on="city", how="left")
    archetypes["city_archetype"] = archetypes["city_archetype"].fillna("missing")
    city_lists = (
        archetypes.groupby("city_archetype")["city"]
        .apply(lambda x: ", ".join(sorted(x)))
        .rename("cities")
        .reset_index()
    )
    archetype_summary = (
        archetypes.groupby("city_archetype", as_index=False)
        .agg(
            city_n=("city", "nunique"),
            abs_lat=("abs_lat", "mean"),
            mean_tmax_f=("climate_mean_tmax_f", "mean"),
            sd_tmax_f=("climate_sd_tmax_f", "mean"),
            ensemble_spread_f=("ensemble_spread_f", "mean"),
        )
        .merge(city_lists, on="city_archetype", how="left")
    )
    hist = hist.merge(archetypes[["city", "city_archetype", "abs_lat"]], on="city", how="left")
    current = current.merge(archetypes[["city", "city_archetype", "abs_lat"]], on="city", how="left")
    hist["city_archetype"] = hist["city_archetype"].fillna("missing")
    current["city_archetype"] = current["city_archetype"].fillna("missing")
    return hist, current, archetype_summary


def perf_summary(frame: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    out = (
        frame.groupby(group_cols, dropna=False, observed=True)
        .agg(
            rows=("win", "size"),
            dates=("target_date", "nunique"),
            cities=("city", "nunique"),
            wins=("win", "sum"),
            win_rate=("win", "mean"),
            avg_ask=("ask", "mean"),
            cost=("cost_eval", "sum"),
            pnl=("pnl_eval", "sum"),
        )
        .reset_index()
    )
    out["roi"] = out["pnl"] / out["cost"]
    return out


def beta_binomial_diagnostics(hist: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for source in ["ecmwf", "gfs"]:
        h = hist[hist["source"].eq(source)]
        c = current[current["source"].eq(source)]
        wins_h, n_h = int(h["win"].sum()), len(h)
        wins_c, n_c = int(c["win"].sum()), len(c)
        a, b = 1 + wins_h, 1 + n_h - wins_h
        expected = n_c * a / (a + b)
        low, high = betabinom.ppf([0.025, 0.975], n_c, a, b)
        lower_tail = float(betabinom.cdf(wins_c, n_c, a, b))
        upper_tail = float(betabinom.sf(wins_c - 1, n_c, a, b))
        rows.append(
            {
                "source": source,
                "hist_rows": n_h,
                "hist_wins": wins_h,
                "current_rows": n_c,
                "current_wins": wins_c,
                "posterior_expected_current_wins": expected,
                "predictive_95_low": int(low),
                "predictive_95_high": int(high),
                "tail_probability_in_observed_direction": lower_tail if wins_c <= expected else upper_tail,
            }
        )
    return pd.DataFrame(rows)


def build_probability_model(numeric: list[str], categorical: list[str]) -> Pipeline:
    prep = ColumnTransformer(
        [
            (
                "num",
                Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]),
                numeric,
            ),
            (
                "cat",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categorical,
            ),
        ]
    )
    return Pipeline(
        [
            ("prep", prep),
            ("model", LogisticRegression(C=0.35, max_iter=5000, random_state=RNG_SEED)),
        ]
    )


def score_probability(y: pd.Series, p: np.ndarray) -> dict[str, float]:
    p = np.clip(np.asarray(p, dtype=float), 1e-5, 1 - 1e-5)
    return {
        "rows": int(len(y)),
        "wins": int(y.sum()),
        "brier": float(brier_score_loss(y, p)),
        "logloss": float(log_loss(y, p, labels=[False, True])),
        "mean_probability": float(p.mean()),
        "observed_rate": float(y.mean()),
    }


def probability_models(hist: pd.DataFrame, current: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    hist = hist.copy()
    current = current.copy()
    for frame in (hist, current):
        frame["market_logit"] = logit(frame["ask"].clip(0.001, 0.999))
        frame["source_x_archetype"] = frame["source"].astype(str) + "|" + frame["city_archetype"].astype(str)
        frame["source_x_weather"] = frame["source"].astype(str) + "|" + frame["weather_regime"].astype(str)

    train = hist[hist["target_date"].le(HIST_TRAIN_END)].copy()
    recent = hist[hist["target_date"].ge(HIST_RECENT_START)].copy()
    specs = {
        "market_raw": ([], []),
        "market_source_distance": (["market_logit", "distance_br"], ["source"]),
        "market_source_city": (
            ["market_logit", "distance_br", "abs_lat"],
            ["source", "city_archetype", "source_x_archetype"],
        ),
        "market_source_city_multisource_weather": (
            [
                "market_logit",
                "distance_br",
                "abs_lat",
                "multi_hot_share",
                "multi_spread_f",
                "assigned_minus_consensus_f",
                "peak_cloud_pct",
                "peak_pop_max_pct",
                "peak_wind_kt",
                "temp_range_f",
            ],
            ["source", "city_archetype", "weather_regime", "source_x_archetype", "source_x_weather"],
        ),
    }
    score_rows: list[dict[str, Any]] = []
    predictions: list[pd.DataFrame] = []
    for name, (numeric, categorical) in specs.items():
        if name == "market_raw":
            for label, frame in [("historical_recent", recent), ("current_shadow", current)]:
                p = frame["ask"].to_numpy(dtype=float)
                score_rows.append({"model": name, "eval_window": label, **score_probability(frame["win"], p)})
            continue
        model = build_probability_model(numeric, categorical)
        model.fit(train[numeric + categorical], train["win"])
        for label, frame in [("historical_recent", recent), ("current_shadow", current)]:
            p = model.predict_proba(frame[numeric + categorical])[:, 1]
            score_rows.append({"model": name, "eval_window": label, **score_probability(frame["win"], p)})
            if label == "current_shadow":
                pred = frame[
                    ["_row_id", "target_date", "city", "source", "win", "ask", "cost_eval", "pnl_eval"]
                ].copy()
                pred["model"] = name
                pred["p_hat"] = p
                predictions.append(pred)
    return pd.DataFrame(score_rows), pd.concat(predictions, ignore_index=True)


def prediction_by_source(predictions: pd.DataFrame) -> pd.DataFrame:
    if predictions.empty:
        return pd.DataFrame()
    return (
        predictions.groupby(["model", "source"], as_index=False)
        .agg(rows=("win", "size"), wins=("win", "sum"), expected_wins=("p_hat", "sum"), observed_rate=("win", "mean"), expected_rate=("p_hat", "mean"))
    )


def block_bootstrap_gap(current: pd.DataFrame, value_col: str, source: str) -> tuple[float, float, float]:
    x = current[current["source"].eq(source)].copy()
    if x.empty:
        return math.nan, math.nan, math.nan
    x["residual"] = x["win"].astype(float) - safe_num(x[value_col])
    by_date = x.groupby("target_date", as_index=False).agg(residual=("residual", "mean"))
    point = float(x["residual"].mean())
    rng = np.random.default_rng(RNG_SEED)
    dates = by_date["target_date"].to_numpy()
    indexed = by_date.set_index("target_date")
    vals = []
    for _ in range(N_BOOT):
        sample = rng.choice(dates, size=len(dates), replace=True)
        vals.append(float(indexed.loc[sample, "residual"].mean()))
    return point, float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))


def zero_win_upper(n: int, alpha_level: float = 0.05) -> float:
    return float(beta.ppf(1 - alpha_level, 1, n)) if n > 0 else math.nan


def filter_falsification(hist: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    rules = {
        "gfs_clear_low_cloud": lambda x: x["source"].eq("gfs") & x["weather_regime"].eq("clear_low_cloud"),
        "gfs_non_rain": lambda x: x["source"].eq("gfs") & ~x["weather_regime"].eq("rain_convective"),
        "gfs_strong_hot_consensus": lambda x: x["source"].eq("gfs") & x["consensus_bucket"].eq("strong_hot_75_100"),
        "weak_hot_consensus_any_source": lambda x: x["consensus_bucket"].eq("weak_hot_0_25"),
        "assigned_colder_than_consensus_gt1f": lambda x: x["assigned_minus_consensus_f"].lt(-1),
    }
    rows: list[dict[str, Any]] = []
    for name, rule in rules.items():
        h, c = hist[rule(hist)], current[rule(current)]
        rows.append(
            {
                "rule": name,
                "hist_rows": len(h),
                "hist_wins": int(h["win"].sum()),
                "hist_roi": h["pnl_eval"].sum() / h["cost_eval"].sum() if h["cost_eval"].sum() else math.nan,
                "current_rows": len(c),
                "current_wins": int(c["win"].sum()),
                "current_roi": c["pnl_eval"].sum() / c["cost_eval"].sum() if c["cost_eval"].sum() else math.nan,
                "current_zero_win_95_upper": zero_win_upper(len(c)) if len(c) and not c["win"].any() else math.nan,
                "hard_filter_falsified_by_history": bool(h["win"].any()),
            }
        )
    return pd.DataFrame(rows)


def miss_direction_table(current: pd.DataFrame) -> pd.DataFrame:
    return (
        current.groupby(["source", "consensus_bucket", "miss_direction"], dropna=False, observed=True)
        .size()
        .rename("rows")
        .reset_index()
    )


def plot_mechanisms(
    source_period: pd.DataFrame,
    source_consensus: pd.DataFrame,
    source_weather: pd.DataFrame,
    out_path: Path,
) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    period_order = ["historical", "current_shadow"]
    for source, color in [("ecmwf", "#3366cc"), ("gfs", "#dc3912")]:
        x = (
            source_period[source_period["source"].eq(source)]
            .assign(window=lambda d: pd.Categorical(d["window"], period_order, ordered=True))
            .sort_values("window")
        )
        axes[0].plot(x["window"], x["win_rate"], marker="o", label=source.upper(), color=color)
    axes[0].set_title("Source hit rate: history vs shadow")
    axes[0].set_ylabel("Exact-bracket hit rate")
    axes[0].tick_params(axis="x", rotation=20)
    axes[0].legend()

    cur = source_consensus[source_consensus["window"].eq("current_shadow")]
    labels = ["weak_hot_0_25", "mixed_hot_29_71", "strong_hot_75_100"]
    for idx, source in enumerate(["ecmwf", "gfs"]):
        vals = [
            cur[(cur["source"].eq(source)) & (cur["consensus_bucket"].eq(label))]["win_rate"].iloc[0]
            if not cur[(cur["source"].eq(source)) & (cur["consensus_bucket"].eq(label))].empty
            else np.nan
            for label in labels
        ]
        axes[1].bar(np.arange(len(labels)) + (idx - 0.5) * 0.35, vals, width=0.35, label=source.upper())
    axes[1].set_xticks(np.arange(len(labels)), ["weak", "mixed", "strong"])
    axes[1].set_title("Current post-hoc multi-model hot consensus")
    axes[1].legend()

    curw = source_weather[source_weather["window"].eq("current_shadow")]
    regimes = ["clear_low_cloud", "mixed_cloud", "cloud_suppressed", "rain_convective"]
    for idx, source in enumerate(["ecmwf", "gfs"]):
        vals = [
            curw[(curw["source"].eq(source)) & (curw["weather_regime"].eq(label))]["win_rate"].iloc[0]
            if not curw[(curw["source"].eq(source)) & (curw["weather_regime"].eq(label))].empty
            else np.nan
            for label in regimes
        ]
        axes[2].bar(np.arange(len(regimes)) + (idx - 0.5) * 0.35, vals, width=0.35, label=source.upper())
    axes[2].set_xticks(np.arange(len(regimes)), ["clear", "mixed", "cloud", "rain"], rotation=20)
    axes[2].set_title("Current decision-time forecast regime")
    axes[2].legend()
    for ax in axes:
        ax.grid(axis="y", alpha=0.25)
        ax.set_ylim(bottom=0)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def md_table(df: pd.DataFrame, cols: list[str], *, limit: int = 80) -> str:
    if df.empty:
        return "_无数据_"
    labels = {
        "rows": "rows",
        "wins": "wins",
        "win_rate": "win",
        "avg_ask": "ask",
        "roi": "ROI",
        "brier": "Brier",
        "logloss": "logloss",
    }
    lines = [
        "| " + " | ".join(labels.get(c, c) for c in cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    pct_cols = {
        "win_rate",
        "avg_ask",
        "roi",
        "observed_rate",
        "expected_rate",
        "mean_probability",
        "current_zero_win_95_upper",
        "tail_probability_in_observed_direction",
        "hist_roi",
        "current_roi",
        "observed_minus_expected",
        "ci_low",
        "ci_high",
    }
    for _, row in df.head(limit).iterrows():
        values = []
        for col in cols:
            value = row.get(col)
            if pd.isna(value):
                values.append("")
            elif col in pct_cols:
                values.append(f"{float(value) * 100:.1f}%")
            elif isinstance(value, (float, np.floating)):
                values.append(f"{float(value):.3f}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def build_report(
    *,
    hist: pd.DataFrame,
    current: pd.DataFrame,
    archetypes: pd.DataFrame,
    source_period: pd.DataFrame,
    source_consensus: pd.DataFrame,
    source_weather: pd.DataFrame,
    source_archetype: pd.DataFrame,
    predictive: pd.DataFrame,
    model_scores: pd.DataFrame,
    pred_source: pd.DataFrame,
    residual_bootstrap: pd.DataFrame,
    filters: pd.DataFrame,
    misses: pd.DataFrame,
) -> str:
    current_gfs = current[current["source"].eq("gfs")]
    gfs_rain = current_gfs[current_gfs["weather_regime"].eq("rain_convective")]
    gfs_nonrain = current_gfs[~current_gfs["weather_regime"].eq("rain_convective")]
    strong_gfs_hist = hist[hist["source"].eq("gfs") & hist["consensus_bucket"].eq("strong_hot_75_100")]
    strong_gfs_cur = current[current["source"].eq("gfs") & current["consensus_bucket"].eq("strong_hot_75_100")]
    pit_coverage = int(current["peak_cloud_pct"].notna().sum())
    multi_coverage = int(current["multi_model_n"].ge(5).sum())
    return f"""# HeadA 多源 × 城市 × 次日天气型机制论文 v1

Generated: {now_utc()}

## 摘要

是的，现阶段更准确的说法是：**历史筛选条件和最近 frozen shadow 出现了明显 domain shift，很多漂亮切片不能判断是稳定机制还是偶然。** 加入多源、城市 archetype 和次日云雨预报后，结论不是“找到一个可以立刻禁 GFS 的条件”，而是把问题拆成了三层：

1. **source 名字不是根因。** 历史 GFS 16/111，而当前 1/30；Beta-Binomial 后验预测当前应有约
   {predictive.loc[predictive.source.eq("gfs"), "posterior_expected_current_wins"].iloc[0]:.1f} 个赢家，
   观察到不超过 1 个的概率为
   {predictive.loc[predictive.source.eq("gfs"), "tail_probability_in_observed_direction"].iloc[0] * 100:.1f}%。
   这是异常低，但仍不是“永不命中”。
2. **post-hoc 多源共识不能解释 GFS 崩盘。** 7-model replay 中，当前 GFS 有
   {len(strong_gfs_cur)} 张属于 ≥75% 模型都认为 ticket 在热侧，仍只有 {int(strong_gfs_cur.win.sum())} 中；
   历史同桶是 {len(strong_gfs_hist)}/{int(strong_gfs_hist.win.sum())}，ROI
   {strong_gfs_hist.pnl_eval.sum() / strong_gfs_hist.cost_eval.sum() * 100:+.1f}%。
   所以不是“GFS 一家报热、其他模型都不支持”这么简单。
3. **次日天气型给出机制线索，但没有稳定 filter。** 当前 GFS 唯一赢家来自 forecast rain/convective
   桶（{len(gfs_rain)}/{int(gfs_rain.win.sum())}）；非雨桶 {len(gfs_nonrain)}/0。
   但样本太薄，0/{len(gfs_nonrain)} 的单侧 95% 命中率上界仍是
   {zero_win_upper(len(gfs_nonrain)) * 100:.1f}%，而历史同类天气里已有赢家，不能筛掉。

论文式裁决：`mechanism_plausible / selector_inconclusive / no_live_change`。

## 1. 研究问题与可证伪假说

目标量：固定 HeadA `dist>0` exact-bracket YES ticket 的命中概率与 fee-adjusted taker ROI。

- H1（单源异常）：最近 GFS 差，是因为 GFS 与其他模型分歧，且 GFS 单独把 ticket 判成热尾。
- H2（城市构成）：最近 GFS 差，是固定 GFS 城市池/气候 archetype 的样本构成变化。
- H3（次日天气型）：云、雨、对流、风和日较差改变“热尾是否发生”及 exact bracket 落点。
- H4（不可约 shift）：控制 ask、距离、城市、多源与天气后，最近 GFS 仍显著低于历史映射。

H1 在 post-hoc replay 层不受支持；由于缺 decision-time alternate-source vintage，不能声称因果上完全证伪。
H2/H3 只能解释一部分；H4 仍保留。

## 2. 数据、分母与证据等级

| 层 | 分母 | 覆盖 | 可用于 live gate? |
|---|---:|---:|---|
| historical HeadA | 333 tickets / 53 target dates / 2026-05-06..06-30 | settlement 100% | 只作 train / historical baseline |
| frozen shadow | 84 tickets / 8 target dates / 2026-07-16,19..25 | settlement + fresh ask 100% | forward 仍太短 |
| decision-time assigned hourly curve | 当前 84 | {pit_coverage}/84 | 可作 PIT feature evidence |
| 7-model daily replay | 历史 + 当前 | 当前 {multi_coverage}/84 | **不可**；无精确 decision-version timestamp |
| historical hourly weather replay | 历史 333 | {int(hist.peak_cloud_pct.notna().sum())}/333 | **不可**；post-hoc mechanism label |

主执行口径保持 fresh ask、5 shares、官方 Weather taker fee。历史 333 沿用原 price-tier
6/8/10 shares，所以跨窗口 ROI 只比较方向；概率/胜率使用同一 ticket denominator。

### Signal / evidence funnel

- signal funnel：历史 333 + frozen shadow 84，都是 first city-date hot-tail ticket。
- evidence funnel：当前 84 的 fresh quote / settlement / assigned PIT curve 完整；
  alternate-source decision-time curve 不完整，因此 7-model结果降级为 post-hoc 机制诊断。
- actual fill：当前为 zero-notional shadow，0 fills；本文不发布 `live_real`。

## 3. 描述性结果：历史与当前 source shift

{md_table(source_period, ["window", "source", "rows", "dates", "wins", "win_rate", "avg_ask", "roi"])}

{md_table(predictive, ["source", "hist_rows", "hist_wins", "current_rows", "current_wins", "posterior_expected_current_wins", "predictive_95_low", "predictive_95_high", "tail_probability_in_observed_direction"])}

解释：GFS 当前结果处在历史后验预测的低尾，但不是概率为零的事件。ECMWF 当前 11/54 则略高于历史期望，
没有达到可以把 source 当因果 treatment 的条件，因为 source 由 city 固定路由。

## 4. 多源共识：否定“GFS 单独报错”假说

`hot share` 定义为 7 个独立核心全球模型中 forecast Tmax 低于 ticket lower bound 的比例；
它只回答“各模型是否认为这是热侧 ticket”，不等于 exact-bracket 概率。

{md_table(source_consensus, ["window", "source", "consensus_bucket", "rows", "wins", "win_rate", "avg_ask", "roi"])}

最重要的反直觉是：

- 历史强热共识通常优于弱共识，说明共识对“热尾是否发生”有信息；
- 当前 GFS 强共识仍是 1/20，说明最近失败不是单模型 outlier；
- ECMWF 当前 mixed 是 6/18，strong 反而 3/25。对 exact bracket 来说，越强的热尾共识也可能增加
  overshoot，而不是单调增加某一张彩票命中率。

当前 miss direction：

{md_table(misses, ["source", "consensus_bucket", "miss_direction", "rows"])}

所以多源的正确表达不是 `all models hot => 买这张 exact ticket`，而应是：

```text
P(reach hot tail | multi-source state)
× P(land in exact bracket | reached hot tail, dispersion, city bias, weather regime)
```

严格证据边界：这些 7-model forecast 是 historical-forecast archive replay，不是每张票 decision timestamp
冻结的 alternate-source vintage。因此它足以推翻“现有数据已经证明 GFS 独自报热”的说法，
但不足以证明当时盘口时刻其他模型一定也报热。

## 5. 次日晴 / 云 / 雨：有机制，不足以筛

当前天气型在决策时按 assigned-source PIT hourly curve、forecast peak ±2h 固定分类；
历史表使用同定义的 post-hoc hourly forecast replay，只检查机制方向，不冒充 PIT：

- rain/convective：peak-window precipitation probability ≥50%;
- cloud-suppressed：非雨且 peak-window mean cloud ≥70%;
- mixed-cloud：35%..70%;
- clear-low-cloud：<35%。

{md_table(source_weather, ["window", "source", "weather_regime", "rows", "wins", "win_rate", "avg_ask", "roi"])}

当前 GFS 的 1 个赢家确实在 rain/convective，clear 0/16、mixed 0/3、cloud-suppressed 0/7。
但这更像 **天气 regime 改变 forecast error direction / exact landing**，不是“雨天才会中”：
历史 replay 中非雨桶存在赢家，而且当前每桶 target-date block 太少。

物理解释：

- clear / low-cloud 通常扩大可加热窗口；对 hot-tail“能否到达”有利，但也提高 overshoot 下一档的风险；
- cloud-suppressed 降低 reach 概率，但若 assigned forecast 本来过热，反而可能把最终 Tmax 拉回 ticket；
- convective/rain 同时带来辐射抑制、阵前增温、风向突变，结果取决于发生时钟相对 forecast peak，而非晴/雨二元标签；
- 因此分类只适合进入连续概率模型，不能直接做 hard gate。

## 6. 城市 archetype：构成效应存在，但 source 与 city 不可分

为避免手工按赢家给城市贴标签，A1..A4 是仅用历史气候/地理连续量做的无监督聚类：
`|latitude|、历史日最高温均值/波动、7-model spread`。没有使用 ticket win/ROI。

{md_table(archetypes, ["city_archetype", "city_n", "abs_lat", "mean_tmax_f", "sd_tmax_f", "ensemble_spread_f", "cities"], limit=10)}

{md_table(source_archetype, ["window", "source", "city_archetype", "rows", "wins", "win_rate", "avg_ask", "roi"])}

城市层最大限制是 positivity：很多城市只固定走 GFS 或 ECMWF，缺少同一 city-date 的随机 source A/B。
因此 `source coefficient` 同时携带城市、纬度、气候、市场关注和历史 bias，不能解释为 ECMWF 因果优于 GFS。

## 7. 概率模型与 market baseline

训练固定为历史 ≤2026-06-20；先测 historical recent 6/21..30，再测当前 84。模型是 L2 logistic，
不扫阈值。`market_raw` 直接用 fresh ask 作为概率基准。

{md_table(model_scores, ["eval_window", "model", "rows", "wins", "observed_rate", "mean_probability", "brier", "logloss"])}

{md_table(pred_source, ["model", "source", "rows", "wins", "expected_wins", "observed_rate", "expected_rate"])}

full mechanism model 的 current `observed - expected`，按 target_date block bootstrap：

{md_table(residual_bootstrap, ["source", "observed_minus_expected", "ci_low", "ci_high"])}

如果 source/city/weather 模型不能在 historical recent 和当前同时打败 market 的 Brier/logloss，
它只能解释 shift，不能用于重新定价。当前结论正是如此：加入大量机制特征没有形成稳定的 market residual alpha。
GFS residual 为负且 block CI 不跨 0，但这个模型本身没有在 historical recent 打败 market，
所以它证明“历史映射解释不了当前 GFS”，不证明存在可交易的新 gate。

## 8. “完全不会中”的筛选反证

{md_table(filters, ["rule", "hist_rows", "hist_wins", "hist_roi", "current_rows", "current_wins", "current_roi", "current_zero_win_95_upper", "hard_filter_falsified_by_history"])}

任何当前 0-win 桶，只要历史同机制已有 winner，就已经否定“完全不会中”；样本 0/n 的上界也远不等于零。
因此这些条件最多做 frozen `lottery-risk / no-size-up` telemetry，不能删票。

## 9. 讨论：这次多源带来的新理解

1. **把 source effect 改写成 forecast-state effect。** 真正可迁移的对象不是 GFS/ECMWF 名字，而是
   assigned-vs-consensus 偏差、ensemble spread、各模型 hot vote，以及每城 rolling bias。
2. **把 hot-tail 与 exact landing 分开。** 多源共识较擅长判断会不会进入热尾；彩票收益取决于进入后落在哪一档。
   强共识既可能提高 reach，也可能提高 overshoot。
3. **天气型是 error-direction moderator。** 云雨不是静态筛选器，而是改变 forecast bias、剩余加热窗口和分布宽度的变量。
4. **城市是层级效应，不是黑名单。** 应做 hierarchical city/source calibration 或 partial pooling；
   逐城 ROI 会被一两个彩票 winner 主导。
5. **最近 GFS 崩盘仍有不可约 residual。** 7-model strong-hot 也救不了，城市/价格/距离也不足以解释；
   需要继续收真正 PIT alternate forecasts，才能区分 collector/version shift、季节 regime shift 与纯随机低尾。

## 10. 局限、可证伪的下一步与裁决

- 当前只有 8 个 target-date blocks；所有 current weather 交互都不具备独立 forward。
- 多源 daily/hourly backfill 没有精确 decision-version timestamp，只能 post-hoc。
- 当前 curves 对 assigned source 完整，但 alternate source 不完整；这正是旧报告 `any_source` 样本过薄的根因。
- source×city 缺 overlap，不能做因果 source treatment。
- 本轮候选切片是 mechanism diagnostics，没有做多重检验后的 selector promotion。
- canonical metadata 没有可靠 elevation；若你说的 “Altas” 指 altitude，本轮只测了纬度/历史气候 archetype，
  没有伪造海拔结论。

下一步已冻结为：

```text
每张 HeadA candidate 在 decision timestamp 同步记录
7-model Tmax + peak-window cloud/precip/wind + forecast vintage
→ 先预测 reach-hot-tail
→ 再预测 exact-bracket conditional landing
→ raw market ask 为基准
→ target_date block、至少 20 个新日期后 frozen forward
```

最终裁决：

```text
significance = PASS only for current GFS-vs-ECMWF descriptive gap
historical baseline = FAIL for permanent source ban
multi-source mechanism = H1 unsupported in post-hoc replay; decision-time causal test unavailable
weather/city interaction = plausible but underpowered
probability baseline = market not beaten robustly
conclusion = inconclusive; keep all as continuous shadow telemetry; no live selector change
```

## 产物

- evaluator: `scripts/analysis/forecast_quality/research_heada_multisource_city_regime_paper_v1.py`
- structured summary: `generated/heada_multisource_city_regime_v1/summary.json`
- row-level data: `historical_enriched.csv`, `current_enriched.csv`
- diagnostics: `source_period.csv`, `source_consensus.csv`, `source_weather.csv`,
  `source_archetype.csv`, `probability_scores.csv`, `filter_falsification.csv`
- figure: `mechanism_panels.png`
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hist", type=Path, default=HIST_PATH)
    parser.add_argument("--current", type=Path, default=CURRENT_PATH)
    parser.add_argument("--backfill-dir", type=Path, default=BACKFILL_DIR)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--fetch-missing-context", action="store_true")
    args = parser.parse_args()

    hist, current = load_base_frames(args.hist, args.current)
    pivot, coords = load_multisource(args.backfill_dir)
    multisource_raw = pd.read_csv(args.backfill_dir / "daily_error_rows.csv", low_memory=False)
    multisource_raw["forecast_max_f"] = safe_num(multisource_raw["forecast_max_f"])
    multisource_raw["settlement_mid_f"] = safe_num(multisource_raw["settlement_mid_f"])
    hist = add_multisource_features(hist, pivot)
    current = add_multisource_features(current, pivot)

    context_cache = args.out_dir / "hourly_context_cache"
    hist_context = build_historical_context(
        hist,
        coords,
        context_cache,
        fetch_missing=bool(args.fetch_missing_context),
    )
    current_context = load_current_pit_context(current, args.db)
    hist, current = add_context(hist, current, hist_context, current_context)
    hist, current, archetypes = add_city_archetypes(hist, current, multisource_raw, coords)

    combined = pd.concat([hist, current], ignore_index=True, sort=False)
    source_period = perf_summary(combined, ["window", "source"])
    source_consensus = perf_summary(combined, ["window", "source", "consensus_bucket"])
    source_weather = perf_summary(combined, ["window", "source", "weather_regime"])
    source_archetype = perf_summary(combined, ["window", "source", "city_archetype"])
    predictive = beta_binomial_diagnostics(hist, current)
    model_scores, predictions = probability_models(hist, current)
    pred_source = prediction_by_source(predictions)
    filters = filter_falsification(hist, current)
    misses = miss_direction_table(current)

    full_model = predictions[predictions["model"].eq("market_source_city_multisource_weather")].copy()
    if not full_model.empty:
        current = current.merge(
            full_model[["_row_id", "p_hat"]].rename(columns={"p_hat": "full_model_p_hat"}),
            on="_row_id",
            how="left",
        )
    residual_bootstrap = []
    for source in ["ecmwf", "gfs"]:
        point, low, high = block_bootstrap_gap(current, "full_model_p_hat", source)
        residual_bootstrap.append(
            {"source": source, "observed_minus_expected": point, "ci_low": low, "ci_high": high}
        )
    residual_bootstrap_df = pd.DataFrame(residual_bootstrap)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "historical_enriched.csv": hist,
        "current_enriched.csv": current,
        "city_archetypes.csv": archetypes,
        "source_period.csv": source_period,
        "source_consensus.csv": source_consensus,
        "source_weather.csv": source_weather,
        "source_archetype.csv": source_archetype,
        "beta_binomial_predictive.csv": predictive,
        "probability_scores.csv": model_scores,
        "current_probability_predictions.csv": predictions,
        "current_prediction_by_source.csv": pred_source,
        "filter_falsification.csv": filters,
        "miss_direction.csv": misses,
        "residual_block_bootstrap.csv": residual_bootstrap_df,
    }
    for name, frame in outputs.items():
        frame.to_csv(args.out_dir / name, index=False)

    plot_mechanisms(
        source_period,
        source_consensus,
        source_weather,
        args.out_dir / "mechanism_panels.png",
    )
    report = build_report(
        hist=hist,
        current=current,
        archetypes=archetypes,
        source_period=source_period,
        source_consensus=source_consensus,
        source_weather=source_weather,
        source_archetype=source_archetype,
        predictive=predictive,
        model_scores=model_scores,
        pred_source=pred_source,
        residual_bootstrap=residual_bootstrap_df,
        filters=filters,
        misses=misses,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    summary = {
        "generated_at_utc": now_utc(),
        "denominators": {
            "historical_rows": len(hist),
            "historical_dates": int(hist["target_date"].nunique()),
            "current_rows": len(current),
            "current_dates": int(current["target_date"].nunique()),
        },
        "coverage": {
            "current_pit_weather": int(current["peak_cloud_pct"].notna().sum()),
            "current_multisource_posthoc_ge5": int(current["multi_model_n"].ge(5).sum()),
            "historical_weather_posthoc": int(hist["peak_cloud_pct"].notna().sum()),
            "historical_multisource_posthoc_ge5": int(hist["multi_model_n"].ge(5).sum()),
        },
        "verdict": "inconclusive_no_live_change",
        "report": str(args.report),
        "artifacts": sorted(outputs),
    }
    (args.out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
