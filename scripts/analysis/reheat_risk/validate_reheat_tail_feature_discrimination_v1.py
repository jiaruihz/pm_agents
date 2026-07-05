#!/usr/bin/env python3
"""Validate current-YES tail weather features before adding them to a model.

Target metric: current_yes_survive_discrimination.  Row grain is one
city + target_date + decision snapshot.  The label is whether the current
running-max YES bracket survives; survive=0 means it is later broken by a
higher observed max.

The script intentionally tests candidates before promotion:
- P0 cheap forms: global wind sin/cos and bare d_sky_3h.
- P1 proper forms: d_sky_3h x solar altitude, and city-local wind direction.
- Pressure tendency is explicit blocked when theta_no_iem_ext_patch_v6 has no
  alti column; no proxy is used.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.sky_cover import SKY_COVER_CODE as SKY_CODE  # noqa: E402

FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/feature_rows.csv"
BASE_MODEL = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/live_model.json"
STATION_SUMMARY = ROOT / "docs/analysis/2026-06/generated/theta_no_wu_obs_patch_v1/summary.json"
EXT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_no_iem_ext_patch_v6"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_proper_form_tail_features_v1"
SEED = 20260620

LABEL = "current_yes_wins"

P0_CHEAP = ["wind_dir_sin", "wind_dir_cos", "d_sky_3h"]
P1_SOLAR = ["solar_altitude_deg", "solar_altitude_pos", "d_sky_3h_x_solar_altitude_pos"]
PRESENT_REF = ["d_tmpf_3h", "d_dwpf_3h", "d_relh_3h", "sky_now", "sknt_now", "decline_c"]
METAR_WEATHER = [
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

# Static coordinates for stations in theta_no_wu_obs_patch_v1/summary.json.
# airportsdata is not installed in this venv; fail hard if the station set drifts.
ICAO_COORDS = {
    "EHAM": (52.3086, 4.7639),
    "LTAC": (40.1281, 32.9950),
    "KATL": (33.6367, -84.4281),
    "KAUS": (30.1945, -97.6699),
    "ZBAA": (40.0801, 116.5846),
    "SAEZ": (-34.8222, -58.5358),
    "RKPK": (35.1795, 128.9382),
    "FACT": (-33.9694, 18.5972),
    "ZUUU": (30.5785, 103.9471),
    "ZUCK": (29.7192, 106.6417),
    "KDAL": (32.8471, -96.8518),
    "KBKF": (39.7017, -104.7517),
    "ZGGG": (23.3924, 113.2988),
    "EFHK": (60.3172, 24.9633),
    "KHOU": (29.6454, -95.2789),
    "LTFM": (41.2753, 28.7519),
    "OEJN": (21.6796, 39.1565),
    "OPKC": (24.9065, 67.1608),
    "VILK": (26.7606, 80.8893),
    "LEMD": (40.4983, -3.5676),
    "RPLL": (14.5086, 121.0198),
    "KMIA": (25.7959, -80.2870),
    "EDDM": (48.3538, 11.7861),
    "KLGA": (40.7769, -73.8740),
    "WSSS": (1.3592, 103.9894),
    "RCSS": (25.0697, 121.5525),
    "LLBG": (32.0114, 34.8867),
    "RJTT": (35.5494, 139.7798),
    "EPWA": (52.1657, 20.9671),
    "NZWN": (-41.3272, 174.8053),
    "ZHHH": (30.7838, 114.2081),
    "KLAX": (33.9425, -118.4081),
    "KSFO": (37.6190, -122.3749),
    "KSEA": (47.4490, -122.3093),
    "SBGR": (-23.4356, -46.4731),
    "ZSPD": (31.1434, 121.8052),
}


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    return value


def coerce_bool(s: pd.Series) -> pd.Series:
    return s.astype(str).str.lower().isin({"true", "1", "yes"}).astype(int)


def score_base_model(rows: pd.DataFrame, art: dict[str, Any]) -> np.ndarray:
    num_f, cat_f = list(art["numeric_features"]), list(art["categorical_features"])
    num = rows[num_f].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    num = np.where(np.isfinite(num), num, np.asarray(art["numeric_medians"], float))
    num = (num - np.asarray(art["numeric_means"], float)) / np.asarray(art["numeric_scales"], float)
    parts = [num]
    for i, f in enumerate(cat_f):
        cats = [str(x) for x in art["categories"][i]]
        mat = np.zeros((len(rows), len(cats)))
        lut = {c: j for j, c in enumerate(cats)}
        for r, v in enumerate(rows[f].astype(str).to_numpy()):
            j = lut.get(v)
            if j is not None:
                mat[r, j] = 1.0
        parts.append(mat)
    logit = np.hstack(parts) @ np.asarray(art["coef"], float) + float(art["intercept"])
    return 1.0 / (1.0 + np.exp(-logit))


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def station_maps() -> tuple[dict[str, str], dict[str, tuple[float, float]]]:
    stations = json.loads(STATION_SUMMARY.read_text(encoding="utf-8"))["stations"]
    city_by_icao = {s["icao"]: s["city"] for s in stations}
    missing = sorted(set(city_by_icao) - set(ICAO_COORDS))
    if missing:
        raise RuntimeError(f"Missing static ICAO coordinates for: {missing}")
    return city_by_icao, {s["city"]: ICAO_COORDS[s["icao"]] for s in stations}


def solar_altitude_deg(ts: pd.Series, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """NOAA-style solar elevation approximation for hour-level feature work."""
    dt = pd.to_datetime(ts, utc=True, errors="coerce")
    doy = dt.dt.dayofyear.to_numpy(float)
    hour = dt.dt.hour.to_numpy(float) + dt.dt.minute.to_numpy(float) / 60.0 + dt.dt.second.to_numpy(float) / 3600.0
    gamma = 2.0 * math.pi / 365.0 * (doy - 1.0 + (hour - 12.0) / 24.0)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * np.cos(gamma)
        - 0.032077 * np.sin(gamma)
        - 0.014615 * np.cos(2 * gamma)
        - 0.040849 * np.sin(2 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * np.cos(gamma)
        + 0.070257 * np.sin(gamma)
        - 0.006758 * np.cos(2 * gamma)
        + 0.000907 * np.sin(2 * gamma)
        - 0.002697 * np.cos(3 * gamma)
        + 0.00148 * np.sin(3 * gamma)
    )
    true_solar_time = (hour * 60.0 + eqtime + 4.0 * lon) % 1440.0
    hour_angle = np.deg2rad(true_solar_time / 4.0 - 180.0)
    lat_rad = np.deg2rad(lat)
    cos_zenith = np.sin(lat_rad) * np.sin(decl) + np.cos(lat_rad) * np.cos(decl) * np.cos(hour_angle)
    return 90.0 - np.rad2deg(np.arccos(np.clip(cos_zenith, -1.0, 1.0)))


def load_ext_obs() -> pd.DataFrame:
    city_by_icao, _coords_by_city = station_maps()
    frames = []
    for path in sorted(EXT_DIR.glob("iem_ext_*.csv")):
        icao = path.stem.split("_")[2]
        city = city_by_icao.get(icao)
        if city is None:
            continue
        df = pd.read_csv(path)
        df["city"] = city
        df["valid_utc"] = pd.to_datetime(df["valid"], utc=True, errors="coerce")
        df["sky"] = df["skyc1"].map(SKY_CODE)
        df["drct"] = pd.to_numeric(df["drct"], errors="coerce")
        keep = ["city", "valid_utc", "sky", "drct", "sknt"]
        if "alti" in df.columns:
            df["alti"] = pd.to_numeric(df["alti"], errors="coerce")
            keep.append("alti")
        frames.append(df[keep].dropna(subset=["valid_utc"]))
    if not frames:
        raise RuntimeError(f"No IEM ext CSV files found in {EXT_DIR}")
    return pd.concat(frames, ignore_index=True).sort_values("valid_utc")


def merge_city_asof(sub: pd.DataFrame, e: pd.DataFrame, left_col: str) -> pd.DataFrame:
    return pd.merge_asof(
        sub[[left_col]].sort_values(left_col),
        e.rename(columns={"valid_utc": left_col}),
        on=left_col,
        direction="backward",
        tolerance=pd.Timedelta(minutes=90),
    )


def enrich(df: pd.DataFrame, ext: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    _city_by_icao, coords_by_city = station_maps()
    missing_city_coords = sorted(set(df["city"].astype(str)) - set(coords_by_city))
    if missing_city_coords:
        raise RuntimeError(f"Missing static coordinates for cities: {missing_city_coords}")

    df["ts"] = pd.to_datetime(df["snapshot_ts_utc"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    df["ts_3h"] = df["ts"] - pd.Timedelta(hours=3)

    sky_now = np.full(len(df), np.nan)
    sky_3h = np.full(len(df), np.nan)
    drct_now = np.full(len(df), np.nan)
    alti_now = np.full(len(df), np.nan)
    alti_3h = np.full(len(df), np.nan)
    for city, sub in df.groupby("city"):
        e = ext[ext["city"] == city].sort_values("valid_utc")
        if e.empty:
            continue
        idx = sub.index.to_numpy()
        now = merge_city_asof(sub, e, "ts")
        lag = merge_city_asof(sub, e, "ts_3h")
        sky_now[idx] = now["sky"].to_numpy()
        drct_now[idx] = now["drct"].to_numpy()
        sky_3h[idx] = lag["sky"].to_numpy()
        if "alti" in e.columns:
            alti_now[idx] = now["alti"].to_numpy()
            alti_3h[idx] = lag["alti"].to_numpy()

    df["sky_now_ext"] = sky_now
    df["d_sky_3h"] = sky_now - sky_3h
    df["wind_dir_sin"] = np.sin(np.deg2rad(drct_now))
    df["wind_dir_cos"] = np.cos(np.deg2rad(drct_now))
    df["alti_now"] = alti_now
    df["d_alti_3h"] = alti_now - alti_3h

    lat = np.asarray([coords_by_city[str(city)][0] for city in df["city"]], dtype=float)
    lon = np.asarray([coords_by_city[str(city)][1] for city in df["city"]], dtype=float)
    df["station_lat"] = lat
    df["station_lon"] = lon
    df["solar_altitude_deg"] = solar_altitude_deg(df["ts"], lat, lon)
    df["solar_altitude_pos"] = np.clip(df["solar_altitude_deg"], 0.0, None) / 90.0
    df["d_sky_3h_x_solar_altitude_pos"] = df["d_sky_3h"] * df["solar_altitude_pos"]

    for city in sorted(df["city"].astype(str).unique()):
        safe = "".join(ch if ch.isalnum() else "_" for ch in city)
        mask = df["city"].astype(str).eq(city).to_numpy(float)
        df[f"wind_city__{safe}__sin"] = df["wind_dir_sin"] * mask
        df[f"wind_city__{safe}__cos"] = df["wind_dir_cos"] * mask
    return df


def feature_matrix(train: pd.DataFrame, hold: pd.DataFrame, feats: list[str]) -> tuple[np.ndarray, np.ndarray]:
    tr = train[feats].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    ho = hold[feats].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    med = np.nanmedian(np.where(np.isfinite(tr), tr, np.nan), axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    tr = np.where(np.isfinite(tr), tr, med)
    ho = np.where(np.isfinite(ho), ho, med)
    mu = np.nanmean(tr, axis=0)
    sd = np.nanstd(tr, axis=0)
    sd = np.where(np.isfinite(sd) & (sd > 0), sd, 1.0)
    return (tr - mu) / sd, (ho - mu) / sd


def row_logloss(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))


def date_bootstrap_delta_ci(
    hold: pd.DataFrame,
    y_h: np.ndarray,
    p0: np.ndarray,
    p1: np.ndarray,
    reps: int = 3000,
) -> list[float | None]:
    delta = row_logloss(y_h.astype(float), p1) - row_logloss(y_h.astype(float), p0)
    by_date = pd.DataFrame({"target_date": hold["target_date"].to_numpy(), "delta": delta}).groupby("target_date")["delta"].mean()
    if len(by_date) < 2:
        return [None, None]
    rng = np.random.default_rng(SEED)
    vals = by_date.to_numpy(float)
    draws = []
    for _ in range(reps):
        idx = rng.integers(0, len(vals), size=len(vals))
        draws.append(float(vals[idx].mean()))
    return [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))]


def incremental_lift(train: pd.DataFrame, hold: pd.DataFrame, feats: str | list[str], y_tr: np.ndarray, y_h: np.ndarray) -> dict[str, float]:
    if isinstance(feats, str):
        feats = [feats]
    x_tr, x_h = feature_matrix(train, hold, feats)
    base_tr = train["base_model_logit"].to_numpy(float).reshape(-1, 1)
    base_h = hold["base_model_logit"].to_numpy(float).reshape(-1, 1)

    m0 = LogisticRegression(max_iter=3000).fit(base_tr, y_tr)
    m1 = LogisticRegression(max_iter=3000, C=0.5).fit(np.column_stack([base_tr, x_tr]), y_tr)
    mf = LogisticRegression(max_iter=3000, C=0.5).fit(x_tr, y_tr)
    p0 = m0.predict_proba(base_h)[:, 1]
    p1 = m1.predict_proba(np.column_stack([base_h, x_h]))[:, 1]
    pf = mf.predict_proba(x_h)[:, 1]
    auc = roc_auc_score(y_h, pf) if len(np.unique(y_h)) > 1 else float("nan")
    return {
        "ll0": float(log_loss(y_h, np.clip(p0, 1e-6, 1 - 1e-6))),
        "ll1": float(log_loss(y_h, np.clip(p1, 1e-6, 1 - 1e-6))),
        "date_bootstrap_delta_logloss_ci95": date_bootstrap_delta_ci(hold, y_h, p0, p1),
        "br0": float(brier_score_loss(y_h, p0)),
        "br1": float(brier_score_loss(y_h, p1)),
        "feature_auc": float(max(auc, 1 - auc)) if math.isfinite(auc) else float("nan"),
        "coef_l2": float(np.linalg.norm(m1.coef_[0][1:])),
    }


def conditional_lift(
    train: pd.DataFrame,
    hold: pd.DataFrame,
    feats: list[str],
    y_tr: np.ndarray,
    y_h: np.ndarray,
    base_name: str,
    base_col: str | None,
) -> dict[str, Any]:
    x_tr, x_h = feature_matrix(train, hold, feats)
    if base_col is None:
        model = LogisticRegression(max_iter=3000, C=0.5).fit(x_tr, y_tr)
        p = model.predict_proba(x_h)[:, 1]
        return {
            "conditioning": base_name,
            "base_logloss": None,
            "with_feature_logloss": float(log_loss(y_h, np.clip(p, 1e-6, 1 - 1e-6))),
            "delta_logloss": None,
            "date_bootstrap_delta_logloss_ci95": [None, None],
            "with_feature_brier": float(brier_score_loss(y_h, p)),
            "with_feature_auc": float(roc_auc_score(y_h, p)) if len(np.unique(y_h)) > 1 else None,
            "coef_l2": float(np.linalg.norm(model.coef_[0])),
        }

    base_tr = train[base_col].to_numpy(float).reshape(-1, 1)
    base_h = hold[base_col].to_numpy(float).reshape(-1, 1)
    m0 = LogisticRegression(max_iter=3000).fit(base_tr, y_tr)
    m1 = LogisticRegression(max_iter=3000, C=0.5).fit(np.column_stack([base_tr, x_tr]), y_tr)
    p0 = m0.predict_proba(base_h)[:, 1]
    p1 = m1.predict_proba(np.column_stack([base_h, x_h]))[:, 1]
    ll0 = float(log_loss(y_h, np.clip(p0, 1e-6, 1 - 1e-6)))
    ll1 = float(log_loss(y_h, np.clip(p1, 1e-6, 1 - 1e-6)))
    return {
        "conditioning": base_name,
        "base_logloss": ll0,
        "with_feature_logloss": ll1,
        "delta_logloss": ll1 - ll0,
        "date_bootstrap_delta_logloss_ci95": date_bootstrap_delta_ci(hold, y_h, p0, p1),
        "with_feature_brier": float(brier_score_loss(y_h, p1)),
        "with_feature_auc": float(roc_auc_score(y_h, p1)) if len(np.unique(y_h)) > 1 else None,
        "coef_l2": float(np.linalg.norm(m1.coef_[0][1:])),
    }


def feature_group_diagnostics(df: pd.DataFrame) -> list[dict[str, Any]]:
    train, hold = df[df["period"].eq("train")].copy(), df[df["period"].eq("holdout")].copy()
    y_tr, y_h = train[LABEL].to_numpy(int), hold[LABEL].to_numpy(int)
    wind_city = [c for c in df.columns if c.startswith("wind_city__")]
    groups = {
        "metar_weather_core": METAR_WEATHER,
        "p0_p1_tail_candidates": P0_CHEAP + P1_SOLAR + wind_city,
        "metar_plus_tail_candidates": METAR_WEATHER + P0_CHEAP + P1_SOLAR + wind_city,
    }
    rows: list[dict[str, Any]] = []
    for group, feats in groups.items():
        feats = [f for f in feats if f in df.columns]
        for base_name, base_col in [
            ("weather_only", None),
            ("plus_market_price", "market_logit"),
            ("plus_base_v9", "base_model_logit"),
        ]:
            diag = conditional_lift(train, hold, feats, y_tr, y_h, base_name, base_col)
            rows.append(
                {
                    "feature_group": group,
                    "n_features": len(feats),
                    "coverage": float(hold[feats].notna().any(axis=1).mean()),
                    **diag,
                }
            )
    print("\n== deeper diagnostic: feature groups by conditioning layer ==")
    for row in rows:
        delta = row["delta_logloss"]
        delta_txt = "NA" if delta is None else f"{delta:.5f}"
        print(
            f"{row['feature_group']:<28} {row['conditioning']:<18} "
            f"AUC={row['with_feature_auc']:.3f} ll={row['with_feature_logloss']:.4f} "
            f"d_ll={delta_txt} ci={row['date_bootstrap_delta_logloss_ci95']}"
        )
    return rows


def residual_diagnostics(df: pd.DataFrame, feats: list[str]) -> list[dict[str, Any]]:
    hold = df[df["period"].eq("holdout")].copy()
    y = hold[LABEL].to_numpy(float)
    market_resid = y - hold["yes_current_ask"].to_numpy(float)
    base_resid = y - hold["base_model_p"].to_numpy(float)
    rows = []
    for feat in feats:
        v = pd.to_numeric(hold[feat], errors="coerce")
        mask = v.notna().to_numpy()
        if mask.sum() <= 2:
            corr_y = corr_market = corr_base = float("nan")
        else:
            x = v.to_numpy(float)[mask]
            corr_y = float(np.corrcoef(x, y[mask])[0, 1])
            corr_market = float(np.corrcoef(x, market_resid[mask])[0, 1])
            corr_base = float(np.corrcoef(x, base_resid[mask])[0, 1])
        rows.append(
            {
                "feature": feat,
                "coverage": float(mask.mean()),
                "corr_with_label": corr_y,
                "corr_with_market_residual": corr_market,
                "corr_with_base_v9_residual": corr_base,
            }
        )
    print("\n== deeper diagnostic: residual correlations ==")
    for row in rows:
        print(
            f"{row['feature']:<34} y={row['corr_with_label']:+.3f} "
            f"market_resid={row['corr_with_market_residual']:+.3f} "
            f"base_resid={row['corr_with_base_v9_residual']:+.3f}"
        )
    return rows


def verdict_from_delta(delta_logloss: float) -> str:
    # P0 already showed 1e-4-scale deltas are not actionable; require a
    # material holdout logloss move before calling a feature additive.
    if delta_logloss < -1e-3:
        return "material_additive"
    if abs(delta_logloss) <= 1e-3:
        return "near_zero"
    return "no_or_degrades"


def report_single(df: pd.DataFrame, title: str, feats: list[str]) -> list[dict[str, Any]]:
    train, hold = df[df["period"].eq("train")].copy(), df[df["period"].eq("holdout")].copy()
    y_tr, y_h = train[LABEL].to_numpy(int), hold[LABEL].to_numpy(int)
    print(f"\n== {title} ==  holdout={len(hold)} survive={y_h.mean():.1%} break={1-y_h.mean():.1%}")
    print(f"{'feature':<34}{'cov%':>7}{'AUC':>8}{'r_pb':>8}{'d_logloss':>12}{'verdict':>18}")
    print("-" * 90)
    rows: list[dict[str, Any]] = []
    for feat in feats:
        v = pd.to_numeric(hold[feat], errors="coerce")
        m = v.notna()
        cov = float(m.mean())
        auc = float("nan")
        if m.sum() > 2 and len(np.unique(y_h[m.to_numpy()])) > 1:
            raw_auc = roc_auc_score(y_h[m.to_numpy()], v[m].to_numpy())
            auc = float(max(raw_auc, 1 - raw_auc))
        r_pb = float(np.corrcoef(v[m].to_numpy(), y_h[m.to_numpy()])[0, 1]) if m.sum() > 2 else float("nan")
        lift = incremental_lift(train, hold, feat, y_tr, y_h)
        delta = lift["ll1"] - lift["ll0"]
        verdict = verdict_from_delta(delta)
        print(f"{feat:<34}{cov*100:>6.0f}%{auc:>8.3f}{r_pb:>8.3f}{delta:>12.5f}{verdict:>18}")
        rows.append(
            {
                "feature": feat,
                "kind": "single",
                "n_features": 1,
                "coverage": cov,
                "holdout_auc": auc,
                "r_pb": r_pb,
                "delta_logloss_vs_base_model": delta,
                "date_bootstrap_delta_logloss_ci95": lift["date_bootstrap_delta_logloss_ci95"],
                "holdout_logloss_with_base": lift["ll1"],
                "holdout_brier_with_base": lift["br1"],
                "coef_l2": lift["coef_l2"],
                "verdict": verdict,
            }
        )
    return rows


def report_wind_city(df: pd.DataFrame) -> list[dict[str, Any]]:
    train, hold = df[df["period"].eq("train")].copy(), df[df["period"].eq("holdout")].copy()
    y_tr, y_h = train[LABEL].to_numpy(int), hold[LABEL].to_numpy(int)
    feats = [c for c in df.columns if c.startswith("wind_city__")]
    if not feats:
        raise RuntimeError("No wind_city__ interaction columns were generated")
    cov = float(hold[feats].notna().any(axis=1).mean())
    lift = incremental_lift(train, hold, feats, y_tr, y_h)
    delta = lift["ll1"] - lift["ll0"]
    verdict = verdict_from_delta(delta)
    print("\n== P1 proper form: wind direction x city ==")
    print(f"wind_city_interaction: n_features={len(feats)} cov={cov:.1%} AUC={lift['feature_auc']:.3f} d_logloss={delta:.5f} verdict={verdict}")
    return [
        {
            "feature": "wind_city_interaction",
            "kind": "family",
            "description": "one-hot city x wind_dir_sin/cos",
            "n_features": len(feats),
            "coverage": cov,
            "holdout_auc": lift["feature_auc"],
            "r_pb": None,
            "delta_logloss_vs_base_model": delta,
            "date_bootstrap_delta_logloss_ci95": lift["date_bootstrap_delta_logloss_ci95"],
            "holdout_logloss_with_base": lift["ll1"],
            "holdout_brier_with_base": lift["br1"],
            "coef_l2": lift["coef_l2"],
            "verdict": verdict,
        }
    ]


def baseline_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    hold = df[df["period"].eq("holdout")].copy()
    y = hold[LABEL].to_numpy(int)
    rows = []
    for name, col in [("market_price_as_probability", "yes_current_ask"), ("base_current_yes_v9", "base_model_p")]:
        p = np.clip(pd.to_numeric(hold[col], errors="coerce").to_numpy(float), 1e-6, 1 - 1e-6)
        rows.append(
            {
                "model": name,
                "rows": int(len(hold)),
                "active_dates": int(hold["target_date"].nunique()),
                "actual_survive_rate": float(y.mean()),
                "mean_pred_survive": float(np.nanmean(p)),
                "auc": float(roc_auc_score(y, p)),
                "brier": float(brier_score_loss(y, p)),
                "logloss": float(log_loss(y, p)),
            }
        )
    return rows


def main() -> int:
    df = pd.read_csv(FEATURE_ROWS)
    df[LABEL] = coerce_bool(df[LABEL])
    art = json.loads(BASE_MODEL.read_text(encoding="utf-8"))
    df["base_model_p"] = score_base_model(df, art)
    df["base_model_logit"] = logit(df["base_model_p"].to_numpy(float))
    df["market_logit"] = logit(pd.to_numeric(df["yes_current_ask"], errors="coerce").to_numpy(float))

    ext = load_ext_obs()
    df = enrich(df, ext)

    chk = df[["sky_now", "sky_now_ext"]].apply(pd.to_numeric, errors="coerce").dropna()
    join_corr = float(np.corrcoef(chk["sky_now"], chk["sky_now_ext"])[0, 1]) if len(chk) > 2 else float("nan")
    print(f"== join self-check == corr(sky_now original, ext asof)={join_corr:.3f}")

    baselines = baseline_rows(df)
    for row in baselines:
        print(f"baseline {row['model']}: AUC={row['auc']:.3f} logloss={row['logloss']:.4f} Brier={row['brier']:.4f}")

    results: list[dict[str, Any]] = []
    results += report_single(df, "P0 cheap form reproduction", P0_CHEAP)
    results += report_single(df, "P1 proper form: d_sky x solar altitude", P1_SOLAR)
    results += report_wind_city(df)
    results += report_single(df, "Reference features already represented in base v9", PRESENT_REF)
    group_diagnostics = feature_group_diagnostics(df)
    residual_features = [
        "d_tmpf_3h",
        "d_relh_3h",
        "solar_altitude_pos",
        "d_sky_3h",
        "d_sky_3h_x_solar_altitude_pos",
        "wind_dir_sin",
        "wind_dir_cos",
    ]
    residuals = residual_diagnostics(df, residual_features)

    blocked = []
    if "alti" not in ext.columns or df["d_alti_3h"].notna().sum() == 0:
        blocked.append(
            {
                "feature": "d_alti_3h",
                "kind": "blocked",
                "reason": "theta_no_iem_ext_patch_v6 has no alti column; add alti to N100 IEM fetch and rematerialize theta_no_iem_ext_patch_v6 before testing pressure tendency.",
                "ext_columns": sorted(set(ext.columns)),
            }
        )
        print("\nBLOCKED d_alti_3h: ext cache has no alti column; no proxy used.")
    else:
        results += report_single(df, "P1 proper form: pressure tendency", ["d_alti_3h"])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_csv = OUT_DIR / "proper_form_tail_feature_discrimination.csv"
    out_json = OUT_DIR / "summary.json"
    pd.DataFrame(results).to_csv(out_csv, index=False)
    pd.DataFrame(group_diagnostics).to_csv(OUT_DIR / "feature_group_conditioning_diagnostics.csv", index=False)
    pd.DataFrame(residuals).to_csv(OUT_DIR / "residual_correlation_diagnostics.csv", index=False)
    out_json.write_text(
        json.dumps(
            json_ready(
                {
                    "target_metric": "current_yes_survive_discrimination",
                    "row_grain": "city + target_date + decision snapshot",
                    "source_feature_rows": str(FEATURE_ROWS.relative_to(ROOT)),
                    "base_model": str(BASE_MODEL.relative_to(ROOT)),
                    "ext_dir": str(EXT_DIR.relative_to(ROOT)),
                    "join_self_check_corr_sky_now": join_corr,
                    "rows": int(len(df)),
                    "train_rows": int(df["period"].eq("train").sum()),
                    "holdout_rows": int(df["period"].eq("holdout").sum()),
                    "target_date_min": str(df["target_date"].min()),
                    "target_date_max": str(df["target_date"].max()),
                    "baselines": baselines,
                    "feature_results": results,
                    "feature_group_conditioning_diagnostics": group_diagnostics,
                    "residual_correlation_diagnostics": residuals,
                    "blocked": blocked,
                }
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote: {out_csv.relative_to(ROOT)}")
    print(f"wrote: {(OUT_DIR / 'feature_group_conditioning_diagnostics.csv').relative_to(ROOT)}")
    print(f"wrote: {(OUT_DIR / 'residual_correlation_diagnostics.csv').relative_to(ROOT)}")
    print(f"wrote: {out_json.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
