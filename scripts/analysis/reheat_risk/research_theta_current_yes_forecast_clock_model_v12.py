#!/usr/bin/env python3
"""Evaluate forecast-clock features inside the current-YES probability model.

Evidence layer: historical orderbook replay joined with Open-Meteo historical
forecast backfill from v3.  This script does not change live configuration.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
SOURCE_ROWS = (
    ROOT
    / "docs/analysis/2026-06/generated/theta_current_yes_forecast_peak_clock_backfill_v3"
    / "forecast_peak_joined_rows.csv"
)
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_current_yes_forecast_clock_model_v12"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-17-theta-current-yes-forecast-clock-model-v12.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-17-theta-current-yes-forecast-clock-model-v12.md"

SEED = 20260617

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
PRICE_FEATURES = ["yes_current_ask", "log_yes_size", "d1_no_ask", "ask_gap_d1_no_minus_yes"]
FORECAST_CLOCK_FEATURES = [
    "gfs_forecast_peak_delta_hours_local",
    "ecmwf_forecast_peak_delta_hours_local",
    "gfs_forecast_gap_to_running_native",
    "ecmwf_forecast_gap_to_running_native",
    "gfs_forecast_max_native",
    "ecmwf_forecast_max_native",
    "gfs_forecast_peak_hour_sin",
    "gfs_forecast_peak_hour_cos",
    "ecmwf_forecast_peak_hour_sin",
    "ecmwf_forecast_peak_hour_cos",
    "forecast_peak_hour_abs_diff",
    "forecast_peak_models_agree_le_1h_num",
    "gfs_forecast_inside_current_bracket_num",
    "ecmwf_forecast_inside_current_bracket_num",
]
CAT_FEATURES = ["city", "unit"]


@dataclass(frozen=True)
class ModelSpec:
    name: str
    kind: str
    numeric_features: list[str]
    categorical_features: list[str]
    pred_col: str
    note: str


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: float | None, signed: bool = True) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    sign = "+" if signed else ""
    return f"{100 * float(value):{sign}.1f}%"


def fnum(value: float | None, digits: int = 4) -> str:
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


def logistic_pipeline(numeric: list[str], categorical: list[str]) -> Pipeline:
    pre = ColumnTransformer(
        [
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), numeric),
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=2), categorical),
        ]
    )
    return Pipeline([("pre", pre), ("clf", LogisticRegression(max_iter=3000, C=0.8, random_state=SEED))])


def hgb_pipeline(numeric: list[str], categorical: list[str]) -> Pipeline:
    pre = ColumnTransformer(
        [
            ("num", SimpleImputer(strategy="median"), numeric),
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=2, sparse_output=False), categorical),
        ],
        sparse_threshold=0,
    )
    clf = HistGradientBoostingClassifier(
        max_iter=80,
        learning_rate=0.04,
        max_leaf_nodes=12,
        l2_regularization=0.05,
        random_state=SEED,
    )
    return Pipeline([("pre", pre), ("clf", clf)])


def add_model_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["label"] = pd.to_numeric(out["label_yes_wins"], errors="coerce").astype(int)
    out["yes_current_ask"] = pd.to_numeric(out["yes_current_ask"], errors="coerce")
    out["yes_current_size"] = pd.to_numeric(out["yes_current_size"], errors="coerce")
    out["available_notional_at_ask"] = out["yes_current_ask"] * out["yes_current_size"]
    out["snapshot_dt"] = pd.to_datetime(out["snapshot_ts_utc"], utc=True, errors="coerce")

    for prefix in ("gfs", "ecmwf"):
        hour = pd.to_numeric(out[f"{prefix}_forecast_peak_hour_local"], errors="coerce")
        out[f"{prefix}_forecast_peak_hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
        out[f"{prefix}_forecast_peak_hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
        out[f"{prefix}_forecast_inside_current_bracket_num"] = (
            out[f"{prefix}_forecast_inside_current_bracket"].astype(str).str.lower().isin({"true", "1"})
        ).astype(int)

    out["forecast_peak_models_agree_le_1h_num"] = (
        out["forecast_peak_models_agree_le_1h"].astype(str).str.lower().isin({"true", "1"})
    ).astype(int)
    out["forecast_peak_hour_abs_diff"] = (
        pd.to_numeric(out["gfs_forecast_peak_hour_local"], errors="coerce")
        - pd.to_numeric(out["ecmwf_forecast_peak_hour_local"], errors="coerce")
    ).abs()
    out["live_slice"] = (
        out["decision_hour_local"].between(13, 15)
        & out["decline_c"].ge(0.5)
        & out["yes_current_ask"].ge(0.55)
        & out["has_d1_no"].fillna(False).astype(bool)
    )
    out["v9_rule_slice"] = out["live_slice"] & out["p_yes_win"].ge(0.5) & (out["p_yes_win"] - out["yes_current_ask"]).ge(0.05)
    return out


def load_rows() -> pd.DataFrame:
    if not SOURCE_ROWS.exists():
        raise FileNotFoundError(f"missing v3 joined rows: {SOURCE_ROWS}")
    df = pd.read_csv(SOURCE_ROWS)
    return add_model_features(df)


def fit_models(rows: pd.DataFrame) -> tuple[pd.DataFrame, list[ModelSpec]]:
    train = rows[rows["period"].eq("train")].copy()
    out = rows.copy()
    out["pred_market_yes_ask"] = out["yes_current_ask"].clip(1e-6, 1 - 1e-6)
    out["pred_live_v9_artifact"] = out["p_yes_win"].clip(1e-6, 1 - 1e-6)

    specs = [
        ModelSpec("market_yes_ask", "baseline", [], [], "pred_market_yes_ask", "raw market current YES ask"),
        ModelSpec("live_v9_artifact", "baseline", [], [], "pred_live_v9_artifact", "existing exported v9 logistic artifact"),
        ModelSpec(
            "weather_price_logit",
            "logit",
            BASE_FEATURES + PRICE_FEATURES,
            CAT_FEATURES,
            "pred_weather_price_logit",
            "v10-style weather + price logistic",
        ),
        ModelSpec(
            "forecast_clock_logit",
            "logit",
            BASE_FEATURES + PRICE_FEATURES + FORECAST_CLOCK_FEATURES,
            CAT_FEATURES,
            "pred_forecast_clock_logit",
            "weather + price + forecast clock logistic",
        ),
        ModelSpec(
            "forecast_clock_logit_iso",
            "logit_iso",
            BASE_FEATURES + PRICE_FEATURES + FORECAST_CLOCK_FEATURES,
            CAT_FEATURES,
            "pred_forecast_clock_logit_iso",
            "weather + price + forecast clock logistic with train-only isotonic calibration",
        ),
        ModelSpec(
            "forecast_clock_hgb_iso",
            "hgb_iso",
            BASE_FEATURES + PRICE_FEATURES + FORECAST_CLOCK_FEATURES,
            CAT_FEATURES,
            "pred_forecast_clock_hgb_iso",
            "nonlinear weather + price + forecast clock with train-only isotonic calibration",
        ),
    ]

    for spec in specs:
        if spec.kind == "baseline":
            continue
        if spec.kind == "logit":
            model = logistic_pipeline(spec.numeric_features, spec.categorical_features)
        elif spec.kind == "logit_iso":
            model = CalibratedClassifierCV(
                logistic_pipeline(spec.numeric_features, spec.categorical_features),
                method="isotonic",
                cv=3,
            )
        elif spec.kind == "hgb_iso":
            model = CalibratedClassifierCV(
                hgb_pipeline(spec.numeric_features, spec.categorical_features),
                method="isotonic",
                cv=3,
            )
        else:
            raise ValueError(spec.kind)
        cols = spec.numeric_features + spec.categorical_features
        model.fit(train[cols], train["label"])
        out[spec.pred_col] = model.predict_proba(out[cols])[:, 1]
    return out, specs


def metric_row(frame: pd.DataFrame, pred_col: str) -> dict[str, Any]:
    y = frame["label"].to_numpy()
    p = np.clip(frame[pred_col].to_numpy(dtype=float), 1e-6, 1 - 1e-6)
    return {
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "actual_rate": float(y.mean()) if len(y) else None,
        "mean_pred": float(p.mean()) if len(y) else None,
        "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else None,
        "brier": float(brier_score_loss(y, p)) if len(y) else None,
        "logloss": float(log_loss(y, p)) if len(y) else None,
        "accuracy_50": float(accuracy_score(y, p >= 0.5)) if len(y) else None,
    }


def model_metrics(rows: pd.DataFrame, specs: list[ModelSpec]) -> pd.DataFrame:
    result = []
    scopes = {
        "holdout_all": rows["period"].eq("holdout"),
        "holdout_live_slice": rows["period"].eq("holdout") & rows["live_slice"],
        "holdout_v9_rule_slice": rows["period"].eq("holdout") & rows["v9_rule_slice"],
        "train_all": rows["period"].eq("train"),
    }
    for spec in specs:
        for scope, mask in scopes.items():
            frame = rows[mask].copy()
            if frame.empty:
                continue
            result.append(
                {
                    "model": spec.name,
                    "scope": scope,
                    "note": spec.note,
                    **metric_row(frame, spec.pred_col),
                }
            )
    return pd.DataFrame(result)


def bootstrap_metric_delta(
    rows: pd.DataFrame,
    *,
    scope_mask: pd.Series,
    candidate_col: str,
    baseline_col: str,
    metric: str,
    n_boot: int = 2000,
) -> dict[str, Any]:
    part = rows[scope_mask].copy()
    dates = sorted(part["target_date"].astype(str).unique())
    if len(dates) < 3:
        return {"n_dates": len(dates), "delta": None, "ci95": [None, None]}
    rng = np.random.default_rng(SEED)

    def losses(frame: pd.DataFrame, col: str) -> np.ndarray:
        y = frame["label"].to_numpy(dtype=float)
        p = np.clip(frame[col].to_numpy(dtype=float), 1e-6, 1 - 1e-6)
        if metric == "brier":
            return (p - y) ** 2
        if metric == "logloss":
            return -(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))
        raise ValueError(metric)

    observed = float(losses(part, candidate_col).mean() - losses(part, baseline_col).mean())
    by_date = []
    for date in dates:
        frame = part[part["target_date"].astype(str).eq(date)]
        by_date.append(
            [
                float(losses(frame, candidate_col).sum()),
                float(losses(frame, baseline_col).sum()),
                float(len(frame)),
            ]
        )
    arr = np.asarray(by_date, dtype=float)
    draws = []
    for _ in range(n_boot):
        sample = arr[rng.integers(0, len(arr), len(arr))]
        draws.append(sample[:, 0].sum() / sample[:, 2].sum() - sample[:, 1].sum() / sample[:, 2].sum())
    lo, hi = np.nanpercentile(draws, [2.5, 97.5])
    return {"n_dates": len(dates), "delta": float(observed), "ci95": [float(lo), float(hi)]}


def dedupe_trades(frame: pd.DataFrame) -> pd.DataFrame:
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


def trade_summary(frame: pd.DataFrame, *, taker_cushion: float = 0.02) -> dict[str, Any]:
    d = dedupe_trades(frame)
    if d.empty:
        return {"orders": 0, "dates": 0, "cities": 0, "win_rate": None, "roi": None, "pnl_usd": 0.0}
    notional = 5.0
    price = np.minimum(d["yes_current_ask"].astype(float).to_numpy() + taker_cushion, 0.999)
    y = d["label"].astype(int).to_numpy()
    pnl = np.where(y == 1, notional / price - notional, -notional)
    return {
        "orders": int(len(d)),
        "dates": int(d["target_date"].nunique()),
        "cities": int(d["city"].nunique()),
        "notional": float(notional * len(d)),
        "wins": int(y.sum()),
        "win_rate": float(y.mean()),
        "roi": float(pnl.sum() / (notional * len(d))),
        "pnl_usd": float(pnl.sum()),
        "avg_ask": float(d["yes_current_ask"].mean()),
        "orders_per_active_day": float(len(d) / d["target_date"].nunique()),
    }


def bootstrap_trade_roi(frame: pd.DataFrame, *, taker_cushion: float = 0.02, n_boot: int = 2000) -> list[float | None]:
    d = dedupe_trades(frame)
    dates = sorted(d["target_date"].astype(str).unique()) if not d.empty else []
    if len(dates) < 3:
        return [None, None]
    notional = 5.0
    by_date = []
    for date, group in d.groupby("target_date"):
        price = np.minimum(group["yes_current_ask"].astype(float).to_numpy() + taker_cushion, 0.999)
        y = group["label"].astype(int).to_numpy()
        pnl = np.where(y == 1, notional / price - notional, -notional).sum()
        by_date.append((float(pnl), float(notional * len(group))))
    arr = np.asarray(by_date, dtype=float)
    rng = np.random.default_rng(SEED)
    draws = []
    for _ in range(n_boot):
        sample = arr[rng.integers(0, len(arr), len(arr))]
        draws.append(sample[:, 0].sum() / sample[:, 1].sum())
    lo, hi = np.nanpercentile(draws, [2.5, 97.5])
    return [float(lo), float(hi)]


def live_rule_summaries(rows: pd.DataFrame, specs: list[ModelSpec]) -> pd.DataFrame:
    summaries = []
    hold = rows[rows["period"].eq("holdout")].copy()
    for spec in specs:
        if spec.name == "market_yes_ask":
            continue
        edge = hold[spec.pred_col] - hold["yes_current_ask"]
        selected = hold[
            hold["live_slice"]
            & hold[spec.pred_col].ge(0.50)
            & edge.ge(0.05)
            & hold["available_notional_at_ask"].ge(2.0)
        ].copy()
        selected["model_edge"] = edge.loc[selected.index]
        row = trade_summary(selected, taker_cushion=0.02)
        row.update(
            {
                "model": spec.name,
                "roi_ci95": bootstrap_trade_roi(selected, taker_cushion=0.02),
                "avg_model_edge": float(selected["model_edge"].mean()) if not selected.empty else None,
            }
        )
        summaries.append(row)
    return pd.DataFrame(summaries)


def write_report(payload: dict[str, Any], metrics_df: pd.DataFrame, trade_df: pd.DataFrame, deltas_df: pd.DataFrame) -> None:
    holdout = metrics_df[metrics_df["scope"].eq("holdout_all")].sort_values("brier")
    live = metrics_df[metrics_df["scope"].eq("holdout_live_slice")].sort_values("brier")
    lines = [
        "# Theta Current YES Forecast Clock Model v12",
        "",
        "Status: research_only / no_live_upgrade",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "Target metric: `forecast_clock_model_incremental_value` = forecast peak clock 作为模型特征，是否提升 current-YES 的成功率、校准和可交易 ROI。",
        "",
        "## 数据完整性自检",
        "",
        f"- fact_built_at_utc: `{payload['data_self_check']['fact_trades_max_built_at_utc']}`",
        f"- fact_trades trade_class: `{payload['data_self_check']['fact_trades_by_class']}`",
        f"- settlement_status: `{payload['data_self_check']['fact_trades_by_settlement_status']}`",
        f"- fact_signal_candidates coverage: `{payload['data_self_check']['fact_signal_candidate_coverage']}`",
        f"- CLOB orders/fills join: `{payload['data_self_check']['clob_order_fill_join']}`",
        f"- CLOB gate: `{payload['clob_gate']}`",
        "",
        "## 人话结论",
        "",
        "forecast-clock 特征接进模型后，没有给 current-YES 带来足够稳定的增量。它能改变一些排序和校准点估，但在真正 live-like slice 里没有打赢市场 ask，也没有明显打赢现有 v9 artifact。",
        "",
        "交易上，继续保留 v9 fixed fade-confirmed tiny-live 候选；forecast-clock 只作为生产 telemetry 和后续模型特征落盘，不能作为本轮升级 live 的理由。",
        "",
        "## Holdout 全样本模型表现",
        "",
        "| model | rows/dates | actual | mean p | AUC | Brier | LogLoss | Acc@0.5 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in holdout.iterrows():
        lines.append(
            f"| {row['model']} | {int(row['rows'])}/{int(row['dates'])} | {pct(row['actual_rate'], signed=False)} | "
            f"{pct(row['mean_pred'], signed=False)} | {fnum(row['auc'])} | {fnum(row['brier'])} | "
            f"{fnum(row['logloss'])} | {pct(row['accuracy_50'], signed=False)} |"
        )
    lines.extend(
        [
            "",
            "## Holdout Live-Like Slice",
            "",
            "| model | rows/dates | actual | mean p | AUC | Brier | LogLoss | Acc@0.5 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for _, row in live.iterrows():
        lines.append(
            f"| {row['model']} | {int(row['rows'])}/{int(row['dates'])} | {pct(row['actual_rate'], signed=False)} | "
            f"{pct(row['mean_pred'], signed=False)} | {fnum(row['auc'])} | {fnum(row['brier'])} | "
            f"{fnum(row['logloss'])} | {pct(row['accuracy_50'], signed=False)} |"
        )
    lines.extend(
        [
            "",
            "## Live Rule ROI Using Each Model",
            "",
            "规则：holdout / live-like slice / `p>=0.5` / `p-ask>=0.05` / top ask notional >= $2 / $5 notional / taker +2c。",
            "",
            "| model | orders/dates | win | ROI | CI95 | avg ask | avg edge |",
            "|---|---:|---:|---:|---|---:|---:|",
        ]
    )
    for _, row in trade_df.sort_values("roi", ascending=False, na_position="last").iterrows():
        ci = row.get("roi_ci95") or [None, None]
        lines.append(
            f"| {row['model']} | {int(row['orders'])}/{int(row['dates'])} | {pct(row['win_rate'])} | "
            f"{pct(row['roi'])} | [{pct(ci[0])}, {pct(ci[1])}] | {fnum(row.get('avg_ask'), 3)} | "
            f"{pct(row.get('avg_model_edge'))} |"
        )
    lines.extend(
        [
            "",
            "## Bootstrap Deltas",
            "",
            "负数表示候选模型的 Brier/LogLoss 比 baseline 更好。",
            "",
            "| scope | candidate | baseline | metric | delta | CI95 |",
            "|---|---|---|---|---:|---|",
        ]
    )
    for _, row in deltas_df.iterrows():
        ci = row["ci95"]
        lines.append(
            f"| {row['scope']} | {row['candidate']} | {row['baseline']} | {row['metric']} | "
            f"{fnum(row['delta'], 5)} | [{fnum(ci[0], 5)}, {fnum(ci[1], 5)}] |"
        )
    lines.extend(
        [
            "",
            "## 交易动作",
            "",
            "- 不替换 v9 current-YES 模型。",
            "- 不因为 forecast-clock 特征上 live。",
            "- 下一步实盘准备应该是：生产 snapshot 原生落 `forecast_peak_*`、fresh-book/execution survival 特征、obs age/METAR blackout guard，然后继续 forward shadow。",
            "",
            "## 三道门",
            "",
            "- significance=FAIL：forecast-clock 模型相对 market ask / v9 的 live-like Brier/logloss delta 没有稳定过门。",
            "- baseline=FAIL：可交易 ROI 没有稳定打赢 v9 fixed rule。",
            "- forward=FAIL：该特征来自 historical backfill，不是生产前瞻原生字段。",
            "- conclusion=`inconclusive` / `research_only`；不允许 live upgrade。",
            "",
            "## 产物",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- metrics CSV: `{(OUT_DIR / 'model_metrics.csv').relative_to(ROOT)}`",
            f"- trade CSV: `{(OUT_DIR / 'live_rule_summaries.csv').relative_to(ROOT)}`",
            f"- delta CSV: `{(OUT_DIR / 'bootstrap_deltas.csv').relative_to(ROOT)}`",
            f"- scored rows CSV: `{(OUT_DIR / 'scored_rows.csv').relative_to(ROOT)}`",
            f"- Script: `{Path(__file__).resolve().relative_to(ROOT)}`",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    scored, specs = fit_models(rows)
    metrics_df = model_metrics(scored, specs)
    trade_df = live_rule_summaries(scored, specs)
    delta_rows = []
    comparisons = [
        ("forecast_clock_logit", "live_v9_artifact"),
        ("forecast_clock_logit_iso", "live_v9_artifact"),
        ("forecast_clock_hgb_iso", "live_v9_artifact"),
        ("forecast_clock_logit", "market_yes_ask"),
        ("forecast_clock_logit_iso", "market_yes_ask"),
        ("forecast_clock_hgb_iso", "market_yes_ask"),
    ]
    scope_masks = {
        "holdout_all": scored["period"].eq("holdout"),
        "holdout_live_slice": scored["period"].eq("holdout") & scored["live_slice"],
    }
    pred_by_model = {spec.name: spec.pred_col for spec in specs}
    for scope, mask in scope_masks.items():
        for cand, base in comparisons:
            for metric in ["brier", "logloss"]:
                delta = bootstrap_metric_delta(
                    scored,
                    scope_mask=mask,
                    candidate_col=pred_by_model[cand],
                    baseline_col=pred_by_model[base],
                    metric=metric,
                )
                delta_rows.append(
                    {
                        "scope": scope,
                        "candidate": cand,
                        "baseline": base,
                        "metric": metric,
                        **delta,
                    }
                )
    deltas_df = pd.DataFrame(delta_rows)

    scored.to_csv(OUT_DIR / "scored_rows.csv", index=False)
    metrics_df.to_csv(OUT_DIR / "model_metrics.csv", index=False)
    trade_df.to_csv(OUT_DIR / "live_rule_summaries.csv", index=False)
    deltas_df.to_csv(OUT_DIR / "bootstrap_deltas.csv", index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "evidence_layer": "historical orderbook replay plus forecast peak clock historical backfill",
        "row_grain": "one current-YES city/date/hour/current-bracket replay row",
        "data_self_check": data_self_check(),
        "clob_gate": load_gate(),
        "coverage": {
            "rows": int(len(scored)),
            "train_rows": int(scored["period"].eq("train").sum()),
            "holdout_rows": int(scored["period"].eq("holdout").sum()),
            "active_dates": int(scored["target_date"].nunique()),
            "rows_with_gfs_peak": int(scored["gfs_forecast_peak_hour_local"].notna().sum()),
            "rows_with_ecmwf_peak": int(scored["ecmwf_forecast_peak_hour_local"].notna().sum()),
        },
        "holdout_all_metrics": json_ready(metrics_df[metrics_df["scope"].eq("holdout_all")].to_dict("records")),
        "holdout_live_slice_metrics": json_ready(metrics_df[metrics_df["scope"].eq("holdout_live_slice")].to_dict("records")),
        "live_rule_summaries": json_ready(trade_df.to_dict("records")),
        "bootstrap_deltas": json_ready(deltas_df.to_dict("records")),
        "outputs": {
            "md": str(OUT_MD.relative_to(ROOT)),
            "json": str(OUT_JSON.relative_to(ROOT)),
            "metrics_csv": str((OUT_DIR / "model_metrics.csv").relative_to(ROOT)),
            "trade_csv": str((OUT_DIR / "live_rule_summaries.csv").relative_to(ROOT)),
            "delta_csv": str((OUT_DIR / "bootstrap_deltas.csv").relative_to(ROOT)),
            "scored_rows_csv": str((OUT_DIR / "scored_rows.csv").relative_to(ROOT)),
        },
    }
    payload = json_ready(payload)
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(payload, metrics_df, trade_df, deltas_df)
    print(json.dumps({"json": str(OUT_JSON), "md": str(OUT_MD)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
