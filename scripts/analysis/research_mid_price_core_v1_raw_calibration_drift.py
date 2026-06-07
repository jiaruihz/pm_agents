#!/usr/bin/env python3
"""Raw probability calibration drift and code-change checks for v1_25_75."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "runtime" / "weather.db"
OUT_DIR = ROOT / "docs" / "analysis" / "2026-06"
OUT_MD = OUT_DIR / "2026-06-07-mid-price-core-v1-raw-calibration-drift.md"
OUT_JSON = OUT_DIR / "2026-06-07-mid-price-core-v1-raw-calibration-drift.json"

RECENT_START = "2026-06-01"
STRATEGY_ID = "live_weather_edge_v1_4ef9b3ec3e2e"
STRATEGY_LABEL = "mid_price_core_v1_25_75"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetchall(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def pct(x: Any) -> str:
    if x is None:
        return ""
    return f"{float(x) * 100:.1f}%"


def money(x: Any) -> str:
    if x is None:
        return ""
    return f"{float(x):+.2f}"


def num(x: Any, digits: int = 3) -> str:
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except TypeError:
        pass
    return f"{float(x):.{digits}f}"


def table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    if not rows:
        return "_No rows._"
    cols = columns or list(rows[0].keys())
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")
    return "\n".join(out)


def period_of(date: str) -> str:
    return "post_2026_06_01" if date >= RECENT_START else "pre_2026_06_01"


def side_prob(side: str, model_p_yes: float) -> float:
    return model_p_yes if side == "BUY_YES" else 1.0 - model_p_yes


def load_trades(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT
          fill_id,
          execution_id,
          strategy_id,
          strategy_name,
          run_id,
          code_version,
          execution_policy,
          entry_price_window,
          trade_class,
          city,
          side,
          model_version,
          target_date,
          bracket,
          condition_id,
          market_id,
          snapshot_ts_utc,
          hours_to_settle,
          model_p_yes,
          market_price,
          edge,
          abs_edge,
          fill_price,
          cost_usd,
          pnl_usd_at_fill,
          win_by_count,
          final_yes,
          fact_built_at_utc
        FROM fact_trades
        WHERE trade_class='live_real'
          AND settlement_status='settled'
          AND strategy_id=?
          AND execution_policy='mid_price_core_v1'
          AND entry_price_window='0.25-0.75'
        """,
        conn,
        params=(STRATEGY_ID,),
    )
    if df.empty:
        return df
    df["period"] = df["target_date"].map(period_of)
    df["market_yes_price"] = df.apply(
        lambda r: float(r["market_price"]) if r["side"] == "BUY_YES" else 1.0 - float(r["market_price"]),
        axis=1,
    )
    df["side_p_raw"] = df.apply(lambda r: side_prob(str(r["side"]), float(r["model_p_yes"])), axis=1)
    df["side_p_market"] = df.apply(lambda r: side_prob(str(r["side"]), float(r["market_yes_price"])), axis=1)
    df["raw_edge_at_fill"] = df["side_p_raw"] - df["fill_price"]
    df["raw_vs_market_side_advantage"] = df["side_p_raw"] - df["side_p_market"]
    df["abs_yes_divergence"] = (df["model_p_yes"] - df["market_yes_price"]).abs()
    df["snapshot_hour_utc"] = pd.to_datetime(df["snapshot_ts_utc"], utc=True, errors="coerce").dt.hour
    df["raw_edge_bin"] = pd.cut(
        df["raw_edge_at_fill"],
        bins=[-10, 0.10, 0.15, 0.25, 10],
        labels=["<=0.10", "0.10-0.15", "0.15-0.25", ">0.25"],
    ).astype(str)
    df["side_p_bin"] = pd.cut(
        df["side_p_raw"],
        bins=[-0.001, 0.45, 0.55, 0.65, 0.75, 0.85, 1.001],
        labels=["<=0.45", "0.45-0.55", "0.55-0.65", "0.65-0.75", "0.75-0.85", ">0.85"],
    ).astype(str)
    df["yes_p_bin"] = pd.cut(
        df["model_p_yes"],
        bins=[-0.001, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 1.001],
        labels=["<=0.15", "0.15-0.25", "0.25-0.35", "0.35-0.45", "0.45-0.55", "0.55-0.65", "0.65-0.75", "0.75-0.85", ">0.85"],
    ).astype(str)
    return df


def load_candidates(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT
          candidate_id,
          condition_id,
          market_id,
          side,
          event_date AS target_date,
          bracket,
          city,
          city_pool,
          model_version,
          decision_hours_to_settle,
          decision_snapshot_ts_utc,
          decision_window_missing,
          model_p_yes,
          market_yes_price,
          edge,
          abs_edge,
          decision_entry_price,
          n_snapshots,
          edge_max,
          edge_mean,
          eligible,
          paper_ordered,
          live_filled,
          final_yes,
          win_by_count,
          counterfactual_pnl
        FROM fact_signal_candidates
        WHERE eligible=1
          AND final_yes IS NOT NULL
          AND decision_window_missing=0
          AND decision_entry_price BETWEEN 0.25 AND 0.75
        """,
        conn,
    )
    if df.empty:
        return df
    df["period"] = df["target_date"].map(period_of)
    df["side_p_raw"] = df.apply(lambda r: side_prob(str(r["side"]), float(r["model_p_yes"])), axis=1)
    df["raw_edge_proxy"] = df["side_p_raw"] - df["decision_entry_price"]
    df = df[df["raw_edge_proxy"] >= 0.10].copy()
    df["raw_edge_bin"] = pd.cut(
        df["raw_edge_proxy"],
        bins=[-10, 0.10, 0.15, 0.25, 10],
        labels=["<=0.10", "0.10-0.15", "0.15-0.25", ">0.25"],
    ).astype(str)
    df["side_p_bin"] = pd.cut(
        df["side_p_raw"],
        bins=[-0.001, 0.45, 0.55, 0.65, 0.75, 0.85, 1.001],
        labels=["<=0.45", "0.45-0.55", "0.55-0.65", "0.65-0.75", "0.75-0.85", ">0.85"],
    ).astype(str)
    return df


def perf_summary(df: pd.DataFrame) -> dict[str, Any]:
    n = int(len(df))
    cost = float(df["cost_usd"].sum()) if n else 0.0
    pnl = float(df["pnl_usd_at_fill"].sum()) if n else 0.0
    return {
        "fills": n,
        "days": int(df["target_date"].nunique()) if n else 0,
        "cost_usd": round(cost, 4),
        "pnl_usd": round(pnl, 4),
        "roi": None if not cost else round(pnl / cost, 6),
        "win_rate": None if not n else round(float((df["pnl_usd_at_fill"] > 0).mean()), 6),
        "avg_side_p_raw": None if not n else round(float(df["side_p_raw"].mean()), 6),
        "avg_raw_edge": None if not n else round(float(df["raw_edge_at_fill"].mean()), 6),
        "avg_abs_yes_divergence": None if not n else round(float(df["abs_yes_divergence"].mean()), 6),
    }


def group_perf(df: pd.DataFrame, keys: list[str], min_n: int = 1) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, g in df.groupby(keys, dropna=False):
        if len(g) < min_n:
            continue
        if not isinstance(key, tuple):
            key = (key,)
        row = {name: "" if pd.isna(value) else str(value) for name, value in zip(keys, key)}
        row.update(perf_summary(g))
        rows.append(row)
    return rows


def candidate_summary(df: pd.DataFrame, keys: list[str], min_n: int = 1) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, g in df.groupby(keys, dropna=False):
        if len(g) < min_n:
            continue
        if not isinstance(key, tuple):
            key = (key,)
        cf_cost = float((g["decision_entry_price"] * 1.0).sum())
        row = {name: "" if pd.isna(value) else str(value) for name, value in zip(keys, key)}
        row.update(
            {
                "eligible": int(len(g)),
                "ordered": int(g["paper_ordered"].sum()),
                "live_filled": int(g["live_filled"].sum()),
                "win_rate": round(float(g["win_by_count"].mean()), 6),
                "cf_pnl": round(float(g["counterfactual_pnl"].sum()), 4),
                "avg_side_p_raw": round(float(g["side_p_raw"].mean()), 6),
                "avg_raw_edge_proxy": round(float(g["raw_edge_proxy"].mean()), 6),
                "avg_entry": round(float(g["decision_entry_price"].mean()), 6),
                "cf_roi_proxy": None if not cf_cost else round(float(g["counterfactual_pnl"].sum()) / cf_cost, 6),
            }
        )
        rows.append(row)
    return rows


def fmt_perf(rows: list[dict[str, Any]], cols: list[str]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        r = dict(row)
        for c in ["cost_usd", "pnl_usd", "avg_side_p_raw", "avg_raw_edge", "avg_abs_yes_divergence"]:
            if c in r:
                r[c] = num(r[c], 3 if c.startswith("avg") else 2)
        for c in ["roi", "win_rate"]:
            if c in r:
                r[c] = pct(r[c])
        out.append({c: r.get(c, "") for c in cols})
    return out


def fmt_candidate(rows: list[dict[str, Any]], cols: list[str]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        r = dict(row)
        for c in ["cf_pnl", "avg_side_p_raw", "avg_raw_edge_proxy", "avg_entry"]:
            if c in r:
                r[c] = num(r[c], 3 if c.startswith("avg") else 2)
        for c in ["win_rate", "cf_roi_proxy"]:
            if c in r:
                r[c] = pct(r[c])
        out.append({c: r.get(c, "") for c in cols})
    return out


def code_log() -> list[dict[str, str]]:
    paths = [
        "scripts/ops/weather_snapshot_signal_builder.py",
        "src/strategies/weather_edge_v1/tools/execution_policy.py",
        "src/strategies/weather_edge_v1/tools/execution_pipeline.py",
        "scripts/ops/weather_live_cycle.py",
        "scripts/ops/weather_order_executor.py",
        "scripts/analysis/build_weather_fact_trades.py",
        "scripts/analysis/build_weather_signal_candidates.py",
    ]
    cmd = [
        "git",
        "log",
        "--date=short",
        "--pretty=format:%h%x09%ad%x09%s",
        "--since=2026-05-24",
        "--until=2026-06-03",
        "--",
        *paths,
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
    rows = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            rows.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
    return rows[:30]


def self_checks(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "db_path": str(DB_PATH),
        "db_mtime_utc": dt.datetime.fromtimestamp(DB_PATH.stat().st_mtime, tz=dt.timezone.utc).isoformat(),
        "fact_built_at_utc": fetchall(conn, "SELECT MAX(fact_built_at_utc) AS v FROM fact_trades")[0]["v"],
        "trade_class": fetchall(conn, "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class"),
        "settlement_status": fetchall(
            conn,
            "SELECT COALESCE(settlement_status, '[NULL]') AS settlement_status, COUNT(*) AS rows FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
        ),
        "signal_candidates": fetchall(
            conn,
            "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, SUM(live_filled) AS live_filled FROM fact_signal_candidates",
        )[0],
    }


def code_breaks(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "by_target_date_code": fetchall(
            conn,
            """
            SELECT target_date, COALESCE(code_version,'[NULL]') AS code_version, COUNT(*) AS fills,
                   COUNT(DISTINCT run_id) AS runs,
                   ROUND(AVG(model_p_yes),4) AS avg_model_p_yes,
                   ROUND(AVG(market_price),4) AS avg_market_price,
                   ROUND(AVG(edge),4) AS avg_fact_edge,
                   ROUND(SUM(pnl_usd_at_fill),2) AS pnl_usd
            FROM fact_trades
            WHERE trade_class='live_real' AND settlement_status='settled'
              AND strategy_id=? AND execution_policy='mid_price_core_v1'
              AND entry_price_window='0.25-0.75'
            GROUP BY target_date, code_version
            ORDER BY target_date, code_version
            """,
            (STRATEGY_ID,),
        ),
        "by_code_period": fetchall(
            conn,
            """
            SELECT CASE WHEN target_date >= '2026-06-01' THEN 'post_2026_06_01' ELSE 'pre_2026_06_01' END AS period,
                   COALESCE(code_version,'[NULL]') AS code_version,
                   COUNT(*) AS fills,
                   COUNT(DISTINCT run_id) AS runs,
                   MIN(target_date) AS min_target_date,
                   MAX(target_date) AS max_target_date,
                   ROUND(SUM(cost_usd),2) AS cost_usd,
                   ROUND(SUM(pnl_usd_at_fill),2) AS pnl_usd,
                   ROUND(SUM(pnl_usd_at_fill)/NULLIF(SUM(cost_usd),0),4) AS roi
            FROM fact_trades
            WHERE trade_class='live_real' AND settlement_status='settled'
              AND strategy_id=? AND execution_policy='mid_price_core_v1'
              AND entry_price_window='0.25-0.75'
            GROUP BY period, code_version
            ORDER BY period, code_version
            """,
            (STRATEGY_ID,),
        ),
        "config": fetchall(
            conn,
            "SELECT config_id, name, params, created_at_utc FROM strategy_config WHERE config_id=?",
            (STRATEGY_ID,),
        ),
    }


def render(data: dict[str, Any]) -> str:
    perf_cols = ["period", "fills", "days", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_side_p_raw", "avg_raw_edge", "avg_abs_yes_divergence"]
    side_cols = ["period", "side", "fills", "days", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_side_p_raw", "avg_raw_edge"]
    edge_cols = ["period", "raw_edge_bin", "fills", "days", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_side_p_raw", "avg_raw_edge"]
    prob_cols = ["period", "side_p_bin", "fills", "days", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_side_p_raw", "avg_raw_edge"]
    cand_cols = ["period", "raw_edge_bin", "eligible", "ordered", "live_filled", "win_rate", "cf_pnl", "cf_roi_proxy", "avg_side_p_raw", "avg_raw_edge_proxy"]
    interaction_cols = ["period", "city", "side", "model_version", "raw_edge_bin", "fills", "pnl_usd", "roi", "win_rate", "avg_side_p_raw"]
    code_date_cols = ["target_date", "code_version", "fills", "runs", "avg_model_p_yes", "avg_market_price", "avg_fact_edge", "pnl_usd"]
    code_period_cols = ["period", "code_version", "fills", "runs", "min_target_date", "max_target_date", "cost_usd", "pnl_usd", "roi"]

    lines = [
        "# mid_price_core_v1 raw probability 校准漂移与代码断点检查",
        "",
        "## 数据快照",
        "",
        f"- 数据源：`{data['checks']['db_path']}`。",
        f"- DB mtime UTC：`{data['checks']['db_mtime_utc']}`；`MAX(fact_built_at_utc)`：`{data['checks']['fact_built_at_utc']}`。",
        f"- 目标：`{STRATEGY_LABEL}` / `strategy_id={STRATEGY_ID}` / live_real settled fills。",
        "- PnL 只读 `fact_trades.pnl_usd_at_fill`；候选机会只读 `fact_signal_candidates.counterfactual_pnl`，不混作实盘 PnL。",
        "",
        "trade_class：",
        table(data["checks"]["trade_class"]),
        "",
        "settlement_status：",
        table(data["checks"]["settlement_status"]),
        "",
        "candidate coverage：",
        table([data["checks"]["signal_candidates"]]),
        "",
        "## 先答：像不像中间代码改坏？",
        "",
        data["code_conclusion"],
        "",
        "### v1_25_75 按 code_version / period",
        "",
        table(data["code_breaks"]["by_code_period"], code_period_cols),
        "",
        "### v1_25_75 按 target_date / code_version",
        "",
        table(data["code_breaks"]["by_target_date_code"], code_date_cols),
        "",
        "### 相关代码提交窗口",
        "",
        table(data["code_log"], ["sha", "date", "subject"]),
        "",
        "## raw probability 失效机制",
        "",
        data["mechanism_conclusion"],
        "",
        "## 1. 已成交样本总览",
        "",
        table(fmt_perf(data["overall"], perf_cols), perf_cols),
        "",
        "## 2. raw side probability 校准桶",
        "",
        "这里用 side 视角：BUY_YES=`model_p_yes`，BUY_NO=`1-model_p_yes`。若 raw probability 可靠，`side_p_bin` 越高，win rate/ROI 应大体越好。",
        "",
        table(fmt_perf(data["by_side_p_bin"], prob_cols), prob_cols),
        "",
        "## 3. raw edge 单调性",
        "",
        table(fmt_perf(data["by_raw_edge_bin"], edge_cols), edge_cols),
        "",
        "## 4. side / model / city 交互",
        "",
        "side：",
        table(fmt_perf(data["by_side"], side_cols), side_cols),
        "",
        "model：",
        table(fmt_perf(data["by_model"], ["period", "model_version", *perf_cols[1:]]), ["period", "model_version", *perf_cols[1:]]),
        "",
        "6 月后最差 city-side-model-edge 组合（样本 >= 3）：",
        table(fmt_perf(data["worst_interactions"], interaction_cols), interaction_cols),
        "",
        "## 5. 候选机会宇宙 sanity check",
        "",
        "候选表不是实盘 PnL，但可检查 raw edge 在全 eligible 机会里是否也退化。过滤：`eligible=1 AND final_yes IS NOT NULL AND decision_window_missing=0 AND decision_entry_price in [0.25,0.75] AND raw_edge_proxy>=0.10`。",
        "",
        table(fmt_candidate(data["candidate_by_edge"], cand_cols), cand_cols),
        "",
        "## 不停实盘的优化建议",
        "",
        data["actions"],
        "",
        "## 结论级别",
        "",
        "- 强机制：真实成交样本里 raw edge 低/中段排序失效。",
        "- 中等机制：ECMWF 与部分 city-side-model 组合在 6 月后显著拖累，但仍需控制 timing 子任务结果。",
        "- 弱/混合证据：candidate universe 只部分支持 raw edge drift，说明执行选择、timing 或实例归属仍可能是共同原因。",
        "- 代码改坏：目前证据不足，但存在明确嫌疑点。fact 中目标实例 `strategy_id`/配置没有 6/1 当天断裂；`127b1a0`/`5dd7fc3` 的 per-side defaults 与 explicit instances 需要 raw live 四层审计才能最终排除。",
        "- 偶然/样本不足：单个城市日和低样本城市不能单独当黑名单理由。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    trades = load_trades(conn)
    candidates = load_candidates(conn)
    if trades.empty:
        raise SystemExit("target trade sample is empty")

    overall = group_perf(trades, ["period"])
    by_side = group_perf(trades, ["period", "side"])
    by_model = group_perf(trades, ["period", "model_version"])
    by_side_p_bin = group_perf(trades, ["period", "side_p_bin"])
    by_raw_edge_bin = group_perf(trades, ["period", "raw_edge_bin"])
    interactions = group_perf(trades[trades["period"] == "post_2026_06_01"], ["period", "city", "side", "model_version", "raw_edge_bin"], min_n=3)
    interactions = sorted(interactions, key=lambda r: float(r["pnl_usd"]))[:25]
    candidate_by_edge = candidate_summary(candidates, ["period", "raw_edge_bin"])

    pre = {r["period"]: r for r in overall}["pre_2026_06_01"]
    post = {r["period"]: r for r in overall}["post_2026_06_01"]
    edge_post = {r["raw_edge_bin"]: r for r in by_raw_edge_bin if r["period"] == "post_2026_06_01"}
    edge_pre = {r["raw_edge_bin"]: r for r in by_raw_edge_bin if r["period"] == "pre_2026_06_01"}
    code = code_breaks(conn)
    code_versions = {row["code_version"] for row in code["by_code_period"]}
    code_conclusion = (
        f"- 目前不像一个简单的 6/1 单点 code_version 断裂：目标样本的 `strategy_id={STRATEGY_ID}` 和 `entry_price_window=0.25-0.75` 连续覆盖 2026-05-16 到 2026-06-07；"
        f"`fact_trades.code_version` 在本 DB 中只有 {len(code_versions)} 个取值（`live-cycle-migration`），6 月前后相同。\n"
        "- 但 `code_version=live-cycle-migration` 不是精确 git SHA，所以它只能说明 fact 层没有记录出版本断点，不能排除真实运行代码在 5/31 已变。\n"
        "- 代码层最可疑的是 `127b1a0 strategy: per-side entry band + min_edge gate` 和 `5dd7fc3 ops: run weather live strategies as explicit instances`。`127b1a0` 的提交说明明确写到 live_cycle defaults baked per-side band strategy，且修改了 signal builder 的 per-side gate；这类改动可能改变候选进入 planner 的集合、实例归属或 `entry_price_window` 记录，而不一定体现在 `execution_policy=mid_price_core_v1`。\n"
        "- 因此结论是：**不像 PnL 公式或 fact 表单点断裂，但存在实例/默认参数层改动嫌疑**。要最终排除，需要抽样 raw live signal -> plan -> order -> fact 四层，验证 6/1 后 `v1_25_75` 是否真的仍是 flat 0.25-0.75/min_edge 0.10，而不是被 per-side defaults 或实例启动脚本污染。"
    )
    mechanism_conclusion = (
        f"- 6 月后退化的核心是 raw probability 的排序/校准失效：已成交样本从 `{pre['pnl_usd']:+.2f}` 变 `{post['pnl_usd']:+.2f}`，win rate 从 {pct(pre['win_rate'])} 降到 {pct(post['win_rate'])}。\n"
        f"- 最关键断点在 raw edge 低/中段：6 月前 `0.10-0.15` 为 `{edge_pre.get('0.10-0.15', {}).get('pnl_usd', 0):+.2f}`，6 月后为 `{edge_post.get('0.10-0.15', {}).get('pnl_usd', 0):+.2f}`；"
        f"6 月前 `0.15-0.25` 为 `{edge_pre.get('0.15-0.25', {}).get('pnl_usd', 0):+.2f}`，6 月后为 `{edge_post.get('0.15-0.25', {}).get('pnl_usd', 0):+.2f}`。"
        f"高 raw edge `>0.25` 6 月后仍为 `{edge_post.get('>0.25', {}).get('pnl_usd', 0):+.2f}`。\n"
        "- 这说明不是所有已成交 raw 信号失效，而是原 v1 live 接受的边际 raw edge 现在噪声过大。候选机会宇宙只部分支持这个结论：`0.15-0.25` 同样转弱，但 `0.10-0.15` 在候选表里 6 月后反而正，说明执行选择/实例归属/timing 仍可能参与了退化。不中止实盘时，应先切掉边际段并做四层审计，而不是全盘否定 raw model。"
    )
    actions = (
        "1. **主 live 规则先升 raw edge 门槛**：`raw_edge<=0.25` 转 shadow，`raw_edge>0.25` 保留小 size live。理由：真实成交亏损集中在 `0.10-0.25`，高 raw edge 仍略正；候选表不完全同向，所以先作为风控，不作为永久 alpha 规则。\n"
        "2. **做 city-side-model 组合黑名单/降权，而不是单变量黑名单**：先降权报告中 6 月后最差的组合；城市单独弱但组合样本不足的只 shadow。理由：避免 Simpson paradox，也避免把可能的实例污染误判为城市 alpha。\n"
        "3. **ECMWF 不直接全停，但降 size 并要求更高 raw edge**：例如 ECMWF live 要 `raw_edge>0.30`，GFS 可先沿用 `>0.25`。理由：6 月后 ECMWF 是主要拖累，但可能和 city/timing/实例默认参数 mix 纠缠。\n"
        "4. **保留 blended edge 作为二级风控**：`blended_edge<0.10` 时 shadow；但不要把它当 alpha。理由：它能拦近期边际 raw 信号，但 6 月前会误伤。\n"
        "5. **立即加一个代码安全审计任务**：对 5/29-5/31 后的 raw live signal JSON 抽样，验证 `model_p_yes`、`market_price`、`edge`、`side`、`strategy_instance`、`entry_price_window` 在 signal、plan、order、fact 四层一致。理由：当前 fact 切片不像简单代码断裂，但 per-side defaults/实例启动脚本确实是可疑变更点。"
    )

    data = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "checks": self_checks(conn),
        "code_breaks": code,
        "code_log": code_log(),
        "overall": overall,
        "by_side": by_side,
        "by_model": by_model,
        "by_side_p_bin": by_side_p_bin,
        "by_raw_edge_bin": by_raw_edge_bin,
        "worst_interactions": interactions,
        "candidate_by_edge": candidate_by_edge,
        "code_conclusion": code_conclusion,
        "mechanism_conclusion": mechanism_conclusion,
        "actions": actions,
    }
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    OUT_MD.write_text(render(data), encoding="utf-8")
    print(f"wrote {OUT_MD}")
    print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
