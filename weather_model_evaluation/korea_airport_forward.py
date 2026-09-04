"""Frozen-forward research for the Seoul/Busan CrossNO model branch.

This is the probabilistic-model challenger to CrossNO's rule-based source-cross
admission, not an independent trading strategy.  The two cities share parsing,
evidence funnels, and scoring utilities, but they do not share a fitted basis,
model parameters, or an admission verdict.  Seoul is evaluated as a five-class
remaining-heat distribution.  Busan is evaluated as the exact-rung NO
probability emitted by its existing zero-notional runtime.

This module is research-only.  It reads immutable journals and settlement
facts, writes external research artifacts, and never emits an intent or order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Mapping, Sequence

import joblib
import numpy as np
import pandas as pd

from .busan_market_prior import RUNTIME_ARTIFACT_SCHEMA_VERSION
from .korea_city_distribution import (
    ARTIFACT_SCHEMA_VERSION,
    CLASSES,
    PHYSICAL_FEATURES,
    _amos_feature_frame,
    _predict_aligned,
    _score_grains,
    attach_next_execution_ladder,
    calibrated_market_weather_posterior,
    join_market_decision_panel,
    load_full_ladder_snapshots,
    replay_distribution_signals,
    summarize_distribution_trades,
)
from .korea_city_exact_no import (
    finite,
    load_checkpoint_frame,
    load_settlement_winners,
    sha256_file,
    stable_hash,
    stable_json,
)
from .probability import (
    binary_loss_values,
    binary_score,
    date_block_bootstrap_delta,
)


OUTPUT_SCHEMA_VERSION = "korea_airport_fast_source_forward_v2"
BUSAN_GRAIN_VERSION = "busan_pending_confirmation_state_v1"
DEFAULT_MIN_FORWARD_DATES = 30
DEFAULT_FRESH_BOOK_SECONDS = 10.0
REPLAY_SHARES = 5.0

# Keep the historical evaluator/schema name above so sealed runs remain
# reproducible. Strategy identity belongs to the existing CrossNO lineage.
CROSSNO_PARENT_STRATEGY_FAMILY = "cross_no_v2_metar"
CROSSNO_PARENT_RUNTIME_STRATEGY_ID = "cross_no_v2_metar_v1"
CROSSNO_MODEL_PREDECESSOR = "korea_cross_event_dual_head_v5"
CROSSNO_CITY_MODEL_IDS = {
    "Seoul": "seoul_intraday_remaining_heat_distribution",
    "Busan": "busan_intraday_exact_no_online_market_prior_residual",
}


def crossno_model_branch_identity(cities: Sequence[str]) -> dict[str, Any]:
    """Return the stable strategy lineage for a Korea airport evaluation run."""
    unknown = sorted(set(cities) - set(CROSSNO_CITY_MODEL_IDS))
    if unknown:
        raise ValueError(f"unsupported CrossNO model-branch cities: {unknown}")
    return {
        "parent_strategy_family": CROSSNO_PARENT_STRATEGY_FAMILY,
        "parent_runtime_strategy_id": CROSSNO_PARENT_RUNTIME_STRATEGY_ID,
        "branch_role": "city_specific_probability_model_challenger",
        "incumbent_decision_form": "rule_based_source_cross",
        "upgrade_axis": (
            "replace binary source-cross admission with a city-specific "
            "settlement probability and same-row market residual"
        ),
        "model_predecessor": CROSSNO_MODEL_PREDECESSOR,
        "city_model_ids": {city: CROSSNO_CITY_MODEL_IDS[city] for city in cities},
        "comparison_scope": "same_row_market_only_in_this_forward",
        "incumbent_rule_replay": "not_run",
        "artifact_experiment_role": "forward_evaluator_not_independent_strategy",
        "execution_contract": "unchanged_and_not_exercised_by_this_research_run",
        "production_replacement_status": "none",
    }


@dataclass(frozen=True)
class KoreaAirportForwardConfig:
    cities: tuple[str, ...]
    start_date: str
    end_date: str
    bootstrap_draws: int = 5000
    seed: int = 20260904
    minimum_forward_dates: int = DEFAULT_MIN_FORWARD_DATES
    fresh_book_seconds: float = DEFAULT_FRESH_BOOK_SECONDS

    def __post_init__(self) -> None:
        normalized = tuple(dict.fromkeys(self.cities))
        if normalized != self.cities or not normalized:
            raise ValueError("cities must be a non-empty unique tuple")
        if not set(normalized).issubset({"Seoul", "Busan"}):
            raise ValueError(f"unsupported Korea airport city set: {normalized}")
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        if self.bootstrap_draws < 100:
            raise ValueError("bootstrap_draws must be at least 100")
        if self.minimum_forward_dates < 1:
            raise ValueError("minimum_forward_dates must be positive")
        if not math.isfinite(self.fresh_book_seconds) or self.fresh_book_seconds <= 0:
            raise ValueError("fresh_book_seconds must be finite and positive")


def _file_identity(path: Path, *, hash_content: bool = True) -> dict[str, Any]:
    realpath = path.resolve()
    stat = realpath.stat()
    return {
        "requested_path": str(path),
        "realpath": str(realpath),
        "device": int(stat.st_dev),
        "inode": int(stat.st_ino),
        "size_bytes": int(stat.st_size),
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        "sha256": sha256_file(realpath) if hash_content else None,
    }


def _json_value(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, np.ndarray):
        return [float(item) for item in value]
    return value


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_json_safe(item) for item in value]
    return _json_value(value)


def _write_jsonl_gz(records: Iterable[Mapping[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw_handle:
        with gzip.GzipFile(fileobj=raw_handle, mode="wb", mtime=0) as gzip_handle:
            with io.TextIOWrapper(gzip_handle, encoding="utf-8") as handle:
                for record in records:
                    handle.write(
                        stable_json(_json_safe(record))
                        + "\n"
                    )


def _frame_records(frame: pd.DataFrame, columns: Sequence[str]) -> Iterable[dict[str, Any]]:
    available = [column for column in columns if column in frame.columns]
    return frame[available].to_dict("records")


def _bracket_value(value: Any) -> int | None:
    text = str(value or "").strip()
    match = re.search(r"-?\d+", text)
    return None if match is None else int(match.group())


def _timestamp_column(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
    return pd.to_datetime(frame[column], utc=True, errors="coerce")


def _clock_audit(
    frame: pd.DataFrame, *, decision_column: str = "decision_ts_utc"
) -> dict[str, Any]:
    """Audit every evidence clock consumed by the probability model.

    Forecast and book availability are independent branches, both of which
    must precede collector emission and the runtime decision.  A missing clock
    is a structured blocker, while ordering violations are counted only where
    both clocks exist.
    """

    source_observation = _timestamp_column(frame, "source_observation_ts_utc")
    source_first_seen = _timestamp_column(frame, "source_first_seen_ts_utc")
    source_available = _timestamp_column(frame, "source_available_at_utc")
    feature_book = _timestamp_column(frame, "feature_book_available_at_utc")
    forecast = _timestamp_column(frame, "forecast_available_at_utc")
    decision = _timestamp_column(frame, decision_column)
    collector = _timestamp_column(frame, "collector_emitted_at_utc")
    required = pd.DataFrame(
        {
            "source_observation": source_observation,
            "source_first_seen": source_first_seen,
            "source_available": source_available,
            "feature_book": feature_book,
            "forecast": forecast,
            "decision": decision,
            "collector": collector,
        }
    )
    missing_any = required.isna().any(axis=1)
    observation_after_first_seen = (
        source_observation.notna()
        & source_first_seen.notna()
        & source_observation.gt(source_first_seen)
    )
    first_seen_after_available = (
        source_first_seen.notna()
        & source_available.notna()
        & source_first_seen.gt(source_available)
    )
    available_after_book = (
        source_available.notna()
        & feature_book.notna()
        & source_available.gt(feature_book)
    )
    book_after_decision = (
        feature_book.notna() & decision.notna() & feature_book.gt(decision)
    )
    collector_before_book = (
        collector.notna() & feature_book.notna() & collector.lt(feature_book)
    )
    forecast_after_collector = (
        forecast.notna() & collector.notna() & forecast.gt(collector)
    )
    forecast_after_decision = (
        forecast.notna() & decision.notna() & forecast.gt(decision)
    )
    collector_after_decision = (
        collector.notna() & decision.notna() & collector.gt(decision)
    )
    ordering_violation = (
        observation_after_first_seen
        | first_seen_after_available
        | available_after_book
        | book_after_decision
        | collector_before_book
        | forecast_after_collector
        | forecast_after_decision
        | collector_after_decision
    )
    return {
        "rows": int(len(frame)),
        "decision_column": decision_column,
        "complete_clock_rows": int((~missing_any).sum()),
        "missing_clock_rows": int(missing_any.sum()),
        "missing_by_clock": {
            str(column): int(required[column].isna().sum())
            for column in required.columns
        },
        "source_observation_after_first_seen": int(
            observation_after_first_seen.sum()
        ),
        "source_first_seen_after_available": int(first_seen_after_available.sum()),
        "source_available_after_feature_book": int(available_after_book.sum()),
        "feature_book_after_decision": int(book_after_decision.sum()),
        "collector_emitted_before_feature_book": int(collector_before_book.sum()),
        "forecast_available_after_collector": int(forecast_after_collector.sum()),
        "forecast_available_after_decision": int(forecast_after_decision.sum()),
        "collector_emitted_after_decision": int(
            collector_after_decision.sum()
        ),
        "ordering_violation_rows": int(ordering_violation.sum()),
        "ordering_pass": bool(not ordering_violation.any()),
        "pass": bool(not missing_any.any() and not ordering_violation.any()),
    }


def build_source_basis_audit(
    checkpoints: pd.DataFrame,
    *,
    city: str,
    station_id: str,
    fresh_book_seconds: float = DEFAULT_FRESH_BOOK_SECONDS,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Audit first airport-source threshold crossings against settlement truth.

    A crossing claims that the airport source running maximum reached the next
    native integer lattice (`running_max >= current_rung + 0.5`).  Settlement
    confirms that claim only when the final winning bracket is above the rung.
    The label is attached after the event and never changes its feature clocks.
    """

    required = {
        "target_date",
        "routine_rung",
        "source_event_key",
        "source_observation_ts_utc",
        "source_first_seen_ts_utc",
        "source_available_at_utc",
        "feature_book_available_at_utc",
        "forecast_available_at_utc",
        "collector_emitted_at_utc",
        "decision_ts_utc",
        "winner_bracket",
        "book_status",
        "source_to_book_lag_seconds",
        "effective_no_ask",
        "effective_no_ask_size",
    }
    missing = sorted(required - set(checkpoints.columns))
    if missing:
        raise ValueError(f"source-basis audit missing columns: {missing}")
    work = checkpoints.copy()
    work["winner_value"] = work["winner_bracket"].map(_bracket_value)
    work["routine_rung"] = pd.to_numeric(work["routine_rung"], errors="coerce")
    work["source_to_book_lag_seconds"] = pd.to_numeric(
        work["source_to_book_lag_seconds"], errors="coerce"
    )
    work = work.sort_values(
        ["target_date", "source_available_at_utc", "source_event_key"], kind="stable"
    )
    anchor_columns = {
        "group_max": "max_runway_running_max_c",
        "preferred_runway": "preferred_running_max_c",
    }
    rows: list[pd.DataFrame] = []
    anchor_summaries: dict[str, Any] = {}
    for anchor, column in anchor_columns.items():
        if column not in work:
            anchor_summaries[anchor] = {"status": "anchor_not_available"}
            continue
        local = work.copy()
        local["source_running_max_c"] = pd.to_numeric(
            local[column], errors="coerce"
        )
        local["source_cross"] = local["source_running_max_c"].ge(
            local["routine_rung"] + 0.5
        )
        crosses = local[local["source_cross"]].copy()
        crosses = crosses.drop_duplicates(
            ["target_date", "routine_rung"], keep="first"
        )
        crosses["basis_anchor"] = anchor
        crosses["basis_status"] = np.where(
            crosses["winner_value"].isna(),
            "settlement_missing",
            np.where(
                crosses["winner_value"].gt(crosses["routine_rung"]),
                "confirmed",
                "terminal_false",
            ),
        )
        crosses["source_minus_settlement_c"] = (
            crosses["source_running_max_c"] - crosses["winner_value"]
        )
        crosses["fresh_executable"] = (
            crosses["book_status"].eq("scorable")
            & crosses["source_to_book_lag_seconds"].between(
                0.0, fresh_book_seconds, inclusive="both"
            )
            & pd.to_numeric(crosses["effective_no_ask"], errors="coerce").between(
                0.0, 1.0, inclusive="both"
            )
            & pd.to_numeric(
                crosses["effective_no_ask_size"], errors="coerce"
            ).gt(0.0)
        )
        settled = crosses[crosses["basis_status"].ne("settlement_missing")]
        correct = settled[settled["basis_status"].eq("confirmed")]
        false = settled[settled["basis_status"].eq("terminal_false")]
        terminal = local[
            local["winner_value"].notna() & local["source_running_max_c"].notna()
        ].copy()
        terminal = terminal.sort_values(
            ["target_date", "source_available_at_utc", "source_event_key"],
            kind="stable",
        ).groupby("target_date", as_index=False, sort=True).tail(1)
        terminal["source_native_rung"] = np.floor(
            terminal["source_running_max_c"] + 0.5
        )
        terminal["source_native_minus_settlement_rungs"] = (
            terminal["source_native_rung"] - terminal["winner_value"]
        )
        terminal_delta = terminal["source_native_minus_settlement_rungs"]
        anchor_summaries[anchor] = {
            "status": "ok",
            "cross_entries": int(len(crosses)),
            "cross_dates": int(crosses["target_date"].nunique()),
            "settled_cross_entries": int(len(settled)),
            "settled_cross_dates": int(settled["target_date"].nunique()),
            "confirmed_entries": int(len(correct)),
            "confirmed_dates": int(correct["target_date"].nunique()),
            "terminal_false_entries": int(len(false)),
            "terminal_false_dates": int(false["target_date"].nunique()),
            "terminal_false_rate": (
                None if settled.empty else float(len(false) / len(settled))
            ),
            "fresh_executable_confirmed_entries": int(
                correct["fresh_executable"].sum()
            ),
            "fresh_executable_terminal_false_entries": int(
                false["fresh_executable"].sum()
            ),
            "actual_fill_confirmed_entries": 0,
            "actual_fill_terminal_false_entries": 0,
            "first_cross_source_minus_final_settlement_c": {
                "p50": finite(settled["source_minus_settlement_c"].quantile(0.50)),
                "p95": finite(settled["source_minus_settlement_c"].quantile(0.95)),
                "min": finite(settled["source_minus_settlement_c"].min()),
                "max": finite(settled["source_minus_settlement_c"].max()),
            },
            "terminal_date_basis": {
                "dates": int(len(terminal)),
                "source_native_rung_definition": "floor(terminal_running_max_c + 0.5)",
                "exact_match_dates": int(terminal_delta.eq(0.0).sum()),
                "source_above_settlement_dates": int(terminal_delta.gt(0.0).sum()),
                "source_below_settlement_dates": int(terminal_delta.lt(0.0).sum()),
                "source_native_minus_settlement_rungs": {
                    "p50": finite(terminal_delta.quantile(0.50)),
                    "p95": finite(terminal_delta.quantile(0.95)),
                    "min": finite(terminal_delta.min()),
                    "max": finite(terminal_delta.max()),
                },
            },
        }
        rows.append(crosses)
    records = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    return (
        {
            "city": city,
            "station_id": station_id,
            "source": "AMOS airport runway point-group first-seen",
            "settlement_role": "label_only_after_decision",
            "cross_definition": "source_running_max_c >= routine_rung + 0.5C",
            "confirmation_definition": "settlement_winner_value > routine_rung",
            "fresh_executable_definition": (
                "current-rung two-sided exact book, positive visible NO size, "
                f"0<=source_to_book_lag<={fresh_book_seconds:g}s"
            ),
            "anchors": anchor_summaries,
        },
        records,
    )


def load_busan_decision_bundles(
    path: Path,
    *,
    start_date: str,
    end_date: str,
    expected_artifact_sha256: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load and deduplicate Busan decision bundles without selecting on outcome."""

    journal_rows = 0
    city_window_rows = 0
    rows: list[dict[str, Any]] = []
    realpath = path.resolve()
    snapshot_stat = realpath.stat()
    snapshot_size = int(snapshot_stat.st_size)
    snapshot_hash = hashlib.sha256()
    incomplete_tail_ignored = False
    with realpath.open("rb") as handle:
        line_number = 0
        remaining = snapshot_size
        while remaining > 0:
            raw_line = handle.readline(remaining)
            if not raw_line:
                break
            remaining -= len(raw_line)
            snapshot_hash.update(raw_line)
            if not raw_line.endswith(b"\n"):
                incomplete_tail_ignored = True
                break
            line_number += 1
            line = raw_line.decode("utf-8", errors="ignore")
            journal_rows += 1
            try:
                bundle = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(bundle, dict):
                continue
            candidate = bundle.get("signal_candidate") or {}
            if candidate.get("city") != "Busan":
                continue
            target_date = str(candidate.get("target_date") or "")
            if not start_date <= target_date <= end_date:
                continue
            city_window_rows += 1
            event = bundle.get("information_event") or {}
            model = bundle.get("model_output") or {}
            checkpoint = bundle.get("state_checkpoint") or {}
            input_refs = candidate.get("input_refs") or model.get("input_refs") or ()
            checkpoint_ref = next(
                (
                    dict(value)
                    for value in input_refs
                    if isinstance(value, Mapping)
                    and str(value.get("role") or "") == "checkpoint"
                ),
                {},
            )
            rows.append(
                {
                    "raw_ref": f"{path}#{line_number}",
                    "bundle_record_id": str(bundle.get("bundle_record_id") or ""),
                    "candidate_id": str(candidate.get("candidate_id") or ""),
                    "checkpoint_id": str(candidate.get("checkpoint_id") or ""),
                    "trigger_event_id": str(candidate.get("trigger_event_id") or ""),
                    "information_event_content_key": str(
                        event.get("content_key") or ""
                    ),
                    "source_event_key": str(
                        checkpoint_ref.get("record_identity") or ""
                    ),
                    "checkpoint_input_path": str(
                        checkpoint_ref.get("physical_path") or ""
                    ),
                    "checkpoint_input_schema_version": str(
                        checkpoint_ref.get("schema_version") or ""
                    ),
                    "source_observation_ts_utc": event.get("source_event_ts_utc"),
                    "source_first_seen_ts_utc": event.get("first_seen_at_utc"),
                    "source_available_at_utc": event.get("available_at_utc"),
                    "decision_ts_utc": candidate.get("decision_ts_utc"),
                    "state_checkpoint_as_of_ts_utc": checkpoint.get("as_of_ts_utc"),
                    "state_checkpoint_created_at_utc": checkpoint.get(
                        "created_at_utc"
                    ),
                    "city": "Busan",
                    "target_date": target_date,
                    "bracket": str(candidate.get("bracket") or ""),
                    "condition_id": str(candidate.get("condition_id") or ""),
                    "market_id": str(candidate.get("market_id") or ""),
                    "token_id": str(candidate.get("token_id") or ""),
                    "side": str(candidate.get("side") or ""),
                    "candidate_status": str(candidate.get("candidate_status") or ""),
                    "blocker_reason": candidate.get("blocker_reason"),
                    "market_evidence_status": candidate.get("market_evidence_status"),
                    "market_p_no": finite(candidate.get("market_p")),
                    "model_p_no": finite(candidate.get("p_model")),
                    "executable_cost": finite(candidate.get("executable_cost")),
                    "policy_selected": bool(candidate.get("selected")),
                    "would_enter": bool(
                        (candidate.get("metadata") or {}).get("legacy_would_enter")
                    ),
                    "edge_after_fee": finite(
                        (candidate.get("metadata") or {}).get("edge_after_fee")
                    ),
                    "model_id": str(candidate.get("model_id") or ""),
                    "model_artifact_sha256": str(
                        candidate.get("model_artifact_id") or ""
                    ),
                    "feature_book_snapshot_id": candidate.get(
                        "feature_book_snapshot_id"
                    ),
                    "execution_book_snapshot_id": candidate.get(
                        "execution_book_snapshot_id"
                    ),
                    "candidate_grain_version": (
                        ((model.get("metadata") or {}).get("candidate_grain_version"))
                        or BUSAN_GRAIN_VERSION
                    ),
                }
            )
    if not rows:
        raise RuntimeError("no Busan decision bundles in requested window")
    frame = pd.DataFrame(rows)
    frame["decision_ts_utc"] = pd.to_datetime(
        frame["decision_ts_utc"], utc=True, errors="coerce"
    )
    frame["source_observation_ts_utc"] = pd.to_datetime(
        frame["source_observation_ts_utc"], utc=True, errors="coerce"
    )
    frame["source_available_at_utc"] = pd.to_datetime(
        frame["source_available_at_utc"], utc=True, errors="coerce"
    )
    frame["source_first_seen_ts_utc"] = pd.to_datetime(
        frame["source_first_seen_ts_utc"], utc=True, errors="coerce"
    )
    frame["state_checkpoint_as_of_ts_utc"] = pd.to_datetime(
        frame["state_checkpoint_as_of_ts_utc"], utc=True, errors="coerce"
    )
    frame["state_checkpoint_created_at_utc"] = pd.to_datetime(
        frame["state_checkpoint_created_at_utc"], utc=True, errors="coerce"
    )
    frame["candidate_identity_valid"] = frame["candidate_id"].str.strip().ne("")
    frame["checkpoint_input_ref_valid"] = (
        frame["source_event_key"].str.strip().ne("")
        & frame["checkpoint_input_path"].str.strip().ne("")
        & frame["checkpoint_input_schema_version"].str.strip().ne("")
    )
    frame["candidate_dedupe_key"] = [
        (
            f"candidate:{candidate_id}"
            if candidate_id
            else f"raw:{raw_ref}"
        )
        for candidate_id, raw_ref in zip(frame["candidate_id"], frame["raw_ref"])
    ]
    frame = frame.sort_values(
        [
            "target_date",
            "decision_ts_utc",
            "candidate_dedupe_key",
            "policy_selected",
        ],
        kind="stable",
    )
    duplicate_candidate_rows = int(
        (
            frame["candidate_identity_valid"]
            & frame.duplicated("candidate_id", keep=False)
        ).sum()
    )
    # A legacy dual-write can contain both an observed and selected view of the
    # same immutable candidate.  Probability and lineage are identical; retain
    # the selected view when present without changing the opportunity universe.
    frame = frame.drop_duplicates("candidate_dedupe_key", keep="last").reset_index(
        drop=True
    )
    frame["artifact_identity_match"] = frame["model_artifact_sha256"].eq(
        expected_artifact_sha256
    )
    frame["causal_clock"] = (
        frame["source_available_at_utc"].notna()
        & frame["decision_ts_utc"].notna()
        & frame["source_available_at_utc"].le(frame["decision_ts_utc"])
    )
    frame["is_raw_checkpoint"] = True
    previous = frame.groupby("target_date", sort=False)["bracket"].shift()
    frame["is_raw_transition"] = previous.isna() | frame["bracket"].ne(previous)
    frame["is_raw_state_entry"] = ~frame.duplicated(
        ["target_date", "bracket"], keep="first"
    )
    identity = {
        "requested_path": str(path),
        "realpath": str(realpath),
        "device": int(snapshot_stat.st_dev),
        "inode": int(snapshot_stat.st_ino),
        "size_bytes": snapshot_size,
        "mtime_utc": datetime.fromtimestamp(
            snapshot_stat.st_mtime, tz=UTC
        ).isoformat(),
        "sha256": snapshot_hash.hexdigest(),
        "snapshot_policy": "bounded_to_open_time_size",
        "incomplete_tail_ignored": incomplete_tail_ignored,
    }
    identity.update(
        {
            "journal_rows": journal_rows,
            "busan_window_journal_rows": city_window_rows,
            "duplicate_candidate_rows": duplicate_candidate_rows,
            "missing_candidate_id_rows": int(
                (~frame["candidate_identity_valid"]).sum()
            ),
            "missing_checkpoint_input_ref_rows": int(
                (~frame["checkpoint_input_ref_valid"]).sum()
            ),
            "unique_candidate_rows": int(len(frame)),
            "target_dates": int(frame["target_date"].nunique()),
            "expected_artifact_sha256": expected_artifact_sha256,
            "artifact_mismatch_rows": int((~frame["artifact_identity_match"]).sum()),
        }
    )
    return frame, identity


def attach_busan_checkpoint_clocks(
    candidates: pd.DataFrame, checkpoints: pd.DataFrame
) -> pd.DataFrame:
    """Attach raw checkpoint clocks by the immutable checkpoint input ref.

    The legacy decision bundle carries a back-dated runtime decision clock for
    some rows.  We preserve that value, compute an explicitly diagnostic
    evidence-availability lower bound, and never let the corrected clock turn a
    runtime clock violation into admission evidence.
    """

    required = {
        "source_event_key",
        "source_observation_ts_utc",
        "source_first_seen_ts_utc",
        "source_available_at_utc",
        "feature_book_available_at_utc",
        "forecast_available_at_utc",
        "collector_emitted_at_utc",
        "decision_ts_utc",
        "raw_ref",
        "book_status",
        "source_to_book_lag_seconds",
        "feature_book_snapshot_id",
        "condition_id",
        "no_token_id",
    }
    missing = sorted(required - set(checkpoints.columns))
    if missing:
        raise ValueError(f"Busan checkpoint join missing columns: {missing}")
    raw = checkpoints[
        [
            "source_event_key",
            "source_observation_ts_utc",
            "source_first_seen_ts_utc",
            "source_available_at_utc",
            "feature_book_available_at_utc",
            "forecast_available_at_utc",
            "collector_emitted_at_utc",
            "decision_ts_utc",
            "raw_ref",
            "book_status",
            "source_to_book_lag_seconds",
            "feature_book_snapshot_id",
            "condition_id",
            "no_token_id",
        ]
    ].copy()
    raw = raw.drop_duplicates("source_event_key", keep="first")
    raw = raw.rename(
        columns={
            column: f"checkpoint_{column}"
            for column in raw.columns
            if column != "source_event_key"
        }
    )
    bundle = candidates.rename(
        columns={
            "source_observation_ts_utc": "bundle_source_observation_ts_utc",
            "source_first_seen_ts_utc": "bundle_source_first_seen_ts_utc",
            "source_available_at_utc": "bundle_source_available_at_utc",
            "decision_ts_utc": "runtime_decision_ts_utc",
            "raw_ref": "bundle_raw_ref",
        }
    )
    joined = bundle.merge(
        raw,
        on="source_event_key",
        how="left",
        validate="many_to_one",
    )
    joined["checkpoint_lineage_match"] = joined[
        "checkpoint_raw_ref"
    ].notna()
    for column in (
        "source_observation_ts_utc",
        "source_first_seen_ts_utc",
        "source_available_at_utc",
        "feature_book_available_at_utc",
        "forecast_available_at_utc",
        "collector_emitted_at_utc",
        "decision_ts_utc",
    ):
        source_column = (
            "runtime_decision_ts_utc"
            if column == "decision_ts_utc"
            else f"checkpoint_{column}"
        )
        joined[column] = _timestamp_column(joined, source_column)
    evidence_columns = [
        "runtime_decision_ts_utc",
        "checkpoint_decision_ts_utc",
        "checkpoint_feature_book_available_at_utc",
        "checkpoint_forecast_available_at_utc",
        "checkpoint_collector_emitted_at_utc",
    ]
    evidence_clocks = pd.concat(
        [_timestamp_column(joined, column) for column in evidence_columns], axis=1
    )
    evidence_clocks.columns = evidence_columns
    joined["evaluation_decision_ts_utc"] = evidence_clocks.max(axis=1)
    observation = joined["source_observation_ts_utc"]
    first_seen = joined["source_first_seen_ts_utc"]
    available = joined["source_available_at_utc"]
    book = joined["feature_book_available_at_utc"]
    forecast = joined["forecast_available_at_utc"]
    collector = joined["collector_emitted_at_utc"]
    runtime_decision = joined["runtime_decision_ts_utc"]
    evaluation_decision = joined["evaluation_decision_ts_utc"]
    joined["clock_lineage_complete"] = pd.concat(
        [
            observation,
            first_seen,
            available,
            book,
            forecast,
            collector,
            runtime_decision,
        ],
        axis=1,
    ).notna().all(axis=1)
    prefix_order_valid = (
        observation.le(first_seen)
        & first_seen.le(available)
        & available.le(book)
        & book.le(collector)
        & forecast.le(collector)
    )
    joined["runtime_four_clock_valid"] = (
        joined["clock_lineage_complete"]
        & prefix_order_valid
        & book.le(runtime_decision)
        & forecast.le(runtime_decision)
        & collector.le(runtime_decision)
    )
    joined["evaluation_four_clock_valid"] = (
        joined["clock_lineage_complete"]
        & prefix_order_valid
        & book.le(evaluation_decision)
        & forecast.le(evaluation_decision)
        & collector.le(evaluation_decision)
    )
    joined["runtime_decision_backdated"] = (
        joined["clock_lineage_complete"]
        & (
            book.gt(runtime_decision)
            | forecast.gt(runtime_decision)
            | collector.gt(runtime_decision)
        )
    )
    joined["causal_clock"] = joined["runtime_four_clock_valid"]
    joined["raw_book_status"] = joined["checkpoint_book_status"]
    joined["raw_source_to_book_lag_seconds"] = pd.to_numeric(
        joined["checkpoint_source_to_book_lag_seconds"], errors="coerce"
    )
    joined["raw_feature_book_snapshot_id"] = joined[
        "checkpoint_feature_book_snapshot_id"
    ]
    joined["raw_condition_id"] = joined["checkpoint_condition_id"]
    joined["raw_no_token_id"] = joined["checkpoint_no_token_id"]
    joined["raw_checkpoint_ref"] = joined["checkpoint_raw_ref"]
    joined = joined.sort_values(
        [
            "target_date",
            "evaluation_decision_ts_utc",
            "bracket",
            "candidate_dedupe_key",
        ],
        kind="stable",
    ).reset_index(drop=True)
    joined["is_raw_checkpoint"] = True
    previous = joined.groupby("target_date", sort=False)["bracket"].shift()
    joined["is_raw_transition"] = previous.isna() | joined["bracket"].ne(previous)
    joined["is_raw_state_entry"] = ~joined.duplicated(
        ["target_date", "bracket"], keep="first"
    )
    return joined


def load_condition_settlements(
    db_path: Path,
    *,
    condition_ids: Sequence[str],
    connection: sqlite3.Connection | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load exact condition settlements; city/date/bracket fallback is forbidden."""

    wanted = sorted({str(value) for value in condition_ids if str(value)})
    rows: list[tuple[Any, ...]] = []
    owns_connection = connection is None
    conn = connection or sqlite3.connect(
        f"file:{db_path}?mode=ro", uri=True, timeout=2.0
    )
    if owns_connection:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=2000")
    for offset in range(0, len(wanted), 400):
        chunk = wanted[offset : offset + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows.extend(
            conn.execute(
                f"""
                SELECT target_date, condition_id, market_id, bracket,
                       final_price, settlement_status, created_at_utc
                FROM settlements
                WHERE condition_id IN ({placeholders})
                  AND settlement_status='settled'
                ORDER BY condition_id
                """,
                chunk,
            ).fetchall()
        )
    fact_build = conn.execute(
        "SELECT MAX(fact_built_at_utc) FROM fact_signal_candidates"
    ).fetchone()[0]
    if owns_connection:
        conn.close()
    columns = [
        "settlement_target_date",
        "condition_id",
        "settlement_market_id",
        "settlement_bracket",
        "final_yes_price",
        "settlement_status",
        "settlement_created_at_utc",
    ]
    frame = pd.DataFrame(rows, columns=columns)
    if not frame.empty:
        conflicting = frame.groupby("condition_id").filter(
            lambda group: group[
                ["settlement_target_date", "settlement_bracket", "final_yes_price"]
            ].drop_duplicates().shape[0]
            > 1
        )
        if not conflicting.empty:
            raise RuntimeError(
                "conflicting condition settlements: "
                + ",".join(sorted(conflicting["condition_id"].unique())[:5])
            )
        frame = frame.drop_duplicates("condition_id", keep="last")
        final_yes = pd.to_numeric(frame["final_yes_price"], errors="coerce")
        frame["settlement_binary_label_valid"] = (
            final_yes.notna()
            & (
                np.isclose(final_yes, 0.0, atol=1e-9)
                | np.isclose(final_yes, 1.0, atol=1e-9)
            )
        )
        frame["label_no"] = np.where(
            frame["settlement_binary_label_valid"], 1.0 - final_yes, np.nan
        )
    else:
        frame["settlement_binary_label_valid"] = pd.Series(dtype=bool)
        frame["label_no"] = pd.Series(dtype=float)
    realpath = db_path.resolve()
    stat = realpath.stat()
    identity = {
        "requested_path": str(db_path),
        "realpath": str(realpath),
        "device": int(stat.st_dev),
        "inode": int(stat.st_ino),
        "size_bytes": int(stat.st_size),
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        "fact_build_at_utc": fact_build,
        "requested_condition_ids": len(wanted),
        "settled_condition_ids": int(len(frame)),
        "valid_binary_label_condition_ids": int(
            frame.get(
                "settlement_binary_label_valid", pd.Series(dtype=bool)
            ).sum()
        ),
        "invalid_binary_label_condition_ids": int(
            (
                ~frame.get(
                    "settlement_binary_label_valid", pd.Series(dtype=bool)
                )
            ).sum()
        ),
        "settlement_evidence_sha256": stable_hash([list(row) for row in rows]),
        "join_key": "condition_id_only",
    }
    return frame.reset_index(drop=True), identity


def _score_binary_grains(
    frame: pd.DataFrame,
    *,
    bootstrap_draws: int,
    seed: int,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for index, (grain, membership_column) in enumerate(
        (
            ("checkpoint", "is_raw_checkpoint"),
            ("transition", "is_raw_transition"),
            ("state_entry", "is_raw_state_entry"),
        )
    ):
        local = frame[frame[membership_column].astype(bool)].copy()
        if local.empty:
            output[grain] = {"status": "empty"}
            continue
        market = local["market_p_no"].to_numpy(float)
        candidate = local["model_p_no"].to_numpy(float)
        label = local["label_no"].to_numpy(float)
        output[grain] = {
            "status": "ok",
            "rows": int(len(local)),
            "target_dates": int(local["target_date"].nunique()),
            "scores": {
                "market": binary_score(local, market, label_column="label_no"),
                "candidate": binary_score(local, candidate, label_column="label_no"),
            },
            "candidate_minus_market": {
                metric: date_block_bootstrap_delta(
                    local,
                    binary_loss_values(label, candidate, metric=metric),
                    binary_loss_values(label, market, metric=metric),
                    draws=bootstrap_draws,
                    seed=seed + index * 10 + metric_index,
                )
                for metric_index, metric in enumerate(("brier", "logloss"))
            },
        }
    return output


def _attach_execution_evidence_status(
    frame: pd.DataFrame, *, fresh_book_seconds: float
) -> pd.DataFrame:
    output = frame.copy()
    executable_cost = pd.to_numeric(output["executable_cost"], errors="coerce")
    source_to_book = pd.to_numeric(
        output["raw_source_to_book_lag_seconds"], errors="coerce"
    )
    output["five_share_sweep_available"] = executable_cost.between(
        0.0, 1.0, inclusive="both"
    )
    output["execution_evidence_status"] = np.select(
        [
            output["same_row_score_status"].ne("scorable"),
            output["side"].str.upper().ne("NO"),
            output["market_evidence_status"].ne("available"),
            output["raw_book_status"].ne("scorable"),
            ~source_to_book.between(
                0.0, fresh_book_seconds, inclusive="both"
            ),
            output["condition_id"].astype(str).str.strip().eq(""),
            output["token_id"].astype(str).str.strip().eq(""),
            output["feature_book_snapshot_id"].astype(str).str.strip().eq(""),
            output["execution_book_snapshot_id"].astype(str).str.strip().eq(""),
            ~output["five_share_sweep_available"],
        ],
        [
            "score_row_not_scorable",
            "side_not_no",
            "market_evidence_not_available",
            "raw_exact_book_not_scorable",
            "raw_feature_book_not_fresh",
            "condition_id_missing",
            "token_id_missing",
            "feature_book_snapshot_id_missing",
            "execution_book_snapshot_id_missing",
            "five_share_sweep_missing_or_invalid",
        ],
        default="executable",
    )
    return output


def _replay_binary(
    frame: pd.DataFrame,
    *,
    probability_column: str,
) -> pd.DataFrame:
    work = frame[
        frame["is_raw_state_entry"].astype(bool)
        & frame["execution_evidence_status"].eq("executable")
    ].copy()
    if work.empty:
        return work
    work["edge"] = work[probability_column] - work["executable_cost"]
    work = work[work["edge"].gt(0.0)].copy()
    work = work.sort_values(
        ["target_date", "decision_ts_utc", "candidate_id"], kind="stable"
    ).drop_duplicates(["target_date", "bracket"], keep="first")
    work["shares"] = REPLAY_SHARES
    work["cost_usd"] = work["shares"] * work["executable_cost"]
    work["pnl_usd"] = work["shares"] * work["label_no"] - work["cost_usd"]
    return work.reset_index(drop=True)


def _summarize_binary_trades(
    trades: pd.DataFrame, *, bootstrap_draws: int, seed: int
) -> dict[str, Any]:
    if trades.empty:
        return {
            "signals": 0,
            "target_dates": 0,
            "wins": 0,
            "cost_usd": 0.0,
            "pnl_usd": 0.0,
            "roi": None,
            "roi_ci95": None,
        }
    daily = trades.groupby("target_date", sort=True)[["pnl_usd", "cost_usd"]].sum()
    values = daily.to_numpy(float)
    rng = np.random.default_rng(seed)
    sampled = values[
        rng.integers(0, len(values), size=(bootstrap_draws, len(values)))
    ].sum(axis=1)
    roi = sampled[:, 0] / sampled[:, 1]
    cost = float(trades["cost_usd"].sum())
    pnl = float(trades["pnl_usd"].sum())
    return {
        "signals": int(len(trades)),
        "target_dates": int(trades["target_date"].nunique()),
        "wins": int(trades["label_no"].sum()),
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost,
        "roi_ci95": [float(value) for value in np.quantile(roi, [0.025, 0.975])],
    }


def _pnl_uplift(
    candidate: pd.DataFrame,
    baseline: pd.DataFrame,
    *,
    target_dates: Sequence[str],
    bootstrap_draws: int,
    seed: int,
) -> dict[str, Any]:
    dates = pd.Index(sorted({str(value) for value in target_dates}), name="target_date")
    if dates.empty:
        return {"status": "empty"}
    candidate_daily = (
        candidate.groupby("target_date")["pnl_usd"].sum()
        if not candidate.empty
        else pd.Series(dtype=float)
    )
    baseline_daily = (
        baseline.groupby("target_date")["pnl_usd"].sum()
        if not baseline.empty
        else pd.Series(dtype=float)
    )
    delta = (
        candidate_daily.reindex(dates, fill_value=0.0)
        - baseline_daily.reindex(dates, fill_value=0.0)
    ).to_numpy(float)
    rng = np.random.default_rng(seed)
    sampled = rng.choice(delta, size=(bootstrap_draws, len(delta)), replace=True).sum(
        axis=1
    )
    return {
        "status": "ok",
        "target_dates": int(len(dates)),
        "candidate_minus_baseline_pnl_usd": float(delta.sum()),
        "ci95": [float(value) for value in np.quantile(sampled, [0.025, 0.975])],
        "daily_mean_pnl_uplift_usd": float(delta.mean()),
    }


def _seoul_artifact_status(
    artifact: Mapping[str, Any],
    *,
    artifact_path: Path,
    physical_states_path: Path,
    start_date: str,
) -> dict[str, Any]:
    if artifact.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise RuntimeError("Seoul frozen artifact schema mismatch")
    if artifact.get("city") != "Seoul":
        raise RuntimeError("Seoul frozen artifact city mismatch")
    if tuple(artifact.get("classes") or ()) != CLASSES:
        raise RuntimeError("Seoul frozen artifact class lattice mismatch")
    if tuple(artifact.get("features") or ()) != PHYSICAL_FEATURES:
        raise RuntimeError("Seoul frozen artifact feature schema mismatch")
    boundary = str(artifact.get("clean_forward_boundary") or "")
    if not boundary or start_date < boundary:
        raise RuntimeError(
            f"Seoul forward start {start_date} precedes frozen boundary {boundary}"
        )
    input_exists = physical_states_path.is_file()
    physical_identity = None
    physical_hash_matches = False
    if input_exists:
        physical_identity = _file_identity(physical_states_path)
        physical_hash_matches = physical_identity["sha256"] == artifact.get(
            "physical_input_sha256"
        )
    return {
        "artifact": _file_identity(artifact_path),
        "clean_forward_boundary": boundary,
        "physical_train_cutoff": artifact.get("physical_train_cutoff"),
        "historical_holdout_end": artifact.get("historical_holdout_end"),
        "selected_market_power": float(artifact["selected_market_power"]),
        "selected_weather_likelihood_ratio_weight": float(
            artifact["selected_market_likelihood_ratio_weight"]
        ),
        "runtime_eligible": bool(artifact.get("runtime_eligible")),
        "live_eligible": bool(artifact.get("live_eligible")),
        "physical_training_input": {
            "path": str(physical_states_path),
            "exists": input_exists,
            "identity": physical_identity,
            "hash_matches_frozen_artifact": physical_hash_matches,
            "fresh_refit_status": (
                "reproducible"
                if input_exists and physical_hash_matches
                else "blocked_missing_or_identity_mismatched_training_input"
            ),
        },
    }


def evaluate_seoul_forward(
    *,
    config: KoreaAirportForwardConfig,
    checkpoint_root: Path,
    market_batches_root: Path,
    db_path: Path,
    artifact_path: Path,
    physical_states_path: Path,
    output_dir: Path,
    winner_evidence: tuple[Mapping[str, str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    artifact = joblib.load(artifact_path)
    if not isinstance(artifact, dict):
        raise RuntimeError("Seoul frozen artifact must be a mapping")
    artifact_status = _seoul_artifact_status(
        artifact,
        artifact_path=artifact_path,
        physical_states_path=physical_states_path,
        start_date=config.start_date,
    )
    if winner_evidence is None:
        winners, db_identity = load_settlement_winners(
            db_path,
            city="Seoul",
            start_date=config.start_date,
            end_date=config.end_date,
        )
    else:
        winners, db_identity = winner_evidence
    checkpoints, checkpoint_identity = load_checkpoint_frame(
        checkpoint_root,
        city="Seoul",
        start_date=config.start_date,
        end_date=config.end_date,
        winners=winners,
    )
    clock_audit = _clock_audit(checkpoints)
    basis_summary, basis_rows = build_source_basis_audit(
        checkpoints,
        city="Seoul",
        station_id="RKSI",
        fresh_book_seconds=config.fresh_book_seconds,
    )
    amos = _amos_feature_frame(checkpoints)
    labeled = amos[amos["label_index"].notna()].copy()
    labeled["label_index"] = labeled["label_index"].astype(int)
    market_snapshots, market_identity = load_full_ladder_snapshots(
        market_batches_root,
        city="Seoul",
        start_date=config.start_date,
        end_date=config.end_date,
    )
    joined = join_market_decision_panel(
        labeled, market_snapshots, max_source_age_minutes=5
    )
    scorable = joined[
        joined["market_join_status"].eq("scorable")
        & joined["market_distribution"].notna()
        & joined["checkpoint_id"].notna()
    ].copy()
    if scorable.empty:
        raise RuntimeError("Seoul clean-forward has no same-row scorable market states")
    weather = _predict_aligned(artifact["physical_model"], scorable)
    market = np.stack(scorable["market_distribution"])
    posterior = calibrated_market_weather_posterior(
        market,
        weather,
        np.asarray(artifact["physical_climatology"], dtype=float),
        market_power=float(artifact["selected_market_power"]),
        weather_weight=float(
            artifact["selected_market_likelihood_ratio_weight"]
        ),
    )
    scorable["weather_distribution"] = list(weather)
    scorable["posterior_distribution"] = list(posterior)
    scorable["split"] = "clean_forward"
    scorable = attach_next_execution_ladder(
        scorable, market_snapshots, max_lag_minutes=45
    )
    scores = _score_grains(
        scorable,
        {"market": market, "weather": weather, "posterior": posterior},
        bootstrap_draws=config.bootstrap_draws,
        seed=config.seed,
        baseline_name="market",
    )
    state_membership = scorable["is_raw_state_entry"].astype(bool).to_numpy()
    state_rows = scorable.loc[state_membership].copy()
    posterior_trades = replay_distribution_signals(
        state_rows, posterior[state_membership]
    )
    market_trades = replay_distribution_signals(
        state_rows, market[state_membership]
    )
    posterior_trade_summary = summarize_distribution_trades(
        posterior_trades,
        draws=config.bootstrap_draws,
        seed=config.seed + 50,
    )
    market_trade_summary = summarize_distribution_trades(
        market_trades,
        draws=config.bootstrap_draws,
        seed=config.seed + 51,
    )
    pnl_uplift = _pnl_uplift(
        posterior_trades,
        market_trades,
        target_dates=sorted(scorable["target_date"].unique()),
        bootstrap_draws=config.bootstrap_draws,
        seed=config.seed + 52,
    )
    prediction_path = output_dir / "predictions.jsonl.gz"
    basis_path = output_dir / "source_basis_crosses.jsonl.gz"
    prediction_columns = (
        "market_decision_id",
        "checkpoint_id",
        "city",
        "target_date",
        "source_event_key",
        "source_observation_ts_utc",
        "source_first_seen_ts_utc",
        "source_available_at_utc",
        "feature_book_available_at_utc",
        "collector_emitted_at_utc",
        "decision_ts_utc",
        "routine_rung",
        "winner_bracket",
        "label_class",
        "label_index",
        "is_raw_state_entry",
        "is_raw_ten_minute_checkpoint",
        "market_distribution",
        "weather_distribution",
        "posterior_distribution",
        "market_join_status",
        "market_source_age_seconds",
        "feature_book_snapshot_id_distribution",
        "execution_quote_status",
        "execution_book_available_at_utc",
        "execution_lag_seconds",
        "execution_book_snapshot_id",
        *PHYSICAL_FEATURES,
    )
    _write_jsonl_gz(_frame_records(scorable, prediction_columns), prediction_path)
    basis_columns = (
        "city",
        "target_date",
        "source_event_key",
        "source_observation_ts_utc",
        "source_first_seen_ts_utc",
        "source_available_at_utc",
        "feature_book_available_at_utc",
        "collector_emitted_at_utc",
        "decision_ts_utc",
        "routine_rung",
        "winner_bracket",
        "basis_anchor",
        "source_running_max_c",
        "source_minus_settlement_c",
        "basis_status",
        "fresh_executable",
        "book_status",
        "source_to_book_lag_seconds",
        "feature_book_snapshot_id",
        "raw_ref",
    )
    _write_jsonl_gz(_frame_records(basis_rows, basis_columns), basis_path)
    primary = scores.get("state_entry", {})
    delta = primary.get("candidate_minus_baseline", {}).get("posterior", {})
    proper_pass = bool(delta) and all(
        float(delta[metric]["ci_high"]) < 0.0
        for metric in ("brier", "logloss", "rps")
    )
    primary_dates = int(primary.get("target_dates") or 0)
    date_gate = primary_dates >= config.minimum_forward_dates
    execution_gate = bool(
        pnl_uplift.get("status") == "ok"
        and pnl_uplift.get("ci95")
        and float(pnl_uplift["ci95"][0]) > 0.0
    )
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "city": "Seoul",
        "station_id": "RKSI",
        "model_id": artifact["model_id"],
        "probability_target": "settlement-native remaining heat negative/stay/+1/+2/+3+",
        "candidate_grain_version": "seoul_remaining_heat_state_entry_v1",
        "forward_window": [config.start_date, config.end_date],
        "identity": {
            "frozen_model": artifact_status,
            "checkpoints": checkpoint_identity,
            "market": market_identity,
            "db": db_identity,
        },
        "clock_audit": clock_audit,
        "source_basis": basis_summary,
        "signal_funnel": {
            "raw_city_checkpoint_rows": checkpoint_identity["raw_city_rows"],
            "unique_point_group_events": checkpoint_identity[
                "unique_checkpoint_rows"
            ],
            "local_09_18_rows": int(len(amos)),
            "settled_feature_rows": int(len(labeled)),
            "raw_state_entries": int(labeled["is_raw_state_entry"].sum()),
            "posterior_expression_signals": int(len(posterior_trades)),
        },
        "evidence_funnel": {
            "pit_source_rows": int(len(amos)),
            "complete_ladder_decision_rows": int(len(joined)),
            "same_row_scorable_rows": int(len(scorable)),
            "same_row_scorable_dates": int(scorable["target_date"].nunique()),
            "state_entry_rows": int(scorable["is_raw_state_entry"].sum()),
            "settled_state_entry_dates": primary_dates,
            "next_ladder_executable_rows": int(
                scorable["execution_quote_status"]
                .eq("executable_next_full_ladder")
                .sum()
            ),
            "actual_fills": 0,
        },
        "market_coverage_by_status": {
            str(key): int(value)
            for key, value in joined["market_join_status"].value_counts().items()
        },
        "proper_scores": scores,
        "fee_adjusted_expression": {
            "entry_clock": "next independently captured complete ladder",
            "entry_price": "direct selected-side ask",
            "shares": REPLAY_SHARES,
            "fee_rate": 0.05,
            "posterior": posterior_trade_summary,
            "same_rows_market": market_trade_summary,
            "posterior_minus_market_pnl": pnl_uplift,
            "actual_fills": 0,
        },
        "gates": {
            "minimum_clean_forward_dates": config.minimum_forward_dates,
            "clean_forward_date_gate": "PASS" if date_gate else "FAIL",
            "same_row_market_proper_score_gate": (
                "PASS" if date_gate and proper_pass else "FAIL" if date_gate else "INSUFFICIENT"
            ),
            "fee_pnl_uplift_gate": (
                "PASS" if date_gate and execution_gate else "FAIL" if date_gate else "INSUFFICIENT"
            ),
            "actual_fill_gate": "FAIL",
        },
        "artifacts": {
            "predictions_path": str(prediction_path),
            "predictions_sha256": sha256_file(prediction_path),
            "source_basis_path": str(basis_path),
            "source_basis_sha256": sha256_file(basis_path),
        },
        "decision": {
            "conclusion": "inconclusive_clean_forward_insufficient_or_gate_failed",
            "action": (
                "keep Seoul coverage-only; retain the frozen model score-only, "
                "restore the hash-matched physical training input before any refit, "
                "and wait for at least 30 clean settled dates"
            ),
            "runtime_change": "none",
            "live_change": "none",
        },
    }


def evaluate_busan_forward(
    *,
    config: KoreaAirportForwardConfig,
    checkpoint_root: Path,
    db_path: Path,
    artifact_path: Path,
    decision_bundles_path: Path,
    output_dir: Path,
    winner_evidence: tuple[Mapping[str, str], Mapping[str, Any]] | None = None,
    candidate_evidence: tuple[pd.DataFrame, Mapping[str, Any]] | None = None,
    settlement_evidence: tuple[pd.DataFrame, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    artifact_identity = _file_identity(artifact_path)
    artifact = joblib.load(artifact_path)
    if not isinstance(artifact, dict):
        raise RuntimeError("Busan frozen artifact must be a mapping")
    if artifact.get("schema_version") != RUNTIME_ARTIFACT_SCHEMA_VERSION:
        raise RuntimeError("Busan frozen artifact schema mismatch")
    if artifact.get("city") != "Busan" or not (artifact.get("parity") or {}).get(
        "pass"
    ):
        raise RuntimeError("Busan frozen artifact identity/parity mismatch")
    trained_through = str(artifact.get("expression_weight_trained_through") or "")
    if not trained_through or config.start_date <= trained_through:
        raise RuntimeError(
            "Busan forward start must be after expression_weight_trained_through"
        )
    if winner_evidence is None:
        winners, winner_db_identity = load_settlement_winners(
            db_path,
            city="Busan",
            start_date=config.start_date,
            end_date=config.end_date,
        )
    else:
        winners, winner_db_identity = winner_evidence
    checkpoints, checkpoint_identity = load_checkpoint_frame(
        checkpoint_root,
        city="Busan",
        start_date=config.start_date,
        end_date=config.end_date,
        winners=winners,
    )
    checkpoint_clock_audit = _clock_audit(checkpoints)
    basis_summary, basis_rows = build_source_basis_audit(
        checkpoints,
        city="Busan",
        station_id="RKPK",
        fresh_book_seconds=config.fresh_book_seconds,
    )
    if candidate_evidence is None:
        candidates, journal_identity = load_busan_decision_bundles(
            decision_bundles_path,
            start_date=config.start_date,
            end_date=config.end_date,
            expected_artifact_sha256=artifact_identity["sha256"],
        )
    else:
        candidate_frame, candidate_identity = candidate_evidence
        candidates = candidate_frame.copy()
        journal_identity = dict(candidate_identity)
    candidates = attach_busan_checkpoint_clocks(candidates, checkpoints)
    runtime_clock_audit = _clock_audit(
        candidates, decision_column="runtime_decision_ts_utc"
    )
    evaluation_clock_audit = _clock_audit(
        candidates, decision_column="evaluation_decision_ts_utc"
    )
    if settlement_evidence is None:
        settlements, settlement_identity = load_condition_settlements(
            db_path,
            condition_ids=candidates["condition_id"].tolist(),
        )
    else:
        settlement_frame, settlement_input_identity = settlement_evidence
        settlements = settlement_frame.copy()
        settlement_identity = dict(settlement_input_identity)
    joined = candidates.merge(settlements, on="condition_id", how="left")
    joined["settlement_identity_match"] = (
        joined["settlement_target_date"].eq(joined["target_date"])
        & joined["settlement_bracket"].astype(str).eq(joined["bracket"].astype(str))
    )
    joined["probability_complete"] = (
        joined["market_p_no"].between(0.0, 1.0, inclusive="both")
        & joined["model_p_no"].between(0.0, 1.0, inclusive="both")
    )
    joined["same_row_score_status"] = np.select(
        [
            ~joined["candidate_identity_valid"],
            ~joined["checkpoint_input_ref_valid"],
            ~joined["checkpoint_lineage_match"],
            ~joined["artifact_identity_match"],
            ~joined["runtime_four_clock_valid"],
            joined["candidate_status"].ne("scored"),
            ~joined["probability_complete"],
            joined["settlement_target_date"].isna(),
            ~joined["settlement_identity_match"],
            ~joined["settlement_binary_label_valid"].eq(True),
        ],
        [
            "candidate_identity_missing",
            "checkpoint_input_ref_missing",
            "checkpoint_lineage_missing",
            "model_artifact_mismatch",
            "runtime_four_clock_invalid",
            "candidate_not_scored",
            "probability_missing",
            "settlement_missing",
            "settlement_identity_mismatch",
            "settlement_label_not_binary",
        ],
        default="scorable",
    )
    joined["diagnostic_score_status"] = np.select(
        [
            ~joined["candidate_identity_valid"],
            ~joined["checkpoint_input_ref_valid"],
            ~joined["checkpoint_lineage_match"],
            ~joined["artifact_identity_match"],
            ~joined["evaluation_four_clock_valid"],
            joined["candidate_status"].ne("scored"),
            ~joined["probability_complete"],
            joined["settlement_target_date"].isna(),
            ~joined["settlement_identity_match"],
            ~joined["settlement_binary_label_valid"].eq(True),
        ],
        [
            "candidate_identity_missing",
            "checkpoint_input_ref_missing",
            "checkpoint_lineage_missing",
            "model_artifact_mismatch",
            "evaluation_four_clock_invalid",
            "candidate_not_scored",
            "probability_missing",
            "settlement_missing",
            "settlement_identity_mismatch",
            "settlement_label_not_binary",
        ],
        default="diagnostic_scorable_rebased_clock",
    )
    joined = _attach_execution_evidence_status(
        joined, fresh_book_seconds=config.fresh_book_seconds
    )
    scorable = joined[joined["same_row_score_status"].eq("scorable")].copy()
    diagnostic_scorable = joined[
        joined["diagnostic_score_status"].eq(
            "diagnostic_scorable_rebased_clock"
        )
    ].copy()
    if not scorable.empty:
        scorable["label_no"] = scorable["label_no"].astype(int)
    if not diagnostic_scorable.empty:
        diagnostic_scorable["label_no"] = diagnostic_scorable[
            "label_no"
        ].astype(int)
    scores = _score_binary_grains(
        scorable,
        bootstrap_draws=config.bootstrap_draws,
        seed=config.seed + 100,
    )
    diagnostic_scores = _score_binary_grains(
        diagnostic_scorable,
        bootstrap_draws=config.bootstrap_draws,
        seed=config.seed + 120,
    )
    model_trades = _replay_binary(scorable, probability_column="model_p_no")
    market_trades = _replay_binary(scorable, probability_column="market_p_no")
    model_trade_summary = _summarize_binary_trades(
        model_trades,
        bootstrap_draws=config.bootstrap_draws,
        seed=config.seed + 150,
    )
    market_trade_summary = _summarize_binary_trades(
        market_trades,
        bootstrap_draws=config.bootstrap_draws,
        seed=config.seed + 151,
    )
    pnl_uplift = _pnl_uplift(
        model_trades,
        market_trades,
        target_dates=sorted(scorable["target_date"].unique()),
        bootstrap_draws=config.bootstrap_draws,
        seed=config.seed + 152,
    )
    prediction_path = output_dir / "predictions.jsonl.gz"
    basis_path = output_dir / "source_basis_crosses.jsonl.gz"
    prediction_columns = (
        "bundle_record_id",
        "candidate_id",
        "candidate_identity_valid",
        "candidate_dedupe_key",
        "checkpoint_id",
        "trigger_event_id",
        "information_event_content_key",
        "source_event_key",
        "source_observation_ts_utc",
        "source_first_seen_ts_utc",
        "source_available_at_utc",
        "feature_book_available_at_utc",
        "collector_emitted_at_utc",
        "decision_ts_utc",
        "runtime_decision_ts_utc",
        "evaluation_decision_ts_utc",
        "runtime_four_clock_valid",
        "evaluation_four_clock_valid",
        "runtime_decision_backdated",
        "checkpoint_input_ref_valid",
        "checkpoint_lineage_match",
        "city",
        "target_date",
        "bracket",
        "condition_id",
        "market_id",
        "token_id",
        "side",
        "candidate_status",
        "blocker_reason",
        "market_evidence_status",
        "market_p_no",
        "model_p_no",
        "executable_cost",
        "would_enter",
        "edge_after_fee",
        "model_id",
        "model_artifact_sha256",
        "candidate_grain_version",
        "is_raw_checkpoint",
        "is_raw_transition",
        "is_raw_state_entry",
        "settlement_target_date",
        "settlement_bracket",
        "final_yes_price",
        "label_no",
        "settlement_binary_label_valid",
        "settlement_created_at_utc",
        "same_row_score_status",
        "diagnostic_score_status",
        "execution_evidence_status",
        "five_share_sweep_available",
        "feature_book_snapshot_id",
        "execution_book_snapshot_id",
        "raw_feature_book_snapshot_id",
        "raw_book_status",
        "raw_source_to_book_lag_seconds",
        "bundle_raw_ref",
        "raw_checkpoint_ref",
    )
    _write_jsonl_gz(_frame_records(joined, prediction_columns), prediction_path)
    basis_columns = (
        "city",
        "target_date",
        "source_event_key",
        "source_observation_ts_utc",
        "source_first_seen_ts_utc",
        "source_available_at_utc",
        "feature_book_available_at_utc",
        "collector_emitted_at_utc",
        "decision_ts_utc",
        "routine_rung",
        "winner_bracket",
        "basis_anchor",
        "source_running_max_c",
        "source_minus_settlement_c",
        "basis_status",
        "fresh_executable",
        "book_status",
        "source_to_book_lag_seconds",
        "feature_book_snapshot_id",
        "raw_ref",
    )
    _write_jsonl_gz(_frame_records(basis_rows, basis_columns), basis_path)
    primary = scores.get("state_entry", {})
    delta = primary.get("candidate_minus_market", {})
    proper_pass = bool(delta) and all(
        float(delta[metric]["ci_high"]) < 0.0
        for metric in ("brier", "logloss")
    )
    primary_dates = int(primary.get("target_dates") or 0)
    date_gate = primary_dates >= config.minimum_forward_dates
    execution_gate = bool(
        pnl_uplift.get("status") == "ok"
        and pnl_uplift.get("ci95")
        and float(pnl_uplift["ci95"][0]) > 0.0
    )
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "city": "Busan",
        "station_id": "RKPK",
        "model_id": artifact["model_id"],
        "probability_target": "final exact-rung NO settlement probability",
        "candidate_grain_version": artifact["candidate_grain_version"],
        "forward_window": [config.start_date, config.end_date],
        "identity": {
            "frozen_model": {
                "artifact": artifact_identity,
                "physical_train_end": artifact.get("physical_train_end"),
                "confirmation_train_window": [
                    artifact.get("confirmation_train_start"),
                    artifact.get("confirmation_train_end"),
                ],
                "expression_weight": float(artifact["expression_weight"]),
                "expression_weight_trained_through": trained_through,
                "parity": artifact["parity"],
            },
            "decision_journal": journal_identity,
            "checkpoints": checkpoint_identity,
            "winner_db": winner_db_identity,
            "condition_settlements": settlement_identity,
        },
        "clock_audit": {
            "raw_checkpoints": checkpoint_clock_audit,
            "runtime_candidate_decision": runtime_clock_audit,
            "diagnostic_rebased_decision": evaluation_clock_audit,
            "runtime_decision_backdated_rows": int(
                candidates["runtime_decision_backdated"].sum()
            ),
            "runtime_decision_backdated_scored_rows": int(
                (
                    candidates["runtime_decision_backdated"]
                    & candidates["candidate_status"].eq("scored")
                ).sum()
            ),
            "policy": (
                "runtime clock violations are blocked from admission scoring; "
                "rebased evidence-clock scores are diagnostic only"
            ),
        },
        "source_basis": basis_summary,
        "signal_funnel": {
            "raw_city_checkpoint_rows": checkpoint_identity["raw_city_rows"],
            "unique_point_group_events": checkpoint_identity[
                "unique_checkpoint_rows"
            ],
            "decision_journal_rows": journal_identity[
                "busan_window_journal_rows"
            ],
            "unique_candidates": int(len(candidates)),
            "mechanism_scored_candidates": int(
                candidates["candidate_status"].eq("scored").sum()
            ),
            "raw_state_entries": int(candidates["is_raw_state_entry"].sum()),
            "model_expression_signals": int(len(model_trades)),
        },
        "evidence_funnel": {
            "strict_runtime_four_clock_candidates": int(
                candidates["runtime_four_clock_valid"].sum()
            ),
            "diagnostic_rebased_four_clock_candidates": int(
                candidates["evaluation_four_clock_valid"].sum()
            ),
            "pit_book_probability_rows": int(joined["probability_complete"].sum()),
            "condition_linked_settled_rows": int(
                joined["settlement_target_date"].notna().sum()
            ),
            "same_row_scorable_rows": int(len(scorable)),
            "same_row_scorable_dates": int(scorable["target_date"].nunique()),
            "settled_state_entry_dates": primary_dates,
            "diagnostic_rebased_scorable_rows": int(len(diagnostic_scorable)),
            "diagnostic_rebased_scorable_dates": int(
                diagnostic_scorable["target_date"].nunique()
            ),
            "diagnostic_rebased_state_entry_dates": int(
                diagnostic_scores.get("state_entry", {}).get("target_dates") or 0
            ),
            "five_share_executable_state_entries": int(
                (
                    joined["is_raw_state_entry"]
                    & joined["execution_evidence_status"].eq("executable")
                ).sum()
            ),
            "actual_fills": 0,
        },
        "score_coverage_by_status": {
            str(key): int(value)
            for key, value in joined["same_row_score_status"].value_counts().items()
        },
        "diagnostic_score_coverage_by_status": {
            str(key): int(value)
            for key, value in joined["diagnostic_score_status"].value_counts().items()
        },
        "execution_coverage_by_status": {
            str(key): int(value)
            for key, value in joined["execution_evidence_status"].value_counts().items()
        },
        "proper_scores": scores,
        "diagnostic_rebased_clock_proper_scores": {
            "admission_role": "diagnostic_only",
            "decision_clock_definition": (
                "max(runtime decision, raw feature-book availability, raw collector "
                "emission); lower-bound reconstruction, not the serialized runtime clock"
            ),
            "scores": diagnostic_scores,
        },
        "fee_adjusted_expression": {
            "entry_clock": "serialized runtime decision-current 5-share sweep",
            "entry_price": "runtime effective_cost_per_share including Weather fee",
            "shares": REPLAY_SHARES,
            "candidate": model_trade_summary,
            "same_rows_market": market_trade_summary,
            "candidate_minus_market_pnl": pnl_uplift,
            "actual_fills": 0,
        },
        "gates": {
            "minimum_clean_forward_dates": config.minimum_forward_dates,
            "runtime_four_clock_gate": (
                "PASS"
                if not candidates["runtime_decision_backdated"].any()
                else "FAIL"
            ),
            "clean_forward_date_gate": "PASS" if date_gate else "FAIL",
            "same_row_market_proper_score_gate": (
                "PASS" if date_gate and proper_pass else "FAIL" if date_gate else "INSUFFICIENT"
            ),
            "fee_pnl_uplift_gate": (
                "PASS" if date_gate and execution_gate else "FAIL" if date_gate else "INSUFFICIENT"
            ),
            "actual_fill_gate": "FAIL",
        },
        "artifacts": {
            "predictions_path": str(prediction_path),
            "predictions_sha256": sha256_file(prediction_path),
            "source_basis_path": str(basis_path),
            "source_basis_sha256": sha256_file(basis_path),
        },
        "decision": {
            "conclusion": "blocked_runtime_decision_clock_lineage_and_low_sample",
            "action": (
                "keep Busan zero-notional and frozen; correct the adapter decision "
                "clock contract before collecting a new admission window; retain "
                "the rebased results as diagnostic only and do not tune on them"
            ),
            "runtime_change": "none",
            "live_change": "none",
        },
    }


def _artifact_manifest(output_dir: Path, paths: Sequence[Path]) -> dict[str, Any]:
    files = []
    for path in paths:
        stat = path.stat()
        files.append(
            {
                "path": str(path.relative_to(output_dir)),
                "size_bytes": int(stat.st_size),
                "sha256": sha256_file(path),
            }
        )
    return {
        "schema_version": "korea_airport_forward_artifact_manifest_v2",
        "artifact_root": str(output_dir),
        "files": files,
        "manifest_content_sha256": stable_hash(files),
    }


def _prepare_output_dir(output_dir: Path) -> None:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(
            f"append-only research run directory is not empty: {output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)


def run_korea_airport_forward(
    *,
    config: KoreaAirportForwardConfig,
    checkpoint_root: Path,
    market_batches_root: Path,
    db_path: Path,
    seoul_artifact_path: Path,
    seoul_physical_states_path: Path,
    busan_artifact_path: Path,
    decision_bundles_path: Path,
    output_dir: Path,
    code_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _prepare_output_dir(output_dir)
    seoul_winner_evidence = None
    busan_winner_evidence = None
    busan_candidate_evidence = None
    busan_settlement_evidence = None
    if "Busan" in config.cities:
        busan_artifact_identity = _file_identity(busan_artifact_path)
        busan_candidate_evidence = load_busan_decision_bundles(
            decision_bundles_path,
            start_date=config.start_date,
            end_date=config.end_date,
            expected_artifact_sha256=busan_artifact_identity["sha256"],
        )
    db_connection = sqlite3.connect(
        f"file:{db_path}?mode=ro", uri=True, timeout=2.0
    )
    db_connection.execute("PRAGMA query_only=ON")
    db_connection.execute("PRAGMA busy_timeout=2000")
    try:
        db_connection.execute("BEGIN")
        if "Seoul" in config.cities:
            seoul_winner_evidence = load_settlement_winners(
                db_path,
                city="Seoul",
                start_date=config.start_date,
                end_date=config.end_date,
                connection=db_connection,
            )
        if "Busan" in config.cities:
            assert busan_candidate_evidence is not None
            busan_winner_evidence = load_settlement_winners(
                db_path,
                city="Busan",
                start_date=config.start_date,
                end_date=config.end_date,
                connection=db_connection,
            )
            busan_settlement_evidence = load_condition_settlements(
                db_path,
                condition_ids=busan_candidate_evidence[0]["condition_id"].tolist(),
                connection=db_connection,
            )
        db_connection.rollback()
    finally:
        if db_connection.in_transaction:
            db_connection.rollback()
        db_connection.close()
    fact_builds = {
        str(identity.get("fact_build_at_utc") or "")
        for evidence in (
            seoul_winner_evidence,
            busan_winner_evidence,
            busan_settlement_evidence,
        )
        if evidence is not None
        for identity in (evidence[1],)
    }
    if len(fact_builds) != 1 or "" in fact_builds:
        raise RuntimeError(
            f"canonical DB snapshot mixed or missing fact builds: {sorted(fact_builds)}"
        )
    db_snapshot_identity = {
        "policy": "single_sqlite_read_transaction_before_model_evaluation",
        "fact_build_at_utc": next(iter(fact_builds)),
        "evidence_reads": [
            name
            for name, evidence in (
                ("seoul_tmax_winners", seoul_winner_evidence),
                ("busan_tmax_winners", busan_winner_evidence),
                ("busan_condition_settlements", busan_settlement_evidence),
            )
            if evidence is not None
        ],
    }
    city_results: dict[str, Any] = {}
    artifact_paths: list[Path] = []
    if "Seoul" in config.cities:
        seoul_dir = output_dir / "city=seoul"
        seoul_dir.mkdir(parents=True, exist_ok=True)
        city_results["Seoul"] = evaluate_seoul_forward(
            config=config,
            checkpoint_root=checkpoint_root,
            market_batches_root=market_batches_root,
            db_path=db_path,
            artifact_path=seoul_artifact_path,
            physical_states_path=seoul_physical_states_path,
            output_dir=seoul_dir,
            winner_evidence=seoul_winner_evidence,
        )
        city_path = seoul_dir / "summary.json"
        city_path.write_text(
            json.dumps(
                _json_safe(city_results["Seoul"]),
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        artifact_paths.extend(
            [
                city_path,
                Path(city_results["Seoul"]["artifacts"]["predictions_path"]),
                Path(city_results["Seoul"]["artifacts"]["source_basis_path"]),
            ]
        )
    if "Busan" in config.cities:
        busan_dir = output_dir / "city=busan"
        busan_dir.mkdir(parents=True, exist_ok=True)
        city_results["Busan"] = evaluate_busan_forward(
            config=config,
            checkpoint_root=checkpoint_root,
            db_path=db_path,
            artifact_path=busan_artifact_path,
            decision_bundles_path=decision_bundles_path,
            output_dir=busan_dir,
            winner_evidence=busan_winner_evidence,
            candidate_evidence=busan_candidate_evidence,
            settlement_evidence=busan_settlement_evidence,
        )
        city_path = busan_dir / "summary.json"
        city_path.write_text(
            json.dumps(
                _json_safe(city_results["Busan"]),
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        artifact_paths.extend(
            [
                city_path,
                Path(city_results["Busan"]["artifacts"]["predictions_path"]),
                Path(city_results["Busan"]["artifacts"]["source_basis_path"]),
            ]
        )
    observed_at = datetime.now(tz=UTC).isoformat()
    summary = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "observed_at_utc": observed_at,
        "hypothesis": (
            "Within the CrossNO opportunity family and each city's strict "
            "first-seen/PIT clocks, a probabilistic AMOS path model improves the "
            "same-row market exact-bracket probability; this tests a model "
            "challenger to the incumbent rule-only source-cross decision, while "
            "Seoul and Busan are never pooled for fit, basis, or verdict."
        ),
        "strategy_lineage": crossno_model_branch_identity(config.cities),
        "config": {
            "cities": list(config.cities),
            "start_date": config.start_date,
            "end_date": config.end_date,
            "bootstrap_draws": config.bootstrap_draws,
            "seed": config.seed,
            "minimum_forward_dates": config.minimum_forward_dates,
            "fresh_book_seconds": config.fresh_book_seconds,
        },
        "code_identity": dict(code_identity or {}),
        "canonical_db_snapshot": db_snapshot_identity,
        "shared_contract": {
            "source": "AMOS airport runway point-group first-seen",
            "stations": {"Seoul": "RKSI", "Busan": "RKPK"},
            "event_delivery_clocks": [
                "source_observation_ts_utc",
                "source_first_seen_ts_utc",
                "source_available_at_utc",
                "collector_emitted_at_utc",
            ],
            "feature_decision_clocks": [
                "feature_book_available_at_utc",
                "decision_ts_utc",
            ],
            "strict_clock_rule": (
                "every consumed input must be available at or before the serialized "
                "decision; corrected/rebased clocks are diagnostic only"
            ),
            "market_role": "same-row prior/baseline; execution quote kept separate",
            "settlement_role": "post-decision label only",
            "pooling": "forbidden_between_cities",
            "runtime_or_live_change": "none",
        },
        "cities": city_results,
        "combined_decision": {
            "conclusion": "city_specific_evidence_only_no_pooled_admission",
            "action": (
                "preserve Busan zero-notional and Seoul coverage-only collection; "
                "repair Busan's serialized decision-clock contract before starting "
                "a new admission window; do not tune on this forward window; reassess "
                "each city only after its own 30 settled clean-forward dates and all "
                "probability, fee, source-basis, and actual-execution gates; before "
                "claiming an upgrade, run a same-opportunity paired replay against "
                "the incumbent CrossNO rule"
            ),
            "live_change": "none",
        },
    }
    summary = _json_safe(summary)
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    artifact_paths.append(summary_path)
    manifest = _artifact_manifest(output_dir, artifact_paths)
    manifest_path = output_dir / "artifact_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return {
        **summary,
        "summary_path": str(summary_path),
        "summary_sha256": sha256_file(summary_path),
        "artifact_manifest_path": str(manifest_path),
        "artifact_manifest_sha256": sha256_file(manifest_path),
    }


__all__ = [
    "BUSAN_GRAIN_VERSION",
    "CROSSNO_CITY_MODEL_IDS",
    "CROSSNO_MODEL_PREDECESSOR",
    "CROSSNO_PARENT_RUNTIME_STRATEGY_ID",
    "CROSSNO_PARENT_STRATEGY_FAMILY",
    "DEFAULT_FRESH_BOOK_SECONDS",
    "DEFAULT_MIN_FORWARD_DATES",
    "KoreaAirportForwardConfig",
    "OUTPUT_SCHEMA_VERSION",
    "build_source_basis_audit",
    "crossno_model_branch_identity",
    "evaluate_busan_forward",
    "evaluate_seoul_forward",
    "load_busan_decision_bundles",
    "load_condition_settlements",
    "run_korea_airport_forward",
]
