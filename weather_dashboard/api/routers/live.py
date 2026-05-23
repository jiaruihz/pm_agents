"""Live monitoring endpoints — Panel A (positions) + Panel B (execution gap)."""

import sqlite3
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query

from weather_dashboard.api.deps import get_db

router = APIRouter(prefix="/live", tags=["live"])

Db = Annotated[sqlite3.Connection, Depends(get_db)]

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


# ---------------------------------------------------------------------------
# GET /api/live/summary
# ---------------------------------------------------------------------------

@router.get("/summary")
def get_live_summary(db: Db):
    """
    High-level live status card:
    - most recent live-cycle timestamp
    - capital deployed (total CLOB cost)
    - open positions count / settled count
    - pending CLOB orders (submitted, no fill)
    - paper run metrics for comparison baseline
    """
    # Most recent ingested live cycle
    last_cycle = db.execute(
        "SELECT MAX(created_at_utc) FROM runs WHERE state = 'live'"
    ).fetchone()[0]

    # CLOB positions breakdown
    clob = db.execute(
        f"""
        SELECT
            COUNT(DISTINCT f.fill_id)                               AS total_fills,
            SUM(CAST(f.filled_shares AS REAL) * CAST(f.filled_price AS REAL))
                                                                    AS capital_deployed_usd,
            SUM(CASE WHEN s.final_price IS NOT NULL THEN 1 ELSE 0 END)
                                                                    AS settled_count,
            SUM(CASE WHEN s.final_price IS NULL THEN 1 ELSE 0 END) AS open_count,
            SUM(CASE
                WHEN s.final_price IS NULL THEN 0
                WHEN o.order_side = 'BUY_YES'
                    THEN CAST(f.filled_shares AS REAL) * (CAST(s.final_price AS REAL) - CAST(f.filled_price AS REAL))
                WHEN o.order_side = 'BUY_NO'
                    THEN CAST(f.filled_shares AS REAL) * ((1 - CAST(s.final_price AS REAL)) - CAST(f.filled_price AS REAL))
                ELSE 0
            END)                                                    AS realized_pnl_usd
        FROM fills f
        JOIN orders o    ON f.execution_id = o.execution_id
        JOIN plans p     ON o.plan_id      = p.plan_id
        JOIN signals sig ON p.signal_id    = sig.signal_id
        LEFT JOIN {_SETTLEMENTS_DEDUP} s
               ON sig.target_date = s.target_date
              AND sig.condition_id = s.condition_id
              AND sig.bracket      = s.bracket
        WHERE o.venue = 'polymarket_clob' AND f.status = 'filled'
        """
    ).fetchone()

    # Pending CLOB orders (submitted, no fill yet, target_date still recent)
    # Exclude stale orders for past target_dates that were never cancelled/expired
    # in our DB (happens when N100 sync is interrupted).
    pending = db.execute(
        """
        SELECT COUNT(*) AS n, SUM(o.cost_usd) AS reserved_usd
        FROM orders o
        JOIN plans   p   ON p.plan_id   = o.plan_id
        JOIN signals sig ON sig.signal_id = p.signal_id
        LEFT JOIN fills f ON f.execution_id = o.execution_id
        WHERE o.venue = 'polymarket_clob'
          AND o.status = 'submitted'
          AND f.fill_id IS NULL
          AND sig.target_date >= date('now', '-1 day')
        """
    ).fetchone()

    # Most recent paper run baseline metrics
    paper_run = db.execute(
        """
        SELECT run_id, metrics, created_at_utc
        FROM runs
        WHERE state = 'paper'
        ORDER BY created_at_utc DESC
        LIMIT 1
        """
    ).fetchone()

    import json
    paper_metrics = None
    if paper_run and paper_run["metrics"]:
        try:
            paper_metrics = json.loads(paper_run["metrics"])
        except Exception:
            pass

    return {
        "last_cycle_utc": last_cycle,
        "clob": {
            "total_positions": clob["total_fills"] or 0,
            "open_count": clob["open_count"] or 0,
            "settled_count": clob["settled_count"] or 0,
            "capital_deployed_usd": round(clob["capital_deployed_usd"] or 0.0, 4),
            "realized_pnl_usd": round(clob["realized_pnl_usd"] or 0.0, 4),
        },
        "pending_orders": {
            "count": pending["n"] or 0,
            "reserved_usd": round(pending["reserved_usd"] or 0.0, 4),
        },
        "paper_baseline": {
            "run_id": paper_run["run_id"] if paper_run else None,
            "metrics": paper_metrics,
        },
    }


# ---------------------------------------------------------------------------
# GET /api/live/positions
# ---------------------------------------------------------------------------

@router.get("/positions")
def get_live_positions(
    db: Db,
    status: Optional[str] = Query(None, description="open | settled | all (default: all)"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """
    Panel A — Current CLOB positions.

    Each row is one filled CLOB order with full lineage:
    signal → order → fill → settlement (if resolved).

    status filter:
      open     — final_price IS NULL (not yet resolved in our DB)
      settled  — final_price IS NOT NULL
      all      — no filter (default)
    """
    where = ["o.venue = 'polymarket_clob'", "f.status = 'filled'"]
    if status == "open":
        where.append("s.final_price IS NULL")
    elif status == "settled":
        where.append("s.final_price IS NOT NULL")

    rows = db.execute(
        f"""
        SELECT
            sig.signal_id,
            sig.city,
            sig.target_date,
            sig.bracket,
            sig.condition_id,
            sig.city_pool,
            sig.forecast_source,
            sig.model_p_yes,
            sig.market_price       AS signal_market_price,
            sig.edge               AS signal_edge,
            o.execution_id,
            o.order_id,
            o.run_id,
            o.order_side,
            o.cost_usd,
            f.fill_id,
            f.filled_shares,
            f.filled_price,
            f.fees_usd,
            f.filled_at_utc,
            s.final_price,
            s.settlement_status,
            CASE
                WHEN s.final_price IS NULL THEN NULL
                WHEN o.order_side = 'BUY_YES'
                    THEN CAST(f.filled_shares AS REAL) * (CAST(s.final_price AS REAL) - CAST(f.filled_price AS REAL))
                WHEN o.order_side = 'BUY_NO'
                    THEN CAST(f.filled_shares AS REAL) * ((1 - CAST(s.final_price AS REAL)) - CAST(f.filled_price AS REAL))
            END AS pnl_usd
        FROM fills f
        JOIN orders o    ON f.execution_id = o.execution_id
        JOIN plans p     ON o.plan_id      = p.plan_id
        JOIN signals sig ON p.signal_id    = sig.signal_id
        LEFT JOIN {_SETTLEMENTS_DEDUP} s
               ON sig.target_date = s.target_date
              AND sig.condition_id = s.condition_id
              AND sig.bracket      = s.bracket
        WHERE {' AND '.join(where)}
        ORDER BY sig.target_date DESC, f.filled_at_utc DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        d["pnl_usd"] = round(d["pnl_usd"], 4) if d["pnl_usd"] is not None else None
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# GET /api/live/execution-gap
# ---------------------------------------------------------------------------

@router.get("/execution-gap")
def get_execution_gap(
    db: Db,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """
    Panel B — Paper vs CLOB execution comparison.

    For signals that had BOTH a paper fill AND a CLOB fill, compare:
    - paper fill price vs CLOB fill price (slippage)
    - paper shares vs CLOB shares (sizing gap)
    - PnL comparison: paper PnL vs real PnL

    Also returns signals with only paper (CLOB order was rejected/errored)
    and signals with only CLOB (shouldn't happen but defensive).
    """
    rows = db.execute(
        f"""
        SELECT
            sig.signal_id,
            sig.city,
            sig.target_date,
            sig.bracket,
            sig.city_pool,
            sig.model_p_yes,
            sig.market_price       AS signal_market_price,
            sig.edge               AS signal_edge,
            sig.signal_side        AS signal_side,

            -- Paper fill
            MAX(CASE WHEN o.venue = 'paper' THEN f.filled_price END)  AS paper_fill_price,
            MAX(CASE WHEN o.venue = 'paper' THEN f.filled_shares END) AS paper_shares,
            MAX(CASE WHEN o.venue = 'paper' THEN f.status END)        AS paper_fill_status,
            MAX(CASE WHEN o.venue = 'paper' THEN o.cost_usd END)      AS paper_cost_usd,

            -- CLOB fill
            MAX(CASE WHEN o.venue = 'polymarket_clob' THEN f.filled_price END)  AS clob_fill_price,
            MAX(CASE WHEN o.venue = 'polymarket_clob' THEN f.filled_shares END) AS clob_shares,
            MAX(CASE WHEN o.venue = 'polymarket_clob' THEN f.status END)        AS clob_fill_status,
            MAX(CASE WHEN o.venue = 'polymarket_clob' THEN o.cost_usd END)      AS clob_cost_usd,

            -- CLOB order status (to detect errors/rejections)
            MAX(CASE WHEN o.venue = 'polymarket_clob' THEN o.status END)        AS clob_order_status,

            -- Settlement
            s.final_price,
            s.settlement_status,

            -- Paper PnL
            CASE
                WHEN s.final_price IS NULL THEN NULL
                ELSE (
                    SELECT SUM(CASE
                        WHEN o2.order_side = 'BUY_YES'
                            THEN CAST(f2.filled_shares AS REAL) * (CAST(s.final_price AS REAL) - CAST(f2.filled_price AS REAL))
                        WHEN o2.order_side = 'BUY_NO'
                            THEN CAST(f2.filled_shares AS REAL) * ((1 - CAST(s.final_price AS REAL)) - CAST(f2.filled_price AS REAL))
                    END)
                    FROM orders o2
                    JOIN plans p2 ON p2.plan_id = o2.plan_id
                    JOIN fills f2 ON f2.execution_id = o2.execution_id
                    WHERE p2.signal_id = sig.signal_id AND o2.venue = 'paper' AND f2.status IN ('filled','simulated')
                )
            END AS paper_pnl_usd,

            -- CLOB PnL
            CASE
                WHEN s.final_price IS NULL THEN NULL
                ELSE (
                    SELECT SUM(CASE
                        WHEN o3.order_side = 'BUY_YES'
                            THEN CAST(f3.filled_shares AS REAL) * (CAST(s.final_price AS REAL) - CAST(f3.filled_price AS REAL))
                        WHEN o3.order_side = 'BUY_NO'
                            THEN CAST(f3.filled_shares AS REAL) * ((1 - CAST(s.final_price AS REAL)) - CAST(f3.filled_price AS REAL))
                    END)
                    FROM orders o3
                    JOIN plans p3 ON p3.plan_id = o3.plan_id
                    JOIN fills f3 ON f3.execution_id = o3.execution_id
                    WHERE p3.signal_id = sig.signal_id AND o3.venue = 'polymarket_clob' AND f3.status = 'filled'
                )
            END AS clob_pnl_usd

        FROM signals sig
        JOIN plans p     ON p.signal_id = sig.signal_id
        JOIN orders o    ON o.plan_id   = p.plan_id
        LEFT JOIN fills f ON f.execution_id = o.execution_id
        LEFT JOIN {_SETTLEMENTS_DEDUP} s
               ON sig.target_date = s.target_date
              AND sig.condition_id = s.condition_id
              AND sig.bracket      = s.bracket
        WHERE o.venue IN ('paper', 'polymarket_clob')
        GROUP BY sig.signal_id
        ORDER BY sig.target_date DESC, sig.city
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        # Compute price slippage: CLOB fill price vs paper fill price
        if d["paper_fill_price"] is not None and d["clob_fill_price"] is not None:
            d["price_slippage"] = round(d["clob_fill_price"] - d["paper_fill_price"], 6)
        else:
            d["price_slippage"] = None

        # Compute PnL gap (real - paper, when both settled)
        if d["clob_pnl_usd"] is not None and d["paper_pnl_usd"] is not None:
            d["pnl_gap_usd"] = round(d["clob_pnl_usd"] - d["paper_pnl_usd"], 4)
        else:
            d["pnl_gap_usd"] = None

        # Round floats
        for key in ("paper_fill_price", "paper_shares", "paper_cost_usd",
                    "clob_fill_price", "clob_shares", "clob_cost_usd",
                    "paper_pnl_usd", "clob_pnl_usd"):
            if d.get(key) is not None:
                d[key] = round(d[key], 4)

        # Classify row
        has_paper = d["paper_fill_status"] is not None or d["clob_order_status"] is not None
        has_clob_fill = d["clob_fill_status"] == "filled"
        if has_clob_fill and d["paper_fill_status"] is not None:
            d["gap_type"] = "both"
        elif has_clob_fill:
            d["gap_type"] = "clob_only"
        elif d["clob_order_status"] in ("error", "cancelled"):
            d["gap_type"] = "paper_only_clob_rejected"
        else:
            d["gap_type"] = "paper_only"

        result.append(d)
    return result
