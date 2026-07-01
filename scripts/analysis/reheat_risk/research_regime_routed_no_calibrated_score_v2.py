#!/usr/bin/env python3
"""Shadow calibrated P(NO wins) model for regime-routed NO candidates."""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from dataclasses import dataclass
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
from sklearn.preprocessing import StandardScaler

try:
    from sklearn.preprocessing import OneHotEncoder
except Exception as exc:  # pragma: no cover
    raise RuntimeError("sklearn OneHotEncoder is required") from exc


ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_DIR = ROOT / "scripts/analysis/reheat_risk"
for path in (ANALYSIS_DIR, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import research_regime_routed_no_score_component_audit_v1 as score_audit  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/regime_routed_no_calibrated_score_v2"
OUT_SUMMARY_JSON = OUT_DIR / "summary.json"
OUT_MODEL_QUALITY = OUT_DIR / "model_quality.csv"
OUT_TRADE_SUMMARY = OUT_DIR / "trade_summary.csv"
OUT_WALK_FORWARD = OUT_DIR / "walk_forward_trade_summary.csv"
OUT_DAILY = OUT_DIR / "daily_summary.csv"
OUT_SCORED_ROWS = OUT_DIR / "scored_rows.csv"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-01-regime-routed-no-calibrated-score-v2.md"
CLOB_GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
DB_PATH = ROOT / "runtime/weather.db"

FORWARD_START = "2026-06-21"
SEED = 20260701


@dataclass(frozen=True)
class ModelSpec:
    name: str
    numeric: list[str]
    categorical: list[str]
    c: float = 0.35


BASE_NUMERIC = [
    "router_ask",
    "forecast_peak_delta_hours_local",
    "minutes_since_running_max",
    "temp_trend_1h_f",
    "wind_speed_kt",
    "relative_humidity_pct",
]
COMPONENT_NUMERIC = [
    "score_route_mult",
    "score_price_mult",
    "score_peak_mult",
    "score_freshness_mult",
    "score_momentum_mult",
    "score_weather_mult",
]
CITY_BIAS_NUMERIC = [
    "city_source_bias",
    "city_source_bias_mae",
    "city_source_bias_p90",
    "city_source_hot_underforecast_rate",
    "city_source_cold_overforecast_rate",
]
HEURISTIC_NUMERIC = [
    "score_deployed_weight",
    "score_deployed_ratio",
]
BASE_CATEGORICAL = [
    "router_route",
    "city_family",
    "moisture_cloud_regime",
    "row_forecast_model",
]
CITY_BIAS_CATEGORICAL = [
    "city_source_bias_regime",
]

MODEL_SPECS = [
    ModelSpec(
        name="logit_market_route",
        numeric=["router_ask"],
        categorical=["router_route"],
        c=0.5,
    ),
    ModelSpec(
        name="logit_mechanism_weather",
        numeric=BASE_NUMERIC + COMPONENT_NUMERIC,
        categorical=BASE_CATEGORICAL,
        c=0.35,
    ),
    ModelSpec(
        name="logit_mechanism_weather_citybias",
        numeric=BASE_NUMERIC + COMPONENT_NUMERIC + CITY_BIAS_NUMERIC,
        categorical=BASE_CATEGORICAL + CITY_BIAS_CATEGORICAL,
        c=0.25,
    ),
    ModelSpec(
        name="logit_mechanism_plus_heuristic_score",
        numeric=BASE_NUMERIC + COMPONENT_NUMERIC + CITY_BIAS_NUMERIC + HEURISTIC_NUMERIC,
        categorical=BASE_CATEGORICAL + CITY_BIAS_CATEGORICAL,
        c=0.20,
    ),
]


def finite(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        val = float(value)
        return val if math.isfinite(val) else None
    return value


def pct(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{100.0 * val:+.1f}%"


def money(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{val:+.3f}"


def load_clob_gate_pass() -> bool | None:
    if not CLOB_GATE.exists():
        return None
    try:
        payload = json.loads(CLOB_GATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    value = payload.get("gate_pass")
    return bool(value) if value is not None else None


def settlement_outcome_map() -> dict[tuple[str, str, str], float]:
    if not DB_PATH.exists():
        return {}
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    try:
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(
            "SELECT city, target_date, bracket, final_price "
            "FROM settlement_outcomes "
            "WHERE source_system='pm_history' AND settlement_status='settled'"
        ).fetchall()
    finally:
        conn.close()
    return {(str(city), str(target_date), str(bracket)): float(final_price) for city, target_date, bracket, final_price in rows}


def routed_bracket(row: pd.Series) -> str | None:
    route = str(row.get("router_route") or row.get("route_leg") or "")
    expression = str(row.get("router_expression") or row.get("expression") or "")
    if route == "capped_d2_no" or expression == "d2_no":
        value = row.get("d2_no_bracket")
    elif expression.endswith("_yes") and pd.notna(row.get("current_bracket")):
        value = row.get("current_bracket")
    else:
        value = row.get("current_bracket")
    if pd.isna(value):
        return None
    text = str(value)
    return text[:-2] if text.endswith(".0") else text


def fill_payoff_from_settlements(frame: pd.DataFrame) -> pd.DataFrame:
    outcomes = settlement_outcome_map()
    if not outcomes:
        return frame
    out = frame.copy()
    if "router_payoff" not in out.columns:
        out["router_payoff"] = np.nan
    if "payoff" not in out.columns:
        out["payoff"] = np.nan
    if "final_winning_bracket" not in out.columns:
        out["final_winning_bracket"] = np.nan
    missing = pd.to_numeric(out["router_payoff"], errors="coerce").isna()
    if not missing.any():
        return out
    for idx, row in out[missing].iterrows():
        city = str(row.get("city") or "")
        target_date = str(row.get("target_date") or "")
        bracket = routed_bracket(row)
        if not city or not target_date or bracket is None:
            continue
        final_price = outcomes.get((city, target_date, bracket))
        if final_price is None:
            continue
        side = str(row.get("router_side") or row.get("side") or "BUY_NO").upper()
        payoff = final_price if side.endswith("YES") else 1.0 - final_price
        out.at[idx, "router_payoff"] = payoff
        out.at[idx, "payoff"] = payoff
        winners = [
            key_bracket
            for (key_city, key_date, key_bracket), price in outcomes.items()
            if key_city == city and key_date == target_date and price == 1.0
        ]
        if winners:
            out.at[idx, "final_winning_bracket"] = winners[0]
    return out


def onehot() -> OneHotEncoder:
    try:
        return OneHotEncoder(handle_unknown="ignore", min_frequency=3, sparse_output=False)
    except TypeError:  # pragma: no cover
        return OneHotEncoder(handle_unknown="ignore", min_frequency=3, sparse=False)


def active_feature_lists(frame: pd.DataFrame, spec: ModelSpec) -> tuple[list[str], list[str], list[str]]:
    numeric: list[str] = []
    dropped_numeric: list[str] = []
    for col in spec.numeric:
        if col in frame.columns and frame[col].notna().any():
            numeric.append(col)
        else:
            dropped_numeric.append(col)
    categorical = [col for col in spec.categorical if col in frame.columns]
    return numeric, categorical, dropped_numeric


def model_pipeline(spec: ModelSpec) -> Pipeline:
    pre = ColumnTransformer(
        [
            (
                "num",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="median")),
                        ("scaler", StandardScaler()),
                    ]
                ),
                spec.numeric,
            ),
            (
                "cat",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", onehot()),
                    ]
                ),
                spec.categorical,
            ),
        ],
        remainder="drop",
    )
    return Pipeline(
        [
            ("pre", pre),
            (
                "clf",
                LogisticRegression(
                    C=spec.c,
                    max_iter=2000,
                    random_state=SEED,
                    solver="lbfgs",
                ),
            ),
        ]
    )


def prepare_frame(frame: pd.DataFrame, *, evidence_layer: str) -> pd.DataFrame:
    out = fill_payoff_from_settlements(frame)
    out["evidence_layer"] = evidence_layer
    out["target_date"] = out["target_date"].astype(str)
    out["label_no_win"] = pd.to_numeric(out["router_payoff"], errors="coerce")
    out["router_ask"] = pd.to_numeric(out["router_ask"], errors="coerce")
    out["score_deployed_ratio"] = pd.to_numeric(out["score_deployed_weight"], errors="coerce") / out["router_ask"]
    for col in sorted({c for spec in MODEL_SPECS for c in spec.numeric}):
        if col not in out.columns:
            out[col] = np.nan
        out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in sorted({c for spec in MODEL_SPECS for c in spec.categorical}):
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("missing").astype(str)
    return out[out["label_no_win"].isin([0.0, 1.0]) & out["router_ask"].notna()].reset_index(drop=True)


def model_quality(frame: pd.DataFrame, *, p_col: str) -> dict[str, Any]:
    clean = frame[frame[p_col].notna()].copy()
    y = pd.to_numeric(clean["label_no_win"], errors="coerce").astype(int)
    p = pd.to_numeric(clean[p_col], errors="coerce").clip(1e-6, 1 - 1e-6)
    return {
        "rows": int(len(clean)),
        "dates": int(clean["target_date"].nunique()) if len(clean) else 0,
        "label_rate": float(y.mean()) if len(clean) else None,
        "prob_mean": float(p.mean()) if len(clean) else None,
        "auc": float(roc_auc_score(y, p)) if len(clean) and y.nunique() > 1 else None,
        "brier": float(brier_score_loss(y, p)) if len(clean) else None,
        "log_loss": float(log_loss(y, p, labels=[0, 1])) if len(clean) else None,
    }


def row_pnl(cost: pd.Series, ask: pd.Series, payoff: pd.Series) -> pd.Series:
    return pd.Series(np.where(payoff.eq(1.0), cost / ask - cost, -cost), index=cost.index)


def apply_daily_cap(frame: pd.DataFrame, *, selected: pd.Series, cost: pd.Series, cap: float = 1.0) -> pd.Series:
    chosen: list[int] = []
    work = frame[selected.astype(bool)].copy()
    if work.empty:
        return pd.Series(False, index=frame.index)
    work["_cost"] = cost.reindex(work.index).fillna(0.0)
    work["_edge_ratio"] = pd.to_numeric(work.get("_edge_ratio"), errors="coerce").fillna(0.0)
    order_cols = ["target_date", "_edge_ratio"]
    asc = [True, False]
    if "decision_snapshot_ts_utc" in work.columns:
        order_cols.append("decision_snapshot_ts_utc")
        asc.append(True)
    order_cols.append("city")
    asc.append(True)
    for _, group in work.sort_values(order_cols, ascending=asc).groupby("target_date", sort=True):
        spent = 0.0
        for idx, row in group.iterrows():
            row_cost = float(row.get("_cost") or 0.0)
            if spent + row_cost <= cap + 1e-9:
                chosen.append(idx)
                spent += row_cost
    out = pd.Series(False, index=frame.index)
    out.loc[chosen] = True
    return out


def date_bootstrap_roi(frame: pd.DataFrame, *, n: int = 3000) -> tuple[float | None, float | None]:
    if frame.empty or frame["target_date"].nunique() < 3:
        return None, None
    daily = frame.groupby("target_date", as_index=False).agg(cost=("_cost", "sum"), pnl=("_pnl", "sum"))
    rng = np.random.default_rng(SEED)
    idx = np.arange(len(daily))
    costs = daily["cost"].to_numpy(float)
    pnls = daily["pnl"].to_numpy(float)
    rois: list[float] = []
    for _ in range(n):
        sample = rng.choice(idx, size=len(idx), replace=True)
        cost = costs[sample].sum()
        if cost > 0:
            rois.append(float(pnls[sample].sum() / cost))
    if not rois:
        return None, None
    low, high = np.quantile(rois, [0.025, 0.975])
    return float(low), float(high)


def summarize_trade(frame: pd.DataFrame, *, selected: pd.Series, cost: pd.Series) -> dict[str, Any]:
    active = frame[selected.astype(bool)].copy()
    if active.empty:
        return {
            "trades": 0,
            "dates": 0,
            "cities": 0,
            "wins": 0,
            "win_rate": None,
            "avg_ask": None,
            "avg_cost": None,
            "cost": 0.0,
            "pnl": 0.0,
            "roi": None,
            "roi_ci_low": None,
            "roi_ci_high": None,
            "daily_negative_100pct": 0,
        }
    ask = pd.to_numeric(active["router_ask"], errors="coerce")
    payoff = pd.to_numeric(active["label_no_win"], errors="coerce")
    active["_cost"] = cost.reindex(active.index).fillna(0.0)
    active["_pnl"] = row_pnl(active["_cost"], ask, payoff)
    total_cost = float(active["_cost"].sum())
    total_pnl = float(active["_pnl"].sum())
    daily = active.groupby("target_date", as_index=False).agg(cost=("_cost", "sum"), pnl=("_pnl", "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"]
    ci_low, ci_high = date_bootstrap_roi(active)
    return {
        "trades": int(len(active)),
        "dates": int(active["target_date"].nunique()),
        "cities": int(active["city"].nunique()),
        "wins": int(payoff.sum()),
        "win_rate": float(payoff.mean()),
        "avg_ask": float(ask.mean()),
        "avg_cost": float(active["_cost"].mean()),
        "cost": total_cost,
        "pnl": total_pnl,
        "roi": total_pnl / total_cost if total_cost else None,
        "roi_ci_low": ci_low,
        "roi_ci_high": ci_high,
        "daily_negative_100pct": int(daily["roi"].le(-0.999).sum()),
    }


def add_trade_rows(rows: list[dict[str, Any]], frame: pd.DataFrame, *, scope: str, evidence_layer: str, model: str, p_col: str) -> pd.DataFrame:
    ask = pd.to_numeric(frame["router_ask"], errors="coerce")
    p = pd.to_numeric(frame[p_col], errors="coerce")
    heuristic_weight = pd.to_numeric(frame["score_deployed_weight"], errors="coerce").fillna(0.0)
    heuristic_edge_ratio = pd.to_numeric(frame["score_deployed_ratio"], errors="coerce")
    model_edge_ratio = p / ask
    model_prob_cost = p.clip(0.05, 1.0).fillna(0.0)
    for variant, selected, cost, edge_ratio in [
        (
            "current_heuristic_score_over_ask_ge_1_size_score",
            heuristic_edge_ratio.ge(1.0),
            heuristic_weight,
            heuristic_edge_ratio,
        ),
        (
            "model_p_ge_ask_size_probability",
            p.ge(ask),
            model_prob_cost,
            model_edge_ratio,
        ),
        (
            "model_p_ge_ask_size_current_score",
            p.ge(ask),
            heuristic_weight,
            model_edge_ratio,
        ),
        (
            "model_p_ge_ask_plus_05_size_probability",
            p.ge(ask + 0.05),
            model_prob_cost,
            model_edge_ratio,
        ),
        (
            "model_p_ge_ask_plus_05_size_current_score",
            p.ge(ask + 0.05),
            heuristic_weight,
            model_edge_ratio,
        ),
    ]:
        work = frame.copy()
        work["_edge_ratio"] = edge_ratio
        selected_capped = apply_daily_cap(work, selected=selected, cost=cost, cap=1.0)
        row = summarize_trade(work, selected=selected_capped, cost=cost)
        row.update(
            {
                "evidence_layer": evidence_layer,
                "scope": scope,
                "model": model,
                "prob_col": p_col,
                "trade_variant": variant,
            }
        )
        rows.append(row)
    return frame


def fit_predict_holdout(frame: pd.DataFrame, *, evidence_layer: str, spec: ModelSpec) -> tuple[pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    out = frame.copy()
    p_col = f"p_no_{spec.name}"
    out[p_col] = np.nan
    train = out[out["target_date"].astype(str) < FORWARD_START].copy()
    forward = out[out["target_date"].astype(str) >= FORWARD_START].copy()
    quality_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    if train["label_no_win"].nunique() < 2:
        return out, quality_rows, trade_rows
    numeric, categorical, dropped_numeric = active_feature_lists(train, spec)
    active_spec = ModelSpec(name=spec.name, numeric=numeric, categorical=categorical, c=spec.c)
    pipe = model_pipeline(active_spec)
    pipe.fit(train[active_spec.numeric + active_spec.categorical], train["label_no_win"].astype(int))
    out.loc[train.index, p_col] = pipe.predict_proba(train[active_spec.numeric + active_spec.categorical])[:, 1]
    if not forward.empty:
        out.loc[forward.index, p_col] = pipe.predict_proba(forward[active_spec.numeric + active_spec.categorical])[:, 1]
    for scope, scope_frame in [
        ("train_to_2026_06_20", out.loc[train.index].copy()),
        (f"forward_{FORWARD_START}_plus", out.loc[forward.index].copy()),
        ("all_trainfit_forward_scored", out[out[p_col].notna()].copy()),
    ]:
        q = model_quality(scope_frame, p_col=p_col)
        q.update(
            {
                "evidence_layer": evidence_layer,
                "scope": scope,
                "model": spec.name,
                "prob_col": p_col,
                "active_numeric": ";".join(active_spec.numeric),
                "active_categorical": ";".join(active_spec.categorical),
                "dropped_numeric_all_missing_in_train": ";".join(dropped_numeric),
            }
        )
        quality_rows.append(q)
        add_trade_rows(trade_rows, scope_frame, scope=scope, evidence_layer=evidence_layer, model=spec.name, p_col=p_col)
    return out, quality_rows, trade_rows


def expanding_walk_forward(frame: pd.DataFrame, *, evidence_layer: str, spec: ModelSpec, min_train_dates: int = 18) -> tuple[pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]], pd.DataFrame]:
    out = frame.copy()
    p_col = f"p_no_wf_{spec.name}"
    out[p_col] = np.nan
    daily_rows: list[dict[str, Any]] = []
    dates = sorted(out["target_date"].dropna().astype(str).unique())
    for i, date in enumerate(dates):
        train_dates = dates[:i]
        if len(train_dates) < min_train_dates:
            continue
        train = out[out["target_date"].isin(train_dates)].copy()
        test = out[out["target_date"].eq(date)].copy()
        if train["label_no_win"].nunique() < 2 or test.empty:
            continue
        numeric, categorical, dropped_numeric = active_feature_lists(train, spec)
        active_spec = ModelSpec(name=spec.name, numeric=numeric, categorical=categorical, c=spec.c)
        pipe = model_pipeline(active_spec)
        pipe.fit(train[active_spec.numeric + active_spec.categorical], train["label_no_win"].astype(int))
        pred = pipe.predict_proba(test[active_spec.numeric + active_spec.categorical])[:, 1]
        out.loc[test.index, p_col] = pred
        tmp = test.copy()
        tmp[p_col] = pred
        ask = pd.to_numeric(tmp["router_ask"], errors="coerce")
        p = pd.to_numeric(tmp[p_col], errors="coerce")
        cost = p.clip(0.05, 1.0).fillna(0.0)
        tmp["_edge_ratio"] = p / ask
        selected = apply_daily_cap(tmp, selected=p.ge(ask), cost=cost, cap=1.0)
        summary = summarize_trade(tmp, selected=selected, cost=cost)
        summary.update(
            {
                "target_date": date,
                "evidence_layer": evidence_layer,
                "model": spec.name,
                "dropped_numeric_all_missing_in_train": ";".join(dropped_numeric),
            }
        )
        daily_rows.append(summary)
    scored = out[out[p_col].notna()].copy()
    quality_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    if not scored.empty:
        q = model_quality(scored, p_col=p_col)
        q.update(
            {
                "evidence_layer": evidence_layer,
                "scope": "expanding_walk_forward",
                "model": spec.name,
                "prob_col": p_col,
                "active_numeric": "date_varying",
                "active_categorical": "date_varying",
                "dropped_numeric_all_missing_in_train": "date_varying",
            }
        )
        quality_rows.append(q)
        add_trade_rows(
            trade_rows,
            scored,
            scope="expanding_walk_forward",
            evidence_layer=evidence_layer,
            model=spec.name,
            p_col=p_col,
        )
    return out, quality_rows, trade_rows, pd.DataFrame(daily_rows)


def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
    shown = df if limit is None else df.head(limit)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in shown.iterrows():
        vals = []
        for col in cols:
            val = row.get(col)
            if col in {"auc", "brier", "log_loss", "label_rate", "prob_mean", "win_rate", "roi", "roi_ci_low", "roi_ci_high"}:
                vals.append(pct(val) if col not in {"brier", "log_loss"} else (f"{float(val):.3f}" if pd.notna(val) else "NA"))
            elif col in {"cost", "pnl", "avg_cost"}:
                vals.append(money(val))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "NA")
            else:
                vals.append("" if pd.isna(val) else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def render_md(payload: dict[str, Any], quality: pd.DataFrame, trades: pd.DataFrame, wf_trades: pd.DataFrame) -> str:
    frozen_quality = quality[
        quality["evidence_layer"].eq("frozen_live_like_route_price")
        & quality["scope"].isin([f"forward_{FORWARD_START}_plus", "expanding_walk_forward"])
    ].copy()
    quality_cols = ["scope", "model", "rows", "dates", "label_rate", "prob_mean", "auc", "brier", "log_loss"]
    frozen_trades = trades[
        trades["evidence_layer"].eq("frozen_live_like_route_price")
        & trades["scope"].isin([f"forward_{FORWARD_START}_plus", "expanding_walk_forward"])
        & trades["trade_variant"].isin(
            [
                "current_heuristic_score_over_ask_ge_1_size_score",
                "model_p_ge_ask_size_probability",
                "model_p_ge_ask_plus_05_size_probability",
            ]
        )
    ].copy()
    frozen_live_sized_trades = trades[
        trades["evidence_layer"].eq("frozen_live_like_route_price")
        & trades["scope"].isin([f"forward_{FORWARD_START}_plus", "expanding_walk_forward"])
        & trades["trade_variant"].isin(
            [
                "current_heuristic_score_over_ask_ge_1_size_score",
                "model_p_ge_ask_size_current_score",
                "model_p_ge_ask_plus_05_size_current_score",
            ]
        )
    ].copy()
    trade_cols = [
        "scope",
        "model",
        "trade_variant",
        "trades",
        "dates",
        "cities",
        "win_rate",
        "avg_ask",
        "avg_cost",
        "roi",
        "roi_ci_low",
        "roi_ci_high",
        "daily_negative_100pct",
    ]
    best_forward = frozen_trades[
        frozen_trades["scope"].eq(f"forward_{FORWARD_START}_plus")
        & frozen_trades["trade_variant"].eq("model_p_ge_ask_size_probability")
    ].sort_values("roi", ascending=False)
    return "\n".join(
        [
            "# Regime-Routed NO Calibrated Score V2",
            "",
            "## Conclusion",
            "",
            "A simple L2 logistic `P(NO wins)` layer does not beat the current fixed-quality heuristic on the frozen forward slice. It is useful as shadow telemetry, not a live replacement.",
            "",
            f"Verdict: `{payload['verdict']['conclusion']}`.",
            "",
            "## Coverage",
            "",
            f"- Frozen/live-like replay: `{payload['coverage']['frozen_live_like_route_price']['min_date']}`..`{payload['coverage']['frozen_live_like_route_price']['max_date']}`, rows `{payload['coverage']['frozen_live_like_route_price']['rows']}`.",
            f"- Historical best-ask diagnostic: `{payload['coverage']['historical_best_ask_diagnostic']['min_date']}`..`{payload['coverage']['historical_best_ask_diagnostic']['max_date']}`, rows `{payload['coverage']['historical_best_ask_diagnostic']['rows']}`.",
            f"- Raw frozen rows before label/ask filtering: `{payload['raw_coverage']['frozen_live_like_route_price']['rows']}` rows through `{payload['raw_coverage']['frozen_live_like_route_price']['max_date']}`; dropped rows `{payload['raw_coverage']['frozen_live_like_route_price']['dropped_unscored_rows']}`.",
            "- Missing replay payoff labels are backfilled from canonical `settlement_outcomes` when the routed bracket has a settled pm_history row.",
            f"- Data refresh: N100 sync completed; local fact rebuild completed but frontend restart exited non-cleanly because port 5174 stayed occupied; CLOB fill coverage gate `gate_pass={payload['data_snapshot']['clob_gate_pass']}`.",
            f"- Train/forward split: train `< {FORWARD_START}`, forward `>= {FORWARD_START}`.",
            "- Evidence is frozen/live-like candidate replay, not live_real PnL.",
            "",
            "## Feature Availability",
            "",
            "- `city_source_bias`, hot-underforecast/cold-overforecast rates, `city_source_bias_regime`, route, ask, peak clock, freshness, trend, wind, humidity, cloud/moisture regime are present on the frozen rows.",
            "- `city_source_bias_mae` and `city_source_bias_p90` are all-missing on the current replay denominator, so they are dropped at training time and recorded in `model_quality.csv`.",
            "",
            "## Frozen Forward Model Quality",
            "",
            table(frozen_quality.sort_values(["scope", "model"]), quality_cols),
            "",
            "## Frozen Forward Trading Expressions",
            "",
            table(frozen_trades.sort_values(["scope", "model", "trade_variant"]), trade_cols),
            "",
            "## Frozen Forward Live-Sized Gate Variants",
            "",
            "These variants use the model only as an entry gate, while keeping the current deployed score as sizing.",
            "",
            table(frozen_live_sized_trades.sort_values(["scope", "model", "trade_variant"]), trade_cols),
            "",
            "## Best Forward Logistic p >= ask Variants",
            "",
            table(best_forward, trade_cols, limit=8),
            "",
            "## Interpretation",
            "",
            "- Natural EV gating is `p_no >= ask`. On this small forward slice it usually selects more/other rows, but does not produce a robust improvement over the current score/ask gate.",
            "- Adding city/source bias and heuristic score features does not rescue forward performance. The forward set is too thin to justify replacing the live score.",
            "- Expanding walk-forward is the right next evidence layer because it prevents using future days to set probabilities. Results are mixed and should remain shadow-only.",
            "- The useful artifact is the per-row `p_no_*` telemetry. It can be logged beside `row_risk_soft_v1` and reviewed after more live/frozen days accumulate.",
            "",
            "## Files",
            "",
            f"- Summary JSON: `{payload['outputs']['summary_json']}`",
            f"- Model quality: `{payload['outputs']['model_quality']}`",
            f"- Trade summary: `{payload['outputs']['trade_summary']}`",
            f"- Walk-forward summary: `{payload['outputs']['walk_forward_summary']}`",
            f"- Daily summary: `{payload['outputs']['daily_summary']}`",
            f"- Scored rows: `{payload['outputs']['scored_rows']}`",
        ]
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = score_audit.load_details()
    prepared = {layer: prepare_frame(score_audit.compute_components(frame), evidence_layer=layer) for layer, frame in raw.items()}
    raw_coverage = {}
    for layer, raw_frame in raw.items():
        scored_frame = prepared[layer]
        raw_coverage[layer] = {
            "rows": int(len(raw_frame)),
            "min_date": str(raw_frame["target_date"].min()) if len(raw_frame) else None,
            "max_date": str(raw_frame["target_date"].max()) if len(raw_frame) else None,
            "scored_rows": int(len(scored_frame)),
            "dropped_unscored_rows": int(len(raw_frame) - len(scored_frame)),
        }

    all_quality: list[dict[str, Any]] = []
    all_trades: list[dict[str, Any]] = []
    all_wf_quality: list[dict[str, Any]] = []
    all_wf_trades: list[dict[str, Any]] = []
    all_daily: list[pd.DataFrame] = []
    scored_frames: list[pd.DataFrame] = []

    for layer, frame in prepared.items():
        scored = frame.copy()
        for spec in MODEL_SPECS:
            holdout_scored, quality_rows, trade_rows = fit_predict_holdout(frame, evidence_layer=layer, spec=spec)
            all_quality.extend(quality_rows)
            all_trades.extend(trade_rows)
            wf_scored, wf_quality_rows, wf_trade_rows, wf_daily = expanding_walk_forward(frame, evidence_layer=layer, spec=spec)
            all_wf_quality.extend(wf_quality_rows)
            all_wf_trades.extend(wf_trade_rows)
            if not wf_daily.empty:
                all_daily.append(wf_daily)
            for col in [c for c in holdout_scored.columns if c.startswith("p_no_")]:
                scored[col] = holdout_scored[col]
            for col in [c for c in wf_scored.columns if c.startswith("p_no_wf_")]:
                scored[col] = wf_scored[col]
        scored_frames.append(scored)

    quality = pd.DataFrame(all_quality + all_wf_quality)
    trades = pd.DataFrame(all_trades + all_wf_trades)
    wf_trades = pd.DataFrame(all_wf_trades)
    daily = pd.concat(all_daily, ignore_index=True) if all_daily else pd.DataFrame()
    scored_rows = pd.concat(scored_frames, ignore_index=True) if scored_frames else pd.DataFrame()

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "strategy": "regime_routed_no_calibrated_score_v2",
        "data_snapshot": {
            "sync_completed": True,
            "fact_rebuild_completed": True,
            "run_stack_exit_note": "non_clean_exit_after_rebuild_due_frontend_port_5174_busy",
            "clob_gate_pass": load_clob_gate_pass(),
        },
        "raw_coverage": raw_coverage,
        "coverage": {
            layer: {
                "rows": int(len(frame)),
                "min_date": str(frame["target_date"].min()) if len(frame) else None,
                "max_date": str(frame["target_date"].max()) if len(frame) else None,
                "train_rows": int((frame["target_date"].astype(str) < FORWARD_START).sum()),
                "forward_rows": int((frame["target_date"].astype(str) >= FORWARD_START).sum()),
            }
            for layer, frame in prepared.items()
        },
        "model_specs": [
            {"name": spec.name, "numeric": spec.numeric, "categorical": spec.categorical, "c": spec.c}
            for spec in MODEL_SPECS
        ],
        "outputs": {
            "summary_json": str(OUT_SUMMARY_JSON.relative_to(ROOT)),
            "model_quality": str(OUT_MODEL_QUALITY.relative_to(ROOT)),
            "trade_summary": str(OUT_TRADE_SUMMARY.relative_to(ROOT)),
            "walk_forward_summary": str(OUT_WALK_FORWARD.relative_to(ROOT)),
            "daily_summary": str(OUT_DAILY.relative_to(ROOT)),
            "scored_rows": str(OUT_SCORED_ROWS.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "significance": "FAIL_FORWARD_THIN_MIXED",
            "baseline": "current_fixed_quality_heuristic",
            "forward": "FAIL_TO_BEAT_CURRENT_HEURISTIC",
            "conclusion": "shadow_only_do_not_replace_live_score",
            "live_change": False,
        },
    }

    quality.to_csv(OUT_MODEL_QUALITY, index=False)
    trades.to_csv(OUT_TRADE_SUMMARY, index=False)
    wf_trades.to_csv(OUT_WALK_FORWARD, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    keep_cols = [
        "evidence_layer",
        "target_date",
        "city",
        "router_route",
        "router_ask",
        "label_no_win",
        "score_deployed_weight",
        "score_deployed_ratio",
    ] + [c for c in scored_rows.columns if c.startswith("p_no_")]
    scored_rows[[c for c in keep_cols if c in scored_rows.columns]].to_csv(OUT_SCORED_ROWS, index=False)
    OUT_SUMMARY_JSON.write_text(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    OUT_MD.write_text(render_md(payload, quality, trades, pd.DataFrame(all_wf_trades)) + "\n", encoding="utf-8")
    print(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
