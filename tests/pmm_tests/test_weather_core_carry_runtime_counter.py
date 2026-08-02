from __future__ import annotations

import sqlite3

from scripts.ops import weather_current_yes_core_carry_tiny_live_v2 as runner


def test_next_runtime_telemetry_rows_uses_runtime_state_not_journal_scan() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE strategy_instance_runtime "
        "(instance_id TEXT PRIMARY KEY, telemetry_rows INTEGER)"
    )

    assert runner.next_runtime_telemetry_rows(conn) == 1

    conn.execute(
        "INSERT INTO strategy_instance_runtime(instance_id, telemetry_rows) VALUES (?, ?)",
        (runner.STRATEGY_INSTANCE, 1234),
    )
    assert runner.next_runtime_telemetry_rows(conn) == 1235
