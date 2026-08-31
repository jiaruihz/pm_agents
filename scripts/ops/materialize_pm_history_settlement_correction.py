#!/usr/bin/env python3
"""Append a narrowly-scoped correction for a stale pm_history settlement.

The canonical ``settlement_outcomes`` table is intentionally append-only.  A
pm_history row first ingested while a market is still open therefore cannot be
updated in place after the same raw file becomes final.  This materializer
preserves the original row and appends a ``manual_backfill`` correction only
when all of the following are true:

* the supplied pm_history file is closed and has exactly one binary winner;
* a same-key ``pm_history`` row already exists and is not settled;
* no settled pm_history row is contradicted; and
* an existing manual correction, if any, has the same value and source hash.

Dry-run is the default.  Pass ``--apply`` to write the append-only rows.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_dashboard.ingest.settlement_outcomes import (  # noqa: E402
    OUTCOME_COLUMNS,
    make_settlement_outcome_id,
    normalize_final_price,
    parse_pm_history_filename,
)


SOURCE_SYSTEM = "manual_backfill"
PRODUCER_BUILD_ID = "materialize_pm_history_settlement_correction_v1"


def _read_closed_document(path: Path) -> tuple[str, str, dict[str, Any], str, str]:
    parsed = parse_pm_history_filename(path.name)
    if parsed is None:
        raise ValueError(f"invalid pm_history filename: {path.name}")
    city, target_date = parsed
    raw_bytes = path.read_bytes()
    document = json.loads(raw_bytes)
    if not isinstance(document, dict) or not isinstance(document.get("brackets"), list):
        raise ValueError(f"invalid pm_history document: {path}")
    if document.get("city") not in (None, city) or document.get("date") not in (
        None,
        target_date,
    ):
        raise ValueError(f"filename/document identity mismatch: {path}")

    seen: set[str] = set()
    winners = 0
    for bracket in document["brackets"]:
        if not isinstance(bracket, dict):
            raise ValueError(f"non-object bracket in {path}")
        label = str(bracket.get("label") or "").strip()
        if not label or label in seen:
            raise ValueError(f"missing or duplicate bracket label in {path}: {label!r}")
        seen.add(label)
        if bracket.get("closed") is not True:
            raise ValueError(f"pm_history bracket is not closed: {city} {target_date} {label}")
        final_price = normalize_final_price(bracket.get("final_price"))
        if final_price is None:
            raise ValueError(
                f"pm_history bracket is not binary settled: {city} {target_date} {label}"
            )
        winners += int(final_price == 1.0)
    if not seen or winners != 1:
        raise ValueError(
            f"closed pm_history must have exactly one winner: {city} {target_date}, winners={winners}"
        )

    source_hash = hashlib.sha256(raw_bytes).hexdigest()
    source_mtime_utc = datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    ).isoformat()
    return city, target_date, document, source_hash, source_mtime_utc


def _rows_for_city_day(
    connection: sqlite3.Connection, *, city: str, target_date: str
) -> dict[str, dict[str, sqlite3.Row]]:
    rows = connection.execute(
        """
        SELECT *
        FROM settlement_outcomes
        WHERE source_system IN ('pm_history', 'manual_backfill')
          AND city = ?
          AND target_date = ?
        """,
        (city, target_date),
    ).fetchall()
    indexed: dict[str, dict[str, sqlite3.Row]] = {}
    for row in rows:
        indexed.setdefault(str(row["bracket"]), {})[str(row["source_system"])] = row
    return indexed


def plan_corrections(
    connection: sqlite3.Connection, *, source_path: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    city, target_date, document, source_hash, source_mtime_utc = _read_closed_document(
        source_path
    )
    existing = _rows_for_city_day(connection, city=city, target_date=target_date)
    if not any("pm_history" in by_source for by_source in existing.values()):
        raise ValueError(f"no canonical pm_history rows for {city} {target_date}")

    available_at_utc = datetime.now(timezone.utc).isoformat()
    planned: list[dict[str, Any]] = []
    already_applied = 0
    settled_unchanged = 0
    for bracket in document["brackets"]:
        label = str(bracket["label"]).strip()
        normalized = normalize_final_price(bracket.get("final_price"))
        assert normalized is not None
        prior = existing.get(label, {}).get("pm_history")
        if prior is None:
            continue
        if str(prior["settlement_status"]) == "settled":
            if float(prior["final_price"]) != normalized:
                raise ValueError(
                    f"closed raw contradicts settled pm_history row: {city} {target_date} {label}"
                )
            settled_unchanged += 1
            continue
        if str(prior["settlement_status"]) not in {"missing_event", "missing_bracket"}:
            raise ValueError(
                f"unsupported prior settlement status: {city} {target_date} {label} "
                f"{prior['settlement_status']}"
            )

        manual = existing.get(label, {}).get(SOURCE_SYSTEM)
        if manual is not None:
            if (
                str(manual["settlement_status"]) != "settled"
                or float(manual["final_price"]) != normalized
                or str(manual["source_payload_hash"] or "") != source_hash
            ):
                raise ValueError(
                    f"conflicting manual_backfill already exists: {city} {target_date} {label}"
                )
            already_applied += 1
            continue

        payload = {
            "closed": True,
            "correction_reason": "closed_pm_history_replaces_non_settled_pm_history",
            "prior_final_price": prior["final_price"],
            "prior_raw_final_price": prior["raw_final_price"],
            "prior_settlement_outcome_id": prior["settlement_outcome_id"],
            "prior_settlement_status": prior["settlement_status"],
            "prior_source_payload_hash": prior["source_payload_hash"],
        }
        planned.append(
            {
                "settlement_outcome_id": make_settlement_outcome_id(
                    SOURCE_SYSTEM, city, target_date, label
                ),
                "source_system": SOURCE_SYSTEM,
                "source_path": str(source_path.resolve()),
                "city": city,
                "target_date": target_date,
                "bracket": label,
                "unit": document.get("unit") or prior["unit"],
                "condition_id": bracket.get("condition_id") or prior["condition_id"],
                "market_id": bracket.get("market_id") or prior["market_id"],
                "token_id": str(bracket.get("token_id") or prior["token_id"] or "") or None,
                "raw_final_price": float(bracket["final_price"]),
                "final_price": normalized,
                "settlement_status": "settled",
                "question": bracket.get("question") or prior["question"],
                "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                "source_payload_hash": source_hash,
                "source_file_mtime_utc": source_mtime_utc,
                "first_seen_at_utc": None,
                "available_at_utc": available_at_utc,
                "pit_lineage_class": "late_backfill_first_seen_unknown",
                "producer_build_id": PRODUCER_BUILD_ID,
            }
        )

    if not planned and already_applied == 0:
        raise ValueError(f"no non-settled pm_history rows need correction: {city} {target_date}")
    return planned, {
        "city": city,
        "target_date": target_date,
        "source_path": str(source_path.resolve()),
        "source_payload_hash": source_hash,
        "planned_corrections": len(planned),
        "already_applied": already_applied,
        "settled_unchanged": settled_unchanged,
        "corrected_brackets": [row["bracket"] for row in planned],
    }


def materialize(
    connection: sqlite3.Connection, *, source_path: Path, apply: bool
) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    planned, summary = plan_corrections(connection, source_path=source_path)
    inserted = 0
    if apply:
        placeholders = ", ".join("?" for _ in OUTCOME_COLUMNS)
        columns = ", ".join(OUTCOME_COLUMNS)
        for row in planned:
            cursor = connection.execute(
                f"INSERT OR IGNORE INTO settlement_outcomes ({columns}) VALUES ({placeholders})",
                tuple(row.get(column) for column in OUTCOME_COLUMNS),
            )
            inserted += int(cursor.rowcount or 0)
        if inserted != len(planned):
            raise RuntimeError(
                f"append-only correction insert mismatch: planned={len(planned)}, inserted={inserted}"
            )
    return {**summary, "apply": apply, "inserted": inserted}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=ROOT / "runtime/weather.db")
    parser.add_argument("--pm-history-file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    uri = f"file:{args.db_path}?mode={'rw' if args.apply else 'ro'}"
    connection = sqlite3.connect(uri, uri=True, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    if not args.apply:
        connection.execute("PRAGMA query_only=ON")
    try:
        if args.apply:
            connection.execute("BEGIN IMMEDIATE")
        result = materialize(
            connection,
            source_path=args.pm_history_file,
            apply=args.apply,
        )
        if args.apply:
            connection.commit()
    except Exception:
        if args.apply:
            connection.rollback()
        raise
    finally:
        connection.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
