#!/usr/bin/env python3
"""First-principles no-reheat hazard score for current-YES.

This script is intentionally not a slice search.  It builds one continuous
mechanism score for future-break hazard, calibrates that one-dimensional score
on train dates, and then checks whether the calibrated physical hazard creates
executable BUY_YES EV after market ask.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk import research_current_yes_no_reheat_segment_breakdown_v1 as seg
from scripts.analysis.reheat_risk import research_current_yes_no_reheat_state_slices_v1 as base


DB = ROOT / "runtime/weather.db"
FEATURE_ROWS = base.FEATURE_ROWS
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_no_reheat_hazard_score_v1"
OUT_SCORED = OUT_DIR / "no_reheat_hazard_score_v1_scored_rows.csv"
OUT_BINS = OUT_DIR / "no_reheat_hazard_score_v1_bins.csv"
OUT_RULES = OUT_DIR / "no_reheat_hazard_score_v1_ev_rules.csv"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-24-current-yes-no-reheat-hazard-score-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-24-current-yes-no-reheat-hazard-score-v1.md"

TRAIN_END = base.TRAIN_END
HOLDOUT_START = base.HOLDOUT_START
SEED = 20260624


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB))
    parser.add_argument("--feature-rows", default=str(FEATURE_ROWS))
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
        rows = {}
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
            rows[name] = dict(zip(cols, cur.fetchone()))
        return rows
    finally:
        conn.close()


def clip01(value: pd.Series) -> pd.Series:
    return pd.to_numeric(value, errors="coerce").clip(0.0, 1.0)


def add_mechanism_score(df: pd.DataFrame) -> pd.DataFrame:
    out = seg.add_segment_features(df)
    hour = pd.to_numeric(out["decision_hour_local"], errors="coerce")
    max_gap = pd.to_numeric(out["max_forecast_gap_to_running_native"], errors="coerce")
    min_peak_delta = pd.to_numeric(out["min_forecast_peak_delta_hours_local"], errors="coerce")
    plateau_obs = pd.to_numeric(out["plateau_obs_count_at_high"], errors="coerce")
    plateau_dur = pd.to_numeric(out["plateau_duration_min"], errors="coerce")
    minutes_since_max = pd.to_numeric(out["minutes_since_running_max"], errors="coerce")
    trend1 = pd.to_numeric(out["temp_trend_1h_f"], errors="coerce")
    trend3 = pd.to_numeric(out["temp_trend_3h_f"], errors="coerce")
    decline = pd.to_numeric(out["decline_native"], errors="coerce")
    obs_age = pd.to_numeric(out["decision_obs_age_min"], errors="coerce")
    spread = pd.to_numeric(out["current_yes_spread"], errors="coerce")

    # Higher component value means higher future-break hazard.  Keep the hazard
    # score physical; market quality is audited separately and used only by the
    # execution/EV layer.
    out["comp_remaining_heat"] = ((17.5 - hour) / 7.5).clip(0.0, 1.0)
    out["comp_forecast_gap"] = ((max_gap + 0.5) / 3.0).clip(0.0, 1.0)
    out["comp_forecast_peak_ahead"] = (min_peak_delta / 3.0).clip(0.0, 1.0)
    out["comp_plateau_not_confirmed"] = (1.0 - (plateau_obs / 4.0).clip(0.0, 1.0)).fillna(0.5)
    out["comp_short_plateau"] = (1.0 - (plateau_dur / 90.0).clip(0.0, 1.0)).fillna(0.5)
    out["comp_fresh_high"] = (1.0 - (minutes_since_max / 120.0).clip(0.0, 1.0)).fillna(0.5)
    out["comp_warming_trend"] = ((trend1.clip(lower=0.0) / 4.0) * 0.45 + (trend3.clip(lower=0.0) / 8.0) * 0.55).clip(0.0, 1.0)
    out["comp_not_faded"] = (1.0 - (decline / 1.5).clip(0.0, 1.0)).fillna(0.5)
    out["comp_stale_obs"] = (obs_age / 45.0).clip(0.0, 1.0).fillna(0.5)
    out["comp_wide_spread"] = (spread / 0.10).clip(0.0, 1.0).fillna(0.5)

    weights = {
        "comp_remaining_heat": 1.15,
        "comp_forecast_gap": 1.35,
        "comp_warming_trend": 1.15,
        "comp_fresh_high": 0.35,
        "comp_not_faded": 0.25,
        "comp_short_plateau": 0.20,
    }
    denom = sum(weights.values())
    out["mechanism_break_score_raw"] = sum(out[col].fillna(0.5) * w for col, w in weights.items()) / denom
    out.attrs["mechanism_weights"] = weights
    return out


def component_audit(frame: pd.DataFrame) -> list[dict[str, Any]]:
    from sklearn.metrics import roc_auc_score

    rows: list[dict[str, Any]] = []
    cols = [c for c in frame.columns if c.startswith("comp_")]
    for period in ["train", "holdout"]:
        sub = frame[frame["period"].eq(period)].copy()
        y = sub["label_future_break"].astype(int)
        for col in cols:
            x = pd.to_numeric(sub[col], errors="coerce")
            mask = x.notna()
            if y[mask].nunique() < 2:
                continue
            rows.append(
                {
                    "period": period,
                    "component": col,
                    "auc_break": float(roc_auc_score(y[mask], x[mask])),
                    "corr_break": float(np.corrcoef(x[mask], y[mask])[0, 1]) if mask.sum() > 2 else None,
                    "mean": float(x[mask].mean()),
                    "used_in_score": bool(col in frame.attrs.get("mechanism_weights", {})),
                }
            )
    return rows


def fit_one_dim_calibrator(train: pd.DataFrame) -> LogisticRegression:
    model = LogisticRegression(random_state=SEED, C=1.0, max_iter=2000)
    model.fit(train[["mechanism_break_score_raw"]], train["label_future_break"])
    return model


def safe_auc(y: pd.Series, p: pd.Series) -> float | None:
    if y.nunique() < 2:
        return None
    return float(roc_auc_score(y, p))


def metrics(frame: pd.DataFrame, p_col: str, name: str) -> dict[str, Any]:
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


def date_bootstrap_roi(frame: pd.DataFrame, reps: int = 4000) -> list[float | None]:
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


def trade_summary(frame: pd.DataFrame, name: str) -> dict[str, Any]:
    if frame.empty:
        return {
            "rule": name,
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
    return {
        "rule": name,
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
        "avg_edge": float(frame["mechanism_yes_edge"].mean()),
        "avg_mechanism_break": float(frame["p_break_mechanism"].mean()),
    }


def build_ev_rules(tradable: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for period in ["train", "holdout"]:
        frame = tradable[tradable["period"].eq(period)].copy()
        for edge in [0.00, 0.02, 0.05, 0.08, 0.10]:
            mask = frame["mechanism_yes_edge"].ge(edge)
            rows.append({**trade_summary(frame[mask], f"mech_edge_ge_{edge:.2f}"), "period": period, "min_edge": edge})
        for low, high in [(0.35, 0.50), (0.50, 0.70), (0.70, 0.90), (0.90, 0.97)]:
            mask = frame["current_yes_ask"].ge(low) & frame["current_yes_ask"].lt(high) & frame["mechanism_yes_edge"].ge(0.02)
            rows.append(
                {
                    **trade_summary(frame[mask], f"mech_edge_ge_0.02_ask_{low:.2f}_{high:.2f}"),
                    "period": period,
                    "min_edge": 0.02,
                    "ask_low": low,
                    "ask_high": high,
                }
            )
    return pd.DataFrame(rows)


def build_bins(scored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for period in ["train", "holdout"]:
        frame = scored[scored["period"].eq(period)].copy()
        if frame.empty:
            continue
        frame["hazard_bin"] = pd.qcut(frame["p_break_mechanism"], q=5, labels=False, duplicates="drop")
        for bin_id, sub in frame.groupby("hazard_bin", dropna=True):
            rows.append(
                {
                    "period": period,
                    "hazard_bin": int(bin_id),
                    "rows": int(len(sub)),
                    "dates": int(sub["target_date"].nunique()),
                    "cities": int(sub["city"].nunique()),
                    "avg_p_break_mechanism": float(sub["p_break_mechanism"].mean()),
                    "actual_break_rate": float(sub["label_future_break"].mean()),
                    "avg_ask": float(sub["current_yes_ask"].mean()),
                    "roi_buy_yes_all": float(sub["trade_pnl_per_share"].sum() / sub["current_yes_ask"].sum()),
                }
            )
    return pd.DataFrame(rows)


def render_rule_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| period | rule | rows | dates | win | avg ask | avg edge | ROI | CI |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {period} | {rule} | {rows} | {dates} | {win} | {ask} | {edge} | {roi} | {ci} |".format(
                period=row["period"],
                rule=row["rule"],
                rows=row["rows"],
                dates=row["dates"],
                win=pct(row["win_rate"]),
                ask=num(row["avg_ask"]),
                edge=pct(row["avg_edge"]),
                roi=pct(row["roi"]),
                ci=f"[{pct(row['roi_ci95'][0])}, {pct(row['roi_ci95'][1])}]",
            )
        )
    return lines


def build_markdown(payload: dict[str, Any], out_md: Path) -> None:
    lines = [
        "# Current-YES No-Reheat Hazard Score v1",
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
        f"- Feature rows: `{payload['inputs']['feature_rows']}`",
        f"- Feature target-date range: `{payload['coverage']['feature_min_target_date']}`..`{payload['coverage']['feature_max_target_date']}`",
        f"- Current-YES rows: {payload['coverage']['current_yes_rows']} / tradable rows: {payload['coverage']['tradable_rows']}",
        f"- Train: `<= {TRAIN_END}`; holdout: `{HOLDOUT_START}`..`{payload['coverage']['feature_max_target_date']}`",
        f"- DB fact refresh: `{payload['data_self_check']['fact_signal_candidates']['max_built_at']}`; CLOB gate not used for these replay rows.",
        "",
        "## First-Principles Score",
        "",
        "目标是连续估计 `p_future_break`，不是继续堆 hard filter。v1 只把方向体检后同号的物理成分放进 hazard：剩余加热时间、forecast ceiling gap、仍在升温、刚摸高、未充分 fade、plateau 时间短。`forecast_peak_ahead`、`plateau_not_confirmed`、`stale_obs`、`wide_spread` 在这批数据里方向弱或反向，不进物理 score。",
        "",
        "```json",
        json.dumps(payload["mechanism_weights"], indent=2, ensure_ascii=False),
        "```",
        "",
        "## Component Audit",
        "",
        "| period | component | used | AUC | corr | mean |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in payload["component_audit"]:
        lines.append(
            "| {period} | {component} | {used} | {auc} | {corr} | {mean} |".format(
                period=row["period"],
                component=row["component"],
                used="Y" if row["used_in_score"] else "N",
                auc=num(row["auc_break"]),
                corr=num(row["corr_break"]),
                mean=num(row["mean"]),
            )
        )
    lines.extend(
        [
        "",
        "## Probability Metrics",
        "",
        "| period | model | rows | break | pred break | AUC | Brier | logloss |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["metrics"]:
        lines.append(
            "| {period} | {model} | {rows} | {actual} | {pred} | {auc} | {brier} | {logloss} |".format(
                period=row["period"],
                model=row["model"],
                rows=row["rows"],
                actual=pct(row["actual_break_rate"]),
                pred=pct(row["mean_pred_break"]),
                auc=num(row["auc_break"]),
                brier=num(row["brier"]),
                logloss=num(row["logloss"]),
            )
        )
    lines.extend(
        [
            "",
            "## Mechanism Hazard Bins",
            "",
            "| period | bin | rows | pred break | actual break | avg ask | buy-YES ROI all |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["bins"]:
        lines.append(
            "| {period} | {bin_id} | {rows} | {pred} | {actual} | {ask} | {roi} |".format(
                period=row["period"],
                bin_id=row["hazard_bin"],
                rows=row["rows"],
                pred=pct(row["avg_p_break_mechanism"]),
                actual=pct(row["actual_break_rate"]),
                ask=num(row["avg_ask"]),
                roi=pct(row["roi_buy_yes_all"]),
            )
        )
    lines.extend(
        [
            "",
            "## EV Rules",
            "",
            *render_rule_table(payload["ev_rules"]),
            "",
            "## Verdict",
            "",
            "significance=FAIL / baseline=FAIL / forward=NA / conclusion=inconclusive",
            "",
            payload["verdict_text"],
            "",
            "## Outputs",
            "",
            f"- scored rows: `{payload['outputs']['scored_rows']}`",
            f"- bins CSV: `{payload['outputs']['bins_csv']}`",
            f"- rules CSV: `{payload['outputs']['rules_csv']}`",
            f"- json: `{payload['outputs']['json']}`",
        ]
    )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    feature_path = Path(args.feature_rows)
    out_dir = Path(args.out_dir)
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_dir.mkdir(parents=True, exist_ok=True)

    self_check = data_self_check(db_path)
    current_yes = base.load_current_yes_rows(feature_path)
    scored = add_mechanism_score(current_yes)
    scored["p_break_market_raw"] = (1.0 - scored["current_yes_ask"]).clip(1e-6, 1 - 1e-6)
    scored["trade_pnl_per_share"] = scored["label_survive"] - scored["current_yes_ask"]

    tradable = scored[
        scored["decision_hour_local"].between(10, 21)
        & scored["current_yes_ask"].between(0.35, 0.97, inclusive="left")
        & scored["current_yes_ask_size"].fillna(0).ge(5.0)
    ].copy()
    audit = component_audit(tradable)

    train = tradable[tradable["period"].eq("train") & tradable["mechanism_break_score_raw"].notna()].copy()
    if train["label_future_break"].nunique() < 2:
        raise RuntimeError("train labels need both future-break and survive rows")
    calibrator = fit_one_dim_calibrator(train)
    scored["p_break_mechanism"] = calibrator.predict_proba(scored[["mechanism_break_score_raw"]])[:, 1]
    scored["p_survive_mechanism"] = 1.0 - scored["p_break_mechanism"]
    scored["mechanism_yes_edge"] = scored["p_survive_mechanism"] - scored["current_yes_ask"]
    tradable = scored.loc[tradable.index].copy()

    metric_rows = []
    for period in ["train", "holdout"]:
        frame = tradable[tradable["period"].eq(period)].copy()
        for col, name in [("p_break_market_raw", "market_implied_break"), ("p_break_mechanism", "first_principles_mechanism")]:
            row = metrics(frame, col, name)
            row["period"] = period
            metric_rows.append(row)

    bins = build_bins(tradable)
    rules = build_ev_rules(tradable)
    rules_for_report = (
        rules[
            rules["rule"].isin(
                [
                    "mech_edge_ge_0.00",
                    "mech_edge_ge_0.02",
                    "mech_edge_ge_0.05",
                    "mech_edge_ge_0.08",
                    "mech_edge_ge_0.10",
                    "mech_edge_ge_0.02_ask_0.50_0.70",
                ]
            )
        ]
        .sort_values(["period", "rule"])
        .to_dict("records")
    )

    scored_out = scored[
        [
            "city",
            "target_date",
            "decision_snapshot_ts_utc",
            "decision_hour_local",
            "current_bracket",
            "current_yes_ask",
            "label_survive",
            "label_future_break",
            "period",
            "mechanism_break_score_raw",
            "p_break_mechanism",
            "p_survive_mechanism",
            "mechanism_yes_edge",
            "p_break_market_raw",
            "comp_remaining_heat",
            "comp_forecast_gap",
            "comp_forecast_peak_ahead",
            "comp_plateau_not_confirmed",
            "comp_short_plateau",
            "comp_fresh_high",
            "comp_warming_trend",
            "comp_not_faded",
            "comp_stale_obs",
            "comp_wide_spread",
        ]
    ].copy()
    scored_out.to_csv(OUT_SCORED, index=False)
    bins.to_csv(OUT_BINS, index=False)
    rules.to_csv(OUT_RULES, index=False)

    holdout_mech = [r for r in metric_rows if r["period"] == "holdout" and r["model"] == "first_principles_mechanism"][0]
    holdout_market = [r for r in metric_rows if r["period"] == "holdout" and r["model"] == "market_implied_break"][0]
    holdout_edge02 = rules[(rules["period"].eq("holdout")) & (rules["rule"].eq("mech_edge_ge_0.02"))].iloc[0].to_dict()
    headline = (
        "第一性原理 hazard score 能做出可解释排序，但当前 v1 没打赢 market baseline："
        f"holdout AUC {holdout_mech['auc_break']:.3f} vs market {holdout_market['auc_break']:.3f}，"
        f"`edge>=2%` 选 {int(holdout_edge02['rows'])} rows，ROI {pct(holdout_edge02['roi'])}，CI "
        f"[{pct(holdout_edge02['roi_ci95'][0])}, {pct(holdout_edge02['roi_ci95'][1])}]。"
    )
    verdict_text = (
        "这说明方向应继续沿连续 hazard 信号改特征，而不是继续加切片门。当前 v1 的物理成分还不够，尤其缺真实太阳高度、小时级 forecast curve、云/风变化和城市 source cadence；"
        "交易上不改 live，只输出 scored rows 供 forward shadow 和失败机制复盘。"
    )

    payload = {
        "generated_at_utc": now_utc(),
        "inputs": {"feature_rows": str(feature_path.relative_to(ROOT)), "db": str(db_path.relative_to(ROOT))},
        "data_self_check": self_check,
        "coverage": {
            "feature_min_target_date": str(current_yes["target_date"].min()),
            "feature_max_target_date": str(current_yes["target_date"].max()),
            "current_yes_rows": int(len(current_yes)),
            "current_yes_dates": int(current_yes["target_date"].nunique()),
            "current_yes_cities": int(current_yes["city"].nunique()),
            "tradable_rows": int(len(tradable)),
            "tradable_dates": int(tradable["target_date"].nunique()),
            "tradable_cities": int(tradable["city"].nunique()),
        },
        "mechanism_weights": scored.attrs.get("mechanism_weights", {}),
        "component_audit": audit,
        "calibrator": {
            "coef": float(calibrator.coef_[0][0]),
            "intercept": float(calibrator.intercept_[0]),
            "train_rows": int(len(train)),
            "train_dates": int(train["target_date"].nunique()),
        },
        "headline": headline,
        "metrics": metric_rows,
        "bins": bins.to_dict("records"),
        "ev_rules": rules_for_report,
        "verdict_text": verdict_text,
        "outputs": {
            "scored_rows": str(OUT_SCORED.relative_to(ROOT)),
            "bins_csv": str(OUT_BINS.relative_to(ROOT)),
            "rules_csv": str(OUT_RULES.relative_to(ROOT)),
            "json": str(out_json.relative_to(ROOT)),
            "markdown": str(out_md.relative_to(ROOT)),
        },
    }
    out_json.write_text(json.dumps(json_ready(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    build_markdown(json_ready(payload), out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
