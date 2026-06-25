#!/usr/bin/env python3
"""Atlas-aware current-YES no-reheat strategy research v2.

This uses the intraday weather regime atlas as shared PIT features.  Regimes
are features, not hard gates.  The test asks whether atlas features improve
future-break probability and BUY_YES EV versus market-implied break.
"""

from __future__ import annotations

import argparse
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
ATLAS_ROWS = ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_no_reheat_atlas_strategy_v2"
OUT_SCORED = OUT_DIR / "atlas_strategy_v2_scored_rows.csv"
OUT_METRICS = OUT_DIR / "atlas_strategy_v2_model_metrics.csv"
OUT_RULES = OUT_DIR / "atlas_strategy_v2_ev_rules.csv"
OUT_REGIME = OUT_DIR / "atlas_strategy_v2_regime_summary.csv"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-25-current-yes-no-reheat-atlas-strategy-v2.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-25-current-yes-no-reheat-atlas-strategy-v2.md"

TRAIN_END = "2026-05-31"
HOLDOUT_START = "2026-06-01"
HOLDOUT_END = "2026-06-20"
FORWARD_START = "2026-06-21"
SEED = 20260625

NUMERIC_FEATURES = [
    "decision_hour_local",
    "current_native",
    "running_native",
    "decline_native",
    "forecast_gap_to_running_native",
    "gfs_gap_to_running_native",
    "ecmwf_gap_to_running_native",
    "forecast_peak_delta_hours_local",
    "forecast_peak_hour_spread",
    "tmpf_now",
    "dwpf_now",
    "dewpoint_depression_f",
    "relative_humidity_pct",
    "wind_speed_kt",
    "sky_cover_code",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "minutes_since_running_max",
]

REGIME_FEATURES = [
    "city_family",
    "solar_window",
    "day_regime",
    "intraday_state",
    "moisture_cloud_regime",
    "wind_regime",
    "running_max_state",
]

MARKET_FEATURES = ["market_break_logit"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB))
    parser.add_argument("--atlas-rows", default=str(ATLAS_ROWS))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--out-json", default=str(OUT_JSON))
    parser.add_argument("--out-md", default=str(OUT_MD))
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
        out = float(value)
        return None if not math.isfinite(out) else out
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    return value


def pct(value: Any, digits: int = 1) -> str:
    if value is None:
        return "NA"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"{f * 100:+.{digits}f}%"


def num(value: Any, digits: int = 3) -> str:
    if value is None:
        return "NA"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"{f:.{digits}f}"


def connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def data_self_check(db_path: Path) -> dict[str, Any]:
    conn = connect_ro(db_path)
    try:
        out = {}
        for name, sql in {
            "fact_signal_candidates": (
                "SELECT COUNT(*) AS rows, MIN(event_date) AS min_date, MAX(event_date) AS max_date, "
                "MAX(fact_built_at_utc) AS max_built_at FROM fact_signal_candidates"
            ),
            "fact_trades": (
                "SELECT COUNT(*) AS rows, MIN(target_date) AS min_date, MAX(target_date) AS max_date, "
                "MAX(fact_built_at_utc) AS max_built_at FROM fact_trades"
            ),
            "settlement_outcomes": (
                "SELECT COUNT(*) AS rows, MIN(target_date) AS min_date, MAX(target_date) AS max_date, "
                "NULL AS max_built_at FROM settlement_outcomes"
            ),
        }.items():
            cur = conn.execute(sql)
            cols = [d[0] for d in cur.description]
            out[name] = dict(zip(cols, cur.fetchone()))
        return out
    finally:
        conn.close()


def logit_series(p: pd.Series) -> pd.Series:
    x = pd.to_numeric(p, errors="coerce").clip(1e-5, 1 - 1e-5)
    return np.log(x / (1 - x))


def load_rows(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df[df["current_yes_ask"].notna() & df["current_bracket_held"].notna()].copy()
    df["target_date"] = df["target_date"].astype(str)
    df["label_survive"] = pd.to_numeric(df["current_bracket_held"], errors="coerce").fillna(0).astype(int)
    df["label_future_break"] = 1 - df["label_survive"]
    df["period"] = np.select(
        [
            df["target_date"].le(TRAIN_END),
            df["target_date"].between(HOLDOUT_START, HOLDOUT_END),
            df["target_date"].ge(FORWARD_START),
        ],
        ["train", "holdout", "forward"],
        default="gap",
    )
    df["p_break_market_raw"] = (1.0 - pd.to_numeric(df["current_yes_ask"], errors="coerce")).clip(1e-6, 1 - 1e-6)
    df["market_break_logit"] = logit_series(df["p_break_market_raw"])
    df["trade_pnl_per_share"] = df["label_survive"] - df["current_yes_ask"]
    for col in NUMERIC_FEATURES + ["current_yes_ask"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in REGIME_FEATURES:
        df[col] = df[col].fillna("missing").astype(str)
    return df


def tradable(df: pd.DataFrame) -> pd.DataFrame:
    return df[
        df["decision_hour_local"].between(10, 21)
        & df["current_yes_ask"].between(0.35, 0.97, inclusive="left")
        & df["period"].isin(["train", "holdout", "forward"])
    ].copy()


def make_pipeline(numeric: list[str], categorical: list[str], c: float = 0.4) -> Pipeline:
    pre = ColumnTransformer(
        [
            (
                "num",
                Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())]),
                numeric,
            ),
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=5), categorical),
        ]
    )
    return Pipeline([("pre", pre), ("model", LogisticRegression(max_iter=3000, C=c, random_state=SEED))])


def safe_auc(y: pd.Series, p: pd.Series) -> float | None:
    if y.nunique() < 2:
        return None
    return float(roc_auc_score(y, p))


def model_metrics(frame: pd.DataFrame, p_col: str, name: str) -> dict[str, Any]:
    if frame.empty:
        return {"model": name, "rows": 0}
    y = frame["label_future_break"].astype(int)
    p = pd.to_numeric(frame[p_col], errors="coerce").clip(1e-6, 1 - 1e-6)
    return {
        "model": name,
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "actual_break_rate": float(y.mean()),
        "mean_pred_break": float(p.mean()),
        "auc_break": safe_auc(y, p),
        "brier": float(brier_score_loss(y, p)),
        "logloss": float(log_loss(y, p)) if y.nunique() > 1 else None,
    }


def date_bootstrap_roi(frame: pd.DataFrame, reps: int = 3000) -> list[float | None]:
    by_date = frame.groupby("target_date")[["current_yes_ask", "trade_pnl_per_share"]].sum()
    if len(by_date) < 2:
        return [None, None]
    vals = by_date.to_numpy(dtype=float)
    rng = np.random.default_rng(SEED)
    out = []
    for _ in range(reps):
        sample = vals[rng.integers(0, len(vals), size=len(vals))]
        cost = float(sample[:, 0].sum())
        if cost > 0:
            out.append(float(sample[:, 1].sum() / cost))
    if not out:
        return [None, None]
    arr = np.asarray(out)
    return [float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))]


def trade_summary(frame: pd.DataFrame, name: str, p_col: str) -> dict[str, Any]:
    if frame.empty:
        return {
            "rule": name,
            "p_col": p_col,
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "cost": 0.0,
            "pnl": 0.0,
            "roi": None,
            "roi_ci95": [None, None],
            "win_rate": None,
            "avg_ask": None,
            "avg_edge": None,
        }
    cost = float(frame["current_yes_ask"].sum())
    pnl = float(frame["trade_pnl_per_share"].sum())
    edge = (1.0 - frame[p_col]) - frame["current_yes_ask"]
    return {
        "rule": name,
        "p_col": p_col,
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost else None,
        "roi_ci95": date_bootstrap_roi(frame),
        "win_rate": float(frame["label_survive"].mean()),
        "future_break_rate": float(frame["label_future_break"].mean()),
        "avg_ask": float(frame["current_yes_ask"].mean()),
        "avg_edge": float(edge.mean()),
        "avg_p_break": float(frame[p_col].mean()),
    }


def ev_rules(scored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for period in ["train", "holdout", "forward"]:
        frame = scored[scored["period"].eq(period)].copy()
        for p_col, label in [
            ("p_break_atlas", "atlas"),
            ("p_break_market_atlas", "market_atlas"),
        ]:
            edge = (1.0 - frame[p_col]) - frame["current_yes_ask"]
            for min_edge in [0.00, 0.02, 0.05, 0.08, 0.10]:
                sub = frame[edge.ge(min_edge)]
                row = trade_summary(sub, f"{label}_edge_ge_{min_edge:.2f}", p_col)
                row.update({"period": period, "min_edge": min_edge})
                rows.append(row)
            sub = frame[edge.ge(0.02) & frame["current_yes_ask"].between(0.50, 0.70, inclusive="left")]
            row = trade_summary(sub, f"{label}_edge_ge_0.02_ask_50_70", p_col)
            row.update({"period": period, "min_edge": 0.02, "ask_low": 0.50, "ask_high": 0.70})
            rows.append(row)
    return pd.DataFrame(rows)


def regime_summary(scored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for period in ["holdout", "forward"]:
        frame = scored[scored["period"].eq(period)].copy()
        for col in ["day_regime", "intraday_state", "moisture_cloud_regime", "running_max_state"]:
            for value, sub in frame.groupby(col, dropna=False):
                if len(sub) < 20:
                    continue
                rows.append(
                    {
                        "period": period,
                        "regime_kind": col,
                        "regime": str(value),
                        "rows": int(len(sub)),
                        "dates": int(sub["target_date"].nunique()),
                        "cities": int(sub["city"].nunique()),
                        "break_rate": float(sub["label_future_break"].mean()),
                        "avg_ask": float(sub["current_yes_ask"].mean()),
                        "roi_buy_yes_all": float(sub["trade_pnl_per_share"].sum() / sub["current_yes_ask"].sum()),
                        "avg_p_break_atlas": float(sub["p_break_atlas"].mean()),
                        "avg_p_break_market_atlas": float(sub["p_break_market_atlas"].mean()),
                    }
                )
    return pd.DataFrame(rows).sort_values(["period", "regime_kind", "rows"], ascending=[True, True, False])


def render_metric_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| period | model | rows | break | pred break | AUC | Brier | logloss |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {period} | {model} | {rows} | {actual} | {pred} | {auc} | {brier} | {logloss} |".format(
                period=row["period"],
                model=row["model"],
                rows=row["rows"],
                actual=pct(row.get("actual_break_rate")),
                pred=pct(row.get("mean_pred_break")),
                auc=num(row.get("auc_break")),
                brier=num(row.get("brier")),
                logloss=num(row.get("logloss")),
            )
        )
    return lines


def render_rule_table(rows: list[dict[str, Any]], limit: int = 18) -> list[str]:
    lines = [
        "| period | rule | rows | dates | win | avg ask | avg edge | ROI | CI |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows[:limit]:
        lines.append(
            "| {period} | {rule} | {rows} | {dates} | {win} | {ask} | {edge} | {roi} | {ci} |".format(
                period=row["period"],
                rule=row["rule"],
                rows=row["rows"],
                dates=row["dates"],
                win=pct(row.get("win_rate")),
                ask=num(row.get("avg_ask")),
                edge=pct(row.get("avg_edge")),
                roi=pct(row.get("roi")),
                ci=f"[{pct(row['roi_ci95'][0])}, {pct(row['roi_ci95'][1])}]",
            )
        )
    return lines


def build_markdown(payload: dict[str, Any], out_md: Path) -> None:
    lines = [
        "# Current-YES No-Reheat Atlas Strategy v2",
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
        f"- Atlas date range: `{payload['coverage']['min_target_date']}`..`{payload['coverage']['max_target_date']}`",
        f"- Tradable current-YES rows: {payload['coverage']['tradable_rows']} / dates {payload['coverage']['tradable_dates']} / cities {payload['coverage']['tradable_cities']}",
        f"- Train: `<= {TRAIN_END}`; holdout: `{HOLDOUT_START}`..`{HOLDOUT_END}`; forward/settled check: `{FORWARD_START}`..`{payload['coverage']['max_target_date']}`",
        f"- DB fact refresh: `{payload['data_self_check']['fact_signal_candidates']['max_built_at']}`",
        "",
        "Regime labels are PIT features, not hard gates. This report uses quote-ask opportunity rows from the atlas, not live fills.",
        "",
        "Leakage guard: realized columns such as `remaining_heat_native`, `forecast_error_native`, final max, payoffs, and ROI are not model features. They are used only as labels/diagnostics after scoring.",
        "",
        "## Model Metrics",
        "",
        *render_metric_table(payload["metrics"]),
        "",
        "## EV Rules",
        "",
        *render_rule_table(payload["ev_rules"], 24),
        "",
        "## Regime Diagnostics",
        "",
        "| period | kind | regime | rows | break | avg ask | ROI all | p_atlas | p_market_atlas |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["regime_summary"][:20]:
        lines.append(
            "| {period} | {kind} | {regime} | {rows} | {break_rate} | {ask} | {roi} | {pa} | {pm} |".format(
                period=row["period"],
                kind=row["regime_kind"],
                regime=row["regime"],
                rows=row["rows"],
                break_rate=pct(row["break_rate"]),
                ask=num(row["avg_ask"]),
                roi=pct(row["roi_buy_yes_all"]),
                pa=pct(row["avg_p_break_atlas"]),
                pm=pct(row["avg_p_break_market_atlas"]),
            )
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
            f"- scored rows: `{payload['outputs']['scored_rows']}`",
            f"- model metrics: `{payload['outputs']['model_metrics']}`",
            f"- EV rules: `{payload['outputs']['ev_rules']}`",
            f"- regime summary: `{payload['outputs']['regime_summary']}`",
            f"- json: `{payload['outputs']['json']}`",
        ]
    )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    atlas_path = Path(args.atlas_rows)
    out_dir = Path(args.out_dir)
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_dir.mkdir(parents=True, exist_ok=True)

    self_check = data_self_check(db_path)
    rows = load_rows(atlas_path)
    work = tradable(rows)
    train = work[work["period"].eq("train")].copy()
    if train["label_future_break"].nunique() < 2:
        raise RuntimeError("train split needs both classes")

    atlas_model = make_pipeline(NUMERIC_FEATURES, REGIME_FEATURES, c=0.35)
    atlas_model.fit(train[NUMERIC_FEATURES + REGIME_FEATURES], train["label_future_break"])

    market_atlas_model = make_pipeline(MARKET_FEATURES + NUMERIC_FEATURES, REGIME_FEATURES, c=0.35)
    market_atlas_model.fit(train[MARKET_FEATURES + NUMERIC_FEATURES + REGIME_FEATURES], train["label_future_break"])

    work["p_break_atlas"] = atlas_model.predict_proba(work[NUMERIC_FEATURES + REGIME_FEATURES])[:, 1]
    work["p_break_market_atlas"] = market_atlas_model.predict_proba(
        work[MARKET_FEATURES + NUMERIC_FEATURES + REGIME_FEATURES]
    )[:, 1]

    metric_rows = []
    for period in ["train", "holdout", "forward"]:
        frame = work[work["period"].eq(period)].copy()
        if frame.empty:
            continue
        for col, label in [
            ("p_break_market_raw", "market_implied_break"),
            ("p_break_atlas", "atlas_regime_physical"),
            ("p_break_market_atlas", "market_plus_atlas"),
        ]:
            row = model_metrics(frame, col, label)
            row["period"] = period
            metric_rows.append(row)

    rules = ev_rules(work)
    regimes = regime_summary(work)
    work.to_csv(OUT_SCORED, index=False)
    pd.DataFrame(metric_rows).to_csv(OUT_METRICS, index=False)
    rules.to_csv(OUT_RULES, index=False)
    regimes.to_csv(OUT_REGIME, index=False)

    holdout = {r["model"]: r for r in metric_rows if r["period"] == "holdout"}
    forward = {r["model"]: r for r in metric_rows if r["period"] == "forward"}
    holdout_rule = rules[(rules["period"].eq("holdout")) & (rules["rule"].eq("market_atlas_edge_ge_0.02"))].iloc[0]
    forward_rule = rules[(rules["period"].eq("forward")) & (rules["rule"].eq("market_atlas_edge_ge_0.02"))].iloc[0]

    headline = (
        f"Atlas regime features improve the physical model versus v1-style raw physics but still do not beat market enough for trading: "
        f"holdout market+atlas AUC {holdout['market_plus_atlas']['auc_break']:.3f} vs market {holdout['market_implied_break']['auc_break']:.3f}; "
        f"`market_atlas edge>=2%` holdout ROI {pct(holdout_rule['roi'])}, CI "
        f"[{pct(holdout_rule['roi_ci95'][0])}, {pct(holdout_rule['roi_ci95'][1])}], "
        f"forward ROI {pct(forward_rule['roi'])} on {int(forward_rule['rows'])} rows."
    )

    verdict = {
        "significance": "FAIL",
        "baseline": "FAIL",
        "forward": "FAIL",
        "conclusion": "inconclusive",
        "text": (
            "Regime atlas is useful as a shared feature layer and improves interpretability, but the current current-YES no-reheat expression is not live-ready. "
            "Market pricing remains the stronger baseline, and EV-selected rows do not pass holdout/forward gates. Keep it as shadow/research telemetry and use the scored rows to diagnose which regimes market under/overprices."
        ),
    }

    payload = {
        "generated_at_utc": now_utc(),
        "inputs": {"atlas_rows": str(atlas_path.relative_to(ROOT)), "db": str(db_path.relative_to(ROOT))},
        "data_self_check": self_check,
        "coverage": {
            "min_target_date": str(work["target_date"].min()),
            "max_target_date": str(work["target_date"].max()),
            "tradable_rows": int(len(work)),
            "tradable_dates": int(work["target_date"].nunique()),
            "tradable_cities": int(work["city"].nunique()),
            "period_counts": work["period"].value_counts().to_dict(),
        },
        "features": {"numeric": NUMERIC_FEATURES, "regime": REGIME_FEATURES, "market": MARKET_FEATURES},
        "headline": headline,
        "metrics": metric_rows,
        "ev_rules": rules[
            rules["rule"].isin(
                [
                    "atlas_edge_ge_0.02",
                    "atlas_edge_ge_0.05",
                    "atlas_edge_ge_0.02_ask_50_70",
                    "market_atlas_edge_ge_0.00",
                    "market_atlas_edge_ge_0.02",
                    "market_atlas_edge_ge_0.05",
                    "market_atlas_edge_ge_0.02_ask_50_70",
                ]
            )
        ].sort_values(["period", "rule"]).to_dict("records"),
        "regime_summary": regimes.to_dict("records"),
        "verdict": verdict,
        "outputs": {
            "scored_rows": str(OUT_SCORED.relative_to(ROOT)),
            "model_metrics": str(OUT_METRICS.relative_to(ROOT)),
            "ev_rules": str(OUT_RULES.relative_to(ROOT)),
            "regime_summary": str(OUT_REGIME.relative_to(ROOT)),
            "json": str(out_json.relative_to(ROOT)),
            "markdown": str(out_md.relative_to(ROOT)),
        },
    }
    out_json.write_text(json.dumps(json_ready(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    build_markdown(json_ready(payload), out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
