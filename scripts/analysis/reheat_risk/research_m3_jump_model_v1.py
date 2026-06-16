#!/usr/bin/env python3
"""M3 jump-risk model v1: calibration-first P(bucket jump) with weather features.

Goal
----
v0 (research_m3_cross_section_no.py) predicts P(jump == d) from
(city, hour, decline-bucket) frequencies + Beta-binomial shrinkage. It is
over-confident out-of-sample (its 98-100% win bucket realized 91.7%).

v1 models the *market-band* jump (C cities: 1C buckets via round-half-up;
F cities: even-aligned 2F bands, matching Polymarket brackets):

    bucket_C(x)  = floor(x_c + 0.5)
    bucket_F(x)  = floor(floor(x_f + 0.5) / 2)
    jump_b       = bucket(final_max) - bucket(running_max)

Four HistGradientBoosting classifiers (P(jump_b >= d), d = 1..4), each
isotonic-calibrated on out-of-fold predictions (GroupKFold by target_date)
within the training window. P(land exactly d) = P(>=d) - P(>=d+1).

Features (all computed strictly from data available at the decision cutoff
= local decision hour :00, no lookahead):
- geometry in band units: decline, fraction to next band threshold, gap of
  current temp to next band threshold
- intraday path from wu_obs: 1/2/3h temperature deltas, hours since the
  running max was last refreshed, morning rise rate, day range, obs age
- warming-condition features from IEM extended ASOS (whitelist stations,
  fetched here, cached in runtime/rule_source_research/obs_cache/):
  dewpoint depression, RH, wind speed, sky cover code, 3h dwpf/relh deltas
- calendar: decision hour, month, city categorical

Discipline
----------
- train: target_date < 2026-05-19 (model choice + isotonic only via grouped
  CV inside this window); eval: target_date >= 2026-05-19
- v0 baseline re-fit on the same training rows / same jump_b labels (i.e.
  the strongest fair version of v0)
- taker test: score whitelist tail-NO quotes, model_ev = P(win) - ask,
  thresholds {0, 0.02, 0.05}, dedup first decision hour per
  (city, day, bracket), realized ROI + daily t-stat, v1 vs v0.

ECMWF cache is NOT used: it is a continuous (non-vintage) series and stops
2026-04-29, so it cannot provide leakage-free features in either window.

Usage:
    .venv/bin/python scripts/analysis/reheat_risk/research_m3_jump_model_v1.py [--skip-fetch]
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]

DETAIL_CSV = REPO / "docs/analysis/2026-06/generated/m3_observed_max_v3_h10_21/m3_observed_max_residual_detail.csv"
QUOTES_CSV = REPO / "docs/analysis/2026-06/generated/m3_exhaustion_no_v0/exhaustion_tail_no_quotes.csv"
WU_OBS_DIR = REPO / "runtime/weather_edge_v1/market_data/cache/wu_obs"
IEM_CACHE_DIR = REPO / "runtime/rule_source_research/obs_cache"
OUT_DIR = REPO / "docs/analysis/2026-06/generated/m3_jump_model_v1"
FEATURE_CACHE = REPO / "runtime/rule_source_research/m3_jump_model_v1_features.csv.gz"

TRAIN_END = "2026-05-19"  # exclusive
IEM_START, IEM_END = "2024-04-01", "2026-06-11"  # IEM end date is exclusive
SHRINK_K = 50.0
JUMP_THRESHOLDS = (1, 2, 3, 4)
SEED = 7

F_CITIES = {"Atlanta", "Austin", "Dallas", "Denver", "Houston", "LA", "Miami", "NYC", "SanFrancisco", "Seattle"}

IEM_COLS = ["tmpf", "dwpf", "relh", "drct", "sknt", "skyc1"]
SKY_CODE = {"CLR": 0, "SKC": 0, "NSC": 0, "NCD": 0, "CAVOK": 0, "FEW": 1, "SCT": 2, "BKN": 3, "OVC": 4, "VV": 4}

FEATURES_NUM = [
    "decision_hour_local", "month",
    "decline_b", "frac_to_next_b", "gap_cur_to_thresh_b",
    "d1h_b", "d2h_b", "d3h_b",
    "hours_since_max", "rise_rate_b", "day_range_b",
    "obs_count_to_decision", "last_obs_age_min",
    "dep_f", "relh_now", "sknt_now", "sky_now", "d_dwpf_3h_f", "d_relh_3h",
]
FEATURES_CAT = ["city"]


def round_half_up(x: float) -> int:
    return math.floor(float(x) + 0.5)


def bucket_value(native: float, is_f: bool) -> int:
    r = round_half_up(native)
    return r // 2 if is_f else r  # floor div: even-aligned 2F bands


def band_width(is_f: bool) -> float:
    return 2.0 if is_f else 1.0


def next_band_threshold(native_running_max: float, is_f: bool) -> float:
    """Raw native temp at which the next band is first reached (round-half-up)."""
    b = bucket_value(native_running_max, is_f)
    if is_f:
        return 2 * b + 1.5  # rhu(x) >= 2b+2  <=>  x >= 2b+1.5
    return b + 0.5


def decline_bucket_c(x: float) -> str:
    if x < 0.5:
        return "<0.5"
    if x < 1.0:
        return "0.5-1.0"
    if x < 2.0:
        return "1.0-2.0"
    return ">=2.0"


# --------------------------------------------------------------------------
# IEM fetch
# --------------------------------------------------------------------------

def fetch_iem_ext(icao: str) -> Path:
    cache = IEM_CACHE_DIR / f"iem_ext_{icao}_{IEM_START}_{IEM_END}.csv"
    if cache.exists() and cache.stat().st_size > 10000:
        return cache
    y1, m1, d1 = IEM_START.split("-")
    y2, m2, d2 = IEM_END.split("-")
    params = [("station", icao)] + [("data", c) for c in IEM_COLS] + [
        ("year1", y1), ("month1", m1), ("day1", d1),
        ("year2", y2), ("month2", m2), ("day2", d2),
        ("tz", "UTC"), ("format", "comma"), ("latlon", "no"),
        ("missing", "M"), ("trace", "T"), ("direct", "no"),
        ("report_type", "3"), ("report_type", "4"),
    ]
    url = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=600) as r:
        text = r.read().decode()
    lines = [l for l in text.splitlines() if not l.startswith("#") and l.strip()]
    if len(lines) < 100:
        raise RuntimeError(f"IEM returned too little data for {icao}: {len(lines)} lines")
    cache.write_text("\n".join(lines) + "\n")
    return cache


# --------------------------------------------------------------------------
# Feature building
# --------------------------------------------------------------------------

def _asof_value(ts_arr: np.ndarray, val_arr: np.ndarray, target: np.datetime64, tol_min: float) -> float:
    """Last value at/before target within tolerance; NaN otherwise."""
    i = np.searchsorted(ts_arr, target, side="right")
    if i == 0:
        return np.nan
    age = (target - ts_arr[i - 1]) / np.timedelta64(1, "m")
    if age > tol_min:
        return np.nan
    return float(val_arr[i - 1])


def build_city_features(city: str, icao: str, tz_name: str, rows: pd.DataFrame) -> pd.DataFrame:
    """Path + condition features for one city's decision rows (no lookahead)."""
    is_f = city in F_CITIES
    bw = band_width(is_f)
    tz = ZoneInfo(tz_name)

    wu_path = WU_OBS_DIR / f"wu_obs_{icao}.csv"
    wu = pd.read_csv(wu_path, usecols=["date_local", "valid_utc", "temp"])
    wu["temp"] = pd.to_numeric(wu["temp"], errors="coerce")
    wu = wu.dropna(subset=["temp"])
    wu["ts"] = pd.to_datetime(wu["valid_utc"], utc=True)
    wu["ts_np"] = wu["ts"].dt.tz_convert("UTC").dt.tz_localize(None)
    wu = wu.sort_values("ts")
    # native unit values
    wu["val"] = wu["temp"] if is_f else (wu["temp"] - 32.0) * 5.0 / 9.0
    wu_by_day = {d: g for d, g in wu.groupby("date_local", sort=False)}

    iem_path = IEM_CACHE_DIR / f"iem_ext_{icao}_{IEM_START}_{IEM_END}.csv"
    iem = None
    if iem_path.exists():
        iem = pd.read_csv(iem_path, na_values=["M"], low_memory=False)
        iem["ts"] = pd.to_datetime(iem["valid"], utc=True)
        iem["ts_np"] = iem["ts"].dt.tz_convert("UTC").dt.tz_localize(None)
        iem = iem.sort_values("ts")
        for c in ("tmpf", "dwpf", "relh", "sknt"):
            iem[c] = pd.to_numeric(iem[c], errors="coerce")
        iem["sky"] = iem["skyc1"].map(SKY_CODE)
        iem_ts = iem["ts_np"].to_numpy(dtype="datetime64[ns]")
        iem_dwpf = iem["dwpf"].to_numpy(dtype=float)
        iem_tmpf = iem["tmpf"].to_numpy(dtype=float)
        iem_relh = iem["relh"].to_numpy(dtype=float)
        iem_sknt = iem["sknt"].to_numpy(dtype=float)
        iem_sky = iem["sky"].to_numpy(dtype=float)

    out = []
    for r in rows.itertuples():
        date, hour = r.target_date, int(r.decision_hour_local)
        feat = {
            "city": city, "target_date": date, "decision_hour_local": hour,
            "month": int(date[5:7]),
            "obs_count_to_decision": r.obs_count_to_decision,
        }
        running_native = r.running_max_f if is_f else r.running_max_c
        current_native = r.current_temp_f if is_f else r.current_temp_c
        final_native = r.final_max_f if is_f else r.final_max_c
        feat["jump_b"] = bucket_value(final_native, is_f) - bucket_value(running_native, is_f)
        thresh = next_band_threshold(running_native, is_f)
        feat["decline_b"] = (running_native - current_native) / bw
        feat["frac_to_next_b"] = (thresh - running_native) / bw
        feat["gap_cur_to_thresh_b"] = (thresh - current_native) / bw
        feat["decline_c_v0"] = r.running_max_c - r.current_temp_c  # for v0 baseline

        cutoff_local = pd.Timestamp(f"{date} {hour:02d}:00", tz=tz)
        cutoff = np.datetime64(cutoff_local.tz_convert("UTC").tz_localize(None).to_datetime64(), "ns")
        day = wu_by_day.get(date)
        if day is not None:
            ts = day["ts_np"].to_numpy(dtype="datetime64[ns]")
            val = day["val"].to_numpy(dtype=float)
            i = np.searchsorted(ts, cutoff, side="right")
            if i > 0:
                seen_ts, seen_val = ts[:i], val[:i]
                cur = seen_val[-1]
                rmax = seen_val.max()
                feat["last_obs_age_min"] = float((cutoff - seen_ts[-1]) / np.timedelta64(1, "m"))
                # hours since running max last refreshed (last obs equal to running max)
                max_idx = np.nonzero(seen_val >= rmax - 1e-9)[0][-1]
                feat["hours_since_max"] = float((cutoff - seen_ts[max_idx]) / np.timedelta64(1, "h"))
                # cooling deltas (band units): current minus temp ~k hours ago
                for k in (1, 2, 3):
                    past = _asof_value(seen_ts, seen_val, cutoff - np.timedelta64(k, "h"), tol_min=75)
                    feat[f"d{k}h_b"] = (cur - past) / bw if not np.isnan(past) else np.nan
                # morning rise rate: first obs -> running max
                t0, v0 = seen_ts[0], seen_val[0]
                first_max_idx = int(np.argmax(seen_val))
                dt_h = (seen_ts[first_max_idx] - t0) / np.timedelta64(1, "h")
                feat["rise_rate_b"] = ((rmax - v0) / bw / dt_h) if dt_h > 0.5 else np.nan
                feat["day_range_b"] = (rmax - seen_val.min()) / bw
                feat["wu_running_max_check"] = abs(rmax - float(running_native))

        if iem is not None:
            j = np.searchsorted(iem_ts, cutoff, side="right")
            if j > 0 and (cutoff - iem_ts[j - 1]) / np.timedelta64(1, "m") <= 150:
                k = j - 1
                if not (np.isnan(iem_tmpf[k]) or np.isnan(iem_dwpf[k])):
                    feat["dep_f"] = iem_tmpf[k] - iem_dwpf[k]
                feat["relh_now"] = iem_relh[k]
                feat["sknt_now"] = iem_sknt[k]
                feat["sky_now"] = iem_sky[k]
                d3 = cutoff - np.timedelta64(3, "h")
                dw3 = _asof_value(iem_ts, iem_dwpf, d3, tol_min=90)
                rh3 = _asof_value(iem_ts, iem_relh, d3, tol_min=90)
                if not (np.isnan(iem_dwpf[k]) or np.isnan(dw3)):
                    feat["d_dwpf_3h_f"] = iem_dwpf[k] - dw3
                if not (np.isnan(iem_relh[k]) or np.isnan(rh3)):
                    feat["d_relh_3h"] = iem_relh[k] - rh3
        out.append(feat)
    return pd.DataFrame(out)


def build_features(detail: pd.DataFrame, city_meta: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for r in city_meta.itertuples():
        rows = detail[detail["city"] == r.city]
        t0 = time.time()
        parts.append(build_city_features(r.city, r.icao, r.timezone, rows))
        print(f"  features {r.city:<14} {len(rows):>6} rows  {time.time()-t0:.1f}s", flush=True)
    return pd.concat(parts, ignore_index=True)


# --------------------------------------------------------------------------
# v0 baseline (re-fit on the same labels)
# --------------------------------------------------------------------------

class V0Baseline:
    """(city, hour, C-decline-bucket) frequencies + shrink-to-pooled, on jump_b."""

    def __init__(self, train: pd.DataFrame):
        t = train.copy()
        t["db"] = t["decline_c_v0"].apply(decline_bucket_c)
        self.events = [("ge", d) for d in JUMP_THRESHOLDS] + [("eq", d) for d in (1, 2, 3)]
        self.pooled: dict = {}
        for (hour, db), g in t.groupby(["decision_hour_local", "db"]):
            for kind, d in self.events:
                y = (g["jump_b"] >= d) if kind == "ge" else (g["jump_b"] == d)
                self.pooled[(hour, db, kind, d)] = float(y.mean())
        self.city: dict = {}
        for (city, hour, db), g in t.groupby(["city", "decision_hour_local", "db"]):
            n = len(g)
            for kind, d in self.events:
                y = (g["jump_b"] >= d) if kind == "ge" else (g["jump_b"] == d)
                p_pool = self.pooled.get((hour, db, kind, d), 0.0)
                self.city[(city, hour, db, kind, d)] = (n * float(y.mean()) + SHRINK_K * p_pool) / (n + SHRINK_K)

    def predict(self, df: pd.DataFrame, kind: str, d: int) -> np.ndarray:
        db = df["decline_c_v0"].apply(decline_bucket_c)
        out = np.full(len(df), np.nan)
        for i, (city, hour, b) in enumerate(zip(df["city"], df["decision_hour_local"], db)):
            key = (city, int(hour), b, kind, d)
            if key in self.city:
                out[i] = self.city[key]
            else:
                out[i] = self.pooled.get((int(hour), b, kind, d), np.nan)
        return out


# --------------------------------------------------------------------------
# v1 model
# --------------------------------------------------------------------------

def fit_v1(train: pd.DataFrame) -> dict:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import log_loss
    from sklearn.model_selection import GroupKFold

    X = train[FEATURES_NUM + FEATURES_CAT].copy()
    X["city"] = X["city"].astype("category")
    groups = train["target_date"].to_numpy()
    gkf = GroupKFold(n_splits=5)
    folds = list(gkf.split(X, groups=groups))

    def make(params):
        return HistGradientBoostingClassifier(
            categorical_features=["city"], random_state=SEED,
            early_stopping=False, max_iter=300, **params,
        )

    # small hyperparameter scan on the d=1 target (train-internal CV only)
    y1 = (train["jump_b"] >= 1).to_numpy(dtype=int)
    grid = [
        {"learning_rate": 0.05, "max_leaf_nodes": 15, "min_samples_leaf": 100},
        {"learning_rate": 0.05, "max_leaf_nodes": 31, "min_samples_leaf": 100},
        {"learning_rate": 0.10, "max_leaf_nodes": 15, "min_samples_leaf": 100},
        {"learning_rate": 0.10, "max_leaf_nodes": 31, "min_samples_leaf": 100},
    ]
    scan = []
    for params in grid:
        oof = np.full(len(X), np.nan)
        for tr, te in folds:
            m = make(params)
            m.fit(X.iloc[tr], y1[tr])
            oof[te] = m.predict_proba(X.iloc[te])[:, 1]
        scan.append((log_loss(y1, oof), params))
        print(f"  scan {params} -> cv logloss {scan[-1][0]:.5f}", flush=True)
    scan.sort(key=lambda t: t[0])
    best = scan[0][1]
    print(f"  best params: {best}")

    models = {}
    for d in JUMP_THRESHOLDS:
        y = (train["jump_b"] >= d).to_numpy(dtype=int)
        oof = np.full(len(X), np.nan)
        for tr, te in folds:
            m = make(best)
            m.fit(X.iloc[tr], y[tr])
            oof[te] = m.predict_proba(X.iloc[te])[:, 1]
        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        iso.fit(oof, y)
        final = make(best)
        final.fit(X, y)
        models[d] = (final, iso)
        print(f"  fitted d>={d}: base rate {y.mean():.4f}, cv logloss {log_loss(y, oof):.5f}", flush=True)
    return {"models": models, "params": best}


def predict_v1(bundle: dict, df: pd.DataFrame) -> pd.DataFrame:
    X = df[FEATURES_NUM + FEATURES_CAT].copy()
    X["city"] = X["city"].astype("category")
    out = {}
    for d, (m, iso) in bundle["models"].items():
        raw = m.predict_proba(X)[:, 1]
        out[f"p_ge{d}"] = iso.predict(raw)
    p = pd.DataFrame(out, index=df.index)
    # enforce monotone thresholds
    for a, b in ((2, 1), (3, 2), (4, 3)):
        p[f"p_ge{a}"] = np.minimum(p[f"p_ge{a}"], p[f"p_ge{b}"])
    for d in (1, 2, 3):
        p[f"p_eq{d}"] = (p[f"p_ge{d}"] - p[f"p_ge{d+1}"]).clip(lower=0.0)
    return p


def p_lose_quote(p: pd.Series, dist_b: int, is_plus: bool) -> float:
    """P(quote bracket wins YES) i.e. NO loses."""
    if is_plus:
        if dist_b <= 4:
            return float(p[f"p_ge{dist_b}"])
        return float(p["p_ge4"])  # conservative upper bound for far open brackets
    if dist_b <= 3:
        return float(p[f"p_eq{dist_b}"])
    return float(p["p_ge4"])  # upper bound for far regular brackets (rare)


def p_lose_v0(v0: V0Baseline, row: pd.Series, dist_b: int, is_plus: bool) -> float:
    df1 = row.to_frame().T
    if is_plus:
        d = min(dist_b, 4)
        return float(v0.predict(df1, "ge", d)[0])
    if dist_b <= 3:
        return float(v0.predict(df1, "eq", dist_b)[0])
    return float(v0.predict(df1, "ge", 4)[0])


# --------------------------------------------------------------------------
# Evaluation helpers
# --------------------------------------------------------------------------

def reliability_table(y: np.ndarray, p: np.ndarray, bins: list[float]) -> pd.DataFrame:
    df = pd.DataFrame({"y": y, "p": p})
    df["bin"] = pd.cut(df["p"], bins, include_lowest=True)
    g = df.groupby("bin", observed=True).agg(n=("y", "size"), pred=("p", "mean"), actual=("y", "mean"))
    g["gap_pp"] = (g["actual"] - g["pred"]) * 100
    return g.reset_index()


def brier_logloss(y: np.ndarray, p: np.ndarray) -> tuple[float, float]:
    eps = 1e-6
    pc = np.clip(p, eps, 1 - eps)
    return float(np.mean((p - y) ** 2)), float(-np.mean(y * np.log(pc) + (1 - y) * np.log(1 - pc)))


def daily_brier_tstat(dates: np.ndarray, y: np.ndarray, p_a: np.ndarray, p_b: np.ndarray) -> float:
    """t-stat of mean daily Brier(a) - Brier(b); negative favors a."""
    d = pd.DataFrame({"date": dates, "diff": (p_a - y) ** 2 - (p_b - y) ** 2})
    daily = d.groupby("date")["diff"].mean()
    if len(daily) < 2 or daily.std() == 0:
        return float("nan")
    return float(daily.mean() / daily.std() * math.sqrt(len(daily)))


def taker_scan(quotes: pd.DataFrame, win_col: str, thresholds: list[float]) -> pd.DataFrame:
    rows = []
    q = quotes.copy()
    q["model_ev"] = q[win_col] - q["best_ask"]
    for thr in thresholds:
        for d_max, tag in ((1, "d1"), (3, "d1-3")):
            for name, mask in (
                ("selected", (q["model_ev"] >= thr) & (q["distance"] <= d_max)),
                ("rejected", (q["model_ev"] < thr) & (q["distance"] <= d_max)),
            ):
                sub = q[mask]
                if sub.empty:
                    rows.append({"ev_thr": thr, "distances": tag, "set": name, "trades": 0})
                    continue
                u = sub.sort_values("decision_hour_local").drop_duplicates(
                    subset=["city", "target_date", "bracket"], keep="first"
                )
                daily = u.groupby("target_date")["pnl"].sum()
                rows.append({
                    "ev_thr": thr, "distances": tag, "set": name,
                    "trades": len(u), "cities": u["city"].nunique(), "days": len(daily),
                    "pos_days": int((daily > 0).sum()),
                    "avg_ask": round(float(u["best_ask"].mean()), 3),
                    "avg_model_win": round(float(u[win_col].mean()), 3),
                    "win_rate": round(float(1 - u["lose"].mean()), 3),
                    "cost": round(float(u["best_ask"].sum()), 2),
                    "pnl": round(float(u["pnl"].sum()), 2),
                    "roi": round(float(u["pnl"].sum() / u["best_ask"].sum()), 4),
                    "daily_tstat": round(float(daily.mean() / daily.std() * math.sqrt(len(daily)))
                                         if len(daily) > 1 and daily.std() > 0 else float("nan"), 2),
                })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--rebuild-features", action="store_true")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    IEM_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    quotes = pd.read_csv(QUOTES_CSV)
    quotes = quotes[quotes["group"].eq("whitelist")].copy()
    whitelist = sorted(quotes["city"].unique())

    detail = pd.read_csv(DETAIL_CSV)
    detail = detail[detail["city"].isin(whitelist)].copy()
    detail = detail.dropna(subset=["running_max_c", "final_max_c", "current_temp_c", "running_max_f", "final_max_f", "current_temp_f"])
    city_meta = detail[["city", "icao", "timezone"]].drop_duplicates().sort_values("city")
    assert len(city_meta) == len(whitelist), "city meta mismatch"

    # ---- 1. fetch IEM extended obs
    iem_status = {}
    if not args.skip_fetch:
        for r in city_meta.itertuples():
            try:
                t0 = time.time()
                path = fetch_iem_ext(r.icao)
                iem_status[r.icao] = f"ok ({path.stat().st_size//1024} KB, {time.time()-t0:.1f}s)"
            except Exception as e:  # noqa: BLE001 - explicit failure, recorded per station
                iem_status[r.icao] = f"FAILED: {e}"
            print(f"  IEM {r.city:<14} {r.icao}: {iem_status[r.icao]}", flush=True)

    # ---- 2. features
    if FEATURE_CACHE.exists() and not args.rebuild_features:
        feats = pd.read_csv(FEATURE_CACHE)
        print(f"loaded cached features: {len(feats)} rows")
    else:
        print("building features ...")
        feats = build_features(detail, city_meta)
        FEATURE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        feats.to_csv(FEATURE_CACHE, index=False, compression="gzip")
        print(f"features built: {len(feats)} rows -> {FEATURE_CACHE}")

    # sanity: wu-recomputed running max vs detail
    chk = feats["wu_running_max_check"].dropna()
    print(f"running-max recompute mismatch >0.1: {(chk > 0.1).mean():.4%} of {len(chk)}")

    train = feats[feats["target_date"] < TRAIN_END].copy()
    test = feats[feats["target_date"] >= TRAIN_END].copy()
    print(f"train {len(train)} rows (< {TRAIN_END}), eval {len(test)} rows")

    # feature coverage report (explicit, no silent fills)
    coverage = feats[FEATURES_NUM].notna().mean().round(4)
    coverage.to_csv(OUT_DIR / "feature_coverage.csv", header=["non_null_frac"])

    # ---- 3. models
    print("fitting v0 baseline ...")
    v0 = V0Baseline(train)
    print("fitting v1 ...")
    bundle = fit_v1(train)

    p1_test = predict_v1(bundle, test)
    test = pd.concat([test.reset_index(drop=True), p1_test.reset_index(drop=True)], axis=1)
    for d in JUMP_THRESHOLDS:
        test[f"v0_ge{d}"] = v0.predict(test, "ge", d)

    # ---- 4. OOS calibration: P(jump >= 1)
    y1 = (test["jump_b"] >= 1).to_numpy(dtype=int)
    bins = [0, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 1.0]
    rel_v1 = reliability_table(y1, test["p_ge1"].to_numpy(), bins)
    rel_v0 = reliability_table(y1, test["v0_ge1"].to_numpy(), bins)
    rel_v1["model"], rel_v0["model"] = "v1", "v0"
    rel = pd.concat([rel_v0, rel_v1], ignore_index=True)
    rel.to_csv(OUT_DIR / "reliability_jump_ge1_oos.csv", index=False)

    summary_rows = []
    marg = np.full(len(test), (train["jump_b"] >= 1).mean())
    for name, p in (("marginal", marg), ("v0", test["v0_ge1"].to_numpy()), ("v1", test["p_ge1"].to_numpy())):
        b, ll = brier_logloss(y1, p)
        summary_rows.append({"target": "jump_ge1", "model": name, "brier": round(b, 5), "logloss": round(ll, 5)})
    for d in (2, 3):
        yd = (test["jump_b"] >= d).to_numpy(dtype=int)
        for name, p in (("v0", test[f"v0_ge{d}"].to_numpy()), ("v1", test[f"p_ge{d}"].to_numpy())):
            b, ll = brier_logloss(yd, p)
            summary_rows.append({"target": f"jump_ge{d}", "model": name, "brier": round(b, 5), "logloss": round(ll, 5)})
    tstat = daily_brier_tstat(test["target_date"].to_numpy(), y1, test["p_ge1"].to_numpy(), test["v0_ge1"].to_numpy())
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(OUT_DIR / "model_eval_summary.csv", index=False)
    print(summary.to_string(index=False))
    print(f"daily-clustered t-stat (v1 brier - v0 brier, jump_ge1): {tstat:.2f}")

    # train-window OOS-style calibration is not reported; eval window only.

    # ---- 5. feature importance (report-only, on eval window, d>=1 raw model)
    from sklearn.inspection import permutation_importance
    m1 = bundle["models"][1][0]
    Xe = test[FEATURES_NUM + FEATURES_CAT].copy()
    Xe["city"] = Xe["city"].astype("category")
    pi = permutation_importance(m1, Xe, y1, scoring="neg_log_loss", n_repeats=5, random_state=SEED)
    imp = pd.DataFrame({"feature": FEATURES_NUM + FEATURES_CAT,
                        "importance_mean": pi.importances_mean, "importance_std": pi.importances_std})
    imp = imp.sort_values("importance_mean", ascending=False)
    imp.to_csv(OUT_DIR / "feature_importance_oos.csv", index=False)
    print(imp.to_string(index=False))

    # ---- 6. quote scoring + taker scan
    feat_keyed = feats.set_index(["city", "target_date", "decision_hour_local"])
    q = quotes.copy()
    q["is_plus"] = q["bracket"].astype(str).str.endswith("+")
    # true band distance (unclipped): bracket_low in native units
    def true_dist_b(r) -> float:
        rv = r["running_max_f"] if r["unit"] == "F" else r["running_max_c"]
        if pd.isna(r["bracket_low"]) or pd.isna(rv):
            return np.nan
        is_f = r["unit"] == "F"
        low_bucket = int(r["bracket_low"]) // 2 if is_f else int(r["bracket_low"])
        return low_bucket - bucket_value(rv, is_f)
    q["dist_b"] = q.apply(true_dist_b, axis=1)

    keys = list(zip(q["city"], q["target_date"], q["decision_hour_local"]))
    in_feats = [k in feat_keyed.index for k in keys]
    n_missing = len(q) - sum(in_feats)
    q = q[pd.Series(in_feats, index=q.index)].copy()
    fq = feat_keyed.loc[list(zip(q["city"], q["target_date"], q["decision_hour_local"]))].reset_index()
    pq = predict_v1(bundle, fq)
    for d in JUMP_THRESHOLDS:
        fq[f"v0_ge{d}"] = v0.predict(fq, "ge", d)
        if d <= 3:
            fq[f"v0_eq{d}"] = v0.predict(fq, "eq", d)
    q = q.reset_index(drop=True)
    pq = pq.reset_index(drop=True)
    fq = fq.reset_index(drop=True)

    def lose_v1(i):
        return p_lose_quote(pq.iloc[i], int(q.at[i, "dist_b"]), bool(q.at[i, "is_plus"]))

    def lose_v0(i):
        d = int(q.at[i, "dist_b"])
        if q.at[i, "is_plus"]:
            return float(fq.at[i, f"v0_ge{min(d,4)}"])
        return float(fq.at[i, f"v0_eq{d}"]) if d <= 3 else float(fq.at[i, "v0_ge4"])

    q["v1_p_lose"] = [lose_v1(i) for i in range(len(q))]
    q["v0_p_lose"] = [lose_v0(i) for i in range(len(q))]
    q["v1_win"] = 1.0 - q["v1_p_lose"]
    q["v0_win"] = 1.0 - q["v0_p_lose"]
    bad = q["v0_p_lose"].isna() | q["v1_p_lose"].isna()
    print(f"quotes scored: {len(q)} (dropped {n_missing} without features, {bad.sum()} without v0 cell)")
    q = q[~bad].copy()

    thresholds = [0.0, 0.02, 0.05]
    res_v1 = taker_scan(q, "v1_win", thresholds)
    res_v0 = taker_scan(q, "v0_win", thresholds)
    res_v1["model"], res_v0["model"] = "v1", "v0"
    taker = pd.concat([res_v1, res_v0], ignore_index=True)
    taker.to_csv(OUT_DIR / "taker_selection_results.csv", index=False)
    print(taker[taker["set"] == "selected"].to_string(index=False))

    # quote-level calibration vs settlement (dedup first hour per city/day/bracket)
    qu = q.sort_values("decision_hour_local").drop_duplicates(subset=["city", "target_date", "bracket"], keep="first")
    y_lose = qu["lose"].astype(int).to_numpy()
    qbins = [0, 0.02, 0.05, 0.10, 0.20, 0.50, 1.0]
    qr_v1 = reliability_table(y_lose, qu["v1_p_lose"].to_numpy(), qbins)
    qr_v0 = reliability_table(y_lose, qu["v0_p_lose"].to_numpy(), qbins)
    qr_v1["model"], qr_v0["model"] = "v1", "v0"
    qrel = pd.concat([qr_v0, qr_v1], ignore_index=True)
    qrel.to_csv(OUT_DIR / "quote_reliability_oos.csv", index=False)
    qb_v1 = brier_logloss(y_lose, qu["v1_p_lose"].to_numpy())
    qb_v0 = brier_logloss(y_lose, qu["v0_p_lose"].to_numpy())
    print("quote-level (dedup) brier/logloss v1:", qb_v1, "v0:", qb_v0)

    q.to_csv(OUT_DIR / "scored_quotes.csv", index=False)

    manifest = {
        "experiment": "m3_jump_model_v1",
        "train_end_exclusive": TRAIN_END,
        "train_rows": int(len(train)),
        "eval_rows": int(len(test)),
        "whitelist_cities": whitelist,
        "label": "jump_b = market-band bucket(final_max) - bucket(running_max); C: 1C round-half-up buckets, F: even-aligned 2F bands",
        "model": {"type": "HistGradientBoostingClassifier x4 (jump_b>=1..4) + OOF isotonic (GroupKFold by date)",
                  "params": bundle["params"], "seed": SEED},
        "features_numeric": FEATURES_NUM,
        "features_categorical": FEATURES_CAT,
        "iem": {"start": IEM_START, "end_exclusive": IEM_END, "cols": IEM_COLS, "stations": iem_status},
        "v0_baseline": "(city,hour,C-decline-bucket) freq + shrink k=50, re-fit on jump_b labels (strongest fair version)",
        "quote_scoring": {
            "regular": "p_lose = P(jump_b == dist_b) for dist_b<=3, P(jump_b>=4) upper bound beyond",
            "plus": "p_lose = P(jump_b >= min(dist_b,4))",
        },
        "ecmwf": "not used: continuous non-vintage series, ends 2026-04-29 (before eval window)",
        "daily_brier_tstat_v1_minus_v0": round(tstat, 3),
        "quote_dedup_brier": {"v1": round(qb_v1[0], 5), "v0": round(qb_v0[0], 5)},
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {OUT_DIR}/manifest.json")


if __name__ == "__main__":
    main()
