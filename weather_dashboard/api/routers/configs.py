"""Strategy configs and universes endpoints."""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Annotated, Optional
from urllib import error as urlerror
from urllib import parse, request

from fastapi import APIRouter, Depends, HTTPException, Query
import sqlite3

from weather_dashboard.api.deps import get_db
from weather_dashboard.api.schemas import ConfigRow, UniverseRow, SettlementRow

router = APIRouter(tags=["registry"])

Db = Annotated[sqlite3.Connection, Depends(get_db)]


# ── Strategies (per-config aggregated stats) ──────────────────────────────────

@router.get("/strategies")
def list_strategies(db: Db, state: str = Query("all")):
    """Per-strategy aggregated performance stats.

    A strategy = one canonical config_id (see config_aliases). Old fragmented
    config_ids are rolled up to their canonical row so each logical strategy
    appears exactly once.

    `state` filters runs by state: all (default) / live / paper / explore.
    """
    _validate_state(state)
    state_filter = "" if state == "all" else f" AND r.state = '{state}'"
    having_filter = "" if state == "all" else "HAVING COUNT(DISTINCT r.run_id) > 0"
    rows = db.execute(
        f"""
        SELECT
            c.config_id,
            c.name,
            c.params,
            c.created_at_utc,
            json_extract(c.params, '$.execution_policy') AS execution_policy,

            COUNT(DISTINCT r.run_id)                                          AS num_runs,
            MAX(r.started_at_utc)                                             AS latest_run_at,
            COUNT(DISTINCT CASE WHEN r.state = 'live'  THEN r.run_id END)    AS live_run_count,
            COUNT(DISTINCT CASE WHEN r.state = 'paper' THEN r.run_id END)    AS paper_run_count,

            COUNT(DISTINCT f.fill_id)                                   AS total_trades,
            COUNT(DISTINCT CASE
                WHEN s.final_price IS NOT NULL THEN f.fill_id
                ELSE NULL
            END)                                                         AS settled_trades,

            SUM(CASE
                WHEN s.final_price IS NULL THEN 0
                WHEN o.order_side = 'BUY_YES'
                    THEN CAST(f.filled_shares AS REAL)
                       * (CAST(s.final_price AS REAL) - CAST(f.filled_price AS REAL))
                WHEN o.order_side = 'BUY_NO'
                    THEN CAST(f.filled_shares AS REAL)
                       * ((1 - CAST(s.final_price AS REAL)) - CAST(f.filled_price AS REAL))
                ELSE 0
            END)                                                         AS total_pnl_usd,

            SUM(CASE
                WHEN s.final_price IS NULL THEN 0
                WHEN o.order_side = 'BUY_YES'
                 AND CAST(f.filled_shares AS REAL)
                   * (CAST(s.final_price AS REAL) - CAST(f.filled_price AS REAL)) > 0
                    THEN 1
                WHEN o.order_side = 'BUY_NO'
                 AND CAST(f.filled_shares AS REAL)
                   * ((1 - CAST(s.final_price AS REAL)) - CAST(f.filled_price AS REAL)) > 0
                    THEN 1
                ELSE 0
            END)                                                         AS win_trades,

            SUM(CAST(f.filled_shares AS REAL) * CAST(f.filled_price AS REAL))
                                                                         AS capital_deployed_usd

        FROM strategy_config c
        JOIN config_aliases ca ON ca.canonical_config_id = c.config_id
        LEFT JOIN runs r      ON r.config_id     = ca.alias_config_id{state_filter}
        LEFT JOIN orders o    ON o.run_id        = r.run_id
        LEFT JOIN fills f     ON f.execution_id  = o.execution_id
                             AND f.status        = 'filled'
        LEFT JOIN plans p     ON p.plan_id       = o.plan_id
        LEFT JOIN signals sig ON sig.signal_id   = p.signal_id
        LEFT JOIN {_SETTLEMENTS_DEDUP} s
               ON sig.target_date = s.target_date
              AND sig.condition_id = s.condition_id
              AND sig.bracket      = s.bracket
        WHERE c.config_id IN (SELECT DISTINCT canonical_config_id FROM config_aliases)
        GROUP BY c.config_id
        {having_filter}
        ORDER BY latest_run_at DESC
        """
    ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        try:
            d["params"] = json.loads(d["params"]) if d["params"] else {}
        except Exception:
            d["params"] = {}

        cap = d["capital_deployed_usd"] or 0.0
        pnl = d["total_pnl_usd"] or 0.0
        settled = d["settled_trades"] or 0
        wins = d["win_trades"] or 0

        d["total_pnl_usd"] = round(pnl, 4)
        d["capital_deployed_usd"] = round(cap, 4)
        d["roi"] = round(pnl / cap, 6) if cap > 0 else None
        d["win_rate"] = round(wins / settled, 4) if settled > 0 else None
        result.append(d)
    return result



# ── Single strategy detail ────────────────────────────────────────────────────

_PNL_CASE = """
    CASE
        WHEN s.final_price IS NULL THEN 0
        WHEN o.order_side = 'BUY_YES'
            THEN CAST(f.filled_shares AS REAL) * (CAST(s.final_price AS REAL) - CAST(f.filled_price AS REAL))
        WHEN o.order_side = 'BUY_NO'
            THEN CAST(f.filled_shares AS REAL) * ((1 - CAST(s.final_price AS REAL)) - CAST(f.filled_price AS REAL))
        ELSE 0
    END
"""

_WIN_CASE = f"""
    CASE WHEN ({_PNL_CASE}) > 0 THEN 1 ELSE 0 END
"""

_SETTLEMENTS_DEDUP = """
    (
        SELECT
            target_date,
            condition_id,
            bracket,
            MAX(market_id) AS market_id,
            MAX(token_id) AS token_id,
            MAX(final_price) AS final_price,
            MAX(settlement_status) AS settlement_status
        FROM settlements
        GROUP BY target_date, condition_id, bracket
    )
"""


# Allowed values for the ?state= filter. "all" = no filter; any specific value
# restricts to that runs.state (typically 'live', 'paper', or 'explore').
_ALLOWED_STATES = {"all", "live", "paper", "explore"}


def _validate_state(state: str) -> str:
    if state not in _ALLOWED_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"state must be one of {sorted(_ALLOWED_STATES)}",
        )
    return state


def _base_joins(state: str = "all") -> str:
    # Unified PnL caliber (see docs/WEATHER_DATA_PIPELINE.md):
    #   one strategy identity (= canonical config_id) -> all aliased runs
    #   -> filled fills -> per-token settlement -> sum.
    # Strategy identity goes through config_aliases so old fragmented config_ids
    # roll up to the canonical row. status='filled' keeps real CLOB fills +
    # historical paper fills (excludes 'simulated' parallel paper).
    state_filter = "" if state == "all" else f" AND r.state = '{state}'"
    return f"""
        FROM config_aliases ca
        JOIN runs r ON r.config_id = ca.alias_config_id{state_filter}
        LEFT JOIN orders o ON o.run_id = r.run_id
        LEFT JOIN fills f  ON f.execution_id = o.execution_id AND f.status = 'filled'
        LEFT JOIN plans p  ON p.plan_id = o.plan_id
        LEFT JOIN signals sig ON sig.signal_id = p.signal_id
        LEFT JOIN {_SETTLEMENTS_DEDUP} s ON sig.target_date = s.target_date
                                        AND sig.condition_id = s.condition_id
                                        AND sig.bracket = s.bracket
        WHERE ca.canonical_config_id = ?
    """


def _canonical_id(db: sqlite3.Connection, config_id: str) -> str:
    """Resolve any (canonical or alias) config_id to its canonical form.
    Raises 404 if not found."""
    row = db.execute(
        "SELECT canonical_config_id FROM config_aliases WHERE alias_config_id = ?",
        (config_id,),
    ).fetchone()
    if row:
        return row["canonical_config_id"]
    # Fallback: legacy callers may pass a config_id that pre-dates the alias
    # table (e.g. a fresh DB before consolidate_configs ran). Accept it if it
    # still exists in strategy_config.
    sc = db.execute(
        "SELECT 1 FROM strategy_config WHERE config_id = ?", (config_id,)
    ).fetchone()
    if sc:
        return config_id
    raise HTTPException(status_code=404, detail=f"Strategy {config_id} not found")


def _float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _best_bid_ask_from_book(book) -> tuple[float, float]:
    bids = book.get("bids") if isinstance(book, dict) else getattr(book, "bids", None)
    asks = book.get("asks") if isinstance(book, dict) else getattr(book, "asks", None)
    bids = bids or []
    asks = asks or []

    def level_price(item) -> float:
        if isinstance(item, dict):
            return _float(item.get("price"), 0.0)
        return _float(getattr(item, "price", 0.0), 0.0)

    best_bid = max((level_price(item) for item in bids if level_price(item) > 0), default=0.0)
    best_ask = min((level_price(item) for item in asks if level_price(item) > 0), default=0.0)
    return best_bid, best_ask


def _clob_host() -> str:
    return (
        os.getenv("CLOB_BASE_URL", "").strip()
        or os.getenv("PM_API_BASE_URL", "").strip()
        or "https://clob.polymarket.com"
    ).rstrip("/")


def _fetch_order_book(token_id: str) -> dict:
    url = f"{_clob_host()}/book?{parse.urlencode({'token_id': token_id})}"
    req = request.Request(url, headers={"Accept": "application/json", "User-Agent": "pm-agent-weather-dashboard"})
    try:
        with request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"CLOB book HTTP {exc.code}: {body}") from exc
    except Exception as exc:
        raise RuntimeError(f"CLOB book unavailable: {type(exc).__name__}: {exc}") from exc


@router.get("/strategies/{config_id}")
def get_strategy(config_id: str, db: Db, state: str = Query("all")):
    """Single strategy detail with full aggregated stats.

    `config_id` accepts either a canonical id or any alias.
    `state` filters runs by state: all / live / paper / explore.
    """
    _validate_state(state)
    cid = _canonical_id(db, config_id)
    row = db.execute(
        "SELECT * FROM strategy_config WHERE config_id = ?", (cid,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Strategy {cid} not found")

    agg = db.execute(f"""
        SELECT
            COUNT(DISTINCT r.run_id)                                                AS num_runs,
            MAX(r.started_at_utc)                                                   AS latest_run_at,
            COUNT(DISTINCT CASE WHEN r.state='live'  THEN r.run_id END)            AS live_run_count,
            COUNT(DISTINCT CASE WHEN r.state='paper' THEN r.run_id END)            AS paper_run_count,
            COUNT(DISTINCT f.fill_id)                                               AS total_trades,
            COUNT(DISTINCT CASE WHEN s.final_price IS NOT NULL THEN f.fill_id END) AS settled_trades,
            SUM({_PNL_CASE})                                                        AS total_pnl_usd,
            SUM({_WIN_CASE})                                                        AS win_trades,
            SUM(CAST(f.filled_shares AS REAL) * CAST(f.filled_price AS REAL))      AS capital_deployed_usd
        {_base_joins(state)}
    """, (cid,)).fetchone()

    try:
        params = json.loads(row["params"]) if row["params"] else {}
    except Exception:
        params = {}

    cap = float(agg["capital_deployed_usd"] or 0)
    pnl = float(agg["total_pnl_usd"] or 0)
    settled = int(agg["settled_trades"] or 0)
    wins = int(agg["win_trades"] or 0)

    return {
        "config_id": row["config_id"],
        "name": row["name"],
        "params": params,
        "created_at_utc": row["created_at_utc"],
        "execution_policy": params.get("execution_policy"),
        "num_runs": int(agg["num_runs"] or 0),
        "latest_run_at": agg["latest_run_at"],
        "live_run_count": int(agg["live_run_count"] or 0),
        "paper_run_count": int(agg["paper_run_count"] or 0),
        "total_trades": int(agg["total_trades"] or 0),
        "settled_trades": settled,
        "total_pnl_usd": round(pnl, 4),
        "capital_deployed_usd": round(cap, 4),
        "roi": round(pnl / cap, 6) if cap > 0 else None,
        "win_rate": round(wins / settled, 4) if settled > 0 else None,
        "win_trades": wins,
    }


@router.get("/strategies/{config_id}/equity")
def get_strategy_equity(config_id: str, db: Db, state: str = Query("all")):
    """Daily cumulative PnL curve — one row per target_date with settled trades.

    `state` filters runs by state (default: all). Use `state=live` to see the
    live-only equity curve, or `state=paper` for the paper backtest curve.
    """
    _validate_state(state)
    cid = _canonical_id(db, config_id)
    rows = db.execute(f"""
        SELECT
            sig.target_date                                         AS date,
            COUNT(DISTINCT f.fill_id)                              AS trades,
            COUNT(DISTINCT CASE WHEN s.final_price IS NOT NULL THEN f.fill_id END) AS settled,
            SUM({_PNL_CASE})                                        AS pnl,
            SUM({_WIN_CASE})                                        AS wins,
            SUM(CAST(f.filled_shares AS REAL)*CAST(f.filled_price AS REAL)) AS capital
        {_base_joins(state)}
        AND sig.target_date IS NOT NULL
        GROUP BY sig.target_date
        ORDER BY sig.target_date
    """, (cid,)).fetchall()

    result = []
    cumulative = 0.0
    for r in rows:
        pnl = float(r["pnl"] or 0)
        cumulative += pnl
        result.append({
            "date": r["date"],
            "pnl": round(pnl, 4),
            "cumulative_pnl": round(cumulative, 4),
            "trades": int(r["trades"] or 0),
            "settled": int(r["settled"] or 0),
            "wins": int(r["wins"] or 0),
            "capital": round(float(r["capital"] or 0), 4),
        })
    return result


@router.get("/strategies/{config_id}/analytics")
def get_strategy_analytics(config_id: str, db: Db, state: str = Query("all")):
    """PnL breakdown by dimension: side, city, bracket, model, order_side."""
    _validate_state(state)
    cid = _canonical_id(db, config_id)

    def _breakdown(group_col: str, label: str):
        rows = db.execute(f"""
            SELECT
                {group_col}                                                         AS dimension,
                COUNT(DISTINCT f.fill_id)                                          AS trades,
                COUNT(DISTINCT CASE WHEN s.final_price IS NOT NULL THEN f.fill_id END) AS settled,
                ROUND(SUM({_PNL_CASE}), 4)                                         AS pnl,
                SUM({_WIN_CASE})                                                    AS wins,
                ROUND(SUM(CAST(f.filled_shares AS REAL)*CAST(f.filled_price AS REAL)), 4) AS capital
            {_base_joins(state)}
            AND {group_col} IS NOT NULL AND {group_col} != ''
            GROUP BY {group_col}
            ORDER BY pnl DESC
        """, (cid,)).fetchall()
        return [dict(r) for r in rows]

    return {
        "by_side": _breakdown("o.order_side", "side"),
        "by_city": _breakdown("sig.city", "city"),
        "by_bracket": _breakdown("sig.bracket", "bracket"),
        "by_model": _breakdown("sig.model_version", "model"),
        "by_forecast_source": _breakdown("sig.forecast_source", "forecast_source"),
    }



@router.get("/strategies/{config_id}/positions")
def get_strategy_positions(config_id: str, db: Db, state: str = Query("all")):
    """Open (unsettled) positions for a strategy — waiting for market resolution."""
    _validate_state(state)
    cid = _canonical_id(db, config_id)
    state_filter = "" if state == "all" else f" AND r.state = '{state}'"
    rows = db.execute(f"""
        SELECT
            f.fill_id,
            f.filled_shares,
            f.filled_price,
            f.filled_at_utc,
            f.status       AS fill_status,
            o.order_side,
            o.venue,
            o.order_id,
            sig.target_date,
            sig.city,
            sig.bracket,
            sig.signal_side,
            sig.model_version,
            sig.model_p_yes,
            sig.market_price AS signal_price,
            sig.condition_id,
            s.final_price,
            s.settlement_status,
            CASE
                WHEN s.final_price IS NULL THEN NULL
                WHEN o.order_side = 'BUY_YES'
                    THEN CAST(f.filled_shares AS REAL) * (CAST(s.final_price AS REAL) - CAST(f.filled_price AS REAL))
                WHEN o.order_side = 'BUY_NO'
                    THEN CAST(f.filled_shares AS REAL) * ((1 - CAST(s.final_price AS REAL)) - CAST(f.filled_price AS REAL))
                ELSE NULL
            END AS pnl_usd
        FROM config_aliases ca
        JOIN runs r ON r.config_id = ca.alias_config_id{state_filter}
        JOIN orders o ON o.run_id = r.run_id
        JOIN fills f ON f.execution_id = o.execution_id AND f.status = 'filled'
        JOIN plans p ON p.plan_id = o.plan_id
        JOIN signals sig ON sig.signal_id = p.signal_id
        LEFT JOIN {_SETTLEMENTS_DEDUP} s ON sig.target_date = s.target_date
                                        AND sig.condition_id = s.condition_id
                                        AND sig.bracket = s.bracket
        WHERE ca.canonical_config_id = ?
        ORDER BY sig.target_date DESC, f.filled_at_utc DESC
    """, (cid,)).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        for k in ("filled_shares", "filled_price", "model_p_yes", "signal_price", "final_price", "pnl_usd"):
            if d[k] is not None:
                d[k] = round(float(d[k]), 6)
        result.append(d)
    return result


@router.get("/strategies/{config_id}/mark-to-market")
def get_strategy_mark_to_market(config_id: str, db: Db, state: str = Query("live")):
    """Realtime mark-to-market for open CLOB fills.

    Uses the current CLOB orderbook for each filled token. PnL is marked to the
    best bid because that is the executable liquidation price for a long token.
    If the book is one-sided, the endpoint reports the row but does not invent a
    fallback mark price.
    """
    _validate_state(state)
    cid = _canonical_id(db, config_id)
    state_filter = "" if state == "all" else f" AND r.state = '{state}'"
    rows = db.execute(f"""
        SELECT
            f.fill_id,
            f.filled_shares,
            f.filled_price,
            f.filled_at_utc,
            o.order_id,
            o.order_side,
            o.venue,
            sig.target_date,
            sig.city,
            sig.bracket,
            sig.signal_side,
            sig.model_p_yes,
            sig.market_price AS signal_price,
            sig.market_id,
            sig.token_id,
            s.final_price,
            s.settlement_status
        FROM config_aliases ca
        JOIN runs r ON r.config_id = ca.alias_config_id{state_filter}
        JOIN orders o ON o.run_id = r.run_id
        JOIN fills f ON f.execution_id = o.execution_id AND f.status = 'filled'
        JOIN plans p ON p.plan_id = o.plan_id
        JOIN signals sig ON sig.signal_id = p.signal_id
        LEFT JOIN {_SETTLEMENTS_DEDUP} s ON sig.target_date = s.target_date
                                        AND sig.condition_id = s.condition_id
                                        AND sig.bracket = s.bracket
        WHERE ca.canonical_config_id = ?
          AND o.venue = 'polymarket_clob'
          AND s.final_price IS NULL
        ORDER BY sig.target_date DESC, f.filled_at_utc DESC
    """, (cid,)).fetchall()

    today_utc = datetime.now(timezone.utc).date().isoformat()
    tokens_to_fetch = sorted({
        str(row["token_id"] or "").strip()
        for row in rows
        if str(row["token_id"] or "").strip()
        and str(row["target_date"] or "") >= today_utc
    })

    def fetch_token_book(token_id: str) -> tuple[str, dict]:
        try:
            book = _fetch_order_book(token_id)
            bid, ask = _best_bid_ask_from_book(book)
            return token_id, {"best_bid": bid, "best_ask": ask, "error": ""}
        except Exception as exc:
            return token_id, {
                "best_bid": 0.0,
                "best_ask": 0.0,
                "error": f"{type(exc).__name__}: {exc}",
            }

    book_cache: dict[str, dict] = {}
    if tokens_to_fetch:
        with ThreadPoolExecutor(max_workers=min(8, len(tokens_to_fetch))) as executor:
            futures = [executor.submit(fetch_token_book, token_id) for token_id in tokens_to_fetch]
            for future in as_completed(futures):
                token_id, book_result = future.result()
                book_cache[token_id] = book_result

    positions = []
    for row in rows:
        d = dict(row)
        token_id = str(d.get("token_id") or "").strip()
        target_date = str(d.get("target_date") or "")
        best_bid = 0.0
        best_ask = 0.0
        mark_error = ""
        if not target_date:
            mark_error = "missing target_date"
        elif target_date < today_utc:
            mark_error = "target date passed; use settlement pnl"
        elif token_id:
            cached = book_cache[token_id]
            best_bid = float(cached["best_bid"])
            best_ask = float(cached["best_ask"])
            mark_error = str(cached["error"])
        else:
            mark_error = "missing token_id"

        mark_price = best_bid if best_bid > 0 else None
        mid_price = (best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask > 0 else None
        shares = float(d["filled_shares"] or 0)
        filled_price = float(d["filled_price"] or 0)
        mark_value = shares * mark_price if mark_price is not None else None
        cost = shares * filled_price
        unrealized_pnl = mark_value - cost if mark_value is not None else None
        d.update(
            {
                "best_bid": round(best_bid, 6) if best_bid > 0 else None,
                "best_ask": round(best_ask, 6) if best_ask > 0 else None,
                "mid_price": round(mid_price, 6) if mid_price is not None else None,
                "mark_price": round(mark_price, 6) if mark_price is not None else None,
                "mark_price_source": "best_bid" if mark_price is not None else None,
                "mark_value_usd": round(mark_value, 6) if mark_value is not None else None,
                "cost_usd": round(cost, 6),
                "unrealized_pnl_usd": round(unrealized_pnl, 6) if unrealized_pnl is not None else None,
                "mark_error": mark_error or None,
            }
        )
        for k in ("filled_shares", "filled_price", "model_p_yes", "signal_price"):
            if d[k] is not None:
                d[k] = round(float(d[k]), 6)
        positions.append(d)

    markable = [p for p in positions if p["unrealized_pnl_usd"] is not None]
    total_cost = sum(float(p["cost_usd"] or 0) for p in positions)
    total_value = sum(float(p["mark_value_usd"] or 0) for p in markable)
    total_pnl = sum(float(p["unrealized_pnl_usd"] or 0) for p in markable)
    return {
        "config_id": cid,
        "state": state,
        "as_of_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "open_positions": len(positions),
        "marked_positions": len(markable),
        "unmarked_positions": len(positions) - len(markable),
        "total_cost_usd": round(total_cost, 6),
        "mark_value_usd": round(total_value, 6),
        "unrealized_pnl_usd": round(total_pnl, 6),
        "positions": positions,
    }


@router.get("/strategies/{config_id}/orders")
def get_strategy_orders(
    config_id: str,
    db: Db,
    state: str = Query("live"),
    venue: Optional[str] = Query(None, description="paper | polymarket_clob | snapshot_replay"),
    target_date: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    """Order-level lineage for a strategy.

    One row per order, including the signal, plan, fill (if any), settlement
    (if any), and realized PnL when both fill and settlement exist.
    """
    _validate_state(state)
    cid = _canonical_id(db, config_id)
    state_filter = "" if state == "all" else f" AND r.state = '{state}'"
    where = ["ca.canonical_config_id = ?"]
    params: list = [cid]
    if venue:
        where.append("o.venue = ?")
        params.append(venue)
    if target_date:
        where.append("sig.target_date = ?")
        params.append(target_date)
    params.extend([limit, offset])

    rows = db.execute(f"""
        SELECT
            r.run_id,
            r.started_at_utc,
            r.producer_system,
            r.config_id AS source_config_id,
            sig.signal_id,
            sig.target_date,
            sig.city,
            sig.bracket,
            sig.city_pool,
            sig.forecast_source,
            sig.model_version,
            sig.model_p_yes,
            sig.market_price AS signal_market_price,
            sig.edge AS signal_edge,
            sig.condition_id,
            p.plan_id,
            p.execution_policy,
            p.skip_reason,
            o.execution_id,
            o.order_id,
            o.venue,
            o.order_side,
            o.status AS order_status,
            o.limit_price,
            o.entry_price,
            o.shares AS order_shares,
            o.cost_usd AS order_cost_usd,
            o.placed_at_utc,
            f.fill_id,
            f.status AS fill_status,
            f.filled_shares,
            f.filled_price,
            f.fees_usd,
            f.filled_at_utc,
            s.final_price,
            s.settlement_status,
            CASE
                WHEN s.final_price IS NULL OR f.fill_id IS NULL THEN NULL
                WHEN o.order_side = 'BUY_YES'
                    THEN CAST(f.filled_shares AS REAL)
                       * (CAST(s.final_price AS REAL) - CAST(f.filled_price AS REAL))
                WHEN o.order_side = 'BUY_NO'
                    THEN CAST(f.filled_shares AS REAL)
                       * ((1 - CAST(s.final_price AS REAL)) - CAST(f.filled_price AS REAL))
                ELSE NULL
            END AS pnl_usd
        FROM config_aliases ca
        JOIN runs r ON r.config_id = ca.alias_config_id{state_filter}
        JOIN plans p ON p.run_id = r.run_id
        JOIN signals sig ON sig.signal_id = p.signal_id
        JOIN orders o ON o.plan_id = p.plan_id
        LEFT JOIN fills f ON f.execution_id = o.execution_id
        LEFT JOIN {_SETTLEMENTS_DEDUP} s ON sig.target_date = s.target_date
                                        AND sig.condition_id = s.condition_id
                                        AND sig.bracket = s.bracket
        WHERE {' AND '.join(where)}
        ORDER BY COALESCE(o.placed_at_utc, r.started_at_utc, r.created_at_utc) DESC,
                 o.venue,
                 o.execution_id
        LIMIT ? OFFSET ?
    """, params).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        for k in (
            "model_p_yes", "signal_market_price", "signal_edge",
            "limit_price", "entry_price", "order_shares", "order_cost_usd",
            "filled_shares", "filled_price", "fees_usd", "final_price", "pnl_usd",
        ):
            if d.get(k) is not None:
                d[k] = round(float(d[k]), 6)
        result.append(d)
    return result


@router.get("/strategies/{config_id}/daily-execution")
def get_strategy_daily_execution(
    config_id: str,
    db: Db,
    state: str = Query("live"),
):
    """Daily paper-vs-CLOB execution summary.

    Paper rows are the simulated benchmark orders attached to the same plans.
    CLOB rows are real Polymarket fills. The gap decomposes as:
      clob_pnl - paper_pnl
        = missed_or_error_paper_gap + execution_diff_after_fill
    where missed_or_error_paper_gap is the signed gap contribution from plans
    that had no CLOB fill. Missing a profitable paper trade is negative;
    avoiding a losing paper trade is positive.
    """
    _validate_state(state)
    cid = _canonical_id(db, config_id)
    state_filter = "" if state == "all" else f" AND r.state = '{state}'"

    rows = db.execute(f"""
        WITH plan_rows AS (
            SELECT
                sig.target_date,
                p.plan_id,
                p.skip_reason,
                MAX(CASE WHEN po.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS has_paper_order,
                MAX(CASE WHEN pf.fill_id IS NOT NULL THEN 1 ELSE 0 END) AS has_paper_fill,
                MAX(CASE WHEN co.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS has_clob_order,
                MAX(CASE
                    WHEN co.execution_id IS NOT NULL
                     AND cf.fill_id IS NULL
                     AND LOWER(COALESCE(co.status, '')) LIKE '%error%'
                    THEN 1 ELSE 0
                END) AS clob_error_no_fill,
                MAX(CASE
                    WHEN co.execution_id IS NOT NULL
                     AND cf.fill_id IS NULL
                     AND LOWER(COALESCE(co.status, '')) NOT LIKE '%error%'
                    THEN 1 ELSE 0
                END) AS clob_submitted_no_fill,
                COUNT(DISTINCT cf.fill_id) AS clob_fills,
                COUNT(DISTINCT CASE WHEN s.final_price IS NOT NULL THEN cf.fill_id END) AS settled_clob_fills,
                CASE
                    WHEN s.final_price IS NULL THEN NULL
                    WHEN po.order_side = 'BUY_YES'
                        THEN SUM(CAST(pf.filled_shares AS REAL)
                               * (CAST(s.final_price AS REAL) - CAST(pf.filled_price AS REAL)))
                    WHEN po.order_side = 'BUY_NO'
                        THEN SUM(CAST(pf.filled_shares AS REAL)
                               * ((1 - CAST(s.final_price AS REAL)) - CAST(pf.filled_price AS REAL)))
                    ELSE NULL
                END AS paper_pnl,
                CASE
                    WHEN s.final_price IS NULL THEN NULL
                    WHEN co.order_side = 'BUY_YES'
                        THEN SUM(CAST(cf.filled_shares AS REAL)
                               * (CAST(s.final_price AS REAL) - CAST(cf.filled_price AS REAL)))
                    WHEN co.order_side = 'BUY_NO'
                        THEN SUM(CAST(cf.filled_shares AS REAL)
                               * ((1 - CAST(s.final_price AS REAL)) - CAST(cf.filled_price AS REAL)))
                    ELSE NULL
                END AS clob_pnl
            FROM config_aliases ca
            JOIN runs r ON r.config_id = ca.alias_config_id{state_filter}
            JOIN plans p ON p.run_id = r.run_id
            JOIN signals sig ON sig.signal_id = p.signal_id
            LEFT JOIN orders po ON po.plan_id = p.plan_id
                               AND po.venue = 'paper'
            LEFT JOIN fills pf ON pf.execution_id = po.execution_id
                              AND pf.status IN ('filled', 'simulated')
            LEFT JOIN orders co ON co.plan_id = p.plan_id
                               AND co.venue = 'polymarket_clob'
            LEFT JOIN fills cf ON cf.execution_id = co.execution_id
                              AND cf.status = 'filled'
            LEFT JOIN {_SETTLEMENTS_DEDUP} s ON sig.target_date = s.target_date
                                            AND sig.condition_id = s.condition_id
                                            AND sig.bracket = s.bracket
            WHERE ca.canonical_config_id = ?
              AND sig.target_date IS NOT NULL
            GROUP BY sig.target_date, p.plan_id, p.skip_reason,
                     s.final_price, po.order_side, co.order_side
        )
        SELECT
            target_date,
            COUNT(DISTINCT plan_id) AS plans,
            SUM(has_paper_order) AS paper_orders,
            SUM(has_paper_fill) AS paper_fills,
            SUM(has_clob_order) AS clob_orders,
            SUM(clob_fills) AS clob_fills,
            SUM(settled_clob_fills) AS settled_clob_fills,
            SUM(clob_error_no_fill) AS clob_error_no_fill,
            SUM(clob_submitted_no_fill) AS clob_submitted_no_fill,
            SUM(CASE WHEN has_clob_order = 0 THEN 1 ELSE 0 END) AS clob_no_order,
            SUM(CASE WHEN paper_pnl IS NULL AND has_paper_fill = 1 THEN 1 ELSE 0 END) AS unsettled_paper_plans,
            SUM(CASE WHEN clob_pnl IS NULL AND clob_fills > 0 THEN clob_fills ELSE 0 END) AS unsettled_clob_fills,
            SUM(COALESCE(paper_pnl, 0)) AS paper_pnl,
            SUM(COALESCE(clob_pnl, 0)) AS clob_pnl,
            SUM(COALESCE(clob_pnl, 0)) - SUM(COALESCE(paper_pnl, 0)) AS gap_clob_minus_paper,
            -SUM(CASE
                WHEN has_paper_fill = 1 AND clob_fills = 0
                THEN COALESCE(paper_pnl, 0)
                ELSE 0
            END) AS missed_or_error_paper_gap,
            SUM(CASE
                WHEN has_paper_fill = 1 AND clob_fills = 0
                THEN COALESCE(paper_pnl, 0)
                ELSE 0
            END) AS missed_or_error_paper_pnl,
            SUM(CASE
                WHEN has_paper_fill = 1 AND clob_fills > 0
                THEN COALESCE(clob_pnl, 0) - COALESCE(paper_pnl, 0)
                ELSE 0
            END) AS execution_diff_after_fill
        FROM plan_rows
        GROUP BY target_date
        ORDER BY target_date
    """, (cid,)).fetchall()

    result = []
    money_fields = (
        "paper_pnl",
        "clob_pnl",
        "gap_clob_minus_paper",
        "missed_or_error_paper_gap",
        "missed_or_error_paper_pnl",
        "execution_diff_after_fill",
    )
    count_fields = (
        "plans",
        "paper_orders",
        "paper_fills",
        "clob_orders",
        "clob_fills",
        "settled_clob_fills",
        "clob_error_no_fill",
        "clob_submitted_no_fill",
        "clob_no_order",
        "unsettled_paper_plans",
        "unsettled_clob_fills",
    )
    for row in rows:
        d = dict(row)
        for field in count_fields:
            d[field] = int(d[field] or 0)
        for field in money_fields:
            d[field] = round(float(d[field] or 0), 4)
        result.append(d)
    return result


# ── Execution Funnel ──────────────────────────────────────────────────────────

@router.get("/strategies/{config_id}/funnel")
def get_strategy_funnel(config_id: str, db: Db):
    """
    Per-day execution funnel: signals evaluated → plans → orders placed → fills.

    Groups by the run's start date so each row represents one day's cycle.
    CLOB orders only (venue = 'polymarket_clob').
    """
    rows = db.execute("""
        SELECT
            date(r.started_at_utc)  AS day,
            COUNT(DISTINCT p.signal_id)                                            AS signals_evaluated,
            COUNT(DISTINCT CASE WHEN p.skip_reason IS NULL     THEN p.plan_id END) AS plans_executed,
            COUNT(DISTINCT CASE WHEN p.skip_reason IS NOT NULL THEN p.plan_id END) AS plans_skipped,
            COUNT(DISTINCT o.execution_id)                                         AS orders_placed,
            COUNT(DISTINCT CASE WHEN f.status = 'filled' THEN f.fill_id END)      AS orders_filled,
            COUNT(DISTINCT CASE
                WHEN o.status = 'submitted' AND f.fill_id IS NULL
                 AND sig.target_date >= date('now', '-1 day')
                THEN o.execution_id END)                                           AS orders_pending,
            ROUND(AVG(o.limit_price), 4)                                           AS avg_limit_price,
            ROUND(AVG(CASE WHEN f.status = 'filled' THEN f.filled_price END), 4)  AS avg_fill_price,
            ROUND(AVG(sig.market_price), 4)                                        AS avg_market_price,
            ROUND(SUM(CASE WHEN f.status = 'filled'
                      THEN CAST(f.filled_shares AS REAL) * CAST(f.filled_price AS REAL)
                      END), 2)                                                     AS filled_capital_usd,
            ROUND(SUM(CASE
                WHEN o.status = 'submitted' AND f.fill_id IS NULL
                 AND sig.target_date >= date('now', '-1 day')
                THEN o.cost_usd END), 2)                                           AS pending_capital_usd
        FROM plans p
        JOIN signals sig ON sig.signal_id = p.signal_id
        JOIN runs r      ON r.run_id       = p.run_id
        LEFT JOIN orders o ON o.plan_id = p.plan_id AND o.venue = 'polymarket_clob'
        LEFT JOIN fills  f ON f.execution_id = o.execution_id
        WHERE r.config_id = ?
          AND r.started_at_utc IS NOT NULL
        GROUP BY day
        ORDER BY day DESC
    """, (config_id,)).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        placed = d["orders_placed"] or 0
        filled = d["orders_filled"] or 0
        d["fill_rate"] = round(filled / placed, 4) if placed > 0 else None
        # limit_discount: how many cents below market the avg limit price is
        if d["avg_market_price"] is not None and d["avg_limit_price"] is not None:
            d["limit_discount"] = round(d["avg_market_price"] - d["avg_limit_price"], 4)
        else:
            d["limit_discount"] = None
        result.append(d)
    return result


@router.get("/strategies/{config_id}/pending-orders")
def get_strategy_pending_orders(config_id: str, db: Db):
    """
    CLOB orders that are submitted but have no fill yet — currently in the market.
    Shows limit price vs signal market price so you can see how aggressive the bid is.
    """
    rows = db.execute("""
        SELECT
            o.execution_id,
            o.order_id,
            sig.city,
            sig.target_date,
            sig.bracket,
            sig.city_pool,
            o.order_side,
            o.limit_price,
            sig.market_price    AS signal_market_price,
            o.shares,
            o.cost_usd,
            o.placed_at_utc
        FROM orders o
        JOIN plans   p   ON p.plan_id   = o.plan_id
        JOIN signals sig ON sig.signal_id = p.signal_id
        JOIN runs    r   ON r.run_id    = o.run_id
        LEFT JOIN fills f ON f.execution_id = o.execution_id
        WHERE r.config_id = ?
          AND o.venue     = 'polymarket_clob'
          AND o.status    = 'submitted'
          AND f.fill_id  IS NULL
          AND sig.target_date >= date('now', '-1 day')
        ORDER BY o.placed_at_utc DESC
    """, (config_id,)).fetchall()

    import datetime as _dt
    now_utc = _dt.datetime.now(_dt.timezone.utc)
    result = []
    for row in rows:
        d = dict(row)
        # limit discount: positive means limit is below market (we're bidding at a discount)
        if d["limit_price"] is not None and d["signal_market_price"] is not None:
            d["limit_discount"] = round(
                float(d["signal_market_price"]) - float(d["limit_price"]), 4
            )
        else:
            d["limit_discount"] = None
        # hours since placed
        if d["placed_at_utc"]:
            try:
                placed = _dt.datetime.fromisoformat(
                    d["placed_at_utc"].replace("Z", "+00:00")
                )
                d["hours_pending"] = round((now_utc - placed).total_seconds() / 3600, 1)
            except Exception:
                d["hours_pending"] = None
        else:
            d["hours_pending"] = None
        for k in ("limit_price", "signal_market_price", "shares", "cost_usd"):
            if d.get(k) is not None:
                d[k] = round(float(d[k]), 4)
        result.append(d)
    return result


# ── Configs ───────────────────────────────────────────────────────────────────

@router.get("/configs", response_model=list[ConfigRow])
def list_configs(db: Db):
    rows = db.execute(
        "SELECT * FROM strategy_config ORDER BY created_at_utc DESC"
    ).fetchall()
    result = []
    for r in rows:
        try:
            params = json.loads(r["params"])
        except Exception:
            params = r["params"]
        result.append({
            "config_id": r["config_id"],
            "name": r["name"],
            "params": params,
            "created_at_utc": r["created_at_utc"],
        })
    return result


@router.get("/configs/{config_id}", response_model=ConfigRow)
def get_config(config_id: str, db: Db):
    row = db.execute(
        "SELECT * FROM strategy_config WHERE config_id = ?", (config_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Config {config_id} not found")
    try:
        params = json.loads(row["params"])
    except Exception:
        params = row["params"]
    return {"config_id": row["config_id"], "name": row["name"],
            "params": params, "created_at_utc": row["created_at_utc"]}


# ── Universes ─────────────────────────────────────────────────────────────────

@router.get("/universes", response_model=list[UniverseRow])
def list_universes(db: Db):
    rows = db.execute(
        "SELECT * FROM universes ORDER BY created_at_utc DESC"
    ).fetchall()
    result = []
    for r in rows:
        result.append({
            "universe_id": r["universe_id"],
            "name": r["name"],
            "description": r["description"],
            "cities": json.loads(r["cities"]) if r["cities"] else [],
            "models": json.loads(r["models"]) if r["models"] else [],
            "created_at_utc": r["created_at_utc"],
            "frozen_at_utc": r["frozen_at_utc"],
            "deprecated_at_utc": r["deprecated_at_utc"],
        })
    return result


@router.get("/universes/{universe_id}", response_model=UniverseRow)
def get_universe(universe_id: str, db: Db):
    row = db.execute(
        "SELECT * FROM universes WHERE universe_id = ?", (universe_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Universe {universe_id} not found")
    return {
        "universe_id": row["universe_id"],
        "name": row["name"],
        "description": row["description"],
        "cities": json.loads(row["cities"]) if row["cities"] else [],
        "models": json.loads(row["models"]) if row["models"] else [],
        "created_at_utc": row["created_at_utc"],
        "frozen_at_utc": row["frozen_at_utc"],
        "deprecated_at_utc": row["deprecated_at_utc"],
    }


# ── Settlements ───────────────────────────────────────────────────────────────

@router.get("/settlements", response_model=list[SettlementRow])
def list_settlements(
    db: Db,
    target_date: str = None,
    bracket: str = None,
):
    where = ["1=1"]
    params = []
    if target_date:
        where.append("target_date = ?")
        params.append(target_date)
    if bracket:
        where.append("bracket = ?")
        params.append(bracket)

    rows = db.execute(
        f"SELECT * FROM settlements WHERE {' AND '.join(where)} "
        f"ORDER BY target_date DESC, bracket",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


# NOTE: /live/* endpoints are now in weather_dashboard.api.routers.live
