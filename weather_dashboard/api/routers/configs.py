"""Strategy configs and universes endpoints."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
import sqlite3

from weather_dashboard.api.deps import get_db
from weather_dashboard.api.schemas import ConfigRow, UniverseRow, SettlementRow

router = APIRouter(tags=["registry"])

Db = Annotated[sqlite3.Connection, Depends(get_db)]


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


@router.get("/live/summary")
def get_live_summary(db: Db):
    by_target_date = db.execute(
        """
        SELECT
            sig.target_date,
            COUNT(*) AS orders,
            COUNT(DISTINCT sig.city) AS cities,
            SUM(CASE WHEN o.venue = 'polymarket_clob' THEN 1 ELSE 0 END) AS clob_orders,
            SUM(CASE WHEN o.venue = 'paper' THEN 1 ELSE 0 END) AS paper_orders,
            SUM(CASE WHEN o.status = 'submitted' THEN 1 ELSE 0 END) AS submitted_orders,
            SUM(o.cost_usd) AS notional_usd,
            MIN(COALESCE(o.placed_at_utc, o.created_at_utc)) AS first_order_at_utc,
            MAX(COALESCE(o.placed_at_utc, o.created_at_utc)) AS last_order_at_utc
        FROM orders o
        JOIN plans p ON p.plan_id = o.plan_id
        JOIN signals sig ON sig.signal_id = p.signal_id
        JOIN runs r ON r.run_id = o.run_id
        WHERE r.execution_mode = 'live'
        GROUP BY sig.target_date
        ORDER BY sig.target_date DESC
        """
    ).fetchall()

    strategy_versions = db.execute(
        """
        SELECT
            c.config_id,
            c.name,
            c.params,
            COUNT(DISTINCT r.run_id) AS runs,
            COUNT(o.execution_id) AS orders,
            SUM(o.cost_usd) AS notional_usd
        FROM strategy_config c
        JOIN runs r ON r.config_id = c.config_id
        LEFT JOIN orders o ON o.run_id = r.run_id
        WHERE r.execution_mode = 'live'
        GROUP BY c.config_id, c.name, c.params
        ORDER BY orders DESC, c.name
        """
    ).fetchall()

    today_account = db.execute(
        """
        SELECT
            date(COALESCE(o.placed_at_utc, o.created_at_utc)) AS order_date_utc,
            COUNT(*) AS orders,
            SUM(CASE WHEN o.venue = 'polymarket_clob' THEN 1 ELSE 0 END) AS clob_orders,
            SUM(CASE WHEN o.venue = 'paper' THEN 1 ELSE 0 END) AS paper_orders,
            SUM(CASE WHEN o.status = 'submitted' THEN 1 ELSE 0 END) AS submitted_orders,
            SUM(o.cost_usd) AS notional_usd,
            COUNT(DISTINCT sig.city) AS cities,
            COUNT(DISTINCT sig.target_date) AS target_dates
        FROM orders o
        JOIN plans p ON p.plan_id = o.plan_id
        JOIN signals sig ON sig.signal_id = p.signal_id
        JOIN runs r ON r.run_id = o.run_id
        WHERE r.execution_mode = 'live'
          AND date(COALESCE(o.placed_at_utc, o.created_at_utc)) = (
              SELECT MAX(date(COALESCE(o2.placed_at_utc, o2.created_at_utc)))
              FROM orders o2
              JOIN runs r2 ON r2.run_id = o2.run_id
              WHERE r2.execution_mode = 'live'
          )
        GROUP BY order_date_utc
        """
    ).fetchone()

    def parsed_config(row):
        payload = dict(row)
        try:
            payload["params"] = json.loads(payload["params"])
        except Exception:
            pass
        return payload

    return {
        "by_target_date": [dict(row) for row in by_target_date],
        "strategy_versions": [parsed_config(row) for row in strategy_versions],
        "today_account": dict(today_account) if today_account else None,
    }
