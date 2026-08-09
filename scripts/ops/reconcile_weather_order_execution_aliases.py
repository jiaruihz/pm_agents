#!/usr/bin/env python3
"""Resolve duplicate canonical executions for one physical CLOB order id."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [
        dict(row)
        for row in conn.execute(
            """
            WITH ranked AS (
              SELECT
                o.execution_id,
                o.order_id AS physical_order_id,
                o.order_side,
                sig.condition_id,
                sig.token_id,
                o.created_at_utc,
                ROW_NUMBER() OVER (
                  PARTITION BY o.order_id
                  ORDER BY o.created_at_utc, o.execution_id
                ) AS identity_rank,
                FIRST_VALUE(o.execution_id) OVER (
                  PARTITION BY o.order_id
                  ORDER BY o.created_at_utc, o.execution_id
                ) AS canonical_execution_id,
                FIRST_VALUE(o.created_at_utc) OVER (
                  PARTITION BY o.order_id
                  ORDER BY o.created_at_utc, o.execution_id
                ) AS canonical_created_at_utc,
                FIRST_VALUE(o.order_side) OVER (
                  PARTITION BY o.order_id
                  ORDER BY o.created_at_utc, o.execution_id
                ) AS canonical_order_side,
                FIRST_VALUE(sig.condition_id) OVER (
                  PARTITION BY o.order_id
                  ORDER BY o.created_at_utc, o.execution_id
                ) AS canonical_condition_id,
                FIRST_VALUE(sig.token_id) OVER (
                  PARTITION BY o.order_id
                  ORDER BY o.created_at_utc, o.execution_id
                ) AS canonical_token_id,
                COUNT(*) OVER (PARTITION BY o.order_id) AS physical_order_rows
              FROM orders o
              JOIN plans p ON p.plan_id=o.plan_id
              JOIN signals sig ON sig.signal_id=p.signal_id
              WHERE o.venue='polymarket_clob'
                AND COALESCE(o.order_id, '') <> ''
            )
            SELECT *
            FROM ranked
            WHERE identity_rank > 1
            ORDER BY physical_order_id, identity_rank
            """
        )
    ]


def reconcile(conn: sqlite3.Connection, *, apply: bool, source_path: str) -> dict[str, Any]:
    rows = _rows(conn)
    conflicts: list[dict[str, Any]] = []
    aliases: list[dict[str, Any]] = []
    for row in rows:
        mismatches = [
            field
            for field, canonical_field in (
                ("order_side", "canonical_order_side"),
                ("condition_id", "canonical_condition_id"),
                ("token_id", "canonical_token_id"),
            )
            if str(row.get(field) or "") != str(row.get(canonical_field) or "")
        ]
        if mismatches:
            conflicts.append(
                {
                    "physical_order_id": row["physical_order_id"],
                    "alias_execution_id": row["execution_id"],
                    "canonical_execution_id": row["canonical_execution_id"],
                    "mismatched_fields": mismatches,
                }
            )
            continue
        aliases.append(row)

    if conflicts:
        return {
            "applied": False,
            "candidate_aliases": len(rows),
            "resolved_aliases": 0,
            "conflicts": conflicts,
        }

    inserted = 0
    if apply:
        for row in aliases:
            evidence = {
                "identity_rule": "earliest_canonical_order_row_for_physical_clob_order_id",
                "physical_order_rows": row["physical_order_rows"],
                "canonical_created_at_utc": row["canonical_created_at_utc"],
                "alias_created_at_utc": row["created_at_utc"],
                "order_side": row["order_side"],
                "condition_id": row["condition_id"],
                "token_id": row["token_id"],
            }
            before = conn.total_changes
            conn.execute(
                """
                INSERT OR IGNORE INTO order_execution_aliases (
                  alias_execution_id, canonical_execution_id, physical_order_id,
                  reason, evidence_json, source_path
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["execution_id"],
                    row["canonical_execution_id"],
                    row["physical_order_id"],
                    "legacy_plan_dependent_execution_id",
                    json.dumps(evidence, sort_keys=True),
                    source_path,
                ),
            )
            inserted += conn.total_changes - before
        conn.commit()

    return {
        "applied": apply,
        "candidate_aliases": len(rows),
        "resolved_aliases": len(aliases),
        "inserted_aliases": inserted,
        "conflicts": [],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    conn = sqlite3.connect(str(args.db_path), timeout=30)
    payload = reconcile(
        conn,
        apply=args.apply,
        source_path=str(Path(__file__).resolve()),
    )
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if not payload["conflicts"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
