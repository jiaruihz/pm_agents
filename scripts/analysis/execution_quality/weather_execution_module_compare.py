#!/usr/bin/env python3
"""Compare realized execution modules on one canonical opportunity denominator.

The comparison unit is a root order chain.  Reprices and taker fallbacks point
backward through ``orders.source_order_id`` and are collapsed into the initial
child order.  Different root policies sharing one ``signal_id`` are therefore
paired executions of the same opportunity instead of unrelated fills.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _weighted_average(pairs: Iterable[tuple[float, float]]) -> float | None:
    values = [(value, weight) for value, weight in pairs if weight > 0]
    weight = sum(item[1] for item in values)
    return sum(value * item_weight for value, item_weight in values) / weight if weight else None


def _root_order(order: dict[str, Any], by_order_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    current = order
    seen: set[str] = set()
    while True:
        source_id = str(current.get("source_order_id") or "").strip()
        if not source_id or source_id in seen or source_id not in by_order_id:
            return current
        seen.add(source_id)
        current = by_order_id[source_id]


def load_order_chains(
    conn: sqlite3.Connection,
    *,
    instances: list[str] | None = None,
    active_live_only: bool = False,
) -> list[dict[str, Any]]:
    where: list[str] = ["o.venue='polymarket_clob'"]
    params: list[Any] = []
    if instances:
        where.append(f"o.instance_id IN ({','.join('?' for _ in instances)})")
        params.extend(instances)
    elif active_live_only:
        where.append(
            "o.instance_id IN (SELECT instance_id FROM strategy_instance "
            "WHERE desired_status='enabled' AND expected_live=1)"
        )
    rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT
              o.execution_id, o.order_id, o.instance_id, o.plan_id,
              p.signal_id, p.execution_profile, p.execution_policy,
              p.order_lifecycle_policy, p.comparison_group_id,
              o.execution_action, o.child_order_role, o.source_order_id,
              o.cancel_before_order_id, o.maker_only, o.status, o.clob_status,
              o.shares, o.cost_usd, o.notional, o.limit_price, o.requested_price,
              o.posted_price, o.posted_notional, o.best_bid, o.best_ask, o.spread,
              o.placed_at_utc, o.created_at_utc,
              s.city, s.target_date, s.bracket, s.signal_side,
              s.condition_id, s.market_id, s.token_id,
              COALESCE(
                (SELECT st.final_price FROM settlements st
                 WHERE st.settlement_status='settled'
                   AND st.target_date=s.target_date AND st.bracket=s.bracket
                   AND s.token_id IS NOT NULL AND st.token_id=s.token_id
                 ORDER BY st.settlement_id LIMIT 1),
                (SELECT st.final_price FROM settlements st
                 WHERE st.settlement_status='settled'
                   AND st.target_date=s.target_date AND st.bracket=s.bracket
                   AND s.condition_id!='' AND st.condition_id=s.condition_id
                 ORDER BY st.settlement_id LIMIT 1),
                (SELECT st.final_price FROM settlements st
                 WHERE st.settlement_status='settled'
                   AND st.target_date=s.target_date AND st.bracket=s.bracket
                   AND s.market_id!='' AND st.market_id=s.market_id
                 ORDER BY st.settlement_id LIMIT 1)
              ) AS final_yes,
              f.fill_id, f.filled_shares, f.filled_price, f.fees_usd,
              f.filled_at_utc, f.status AS fill_status,
              ft.settled, ft.pnl_usd_at_fill
            FROM orders o
            JOIN plans p ON p.plan_id=o.plan_id
            JOIN signals s ON s.signal_id=p.signal_id
            LEFT JOIN fills f ON f.execution_id=o.execution_id
            LEFT JOIN fact_trades ft ON ft.fill_id=f.fill_id
            WHERE {' AND '.join(where)}
            ORDER BY o.created_at_utc, o.execution_id, f.fill_id
            """,
            params,
        ).fetchall()
    ]
    orders: dict[str, dict[str, Any]] = {}
    fills_by_execution: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        execution_id = str(row["execution_id"])
        orders.setdefault(execution_id, {k: v for k, v in row.items() if not k.startswith("fill") and k not in {"fees_usd", "settled", "pnl_usd_at_fill"}})
        if row.get("fill_id"):
            fills_by_execution[execution_id].append(
                {
                    "fill_id": row["fill_id"],
                    "shares": _float(row.get("filled_shares")),
                    "price": _float(row.get("filled_price")),
                    "fees": _float(row.get("fees_usd")),
                    "filled_at_utc": row.get("filled_at_utc"),
                    "settled": int(row.get("settled") or 0),
                    "pnl": _float(row.get("pnl_usd_at_fill")),
                }
            )
    by_order_id = {
        str(order["order_id"]): order
        for order in orders.values()
        if str(order.get("order_id") or "").strip()
    }
    chains: dict[str, dict[str, Any]] = {}
    for order in orders.values():
        root = _root_order(order, by_order_id)
        root_execution_id = str(root["execution_id"])
        chain = chains.setdefault(
            root_execution_id,
            {
                "root_execution_id": root_execution_id,
                "root_order_id": root.get("order_id"),
                "instance_id": root.get("instance_id"),
                "signal_id": root.get("signal_id"),
                "city": root.get("city"),
                "target_date": root.get("target_date"),
                "bracket": root.get("bracket"),
                "signal_side": root.get("signal_side"),
                "final_yes": root.get("final_yes"),
                "root_execution_policy": root.get("execution_policy"),
                "execution_profile": root.get("execution_profile") or "legacy_unprofiled",
                "root_order_lifecycle_policy": root.get("order_lifecycle_policy"),
                "comparison_group_id": root.get("comparison_group_id") or root.get("signal_id"),
                "root_child_order_role": root.get("child_order_role"),
                "root_maker_only": int(root.get("maker_only") or 0),
                "planned_shares": _float(root.get("shares")),
                "planned_notional": _float(root.get("posted_notional"))
                or _float(root.get("notional"))
                or _float(root.get("cost_usd")),
                "root_limit_price": _float(root.get("limit_price")),
                "root_best_bid": _float(root.get("best_bid")),
                "root_best_ask": _float(root.get("best_ask")),
                "root_created_at_utc": root.get("placed_at_utc") or root.get("created_at_utc"),
                "attempt_execution_ids": [],
                "route_policies": [],
                "execution_actions": [],
                "fills": [],
            },
        )
        execution_id = str(order["execution_id"])
        if execution_id not in chain["attempt_execution_ids"]:
            chain["attempt_execution_ids"].append(execution_id)
        policy = str(order.get("execution_policy") or "")
        if policy and policy not in chain["route_policies"]:
            chain["route_policies"].append(policy)
        action = str(order.get("execution_action") or "")
        if action and action not in chain["execution_actions"]:
            chain["execution_actions"].append(action)
        chain["fills"].extend(fills_by_execution.get(execution_id, []))

    out: list[dict[str, Any]] = []
    for chain in chains.values():
        fills = chain.pop("fills")
        filled_shares = sum(fill["shares"] for fill in fills)
        fill_cost = sum(fill["shares"] * fill["price"] + fill["fees"] for fill in fills)
        fees = sum(fill["fees"] for fill in fills)
        average_fill_price = _weighted_average((fill["price"], fill["shares"]) for fill in fills)
        fill_times = [ts for ts in (_parse_ts(fill["filled_at_utc"]) for fill in fills) if ts]
        created = _parse_ts(chain["root_created_at_utc"])
        latency_sec = (min(fill_times) - created).total_seconds() if created and fill_times else None
        settled_fills = [fill for fill in fills if fill["settled"]]
        settled_fill_cost = sum(
            fill["shares"] * fill["price"] + fill["fees"] for fill in settled_fills
        )
        final_yes = chain.get("final_yes")
        payout = None
        if final_yes is not None:
            payout = _float(final_yes)
            if str(chain.get("signal_side") or "").upper() == "NO":
                payout = 1.0 - payout
        unfilled_shares = max(0.0, _float(chain["planned_shares"]) - filled_shares)
        counterfactual_pnl = (
            _float(chain["planned_shares"]) * (payout - _float(chain["root_limit_price"]))
            if payout is not None
            else None
        )
        unfilled_counterfactual_pnl = (
            unfilled_shares * (payout - _float(chain["root_limit_price"]))
            if payout is not None
            else None
        )
        chain.update(
            {
                "attempt_count": len(chain["attempt_execution_ids"]),
                "filled_shares": _round(filled_shares),
                "fill_rate_shares": _round(filled_shares / chain["planned_shares"])
                if chain["planned_shares"]
                else None,
                "average_fill_price": _round(average_fill_price),
                "fill_cost_usd": _round(fill_cost),
                "fees_usd": _round(fees),
                "first_fill_latency_sec": _round(latency_sec),
                "settled_fill_shares": _round(sum(fill["shares"] for fill in settled_fills)),
                "settled_fill_cost_usd": _round(settled_fill_cost),
                "realized_pnl_usd": _round(sum(fill["pnl"] for fill in settled_fills))
                if settled_fills
                else None,
                "all_fills_settled": bool(fills) and len(settled_fills) == len(fills),
                "settlement_available": payout is not None,
                "planned_counterfactual_pnl_at_root_limit": _round(counterfactual_pnl),
                "unfilled_shares": _round(unfilled_shares),
                "unfilled_counterfactual_pnl_at_root_limit": _round(unfilled_counterfactual_pnl),
            }
        )
        out.append(chain)
    return sorted(out, key=lambda row: (str(row["root_created_at_utc"]), str(row["root_execution_id"])))


def summarize(chains: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for chain in chains:
        key = (
            str(chain.get("instance_id") or ""),
            str(chain.get("execution_profile") or "legacy_unprofiled"),
            str(chain.get("root_execution_policy") or ""),
            str(chain.get("root_child_order_role") or ""),
        )
        groups[key].append(chain)
    result: list[dict[str, Any]] = []
    for (instance, profile, policy, role), rows in sorted(groups.items()):
        planned_shares = sum(_float(row["planned_shares"]) for row in rows)
        planned_notional = sum(_float(row["planned_notional"]) for row in rows)
        filled_shares = sum(_float(row["filled_shares"]) for row in rows)
        fill_cost = sum(_float(row["fill_cost_usd"]) for row in rows)
        settled_fill_shares = sum(_float(row["settled_fill_shares"]) for row in rows)
        settled_fill_cost = sum(_float(row["settled_fill_cost_usd"]) for row in rows)
        resolved_planned_notional = sum(
            _float(row["planned_notional"]) for row in rows if row["settlement_available"]
        )
        pnl = (
            sum(_float(row["realized_pnl_usd"]) for row in rows)
            if settled_fill_shares > 0
            else None
        )
        result.append(
            {
                "instance_id": instance,
                "execution_profile": profile,
                "root_execution_policy": policy,
                "root_child_order_role": role,
                "root_chains": len(rows),
                "opportunities": len({str(row["signal_id"]) for row in rows}),
                "planned_shares": _round(planned_shares),
                "filled_shares": _round(filled_shares),
                "fill_rate_shares": _round(filled_shares / planned_shares) if planned_shares else None,
                "planned_notional_usd": _round(planned_notional),
                "fill_cost_usd": _round(fill_cost),
                "fees_usd": _round(sum(_float(row["fees_usd"]) for row in rows)),
                "average_fill_price": _round(
                    _weighted_average(
                        (_float(row["average_fill_price"]), _float(row["filled_shares"]))
                        for row in rows
                    )
                ),
                "average_first_fill_latency_sec": _round(
                    _weighted_average(
                        (_float(row["first_fill_latency_sec"]), 1.0)
                        for row in rows
                        if row["first_fill_latency_sec"] is not None
                    )
                ),
                "settled_fill_shares": _round(settled_fill_shares),
                "settled_fill_cost_usd": _round(settled_fill_cost),
                "resolved_root_chains": sum(1 for row in rows if row["settlement_available"]),
                "resolved_planned_notional_usd": _round(resolved_planned_notional),
                "realized_pnl_usd": _round(pnl),
                "realized_roi_on_fill_cost": _round(pnl / settled_fill_cost)
                if pnl is not None and settled_fill_cost
                else None,
                "planned_counterfactual_pnl_at_root_limit": _round(
                    sum(
                        _float(row["planned_counterfactual_pnl_at_root_limit"])
                        for row in rows
                        if row["settlement_available"]
                    )
                )
                if any(row["settlement_available"] for row in rows)
                else None,
                "unfilled_counterfactual_pnl_at_root_limit": _round(
                    sum(
                        _float(row["unfilled_counterfactual_pnl_at_root_limit"])
                        for row in rows
                        if row["settlement_available"]
                    )
                )
                if any(row["settlement_available"] for row in rows)
                else None,
                "realized_pnl_on_planned_notional": _round(pnl / resolved_planned_notional)
                if pnl is not None and resolved_planned_notional
                else None,
                "route_policy_counts": dict(
                    sorted(
                        (
                            route,
                            sum(1 for row in rows if route in row["route_policies"]),
                        )
                        for route in {route for row in rows for route in row["route_policies"]}
                    )
                ),
            }
        )
    return result


def paired_taker_maker(chains: list[dict[str, Any]]) -> dict[str, Any]:
    by_signal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chain in chains:
        by_signal[str(chain.get("comparison_group_id") or chain["signal_id"])].append(chain)
    pairs: list[dict[str, Any]] = []
    for comparison_group_id, rows in sorted(by_signal.items()):
        takers = [
            row
            for row in rows
            if not row["root_maker_only"]
            and str(row["root_child_order_role"]) in {"taker", "single"}
        ]
        makers = [
            row
            for row in rows
            if row["root_maker_only"] or str(row["root_child_order_role"]) == "maker"
        ]
        for taker in takers:
            for maker in makers:
                taker_price = taker["average_fill_price"]
                maker_price = maker["average_fill_price"]
                common_filled = min(_float(taker["filled_shares"]), _float(maker["filled_shares"]))
                pairs.append(
                    {
                        "comparison_group_id": comparison_group_id,
                        "signal_id": taker["signal_id"],
                        "city": taker["city"],
                        "target_date": taker["target_date"],
                        "taker_policy": taker["root_execution_policy"],
                        "maker_policy": maker["root_execution_policy"],
                        "taker_filled_shares": taker["filled_shares"],
                        "maker_filled_shares": maker["filled_shares"],
                        "maker_fill_rate_shares": maker["fill_rate_shares"],
                        "taker_average_fill_price": taker_price,
                        "maker_average_fill_price": maker_price,
                        "maker_price_improvement": _round(
                            _float(taker_price) - _float(maker_price)
                        )
                        if taker_price is not None and maker_price is not None
                        else None,
                        "common_filled_shares": _round(common_filled),
                        "gross_price_saving_usd": _round(
                            common_filled * (_float(taker_price) - _float(maker_price))
                        )
                        if taker_price is not None and maker_price is not None
                        else None,
                        "maker_route_policies": maker["route_policies"],
                    }
                )
    filled_pairs = [row for row in pairs if row["maker_average_fill_price"] is not None]
    return {
        "paired_opportunities": len(pairs),
        "maker_filled_pairs": len(filled_pairs),
        "maker_pair_fill_rate": _round(len(filled_pairs) / len(pairs)) if pairs else None,
        "average_maker_price_improvement": _round(
            _weighted_average((row["maker_price_improvement"], 1.0) for row in filled_pairs)
        ),
        "gross_price_saving_usd": _round(
            sum(_float(row["gross_price_saving_usd"]) for row in filled_pairs)
        ),
        "pairs": pairs,
    }


def build_report(
    conn: sqlite3.Connection,
    *,
    instances: list[str] | None = None,
    active_live_only: bool = False,
) -> dict[str, Any]:
    chains = load_order_chains(conn, instances=instances, active_live_only=active_live_only)
    freshness = dict(
        conn.execute(
            """
            SELECT
              (SELECT MAX(created_at_utc) FROM orders) AS latest_order_ts_utc,
              (SELECT MAX(filled_at_utc) FROM fills) AS latest_fill_ts_utc,
              (SELECT MAX(fact_built_at_utc) FROM fact_trades) AS latest_fact_built_at_utc
            """
        ).fetchone()
    )
    return {
        "schema_version": "weather_execution_module_compare_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "denominator": "root_order_chain; policies pair only when signal_id matches",
        "filters": {"instances": instances or [], "active_live_only": active_live_only},
        "freshness": freshness,
        "root_chains": len(chains),
        "summary": summarize(chains),
        "paired_taker_maker": paired_taker_maker(chains),
        "chains": chains,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="runtime/weather.db")
    parser.add_argument("--instance", action="append")
    parser.add_argument("--active-live-only", action="store_true")
    parser.add_argument("--json-out")
    args = parser.parse_args()
    conn = sqlite3.connect(f"file:{Path(args.db).resolve()}?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    try:
        report = build_report(
            conn,
            instances=args.instance,
            active_live_only=bool(args.active_live_only),
        )
    finally:
        conn.close()
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
