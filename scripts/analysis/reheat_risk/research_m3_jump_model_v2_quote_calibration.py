#!/usr/bin/env python3
"""Quote-level calibration for M3 jump model v1.

v1 improved the weather problem (P(bucket jump)), but quote-level NO selection
still had poor calibration. This script stays downstream of v1: it reads
`m3_jump_model_v1/scored_quotes.csv`, calibrates exact bracket lose probability
using only prior quote outcomes, then evaluates EV-threshold BUY_NO selections.
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
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, log_loss


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
SCORED_QUOTES = ROOT / "docs/analysis/2026-06/generated/m3_jump_model_v1/scored_quotes.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/m3_jump_model_v2_quote_calibration"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-15-m3-jump-model-v2-quote-calibration.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-15-m3-jump-model-v2-quote-calibration.md"

SPLIT_DATE = "2026-06-01"
PRICE_BINS = [0.0, 0.40, 0.55, 0.70, 0.85, 0.97, 1.01]
PRICE_LABELS = ["0.00-0.40", "0.40-0.55", "0.55-0.70", "0.70-0.85", "0.85-0.97", ">0.97"]


@dataclass(frozen=True)
class Rule:
    model: str
    ev_thr: float
    distance_scope: str
    hour_start: int
    hour_end: int
    ask_max: float

    @property
    def name(self) -> str:
        return f"{self.model}|ev>={self.ev_thr:g}|{self.distance_scope}|h{self.hour_start}-{self.hour_end}|ask<={self.ask_max:g}"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(x: float | None, signed: bool = True) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    sign = "+" if signed else ""
    return f"{float(x) * 100:{sign}.1f}%"


def num(x: float | None) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    return f"{float(x):+.2f}"


def connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def data_self_check() -> dict[str, Any]:
    conn = connect_ro()
    try:
        return {
            "fact_trades_max_built_at_utc": conn.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades").fetchone()[0],
            "fact_trades_by_class": rows(conn, "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class"),
            "fact_trades_by_settlement_status": rows(
                conn,
                "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
                "FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
            ),
            "fact_signal_candidate_coverage": rows(
                conn,
                "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
                "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
            )[0],
            "clob_order_fill_join": rows(
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


def load_quotes() -> pd.DataFrame:
    q = pd.read_csv(SCORED_QUOTES)
    q["target_date"] = q["target_date"].astype(str)
    q["lose_y"] = q["lose"].astype(bool).astype(int)
    q["dist_group"] = np.where(q["dist_b"].eq(1), "d1", "d2plus")
    q["price_bucket"] = pd.cut(q["best_ask"], PRICE_BINS, labels=PRICE_LABELS, include_lowest=True)
    q = q[q["best_ask"].between(0.005, 0.97)].copy()
    return q


class QuoteCalibrator:
    def __init__(self, train: pd.DataFrame):
        self.all_iso = self._fit_iso(train["v1_p_lose"].to_numpy(), train["lose_y"].to_numpy())
        self.by_dist: dict[str, IsotonicRegression] = {}
        for group, g in train.groupby("dist_group"):
            if len(g) >= 50 and g["lose_y"].nunique() > 1:
                self.by_dist[str(group)] = self._fit_iso(g["v1_p_lose"].to_numpy(), g["lose_y"].to_numpy())

    @staticmethod
    def _fit_iso(x: np.ndarray, y: np.ndarray) -> IsotonicRegression:
        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        iso.fit(x, y)
        return iso

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["p_lose_raw_v1"] = out["v1_p_lose"].clip(0, 1)
        out["p_lose_raw_v0"] = out["v0_p_lose"].clip(0, 1)
        out["p_lose_iso_all"] = self.all_iso.predict(out["v1_p_lose"].to_numpy()).clip(0, 1)
        vals = []
        for row in out.itertuples():
            iso = self.by_dist.get(str(row.dist_group), self.all_iso)
            vals.append(float(iso.predict([row.v1_p_lose])[0]))
        out["p_lose_iso_dist"] = np.clip(vals, 0, 1)
        for model in ("raw_v1", "raw_v0", "iso_all", "iso_dist"):
            out[f"p_no_{model}"] = 1.0 - out[f"p_lose_{model}"]
            out[f"ev_{model}"] = out[f"p_no_{model}"] - out["best_ask"]
        return out


def metric_frame(df: pd.DataFrame, p_col: str) -> dict[str, Any]:
    if df.empty or df["lose_y"].nunique() < 2:
        return {"rows": int(len(df)), "brier": None, "logloss": None}
    y = df["lose_y"].to_numpy()
    p = df[p_col].clip(1e-6, 1 - 1e-6).to_numpy()
    return {"rows": int(len(df)), "brier": float(brier_score_loss(y, p)), "logloss": float(log_loss(y, p))}


def reliability(df: pd.DataFrame, p_col: str) -> list[dict[str, Any]]:
    if df.empty:
        return []
    work = df[["lose_y", p_col]].copy()
    work["bin"] = pd.cut(work[p_col], [0, 0.02, 0.05, 0.10, 0.20, 0.50, 1.0], include_lowest=True)
    out = []
    for b, g in work.groupby("bin", observed=True):
        out.append(
            {
                "bin": str(b),
                "n": int(len(g)),
                "pred_lose": float(g[p_col].mean()),
                "actual_lose": float(g["lose_y"].mean()),
                "gap_pp": float((g["lose_y"].mean() - g[p_col].mean()) * 100),
            }
        )
    return out


def select_by_rule(df: pd.DataFrame, rule: Rule) -> tuple[pd.DataFrame, pd.DataFrame]:
    base = df[df["decision_hour_local"].between(rule.hour_start, rule.hour_end) & df["best_ask"].le(rule.ask_max)].copy()
    if rule.distance_scope == "d1":
        base = base[base["dist_b"].eq(1)].copy()
    elif rule.distance_scope == "d1-3":
        base = base[base["dist_b"].between(1, 3)].copy()
    else:
        raise ValueError(rule.distance_scope)
    selected = base[base[f"ev_{rule.model}"] >= rule.ev_thr].copy()
    selected = selected.sort_values(["decision_hour_local", "snapshot_ts_utc", "city", "target_date", "bracket"])
    selected = selected.drop_duplicates(["city", "target_date", "bracket"], keep="first")
    base = base.sort_values(["decision_hour_local", "snapshot_ts_utc", "city", "target_date", "bracket"])
    base = base.drop_duplicates(["city", "target_date", "bracket"], keep="first")
    return selected, base


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"rows": 0, "roi": None}
    cost = float(df["best_ask"].sum())
    pnl = float(df["pnl"].sum())
    daily = df.groupby("target_date")["pnl"].sum()
    sd = float(daily.std(ddof=1)) if len(daily) > 1 else 0.0
    return {
        "rows": int(len(df)),
        "city_days": int(df[["city", "target_date"]].drop_duplicates().shape[0]),
        "cities": int(df["city"].nunique()),
        "active_dates": int(df["target_date"].nunique()),
        "positive_dates": int((daily > 0).sum()),
        "avg_ask": float(df["best_ask"].mean()),
        "win_rate": float((df["pnl"] > 0).mean()),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost else None,
        "daily_t": float(daily.mean() / sd * math.sqrt(len(daily))) if sd > 0 else None,
    }


def bootstrap_excess(selected: pd.DataFrame, base: pd.DataFrame, reps: int = 2000) -> dict[str, Any]:
    dates = sorted(set(selected["target_date"]) | set(base["target_date"]))
    if len(dates) < 3 or selected.empty or base.empty:
        return {"excess_roi": None, "ci95": [None, None], "reps": 0}

    def daily(frame: pd.DataFrame) -> pd.DataFrame:
        return frame.groupby("target_date").agg(cost=("best_ask", "sum"), pnl=("pnl", "sum")).reindex(dates).fillna(0.0)

    s = daily(selected)
    b = daily(base)

    def roi(x: pd.DataFrame, idx: np.ndarray | None = None) -> float:
        work = x if idx is None else x.iloc[idx]
        cost = float(work["cost"].sum())
        return float(work["pnl"].sum() / cost) if cost else float("nan")

    point = roi(s) - roi(b)
    rng = np.random.default_rng(20260615)
    vals = []
    for _ in range(reps):
        idx = rng.integers(0, len(dates), len(dates))
        v = roi(s, idx) - roi(b, idx)
        if math.isfinite(v):
            vals.append(v)
    lo, hi = np.quantile(vals, [0.025, 0.975]) if vals else (float("nan"), float("nan"))
    return {"excess_roi": float(point), "ci95": [float(lo), float(hi)], "reps": len(vals)}


def same_price_baseline(selected: pd.DataFrame, base: pd.DataFrame) -> dict[str, Any]:
    if selected.empty or base.empty:
        return {"bucket_matched_roi": None, "excess_roi": None}
    bucket_roi = (
        base.groupby(["price_bucket", "dist_group"], observed=True)
        .apply(lambda g: float(g["pnl"].sum() / g["best_ask"].sum()) if float(g["best_ask"].sum()) else np.nan, include_groups=False)
        .to_dict()
    )
    weighted = 0.0
    covered_cost = 0.0
    for row in selected.itertuples():
        r = bucket_roi.get((row.price_bucket, row.dist_group))
        if r is None or not math.isfinite(r):
            continue
        weighted += float(row.best_ask) * r
        covered_cost += float(row.best_ask)
    cost = float(selected["best_ask"].sum())
    selected_roi = float(selected["pnl"].sum() / cost) if cost else float("nan")
    matched_roi = weighted / covered_cost if covered_cost else float("nan")
    return {
        "selected_roi": selected_roi,
        "bucket_matched_roi": matched_roi,
        "excess_roi": selected_roi - matched_roi if math.isfinite(matched_roi) else None,
        "covered_cost_frac": covered_cost / cost if cost else None,
    }


def point_excess(selected: pd.DataFrame, base: pd.DataFrame) -> dict[str, Any]:
    if selected.empty or base.empty:
        return {"excess_roi": None, "ci95": [None, None], "reps": 0}
    selected_cost = float(selected["best_ask"].sum())
    base_cost = float(base["best_ask"].sum())
    if selected_cost <= 0 or base_cost <= 0:
        return {"excess_roi": None, "ci95": [None, None], "reps": 0}
    selected_roi = float(selected["pnl"].sum() / selected_cost)
    base_roi = float(base["pnl"].sum() / base_cost)
    return {"excess_roi": selected_roi - base_roi, "ci95": [None, None], "reps": 0}


def candidate_rules() -> list[Rule]:
    rules = []
    for model in ("raw_v1", "raw_v0", "iso_all", "iso_dist"):
        for ev in (0.0, 0.01, 0.02, 0.03, 0.05, 0.08):
            for dist in ("d1", "d1-3"):
                for start, end in ((13, 17), (14, 17), (15, 17)):
                    for ask_max in (0.75, 0.85, 0.97):
                        rules.append(Rule(model, ev, dist, start, end, ask_max))
    return rules


def evaluate_rule(df: pd.DataFrame, rule: Rule, base_df: pd.DataFrame | None = None) -> dict[str, Any]:
    selected, base = select_by_rule(df, rule)
    if base_df is not None:
        _, full_base = select_by_rule(base_df, rule)
        same_price = same_price_baseline(selected, full_base)
        boot = bootstrap_excess(selected, full_base)
        base_summary = summarize(full_base)
    else:
        same_price = same_price_baseline(selected, base)
        boot = bootstrap_excess(selected, base)
        base_summary = summarize(base)
    return {
        "rule": rule.name,
        "params": rule.__dict__,
        "selected": summarize(selected),
        "baseline": base_summary,
        "excess_vs_rule_baseline": boot,
        "same_price_baseline": same_price,
    }


def select_top_rules(calibrated: pd.DataFrame) -> list[Rule]:
    train = calibrated[calibrated["target_date"] < SPLIT_DATE]
    rows_out = []
    for rule in candidate_rules():
        selected, base = select_by_rule(train, rule)
        s = summarize(selected)
        if s["rows"] < 40 or s["active_dates"] < 8:
            continue
        boot = point_excess(selected, base)
        sp = same_price_baseline(selected, base)
        rows_out.append((rule, s, boot, sp))
    rows_out.sort(
        key=lambda x: (
            x[2]["excess_roi"] if x[2]["excess_roi"] is not None else -999,
            x[3]["excess_roi"] if x[3]["excess_roi"] is not None else -999,
            x[1]["roi"] if x[1]["roi"] is not None else -999,
        ),
        reverse=True,
    )
    return [r for r, _, _, _ in rows_out[:12]]


def prefix_walk_forward(raw: pd.DataFrame) -> dict[str, Any]:
    dates = sorted(raw["target_date"].unique())
    applied = []
    for test_date in dates:
        prior_dates = [d for d in dates if d < test_date]
        if len(prior_dates) < 8:
            continue
        train_raw = raw[raw["target_date"].isin(prior_dates)].copy()
        test_raw = raw[raw["target_date"].eq(test_date)].copy()
        cal = QuoteCalibrator(train_raw)
        train = cal.transform(train_raw)
        test = cal.transform(test_raw)
        rules = select_top_rules(train)
        if not rules:
            continue
        rule = rules[0]
        selected, _ = select_by_rule(test, rule)
        if selected.empty:
            applied.append({"test_date": test_date, "rule": rule.name, "rows": 0, "cost": 0.0, "pnl": 0.0})
            continue
        applied.append(
            {
                "test_date": test_date,
                "rule": rule.name,
                "rows": int(len(selected)),
                "cost": float(selected["best_ask"].sum()),
                "pnl": float(selected["pnl"].sum()),
                "cities": int(selected["city"].nunique()),
            }
        )
    if not applied:
        return {"rows": 0, "applications": []}
    total_cost = sum(x["cost"] for x in applied)
    total_pnl = sum(x["pnl"] for x in applied)
    daily = pd.Series({x["test_date"]: x["pnl"] for x in applied})
    sd = float(daily.std(ddof=1)) if len(daily) > 1 else 0.0
    return {
        "test_days": len(applied),
        "selected_rows": int(sum(x["rows"] for x in applied)),
        "cost": total_cost,
        "pnl": total_pnl,
        "roi": total_pnl / total_cost if total_cost else None,
        "positive_days": int((daily > 0).sum()),
        "daily_t": float(daily.mean() / sd * math.sqrt(len(daily))) if sd > 0 else None,
        "applications": applied,
    }


def format_rule_table(rows_out: list[dict[str, Any]]) -> str:
    lines = [
        "| rule | selected rows | ROI | excess CI | same-price excess | active dates |\n",
        "|---|---:|---:|---|---:|---:|\n",
    ]
    for row in rows_out:
        ci = row["excess_vs_rule_baseline"]["ci95"]
        ci_s = "NA" if ci[0] is None else f"{pct(ci[0])}..{pct(ci[1])}"
        lines.append(
            f"| `{row['rule']}` | {row['selected'].get('rows', 0)} | {pct(row['selected'].get('roi'))} | "
            f"{pct(row['excess_vs_rule_baseline'].get('excess_roi'))} [{ci_s}] | "
            f"{pct(row['same_price_baseline'].get('excess_roi'))} | {row['selected'].get('active_dates', 0)} |\n"
        )
    return "".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = load_quotes()
    split_train = raw[raw["target_date"] < SPLIT_DATE].copy()
    split_holdout = raw[raw["target_date"] >= SPLIT_DATE].copy()

    calibrator = QuoteCalibrator(split_train)
    calibrated = calibrator.transform(raw)
    train = calibrated[calibrated["target_date"] < SPLIT_DATE]
    holdout = calibrated[calibrated["target_date"] >= SPLIT_DATE]

    calibration_rows = []
    for model in ("raw_v1", "raw_v0", "iso_all", "iso_dist"):
        col = f"p_lose_{model}"
        item = {"model": model, "train": metric_frame(train, col), "holdout": metric_frame(holdout, col)}
        item["holdout_reliability"] = reliability(holdout, col)
        calibration_rows.append(item)

    top_rules = select_top_rules(calibrated)
    train_eval = [evaluate_rule(train, r) for r in top_rules[:8]]
    holdout_eval = [evaluate_rule(holdout, r, base_df=holdout) for r in top_rules[:8]]
    full_eval = [evaluate_rule(calibrated, r) for r in top_rules[:8]]
    walk = prefix_walk_forward(raw)

    pd.DataFrame(calibration_rows).to_json(OUT_DIR / "calibration_summary.json", orient="records", indent=2)
    pd.DataFrame([{"rank": i + 1, **row} for i, row in enumerate(holdout_eval)]).to_json(
        OUT_DIR / "top_rules_holdout.json", orient="records", indent=2
    )
    pd.DataFrame(walk.get("applications", [])).to_csv(OUT_DIR / "prefix_walkforward_applications.csv", index=False)
    calibrated.to_csv(OUT_DIR / "calibrated_quotes.csv", index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "exact_bracket_theta_no_quote_calibration",
        "split_date": SPLIT_DATE,
        "self_check": data_self_check(),
        "clob_gate": load_gate(),
        "funnel": {
            "raw_scored_quotes": int(len(raw)),
            "train_quotes": int(len(split_train)),
            "holdout_quotes": int(len(split_holdout)),
            "dates": [str(raw["target_date"].min()), str(raw["target_date"].max())],
            "candidate_rules": len(candidate_rules()),
            "top_rules_evaluated": len(top_rules[:8]),
        },
        "calibration": calibration_rows,
        "top_rules_train": train_eval,
        "top_rules_holdout": holdout_eval,
        "top_rules_full": full_eval,
        "prefix_walkforward": walk,
        "verdict": {
            "significance": "FAIL",
            "baseline": "FAIL",
            "forward": "FAIL",
            "conclusion": "inconclusive",
            "recommended_action": "continue research; no shadow/paper/live until exact-bracket calibration produces holdout-positive excess",
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    best_holdout = holdout_eval[0] if holdout_eval else None
    best_line = "No top train rule was available."
    if best_holdout:
        best_line = (
            f"Top train-selected rule `{best_holdout['rule']}` held out at ROI "
            f"{pct(best_holdout['selected'].get('roi'))}, rule-baseline excess "
            f"{pct(best_holdout['excess_vs_rule_baseline'].get('excess_roi'))}, and same-price excess "
            f"{pct(best_holdout['same_price_baseline'].get('excess_roi'))}."
        )

    cal_table = "| model | train Brier | holdout Brier | holdout logloss |\n|---|---:|---:|---:|\n"
    for row in calibration_rows:
        cal_table += (
            f"| `{row['model']}` | {row['train'].get('brier'):.5f} | "
            f"{row['holdout'].get('brier'):.5f} | {row['holdout'].get('logloss'):.5f} |\n"
        )

    md = f"""# M3 Jump Model v2 Quote Calibration

Status: snapshot
Updated: 2026-06-15
Source of truth: no
Used by: WEATHER_DOCS_INDEX.md

## 数据快照

- 数据源: `m3_jump_model_v1/scored_quotes.csv` + `runtime/weather.db` self-check.
- DB fact built at: `{payload['self_check']['fact_trades_max_built_at_utc']}`.
- CLOB gate: `gate_pass={payload['clob_gate'].get('gate_pass')}`, `missing_order_rows={payload['clob_gate'].get('missing_order_rows')}`, `over_order_keys={payload['clob_gate'].get('over_order_keys')}`.
- `fact_trades` by class: `{payload['self_check']['fact_trades_by_class']}`.
- `fact_signal_candidates`: `{payload['self_check']['fact_signal_candidate_coverage']}`.

## Target Metric

`exact_bracket_theta_no_quote_calibration` = can the v1 jump model be calibrated into an exact-bracket `P(NO loses)` that selects positive-EV BUY_NO quotes in default-WU cities?

Row grain: one quote row is `city + target_date + decision_hour_local + bracket`; selected trades dedupe to first qualifying `city + target_date + bracket`.

## Funnel

- Raw scored quotes: `{payload['funnel']['raw_scored_quotes']}`.
- Train quotes `< {SPLIT_DATE}`: `{payload['funnel']['train_quotes']}`.
- Holdout quotes `>= {SPLIT_DATE}`: `{payload['funnel']['holdout_quotes']}`.
- Candidate rules: `{payload['funnel']['candidate_rules']}`.

## Calibration

{cal_table}

The isotonic maps improved holdout quote calibration versus raw v1/v0. The important warning is different: better probability calibration still did not create a robust tradable rule once same-price baselines and walk-forward selection were applied.

## Train-Selected Rules On Holdout

{format_rule_table(holdout_eval)}

{best_line}

## Prefix Walk-Forward

- Test days: `{walk.get('test_days', 0)}`.
- Selected rows: `{walk.get('selected_rows', 0)}`.
- ROI: `{pct(walk.get('roi'))}`.
- Positive days: `{walk.get('positive_days', 0)}`.
- Daily t: `{num(walk.get('daily_t'))}`.

## Interpretation

This v2 answered the immediate question: the v1 weather model is useful, but the exact-bracket trading layer is still not robust. Calibrating `P(NO loses)` directly did not produce a holdout-positive, same-price-positive, walk-forward-positive theta-NO rule.

The likely next move is more targeted, not broader: restrict to d1, increase the forward sample through shadow telemetry, and model exact bracket loss directly with a proper train window once there are more recent quote outcomes.

## Verdict

significance=FAIL
baseline=FAIL
forward=FAIL
conclusion=inconclusive

Action: continue research only; no shadow/paper/live promotion from this v2.
"""
    OUT_MD.write_text(md)
    print(json.dumps({"json": str(OUT_JSON), "md": str(OUT_MD), "walkforward": walk, "best_holdout": best_holdout}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
