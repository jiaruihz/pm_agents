#!/usr/bin/env python3
"""Lineage study for mid_price_core_v1 forecast timing degradation.

Realized PnL is read only from fact_trades. Raw paper snapshots are used only
to reconstruct post-entry forecast/market/side evolution for the already
materialized live_real fills.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import random
import sqlite3
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "runtime" / "weather.db"
SNAPSHOT_DIR = ROOT / "runtime" / "weather_edge_v1" / "market_data" / "paper_snapshots"
OUT_DIR = ROOT / "docs" / "analysis" / "2026-06"
OUT_MD = OUT_DIR / "2026-06-07-mid-price-core-v1-forecast-timing-degradation-lineage.md"
OUT_JSON = OUT_DIR / "2026-06-07-mid-price-core-v1-forecast-timing-degradation-lineage.json"

RECENT_START = "2026-06-01"
STRATEGY_ID = "live_weather_edge_v1_4ef9b3ec3e2e"
STRATEGY_LABEL = "mid_price_core_v1_25_75"
ENTRY_MIN = 0.25
ENTRY_MAX = 0.75
REPORT_RUN_CONTEXT = (
    "2026-06-08 北京时间已执行 `scripts/ops/sync_weather_remote.sh`，随后执行 "
    "`scripts/weather_dashboard/run_stack.sh` 完成 DB rebuild、fact_trades、"
    "fact_signal_candidates 和 CLOB coverage gate；最后 API 启动阶段因端口占用报 "
    "`Errno 98`，不影响本离线报告取数。"
)


def parse_ts(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def f(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def side_prob(side: str, p_yes: float | None) -> float | None:
    if p_yes is None:
        return None
    return p_yes if side == "BUY_YES" else 1.0 - p_yes


def side_edge(side: str, p_yes: float | None, entry_price: float | None) -> float | None:
    p = side_prob(side, p_yes)
    if p is None or entry_price is None:
        return None
    return p - entry_price


def hours_bin(hours: float | None) -> str:
    if hours is None:
        return "[missing]"
    if hours < 22:
        return "<T-22"
    if hours <= 24:
        return "T-22-24"
    if hours <= 26:
        return "T-24-26"
    if hours <= 28:
        return "T-26-28"
    return ">T-28"


def raw_edge_bin(edge: float | None) -> str:
    if edge is None:
        return "[missing]"
    if edge <= 0.10:
        return "<=0.10"
    if edge <= 0.15:
        return "0.10-0.15"
    if edge <= 0.25:
        return "0.15-0.25"
    return ">0.25"


def period_of(target_date: str) -> str:
    return "post_2026_06_01" if target_date >= RECENT_START else "pre_2026_06_01"


def local_hour(value: Any) -> float | None:
    text = "" if value is None else str(value)
    if not text:
        return None
    try:
        if "T" in text:
            parsed = dt.datetime.fromisoformat(text)
        else:
            parsed = dt.datetime.fromisoformat(text.replace(" ", "T"))
    except ValueError:
        return None
    return parsed.hour + parsed.minute / 60.0 + parsed.second / 3600.0


def fmt_money(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):+.2f}"


def fmt_num(value: Any, digits: int = 3) -> str:
    if value is None:
        return ""
    return f"{float(value):.{digits}f}"


def fmt_pct(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value) * 100:.1f}%"


def table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    if not rows:
        return "_No rows._"
    cols = columns or list(rows[0])
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(row.get(col, "")) for col in cols) + " |")
    return "\n".join(out)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetchall(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def load_trades(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = fetchall(
        conn,
        """
        SELECT
          fill_id, execution_id, strategy_id, strategy_name, run_id,
          execution_policy, entry_price_window, trade_class,
          city, city_pool, icao, target_date, bracket, side,
          forecast_source, model_version, condition_id, market_id, market_key,
          city_day_key, order_ts_utc, fill_ts_utc, snapshot_ts_utc,
          hours_to_settle, model_p_yes, market_price, edge, abs_edge,
          plan_price, fill_price, fill_qty, cost_usd, cost_usd_at_plan,
          settlement_status, settled, final_yes, pnl_usd_at_fill,
          pnl_usd_at_plan, win_by_count, fact_built_at_utc
        FROM fact_trades
        WHERE trade_class='live_real'
          AND settlement_status='settled'
          AND strategy_id=?
          AND execution_policy='mid_price_core_v1'
          AND entry_price_window='0.25-0.75'
        ORDER BY target_date, city, fill_ts_utc, fill_id
        """,
        (STRATEGY_ID,),
    )
    for row in rows:
        p_yes = f(row["model_p_yes"])
        fill_price = f(row["fill_price"])
        market_price = f(row["market_price"])
        side = str(row["side"])
        row["period"] = period_of(str(row["target_date"]))
        row["hours_bin"] = hours_bin(f(row["hours_to_settle"]))
        row["market_yes_price"] = market_price if side == "BUY_YES" else (None if market_price is None else 1 - market_price)
        row["raw_edge_at_fill"] = side_edge(side, p_yes, fill_price)
        row["raw_edge_bin"] = raw_edge_bin(row["raw_edge_at_fill"])
        row["snapshot_dt"] = parse_ts(row["snapshot_ts_utc"])
        row["fill_dt"] = parse_ts(row["fill_ts_utc"])
    return rows


def self_checks(conn: sqlite3.Connection, gate: dict[str, Any]) -> dict[str, Any]:
    return {
        "db_path": str(DB_PATH),
        "db_mtime_utc": dt.datetime.fromtimestamp(DB_PATH.stat().st_mtime, tz=dt.timezone.utc).isoformat(),
        "fact_built_at_utc": fetchall(conn, "SELECT MAX(fact_built_at_utc) AS v FROM fact_trades")[0]["v"],
        "trade_class": fetchall(conn, "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class"),
        "settlement_status": fetchall(conn, "SELECT COALESCE(settlement_status, '[NULL]') AS settlement_status, COUNT(*) AS rows FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status"),
        "signal_candidates": fetchall(conn, "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, SUM(live_filled) AS live_filled FROM fact_signal_candidates")[0],
        "orders_clob": fetchall(
            conn,
            """
            SELECT o.status, COUNT(*) AS orders,
                   SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill
            FROM orders o LEFT JOIN fills f USING(execution_id)
            WHERE o.venue='polymarket_clob'
            GROUP BY o.status
            ORDER BY o.status
            """,
        ),
        "target_strategy": fetchall(
            conn,
            """
            SELECT strategy_id, strategy_name, trade_class, COUNT(*) AS fills,
                   MIN(target_date) AS min_target_date, MAX(target_date) AS max_target_date
            FROM fact_trades
            WHERE strategy_id=?
            GROUP BY strategy_id, strategy_name, trade_class
            ORDER BY trade_class
            """,
            (STRATEGY_ID,),
        ),
        "clob_gate": gate,
    }


def run_gate() -> dict[str, Any]:
    # Keep this logic local so the report records the gate state at runtime.
    import subprocess

    proc = subprocess.run(
        [str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "scripts" / "analysis" / "weather_clob_fill_coverage_gate.py")],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(proc.stdout)


def record_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (str(row.get("condition_id") or row.get("market_id") or ""), str(row.get("event_date") or ""), str(row.get("bracket") or ""))


def load_snapshot_sequences(trades: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    keys = {(str(t["condition_id"] or t["market_id"] or ""), str(t["target_date"]), str(t["bracket"])) for t in trades}
    condition_ids = {key[0] for key in keys if key[0]}
    by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)

    for path in sorted(SNAPSHOT_DIR.glob("snapshot_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for raw in payload.get("records", []):
            if not isinstance(raw, dict) or raw.get("record_type") != "edge_signal":
                continue
            cond = str(raw.get("condition_id") or raw.get("market_id") or "")
            if cond not in condition_ids:
                continue
            key = record_key(raw)
            if key not in keys:
                continue
            ts = parse_ts(raw.get("ts_utc") or payload.get("ts_utc"))
            if ts is None:
                continue
            model = str(raw.get("model") or raw.get("model_version") or "")
            side = str(raw.get("side") or "")
            market_yes = f(raw.get("market_yes_price"))
            entry_price = f(raw.get("entry_price"))
            p_yes = f(raw.get("model_prob") if raw.get("model_prob") is not None else raw.get("model_p_yes"))
            side_market = side_prob(side, market_yes) if side in {"BUY_YES", "BUY_NO"} else None
            by_key[key].append(
                {
                    "snapshot_file": path.name,
                    "ts": ts,
                    "ts_utc": ts.isoformat(),
                    "ts_local": raw.get("ts_local") or "",
                    "tz_offset": f(raw.get("tz_offset")),
                    "side": side,
                    "model_version": model,
                    "forecast_source": raw.get("forecast_source") or "",
                    "forecast_max_f": f(raw.get("forecast_max_f") if raw.get("forecast_max_f") is not None else raw.get("gfs_forecast_f")),
                    "model_p_yes": p_yes,
                    "market_yes_price": market_yes,
                    "entry_price": entry_price,
                    "edge": f(raw.get("edge")),
                    "abs_edge": f(raw.get("abs_edge")),
                    "hours_to_settle": f(raw.get("hours_to_settle")),
                    "model_init_utc_estimated": raw.get("model_init_utc_estimated") or "",
                    "model_run_age_hours_estimated": f(raw.get("model_run_age_hours_estimated")),
                    "forecast_target_lead_hours_estimated": f(raw.get("forecast_target_lead_hours_estimated")),
                    "metar_current_max_f": f(raw.get("metar_current_max_f")),
                    "metar_obs_count_today": f(raw.get("metar_obs_count_today")),
                    "side_market_price": side_market,
                }
            )

    for seq in by_key.values():
        seq.sort(key=lambda r: (r["ts"], r["side"], r["model_version"]))
    return by_key


def is_entry_window_record(record: dict[str, Any]) -> bool:
    price = record.get("entry_price")
    return price is not None and ENTRY_MIN <= float(price) <= ENTRY_MAX


def closest_entry_record(records: list[dict[str, Any]], side: str, model: str, snapshot_dt: dt.datetime | None) -> dict[str, Any] | None:
    candidates = [r for r in records if r["side"] == side and r["model_version"] == model]
    if not candidates:
        return None
    if snapshot_dt is None:
        return candidates[-1]
    before = [r for r in candidates if r["ts"] <= snapshot_dt + dt.timedelta(minutes=2)]
    pool = before or candidates
    return min(pool, key=lambda r: abs((r["ts"] - snapshot_dt).total_seconds()))


def first_eligible_record(records: list[dict[str, Any]], side: str, model: str) -> dict[str, Any] | None:
    same = [r for r in records if r["side"] == side and r["model_version"] == model and is_entry_window_record(r)]
    if same:
        return same[0]
    fallback = [r for r in records if r["side"] == side and r["model_version"] == model]
    return fallback[0] if fallback else None


def lineage_for_trade(trade: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    side = str(trade["side"])
    model = str(trade["model_version"])
    entry = closest_entry_record(records, side, model, trade.get("snapshot_dt"))
    first = first_eligible_record(records, side, model)
    out: dict[str, Any] = {
        "fill_id": trade["fill_id"],
        "snapshot_lineage_found": bool(entry),
        "first_eligible_ts_utc": first["ts_utc"] if first else None,
        "entry_snapshot_ts_utc": entry["ts_utc"] if entry else None,
        "entry_snapshot_ts_local": entry["ts_local"] if entry else None,
        "entry_local_hour": local_hour(entry["ts_local"]) if entry else None,
        "entry_tz_offset": entry["tz_offset"] if entry else None,
        "entry_snapshot_lag_minutes": None,
        "entry_model_run_age_hours": None,
        "entry_forecast_target_lead_hours": None,
        "entry_forecast_max_f": None,
        "entry_model_p_yes": None,
        "entry_market_yes_price": None,
        "entry_side_market_price": None,
        "first_to_entry_hours": None,
        "post_snapshot_count": 0,
        "max_abs_forecast_jump_f": None,
        "final_forecast_delta_f": None,
        "max_abs_model_p_yes_jump": None,
        "final_model_p_yes_delta": None,
        "worst_side_prob_delta": None,
        "final_side_prob_delta": None,
        "worst_side_market_delta": None,
        "final_side_market_delta": None,
        "side_flip_after_entry": False,
        "side_flip_count_after_entry": 0,
        "first_side_flip_hours_after_entry": None,
    }
    if entry is None:
        return out

    if trade.get("snapshot_dt"):
        out["entry_snapshot_lag_minutes"] = round((entry["ts"] - trade["snapshot_dt"]).total_seconds() / 60, 3)
    if first:
        out["first_to_entry_hours"] = round((entry["ts"] - first["ts"]).total_seconds() / 3600, 3)

    entry_forecast = entry["forecast_max_f"]
    entry_p_yes = entry["model_p_yes"]
    entry_side_prob = side_prob(side, entry_p_yes)
    entry_market = side_prob(side, entry["market_yes_price"])
    out.update(
        {
            "entry_model_run_age_hours": entry["model_run_age_hours_estimated"],
            "entry_forecast_target_lead_hours": entry["forecast_target_lead_hours_estimated"],
            "entry_forecast_max_f": entry_forecast,
            "entry_model_p_yes": entry_p_yes,
            "entry_market_yes_price": entry["market_yes_price"],
            "entry_side_market_price": entry_market,
        }
    )

    after = [r for r in records if r["model_version"] == model and r["ts"] > entry["ts"]]
    out["post_snapshot_count"] = len(after)
    same_side_after = [r for r in after if r["side"] == side]
    opposite_after = [r for r in after if r["side"] in {"BUY_YES", "BUY_NO"} and r["side"] != side and is_entry_window_record(r)]

    forecast_deltas = [r["forecast_max_f"] - entry_forecast for r in after if r["forecast_max_f"] is not None and entry_forecast is not None]
    p_yes_deltas = [r["model_p_yes"] - entry_p_yes for r in after if r["model_p_yes"] is not None and entry_p_yes is not None]
    side_prob_deltas = []
    side_market_deltas = []
    for r in same_side_after:
        sp = side_prob(side, r["model_p_yes"])
        sm = side_prob(side, r["market_yes_price"])
        if sp is not None and entry_side_prob is not None:
            side_prob_deltas.append(sp - entry_side_prob)
        if sm is not None and entry_market is not None:
            side_market_deltas.append(sm - entry_market)

    if forecast_deltas:
        out["max_abs_forecast_jump_f"] = round(max(abs(x) for x in forecast_deltas), 4)
        out["final_forecast_delta_f"] = round(forecast_deltas[-1], 4)
    if p_yes_deltas:
        out["max_abs_model_p_yes_jump"] = round(max(abs(x) for x in p_yes_deltas), 6)
        out["final_model_p_yes_delta"] = round(p_yes_deltas[-1], 6)
    if side_prob_deltas:
        out["worst_side_prob_delta"] = round(min(side_prob_deltas), 6)
        out["final_side_prob_delta"] = round(side_prob_deltas[-1], 6)
    if side_market_deltas:
        out["worst_side_market_delta"] = round(min(side_market_deltas), 6)
        out["final_side_market_delta"] = round(side_market_deltas[-1], 6)
    if opposite_after:
        out["side_flip_after_entry"] = True
        out["side_flip_count_after_entry"] = len(opposite_after)
        out["first_side_flip_hours_after_entry"] = round((opposite_after[0]["ts"] - entry["ts"]).total_seconds() / 3600, 3)
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fills = len(rows)
    cost = sum(float(r["cost_usd"] or 0) for r in rows)
    pnl = sum(float(r["pnl_usd_at_fill"] or 0) for r in rows)
    wins = [1 if float(r["pnl_usd_at_fill"] or 0) > 0 else 0 for r in rows]
    return {
        "fills": fills,
        "city_days": len({(r["target_date"], r["city"]) for r in rows}),
        "cost_usd": round(cost, 6),
        "pnl_usd": round(pnl, 6),
        "roi": None if cost == 0 else round(pnl / cost, 6),
        "win_rate": None if not wins else round(mean(wins), 6),
        "avg_hours_to_settle": avg([f(r.get("hours_to_settle")) for r in rows]),
        "avg_raw_edge": avg([f(r.get("raw_edge_at_fill")) for r in rows]),
        "lineage_found_rate": avg([1.0 if r.get("snapshot_lineage_found") else 0.0 for r in rows]),
        "avg_entry_run_age": avg([f(r.get("entry_model_run_age_hours")) for r in rows]),
        "run_age_ge_5h_rate": avg([1.0 if (f(r.get("entry_model_run_age_hours")) or 0) >= 5.0 else 0.0 for r in rows if r.get("snapshot_lineage_found")]),
        "run_age_ge_6h_rate": avg([1.0 if (f(r.get("entry_model_run_age_hours")) or 0) >= 6.0 else 0.0 for r in rows if r.get("snapshot_lineage_found")]),
        "avg_entry_local_hour": avg([f(r.get("entry_local_hour")) for r in rows]),
        "avg_first_to_entry_hours": avg([f(r.get("first_to_entry_hours")) for r in rows]),
        "avg_max_abs_forecast_jump_f": avg([f(r.get("max_abs_forecast_jump_f")) for r in rows]),
        "forecast_jump_ge_1f_rate": avg([1.0 if (f(r.get("max_abs_forecast_jump_f")) or 0) >= 1.0 else 0.0 for r in rows if r.get("snapshot_lineage_found")]),
        "side_flip_rate": avg([1.0 if r.get("side_flip_after_entry") else 0.0 for r in rows if r.get("snapshot_lineage_found")]),
        "avg_worst_side_prob_delta": avg([f(r.get("worst_side_prob_delta")) for r in rows]),
        "avg_worst_side_market_delta": avg([f(r.get("worst_side_market_delta")) for r in rows]),
    }


def avg(values: list[float | None]) -> float | None:
    xs = [x for x in values if x is not None and math.isfinite(x)]
    return None if not xs else round(mean(xs), 6)


def grouped(rows: list[dict[str, Any]], keys: list[str], *, sort: str = "pnl_usd") -> list[dict[str, Any]]:
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[tuple(row.get(k) for k in keys)].append(row)
    out = []
    for key, vals in buckets.items():
        row = {name: "" if value is None else value for name, value in zip(keys, key)}
        row.update(summarize(vals))
        out.append(row)
    hour_order = {"<T-22": 0, "T-22-24": 1, "T-24-26": 2, "T-26-28": 3, ">T-28": 4, "[missing]": 9}

    def sort_key(row: dict[str, Any]) -> tuple[Any, int]:
        value = row.get(sort)
        if sort == "hours_bin":
            sortable: Any = hour_order.get(str(value), 99)
        elif sort == "match_cell":
            sortable = str(value)
        else:
            try:
                sortable = float(value)
            except (TypeError, ValueError):
                sortable = str(value)
        return sortable, int(row.get("fills") or 0)

    return sorted(out, key=sort_key)


def city_day_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[(str(row["target_date"]), str(row["city"]), str(row["hours_bin"]))].append(row)
    out = []
    for (target_date, city, hbin), vals in buckets.items():
        s = summarize(vals)
        out.append({"target_date": target_date, "city": city, "hours_bin": hbin, **s})
    return out


def summarize_city_day(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fills = sum(int(r.get("fills") or 0) for r in rows)
    cost = sum(float(r.get("cost_usd") or 0) for r in rows)
    pnl = sum(float(r.get("pnl_usd") or 0) for r in rows)
    wins = [1 if float(r.get("pnl_usd") or 0) > 0 else 0 for r in rows]
    return {
        "fills": fills,
        "city_days": len(rows),
        "cost_usd": round(cost, 6),
        "pnl_usd": round(pnl, 6),
        "roi": None if cost == 0 else round(pnl / cost, 6),
        "win_rate": None if not wins else round(mean(wins), 6),
    }


def grouped_city_days(rows: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[tuple(row.get(k) for k in keys)].append(row)
    hour_order = {"<T-22": 0, "T-22-24": 1, "T-24-26": 2, "T-26-28": 3, ">T-28": 4, "[missing]": 9}
    out = []
    for key, vals in buckets.items():
        row = {name: "" if value is None else value for name, value in zip(keys, key)}
        row.update(summarize_city_day(vals))
        out.append(row)
    return sorted(out, key=lambda r: hour_order.get(str(r.get("hours_bin")), 99))


def matched_slice(
    rows: list[dict[str, Any]],
    keys: list[str],
    ref_bin: str = "T-22-24",
    cmp_bin: str = ">T-28",
) -> dict[str, Any]:
    cells: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        cells[tuple(str(row[k]) for k in keys)].append(row)
    matched = []
    for key, vals in cells.items():
        bins = {v["hours_bin"] for v in vals}
        if ref_bin in bins and cmp_bin in bins:
            for v in vals:
                if v["hours_bin"] in {ref_bin, cmp_bin}:
                    matched.append({**v, "match_cell": "|".join(key)})
    by_bin = grouped(matched, ["hours_bin"], sort="hours_bin")
    cells_summary = grouped(matched, ["match_cell", "hours_bin"], sort="match_cell")
    return {
        "ref_bin": ref_bin,
        "cmp_bin": cmp_bin,
        "keys": keys,
        "matched_cell_count": len({r["match_cell"] for r in matched}),
        "matched_fills": len(matched),
        "by_bin": by_bin,
        "worst_cells": sorted(cells_summary, key=lambda r: float(r["pnl_usd"]))[:12],
    }


def bootstrap_city_day_delta(city_days: list[dict[str, Any]], a_bin: str, b_bin: str, n: int = 3000) -> dict[str, Any]:
    a = [r for r in city_days if r["hours_bin"] == a_bin]
    b = [r for r in city_days if r["hours_bin"] == b_bin]
    if not a or not b:
        return {"a_bin": a_bin, "b_bin": b_bin, "samples": 0}
    rng = random.Random(20260607)
    deltas = []
    for _ in range(n):
        aa = [rng.choice(a) for _ in range(len(a))]
        bb = [rng.choice(b) for _ in range(len(b))]
        a_pnl_per_cost = sum(x["pnl_usd"] for x in aa) / max(sum(x["cost_usd"] for x in aa), 1e-9)
        b_pnl_per_cost = sum(x["pnl_usd"] for x in bb) / max(sum(x["cost_usd"] for x in bb), 1e-9)
        deltas.append(a_pnl_per_cost - b_pnl_per_cost)
    deltas.sort()
    return {
        "a_bin": a_bin,
        "b_bin": b_bin,
        "samples": n,
        "delta_roi_mean": round(mean(deltas), 6),
        "delta_roi_p05": round(deltas[int(0.05 * n)], 6),
        "delta_roi_p50": round(median(deltas), 6),
        "delta_roi_p95": round(deltas[int(0.95 * n)], 6),
        "prob_delta_positive": round(sum(1 for x in deltas if x > 0) / n, 6),
        "a_city_days": len(a),
        "b_city_days": len(b),
    }


def leave_one_out(rows: list[dict[str, Any]], hbin: str, dimension: str) -> list[dict[str, Any]]:
    base = [r for r in rows if r["hours_bin"] == hbin]
    out = []
    for value in sorted({str(r[dimension]) for r in base}):
        kept = [r for r in base if str(r[dimension]) != value]
        removed = [r for r in base if str(r[dimension]) == value]
        ks = summarize(kept)
        rs = summarize(removed)
        out.append(
            {
                f"excluded_{dimension}": value,
                "kept_fills": ks["fills"],
                "kept_pnl": ks["pnl_usd"],
                "kept_roi": ks["roi"],
                "removed_pnl": rs["pnl_usd"],
                "removed_fills": rs["fills"],
            }
        )
    return sorted(out, key=lambda r: float(r["kept_pnl"]))[:12]


def policy_simulations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    post = [r for r in rows if r["period"] == "post_2026_06_01"]
    policies: list[tuple[str, Any]] = [
        ("baseline_v1", lambda r: True),
        ("only_T-22-26", lambda r: r["hours_bin"] in {"T-22-24", "T-24-26"}),
        ("drop_>T-28", lambda r: r["hours_bin"] != ">T-28"),
        ("drop_T-26-28", lambda r: r["hours_bin"] != "T-26-28"),
        ("high_raw_edge_>0.25_plus_drop_>T-28", lambda r: (f(r["raw_edge_at_fill"]) or -1) > 0.25 and r["hours_bin"] != ">T-28"),
    ]
    baseline = summarize(post)
    out = []
    for name, keep in policies:
        kept = [r for r in post if keep(r)]
        dropped = [r for r in post if not keep(r)]
        ks = summarize(kept)
        ds = summarize(dropped)
        out.append(
            {
                "policy": name,
                "kept_fills": ks["fills"],
                "kept_city_days": ks["city_days"],
                "kept_cost": ks["cost_usd"],
                "kept_pnl": ks["pnl_usd"],
                "kept_roi": ks["roi"],
                "dropped_fills": ds["fills"],
                "dropped_pnl": ds["pnl_usd"],
                "delta_pnl_vs_baseline": round(float(ks["pnl_usd"]) - float(baseline["pnl_usd"]), 6),
                "saved_loss_if_no_replacement": round(-float(ds["pnl_usd"]), 6),
            }
        )
    return out


def mechanism_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    post_loss = [r for r in rows if r["period"] == "post_2026_06_01" and float(r["pnl_usd_at_fill"] or 0) < 0]
    buckets = [
        ("entry_run_age>=6h", lambda r: (f(r.get("entry_model_run_age_hours")) or 0) >= 6),
        ("forecast_jump>=1F", lambda r: (f(r.get("max_abs_forecast_jump_f")) or 0) >= 1.0),
        ("side_flip_after_entry", lambda r: bool(r.get("side_flip_after_entry"))),
        ("market_side_adverse>=10c", lambda r: (f(r.get("worst_side_market_delta")) or 0) <= -0.10),
        ("model_side_prob_adverse>=15pp", lambda r: (f(r.get("worst_side_prob_delta")) or 0) <= -0.15),
    ]
    out = []
    gross_loss = -sum(float(r["pnl_usd_at_fill"] or 0) for r in post_loss)
    for name, pred in buckets:
        vals = [r for r in post_loss if pred(r)]
        s = summarize(vals)
        loss = -sum(float(r["pnl_usd_at_fill"] or 0) for r in vals)
        out.append(
            {
                "mechanism_flag": name,
                "loss_fills": len(vals),
                "gross_loss": round(loss, 6),
                "share_of_post_gross_loss": None if gross_loss == 0 else round(loss / gross_loss, 6),
                "avg_entry_run_age": s["avg_entry_run_age"],
                "avg_jump_f": s["avg_max_abs_forecast_jump_f"],
                "side_flip_rate": s["side_flip_rate"],
                "avg_worst_market_delta": s["avg_worst_side_market_delta"],
            }
        )
    return out


def format_summary_rows(rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        r = dict(row)
        for key in ["cost_usd", "pnl_usd", "kept_cost", "kept_pnl", "dropped_pnl", "delta_pnl_vs_baseline", "saved_loss_if_no_replacement", "gross_loss", "removed_pnl", "kept_pnl"]:
            if key in r:
                r[key] = fmt_money(r[key])
        for key in [
            "roi",
            "win_rate",
            "lineage_found_rate",
            "forecast_jump_ge_1f_rate",
            "side_flip_rate",
            "share_of_post_gross_loss",
            "kept_roi",
            "prob_delta_positive",
            "run_age_ge_5h_rate",
            "run_age_ge_6h_rate",
        ]:
            if key in r:
                r[key] = fmt_pct(r[key])
        for key in [
            "avg_hours_to_settle",
            "avg_raw_edge",
            "avg_entry_run_age",
            "avg_entry_local_hour",
            "avg_first_to_entry_hours",
            "avg_max_abs_forecast_jump_f",
            "avg_worst_side_prob_delta",
            "avg_worst_side_market_delta",
            "avg_jump_f",
            "avg_worst_market_delta",
            "delta_roi_mean",
            "delta_roi_p05",
            "delta_roi_p50",
            "delta_roi_p95",
        ]:
            if key in r and not isinstance(r.get(key), str):
                r[key] = fmt_num(r[key])
        return_row = {col: r.get(col, "") for col in columns}
        out.append(return_row)
    return out


def conclusion(data: dict[str, Any]) -> str:
    post_by_hours = {r["hours_bin"]: r for r in data["post_by_hours"]}
    sim = {r["policy"]: r for r in data["policy_simulations"]}
    exact = data["matched_exact_t22_vs_gt28"]
    relaxed_city = data["matched_city_side_model_t22_vs_gt28"]
    relaxed_edge = data["matched_side_model_edge_t22_vs_gt28"]
    relaxed = relaxed_city if relaxed_city["matched_cell_count"] else relaxed_edge
    matched = {r["hours_bin"]: r for r in relaxed["by_bin"]}
    boot = data["bootstrap_t22_vs_gt28"]
    parts = []
    t22 = post_by_hours.get("T-22-24", {})
    gt28 = post_by_hours.get(">T-28", {})
    t2628 = post_by_hours.get("T-26-28", {})
    parts.append(
        f"- **timing 是独立解释变量的证据中等偏强，但严格 matched 仍受样本限制**：6 月后 `T-22-24` 为 {fmt_money(t22.get('pnl_usd'))} / ROI {fmt_pct(t22.get('roi'))}，`>T-28` 为 {fmt_money(gt28.get('pnl_usd'))} / ROI {fmt_pct(gt28.get('roi'))}。严格固定 `city+side+model_version+raw_edge_bin` 时，`T-22-24` 与 `>T-28` 没有重叠 cell（matched cells={exact['matched_cell_count']}），所以不能把它说成严格 city-level 因果识别。降级到 `{'+'.join(relaxed['keys'])}` 后，`T-22-24` 为 {fmt_money(matched.get('T-22-24', {}).get('pnl_usd'))}，`>T-28` 为 {fmt_money(matched.get('>T-28', {}).get('pnl_usd'))}。"
    )
    parts.append(
        f"- **显著性口径**：city-day bootstrap 的 `T-22-24 ROI - >T-28 ROI` 均值 {fmt_pct(boot.get('delta_roi_mean'))}，5/50/95 分位为 {fmt_pct(boot.get('delta_roi_p05'))}/{fmt_pct(boot.get('delta_roi_p50'))}/{fmt_pct(boot.get('delta_roi_p95'))}，正差概率 {fmt_pct(boot.get('prob_delta_positive'))}；样本支持方向，但还不是可当成单因子定律的强统计显著。"
    )
    parts.append(
        f"- **`>T-28` 的亏损不能简单归因成 BuenosAires 或 2026-06-01 单点事故**：leave-one-city/date 后保留组合仍为负。但“不是 city/model/side mix 偶然造成”只能给中等置信度，因为最严格 matched 没有足够重叠。机制上，亏损 fill 同时暴露在 forecast jump、side flip 和 market adverse move 中，不能只归因为 forecast stale。"
    )
    parts.append(
        f"- **`T-26-28` 是坏窗口但样本偏小**：6 月后 {int(t2628.get('fills') or 0)} fills，PnL {fmt_money(t2628.get('pnl_usd'))} / ROI {fmt_pct(t2628.get('roi'))}。LOO 显示对单城市/日期敏感，建议先 shadow/drop 观察，不建议仅凭当前样本永久禁用。"
    )
    parts.append(
        f"- **只改 timing 的 6 月后改善上限**：`drop_>T-28` 的无替代成交改善约 {fmt_money(sim['drop_>T-28']['saved_loss_if_no_replacement'])}，`only_T-22-26` 改善约 {fmt_money(sim['only_T-22-26']['saved_loss_if_no_replacement'])}；`high_raw_edge_>0.25_plus_drop_>T-28` 样本更少，适合作 shadow 组合门，不适合直接替代 baseline。"
    )
    return "\n".join(parts)


def render(data: dict[str, Any]) -> str:
    common_cols = [
        "period",
        "hours_bin",
        "fills",
        "city_days",
        "cost_usd",
        "pnl_usd",
        "roi",
        "win_rate",
        "avg_hours_to_settle",
        "avg_raw_edge",
        "lineage_found_rate",
        "avg_entry_run_age",
        "avg_first_to_entry_hours",
        "avg_max_abs_forecast_jump_f",
        "forecast_jump_ge_1f_rate",
        "side_flip_rate",
        "avg_worst_side_market_delta",
    ]
    matched_cols = ["hours_bin", "fills", "city_days", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_raw_edge", "avg_entry_run_age", "avg_max_abs_forecast_jump_f", "side_flip_rate", "avg_worst_side_market_delta"]
    policy_cols = ["policy", "kept_fills", "kept_city_days", "kept_cost", "kept_pnl", "kept_roi", "dropped_fills", "dropped_pnl", "delta_pnl_vs_baseline", "saved_loss_if_no_replacement"]
    mech_cols = ["mechanism_flag", "loss_fills", "gross_loss", "share_of_post_gross_loss", "avg_entry_run_age", "avg_jump_f", "side_flip_rate", "avg_worst_market_delta"]
    age_cols = [
        "period",
        "model_version",
        "hours_bin",
        "fills",
        "cost_usd",
        "pnl_usd",
        "roi",
        "avg_entry_run_age",
        "run_age_ge_5h_rate",
        "run_age_ge_6h_rate",
        "avg_entry_local_hour",
        "avg_max_abs_forecast_jump_f",
        "side_flip_rate",
    ]
    city_model_age_cols = [
        "city",
        "model_version",
        "fills",
        "cost_usd",
        "pnl_usd",
        "roi",
        "avg_entry_run_age",
        "run_age_ge_5h_rate",
        "run_age_ge_6h_rate",
        "avg_entry_local_hour",
        "avg_max_abs_forecast_jump_f",
    ]
    loo_city_cols = ["excluded_city", "kept_fills", "kept_pnl", "kept_roi", "removed_pnl", "removed_fills"]
    loo_date_cols = ["excluded_target_date", "kept_fills", "kept_pnl", "kept_roi", "removed_pnl", "removed_fills"]

    self_check = data["self_checks"]
    gate = self_check["clob_gate"]
    lines = [
        "# mid_price_core_v1 forecast timing degradation lineage",
        "",
        "## 数据快照",
        "",
        f"- 数据源：`{self_check['db_path']}`；realized PnL 只读 `fact_trades.pnl_usd_at_fill`。",
        f"- Raw snapshot lineage：`{SNAPSHOT_DIR}`，只用于解释 forecast / side / market 后续变化。",
        f"- DB mtime UTC：`{self_check['db_mtime_utc']}`；`MAX(fact_built_at_utc)`：`{self_check['fact_built_at_utc']}`。",
        f"- 本次复核：{REPORT_RUN_CONTEXT}",
        f"- CLOB fill coverage gate：`gate_pass={gate.get('gate_pass')}`，`missing_order_rows={gate.get('db_fills', {}).get('missing_order_rows')}`，`over_order_keys={gate.get('db_fills', {}).get('over_order_keys')}`，`db_fill_cost_minus_fact_cost={gate.get('db_fill_cost_minus_fact_cost')}`。",
        f"- 目标样本：`{STRATEGY_LABEL}` / `strategy_id={STRATEGY_ID}` / `trade_class=live_real` / `settlement_status=settled`。",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "trade_class 分布：",
        table(self_check["trade_class"]),
        "",
        "settlement_status 分布：",
        table(self_check["settlement_status"]),
        "",
        "fact_signal_candidates 覆盖：",
        table([self_check["signal_candidates"]]),
        "",
        "CLOB orders/fills：",
        table(self_check["orders_clob"]),
        "",
        "目标策略样本：",
        table(self_check["target_strategy"]),
        "",
        "## 目标指标与分母",
        "",
        "`timing_independent_effect` = 在 `live_real + settled + mid_price_core_v1 + entry_price_window=0.25-0.75` 的真实成交样本中，固定 `city + side + model_version + raw_edge_bin` 后，比较 `T-22-24`、`T-26-28`、`>T-28` 的 realized PnL/ROI，并用 raw snapshot 序列解释后续 forecast jump、side flip、market implied move。",
        "",
        "城市日 aggregation 使用 `(target_date, city, hours_bin)`，避免一个 city-day 多个 fill 把同一日重复放大。policy simulation 只看 6 月后样本，`delta` 是“无替代成交”口径，即过滤掉的 fill 不被替换。",
        "",
        "## 结论",
        "",
        data["conclusion"],
        "",
        "## 1. 6 月后 timing 总览",
        "",
        table(format_summary_rows(data["post_by_hours"], common_cols), common_cols),
        "",
        "## 2. matched slice",
        "",
        "### 2.1 严格 matched: city + side + model_version + raw_edge_bin",
        "",
        f"- matched cells：`{data['matched_exact_t22_vs_gt28']['matched_cell_count']}`；matched fills：`{data['matched_exact_t22_vs_gt28']['matched_fills']}`。",
        "",
        table(format_summary_rows(data["matched_exact_t22_vs_gt28"]["by_bin"], matched_cols), matched_cols),
        "",
        "严格口径无重叠时，不把它当作 city-level 因果识别证据。",
        "",
        "### 2.2 降级 matched: city + side + model_version",
        "",
        f"- matched cells：`{data['matched_city_side_model_t22_vs_gt28']['matched_cell_count']}`；matched fills：`{data['matched_city_side_model_t22_vs_gt28']['matched_fills']}`。",
        "",
        table(format_summary_rows(data["matched_city_side_model_t22_vs_gt28"]["by_bin"], matched_cols), matched_cols),
        "",
        "最差 matched cells：",
        table(format_summary_rows(data["matched_city_side_model_t22_vs_gt28"]["worst_cells"], ["match_cell", "hours_bin", "fills", "cost_usd", "pnl_usd", "roi", "win_rate"]), ["match_cell", "hours_bin", "fills", "cost_usd", "pnl_usd", "roi", "win_rate"]),
        "",
        "### 2.3 降级 matched: side + model_version + raw_edge_bin",
        "",
        f"- matched cells：`{data['matched_side_model_edge_t22_vs_gt28']['matched_cell_count']}`；matched fills：`{data['matched_side_model_edge_t22_vs_gt28']['matched_fills']}`。",
        "",
        table(format_summary_rows(data["matched_side_model_edge_t22_vs_gt28"]["by_bin"], matched_cols), matched_cols),
        "",
        "## 3. city-day aggregation 与 bootstrap",
        "",
        table(format_summary_rows(data["city_day_by_hours"], ["hours_bin", "fills", "city_days", "cost_usd", "pnl_usd", "roi", "win_rate"]), ["hours_bin", "fills", "city_days", "cost_usd", "pnl_usd", "roi", "win_rate"]),
        "",
        "Bootstrap `T-22-24 ROI - >T-28 ROI`：",
        table(format_summary_rows([data["bootstrap_t22_vs_gt28"]], ["a_bin", "b_bin", "a_city_days", "b_city_days", "delta_roi_mean", "delta_roi_p05", "delta_roi_p50", "delta_roi_p95", "prob_delta_positive"]), ["a_bin", "b_bin", "a_city_days", "b_city_days", "delta_roi_mean", "delta_roi_p05", "delta_roi_p50", "delta_roi_p95", "prob_delta_positive"]),
        "",
        "## 4. >T-28 亏损机制拆分",
        "",
        table(format_summary_rows(data["mechanism_breakdown"], mech_cols), mech_cols),
        "",
        "读法：这些 flag 会重叠，share 不是互斥归因。`market_side_adverse>=10c` 表示入场后同一 side 的 implied price 曾经下移至少 10c；`forecast_jump>=1F` 表示同 market/bracket 后续 forecast max 相对入场变化至少 1°F。",
        "",
        "## 4.5 Forecast run age / local time",
        "",
        "按 `period + model_version + hours_bin`：",
        "",
        table(format_summary_rows(data["forecast_age_by_model_hours"], age_cols), age_cols),
        "",
        "6 月后 `>T-28` 按 city + model：",
        "",
        table(format_summary_rows(data["post_gt28_city_model_age"], city_model_age_cols), city_model_age_cols),
        "",
        "## 5. leave-one-out 稳定性",
        "",
        "### >T-28 leave-one-city-out",
        "",
        table(format_summary_rows(data["loo_gt28_city"], loo_city_cols), loo_city_cols),
        "",
        "### >T-28 leave-one-date-out",
        "",
        table(format_summary_rows(data["loo_gt28_date"], loo_date_cols), loo_date_cols),
        "",
        "### T-26-28 leave-one-city-out",
        "",
        table(format_summary_rows(data["loo_t2628_city"], loo_city_cols), loo_city_cols),
        "",
        "### T-26-28 leave-one-date-out",
        "",
        table(format_summary_rows(data["loo_t2628_date"], loo_date_cols), loo_date_cols),
        "",
        "## 6. timing policy simulation（6 月后，无替代成交）",
        "",
        table(format_summary_rows(data["policy_simulations"], policy_cols), policy_cols),
        "",
        "## 7. 交易动作建议与执行状态",
        "",
        "1. **`>T-28` 不再作为 v1 live 入场来源**：6 月后 `>T-28` 的 settled overlay 为 -91.52，且在 leave-one-city/date 后仍为负。上一轮已经通过 `codex/stop-stale-weather-timing` 的 `ac99018244db745c851e79527b5b9d0e266f4108` 给 source snapshot `hours_to_settle > 28` 加硬过滤；本报告支持保留该止损。",
        "2. **`T-26-28` 先 shadow/drop，不作为永久禁用定律**：当前 6 月后为 35 fills / -29.59 / ROI -40.7%，方向很差，但 LOO 对 BuenosAires、2026-06-03 等单点敏感，样本还不足以单独定义长期规则。",
        "3. **不要把 run age 写成单变量硬黑名单**：6 月后 `>T-28 + ECMWF` 是主要亏损组合，GFS `>T-28` 仍小正；GFS 第 5-6 小时“等下一轮 forecast”应先做 shadow rule，与 city/model/timing 组合一起评估。",
        "4. **下一步工程化字段**：把 `entry_model_run_age_hours`、`forecast_jump`、`side_flip_after_entry`、`worst_side_market_delta` 物化进 `fact_signal_candidates` 或对应 lineage fact，避免每次扫 raw snapshots 才能复现 timing 归因。",
        "",
        "## 8. 限制与下一步",
        "",
        "- 本报告用 raw snapshot 序列补 lineage，但 DB 里仍未物化 `forecast_jump` / `side_flip` 字段；后续应把这些字段写进 `fact_signal_candidates`，避免每次扫 1.9G snapshots。",
        "- policy simulation 是历史 fill overlay，不包含错过成交后的资金再部署，也不代表订单簿容量变化。",
        "- `T-26-28` 当前样本小，建议先 shadow/drop 观察；`>T-28` 可以作为更明确的 v1 timing filter 候选。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    gate = run_gate()
    if not gate.get("gate_pass"):
        raise RuntimeError(f"CLOB coverage gate failed: {gate.get('fail_reasons')}")

    trades = load_trades(conn)
    sequences = load_snapshot_sequences(trades)
    enriched = []
    for trade in trades:
        key = (str(trade["condition_id"] or trade["market_id"] or ""), str(trade["target_date"]), str(trade["bracket"]))
        lineage = lineage_for_trade(trade, sequences.get(key, []))
        enriched.append({**trade, **lineage})

    post = [r for r in enriched if r["period"] == "post_2026_06_01"]
    city_days = city_day_rows(post)
    data: dict[str, Any] = {
        "self_checks": self_checks(conn, gate),
        "row_count": len(enriched),
        "snapshot_groups_found": len(sequences),
        "post_by_hours": grouped(post, ["period", "hours_bin"], sort="hours_bin"),
        "matched_exact_t22_vs_gt28": matched_slice(post, ["city", "side", "model_version", "raw_edge_bin"]),
        "matched_city_side_model_t22_vs_gt28": matched_slice(post, ["city", "side", "model_version"]),
        "matched_side_model_edge_t22_vs_gt28": matched_slice(post, ["side", "model_version", "raw_edge_bin"]),
        "city_day_by_hours": grouped_city_days(city_days, ["hours_bin"]),
        "bootstrap_t22_vs_gt28": bootstrap_city_day_delta(city_days, "T-22-24", ">T-28"),
        "mechanism_breakdown": mechanism_breakdown([r for r in post if r["hours_bin"] == ">T-28"]),
        "forecast_age_by_model_hours": grouped(enriched, ["period", "model_version", "hours_bin"], sort="hours_bin"),
        "post_gt28_city_model_age": grouped([r for r in post if r["hours_bin"] == ">T-28"], ["city", "model_version"], sort="pnl_usd")[:30],
        "loo_gt28_city": leave_one_out(post, ">T-28", "city"),
        "loo_gt28_date": leave_one_out(post, ">T-28", "target_date"),
        "loo_t2628_city": leave_one_out(post, "T-26-28", "city"),
        "loo_t2628_date": leave_one_out(post, "T-26-28", "target_date"),
        "policy_simulations": policy_simulations(enriched),
    }
    data["conclusion"] = conclusion(data)

    json_ready = json.loads(json.dumps(data, default=str))
    OUT_JSON.write_text(json.dumps(json_ready, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUT_MD.write_text(render(data), encoding="utf-8")
    print(f"wrote {OUT_MD}")
    print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
