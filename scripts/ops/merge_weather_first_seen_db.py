#!/usr/bin/env python3
"""Merge the split first-seen lineage tables into a canonical weather DB."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any


TABLES = (
    ("weather_information_events", "information_event_id"),
    ("weather_observation_events", "observation_id"),
    ("fact_forecast_hourly_curves", "curve_id"),
    ("weather_state_checkpoints", "state_checkpoint_id"),
    ("fact_signal_candidates", "candidate_id"),
)
SOURCE_FILTERS = {
    "fact_signal_candidates": (
        "candidate_grain_version",
        "candidate_grain_version = 'v2_event_checkpoint'",
    ),
}


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def table_columns(
    conn: sqlite3.Connection,
    schema: str,
    table: str,
) -> list[str]:
    return [
        str(row[1])
        for row in conn.execute(
            f"PRAGMA {quote_identifier(schema)}.table_info({quote_identifier(table)})"
        )
    ]


def merge_tables(
    target: Path,
    source: Path,
    *,
    apply: bool,
) -> dict[str, Any]:
    target = target.resolve(strict=True)
    source = source.resolve(strict=True)
    if target.samefile(source):
        raise ValueError("source and target resolve to the same file")

    conn = sqlite3.connect(
        target.as_uri() + "?mode=rw",
        uri=True,
        timeout=60.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "ATTACH DATABASE ? AS source_db",
        (source.as_uri() + "?mode=ro",),
    )
    report: dict[str, Any] = {
        "target": str(target),
        "source": str(source),
        "applied": apply,
        "tables": [],
    }
    try:
        if apply:
            conn.execute("PRAGMA synchronous=OFF")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA cache_size=-524288")
            conn.execute("PRAGMA wal_autocheckpoint=0")
            conn.execute("BEGIN IMMEDIATE")
        for table, primary_key in TABLES:
            target_columns = table_columns(conn, "main", table)
            source_columns = table_columns(conn, "source_db", table)
            if not target_columns or not source_columns:
                raise RuntimeError(f"required split-DB table missing: {table}")
            if primary_key not in target_columns or primary_key not in source_columns:
                raise RuntimeError(f"required primary key missing: {table}.{primary_key}")
            columns = [column for column in target_columns if column in source_columns]
            filter_column, configured_filter = SOURCE_FILTERS.get(table, ("", ""))
            source_filter = (
                f" WHERE {configured_filter}"
                if filter_column and filter_column in source_columns
                else ""
            )
            quoted_columns = ", ".join(quote_identifier(column) for column in columns)
            quoted_table = quote_identifier(table)
            select_columns = [f"source_db.{quoted_table}.{quote_identifier(column)}" for column in columns]
            normalized_orphan_revisions = 0
            if table == "weather_information_events" and "revision_of_event_id" in columns:
                revision_index = columns.index("revision_of_event_id")
                revision_column = quote_identifier("revision_of_event_id")
                id_column = quote_identifier("information_event_id")
                orphan_predicate = f"""
                    source_db.{quoted_table}.{revision_column} IS NOT NULL
                    AND NOT EXISTS (
                      SELECT 1 FROM main.{quoted_table} AS target_parent
                      WHERE target_parent.{id_column} =
                        source_db.{quoted_table}.{revision_column}
                    )
                    AND NOT EXISTS (
                      SELECT 1 FROM source_db.{quoted_table} AS source_parent
                      WHERE source_parent.{id_column} =
                        source_db.{quoted_table}.{revision_column}
                    )
                """
                normalized_orphan_revisions = int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM source_db.{quoted_table} WHERE {orphan_predicate}"
                    ).fetchone()[0]
                )
                select_columns[revision_index] = (
                    f"CASE WHEN {orphan_predicate} THEN NULL "
                    f"ELSE source_db.{quoted_table}.{revision_column} END"
                )
            changes_before = conn.total_changes
            if apply:
                conn.execute(
                    f"""
                    INSERT OR IGNORE INTO main.{quoted_table} ({quoted_columns})
                    SELECT {", ".join(select_columns)}
                    FROM source_db.{quoted_table}{source_filter}
                    """
                )
            inserted = conn.total_changes - changes_before
            report["tables"].append(
                {
                    "table": table,
                    "primary_key": primary_key,
                    "inserted": inserted,
                    "source_filter": source_filter.strip() or None,
                    "merged_columns": columns,
                    "normalized_orphan_revisions": normalized_orphan_revisions,
                }
            )
        if apply:
            conn.commit()
            violations: list[list[Any]] = []
            for table, _ in TABLES:
                violations.extend(
                    list(row)
                    for row in conn.execute(
                        f"PRAGMA foreign_key_check({quote_identifier(table)})"
                    )
                )
            report["foreign_key_violations"] = violations
            if report["foreign_key_violations"]:
                raise RuntimeError("foreign-key violations remain after first-seen merge")
            conn.execute("PRAGMA synchronous=FULL")
            report["wal_checkpoint"] = list(
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            )
            if report["wal_checkpoint"][0] != 0:
                raise RuntimeError(
                    f"staging WAL checkpoint failed: {report['wal_checkpoint']}"
                )
        else:
            conn.rollback()
            report["foreign_key_violations"] = []
            report["wal_checkpoint"] = None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return report


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    report = merge_tables(args.target, args.source, apply=args.apply)
    if args.json_out:
        write_json_atomic(args.json_out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
