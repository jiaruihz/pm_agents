from __future__ import annotations

import sqlite3

from scripts.etl.build_weather_signal_candidates import CANDIDATE_DDL, write_db
from weather_dashboard.db.first_seen_schema import apply_first_seen_schema
from weather_dashboard.db.replay_case_schema import (
    REPLAY_CASE_VIEW,
    REPLAY_MISTAKE_VIEW,
    apply_replay_case_schema,
)


def _insert_event(
    conn: sqlite3.Connection,
    event_id: str,
    *,
    pit_lineage_class: str = "collector_exact",
    available_at_utc: str | None = "2026-08-29T01:00:00Z",
) -> None:
    conn.execute(
        """
        INSERT INTO weather_information_events (
          information_event_id, event_kind, event_role, source, city,
          content_key, payload_hash, first_seen_at_utc, available_at_utc,
          ingested_at_utc, pit_lineage_class, original_first_seen_unknown,
          raw_source_path, raw_row_hash
        ) VALUES (?, 'observation', 'new_content', 'test_source', 'Manila',
                  ?, ?, ?, ?, '2026-08-29T01:00:01Z', ?, ?, ?, ?)
        """,
        (
            event_id,
            f"content:{event_id}",
            f"payload:{event_id}",
            available_at_utc,
            available_at_utc,
            pit_lineage_class,
            int(pit_lineage_class == "late_backfill_first_seen_unknown"),
            f"raw/{event_id}.jsonl",
            f"row:{event_id}",
        ),
    )


def _insert_checkpoint(
    conn: sqlite3.Connection,
    checkpoint_id: str,
    event_id: str,
) -> None:
    conn.execute(
        """
        INSERT INTO weather_state_checkpoints (
          state_checkpoint_id, city, target_date, trigger_event_id,
          as_of_ts_utc, input_event_set_hash, feature_store_frame_id,
          feature_row_id, feature_schema_version, feature_version_manifest,
          pit_provenance, checkpoint_status, created_at_utc
        ) VALUES (?, 'Manila', '2026-08-29', ?, '2026-08-29T01:00:02Z',
                  ?, ?, ?, 'feature-v1', '{}', ?, 'built',
                  '2026-08-29T01:00:03Z')
        """,
        (
            checkpoint_id,
            event_id,
            f"event-set:{checkpoint_id}",
            f"frame:{checkpoint_id}",
            f"row:{checkpoint_id}",
            "test",
        ),
    )


def _insert_candidate(
    conn: sqlite3.Connection,
    candidate_id: str,
    *,
    event_id: str | None,
    checkpoint_id: str | None,
    grain: str = "v2_event_checkpoint",
    side: str = "BUY_YES",
    model_probability: float | None = 0.8,
    final_yes: float | None = 1.0,
    candidate_status: str = "scored",
    policy_selected: int = 0,
    paper_ordered: int = 0,
    live_filled: int = 0,
    live_pnl_usd: float | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO fact_signal_candidates (
          candidate_id, candidate_grain_version, strategy_key,
          model_artifact_id, trigger_event_id, state_checkpoint_id,
          feature_store_frame_id, feature_row_id, decision_ts_utc,
          book_snapshot_id, book_available_at_utc, candidate_status,
          policy_selected, condition_id, market_id, side, event_date,
          bracket, city, model_probability_after, model_p_yes,
          decision_entry_price, paper_ordered, live_filled, live_pnl_usd,
          settlement_status, final_yes
        ) VALUES (?, ?, 'core_carry', ?, ?, ?, ?, ?,
                  '2026-08-29T01:00:05Z', ?, '2026-08-29T01:00:04Z', ?,
                  ?, ?, ?, ?, '2026-08-29', '90-91', 'Manila',
                  ?, ?, 0.40, ?, ?, ?, ?, ?)
        """,
        (
            candidate_id,
            grain,
            "model:test" if grain == "v2_event_checkpoint" else None,
            event_id,
            checkpoint_id,
            f"frame:{checkpoint_id}" if checkpoint_id else None,
            f"row:{checkpoint_id}" if checkpoint_id else None,
            f"book:{candidate_id}" if grain == "v2_event_checkpoint" else None,
            candidate_status,
            policy_selected,
            f"condition:{candidate_id}",
            f"market:{candidate_id}",
            side,
            model_probability if grain == "v2_event_checkpoint" else None,
            (
                None
                if model_probability is None
                else 1.0 - model_probability
                if side in {"BUY_NO", "NO"}
                else model_probability
            ),
            paper_ordered,
            live_filled,
            live_pnl_usd,
            "settled" if final_yes is not None else "pending",
            final_yes,
        ),
    )


def _populated_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(CANDIDATE_DDL)
    apply_first_seen_schema(conn)

    _insert_event(conn, "event-ready")
    _insert_checkpoint(conn, "checkpoint-ready", "event-ready")
    _insert_event(
        conn,
        "event-late",
        pit_lineage_class="late_backfill_first_seen_unknown",
        available_at_utc=None,
    )
    _insert_checkpoint(conn, "checkpoint-late", "event-late")

    _insert_candidate(
        conn,
        "selected-win",
        event_id="event-ready",
        checkpoint_id="checkpoint-ready",
        policy_selected=1,
        live_filled=1,
        live_pnl_usd=0.60,
    )
    _insert_candidate(
        conn,
        "selected-loss",
        event_id="event-ready",
        checkpoint_id="checkpoint-ready",
        final_yes=0.0,
        policy_selected=1,
        live_filled=1,
        live_pnl_usd=-0.40,
    )
    _insert_candidate(
        conn,
        "paper-unfilled-win",
        event_id="event-ready",
        checkpoint_id="checkpoint-ready",
        paper_ordered=1,
    )
    _insert_candidate(
        conn,
        "unselected-correct",
        event_id="event-ready",
        checkpoint_id="checkpoint-ready",
        model_probability=0.2,
        final_yes=0.0,
    )
    _insert_candidate(
        conn,
        "no-side-correct",
        event_id="event-ready",
        checkpoint_id="checkpoint-ready",
        side="BUY_NO",
        model_probability=0.75,
        final_yes=0.0,
    )
    _insert_candidate(
        conn,
        "late-blocked",
        event_id="event-late",
        checkpoint_id="checkpoint-late",
        final_yes=None,
        candidate_status="blocked",
    )
    _insert_candidate(
        conn,
        "legacy-error",
        event_id=None,
        checkpoint_id=None,
        grain="v1_legacy_daily",
        final_yes=0.0,
    )
    conn.commit()
    return conn


def test_replay_case_view_keeps_the_full_denominator_and_derives_mistakes() -> None:
    conn = _populated_db()
    rows = {
        str(row["candidate_id"]): row
        for row in conn.execute(f"SELECT * FROM {REPLAY_CASE_VIEW}")
    }

    assert set(rows) == {
        "selected-win",
        "selected-loss",
        "paper-unfilled-win",
        "unselected-correct",
        "no-side-correct",
        "late-blocked",
        "legacy-error",
    }
    assert rows["selected-win"]["replay_capability"] == "full_decision"
    assert rows["selected-win"]["replay_artifact_verification_status"] == (
        "not_checked_by_catalog"
    )
    assert rows["selected-win"]["case_outcome_class"] == "live_filled_win"
    assert rows["selected-win"]["mistake_case_flag"] == 0

    assert rows["selected-loss"]["case_outcome_class"] == "live_filled_loss"
    assert rows["selected-loss"]["model_error_flag"] == 1
    assert rows["selected-loss"]["selected_loss_flag"] == 1
    assert rows["selected-loss"]["realized_loss_flag"] == 1
    assert rows["selected-loss"]["mistake_case_flag"] == 1

    assert rows["paper-unfilled-win"]["case_outcome_class"] == "paper_unfilled_win"
    assert rows["paper-unfilled-win"]["paper_unfilled_winner_flag"] == 1
    assert rows["unselected-correct"]["case_outcome_class"] == "unselected_model_correct"
    assert rows["unselected-correct"]["mistake_case_flag"] == 0
    assert rows["no-side-correct"]["expression_probability"] == 0.75
    assert rows["no-side-correct"]["expression_outcome"] == 1.0
    assert rows["no-side-correct"]["model_classification_correct"] == 1

    assert rows["late-blocked"]["replay_capability"] == "blocked"
    assert rows["late-blocked"]["model_replay_blocker"] == (
        "late_backfill_first_seen_unknown"
    )
    assert rows["late-blocked"]["case_outcome_class"] == "blocked_candidate"
    assert rows["late-blocked"]["mistake_case_flag"] == 0

    assert rows["legacy-error"]["replay_capability"] == "legacy_summary_only"
    assert rows["legacy-error"]["model_replay_status"] == "legacy_summary_only"
    assert rows["legacy-error"]["mistake_case_flag"] == 1

    mistake_ids = {
        str(row[0])
        for row in conn.execute(
            f"SELECT candidate_id FROM {REPLAY_MISTAKE_VIEW} ORDER BY candidate_id"
        )
    }
    assert mistake_ids == {"legacy-error", "paper-unfilled-win", "selected-loss"}
    conn.close()


def test_replay_case_view_is_idempotent_and_one_row_per_candidate() -> None:
    conn = _populated_db()
    assert apply_replay_case_schema(conn) is True
    assert apply_replay_case_schema(conn) is True

    candidate_count = conn.execute(
        "SELECT count(*) FROM fact_signal_candidates"
    ).fetchone()[0]
    case_count = conn.execute(f"SELECT count(*) FROM {REPLAY_CASE_VIEW}").fetchone()[0]
    first_read = conn.execute(
        f"SELECT replay_case_id FROM {REPLAY_CASE_VIEW} ORDER BY replay_case_id"
    ).fetchall()
    second_read = conn.execute(
        f"SELECT replay_case_id FROM {REPLAY_CASE_VIEW} ORDER BY replay_case_id"
    ).fetchall()

    assert case_count == candidate_count
    assert first_read == second_read
    assert all(row[0] for row in first_read)
    conn.close()


def test_replay_case_schema_replaces_a_stale_existing_view_pair() -> None:
    conn = _populated_db()
    conn.execute(f"DROP VIEW {REPLAY_MISTAKE_VIEW}")
    conn.execute(f"DROP VIEW {REPLAY_CASE_VIEW}")
    conn.execute(
        f"CREATE VIEW {REPLAY_CASE_VIEW} AS "
        "SELECT candidate_id AS replay_case_id FROM fact_signal_candidates"
    )
    conn.execute(
        f"CREATE VIEW {REPLAY_MISTAKE_VIEW} AS "
        f"SELECT * FROM {REPLAY_CASE_VIEW} WHERE 0"
    )

    assert apply_replay_case_schema(conn) is True
    columns = {
        str(row[1]) for row in conn.execute(f"PRAGMA table_info({REPLAY_CASE_VIEW})")
    }
    assert "replay_artifact_verification_status" in columns
    assert conn.execute(f"SELECT count(*) FROM {REPLAY_CASE_VIEW}").fetchone()[0] == 7
    assert conn.execute(f"SELECT count(*) FROM {REPLAY_MISTAKE_VIEW}").fetchone()[0] == 3
    conn.close()


def test_replay_readiness_fails_closed_on_conflicting_or_incomplete_lineage() -> None:
    conn = _populated_db()
    conn.execute(
        """
        UPDATE fact_signal_candidates
        SET feature_store_frame_id = 'frame:wrong'
        WHERE candidate_id = 'selected-win'
        """
    )
    mismatch = conn.execute(
        f"""
        SELECT model_replay_status, model_replay_blocker
        FROM {REPLAY_CASE_VIEW}
        WHERE candidate_id = 'selected-win'
        """
    ).fetchone()
    assert tuple(mismatch) == ("blocked", "feature_store_frame_id_mismatch")

    conn.execute(
        """
        UPDATE weather_state_checkpoints
        SET pit_provenance = ''
        WHERE state_checkpoint_id = 'checkpoint-ready'
        """
    )
    missing_pit = conn.execute(
        f"""
        SELECT model_replay_status, model_replay_blocker
        FROM {REPLAY_CASE_VIEW}
        WHERE candidate_id = 'unselected-correct'
        """
    ).fetchone()
    assert tuple(missing_pit) == ("blocked", "missing_checkpoint_pit_provenance")
    conn.close()


def test_candidate_builder_drop_and_recreate_preserves_queryable_views() -> None:
    conn = _populated_db()
    write_db(conn, [])

    assert conn.execute(
        "SELECT count(*) FROM fact_signal_candidates"
    ).fetchone()[0] == 6
    assert conn.execute(f"SELECT count(*) FROM {REPLAY_CASE_VIEW}").fetchone()[0] == 6
    assert conn.execute(
        f"SELECT count(*) FROM {REPLAY_MISTAKE_VIEW}"
    ).fetchone()[0] == 2
    conn.close()


def test_replay_views_fail_closed_for_a_skeletal_legacy_table() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE fact_signal_candidates (candidate_id TEXT PRIMARY KEY, event_date TEXT)"
    )

    assert apply_replay_case_schema(conn) is False
    assert conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='view' AND name LIKE 'weather_replay_%'"
    ).fetchone()[0] == 0
    conn.close()


def test_replay_views_fail_closed_when_lineage_tables_are_missing() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(CANDIDATE_DDL)

    assert apply_replay_case_schema(conn) is False
    assert conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='view' AND name LIKE 'weather_replay_%'"
    ).fetchone()[0] == 0
    conn.close()
