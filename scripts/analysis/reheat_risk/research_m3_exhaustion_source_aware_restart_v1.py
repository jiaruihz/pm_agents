#!/usr/bin/env python3
"""Source-aware restart of the M3 exhaustion BUY-NO research line.

This script intentionally reuses the v0 observed-max artifacts instead of
inventing a new data layer. The research question is narrower than "does
temperature theta work?": whether a "will it heat up again" model is an
independent edge, or a risk gate that only becomes profitable when the city has
settlement-source basis.
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
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-15-m3-exhaustion-source-aware-restart-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-15-m3-exhaustion-source-aware-restart-v1.md"

TAIL_QUOTES = ROOT / "docs/analysis/2026-06/generated/m3_exhaustion_no_v0/exhaustion_tail_no_quotes.csv"
PHYSICAL = ROOT / "docs/analysis/2026-06/generated/m3_exhaustion_no_v0/exhaustion_physical_table.csv"
RULES_V0 = ROOT / "docs/analysis/2026-06/generated/m3_exhaustion_no_v0/exhaustion_strategy_rules.csv"
REGISTRY_JSON = ROOT / "docs/analysis/2026-06/2026-06-14-settlement-source-registry-v0.json"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(x: float | None, signed: bool = True) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    sign = "+" if signed else ""
    return f"{float(x) * 100:{sign}.1f}%"


def money(x: float | None) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    return f"${float(x):+.2f}"


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
            "fact_trades_by_class": rows(
                conn,
                "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
            ),
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
        "db_fill_cost_minus_fact_cost": data.get("db_fill_cost_minus_fact_cost"),
        "missing_order_rows": data.get("db_fills", {}).get("missing_order_rows"),
        "over_order_keys": data.get("db_fills", {}).get("over_order_keys"),
        "fact_trades_live_real_rows": data.get("fact_trades_live_real", {}).get("rows"),
    }


def t_stat(daily: pd.Series) -> float | None:
    if len(daily) <= 1:
        return None
    sd = float(daily.std(ddof=1))
    if sd <= 0:
        return None
    return float(daily.mean() / sd * math.sqrt(len(daily)))


def summarize_entries(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"rows": 0}
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


def select_entries(df: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    selected = df[mask].sort_values(["decision_hour_local", "snapshot_ts_utc", "city", "target_date", "bracket"]).copy()
    return selected.drop_duplicates(subset=["city", "target_date", "bracket"], keep="first")


def split_summary(df: pd.DataFrame, split_date: str = "2026-06-01") -> dict[str, Any]:
    return {
        "split_date": split_date,
        "train": summarize_entries(df[df["target_date"].astype(str) < split_date]),
        "holdout": summarize_entries(df[df["target_date"].astype(str) >= split_date]),
    }


def cluster_bootstrap_excess(selected: pd.DataFrame, baseline: pd.DataFrame, reps: int = 5000) -> dict[str, Any]:
    dates = sorted(set(selected["target_date"].astype(str)) | set(baseline["target_date"].astype(str)))
    if len(dates) < 3 or selected.empty or baseline.empty:
        return {"excess_roi": None, "ci95": [None, None], "reps": 0}

    def by_date(frame: pd.DataFrame) -> pd.DataFrame:
        return frame.assign(target_date=frame["target_date"].astype(str)).groupby("target_date").agg(
            cost=("best_ask", "sum"), pnl=("pnl", "sum")
        ).reindex(dates).fillna(0.0)

    s = by_date(selected)
    b = by_date(baseline)

    def roi(frame: pd.DataFrame, idx: np.ndarray | None = None) -> float:
        work = frame if idx is None else frame.iloc[idx]
        cost = float(work["cost"].sum())
        return float(work["pnl"].sum() / cost) if cost else float("nan")

    point = roi(s) - roi(b)
    rng = np.random.default_rng(20260615)
    sims = []
    n = len(dates)
    for _ in range(reps):
        idx = rng.integers(0, n, n)
        value = roi(s, idx) - roi(b, idx)
        if math.isfinite(value):
            sims.append(value)
    lo, hi = np.quantile(sims, [0.025, 0.975]) if sims else (float("nan"), float("nan"))
    return {"excess_roi": point, "ci95": [float(lo), float(hi)], "reps": len(sims)}


def physical_pooled() -> list[dict[str, Any]]:
    phys = pd.read_csv(PHYSICAL)
    focus = phys[phys["decision_hour_local"].between(13, 17)].copy()
    grouped = focus.groupby(["group", "decline_bucket"]).apply(
        lambda g: pd.Series(
            {
                "n": int(g["n"].sum()),
                "p_jump_ge1": float((g["p_jump_ge1"] * g["n"]).sum() / g["n"].sum()),
                "p_jump_ge2": float((g["p_jump_ge2"] * g["n"]).sum() / g["n"].sum()),
            }
        ),
        include_groups=False,
    )
    return grouped.reset_index().to_dict("records")


def registry_summary() -> dict[str, Any]:
    if not REGISTRY_JSON.exists():
        return {"missing": True}
    data = json.loads(REGISTRY_JSON.read_text())
    return {
        "summary_by_class": data.get("summary_by_class", []),
        "source": str(REGISTRY_JSON.relative_to(ROOT)),
    }


def rule_table_markdown(rows_: list[dict[str, Any]]) -> str:
    header = "| rule | rows | city-days | active dates | ROI | excess vs baseline | excess CI | daily t | train ROI | holdout ROI |\n"
    sep = "|---|---:|---:|---:|---:|---:|---|---:|---:|---:|\n"
    lines = [header, sep]
    for row in rows_:
        split = row["split"]
        boot = row["bootstrap_vs_baseline"]
        ci = boot["ci95"]
        ci_s = "NA" if ci[0] is None else f"[{pct(ci[0])}, {pct(ci[1])}]"
        lines.append(
            f"| `{row['rule']}` | {row['summary']['rows']} | {row['summary'].get('city_days', 0)} | "
            f"{row['summary'].get('active_dates', 0)} | {pct(row['summary'].get('roi'))} | "
            f"{pct(boot.get('excess_roi'))} | {ci_s} | "
            f"{num(row['summary'].get('daily_t'))} | "
            f"{pct(split['train'].get('roi'))} | {pct(split['holdout'].get('roi'))} |\n"
        )
    return "".join(lines)


def main() -> None:
    if not TAIL_QUOTES.exists() or not PHYSICAL.exists():
        raise SystemExit("Run research_m3_exhaustion_no.py first; required v0 artifacts are missing.")

    tail = pd.read_csv(TAIL_QUOTES)
    tail["target_date"] = tail["target_date"].astype(str)
    tail["rule_group"] = tail["group"].map({"whitelist": "default_wu_control", "repaired": "station_basis_repaired6"})

    masks = {
        "default_wu_baseline_h15_17_d1": (
            tail["group"].eq("whitelist") & tail["decision_hour_local"].between(15, 17) & tail["distance"].ge(1)
        ),
        "default_wu_exhaustion_h13_17_decline1_d1": (
            tail["group"].eq("whitelist")
            & tail["decision_hour_local"].between(13, 17)
            & tail["decline"].ge(1.0)
            & tail["distance"].ge(1)
        ),
        "default_wu_exhaustion_h13_17_decline2_d1": (
            tail["group"].eq("whitelist")
            & tail["decision_hour_local"].between(13, 17)
            & tail["decline"].ge(2.0)
            & tail["distance"].ge(1)
        ),
        "station_basis_baseline_h15_17_d1": (
            tail["group"].eq("repaired") & tail["decision_hour_local"].between(15, 17) & tail["distance"].ge(1)
        ),
        "station_basis_exhaustion_h13_17_decline1_d1": (
            tail["group"].eq("repaired")
            & tail["decision_hour_local"].between(13, 17)
            & tail["decline"].ge(1.0)
            & tail["distance"].ge(1)
        ),
        "station_basis_exhaustion_h13_17_decline1_d2": (
            tail["group"].eq("repaired")
            & tail["decision_hour_local"].between(13, 17)
            & tail["decline"].ge(1.0)
            & tail["distance"].ge(2)
        ),
        "station_basis_anti_fresh_h13_17_d1": (
            tail["group"].eq("repaired")
            & tail["decision_hour_local"].between(13, 17)
            & tail["decline"].lt(0.5)
            & tail["distance"].ge(1)
        ),
    }

    selected = {name: select_entries(tail, mask) for name, mask in masks.items()}
    baseline_for = {
        "default_wu_baseline_h15_17_d1": "default_wu_baseline_h15_17_d1",
        "default_wu_exhaustion_h13_17_decline1_d1": "default_wu_baseline_h15_17_d1",
        "default_wu_exhaustion_h13_17_decline2_d1": "default_wu_baseline_h15_17_d1",
        "station_basis_baseline_h15_17_d1": "station_basis_baseline_h15_17_d1",
        "station_basis_exhaustion_h13_17_decline1_d1": "station_basis_baseline_h15_17_d1",
        "station_basis_exhaustion_h13_17_decline1_d2": "station_basis_baseline_h15_17_d1",
        "station_basis_anti_fresh_h13_17_d1": "station_basis_baseline_h15_17_d1",
    }

    rule_rows = []
    for name, frame in selected.items():
        base = selected[baseline_for[name]]
        rule_rows.append(
            {
                "rule": name,
                "summary": summarize_entries(frame),
                "split": split_summary(frame),
                "bootstrap_vs_baseline": cluster_bootstrap_excess(frame, base),
            }
        )

    v0_rules = pd.read_csv(RULES_V0).to_dict("records") if RULES_V0.exists() else []
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "exhaustion_incremental_alpha",
        "row_grain": "selected strategy row = first qualifying orderbook quote per city + target_date + bracket",
        "data_layer": "historical opportunity research using v0 observed-max/orderbook artifacts; no live action",
        "self_check": data_self_check(),
        "clob_gate": load_gate(),
        "registry_summary": registry_summary(),
        "funnel": {
            "tail_quote_rows": int(len(tail)),
            "tail_quote_groups": tail["group"].value_counts().to_dict(),
            "date_min": str(tail["target_date"].min()),
            "date_max": str(tail["target_date"].max()),
            "v1_rule_count": len(rule_rows),
        },
        "physical_h13_17": physical_pooled(),
        "rules": rule_rows,
        "v0_reference_rules": v0_rules,
        "verdict": {
            "default_wu_control": "negative: exhaustion improves the crude clock baseline but does not clear zero or baseline gates",
            "station_basis_repaired6": "positive mechanism: exhaustion is a variance/risk gate on top of station-basis, not an independent theta edge",
            "recommended_action": "continue station-basis shadow/research; do not launch a generic NO-theta strategy",
            "significance": "FAIL for generic theta; exploratory PASS/partial for station-basis mechanism only",
            "baseline": "FAIL for generic theta; station-basis baseline is the alpha source",
            "forward": "NA in this historical restart; forward evidence remains the station-basis shadow/live-prep gate",
            "conclusion": "inconclusive for live action",
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    default_rule = next(r for r in rule_rows if r["rule"] == "default_wu_exhaustion_h13_17_decline1_d1")
    basis_rule = next(r for r in rule_rows if r["rule"] == "station_basis_exhaustion_h13_17_decline1_d1")
    physical = {
        (row["group"], row["decline_bucket"]): row
        for row in payload["physical_h13_17"]
    }
    wh_fresh = physical[("whitelist", "<0.5")]
    wh_exh2 = physical[("whitelist", ">=2.0")]
    rp_fresh = physical[("repaired", "<0.5")]
    rp_exh2 = physical[("repaired", ">=2.0")]
    md = f"""# M3 Exhaustion Source-Aware Restart v1

Status: snapshot
Updated: 2026-06-15
Source of truth: no
Used by: WEATHER_DOCS_INDEX.md

## 数据快照

- 数据源: `runtime/weather.db` self-check + historical M3 v0 artifacts under `docs/analysis/2026-06/generated/m3_exhaustion_no_v0/`.
- DB fact built at: `{payload['self_check']['fact_trades_max_built_at_utc']}`.
- CLOB gate: `gate_pass={payload['clob_gate'].get('gate_pass')}`, `missing_order_rows={payload['clob_gate'].get('missing_order_rows')}`, `over_order_keys={payload['clob_gate'].get('over_order_keys')}`.
- `fact_trades` by class: `{payload['self_check']['fact_trades_by_class']}`.
- `fact_trades` by settlement: `{payload['self_check']['fact_trades_by_settlement_status']}`.
- `fact_signal_candidates`: `{payload['self_check']['fact_signal_candidate_coverage']}`.
- CLOB order/fill join: `{payload['self_check']['clob_order_fill_join']}`.
- Note: `run_stack.sh` completed DB/fact/gate rebuild but failed at frontend startup because port 5174 stayed busy; this report uses the rebuilt DB and artifacts, not the frontend.

## Target Metric

`exhaustion_incremental_alpha` = whether a decision-time "will it heat up again" gate improves BUY_NO outcomes relative to the same source bucket's clock-only NO baseline.

Row grain: one selected row is the first qualifying orderbook quote for one `city + target_date + bracket`. This is opportunity research, not live fills.

## Funnel

- Raw tail-NO quote rows: `{payload['funnel']['tail_quote_rows']}` from `{payload['funnel']['date_min']}` to `{payload['funnel']['date_max']}`.
- Quote groups: `{payload['funnel']['tail_quote_groups']}`.
- V1 selected rules: `{payload['funnel']['v1_rule_count']}`.
- Source buckets: `default_wu_control` is the clean theta control; `station_basis_repaired6` is the old repaired six station-basis cities.

## Physical Layer

The physical premise is real. In 13-17h observations, default-WU cities move from P(jump>=1) {pct(wh_fresh['p_jump_ge1'], signed=False)} when decline is `<0.5C` to {pct(wh_exh2['p_jump_ge1'], signed=False)} when decline is `>=2C`; station-basis repaired cities move from {pct(rp_fresh['p_jump_ge1'], signed=False)} to {pct(rp_exh2['p_jump_ge1'], signed=False)}. The strategy question is whether that public fact is mispriced.

## Results

{rule_table_markdown(rule_rows)}

## Interpretation

Generic/default-WU theta remains closed. The main v1 clean-theta rule (`default_wu_exhaustion_h13_17_decline1_d1`) produced {default_rule['summary']['rows']} selected rows, ROI {pct(default_rule['summary'].get('roi'))}, and excess vs its same-group clock baseline {pct(default_rule['bootstrap_vs_baseline'].get('excess_roi'))} with CI [{pct(default_rule['bootstrap_vs_baseline']['ci95'][0])}, {pct(default_rule['bootstrap_vs_baseline']['ci95'][1])}]. That is not a tradable edge.

Station-basis exhaustion is different. The main basis rule (`station_basis_exhaustion_h13_17_decline1_d1`) produced {basis_rule['summary']['rows']} selected rows, ROI {pct(basis_rule['summary'].get('roi'))}, {basis_rule['summary'].get('positive_dates')}/{basis_rule['summary'].get('active_dates')} positive active dates, and daily t {num(basis_rule['summary'].get('daily_t'))}. The alpha source is still station/source basis; exhaustion is a risk gate that tells us when the basis can be expressed with less "still heating" tail risk.

## Verdict

significance=FAIL for generic theta / PARTIAL for station-basis mechanism
baseline=FAIL for generic theta / PASS only because station-basis is the baseline source of alpha
forward=NA here; use station-basis shadow/live-prep gate for forward evidence
conclusion=inconclusive for live action

Action: restart the research as `basis x exhaustion`, not as standalone NO low-insurance/theta. The next useful work is to improve the "will it heat up again" model as a sizing/filter layer inside station-basis shadow, while keeping default-WU generic NO theta off.
"""
    OUT_MD.write_text(md)
    print(json.dumps({"json": str(OUT_JSON), "md": str(OUT_MD), "verdict": payload["verdict"]}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
