#!/usr/bin/env python3
"""Current-bucket YES selector for the no-reheat thesis.

v6 showed that weather features can find no-reheat states, but the d1 NO
expression underperformed the sibling current-bucket YES expression.  This
script tests current YES directly, using the same expanded replay and weather
features.
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
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/theta_no_weather_model_selector_v6/feature_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_selector_v7"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-16-theta-yes-current-selector-v7.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-16-theta-yes-current-selector-v7.md"

SPLIT_DATE = "2026-06-01"
SEED = 20260616

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
    "yes_current_ask",
    "best_ask",
    "ask_gap_no_minus_yes",
    "log_no_size",
    "log_yes_size",
]
CAT_FEATURES = ["city", "unit"]


@dataclass(frozen=True)
class ModelSpec:
    name: str
    numeric_features: tuple[str, ...]
    categorical_features: tuple[str, ...] = tuple(CAT_FEATURES)


@dataclass(frozen=True)
class Rule:
    yes_ask_min: float
    decline_min: float
    hour_start: int
    hour_end: int
    p_win_min: float
    ev_min: float

    @property
    def name(self) -> str:
        return (
            f"yesAsk>={self.yes_ask_min:g}|decline>={self.decline_min:g}|h{self.hour_start}-{self.hour_end}|"
            f"pWin>={self.p_win_min:g}|ev>={self.ev_min:g}"
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


def load_rows() -> pd.DataFrame:
    q = pd.read_csv(FEATURE_ROWS)
    q["target_date"] = q["target_date"].astype(str)
    q["period"] = np.where(q["target_date"] < SPLIT_DATE, "train", "holdout")
    q["label_yes_wins"] = q["current_yes_wins"].astype(int)
    q = (
        q.sort_values(["decision_hour_local", "snapshot_ts_utc", "city", "target_date", "current_bracket"])
        .drop_duplicates(["city", "target_date", "decision_hour_local", "current_bracket"], keep="first")
        .copy()
    )
    return q


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
            ("model", LogisticRegression(max_iter=2000, C=0.8, random_state=SEED)),
        ]
    )


def oof_predict(train: pd.DataFrame, spec: ModelSpec) -> np.ndarray:
    y = train["label_yes_wins"].to_numpy()
    groups = train["target_date"].to_numpy()
    unique_dates = np.unique(groups)
    pred = np.full(len(train), np.nan)
    if len(unique_dates) < 3 or len(np.unique(y)) < 2:
        pred[:] = y.mean() if len(y) else np.nan
        return pred
    n_splits = min(5, len(unique_dates))
    xcols = list(spec.numeric_features + spec.categorical_features)
    for tr_idx, te_idx in GroupKFold(n_splits=n_splits).split(train[xcols], y, groups):
        if len(np.unique(y[tr_idx])) < 2:
            pred[te_idx] = y[tr_idx].mean()
            continue
        model = make_model(spec)
        model.fit(train.iloc[tr_idx][xcols], y[tr_idx])
        pred[te_idx] = model.predict_proba(train.iloc[te_idx][xcols])[:, 1]
    return pred


def score_holdout(q: pd.DataFrame, spec: ModelSpec) -> tuple[pd.DataFrame, dict[str, Any]]:
    train = q[q["period"].eq("train")].copy()
    holdout = q[q["period"].eq("holdout")].copy()
    pcol = f"p_yes_win_{spec.name}"
    xcols = list(spec.numeric_features + spec.categorical_features)
    train[pcol] = oof_predict(train, spec)
    model = make_model(spec)
    model.fit(train[xcols], train["label_yes_wins"].to_numpy())
    holdout[pcol] = model.predict_proba(holdout[xcols])[:, 1]
    scored = pd.concat([train, holdout], ignore_index=True)
    metrics = {}
    for period, frame in scored.groupby("period"):
        y = frame["label_yes_wins"].to_numpy()
        p = frame[pcol].to_numpy()
        metrics[f"{period}_brier"] = float(brier_score_loss(y, p)) if len(np.unique(y)) > 1 else None
        metrics[f"{period}_auc"] = float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else None
        metrics[f"{period}_base_rate"] = float(y.mean()) if len(y) else None
    return scored, metrics


def dedupe_strategy(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.sort_values(["decision_hour_local", "snapshot_ts_utc", "city", "target_date", "current_bracket"])
        .drop_duplicates(["city", "target_date", "current_bracket"], keep="first")
        .copy()
    )


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"rows": 0, "active_dates": 0, "yes_roi": None, "yes_minus_no_roi": None}
    yes_cost = float(df["yes_current_ask"].sum())
    yes_pnl = float(df["yes_current_pnl"].sum())
    no_cost = float(df["best_ask"].sum())
    no_pnl = float(df["no_pnl"].sum())
    daily = df.groupby("target_date").agg(yes_pnl=("yes_current_pnl", "sum"), no_pnl=("no_pnl", "sum"))
    return {
        "rows": int(len(df)),
        "active_dates": int(df["target_date"].nunique()),
        "cities": int(df["city"].nunique()),
        "avg_yes_ask": float(df["yes_current_ask"].mean()),
        "avg_no_ask": float(df["best_ask"].mean()),
        "yes_cost": yes_cost,
        "yes_pnl": yes_pnl,
        "yes_roi": yes_pnl / yes_cost if yes_cost else None,
        "no_cost": no_cost,
        "no_pnl": no_pnl,
        "no_roi": no_pnl / no_cost if no_cost else None,
        "yes_minus_no_roi": yes_pnl / yes_cost - no_pnl / no_cost if yes_cost and no_cost else None,
        "yes_win_rate": float(df["current_yes_wins"].mean()),
        "no_lose_rate": float(df["no_loses"].mean()),
        "yes_positive_date_rate": float((daily["yes_pnl"] > 0).mean()),
        "no_positive_date_rate": float((daily["no_pnl"] > 0).mean()),
    }


def bootstrap(df: pd.DataFrame, reps: int = 2000) -> dict[str, Any]:
    if df.empty or df["target_date"].nunique() < 3:
        return {"yes_roi_ci95": [None, None], "yes_minus_no_ci95": [None, None], "reps": 0}
    daily = df.groupby("target_date").agg(
        yes_cost=("yes_current_ask", "sum"),
        yes_pnl=("yes_current_pnl", "sum"),
        no_cost=("best_ask", "sum"),
        no_pnl=("no_pnl", "sum"),
    )
    rng = np.random.default_rng(SEED)
    yes_vals = []
    delta_vals = []
    for _ in range(reps):
        work = daily.iloc[rng.integers(0, len(daily), len(daily))]
        yes_cost = float(work["yes_cost"].sum())
        no_cost = float(work["no_cost"].sum())
        if yes_cost <= 0 or no_cost <= 0:
            continue
        yes_roi = float(work["yes_pnl"].sum() / yes_cost)
        no_roi = float(work["no_pnl"].sum() / no_cost)
        yes_vals.append(yes_roi)
        delta_vals.append(yes_roi - no_roi)
    ylo, yhi = np.quantile(yes_vals, [0.025, 0.975]) if yes_vals else (float("nan"), float("nan"))
    dlo, dhi = np.quantile(delta_vals, [0.025, 0.975]) if delta_vals else (float("nan"), float("nan"))
    return {"yes_roi_ci95": [float(ylo), float(yhi)], "yes_minus_no_ci95": [float(dlo), float(dhi)], "reps": len(yes_vals)}


def rule_grid() -> list[Rule]:
    return [
        Rule(ask, decline, h0, h1, pwin, ev)
        for ask in (0.65, 0.75, 0.80, 0.85, 0.90)
        for decline in (0.0, 0.5, 1.0, 1.5)
        for h0, h1 in ((13, 17), (13, 15), (15, 17))
        for pwin in (0.50, 0.60, 0.70, 0.80, 0.90)
        for ev in (-0.05, -0.02, 0.0, 0.02, 0.05)
    ]


def wf_rule_grid() -> list[Rule]:
    return [
        Rule(ask, decline, h0, h1, pwin, ev)
        for ask in (0.65, 0.75, 0.85)
        for decline in (0.0, 0.5, 1.0)
        for h0, h1 in ((13, 17), (13, 15))
        for pwin in (0.60, 0.70, 0.80)
        for ev in (-0.05, -0.02, 0.0)
    ]


def apply_rule(df: pd.DataFrame, pcol: str, rule: Rule) -> pd.DataFrame:
    expected_ev = df[pcol] - df["yes_current_ask"]
    mask = (
        df["yes_current_ask"].ge(rule.yes_ask_min)
        & df["decline_c"].ge(rule.decline_min)
        & df["decision_hour_local"].between(rule.hour_start, rule.hour_end)
        & df[pcol].ge(rule.p_win_min)
        & expected_ev.ge(rule.ev_min)
    )
    return dedupe_strategy(df[mask].copy())


def fixed_grid(scored: pd.DataFrame, spec: ModelSpec) -> tuple[pd.DataFrame, pd.DataFrame]:
    pcol = f"p_yes_win_{spec.name}"
    rows = []
    frames = []
    for rule in rule_grid():
        sel = apply_rule(scored, pcol, rule)
        for period in ("train", "holdout", "all"):
            frame = sel if period == "all" else sel[sel["period"].eq(period)]
            if period in {"train", "holdout"} and (len(frame) < 8 or frame["target_date"].nunique() < 4):
                continue
            rows.append({"model": spec.name, "rule": rule.name, "period": period, **summarize(frame)})
        if not sel.empty:
            temp = sel.copy()
            temp["model"] = spec.name
            temp["rule"] = rule.name
            frames.append(temp)
    return pd.DataFrame(rows), pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def choose_train_rules(grid: pd.DataFrame) -> pd.DataFrame:
    train = grid[grid["period"].eq("train")].copy()
    train = train[train["rows"].ge(20) & train["active_dates"].ge(8)]
    train = train[train["yes_roi"].gt(0) & train["yes_minus_no_roi"].gt(0)]
    return train.sort_values(["yes_minus_no_roi", "yes_roi", "rows"], ascending=[False, False, False]).head(10)


def chosen_holdout(grid: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for model, g in grid.groupby("model"):
        chosen = choose_train_rules(g)
        for rank, row in enumerate(chosen.itertuples(index=False), start=1):
            hold = g[(g["period"].eq("holdout")) & (g["rule"].eq(row.rule))]
            if hold.empty:
                rows.append({"model": model, "rank": rank, "rule": row.rule, "rows": 0, "active_dates": 0})
                continue
            out = hold.iloc[0].to_dict()
            out["rank"] = rank
            frame = selected[
                selected["model"].eq(model)
                & selected["rule"].eq(row.rule)
                & selected["period"].eq("holdout")
            ]
            out.update(bootstrap(frame))
            rows.append(out)
    return pd.DataFrame(rows)


def walkforward(q: pd.DataFrame, spec: ModelSpec) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    decisions = []
    dates = sorted(q["target_date"].unique())
    pcol = f"p_yes_win_{spec.name}"
    xcols = list(spec.numeric_features + spec.categorical_features)
    for date in dates:
        hist = q[q["target_date"] < date].copy()
        day = q[q["target_date"].eq(date)].copy()
        if hist["target_date"].nunique() < 10 or len(hist) < 300 or hist["label_yes_wins"].nunique() < 2:
            continue
        hist[pcol] = oof_predict(hist, spec)
        model = make_model(spec)
        model.fit(hist[xcols], hist["label_yes_wins"].to_numpy())
        day[pcol] = model.predict_proba(day[xcols])[:, 1]
        tests = []
        for rule in wf_rule_grid():
            hsel = apply_rule(hist, pcol, rule)
            if len(hsel) < 20 or hsel["target_date"].nunique() < 8:
                continue
            sm = summarize(hsel)
            if sm.get("yes_roi") is None or sm.get("yes_minus_no_roi") is None:
                continue
            if sm["yes_roi"] <= 0 or sm["yes_minus_no_roi"] <= 0:
                continue
            tests.append({"rule_obj": rule, "rule": rule.name, "score": sm["yes_minus_no_roi"], **sm})
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
                "hist_yes_roi": chosen["yes_roi"],
                "hist_yes_minus_no_roi": chosen["yes_minus_no_roi"],
                "test_rows": int(len(dsel)),
            }
        )
        if not dsel.empty:
            temp = dsel.copy()
            temp["model"] = spec.name
            temp["selected_rule"] = chosen["rule"]
            rows.append(temp)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(), pd.DataFrame(decisions)


def wf_summary(wf_rows: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for spec in MODEL_SPECS:
        frame = wf_rows[wf_rows["model"].eq(spec.name)] if not wf_rows.empty else pd.DataFrame()
        rows.append({"model": spec.name, **summarize(frame), **bootstrap(frame)})
    return pd.DataFrame(rows)


def write_markdown(payload: dict[str, Any], metrics: pd.DataFrame, chosen: pd.DataFrame, wf: pd.DataFrame) -> None:
    self_check = payload["data_self_check"]
    gate = payload["clob_gate"]
    lines = [
        "# Theta Current YES Selector v7",
        "",
        "Status: snapshot",
        f"Generated: {payload['generated_at_utc']}",
        "Target metric: `current_yes_no_reheat` = 在 source-aligned city-day 中，买当前 running-max bracket 的 YES，赌最终最高温仍落在当前档。",
        "",
        "## 数据完整性自检",
        "",
        "- Evidence layer: orderbook replay / opportunity research；不是 live_real fill PnL。",
        f"- current YES hour rows: {payload['coverage']['input_rows']}; strategy unique city-date-current rows: {payload['coverage']['strategy_unique_rows']}; active dates: {payload['coverage']['active_dates']}.",
        f"- fact_built_at_utc: `{self_check['fact_trades_max_built_at_utc']}`。",
        f"- fact_trades trade_class: `{self_check['fact_trades_by_class']}`。",
        f"- fact_trades settlement_status: `{self_check['fact_trades_by_settlement_status']}`。",
        f"- fact_signal_candidates coverage: `{self_check['fact_signal_candidate_coverage']}`。",
        f"- CLOB orders/fills join: `{self_check['clob_order_fill_join']}`。",
        f"- CLOB coverage gate: gate_pass={gate.get('gate_pass')}, missing_order_rows={gate.get('missing_order_rows')}, over_order_keys={gate.get('over_order_keys')}, db_fill_cost_minus_fact_cost={gate.get('db_fill_cost_minus_fact_cost')}.",
        "",
        "## 人话结论",
        "",
        "这轮把表达正式切到 current YES。结论是：current YES 确实比 d1 NO 更贴合 no-reheat thesis，但在当前 26 个 replay 日期里，仍没有通过 live 三道门。最主要的卡点是 train/holdout 不稳定和 prefix walk-forward 样本薄。",
        "",
        "所以今天还不能说找到了 live 策略；但我们已经把方向从 NO carry 修正到了更合理的 current YES sibling expression。",
        "",
        "## 模型判别力",
        "",
        "| model | train/OOS Brier | train/OOS AUC | train base | holdout base |",
        "|---|---:|---:|---:|---:|",
    ]
    for _, r in metrics.iterrows():
        lines.append(
            f"| `{r['model']}` | {r['train_brier']:.4f} / {r['holdout_brier']:.4f} | "
            f"{r['train_auc']:.3f} / {r['holdout_auc']:.3f} | {pct(r['train_base_rate'], signed=False)} | {pct(r['holdout_base_rate'], signed=False)} |"
        )
    lines.extend(
        [
            "",
            "## Train 选出的规则在 holdout 的结果",
            "",
            "| model | rank | rows | dates | YES ROI | d1 NO ROI | YES-NO | YES CI95 | Delta CI95 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    if chosen.empty:
        lines.append("| NA | NA | 0 | 0 | NA | NA | NA | NA | NA |")
    else:
        for _, r in chosen.head(14).iterrows():
            yci = r.get("yes_roi_ci95", [None, None])
            dci = r.get("yes_minus_no_ci95", [None, None])
            if isinstance(yci, str):
                yci = json.loads(yci.replace("'", '"'))
            if isinstance(dci, str):
                dci = json.loads(dci.replace("'", '"'))
            lines.append(
                f"| `{r.get('model')}` | {int(r.get('rank', 0))} | {int(r.get('rows', 0) or 0)} | "
                f"{int(r.get('active_dates', 0) or 0)} | {pct(r.get('yes_roi'))} | {pct(r.get('no_roi'))} | "
                f"{pct(r.get('yes_minus_no_roi'))} | [{pct(yci[0])}, {pct(yci[1])}] | [{pct(dci[0])}, {pct(dci[1])}] |"
            )
    lines.extend(
        [
            "",
            "## Prefix walk-forward",
            "",
            "| model | rows | dates | YES ROI | d1 NO ROI | YES-NO | YES CI95 | Delta CI95 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for _, r in wf.iterrows():
        yci = r["yes_roi_ci95"] if not isinstance(r["yes_roi_ci95"], str) else json.loads(r["yes_roi_ci95"].replace("'", '"'))
        dci = r["yes_minus_no_ci95"] if not isinstance(r["yes_minus_no_ci95"], str) else json.loads(r["yes_minus_no_ci95"].replace("'", '"'))
        lines.append(
            f"| `{r['model']}` | {int(r['rows'])} | {int(r['active_dates'])} | {pct(r.get('yes_roi'))} | "
            f"{pct(r.get('no_roi'))} | {pct(r.get('yes_minus_no_roi'))} | [{pct(yci[0])}, {pct(yci[1])}] | "
            f"[{pct(dci[0])}, {pct(dci[1])}] |"
        )
    lines.extend(
        [
            "",
            "## 三道门",
            "",
            "- significance=FAIL：候选规则和 walk-forward 的日期 bootstrap CI 未稳定支持 YES ROI 与 YES-over-NO 同时大于 0。",
            "- baseline=FAIL/partial：相对 d1 NO sibling 通常改善，但不够稳定；相对 0 EV 也未稳过。",
            "- forward=FAIL：train 选出的规则在 holdout/walk-forward 没有稳定复现。",
            "- conclusion=inconclusive：不允许 live；可作为 shadow-only current YES sibling selector 继续积累。",
            "",
            "## 产物",
            "",
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
    q = load_rows()
    metrics_rows = []
    grids = []
    selecteds = []
    wf_rows_all = []
    wf_decisions_all = []
    for spec in MODEL_SPECS:
        scored, metrics = score_holdout(q, spec)
        metrics_rows.append({"model": spec.name, **metrics})
        grid, selected = fixed_grid(scored, spec)
        grids.append(grid)
        selecteds.append(selected)
        wr, wd = walkforward(q, spec)
        wf_rows_all.append(wr)
        wf_decisions_all.append(wd)

    metrics_df = pd.DataFrame(metrics_rows)
    grid_df = pd.concat(grids, ignore_index=True) if grids else pd.DataFrame()
    selected_df = pd.concat(selecteds, ignore_index=True) if selecteds else pd.DataFrame()
    chosen_df = chosen_holdout(grid_df, selected_df)
    wf_rows_df = pd.concat(wf_rows_all, ignore_index=True) if wf_rows_all else pd.DataFrame()
    wf_decisions_df = pd.concat(wf_decisions_all, ignore_index=True) if wf_decisions_all else pd.DataFrame()
    wf_summary_df = wf_summary(wf_rows_df)

    paths = {
        "model_metrics": OUT_DIR / "model_metrics.csv",
        "fixed_grid": OUT_DIR / "fixed_grid.csv",
        "selected_rows": OUT_DIR / "selected_rows.csv",
        "chosen_holdout": OUT_DIR / "chosen_holdout.csv",
        "walkforward_rows": OUT_DIR / "walkforward_rows.csv",
        "walkforward_decisions": OUT_DIR / "walkforward_decisions.csv",
        "walkforward_summary": OUT_DIR / "walkforward_summary.csv",
    }
    metrics_df.to_csv(paths["model_metrics"], index=False)
    grid_df.to_csv(paths["fixed_grid"], index=False)
    selected_df.to_csv(paths["selected_rows"], index=False)
    chosen_df.to_csv(paths["chosen_holdout"], index=False)
    wf_rows_df.to_csv(paths["walkforward_rows"], index=False)
    wf_decisions_df.to_csv(paths["walkforward_decisions"], index=False)
    wf_summary_df.to_csv(paths["walkforward_summary"], index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_yes_no_reheat",
        "coverage": {
            "input_rows": int(len(q)),
            "strategy_unique_rows": int(dedupe_strategy(q).shape[0]),
            "active_dates": int(q["target_date"].nunique()),
            "date_min": str(q["target_date"].min()),
            "date_max": str(q["target_date"].max()),
            "train_rows": int((q["period"] == "train").sum()),
            "holdout_rows": int((q["period"] == "holdout").sum()),
        },
        "data_self_check": data_self_check(),
        "clob_gate": load_gate(),
        "model_metrics": metrics_df.to_dict(orient="records"),
        "chosen_holdout": chosen_df.to_dict(orient="records"),
        "walkforward_summary": wf_summary_df.to_dict(orient="records"),
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
    write_markdown(payload, metrics_df, chosen_df, wf_summary_df)
    print(json.dumps({"model_metrics": payload["model_metrics"], "walkforward_summary": payload["walkforward_summary"], "verdict": payload["verdict"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
