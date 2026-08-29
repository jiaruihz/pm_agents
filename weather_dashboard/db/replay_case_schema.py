"""Derived, read-only replay-case views over canonical signal candidates.

``fact_signal_candidates`` remains the canonical opportunity fact.  These
views give every candidate the same replay and outcome vocabulary without
creating a second fact table or a manually curated "mistake" truth.
"""
from __future__ import annotations

import sqlite3


REPLAY_CASE_VIEW = "weather_replay_cases_v1"
REPLAY_MISTAKE_VIEW = "weather_replay_mistakes_v1"


# Older tests and partially migrated databases can contain skeletal candidate
# or lineage tables.  Do not install an invalid view there; the normal schema
# and candidate builders call this hook again after installing full contracts.
_REQUIRED_TABLE_COLUMNS = {
    "fact_signal_candidates": {
        "candidate_id",
        "candidate_grain_version",
        "strategy_key",
        "model_artifact_id",
        "trigger_event_id",
        "state_checkpoint_id",
        "feature_store_frame_id",
        "feature_row_id",
        "decision_ts_utc",
        "book_snapshot_id",
        "book_available_at_utc",
        "model_probability_after",
        "candidate_status",
        "candidate_blocker",
        "policy_selected",
        "side",
        "model_p_yes",
        "decision_entry_price",
        "paper_ordered",
        "live_filled",
        "live_pnl_usd",
        "settlement_status",
        "final_yes",
    },
    "weather_state_checkpoints": {
        "state_checkpoint_id",
        "trigger_event_id",
        "as_of_ts_utc",
        "input_event_set_hash",
        "feature_store_frame_id",
        "feature_row_id",
        "feature_schema_version",
        "feature_version_manifest",
        "pit_provenance",
        "checkpoint_status",
        "checkpoint_blocker",
    },
    "weather_information_events": {
        "information_event_id",
        "event_kind",
        "event_role",
        "source",
        "payload_hash",
        "source_event_ts_utc",
        "first_seen_at_utc",
        "available_at_utc",
        "ingested_at_utc",
        "pit_lineage_class",
        "raw_source_path",
        "raw_row_hash",
    },
}


REPLAY_CASE_SQL = f"""
CREATE VIEW {REPLAY_CASE_VIEW} AS
WITH replay_inputs AS (
  SELECT
    c.*,
    cp.trigger_event_id AS checkpoint_trigger_event_id,
    cp.as_of_ts_utc AS checkpoint_as_of_ts_utc,
    cp.input_event_set_hash AS checkpoint_input_event_set_hash,
    cp.feature_store_frame_id AS checkpoint_feature_store_frame_id,
    cp.feature_row_id AS checkpoint_feature_row_id,
    cp.feature_schema_version AS checkpoint_feature_schema_version,
    cp.feature_version_manifest AS checkpoint_feature_version_manifest,
    cp.pit_provenance AS checkpoint_pit_provenance,
    cp.checkpoint_status AS lineage_checkpoint_status,
    cp.checkpoint_blocker AS lineage_checkpoint_blocker,
    e.event_kind AS trigger_event_kind,
    e.event_role AS trigger_event_role,
    e.source AS trigger_event_source,
    e.payload_hash AS trigger_event_payload_hash,
    e.source_event_ts_utc AS trigger_event_source_ts_utc,
    e.first_seen_at_utc AS trigger_event_first_seen_at_utc,
    e.available_at_utc AS trigger_event_available_at_utc,
    e.ingested_at_utc AS trigger_event_ingested_at_utc,
    e.pit_lineage_class AS trigger_event_pit_lineage_class,
    e.raw_source_path AS trigger_event_raw_source_path,
    e.raw_row_hash AS trigger_event_raw_row_hash,
    CASE
      WHEN c.side IN ('BUY_YES', 'YES') AND c.model_probability_after IS NOT NULL
        THEN c.model_probability_after
      WHEN c.side IN ('BUY_NO', 'NO') AND c.model_probability_after IS NOT NULL
        THEN c.model_probability_after
      WHEN c.side IN ('BUY_YES', 'YES') THEN c.model_p_yes
      WHEN c.side IN ('BUY_NO', 'NO') AND c.model_p_yes IS NOT NULL
        THEN 1.0 - c.model_p_yes
      ELSE NULL
    END AS expression_probability,
    CASE
      WHEN c.final_yes IS NULL THEN NULL
      WHEN c.final_yes NOT IN (0.0, 1.0) THEN NULL
      WHEN c.side IN ('BUY_NO', 'NO') THEN 1.0 - c.final_yes
      WHEN c.side IN ('BUY_YES', 'YES') THEN c.final_yes
      ELSE NULL
    END AS expression_outcome,
    CASE
      WHEN COALESCE(c.policy_selected, 0) <> 0
        OR COALESCE(c.paper_ordered, 0) <> 0
        OR COALESCE(c.live_filled, 0) <> 0
      THEN 1 ELSE 0
    END AS decision_selected
  FROM fact_signal_candidates AS c
  LEFT JOIN weather_state_checkpoints AS cp
    ON cp.state_checkpoint_id = c.state_checkpoint_id
  LEFT JOIN weather_information_events AS e
    ON e.information_event_id = c.trigger_event_id
), replay_scored AS (
  SELECT
    replay_inputs.*,
    CASE
      WHEN expression_outcome IS NULL THEN NULL
      WHEN expression_outcome >= 0.5 THEN 1 ELSE 0
    END AS expression_won,
    CASE
      WHEN expression_probability IS NULL OR expression_outcome IS NULL THEN NULL
      WHEN expression_probability < 0.0 OR expression_probability > 1.0 THEN NULL
      WHEN (expression_probability >= 0.5 AND expression_outcome >= 0.5)
        OR (expression_probability < 0.5 AND expression_outcome < 0.5)
      THEN 1 ELSE 0
    END AS model_classification_correct,
    CASE
      WHEN expression_probability IS NULL OR expression_outcome IS NULL THEN NULL
      WHEN expression_probability < 0.0 OR expression_probability > 1.0 THEN NULL
      ELSE (expression_probability - expression_outcome)
           * (expression_probability - expression_outcome)
    END AS brier_score,
    CASE
      WHEN candidate_grain_version <> 'v2_event_checkpoint'
        THEN 'legacy_candidate_grain'
      WHEN side IS NULL OR side NOT IN ('BUY_YES', 'YES', 'BUY_NO', 'NO')
        THEN 'invalid_expression_side'
      WHEN decision_ts_utc IS NULL
        THEN 'missing_decision_ts_utc'
      WHEN julianday(decision_ts_utc) IS NULL
        THEN 'invalid_decision_ts_utc'
      WHEN state_checkpoint_id IS NULL OR trim(state_checkpoint_id) = ''
        THEN 'missing_state_checkpoint_id'
      WHEN lineage_checkpoint_status IS NULL
        THEN 'missing_state_checkpoint'
      WHEN trigger_event_id IS NULL OR trim(trigger_event_id) = ''
        THEN 'missing_trigger_event_id'
      WHEN trigger_event_pit_lineage_class IS NULL
        THEN 'missing_trigger_event'
      WHEN checkpoint_trigger_event_id <> trigger_event_id
        THEN 'checkpoint_trigger_event_mismatch'
      WHEN trigger_event_pit_lineage_class = 'late_backfill_first_seen_unknown'
        THEN 'late_backfill_first_seen_unknown'
      WHEN trigger_event_available_at_utc IS NULL
        THEN 'missing_trigger_event_available_at_utc'
      WHEN julianday(trigger_event_available_at_utc) IS NULL
        THEN 'invalid_trigger_event_available_at_utc'
      WHEN julianday(trigger_event_available_at_utc) > julianday(decision_ts_utc)
        THEN 'trigger_event_available_after_decision'
      WHEN checkpoint_as_of_ts_utc IS NULL
        THEN 'missing_checkpoint_as_of_ts_utc'
      WHEN julianday(checkpoint_as_of_ts_utc) IS NULL
        THEN 'invalid_checkpoint_as_of_ts_utc'
      WHEN julianday(checkpoint_as_of_ts_utc) > julianday(decision_ts_utc)
        THEN 'checkpoint_after_decision'
      WHEN lineage_checkpoint_status <> 'built'
        THEN COALESCE(lineage_checkpoint_blocker, 'checkpoint_not_built')
      WHEN checkpoint_input_event_set_hash IS NULL
        OR trim(checkpoint_input_event_set_hash) = ''
        THEN 'missing_checkpoint_input_event_set_hash'
      WHEN checkpoint_feature_schema_version IS NULL
        OR trim(checkpoint_feature_schema_version) = ''
        THEN 'missing_checkpoint_feature_schema_version'
      WHEN checkpoint_feature_version_manifest IS NULL
        OR trim(checkpoint_feature_version_manifest) = ''
        OR json_valid(checkpoint_feature_version_manifest) = 0
        THEN 'invalid_checkpoint_feature_version_manifest'
      WHEN checkpoint_pit_provenance IS NULL OR trim(checkpoint_pit_provenance) = ''
        THEN 'missing_checkpoint_pit_provenance'
      WHEN feature_store_frame_id IS NOT NULL
        AND checkpoint_feature_store_frame_id IS NOT NULL
        AND feature_store_frame_id <> checkpoint_feature_store_frame_id
        THEN 'feature_store_frame_id_mismatch'
      WHEN feature_row_id IS NOT NULL
        AND checkpoint_feature_row_id IS NOT NULL
        AND feature_row_id <> checkpoint_feature_row_id
        THEN 'feature_row_id_mismatch'
      WHEN COALESCE(feature_store_frame_id, checkpoint_feature_store_frame_id) IS NULL
        OR COALESCE(feature_row_id, checkpoint_feature_row_id) IS NULL
        THEN 'missing_feature_frame_reference'
      WHEN model_artifact_id IS NULL OR trim(model_artifact_id) = ''
        THEN 'missing_model_artifact_id'
      ELSE NULL
    END AS model_replay_blocker
  FROM replay_inputs
), replay_classified AS (
  SELECT
    replay_scored.*,
    CASE
      WHEN candidate_grain_version <> 'v2_event_checkpoint'
        THEN 'legacy_summary_only'
      WHEN model_replay_blocker IS NULL THEN 'ready'
      ELSE 'blocked'
    END AS model_replay_status,
    CASE
      WHEN candidate_grain_version <> 'v2_event_checkpoint'
        THEN 'legacy_candidate_grain'
      WHEN model_replay_blocker IS NOT NULL THEN model_replay_blocker
      WHEN book_snapshot_id IS NULL OR trim(book_snapshot_id) = ''
        THEN 'missing_book_snapshot_id'
      WHEN book_available_at_utc IS NULL
        THEN 'missing_book_available_at_utc'
      WHEN julianday(book_available_at_utc) IS NULL
        THEN 'invalid_book_available_at_utc'
      WHEN julianday(book_available_at_utc) > julianday(decision_ts_utc)
        THEN 'book_available_after_decision'
      WHEN decision_entry_price IS NULL
        THEN 'missing_decision_entry_price'
      ELSE NULL
    END AS policy_replay_blocker,
    CASE WHEN model_classification_correct = 0 THEN 1 ELSE 0 END
      AS model_error_flag,
    CASE WHEN decision_selected = 1 AND expression_won = 0 THEN 1 ELSE 0 END
      AS selected_loss_flag,
    CASE
      WHEN settlement_status = 'settled' AND live_pnl_usd < 0 THEN 1 ELSE 0
    END
      AS realized_loss_flag,
    CASE
      WHEN COALESCE(paper_ordered, 0) <> 0
        AND COALESCE(live_filled, 0) = 0
        AND expression_won = 1
      THEN 1 ELSE 0
    END AS paper_unfilled_winner_flag
  FROM replay_scored
)
SELECT
  replay_classified.*,
  'weather_replay_case_v1' AS replay_contract_version,
  candidate_id AS replay_case_id,
  CASE
    WHEN candidate_grain_version <> 'v2_event_checkpoint'
      THEN 'legacy_summary_only'
    WHEN model_replay_blocker IS NOT NULL THEN 'blocked'
    WHEN policy_replay_blocker IS NOT NULL THEN 'model_only'
    ELSE 'full_decision'
  END AS replay_capability,
  CASE
    WHEN candidate_grain_version <> 'v2_event_checkpoint'
      THEN 'legacy_summary_only'
    WHEN model_replay_blocker IS NOT NULL THEN 'blocked'
    WHEN policy_replay_blocker IS NULL THEN 'ready'
    ELSE 'blocked'
  END AS policy_replay_status,
  'not_checked_by_catalog' AS replay_artifact_verification_status,
  CASE
    WHEN candidate_status = 'blocked' THEN 'blocked_candidate'
    WHEN final_yes IS NULL THEN 'pending_settlement'
    WHEN COALESCE(live_filled, 0) <> 0 AND expression_won = 1 THEN 'live_filled_win'
    WHEN COALESCE(live_filled, 0) <> 0 AND expression_won = 0 THEN 'live_filled_loss'
    WHEN COALESCE(paper_ordered, 0) <> 0 AND expression_won = 1 THEN 'paper_unfilled_win'
    WHEN COALESCE(paper_ordered, 0) <> 0 AND expression_won = 0 THEN 'paper_unfilled_loss'
    WHEN COALESCE(policy_selected, 0) <> 0 AND expression_won = 1 THEN 'policy_selected_win'
    WHEN COALESCE(policy_selected, 0) <> 0 AND expression_won = 0 THEN 'policy_selected_loss'
    WHEN model_classification_correct = 1 THEN 'unselected_model_correct'
    WHEN model_classification_correct = 0 THEN 'unselected_model_error'
    ELSE 'settled_unscored'
  END AS case_outcome_class,
  CASE
    WHEN model_error_flag = 1
      OR selected_loss_flag = 1
      OR realized_loss_flag = 1
      OR paper_unfilled_winner_flag = 1
    THEN 1 ELSE 0
  END AS mistake_case_flag
FROM replay_classified
"""


REPLAY_MISTAKE_SQL = f"""
CREATE VIEW {REPLAY_MISTAKE_VIEW} AS
SELECT *
FROM {REPLAY_CASE_VIEW}
WHERE mistake_case_flag = 1
"""


def _normalized_sql(value: str | None) -> str:
    return " ".join(str(value or "").rstrip(";").split())


def _view_matches(conn: sqlite3.Connection, view: str, expected_sql: str) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name=?",
        (view,),
    ).fetchone()
    return row is not None and _normalized_sql(row[0]) == _normalized_sql(expected_sql)


def apply_replay_case_schema(conn: sqlite3.Connection) -> bool:
    """Install replay views when the full candidate contract is available.

    Returns ``True`` when the views are available and ``False`` when any
    required candidate or lineage table is missing or skeletal.
    """

    for table, required_columns in _REQUIRED_TABLE_COLUMNS.items():
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if row is None:
            return False
        actual_columns = {
            str(column[1]) for column in conn.execute(f"PRAGMA table_info({table})")
        }
        if not required_columns <= actual_columns:
            return False

    if _view_matches(conn, REPLAY_CASE_VIEW, REPLAY_CASE_SQL) and _view_matches(
        conn, REPLAY_MISTAKE_VIEW, REPLAY_MISTAKE_SQL
    ):
        return True

    # Views are derived catalog projections, so replace stale definitions in a
    # savepoint rather than leaving CREATE VIEW IF NOT EXISTS to silently keep
    # an older replay contract.  Dropping the dependent mistake view first and
    # recreating both inside one savepoint keeps readers on either the old or
    # the new pair; a failed migration rolls back to the old definitions.
    savepoint = "weather_replay_case_schema"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        conn.execute(f"DROP VIEW IF EXISTS {REPLAY_MISTAKE_VIEW}")
        conn.execute(f"DROP VIEW IF EXISTS {REPLAY_CASE_VIEW}")
        conn.execute(REPLAY_CASE_SQL)
        conn.execute(REPLAY_MISTAKE_SQL)
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    return True
