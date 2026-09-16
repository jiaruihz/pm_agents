#!/usr/bin/env python3
"""Current-YES first-principles survival model v2.

This is a mechanism-first research slice, not a live strategy:

1. Estimate whether the current running-max YES bracket survives using only
   point-in-time weather/forecast/regime/cadence features.
2. Compare that physical model with the market-implied probability.
3. Measure whether any residual edge survives after the market price.

Regime labels and physical components are features/diagnostics, not hard gates.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
SOURCE_ROWS = ROOT / "docs/analysis/2026-06/generated/current_yes_peak_yes_mechanism_v4/peak_yes_mechanism_v4_scored_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_first_principles_survival_v2"
OUT_SCORED = OUT_DIR / "current_yes_first_principles_survival_v2_scored_rows.csv"
OUT_METRICS = OUT_DIR / "current_yes_first_principles_survival_v2_model_metrics.csv"
OUT_RULES = OUT_DIR / "current_yes_first_principles_survival_v2_rule_comparison.csv"
OUT_BINS = OUT_DIR / "current_yes_first_principles_survival_v2_physics_bins.csv"
OUT_COMPONENTS = OUT_DIR / "current_yes_first_principles_survival_v2_component_audit.csv"
OUT_JSON = OUT_DIR / "summary.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-26-current-yes-first-principles-survival-v2.md"

TRAIN_END = "2026-05-31"
HOLDOUT_START = "2026-06-01"
FORWARD_START = "2026-06-21"
SEED = 20260626
BOOT_REPS = 500

WEATHER_CORE_NUMERIC = [
    "decision_hour_local",
    "current_native",
    "running_native",
    "decline_native",
    "running_value",
    "current_temp_c",
    "running_max_c",
    "tmpf_now",
    "dwpf_now",
    "dewpoint_depression_f",
    "relative_humidity_pct",
    "wind_speed_kt",
    "sky_cover_code",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "minutes_since_running_max",
    "forecast_max_native",
    "forecast_gap_to_running_native",
    "gfs_gap_to_running_native",
    "ecmwf_gap_to_running_native",
    "forecast_peak_hour_local",
    "forecast_peak_delta_hours_local",
    "forecast_peak_hour_spread",
    "gfs_forecast_peak_delta_hours_local",
    "ecmwf_forecast_peak_delta_hours_local",
    "forecast_peak_models_agree_le_1h",
]

MECHANISM_NUMERIC = [
    *WEATHER_CORE_NUMERIC,
    "obs_count_day",
    "obs_count_to_decision",
    "decision_obs_age_min",
    "obs_per_elapsed_hour",
    "forecast_hours_to_peak_max",
    "forecast_slope_to_peak_native_per_h",
    "forecast_curve_hourly_count_max",
    "solar_altitude_deg",
    "solar_altitude_pos",
    "solar_altitude_2h_deg",
    "solar_altitude_2h_pos",
    "solar_delta_2h_deg",
    "sky_ext_now",
    "d_sky_1h",
    "d_sky_3h",
    "sknt_ext_now",
    "d_sknt_1h",
    "d_sknt_3h",
    "dwpf_ext_now",
    "d_dwpf_1h",
    "d_dwpf_3h",
    "relh_ext_now",
    "d_relh_1h",
    "d_relh_3h",
    "wind_dir_sin",
    "wind_dir_cos",
    "d_sky_3h_x_solar",
    "cloud_clearing_x_solar",
    "wind_ramp_x_solar",
    "drying_x_solar",
]

COMPONENT_NUMERIC = [
    "comp_solar_remaining",
    "comp_forecast_runway",
    "comp_forecast_peak_ahead",
    "comp_forecast_disagreement",
    "comp_warming_momentum",
    "comp_fresh_high",
    "comp_not_faded",
    "comp_dry_clear_reheat",
    "comp_wind_mixing",
    "comp_day_regime_prior",
    "comp_intraday_prior",
    "comp_moisture_prior",
    "comp_running_max_prior",
    "comp_solar_geometry",
    "comp_forecast_slope_to_peak",
    "comp_obs_cadence_risk",
    "comp_cloud_clearing_solar",
    "comp_wind_ramp_solar",
    "comp_drying_solar",
]

CAT_FEATURES = [
    "unit",
    "forecast_source",
    "forecast_clock_source",
    "city_family",
    "solar_window",
    "day_regime",
    "moisture_cloud_regime",
    "wind_regime",
    "running_max_state",
    "intraday_state",
    "composite_regime",
]

IDENTITY_COLS = [
    "city",
    "target_date",
    "decision_hour_local",
    "decision_snapshot_ts_utc",
    "current_bracket",
    "current_yes_ask",
    "label_survive",
    "label_future_break",
    "period",
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def sigmoid(x: pd.Series) -> pd.Series:
    return 1.0 / (1.0 + np.exp(-x.clip(lower=-30, upper=30)))


def logit(p: pd.Series) -> pd.Series:
    p = p.clip(1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def pct(x: Any) -> str:
    if x is None:
        return "NA"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(v):
        return "NA"
    return f"{v * 100:+.1f}%"


def num(x: Any, digits: int = 3) -> str:
    if x is None:
        return "NA"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(v):
        return "NA"
    return f"{v:.{digits}f}"


def connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def db_self_check() -> dict[str, Any]:
    if not DB.exists():
        return {"db_exists": False}
    conn = connect_ro()
    try:
        fsc = conn.execute(
            "SELECT COUNT(*) AS rows, MIN(event_date) AS min_date, MAX(event_date) AS max_date, "
            "MAX(fact_built_at_utc) AS max_built_at FROM fact_signal_candidates"
        ).fetchone()
        ft = conn.execute(
            "SELECT COUNT(*) AS rows, MIN(target_date) AS min_date, MAX(target_date) AS max_date, "
            "MAX(fact_built_at_utc) AS max_built_at FROM fact_trades"
        ).fetchone()
        classes = [
            dict(row)
            for row in conn.execute(
                "SELECT trade_class, COUNT(*) AS rows, ROUND(SUM(cost_usd), 6) AS cost_usd "
                "FROM fact_trades GROUP BY trade_class ORDER BY trade_class"
            ).fetchall()
        ]
    finally:
        conn.close()
    return {
        "db_exists": True,
        "fact_signal_candidates": dict(fsc),
        "fact_trades": dict(ft),
        "fact_trades_by_class": classes,
    }


def one_hot_encoder() -> OneHotEncoder:
    try:
        return OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    except TypeError:
        return OneHotEncoder(handle_unknown="ignore", sparse=False)


def existing(cols: list[str], df: pd.DataFrame) -> list[str]:
    return [c for c in cols if c in df.columns]


def make_pipeline(numeric: list[str], categorical: list[str]) -> Pipeline:
    transformer = ColumnTransformer(
        transformers=[
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())]), numeric),
            ("cat", Pipeline([("imputer", SimpleImputer(strategy="most_frequent")), ("onehot", one_hot_encoder())]), categorical),
        ],
        remainder="drop",
    )
    return Pipeline(
        [
            ("prep", transformer),
            ("clf", LogisticRegression(max_iter=2000, C=0.35, solver="lbfgs")),
        ]
    )


def load_rows() -> pd.DataFrame:
    if not SOURCE_ROWS.exists():
        raise FileNotFoundError(SOURCE_ROWS)
    df = pd.read_csv(SOURCE_ROWS, low_memory=False)
    df["target_date"] = df["target_date"].astype(str)
    df["current_yes_ask"] = pd.to_numeric(df["current_yes_ask"], errors="coerce")
    df["label_survive"] = pd.to_numeric(df["label_survive"], errors="coerce")
    df = df[df["current_yes_ask"].between(0.01, 0.999) & df["label_survive"].isin([0, 1])].copy()
    df["label_future_break"] = 1 - df["label_survive"]
    df["market_survive_raw"] = df["current_yes_ask"].clip(0.001, 0.999)
    df["market_survive_logit"] = logit(df["market_survive_raw"])
    if "period" not in df.columns:
        df["period"] = np.select(
            [df["target_date"].le(TRAIN_END), df["target_date"].ge(FORWARD_START)],
            ["train", "forward"],
            default="holdout",
        )
    return df.reset_index(drop=True)


def fit_predict(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    train = out[out["target_date"].le(TRAIN_END)].copy()
    if train["label_survive"].nunique() < 2:
        raise RuntimeError("training split has only one class")

    model_specs = {
        "weather_core": (existing(WEATHER_CORE_NUMERIC, out), []),
        "mechanism_full": (existing(MECHANISM_NUMERIC + COMPONENT_NUMERIC, out), existing(CAT_FEATURES, out)),
        "components_only": (existing(COMPONENT_NUMERIC, out), existing(["day_regime", "intraday_state", "moisture_cloud_regime"], out)),
        "market_plus_mechanism": (existing(["market_survive_logit"] + MECHANISM_NUMERIC + COMPONENT_NUMERIC, out), existing(CAT_FEATURES, out)),
        "market_plus_components": (existing(["market_survive_logit"] + COMPONENT_NUMERIC, out), existing(["day_regime", "intraday_state", "moisture_cloud_regime"], out)),
    }
    out["p_survive_market_raw"] = out["market_survive_raw"]
    for name, (num_cols, cat_cols) in model_specs.items():
        if not num_cols and not cat_cols:
            out[f"p_survive_{name}"] = np.nan
            continue
        pipe = make_pipeline(num_cols, cat_cols)
        pipe.fit(train[num_cols + cat_cols], train["label_survive"].astype(int))
        out[f"p_survive_{name}"] = pipe.predict_proba(out[num_cols + cat_cols])[:, 1]

    for col in [
        "p_survive_weather_core",
        "p_survive_mechanism_full",
        "p_survive_components_only",
        "p_survive_market_plus_mechanism",
        "p_survive_market_plus_components",
    ]:
        out[f"edge_{col.removeprefix('p_survive_')}"] = out[col] - out["current_yes_ask"]
    out["physics_minus_market"] = out["p_survive_mechanism_full"] - out["p_survive_market_raw"]
    out["component_minus_market"] = out["p_survive_components_only"] - out["p_survive_market_raw"]
    return out


def model_metric_rows(scored: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    models = [
        ("market_raw", "p_survive_market_raw"),
        ("weather_core", "p_survive_weather_core"),
        ("mechanism_full", "p_survive_mechanism_full"),
        ("components_only", "p_survive_components_only"),
        ("market_plus_mechanism", "p_survive_market_plus_mechanism"),
        ("market_plus_components", "p_survive_market_plus_components"),
    ]
    for period in ["train", "holdout", "forward"]:
        sub = scored[scored["period"].eq(period)].copy()
        y = sub["label_survive"].astype(int)
        for model, col in models:
            pred = pd.to_numeric(sub[col], errors="coerce")
            mask = pred.notna()
            if mask.sum() == 0:
                continue
            yy = y[mask]
            pp = pred[mask].clip(1e-6, 1 - 1e-6)
            rows.append(
                {
                    "period": period,
                    "model": model,
                    "rows": int(mask.sum()),
                    "dates": int(sub.loc[mask, "target_date"].nunique()),
                    "actual_survive": float(yy.mean()),
                    "mean_p_survive": float(pp.mean()),
                    "auc": float(roc_auc_score(yy, pp)) if yy.nunique() == 2 else np.nan,
                    "brier": float(brier_score_loss(yy, pp)),
                    "logloss": float(log_loss(yy, pp, labels=[0, 1])),
                    "mean_edge_vs_ask": float((pp - sub.loc[mask, "current_yes_ask"]).mean()),
                }
            )
    return rows


def roi_for(sub: pd.DataFrame) -> float:
    cost = pd.to_numeric(sub["current_yes_ask"], errors="coerce").sum()
    if cost <= 0:
        return np.nan
    pnl = (pd.to_numeric(sub["label_survive"], errors="coerce") - pd.to_numeric(sub["current_yes_ask"], errors="coerce")).sum()
    return float(pnl / cost)


def bootstrap_roi_ci(sub: pd.DataFrame) -> list[float]:
    if sub.empty or sub["target_date"].nunique() < 2:
        return [np.nan, np.nan]
    rng = np.random.default_rng(SEED)
    dates = np.array(sorted(sub["target_date"].unique()))
    vals = []
    for _ in range(BOOT_REPS):
        sampled = rng.choice(dates, size=len(dates), replace=True)
        boot = pd.concat([sub[sub["target_date"].eq(d)] for d in sampled], ignore_index=True)
        vals.append(roi_for(boot))
    return [float(np.nanpercentile(vals, 2.5)), float(np.nanpercentile(vals, 97.5))]


def rule_metric_rows(scored: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rules = {
        "buy_all_current_yes": lambda d: pd.Series(True, index=d.index),
        "weather_core_edge_ge_0.02": lambda d: d["edge_weather_core"].ge(0.02),
        "mechanism_full_edge_ge_0.00": lambda d: d["edge_mechanism_full"].ge(0.00),
        "mechanism_full_edge_ge_0.02": lambda d: d["edge_mechanism_full"].ge(0.02),
        "mechanism_full_edge_ge_0.05": lambda d: d["edge_mechanism_full"].ge(0.05),
        "components_only_edge_ge_0.02": lambda d: d["edge_components_only"].ge(0.02),
        "market_plus_mechanism_edge_ge_0.00": lambda d: d["edge_market_plus_mechanism"].ge(0.00),
        "market_plus_mechanism_edge_ge_0.02": lambda d: d["edge_market_plus_mechanism"].ge(0.02),
        "market_plus_components_edge_ge_0.02": lambda d: d["edge_market_plus_components"].ge(0.02),
        "physics_market_agree_positive_edge": lambda d: d["edge_market_plus_mechanism"].ge(0.02) & d["physics_minus_market"].ge(-0.02),
    }
    for period in ["train", "holdout", "forward"]:
        base = scored[scored["period"].eq(period)].copy()
        for name, fn in rules.items():
            pick = base[fn(base).fillna(False)].copy()
            rows.append(
                {
                    "period": period,
                    "rule": name,
                    "rows": int(len(pick)),
                    "dates": int(pick["target_date"].nunique()) if not pick.empty else 0,
                    "cities": int(pick["city"].nunique()) if not pick.empty else 0,
                    "avg_ask": float(pick["current_yes_ask"].mean()) if not pick.empty else np.nan,
                    "win_rate": float(pick["label_survive"].mean()) if not pick.empty else np.nan,
                    "roi": roi_for(pick) if not pick.empty else np.nan,
                    "ci95": bootstrap_roi_ci(pick),
                    "avg_physics_minus_market": float(pick["physics_minus_market"].mean()) if not pick.empty else np.nan,
                }
            )
    return rows


def bin_rows(scored: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for period in ["holdout", "forward"]:
        sub = scored[scored["period"].eq(period)].copy()
        for col in ["p_survive_mechanism_full", "physics_minus_market"]:
            valid = sub[pd.to_numeric(sub[col], errors="coerce").notna()].copy()
            if valid.empty:
                continue
            try:
                valid["bucket"] = pd.qcut(valid[col], q=min(5, valid[col].nunique()), duplicates="drop")
            except ValueError:
                continue
            for bucket, g in valid.groupby("bucket", observed=True):
                rows.append(
                    {
                        "period": period,
                        "score": col,
                        "bucket": str(bucket),
                        "rows": int(len(g)),
                        "dates": int(g["target_date"].nunique()),
                        "actual_survive": float(g["label_survive"].mean()),
                        "avg_ask": float(g["current_yes_ask"].mean()),
                        "avg_model_p": float(g[col].mean()),
                        "avg_edge": float((g["p_survive_mechanism_full"] - g["current_yes_ask"]).mean()),
                        "buy_all_roi": roi_for(g),
                    }
                )
    return rows


def component_rows(scored: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for period in ["holdout", "forward"]:
        sub = scored[scored["period"].eq(period)].copy()
        y_break = sub["label_future_break"].astype(float)
        residual_break = y_break - (1.0 - sub["current_yes_ask"])
        for col in existing(COMPONENT_NUMERIC + [
            "physics_break_score_raw",
            "forecast_slope_to_peak_native_per_h",
            "cloud_clearing_x_solar",
            "wind_ramp_x_solar",
            "drying_x_solar",
        ], sub):
            x = pd.to_numeric(sub[col], errors="coerce")
            mask = x.notna()
            if mask.sum() < 20:
                continue
            rows.append(
                {
                    "period": period,
                    "component": col,
                    "rows": int(mask.sum()),
                    "auc_future_break": float(roc_auc_score(y_break[mask], x[mask])) if y_break[mask].nunique() == 2 else np.nan,
                    "corr_future_break": float(np.corrcoef(x[mask], y_break[mask])[0, 1]) if mask.sum() > 2 else np.nan,
                    "corr_market_residual_break": float(np.corrcoef(x[mask], residual_break[mask])[0, 1]) if mask.sum() > 2 else np.nan,
                    "mean": float(x[mask].mean()),
                }
            )
    return rows


def write_report(payload: dict[str, Any], metrics: pd.DataFrame, rules: pd.DataFrame, bins: pd.DataFrame, components: pd.DataFrame) -> None:
    def metric_line(period: str, model: str) -> str:
        row = metrics[metrics["period"].eq(period) & metrics["model"].eq(model)]
        if row.empty:
            return "NA"
        r = row.iloc[0]
        return f"AUC {num(r['auc'])}, Brier {num(r['brier'])}, edge {pct(r['mean_edge_vs_ask'])}"

    lines = [
        "# Current-YES First-Principles Survival V2",
        "",
        "Status: `research_only_not_live`",
        f"Generated UTC: `{payload['generated_at_utc']}`",
        "",
        "## Human Summary",
        "",
        "这版不假设一定能打败市场，而是把问题拆成：天气机制能不能解释 `current bracket survives`，",
        "以及这些机制在扣掉盘口以后还剩多少 residual。输入是 v4 已物化的 PIT 物理特征：METAR core、forecast peak clock、",
        "太阳高度、观测 cadence、forecast slope、云/风/湿度变化和 intraday regime；后验字段不进训练。",
        "",
        f"- Rows: `{payload['data']['rows']}` / dates `{payload['data']['min_target_date']}`..`{payload['data']['max_target_date']}` / cities `{payload['data']['cities']}`.",
        f"- Train: `<= {TRAIN_END}`; holdout: `{HOLDOUT_START}`..`2026-06-20`; forward-like: `>= {FORWARD_START}`.",
        f"- Source rows: `{payload['input']}`.",
        "",
        "## Model Metrics",
        "",
        "| period | market raw | weather core | mechanism full | market + mechanism |",
        "|---|---|---|---|---|",
    ]
    for period in ["holdout", "forward"]:
        lines.append(
            f"| {period} | {metric_line(period, 'market_raw')} | {metric_line(period, 'weather_core')} | "
            f"{metric_line(period, 'mechanism_full')} | {metric_line(period, 'market_plus_mechanism')} |"
        )

    top_rules = pd.concat(
        [
            rules[rules["period"].eq("holdout")].sort_values("roi", ascending=False, na_position="last").head(7),
            rules[rules["period"].eq("forward")].sort_values("roi", ascending=False, na_position="last").head(7),
        ],
        ignore_index=True,
    )
    lines += [
        "",
        "## Trading Sanity Check",
        "",
        "| period | rule | rows | dates | avg ask | win | ROI | CI |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for _, row in top_rules.iterrows():
        ci = row.get("ci95")
        ci_txt = "NA" if not isinstance(ci, list) else f"[{pct(ci[0])}, {pct(ci[1])}]"
        lines.append(
            f"| {row['period']} | `{row['rule']}` | {int(row['rows'])} | {int(row['dates'])} | "
            f"{num(row['avg_ask'])} | {pct(row['win_rate'])} | {pct(row['roi'])} | {ci_txt} |"
        )

    component_top = components[components["period"].eq("holdout")].copy()
    component_top = component_top.sort_values("auc_future_break", ascending=False).head(12)
    lines += [
        "",
        "## Mechanism Diagnostics",
        "",
        "| component | rows | AUC future-break | corr break | corr market residual | mean |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for _, row in component_top.iterrows():
        lines.append(
            f"| `{row['component']}` | {int(row['rows'])} | {num(row['auc_future_break'])} | "
            f"{num(row['corr_future_break'])} | {num(row['corr_market_residual_break'])} | {num(row['mean'])} |"
        )

    bin_top = bins[bins["period"].eq("holdout") & bins["score"].eq("p_survive_mechanism_full")].copy()
    lines += [
        "",
        "## Physics Probability Buckets",
        "",
        "| period | score | bucket | rows | survive | avg ask | model p | buy-all ROI |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for _, row in bin_top.iterrows():
        lines.append(
            f"| {row['period']} | `{row['score']}` | `{row['bucket']}` | {int(row['rows'])} | "
            f"{pct(row['actual_survive'])} | {num(row['avg_ask'])} | {num(row['avg_model_p'])} | {pct(row['buy_all_roi'])} |"
        )

    lines += [
        "",
        "## Verdict",
        "",
        "significance=FAIL / baseline=FAIL / forward=FAIL / conclusion=inconclusive",
        "",
        "第一性原理特征确实能解释温度路径，但这次仍没有给出可恢复 live 的 residual edge。",
        "如果要继续，最有价值的不是再加 hard gate，而是把这些机制作为 forward logging 和风险分层，",
        "等更多 settled forward dates 后再检查 `market + mechanism` 是否稳定优于 raw market。",
        "",
        "## 8-Ring Coverage",
        "",
        "- Covered: signal discrimination, probability calibration, date bootstrap ROI sanity check, market-price baseline, target-date clustered CI.",
        "- Partially covered: execution microstructure uses historical current YES ask only, not live maker/taker fill simulation.",
        "- Not live-covered: capacity, real-time shadow fills, post-2026-06-23 settled labels for this feature layer.",
        "",
        "## Outputs",
        "",
        f"- scored_rows: `{rel(OUT_SCORED)}`",
        f"- metrics: `{rel(OUT_METRICS)}`",
        f"- rules: `{rel(OUT_RULES)}`",
        f"- bins: `{rel(OUT_BINS)}`",
        f"- components: `{rel(OUT_COMPONENTS)}`",
        f"- summary: `{rel(OUT_JSON)}`",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    rows = load_rows()
    scored = fit_predict(rows)
    metrics = pd.DataFrame(model_metric_rows(scored))
    rules = pd.DataFrame(rule_metric_rows(scored))
    bins = pd.DataFrame(bin_rows(scored))
    components = pd.DataFrame(component_rows(scored))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    keep = [c for c in IDENTITY_COLS if c in scored.columns]
    score_cols = [c for c in scored.columns if c.startswith("p_survive_") or c.startswith("edge_")]
    extra = existing(["physics_minus_market", "component_minus_market", *CAT_FEATURES, *COMPONENT_NUMERIC], scored)
    scored[keep + score_cols + extra].to_csv(OUT_SCORED, index=False)
    metrics.to_csv(OUT_METRICS, index=False)
    rules.to_csv(OUT_RULES, index=False)
    bins.to_csv(OUT_BINS, index=False)
    components.to_csv(OUT_COMPONENTS, index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "status": "research_only_not_live",
        "input": rel(SOURCE_ROWS),
        "outputs": {
            "scored_rows": rel(OUT_SCORED),
            "metrics": rel(OUT_METRICS),
            "rules": rel(OUT_RULES),
            "bins": rel(OUT_BINS),
            "components": rel(OUT_COMPONENTS),
            "report": rel(OUT_MD),
        },
        "data": {
            "rows": int(len(rows)),
            "cities": int(rows["city"].nunique()),
            "dates": int(rows["target_date"].nunique()),
            "min_target_date": str(rows["target_date"].min()),
            "max_target_date": str(rows["target_date"].max()),
            "period_rows": {k: int(v) for k, v in rows["period"].value_counts().sort_index().to_dict().items()},
        },
        "db_self_check": db_self_check(),
        "model_metrics": metrics.to_dict(orient="records"),
        "rule_comparison": rules.to_dict(orient="records"),
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(payload, metrics, rules, bins, components)
    print(json.dumps({"data": payload["data"], "outputs": payload["outputs"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
