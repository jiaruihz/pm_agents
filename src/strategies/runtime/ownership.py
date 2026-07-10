"""Deterministic ownership rules for config and order lineage.

No name-based guessing: a config is assigned only when its explicit
execution_policy identifies a single strategy definition.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def strategy_key_for_params(params: dict[str, Any]) -> str | None:
    policy = str(params.get("execution_policy") or "").strip()
    if not policy:
        return None
    if policy in {"mid_price_core_v1", "mid_price_core_v2"}:
        return "weather_edge_v1"
    if policy.startswith("low_price_yes_lottery") or policy.startswith("low_price_yes_take_profit_exit"):
        return "forecast_quality.low_price_yes_lottery"
    if policy.startswith("fast_source_prev_no"):
        return "latency_arb.fast_source_prev_no"
    if policy.startswith("regime_routed_no"):
        return "reheat_risk.regime_routed_no"
    if policy.startswith("theta_current_yes"):
        return "reheat_risk.current_yes"
    if policy.startswith("tmax_distribution_edge"):
        return "reheat_risk.tmax_distribution_edge"
    if policy.startswith("value_d1_no"):
        return "reheat_risk.value_d1_no"
    return None


def sync_config_ownership(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT config_id, strategy_key, params FROM strategy_config").fetchall()
    assigned = 0
    unassigned = 0
    for row in rows:
        if row["strategy_key"]:
            continue
        try:
            params = json.loads(row["params"] or "{}")
        except (TypeError, json.JSONDecodeError):
            params = {}
        key = strategy_key_for_params(params if isinstance(params, dict) else {})
        if key:
            conn.execute("UPDATE strategy_config SET strategy_key=? WHERE config_id=?", (key, row["config_id"]))
            assigned += 1
        else:
            unassigned += 1
    return {"config_ownership_assigned": assigned, "config_ownership_unassigned": unassigned}


def backfill_order_instance_links(conn: sqlite3.Connection) -> dict[str, int]:
    """Fill only evidence-backed legacy order instance links.

    Strategy-runtime migration runs carry the exact instance id in their tags.
    A config can identify an instance only when it has a single deployment.
    """
    tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"orders", "strategy_instance", "order_instance_lineage"} <= tables:
        return {"order_instance_from_run_tag": 0, "order_instance_from_unique_config": 0}

    known = {str(row[0]) for row in conn.execute("SELECT instance_id FROM strategy_instance")}
    from_tag = 0
    tagged_rows = conn.execute(
        """
        SELECT o.execution_id, r.tags
        FROM orders o JOIN runs r ON r.run_id=o.run_id
        LEFT JOIN order_instance_lineage oil ON oil.execution_id=o.execution_id
        WHERE o.instance_id IS NULL AND oil.execution_id IS NULL AND r.tags IS NOT NULL
        """
    ).fetchall()
    for row in tagged_rows:
        try:
            tags = json.loads(row["tags"])
        except (TypeError, json.JSONDecodeError):
            continue
        matches = [str(tag) for tag in tags if str(tag) in known] if isinstance(tags, list) else []
        if len(matches) == 1:
            conn.execute(
                "INSERT OR IGNORE INTO order_instance_lineage (execution_id, instance_id, source, evidence) VALUES (?, ?, 'run_tag', ?)",
                (row["execution_id"], matches[0], json.dumps(tags, ensure_ascii=False)),
            )
            from_tag += 1

    from_config = 0
    unique_config_rows = conn.execute(
        """
        SELECT config_id, MIN(instance_id) AS instance_id
        FROM strategy_instance
        WHERE config_id IS NOT NULL
        GROUP BY config_id
        HAVING COUNT(*) = 1
        """
    ).fetchall()
    for row in unique_config_rows:
        candidate_orders = conn.execute(
            """
            SELECT o.execution_id
            FROM orders o JOIN runs r ON r.run_id=o.run_id
            LEFT JOIN order_instance_lineage oil ON oil.execution_id=o.execution_id
            WHERE o.instance_id IS NULL AND oil.execution_id IS NULL AND r.config_id=?
            """,
            (row["config_id"],),
        ).fetchall()
        for order in candidate_orders:
            conn.execute(
                "INSERT OR IGNORE INTO order_instance_lineage (execution_id, instance_id, source, evidence) VALUES (?, ?, 'unique_config', ?)",
                (order["execution_id"], row["instance_id"], row["config_id"]),
            )
            from_config += 1
    return {"order_instance_from_run_tag": from_tag, "order_instance_from_unique_config": from_config}
