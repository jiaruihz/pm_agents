import sqlite3

from scripts.etl.build_weather_signal_candidates import CANDIDATE_DDL
from weather_dashboard.db.first_seen_schema import apply_first_seen_schema


def test_candidate_fact_has_online_selector_indexes() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(CANDIDATE_DDL)
        apply_first_seen_schema(conn)
        indexes = {str(row[1]) for row in conn.execute("PRAGMA index_list(fact_signal_candidates)")}
    finally:
        conn.close()

    assert "idx_fact_signal_candidates_active_selector" in indexes
    assert "idx_fact_signal_candidates_event_grain" in indexes
