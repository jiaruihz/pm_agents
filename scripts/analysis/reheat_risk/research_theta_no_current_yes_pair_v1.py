#!/usr/bin/env python3
"""Paired current-bucket YES vs d1 NO carry expression test.

The question is whether high-ask NO carry is an independent direction or just
another expression of buying YES on the current observed max bucket. This script
keeps the comparison paired at the same orderbook file / city / target date.
"""

from __future__ import annotations

import gzip
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
QUOTES = ROOT / "docs/analysis/2026-06/generated/m3_jump_model_v2_quote_calibration/calibrated_quotes.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_no_current_yes_pair_v1"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-16-theta-no-current-yes-pair-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-16-theta-no-current-yes-pair-v1.md"
SPLIT_DATE = "2026-06-01"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(x: float | None, signed: bool = True) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    sign = "+" if signed else ""
    return f"{float(x) * 100:{sign}.1f}%"


def fnum(x: float | None, signed: bool = False) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    sign = "+" if signed else ""
    return f"{float(x):{sign}.2f}"


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


def load_d1_quotes() -> pd.DataFrame:
    q = pd.read_csv(QUOTES)
    q["target_date"] = q["target_date"].astype(str)
    q = q[
        q["decision_hour_local"].between(13, 17)
        & q["dist_b"].eq(1)
        & q["best_ask"].between(0.005, 0.97)
    ].copy()
    q = q.sort_values(["decision_hour_local", "snapshot_ts_utc", "city", "target_date", "bracket"])
    return q.copy()


def load_yes_lookup(files: list[str]) -> tuple[dict[tuple[str, str, str, str], tuple[float | None, float | None]], dict[tuple[str, str, str], set[str]]]:
    yes: dict[tuple[str, str, str, str], tuple[float | None, float | None]] = {}
    labels: dict[tuple[str, str, str], set[str]] = {}
    for fp in files:
        with gzip.open(ROOT / fp if not fp.startswith("/") else fp, "rt", encoding="utf-8") as f:
            for line in f:
                obj = json.loads(line)
                if obj.get("status") != "ok":
                    continue
                city = str(obj.get("city"))
                date = str(obj.get("event_date"))
                bracket = str(obj.get("bracket"))
                labels.setdefault((fp, city, date), set()).add(bracket)
                if str(obj.get("outcome")).lower() == "yes":
                    summary = obj.get("summary") or {}
                    yes[(fp, city, date, bracket)] = (summary.get("best_ask"), summary.get("ask_size"))
    return yes, labels


def choose_current_bracket(row: pd.Series, labels: dict[tuple[str, str, str], set[str]]) -> str | None:
    labs = labels.get((row["orderbook_file"], row["city"], row["target_date"]), set())
    if not labs or pd.isna(row["running_value"]):
        return None
    try:
        running = int(row["running_value"])
    except ValueError:
        return None
    exact = str(running)
    if exact in labs:
        return exact
    for label in sorted(labs):
        if not label.endswith("+"):
            continue
        try:
            if int(label[:-1]) <= running:
                return label
        except ValueError:
            continue
    return None


def build_paired() -> pd.DataFrame:
    q = load_d1_quotes()
    yes, labels = load_yes_lookup(sorted(q["orderbook_file"].unique()))
    q["current_bracket"] = q.apply(lambda row: choose_current_bracket(row, labels), axis=1)
    q["yes_current_ask"] = q.apply(
        lambda row: yes.get((row["orderbook_file"], row["city"], row["target_date"], str(row["current_bracket"])), (None, None))[0],
        axis=1,
    )
    q["yes_current_size"] = q.apply(
        lambda row: yes.get((row["orderbook_file"], row["city"], row["target_date"], str(row["current_bracket"])), (None, None))[1],
        axis=1,
    )
    q["paired_executable"] = q["yes_current_ask"].notna()
    q = q[q["paired_executable"]].copy()
    q["current_yes_wins"] = q["winner_label"].astype(str).eq(q["current_bracket"].astype(str))
    q["d1_yes_wins"] = q["winner_label"].astype(str).eq(q["bracket"].astype(str))
    q["skip_over_wins_no_only"] = (~q["current_yes_wins"]) & (~q["d1_yes_wins"])
    q["no_d1_pnl"] = q["pnl"].astype(float)
    q["yes_current_pnl"] = q["current_yes_wins"].astype(float) - q["yes_current_ask"].astype(float)
    q["period"] = np.where(q["target_date"] < SPLIT_DATE, "train", "holdout")
    return q


def dedupe_strategy(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.sort_values(["decision_hour_local", "snapshot_ts_utc", "city", "target_date", "bracket"])
        .drop_duplicates(["city", "target_date", "bracket"], keep="first")
        .copy()
    )


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"rows": 0}
    no_cost = float(df["best_ask"].sum())
    yes_cost = float(df["yes_current_ask"].sum())
    no_pnl = float(df["no_d1_pnl"].sum())
    yes_pnl = float(df["yes_current_pnl"].sum())
    daily = df.groupby("target_date").agg(no_pnl=("no_d1_pnl", "sum"), yes_pnl=("yes_current_pnl", "sum"))
    return {
        "rows": int(len(df)),
        "active_dates": int(df["target_date"].nunique()),
        "cities": int(df["city"].nunique()),
        "no_cost": no_cost,
        "no_pnl": no_pnl,
        "no_roi": no_pnl / no_cost if no_cost else None,
        "yes_cost": yes_cost,
        "yes_pnl": yes_pnl,
        "yes_roi": yes_pnl / yes_cost if yes_cost else None,
        "no_minus_yes_pnl": no_pnl - yes_pnl,
        "no_avg_ask": float(df["best_ask"].mean()),
        "yes_avg_ask": float(df["yes_current_ask"].mean()),
        "current_win_rate": float(df["current_yes_wins"].mean()),
        "d1_hit_rate": float(df["d1_yes_wins"].mean()),
        "skip_over_rate": float(df["skip_over_wins_no_only"].mean()),
        "no_positive_dates": float((daily["no_pnl"] > 0).mean()),
        "yes_positive_dates": float((daily["yes_pnl"] > 0).mean()),
    }


def bootstrap_delta(df: pd.DataFrame, reps: int = 3000) -> dict[str, Any]:
    if df.empty or df["target_date"].nunique() < 3:
        return {"roi_delta_no_minus_yes": None, "ci95": [None, None], "reps": 0}
    dates = sorted(df["target_date"].unique())
    daily = (
        df.groupby("target_date")
        .agg(no_cost=("best_ask", "sum"), no_pnl=("no_d1_pnl", "sum"), yes_cost=("yes_current_ask", "sum"), yes_pnl=("yes_current_pnl", "sum"))
        .reindex(dates)
        .fillna(0.0)
    )

    def delta(frame: pd.DataFrame, idx: np.ndarray | None = None) -> float:
        work = frame if idx is None else frame.iloc[idx]
        no_roi = float(work["no_pnl"].sum() / work["no_cost"].sum()) if work["no_cost"].sum() else float("nan")
        yes_roi = float(work["yes_pnl"].sum() / work["yes_cost"].sum()) if work["yes_cost"].sum() else float("nan")
        return no_roi - yes_roi

    point = delta(daily)
    rng = np.random.default_rng(20260616)
    vals = []
    for _ in range(reps):
        idx = rng.integers(0, len(dates), len(dates))
        v = delta(daily, idx)
        if math.isfinite(v):
            vals.append(v)
    lo, hi = np.quantile(vals, [0.025, 0.975]) if vals else (float("nan"), float("nan"))
    return {"roi_delta_no_minus_yes": float(point), "ci95": [float(lo), float(hi)], "reps": len(vals)}


def profile_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "all_paired_d1_h13_17": pd.Series(True, index=df.index),
        "carry_ask75_decline05": df["best_ask"].ge(0.75) & df["decline"].ge(0.5),
        "carry_ask85_decline05_risk20": df["best_ask"].ge(0.85) & df["decline"].ge(0.5) & df["p_lose_raw_v1"].le(0.20),
        "walkforward_shape_ask70_decline05": df["best_ask"].ge(0.70) & df["decline"].ge(0.5),
    }


def run_analysis() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    paired_quote = build_paired()
    rows = []
    strategy_frames = []
    for profile, mask in profile_masks(paired_quote).items():
        selected = dedupe_strategy(paired_quote[mask].copy())
        selected["profile"] = profile
        strategy_frames.append(selected)
        for period in ("train", "holdout", "all"):
            frame = selected if period == "all" else selected[selected["period"].eq(period)]
            sm = summarize(frame)
            boot = bootstrap_delta(frame)
            rows.append({"profile": profile, "period": period, **sm, **boot})
    strategy = pd.concat(strategy_frames, ignore_index=True) if strategy_frames else pd.DataFrame()
    return paired_quote, strategy, pd.DataFrame(rows)


def write_markdown(payload: dict[str, Any], summary: pd.DataFrame) -> None:
    self_check = payload["data_self_check"]
    gate = payload["clob_gate"]
    carry_hold = summary[(summary["profile"].eq("carry_ask75_decline05")) & (summary["period"].eq("holdout"))].iloc[0].to_dict()
    strict_hold = summary[(summary["profile"].eq("carry_ask85_decline05_risk20")) & (summary["period"].eq("holdout"))].iloc[0].to_dict()
    all_quote = payload["quote_grain_payoff"]
    lines = [
        "# Theta NO vs Current YES Pair v1",
        "",
        "Status: snapshot",
        f"Generated: {payload['generated_at_utc']}",
        "Target metric: `theta_no_current_yes_expression_delta` = 同一 orderbook snapshot/city/date 下，买 d1 NO carry 与买当前 observed-max bucket YES 是否只是同一表达。",
        "",
        "## 数据快照",
        "",
        "- 数据源: v2 calibrated NO quotes + 原始 orderbook snapshot 中同文件 current-bucket YES ask；DB 只用于强制自检和 gate。",
        f"- run_stack: DB rebuild completed; FE start failed because port 5174 remained busy.",
        f"- fact_built_at_utc: `{self_check['fact_trades_max_built_at_utc']}`。",
        f"- fact_trades trade_class: `{self_check['fact_trades_by_class']}`。",
        f"- fact_trades settlement_status: `{self_check['fact_trades_by_settlement_status']}`。",
        f"- fact_signal_candidates coverage: `{self_check['fact_signal_candidate_coverage']}`。",
        f"- CLOB orders/fills join: `{self_check['clob_order_fill_join']}`。",
        f"- CLOB coverage gate: gate_pass={gate.get('gate_pass')}, missing_order_rows={gate.get('missing_order_rows')}, over_order_keys={gate.get('over_order_keys')}, db_fill_cost_minus_fact_cost={gate.get('db_fill_cost_minus_fact_cost')}.",
        "",
        "## 人话结论",
        "",
        "它们是同一个天气 thesis 的两个表达，但不是同一个 payoff。`YES current` 只在最终停在当前档时赢；`NO d1` 在当前档赢，也在跳过下一档时赢，只输给“刚好命中下一档”。",
        "",
        f"在全部可配对 quote 里，最终当前档占 {pct(all_quote['current_win_rate'], signed=False)}，d1 命中占 {pct(all_quote['d1_hit_rate'], signed=False)}，skip-over 占 {pct(all_quote['skip_over_rate'], signed=False)}。这 {pct(all_quote['skip_over_rate'], signed=False)} 就是 NO d1 相对 current YES 的 payoff convexity。",
        "",
        f"但在真正 high-ask carry 子集里，skip-over 很小：`ask>=0.75/decline>=0.5` holdout skip-over {pct(carry_hold['skip_over_rate'], signed=False)}；`ask>=0.85/decline>=0.5/risk<=0.2` holdout skip-over {pct(strict_hold['skip_over_rate'], signed=False)}。所以越接近低保 theta，它越像 current YES 的 sibling expression，而不是完全独立方向。",
        "",
        "交易上它们仍有区别：NO d1 通常更贵、更稳，YES current 更集中、更依赖不再升温。当前数据里没有证明 NO carry 明显优于 YES current；更像是同一 no-reheat thesis 下的 payoff/价格选择问题。",
        "",
        "## Strategy-Grain Paired Performance",
        "",
        "| profile | period | rows | dates | NO ROI | YES-current ROI | NO-YES ROI delta | delta CI95 | current | d1 hit | skip-over |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in summary.iterrows():
        lines.append(
            f"| `{r['profile']}` | {r['period']} | {int(r.get('rows', 0))} | {int(r.get('active_dates', 0))} | "
            f"{pct(r.get('no_roi'))} | {pct(r.get('yes_roi'))} | {pct(r.get('roi_delta_no_minus_yes'))} | "
            f"[{pct(r.get('ci95', [None, None])[0] if isinstance(r.get('ci95'), list) else r.get('ci95_low'))}, {pct(r.get('ci95', [None, None])[1] if isinstance(r.get('ci95'), list) else r.get('ci95_high'))}] | "
            f"{pct(r.get('current_win_rate'), signed=False)} | {pct(r.get('d1_hit_rate'), signed=False)} | {pct(r.get('skip_over_rate'), signed=False)} |"
        )
    lines.extend(
        [
            "",
            "Row grain: strategy 表中一行是每个 profile 下首次触发的 `city,target_date,d1_bracket` paired opportunity；quote-grain payoff 只用于解释状态关系。",
            "",
            "## 三道门 verdict",
            "",
            "在 paired strategy-grain 下，NO carry 相对 current YES 的 ROI delta 没有稳定过门；结论等级 `inconclusive`。表达层结论是：不是完全同一 payoff，但应归入同一个 no-reheat thesis family，一起比较价格和 skip-over convexity。",
            "",
            "- significance=FAIL: NO-vs-YES delta CI 不稳定。",
            "- baseline=FAIL/NA: current YES 是 sibling expression baseline，不是被显著打败的弱 baseline。",
            "- forward=FAIL: train/holdout 没有一致证明 NO 优于 YES。",
            "- conclusion=inconclusive: 不进 live；下一步把 current YES、NO d1、NO d2/ladder 放进同一个 expression selector。",
            "",
            "## 输出文件",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- generated CSV dir: `{OUT_DIR.relative_to(ROOT)}`",
            f"- Script: `scripts/analysis/reheat_risk/research_theta_no_current_yes_pair_v1.py`",
            "",
        ]
    )
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paired_quote, strategy, summary = run_analysis()
    # Store CI columns separately for CSV friendliness.
    summary["ci95_low"] = summary["ci95"].apply(lambda x: x[0] if isinstance(x, list) else None)
    summary["ci95_high"] = summary["ci95"].apply(lambda x: x[1] if isinstance(x, list) else None)
    paired_quote.to_csv(OUT_DIR / "paired_quote_rows.csv", index=False)
    strategy.to_csv(OUT_DIR / "paired_strategy_rows.csv", index=False)
    summary.to_csv(OUT_DIR / "paired_summary.csv", index=False)

    quote_payoff = summarize(paired_quote)
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "theta_no_current_yes_expression_delta",
        "inputs": {
            "calibrated_quotes": str(QUOTES.relative_to(ROOT)),
            "orderbook_snapshots": "runtime/weather_edge_v1/market_data/orderbook_snapshots",
        },
        "data_self_check": data_self_check(),
        "clob_gate": load_gate(),
        "coverage": {
            "d1_quote_rows": int(len(load_d1_quotes())),
            "paired_quote_rows_with_yes_current_ask": int(len(paired_quote)),
            "paired_strategy_rows": int(len(strategy)),
        },
        "quote_grain_payoff": quote_payoff,
        "summary": summary.drop(columns=["ci95"]).to_dict(orient="records"),
        "verdict": {
            "significance": "FAIL",
            "baseline": "FAIL/NA",
            "forward": "FAIL",
            "conclusion": "inconclusive",
            "live_action": "none",
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(payload, summary)
    print(json.dumps({"ok": True, "out_md": str(OUT_MD), "out_json": str(OUT_JSON)}, indent=2))


if __name__ == "__main__":
    main()
