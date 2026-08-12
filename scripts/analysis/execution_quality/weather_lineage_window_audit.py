#!/usr/bin/env python3
"""Audit canonical weather lineage for a bounded target-date window.

The JSON output keeps one row per canonical order and separately records
plan/order gaps, candidate coverage, fills, and settlement state.  It is a
diagnostic artifact: authenticated exchange state remains authoritative for
currently open orders.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def classify_order(row: dict[str, Any]) -> str:
    if row.get("alias_execution_id"):
        return "duplicate_execution_alias_excluded"
    status = str(row.get("order_status") or "").lower()
    error = " ".join(
        str(row.get(key) or "") for key in ("error_reason", "exchange_response")
    ).lower()
    if status == "failed":
        if "invalid expiration" in error:
            return "rejected_gtd_expiration_historical_code_issue"
        if "post-only" in error and ("cross" in error or "invalid" in error):
            return "rejected_post_only_cross_historical_retry_gap"
        return "rejected_other"
    if status == "blocked":
        return "blocked_before_exchange"
    if status == "cancelled":
        return "lifecycle_cancel"
    if status == "simulated_open":
        return "paper_or_simulated"
    if status == "submitted" and int(row.get("effective_fill_count") or 0) > 0:
        return "submitted_filled"
    if status == "submitted":
        return "submitted_unfilled_historical"
    return status or "unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")

    order_rows = rows(
        conn,
        """
        WITH effective_fills AS (
          SELECT ft.execution_id, COUNT(*) AS effective_fill_count,
                 SUM(ft.fill_qty) AS effective_fill_shares,
                 SUM(ft.cost_usd) AS effective_fill_cost_usd,
                 SUM(ft.fees_usd) AS effective_fees_usd,
                 GROUP_CONCAT(ft.fill_id) AS fill_ids,
                 GROUP_CONCAT(DISTINCT COALESCE(ft.settlement_status, 'NULL')) AS settlement_statuses,
                 SUM(CASE WHEN ft.settlement_status='settled' THEN ft.pnl_usd_at_fill ELSE 0 END) AS realized_pnl_usd
          FROM fact_trades ft
          WHERE ft.trade_class='live_real'
          GROUP BY ft.execution_id
        ), candidate_keys AS (
          SELECT condition_id, side, event_date, COUNT(*) AS candidate_rows,
                 GROUP_CONCAT(DISTINCT candidate_grain_version) AS candidate_versions
          FROM fact_signal_candidates
          GROUP BY condition_id, side, event_date
        )
        SELECT
          o.execution_id, o.order_id, o.instance_id, o.run_id,
          o.status AS order_status, o.clob_status, o.order_side,
          o.shares AS submitted_shares, o.cost_usd AS submitted_cost_usd,
          o.notional AS submitted_notional_usd, o.limit_price, o.posted_price,
          o.maker_only, o.execution_action, o.child_order_role,
          o.risk_status, o.risk_reason,
          json_extract(o.order_payload, '$.order_type') AS order_type,
          json_extract(o.order_payload, '$.expiration') AS expiration,
          json_extract(o.order_payload, '$.live_attempt_ts_utc') AS live_attempt_ts_utc,
          json_extract(o.order_payload, '$.max_shares_per_market') AS scoped_share_cap,
          json_extract(o.order_payload, '$.share_cap_check.share_cap_violation') AS share_cap_violation,
          o.placed_at_utc, o.created_at_utc, o.error_classification,
          o.error_reason, o.exchange_response,
          p.plan_id, p.status AS plan_status, p.order_side AS plan_side,
          p.desired_shares, p.notional AS plan_notional_usd,
          p.execution_policy, p.skip_reason, p.execution_profile,
          s.signal_id, s.producer_system, s.producer_run_id,
          s.target_date, s.city, s.bracket, s.condition_id, s.market_id,
          s.token_id, s.signal_side, s.snapshot_ts_utc,
          a.alias_execution_id,
          COALESCE(ef.effective_fill_count, 0) AS effective_fill_count,
          COALESCE(ef.effective_fill_shares, 0) AS effective_fill_shares,
          COALESCE(ef.effective_fill_cost_usd, 0) AS effective_fill_cost_usd,
          COALESCE(ef.effective_fees_usd, 0) AS effective_fees_usd,
          ef.fill_ids, ef.settlement_statuses, ef.realized_pnl_usd,
          COALESCE(ck.candidate_rows, 0) AS candidate_rows,
          ck.candidate_versions
        FROM orders o
        LEFT JOIN plans p ON p.plan_id=o.plan_id
        LEFT JOIN signals s ON s.signal_id=p.signal_id
        LEFT JOIN order_execution_aliases a ON a.alias_execution_id=o.execution_id
        LEFT JOIN effective_fills ef ON ef.execution_id=o.execution_id
        LEFT JOIN candidate_keys ck
          ON ck.condition_id=s.condition_id
         AND ck.side=o.order_side
         AND ck.event_date=s.target_date
        WHERE s.target_date BETWEEN ? AND ?
        ORDER BY s.target_date, s.city, s.condition_id, o.created_at_utc, o.execution_id
        """,
        (args.start, args.end),
    )
    for row in order_rows:
        row["classification"] = classify_order(row)
        row["side_mismatch"] = bool(
            row.get("plan_side") and row.get("order_side")
            and row["plan_side"] != row["order_side"]
        )
        row["candidate_gap"] = bool(
            str(row.get("order_side") or "").startswith("BUY_")
            and int(row.get("candidate_rows") or 0) == 0
        )

    plan_gaps = rows(
        conn,
        """
        SELECT p.plan_id, p.run_id, p.status AS plan_status, r.execution_mode,
               p.order_side, p.desired_shares, p.notional, p.execution_policy,
               p.skip_reason, p.created_at_utc,
               s.signal_id, s.target_date, s.city, s.bracket, s.condition_id,
               s.signal_side,
               CASE WHEN EXISTS (
                 SELECT 1 FROM plans p2 JOIN orders o2 ON o2.plan_id=p2.plan_id
                 WHERE p2.signal_id=p.signal_id
               ) THEN 1 ELSE 0 END AS signal_has_other_order
        FROM plans p
        JOIN signals s ON s.signal_id=p.signal_id
        LEFT JOIN runs r ON r.run_id=p.run_id
        LEFT JOIN orders o ON o.plan_id=p.plan_id
        WHERE s.target_date BETWEEN ? AND ? AND o.execution_id IS NULL
        ORDER BY s.target_date, s.city, p.created_at_utc, p.plan_id
        """,
        (args.start, args.end),
    )
    for row in plan_gaps:
        if row.get("execution_mode") == "paper":
            row["classification"] = "paper_plan_without_venue_order"
        elif row.get("plan_status") == "blocked":
            row["classification"] = "blocked_plan_without_order"
        else:
            row["classification"] = "historical_canonical_orphan_plan"

    fill_candidate_gaps = rows(
        conn,
        """
        WITH fill_keys AS (
          SELECT condition_id, side, target_date, COUNT(*) AS fills,
                 SUM(cost_usd) AS cost_usd, GROUP_CONCAT(fill_id) AS fill_ids
          FROM fact_trades
          WHERE trade_class='live_real' AND target_date BETWEEN ? AND ?
          GROUP BY condition_id, side, target_date
        ), candidate_keys AS (
          SELECT DISTINCT condition_id, side, event_date
          FROM fact_signal_candidates
          WHERE event_date BETWEEN ? AND ?
        )
        SELECT fk.*,
               CASE WHEN fk.side LIKE 'SELL_%' THEN 'expected_exit_without_entry_candidate'
                    ELSE 'unexpected_fill_candidate_gap' END AS classification
        FROM fill_keys fk
        LEFT JOIN candidate_keys ck
          ON ck.condition_id=fk.condition_id AND ck.side=fk.side
         AND ck.event_date=fk.target_date
        WHERE ck.condition_id IS NULL
        ORDER BY fk.target_date, fk.condition_id, fk.side
        """,
        (args.start, args.end, args.start, args.end),
    )

    selected_v2 = rows(
        conn,
        """
        SELECT candidate_id, strategy_key, city, event_date, condition_id,
               side, candidate_status, candidate_blocker, policy_id
        FROM fact_signal_candidates
        WHERE candidate_grain_version<>'v1_legacy_daily'
          AND event_date BETWEEN ? AND ? AND policy_selected=1
        ORDER BY event_date, city, candidate_id
        """,
        (args.start, args.end),
    )

    open_fills = rows(
        conn,
        """
        SELECT instance_id, strategy_id, execution_policy, city, target_date,
               condition_id, market_id, bracket, side, fill_id, fill_ts_utc,
               fill_price, fill_qty, fees_usd, cost_usd, settlement_status,
               val_mid, val_bid, val_last_fill, unrealized_pnl_mid,
               val_snapshot_ts_utc
        FROM fact_trades
        WHERE trade_class='live_real' AND COALESCE(settlement_status, '')<>'settled'
        ORDER BY target_date, city, condition_id, side, fill_ts_utc
        """,
    )

    classifications = Counter(str(row["classification"]) for row in order_rows)
    plan_classes = Counter(str(row["classification"]) for row in plan_gaps)
    stat = os.stat(args.db.resolve())
    payload = {
        "schema_version": "weather_lineage_window_audit_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {"target_date_start": args.start, "target_date_end": args.end},
        "db_identity": {
            "requested_path": str(args.db),
            "realpath": str(args.db.resolve()),
            "device": stat.st_dev,
            "inode": stat.st_ino,
            "fact_built_at_utc": conn.execute(
                "SELECT MAX(fact_built_at_utc) FROM fact_trades"
            ).fetchone()[0],
        },
        "summary": {
            "orders": len(order_rows),
            "canonical_non_alias_orders": sum(not row.get("alias_execution_id") for row in order_rows),
            "order_classifications": dict(sorted(classifications.items())),
            "side_mismatches": sum(bool(row["side_mismatch"]) for row in order_rows),
            "buy_order_candidate_gaps": sum(bool(row["candidate_gap"]) for row in order_rows),
            "plan_without_order": len(plan_gaps),
            "plan_gap_classifications": dict(sorted(plan_classes.items())),
            "fill_candidate_gap_keys": len(fill_candidate_gaps),
            "unexpected_fill_candidate_gap_keys": sum(
                row["classification"] == "unexpected_fill_candidate_gap"
                for row in fill_candidate_gaps
            ),
            "selected_v2_research_candidates": len(selected_v2),
            "current_unsettled_fills": len(open_fills),
            "current_unsettled_cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in open_fills), 6),
        },
        "orders": order_rows,
        "plans_without_orders": plan_gaps,
        "fill_candidate_gaps": fill_candidate_gaps,
        "selected_v2_research_candidates": selected_v2,
        "current_unsettled_fills": open_fills,
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    print(f"artifact={args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
