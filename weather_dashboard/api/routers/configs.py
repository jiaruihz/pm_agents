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
