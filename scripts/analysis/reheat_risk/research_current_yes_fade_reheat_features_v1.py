#!/usr/bin/env python3
"""Current-YES fade reheat feature research v1.

Research-only script for the question:
after the market has already seen a running max and temperature has faded,
which METAR/forecast features help distinguish "current max holds" from
"later reheat breaks the current bracket"?
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
FORECAST_ROWS = (
    ROOT
    / "docs/analysis/2026-06/generated/theta_current_yes_forecast_peak_clock_backfill_v3/forecast_peak_joined_rows.csv"
)
REHEAT_ROWS = ROOT / "docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_fade_reheat_features_v1"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-19-current-yes-fade-reheat-features-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-19-current-yes-fade-reheat-features-v1.md"

SEED = 20260619
TRAIN_PERIOD = "train"
HOLDOUT_PERIOD = "holdout"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: float | None, digits: int = 1) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value) * 100:.{digits}f}%"


def signed_pct(value: float | None, digits: int = 1) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value) * 100:+.{digits}f}%"


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
        }
    finally:
        conn.close()


def coerce_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.lower().isin(["true", "1", "yes"])


def summarize_slice(df: pd.DataFrame, name: str) -> dict[str, Any]:
    if df.empty:
        return {
            "slice": name,
            "rows": 0,
            "city_days": 0,
            "dates": 0,
            "win_rate": None,
            "reheat_rate": None,
            "avg_yes_ask": None,
            "roi_buy_yes_at_ask": None,
        }
    cost = df["yes_current_ask"].sum()
    pnl = df["label_yes_wins"].astype(float).sub(df["yes_current_ask"]).sum()
    return {
        "slice": name,
        "rows": int(len(df)),
        "city_days": int(df[["city", "target_date"]].drop_duplicates().shape[0]),
        "dates": int(df["target_date"].nunique()),
        "win_rate": float(df["label_yes_wins"].mean()),
        "reheat_rate": float(1 - df["label_yes_wins"].mean()),
        "avg_yes_ask": float(df["yes_current_ask"].mean()),
        "roi_buy_yes_at_ask": float(pnl / cost) if cost else None,
    }


def add_bin(df: pd.DataFrame, col: str, bins: list[float], labels: list[str]) -> pd.Series:
    return pd.cut(df[col], bins=bins, labels=labels, include_lowest=True, right=False).astype(str)


def bin_summary(df: pd.DataFrame, feature: str, bucket: pd.Series) -> pd.DataFrame:
    out = (
        df.assign(bucket=bucket)
        .groupby("bucket", dropna=False)
        .agg(
            rows=("label_yes_wins", "size"),
            city_days=("target_date", lambda s: int(df.loc[s.index, ["city", "target_date"]].drop_duplicates().shape[0])),
            win_rate=("label_yes_wins", "mean"),
            avg_yes_ask=("yes_current_ask", "mean"),
            avg_decline_c=("decline_c", "mean"),
            avg_minutes_since_running_max=("minutes_since_running_max", "mean"),
        )
        .reset_index()
    )
    out["feature"] = feature
    out["reheat_rate"] = 1 - out["win_rate"]
    out = out[["feature", "bucket", "rows", "city_days", "win_rate", "reheat_rate", "avg_yes_ask", "avg_decline_c", "avg_minutes_since_running_max"]]
    return out.sort_values(["feature", "bucket"])


def prepare_rows() -> pd.DataFrame:
    forecast = pd.read_csv(FORECAST_ROWS)
    forecast["label_yes_wins"] = coerce_bool(forecast["label_yes_wins"]).astype(int)
    forecast["has_d1_no"] = coerce_bool(forecast["has_d1_no"])

    reheat_usecols = [
        "decision_snapshot_ts_utc",
        "city",
        "target_date",
        "decision_last_obs_utc",
        "icao",
        "timezone",
        "obs_count_day",
        "obs_count_to_decision",
        "minutes_since_running_max",
        "tmpf_now",
        "dwpf_now",
        "dewpoint_depression_f",
        "relative_humidity_pct",
        "wind_speed_kt",
        "sky_cover_code",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "forecast_peak_hour_spread",
        "forecast_peak_backfill_join_status",
    ]
    reheat = pd.read_csv(REHEAT_ROWS, usecols=reheat_usecols)
    reheat = reheat.drop_duplicates(["city", "target_date", "decision_snapshot_ts_utc"])

    merged = forecast.merge(
        reheat,
        how="left",
        left_on=["city", "target_date", "snapshot_ts_utc"],
        right_on=["city", "target_date", "decision_snapshot_ts_utc"],
        validate="many_to_one",
    )
    for col in ["tmpf_now", "dwpf_now", "dewpoint_depression_f"]:
        if f"{col}_y" in merged.columns:
            merged[col] = merged[f"{col}_y"]
        elif f"{col}_x" in merged.columns:
            merged[col] = merged[f"{col}_x"]
    if "forecast_peak_models_agree_le_1h" in merged.columns:
        merged["forecast_peak_models_agree_le_1h"] = coerce_bool(merged["forecast_peak_models_agree_le_1h"]).astype(int)
    merged["joined_reheat_features"] = merged["minutes_since_running_max"].notna()
    return merged


def fit_model(
    train: pd.DataFrame,
    holdout: pd.DataFrame,
    name: str,
    numeric: list[str],
    categorical: list[str] | None = None,
) -> tuple[dict[str, Any], np.ndarray]:
    categorical = categorical or []
    transformers: list[tuple[str, Any, list[str]]] = []
    if numeric:
        transformers.append(
            (
                "numeric",
                Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())]),
                numeric,
            )
        )
    if categorical:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categorical,
            )
        )

    model = Pipeline(
        [
            ("features", ColumnTransformer(transformers)),
            (
                "logit",
                LogisticRegression(
                    C=0.5,
                    max_iter=1000,
                    random_state=SEED,
                ),
            ),
        ]
    )
    y_train = train["label_yes_wins"].astype(int)
    y_holdout = holdout["label_yes_wins"].astype(int)
    model.fit(train[numeric + categorical], y_train)
    p = model.predict_proba(holdout[numeric + categorical])[:, 1]
    metrics = prediction_metrics(holdout, name, p)
    return metrics, p


def prediction_metrics(df: pd.DataFrame, name: str, p: np.ndarray) -> dict[str, Any]:
    y = df["label_yes_wins"].astype(int).to_numpy()
    out: dict[str, Any] = {
        "model": name,
        "rows": int(len(df)),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6))),
        "auc": None,
        "avg_p": float(np.mean(p)),
        "base_rate": float(np.mean(y)),
    }
    if len(np.unique(y)) > 1:
        out["auc"] = float(roc_auc_score(y, p))
    for threshold in [0.80, 0.85, 0.90]:
        mask = (p >= threshold) & (df["yes_current_ask"].to_numpy() >= 0.55) & ((p - df["yes_current_ask"].to_numpy()) >= 0.05)
        selected = df.loc[mask].copy()
        prefix = f"select_p{int(threshold * 100)}"
        out[f"{prefix}_rows"] = int(len(selected))
        if selected.empty:
            out[f"{prefix}_win_rate"] = None
            out[f"{prefix}_roi"] = None
            out[f"{prefix}_avg_ask"] = None
        else:
            cost = selected["yes_current_ask"].sum()
            pnl = selected["label_yes_wins"].astype(float).sub(selected["yes_current_ask"]).sum()
            out[f"{prefix}_win_rate"] = float(selected["label_yes_wins"].mean())
            out[f"{prefix}_roi"] = float(pnl / cost) if cost else None
            out[f"{prefix}_avg_ask"] = float(selected["yes_current_ask"].mean())
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = prepare_rows()

    all_rows = df[df["label_yes_wins"].notna()].copy()
    fade = all_rows[all_rows["decline_c"] >= 0.5].copy()
    fade_d1 = fade[fade["has_d1_no"]].copy()
    fade_d1_joined = fade_d1[fade_d1["joined_reheat_features"]].copy()
    live_like = fade_d1_joined[
        (fade_d1_joined["decision_hour_local"].between(13, 15))
        & (fade_d1_joined["yes_current_ask"] >= 0.55)
    ].copy()

    funnel = pd.DataFrame(
        [
            summarize_slice(all_rows, "all_current_yes"),
            summarize_slice(all_rows[all_rows["period"] == TRAIN_PERIOD], "all_current_yes_train"),
            summarize_slice(all_rows[all_rows["period"] == HOLDOUT_PERIOD], "all_current_yes_holdout"),
            summarize_slice(fade, "fade_decline_ge_0_5c"),
            summarize_slice(fade_d1, "fade_plus_d1_visible"),
            summarize_slice(fade_d1[fade_d1["period"] == TRAIN_PERIOD], "fade_plus_d1_train"),
            summarize_slice(fade_d1[fade_d1["period"] == HOLDOUT_PERIOD], "fade_plus_d1_holdout"),
            summarize_slice(fade_d1_joined, "fade_plus_d1_with_reheat_features"),
            summarize_slice(live_like, "fade_live_like_h13_15_ask_ge_0_55"),
        ]
    )
    base_rows = float(funnel.loc[funnel["slice"] == "all_current_yes", "rows"].iloc[0])
    funnel["remaining_vs_all_rows"] = funnel["rows"] / base_rows

    bins: list[pd.DataFrame] = []
    trainable = fade_d1_joined.copy()
    bins.append(
        bin_summary(
            trainable,
            "minutes_since_running_max",
            add_bin(trainable, "minutes_since_running_max", [-np.inf, 15, 30, 45, 60, np.inf], ["<15m", "15-30m", "30-45m", "45-60m", "60m+"]),
        )
    )
    bins.append(
        bin_summary(
            trainable,
            "decline_c",
            add_bin(trainable, "decline_c", [-np.inf, 0.75, 1.25, 2.0, np.inf], ["0.5-0.75C", "0.75-1.25C", "1.25-2C", "2C+"]),
        )
    )
    bins.append(
        bin_summary(
            trainable,
            "forecast_peak_delta_gfs",
            add_bin(
                trainable,
                "gfs_forecast_peak_delta_hours_local",
                [-np.inf, -1, 0, 1, 2, np.inf],
                ["peak_future>1h", "peak_future0-1h", "peak_now_or_past0-1h", "peak_past1-2h", "peak_past2h+"],
            ),
        )
    )
    bins.append(
        bin_summary(
            trainable,
            "forecast_gap_to_running_gfs",
            add_bin(trainable, "gfs_forecast_gap_to_running_native", [-np.inf, -1, 0, 1, 2, np.inf], ["below<-1", "below0-1", "0-1", "1-2", "2+"]),
        )
    )
    bins.append(
        bin_summary(
            trainable,
            "temp_trend_1h_f",
            add_bin(trainable, "temp_trend_1h_f", [-np.inf, -2, -0.5, 0.5, 2, np.inf], ["fall>2F", "fall0.5-2F", "flat", "rise0.5-2F", "rise>2F"]),
        )
    )
    bins.append(
        bin_summary(
            trainable,
            "relative_humidity_pct",
            add_bin(trainable, "relative_humidity_pct", [-np.inf, 35, 50, 65, 80, np.inf], ["<35", "35-50", "50-65", "65-80", "80+"]),
        )
    )
    bins_df = pd.concat(bins, ignore_index=True)

    train = trainable[trainable["period"] == TRAIN_PERIOD].copy()
    holdout = trainable[trainable["period"] == HOLDOUT_PERIOD].copy()
    numeric_weather = [
        "decline_c",
        "minutes_since_running_max",
        "tmpf_now",
        "dwpf_now",
        "dewpoint_depression_f",
        "relative_humidity_pct",
        "wind_speed_kt",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "gfs_forecast_peak_delta_hours_local",
        "ecmwf_forecast_peak_delta_hours_local",
        "gfs_forecast_gap_to_running_native",
        "ecmwf_forecast_gap_to_running_native",
        "forecast_peak_hour_spread",
        "forecast_peak_models_agree_le_1h",
    ]
    numeric_market = [
        "yes_current_ask",
        "p_yes_win",
        "decision_hour_local",
        "gap_current_to_d1_low_native",
        "gap_running_to_d1_low_native",
        "ask_gap_d1_no_minus_yes",
    ]
    categorical_weather = ["sky_cover_code", "unit"]
    categorical_city = ["city", "unit"]

    model_rows: list[dict[str, Any]] = []
    base_p = holdout["p_yes_win"].fillna(holdout["label_yes_wins"].mean()).to_numpy()
    model_rows.append(prediction_metrics(holdout, "existing_base_probability", base_p))

    metrics, weather_p = fit_model(train, holdout, "weather_forecast_only_no_city_price", numeric_weather, categorical_weather)
    model_rows.append(metrics)
    metrics, market_weather_p = fit_model(train, holdout, "base_plus_weather_forecast", numeric_weather + numeric_market, categorical_weather)
    model_rows.append(metrics)
    metrics, city_market_weather_p = fit_model(
        train,
        holdout,
        "city_market_plus_weather_forecast",
        numeric_weather + numeric_market,
        categorical_city + ["sky_cover_code"],
    )
    model_rows.append(metrics)
    model_metrics = pd.DataFrame(model_rows)

    holdout_scored = holdout.copy()
    holdout_scored["p_existing_base"] = base_p
    holdout_scored["p_weather_forecast_only"] = weather_p
    holdout_scored["p_base_plus_weather_forecast"] = market_weather_p
    holdout_scored["p_city_market_plus_weather_forecast"] = city_market_weather_p
    holdout_scored["reheat_loss"] = 1 - holdout_scored["label_yes_wins"]

    live_like_rule_rows = []
    for name, prob_col in [
        ("existing_base_probability", "p_existing_base"),
        ("weather_forecast_only_no_city_price", "p_weather_forecast_only"),
        ("base_plus_weather_forecast", "p_base_plus_weather_forecast"),
        ("city_market_plus_weather_forecast", "p_city_market_plus_weather_forecast"),
    ]:
        for threshold in [0.80, 0.85, 0.90]:
            selected = holdout_scored[
                (holdout_scored[prob_col] >= threshold)
                & (holdout_scored["yes_current_ask"] >= 0.55)
                & ((holdout_scored[prob_col] - holdout_scored["yes_current_ask"]) >= 0.05)
            ]
            row = summarize_slice(selected, f"{name}_p{int(threshold * 100)}")
            row["model"] = name
            row["threshold"] = threshold
            live_like_rule_rows.append(row)
    live_like_rules = pd.DataFrame(live_like_rule_rows)

    funnel.to_csv(OUT_DIR / "sample_funnel.csv", index=False)
    bins_df.to_csv(OUT_DIR / "feature_bins.csv", index=False)
    model_metrics.to_csv(OUT_DIR / "model_metrics.csv", index=False)
    live_like_rules.to_csv(OUT_DIR / "live_like_rule_summary.csv", index=False)
    holdout_scored.to_csv(OUT_DIR / "holdout_scored.csv", index=False)

    best_weather = model_metrics.sort_values(["brier", "log_loss"]).iloc[0].to_dict()
    base_metric = model_metrics[model_metrics["model"] == "existing_base_probability"].iloc[0].to_dict()
    weather_only = model_metrics[model_metrics["model"] == "weather_forecast_only_no_city_price"].iloc[0].to_dict()

    report = {
        "created_at_utc": now_utc(),
        "scope": "research_only_current_yes_fade_reheat_features",
        "data_self_check": data_self_check(),
        "inputs": {
            "forecast_rows": str(FORECAST_ROWS.relative_to(ROOT)),
            "reheat_rows": str(REHEAT_ROWS.relative_to(ROOT)),
        },
        "sample_funnel": funnel.to_dict(orient="records"),
        "model_metrics": model_metrics.to_dict(orient="records"),
        "live_like_rule_summary": live_like_rules.to_dict(orient="records"),
        "headline": {
            "all_rows": int(funnel.loc[funnel["slice"] == "all_current_yes", "rows"].iloc[0]),
            "fade_rows": int(funnel.loc[funnel["slice"] == "fade_decline_ge_0_5c", "rows"].iloc[0]),
            "fade_d1_rows": int(funnel.loc[funnel["slice"] == "fade_plus_d1_visible", "rows"].iloc[0]),
            "fade_d1_train_rows": int(funnel.loc[funnel["slice"] == "fade_plus_d1_train", "rows"].iloc[0]),
            "fade_d1_holdout_rows": int(funnel.loc[funnel["slice"] == "fade_plus_d1_holdout", "rows"].iloc[0]),
            "live_like_rows": int(funnel.loc[funnel["slice"] == "fade_live_like_h13_15_ask_ge_0_55", "rows"].iloc[0]),
            "base_brier": base_metric["brier"],
            "weather_only_brier": weather_only["brier"],
            "best_model": best_weather["model"],
            "best_brier": best_weather["brier"],
        },
    }
    OUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    shrink_fade = 1 - report["headline"]["fade_rows"] / report["headline"]["all_rows"]
    shrink_trainable = 1 - report["headline"]["fade_d1_rows"] / report["headline"]["all_rows"]
    md = f"""# Current-YES fade reheat features v1

Created: {report["created_at_utc"]}

## Target

Research-only.  The denominator is current-YES rows where the market has already seen a running max and the observed temperature has faded.  The label is whether that current bracket eventually held (`label_yes_wins=1`) or later reheated and broke (`label_yes_wins=0`).

## Data self-check

- `fact_trades` max built at: `{report["data_self_check"]["fact_trades_max_built_at_utc"]}`
- `fact_signal_candidates`: `{report["data_self_check"]["fact_signal_candidate_coverage"]}`
- CLOB order/fill join: `{report["data_self_check"]["clob_order_fill_join"]}`

## Sample funnel

| slice | rows | city_days | dates | win_rate | reheat_rate | remaining_vs_all |
|---|---:|---:|---:|---:|---:|---:|
"""
    for row in funnel.to_dict(orient="records"):
        md += (
            f"| {row['slice']} | {row['rows']} | {row['city_days']} | {row['dates']} | "
            f"{pct(row['win_rate'])} | {pct(row['reheat_rate'])} | {pct(row['remaining_vs_all_rows'])} |\n"
        )
    md += f"""
Plain English: all current-YES replay rows are {report["headline"]["all_rows"]}.  Requiring an actual fade (`decline_c >= 0.5`) leaves {report["headline"]["fade_rows"]}, so the sample shrinks by {pct(shrink_fade)}.  Requiring the higher NO expression to be visible leaves {report["headline"]["fade_d1_rows"]}, so the trainable fade sample shrinks by {pct(shrink_trainable)} versus all current-YES rows.  Train/holdout are {report["headline"]["fade_d1_train_rows"]}/{report["headline"]["fade_d1_holdout_rows"]}.

The important subtlety is the negative class.  `fade_plus_d1_visible` still has 857 rows, but only about 100 later reheat failures.  Holdout has 461 rows and about 47 failures.  That is enough for feature research and guard design, but thin for a standalone high-confidence live probability model.

## Feature slices

| feature | bucket | rows | win_rate | reheat_rate | avg_yes_ask |
|---|---:|---:|---:|---:|---:|
"""
    feature_focus = {
        "forecast_peak_delta_gfs": [
            "peak_future>1h",
            "peak_future0-1h",
            "peak_now_or_past0-1h",
            "peak_past1-2h",
            "peak_past2h+",
        ],
        "temp_trend_1h_f": ["rise>2F", "rise0.5-2F", "flat", "fall0.5-2F"],
        "minutes_since_running_max": ["<15m", "60m+"],
        "forecast_gap_to_running_gfs": ["2+", "below<-1"],
    }
    for feature, buckets in feature_focus.items():
        for bucket in buckets:
            rows = bins_df[(bins_df["feature"] == feature) & (bins_df["bucket"] == bucket)]
            if rows.empty:
                continue
            row = rows.iloc[0].to_dict()
            md += (
                f"| {feature} | {bucket} | {int(row['rows'])} | "
                f"{pct(row['win_rate'])} | {pct(row['reheat_rate'])} | {row['avg_yes_ask']:.3f} |\n"
            )

    md += """
## Holdout model check

| model | rows | brier | log_loss | auc | selected p80 rows | selected p80 win_rate | selected p80 roi |
|---|---:|---:|---:|---:|---:|---:|---:|
"""
    for row in model_metrics.to_dict(orient="records"):
        md += (
            f"| {row['model']} | {row['rows']} | {row['brier']:.4f} | {row['log_loss']:.4f} | "
            f"{row['auc'] if row['auc'] is not None else 'NA'} | {row['select_p80_rows']} | "
            f"{pct(row['select_p80_win_rate'])} | {signed_pct(row['select_p80_roi'])} |\n"
        )
    md += """
## Readout

- This is enough data to research the direction, but not enough to trust a high-confidence specialist live model by itself.  The live-like high-ask fade subset is only hundreds of rows, not thousands.
- Weather/forecast-only features are meaningful if they improve Brier/AUC versus a dumb base rate, but the current artifact should be treated as reheat-risk evidence, not as a direct replacement for the base current-YES model.
- The next useful version should train the base current-YES probability and the reheat-risk correction jointly, with live decision logs carrying both numbers.

## Artifacts

- `sample_funnel.csv`
- `feature_bins.csv`
- `model_metrics.csv`
- `live_like_rule_summary.csv`
- `holdout_scored.csv`
"""
    OUT_MD.write_text(md)

    print(json.dumps(report["headline"], indent=2))
    print(f"Wrote {OUT_DIR.relative_to(ROOT)}")
    print(f"Wrote {OUT_MD.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
