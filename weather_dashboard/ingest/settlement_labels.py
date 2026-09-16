"""Append-only, availability-aware settlement labels for replay datasets."""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from typing import Any

from weather_data_feed.information_events import canonical_json_hash
from weather_model_evaluation.contracts import parse_utc


def _namespace(question: str | None) -> tuple[str, str]:
    normalized = str(question or "").lower()
    if any(marker in normalized for marker in ("lowest", "minimum", "min temperature")):
        return "tmin", "explicit_question"
    if any(marker in normalized for marker in ("highest", "maximum", "max temperature")):
        return "tmax", "explicit_question"
    return "unknown", "unknown"


def _direct_rows(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT c.candidate_id, c.condition_id, c.city, c.event_date AS target_date,
               CASE WHEN o.final_price >= 0.99 THEN 1.0
                    WHEN o.final_price <= 0.01 THEN 0.0 END AS source_settlement_yes,
               o.settlement_outcome_id AS source_id,
               o.source_payload_hash, o.available_at_utc, o.first_seen_at_utc,
               o.created_at_utc AS outcome_created_at_utc, o.question
        FROM fact_signal_candidates AS c
        JOIN settlement_outcomes AS o
          ON o.settlement_outcome_id = (
            SELECT latest.settlement_outcome_id
            FROM settlement_outcomes AS latest
            WHERE latest.condition_id = c.condition_id
              AND latest.settlement_status = 'settled'
            ORDER BY COALESCE(latest.available_at_utc, latest.first_seen_at_utc,
                              latest.created_at_utc) DESC,
                     latest.settlement_outcome_id DESC
            LIMIT 1
          )
        WHERE c.candidate_grain_version = 'v2_event_checkpoint'
          AND o.final_price IS NOT NULL
          AND (o.final_price >= 0.99 OR o.final_price <= 0.01)
          AND NOT EXISTS (
            SELECT 1 FROM weather_candidate_settlement_labels AS label
            WHERE label.candidate_id = c.candidate_id
              AND label.settlement_source_kind = 'settlement_outcome'
              AND label.settlement_source_id = o.settlement_outcome_id
          )
        ORDER BY c.event_date, c.candidate_id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def _legacy_rows(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT c.candidate_id, c.condition_id, c.city, c.event_date AS target_date,
               c.final_yes, legacy.candidate_id AS source_id,
               legacy.fact_built_at_utc, legacy.bracket,
               legacy.final_yes AS legacy_settlement_yes
        FROM fact_signal_candidates AS c
        JOIN fact_signal_candidates AS legacy
          ON legacy.candidate_id = (
            SELECT source.candidate_id
            FROM fact_signal_candidates AS source
            WHERE source.condition_id = c.condition_id
              AND source.candidate_grain_version = 'v1_legacy_daily'
              AND source.final_yes IS NOT NULL
            ORDER BY source.fact_built_at_utc DESC, source.candidate_id DESC
            LIMIT 1
          )
        WHERE c.candidate_grain_version = 'v2_event_checkpoint'
          AND c.final_yes IN (0.0, 1.0)
          AND NOT EXISTS (
            SELECT 1 FROM settlement_outcomes AS direct
            WHERE direct.condition_id = c.condition_id
              AND direct.settlement_status = 'settled'
          )
          AND NOT EXISTS (
            SELECT 1 FROM weather_candidate_settlement_labels AS label
            WHERE label.candidate_id = c.candidate_id
              AND label.settlement_source_kind = 'legacy_candidate_fact'
              AND label.settlement_source_id = legacy.candidate_id
          )
        ORDER BY c.event_date, c.candidate_id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def _validated_available(*values: Any) -> str | None:
    for value in values:
        if value not in (None, ""):
            parse_utc(str(value))
            return str(value)
    return None


def materialize_candidate_settlement_label_facts(
    conn: sqlite3.Connection,
    *,
    max_rows: int = 5_000,
    commit: bool = True,
) -> dict[str, int]:
    if max_rows < 0:
        raise ValueError("max_rows must be non-negative")
    if max_rows == 0:
        return {"inserted": 0, "direct": 0, "legacy": 0}
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        direct = _direct_rows(conn, max_rows)
        legacy = _legacy_rows(conn, max(0, max_rows - len(direct)))
        inserted = 0
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for row in direct:
            namespace, namespace_status = _namespace(row["question"])
            available = _validated_available(
                row["available_at_utc"],
                row["first_seen_at_utc"],
                row["outcome_created_at_utc"],
            )
            payload_hash = str(row["source_payload_hash"] or "") or canonical_json_hash(
                {
                    "settlement_outcome_id": row["source_id"],
                    "settlement_yes": row["source_settlement_yes"],
                }
            )
            values = {
                "candidate_id": row["candidate_id"],
                "condition_id": row["condition_id"],
                "city": row["city"],
                "target_date": row["target_date"],
                "measurement_namespace": namespace,
                "namespace_status": namespace_status,
                "settlement_yes": float(row["source_settlement_yes"]),
                "settlement_source_kind": "settlement_outcome",
                "settlement_source_id": row["source_id"],
                "source_payload_hash": payload_hash,
                "source_available_at_utc": available,
                "label_available_at_utc": available,
                "label_availability_status": "known" if available else "unknown",
                "created_at_utc": now,
            }
            values["label_fact_id"] = canonical_json_hash(
                {
                    "candidate_id": values["candidate_id"],
                    "source_kind": values["settlement_source_kind"],
                    "source_id": values["settlement_source_id"],
                    "source_payload_hash": values["source_payload_hash"],
                }
            )
            columns = list(values)
            conn.execute(
                f"INSERT INTO weather_candidate_settlement_labels "
                f"({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                [values[column] for column in columns],
            )
            inserted += 1
        for row in legacy:
            available = _validated_available(row["fact_built_at_utc"])
            payload_hash = canonical_json_hash(
                {
                    "legacy_candidate_id": row["source_id"],
                    "bracket": row["bracket"],
                    "settlement_yes": row["legacy_settlement_yes"],
                    "fact_built_at_utc": available,
                }
            )
            values = {
                "candidate_id": row["candidate_id"],
                "condition_id": row["condition_id"],
                "city": row["city"],
                "target_date": row["target_date"],
                "measurement_namespace": "unknown",
                "namespace_status": "unknown",
                "settlement_yes": float(row["final_yes"]),
                "settlement_source_kind": "legacy_candidate_fact",
                "settlement_source_id": row["source_id"],
                "source_payload_hash": payload_hash,
                "source_available_at_utc": available,
                "label_available_at_utc": available,
                "label_availability_status": "known" if available else "unknown",
                "created_at_utc": now,
            }
            values["label_fact_id"] = canonical_json_hash(
                {
                    "candidate_id": values["candidate_id"],
                    "source_kind": values["settlement_source_kind"],
                    "source_id": values["settlement_source_id"],
                    "source_payload_hash": values["source_payload_hash"],
                }
            )
            columns = list(values)
            conn.execute(
                f"INSERT INTO weather_candidate_settlement_labels "
                f"({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                [values[column] for column in columns],
            )
            inserted += 1
        if commit:
            conn.commit()
        return {"inserted": inserted, "direct": len(direct), "legacy": len(legacy)}
    finally:
        conn.row_factory = previous_factory


def labels_as_of(
    conn: sqlite3.Connection, *, cutoff_utc: str
) -> list[dict[str, Any]]:
    """Select one known-availability label per candidate without future leakage."""

    parse_utc(cutoff_utc)
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT label.*
            FROM weather_candidate_settlement_labels AS label
            WHERE label.label_availability_status = 'known'
              AND datetime(label.label_available_at_utc) <= datetime(?)
              AND label.label_fact_id = (
                SELECT candidate_label.label_fact_id
                FROM weather_candidate_settlement_labels AS candidate_label
                WHERE candidate_label.candidate_id = label.candidate_id
                  AND candidate_label.label_availability_status = 'known'
                  AND datetime(candidate_label.label_available_at_utc) <= datetime(?)
                ORDER BY CASE candidate_label.settlement_source_kind
                           WHEN 'settlement_outcome' THEN 0 ELSE 1 END,
                         datetime(candidate_label.label_available_at_utc) DESC,
                         candidate_label.label_fact_id DESC
                LIMIT 1
              )
            ORDER BY label.candidate_id
            """,
            (cutoff_utc, cutoff_utc),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.row_factory = previous_factory
