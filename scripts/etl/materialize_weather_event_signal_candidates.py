#!/usr/bin/env python3
"""Materialize additive event-checkpoint candidates without execution effects."""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.etl.build_weather_signal_candidates import CANDIDATE_DDL
from weather_dashboard.db.first_seen_schema import apply_first_seen_schema
from weather_data_feed.information_events import canonical_json_hash


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid candidate JSONL at {path}:{line_number}") from exc
            if isinstance(row, dict):
                yield row


def _candidate_id(raw: Mapping[str, Any], checkpoint: Mapping[str, Any]) -> str:
    return canonical_json_hash(
        {
            "candidate_grain_version": "v2_event_checkpoint",
            "strategy_key": raw["strategy_key"],
            "model_artifact_id": raw["model_artifact_id"],
            "condition_id": raw.get("condition_id"),
            "bracket": raw.get("bracket"),
            "expression_side": raw.get("side"),
            "trigger_event_id": checkpoint["trigger_event_id"],
            "state_checkpoint_id": checkpoint["state_checkpoint_id"],
        }
    )


def candidate_row(
    raw: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
) -> dict[str, Any]:
    blocker = raw.get("candidate_blocker")
    status = str(raw.get("candidate_status") or ("blocked" if blocker else "observed"))
    model_after = raw.get("model_probability_after")
    market_probability = raw.get("market_probability")
    residual = raw.get("probability_residual")
    if residual is None and model_after is not None and market_probability is not None:
        residual = float(model_after) - float(market_probability)
    row = {
        **dict(raw),
        "candidate_id": _candidate_id(raw, checkpoint),
        "candidate_grain_version": "v2_event_checkpoint",
        "trigger_event_id": checkpoint["trigger_event_id"],
        "state_checkpoint_id": checkpoint["state_checkpoint_id"],
        "feature_store_frame_id": checkpoint["feature_store_frame_id"],
        "feature_row_id": checkpoint["feature_row_id"],
        "decision_ts_utc": raw.get("decision_ts_utc") or checkpoint["as_of_ts_utc"],
        "event_date": checkpoint["target_date"],
        "city": checkpoint["city"],
        "probability_residual": residual,
        "candidate_status": status,
        "policy_selected": int(bool(raw.get("policy_selected"))),
        "first_city_day_selected": int(bool(raw.get("first_city_day_selected"))),
        "seen": 1,
        "eligible": int(status in {"scored", "selected"}),
        "paper_ordered": 0,
        "live_filled": 0,
        "fact_built_at_utc": _utc_now(),
    }
    return row


def materialize_candidate_rows(
    conn: sqlite3.Connection,
    rows: Iterable[Mapping[str, Any]],
    *,
    batch_size: int | None = None,
    initialize_schema: bool = True,
) -> dict[str, int]:
    if initialize_schema:
        conn.execute(CANDIDATE_DDL)
        apply_first_seen_schema(conn)
    table_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(fact_signal_candidates)")
    }
    inserted = 0
    duplicates = 0
    blocked = 0
    pending = 0
    for raw in rows:
        checkpoint = conn.execute(
            """
            SELECT *
            FROM weather_state_checkpoints
            WHERE state_checkpoint_id = ?
            """,
            (raw["state_checkpoint_id"],),
        ).fetchone()
        if checkpoint is None:
            raise ValueError(f"unknown state_checkpoint_id: {raw['state_checkpoint_id']}")
        checkpoint_row = dict(checkpoint)
        row = candidate_row(raw, checkpoint_row)
        row = {key: value for key, value in row.items() if key in table_columns}
        columns = list(row)
        cursor = conn.execute(
            f"""
            INSERT OR IGNORE INTO fact_signal_candidates ({", ".join(columns)})
            VALUES ({", ".join("?" for _ in columns)})
            """,
            [row[column] for column in columns],
        )
        if cursor.rowcount:
            inserted += 1
        else:
            duplicates += 1
        blocked += int(row.get("candidate_status") == "blocked")
        pending += 1
        if batch_size is not None and pending >= max(1, int(batch_size)):
            conn.commit()
            pending = 0
    conn.commit()
    return {
        "inserted": inserted,
        "duplicates": duplicates,
        "blocked": blocked,
    }


def attach_settlements(conn: sqlite3.Connection) -> int:
    updated = 0
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settlement_outcomes'"
    ).fetchone() is not None:
        cursor = conn.execute(
            """
            UPDATE fact_signal_candidates AS candidate
            SET
              settlement_status = (
                SELECT outcome.settlement_status
                FROM settlement_outcomes AS outcome
                WHERE outcome.condition_id = candidate.condition_id
                ORDER BY outcome.created_at_utc DESC
                LIMIT 1
              ),
              final_yes = (
                SELECT CASE
                  WHEN outcome.final_price >= 0.99 THEN 1.0
                  WHEN outcome.final_price <= 0.01 THEN 0.0
                  ELSE NULL
                END
                FROM settlement_outcomes AS outcome
                WHERE outcome.condition_id = candidate.condition_id
                ORDER BY outcome.created_at_utc DESC
                LIMIT 1
              )
            WHERE candidate.candidate_grain_version = 'v2_event_checkpoint'
              AND EXISTS (
                SELECT 1
                FROM settlement_outcomes AS outcome
                WHERE outcome.condition_id = candidate.condition_id
              )
            """
        )
        updated += int(cursor.rowcount or 0)
    cursor = conn.execute(
        """
        UPDATE fact_signal_candidates AS candidate
        SET
          settlement_status = 'resolved_via_v1_candidate_fact',
          final_yes = (
            SELECT MAX(legacy.final_yes)
            FROM fact_signal_candidates AS legacy
            WHERE legacy.condition_id = candidate.condition_id
              AND legacy.candidate_grain_version = 'v1_legacy_daily'
              AND legacy.final_yes IS NOT NULL
          )
        WHERE candidate.candidate_grain_version = 'v2_event_checkpoint'
          AND candidate.final_yes IS NULL
          AND EXISTS (
            SELECT 1
            FROM fact_signal_candidates AS legacy
            WHERE legacy.condition_id = candidate.condition_id
              AND legacy.candidate_grain_version = 'v1_legacy_daily'
              AND legacy.final_yes IS NOT NULL
          )
        """
    )
    updated += int(cursor.rowcount or 0)
    conn.commit()
    return updated


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(ROOT / "runtime/weather.db"))
    parser.add_argument("--inputs", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        result = materialize_candidate_rows(conn, _rows(Path(args.inputs)))
        result["settlements_attached"] = attach_settlements(conn)
    finally:
        conn.close()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
