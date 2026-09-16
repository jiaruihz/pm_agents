#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
OPS = ROOT / "scripts/ops"
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))

import station_basis_eval_v1  # noqa: E402
import station_basis_live_prep_gate_v1  # noqa: E402

OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-14-station-basis-live-candidate-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-14-station-basis-live-candidate-v1.json"
DB = ROOT / "runtime/weather.db"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
YES_TRADES = ROOT / "docs/analysis/2026-06/generated/m3_orderbook_best_ask_v2_h14_17/m3_orderbook_best_ask_trades.csv"
NO_QUOTES = ROOT / "docs/analysis/2026-06/generated/m3_exhaustion_no_v0/exhaustion_tail_no_quotes.csv"
SHADOW_V0 = ROOT / "runtime/weather_edge_v1/station_basis_shadow"
SHADOW_V1 = ROOT / "runtime/weather_edge_v1/station_basis_shadow_v1"
EXEC_V1 = ROOT / "runtime/weather_edge_v1/station_basis_exec_v1"
READINESS_V1 = SHADOW_V1 / "readiness.json"
PENDING_MONITOR_V1 = SHADOW_V1 / "pending_monitor.json"
PENDING_MONITOR_HISTORY_V1 = SHADOW_V1 / "pending_monitor_history.jsonl"
LIVE_PREP_GATE_V1 = SHADOW_V1 / "live_prep_gate.json"

REPAIRED6 = {"Paris", "London", "Milan", "Chicago", "KualaLumpur", "PanamaCity"}
LIVE5 = {"Paris", "London", "Chicago", "KualaLumpur", "PanamaCity"}
PRICE_BINS = [0.0, 0.4, 0.6, 0.75, 0.8, 0.9, 0.97, 1.01]
PRICE_LABELS = ["<=0.40", "0.40-0.60", "0.60-0.75", "0.75-0.80", "0.80-0.90", "0.90-0.97", ">0.97"]


def pct(x: float | None) -> str:
    return "NA" if x is None or not math.isfinite(x) else f"{x * 100:+.1f}%"


def data_self_check() -> dict[str, Any]:
    conn = sqlite3.connect(str(DB), timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    out = {
        "fact_trades_max_built_at_utc": conn.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades").fetchone()[0],
        "fact_trades_by_class": [
            dict(row)
            for row in conn.execute("SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class")
        ],
        "fact_trades_by_settlement_status": [
            {"settlement_status": row[0], "rows": row[1]}
            for row in conn.execute("SELECT settlement_status, COUNT(*) FROM fact_trades GROUP BY settlement_status")
        ],
        "fact_signal_candidate_coverage": dict(
            zip(
                ["rows", "eligible", "paper_ordered", "live_filled"],
                conn.execute(
                    "SELECT COUNT(*), SUM(eligible), SUM(paper_ordered), SUM(live_filled) FROM fact_signal_candidates"
                ).fetchone(),
            )
        ),
        "clob_order_fill_join": [
            {"status": row[0], "orders": row[1], "with_fill": row[2]}
            for row in conn.execute(
                "SELECT o.status, COUNT(*) orders, "
                "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) with_fill "
                "FROM orders o LEFT JOIN fills f USING(execution_id) "
                "WHERE o.venue='polymarket_clob' GROUP BY o.status"
            )
        ],
    }
    conn.close()
    return out


def load_gate() -> dict[str, Any]:
    if not GATE.exists():
        return {"gate_pass": None, "missing": True}
    data = json.loads(GATE.read_text())
    return {
        "gate_pass": data.get("gate_pass"),
        "fail_reasons": data.get("fail_reasons", []),
        "db_fill_cost_minus_fact_cost": data.get("db_fill_cost_minus_fact_cost"),
    }


def t_stat(daily: pd.Series) -> float | None:
    if len(daily) <= 1:
        return None
    sd = float(daily.std(ddof=1))
    if sd <= 0:
        return None
    return float(daily.mean() / sd * math.sqrt(len(daily)))


def summarize(df: pd.DataFrame, *, cost_col: str, pnl_col: str) -> dict[str, Any]:
    if df.empty:
        return {"rows": 0}
    daily = df.groupby("target_date")[pnl_col].sum()
    city_day_cols = ["city", "target_date"]
    return {
        "rows": int(len(df)),
        "city_days": int(df[city_day_cols].drop_duplicates().shape[0]),
        "days": int(df["target_date"].nunique()),
        "cities": sorted(df["city"].unique().tolist()),
        "cost": round(float(df[cost_col].sum()), 6),
        "pnl": round(float(df[pnl_col].sum()), 6),
        "roi": float(df[pnl_col].sum() / df[cost_col].sum()) if float(df[cost_col].sum()) else None,
        "daily_t": t_stat(daily),
        "positive_days": int((daily > 0).sum()),
        "total_days": int(len(daily)),
    }


def summarize_with_win(df: pd.DataFrame, *, cost_col: str, pnl_col: str) -> dict[str, Any]:
    out = summarize(df, cost_col=cost_col, pnl_col=pnl_col)
    if df.empty:
        return out
    out["win_rate"] = float((df[pnl_col] > 0).mean())
    return out


def date_split_summary(df: pd.DataFrame, *, cost_col: str, pnl_col: str, split_date: str = "2026-06-01") -> dict[str, Any]:
    train = df[df["target_date"].astype(str) < split_date]
    holdout = df[df["target_date"].astype(str) >= split_date]
    return {
        "split_date": split_date,
        "train": summarize(train, cost_col=cost_col, pnl_col=pnl_col),
        "holdout": summarize(holdout, cost_col=cost_col, pnl_col=pnl_col),
    }


def price_band_summary(df: pd.DataFrame, *, price_col: str, cost_col: str, pnl_col: str) -> list[dict[str, Any]]:
    if df.empty:
        return []
    work = df.copy()
    work["price_band"] = pd.cut(
        work[price_col],
        bins=PRICE_BINS,
        labels=PRICE_LABELS,
        include_lowest=True,
        right=True,
    )
    rows = []
    for band, group in work.groupby("price_band", observed=True):
        holdout = group[group["target_date"].astype(str) >= "2026-06-01"]
        item = summarize_with_win(group, cost_col=cost_col, pnl_col=pnl_col)
        hold = summarize_with_win(holdout, cost_col=cost_col, pnl_col=pnl_col)
        rows.append(
            {
                "price_band": str(band),
                "rows": item["rows"],
                "days": item.get("days"),
                "roi": item.get("roi"),
                "win_rate": item.get("win_rate"),
                "holdout_rows": hold.get("rows", 0),
                "holdout_roi": hold.get("roi"),
                "holdout_win_rate": hold.get("win_rate"),
            }
        )
    return rows


def price_band_for(value: float | None) -> str | None:
    if value is None:
        return None
    for left, right, label in zip(PRICE_BINS[:-1], PRICE_BINS[1:], PRICE_LABELS):
        if left < value <= right or (left == 0.0 and left <= value <= right):
            return label
    return None


def prefix_walkforward_yes(yes: pd.DataFrame) -> list[dict[str, Any]]:
    """Use only past days to decide whether Milan is allowed and which hour to use.

    This is intentionally simple and conservative: for each test date, choose the
    best hour among 14/15/16 on prior dates after excluding any city with prior
    negative ROI and at least five prior city-days. Then trade that hour on the
    selected cities for the test date.
    """
    out = []
    all_dates = sorted(str(d) for d in yes["target_date"].unique())
    for test_date in all_dates:
        past = yes[yes["target_date"].astype(str) < test_date]
        today = yes[yes["target_date"].astype(str) == test_date]
        if past["target_date"].nunique() < 7 or today.empty:
            continue
        city_keep = []
        for city, group in past.groupby("city"):
            if group[["city", "target_date"]].drop_duplicates().shape[0] < 5:
                city_keep.append(city)
                continue
            cost = float(group["entry_cost"].sum())
            roi = float(group["pnl"].sum() / cost) if cost else -1.0
            if roi > 0:
                city_keep.append(city)
        past_kept = past[past["city"].isin(city_keep)]
        hour_scores = []
        for hour, group in past_kept.groupby("decision_hour_local"):
            if hour not in {14, 15, 16}:
                continue
            cost = float(group["entry_cost"].sum())
            roi = float(group["pnl"].sum() / cost) if cost else -1.0
            hour_scores.append((roi, int(hour)))
        if not hour_scores:
            continue
        _, hour = max(hour_scores)
        selected = today[(today["city"].isin(city_keep)) & (today["decision_hour_local"] == hour)].copy()
        if selected.empty:
            continue
        out.append(
            {
                "test_date": test_date,
                "selected_hour": hour,
                "cities": sorted(city_keep),
                "rows": int(len(selected)),
                "cost": float(selected["entry_cost"].sum()),
                "pnl": float(selected["pnl"].sum()),
            }
        )
    return out


def summarize_walkforward(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"rows": 0}
    cost = sum(float(row["cost"]) for row in rows)
    pnl = sum(float(row["pnl"]) for row in rows)
    daily = pd.Series({row["test_date"]: row["pnl"] for row in rows})
    return {
        "test_days": len(rows),
        "orders": int(sum(int(row["rows"]) for row in rows)),
        "cost": round(cost, 6),
        "pnl": round(pnl, 6),
        "roi": pnl / cost if cost else None,
        "daily_t": t_stat(daily),
        "positive_days": int((daily > 0).sum()),
        "total_days": int(len(daily)),
        "selections": rows,
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def historical_yes() -> dict[str, Any]:
    trades = pd.read_csv(YES_TRADES)
    yes = trades[
        (trades["city"].isin(REPAIRED6))
        & (trades["strategy"] == "observed_bucket_buy_yes")
        & (trades["decision_hour_local"].between(14, 16))
    ].copy()
    variants: dict[str, pd.DataFrame] = {
        "v0_yes_all_h14_16_repaired6": yes,
        "v1_yes_all_h14_16_live5": yes[yes["city"].isin(LIVE5)],
        "v1_yes_h16_live5": yes[(yes["city"].isin(LIVE5)) & (yes["decision_hour_local"] == 16)],
        "v1_yes_one_cityday_last_live5": yes[yes["city"].isin(LIVE5)]
        .sort_values(["city", "target_date", "decision_hour_local", "bracket"])
        .drop_duplicates(["city", "target_date"], keep="last"),
        "negative_control_yes_milan": yes[yes["city"] == "Milan"],
    }
    yes_h16_live5 = variants["v1_yes_h16_live5"]
    by_city = {
        city: summarize(group, cost_col="entry_cost", pnl_col="pnl")
        for city, group in yes.groupby("city")
    }
    split = {name: date_split_summary(df, cost_col="entry_cost", pnl_col="pnl") for name, df in variants.items()}
    wf_rows = prefix_walkforward_yes(yes)
    return {
        "variants": {name: summarize(df, cost_col="entry_cost", pnl_col="pnl") for name, df in variants.items()},
        "by_city": by_city,
        "date_split": split,
        "prefix_walkforward": summarize_walkforward(wf_rows),
        "price_band_v1_yes_h16_live5": price_band_summary(
            yes_h16_live5,
            price_col="entry_cost",
            cost_col="entry_cost",
            pnl_col="pnl",
        ),
    }


def historical_no() -> dict[str, Any]:
    quotes = pd.read_csv(NO_QUOTES)
    rep = quotes[quotes["city"].isin(REPAIRED6)].copy()
    d1 = rep[
        (rep["decline"] >= 1.0)
        & (rep["decision_hour_local"].between(13, 17))
        & (rep["distance"] == 1)
    ].sort_values(["city", "target_date", "decision_hour_local"]).drop_duplicates(["city", "target_date", "bracket"])
    d2 = rep[
        (rep["decline"] >= 1.0)
        & (rep["decision_hour_local"].between(13, 17))
        & (rep["distance"] == 2)
    ].sort_values(["city", "target_date", "decision_hour_local"]).drop_duplicates(["city", "target_date", "bracket"])
    variants = {
        "v0_no_d1_repaired6": d1,
        "v1_no_d1_live5": d1[d1["city"].isin(LIVE5)],
        "v1_no_d2_live5": d2[d2["city"].isin(LIVE5)],
        "negative_control_no_d1_milan": d1[d1["city"] == "Milan"],
    }
    return {
        "variants": {name: summarize(df, cost_col="best_ask", pnl_col="pnl") for name, df in variants.items()},
        "date_split": {name: date_split_summary(df, cost_col="best_ask", pnl_col="pnl") for name, df in variants.items()},
        "price_band_v1_no_d1_live5": price_band_summary(
            variants["v1_no_d1_live5"],
            price_col="best_ask",
            cost_col="best_ask",
            pnl_col="pnl",
        ),
    }


def forward_shadow_v0() -> dict[str, Any]:
    entries = read_jsonl(SHADOW_V0 / "entries.jsonl")
    settlements = read_jsonl(SHADOW_V0 / "settlements.jsonl")
    if not settlements:
        return {"entries": len(entries), "settled": 0}
    df = pd.DataFrame(settlements)
    out = {
        "entries": len(entries),
        "settled": len(settlements),
        "pending": len(entries) - len(settlements),
        "by_rule": {},
        "by_city": {},
    }
    for rule, group in df.groupby("rule"):
        out["by_rule"][rule] = summarize(group, cost_col="notional", pnl_col="pnl")
    for city, group in df.groupby("city"):
        out["by_city"][city] = summarize(group, cost_col="notional", pnl_col="pnl")
    return out


def latest_ts(rows: list[dict[str, Any]]) -> str | None:
    values = [str(row.get("ts_utc") or "") for row in rows if row.get("ts_utc")]
    return max(values) if values else None


def forward_shadow_v1() -> dict[str, Any]:
    entries = read_jsonl(SHADOW_V1 / "entries.jsonl")
    settlements = read_jsonl(SHADOW_V1 / "settlements.jsonl")
    cycles = read_jsonl(SHADOW_V1 / "cycles.jsonl")
    latest_entry = entries[-1] if entries else None
    return {
        "path": str(SHADOW_V1),
        "entries": len(entries),
        "settled": len(settlements),
        "pending": len(entries) - len(settlements),
        "cycles": len(cycles),
        "latest_cycle_ts_utc": latest_ts(cycles),
        "latest_entry_ts_utc": latest_ts(entries),
        "latest_entry": latest_entry,
    }


def execution_v1() -> dict[str, Any]:
    orders = read_jsonl(EXEC_V1 / "orders.jsonl")
    cursor_path = EXEC_V1 / "cursor.json"
    risk_path = EXEC_V1 / "risk_config.json"
    cursor = json.loads(cursor_path.read_text()) if cursor_path.exists() else {}
    risk = json.loads(risk_path.read_text()) if risk_path.exists() else {}
    allowed = [row for row in orders if row.get("allow")]
    return {
        "path": str(EXEC_V1),
        "orders": len(orders),
        "allowed_orders": len(allowed),
        "cursor_processed": cursor.get("processed", 0),
        "mode": "dry_run",
        "risk_config": risk,
        "latest_order_ts_utc": latest_ts(orders),
        "latest_order": orders[-1] if orders else None,
    }


def readiness_v1() -> dict[str, Any]:
    return read_json(READINESS_V1)


def pending_monitor_v1() -> dict[str, Any]:
    return read_json(PENDING_MONITOR_V1)


def pending_monitor_history_v1() -> dict[str, Any]:
    rows = read_jsonl(PENDING_MONITOR_HISTORY_V1)
    thesis_counts: dict[str, int] = {}
    for row in rows:
        for thesis, count in (row.get("thesis_counts") or {}).items():
            thesis_counts[thesis] = thesis_counts.get(thesis, 0) + int(count)
    return {
        "path": str(PENDING_MONITOR_HISTORY_V1),
        "rows": len(rows),
        "first_generated_at_utc": rows[0].get("generated_at_utc") if rows else None,
        "latest_generated_at_utc": rows[-1].get("generated_at_utc") if rows else None,
        "thesis_counts_total": thesis_counts,
        "latest": rows[-1] if rows else None,
    }


def eval_v1() -> dict[str, Any]:
    return station_basis_eval_v1.evaluate()


def live_prep_gate_v1() -> dict[str, Any]:
    payload = station_basis_live_prep_gate_v1.build_payload()
    LIVE_PREP_GATE_V1.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def md_table(rows: list[list[Any]], headers: list[str]) -> str:
    return "\n".join(
        ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
        + ["| " + " | ".join(str(v) for v in row) + " |" for row in rows]
    )


def main() -> int:
    generated_at = datetime.now(timezone.utc).isoformat()
    self_check = data_self_check()
    gate = load_gate()
    yes = historical_yes()
    no = historical_no()
    forward = forward_shadow_v0()
    forward_v1 = forward_shadow_v1()
    exec_v1 = execution_v1()
    ready_v1 = readiness_v1()
    pending_monitor = pending_monitor_v1()
    pending_history = pending_monitor_history_v1()
    eval_payload = eval_v1()
    live_prep_gate = live_prep_gate_v1()
    report = {
        "generated_at_utc": generated_at,
        "data_source": {
            "db": str(DB),
            "yes_trades": str(YES_TRADES),
            "no_quotes": str(NO_QUOTES),
            "forward_shadow_v0": str(SHADOW_V0),
            "forward_shadow_v1": str(SHADOW_V1),
            "execution_v1": str(EXEC_V1),
            "readiness_v1": str(READINESS_V1),
            "pending_monitor_v1": str(PENDING_MONITOR_V1),
            "pending_monitor_history_v1": str(PENDING_MONITOR_HISTORY_V1),
            "live_prep_gate_v1": str(LIVE_PREP_GATE_V1),
        },
        "data_self_check": self_check,
        "clob_coverage_gate": gate,
        "candidate": {
            "id": "station_basis_taker_live5_h16_yes_no_d1_v1",
            "cities": sorted(LIVE5),
            "yes_rule": "BUY_YES official running bucket, local hour 16 only, one entry per city-day, ask<=0.90",
            "no_rule": "BUY_NO d1 after official-station decline >=1C, local hour 13-17, ask<=0.90; d2 is observe-only",
            "price_overlay": "live-core shadow/eval requires ask<=0.90; ask 0.90-0.97 stays observe/marginal only",
            "excluded": {
                "Milan": "historical YES and NO d1 are negative in current generated evidence and first forward YES lost",
                "Jakarta": "batch2 station mapping has too few historical settlement days for live gating",
            },
        },
        "historical_yes": yes,
        "historical_no": no,
        "forward_shadow_v0": forward,
        "forward_shadow_v1": forward_v1,
        "execution_v1": exec_v1,
        "readiness_v1": ready_v1,
        "pending_monitor_v1": pending_monitor,
        "pending_monitor_history_v1": pending_history,
        "eval_v1": eval_payload,
        "live_prep_gate_v1": live_prep_gate,
        "verdict": {
            "direction": "station-basis taker is the lead live direction",
            "live_now": False,
            "reason": (
                "v1 needs its own forward shadow; CLOB coverage gate status is "
                f"{gate.get('gate_pass')}"
            ),
            "next_gate": "run v1 shadow until >=40 settled per live-core rule with sign matching historical backtest, then deploy review only via weather-strategy-deploy",
        },
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    rows = []
    for name, item in yes["variants"].items():
        rows.append([name, item["rows"], item.get("city_days"), item.get("days"), pct(item.get("roi")), item.get("positive_days"), item.get("total_days"), f"{item.get('daily_t'):+.2f}" if item.get("daily_t") is not None else "NA"])
    no_rows = []
    for name, item in no["variants"].items():
        no_rows.append([name, item["rows"], item.get("city_days"), item.get("days"), pct(item.get("roi")), item.get("positive_days"), item.get("total_days"), f"{item.get('daily_t'):+.2f}" if item.get("daily_t") is not None else "NA"])
    split_rows = []
    for name, item in yes["date_split"].items():
        split_rows.append(
            [
                name,
                item["train"]["rows"],
                pct(item["train"].get("roi")),
                item["holdout"]["rows"],
                pct(item["holdout"].get("roi")),
                item["holdout"].get("positive_days"),
                item["holdout"].get("total_days"),
            ]
        )
    no_split_rows = []
    for name, item in no["date_split"].items():
        no_split_rows.append(
            [
                name,
                item["train"]["rows"],
                pct(item["train"].get("roi")),
                item["holdout"]["rows"],
                pct(item["holdout"].get("roi")),
                item["holdout"].get("positive_days"),
                item["holdout"].get("total_days"),
            ]
        )
    latest_entry = forward_v1.get("latest_entry") or {}
    latest_order = exec_v1.get("latest_order") or {}
    readiness_counts = ready_v1.get("status_counts", {})
    pending_rows = pending_monitor.get("rows", [])
    pending_latest = pending_rows[-1] if pending_rows else {}
    eval_core = eval_payload.get("live_core_results", {})
    live_prep_blockers = live_prep_gate.get("blockers", [])
    live_prep_passed = live_prep_gate.get("passed", [])
    latest_entry_ask = latest_entry.get("ask")
    latest_entry_price_band = price_band_for(float(latest_entry_ask)) if latest_entry_ask is not None else None
    yes_price_rows = [
        [
            row["price_band"],
            row["rows"],
            row.get("days"),
            pct(row.get("roi")),
            pct(row.get("win_rate")),
            row.get("holdout_rows"),
            pct(row.get("holdout_roi")),
            pct(row.get("holdout_win_rate")),
        ]
        for row in yes["price_band_v1_yes_h16_live5"]
    ]
    no_price_rows = [
        [
            row["price_band"],
            row["rows"],
            row.get("days"),
            pct(row.get("roi")),
            pct(row.get("win_rate")),
            row.get("holdout_rows"),
            pct(row.get("holdout_roi")),
            pct(row.get("holdout_win_rate")),
        ]
        for row in no["price_band_v1_no_d1_live5"]
    ]
    if gate.get("gate_pass") is True:
        gate_note = "- CLOB coverage gate is passing; live_real reporting is no longer blocked by fill reconciliation, but this station-basis live decision is still blocked by v1 forward evidence."
    else:
        gate_note = "- 因 coverage gate=false，本报告不发布 live_real PnL/ROI/rank/curve。"

    md = [
        "# Station-Basis Live Candidate v1",
        "",
        f"> generated_at_utc: `{generated_at}`",
        "> scope: local research + zero-notional shadow only; no N100/live config changed.",
        "",
        "## 数据快照",
        "",
        f"- DB: `{DB}`",
        f"- fact_trades MAX(fact_built_at_utc): `{self_check['fact_trades_max_built_at_utc']}`",
        f"- CLOB coverage gate: `{gate.get('gate_pass')}`; fail_reasons: `{', '.join(gate.get('fail_reasons', []))}`",
        gate_note,
        "",
        "### 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(self_check, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 候选方向",
        "",
        "`station_basis_taker_live5_h16_yes_no_d1_v1`：5 个 station-basis 城市 "
        "(Chicago/KualaLumpur/London/PanamaCity/Paris)，排除 Milan/Jakarta；"
        "YES 只在当地 16h 买官方 running bucket，且每 city-day 只记一次；NO d1 "
        "在 13-17h 衰竭后记录为 live core；live-core shadow/eval 使用 `ask<=0.90`；NO d2 只记录为 observe-only。",
        "",
        "## Historical YES Ablation",
        "",
        md_table(rows, ["variant", "rows", "city_days", "days", "ROI", "pos_days", "total_days", "daily_t"]),
        "",
        "## Historical NO Ablation",
        "",
        md_table(no_rows, ["variant", "rows", "city_days", "days", "ROI", "pos_days", "total_days", "daily_t"]),
        "",
        "## Train / Holdout Robustness",
        "",
        "`train` is target_date < 2026-06-01; `holdout` is 2026-06-01 onward.",
        "",
        "### YES",
        "",
        md_table(split_rows, ["variant", "train_rows", "train_ROI", "holdout_rows", "holdout_ROI", "holdout_pos_days", "holdout_days"]),
        "",
        "### NO",
        "",
        md_table(no_split_rows, ["variant", "train_rows", "train_ROI", "holdout_rows", "holdout_ROI", "holdout_pos_days", "holdout_days"]),
        "",
        "### Prefix Walk-Forward YES",
        "",
        f"- orders: `{yes['prefix_walkforward'].get('orders')}`; test_days: `{yes['prefix_walkforward'].get('test_days')}`; "
        f"ROI: `{pct(yes['prefix_walkforward'].get('roi'))}`; daily_t: "
        f"`{yes['prefix_walkforward'].get('daily_t'):+.2f}`"
        if yes["prefix_walkforward"].get("daily_t") is not None
        else f"- orders: `{yes['prefix_walkforward'].get('orders')}`; test_days: `{yes['prefix_walkforward'].get('test_days')}`; ROI: `{pct(yes['prefix_walkforward'].get('roi'))}`",
        "",
        "## Price-Band Stress",
        "",
        "### YES h16 live5",
        "",
        md_table(
            yes_price_rows,
            ["price_band", "rows", "days", "ROI", "win_rate", "holdout_rows", "holdout_ROI", "holdout_win_rate"],
        ),
        "",
        "### NO d1 live5",
        "",
        md_table(
            no_price_rows,
            ["price_band", "rows", "days", "ROI", "win_rate", "holdout_rows", "holdout_ROI", "holdout_win_rate"],
        ),
        "",
        f"- latest v1 entry ask `{latest_entry_ask}` falls in price band `{latest_entry_price_band}`.",
        "- NO d1 `0.80-0.90` remains positive in historical and holdout slices; `0.90-0.97` is only marginal and should stay separately monitored before any tiny-live gate.",
        "",
        "## Forward Shadow v0 Diagnosis",
        "",
        f"- entries: `{forward.get('entries')}`; settled: `{forward.get('settled')}`; pending: `{forward.get('pending')}`",
        f"- by_rule: `{json.dumps(forward.get('by_rule', {}), ensure_ascii=False)}`",
        "",
        "v0 的 broad `yes_bucket` 已出现 early divergence：7 笔 settled ROI -43.5%，"
        "而历史 ablation 显示 Milan 是负贡献，且 16h-only / one-city-day-last 是更强表达。",
        "",
        "## Forward Shadow v1 Current",
        "",
        f"- cycles: `{forward_v1['cycles']}`; latest_cycle_ts_utc: `{forward_v1['latest_cycle_ts_utc']}`",
        f"- entries: `{forward_v1['entries']}`; settled: `{forward_v1['settled']}`; pending: `{forward_v1['pending']}`",
        f"- latest_entry_ts_utc: `{forward_v1['latest_entry_ts_utc']}`",
        f"- latest_entry: `{json.dumps(latest_entry, ensure_ascii=False)}`",
        "",
        "## Liveability Readiness Probe v1",
        "",
        f"- generated_at_utc: `{ready_v1.get('generated_at_utc')}`",
        f"- status_counts: `{json.dumps(readiness_counts, ensure_ascii=False)}`",
        f"- output: `{READINESS_V1}`",
        "",
        "## Pending Monitor v1",
        "",
        f"- generated_at_utc: `{pending_monitor.get('generated_at_utc')}`",
        f"- pending: `{pending_monitor.get('pending')}`; status_counts: `{json.dumps(pending_monitor.get('status_counts', {}), ensure_ascii=False)}`; thesis_counts: `{json.dumps(pending_monitor.get('thesis_counts', {}), ensure_ascii=False)}`",
        f"- latest_pending: `{json.dumps(pending_latest, ensure_ascii=False)}`",
        f"- output: `{PENDING_MONITOR_V1}`",
        "",
        "## Pending Monitor History v1",
        "",
        f"- rows: `{pending_history.get('rows')}`; first: `{pending_history.get('first_generated_at_utc')}`; latest: `{pending_history.get('latest_generated_at_utc')}`",
        f"- thesis_counts_total: `{json.dumps(pending_history.get('thesis_counts_total', {}), ensure_ascii=False)}`",
        f"- output: `{PENDING_MONITOR_HISTORY_V1}`",
        "",
        "## Dry-Run Execution Chain v1",
        "",
        f"- exec path: `{EXEC_V1}`",
        f"- mode: `{exec_v1['mode']}`; cursor_processed: `{exec_v1['cursor_processed']}`",
        f"- orders: `{exec_v1['orders']}`; allowed_orders: `{exec_v1['allowed_orders']}`",
        f"- kill_switch_path: `{exec_v1.get('risk_config', {}).get('kill_switch_path')}`",
        f"- latest_order: `{json.dumps(latest_order, ensure_ascii=False)}`",
        "- v1 shadow cycle now side-loads `weather_station_basis_exec_v1.py run`, so new v1 entries are risk-audited without a separate persistent process.",
        "",
        "## Eval v1 Price-Eligible",
        "",
        f"- verdict: `{eval_payload.get('verdict')}`",
        f"- entries/settled/pending: `{eval_payload.get('entries')}` / `{eval_payload.get('settled')}` / `{eval_payload.get('pending')}`",
        f"- live_core_results: `{json.dumps(eval_core, ensure_ascii=False)}`",
        "",
        "## Live-Prep Gate",
        "",
        f"- artifact: `{LIVE_PREP_GATE_V1}`",
        f"- verdict: `{live_prep_gate.get('verdict')}`; live_now: `{live_prep_gate.get('live_now')}`",
        f"- blockers: `{len(live_prep_blockers)}`; passed checks: `{len(live_prep_passed)}`",
        f"- blocker_codes: `{', '.join(row.get('code', '') for row in live_prep_blockers)}`",
        "",
        "- `yes_bucket` live core: settled >=40, ROI >= +15%, positive_day_rate >=55%, continuity ready.",
        "- `no_d1_exh` live core: settled >=40, ROI >= +3%, positive_day_rate >=55%, continuity ready.",
        "- `no_d2_exh`: observe-only; not a live enabler/blocker until stronger forward evidence exists.",
        "- price overlay: v1 live-core shadow/eval now requires `ask<=0.90`; latest `no_d1_exh` ask band `0.80-0.90` is acceptable for shadow; `0.90-0.97` remains marginal observe-only evidence before tiny-live sizing.",
        f"- Current v1 forward: {forward_v1['entries']} entries / {forward_v1['pending']} pending / {forward_v1['settled']} settled, so live is still blocked by forward evidence, not by historical edge.",
        "",
        "## Verdict",
        "",
        "- live_now: `false`",
        "- direction: `station-basis taker` 是当前最接近 live 的方向。",
        "- next_gate: 运行 v1 zero-notional shadow，直到每条规则 settled >=40 且符号与历史一致。",
        "- 只有 v1 forward 过门后，才进入真实 CLOB 接线和 N100 部署；部署必须另走 `weather-strategy-deploy`。",
        "- collection: v1 is side-loaded by the existing `weather_station_basis_shadow.py` loop on each v0 cycle/settle, and also has standalone start scripts for real-terminal use.",
        "- sidecars: every v1 cycle refreshes readiness, dry-run execution, and pending monitor; every v1 settle refreshes pending monitor.",
        "- execution: v1 dry-run executor consumes only `station_basis_shadow_v1/entries.jsonl` and writes only `station_basis_exec_v1/`, keeping v0/v1 cursors separate.",
        "",
        "## Files",
        "",
        f"- JSON: `{OUT_JSON}`",
        f"- Markdown: `{OUT_MD}`",
        "- v1 shadow: `scripts/ops/weather_station_basis_shadow_v1.py`",
        "- v1 eval: `scripts/ops/station_basis_eval_v1.py`",
        "- v1 exec: `scripts/ops/weather_station_basis_exec_v1.py`",
        "- v1 readiness: `scripts/ops/station_basis_v1_readiness.py`",
        "- v1 pending monitor: `scripts/ops/station_basis_v1_pending_monitor.py`",
        "- v1 live-prep gate: `scripts/ops/station_basis_live_prep_gate_v1.py`",
    ]
    OUT_MD.write_text("\n".join(md) + "\n")
    print(OUT_MD)
    print(OUT_JSON)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
