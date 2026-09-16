#!/usr/bin/env python3
"""Pure theta-NO backtest for observed-max weather markets.

This deliberately keeps station-basis separate. The main universe is
`whitelist` / default-WU cities from the M3 v0 artifacts, where the settlement
source is aligned and the only hypothesis is: after the temperature has rolled
over, buy NO on higher brackets and collect residual uncertainty premium.
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


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"

TAIL_QUOTES = ROOT / "docs/analysis/2026-06/generated/m3_exhaustion_no_v0/exhaustion_tail_no_quotes.csv"
PHYSICAL = ROOT / "docs/analysis/2026-06/generated/m3_exhaustion_no_v0/exhaustion_physical_table.csv"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-15-theta-no-pure-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-15-theta-no-pure-v1.md"

SPLIT_DATE = "2026-06-01"
HOUR_WINDOWS = [(13, 17), (14, 17), (15, 17), (16, 17), (17, 17)]
DECLINE_THRESHOLDS = [0.5, 1.0, 1.5, 2.0, 2.5]
DISTANCE_MINS = [1, 2, 3]
ASK_MAXES = [0.75, 0.85, 0.90, 0.97]


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


def select_entries(df: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    work = df[mask].sort_values(["decision_hour_local", "snapshot_ts_utc", "city", "target_date", "bracket"]).copy()
    return work.drop_duplicates(subset=["city", "target_date", "bracket"], keep="first")


def t_stat(daily: pd.Series) -> float | None:
    if len(daily) <= 1:
        return None
    sd = float(daily.std(ddof=1))
    if sd <= 0:
        return None
    return float(daily.mean() / sd * math.sqrt(len(daily)))


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"rows": 0, "roi": None}
    cost = float(df["best_ask"].sum())
    pnl = float(df["pnl"].sum())
    daily = df.groupby("target_date")["pnl"].sum()
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
        "daily_t": t_stat(daily),
    }


def split_summary(df: pd.DataFrame) -> dict[str, Any]:
    return {
        "train": summarize(df[df["target_date"] < SPLIT_DATE]),
        "holdout": summarize(df[df["target_date"] >= SPLIT_DATE]),
    }


def daily_cost_pnl(df: pd.DataFrame, dates: list[str]) -> pd.DataFrame:
    return (
        df.assign(target_date=df["target_date"].astype(str))
        .groupby("target_date")
        .agg(cost=("best_ask", "sum"), pnl=("pnl", "sum"))
        .reindex(dates)
        .fillna(0.0)
    )


def roi(frame: pd.DataFrame, idx: np.ndarray | None = None) -> float:
    work = frame if idx is None else frame.iloc[idx]
    cost = float(work["cost"].sum())
    return float(work["pnl"].sum() / cost) if cost else float("nan")


def bootstrap_excess(selected: pd.DataFrame, baseline: pd.DataFrame, reps: int = 3000) -> dict[str, Any]:
    dates = sorted(set(selected["target_date"].astype(str)) | set(baseline["target_date"].astype(str)))
    if len(dates) < 3 or selected.empty or baseline.empty:
        return {"excess_roi": None, "ci95": [None, None], "reps": 0}
    s = daily_cost_pnl(selected, dates)
    b = daily_cost_pnl(baseline, dates)
    point = roi(s) - roi(b)
    rng = np.random.default_rng(20260615)
    sims: list[float] = []
    for _ in range(reps):
        idx = rng.integers(0, len(dates), len(dates))
        value = roi(s, idx) - roi(b, idx)
        if math.isfinite(value):
            sims.append(value)
    lo, hi = np.quantile(sims, [0.025, 0.975]) if sims else (float("nan"), float("nan"))
    return {"excess_roi": float(point), "ci95": [float(lo), float(hi)], "reps": len(sims)}


def point_excess(selected: pd.DataFrame, baseline: pd.DataFrame) -> dict[str, Any]:
    if selected.empty or baseline.empty:
        return {"excess_roi": None, "ci95": [None, None], "reps": 0}
    selected_cost = float(selected["best_ask"].sum())
    baseline_cost = float(baseline["best_ask"].sum())
    if selected_cost <= 0 or baseline_cost <= 0:
        return {"excess_roi": None, "ci95": [None, None], "reps": 0}
    selected_roi = float(selected["pnl"].sum() / selected_cost)
    baseline_roi = float(baseline["pnl"].sum() / baseline_cost)
    return {"excess_roi": selected_roi - baseline_roi, "ci95": [None, None], "reps": 0}


def physical_summary() -> list[dict[str, Any]]:
    phys = pd.read_csv(PHYSICAL)
    focus = phys[phys["decision_hour_local"].between(13, 17)].copy()
    out = []
    for (group, bucket), g in focus.groupby(["group", "decline_bucket"]):
        n = float(g["n"].sum())
        out.append(
            {
                "group": group,
                "decline_bucket": bucket,
                "n": int(n),
                "p_jump_ge1": float((g["p_jump_ge1"] * g["n"]).sum() / n),
                "p_jump_ge2": float((g["p_jump_ge2"] * g["n"]).sum() / n),
            }
        )
    return out


def rule_label(start: int, end: int, decline: float, distance: int, ask_max: float) -> str:
    return f"h{start}_{end}_decline{decline:g}_d{distance}_ask{ask_max:g}"


def run_grid(df: pd.DataFrame, group_name: str) -> list[dict[str, Any]]:
    universe = df[df["group"].eq(group_name)].copy()
    results = []
    for start, end in HOUR_WINDOWS:
        hour_mask = universe["decision_hour_local"].between(start, end)
        for distance in DISTANCE_MINS:
            distance_mask = universe["distance"].ge(distance)
            for ask_max in ASK_MAXES:
                ask_mask = universe["best_ask"].le(ask_max)
                baseline = select_entries(universe, hour_mask & distance_mask & ask_mask)
                for decline in DECLINE_THRESHOLDS:
                    selected = select_entries(universe, hour_mask & distance_mask & ask_mask & universe["decline"].ge(decline))
                    if selected.empty:
                        continue
                    train_selected = selected[selected["target_date"] < SPLIT_DATE]
                    train_baseline = baseline[baseline["target_date"] < SPLIT_DATE]
                    hold_selected = selected[selected["target_date"] >= SPLIT_DATE]
                    hold_baseline = baseline[baseline["target_date"] >= SPLIT_DATE]
                    results.append(
                        {
                            "group": group_name,
                            "rule": rule_label(start, end, decline, distance, ask_max),
                            "hour_start": start,
                            "hour_end": end,
                            "decline_min": decline,
                            "distance_min": distance,
                            "ask_max": ask_max,
                            "full": summarize(selected),
                            "baseline_full": summarize(baseline),
                            "split": split_summary(selected),
                            "baseline_split": split_summary(baseline),
                            "excess_full": point_excess(selected, baseline),
                            "excess_train": point_excess(train_selected, train_baseline),
                            "excess_holdout": point_excess(hold_selected, hold_baseline),
                        }
                    )
    return results


def materialize_rule(df: pd.DataFrame, row: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    universe = df[df["group"].eq(row["group"])].copy()
    hour_mask = universe["decision_hour_local"].between(row["hour_start"], row["hour_end"])
    distance_mask = universe["distance"].ge(row["distance_min"])
    ask_mask = universe["best_ask"].le(row["ask_max"])
    baseline = select_entries(universe, hour_mask & distance_mask & ask_mask)
    selected = select_entries(universe, hour_mask & distance_mask & ask_mask & universe["decline"].ge(row["decline_min"]))
    return selected, baseline


def add_bootstrap_to_rows(df: pd.DataFrame, rows_: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    for row in rows_:
        selected, baseline = materialize_rule(df, row)
        train_selected = selected[selected["target_date"] < SPLIT_DATE]
        train_baseline = baseline[baseline["target_date"] < SPLIT_DATE]
        hold_selected = selected[selected["target_date"] >= SPLIT_DATE]
        hold_baseline = baseline[baseline["target_date"] >= SPLIT_DATE]
        item = dict(row)
        item["excess_full"] = bootstrap_excess(selected, baseline, reps=1500)
        item["excess_train"] = bootstrap_excess(train_selected, train_baseline, reps=1500)
        item["excess_holdout"] = bootstrap_excess(hold_selected, hold_baseline, reps=1500)
        enriched.append(item)
    return enriched


def viable_train(row: dict[str, Any], min_rows: int = 30, min_active_dates: int = 8) -> bool:
    train = row["split"]["train"]
    return (
        train.get("rows", 0) >= min_rows
        and train.get("active_dates", 0) >= min_active_dates
        and row["excess_train"].get("excess_roi") is not None
    )


def top_train_rules(rows_: list[dict[str, Any]], n: int = 10, min_rows: int = 30, min_active_dates: int = 8) -> list[dict[str, Any]]:
    candidates = [r for r in rows_ if viable_train(r, min_rows=min_rows, min_active_dates=min_active_dates)]
    candidates.sort(key=lambda r: (r["excess_train"]["excess_roi"], r["split"]["train"].get("roi") or -999), reverse=True)
    return candidates[:n]


def compact_rule(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule": row["rule"],
        "train_rows": row["split"]["train"].get("rows"),
        "train_active_dates": row["split"]["train"].get("active_dates"),
        "train_roi": row["split"]["train"].get("roi"),
        "train_excess_roi": row["excess_train"].get("excess_roi"),
        "train_excess_ci95": row["excess_train"].get("ci95"),
        "holdout_rows": row["split"]["holdout"].get("rows"),
        "holdout_active_dates": row["split"]["holdout"].get("active_dates"),
        "holdout_roi": row["split"]["holdout"].get("roi"),
        "holdout_excess_roi": row["excess_holdout"].get("excess_roi"),
        "holdout_excess_ci95": row["excess_holdout"].get("ci95"),
        "full_roi": row["full"].get("roi"),
        "full_rows": row["full"].get("rows"),
    }


def rule_table(rows_: list[dict[str, Any]]) -> str:
    header = "| rule | train rows | train ROI | train excess CI | holdout rows | holdout ROI | holdout excess CI |\n"
    sep = "|---|---:|---:|---|---:|---:|---|\n"
    lines = [header, sep]
    for row in rows_:
        ci_t = row["excess_train"].get("ci95")
        ci_h = row["excess_holdout"].get("ci95")
        ci_ts = "NA" if ci_t[0] is None else f"{pct(ci_t[0])}..{pct(ci_t[1])}"
        ci_hs = "NA" if ci_h[0] is None else f"{pct(ci_h[0])}..{pct(ci_h[1])}"
        lines.append(
            f"| `{row['rule']}` | {row['split']['train'].get('rows', 0)} | {pct(row['split']['train'].get('roi'))} | "
            f"{pct(row['excess_train'].get('excess_roi'))} [{ci_ts}] | "
            f"{row['split']['holdout'].get('rows', 0)} | {pct(row['split']['holdout'].get('roi'))} | "
            f"{pct(row['excess_holdout'].get('excess_roi'))} [{ci_hs}] |\n"
        )
    return "".join(lines)


def main() -> None:
    if not TAIL_QUOTES.exists() or not PHYSICAL.exists():
        raise SystemExit("Missing M3 v0 artifacts; run research_m3_exhaustion_no.py first.")
    tail = pd.read_csv(TAIL_QUOTES)
    tail["target_date"] = tail["target_date"].astype(str)

    default_rows = run_grid(tail, "whitelist")
    basis_rows = run_grid(tail, "repaired")
    default_top = add_bootstrap_to_rows(tail, top_train_rules(default_rows))
    basis_top = add_bootstrap_to_rows(tail, top_train_rules(basis_rows, n=5, min_rows=10, min_active_dates=5))

    physical = physical_summary()
    physical_by_key = {(r["group"], r["decline_bucket"]): r for r in physical}
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "pure_theta_no_alpha",
        "row_grain": "selected strategy row = first qualifying orderbook quote per city + target_date + bracket",
        "main_universe": "whitelist/default-WU source-aligned cities; station-basis repaired cities are separate contrast only",
        "split_date": SPLIT_DATE,
        "self_check": data_self_check(),
        "clob_gate": load_gate(),
        "funnel": {
            "tail_quote_rows": int(len(tail)),
            "tail_quote_groups": tail["group"].value_counts().to_dict(),
            "date_min": str(tail["target_date"].min()),
            "date_max": str(tail["target_date"].max()),
            "default_wu_grid_rules": len(default_rows),
            "basis_contrast_grid_rules": len(basis_rows),
        },
        "physical_h13_17": physical,
        "default_wu_top_train_rules": [compact_rule(r) for r in default_top],
        "basis_contrast_top_train_rules": [compact_rule(r) for r in basis_top],
        "all_default_wu_grid": default_rows,
        "verdict": {
            "significance": "FAIL",
            "baseline": "FAIL",
            "forward": "FAIL",
            "conclusion": "inconclusive",
            "recommended_action": "keep pure theta-NO as a separate research track, but do not shadow/paper/live it from this backtest",
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    wh_fresh = physical_by_key[("whitelist", "<0.5")]
    wh_exh2 = physical_by_key[("whitelist", ">=2.0")]
    best = default_top[0] if default_top else None
    best_sentence = "No viable train rule met the minimum row/date screen."
    if best:
        best_sentence = (
            f"The best train-selected default-WU theta rule was `{best['rule']}`: "
            f"train ROI {pct(best['split']['train'].get('roi'))}, train excess {pct(best['excess_train'].get('excess_roi'))}, "
            f"holdout ROI {pct(best['split']['holdout'].get('roi'))}, holdout excess {pct(best['excess_holdout'].get('excess_roi'))}."
        )

    md = f"""# Pure Theta-NO Backtest v1

Status: snapshot
Updated: 2026-06-15
Source of truth: no
Used by: WEATHER_DOCS_INDEX.md

## 数据快照

- 数据源: `runtime/weather.db` self-check + M3 observed/orderbook artifacts from `docs/analysis/2026-06/generated/m3_exhaustion_no_v0/`.
- DB fact built at: `{payload['self_check']['fact_trades_max_built_at_utc']}`.
- CLOB gate: `gate_pass={payload['clob_gate'].get('gate_pass')}`, `missing_order_rows={payload['clob_gate'].get('missing_order_rows')}`, `over_order_keys={payload['clob_gate'].get('over_order_keys')}`.
- `fact_trades` by class: `{payload['self_check']['fact_trades_by_class']}`.
- `fact_trades` by settlement: `{payload['self_check']['fact_trades_by_settlement_status']}`.
- `fact_signal_candidates`: `{payload['self_check']['fact_signal_candidate_coverage']}`.
- CLOB order/fill join: `{payload['self_check']['clob_order_fill_join']}`.
- Note: last full `run_stack.sh` rebuilt DB/facts/gate, then failed only at frontend port startup; this report does not depend on the frontend.

## Target Metric

`pure_theta_no_alpha` = in source-aligned/default-WU cities only, does a decision-time no-reheat signal create positive taker EV in above-running-max BUY_NO quotes?

Main denominator: default-WU/whitelist cities only. Station-basis repaired cities are a separate contrast table, not part of the theta conclusion.

Row grain: first qualifying orderbook quote per `city + target_date + bracket`; this is opportunity research, not live fills.

## Funnel

- Raw tail-NO quote rows: `{payload['funnel']['tail_quote_rows']}` from `{payload['funnel']['date_min']}` to `{payload['funnel']['date_max']}`.
- Groups: `{payload['funnel']['tail_quote_groups']}`.
- Default-WU grid rules tested: `{payload['funnel']['default_wu_grid_rules']}`.
- Train/holdout split: `< {SPLIT_DATE}` vs `>= {SPLIT_DATE}`.

## Physical Layer

The physics is strong: in default-WU cities, 13-17h P(jump>=1) drops from {pct(wh_fresh['p_jump_ge1'], signed=False)} when decline is `<0.5C` to {pct(wh_exh2['p_jump_ge1'], signed=False)} when decline is `>=2C`.

## Default-WU Theta Grid

{rule_table(default_top)}

{best_sentence}

## Basis Contrast

This is not part of the pure theta conclusion, but confirms why the two tracks must stay separate:

{rule_table(basis_top)}

## Verdict

significance=FAIL
baseline=FAIL
forward=FAIL
conclusion=inconclusive

一句话: 在 `2026-05-20..2026-06-09`，纯 theta-NO 在 default-WU/source-aligned 城市里没有通过 train/holdout 的正超额检验；物理信号是真的，但 taker 价格大体已经包含它。这个方向可以继续作为独立研究线改模型，但当前回测不支持 shadow/paper/live。
"""
    OUT_MD.write_text(md)
    print(json.dumps({"json": str(OUT_JSON), "md": str(OUT_MD), "top_default_rule": compact_rule(default_top[0]) if default_top else None}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
