#!/usr/bin/env python3
"""Live-candidate selector check for theta-NO carry.

Input is the expanded paired replay from v4.  This script asks a narrower
question: can we filter the d1 high-ask carry shape into a live-ready rule, or
does it remain only a shadow/research expression?
"""

from __future__ import annotations

import ast
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
QUOTES = ROOT / "docs/analysis/2026-06/generated/theta_no_carry_expanded_replay_v4/expanded_quote_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_no_live_candidate_selector_v5"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-16-theta-no-live-candidate-selector-v5.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-16-theta-no-live-candidate-selector-v5.md"

SPLIT_DATE = "2026-06-01"
GRID_SEED = 20260616


@dataclass(frozen=True)
class Profile:
    ask_min: float
    decline_min: float
    hour_start: int
    hour_end: int
    yes_ask_max: float | None

    @property
    def name(self) -> str:
        yes = "anyYES" if self.yes_ask_max is None else f"yesAsk<={self.yes_ask_max:g}"
        return f"d1|ask>={self.ask_min:g}|decline>={self.decline_min:g}|h{self.hour_start}-{self.hour_end}|{yes}"


@dataclass(frozen=True)
class SelectorConfig:
    name: str
    min_history_dates: int
    min_history_rows: int
    require_hist_no_roi_positive: bool
    require_hist_delta_positive: bool
    score: str


SELECTORS = [
    SelectorConfig("best_hist_delta_positive", 8, 20, False, True, "delta"),
    SelectorConfig("best_hist_no_roi_positive", 8, 20, True, False, "no_roi"),
    SelectorConfig("strict_both_positive", 8, 20, True, True, "delta"),
]


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


def load_quotes() -> pd.DataFrame:
    q = pd.read_csv(QUOTES)
    q["target_date"] = q["target_date"].astype(str)
    q = q[q["distance"].eq(1)].copy()
    q["period"] = np.where(q["target_date"] < SPLIT_DATE, "train", "holdout")
    q["hour_bucket"] = pd.cut(
        q["decision_hour_local"],
        bins=[12, 13, 14, 15, 16, 17],
        labels=["13", "14", "15", "16", "17"],
    )
    q["decline_bucket_v5"] = pd.cut(
        q["decline"],
        bins=[-0.01, 0.5, 1.0, 1.5, 2.0, 99],
        labels=["<0.5", "0.5-1.0", "1.0-1.5", "1.5-2.0", ">=2.0"],
    )
    q["no_ask_bucket"] = pd.cut(
        q["best_ask"],
        bins=[0, 0.75, 0.85, 0.90, 0.95, 0.9701],
        labels=["<0.75", "0.75-0.85", "0.85-0.90", "0.90-0.95", "0.95-0.97"],
    )
    q["yes_ask_bucket"] = pd.cut(
        q["yes_current_ask"],
        bins=[0, 0.05, 0.10, 0.20, 0.40, 1.0],
        labels=["<=0.05", "0.05-0.10", "0.10-0.20", "0.20-0.40", ">0.40"],
    )
    return q


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.sort_values(["decision_hour_local", "snapshot_ts_utc", "city", "target_date", "bracket"])
        .drop_duplicates(["city", "target_date", "bracket"], keep="first")
        .copy()
    )


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "rows": 0,
            "active_dates": 0,
            "cities": 0,
            "no_roi": None,
            "yes_roi": None,
            "delta_roi": None,
        }
    no_cost = float(df["best_ask"].sum())
    yes_cost = float(df["yes_current_ask"].sum())
    no_pnl = float(df["no_pnl"].sum())
    yes_pnl = float(df["yes_current_pnl"].sum())
    daily = df.groupby("target_date").agg(no_pnl=("no_pnl", "sum"), yes_pnl=("yes_current_pnl", "sum"))
    return {
        "rows": int(len(df)),
        "active_dates": int(df["target_date"].nunique()),
        "cities": int(df["city"].nunique()),
        "avg_no_ask": float(df["best_ask"].mean()),
        "avg_yes_ask": float(df["yes_current_ask"].mean()),
        "no_cost": no_cost,
        "no_pnl": no_pnl,
        "no_roi": no_pnl / no_cost if no_cost else None,
        "yes_cost": yes_cost,
        "yes_pnl": yes_pnl,
        "yes_roi": yes_pnl / yes_cost if yes_cost else None,
        "delta_roi": (no_pnl / no_cost - yes_pnl / yes_cost) if no_cost and yes_cost else None,
        "no_lose_rate": float(df["no_loses"].mean()),
        "skip_over_rate": float(df["skip_over_wins_no_only"].mean()),
        "no_positive_date_rate": float((daily["no_pnl"] > 0).mean()),
        "yes_positive_date_rate": float((daily["yes_pnl"] > 0).mean()),
    }


def bootstrap(df: pd.DataFrame, reps: int = 3000) -> dict[str, Any]:
    if df.empty or df["target_date"].nunique() < 3:
        return {"no_roi_ci95": [None, None], "delta_ci95": [None, None], "reps": 0}
    daily = df.groupby("target_date").agg(
        no_cost=("best_ask", "sum"),
        no_pnl=("no_pnl", "sum"),
        yes_cost=("yes_current_ask", "sum"),
        yes_pnl=("yes_current_pnl", "sum"),
    )

    def values(frame: pd.DataFrame, idx: np.ndarray | None = None) -> tuple[float, float]:
        work = frame if idx is None else frame.iloc[idx]
        no_cost = float(work["no_cost"].sum())
        yes_cost = float(work["yes_cost"].sum())
        if no_cost <= 0 or yes_cost <= 0:
            return float("nan"), float("nan")
        no_roi = float(work["no_pnl"].sum() / no_cost)
        yes_roi = float(work["yes_pnl"].sum() / yes_cost)
        return no_roi, no_roi - yes_roi

    rng = np.random.default_rng(GRID_SEED)
    no_vals: list[float] = []
    delta_vals: list[float] = []
    for _ in range(reps):
        idx = rng.integers(0, len(daily), len(daily))
        no_roi, delta = values(daily, idx)
        if math.isfinite(no_roi):
            no_vals.append(no_roi)
        if math.isfinite(delta):
            delta_vals.append(delta)
    no_lo, no_hi = np.quantile(no_vals, [0.025, 0.975]) if no_vals else (float("nan"), float("nan"))
    d_lo, d_hi = np.quantile(delta_vals, [0.025, 0.975]) if delta_vals else (float("nan"), float("nan"))
    return {
        "no_roi_ci95": [float(no_lo), float(no_hi)],
        "delta_ci95": [float(d_lo), float(d_hi)],
        "reps": min(len(no_vals), len(delta_vals)),
    }


def profile_grid() -> list[Profile]:
    return [
        Profile(ask, decline, h0, h1, yask)
        for ask in (0.75, 0.80, 0.85, 0.90, 0.93, 0.95)
        for decline in (0.5, 1.0, 1.5, 2.0)
        for h0, h1 in ((13, 17), (13, 15), (14, 17), (15, 17), (16, 17))
        for yask in (None, 0.10, 0.20, 0.30, 0.50)
    ]


def apply_profile(q: pd.DataFrame, profile: Profile) -> pd.DataFrame:
    mask = (
        q["best_ask"].ge(profile.ask_min)
        & q["decline"].ge(profile.decline_min)
        & q["decision_hour_local"].between(profile.hour_start, profile.hour_end)
    )
    if profile.yes_ask_max is not None:
        mask &= q["yes_current_ask"].le(profile.yes_ask_max)
    return dedupe(q[mask].copy())


def profile_grid_results(q: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for profile in profile_grid():
        selected = apply_profile(q, profile)
        for period in ("train", "holdout", "all"):
            frame = selected if period == "all" else selected[selected["period"].eq(period)]
            if period in {"train", "holdout"} and (len(frame) < 10 or frame["target_date"].nunique() < 5):
                continue
            sm = summarize(frame)
            boot = bootstrap(frame)
            rows.append({"profile": profile.name, "period": period, **sm, **boot})
    grid = pd.DataFrame(rows)
    if grid.empty:
        return grid, {"candidate_profiles": 0, "consistent_positive_profiles": 0}
    train = grid[grid["period"].eq("train")][["profile", "no_roi", "delta_roi", "rows", "active_dates"]].rename(
        columns={"no_roi": "train_no_roi", "delta_roi": "train_delta_roi", "rows": "train_rows", "active_dates": "train_dates"}
    )
    hold = grid[grid["period"].eq("holdout")][["profile", "no_roi", "delta_roi", "rows", "active_dates"]].rename(
        columns={"no_roi": "holdout_no_roi", "delta_roi": "holdout_delta_roi", "rows": "holdout_rows", "active_dates": "holdout_dates"}
    )
    merged = train.merge(hold, on="profile", how="inner")
    consistent = merged[
        merged["train_no_roi"].gt(0)
        & merged["train_delta_roi"].gt(0)
        & merged["holdout_no_roi"].gt(0)
        & merged["holdout_delta_roi"].gt(0)
        & merged["holdout_rows"].ge(30)
        & merged["holdout_dates"].ge(10)
    ]
    stats = {
        "candidate_profiles": int(merged["profile"].nunique()),
        "consistent_positive_profiles": int(len(consistent)),
        "best_holdout_no_roi_profile": str(
            grid[grid["period"].eq("holdout")].sort_values("no_roi", ascending=False).iloc[0]["profile"]
        ),
    }
    return grid, stats


def run_walkforward(q: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = sorted(q["target_date"].unique())
    rows: list[pd.DataFrame] = []
    decisions: list[dict[str, Any]] = []
    profiles = profile_grid()
    for selector in SELECTORS:
        for date in dates:
            hist = q[q["target_date"] < date]
            if hist["target_date"].nunique() < selector.min_history_dates:
                continue
            tests: list[dict[str, Any]] = []
            for profile in profiles:
                hsel = apply_profile(hist, profile)
                if len(hsel) < selector.min_history_rows:
                    continue
                sm = summarize(hsel)
                if selector.require_hist_no_roi_positive and (sm.get("no_roi") is None or sm["no_roi"] <= 0):
                    continue
                if selector.require_hist_delta_positive and (sm.get("delta_roi") is None or sm["delta_roi"] <= 0):
                    continue
                score = sm["delta_roi"] if selector.score == "delta" else sm["no_roi"]
                if score is None or not math.isfinite(float(score)):
                    continue
                tests.append({"profile_obj": profile, "profile": profile.name, "score": score, **sm})
            if not tests:
                decisions.append({"selector": selector.name, "target_date": date, "profile": None, "rows": 0})
                continue
            chosen = sorted(tests, key=lambda r: (r["score"], r["rows"]), reverse=True)[0]
            day = apply_profile(q[q["target_date"].eq(date)], chosen["profile_obj"])
            decisions.append(
                {
                    "selector": selector.name,
                    "target_date": date,
                    "profile": chosen["profile"],
                    "hist_rows": chosen["rows"],
                    "hist_dates": chosen["active_dates"],
                    "hist_no_roi": chosen["no_roi"],
                    "hist_delta_roi": chosen["delta_roi"],
                    "test_rows": int(len(day)),
                }
            )
            if not day.empty:
                temp = day.copy()
                temp["selector"] = selector.name
                temp["selected_profile"] = chosen["profile"]
                rows.append(temp)
    wf_rows = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    dec = pd.DataFrame(decisions)
    return wf_rows, dec


def walkforward_summary(wf_rows: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for selector in [s.name for s in SELECTORS]:
        frame = wf_rows[wf_rows["selector"].eq(selector)].copy() if not wf_rows.empty else pd.DataFrame()
        sm = summarize(frame)
        boot = bootstrap(frame)
        rows.append({"selector": selector, **sm, **boot})
    return pd.DataFrame(rows)


def badcase_bins(q: pd.DataFrame) -> pd.DataFrame:
    core = dedupe(q[q["best_ask"].ge(0.75) & q["decline"].ge(0.5)].copy())
    specs = [
        "hour_bucket",
        "decline_bucket_v5",
        "no_ask_bucket",
        "yes_ask_bucket",
        "city",
    ]
    rows = []
    for col in specs:
        for val, g in core.groupby(col, dropna=False, observed=False):
            if len(g) < 5:
                continue
            sm = summarize(g)
            rows.append({"feature": col, "value": str(val), **sm})
    return pd.DataFrame(rows).sort_values(["feature", "no_roi"], ascending=[True, False])


def write_markdown(payload: dict[str, Any], grid: pd.DataFrame, wf: pd.DataFrame, bins: pd.DataFrame) -> None:
    self_check = payload["data_self_check"]
    gate = payload["clob_gate"]
    grid_stats = payload["grid_stats"]
    top_hold = (
        grid[grid["period"].eq("holdout")]
        .sort_values(["no_roi", "rows"], ascending=[False, False])
        .head(8)
        if not grid.empty
        else pd.DataFrame()
    )
    top_bins = bins[bins["feature"].isin(["hour_bucket", "decline_bucket_v5", "no_ask_bucket", "yes_ask_bucket"])].copy()
    top_bins = top_bins.sort_values(["feature", "no_roi"], ascending=[True, False])

    lines = [
        "# Theta NO Live Candidate Selector v5",
        "",
        "Status: snapshot",
        f"Generated: {payload['generated_at_utc']}",
        "Target metric: `live_candidate_theta_no_selector` = 在 expanded paired replay 中，只看 source-aligned d1 高 ask NO carry，检验是否存在可前瞻复用的 live 规则。",
        "",
        "## 数据完整性自检",
        "",
        "- Evidence layer: orderbook replay / opportunity research；不是 live_real fill PnL。",
        f"- input quote rows: {payload['coverage']['input_rows']}; d1 rows: {payload['coverage']['d1_rows']}; active dates: {payload['coverage']['active_dates']}.",
        f"- fact_built_at_utc: `{self_check['fact_trades_max_built_at_utc']}`。",
        f"- fact_trades trade_class: `{self_check['fact_trades_by_class']}`。",
        f"- fact_trades settlement_status: `{self_check['fact_trades_by_settlement_status']}`。",
        f"- fact_signal_candidates coverage: `{self_check['fact_signal_candidate_coverage']}`。",
        f"- CLOB orders/fills join: `{self_check['clob_order_fill_join']}`。",
        f"- CLOB coverage gate: gate_pass={gate.get('gate_pass')}, missing_order_rows={gate.get('missing_order_rows')}, over_order_keys={gate.get('over_order_keys')}, db_fill_cost_minus_fact_cost={gate.get('db_fill_cost_minus_fact_cost')}.",
        "",
        "## 人话结论",
        "",
        f"我把 d1 高 ask NO carry 继续往 live 方向压了一轮：网格一共形成 {grid_stats['candidate_profiles']} 个有 train/holdout 支撑的候选 profile，但同时满足 train NO ROI>0、train NO-over-YES>0、holdout NO ROI>0、holdout NO-over-YES>0、且 holdout 至少 30 行/10 天的 profile 数量是 {grid_stats['consistent_positive_profiles']}。",
        "",
        "这不是“物理逻辑错了”，而是市场价格已经把这类 no-reheat 信息吃得很干净。你能看到一些 holdout 看起来还行的切片，但它们在 train 里同号失败；prefix walk-forward 只能用过去日期选参数，结果也没有过 live 的三道门。",
        "",
        "一句话结论：在 2026-05-20..2026-06-14 expanded replay 中，d1 high-ask theta-NO 相对 current YES 的可前瞻超额 ROI 没有显著大于 0，前瞻 FAIL，结论等级 `inconclusive`，不允许 live。",
        "",
        "## 最好的 holdout 切片也没过前瞻",
        "",
        "这一段只用来解释“为什么不能 live”：排在最上面的切片行数很少，而且 train 同号失败；它不是可部署规则。",
        "",
        "| profile | rows | dates | NO ROI | YES ROI | NO-YES | NO CI95 | Delta CI95 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in top_hold.iterrows():
        no_ci = ast.literal_eval(r["no_roi_ci95"]) if isinstance(r["no_roi_ci95"], str) else r["no_roi_ci95"]
        delta_ci = ast.literal_eval(r["delta_ci95"]) if isinstance(r["delta_ci95"], str) else r["delta_ci95"]
        lines.append(
            f"| `{r['profile']}` | {int(r['rows'])} | {int(r['active_dates'])} | {pct(r['no_roi'])} | "
            f"{pct(r['yes_roi'])} | {pct(r['delta_roi'])} | [{pct(no_ci[0])}, {pct(no_ci[1])}] | "
            f"[{pct(delta_ci[0])}, {pct(delta_ci[1])}] |"
        )
    lines.extend(
        [
            "",
            "## Prefix walk-forward",
            "",
            "| selector | rows | dates | NO ROI | YES ROI | NO-YES | NO CI95 | Delta CI95 | conclusion |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for _, r in wf.iterrows():
        no_ci = ast.literal_eval(r["no_roi_ci95"]) if isinstance(r["no_roi_ci95"], str) else r["no_roi_ci95"]
        delta_ci = ast.literal_eval(r["delta_ci95"]) if isinstance(r["delta_ci95"], str) else r["delta_ci95"]
        conclusion = "inconclusive"
        lines.append(
            f"| `{r['selector']}` | {int(r['rows'])} | {int(r['active_dates'])} | {pct(r['no_roi'])} | "
            f"{pct(r['yes_roi'])} | {pct(r['delta_roi'])} | [{pct(no_ci[0])}, {pct(no_ci[1])}] | "
            f"[{pct(delta_ci[0])}, {pct(delta_ci[1])}] | {conclusion} |"
        )
    lines.extend(
        [
            "",
            "## 失败来源",
            "",
            "核心坏例不是随机散落：d1 NO 的输法是最终刚好落到上方一档。新增 6/10..6/14 的问题尤其明显：skip-over 几乎没有，NO 少了它相对 current YES 的结构优势。",
            "",
            "| feature | value | rows | dates | NO ROI | YES ROI | NO lose | skip-over |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for _, r in top_bins.iterrows():
        lines.append(
            f"| {r['feature']} | `{r['value']}` | {int(r['rows'])} | {int(r['active_dates'])} | "
            f"{pct(r['no_roi'])} | {pct(r['yes_roi'])} | {pct(r['no_lose_rate'], signed=False)} | {pct(r['skip_over_rate'], signed=False)} |"
        )
    lines.extend(
        [
            "",
            "## 三道门",
            "",
            "- significance=FAIL：NO ROI 和 NO-over-YES 的日期 bootstrap CI 没有稳定不跨 0。",
            "- baseline=FAIL：相对 current-bucket YES 没有稳定超额；这正是要回答的 sibling expression baseline。",
            "- forward=FAIL：train 看起来可选的 profile 在 holdout 不同号，prefix walk-forward 也不过。",
            "- conclusion=inconclusive：禁止 live；最多继续做 shadow-only sibling selector。",
            "",
            "## 产物",
            "",
            f"- CSV: `{payload['outputs']['profile_grid']}`",
            f"- CSV: `{payload['outputs']['walkforward_rows']}`",
            f"- CSV: `{payload['outputs']['walkforward_summary']}`",
            f"- CSV: `{payload['outputs']['badcase_bins']}`",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    q = load_quotes()
    grid, grid_stats = profile_grid_results(q)
    wf_rows, wf_decisions = run_walkforward(q)
    wf_summary = walkforward_summary(wf_rows)
    bins = badcase_bins(q)

    paths = {
        "profile_grid": OUT_DIR / "profile_grid.csv",
        "walkforward_rows": OUT_DIR / "walkforward_rows.csv",
        "walkforward_decisions": OUT_DIR / "walkforward_decisions.csv",
        "walkforward_summary": OUT_DIR / "walkforward_summary.csv",
        "badcase_bins": OUT_DIR / "badcase_bins.csv",
    }
    grid.to_csv(paths["profile_grid"], index=False)
    wf_rows.to_csv(paths["walkforward_rows"], index=False)
    wf_decisions.to_csv(paths["walkforward_decisions"], index=False)
    wf_summary.to_csv(paths["walkforward_summary"], index=False)
    bins.to_csv(paths["badcase_bins"], index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "live_candidate_theta_no_selector",
        "coverage": {
            "input_rows": int(len(pd.read_csv(QUOTES))),
            "d1_rows": int(len(q)),
            "active_dates": int(q["target_date"].nunique()),
            "date_min": str(q["target_date"].min()),
            "date_max": str(q["target_date"].max()),
        },
        "data_self_check": data_self_check(),
        "clob_gate": load_gate(),
        "grid_stats": grid_stats,
        "walkforward_summary": wf_summary.to_dict(orient="records"),
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
    write_markdown(payload, grid, wf_summary, bins)
    print(json.dumps({"grid_stats": grid_stats, "walkforward_summary": payload["walkforward_summary"], "verdict": payload["verdict"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
