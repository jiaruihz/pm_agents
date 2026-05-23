"""Strategy configs and universes endpoints."""

import json
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
import sqlite3

from weather_dashboard.api.deps import get_db
from weather_dashboard.api.schemas import ConfigRow, UniverseRow, SettlementRow

router = APIRouter(tags=["registry"])

Db = Annotated[sqlite3.Connection, Depends(get_db)]


# ── Strategies (per-config aggregated stats) ──────────────────────────────────

@router.get("/strategies")
def list_strategies(db: Db):
    """
    Per-strategy aggregated performance stats.

    Strategy = config_id in strategy_config.
    All strategy dimensions (execution_policy, kelly_fraction, etc.) live in
    strategy_config.params JSON — queried via SQLite JSON1.
    """
    rows = db.execute(
        """
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
            SUM(CASE WHEN s.final_price IS NOT NULL THEN 1 ELSE 0 END)  AS settled_trades,

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
        LEFT JOIN runs r      ON r.config_id    = c.config_id
        LEFT JOIN orders o    ON o.run_id        = r.run_id
        LEFT JOIN fills f     ON f.execution_id  = o.execution_id
                             AND f.status        = 'filled'
        LEFT JOIN plans p     ON p.plan_id       = o.plan_id
        LEFT JOIN signals sig ON sig.signal_id   = p.signal_id
        LEFT JOIN settlements s
               ON sig.target_date = s.target_date
              AND sig.condition_id = s.condition_id
              AND sig.bracket      = s.bracket
        GROUP BY c.config_id
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


def _base_joins() -> str:
    # status='filled' keeps real CLOB fills + historical paper fills,
    # excludes status='simulated' (parallel paper simulation alongside live trades).
    return """
        FROM runs r
        LEFT JOIN orders o ON o.run_id = r.run_id
        LEFT JOIN fills f  ON f.execution_id = o.execution_id AND f.status = 'filled'
        LEFT JOIN plans p  ON p.plan_id = o.plan_id
        LEFT JOIN signals sig ON sig.signal_id = p.signal_id
        LEFT JOIN settlements s ON sig.target_date = s.target_date
                               AND sig.condition_id = s.condition_id
                               AND sig.bracket = s.bracket
        WHERE r.config_id = ?
    """


@router.get("/strategies/{config_id}")
def get_strategy(config_id: str, db: Db):
    """Single strategy detail with full aggregated stats."""
    row = db.execute("SELECT * FROM strategy_config WHERE config_id = ?", (config_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Strategy {config_id} not found")

    agg = db.execute(f"""
        SELECT
            COUNT(DISTINCT r.run_id)                                                AS num_runs,
            MAX(r.started_at_utc)                                                   AS latest_run_at,
            COUNT(DISTINCT CASE WHEN r.state='live'  THEN r.run_id END)            AS live_run_count,
            COUNT(DISTINCT CASE WHEN r.state='paper' THEN r.run_id END)            AS paper_run_count,
            COUNT(DISTINCT f.fill_id)                                               AS total_trades,
            SUM(CASE WHEN s.final_price IS NOT NULL THEN 1 ELSE 0 END)             AS settled_trades,
            SUM({_PNL_CASE})                                                        AS total_pnl_usd,
            SUM({_WIN_CASE})                                                        AS win_trades,
            SUM(CAST(f.filled_shares AS REAL) * CAST(f.filled_price AS REAL))      AS capital_deployed_usd
        {_base_joins()}
    """, (config_id,)).fetchone()

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
def get_strategy_equity(config_id: str, db: Db):
    """Daily cumulative PnL curve — one row per target_date with settled trades."""
    rows = db.execute(f"""
        SELECT
            sig.target_date                                         AS date,
            COUNT(DISTINCT f.fill_id)                              AS trades,
            SUM(CASE WHEN s.final_price IS NOT NULL THEN 1 ELSE 0 END) AS settled,
            SUM({_PNL_CASE})                                        AS pnl,
            SUM({_WIN_CASE})                                        AS wins,
            SUM(CAST(f.filled_shares AS REAL)*CAST(f.filled_price AS REAL)) AS capital
        {_base_joins()}
        AND sig.target_date IS NOT NULL
        GROUP BY sig.target_date
        ORDER BY sig.target_date
    """, (config_id,)).fetchall()

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
def get_strategy_analytics(config_id: str, db: Db):
    """PnL breakdown by dimension: side, city, bracket, model, order_side."""

    def _breakdown(group_col: str, label: str):
        rows = db.execute(f"""
            SELECT
                {group_col}                                                         AS dimension,
                COUNT(DISTINCT f.fill_id)                                          AS trades,
                SUM(CASE WHEN s.final_price IS NOT NULL THEN 1 ELSE 0 END)        AS settled,
                ROUND(SUM({_PNL_CASE}), 4)                                         AS pnl,
                SUM({_WIN_CASE})                                                    AS wins,
                ROUND(SUM(CAST(f.filled_shares AS REAL)*CAST(f.filled_price AS REAL)), 4) AS capital
            {_base_joins()}
            AND {group_col} IS NOT NULL AND {group_col} != ''
            GROUP BY {group_col}
            ORDER BY pnl DESC
        """, (config_id,)).fetchall()
        return [dict(r) for r in rows]

    return {
        "by_side": _breakdown("o.order_side", "side"),
        "by_city": _breakdown("sig.city", "city"),
        "by_bracket": _breakdown("sig.bracket", "bracket"),
        "by_model": _breakdown("sig.model_version", "model"),
        "by_forecast_source": _breakdown("sig.forecast_source", "forecast_source"),
    }



@router.get("/strategies/{config_id}/positions")
def get_strategy_positions(config_id: str, db: Db):
    """Open (unsettled) positions for a strategy — waiting for market resolution."""
    rows = db.execute("""
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
        FROM runs r
        JOIN orders o ON o.run_id = r.run_id
        JOIN fills f ON f.execution_id = o.execution_id AND f.status = 'filled'
        JOIN plans p ON p.plan_id = o.plan_id
        JOIN signals sig ON sig.signal_id = p.signal_id
        LEFT JOIN settlements s ON sig.target_date = s.target_date
                               AND sig.condition_id = s.condition_id
                               AND sig.bracket = s.bracket
        WHERE r.config_id = ?
        ORDER BY sig.target_date DESC, f.filled_at_utc DESC
    """, (config_id,)).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        for k in ("filled_shares", "filled_price", "model_p_yes", "signal_price", "final_price", "pnl_usd"):
            if d[k] is not None:
                d[k] = round(float(d[k]), 6)
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
                THEN o.execution_id END)                                           AS orders_pending,
            ROUND(AVG(o.limit_price), 4)                                           AS avg_limit_price,
            ROUND(AVG(CASE WHEN f.status = 'filled' THEN f.filled_price END), 4)  AS avg_fill_price,
            ROUND(AVG(sig.market_price), 4)                                        AS avg_market_price,
            ROUND(SUM(CASE WHEN f.status = 'filled'
                      THEN CAST(f.filled_shares AS REAL) * CAST(f.filled_price AS REAL)
                      END), 2)                                                     AS filled_capital_usd,
            ROUND(SUM(CASE
                WHEN o.status = 'submitted' AND f.fill_id IS NULL
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
