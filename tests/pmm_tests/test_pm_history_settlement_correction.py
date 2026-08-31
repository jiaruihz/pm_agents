from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from weather_dashboard.ingest.settlement_outcomes import (
    ensure_settlement_outcomes_schema,
    insert_settlement_outcome,
    make_settlement_outcome_id,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/ops/materialize_pm_history_settlement_correction.py"
SPEC = importlib.util.spec_from_file_location("pm_history_settlement_correction", SCRIPT)
assert SPEC and SPEC.loader
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)


def _connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    ensure_settlement_outcomes_schema(connection)
    return connection


def _prior_row(*, bracket: str, final_price: float, status: str) -> dict[str, object]:
    return {
        "settlement_outcome_id": make_settlement_outcome_id(
            "pm_history", "Madrid", "2026-08-27", bracket
        ),
        "source_system": "pm_history",
        "source_path": "/old/Madrid_2026-08-27.json",
        "city": "Madrid",
        "target_date": "2026-08-27",
        "bracket": bracket,
        "unit": "C",
        "condition_id": f"condition-{bracket}",
        "market_id": f"market-{bracket}",
        "token_id": f"token-{bracket}",
        "raw_final_price": final_price,
        "final_price": final_price,
        "settlement_status": status,
        "question": f"Madrid {bracket}",
        "payload": "{}",
        "source_payload_hash": "old-hash",
        "source_file_mtime_utc": "2026-08-27T00:00:00+00:00",
        "first_seen_at_utc": None,
        "available_at_utc": "2026-08-27T00:00:00+00:00",
        "pit_lineage_class": "late_backfill_first_seen_unknown",
        "producer_build_id": "test",
    }


def _write_closed_raw(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "city": "Madrid",
                "date": "2026-08-27",
                "unit": "C",
                "brackets": [
                    {
                        "label": "23",
                        "closed": True,
                        "final_price": 1.0,
                        "condition_id": "condition-23",
                        "market_id": "market-23",
                        "token_id": "token-23",
                    },
                    {
                        "label": "24",
                        "closed": True,
                        "final_price": 0.0,
                        "condition_id": "condition-24",
                        "market_id": "market-24",
                        "token_id": "token-24",
                    },
                    {
                        "label": "25",
                        "closed": True,
                        "final_price": 0.0,
                        "condition_id": "condition-25",
                        "market_id": "market-25",
                        "token_id": "token-25",
                    },
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def test_materializer_appends_only_non_settled_rows_and_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "weather.db"
    raw_path = tmp_path / "Madrid_2026-08-27.json"
    _write_closed_raw(raw_path)
    connection = _connection(db_path)
    try:
        insert_settlement_outcome(
            connection,
            _prior_row(bracket="23", final_price=0.985, status="missing_bracket"),
        )
        insert_settlement_outcome(
            connection,
            _prior_row(bracket="24", final_price=0.012, status="missing_bracket"),
        )
        insert_settlement_outcome(
            connection,
            _prior_row(bracket="25", final_price=0.0, status="settled"),
        )
        connection.commit()

        dry_run = subject.materialize(connection, source_path=raw_path, apply=False)
        assert dry_run["planned_corrections"] == 2
        assert dry_run["inserted"] == 0

        applied = subject.materialize(connection, source_path=raw_path, apply=True)
        connection.commit()
        assert applied["inserted"] == 2
        rows = connection.execute(
            "SELECT bracket, final_price, source_payload_hash FROM settlement_outcomes "
            "WHERE source_system='manual_backfill' ORDER BY bracket"
        ).fetchall()
        assert [(row["bracket"], row["final_price"]) for row in rows] == [
            ("23", 1.0),
            ("24", 0.0),
        ]
        assert all(row["source_payload_hash"] for row in rows)

        rerun = subject.materialize(connection, source_path=raw_path, apply=True)
        assert rerun["planned_corrections"] == 0
        assert rerun["already_applied"] == 2
        assert rerun["inserted"] == 0
    finally:
        connection.close()


def test_materializer_rejects_contradiction_of_settled_pm_history(tmp_path: Path) -> None:
    db_path = tmp_path / "weather.db"
    raw_path = tmp_path / "Madrid_2026-08-27.json"
    _write_closed_raw(raw_path)
    connection = _connection(db_path)
    try:
        insert_settlement_outcome(
            connection,
            _prior_row(bracket="23", final_price=0.0, status="settled"),
        )
        connection.commit()
        with pytest.raises(ValueError, match="contradicts settled pm_history"):
            subject.materialize(connection, source_path=raw_path, apply=False)
    finally:
        connection.close()
