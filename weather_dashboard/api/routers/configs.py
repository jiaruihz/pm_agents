"""Strategy configs and universes endpoints."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
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
            MAX(r.created_at_utc)                                             AS latest_run_at,
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
                             AND o.venue         = 'polymarket_clob'
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
