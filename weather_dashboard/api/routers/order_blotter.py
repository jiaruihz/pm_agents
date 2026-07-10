"""Unified order/fill blotter for weather strategy execution."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Query

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


@router.get("")
def get_order_blotter(
    db: Db,
    trade_class: str = Query("live_real", description="live_real | paper | snapshot_replay | live_simulated | all"),
    status: str = Query("all", description="all | open | settled | unfilled"),
    config_id: Optional[str] = Query(None),
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
    fact_where = ["1=1"]
    fact_params: list[Any] = []
    if trade_class != "all":
        fact_where.append("trade_class = ?")
        fact_params.append(trade_class)
    if status == "open":
        fact_where.append("COALESCE(settled, 0) = 0")
    elif status == "settled":
        fact_where.append("COALESCE(settled, 0) = 1")
    elif status == "unfilled":
        fact_where.append("0")
    if config_id:
        fact_where.append("config_id = ?")
        fact_params.append(config_id)
    if strategy_id:
        fact_where.append("strategy_id = ?")
        fact_params.append(strategy_id)
    if target_date:
        fact_where.append("target_date = ?")
        fact_params.append(target_date)
    if city:
        fact_where.append("city = ?")
        fact_params.append(city)

    filled = [
        {
            **dict(row),
            "row_kind": "fill",
        }
        for row in db.execute(
            f"""
            SELECT
                trade_class, execution_mode, venue,
                config_id, strategy_id, strategy_name,
                run_id, signal_id, plan_id, execution_id, order_id, fill_id,
                target_date, city, city_pool, icao, bracket, side,
                order_status, fill_status,
                order_ts_utc, fill_ts_utc, snapshot_ts_utc,
                market_price, limit_price, fill_price, fill_qty,
                cost_usd, notional, fees_usd,
                settled, final_yes, pnl_usd_at_fill,
                unrealized_pnl_mid, val_mid, val_snapshot_ts_utc,
                condition_id, market_id
            FROM fact_trades
            WHERE {' AND '.join(fact_where)}
            """,
            fact_params,
        ).fetchall()
    ]

    order_where = ["f.fill_id IS NULL"]
    order_params: list[Any] = []
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
    if config_id:
        order_where.append("r.config_id = ?")
        order_params.append(config_id)
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
                    r.config_id, r.config_id AS strategy_id, sc.name AS strategy_name,
                    r.run_id, p.signal_id, p.plan_id, o.execution_id, o.order_id,
                    NULL AS fill_id,
                    sig.target_date, sig.city, sig.city_pool, sig.icao, sig.bracket,
                    o.order_side AS side,
                    o.status AS order_status, NULL AS fill_status,
                    o.placed_at_utc AS order_ts_utc, NULL AS fill_ts_utc, sig.snapshot_ts_utc,
                    sig.market_price, o.limit_price, NULL AS fill_price, NULL AS fill_qty,
                    o.cost_usd, o.notional, NULL AS fees_usd,
                    0 AS settled, NULL AS final_yes, NULL AS pnl_usd_at_fill,
                    NULL AS unrealized_pnl_mid, NULL AS val_mid, NULL AS val_snapshot_ts_utc,
                    sig.condition_id, sig.market_id,
                    r.state AS run_state
                FROM orders o
                JOIN plans p ON p.plan_id = o.plan_id
                JOIN signals sig ON sig.signal_id = p.signal_id
                JOIN runs r ON r.run_id = o.run_id
                LEFT JOIN strategy_config sc ON sc.config_id = r.config_id
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
            "config_id": config_id,
            "strategy_id": strategy_id,
            "target_date": target_date,
            "city": city,
        },
    }
