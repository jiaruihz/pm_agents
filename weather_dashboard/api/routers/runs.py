"""Runs endpoints."""

import json
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
import sqlite3

from weather_dashboard.api.deps import get_db
from weather_dashboard.api.schemas import RunDetail, RunSummary, TradeDrilldown, TradeRow
from weather_dashboard.metrics.calc import compute_metrics
from weather_dashboard.metrics.save import save_metrics

router = APIRouter(prefix="/runs", tags=["runs"])

Db = Annotated[sqlite3.Connection, Depends(get_db)]

_GROUP_BY_COLS: dict[str, str] = {
    "city_pool":       "sig.city_pool",
    "forecast_source": "sig.forecast_source",
    "model_version":   "sig.model_version",
    "side":            "o.order_side",
    "city":            "sig.city",
    "target_date":     "sig.target_date",
    "bracket":         "sig.bracket",
}

_SETTLEMENTS_DEDUP = """
    (
        SELECT
            target_date,
            condition_id,
            bracket,
            MAX(final_price) AS final_price,
            MAX(settlement_status) AS settlement_status
        FROM settlements
        GROUP BY target_date, condition_id, bracket
    )
"""


def _parse_tags(raw) -> list[str]:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def _row_to_run_summary(row) -> dict:
    keys = row.keys() if hasattr(row, "keys") else []
    cached_metrics = row["metrics"] if "metrics" in keys else None
    return {
        "run_id": row["run_id"],
        "producer_system": row["producer_system"] if "producer_system" in keys else None,
        "producer_run_id": row["producer_run_id"] if "producer_run_id" in keys else None,
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
        "metrics": json.loads(cached_metrics) if cached_metrics else None,
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
        # Accept either canonical id or any alias — match all aliased runs.
        where.append(
            "config_id IN (SELECT alias_config_id FROM config_aliases "
            "WHERE canonical_config_id = COALESCE("
            "(SELECT canonical_config_id FROM config_aliases WHERE alias_config_id = ?), ?))"
        )
        params.extend([config_id, config_id])

    params += [limit, offset]
    rows = db.execute(
        f"SELECT * FROM runs WHERE {' AND '.join(where)} "
        f"ORDER BY created_at_utc DESC LIMIT ? OFFSET ?",
        params,
    ).fetchall()

    return [_row_to_run_summary(r) for r in rows]


@router.get("/{run_id}", response_model=RunDetail)
def get_run(
    run_id: str,
    db: Db,
    include_metrics: bool = Query(True),
    refresh: bool = Query(False, description="Force recompute metrics even if cached"),
):
    row = db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    result = _row_to_run_summary(row)  # includes cached metrics from runs.metrics
    if include_metrics and (refresh or result["metrics"] is None):
        result["metrics"] = save_metrics(db, run_id)
    elif not include_metrics:
        result["metrics"] = None
    return result


@router.post("/metrics/refresh-all")
def refresh_all_metrics(db: Db):
    """Recompute and persist metrics for all runs. Call after settlement ingestion."""
    from weather_dashboard.metrics.save import save_all_metrics
    results = save_all_metrics(db)
    updated = sum(1 for m in results.values() if m["num_trades"] > 0)
    return {"total_runs": len(results), "runs_with_trades": updated}


@router.get("/{run_id}/metrics/slice")
def get_run_metrics_slice(
    run_id: str,
    db: Db,
    group_by: Literal["city_pool", "forecast_source", "model_version", "side", "city", "target_date", "bracket"] = Query(
        ..., description="Dimension to slice by"
    ),
):
    """
    Per-slice metrics for a run (e.g. T1 vs T2, ecmwf vs gfs).
    Returns a list of {slice_value, num_trades, settled_trades, total_pnl_usd,
    win_rate, total_cost_usd, roi} dicts.
    """
    row = db.execute("SELECT run_id FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    col = _GROUP_BY_COLS[group_by]
    rows = db.execute(
        f"""
        SELECT
            {col} AS slice_value,
            COUNT(*)                                                  AS num_trades,
            SUM(CASE WHEN s.final_price IS NOT NULL THEN 1 ELSE 0 END) AS settled_trades,
            SUM(CASE
                WHEN s.final_price IS NULL THEN 0
                WHEN o.order_side = 'BUY_YES'
                    THEN CAST(f.filled_shares AS REAL) * (CAST(s.final_price AS REAL) - CAST(f.filled_price AS REAL))
                WHEN o.order_side = 'BUY_NO'
                    THEN CAST(f.filled_shares AS REAL) * ((1 - CAST(s.final_price AS REAL)) - CAST(f.filled_price AS REAL))
                ELSE 0
            END)                                                      AS total_pnl_usd,
            SUM(CASE
                WHEN s.final_price IS NOT NULL AND (
                    (o.order_side = 'BUY_YES' AND CAST(s.final_price AS REAL) > CAST(f.filled_price AS REAL)) OR
                    (o.order_side = 'BUY_NO'  AND (1 - CAST(s.final_price AS REAL)) > CAST(f.filled_price AS REAL))
                ) THEN 1 ELSE 0
            END) * 1.0 /
            NULLIF(SUM(CASE WHEN s.final_price IS NOT NULL THEN 1 ELSE 0 END), 0)
                                                                      AS win_rate,
            SUM(CAST(f.filled_shares AS REAL) * CAST(f.filled_price AS REAL))
                                                                      AS total_cost_usd
        FROM fills f
        JOIN orders o    ON f.execution_id = o.execution_id
        JOIN plans p     ON o.plan_id    = p.plan_id
        JOIN signals sig ON p.signal_id  = sig.signal_id
        LEFT JOIN {_SETTLEMENTS_DEDUP} s
               ON sig.target_date = s.target_date
              AND sig.condition_id = s.condition_id
              AND sig.bracket      = s.bracket
        WHERE o.run_id = ? AND f.status IN ('filled', 'simulated')
        GROUP BY {col}
        ORDER BY total_pnl_usd DESC
        """,
        (run_id,),
    ).fetchall()

    result = []
    for r in rows:
        cost = r["total_cost_usd"] or 0.0
        pnl = r["total_pnl_usd"] or 0.0
        result.append({
            "slice_value":    r["slice_value"],
            "num_trades":     r["num_trades"],
            "settled_trades": r["settled_trades"],
            "total_pnl_usd":  round(pnl, 4),
            "win_rate":       round(r["win_rate"], 4) if r["win_rate"] is not None else None,
            "total_cost_usd": round(cost, 4),
            "roi":            round(pnl / cost, 4) if cost else None,
        })
    return {"group_by": group_by, "slices": result}


@router.get("/{run_id}/equity")
def get_run_equity(run_id: str, db: Db):
    """Daily cumulative PnL for the equity curve chart."""
    row = db.execute("SELECT run_id FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    rows = db.execute(
        f"""
        SELECT
            sig.target_date AS date,
            SUM(CASE
                WHEN s.final_price IS NULL THEN 0
                WHEN o.order_side = 'BUY_YES'
                    THEN CAST(f.filled_shares AS REAL) * (CAST(s.final_price AS REAL) - CAST(f.filled_price AS REAL))
                WHEN o.order_side = 'BUY_NO'
                    THEN CAST(f.filled_shares AS REAL) * ((1 - CAST(s.final_price AS REAL)) - CAST(f.filled_price AS REAL))
                ELSE 0
            END) AS daily_pnl
        FROM fills f
        JOIN orders o    ON f.execution_id = o.execution_id
        JOIN plans p     ON o.plan_id    = p.plan_id
        JOIN signals sig ON p.signal_id  = sig.signal_id
        LEFT JOIN {_SETTLEMENTS_DEDUP} s
               ON sig.target_date = s.target_date
              AND sig.condition_id = s.condition_id
              AND sig.bracket      = s.bracket
        WHERE o.run_id = ? AND f.status IN ('filled', 'simulated') AND sig.target_date IS NOT NULL
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


def _json_or_raw(raw: str | None):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw


@router.get("/{run_id}/trades/{signal_id}", response_model=TradeDrilldown)
def get_trade_drilldown(run_id: str, signal_id: str, db: Db):
    """Vertical canonical lineage for one trade: Signal -> Plan -> Order -> Fill -> Settlement."""
    run = db.execute("SELECT run_id FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    signal = db.execute(
        """
        SELECT DISTINCT sig.*
        FROM signals sig
        JOIN plans p ON p.signal_id = sig.signal_id
        WHERE p.run_id = ? AND sig.signal_id = ?
        """,
        (run_id, signal_id),
    ).fetchone()
    if not signal:
        raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found in run {run_id}")

    plans = db.execute(
        "SELECT * FROM plans WHERE run_id = ? AND signal_id = ? ORDER BY created_at_utc",
        (run_id, signal_id),
    ).fetchall()
    orders = db.execute(
        """
        SELECT o.*
        FROM orders o
        JOIN plans p ON p.plan_id = o.plan_id
        WHERE o.run_id = ? AND p.signal_id = ?
        ORDER BY o.created_at_utc
        """,
        (run_id, signal_id),
    ).fetchall()
    fills = db.execute(
        """
        SELECT f.*
        FROM fills f
        JOIN orders o ON o.execution_id = f.execution_id
        JOIN plans p ON p.plan_id = o.plan_id
        WHERE o.run_id = ? AND p.signal_id = ?
        ORDER BY f.created_at_utc
        """,
        (run_id, signal_id),
    ).fetchall()
    settlement = db.execute(
        """
        SELECT s.*
        FROM settlements s
        WHERE s.target_date = ?
          AND s.condition_id = ?
          AND s.bracket = ?
        ORDER BY s.created_at_utc DESC
        LIMIT 1
        """,
        (signal["target_date"], signal["condition_id"], signal["bracket"]),
    ).fetchone()
    artifacts = db.execute(
        """
        SELECT artifact_id, artifact_kind, source_path, row_count, payload, created_at_utc
        FROM run_artifacts
        WHERE run_id = ?
        ORDER BY artifact_kind, source_path
        """,
        (run_id,),
    ).fetchall()

    order_rows = []
    for row in orders:
        payload = dict(row)
        payload["exchange_response"] = _json_or_raw(payload.get("exchange_response"))
        order_rows.append(payload)

    artifact_rows = []
    for row in artifacts:
        payload = dict(row)
        payload["payload"] = _json_or_raw(payload.get("payload"))
        artifact_rows.append(payload)

    return {
        "run_id": run_id,
        "signal_id": signal_id,
        "signal": dict(signal),
        "plans": [dict(row) for row in plans],
        "orders": order_rows,
        "fills": [dict(row) for row in fills],
        "settlement": dict(settlement) if settlement else None,
        "artifacts": artifact_rows,
    }


@router.get("/{run_id}/trades", response_model=list[TradeRow])
def get_run_trades(
    run_id: str,
    db: Db,
    city: Optional[str] = Query(None),
    target_date: Optional[str] = Query(None),
    city_pool: Optional[str] = Query(None, description="t1_trading | t2_research"),
    forecast_source: Optional[str] = Query(None, description="e.g. open_meteo_live_gfs"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """Joined signal+order+fill+settlement rows for the history view."""
    where = ["o.run_id = ?"]
    params: list = [run_id]

    if city:
        where.append("sig.city = ?")
        params.append(city)
    if target_date:
        where.append("sig.target_date = ?")
        params.append(target_date)
    if city_pool:
        where.append("sig.city_pool = ?")
        params.append(city_pool)
    if forecast_source:
        where.append("sig.forecast_source = ?")
        params.append(forecast_source)

    params += [limit, offset]

    rows = db.execute(
        f"""
        SELECT
            sig.signal_id,
            sig.target_date,
            sig.city,
            sig.bracket,
            sig.signal_side,
            sig.model_version,
            sig.model_p_yes,
            sig.market_price,
            sig.edge,
            sig.city_pool,
            sig.forecast_source,
            sig.icao,
            sig.hours_to_settle,
            o.execution_id,
            o.order_id,
            o.order_side,
            o.entry_price,
            o.shares,
            o.cost_usd,
            COALESCE(f.status, o.status) AS fill_status,
            f.filled_at_utc,
            s.final_price,
            s.settlement_status,
            CASE
                WHEN s.final_price IS NULL OR f.execution_id IS NULL THEN NULL
                WHEN o.order_side = 'BUY_YES'
                    THEN CAST(CAST(f.filled_shares AS REAL) * (CAST(s.final_price AS REAL) - CAST(f.filled_price AS REAL)) AS TEXT)
                WHEN o.order_side = 'BUY_NO'
                    THEN CAST(CAST(f.filled_shares AS REAL) * ((1 - CAST(s.final_price AS REAL)) - CAST(f.filled_price AS REAL)) AS TEXT)
                ELSE NULL
            END AS pnl_usd
        FROM orders o
        JOIN plans p     ON o.plan_id    = p.plan_id
        JOIN signals sig ON p.signal_id  = sig.signal_id
        LEFT JOIN fills f ON f.execution_id = o.execution_id
        LEFT JOIN {_SETTLEMENTS_DEDUP} s
               ON sig.target_date = s.target_date
              AND sig.condition_id = s.condition_id
              AND sig.bracket      = s.bracket
        WHERE {' AND '.join(where)}
        ORDER BY COALESCE(f.filled_at_utc, o.placed_at_utc, o.created_at_utc) DESC
        LIMIT ? OFFSET ?
        """,
        params,
    ).fetchall()

    return [dict(r) for r in rows]
