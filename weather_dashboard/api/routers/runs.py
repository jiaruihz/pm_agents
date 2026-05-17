"""Runs endpoints."""

import json
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
import sqlite3

from weather_dashboard.api.deps import get_db
from weather_dashboard.api.schemas import RunDetail, RunSummary, TradeRow
from weather_dashboard.metrics.calc import compute_metrics

router = APIRouter(prefix="/runs", tags=["runs"])

Db = Annotated[sqlite3.Connection, Depends(get_db)]


def _parse_tags(raw) -> list[str]:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def _row_to_run_summary(row) -> dict:
    return {
        "run_id": row["run_id"],
        "config_id": row["config_id"],
        "universe_id": row["universe_id"],
        "code_version": row["code_version"],
        "execution_mode": row["execution_mode"],
        "date_range_start": row["date_range_start"],
        "date_range_end": row["date_range_end"],
        "state": row["state"],
        "repro_key": row["repro_key"],
        "tags": _parse_tags(row["tags"]),
        "notes": row["notes"],
        "created_at_utc": row["created_at_utc"],
        "started_at_utc": row["started_at_utc"],
    }


@router.get("", response_model=list[RunSummary])
def list_runs(
    db: Db,
    state: Optional[str] = Query(None, description="Filter by state: explore/paper/live/retired"),
    execution_mode: Optional[str] = Query(None),
    config_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    where = ["1=1"]
    params: list = []

    if state:
        where.append("state = ?")
        params.append(state)
    if execution_mode:
        where.append("execution_mode = ?")
        params.append(execution_mode)
    if config_id:
        where.append("config_id = ?")
        params.append(config_id)

    params += [limit, offset]
    rows = db.execute(
        f"SELECT * FROM runs WHERE {' AND '.join(where)} "
        f"ORDER BY created_at_utc DESC LIMIT ? OFFSET ?",
        params,
    ).fetchall()

    return [_row_to_run_summary(r) for r in rows]


@router.get("/{run_id}", response_model=RunDetail)
def get_run(run_id: str, db: Db, include_metrics: bool = Query(True)):
    row = db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    result = _row_to_run_summary(row)
    if include_metrics:
        cached = row["metrics"]
        result["metrics"] = json.loads(cached) if cached else compute_metrics(db, run_id)
    else:
        result["metrics"] = None
    return result


@router.get("/{run_id}/equity")
def get_run_equity(run_id: str, db: Db):
    """Daily cumulative PnL for the equity curve chart."""
    row = db.execute("SELECT run_id FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    rows = db.execute(
        """
        SELECT
            sig.target_date AS date,
            SUM(CASE
                WHEN s.final_yes IS NULL THEN 0
                WHEN o.side = 'BUY_YES' AND s.final_yes = 1
                    THEN CAST(f.filled_shares AS REAL) * (1 - CAST(f.filled_price AS REAL))
                WHEN o.side = 'BUY_YES' AND s.final_yes = 0
                    THEN -CAST(f.filled_shares AS REAL) * CAST(f.filled_price AS REAL)
                WHEN o.side = 'BUY_NO' AND s.final_yes = 0
                    THEN CAST(f.filled_shares AS REAL) * (1 - CAST(f.filled_price AS REAL))
                WHEN o.side = 'BUY_NO' AND s.final_yes = 1
                    THEN -CAST(f.filled_shares AS REAL) * CAST(f.filled_price AS REAL)
                ELSE 0
            END) AS daily_pnl
        FROM fills f
        JOIN orders o    ON f.order_id   = o.order_id
        JOIN plans p     ON o.plan_id    = p.plan_id
        JOIN signals sig ON p.signal_id  = sig.signal_id
        LEFT JOIN settlements s
               ON sig.target_date = s.target_date
              AND sig.bracket      = s.bracket
        WHERE o.run_id = ? AND f.status = 'filled' AND sig.target_date IS NOT NULL
        GROUP BY sig.target_date
        ORDER BY sig.target_date
        """,
        (run_id,),
    ).fetchall()

    cumulative = 0.0
    points = []
    for r in rows:
        cumulative += r["daily_pnl"] or 0.0
        points.append({"date": r["date"], "cumulative_pnl": round(cumulative, 4)})
    return points


@router.get("/{run_id}/trades", response_model=list[TradeRow])
def get_run_trades(
    run_id: str,
    db: Db,
    city: Optional[str] = Query(None),
    target_date: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """Joined signal+order+fill+settlement rows for the history view."""
    where = ["o.run_id = ?", "f.status = 'filled'"]
    params: list = [run_id]

    if city:
        where.append("sig.city = ?")
        params.append(city)
    if target_date:
        where.append("sig.target_date = ?")
        params.append(target_date)

    params += [limit, offset]

    rows = db.execute(
        f"""
        SELECT
            sig.signal_id,
            sig.target_date,
            sig.city,
            sig.bracket,
            sig.side            AS signal_side,
            sig.model_version,
            sig.model_p_yes,
            sig.market_price,
            sig.edge,
            o.order_id,
            o.side              AS order_side,
            o.entry_price,
            o.shares,
            o.cost_usd,
            f.status            AS fill_status,
            f.filled_at_utc,
            s.final_yes,
            s.status            AS settlement_status,
            -- pnl_usd computed inline (TEXT result so frontend formats it)
            CASE
                WHEN s.final_yes IS NULL THEN NULL
                WHEN o.side = 'BUY_YES' AND s.final_yes = 1
                    THEN CAST(CAST(f.filled_shares AS REAL) * (1 - CAST(f.filled_price AS REAL)) AS TEXT)
                WHEN o.side = 'BUY_YES' AND s.final_yes = 0
                    THEN CAST(-CAST(f.filled_shares AS REAL) * CAST(f.filled_price AS REAL) AS TEXT)
                WHEN o.side = 'BUY_NO' AND s.final_yes = 0
                    THEN CAST(CAST(f.filled_shares AS REAL) * (1 - CAST(f.filled_price AS REAL)) AS TEXT)
                WHEN o.side = 'BUY_NO' AND s.final_yes = 1
                    THEN CAST(-CAST(f.filled_shares AS REAL) * CAST(f.filled_price AS REAL) AS TEXT)
                ELSE NULL
            END AS pnl_usd
        FROM fills f
        JOIN orders o   ON f.order_id   = o.order_id
        JOIN plans p    ON o.plan_id    = p.plan_id
        JOIN signals sig ON p.signal_id = sig.signal_id
        LEFT JOIN settlements s
               ON sig.target_date = s.target_date
              AND sig.bracket      = s.bracket
        WHERE {' AND '.join(where)}
        ORDER BY f.filled_at_utc DESC
        LIMIT ? OFFSET ?
        """,
        params,
    ).fetchall()

    return [dict(r) for r in rows]
