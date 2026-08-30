"""Busan pending-confirmation market-prior adapter.

The adapter consumes immutable Korea AMOS checkpoints and a parity-locked
composite artifact.  It never reads settlement labels and never calls a market
or weather network endpoint.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd

from weather_model_evaluation.busan_market_prior import (
    RUNTIME_ARTIFACT_SCHEMA_VERSION,
    logit_shrunk_probability,
)
from weather_clock_contract import parse_utc_or_none

from .core import CityScore, InputNotReady


SEOUL_TZ = ZoneInfo("Asia/Seoul")
CHECKPOINT_SCHEMA_VERSION = "korea_amos_first_seen_state_v1"


def _parse_utc(value: Any) -> datetime | None:
    return parse_utc_or_none(value, field="busan_probability_timestamp")


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _tail_rows(root: Path, *, max_bytes: int) -> list[dict[str, Any]]:
    paths = [root] if root.is_file() else sorted(root.glob("*.jsonl"), reverse=True)
    remaining = max_bytes
    chunks: list[list[dict[str, Any]]] = []
    for path in paths:
        if remaining <= 0 or not path.is_file():
            break
        size = path.stat().st_size
        read_bytes = min(size, remaining)
        with path.open("rb") as handle:
            offset = max(0, size - read_bytes)
            handle.seek(offset)
            if offset:
                handle.readline()
            payload = handle.read()
        rows: list[dict[str, Any]] = []
        for raw in payload.splitlines():
            try:
                row = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(row, dict):
                rows.append(row)
        chunks.append(rows)
        remaining -= read_bytes
    return [row for chunk in reversed(chunks) for row in chunk]


def _routine_report_ts(raw_metar: Any, target_date: str) -> datetime | None:
    match = re.search(r"\b(\d{2})(\d{2})(\d{2})Z\b", str(raw_metar or ""))
    if not match:
        return None
    day, hour, minute = map(int, match.groups())
    base = pd.Timestamp(target_date, tz="UTC")
    candidates = []
    for shift in (-1, 0, 1):
        shifted = base + pd.DateOffset(months=shift)
        try:
            candidates.append(
                datetime(
                    shifted.year,
                    shifted.month,
                    day,
                    hour,
                    minute,
                    tzinfo=UTC,
                )
            )
        except ValueError:
            continue
    return (
        min(candidates, key=lambda value: abs(value - base.to_pydatetime()))
        if candidates
        else None
    )


def _next_routine_minutes(decision: datetime) -> float:
    next_hour = decision.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return (next_hour - decision).total_seconds() / 60.0


def _exact_no_book(row: dict[str, Any], rung: int) -> dict[str, Any] | None:
    for book in (row.get("market_capture") or {}).get("books", []):
        if str(book.get("outcome") or "").casefold() != "no":
            continue
        question = str(book.get("question") or "").casefold()
        if "or below" in question or "or higher" in question:
            continue
        match = re.search(r"-?\d+", str(book.get("bracket") or ""))
        if match and int(match.group()) == rung:
            return book
    return None


def _summary_float(book: dict[str, Any] | None, key: str) -> float | None:
    value = ((book or {}).get("summary") or {}).get(key)
    return None if value is None else float(value)


def _artifact(profile: dict[str, Any]) -> dict[str, Any]:
    declaration = (profile.get("artifacts") or {}).get("runtime") or {}
    path = Path(str(declaration.get("path") or ""))
    payload = joblib.load(path)
    if not isinstance(payload, dict):
        raise RuntimeError("Busan runtime artifact must be a mapping")
    if payload.get("schema_version") != RUNTIME_ARTIFACT_SCHEMA_VERSION:
        raise RuntimeError("Busan runtime artifact schema mismatch")
    if payload.get("city") != "Busan" or not (payload.get("parity") or {}).get("pass"):
        raise RuntimeError("Busan runtime artifact identity/parity mismatch")
    return payload


def _feature_values(
    row: dict[str, Any], artifact: dict[str, Any], decision: datetime, rung: int
) -> tuple[dict[str, float | None], float, float]:
    local = decision.astimezone(SEOUL_TZ)
    day = local.timetuple().tm_yday
    physical_frame = pd.DataFrame(
        [
            {
                "local_hour": float(local.hour),
                "running_max_market_value": float(rung),
                "day_of_year_sin": math.sin(2.0 * math.pi * day / 366.0),
                "day_of_year_cos": math.cos(2.0 * math.pi * day / 366.0),
            }
        ]
    )
    physical_probability = float(
        artifact["physical_model"].predict_proba(
            physical_frame[artifact["physical_features"]]
        )[0, 1]
    )
    forecast = row.get("forecast_context") or {}
    forecast_max_f = forecast.get("forecast_max_f")
    forecast_max_c = (
        None
        if forecast_max_f is None
        else (float(forecast_max_f) - 32.0) * 5.0 / 9.0
    )
    peak_hour = forecast.get("forecast_peak_hour_local")
    path = row.get("path_windows") or {}
    source_temp = row.get("source_temp_c")
    source_running = row.get("source_running_max_c")
    values: dict[str, float | None] = {
        "source_margin_to_rung_c": None if source_temp is None else float(source_temp) - rung,
        "source_running_margin_to_rung_c": None if source_running is None else float(source_running) - rung,
        "distance_below_source_running_max_c": row.get("distance_below_source_running_max_c"),
        "minutes_since_source_running_max": row.get("minutes_since_source_running_max"),
        "current_cross_retained": None if source_temp is None else float(float(source_temp) - rung >= 0.5),
        "observation_history_count": row.get("observation_history_count"),
        "minutes_to_next_routine": _next_routine_minutes(decision),
        "local_hour": float(local.hour),
        "path_15m_slope_c_per_hour": (path.get("15m") or {}).get("temp_slope_c_per_hour"),
        "path_60m_slope_c_per_hour": (path.get("60m") or {}).get("temp_slope_c_per_hour"),
        "hours_to_forecast_peak": None if peak_hour is None else float(peak_hour) - local.hour - local.minute / 60.0,
        "forecast_ceiling_margin_c": None if forecast_max_c is None else forecast_max_c - rung,
        "relative_humidity_pct": row.get("relative_humidity_pct"),
        "dewpoint_depression_c": row.get("dewpoint_depression_c"),
        "forecast_cloud_cover_remaining_3h_mean_pct": forecast.get("forecast_cloud_cover_remaining_3h_mean_pct"),
        "forecast_precip_probability_remaining_3h_max_pct": forecast.get("forecast_precip_probability_remaining_3h_max_pct"),
        "forecast_wind_speed_remaining_3h_max_kt": forecast.get("forecast_wind_speed_remaining_3h_max_kt"),
        "physical_prior_logit": math.log(
            np.clip(physical_probability, 1e-6, 1 - 1e-6)
            / np.clip(1.0 - physical_probability, 1e-6, 1.0)
        ),
    }
    matrix = pd.DataFrame([values], columns=artifact["confirmation_features"])
    confirmation_probability = float(
        artifact["confirmation_model"].predict_proba(matrix)[0, 1]
    )
    weather_probability = confirmation_probability + (
        1.0 - confirmation_probability
    ) * physical_probability
    return values, physical_probability, weather_probability


class BusanOnlineMarketPriorAdapter:
    """Score every recent, pending AMOS confirmation checkpoint without labels."""

    def score(self, profile: dict[str, Any], now: datetime) -> list[CityScore]:
        now = now.astimezone(UTC)
        artifact = _artifact(profile)
        root = Path(profile["source_journal"])
        rows = _tail_rows(
            root, max_bytes=int(profile.get("source_tail_bytes", 32 * 1024 * 1024))
        )
        max_source_age = float(profile.get("max_source_age_seconds", 180.0))
        forward_start = _parse_utc(profile.get("forward_start_utc"))
        eligible: list[tuple[datetime, dict[str, Any]]] = []
        for row in rows:
            if row.get("city") != "Busan" or row.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
                continue
            decision = _parse_utc(
                row.get("source_available_at_utc")
                or row.get("source_first_seen_ts_utc")
            )
            if decision is None or decision > now:
                continue
            if forward_start is not None and decision < forward_start:
                continue
            if (now - decision).total_seconds() > max_source_age:
                continue
            eligible.append((decision, row))
        if not eligible:
            raise InputNotReady(
                "busan_checkpoint_not_available",
                city="Busan",
                target_date=now.astimezone(SEOUL_TZ).date().isoformat(),
                decision_ts_utc=now.isoformat(),
                details={
                    "source_journal": str(root),
                    "forward_start_utc": profile.get("forward_start_utc"),
                    "max_source_age_seconds": max_source_age,
                },
            )

        output: list[CityScore] = []
        seen_events: set[str] = set()
        for decision, row in sorted(eligible, key=lambda item: item[0]):
            event_key = str(row.get("source_event_key") or row.get("source_first_seen_ts_utc"))
            if event_key in seen_events:
                continue
            seen_events.add(event_key)
            target_date = str(row.get("target_date") or "unknown")
            source_obs = str(row.get("source_observation_ts_utc") or decision.isoformat())
            rung_value = row.get("routine_running_max_market_value")
            if rung_value is None:
                continue
            rung = int(rung_value)
            local = decision.astimezone(SEOUL_TZ)
            source_running = row.get("source_running_max_c")
            minutes_since_peak = row.get("minutes_since_source_running_max")
            peak_ts = (
                None
                if minutes_since_peak is None
                else decision - timedelta(minutes=float(minutes_since_peak))
            )
            routine_ts = _routine_report_ts(row.get("raw_metar"), target_date)
            pending = bool(
                9 <= local.hour < 18
                and source_running is not None
                and float(source_running) - rung >= 0.5
                and peak_ts is not None
                and (routine_ts is None or routine_ts < peak_ts)
            )
            book = _exact_no_book(row, rung)
            bid = _summary_float(book, "best_bid")
            ask = _summary_float(book, "best_ask")
            book_ts = _parse_utc((book or {}).get("fetched_at_utc"))
            response_seconds = (
                None if book_ts is None else (book_ts - decision).total_seconds()
            )
            runtime_book_age_seconds = (
                None if book_ts is None else (now - book_ts).total_seconds()
            )
            max_response = float(
                profile.get("max_feature_book_response_seconds", 10.0)
            )
            max_runtime_age = float(
                profile.get("max_execution_book_age_seconds", 90.0)
            )
            reason = None
            if not pending:
                reason = "outside_pending_confirmation_state"
            elif book is None or bid is None or ask is None:
                reason = "exact_no_book_not_two_sided"
            elif response_seconds is None or not 0.0 <= response_seconds <= max_response:
                reason = "feature_book_response_stale"
            elif runtime_book_age_seconds is None or not 0.0 <= runtime_book_age_seconds <= max_runtime_age:
                reason = "execution_book_stale"

            features: dict[str, float | None] = {}
            physical_probability = None
            weather_probability = None
            posterior_probability = None
            if pending:
                features, physical_probability, weather_probability = _feature_values(
                    row, artifact, decision, rung
                )
                if reason is None:
                    market_probability = (float(bid) + float(ask)) / 2.0
                    posterior_probability = float(
                        np.asarray(
                            logit_shrunk_probability(
                                market_probability,
                                weather_probability,
                                weather_weight=float(artifact["expression_weight"]),
                            )
                        ).item()
                    )
            market_probability = (
                None if bid is None or ask is None else (float(bid) + float(ask)) / 2.0
            )
            snapshot_id = _sha256_json(
                {
                    "event": event_key,
                    "token_id": (book or {}).get("token_id"),
                    "fetched_at_utc": (book or {}).get("fetched_at_utc"),
                    "raw": (book or {}).get("raw"),
                }
            )
            missing = [
                name
                for name in artifact["confirmation_features"]
                if features.get(name) is None
            ]
            output.append(
                CityScore(
                    city="Busan",
                    target_date=target_date,
                    decision_ts_utc=decision.isoformat(),
                    source_obs_ts_utc=source_obs,
                    current_bracket=rung,
                    market_side="NO",
                    market_probability=market_probability,
                    market_entry_price=ask,
                    model_probability=posterior_probability,
                    model_id=str(artifact["model_id"]),
                    feature_coverage=(
                        0.0
                        if not features
                        else 1.0 - len(missing) / len(artifact["confirmation_features"])
                    ),
                    missing_features=missing,
                    features={
                        **features,
                        "physical_no_probability": physical_probability,
                        "weather_no_probability": weather_probability,
                        "expression_weight": float(artifact["expression_weight"]),
                        "book_response_seconds": response_seconds,
                        "runtime_book_age_seconds": runtime_book_age_seconds,
                    },
                    market={
                        "token_id": (book or {}).get("token_id"),
                        "condition_id": (book or {}).get("condition_id"),
                        "market_id": (book or {}).get("market_id"),
                        "outcome": "NO",
                        "question": (book or {}).get("question"),
                        "raw": (book or {}).get("raw") or {},
                        "book_snapshot_id": snapshot_id,
                        "feature_book_snapshot_id": snapshot_id,
                        "execution_book_snapshot_id": snapshot_id,
                        "book_ts_utc": (book or {}).get("fetched_at_utc"),
                    },
                    lineage={
                        "source": "amos_runway",
                        "profile_id": profile.get("profile_id"),
                        "source_event_key": event_key,
                        "source_first_seen_at_utc": row.get("source_first_seen_ts_utc"),
                        "source_first_seen_ts_utc": row.get("source_first_seen_ts_utc"),
                        "source_available_at_utc": row.get("source_available_at_utc"),
                        "collector_emitted_at_utc": row.get("collector_emitted_at_utc"),
                        "forecast_first_seen_utc": (row.get("forecast_context") or {}).get("forecast_first_seen_utc"),
                        "forecast_available_at_utc": (row.get("forecast_context") or {}).get("forecast_available_at_utc"),
                        "candidate_grain_version": artifact["candidate_grain_version"],
                        "probability_target": f"busan_exact_bracket_{rung}",
                        "probability_policy": "market_prior_residual_v1",
                        "market_feature_role": "prior_offset",
                        "market_feature_clock": "decision_current",
                        "book_snapshot_id": snapshot_id,
                        "model_artifact_sha256": (
                            ((profile.get("artifacts") or {}).get("runtime") or {}).get(
                                "sha256"
                            )
                        ),
                        "checkpoint_input_ref": {
                            "physical_path": str(root),
                            "record_identity": event_key,
                            "schema_version": CHECKPOINT_SCHEMA_VERSION,
                        },
                        "artifact_confirmation_train_end": artifact["confirmation_train_end"],
                        "artifact_physical_train_end": artifact["physical_train_end"],
                        "expression_weight_trained_through": artifact["expression_weight_trained_through"],
                        "label_fields_consumed": [],
                    },
                    evaluation_status="scored" if reason is None else "not_scorable",
                    not_scorable_reason=reason,
                )
            )
        return output


__all__ = ["BusanOnlineMarketPriorAdapter"]
