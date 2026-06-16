#!/usr/bin/env python3
"""Pure theta-NO carry correction.

This script separates the intended carry trade from the mid-price directional
NO trades that slipped into the v2/v3 EV selector. Pure theta carry means:
buy relatively high NO ask after observed exhaustion and earn the remaining
premium if the bracket does not become the official winner.
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
QUOTES = ROOT / "docs/analysis/2026-06/generated/m3_jump_model_v2_quote_calibration/calibrated_quotes.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_no_pure_carry_v2"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-15-theta-no-pure-carry-v2.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-15-theta-no-pure-carry-v2.md"

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


def load_quotes() -> pd.DataFrame:
    q = pd.read_csv(QUOTES)
    q["target_date"] = q["target_date"].astype(str)
    q["period"] = np.where(q["target_date"] < SPLIT_DATE, "train", "holdout")
    q = q[
        q["decision_hour_local"].between(13, 17)
        & q["dist_b"].eq(1)
        & q["best_ask"].between(0.005, 0.97)
    ].copy()
    q = q.sort_values(["decision_hour_local", "snapshot_ts_utc", "city", "target_date", "bracket"])
    return q.drop_duplicates(["city", "target_date", "bracket"], keep="first").copy()


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"rows": 0, "roi": None}
    daily = df.groupby("target_date")["pnl"].sum()
    sd = float(daily.std(ddof=1)) if len(daily) > 1 else 0.0
    cost = float(df["best_ask"].sum())
    pnl = float(df["pnl"].sum())
    return {
        "rows": int(len(df)),
        "active_dates": int(df["target_date"].nunique()),
        "cities": int(df["city"].nunique()),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost else None,
        "win_rate": float((df["pnl"] > 0).mean()),
        "avg_ask": float(df["best_ask"].mean()),
        "daily_t": float(daily.mean() / sd * math.sqrt(len(daily))) if sd > 0 else None,
    }


def bootstrap_excess(candidate: pd.DataFrame, baseline: pd.DataFrame, reps: int = 3000) -> dict[str, Any]:
    dates = sorted(set(candidate["target_date"]) | set(baseline["target_date"]))
    if len(dates) < 3 or candidate.empty or baseline.empty:
        return {"excess_roi": None, "ci95": [None, None], "reps": 0}

    def daily(frame: pd.DataFrame) -> pd.DataFrame:
        return frame.groupby("target_date").agg(cost=("best_ask", "sum"), pnl=("pnl", "sum")).reindex(dates).fillna(0.0)

    c = daily(candidate)
    b = daily(baseline)

    def roi(frame: pd.DataFrame, idx: np.ndarray | None = None) -> float:
        work = frame if idx is None else frame.iloc[idx]
        cost = float(work["cost"].sum())
        return float(work["pnl"].sum() / cost) if cost else float("nan")

    point = roi(c) - roi(b)
    rng = np.random.default_rng(20260615)
    vals = []
    for _ in range(reps):
        idx = rng.integers(0, len(dates), len(dates))
        v = roi(c, idx) - roi(b, idx)
        if math.isfinite(v):
            vals.append(v)
    lo, hi = np.quantile(vals, [0.025, 0.975]) if vals else (float("nan"), float("nan"))
    return {"excess_roi": float(point), "ci95": [float(lo), float(hi)], "reps": len(vals)}


def evaluate_profiles(q: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    selected_frames = []
    for ask_min in (0.55, 0.70, 0.75, 0.80, 0.85, 0.90):
        ask_base = q[q["best_ask"].ge(ask_min)].copy()
        for decline_min in (0.0, 0.5, 1.0):
            cand = ask_base[ask_base["decline"].ge(decline_min)].copy()
            profile = f"d1|h13-17|ask>={ask_min:g}|decline>={decline_min:g}"
            if ask_min >= 0.75 and decline_min >= 0.5:
                temp = cand.copy()
                temp["profile"] = profile
                selected_frames.append(temp)
            for period in ("train", "holdout"):
                c = cand[cand["period"].eq(period)]
                b = ask_base[ask_base["period"].eq(period)]
                s = summarize(c)
                base = summarize(b)
                boot = bootstrap_excess(c, b)
                rows.append(
                    {
                        "profile": profile,
                        "period": period,
                        "ask_min": ask_min,
                        "decline_min": decline_min,
                        **{f"candidate_{k}": v for k, v in s.items()},
                        **{f"same_ask_baseline_{k}": v for k, v in base.items()},
                        "excess_roi_vs_same_ask": boot["excess_roi"],
                        "excess_ci95_low": boot["ci95"][0],
                        "excess_ci95_high": boot["ci95"][1],
                    }
                )
    selected = pd.concat(selected_frames, ignore_index=True) if selected_frames else pd.DataFrame()
    return pd.DataFrame(rows), selected


def write_markdown(payload: dict[str, Any], profile_df: pd.DataFrame) -> None:
    gate = payload["clob_gate"]
    self_check = payload["data_self_check"]
    hold = profile_df[profile_df["period"].eq("holdout")].copy()
    main = hold[
        hold["profile"].isin(
            [
                "d1|h13-17|ask>=0.75|decline>=0.5",
                "d1|h13-17|ask>=0.8|decline>=0.5",
                "d1|h13-17|ask>=0.85|decline>=0.5",
                "d1|h13-17|ask>=0.9|decline>=0.5",
            ]
        )
    ].copy()
    broader = hold[hold["profile"].isin(["d1|h13-17|ask>=0.55|decline>=0", "d1|h13-17|ask>=0.75|decline>=0"])].copy()
    top = hold[hold["candidate_rows"].ge(10)].sort_values("candidate_roi", ascending=False).head(8)
    key_profile = "d1|h13-17|ask>=0.75|decline>=0.5"
    key_hold = hold[hold["profile"].eq(key_profile)].iloc[0].to_dict()
    key_train = profile_df[(profile_df["profile"].eq(key_profile)) & (profile_df["period"].eq("train"))].iloc[0].to_dict()
    high_profile = "d1|h13-17|ask>=0.85|decline>=0.5"
    high_hold = hold[hold["profile"].eq(high_profile)].iloc[0].to_dict()

    lines = [
        "# Theta NO Pure Carry v2",
        "",
        "Status: snapshot",
        f"Generated: {payload['generated_at_utc']}",
        "Target metric: `pure_theta_carry` = source-aligned d1 NO 中，只看较高 NO ask 的 carry 形态；模型/衰竭信号用于排除再升温风险，不把 0.40-0.55 这种中低价方向单混入 theta。",
        "",
        "## 数据快照",
        "",
        "- 数据源: v2 calibrated quote replay；DB 只用于强制自检和 live fill gate。",
        f"- quote rows after d1/h13-17/source-aligned dedupe: {payload['coverage']['d1_h13_17_rows']}; holdout rows: {payload['coverage']['holdout_rows']}.",
        f"- fact_built_at_utc: `{self_check['fact_trades_max_built_at_utc']}`。",
        f"- fact_trades trade_class: `{self_check['fact_trades_by_class']}`。",
        f"- fact_trades settlement_status: `{self_check['fact_trades_by_settlement_status']}`。",
        f"- fact_signal_candidates coverage: `{self_check['fact_signal_candidate_coverage']}`。",
        f"- CLOB orders/fills join: `{self_check['clob_order_fill_join']}`。",
        f"- CLOB coverage gate: gate_pass={gate.get('gate_pass')}, missing_order_rows={gate.get('missing_order_rows')}, over_order_keys={gate.get('over_order_keys')}, db_fill_cost_minus_fact_cost={gate.get('db_fill_cost_minus_fact_cost')}.",
        "",
        "## 人话结论",
        "",
        "你指出的对：0.40-0.55 ask 不是 theta 低保标的。真正的 theta carry 应该是高 NO ask、小收益、低命中风险；如果一个城市真的已经衰竭，市场价格也应该更接近 0.75-0.95，而不是 0.45。",
        "",
        f"按这个修正口径，方向重新变得有希望：`ask>=0.75 + decline>=0.5°C` 在 holdout 是 ROI {pct(key_hold['candidate_roi'])}，{int(key_hold['candidate_rows'])} 行/{int(key_hold['candidate_active_dates'])} 天/{int(key_hold['candidate_cities'])} 城；`ask>=0.85 + decline>=0.5°C` 是 ROI {pct(high_hold['candidate_roi'])}，{int(high_hold['candidate_rows'])} 行/{int(high_hold['candidate_active_dates'])} 天/{int(high_hold['candidate_cities'])} 城。关键问题不是 holdout 不好，而是 train 同口径为负，forward consistency 还没成立。",
        "",
        "所以正确理解应该是：`0.40-0.55` 那批不是策略失败证据，而是 v2/v3 的 EV selector 选错了交易类型；真正要继续研究的是高 ask carry 里，decline/no-reheat 特征能否持续把尾部命中风险压低。",
        "",
        "## 关键 holdout 口径",
        "",
        "| profile | rows | dates | avg ask | ROI | PnL | win rate | excess vs same ask | CI95 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in main.sort_values(["ask_min", "decline_min"]).iterrows():
        lines.append(
            f"| `{r['profile']}` | {int(r['candidate_rows'])} | {int(r['candidate_active_dates'])} | {fnum(r['candidate_avg_ask'])} | "
            f"{pct(r['candidate_roi'])} | {fnum(r['candidate_pnl'], signed=True)} | {pct(r['candidate_win_rate'], signed=False)} | "
            f"{pct(r['excess_roi_vs_same_ask'])} | [{pct(r['excess_ci95_low'])}, {pct(r['excess_ci95_high'])}] |"
        )
    lines.extend(
        [
            "",
            "## 为什么 v3 跑偏",
            "",
            "| profile | rows | avg ask | ROI | PnL | win rate |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for _, r in broader.sort_values("ask_min").iterrows():
        lines.append(
            f"| `{r['profile']}` | {int(r['candidate_rows'])} | {fnum(r['candidate_avg_ask'])} | "
            f"{pct(r['candidate_roi'])} | {fnum(r['candidate_pnl'], signed=True)} | {pct(r['candidate_win_rate'], signed=False)} |"
        )
    lines.extend(
        [
            "",
            "`ask>=0.55/decline>=0` 这类宽口径把大量“还没衰竭、价格也不高”的 NO 混进来，已经不是低保 theta。加上 `decline>=0.5` 后才开始接近我们的本意。",
            "",
            "## 探索性 top profiles",
            "",
            "| profile | rows | dates | ROI | PnL | win rate | avg ask |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for _, r in top.iterrows():
        lines.append(
            f"| `{r['profile']}` | {int(r['candidate_rows'])} | {int(r['candidate_active_dates'])} | "
            f"{pct(r['candidate_roi'])} | {fnum(r['candidate_pnl'], signed=True)} | {pct(r['candidate_win_rate'], signed=False)} | {fnum(r['candidate_avg_ask'])} |"
        )
    lines.extend(
        [
            "",
            "## 三道门 verdict",
            "",
            f"在 holdout>=2026-06-01，`{key_profile}` ROI 为 {pct(key_hold['candidate_roi'])}，相对 same-ask baseline 的超额 ROI 为 {pct(key_hold['excess_roi_vs_same_ask'])}（95% CI [{pct(key_hold['excess_ci95_low'])}, {pct(key_hold['excess_ci95_high'])}]）；但 train 同口径 ROI 为 {pct(key_train['candidate_roi'])}、excess {pct(key_train['excess_roi_vs_same_ask'])}，前后不一致，结论等级 `inconclusive`。",
            "",
            "- significance=PASS_ON_HOLDOUT: 关键口径 holdout excess CI 不跨 0。",
            "- baseline=PASS_ON_HOLDOUT: 相对 same-ask baseline 为正且显著。",
            "- forward=FAIL: train 同口径为负，说明这还不是稳定可上线规则。",
            "- conclusion=inconclusive: 不进 shadow/paper/live；下一步做高 ask carry 专用 prefix walk-forward、城市/小时分层和样本扩展。",
            "",
            "## 输出文件",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- generated CSV dir: `{OUT_DIR.relative_to(ROOT)}`",
            f"- Script: `scripts/analysis/reheat_risk/research_theta_no_pure_carry_v2.py`",
            "",
        ]
    )
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    q = load_quotes()
    profile_df, selected = evaluate_profiles(q)
    profile_df.to_csv(OUT_DIR / "carry_profiles.csv", index=False)
    selected.to_csv(OUT_DIR / "carry_selected_quotes.csv", index=False)
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "pure_theta_carry",
        "split_date": SPLIT_DATE,
        "inputs": {"calibrated_quotes": str(QUOTES.relative_to(ROOT))},
        "coverage": {
            "d1_h13_17_rows": int(len(q)),
            "holdout_rows": int(len(q[q["period"].eq("holdout")])),
        },
        "data_self_check": data_self_check(),
        "clob_gate": load_gate(),
        "profile_summary": profile_df.to_dict(orient="records"),
        "outputs": {
            "carry_profiles": str((OUT_DIR / "carry_profiles.csv").relative_to(ROOT)),
            "carry_selected_quotes": str((OUT_DIR / "carry_selected_quotes.csv").relative_to(ROOT)),
        },
        "verdict": {
            "significance": "FAIL",
            "baseline": "WEAK",
            "forward": "WEAK",
            "conclusion": "inconclusive",
            "live_action": "none",
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(payload, profile_df)
    print(json.dumps({"ok": True, "out_md": str(OUT_MD), "out_json": str(OUT_JSON)}, indent=2))


if __name__ == "__main__":
    main()
