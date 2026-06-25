#!/usr/bin/env python3
"""Peak-YES first-principles mechanism v4.

V3 showed that the physical score ranks future-break hazard but is weaker than
market.  This version adds the missing mechanism families that are available as
point-in-time or deterministic features:

- true solar geometry from station coordinates and decision timestamp,
- observation cadence/staleness from the feature factory,
- forecast slope-to-peak proxy,
- cloud clearing / wind ramp / drying tendency from IEM ext cache.

The goal is still not to find a new hard gate.  The test is whether these
mechanism features improve future-break probability and BUY peak-YES EV.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import research_current_yes_peak_yes_first_principles_v3 as v3  # noqa: E402
import validate_reheat_tail_feature_discrimination_v1 as tail  # noqa: E402


FEATURE_ROWS = [
    ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260519_20260620/reheat_feature_rows.csv",
    ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260621_20260623/reheat_feature_rows.csv",
]
EXT_DIRS = [
    ROOT / "docs/analysis/2026-06/generated/theta_no_iem_ext_patch_v7_20260617",
    ROOT / "docs/analysis/2026-06/generated/theta_no_iem_ext_patch_v9_20260623",
]
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_peak_yes_mechanism_v4"
OUT_SCORED = OUT_DIR / "peak_yes_mechanism_v4_scored_rows.csv"
OUT_METRICS = OUT_DIR / "peak_yes_mechanism_v4_model_metrics.csv"
OUT_RULES = OUT_DIR / "peak_yes_mechanism_v4_ev_rules.csv"
OUT_COMPONENTS = OUT_DIR / "peak_yes_mechanism_v4_component_audit.csv"
OUT_BINS = OUT_DIR / "peak_yes_mechanism_v4_score_bins.csv"
OUT_COVERAGE = OUT_DIR / "peak_yes_mechanism_v4_feature_coverage.csv"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-25-current-yes-peak-yes-mechanism-v4.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-25-current-yes-peak-yes-mechanism-v4.md"
SEED = 20260625


STATE_KEYS = ["city", "target_date", "decision_hour_local"]
FACTORY_KEEP = [
    *STATE_KEYS,
    "decision_snapshot_ts_utc",
    "obs_count_day",
    "obs_count_to_decision",
    "decision_last_obs_utc",
    "gfs_forecast_peak_delta_hours_local",
    "ecmwf_forecast_peak_delta_hours_local",
    "gfs_forecast_hourly_count",
    "ecmwf_forecast_hourly_count",
    "forecast_hourly_count",
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def load_factory_states() -> pd.DataFrame:
    frames = []
    for path in FEATURE_ROWS:
        df = pd.read_csv(path, low_memory=False)
        keep = [c for c in FACTORY_KEEP if c in df.columns]
        df = df[keep].copy()
        df["target_date"] = df["target_date"].astype(str)
        df["decision_hour_local"] = pd.to_numeric(df["decision_hour_local"], errors="coerce")
        df["decision_snapshot_sort"] = pd.to_datetime(df["decision_snapshot_ts_utc"], utc=True, errors="coerce")
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values(STATE_KEYS + ["decision_snapshot_sort"]).drop_duplicates(STATE_KEYS, keep="last")
    return out.drop(columns=["decision_snapshot_sort"]).reset_index(drop=True)


def load_ext_obs() -> pd.DataFrame:
    city_by_icao, _coords = tail.station_maps()
    frames = []
    for ext_dir in EXT_DIRS:
        for path in sorted(ext_dir.glob("iem_ext_*.csv")):
            icao = path.stem.split("_")[2]
            city = city_by_icao.get(icao)
            if city is None:
                continue
            df = pd.read_csv(path)
            df["city"] = city
            df["valid_utc"] = pd.to_datetime(df["valid"], utc=True, errors="coerce")
            df["sky"] = df.get("skyc1").map(tail.SKY_CODE) if "skyc1" in df.columns else np.nan
            for col in ["tmpf", "dwpf", "relh", "drct", "sknt", "sky"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            keep = ["city", "valid_utc", "sky", "sknt", "dwpf", "relh", "drct"]
            frames.append(df[keep].dropna(subset=["valid_utc"]))
    if not frames:
        return pd.DataFrame(columns=["city", "valid_utc", "sky", "sknt", "dwpf", "relh", "drct"])
    return (
        pd.concat(frames, ignore_index=True)
        .sort_values(["city", "valid_utc"])
        .drop_duplicates(["city", "valid_utc"], keep="last")
        .sort_values("valid_utc")
        .reset_index(drop=True)
    )


def merge_city_asof(sub: pd.DataFrame, ext: pd.DataFrame, left_col: str) -> pd.DataFrame:
    return pd.merge_asof(
        sub[[left_col]].sort_values(left_col),
        ext.rename(columns={"valid_utc": left_col}).sort_values(left_col),
        on=left_col,
        direction="backward",
        tolerance=pd.Timedelta(minutes=90),
    )


def add_solar_and_weather_tendency(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy().reset_index(drop=True)
    _city_by_icao, coords_by_city = tail.station_maps()
    missing = sorted(set(out["city"].astype(str)) - set(coords_by_city))
    if missing:
        raise RuntimeError(f"missing station coordinates for {missing}")

    out["ts"] = pd.to_datetime(out["decision_snapshot_ts_utc"], utc=True, errors="coerce")
    lat = np.asarray([coords_by_city[str(city)][0] for city in out["city"]], dtype=float)
    lon = np.asarray([coords_by_city[str(city)][1] for city in out["city"]], dtype=float)
    out["station_lat"] = lat
    out["station_lon"] = lon
    out["solar_altitude_deg"] = tail.solar_altitude_deg(out["ts"], lat, lon)
    out["solar_altitude_pos"] = np.clip(out["solar_altitude_deg"], 0.0, None) / 90.0
    out["ts_2h"] = out["ts"] + pd.Timedelta(hours=2)
    out["solar_altitude_2h_deg"] = tail.solar_altitude_deg(out["ts_2h"], lat, lon)
    out["solar_altitude_2h_pos"] = np.clip(out["solar_altitude_2h_deg"], 0.0, None) / 90.0
    out["solar_delta_2h_deg"] = out["solar_altitude_2h_deg"] - out["solar_altitude_deg"]

    ext = load_ext_obs()
    for suffix, hours in [("1h", 1), ("3h", 3)]:
        out[f"ts_{suffix}_lag"] = out["ts"] - pd.Timedelta(hours=hours)

    cols = ["sky", "sknt", "dwpf", "relh", "drct"]
    now_vals = {c: np.full(len(out), np.nan) for c in cols}
    lag1_vals = {c: np.full(len(out), np.nan) for c in cols}
    lag3_vals = {c: np.full(len(out), np.nan) for c in cols}
    for city, sub in out.groupby("city", sort=False):
        e = ext[ext["city"].eq(city)]
        if e.empty:
            continue
        idx = sub.index.to_numpy()
        now = merge_city_asof(sub, e, "ts")
        lag1 = merge_city_asof(sub, e, "ts_1h_lag")
        lag3 = merge_city_asof(sub, e, "ts_3h_lag")
        for col in cols:
            now_vals[col][idx] = now[col].to_numpy()
            lag1_vals[col][idx] = lag1[col].to_numpy()
            lag3_vals[col][idx] = lag3[col].to_numpy()

    for col in cols:
        out[f"{col}_ext_now"] = now_vals[col]
        out[f"d_{col}_1h"] = now_vals[col] - lag1_vals[col]
        out[f"d_{col}_3h"] = now_vals[col] - lag3_vals[col]

    out["wind_dir_sin"] = np.sin(np.deg2rad(out["drct_ext_now"]))
    out["wind_dir_cos"] = np.cos(np.deg2rad(out["drct_ext_now"]))
    out["d_sky_3h_x_solar"] = out["d_sky_3h"] * out["solar_altitude_pos"]
    out["cloud_clearing_x_solar"] = (-out["d_sky_3h"]).clip(lower=0) * out["solar_altitude_pos"]
    out["wind_ramp_x_solar"] = out["d_sknt_3h"].clip(lower=0) * out["solar_altitude_pos"]
    out["drying_x_solar"] = (-out["d_relh_3h"]).clip(lower=0) * out["solar_altitude_pos"]
    return out


def add_cadence_and_curve_proxy(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    snap = pd.to_datetime(out["decision_snapshot_ts_utc"], utc=True, errors="coerce")
    last = pd.to_datetime(out["decision_last_obs_utc"], utc=True, errors="coerce")
    hour = pd.to_numeric(out["decision_hour_local"], errors="coerce")
    out["decision_obs_age_min"] = (snap - last).dt.total_seconds() / 60.0
    out["obs_per_elapsed_hour"] = pd.to_numeric(out["obs_count_to_decision"], errors="coerce") / hour.clip(lower=1.0)

    gap_cols = [
        pd.to_numeric(out.get("forecast_gap_to_running_native"), errors="coerce"),
        pd.to_numeric(out.get("gfs_gap_to_running_native"), errors="coerce"),
        pd.to_numeric(out.get("ecmwf_gap_to_running_native"), errors="coerce"),
    ]
    gap = pd.concat(gap_cols, axis=1).max(axis=1)
    peak_deltas = [
        pd.to_numeric(out.get("forecast_peak_delta_hours_local"), errors="coerce"),
        pd.to_numeric(out.get("gfs_forecast_peak_delta_hours_local"), errors="coerce"),
        pd.to_numeric(out.get("ecmwf_forecast_peak_delta_hours_local"), errors="coerce"),
    ]
    hours_to_peak = pd.concat([(-x) for x in peak_deltas], axis=1).max(axis=1).clip(lower=0)
    out["forecast_hours_to_peak_max"] = hours_to_peak
    out["forecast_slope_to_peak_native_per_h"] = gap.clip(lower=0) / hours_to_peak.clip(lower=1.0)
    out["forecast_curve_hourly_count_max"] = pd.concat(
        [
            pd.to_numeric(out.get("forecast_hourly_count"), errors="coerce"),
            pd.to_numeric(out.get("gfs_forecast_hourly_count"), errors="coerce"),
            pd.to_numeric(out.get("ecmwf_forecast_hourly_count"), errors="coerce"),
        ],
        axis=1,
    ).max(axis=1)
    return out


def add_v4_components(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    out, weights = v3.add_physics_components(df)
    out = add_cadence_and_curve_proxy(add_solar_and_weather_tendency(out))

    unit_is_f = out["unit"].astype(str).str.upper().eq("F")
    slope_scale = np.where(unit_is_f, 2.0, 1.1)
    out["comp_solar_geometry"] = (
        0.65 * pd.to_numeric(out["solar_altitude_pos"], errors="coerce")
        + 0.35 * (pd.to_numeric(out["solar_altitude_2h_pos"], errors="coerce"))
    ).clip(0, 1).fillna(0.45)
    out["comp_forecast_slope_to_peak"] = (
        pd.to_numeric(out["forecast_slope_to_peak_native_per_h"], errors="coerce") / slope_scale
    ).clip(0, 1).fillna(0.35)
    out["comp_obs_cadence_risk"] = (
        0.65 * (pd.to_numeric(out["decision_obs_age_min"], errors="coerce") / 60.0).clip(0, 1)
        + 0.35 * (1.0 - (pd.to_numeric(out["obs_per_elapsed_hour"], errors="coerce") / 2.0).clip(0, 1))
    ).fillna(0.45)
    out["comp_cloud_clearing_solar"] = (
        pd.to_numeric(out["cloud_clearing_x_solar"], errors="coerce") / 3.0
    ).clip(0, 1).fillna(0.35)
    out["comp_wind_ramp_solar"] = (pd.to_numeric(out["wind_ramp_x_solar"], errors="coerce") / 12.0).clip(0, 1).fillna(0.35)
    out["comp_drying_solar"] = (pd.to_numeric(out["drying_x_solar"], errors="coerce") / 30.0).clip(0, 1).fillna(0.35)

    weights = dict(weights)
    weights.update(
        {
            "comp_solar_geometry": 0.85,
            "comp_forecast_slope_to_peak": 0.75,
            "comp_obs_cadence_risk": 0.35,
            "comp_cloud_clearing_solar": 0.45,
            "comp_wind_ramp_solar": 0.25,
            "comp_drying_solar": 0.30,
        }
    )
    denom = sum(weights.values())
    out["physics_break_score_raw"] = sum(pd.to_numeric(out[col], errors="coerce").fillna(0.5) * w for col, w in weights.items()) / denom
    return out, weights


def feature_coverage(scored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    cols = [
        "solar_altitude_deg",
        "decision_obs_age_min",
        "obs_per_elapsed_hour",
        "forecast_slope_to_peak_native_per_h",
        "d_sky_3h",
        "d_sknt_3h",
        "d_relh_3h",
        "cloud_clearing_x_solar",
    ]
    for period, frame in scored.groupby("period"):
        for col in cols:
            rows.append({"period": period, "feature": col, "coverage": float(frame[col].notna().mean())})
    return pd.DataFrame(rows)


def component_columns(scored: pd.DataFrame) -> list[str]:
    return sorted(c for c in scored.columns if c.startswith("comp_"))


def fit_component_model(train: pd.DataFrame, cols: list[str]) -> Pipeline:
    model = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(max_iter=2000, C=0.25, random_state=SEED)),
        ]
    )
    model.fit(train[cols], train["label_future_break"].astype(int))
    return model


def learned_ev_rules(scored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for period in ["train", "holdout", "forward"]:
        frame = scored[scored["period"].eq(period)].copy()
        for p_col, label in [
            ("p_break_components_l2", "components_l2"),
            ("p_break_market_components_l2", "market_components_l2"),
        ]:
            edge = (1.0 - frame[p_col]) - frame["current_yes_ask"]
            for min_edge in [0.00, 0.02, 0.05]:
                sub = frame[edge.ge(min_edge)]
                rows.append(
                    {
                        **v3.trade_summary(sub, f"{label}_edge_ge_{min_edge:.2f}", p_col),
                        "period": period,
                        "min_edge": min_edge,
                        "ask_low": None,
                        "ask_high": None,
                    }
                )
    return pd.DataFrame(rows)


def render_metric_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| period | model | rows | break | pred break | AUC | Brier | logloss |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['period']} | {row['model']} | {row['rows']} | {v3.pct(row['actual_break_rate'])} | "
            f"{v3.pct(row['mean_pred_break'])} | {v3.num(row['auc_break'])} | {v3.num(row['brier'])} | {v3.num(row['logloss'])} |"
        )
    return lines


def render_rule_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| period | rule | rows | dates | win | avg ask | ROI | CI |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['period']} | {row['rule']} | {row['rows']} | {row['dates']} | "
            f"{v3.pct(row.get('win_rate'))} | {v3.num(row.get('avg_ask'))} | {v3.pct(row.get('roi'))} | "
            f"[{v3.pct(row['roi_ci95'][0])}, {v3.pct(row['roi_ci95'][1])}] |"
        )
    return lines


def build_markdown(payload: dict[str, Any]) -> None:
    lines = [
        "# Current-YES Peak-YES Mechanism v4",
        "",
        "Status: research-only",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "## 一句话结论",
        "",
        payload["headline"],
        "",
        "## 数据范围",
        "",
        f"- Atlas rows: `{payload['inputs']['atlas_rows']}`",
        f"- Feature factory rows: `{', '.join(payload['inputs']['feature_rows'])}`",
        f"- IEM ext cache: `{', '.join(payload['inputs']['ext_dirs'])}`",
        f"- Tradable peak-YES rows: {payload['coverage']['tradable_rows']} / dates {payload['coverage']['tradable_dates']} / cities {payload['coverage']['tradable_cities']}",
        f"- DB fact refresh: `{payload['data_self_check']['fact_signal_candidates']['max_built_at']}`",
        "",
        "V4 adds solar geometry, observation cadence, forecast slope-to-peak proxy, and cloud/wind/drying tendency features. Regimes remain continuous priors, not hard gates.",
        "",
        "## Model Metrics",
        "",
        *render_metric_table(payload["metrics"]),
        "",
        "## EV Rules",
        "",
        *render_rule_table(payload["ev_rules"]),
        "",
        "## Feature Coverage",
        "",
        "| period | feature | coverage |",
        "|---|---|---:|",
    ]
    for row in payload["feature_coverage"]:
        lines.append(f"| {row['period']} | {row['feature']} | {v3.pct(row['coverage'])} |")
    lines.extend(
        [
            "",
            "## Component Audit",
            "",
            "| period | component | rows | AUC break | corr | mean |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in payload["component_audit"][:18]:
        lines.append(
            f"| {row['period']} | {row['component']} | {row['rows']} | {v3.num(row['auc_break'])} | "
            f"{v3.num(row['corr_break'])} | {v3.num(row['mean'])} |"
        )
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            f"significance={payload['verdict']['significance']} / baseline={payload['verdict']['baseline']} / forward={payload['verdict']['forward']} / conclusion={payload['verdict']['conclusion']}",
            "",
            payload["verdict"]["text"],
            "",
            "## Outputs",
            "",
        ]
    )
    for label, path in payload["outputs"].items():
        lines.append(f"- {label}: `{path}`")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    self_check = v3.data_self_check(v3.DB)
    atlas = v3.tradable(v3.load_rows(v3.ATLAS_ROWS))
    factory = load_factory_states()
    merged = atlas.merge(factory, on=STATE_KEYS, how="left", suffixes=("", "_factory"))
    for col in ["decision_snapshot_ts_utc"]:
        alt = f"{col}_factory"
        if alt in merged.columns:
            merged[col] = merged[col].fillna(merged[alt])

    scored, weights = add_v4_components(merged)
    train = scored[scored["period"].eq("train")].copy()
    physics_model = v3.fit_physics_calibrator(train)
    market_physics_model = v3.fit_market_physics(train)
    scored["p_break_physics"] = physics_model.predict_proba(scored[["physics_break_score_raw"]])[:, 1]
    scored["p_break_market_physics"] = market_physics_model.predict_proba(
        scored[["market_break_logit", "physics_break_score_raw"]]
    )[:, 1]
    comp_cols = component_columns(scored)
    comp_model = fit_component_model(train, comp_cols)
    market_comp_cols = ["market_break_logit"] + comp_cols
    market_comp_model = fit_component_model(train, market_comp_cols)
    scored["p_break_components_l2"] = comp_model.predict_proba(scored[comp_cols])[:, 1]
    scored["p_break_market_components_l2"] = market_comp_model.predict_proba(scored[market_comp_cols])[:, 1]

    metric_rows = []
    for period in ["train", "holdout", "forward"]:
        frame = scored[scored["period"].eq(period)].copy()
        for col, label in [
            ("p_break_market_raw", "market_implied_break"),
            ("p_break_physics", "mechanism_v4_physics"),
            ("p_break_market_physics", "market_plus_mechanism_v4"),
            ("p_break_components_l2", "learned_components_l2"),
            ("p_break_market_components_l2", "market_plus_components_l2"),
        ]:
            metric_rows.append(v3.metric_row(frame, col, label, period))

    rules = pd.concat([v3.build_ev_rules(scored), learned_ev_rules(scored)], ignore_index=True)
    components = v3.component_audit(scored)
    bins = v3.score_bins(scored)
    coverage = feature_coverage(scored)

    scored.to_csv(OUT_SCORED, index=False)
    pd.DataFrame(metric_rows).to_csv(OUT_METRICS, index=False)
    rules.to_csv(OUT_RULES, index=False)
    components.to_csv(OUT_COMPONENTS, index=False)
    bins.to_csv(OUT_BINS, index=False)
    coverage.to_csv(OUT_COVERAGE, index=False)

    hold_market = next(r for r in metric_rows if r["period"] == "holdout" and r["model"] == "market_implied_break")
    hold_phys = next(r for r in metric_rows if r["period"] == "holdout" and r["model"] == "mechanism_v4_physics")
    hold_mp = next(r for r in metric_rows if r["period"] == "holdout" and r["model"] == "market_plus_mechanism_v4")
    hold_learned = next(r for r in metric_rows if r["period"] == "holdout" and r["model"] == "learned_components_l2")
    hold_market_learned = next(r for r in metric_rows if r["period"] == "holdout" and r["model"] == "market_plus_components_l2")
    fwd_mp = next(r for r in metric_rows if r["period"] == "forward" and r["model"] == "market_plus_mechanism_v4")
    fwd_market_learned = next(r for r in metric_rows if r["period"] == "forward" and r["model"] == "market_plus_components_l2")
    hold_rule = rules[(rules["period"].eq("holdout")) & (rules["rule"].eq("physics_edge_ge_0.02"))].iloc[0]
    fwd_rule = rules[(rules["period"].eq("forward")) & (rules["rule"].eq("physics_edge_ge_0.02"))].iloc[0]
    hold_mp_rule = rules[(rules["period"].eq("holdout")) & (rules["rule"].eq("market_physics_edge_ge_0.00"))].iloc[0]
    hold_learned_rule = rules[
        (rules["period"].eq("holdout")) & (rules["rule"].eq("market_components_l2_edge_ge_0.00"))
    ].iloc[0]

    headline = (
        f"Mechanism v4 adds the missing physical features, but the hand-weighted score does not beat v3 or market and still does not create tradable peak-YES edge: "
        f"holdout physics AUC {hold_phys['auc_break']:.3f} vs market {hold_market['auc_break']:.3f}; "
        f"market+mechanism AUC {hold_mp['auc_break']:.3f} holdout and {fwd_mp['auc_break']:.3f} forward. "
        f"Learned components do not rescue it: holdout component AUC {hold_learned['auc_break']:.3f}, "
        f"market+components AUC {hold_market_learned['auc_break']:.3f} holdout / {fwd_market_learned['auc_break']:.3f} forward. "
        f"`physics edge>=2%` holdout ROI {v3.pct(hold_rule['roi'])}, CI "
        f"[{v3.pct(hold_rule['roi_ci95'][0])}, {v3.pct(hold_rule['roi_ci95'][1])}], "
        f"forward ROI {v3.pct(fwd_rule['roi'])}. "
        f"`market+mechanism edge>=0` holdout is {int(hold_mp_rule['rows'])} rows ROI {v3.pct(hold_mp_rule['roi'])}; "
        f"`market+components edge>=0` is {int(hold_learned_rule['rows'])} rows ROI {v3.pct(hold_learned_rule['roi'])}."
    )

    verdict = {
        "significance": "FAIL",
        "baseline": "FAIL",
        "forward": "FAIL",
        "conclusion": "inconclusive",
        "text": (
            "The added mechanism features help diagnosis but do not overturn the v3 conclusion. "
            "A learned component model has a better point estimate than the hand-weighted score, but holdout ROI confidence intervals still cross zero and forward support is only three dates. "
            "Solar/cadence/cloud-wind features should stay in forward logging; peak-YES live entry still needs either better price/timing execution or a materially stronger residual signal than current PIT data provides."
        ),
    }

    payload = {
        "generated_at_utc": now_utc(),
        "inputs": {
            "atlas_rows": rel(v3.ATLAS_ROWS),
            "feature_rows": [rel(p) for p in FEATURE_ROWS],
            "ext_dirs": [rel(p) for p in EXT_DIRS],
            "db": rel(v3.DB),
        },
        "data_self_check": self_check,
        "coverage": {
            "min_target_date": str(scored["target_date"].min()),
            "max_target_date": str(scored["target_date"].max()),
            "tradable_rows": int(len(scored)),
            "tradable_dates": int(scored["target_date"].nunique()),
            "tradable_cities": int(scored["city"].nunique()),
            "period_counts": scored["period"].value_counts().to_dict(),
        },
        "physics_weights": weights,
        "headline": headline,
        "metrics": metric_rows,
        "ev_rules": rules[
            rules["rule"].isin(
                [
                    "buy_all_peak_yes",
                    "physics_edge_ge_0.02",
                    "physics_edge_ge_0.05",
                    "physics_edge_ge_0.02_ask_50_70",
                    "market_physics_edge_ge_0.00",
                    "market_physics_edge_ge_0.02",
                    "market_physics_edge_ge_0.05",
                    "components_l2_edge_ge_0.02",
                    "market_components_l2_edge_ge_0.00",
                    "market_components_l2_edge_ge_0.02",
                ]
            )
        ].sort_values(["period", "rule"]).to_dict("records"),
        "feature_coverage": coverage.to_dict("records"),
        "component_audit": components.to_dict("records"),
        "score_bins": bins.to_dict("records"),
        "verdict": verdict,
        "outputs": {
            "scored_rows": rel(OUT_SCORED),
            "model_metrics": rel(OUT_METRICS),
            "ev_rules": rel(OUT_RULES),
            "component_audit": rel(OUT_COMPONENTS),
            "score_bins": rel(OUT_BINS),
            "feature_coverage": rel(OUT_COVERAGE),
            "json": rel(OUT_JSON),
            "markdown": rel(OUT_MD),
        },
    }
    OUT_JSON.write_text(json.dumps(v3.json_ready(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    build_markdown(v3.json_ready(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
