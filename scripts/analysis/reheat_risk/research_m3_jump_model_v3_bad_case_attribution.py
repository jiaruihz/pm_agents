#!/usr/bin/env python3
"""Bad-case attribution for pure theta-NO M3 jump model.

v1 showed the weather-path model can predict late-day bucket jumps. v2 showed
quote calibration improves Brier but train-selected trading rules still fail
walk-forward. This script asks a narrower question: for the most plausible d1
NO variants, where do holdout losses come from?
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
CALIBRATED_QUOTES = ROOT / "docs/analysis/2026-06/generated/m3_jump_model_v2_quote_calibration/calibrated_quotes.csv"
FEATURES = ROOT / "runtime/rule_source_research/m3_jump_model_v1_features.csv.gz"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/m3_jump_model_v3_bad_case_attribution"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-15-m3-jump-model-v3-bad-case-attribution.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-15-m3-jump-model-v3-bad-case-attribution.md"

SPLIT_DATE = "2026-06-01"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(x: float | None, signed: bool = True) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    sign = "+" if signed else ""
    return f"{float(x) * 100:{sign}.1f}%"


def fnum(x: float | None, digits: int = 2, signed: bool = False) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    sign = "+" if signed else ""
    return f"{float(x):{sign}.{digits}f}"


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
            "fact_trades_by_class": query_rows(
                conn,
                "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
            ),
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


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "rows": 0,
            "city_days": 0,
            "cities": 0,
            "active_dates": 0,
            "positive_dates": 0,
            "cost": 0.0,
            "pnl": 0.0,
            "roi": None,
            "win_rate": None,
            "avg_ask": None,
            "daily_t": None,
        }
    daily = df.groupby("target_date")["pnl"].sum()
    sd = float(daily.std(ddof=1)) if len(daily) > 1 else 0.0
    cost = float(df["best_ask"].sum())
    return {
        "rows": int(len(df)),
        "city_days": int(df[["city", "target_date"]].drop_duplicates().shape[0]),
        "cities": int(df["city"].nunique()),
        "active_dates": int(df["target_date"].nunique()),
        "positive_dates": int((daily > 0).sum()),
        "cost": cost,
        "pnl": float(df["pnl"].sum()),
        "roi": float(df["pnl"].sum() / cost) if cost else None,
        "win_rate": float((df["pnl"] > 0).mean()),
        "avg_ask": float(df["best_ask"].mean()),
        "daily_t": float(daily.mean() / sd * math.sqrt(len(daily))) if sd > 0 else None,
    }


def bootstrap_excess(selected: pd.DataFrame, baseline: pd.DataFrame, reps: int = 3000) -> dict[str, Any]:
    dates = sorted(set(selected["target_date"]) | set(baseline["target_date"]))
    if len(dates) < 3 or selected.empty or baseline.empty:
        return {"excess_roi": None, "ci95": [None, None], "reps": 0}

    def daily(frame: pd.DataFrame) -> pd.DataFrame:
        return frame.groupby("target_date").agg(cost=("best_ask", "sum"), pnl=("pnl", "sum")).reindex(dates).fillna(0.0)

    s = daily(selected)
    b = daily(baseline)

    def roi(frame: pd.DataFrame, idx: np.ndarray | None = None) -> float:
        work = frame if idx is None else frame.iloc[idx]
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


def load_joined_quotes() -> pd.DataFrame:
    q = pd.read_csv(CALIBRATED_QUOTES)
    q["target_date"] = q["target_date"].astype(str)
    q["period"] = np.where(q["target_date"] < SPLIT_DATE, "train", "holdout")
    q["price_bucket"] = pd.cut(
        q["best_ask"],
        [0.0, 0.40, 0.55, 0.70, 0.75, 0.85, 0.97, 1.01],
        labels=["0.00-0.40", "0.40-0.55", "0.55-0.70", "0.70-0.75", "0.75-0.85", "0.85-0.97", ">0.97"],
        include_lowest=True,
    ).astype(str)

    f = pd.read_csv(FEATURES)
    f["target_date"] = f["target_date"].astype(str)
    joined = q.merge(f, on=["city", "target_date", "decision_hour_local"], how="left", suffixes=("", "_feat"))
    joined["feature_joined"] = joined["jump_b"].notna()
    return joined


def base_universe(df: pd.DataFrame) -> pd.DataFrame:
    base = df[
        df["decision_hour_local"].between(13, 17)
        & df["dist_b"].eq(1)
        & df["best_ask"].le(0.75)
        & df["best_ask"].between(0.005, 0.97)
    ].copy()
    return dedupe(base)


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.sort_values(["decision_hour_local", "snapshot_ts_utc", "city", "target_date", "bracket"])
        .drop_duplicates(["city", "target_date", "bracket"], keep="first")
        .copy()
    )


def selected_variants(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    base = df[
        df["decision_hour_local"].between(13, 17)
        & df["dist_b"].eq(1)
        & df["best_ask"].le(0.75)
        & df["best_ask"].between(0.005, 0.97)
    ].copy()
    return {
        "raw_v1_ev08_d1_h13_17_ask075": dedupe(base[base["ev_raw_v1"].ge(0.08)]),
        "iso_dist_ev08_d1_h13_17_ask075": dedupe(base[base["ev_iso_dist"].ge(0.08)]),
    }


def summarize_by_period(name: str, selected: pd.DataFrame, baseline: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for period in ("train", "holdout"):
        sel = selected[selected["period"].eq(period)]
        base = baseline[baseline["period"].eq(period)]
        s = summarize(sel)
        b = summarize(base)
        boot = bootstrap_excess(sel, base)
        out.append(
            {
                "variant": name,
                "period": period,
                **{f"selected_{k}": v for k, v in s.items()},
                **{f"baseline_{k}": v for k, v in b.items()},
                "excess_roi_vs_all_d1_ask075": boot["excess_roi"],
                "excess_ci95_low": boot["ci95"][0],
                "excess_ci95_high": boot["ci95"][1],
            }
        )
    return out


def grouped_summary(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    rows = []
    for key, g in df.groupby(cols, dropna=False):
        if not isinstance(key, tuple):
            key = (key,)
        item = {col: val for col, val in zip(cols, key)}
        item.update(summarize(g))
        rows.append(item)
    return pd.DataFrame(rows).sort_values(["pnl", "cost"], ascending=[True, False])


def weather_contrast(df: pd.DataFrame) -> pd.DataFrame:
    features = [
        "best_ask",
        "ev_raw_v1",
        "ev_iso_dist",
        "p_lose_raw_v1",
        "p_lose_iso_dist",
        "decline",
        "jump_c",
        "decline_b",
        "gap_cur_to_thresh_b",
        "d1h_b",
        "d2h_b",
        "d3h_b",
        "hours_since_max",
        "rise_rate_b",
        "day_range_b",
        "dep_f",
        "relh_now",
        "sknt_now",
        "sky_now",
        "d_dwpf_3h_f",
        "d_relh_3h",
    ]
    rows = []
    for name, g in df.groupby("variant"):
        hold = g[g["period"].eq("holdout")].copy()
        if hold.empty:
            continue
        winners = hold[hold["pnl"] > 0]
        losers = hold[hold["pnl"] <= 0]
        for col in features:
            if col not in hold.columns:
                continue
            rows.append(
                {
                    "variant": name,
                    "feature": col,
                    "winner_mean": float(winners[col].mean()) if not winners.empty else None,
                    "loser_mean": float(losers[col].mean()) if not losers.empty else None,
                    "loser_minus_winner": float(losers[col].mean() - winners[col].mean())
                    if not winners.empty and not losers.empty
                    else None,
                    "non_null": int(hold[col].notna().sum()),
                }
            )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["variant", "feature"])


def path_bucket_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    work = df.copy()
    work["jump_bin"] = pd.cut(
        work["jump_c"],
        [-0.1, 0.0, 0.5, 1.0, 2.0, 99.0],
        labels=["0", "0-0.5", "0.5-1", "1-2", "2+"],
        include_lowest=True,
    ).astype(str)
    rows = []
    for bucket_type, col in [
        ("decision_hour_local", "decision_hour_local"),
        ("price_bucket", "price_bucket"),
        ("decline_bucket", "decline_bucket"),
        ("jump_bin_ex_post", "jump_bin"),
    ]:
        gsum = grouped_summary(work, ["variant", col])
        if gsum.empty:
            continue
        gsum = gsum.rename(columns={col: "bucket_value"})
        gsum.insert(1, "bucket_type", bucket_type)
        rows.append(gsum)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def loss_rows(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "variant",
        "target_date",
        "city",
        "decision_hour_local",
        "bracket",
        "winner_label",
        "best_ask",
        "pnl",
        "ev_raw_v1",
        "ev_iso_dist",
        "p_lose_raw_v1",
        "p_lose_iso_dist",
        "running_max_c",
        "current_temp_c",
        "final_max_c",
        "decline",
        "jump_c",
        "decline_b",
        "gap_cur_to_thresh_b",
        "d1h_b",
        "d2h_b",
        "dep_f",
        "relh_now",
        "sknt_now",
        "sky_now",
    ]
    have = [c for c in cols if c in df.columns]
    return df[df["period"].eq("holdout") & df["pnl"].le(0)][have].sort_values(["pnl", "best_ask"]).head(80)


def top_plain_findings(
    summary_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    city_df: pd.DataFrame,
    bucket_df: pd.DataFrame,
    contrast_df: pd.DataFrame,
) -> list[str]:
    findings: list[str] = []
    hold = summary_df[summary_df["period"].eq("holdout")].copy()
    if not hold.empty:
        raw = hold[hold["variant"].eq("raw_v1_ev08_d1_h13_17_ask075")]
        iso = hold[hold["variant"].eq("iso_dist_ev08_d1_h13_17_ask075")]
        if not raw.empty and not iso.empty:
            r = raw.iloc[0]
            i = iso.iloc[0]
            findings.append(
                "raw_v1 这个未校准版本在 holdout 反而比 isotonic 版本好："
                f"raw_v1 ROI {pct(r['selected_roi'])}，iso_dist ROI {pct(i['selected_roi'])}。"
                "这说明 v2 的校准改善了概率评分，但没有改善交易选择。"
            )
    raw_daily = daily_df[daily_df["variant"].eq("raw_v1_ev08_d1_h13_17_ask075")]
    if not raw_daily.empty:
        worst = raw_daily.sort_values("pnl").iloc[0]
        findings.append(
            f"raw_v1 的 holdout 盈亏不是每天稳定小赚，而是被单日冲击主导：最差日 {worst['target_date']} "
            f"PnL {fnum(worst['pnl'], signed=True)}，当天 ROI {pct(worst['roi'])}。"
        )
    raw_city = city_df[city_df["variant"].eq("raw_v1_ev08_d1_h13_17_ask075")]
    if not raw_city.empty:
        worst_city = raw_city.sort_values("pnl").iloc[0]
        findings.append(
            f"城市上也有集中度：raw_v1 最大亏损城市是 {worst_city['city']}，"
            f"holdout PnL {fnum(worst_city['pnl'], signed=True)}，ROI {pct(worst_city['roi'])}。"
        )
    raw_price = bucket_df[
        bucket_df["variant"].eq("raw_v1_ev08_d1_h13_17_ask075") & bucket_df["bucket_type"].eq("price_bucket")
    ]
    if not raw_price.empty:
        worst_bucket = raw_price.sort_values("pnl").iloc[0]
        findings.append(
            f"价格段里最拖累 raw_v1 的是 ask {worst_bucket['bucket_value']}，"
            f"PnL {fnum(worst_bucket['pnl'], signed=True)}，ROI {pct(worst_bucket['roi'])}；"
            "这不是纯 theta carry 的目标价位，而是 EV selector 把中低价方向单混进来了。"
        )
    raw_hour = bucket_df[
        bucket_df["variant"].eq("raw_v1_ev08_d1_h13_17_ask075") & bucket_df["bucket_type"].eq("decision_hour_local")
    ]
    if not raw_hour.empty:
        worst_hour = raw_hour.sort_values("pnl").iloc[0]
        findings.append(
            f"时间上，raw_v1 最差的是 local {worst_hour['bucket_value']} 点，"
            f"PnL {fnum(worst_hour['pnl'], signed=True)}，ROI {pct(worst_hour['roi'])}；13-14 点反而是正的。"
        )
    raw_decline = bucket_df[
        bucket_df["variant"].eq("raw_v1_ev08_d1_h13_17_ask075") & bucket_df["bucket_type"].eq("decline_bucket")
    ]
    if not raw_decline.empty:
        bad = raw_decline[raw_decline["bucket_value"].eq("<0.5")]
        good = raw_decline[raw_decline["bucket_value"].eq("1.0-2.0")]
        if not bad.empty and not good.empty:
            findings.append(
                f"最关键的事前信号仍是“真的衰竭了吗”：decline<0.5°C 的 raw_v1 holdout ROI {pct(bad.iloc[0]['roi'])}，"
                f"decline 1-2°C 的 ROI {pct(good.iloc[0]['roi'])}，但后者只有 {int(good.iloc[0]['rows'])} 行。"
            )
    raw_jump = bucket_df[
        bucket_df["variant"].eq("raw_v1_ev08_d1_h13_17_ask075") & bucket_df["bucket_type"].eq("jump_bin_ex_post")
    ]
    if not raw_jump.empty:
        danger = raw_jump[raw_jump["bucket_value"].eq("0.5-1")]
        skip = raw_jump[raw_jump["bucket_value"].eq("1-2")]
        if not danger.empty and not skip.empty:
            findings.append(
                f"事后机制很清楚：d1 NO 最怕刚好再升 0.5-1°C，raw_v1 在这桶 PnL {fnum(danger.iloc[0]['pnl'], signed=True)}；"
                f"再升 1-2°C 反而 PnL {fnum(skip.iloc[0]['pnl'], signed=True)}，因为很多时候会跳过所买的下一档。"
            )
    return findings


def write_markdown(payload: dict[str, Any]) -> None:
    summary = pd.DataFrame(payload["candidate_summary"])
    daily = pd.read_csv(OUT_DIR / "holdout_daily.csv")
    city = pd.read_csv(OUT_DIR / "holdout_city.csv")
    bucket = pd.read_csv(OUT_DIR / "holdout_path_buckets.csv")
    contrast = pd.read_csv(OUT_DIR / "weather_contrast.csv")
    findings = payload["plain_findings"]

    def row_for(variant: str, period: str) -> dict[str, Any]:
        r = summary[(summary["variant"] == variant) & (summary["period"] == period)]
        return {} if r.empty else r.iloc[0].to_dict()

    raw_hold = row_for("raw_v1_ev08_d1_h13_17_ask075", "holdout")
    iso_hold = row_for("iso_dist_ev08_d1_h13_17_ask075", "holdout")

    self_check = payload["data_self_check"]
    gate = payload["clob_gate"]
    coverage = payload["coverage"]

    lines = [
        "# M3 Jump Model v3 Bad-Case Attribution",
        "",
        "Status: snapshot",
        f"Generated: {payload['generated_at_utc']}",
        "Target metric: `theta_no_bad_case_attribution` = source-aligned pure theta NO 中，d1/13-17h/ask<=0.75/EV>=0.08 候选在 holdout 亏损来自哪些日期、城市、价格段和天气路径。",
        "",
        "## 数据快照",
        "",
        "- 数据源: v2 calibrated quote replay + v1 METAR path feature cache；DB 只用于强制自检和 live fill gate。",
        f"- quote rows: {coverage['quote_rows']}; feature rows: {coverage['feature_rows']}; joined feature coverage: {coverage['feature_join_rate']:.1%}。",
        f"- fact_built_at_utc: `{self_check['fact_trades_max_built_at_utc']}`。",
        f"- fact_trades trade_class: `{self_check['fact_trades_by_class']}`。",
        f"- fact_trades settlement_status: `{self_check['fact_trades_by_settlement_status']}`。",
        f"- fact_signal_candidates coverage: `{self_check['fact_signal_candidate_coverage']}`。",
        f"- CLOB orders/fills join: `{self_check['clob_order_fill_join']}`。",
        f"- CLOB coverage gate: gate_pass={gate.get('gate_pass')}, missing_order_rows={gate.get('missing_order_rows')}, over_order_keys={gate.get('over_order_keys')}, db_fill_cost_minus_fact_cost={gate.get('db_fill_cost_minus_fact_cost')}.",
        "",
        "## 人话结论",
        "",
        "这轮没有证明“现在可以开 theta NO”，但把问题说清楚了一层：天气模型确实能更好地判断会不会再升温；真正坏掉的是“把这个概率变成可交易报价”的选择层，尤其是校准后选出来的单不赚钱。",
        "",
    ]
    for item in findings:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "我的当前判断：继续研究是值得的，但 v3 暴露的是口径污染：0.40-0.55 这类中低价 NO 本来就不是“低保 theta”，真正 theta carry 应该回到高 NO ask + 明确衰竭 + no-reheat 风险排除，并用日期 walk-forward 单独验证。",
            "",
            "## 候选表现",
            "",
            "| variant | holdout rows | active dates | ROI | PnL | win rate | excess vs all d1 ask<=0.75 | CI95 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for variant, row in [
        ("raw_v1_ev08_d1_h13_17_ask075", raw_hold),
        ("iso_dist_ev08_d1_h13_17_ask075", iso_hold),
    ]:
        lines.append(
            f"| `{variant}` | {int(row.get('selected_rows', 0))} | {int(row.get('selected_active_dates', 0))} | "
            f"{pct(row.get('selected_roi'))} | {fnum(row.get('selected_pnl'), signed=True)} | "
            f"{pct(row.get('selected_win_rate'), signed=False)} | {pct(row.get('excess_roi_vs_all_d1_ask075'))} | "
            f"[{pct(row.get('excess_ci95_low'))}, {pct(row.get('excess_ci95_high'))}] |"
        )
    lines.extend(
        [
            "",
            "Row grain: 一行是一个去重后的 `city,target_date,bracket` NO 买入机会，不是一笔真实成交，也不是 city-day basket。",
            "",
            "## 亏损来源",
            "",
            "### 最差日期",
            "",
            "| variant | target_date | rows | ROI | PnL | cities |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for _, r in daily.sort_values(["variant", "pnl"]).groupby("variant").head(5).iterrows():
        lines.append(
            f"| `{r['variant']}` | {r['target_date']} | {int(r['rows'])} | {pct(r['roi'])} | {fnum(r['pnl'], signed=True)} | {int(r['cities'])} |"
        )
    lines.extend(
        [
            "",
            "### 最差城市",
            "",
            "| variant | city | rows | active dates | ROI | PnL |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for _, r in city.sort_values(["variant", "pnl"]).groupby("variant").head(8).iterrows():
        lines.append(
            f"| `{r['variant']}` | {r['city']} | {int(r['rows'])} | {int(r['active_dates'])} | {pct(r['roi'])} | {fnum(r['pnl'], signed=True)} |"
        )
    lines.extend(
        [
            "",
            "### 价格段",
            "",
            "| variant | ask bucket | rows | ROI | PnL | win rate |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    price_rows = bucket[bucket["bucket_type"].eq("price_bucket")].copy()
    for _, r in price_rows.sort_values(["variant", "pnl"]).iterrows():
        lines.append(
            f"| `{r['variant']}` | {r['bucket_value']} | {int(r['rows'])} | {pct(r['roi'])} | {fnum(r['pnl'], signed=True)} | {pct(r['win_rate'], signed=False)} |"
        )
    lines.extend(
        [
            "",
            "### 路径分桶",
            "",
            "`jump_bin_ex_post` 是事后归因，不能当事前过滤器；它用来解释 d1 NO 为什么会输。",
            "",
            "| variant | bucket type | bucket | rows | ROI | PnL | win rate |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    path_rows = bucket[bucket["bucket_type"].isin(["decision_hour_local", "decline_bucket", "jump_bin_ex_post"])].copy()
    for _, r in path_rows.sort_values(["variant", "bucket_type", "pnl"]).iterrows():
        lines.append(
            f"| `{r['variant']}` | `{r['bucket_type']}` | {r['bucket_value']} | {int(r['rows'])} | "
            f"{pct(r['roi'])} | {fnum(r['pnl'], signed=True)} | {pct(r['win_rate'], signed=False)} |"
        )
    lines.extend(
        [
            "",
            "## 天气路径差异",
            "",
            "下表的 `loser-winner` 是 holdout 亏损单均值减盈利单均值；正数表示亏损单更高。",
            "",
            "| variant | feature | winner mean | loser mean | loser-winner |",
            "|---|---|---:|---:|---:|",
        ]
    )
    show_features = [
        "best_ask",
        "jump_c",
        "decline",
        "gap_cur_to_thresh_b",
        "d1h_b",
        "d2h_b",
        "hours_since_max",
        "dep_f",
        "relh_now",
        "sknt_now",
        "sky_now",
    ]
    for _, r in contrast[contrast["feature"].isin(show_features)].iterrows():
        lines.append(
            f"| `{r['variant']}` | `{r['feature']}` | {fnum(r['winner_mean'])} | {fnum(r['loser_mean'])} | {fnum(r['loser_minus_winner'], signed=True)} |"
        )
    lines.extend(
        [
            "",
            "## 三道门 verdict",
            "",
            f"在 holdout>=2026-06-01，raw_v1 d1/h13-17/ask<=0.75/EV>=0.08 相对 all-d1 ask<=0.75 baseline 的超额 ROI 为 {pct(raw_hold.get('excess_roi_vs_all_d1_ask075'))}（95% CI [{pct(raw_hold.get('excess_ci95_low'))}, {pct(raw_hold.get('excess_ci95_high'))}]），前瞻 FAIL，结论等级 `inconclusive`。",
            "",
            "- significance=FAIL: holdout excess CI 跨 0。",
            "- baseline=FAIL: raw_v1 虽有正点估计，但相对同价/同距离 baseline 不稳；iso_dist 直接为负。",
            "- forward=FAIL: v2 prefix walk-forward 为负，本 v3 没有反转这个结论。",
            "- conclusion=inconclusive: 不建议 shadow/paper/live；下一步只做研究脚本层的分层验证。",
            "",
            "## 输出文件",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- generated CSV dir: `{OUT_DIR.relative_to(ROOT)}`",
            f"- Script: `scripts/analysis/reheat_risk/research_m3_jump_model_v3_bad_case_attribution.py`",
            "",
        ]
    )
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    q = load_joined_quotes()
    baseline = base_universe(q)
    variants = selected_variants(q)

    selected_frames = []
    candidate_summary = []
    for name, selected in variants.items():
        selected = selected.copy()
        selected["variant"] = name
        selected_frames.append(selected)
        candidate_summary.extend(summarize_by_period(name, selected, baseline))

    selected_all = pd.concat(selected_frames, ignore_index=True) if selected_frames else pd.DataFrame()
    holdout = selected_all[selected_all["period"].eq("holdout")].copy()

    daily = grouped_summary(holdout, ["variant", "target_date"])
    city = grouped_summary(holdout, ["variant", "city"])
    bucket = path_bucket_summary(holdout)
    contrast = weather_contrast(selected_all)
    losses = loss_rows(selected_all)

    summary_df = pd.DataFrame(candidate_summary)
    summary_df.to_csv(OUT_DIR / "candidate_summary.csv", index=False)
    daily.to_csv(OUT_DIR / "holdout_daily.csv", index=False)
    city.to_csv(OUT_DIR / "holdout_city.csv", index=False)
    bucket.to_csv(OUT_DIR / "holdout_path_buckets.csv", index=False)
    contrast.to_csv(OUT_DIR / "weather_contrast.csv", index=False)
    losses.to_csv(OUT_DIR / "holdout_loss_rows.csv", index=False)
    selected_all.to_csv(OUT_DIR / "selected_quotes.csv", index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "theta_no_bad_case_attribution",
        "split_date": SPLIT_DATE,
        "inputs": {
            "calibrated_quotes": str(CALIBRATED_QUOTES.relative_to(ROOT)),
            "features": str(FEATURES.relative_to(ROOT)),
        },
        "coverage": {
            "quote_rows": int(len(q)),
            "feature_rows": int(pd.read_csv(FEATURES, usecols=["city"]).shape[0]),
            "feature_join_rate": float(q["feature_joined"].mean()),
            "baseline_rows": int(len(baseline)),
            "baseline_holdout_rows": int(len(baseline[baseline["period"].eq("holdout")])),
        },
        "data_self_check": data_self_check(),
        "clob_gate": load_gate(),
        "candidate_summary": candidate_summary,
        "plain_findings": top_plain_findings(summary_df, daily, city, bucket, contrast),
        "outputs": {
            "candidate_summary": str((OUT_DIR / "candidate_summary.csv").relative_to(ROOT)),
            "holdout_daily": str((OUT_DIR / "holdout_daily.csv").relative_to(ROOT)),
            "holdout_city": str((OUT_DIR / "holdout_city.csv").relative_to(ROOT)),
            "holdout_path_buckets": str((OUT_DIR / "holdout_path_buckets.csv").relative_to(ROOT)),
            "weather_contrast": str((OUT_DIR / "weather_contrast.csv").relative_to(ROOT)),
            "holdout_loss_rows": str((OUT_DIR / "holdout_loss_rows.csv").relative_to(ROOT)),
            "selected_quotes": str((OUT_DIR / "selected_quotes.csv").relative_to(ROOT)),
        },
        "verdict": {
            "significance": "FAIL",
            "baseline": "FAIL",
            "forward": "FAIL",
            "conclusion": "inconclusive",
            "live_action": "none",
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(payload)
    print(json.dumps({"ok": True, "out_md": str(OUT_MD), "out_json": str(OUT_JSON)}, indent=2))


if __name__ == "__main__":
    main()
