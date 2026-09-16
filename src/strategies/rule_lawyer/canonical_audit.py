"""Read-only health and lineage audit for the dispute canonical mart."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any


def _scalar(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def audit_canonical(db_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    sources: list[dict[str, Any]] = []
    if not db_path.exists():
        return {
            "schema_version": "dispute_canonical_health_v1",
            "status": "error",
            "errors": ["canonical_db_missing"],
            "warnings": [],
            "db_path": str(db_path),
        }
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
    conn.execute("PRAGMA query_only=ON")
    try:
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        if quick_check != "ok":
            errors.append(f"sqlite_quick_check:{quick_check}")
        for source_path, offset, source_size, prefix_bytes, expected_hash in conn.execute(
            """SELECT source_path,byte_offset,source_size,prefix_bytes,head_sha256
               FROM ingest_watermarks ORDER BY source_path"""
        ):
            path = Path(str(source_path))
            item = {
                "source_path": str(path),
                "watermark_byte_offset": int(offset),
                "watermark_source_size": int(source_size),
                "prefix_bytes": int(prefix_bytes),
            }
            if not path.exists():
                item["status"] = "missing"
                errors.append(f"watermarked_source_missing:{path}")
                sources.append(item)
                continue
            actual_size = path.stat().st_size
            item["actual_size"] = actual_size
            with path.open("rb") as handle:
                actual_hash = hashlib.sha256(handle.read(int(prefix_bytes))).hexdigest()
            if actual_hash != str(expected_hash):
                item["status"] = "prefix_changed"
                errors.append(f"watermarked_source_prefix_changed:{path}")
            elif actual_size < int(offset):
                item["status"] = "shrunk"
                errors.append(f"watermarked_source_shrank:{path}")
            elif actual_size > int(offset):
                item["status"] = "unmaterialized_append"
                item["unmaterialized_bytes"] = actual_size - int(offset)
                warnings.append(f"unmaterialized_source_bytes:{path}:{actual_size-int(offset)}")
            else:
                item["status"] = "current"
            sources.append(item)

        metrics = {
            "cases": _scalar(conn, "SELECT COUNT(*) FROM dispute_cases"),
            "case_events": _scalar(conn, "SELECT COUNT(*) FROM case_events"),
            "book_snapshots": _scalar(conn, "SELECT COUNT(*) FROM market_book_snapshots"),
            "market_group_snapshots": _scalar(conn, "SELECT COUNT(*) FROM market_group_snapshots"),
            "candidates": _scalar(conn, "SELECT COUNT(*) FROM fact_signal_candidates"),
            "selected_candidates": _scalar(
                conn, "SELECT COUNT(*) FROM fact_signal_candidates WHERE policy_selected=1"
            ),
            "trade_intents": _scalar(conn, "SELECT COUNT(*) FROM trade_intents"),
            "paper_plans": _scalar(conn, "SELECT COUNT(*) FROM paper_plans"),
            "paper_orders": _scalar(conn, "SELECT COUNT(*) FROM paper_orders"),
            "paper_fills": _scalar(conn, "SELECT COUNT(*) FROM paper_fills"),
            "capture_demands": _scalar(conn, "SELECT COUNT(*) FROM capture_demands"),
            "capture_receipts": _scalar(conn, "SELECT COUNT(*) FROM capture_receipts"),
            "rest_checkpoint_receipts": _scalar(
                conn, "SELECT COUNT(*) FROM rest_checkpoint_receipts"
            ),
            "shadow_positions": _scalar(conn, "SELECT COUNT(*) FROM shadow_positions"),
            "shadow_markouts": _scalar(conn, "SELECT COUNT(*) FROM shadow_markouts"),
        }
        checks = {
            "cases_without_event": _scalar(
                conn,
                """SELECT COUNT(*) FROM dispute_cases c
                   LEFT JOIN case_events e ON e.case_id=c.case_id
                   WHERE e.event_id IS NULL""",
            ),
            "selected_candidates_without_intent": _scalar(
                conn,
                """SELECT COUNT(*) FROM fact_signal_candidates c
                   LEFT JOIN trade_intents i ON i.candidate_id=c.candidate_id
                   WHERE c.policy_selected=1 AND i.intent_id IS NULL""",
            ),
            "selected_candidates_with_unknown_book_snapshot": _scalar(
                conn,
                """SELECT COUNT(*) FROM fact_signal_candidates c
                   LEFT JOIN market_book_snapshots b
                     ON b.book_snapshot_id=c.execution_book_snapshot_id
                   WHERE c.policy_selected=1
                     AND c.execution_book_snapshot_id IS NOT NULL
                     AND c.execution_book_snapshot_id<>''
                     AND b.book_snapshot_id IS NULL""",
            ),
            "orphan_trade_intents": _scalar(
                conn,
                """SELECT COUNT(*) FROM trade_intents i
                   LEFT JOIN fact_signal_candidates c ON c.candidate_id=i.candidate_id
                   WHERE c.candidate_id IS NULL""",
            ),
            "unsafe_live_intents": _scalar(
                conn, "SELECT COUNT(*) FROM trade_intents WHERE mode='live'"
            ),
            "invalid_zero_notional_size": _scalar(
                conn,
                "SELECT COUNT(*) FROM trade_intents WHERE mode='zero_notional' AND requested_size<>0",
            ),
            "intents_without_paper_fill": _scalar(
                conn,
                """SELECT COUNT(*) FROM trade_intents i
                   LEFT JOIN paper_fills f ON f.intent_id=i.intent_id
                   WHERE i.mode='zero_notional' AND f.fill_id IS NULL""",
            ),
            "orphan_paper_orders": _scalar(
                conn,
                """SELECT COUNT(*) FROM paper_orders o
                   LEFT JOIN paper_plans p ON p.plan_id=o.plan_id
                   WHERE p.plan_id IS NULL""",
            ),
            "orphan_paper_plans": _scalar(
                conn,
                """SELECT COUNT(*) FROM paper_plans p
                   LEFT JOIN trade_intents i ON i.intent_id=p.intent_id
                   WHERE i.intent_id IS NULL""",
            ),
            "orphan_paper_fills": _scalar(
                conn,
                """SELECT COUNT(*) FROM paper_fills f
                   LEFT JOIN paper_orders o ON o.order_id=f.order_id
                   WHERE o.order_id IS NULL""",
            ),
            "paper_lineage_identity_mismatch": _scalar(
                conn,
                """SELECT COUNT(*) FROM paper_fills f
                   JOIN paper_orders o ON o.order_id=f.order_id
                   JOIN paper_plans p ON p.plan_id=f.plan_id
                   WHERE f.intent_id<>o.intent_id OR f.intent_id<>p.intent_id
                      OR o.plan_id<>p.plan_id OR f.token_id<>p.token_id""",
            ),
            "position_paper_fill_reference_missing": _scalar(
                conn,
                """SELECT COUNT(*) FROM shadow_positions p
                   LEFT JOIN paper_fills f
                     ON f.fill_id=json_extract(p.metadata_json,'$.paper_fill_id')
                   WHERE json_extract(p.metadata_json,'$.paper_fill_id') IS NOT NULL
                     AND f.fill_id IS NULL""",
            ),
            "duplicate_paper_fills_per_intent": _scalar(
                conn,
                """SELECT COUNT(*) FROM (
                     SELECT intent_id FROM paper_fills GROUP BY intent_id HAVING COUNT(*)>1
                   )""",
            ),
            "unsafe_paper_cashflow": _scalar(
                conn,
                """SELECT (SELECT COUNT(*) FROM paper_orders WHERE actual_notional<>0)
                         + (SELECT COUNT(*) FROM paper_fills WHERE actual_shares<>0 OR actual_cost<>0)""",
            ),
            "orphan_capture_receipts": _scalar(
                conn,
                """SELECT COUNT(*) FROM capture_receipts r
                   LEFT JOIN capture_demands d ON d.demand_id=r.demand_id
                   WHERE d.demand_id IS NULL""",
            ),
            "orphan_rest_checkpoint_receipts": _scalar(
                conn,
                """SELECT COUNT(*) FROM rest_checkpoint_receipts r
                   LEFT JOIN capture_demands d ON d.demand_id=r.demand_id
                   WHERE d.demand_id IS NULL""",
            ),
            "rest_checkpoint_book_missing": _scalar(
                conn,
                """SELECT COUNT(*) FROM rest_checkpoint_receipts r
                   LEFT JOIN market_book_snapshots b ON b.book_snapshot_id=r.book_snapshot_id
                   WHERE b.book_snapshot_id IS NULL""",
            ),
            "orphan_shadow_positions": _scalar(
                conn,
                """SELECT COUNT(*) FROM shadow_positions p
                   LEFT JOIN fact_signal_candidates c ON c.candidate_id=p.candidate_id
                   WHERE c.candidate_id IS NULL""",
            ),
            "orphan_shadow_markouts": _scalar(
                conn,
                """SELECT COUNT(*) FROM shadow_markouts m
                   LEFT JOIN shadow_positions p ON p.position_id=m.position_id
                   WHERE p.position_id IS NULL""",
            ),
            "complete_group_expressions_without_book": _scalar(
                conn,
                """SELECT COUNT(*) FROM market_group_expressions e
                   JOIN market_group_snapshots g ON g.group_snapshot_id=e.group_snapshot_id
                   WHERE g.batch_complete=1 AND (e.book_snapshot_id IS NULL OR e.book_snapshot_id='')""",
            ),
        }
        for name, value in checks.items():
            if value:
                errors.append(f"{name}:{value}")
        owner_started_at_utc = conn.execute(
            "SELECT MIN(accepted_at_utc) FROM capture_receipts"
        ).fetchone()[0]
        expired_missing_base = """FROM capture_demands d
            LEFT JOIN capture_receipts r ON r.demand_id=d.demand_id
              AND r.resolution_status='resolved_direct_token'
            WHERE r.receipt_id IS NULL
              AND d.expires_at_utc < strftime('%Y-%m-%dT%H:%M:%fZ','now')"""
        pre_owner_missing = int(
            conn.execute(
                "SELECT COUNT(*) " + expired_missing_base
                + " AND (? IS NULL OR d.requested_at_utc < ?)",
                (owner_started_at_utc, owner_started_at_utc),
            ).fetchone()[0]
        )
        operational_missing = int(
            conn.execute(
                "SELECT COUNT(*) " + expired_missing_base
                + " AND ? IS NOT NULL AND d.requested_at_utc >= ?",
                (owner_started_at_utc, owner_started_at_utc),
            ).fetchone()[0]
        )
        checks["expired_capture_demands_after_owner_without_successful_receipt"] = (
            operational_missing
        )
        metrics["capture_owner_started_at_utc"] = owner_started_at_utc
        metrics["pre_owner_expired_capture_gaps"] = pre_owner_missing
        metrics["operational_expired_capture_gaps"] = operational_missing
        if pre_owner_missing:
            warnings.append(
                f"pre_owner_capture_demands_without_receipt:{pre_owner_missing}"
            )
        if operational_missing:
            errors.append(
                "expired_capture_demands_after_owner_without_successful_receipt:"
                f"{operational_missing}"
            )
        pending_capture_receipts = _scalar(
            conn,
            """SELECT COUNT(*) FROM capture_demands d
               LEFT JOIN capture_receipts r ON r.demand_id=d.demand_id
                 AND r.resolution_status='resolved_direct_token'
               WHERE r.receipt_id IS NULL
                 AND d.expires_at_utc >= strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
        )
        metrics["pending_capture_receipts"] = pending_capture_receipts
        if pending_capture_receipts:
            warnings.append(f"capture_demands_awaiting_receipt:{pending_capture_receipts}")
        rejected_capture_demands = _scalar(
            conn,
            """SELECT COUNT(DISTINCT demand_id) FROM capture_receipts
               WHERE resolution_status<>'resolved_direct_token'""",
        )
        metrics["rejected_capture_demands"] = rejected_capture_demands
        if rejected_capture_demands:
            warnings.append(f"capture_demands_rejected_by_owner:{rejected_capture_demands}")
        late_rest_checkpoints = _scalar(
            conn,
            "SELECT COUNT(*) FROM rest_checkpoint_receipts WHERE capture_status='late'",
        )
        metrics["late_rest_checkpoints"] = late_rest_checkpoints
        if late_rest_checkpoints:
            warnings.append(f"late_rest_checkpoints:{late_rest_checkpoints}")
        legacy_paper_price_lineage = _scalar(
            conn, "SELECT COUNT(*) FROM paper_fills WHERE book_snapshot_id IS NULL"
        )
        metrics["legacy_paper_price_lineage"] = legacy_paper_price_lineage
        if legacy_paper_price_lineage:
            warnings.append(
                f"legacy_paper_fills_without_book_snapshot:{legacy_paper_price_lineage}"
            )
        positions_without_paper_fill = _scalar(
            conn,
            """SELECT COUNT(*) FROM shadow_positions
               WHERE json_extract(metadata_json,'$.paper_fill_id') IS NULL""",
        )
        metrics["legacy_positions_without_paper_fill"] = positions_without_paper_fill
        if positions_without_paper_fill:
            warnings.append(
                f"legacy_positions_without_paper_fill:{positions_without_paper_fill}"
            )
        if metrics["book_snapshots"] == 0 or metrics["market_group_snapshots"] == 0:
            warnings.append("shared_book_group_forward_coverage_not_started")
        if metrics["capture_demands"] == 0:
            warnings.append("capture_demand_forward_coverage_not_started")
        return {
            "schema_version": "dispute_canonical_health_v1",
            "status": "error" if errors else "healthy_with_warnings" if warnings else "healthy",
            "db_path": str(db_path),
            "sqlite_quick_check": quick_check,
            "metrics": metrics,
            "lineage_checks": checks,
            "sources": sources,
            "errors": errors,
            "warnings": warnings,
        }
    finally:
        conn.close()
