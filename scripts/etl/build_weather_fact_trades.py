#!/usr/bin/env python3
"""
build_weather_fact_trades.py

唯一派生层:从 weather.db 的规范化表构建 fact_trades 宽表。
grain = 一个 fill(每笔成交一行,含未结算)。

输出:
  - runtime/weather.db  的 fact_trades 表(幂等重建)
  - runtime/weather_edge_v1/market_data/research/fact_trades.parquet

设计文档: docs/WEATHER_FACT_TRADES_DESIGN.md
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "runtime" / "weather.db"
PARQUET_PATH = (
    ROOT / "runtime" / "weather_edge_v1" / "market_data" / "research" / "fact_trades.parquet"
)

# ---------------------------------------------------------------------------
# City → IANA timezone (contract §3)
# ---------------------------------------------------------------------------
CITY_TZ: dict[str, str] = {
    "Amsterdam": "Europe/Amsterdam",
    "Ankara": "Europe/Istanbul",
    "Atlanta": "America/New_York",
    "Austin": "America/Chicago",
    "Beijing": "Asia/Shanghai",
    "BuenosAires": "America/Argentina/Buenos_Aires",
    "Busan": "Asia/Seoul",
    "CapeTown": "Africa/Johannesburg",
    "Chengdu": "Asia/Shanghai",
    "Chicago": "America/Chicago",
    "Chongqing": "Asia/Shanghai",
    "Dallas": "America/Chicago",
    "Denver": "America/Denver",
    "Guangzhou": "Asia/Shanghai",
    "Helsinki": "Europe/Helsinki",
    "HongKong": "Asia/Hong_Kong",
    "Houston": "America/Chicago",
    "Istanbul": "Europe/Istanbul",
    "Jakarta": "Asia/Jakarta",
    "Jeddah": "Asia/Riyadh",
    "Karachi": "Asia/Karachi",
    "KualaLumpur": "Asia/Kuala_Lumpur",
    "LA": "America/Los_Angeles",
    "Lagos": "Africa/Lagos",
    "London": "Europe/London",
    "Lucknow": "Asia/Kolkata",
    "Madrid": "Europe/Madrid",
    "Manila": "Asia/Manila",
    "MexicoCity": "America/Mexico_City",
    "Miami": "America/New_York",
    "Milan": "Europe/Rome",
    "Moscow": "Europe/Moscow",
    "Munich": "Europe/Berlin",
    "NYC": "America/New_York",
    "PanamaCity": "America/Panama",
    "Paris": "Europe/Paris",
    "SanFrancisco": "America/Los_Angeles",
    "SaoPaulo": "America/Sao_Paulo",
    "Seattle": "America/Los_Angeles",
    "Seoul": "Asia/Seoul",
    "Shanghai": "Asia/Shanghai",
    "Shenzhen": "Asia/Shanghai",
    "Singapore": "Asia/Singapore",
    "Taipei": "Asia/Taipei",
    "TelAviv": "Asia/Jerusalem",
    "Tokyo": "Asia/Tokyo",
    "Warsaw": "Europe/Warsaw",
    "Wellington": "Pacific/Auckland",
    "Wuhan": "Asia/Shanghai",
}

BJ_TZ = ZoneInfo("Asia/Shanghai")

SNAPSHOT_DIR = ROOT / "runtime" / "weather_edge_v1" / "market_data" / "paper_snapshots"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts_to_date(ts_utc: str | None, tz: ZoneInfo) -> str | None:
    if not ts_utc:
        return None
    try:
        # Handle ISO strings with or without +00:00
        ts = ts_utc.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts).astimezone(tz)
        return dt.date().isoformat()
    except Exception:
        return None


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _binary_final_yes(v: Any) -> float | None:
    fp = _safe_float(v)
    if fp is None:
        return None
    if fp >= 0.99:
        return 1.0
    if fp <= 0.01:
        return 0.0
    return None


def _load_snapshot_prices() -> tuple[dict[tuple, dict], str | None]:
    """Load latest snapshot file and return (lookup, snapshot_ts_utc).

    lookup key: (condition_id, bracket, event_date) → {val_mid, val_bid}
    Returns ({}, None) if no snapshot files found.
    """
    snapshots = sorted(SNAPSHOT_DIR.glob("snapshot_*.json")) if SNAPSHOT_DIR.exists() else []
    if not snapshots:
        return {}, None
    latest = snapshots[-1]
    try:
        data = json.loads(latest.read_text())
    except Exception:
        return {}, None

    snap_ts = None
    lookup: dict[tuple, dict] = {}
    for rec in data.get("records", []):
        if not isinstance(rec, dict):
            continue
        if snap_ts is None:
            snap_ts = rec.get("ts_utc")
        cid = rec.get("condition_id") or ""
        bracket = str(rec.get("bracket") or "")
        event_date = str(rec.get("event_date") or "")
        if not (cid and bracket and event_date):
            continue
        bid = _safe_float(rec.get("yes_best_bid"))
        ask = _safe_float(rec.get("yes_best_ask"))
        mid = (bid + ask) / 2.0 if (bid is not None and ask is not None) else _safe_float(rec.get("market_yes_price"))
        lookup[(cid, bracket, event_date)] = {"val_mid": mid, "val_bid": bid}
    return lookup, snap_ts


def _derive_trade_class(execution_mode: str | None, fill_status: str | None) -> str:
    mode = (execution_mode or "").lower()
    status = (fill_status or "").lower()
    if mode == "live" and status == "filled":
        return "live_real"
    if mode == "live" and status == "simulated":
        return "live_simulated"
    if mode == "paper":
        return "paper"
    if mode == "snapshot_replay":
        return "snapshot_replay"
    return f"{mode}_{status}"


def _compute_pnl(
    side: str,
    price: float | None,
    final_yes: float | None,
    qty: float | None,
    fees: float | None,
) -> float | None:
    if price is None or final_yes is None or qty is None:
        return None
    f = fees or 0.0
    if side == "BUY_YES":
        return (final_yes - price) * qty - f
    if side == "BUY_NO":
        return ((1.0 - final_yes) - price) * qty - f
    if side == "SELL_YES":
        return (price - final_yes) * qty - f
    if side == "SELL_NO":
        return (price - (1.0 - final_yes)) * qty - f
    return None


# ---------------------------------------------------------------------------
# Load settlements into lookup structures
# ---------------------------------------------------------------------------

def _load_settlements(conn: sqlite3.Connection) -> tuple[
    dict[str, dict],          # by_token:   token_id → row
    dict[tuple, list[dict]],  # by_key_cid: (target_date, condition_id, bracket) → [rows]
    dict[tuple, list[dict]],  # by_key_mid: (target_date, market_id, bracket) → [rows]
]:
    rows = conn.execute(
        "SELECT settlement_id, target_date, condition_id, market_id, bracket, "
        "token_id, final_price, settlement_status FROM settlements"
    ).fetchall()
    cols = ["settlement_id", "target_date", "condition_id", "market_id", "bracket",
            "token_id", "final_price", "settlement_status"]
    by_token: dict[str, dict] = {}
    by_cid: dict[tuple, list[dict]] = {}
    by_mid: dict[tuple, list[dict]] = {}
    for r in rows:
        row = dict(zip(cols, r))
        tok = row.get("token_id")
        if tok:
            by_token[tok] = row
        key_cid = (row["target_date"], row["condition_id"], row["bracket"])
        key_mid = (row["target_date"], row["market_id"], row["bracket"])
        by_cid.setdefault(key_cid, []).append(row)
        by_mid.setdefault(key_mid, []).append(row)
    return by_token, by_cid, by_mid


def _match_settlement(
    token_id: str | None,
    target_date: str | None,
    condition_id: str | None,
    market_id: str | None,
    bracket: str | None,
    by_token: dict,
    by_cid: dict,
    by_mid: dict,
) -> tuple[str, dict | None, int]:
    """Returns (join_method, settlement_row_or_None, match_count)."""
    # Token-first
    if token_id and token_id in by_token:
        return "token", by_token[token_id], 1

    # Fallback via condition_id then market_id
    candidates: list[dict] = []
    key_cid = (target_date, condition_id, bracket)
    key_mid = (target_date, market_id, bracket)
    seen_ids: set[str] = set()
    for row in by_cid.get(key_cid, []):
        sid = row["settlement_id"]
        if sid not in seen_ids:
            candidates.append(row)
            seen_ids.add(sid)
    for row in by_mid.get(key_mid, []):
        sid = row["settlement_id"]
        if sid not in seen_ids:
            candidates.append(row)
            seen_ids.add(sid)

    if not candidates:
        return "none", None, 0

    # Deterministic dedup: sort by settlement_id, take first
    candidates.sort(key=lambda x: x["settlement_id"])
    return "fallback", candidates[0], len(candidates)


# ---------------------------------------------------------------------------
# Base SQL query
# ---------------------------------------------------------------------------

BASE_SQL = """
SELECT
  f.fill_id,
  f.execution_id,
  f.order_id,
  f.filled_shares      AS fill_qty,
  f.filled_price       AS fill_price,
  f.fees_usd,
  f.status             AS fill_status,
  f.filled_at_utc,
  f.created_at_utc     AS fill_created_at_utc,

  o.run_id,
  COALESCE(o.instance_id, oil.instance_id) AS instance_id,
  o.plan_id,
  o.venue,
  o.order_side         AS side,
  o.limit_price,
  o.entry_price        AS plan_price,
  o.shares             AS order_shares,
  o.notional,
  o.status             AS order_status,
  o.placed_at_utc,
  COALESCE(o.placed_at_utc, o.created_at_utc) AS order_ts_utc,

  p.signal_id,
  p.config_id,
  p.desired_shares,
  p.sizing_mode,
  p.entry_price_window,
  p.execution_policy,

  s.target_date,
  s.city,
  s.city_pool,
  s.icao,
  s.bracket,
  s.unit,
  s.signal_side,
  s.model_version,
  s.model_p_yes,
  s.forecast_source,
  s.market_price,
  s.edge,
  s.abs_edge,
  s.condition_id,
  s.market_id,
  s.token_id,
  s.hours_to_settle,
  s.snapshot_ts_utc,

  r.code_version,
  r.execution_mode,
  r.universe_id,
  r.producer_system    AS producer_system,
  r.producer_run_id    AS producer_run_id,

  sc.strategy_key,
  COALESCE(NULLIF(sd.strategy_name, ''), sc.strategy_key, sc.name) AS strategy_name,
  sc.name              AS config_name,
  sc.config_id         AS strategy_id
FROM fills f
JOIN orders o        ON o.execution_id = f.execution_id
JOIN plans p         ON p.plan_id      = o.plan_id
JOIN signals s       ON s.signal_id    = p.signal_id
JOIN runs r          ON r.run_id       = o.run_id
LEFT JOIN strategy_config sc ON sc.config_id = r.config_id
LEFT JOIN strategy_def sd ON sd.strategy_key = sc.strategy_key
LEFT JOIN order_instance_lineage oil ON oil.execution_id = o.execution_id
WHERE f.status IN ('filled', 'simulated')
"""

# ---------------------------------------------------------------------------
# DDL for fact_trades
# ---------------------------------------------------------------------------

FACT_DDL = """
CREATE TABLE IF NOT EXISTS fact_trades (
  -- grain / blood-line keys
  fill_id              TEXT PRIMARY KEY,
  execution_id         TEXT,
  order_id             TEXT,
  plan_id              TEXT,
  signal_id            TEXT,
  run_id               TEXT,
  config_id            TEXT,
  strategy_key         TEXT,
  strategy_id          TEXT,
  strategy_name        TEXT,
  config_name          TEXT,
  instance_id          TEXT,

  -- source / strategy dimensions
  execution_mode       TEXT,
  fill_status          TEXT,
  order_status         TEXT,
  trade_class          TEXT,
  code_version         TEXT,
  execution_policy     TEXT,
  sizing_mode          TEXT,
  venue                TEXT,
  producer_system      TEXT,
  producer_run_id      TEXT,
  universe_id          TEXT,

  -- market / signal dimensions
  city                 TEXT,
  city_pool            TEXT,
  icao                 TEXT,
  target_date          TEXT,
  bracket              TEXT,
  unit                 TEXT,
  side                 TEXT,
  signal_side          TEXT,
  forecast_source      TEXT,
  model_version        TEXT,
  condition_id         TEXT,
  market_id            TEXT,
  token_id             TEXT,
  market_key           TEXT,
  city_day_key         TEXT,

  -- time dimensions
  order_ts_utc         TEXT,
  order_date_bj        TEXT,
  order_date_local     TEXT,
  fill_ts_utc          TEXT,
  snapshot_ts_utc      TEXT,
  hours_to_settle      REAL,

  -- signal measures
  model_p_yes          REAL,
  market_price         REAL,
  edge                 REAL,
  abs_edge             REAL,

  -- pricing / quantity
  plan_price           REAL,
  limit_price          REAL,
  entry_price_window   TEXT,
  fill_price           REAL,
  fill_qty             REAL,
  desired_shares       REAL,
  order_shares         REAL,
  fees_usd             REAL,
  cost_usd             REAL,
  cost_usd_at_plan     REAL,
  notional             REAL,

  -- settlement audit
  settlement_id        TEXT,
  settlement_join_method TEXT,
  settlement_match_count INTEGER,
  settlement_status    TEXT,
  settled              INTEGER,
  final_yes            REAL,

  -- pnl / outcome (NULL if not settled)
  pnl_usd_at_fill      REAL,
  pnl_usd_at_plan      REAL,
  win_by_count         INTEGER,
  contract_won         INTEGER,
  bracket_hit          INTEGER,

  -- unsettled valuation (Phase 1.5, NULL in Phase 1)
  val_mid              REAL,
  val_bid              REAL,
  val_last_fill        REAL,
  unrealized_pnl_mid   REAL,
  val_snapshot_ts_utc  TEXT,

  -- build metadata
  fact_built_at_utc    TEXT
)
"""

# ---------------------------------------------------------------------------
# Unsettled valuation helper
# ---------------------------------------------------------------------------

def _snap_valuation(
    final_yes: float | None,
    side: str,
    fill_price: float | None,
    fill_qty: float | None,
    condition_id: str | None,
    bracket: str,
    event_date: str,
    snap_prices: dict[tuple, dict],
    snap_ts: str | None,
) -> dict:
    """Return valuation dict for the fact row.

    For settled fills: all val_ columns are None (settlement is the source of truth).
    For unsettled fills: look up current market price from latest snapshot.
    """
    base = {"val_mid": None, "val_bid": None, "val_last_fill": None,
            "unrealized_pnl_mid": None, "val_snapshot_ts_utc": None}
    if final_yes is not None:
        return base  # already settled — no need for market valuation

    key = (condition_id or "", bracket, event_date)
    snap = snap_prices.get(key)
    if snap is None:
        return base

    val_mid = snap.get("val_mid")
    val_bid = snap.get("val_bid")
    unrealized = _compute_pnl(side, fill_price, val_mid, fill_qty, 0.0) if val_mid is not None else None
    return {
        "val_mid": val_mid,
        "val_bid": val_bid,
        "val_last_fill": None,
        "unrealized_pnl_mid": unrealized,
        "val_snapshot_ts_utc": snap_ts,
    }


# ---------------------------------------------------------------------------
# Main build logic
# ---------------------------------------------------------------------------

def build(conn: sqlite3.Connection) -> tuple[list[dict], list[str]]:
    """Build all fact rows. Returns (rows, alerts)."""
    by_token, by_cid, by_mid = _load_settlements(conn)
    snap_prices, snap_ts = _load_snapshot_prices()

    cursor = conn.execute(BASE_SQL)
    base_cols = [d[0] for d in cursor.description]
    base_rows = [dict(zip(base_cols, r)) for r in cursor.fetchall()]

    now_utc = datetime.now(timezone.utc).isoformat()
    alerts: list[str] = []
    fact_rows: list[dict] = []

    for b in base_rows:
        fill_id = b["fill_id"]
        city = b.get("city") or ""
        side = b.get("side") or ""
        signal_side = b.get("signal_side") or ""
        fill_price = _safe_float(b.get("fill_price"))
        plan_price = _safe_float(b.get("plan_price"))
        fill_qty = _safe_float(b.get("fill_qty"))
        fees_usd = _safe_float(b.get("fees_usd")) or 0.0

        # trade_class
        trade_class = _derive_trade_class(b.get("execution_mode"), b.get("fill_status"))

        # alert: side vs signal_side mismatch (normalize: YES→BUY_YES, NO→BUY_NO)
        _norm = {"YES": {"BUY_YES", "SELL_YES"}, "NO": {"BUY_NO", "SELL_NO"}}
        signal_side_norm = _norm.get(signal_side, {signal_side})
        if side and signal_side_norm and side not in signal_side_norm:
            alerts.append(f"SIDE_MISMATCH fill_id={fill_id} side={side} signal_side={signal_side}")

        # derived cost cols
        cost_usd = (fill_price * fill_qty) if fill_price is not None and fill_qty is not None else None
        cost_usd_at_plan = (plan_price * fill_qty) if plan_price is not None and fill_qty is not None else None

        # date conversions
        order_ts_utc = b.get("order_ts_utc")
        fill_ts_utc = b.get("filled_at_utc") or b.get("fill_created_at_utc")
        order_date_bj = _ts_to_date(order_ts_utc, BJ_TZ)
        city_tz_str = CITY_TZ.get(city)
        order_date_local = _ts_to_date(order_ts_utc, ZoneInfo(city_tz_str)) if city_tz_str else None

        # derived keys
        market_id = b.get("market_id") or ""
        bracket = b.get("bracket") or ""
        market_key = f"{market_id}|{bracket}" if market_id else None
        target_date = b.get("target_date") or ""
        city_day_key = f"{city}|{target_date}" if city and target_date else None

        # settlement join
        join_method, sett, match_count = _match_settlement(
            b.get("token_id"),
            target_date or None,
            b.get("condition_id"),
            market_id or None,
            bracket or None,
            by_token, by_cid, by_mid,
        )

        # settlement fields
        settlement_id = sett["settlement_id"] if sett else None
        settlement_status = sett["settlement_status"] if sett else None
        settled = int(settlement_status == "settled") if settlement_status else 0

        # final_yes only for settled; non-{0,1} on settled rows → alert
        final_yes: float | None = None
        if settled and sett:
            final_yes = _binary_final_yes(sett.get("final_price"))
            if final_yes is None:
                alerts.append(
                    f"FINAL_YES_UNEXPECTED fill_id={fill_id} final_price={sett.get('final_price')} "
                    f"settlement_id={settlement_id}"
                )

        # pnl / outcome (only when settled and final_yes clean)
        pnl_at_fill = _compute_pnl(side, fill_price, final_yes, fill_qty, fees_usd) if final_yes is not None else None
        pnl_at_plan = _compute_pnl(side, plan_price, final_yes, fill_qty, fees_usd) if final_yes is not None else None
        win_by_count = int(pnl_at_fill > 0) if pnl_at_fill is not None else None
        contract_won = int(final_yes == 1.0) if final_yes is not None else None
        # bracket_hit = whether the temperature bracket won (YES resolved 1.0),
        # side-independent. Use win_by_count to check if YOUR position won.
        bracket_hit = int(final_yes == 1.0) if final_yes is not None else None

        fact_rows.append({
            # grain / blood-line
            "fill_id": fill_id,
            "execution_id": b.get("execution_id"),
            "order_id": b.get("order_id"),
            "plan_id": b.get("plan_id"),
            "signal_id": b.get("signal_id"),
            "run_id": b.get("run_id"),
            "config_id": b.get("config_id"),
            "strategy_key": b.get("strategy_key"),
            "strategy_id": b.get("strategy_id"),
            "strategy_name": b.get("strategy_name"),
            "config_name": b.get("config_name"),
            "instance_id": b.get("instance_id"),
            # source / strategy dims
            "execution_mode": b.get("execution_mode"),
            "fill_status": b.get("fill_status"),
            "order_status": b.get("order_status"),
            "trade_class": trade_class,
            "code_version": b.get("code_version"),
            "execution_policy": b.get("execution_policy"),
            "sizing_mode": b.get("sizing_mode"),
            "venue": b.get("venue"),
            "producer_system": b.get("producer_system"),
            "producer_run_id": b.get("producer_run_id"),
            "universe_id": b.get("universe_id"),
            # market / signal dims
            "city": city,
            "city_pool": b.get("city_pool"),
            "icao": b.get("icao"),
            "target_date": target_date or None,
            "bracket": bracket or None,
            "unit": b.get("unit"),
            "side": side or None,
            "signal_side": signal_side or None,
            "forecast_source": b.get("forecast_source"),
            "model_version": b.get("model_version"),
            "condition_id": b.get("condition_id"),
            "market_id": market_id or None,
            "token_id": b.get("token_id"),
            "market_key": market_key,
            "city_day_key": city_day_key,
            # time dims
            "order_ts_utc": order_ts_utc,
            "order_date_bj": order_date_bj,
            "order_date_local": order_date_local,
            "fill_ts_utc": fill_ts_utc,
            "snapshot_ts_utc": b.get("snapshot_ts_utc"),
            "hours_to_settle": _safe_float(b.get("hours_to_settle")),
            # signal measures
            "model_p_yes": _safe_float(b.get("model_p_yes")),
            "market_price": _safe_float(b.get("market_price")),
            "edge": _safe_float(b.get("edge")),
            "abs_edge": _safe_float(b.get("abs_edge")),
            # pricing / quantity
            "plan_price": plan_price,
            "limit_price": _safe_float(b.get("limit_price")),
            "entry_price_window": b.get("entry_price_window"),
            "fill_price": fill_price,
            "fill_qty": fill_qty,
            "desired_shares": _safe_float(b.get("desired_shares")),
            "order_shares": _safe_float(b.get("order_shares")),
            "fees_usd": fees_usd,
            "cost_usd": cost_usd,
            "cost_usd_at_plan": cost_usd_at_plan,
            "notional": _safe_float(b.get("notional")),
            # settlement audit
            "settlement_id": settlement_id,
            "settlement_join_method": join_method,
            "settlement_match_count": match_count,
            "settlement_status": settlement_status,
            "settled": settled,
            "final_yes": final_yes,
            # pnl / outcome
            "pnl_usd_at_fill": pnl_at_fill,
            "pnl_usd_at_plan": pnl_at_plan,
            "win_by_count": win_by_count,
            "contract_won": contract_won,
            "bracket_hit": bracket_hit,
            # valuation (Phase 1.5) — populated for unsettled fills from latest snapshot
            **_snap_valuation(
                final_yes, side, fill_price, fill_qty,
                b.get("condition_id"), str(b.get("bracket") or ""), str(b.get("target_date") or ""),
                snap_prices, snap_ts,
            ),
            # build metadata
            "fact_built_at_utc": now_utc,
        })

    return fact_rows, alerts


def write_db(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.execute("DROP TABLE IF EXISTS fact_trades")
    conn.execute(FACT_DDL)
    if not rows:
        return
    cols = list(rows[0].keys())
    placeholders = ",".join("?" for _ in cols)
    col_list = ",".join(cols)
    conn.executemany(
        f"INSERT INTO fact_trades ({col_list}) VALUES ({placeholders})",
        [[r[c] for c in cols] for r in rows],
    )
    conn.commit()


def write_parquet(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_parquet(path, index=False)


def print_summary(rows: list[dict], alerts: list[str]) -> None:
    total = len(rows)
    if total == 0:
        print("fact_trades: 0 rows (no fills found)")
        return

    by_class: dict[str, int] = {}
    by_method: dict[str, int] = {}
    settled_n = 0
    for r in rows:
        tc = r.get("trade_class") or "unknown"
        by_class[tc] = by_class.get(tc, 0) + 1
        jm = r.get("settlement_join_method") or "none"
        by_method[jm] = by_method.get(jm, 0) + 1
        if r.get("settled"):
            settled_n += 1

    print(f"\n=== fact_trades build summary ===")
    print(f"total rows  : {total}")
    print(f"settled     : {settled_n} ({settled_n/total*100:.1f}%)")
    print(f"unsettled   : {total-settled_n} ({(total-settled_n)/total*100:.1f}%)")
    print("by trade_class:")
    for k, v in sorted(by_class.items()):
        print(f"  {k:<22} {v:>5}")
    print("by settlement_join_method:")
    for k, v in sorted(by_method.items()):
        print(f"  {k:<22} {v:>5}")
    if alerts:
        print(f"\nalerts ({len(alerts)}):")
        for a in alerts[:20]:
            print(f"  [ALERT] {a}")
        if len(alerts) > 20:
            print(f"  ... {len(alerts)-20} more")
    else:
        print("\nalerts: none")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Build weather fact_trades table")
    ap.add_argument("--db-path", default=str(DB_PATH))
    ap.add_argument("--parquet-path", default=str(PARQUET_PATH))
    ap.add_argument("--no-parquet", action="store_true",
                    help="Write the DB table only; skip parquet export")
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute rows but do not write to DB or parquet")
    args = ap.parse_args()

    db_path = Path(args.db_path)
    parquet_path = Path(args.parquet_path)

    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        rows, alerts = build(conn)
        print_summary(rows, alerts)
        if args.dry_run:
            print("\n[dry-run] skipping write")
            return
        write_db(conn, rows)
        print(f"fact_trades written to DB: {db_path}")
        if args.no_parquet:
            print("fact_trades parquet export skipped (--no-parquet)")
        else:
            write_parquet(rows, parquet_path)
            print(f"fact_trades written to parquet: {parquet_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
