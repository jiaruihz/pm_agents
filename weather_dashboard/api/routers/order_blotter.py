"""Unified order/fill blotter for weather strategy execution."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from weather_dashboard.api.deps import get_db

router = APIRouter(prefix="/order-blotter", tags=["order-blotter"])

Db = Annotated[sqlite3.Connection, Depends(get_db)]


def _trade_class_for_venue(venue: str | None, run_state: str | None) -> str:
    if venue == "polymarket_clob":
        return "live_real" if run_state == "live" else "live_simulated"
    if venue == "snapshot_replay":
        return "snapshot_replay"
    return "paper"


def _row_ts(row: dict[str, Any]) -> str:
    return str(row.get("fill_ts_utc") or row.get("order_ts_utc") or row.get("snapshot_ts_utc") or "")


@router.get("/daily-summary")
def get_order_blotter_daily_summary(
    db: Db,
    trade_class: str = Query("live_real", description="live_real | paper | snapshot_replay | live_simulated | all"),
    status: str = Query("all", description="all | open | settled | unfilled"),
    instance_id: Optional[str] = Query(None),
    config_id: Optional[str] = Query(None),
    strategy_key: Optional[str] = Query(None),
    strategy_id: Optional[str] = Query(None),
    target_date: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
) -> dict[str, Any]:
    """Aggregate fill-grain PnL by target date for the order blotter."""
    if instance_id:
        instance = db.execute(
            "SELECT 1 FROM strategy_instance WHERE instance_id = ?", (instance_id,)
        ).fetchone()
        if instance is None:
            raise HTTPException(status_code=404, detail=f"unknown strategy_instance: {instance_id}")

    fact_where = ["1=1"]
    fact_params: list[Any] = []
    if instance_id:
        fact_where.append("ft.instance_id = ?")
        fact_params.append(instance_id)
    if trade_class != "all":
        fact_where.append("ft.trade_class = ?")
        fact_params.append(trade_class)
    if status == "open":
        fact_where.append("COALESCE(ft.settled, 0) = 0")
    elif status == "settled":
        fact_where.append("COALESCE(ft.settled, 0) = 1")
    elif status == "unfilled":
        fact_where.append("0")
    if config_id:
        fact_where.append("ft.config_id = ?")
        fact_params.append(config_id)
    if strategy_key:
        fact_where.append("ft.strategy_key = ?")
        fact_params.append(strategy_key)
    if strategy_id:
        fact_where.append("ft.strategy_id = ?")
        fact_params.append(strategy_id)
    if target_date:
        fact_where.append("ft.target_date = ?")
        fact_params.append(target_date)
    if city:
        fact_where.append("ft.city = ?")
        fact_params.append(city)

    rows = [
        dict(row)
        for row in db.execute(
            f"""
            SELECT
                COALESCE(ft.target_date, '') AS target_date,
                COUNT(*) AS fill_count,
                SUM(COALESCE(ft.cost_usd, 0) + COALESCE(ft.fees_usd, 0)) AS cost_with_fees_usd,
                SUM(CASE WHEN COALESCE(ft.settled, 0) = 1 THEN 1 ELSE 0 END) AS settled_count,
                SUM(CASE WHEN COALESCE(ft.settled, 0) = 1 THEN COALESCE(ft.pnl_usd_at_fill, 0) ELSE 0 END) AS realized_pnl_usd,
                SUM(CASE WHEN COALESCE(ft.settled, 0) = 0 THEN 1 ELSE 0 END) AS open_count,
                SUM(CASE WHEN COALESCE(ft.settled, 0) = 0 AND ft.unrealized_pnl_mid IS NOT NULL THEN 1 ELSE 0 END) AS marked_open_count,
                SUM(CASE WHEN COALESCE(ft.settled, 0) = 0 THEN COALESCE(ft.unrealized_pnl_mid, 0) ELSE 0 END) AS unrealized_pnl_mid_usd
            FROM fact_trades ft
            WHERE {' AND '.join(fact_where)}
            GROUP BY COALESCE(ft.target_date, '')
            ORDER BY target_date DESC
            """,
            fact_params,
        ).fetchall()
    ]
    return {"rows": rows, "filters": {"trade_class": trade_class, "status": status}}


@router.get("")
def get_order_blotter(
    db: Db,
    trade_class: str = Query("live_real", description="live_real | paper | snapshot_replay | live_simulated | all"),
    status: str = Query("all", description="all | open | settled | unfilled"),
    instance_id: Optional[str] = Query(None),
    config_id: Optional[str] = Query(None),
    strategy_key: Optional[str] = Query(None),
    strategy_id: Optional[str] = Query(None),
    target_date: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    limit: int = Query(300, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Return one row per filled trade plus no-fill order rows.

    `fact_trades` is the canonical fill grain. `orders` rows without a matching
    fill are included so rejected/submitted-but-unfilled execution attempts are
    visible in the same blotter.
    """
    effective_config_id = config_id
    if instance_id:
        instance = db.execute(
            "SELECT 1 FROM strategy_instance WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
        if instance is None:
            raise HTTPException(status_code=404, detail=f"unknown strategy_instance: {instance_id}")

    fact_where = ["1=1"]
    fact_params: list[Any] = []
    if instance_id:
        fact_where.append("ft.instance_id = ?")
        fact_params.append(instance_id)
    if trade_class != "all":
        fact_where.append("ft.trade_class = ?")
        fact_params.append(trade_class)
    if status == "open":
        fact_where.append("COALESCE(ft.settled, 0) = 0")
    elif status == "settled":
        fact_where.append("COALESCE(ft.settled, 0) = 1")
    elif status == "unfilled":
        fact_where.append("0")
    if effective_config_id:
        fact_where.append("ft.config_id = ?")
        fact_params.append(effective_config_id)
    if strategy_key:
        fact_where.append("ft.strategy_key = ?")
        fact_params.append(strategy_key)
    if strategy_id:
        fact_where.append("ft.strategy_id = ?")
        fact_params.append(strategy_id)
    if target_date:
        fact_where.append("ft.target_date = ?")
        fact_params.append(target_date)
    if city:
        fact_where.append("ft.city = ?")
        fact_params.append(city)

    filled = [
        {
            **dict(row),
            "row_kind": "fill",
        }
        for row in db.execute(
            f"""
            SELECT
                ft.trade_class, ft.execution_mode, ft.venue,
                ft.config_id, ft.strategy_key, ft.strategy_id,
                COALESCE(NULLIF(sd.strategy_name, ''), ft.strategy_name, sc.name) AS strategy_name,
                COALESCE(ft.config_name, sc.name) AS config_name,
                ft.instance_id AS strategy_instance,
                ft.run_id, ft.signal_id, ft.plan_id, ft.execution_id, ft.order_id, ft.fill_id,
                ft.target_date, ft.city, ft.city_pool, ft.icao, ft.bracket, ft.side,
                ft.order_status, ft.fill_status,
                ft.order_ts_utc, ft.fill_ts_utc, ft.snapshot_ts_utc,
                ft.market_price, ft.limit_price, ft.fill_price, ft.fill_qty,
                ft.cost_usd, ft.notional, ft.fees_usd,
                ft.settlement_status, ft.settled, ft.final_yes, ft.contract_won,
                ft.pnl_usd_at_fill,
                ft.unrealized_pnl_mid, ft.val_mid, ft.val_snapshot_ts_utc,
                ft.condition_id, ft.market_id
            FROM fact_trades ft
            LEFT JOIN strategy_config sc ON sc.config_id = ft.config_id
            LEFT JOIN strategy_def sd ON sd.strategy_key = ft.strategy_key
            WHERE {' AND '.join(fact_where)}
            """,
            fact_params,
        ).fetchall()
    ]

    order_where = ["f.fill_id IS NULL"]
    order_params: list[Any] = []
    if instance_id:
        order_where.append("COALESCE(o.instance_id, oil.instance_id) = ?")
        order_params.append(instance_id)
    if trade_class != "all":
        if trade_class == "live_real":
            order_where.append("o.venue = 'polymarket_clob' AND r.state = 'live'")
        elif trade_class == "live_simulated":
            order_where.append("o.venue = 'polymarket_clob' AND r.state <> 'live'")
        elif trade_class == "paper":
            order_where.append("o.venue = 'paper'")
        elif trade_class == "snapshot_replay":
            order_where.append("o.venue = 'snapshot_replay'")
        else:
            order_where.append("0")
    if status in {"settled"}:
        order_where.append("0")
    if effective_config_id:
        order_where.append("r.config_id = ?")
        order_params.append(effective_config_id)
    if strategy_key:
        order_where.append("sc.strategy_key = ?")
        order_params.append(strategy_key)
    if strategy_id:
        # No-fill canonical orders do not have a separate strategy_id column.
        order_where.append("r.config_id = ?")
        order_params.append(strategy_id)
    if target_date:
        order_where.append("sig.target_date = ?")
        order_params.append(target_date)
    if city:
        order_where.append("sig.city = ?")
        order_params.append(city)

    unfilled = []
    if status in {"all", "open", "unfilled"}:
        unfilled = [
            {
                "row_kind": "order",
                "trade_class": _trade_class_for_venue(row["venue"], row["run_state"]),
                **{k: v for k, v in dict(row).items() if k != "run_state"},
            }
            for row in db.execute(
                f"""
                SELECT
                    r.execution_mode, o.venue,
                    r.config_id, sc.strategy_key, r.config_id AS strategy_id,
                    COALESCE(NULLIF(sd.strategy_name, ''), sc.name) AS strategy_name,
                    sc.name AS config_name,
                    COALESCE(o.instance_id, oil.instance_id) AS strategy_instance,
                    r.run_id, p.signal_id, p.plan_id, o.execution_id, o.order_id,
                    NULL AS fill_id,
                    sig.target_date, sig.city, sig.city_pool, sig.icao, sig.bracket,
                    o.order_side AS side,
                    o.status AS order_status, NULL AS fill_status,
                    o.placed_at_utc AS order_ts_utc, NULL AS fill_ts_utc, sig.snapshot_ts_utc,
                    sig.market_price, o.limit_price, NULL AS fill_price, NULL AS fill_qty,
                    o.cost_usd, o.notional, NULL AS fees_usd,
                    NULL AS settlement_status, 0 AS settled, NULL AS final_yes,
                    NULL AS contract_won, NULL AS pnl_usd_at_fill,
                    NULL AS unrealized_pnl_mid, NULL AS val_mid, NULL AS val_snapshot_ts_utc,
                    sig.condition_id, sig.market_id,
                    r.state AS run_state
                FROM orders o
                JOIN plans p ON p.plan_id = o.plan_id
                JOIN signals sig ON sig.signal_id = p.signal_id
                JOIN runs r ON r.run_id = o.run_id
                LEFT JOIN strategy_config sc ON sc.config_id = r.config_id
                LEFT JOIN strategy_def sd ON sd.strategy_key = sc.strategy_key
                LEFT JOIN order_instance_lineage oil ON oil.execution_id=o.execution_id
                LEFT JOIN fills f ON f.execution_id = o.execution_id
                WHERE {' AND '.join(order_where)}
                """,
                order_params,
            ).fetchall()
        ]

    rows = filled + unfilled
    rows.sort(key=_row_ts, reverse=True)
    sliced = rows[offset: offset + limit]
    return {
        "rows": sliced,
        "total": len(rows),
        "limit": limit,
        "offset": offset,
        "filters": {
            "trade_class": trade_class,
            "status": status,
            "instance_id": instance_id,
            "config_id": effective_config_id,
            "strategy_key": strategy_key,
            "strategy_id": strategy_id,
            "target_date": target_date,
            "city": city,
        },
    }
