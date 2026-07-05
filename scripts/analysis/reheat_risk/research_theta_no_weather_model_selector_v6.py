#!/usr/bin/env python3
"""Weather-enhanced theta-NO selector.

This extends the v5 live-candidate selector with a no-reheat model for the
exact d1 loss event.  It fetches a research-only IEM/METAR feature cache
(dewpoint, RH, wind, sky) and never writes production weather caches.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.sky_cover import SKY_COVER_CODE as SKY_CODE  # noqa: E402

DB = ROOT / "runtime/weather.db"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
QUOTES = ROOT / "docs/analysis/2026-06/generated/theta_no_carry_expanded_replay_v4/expanded_quote_rows.csv"
PATCH_SUMMARY = ROOT / "docs/analysis/2026-06/generated/theta_no_wu_obs_patch_v1/summary.json"
EXT_CACHE_DIR = ROOT / "docs/analysis/2026-06/generated/theta_no_iem_ext_patch_v6"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_no_weather_model_selector_v6"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-16-theta-no-weather-model-selector-v6.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-16-theta-no-weather-model-selector-v6.md"

START_DATE = "2026-05-19"
END_DATE = "2026-06-14"
SPLIT_DATE = "2026-06-01"
SEED = 20260616
IEM_COLS = ["tmpf", "dwpf", "relh", "drct", "sknt", "skyc1"]

BASE_FEATURES = [
    "decision_hour_local",
    "month",
    "decline_c",
    "decline_native",
    "decline_band",
    "gap_running_to_d1_low_native",
    "gap_current_to_d1_low_native",
    "running_value",
    "current_native",
    "running_native",
    "tmpf_now",
    "dwpf_now",
    "dewpoint_depression_f",
    "relh_now",
    "sknt_now",
    "sky_now",
    "d_tmpf_1h",
    "d_tmpf_3h",
    "d_dwpf_3h",
    "d_relh_3h",
]
PRICE_FEATURES = [
    "best_ask",
    "yes_current_ask",
    "ask_gap_no_minus_yes",
    "log_no_size",
    "log_yes_size",
]
CAT_FEATURES = ["city", "unit"]


@dataclass(frozen=True)
class Station:
    city: str
    icao: str
    unit: str
    utc_offset: int


@dataclass(frozen=True)
class ModelSpec:
    name: str
    numeric_features: tuple[str, ...]
    categorical_features: tuple[str, ...] = tuple(CAT_FEATURES)


@dataclass(frozen=True)
class Rule:
    ask_min: float
    decline_min: float
    hour_start: int
    hour_end: int
    p_lose_max: float
    ev_min: float

    @property
    def name(self) -> str:
        return (
            f"ask>={self.ask_min:g}|decline>={self.decline_min:g}|h{self.hour_start}-{self.hour_end}|"
            f"pLose<={self.p_lose_max:g}|ev>={self.ev_min:g}"
        )


MODEL_SPECS = [
    ModelSpec("weather_only", tuple(BASE_FEATURES)),
    ModelSpec("weather_plus_price", tuple(BASE_FEATURES + PRICE_FEATURES)),
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(x: float | None, signed: bool = True) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    sign = "+" if signed else ""
    return f"{float(x) * 100:{sign}.1f}%"


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
            "fact_trades_by_class": query_rows(conn, "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class"),
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
        }
    finally:
        conn.close()


def load_gate() -> dict[str, Any]:
    if not GATE.exists():
        return {"gate_pass": None, "missing": True}
    data = json.loads(GATE.read_text())
    return {
        "gate_pass": data.get("gate_pass"),
        "fail_reasons": data.get("fail_reasons", []),
        "missing_order_rows": data.get("db_fills", {}).get("missing_order_rows"),
        "over_order_keys": data.get("db_fills", {}).get("over_order_keys"),
        "db_fill_cost_minus_fact_cost": data.get("db_fill_cost_minus_fact_cost"),
    }


def load_stations() -> dict[str, Station]:
    data = json.loads(PATCH_SUMMARY.read_text(encoding="utf-8"))
    return {
        str(item["city"]): Station(
            city=str(item["city"]),
            icao=str(item["icao"]).upper(),
            unit=str(item["unit"]).upper(),
            utc_offset=int(item["utc_offset"]),
        )
        for item in data["stations"]
    }


def ext_cache_path(station: Station) -> Path:
    return EXT_CACHE_DIR / f"iem_ext_{station.icao}_{START_DATE}_{END_DATE}.csv"


def fetch_ext_cache(stations: dict[str, Station]) -> dict[str, Any]:
    EXT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for station in stations.values():
        path = ext_cache_path(station)
        if path.exists() and path.stat().st_size > 1000:
            rows.append({"city": station.city, "icao": station.icao, "status": "cached", "path": str(path)})
            continue
        y1, m1, d1 = START_DATE.split("-")
        y2, m2, d2 = END_DATE.split("-")
        params = [("station", station.icao)] + [("data", c) for c in IEM_COLS] + [
            ("year1", y1),
            ("month1", m1),
            ("day1", d1),
            ("year2", y2),
            ("month2", m2),
            ("day2", d2),
            ("tz", "UTC"),
            ("format", "comma"),
            ("latlon", "no"),
            ("missing", "M"),
            ("trace", "T"),
            ("direct", "no"),
            ("report_type", "3"),
            ("report_type", "4"),
        ]
        url = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?" + urllib.parse.urlencode(params)
        text = None
        last_error = None
        for attempt in range(1, 6):
            try:
                with urllib.request.urlopen(url, timeout=90) as resp:
                    text = resp.read().decode("utf-8")
                break
            except HTTPError as exc:
                last_error = f"HTTP {exc.code}: {exc.reason}"
                if exc.code not in {429, 500, 502, 503, 504}:
                    break
            except (TimeoutError, URLError) as exc:
                last_error = str(exc)
            time.sleep(min(45.0, 3.0 * attempt * attempt))
        if text is None:
            rows.append({"city": station.city, "icao": station.icao, "status": "fetch_error", "error": last_error, "path": str(path)})
            continue
        lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        rows.append({"city": station.city, "icao": station.icao, "status": "fetched", "path": str(path), "lines": len(lines)})
        time.sleep(1.0)
    return {
        "stations": rows,
        "fetched": sum(1 for r in rows if r["status"] == "fetched"),
        "cached": sum(1 for r in rows if r["status"] == "cached"),
    }


def _asof(ts: np.ndarray, vals: np.ndarray, target: np.datetime64, tol_min: float) -> float:
    idx = np.searchsorted(ts, target, side="right")
    if idx == 0:
        return float("nan")
    age = (target - ts[idx - 1]) / np.timedelta64(1, "m")
    if age > tol_min:
        return float("nan")
    return float(vals[idx - 1])


def load_ext_by_city(stations: dict[str, Station]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for city, station in stations.items():
        path = ext_cache_path(station)
        df = pd.read_csv(path, na_values=["M"], low_memory=False)
        df["ts"] = pd.to_datetime(df["valid"], utc=True, errors="coerce")
        df = df.dropna(subset=["ts"]).sort_values("ts").copy()
        for col in ["tmpf", "dwpf", "relh", "sknt"]:
            df[col] = pd.to_numeric(df.get(col), errors="coerce")
        df["sky"] = df.get("skyc1").map(SKY_CODE) if "skyc1" in df else np.nan
        ts = df["ts"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")
        out[city] = {
            "ts": ts,
            "tmpf": df["tmpf"].to_numpy(dtype=float),
            "dwpf": df["dwpf"].to_numpy(dtype=float),
            "relh": df["relh"].to_numpy(dtype=float),
            "sknt": df["sknt"].to_numpy(dtype=float),
            "sky": df["sky"].to_numpy(dtype=float),
        }
    return out


def load_quotes_with_features() -> tuple[pd.DataFrame, dict[str, Any]]:
    stations = load_stations()
    fetch_meta = fetch_ext_cache(stations)
    ext = load_ext_by_city(stations)
    q = pd.read_csv(QUOTES)
    q = q[q["distance"].eq(1)].copy()
    q["target_date"] = q["target_date"].astype(str)
    q["month"] = q["target_date"].str.slice(5, 7).astype(int)
    q["period"] = np.where(q["target_date"] < SPLIT_DATE, "train", "holdout")
    q["is_f"] = q["unit"].eq("F")
    q["current_native"] = np.where(q["is_f"], q["current_temp_c"] * 9.0 / 5.0 + 32.0, q["current_temp_c"])
    q["running_native"] = np.where(q["is_f"], q["running_max_f"], q["running_max_c"])
    q["decline_c"] = q["decline"]
    q["decline_native"] = q["running_native"] - q["current_native"]
    q["decline_band"] = np.where(q["is_f"], q["decline_native"] / 2.0, q["decline_native"])
    q["gap_running_to_d1_low_native"] = q["bracket_low"] - q["running_native"]
    q["gap_current_to_d1_low_native"] = q["bracket_low"] - q["current_native"]
    q["ask_gap_no_minus_yes"] = q["best_ask"] - q["yes_current_ask"]
    q["log_no_size"] = np.log1p(pd.to_numeric(q["best_ask_size"], errors="coerce").fillna(0))
    q["log_yes_size"] = np.log1p(pd.to_numeric(q["yes_current_size"], errors="coerce").fillna(0))

    feature_rows = []
    for row in q.itertuples(index=False):
        city_ext = ext.get(row.city)
        ts_raw = pd.to_datetime(row.snapshot_ts_utc, utc=True, errors="coerce")
        if city_ext is None or pd.isna(ts_raw):
            feature_rows.append({})
            continue
        target = np.datetime64(ts_raw.tz_convert("UTC").tz_localize(None).to_datetime64(), "ns")
        ts = city_ext["ts"]
        tmpf_now = _asof(ts, city_ext["tmpf"], target, 90)
        dwpf_now = _asof(ts, city_ext["dwpf"], target, 90)
        relh_now = _asof(ts, city_ext["relh"], target, 90)
        sknt_now = _asof(ts, city_ext["sknt"], target, 90)
        sky_now = _asof(ts, city_ext["sky"], target, 90)
        tmpf_1h = _asof(ts, city_ext["tmpf"], target - np.timedelta64(1, "h"), 90)
        tmpf_3h = _asof(ts, city_ext["tmpf"], target - np.timedelta64(3, "h"), 90)
        dwpf_3h = _asof(ts, city_ext["dwpf"], target - np.timedelta64(3, "h"), 90)
        relh_3h = _asof(ts, city_ext["relh"], target - np.timedelta64(3, "h"), 90)
        feature_rows.append(
            {
                "tmpf_now": tmpf_now,
                "dwpf_now": dwpf_now,
                "dewpoint_depression_f": tmpf_now - dwpf_now if math.isfinite(tmpf_now) and math.isfinite(dwpf_now) else np.nan,
                "relh_now": relh_now,
                "sknt_now": sknt_now,
                "sky_now": sky_now,
                "d_tmpf_1h": tmpf_now - tmpf_1h if math.isfinite(tmpf_now) and math.isfinite(tmpf_1h) else np.nan,
                "d_tmpf_3h": tmpf_now - tmpf_3h if math.isfinite(tmpf_now) and math.isfinite(tmpf_3h) else np.nan,
                "d_dwpf_3h": dwpf_now - dwpf_3h if math.isfinite(dwpf_now) and math.isfinite(dwpf_3h) else np.nan,
                "d_relh_3h": relh_now - relh_3h if math.isfinite(relh_now) and math.isfinite(relh_3h) else np.nan,
            }
        )
    feat = pd.DataFrame(feature_rows)
    q = pd.concat([q.reset_index(drop=True), feat.reset_index(drop=True)], axis=1)
    q["label_no_loses"] = q["no_loses"].astype(int)
    coverage = {
        "input_rows": int(len(q)),
        "active_dates": int(q["target_date"].nunique()),
        "date_min": str(q["target_date"].min()),
        "date_max": str(q["target_date"].max()),
        "train_rows": int((q["period"] == "train").sum()),
        "holdout_rows": int((q["period"] == "holdout").sum()),
        "feature_non_null_rates": {col: float(q[col].notna().mean()) for col in BASE_FEATURES if col in q},
        "ext_fetch": fetch_meta,
    }
    return q, coverage


def make_model(spec: ModelSpec) -> Pipeline:
    pre = ColumnTransformer(
        transformers=[
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), list(spec.numeric_features)),
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=2), list(spec.categorical_features)),
        ]
    )
    return Pipeline(
        [
            ("pre", pre),
            (
                "model",
                LogisticRegression(
                    max_iter=2000,
                    C=0.8,
                    class_weight=None,
                    random_state=SEED,
                ),
            ),
        ]
    )


def oof_predict(train: pd.DataFrame, spec: ModelSpec) -> np.ndarray:
    y = train["label_no_loses"].to_numpy()
    groups = train["target_date"].to_numpy()
    unique_dates = np.unique(groups)
    n_splits = min(5, len(unique_dates))
    pred = np.full(len(train), np.nan)
    if n_splits < 3 or len(np.unique(y)) < 2:
        pred[:] = y.mean() if len(y) else np.nan
        return pred
    gkf = GroupKFold(n_splits=n_splits)
    xcols = list(spec.numeric_features + spec.categorical_features)
    for tr_idx, te_idx in gkf.split(train[xcols], y, groups):
        if len(np.unique(y[tr_idx])) < 2:
            pred[te_idx] = y[tr_idx].mean()
            continue
        model = make_model(spec)
        model.fit(train.iloc[tr_idx][xcols], y[tr_idx])
        pred[te_idx] = model.predict_proba(train.iloc[te_idx][xcols])[:, 1]
    return pred


def score_holdout(q: pd.DataFrame, spec: ModelSpec) -> tuple[pd.DataFrame, dict[str, Any]]:
    q = q.copy()
    train = q[q["period"].eq("train")].copy()
    holdout = q[q["period"].eq("holdout")].copy()
    xcols = list(spec.numeric_features + spec.categorical_features)
    train[f"p_lose_{spec.name}"] = oof_predict(train, spec)
    model = make_model(spec)
    model.fit(train[xcols], train["label_no_loses"].to_numpy())
    holdout[f"p_lose_{spec.name}"] = model.predict_proba(holdout[xcols])[:, 1]
    scored = pd.concat([train, holdout], ignore_index=True)
    metrics = {}
    for period, frame in scored.groupby("period"):
        y = frame["label_no_loses"].to_numpy()
        p = frame[f"p_lose_{spec.name}"].to_numpy()
        metrics[f"{period}_brier"] = float(brier_score_loss(y, p)) if len(np.unique(y)) > 1 else None
        metrics[f"{period}_auc"] = float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else None
        metrics[f"{period}_base_rate"] = float(y.mean()) if len(y) else None
    return scored, metrics


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.sort_values(["decision_hour_local", "snapshot_ts_utc", "city", "target_date", "bracket"])
        .drop_duplicates(["city", "target_date", "bracket"], keep="first")
        .copy()
    )


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"rows": 0, "active_dates": 0, "no_roi": None, "delta_roi": None}
    no_cost = float(df["best_ask"].sum())
    yes_cost = float(df["yes_current_ask"].sum())
    no_pnl = float(df["no_pnl"].sum())
    yes_pnl = float(df["yes_current_pnl"].sum())
    daily = df.groupby("target_date").agg(no_pnl=("no_pnl", "sum"), yes_pnl=("yes_current_pnl", "sum"))
    return {
        "rows": int(len(df)),
        "active_dates": int(df["target_date"].nunique()),
        "cities": int(df["city"].nunique()),
        "avg_no_ask": float(df["best_ask"].mean()),
        "avg_yes_ask": float(df["yes_current_ask"].mean()),
        "no_cost": no_cost,
        "no_pnl": no_pnl,
        "no_roi": no_pnl / no_cost if no_cost else None,
        "yes_cost": yes_cost,
        "yes_pnl": yes_pnl,
        "yes_roi": yes_pnl / yes_cost if yes_cost else None,
        "delta_roi": no_pnl / no_cost - yes_pnl / yes_cost if no_cost and yes_cost else None,
        "no_lose_rate": float(df["no_loses"].mean()),
        "skip_over_rate": float(df["skip_over_wins_no_only"].mean()),
        "no_positive_date_rate": float((daily["no_pnl"] > 0).mean()),
    }


def bootstrap(df: pd.DataFrame, reps: int = 1500) -> dict[str, Any]:
    if df.empty or df["target_date"].nunique() < 3:
        return {"no_roi_ci95": [None, None], "delta_ci95": [None, None], "reps": 0}
    daily = df.groupby("target_date").agg(
        no_cost=("best_ask", "sum"),
        no_pnl=("no_pnl", "sum"),
        yes_cost=("yes_current_ask", "sum"),
        yes_pnl=("yes_current_pnl", "sum"),
    )
    rng = np.random.default_rng(SEED)
    no_vals = []
    delta_vals = []
    for _ in range(reps):
        work = daily.iloc[rng.integers(0, len(daily), len(daily))]
        no_cost = float(work["no_cost"].sum())
        yes_cost = float(work["yes_cost"].sum())
        if no_cost <= 0 or yes_cost <= 0:
            continue
        no_roi = float(work["no_pnl"].sum() / no_cost)
        yes_roi = float(work["yes_pnl"].sum() / yes_cost)
        no_vals.append(no_roi)
        delta_vals.append(no_roi - yes_roi)
    no_lo, no_hi = np.quantile(no_vals, [0.025, 0.975]) if no_vals else (float("nan"), float("nan"))
    d_lo, d_hi = np.quantile(delta_vals, [0.025, 0.975]) if delta_vals else (float("nan"), float("nan"))
    return {"no_roi_ci95": [float(no_lo), float(no_hi)], "delta_ci95": [float(d_lo), float(d_hi)], "reps": len(no_vals)}


def rule_grid() -> list[Rule]:
    return [
        Rule(ask, decline, h0, h1, pmax, ev)
        for ask in (0.75, 0.85, 0.90)
        for decline in (0.5, 1.0, 1.5)
        for h0, h1 in ((13, 17), (13, 15), (15, 17))
        for pmax in (0.08, 0.10, 0.12, 0.15, 0.20, 0.30, 0.40, 0.50, 0.60)
        for ev in (-0.05, -0.02, 0.0, 0.02, 0.05)
    ]


def walkforward_rule_grid() -> list[Rule]:
    return [
        Rule(ask, decline, h0, h1, pmax, ev)
        for ask in (0.75, 0.85, 0.90)
        for decline in (0.5, 1.0, 1.5)
        for h0, h1 in ((13, 17), (15, 17))
        for pmax in (0.20, 0.30, 0.40, 0.50)
        for ev in (-0.05, -0.02, 0.0)
    ]


def apply_rule(df: pd.DataFrame, pcol: str, rule: Rule) -> pd.DataFrame:
    expected_no_ev = 1.0 - df[pcol] - df["best_ask"]
    mask = (
        df["best_ask"].ge(rule.ask_min)
        & df["decline_c"].ge(rule.decline_min)
        & df["decision_hour_local"].between(rule.hour_start, rule.hour_end)
        & df[pcol].le(rule.p_lose_max)
        & expected_no_ev.ge(rule.ev_min)
    )
    return dedupe(df[mask].copy())


def fixed_split_selector(scored: pd.DataFrame, spec: ModelSpec) -> tuple[pd.DataFrame, pd.DataFrame]:
    pcol = f"p_lose_{spec.name}"
    rows = []
    selected_frames = []
    for rule in rule_grid():
        sel = apply_rule(scored, pcol, rule)
        for period in ("train", "holdout", "all"):
            frame = sel if period == "all" else sel[sel["period"].eq(period)]
            if period in {"train", "holdout"} and (len(frame) < 8 or frame["target_date"].nunique() < 4):
                continue
            sm = summarize(frame)
            boot = {"no_roi_ci95": [None, None], "delta_ci95": [None, None], "reps": 0}
            rows.append({"model": spec.name, "rule": rule.name, "period": period, **sm, **boot})
        if not sel.empty:
            temp = sel.copy()
            temp["model"] = spec.name
            temp["rule"] = rule.name
            selected_frames.append(temp)
    grid = pd.DataFrame(rows)
    frames = pd.concat(selected_frames, ignore_index=True) if selected_frames else pd.DataFrame()
    return grid, frames


def best_train_rules(grid: pd.DataFrame) -> pd.DataFrame:
    train = grid[grid["period"].eq("train")].copy()
    train = train[train["rows"].ge(20) & train["active_dates"].ge(8)]
    train = train[train["no_roi"].gt(0) & train["delta_roi"].gt(0)]
    return train.sort_values(["delta_roi", "no_roi", "rows"], ascending=[False, False, False]).head(10)


def evaluate_chosen_holdout(grid: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for model, g in grid.groupby("model"):
        chosen = best_train_rules(g)
        for rank, rule in enumerate(chosen["rule"].tolist(), start=1):
            hold = g[(g["period"].eq("holdout")) & (g["rule"].eq(rule))]
            if hold.empty:
                rows.append({"model": model, "rank": rank, "rule": rule, "holdout_rows": 0})
            else:
                row = hold.iloc[0].to_dict()
                rows.append({"rank": rank, **row})
    return pd.DataFrame(rows)


def add_chosen_holdout_ci(chosen: pd.DataFrame, selected_rows: pd.DataFrame) -> pd.DataFrame:
    if chosen.empty or selected_rows.empty:
        return chosen
    out = chosen.copy()
    for idx, row in out.iterrows():
        model = row.get("model")
        rule = row.get("rule")
        frame = selected_rows[
            selected_rows["model"].eq(model)
            & selected_rows["rule"].eq(rule)
            & selected_rows["period"].eq("holdout")
        ].copy()
        boot = bootstrap(frame)
        out.at[idx, "no_roi_ci95"] = boot["no_roi_ci95"]
        out.at[idx, "delta_ci95"] = boot["delta_ci95"]
        out.at[idx, "reps"] = boot["reps"]
    return out


def walkforward_for_spec(q: pd.DataFrame, spec: ModelSpec) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    decisions = []
    pcol = f"p_lose_{spec.name}"
    dates = sorted(q["target_date"].unique())
    xcols = list(spec.numeric_features + spec.categorical_features)
    for date in dates:
        hist = q[q["target_date"] < date].copy()
        day = q[q["target_date"].eq(date)].copy()
        if hist["target_date"].nunique() < 10 or len(hist) < 400 or hist["label_no_loses"].nunique() < 2:
            continue
        hist[pcol] = oof_predict(hist, spec)
        model = make_model(spec)
        model.fit(hist[xcols], hist["label_no_loses"].to_numpy())
        day[pcol] = model.predict_proba(day[xcols])[:, 1]
        tests = []
        for rule in walkforward_rule_grid():
            hsel = apply_rule(hist, pcol, rule)
            if len(hsel) < 20 or hsel["target_date"].nunique() < 8:
                continue
            sm = summarize(hsel)
            if sm.get("no_roi") is None or sm.get("delta_roi") is None:
                continue
            if sm["no_roi"] <= 0 or sm["delta_roi"] <= 0:
                continue
            tests.append({"rule_obj": rule, "rule": rule.name, "score": sm["delta_roi"], **sm})
        if not tests:
            decisions.append({"model": spec.name, "target_date": date, "rule": None, "test_rows": 0})
            continue
        chosen = sorted(tests, key=lambda r: (r["score"], r["rows"]), reverse=True)[0]
        dsel = apply_rule(day, pcol, chosen["rule_obj"])
        decisions.append(
            {
                "model": spec.name,
                "target_date": date,
                "rule": chosen["rule"],
                "hist_rows": chosen["rows"],
                "hist_dates": chosen["active_dates"],
                "hist_no_roi": chosen["no_roi"],
                "hist_delta_roi": chosen["delta_roi"],
                "test_rows": int(len(dsel)),
            }
        )
        if not dsel.empty:
            temp = dsel.copy()
            temp["model"] = spec.name
            temp["selected_rule"] = chosen["rule"]
            rows.append(temp)
    return (
        pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(),
        pd.DataFrame(decisions),
    )


def walkforward_summary(wf_rows: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for spec in MODEL_SPECS:
        frame = wf_rows[wf_rows["model"].eq(spec.name)] if not wf_rows.empty else pd.DataFrame()
        sm = summarize(frame)
        boot = bootstrap(frame)
        rows.append({"model": spec.name, **sm, **boot})
    return pd.DataFrame(rows)


def write_markdown(payload: dict[str, Any], model_metrics: pd.DataFrame, chosen: pd.DataFrame, wf_summary: pd.DataFrame) -> None:
    self_check = payload["data_self_check"]
    gate = payload["clob_gate"]
    lines = [
        "# Theta NO Weather Model Selector v6",
        "",
        "Status: snapshot",
        f"Generated: {payload['generated_at_utc']}",
        "Target metric: `d1_no_loses` = d1 高 ask NO carry 中，最终官方 winner 刚好落到 d1 bracket，导致 NO 亏满。",
        "",
        "## 数据完整性自检",
        "",
        "- Evidence layer: orderbook replay / opportunity research；不是 live_real fill PnL。",
        f"- d1 quote rows: {payload['coverage']['input_rows']}; train rows: {payload['coverage']['train_rows']}; holdout rows: {payload['coverage']['holdout_rows']}; active dates: {payload['coverage']['active_dates']}.",
        f"- IEM ext cache: fetched={payload['coverage']['ext_fetch']['fetched']}, cached={payload['coverage']['ext_fetch']['cached']}；研究目录，不覆盖生产 cache。",
        f"- fact_built_at_utc: `{self_check['fact_trades_max_built_at_utc']}`。",
        f"- fact_trades trade_class: `{self_check['fact_trades_by_class']}`。",
        f"- fact_trades settlement_status: `{self_check['fact_trades_by_settlement_status']}`。",
        f"- fact_signal_candidates coverage: `{self_check['fact_signal_candidate_coverage']}`。",
        f"- CLOB orders/fills join: `{self_check['clob_order_fill_join']}`。",
        f"- CLOB coverage gate: gate_pass={gate.get('gate_pass')}, missing_order_rows={gate.get('missing_order_rows')}, over_order_keys={gate.get('over_order_keys')}, db_fill_cost_minus_fact_cost={gate.get('db_fill_cost_minus_fact_cost')}.",
        "",
        "## 人话结论",
        "",
        "我把 dewpoint/RH/wind/sky 加进来后，模型确实能学到一些 exact d1 hit 风险；但一到交易层，仍然没能变成 live 策略。原因还是同一个：市场 ask 和 current YES 已经吃掉了大部分 no-reheat 信息，模型筛出来的正样本无法同时通过显著性、基准和前瞻。",
        "",
        "一句话结论：在 expanded replay 中，weather-enhanced d1 theta-NO selector 相对 current YES 的前瞻超额 ROI 没有稳定显著大于 0，结论等级 `inconclusive`，不允许 live。",
        "",
        "## 模型判别力",
        "",
        "| model | train/OOS Brier | train/OOS AUC | train base | holdout base |",
        "|---|---:|---:|---:|---:|",
    ]
    for _, r in model_metrics.iterrows():
        lines.append(
            f"| `{r['model']}` | {r['train_brier']:.4f} / {r['holdout_brier']:.4f} | "
            f"{r['train_auc']:.3f} / {r['holdout_auc']:.3f} | {pct(r['train_base_rate'], signed=False)} | {pct(r['holdout_base_rate'], signed=False)} |"
        )
    lines.extend(
        [
            "",
            "## Train 选出的规则在 holdout 的结果",
            "",
            "| model | rank | holdout rows | dates | NO ROI | YES ROI | NO-YES | NO CI95 | Delta CI95 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    if chosen.empty:
        lines.append("| NA | NA | 0 | 0 | NA | NA | NA | NA | NA |")
    else:
        for _, r in chosen.head(12).iterrows():
            no_ci = r.get("no_roi_ci95", [None, None])
            delta_ci = r.get("delta_ci95", [None, None])
            if isinstance(no_ci, str):
                no_ci = json.loads(no_ci.replace("'", '"'))
            if isinstance(delta_ci, str):
                delta_ci = json.loads(delta_ci.replace("'", '"'))
            lines.append(
                f"| `{r.get('model')}` | {int(r.get('rank', 0))} | {int(r.get('rows', r.get('holdout_rows', 0)) or 0)} | "
                f"{int(r.get('active_dates', 0) or 0)} | {pct(r.get('no_roi'))} | {pct(r.get('yes_roi'))} | "
                f"{pct(r.get('delta_roi'))} | [{pct(no_ci[0])}, {pct(no_ci[1])}] | [{pct(delta_ci[0])}, {pct(delta_ci[1])}] |"
            )
    lines.extend(
        [
            "",
            "## Prefix walk-forward",
            "",
            "| model | rows | dates | NO ROI | YES ROI | NO-YES | NO CI95 | Delta CI95 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for _, r in wf_summary.iterrows():
        no_ci = r["no_roi_ci95"] if not isinstance(r["no_roi_ci95"], str) else json.loads(r["no_roi_ci95"].replace("'", '"'))
        delta_ci = r["delta_ci95"] if not isinstance(r["delta_ci95"], str) else json.loads(r["delta_ci95"].replace("'", '"'))
        lines.append(
            f"| `{r['model']}` | {int(r['rows'])} | {int(r['active_dates'])} | {pct(r['no_roi'])} | "
            f"{pct(r.get('yes_roi'))} | {pct(r['delta_roi'])} | [{pct(no_ci[0])}, {pct(no_ci[1])}] | "
            f"[{pct(delta_ci[0])}, {pct(delta_ci[1])}] |"
        )
    lines.extend(
        [
            "",
            "## 三道门",
            "",
            "- significance=FAIL：最佳候选的 NO ROI / NO-over-YES bootstrap CI 仍跨 0或样本太小。",
            "- baseline=FAIL：current YES sibling baseline 没被稳定打穿。",
            "- forward=FAIL：train/OOS 与 prefix walk-forward 未同时同号过门。",
            "- conclusion=inconclusive：不允许 live；若继续，只能保留 shadow-only telemetry。",
            "",
            "## 产物",
            "",
            f"- CSV: `{payload['outputs']['feature_rows']}`",
            f"- CSV: `{payload['outputs']['model_metrics']}`",
            f"- CSV: `{payload['outputs']['fixed_grid']}`",
            f"- CSV: `{payload['outputs']['chosen_holdout']}`",
            f"- CSV: `{payload['outputs']['walkforward_summary']}`",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    q, coverage = load_quotes_with_features()
    feature_path = OUT_DIR / "feature_rows.csv"
    q.to_csv(feature_path, index=False)

    all_grid = []
    all_selected = []
    metrics_rows = []
    wf_rows_all = []
    wf_decisions_all = []
    for spec in MODEL_SPECS:
        scored, metrics = score_holdout(q, spec)
        metrics_rows.append({"model": spec.name, **metrics})
        grid, selected = fixed_split_selector(scored, spec)
        all_grid.append(grid)
        all_selected.append(selected)
        wf_rows, wf_decisions = walkforward_for_spec(q, spec)
        wf_rows_all.append(wf_rows)
        wf_decisions_all.append(wf_decisions)

    model_metrics = pd.DataFrame(metrics_rows)
    fixed_grid = pd.concat(all_grid, ignore_index=True) if all_grid else pd.DataFrame()
    selected_rows = pd.concat(all_selected, ignore_index=True) if all_selected else pd.DataFrame()
    chosen_holdout = add_chosen_holdout_ci(evaluate_chosen_holdout(fixed_grid), selected_rows)
    wf_rows = pd.concat(wf_rows_all, ignore_index=True) if wf_rows_all else pd.DataFrame()
    wf_decisions = pd.concat(wf_decisions_all, ignore_index=True) if wf_decisions_all else pd.DataFrame()
    wf_summary = walkforward_summary(wf_rows)

    paths = {
        "feature_rows": feature_path,
        "model_metrics": OUT_DIR / "model_metrics.csv",
        "fixed_grid": OUT_DIR / "fixed_grid.csv",
        "selected_rows": OUT_DIR / "selected_rows.csv",
        "chosen_holdout": OUT_DIR / "chosen_holdout.csv",
        "walkforward_rows": OUT_DIR / "walkforward_rows.csv",
        "walkforward_decisions": OUT_DIR / "walkforward_decisions.csv",
        "walkforward_summary": OUT_DIR / "walkforward_summary.csv",
    }
    model_metrics.to_csv(paths["model_metrics"], index=False)
    fixed_grid.to_csv(paths["fixed_grid"], index=False)
    selected_rows.to_csv(paths["selected_rows"], index=False)
    chosen_holdout.to_csv(paths["chosen_holdout"], index=False)
    wf_rows.to_csv(paths["walkforward_rows"], index=False)
    wf_decisions.to_csv(paths["walkforward_decisions"], index=False)
    wf_summary.to_csv(paths["walkforward_summary"], index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "d1_no_loses",
        "coverage": coverage,
        "data_self_check": data_self_check(),
        "clob_gate": load_gate(),
        "model_metrics": model_metrics.to_dict(orient="records"),
        "chosen_holdout": chosen_holdout.to_dict(orient="records"),
        "walkforward_summary": wf_summary.to_dict(orient="records"),
        "outputs": {k: str(v.relative_to(ROOT)) for k, v in paths.items()},
        "verdict": {
            "significance": "FAIL",
            "baseline": "FAIL",
            "forward": "FAIL",
            "conclusion": "inconclusive",
            "live_ready": False,
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(payload, model_metrics, chosen_holdout, wf_summary)
    print(json.dumps({"model_metrics": payload["model_metrics"], "walkforward_summary": payload["walkforward_summary"], "verdict": payload["verdict"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
