"""Durable append-only corrections for immutable signal snapshot clocks."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from src.strategies.runtime.production import load_production_spec
from weather_clock_contract import parse_utc, utc_text


DEFAULT_SIGNAL_CLOCK_ADJUSTMENT_PATH = (
    load_production_spec().pm_runtime_root
    / "weather_edge_v1"
    / "signal_clock_adjustments.jsonl"
)

EVIDENCE_CLASSES = {"exact", "reconstructed", "proxy"}
LINEAGE_STATUSES = {
    "explicit_causal",
    "reconstructed_causal",
    "proxy_not_feature_snapshot",
    "blocked_no_signal_snapshot",
}


def _validated(row: dict[str, Any]) -> dict[str, Any]:
    required = (
        "adjustment_id",
        "signal_id",
        "corrected_snapshot_ts_utc",
        "timestamp_source",
        "timestamp_evidence_class",
        "lineage_status",
        "created_at_utc",
    )
    missing = [key for key in required if row.get(key) in (None, "")]
    if missing:
        raise ValueError("signal clock adjustment missing: " + ",".join(missing))
    evidence_class = str(row["timestamp_evidence_class"])
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(f"invalid timestamp_evidence_class: {evidence_class}")
    lineage_status = str(row["lineage_status"])
    if lineage_status not in LINEAGE_STATUSES:
        raise ValueError(f"invalid lineage_status: {lineage_status}")
    normalized = dict(row)
    normalized["corrected_snapshot_ts_utc"] = utc_text(
        parse_utc(
            row["corrected_snapshot_ts_utc"], field="corrected_snapshot_ts_utc"
        ),
        field="corrected_snapshot_ts_utc",
        timespec="auto",
    )
    normalized["created_at_utc"] = utc_text(
        parse_utc(row["created_at_utc"], field="created_at_utc"),
        field="created_at_utc",
        timespec="auto",
    )
    normalized["evidence"] = dict(row.get("evidence") or {})
    normalized["source_snapshot_ref"] = (
        str(row.get("source_snapshot_ref") or "").strip() or None
    )
    return normalized


def iter_signal_clock_adjustments(
    path: str | Path = DEFAULT_SIGNAL_CLOCK_ADJUSTMENT_PATH,
) -> Iterable[dict[str, Any]]:
    journal_path = Path(path)
    if not journal_path.exists():
        return
    with journal_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid signal clock journal JSON {journal_path}:{line_number}"
                ) from exc
            if not isinstance(payload, dict):
                raise ValueError(
                    f"signal clock journal row must be an object: {journal_path}:{line_number}"
                )
            yield _validated(payload)


def _identity_payload(row: dict[str, Any]) -> str:
    normalized = _validated(row)
    return json.dumps(normalized, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def append_signal_clock_adjustment(
    row: dict[str, Any],
    path: str | Path = DEFAULT_SIGNAL_CLOCK_ADJUSTMENT_PATH,
) -> bool:
    """Append a correction once; reject conflicting ids or signal ownership."""

    normalized = _validated(row)
    wanted = _identity_payload(normalized)
    for existing in iter_signal_clock_adjustments(path):
        same_id = existing["adjustment_id"] == normalized["adjustment_id"]
        same_signal = existing["signal_id"] == normalized["signal_id"]
        if same_id or same_signal:
            if _identity_payload(existing) == wanted:
                return False
            collision = "adjustment_id" if same_id else "signal_id"
            raise ValueError(f"conflicting signal clock adjustment {collision}")
    journal_path = Path(path)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with journal_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(normalized, ensure_ascii=True, sort_keys=True) + "\n")
    return True


def import_signal_clock_adjustments(
    conn: sqlite3.Connection,
    path: str | Path = DEFAULT_SIGNAL_CLOCK_ADJUSTMENT_PATH,
) -> int:
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "signal_clock_adjustments" not in tables:
        return 0
    inserted = 0
    for row in iter_signal_clock_adjustments(path):
        existing = conn.execute(
            "SELECT adjustment_id, signal_id, corrected_snapshot_ts_utc, "
            "timestamp_source, timestamp_evidence_class, lineage_status, "
            "source_snapshot_ref, evidence_json "
            "FROM signal_clock_adjustments "
            "WHERE adjustment_id=? OR signal_id=?",
            (row["adjustment_id"], row["signal_id"]),
        ).fetchone()
        values = (
            row["adjustment_id"],
            row["signal_id"],
            row["corrected_snapshot_ts_utc"],
            row["timestamp_source"],
            row["timestamp_evidence_class"],
            row["lineage_status"],
            row["source_snapshot_ref"],
            json.dumps(row["evidence"], ensure_ascii=True, sort_keys=True),
        )
        if existing is not None:
            if tuple(existing) != values:
                raise ValueError(
                    f"conflicting DB signal clock adjustment for {row['signal_id']}"
                )
            continue
        conn.execute(
            """
            INSERT INTO signal_clock_adjustments (
                adjustment_id, signal_id, corrected_snapshot_ts_utc,
                timestamp_source, timestamp_evidence_class, lineage_status,
                source_snapshot_ref, evidence_json, source_path, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*values, str(Path(path)), row["created_at_utc"]),
        )
        inserted += 1
    conn.commit()
    return inserted
