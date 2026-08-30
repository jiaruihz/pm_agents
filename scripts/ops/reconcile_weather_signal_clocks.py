#!/usr/bin/env python3
"""Reconcile causal signal clocks through append-only canonical adjustments."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_clock_contract import parse_utc, utc_text  # noqa: E402
from weather_dashboard.ingest.signal_clock_adjustments import (  # noqa: E402
    DEFAULT_SIGNAL_CLOCK_ADJUSTMENT_PATH,
    append_signal_clock_adjustment,
    import_signal_clock_adjustments,
)
from weather_dashboard.legacy_migration.strategy_runtime_orders import (  # noqa: E402
    _build_snapshot_lookup,
    _snapshot_lookup_key,
)


SCHEMA_VERSION = "weather_signal_clock_reconciliation_v1"


def _db_identity(path: Path) -> dict[str, Any]:
    stat = os.stat(path)
    return {
        "path": str(path),
        "realpath": str(path.resolve()),
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size_bytes": stat.st_size,
    }


def _assert_same_database(path: Path, expected: Path | None) -> None:
    if expected is None:
        return
    actual_stat = os.stat(path)
    expected_stat = os.stat(expected)
    if (actual_stat.st_dev, actual_stat.st_ino) != (
        expected_stat.st_dev,
        expected_stat.st_ino,
    ):
        raise RuntimeError(
            "DB identity mismatch: "
            f"{path.resolve()} != {expected.resolve()}"
        )


def _json_object(value: Any, *, field: str) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {field} JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{field} must be a JSON object")
    return payload


def _discover_issues(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return one issue per immutable signal that contaminated a fact row."""

    conn.row_factory = sqlite3.Row
    has_adjustments = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='signal_clock_adjustments'"
    ).fetchone() is not None
    adjustment_join = (
        "LEFT JOIN signal_clock_adjustments adjustment "
        "ON adjustment.signal_id=ft.signal_id"
        if has_adjustments
        else ""
    )
    adjustment_filter = "AND adjustment.signal_id IS NULL" if has_adjustments else ""
    rows = conn.execute(
        f"""
        SELECT
          ft.fill_id,
          ft.execution_id,
          ft.signal_id,
          ft.strategy_key,
          ft.city,
          ft.target_date,
          ft.bracket,
          ft.token_id,
          ft.order_ts_utc,
          ft.cost_usd,
          ft.pnl_usd_at_fill,
          s.snapshot_ts_utc AS original_snapshot_ts_utc,
          s.model_p_yes,
          s.condition_id,
          s.market_id
        FROM fact_trades ft
        JOIN signals s ON s.signal_id=ft.signal_id
        {adjustment_join}
        WHERE julianday(s.snapshot_ts_utc) > julianday(ft.order_ts_utc)
          {adjustment_filter}
        ORDER BY ft.signal_id, ft.order_ts_utc, ft.fill_id
        """
    ).fetchall()
    by_signal: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_signal.setdefault(str(row["signal_id"]), []).append(row)

    issues: list[dict[str, Any]] = []
    for signal_id, fact_rows in sorted(by_signal.items()):
        signal = fact_rows[0]
        order = conn.execute(
            """
            SELECT o.execution_id, o.order_payload,
                   COALESCE(o.placed_at_utc, o.created_at_utc) AS order_ts_utc
            FROM plans p
            JOIN orders o ON o.plan_id=p.plan_id
            WHERE p.signal_id=?
            ORDER BY julianday(COALESCE(o.placed_at_utc, o.created_at_utc)),
                     o.execution_id
            LIMIT 1
            """,
            (signal_id,),
        ).fetchone()
        if order is None:
            raise RuntimeError(f"signal has fact rows but no order: {signal_id}")
        earliest_order = parse_utc(order["order_ts_utc"], field="order_ts_utc")
        original = parse_utc(
            signal["original_snapshot_ts_utc"], field="original_snapshot_ts_utc"
        )
        if original <= earliest_order:
            # A later lifecycle fill may be the actual bad row; retain the
            # earliest contaminated fact clock as the causal cutoff.
            earliest_order = min(
                parse_utc(row["order_ts_utc"], field="order_ts_utc")
                for row in fact_rows
            )
        issues.append(
            {
                "signal_id": signal_id,
                "strategy_key": signal["strategy_key"],
                "city": signal["city"],
                "target_date": signal["target_date"],
                "bracket": signal["bracket"],
                "token_id": signal["token_id"],
                "model_p_yes": signal["model_p_yes"],
                "condition_id": signal["condition_id"],
                "market_id": signal["market_id"],
                "original_snapshot_ts_utc": utc_text(original, timespec="auto"),
                "earliest_order_ts_utc": utc_text(earliest_order, timespec="auto"),
                "representative_execution_id": str(order["execution_id"]),
                "order_payload": _json_object(
                    order["order_payload"], field="order_payload"
                ),
                "fill_ids": [str(row["fill_id"]) for row in fact_rows],
                "execution_ids": sorted(
                    {str(row["execution_id"]) for row in fact_rows}
                ),
                "fact_rows": len(fact_rows),
                "cost_usd": sum(float(row["cost_usd"] or 0.0) for row in fact_rows),
                "settled_pnl_usd": sum(
                    float(row["pnl_usd_at_fill"] or 0.0)
                    for row in fact_rows
                    if row["pnl_usd_at_fill"] is not None
                ),
            }
        )
    return issues


def _source_record_hash(record: dict[str, Any]) -> str:
    payload = {key: value for key, value in record.items() if not key.startswith("_")}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _build_adjustment(
    issue: dict[str, Any], *, created_at_utc: datetime
) -> dict[str, Any]:
    raw = dict(issue["order_payload"])
    raw.update(
        {
            "signal_id": issue["signal_id"],
            "execution_id": issue["representative_execution_id"],
            "created_at_utc": issue["earliest_order_ts_utc"],
            "city": issue["city"],
            "target_date": issue["target_date"],
            "bracket": issue["bracket"],
            "token_id": issue["token_id"],
            "model_p_yes": issue["model_p_yes"],
            "condition_id": issue["condition_id"],
            "market_id": issue["market_id"],
        }
    )
    # The payload stored by the old migration contains the injected future
    # clock. Remove it before asking the causal snapshot resolver to replay.
    raw.pop("decision_snapshot_ts_utc", None)
    raw.pop("snapshot_ts_utc", None)
    lookup = _build_snapshot_lookup([raw], force=True)
    snapshot = lookup.get(_snapshot_lookup_key(raw))
    cutoff = parse_utc(
        issue["earliest_order_ts_utc"], field="earliest_order_ts_utc"
    )
    source_ref: str | None = None
    source_record_hash: str | None = None
    if snapshot is not None:
        snapshot_clock = parse_utc(
            snapshot.get("snapshot_ts_utc") or snapshot.get("ts_utc"),
            field="reconstructed_snapshot_ts_utc",
        )
        if snapshot_clock > cutoff:
            raise RuntimeError(
                f"snapshot resolver returned future evidence for {issue['signal_id']}"
            )
        corrected = snapshot_clock
        timestamp_source = "strategy_snapshot_record"
        evidence_class = "reconstructed"
        lineage_status = "reconstructed_causal"
        source_ref = str(snapshot.get("_snapshot_source_path") or "").strip() or None
        source_record_hash = _source_record_hash(snapshot)
        blocked_reason = None
    else:
        # No source snapshot proves the model probability used by this signal.
        # The order clock is an upper bound, not a feature snapshot; expose that
        # weaker claim explicitly rather than retaining a future timestamp.
        corrected = cutoff
        timestamp_source = "order_clock_placeholder"
        evidence_class = "proxy"
        lineage_status = "blocked_no_signal_snapshot"
        blocked_reason = "no_matching_causal_strategy_snapshot"

    identity = {
        "signal_id": issue["signal_id"],
        "original_snapshot_ts_utc": issue["original_snapshot_ts_utc"],
        "corrected_snapshot_ts_utc": utc_text(corrected, timespec="auto"),
        "timestamp_source": timestamp_source,
        "lineage_status": lineage_status,
        "source_snapshot_ref": source_ref,
    }
    adjustment_id = hashlib.sha256(
        (SCHEMA_VERSION + "|" + json.dumps(identity, sort_keys=True)).encode("utf-8")
    ).hexdigest()
    return {
        "adjustment_id": adjustment_id,
        "signal_id": issue["signal_id"],
        "corrected_snapshot_ts_utc": identity["corrected_snapshot_ts_utc"],
        "timestamp_source": timestamp_source,
        "timestamp_evidence_class": evidence_class,
        "lineage_status": lineage_status,
        "source_snapshot_ref": source_ref,
        "evidence": {
            "fault_type": "signal_snapshot_after_order",
            "original_snapshot_ts_utc": issue["original_snapshot_ts_utc"],
            "earliest_order_ts_utc": issue["earliest_order_ts_utc"],
            "source_record_hash": source_record_hash,
            "blocked_reason": blocked_reason,
            "strategy_key": issue["strategy_key"],
            "city": issue["city"],
            "target_date": issue["target_date"],
            "bracket": issue["bracket"],
            "token_id": issue["token_id"],
            "model_p_yes": issue["model_p_yes"],
            "fill_ids": issue["fill_ids"],
            "execution_ids": issue["execution_ids"],
        },
        "created_at_utc": utc_text(created_at_utc, timespec="auto"),
    }


def reconcile(
    conn: sqlite3.Connection,
    *,
    journal_path: Path,
    apply: bool,
    created_at_utc: datetime | None = None,
) -> dict[str, Any]:
    imported_before = import_signal_clock_adjustments(conn, journal_path) if apply else 0
    issues = _discover_issues(conn)
    now = created_at_utc or datetime.now(timezone.utc)
    adjustments = [
        _build_adjustment(issue, created_at_utc=now) for issue in issues
    ]
    appended = imported_after = 0
    if apply:
        for row in adjustments:
            appended += int(append_signal_clock_adjustment(row, journal_path))
        imported_after = import_signal_clock_adjustments(conn, journal_path)

    by_strategy = Counter(str(issue["strategy_key"] or "unknown") for issue in issues)
    by_status = Counter(row["lineage_status"] for row in adjustments)
    impact = {
        "signals": len(issues),
        "fact_rows": sum(int(issue["fact_rows"]) for issue in issues),
        "executions": len(
            {
                execution_id
                for issue in issues
                for execution_id in issue["execution_ids"]
            }
        ),
        "cost_usd": sum(float(issue["cost_usd"]) for issue in issues),
        "settled_pnl_usd": sum(
            float(issue["settled_pnl_usd"]) for issue in issues
        ),
        "target_date_start": min(
            (str(issue["target_date"]) for issue in issues), default=None
        ),
        "target_date_end": max(
            (str(issue["target_date"]) for issue in issues), default=None
        ),
        "by_strategy": dict(sorted(by_strategy.items())),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_text(now, timespec="auto"),
        "apply": apply,
        "journal_path": str(journal_path),
        "impact": impact,
        "resolution": {
            "by_lineage_status": dict(sorted(by_status.items())),
            "journal_rows_appended": appended,
            "db_rows_imported_before_scan": imported_before,
            "db_rows_imported_after_append": imported_after,
            "economic_fields_changed_by_clock_adjustment": False,
        },
        "adjustments": [
            {
                **row,
                "fact_rows": issue["fact_rows"],
                "cost_usd": issue["cost_usd"],
                "settled_pnl_usd": issue["settled_pnl_usd"],
            }
            for issue, row in zip(issues, adjustments)
        ],
        "status": "repaired" if apply and issues else ("clean" if not issues else "dry_run"),
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--expected-db")
    parser.add_argument(
        "--journal", default=str(DEFAULT_SIGNAL_CLOCK_ADJUSTMENT_PATH)
    )
    parser.add_argument("--report", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db_path).expanduser()
    expected = Path(args.expected_db).expanduser() if args.expected_db else None
    _assert_same_database(db_path, expected)
    if args.apply:
        conn = sqlite3.connect(db_path, timeout=30.0)
    else:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, timeout=5.0
        )
        conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        report = reconcile(
            conn,
            journal_path=Path(args.journal).expanduser(),
            apply=args.apply,
        )
    finally:
        conn.close()
    report["database"] = _db_identity(db_path)
    _write_json_atomic(Path(args.report).expanduser(), report)
    print(json.dumps(report["impact"], ensure_ascii=False, sort_keys=True))
    print(json.dumps(report["resolution"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
