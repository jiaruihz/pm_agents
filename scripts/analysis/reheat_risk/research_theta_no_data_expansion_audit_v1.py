#!/usr/bin/env python3
"""Data expansion audit for pure theta-NO carry research.

This script answers why the high-ask carry sample is still small and what data
layer must be expanded next: weather history, settlement history, or executable
orderbook history.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
MARKET_DATA = ROOT / "runtime/weather_edge_v1/market_data"
ORDERBOOK = MARKET_DATA / "orderbook_snapshots"
PAPER_SNAPSHOTS = MARKET_DATA / "paper_snapshots"
PM_HISTORY = MARKET_DATA / "cache/pm_history"
IEM_CACHE = MARKET_DATA / "cache/iem"
WU_CACHE = MARKET_DATA / "cache/wu_obs"
CALIBRATED_QUOTES = ROOT / "docs/analysis/2026-06/generated/m3_jump_model_v2_quote_calibration/calibrated_quotes.csv"
WALKFORWARD_SUMMARY = ROOT / "docs/analysis/2026-06/generated/theta_no_pure_carry_walkforward_v3/walkforward_summary.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_no_data_expansion_audit_v1"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-16-theta-no-data-expansion-audit-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-16-theta-no-data-expansion-audit-v1.md"


CITY_DATE_RE = re.compile(r"^(?P<city>.+)_(?P<date>\d{4}-\d{2}-\d{2})\.json$")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def orderbook_coverage() -> dict[str, Any]:
    by_date = {}
    if ORDERBOOK.exists():
        for day_dir in sorted(p for p in ORDERBOOK.iterdir() if p.is_dir()):
            files = sorted(day_dir.glob("orderbook_snapshot_*.jsonl.gz"))
            by_date[day_dir.name] = {
                "files": len(files),
                "first_file": files[0].name if files else None,
                "last_file": files[-1].name if files else None,
                "bytes": sum(p.stat().st_size for p in files),
            }
    dates = sorted(by_date)
    return {
        "dates": dates,
        "date_count": len(dates),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "total_files": sum(x["files"] for x in by_date.values()),
        "total_bytes": sum(x["bytes"] for x in by_date.values()),
        "by_date": by_date,
    }


def paper_snapshot_coverage() -> dict[str, Any]:
    files = sorted(PAPER_SNAPSHOTS.glob("snapshot_*.json")) if PAPER_SNAPSHOTS.exists() else []
    dates = sorted({f.name.split("_")[1] for f in files if len(f.name.split("_")) >= 2})
    return {
        "file_count": len(files),
        "date_count": len(dates),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "last_files": [f.name for f in files[-8:]],
    }


def pm_history_coverage() -> dict[str, Any]:
    city_date_files = []
    price_files = 0
    by_date: Counter[str] = Counter()
    by_city: Counter[str] = Counter()
    if PM_HISTORY.exists():
        for f in PM_HISTORY.glob("*.json"):
            m = CITY_DATE_RE.match(f.name)
            if not m:
                if f.name.startswith("prices_"):
                    price_files += 1
                continue
            city = m.group("city")
            date = m.group("date")
            city_date_files.append(f)
            by_city[city] += 1
            by_date[date] += 1
    dates = sorted(by_date)
    return {
        "city_date_file_count": len(city_date_files),
        "price_file_count": price_files,
        "date_count": len(dates),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "by_date_tail": dict(sorted(by_date.items())[-12:]),
        "city_count": len(by_city),
    }


def weather_cache_coverage() -> dict[str, Any]:
    iem_files = sorted(IEM_CACHE.glob("iem_v2_*.csv")) if IEM_CACHE.exists() else []
    wu_files = sorted(WU_CACHE.glob("wu_obs_*.csv")) if WU_CACHE.exists() else []
    return {
        "iem_files": len(iem_files),
        "wu_obs_files": len(wu_files),
        "iem_bytes": sum(p.stat().st_size for p in iem_files),
        "wu_obs_bytes": sum(p.stat().st_size for p in wu_files),
    }


def replay_coverage() -> dict[str, Any]:
    if not CALIBRATED_QUOTES.exists():
        return {"exists": False}
    q = pd.read_csv(CALIBRATED_QUOTES)
    q["target_date"] = q["target_date"].astype(str)
    d1 = q[q["dist_b"].eq(1) & q["decision_hour_local"].between(13, 17)].copy()
    high = d1[d1["best_ask"].ge(0.75) & d1["decline"].ge(0.5)]
    wf = pd.read_csv(WALKFORWARD_SUMMARY) if WALKFORWARD_SUMMARY.exists() else pd.DataFrame()
    return {
        "exists": True,
        "quote_rows": int(len(q)),
        "target_date_count": int(q["target_date"].nunique()),
        "first_target_date": str(q["target_date"].min()),
        "last_target_date": str(q["target_date"].max()),
        "d1_h13_17_rows": int(len(d1)),
        "d1_h13_17_dates": int(d1["target_date"].nunique()),
        "high_ask_decline_rows_quote_grain": int(len(high)),
        "high_ask_decline_dates": int(high["target_date"].nunique()),
        "walkforward_summary": wf.to_dict(orient="records") if not wf.empty else [],
    }


def gap_analysis(orderbook: dict[str, Any], pmh: dict[str, Any], replay: dict[str, Any]) -> dict[str, Any]:
    orderbook_dates = set(orderbook["dates"])
    replay_dates = set()
    if replay.get("exists"):
        q = pd.read_csv(CALIBRATED_QUOTES, usecols=["target_date"])
        replay_dates = set(q["target_date"].astype(str).unique())
    pm_dates = set(pmh.get("by_date_tail", {}).keys())
    all_pm_dates = set()
    if PM_HISTORY.exists():
        for f in PM_HISTORY.glob("*.json"):
            m = CITY_DATE_RE.match(f.name)
            if m:
                all_pm_dates.add(m.group("date"))
    return {
        "orderbook_not_in_replay": sorted(orderbook_dates - replay_dates),
        "pm_history_not_in_replay": sorted(all_pm_dates - replay_dates),
        "orderbook_and_pm_history_not_in_replay": sorted((orderbook_dates & all_pm_dates) - replay_dates),
        "replay_dates_without_orderbook_dir": sorted(replay_dates - orderbook_dates),
    }


def write_markdown(payload: dict[str, Any]) -> None:
    self_check = payload["data_self_check"]
    gate = payload["clob_gate"]
    ob = payload["orderbook_coverage"]
    pmh = payload["pm_history_coverage"]
    replay = payload["replay_coverage"]
    gaps = payload["gap_analysis"]
    wf = replay.get("walkforward_summary") or []
    disciplined = next((x for x in wf if x.get("selector") == "disciplined_ask65"), None)
    strict = next((x for x in wf if x.get("selector") == "strict_ask75"), None)
    lines = [
        "# Theta NO Data Expansion Audit v1",
        "",
        "Status: snapshot",
        f"Generated: {payload['generated_at_utc']}",
        "Target metric: `theta_no_data_expansion_gap` = 为什么 high-ask NO carry 样本少，以及下一步该补哪一层数据。",
        "",
        "## 数据快照",
        "",
        "- 数据源: 本轮已运行 `scripts/ops/sync_weather_remote.sh`；`run_stack.sh` 已完成 DB rebuild，但 FE 因 5174 端口仍忙启动失败。",
        f"- fact_built_at_utc: `{self_check['fact_trades_max_built_at_utc']}`。",
        f"- fact_trades trade_class: `{self_check['fact_trades_by_class']}`。",
        f"- fact_trades settlement_status: `{self_check['fact_trades_by_settlement_status']}`。",
        f"- fact_signal_candidates coverage: `{self_check['fact_signal_candidate_coverage']}`。",
        f"- CLOB orders/fills join: `{self_check['clob_order_fill_join']}`。",
        f"- CLOB coverage gate: gate_pass={gate.get('gate_pass')}, missing_order_rows={gate.get('missing_order_rows')}, over_order_keys={gate.get('over_order_keys')}, db_fill_cost_minus_fact_cost={gate.get('db_fill_cost_minus_fact_cost')}.",
        "",
        "## 人话结论",
        "",
        "可以补历史，而且已经同步到了更多 raw 数据；但当前瓶颈不是天气历史，而是“已物化到 theta carry replay 的可成交盘口历史”。",
        "",
        f"本机 raw orderbook 现在有 {ob['date_count']} 个日期目录，范围 {ob['first_date']} 到 {ob['last_date']}，共 {ob['total_files']} 个 snapshot 文件。pm_history city-date 文件覆盖到 {pmh['last_date']}。但当前 `calibrated_quotes.csv` 仍只覆盖 {replay.get('first_target_date')} 到 {replay.get('last_target_date')}，{replay.get('target_date_count')} 个 target dates。",
        "",
        f"也就是说，数据已经拉下来了，但 NO carry 研究样本还没吃到 {', '.join(gaps['orderbook_and_pm_history_not_in_replay'][-8:]) or 'NA'} 这些既有 orderbook 又有 pm_history 的新日期。下一步最有价值的是把这些日期重新物化进 M3/v2 quote replay，而不是继续只扩天气缓存。",
        "",
    ]
    if disciplined and strict:
        lines.extend(
            [
                f"当前 walk-forward 的样本基线是 disciplined selector {int(disciplined['candidate_rows'])} 行/{int(disciplined['candidate_active_dates'])} 天，strict high-ask {int(strict['candidate_rows'])} 行/{int(strict['candidate_active_dates'])} 天。粗略说，每多物化 5 个完整 settled target days，可能只多几十个 high-ask carry quote，真正要达到稳定结论需要持续 forward 或补更早 orderbook。",
                "",
            ]
        )
    lines.extend(
        [
            "## 覆盖表",
            "",
            "| layer | coverage | why it matters |",
            "|---|---:|---|",
            f"| raw orderbook snapshots | {ob['date_count']} dates / {ob['total_files']} files | 可成交 best ask/size，是 ROI 的关键层 |",
            f"| pm_history city-date settlements | {pmh['date_count']} dates / {pmh['city_date_file_count']} files | 决定最终 winner/payoff |",
            f"| weather cache | IEM {payload['weather_cache_coverage']['iem_files']} files, WU {payload['weather_cache_coverage']['wu_obs_files']} files | 训练 no-reheat 物理模型 |",
            f"| calibrated theta replay | {replay.get('target_date_count')} dates / {replay.get('quote_rows')} quote rows | 当前 NO carry 回测实际使用层 |",
            "",
            "## Replay 缺口",
            "",
            f"- orderbook exists but replay missing: `{gaps['orderbook_not_in_replay']}`",
            f"- pm_history exists but replay missing: `{gaps['pm_history_not_in_replay'][-20:]}`",
            f"- both orderbook and pm_history exist but replay missing: `{gaps['orderbook_and_pm_history_not_in_replay']}`",
            "",
            "## 建议的补全顺序",
            "",
            "1. 先重跑/改造 M3 quote materializer，让 `calibrated_quotes.csv` 吃到 2026-06-10 之后已有 orderbook + pm_history 的日期。",
            "2. 再做 current YES / NO d1 / NO d2 ladder 的同窗 expression selector，而不是只补 NO d1。",
            "3. 若要扩到 5 月 19 日之前，需要找 N100 备份或外部历史盘口；只有天气/settlement 没有盘口时，只能训练 no-reheat，不能算 executable ROI。",
            "4. 从现在开始可加 zero-notional forward telemetry：每天记录 high-ask carry + current YES sibling quotes，用于累积最干净的前瞻样本。",
            "",
            "## 三道门 verdict",
            "",
            "本报告是数据覆盖审计，不给 live 动作。结论是：当前 no-live 不是因为物理逻辑缺，而是 executable replay 样本少；数据补全应优先补 quote materialization 和 forward telemetry。",
            "",
            "## 输出文件",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- generated CSV dir: `{OUT_DIR.relative_to(ROOT)}`",
            f"- Script: `scripts/analysis/reheat_risk/research_theta_no_data_expansion_audit_v1.py`",
            "",
        ]
    )
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    orderbook = orderbook_coverage()
    paper = paper_snapshot_coverage()
    pmh = pm_history_coverage()
    weather = weather_cache_coverage()
    replay = replay_coverage()
    gaps = gap_analysis(orderbook, pmh, replay)
    pd.DataFrame(orderbook["by_date"]).T.to_csv(OUT_DIR / "orderbook_by_date.csv")
    pd.DataFrame([pmh]).to_csv(OUT_DIR / "pm_history_summary.csv", index=False)
    pd.DataFrame([replay]).to_csv(OUT_DIR / "replay_summary.csv", index=False)
    pd.DataFrame({"orderbook_and_pm_history_not_in_replay": gaps["orderbook_and_pm_history_not_in_replay"]}).to_csv(
        OUT_DIR / "materialization_gap_dates.csv", index=False
    )
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "theta_no_data_expansion_gap",
        "data_self_check": data_self_check(),
        "clob_gate": load_gate(),
        "orderbook_coverage": orderbook,
        "paper_snapshot_coverage": paper,
        "pm_history_coverage": pmh,
        "weather_cache_coverage": weather,
        "replay_coverage": replay,
        "gap_analysis": gaps,
        "outputs": {
            "orderbook_by_date": str((OUT_DIR / "orderbook_by_date.csv").relative_to(ROOT)),
            "pm_history_summary": str((OUT_DIR / "pm_history_summary.csv").relative_to(ROOT)),
            "replay_summary": str((OUT_DIR / "replay_summary.csv").relative_to(ROOT)),
            "materialization_gap_dates": str((OUT_DIR / "materialization_gap_dates.csv").relative_to(ROOT)),
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(payload)
    print(json.dumps({"ok": True, "out_md": str(OUT_MD), "out_json": str(OUT_JSON)}, indent=2))


if __name__ == "__main__":
    main()
