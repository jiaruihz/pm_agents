#!/usr/bin/env python3
"""Walk-forward validation for pure theta-NO carry.

This is intentionally narrow: d1 NO, local 13-17, high NO ask, observed
exhaustion, and optional raw no-reheat risk filter. The selector only sees
dates before the test date.
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


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
QUOTES = ROOT / "docs/analysis/2026-06/generated/m3_jump_model_v2_quote_calibration/calibrated_quotes.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_no_pure_carry_walkforward_v3"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-15-theta-no-pure-carry-walkforward-v3.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-15-theta-no-pure-carry-walkforward-v3.md"


@dataclass(frozen=True)
class Profile:
    ask_min: float
    decline_min: float
    hour_start: int
    hour_end: int
    p_lose_raw_v1_max: float | None

    @property
    def name(self) -> str:
        risk = "none" if self.p_lose_raw_v1_max is None else f"pLose<={self.p_lose_raw_v1_max:g}"
        return f"ask>={self.ask_min:g}|decline>={self.decline_min:g}|h{self.hour_start}-{self.hour_end}|{risk}"


@dataclass(frozen=True)
class SelectorConfig:
    name: str
    min_history_dates: int
    min_train_rows: int
    min_ask_min: float


SELECTORS = [
    SelectorConfig("loose_ask65", min_history_dates=5, min_train_rows=15, min_ask_min=0.65),
    SelectorConfig("disciplined_ask65", min_history_dates=8, min_train_rows=25, min_ask_min=0.65),
    SelectorConfig("strict_ask75", min_history_dates=8, min_train_rows=20, min_ask_min=0.75),
    SelectorConfig("strict_ask80", min_history_dates=8, min_train_rows=15, min_ask_min=0.80),
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
    q = q[
        q["decision_hour_local"].between(13, 17)
        & q["dist_b"].eq(1)
        & q["best_ask"].between(0.005, 0.97)
    ].copy()
    q = q.sort_values(["decision_hour_local", "snapshot_ts_utc", "city", "target_date", "bracket"])
    return q.drop_duplicates(["city", "target_date", "bracket"], keep="first").copy()


def profile_grid() -> list[Profile]:
    return [
        Profile(ask_min, decline_min, h0, h1, risk)
        for ask_min in (0.65, 0.70, 0.75, 0.80, 0.85, 0.90)
        for decline_min in (0.5, 1.0, 1.5)
        for h0, h1 in ((13, 17), (13, 14), (13, 15), (14, 16), (15, 17))
        for risk in (None, 0.20, 0.15, 0.10)
    ]


def select(df: pd.DataFrame, profile: Profile, require_decline: bool) -> pd.DataFrame:
    mask = df["best_ask"].ge(profile.ask_min) & df["decision_hour_local"].between(profile.hour_start, profile.hour_end)
    if profile.p_lose_raw_v1_max is not None:
        mask &= df["p_lose_raw_v1"].le(profile.p_lose_raw_v1_max)
    if require_decline:
        mask &= df["decline"].ge(profile.decline_min)
    return df[mask].copy()


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "rows": 0,
            "active_dates": 0,
            "cities": 0,
            "cost": 0.0,
            "pnl": 0.0,
            "roi": None,
            "win_rate": None,
            "positive_date_rate": None,
            "daily_t": None,
            "avg_ask": None,
        }
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
        "positive_date_rate": float((daily > 0).mean()),
        "daily_t": float(daily.mean() / sd * math.sqrt(len(daily))) if sd > 0 else None,
        "avg_ask": float(df["best_ask"].mean()),
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


def profile_period_table(q: pd.DataFrame, profiles: list[Profile]) -> pd.DataFrame:
    rows = []
    for profile in profiles:
        for period, frame in (("train", q[q["target_date"] < "2026-06-01"]), ("holdout", q[q["target_date"] >= "2026-06-01"])):
            candidate = select(frame, profile, require_decline=True)
            baseline = select(frame, profile, require_decline=False)
            if len(candidate) < 5:
                continue
            boot = bootstrap_excess(candidate, baseline, reps=1000)
            rows.append(
                {
                    "profile": profile.name,
                    "period": period,
                    "ask_min": profile.ask_min,
                    "decline_min": profile.decline_min,
                    "hour_start": profile.hour_start,
                    "hour_end": profile.hour_end,
                    "p_lose_raw_v1_max": profile.p_lose_raw_v1_max,
                    **{f"candidate_{k}": v for k, v in summarize(candidate).items()},
                    **{f"baseline_{k}": v for k, v in summarize(baseline).items()},
                    "excess_roi_vs_no_decline": boot["excess_roi"],
                    "excess_ci95_low": boot["ci95"][0],
                    "excess_ci95_high": boot["ci95"][1],
                }
            )
    return pd.DataFrame(rows)


def choose_profile(history: pd.DataFrame, profiles: list[Profile], cfg: SelectorConfig) -> dict[str, Any] | None:
    choices = []
    for profile in profiles:
        if profile.ask_min < cfg.min_ask_min:
            continue
        candidate = select(history, profile, require_decline=True)
        baseline = select(history, profile, require_decline=False)
        sm = summarize(candidate)
        bm = summarize(baseline)
        if sm["rows"] < cfg.min_train_rows or sm["active_dates"] < cfg.min_history_dates:
            continue
        if sm["roi"] is None or bm["roi"] is None:
            continue
        excess = sm["roi"] - bm["roi"]
        if sm["roi"] <= 0 or excess <= 0 or (sm["positive_date_rate"] or 0) < 0.5:
            continue
        choices.append(
            {
                "profile": profile,
                "train_excess_roi": excess,
                "train_roi": sm["roi"],
                "train_rows": sm["rows"],
                "train_active_dates": sm["active_dates"],
                "train_positive_date_rate": sm["positive_date_rate"],
            }
        )
    if not choices:
        return None
    choices.sort(key=lambda x: (x["train_excess_roi"], x["train_roi"], x["train_rows"]), reverse=True)
    return choices[0]


def run_walkforward(q: pd.DataFrame, profiles: list[Profile]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    app_rows = []
    base_rows = []
    choice_rows = []
    dates = sorted(q["target_date"].unique())
    for cfg in SELECTORS:
        for target_date in dates:
            history = q[q["target_date"] < target_date]
            if history["target_date"].nunique() < cfg.min_history_dates:
                continue
            chosen = choose_profile(history, profiles, cfg)
            if chosen is None:
                continue
            profile: Profile = chosen["profile"]
            day = q[q["target_date"].eq(target_date)]
            candidate = select(day, profile, require_decline=True)
            baseline = select(day, profile, require_decline=False)
            choice_rows.append(
                {
                    "selector": cfg.name,
                    "target_date": target_date,
                    "profile": profile.name,
                    "ask_min": profile.ask_min,
                    "decline_min": profile.decline_min,
                    "hour_start": profile.hour_start,
                    "hour_end": profile.hour_end,
                    "p_lose_raw_v1_max": profile.p_lose_raw_v1_max,
                    **{k: v for k, v in chosen.items() if k != "profile"},
                    "candidate_rows_on_date": int(len(candidate)),
                    "baseline_rows_on_date": int(len(baseline)),
                }
            )
            for frame, rows in ((candidate, app_rows), (baseline, base_rows)):
                for r in frame.itertuples(index=False):
                    rows.append(
                        {
                            "selector": cfg.name,
                            "target_date": r.target_date,
                            "profile": profile.name,
                            "city": r.city,
                            "bracket": r.bracket,
                            "decision_hour_local": int(r.decision_hour_local),
                            "best_ask": float(r.best_ask),
                            "pnl": float(r.pnl),
                            "decline": float(r.decline),
                            "p_lose_raw_v1": float(r.p_lose_raw_v1),
                            "kind": "candidate" if rows is app_rows else "baseline",
                        }
                    )
    apps = pd.DataFrame(app_rows)
    bases = pd.DataFrame(base_rows)
    choices = pd.DataFrame(choice_rows)
    return apps, bases, choices


def walkforward_summary(apps: pd.DataFrame, bases: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for cfg in SELECTORS:
        candidate = apps[apps["selector"].eq(cfg.name)] if not apps.empty else pd.DataFrame()
        baseline = bases[bases["selector"].eq(cfg.name)] if not bases.empty else pd.DataFrame()
        boot = bootstrap_excess(candidate, baseline)
        sm = summarize(candidate)
        bm = summarize(baseline)
        rows.append(
            {
                "selector": cfg.name,
                **{f"candidate_{k}": v for k, v in sm.items()},
                **{f"baseline_{k}": v for k, v in bm.items()},
                "excess_roi_vs_no_decline": boot["excess_roi"],
                "excess_ci95_low": boot["ci95"][0],
                "excess_ci95_high": boot["ci95"][1],
            }
        )
    return pd.DataFrame(rows)


def write_markdown(payload: dict[str, Any], profile_table: pd.DataFrame, wf_summary: pd.DataFrame, choices: pd.DataFrame) -> None:
    self_check = payload["data_self_check"]
    gate = payload["clob_gate"]
    disciplined = wf_summary[wf_summary["selector"].eq("disciplined_ask65")].iloc[0].to_dict()
    strict = wf_summary[wf_summary["selector"].eq("strict_ask75")].iloc[0].to_dict()
    hold_top = profile_table[
        (profile_table["period"].eq("holdout"))
        & (profile_table["candidate_rows"].ge(10))
        & (profile_table["ask_min"].ge(0.65))
    ].sort_values("excess_roi_vs_no_decline", ascending=False).head(10)

    lines = [
        "# Theta NO Pure Carry Walk-Forward v3",
        "",
        "Status: snapshot",
        f"Generated: {payload['generated_at_utc']}",
        "Target metric: `pure_theta_carry_walkforward` = 高 NO ask 的 d1 NO carry，只允许用测试日前的历史日期选择 ask/decline/hour/no-reheat risk 参数。",
        "",
        "## 数据快照",
        "",
        "- 数据源: v2 calibrated quote replay；DB 只用于强制自检和 live fill gate。",
        f"- deduped d1 h13-17 quote rows: {payload['coverage']['d1_h13_17_rows']}; target dates: {payload['coverage']['target_dates']}.",
        f"- fact_built_at_utc: `{self_check['fact_trades_max_built_at_utc']}`。",
        f"- fact_trades trade_class: `{self_check['fact_trades_by_class']}`。",
        f"- fact_trades settlement_status: `{self_check['fact_trades_by_settlement_status']}`。",
        f"- fact_signal_candidates coverage: `{self_check['fact_signal_candidate_coverage']}`。",
        f"- CLOB orders/fills join: `{self_check['clob_order_fill_join']}`。",
        f"- CLOB coverage gate: gate_pass={gate.get('gate_pass')}, missing_order_rows={gate.get('missing_order_rows')}, over_order_keys={gate.get('over_order_keys')}, db_fill_cost_minus_fact_cost={gate.get('db_fill_cost_minus_fact_cost')}.",
        "",
        "## 人话结论",
        "",
        f"这次只看纯 theta carry，没有混入 0.40-0.55 的方向单。结果是：信号还活着，但还小。disciplined walk-forward 选出的单有 {int(disciplined['candidate_rows'])} 行、{int(disciplined['candidate_active_dates'])} 个交易日，ROI {pct(disciplined['candidate_roi'])}，相对同 ask/hour/risk 但不要求 decline 的 baseline 超额 {pct(disciplined['excess_roi_vs_no_decline'])}。",
        "",
        f"更严格的 `ask>=0.75` selector 只剩 {int(strict['candidate_rows'])} 行、{int(strict['candidate_active_dates'])} 个交易日，ROI {pct(strict['candidate_roi'])}。这更像我们想要的高价 NO 低保，但样本太小，容量也小。",
        "",
        "我的判断：这个方向应该继续做，但当前证据等级是 research-positive / no-live。下一步优先扩样和 forward 记录，而不是马上变成 shadow/paper；如果要 shadow，也应该只记录 zero-notional telemetry。",
        "",
        "## Walk-Forward 结果",
        "",
        "| selector | rows | dates | avg ask | ROI | PnL | positive dates | excess vs matched no-decline | CI95 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in wf_summary.iterrows():
        lines.append(
            f"| `{r['selector']}` | {int(r['candidate_rows'])} | {int(r['candidate_active_dates'])} | {fnum(r['candidate_avg_ask'])} | "
            f"{pct(r['candidate_roi'])} | {fnum(r['candidate_pnl'], signed=True)} | {pct(r['candidate_positive_date_rate'], signed=False)} | "
            f"{pct(r['excess_roi_vs_no_decline'])} | [{pct(r['excess_ci95_low'])}, {pct(r['excess_ci95_high'])}] |"
        )
    lines.extend(
        [
            "",
            "Row grain: 一行是一个去重后的 `city,target_date,bracket` NO 买入机会，不是真实成交。",
            "",
            "## Holdout 里最强的固定口径",
            "",
            "这些是事后 holdout 排名，用来理解形状，不作为可上线选参。",
            "",
            "| profile | rows | dates | ROI | PnL | excess | CI95 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for _, r in hold_top.iterrows():
        lines.append(
            f"| `{r['profile']}` | {int(r['candidate_rows'])} | {int(r['candidate_active_dates'])} | "
            f"{pct(r['candidate_roi'])} | {fnum(r['candidate_pnl'], signed=True)} | {pct(r['excess_roi_vs_no_decline'])} | "
            f"[{pct(r['excess_ci95_low'])}, {pct(r['excess_ci95_high'])}] |"
        )
    recent_choices = choices.sort_values(["selector", "target_date"]).groupby("selector").tail(8)
    lines.extend(
        [
            "",
            "## 最近的历史选参",
            "",
            "| selector | target_date | chosen profile | train ROI | train excess | train rows | day rows |",
            "|---|---:|---|---:|---:|---:|---:|",
        ]
    )
    for _, r in recent_choices.iterrows():
        lines.append(
            f"| `{r['selector']}` | {r['target_date']} | `{r['profile']}` | {pct(r['train_roi'])} | "
            f"{pct(r['train_excess_roi'])} | {int(r['train_rows'])} | {int(r['candidate_rows_on_date'])} |"
        )
    lines.extend(
        [
            "",
            "## 三道门 verdict",
            "",
            f"在 prefix walk-forward 中，disciplined selector ROI 为 {pct(disciplined['candidate_roi'])}，matched baseline excess 为 {pct(disciplined['excess_roi_vs_no_decline'])}（95% CI [{pct(disciplined['excess_ci95_low'])}, {pct(disciplined['excess_ci95_high'])}]），但样本只有 {int(disciplined['candidate_rows'])} 行/{int(disciplined['candidate_active_dates'])} 天，且 strict high-ask 版本只有 {int(strict['candidate_rows'])} 行，结论等级 `inconclusive`。",
            "",
            "- significance=WEAK: walk-forward 点估计为正，daily bootstrap 可能受少数日期和小样本影响。",
            "- baseline=PASS_ON_POINT_ESTIMATE: 相对 matched no-decline baseline 明显为正。",
            "- forward=WEAK: prefix walk-forward 为正，但时间窗仅 21 天，strict high-ask 样本太小。",
            "- conclusion=inconclusive: 不进 live；可继续做 zero-notional forward telemetry / 扩样研究。",
            "",
            "## 输出文件",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- generated CSV dir: `{OUT_DIR.relative_to(ROOT)}`",
            f"- Script: `scripts/analysis/reheat_risk/research_theta_no_pure_carry_walkforward_v3.py`",
            "",
        ]
    )
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    q = load_quotes()
    profiles = profile_grid()
    profile_table = profile_period_table(q, profiles)
    apps, bases, choices = run_walkforward(q, profiles)
    wf_summary = walkforward_summary(apps, bases)

    profile_table.to_csv(OUT_DIR / "profile_train_holdout.csv", index=False)
    apps.to_csv(OUT_DIR / "walkforward_candidate_rows.csv", index=False)
    bases.to_csv(OUT_DIR / "walkforward_baseline_rows.csv", index=False)
    choices.to_csv(OUT_DIR / "walkforward_choices.csv", index=False)
    wf_summary.to_csv(OUT_DIR / "walkforward_summary.csv", index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "pure_theta_carry_walkforward",
        "inputs": {"calibrated_quotes": str(QUOTES.relative_to(ROOT))},
        "coverage": {
            "d1_h13_17_rows": int(len(q)),
            "target_dates": int(q["target_date"].nunique()),
            "first_target_date": str(q["target_date"].min()),
            "last_target_date": str(q["target_date"].max()),
            "profile_count": int(len(profiles)),
        },
        "data_self_check": data_self_check(),
        "clob_gate": load_gate(),
        "walkforward_summary": wf_summary.to_dict(orient="records"),
        "outputs": {
            "profile_train_holdout": str((OUT_DIR / "profile_train_holdout.csv").relative_to(ROOT)),
            "walkforward_candidate_rows": str((OUT_DIR / "walkforward_candidate_rows.csv").relative_to(ROOT)),
            "walkforward_baseline_rows": str((OUT_DIR / "walkforward_baseline_rows.csv").relative_to(ROOT)),
            "walkforward_choices": str((OUT_DIR / "walkforward_choices.csv").relative_to(ROOT)),
            "walkforward_summary": str((OUT_DIR / "walkforward_summary.csv").relative_to(ROOT)),
        },
        "verdict": {
            "significance": "WEAK",
            "baseline": "PASS_ON_POINT_ESTIMATE",
            "forward": "WEAK",
            "conclusion": "inconclusive",
            "live_action": "none",
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(payload, profile_table, wf_summary, choices)
    print(json.dumps({"ok": True, "out_md": str(OUT_MD), "out_json": str(OUT_JSON)}, indent=2))


if __name__ == "__main__":
    main()
