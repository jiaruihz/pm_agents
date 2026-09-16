import argparse
import sqlite3

import pytest

from scripts.ops import weather_first_seen_zero_notional_forward as forward


def retry_args() -> argparse.Namespace:
    return argparse.Namespace(
        db_lock_retries=2,
        db_lock_retry_delay_seconds=0.0,
    )


def test_lock_retry_does_not_advance_state_until_success(monkeypatch):
    attempts = []

    def fake_run_cycle(_args, working_state):
        attempts.append(dict(working_state))
        working_state["offsets"] = {"raw.jsonl": 100}
        if len(attempts) == 1:
            raise sqlite3.OperationalError("database is locked")
        return {"status": "ok"}

    monkeypatch.setattr(forward, "run_cycle", fake_run_cycle)
    state = {"offsets": {"raw.jsonl": 10}}

    result = forward.run_cycle_with_lock_retry(retry_args(), state)

    assert result == {"status": "ok"}
    assert attempts == [
        {"offsets": {"raw.jsonl": 10}},
        {"offsets": {"raw.jsonl": 10}},
    ]
    assert state == {"offsets": {"raw.jsonl": 100}}


def test_non_lock_operational_error_is_not_retried(monkeypatch):
    def fake_run_cycle(_args, _working_state):
        raise sqlite3.OperationalError("no such table: missing")

    monkeypatch.setattr(forward, "run_cycle", fake_run_cycle)

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        forward.run_cycle_with_lock_retry(retry_args(), {"offsets": {}})
