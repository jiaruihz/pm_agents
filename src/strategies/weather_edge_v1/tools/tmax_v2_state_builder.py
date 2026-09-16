"""Read-only, label-free point-in-time Tmax V2 feature-state builder."""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.strategies.runtime.production import load_production_spec
from weather_data_feed.market_brackets import MarketBracket, parse_market_bracket

from .tmax_feature_contract_v2 import FEATURE_CONTRACT_VERSION, feature_envelope


FEATURE_ARTIFACT_VERSION = "tmax_v2_state_builder_v2"
DEFAULT_DB = load_production_spec().canonical_db_path
_QUESTION_UNIT_RE = re.compile(r"(?:°|degrees?\s*)([CF])\b", re.IGNORECASE)


class TmaxV2StateBuilderError(ValueError):
    """The canonical state cannot be transformed without breaking semantics."""


def _utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _minutes_between(later: str, earlier: str | None) -> float | None:
    left, right = _utc(later), _utc(earlier)
    if left is None or right is None:
        return None
    return (left - right).total_seconds() / 60.0


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _same_or_before(value: str | None, decision_ts: str) -> bool:
    candidate, decision = _utc(value), _utc(decision_ts)
    return candidate is not None and decision is not None and candidate <= decision


def _assert_asof(value: str | None, decision_ts: str, source: str) -> None:
    if not _same_or_before(value, decision_ts):
        raise TmaxV2StateBuilderError(f"{source} is missing or after decision_ts_utc")


def _normalize_unit(value: Any) -> str | None:
    unit = str(value or "").strip().upper()
    return unit if unit in {"C", "F"} else None


def _question_unit(question: Any) -> str | None:
    match = _QUESTION_UNIT_RE.search(str(question or ""))
    return match.group(1).upper() if match else None


def _temperature_from_f(temp_f: Any, market_unit: str | None) -> float | None:
    value = _finite(temp_f)
    if value is None or market_unit not in {"C", "F"}:
        return None
    return value if market_unit == "F" else (value - 32.0) * 5.0 / 9.0


def _delta_from_f(delta_f: Any, market_unit: str | None) -> float | None:
    value = _finite(delta_f)
    if value is None or market_unit not in {"C", "F"}:
        return None
    return value if market_unit == "F" else value * 5.0 / 9.0


def _bracket_sort_key(rung: dict[str, Any]) -> tuple[float, float, str]:
    geometry = rung.get("bracket_geometry")
    if not geometry:
        return (math.inf, math.inf, str(rung["absolute_bracket_identity"]))
    low = -math.inf if geometry["bottom"] else geometry["low"]
    high = math.inf if geometry["top"] else geometry["high"]
    return (float(low), float(high), str(rung["absolute_bracket_identity"]))


def _book_availability_basis(
    book_ts: Any,
    *,
    parent_report_ts: str,
    parent_available_at: str,
    decision_ts: str,
) -> tuple[bool, dict[str, Any], str | None]:
    if book_ts is None or not str(book_ts).strip():
        return True, {
            "basis": "parent_ladder_snapshot",
            "source_report_ts_utc": parent_report_ts,
            "available_at_utc": parent_available_at,
            "book_fetched_at_utc": None,
        }, None
    parsed = _utc(str(book_ts))
    if parsed is None:
        return False, {
            "basis": "invalid_book_timestamp",
            "source_report_ts_utc": None,
            "available_at_utc": None,
            "book_fetched_at_utc": None,
        }, "invalid_book_timestamp"
    if not _same_or_before(str(book_ts), decision_ts):
        return False, {
            "basis": "book_fetched_after_decision",
            "source_report_ts_utc": None,
            "available_at_utc": None,
            "book_fetched_at_utc": None,
        }, "book_fetched_after_decision"
    return True, {
        "basis": "book_fetched_at_utc",
        "source_report_ts_utc": str(book_ts),
        "available_at_utc": str(book_ts),
        "book_fetched_at_utc": str(book_ts),
    }, None


def _side_quote(
    rung: dict[str, Any],
    prefix: str,
    *,
    parent_report_ts: str,
    parent_available_at: str,
    decision_ts: str,
) -> dict[str, Any]:
    usable, basis, missing_reason = _book_availability_basis(
        rung.get(f"{prefix}_book_fetched_at_utc"),
        parent_report_ts=parent_report_ts,
        parent_available_at=parent_available_at,
        decision_ts=decision_ts,
    )
    return {
        "bid": rung.get(f"{prefix}_direct_bid") if usable else None,
        "ask": rung.get(f"{prefix}_direct_ask") if usable else None,
        "bid_size": rung.get(f"{prefix}_direct_bid_size") if usable else None,
        "ask_size": rung.get(f"{prefix}_direct_ask_size") if usable else None,
        "depth_bid_5c": rung.get(f"{prefix}_direct_depth_bid_5c") if usable else None,
        "depth_ask_5c": rung.get(f"{prefix}_direct_depth_ask_5c") if usable else None,
        "depth_bid_10c": rung.get(f"{prefix}_direct_depth_bid_10c") if usable else None,
        "depth_ask_10c": rung.get(f"{prefix}_direct_depth_ask_10c") if usable else None,
        "book_status": rung.get(f"{prefix}_book_status"),
        "book_fetched_at_utc": basis["book_fetched_at_utc"],
        "availability_basis": basis,
        "missing_reason": missing_reason,
    }


def _legal_probability(value: Any) -> float | None:
    parsed = _finite(value)
    return parsed if parsed is not None and 0.0 <= parsed <= 1.0 else None


def _yes_quote(
    rung: dict[str, Any],
    *,
    parent_report_ts: str,
    parent_available_at: str,
    decision_ts: str,
) -> tuple[dict[str, Any], float | None]:
    yes = _side_quote(
        rung,
        "yes",
        parent_report_ts=parent_report_ts,
        parent_available_at=parent_available_at,
        decision_ts=decision_ts,
    )
    no = _side_quote(
        rung,
        "no",
        parent_report_ts=parent_report_ts,
        parent_available_at=parent_available_at,
        decision_ts=decision_ts,
    )
    bid_candidates = [_legal_probability(yes["bid"])]
    no_ask = _legal_probability(no["ask"])
    if no_ask is not None:
        bid_candidates.append(1.0 - no_ask)
    ask_candidates = [_legal_probability(yes["ask"])]
    no_bid = _legal_probability(no["bid"])
    if no_bid is not None:
        ask_candidates.append(1.0 - no_bid)
    bids = [value for value in bid_candidates if value is not None]
    asks = [value for value in ask_candidates if value is not None]
    bid, ask = (max(bids) if bids else None), (min(asks) if asks else None)
    if bid is not None and ask is not None and bid > ask:
        mark, mark_status = None, "crossed_implied_quote"
    elif bid is not None and ask is not None:
        mark, mark_status = (bid + ask) / 2.0, "valid"
    elif bid is not None or ask is not None:
        mark, mark_status = (bid if bid is not None else ask), "valid"
    else:
        mark, mark_status = None, "missing_legal_mark"
    return {
        "yes": yes,
        "no": no,
        "yes_implied_bid": bid,
        "yes_implied_ask": ask,
        "yes_mark": mark,
        "mark_status": mark_status,
    }, mark


def _validate_ladder_unit(rungs: list[dict[str, Any]], market_unit: str | None) -> None:
    known_units = {
        str(rung["native_unit"])
        for rung in rungs
        if rung.get("native_unit") is not None
    }
    if len(known_units) > 1:
        raise TmaxV2StateBuilderError(f"ladder questions contain conflicting units: {sorted(known_units)}")
    if market_unit is not None and known_units and known_units != {market_unit}:
        raise TmaxV2StateBuilderError(
            f"ladder question unit {sorted(known_units)} conflicts with market_unit {market_unit}"
        )


def _market_ladder(
    rows: Iterable[sqlite3.Row],
    *,
    market_unit: str | None,
    parent_report_ts: str,
    parent_available_at: str,
    decision_ts: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ladder: list[dict[str, Any]] = []
    for row in rows:
        rung = _row_dict(row)
        question = rung.get("effective_question")
        native_unit = _question_unit(question)
        parsed = parse_market_bracket(str(rung["absolute_bracket_identity"]), str(question or ""))
        direct, _ = _yes_quote(
            rung,
            parent_report_ts=parent_report_ts,
            parent_available_at=parent_available_at,
            decision_ts=decision_ts,
        )
        ladder.append(
            {
                "absolute_bracket_identity": rung["absolute_bracket_identity"],
                "bracket_geometry": parsed.as_dict() if parsed else None,
                "question": question,
                "question_source": rung.get("effective_question_source"),
                "question_missing_reason": rung.get("effective_question_missing_reason"),
                "native_unit": native_unit,
                "native_unit_missing_reason": None if native_unit else "question_unit_unavailable",
                "condition_id": rung["condition_id"],
                "market_id": rung["market_id"],
                "yes_token_id": rung["yes_token_id"],
                "no_token_id": rung["no_token_id"],
                "direct_quote": direct,
                "source_record_hash": rung["source_record_hash"],
            }
        )
    ladder.sort(key=_bracket_sort_key)
    _validate_ladder_unit(ladder, market_unit)

    missing_marks = [
        str(item["absolute_bracket_identity"])
        for item in ladder
        if item["direct_quote"]["mark_status"] != "valid"
    ]
    marks = [item["direct_quote"]["yes_mark"] for item in ladder]
    mark_sum = sum(mark for mark in marks if mark is not None)
    complete = bool(ladder) and not missing_marks and len(marks) == len(ladder) and mark_sum > 0.0
    if complete:
        normalization_status = "complete"
        for item in ladder:
            item["market_prior_probability"] = item["direct_quote"]["yes_mark"] / mark_sum
    else:
        normalization_status = "invalid_mark_sum" if not missing_marks and mark_sum <= 0.0 else "incomplete_marks"
        for item in ladder:
            item["market_prior_probability"] = None

    spread_values = [
        item["direct_quote"]["yes_implied_ask"] - item["direct_quote"]["yes_implied_bid"]
        for item in ladder
        if item["direct_quote"]["yes_implied_ask"] is not None
        and item["direct_quote"]["yes_implied_bid"] is not None
    ]
    depth_values = [
        value
        for item in ladder
        for value in (
            _finite(item["direct_quote"]["yes"]["depth_bid_5c"]),
            _finite(item["direct_quote"]["yes"]["depth_ask_5c"]),
            _finite(item["direct_quote"]["no"]["depth_bid_5c"]),
            _finite(item["direct_quote"]["no"]["depth_ask_5c"]),
        )
        if value is not None
    ]
    return ladder, {
        "normalized": complete,
        "normalization_status": normalization_status,
        "raw_mark_sum": mark_sum if marks else None,
        "quoted_rung_count": sum(mark is not None for mark in marks),
        "relevant_rung_count": len(ladder),
        "missing_mark_rungs": missing_marks,
        "mean_yes_spread": sum(spread_values) / len(spread_values) if spread_values else None,
        "total_direct_depth_5c": sum(depth_values) if depth_values else None,
    }


def _anchor_index(ladder: list[dict[str, Any]], anchor: float | None) -> tuple[int | None, float | None]:
    if anchor is None:
        return None, None
    for index, rung in enumerate(ladder):
        geometry = rung["bracket_geometry"]
        if geometry and MarketBracket(**geometry).contains(anchor):
            return index, anchor
    settlement_aligned = math.floor(anchor + 0.5)
    for index, rung in enumerate(ladder):
        geometry = rung["bracket_geometry"]
        if geometry and MarketBracket(**geometry).contains(settlement_aligned):
            return index, float(settlement_aligned)
    return None, None


def _bucket_geometry(
    ladder: list[dict[str, Any]],
    *,
    current_temperature: float | None,
    running_max_temperature: float | None,
    market_unit: str | None,
) -> dict[str, Any]:
    index, ladder_anchor = _anchor_index(ladder, running_max_temperature)
    names = ("below", "current", "d1", "d2", "tail")
    if index is None:
        return {
            "market_unit": market_unit,
            "current_temperature": current_temperature,
            "running_max_temperature": running_max_temperature,
            "anchor_source": "running_max_temperature",
            "anchor_ladder_value": None,
            "current_index": None,
            "buckets": {name: None for name in names},
        }
    positions = {
        "below": list(range(0, index)),
        "current": [index],
        "d1": [index + 1] if index + 1 < len(ladder) else [],
        "d2": [index + 2] if index + 2 < len(ladder) else [],
        "tail": list(range(index + 3, len(ladder))),
    }
    buckets: dict[str, Any] = {}
    for name, selected in positions.items():
        selected_rungs = [ladder[position] for position in selected]
        probabilities = [rung["market_prior_probability"] for rung in selected_rungs]
        buckets[name] = {
            "absolute_bracket_identities": [rung["absolute_bracket_identity"] for rung in selected_rungs],
            "market_prior_mass": (
                sum(probabilities)
                if selected_rungs and all(probability is not None for probability in probabilities)
                else None
            ),
        }
    return {
        "market_unit": market_unit,
        "current_temperature": current_temperature,
        "running_max_temperature": running_max_temperature,
        "anchor_source": "running_max_temperature",
        "anchor_ladder_value": ladder_anchor,
        "current_index": index,
        "buckets": buckets,
    }


def _observation_history(conn: sqlite3.Connection, state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        WITH exact_value_first_seen AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY source_observation_id
                ORDER BY available_at_utc ASC, tmax_v2_observation_id ASC
            ) AS exact_value_seen_rank
            FROM tmax_v2_observation_event_lineage_enriched
            WHERE city = :city AND target_date = :target_date
              AND lineage_status = 'pit_verified_first_seen'
              AND available_at_utc <= :decision_ts AND obs_ts_utc <= :decision_ts
        ), asof_revision AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY obs_ts_utc
                ORDER BY CASE WHEN source_kind = 'embedded_snapshot_capture' THEN 1 ELSE 0 END DESC,
                         available_at_utc DESC, tmax_v2_observation_id DESC
            ) AS observed_ts_rank
            FROM exact_value_first_seen
            WHERE exact_value_seen_rank = 1
        )
        SELECT * FROM asof_revision WHERE observed_ts_rank = 1
        ORDER BY obs_ts_utc ASC, available_at_utc ASC, tmax_v2_observation_id ASC
        """,
        {
            "city": state["city"],
            "target_date": state["target_date"],
            "decision_ts": state["decision_ts_utc"],
        },
    ).fetchall()
    return [_row_dict(row) for row in rows]


def _path_features(
    history: list[dict[str, Any]], decision_ts: str, market_unit: str | None
) -> dict[str, Any]:
    usable = [row for row in history if _finite(row["temp_f"]) is not None]
    if not usable:
        return {
            "market_unit": market_unit,
            "current_temperature": None,
            "running_max_temperature": None,
            "missing_reason": "no_pit_verified_observation",
            "history": [],
        }
    current = usable[-1]
    current_temp_f = _finite(current["temp_f"])
    running_max_f = max(_finite(row["temp_f"]) for row in usable)
    max_row = [row for row in usable if _finite(row["temp_f"]) == running_max_f][-1]
    reference = _utc(current["obs_ts_utc"])

    def trend(hours: int) -> float | None:
        if reference is None or current_temp_f is None:
            return None
        cutoff = reference - timedelta(hours=hours)
        candidates = [
            row for row in usable
            if (observed := _utc(row["obs_ts_utc"])) is not None and observed <= cutoff
        ]
        if not candidates:
            return None
        base = _finite(candidates[-1]["temp_f"])
        return _delta_from_f(current_temp_f - base, market_unit) if base is not None else None

    intervals = [
        (right_ts - left_ts).total_seconds() / 60.0
        for left, right in zip(usable, usable[1:])
        if (left_ts := _utc(left["obs_ts_utc"])) is not None
        and (right_ts := _utc(right["obs_ts_utc"])) is not None
        and right_ts > left_ts
    ]
    history_payload = []
    for row in usable:
        history_payload.append(
            {
                key: row.get(key)
                for key in (
                    "tmax_v2_observation_id",
                    "source_system",
                    "obs_ts_utc",
                    "temp_f",
                    "first_seen_at_utc",
                    "available_at_utc",
                    "source_kind",
                    "effective_station_id",
                    "effective_icao",
                    "effective_feed_identity",
                    "effective_identity_missing_reason",
                )
            }
        )
        history_payload[-1]["temperature_market"] = _temperature_from_f(row["temp_f"], market_unit)
        history_payload[-1]["market_unit"] = market_unit
    current_market = _temperature_from_f(current_temp_f, market_unit)
    running_max_market = _temperature_from_f(running_max_f, market_unit)
    return {
        "market_unit": market_unit,
        "current_temperature": current_market,
        "current_temperature_f_lineage": current_temp_f,
        "current_observation_id": current["tmax_v2_observation_id"],
        "current_observed_at_utc": current["obs_ts_utc"],
        "current_available_at_utc": current["available_at_utc"],
        "running_max_temperature": running_max_market,
        "running_max_temperature_f_lineage": running_max_f,
        "running_max_observed_at_utc": max_row["obs_ts_utc"],
        "running_max_available_at_utc": max_row["available_at_utc"],
        "observation_age_minutes": _minutes_between(decision_ts, current["obs_ts_utc"]),
        "max_age_minutes": _minutes_between(decision_ts, max_row["obs_ts_utc"]),
        "observation_cadence_minutes": median(intervals[-6:]) if intervals else None,
        "observation_count": len(usable),
        "temperature_trend_reference_ts_utc": current["obs_ts_utc"],
        "temperature_trend_1h": trend(1),
        "temperature_trend_3h": trend(3),
        "temperature_decline": _delta_from_f(running_max_f - current_temp_f, market_unit),
        "is_pullback": running_max_f > current_temp_f,
        "identity": {
            "station_id": current.get("effective_station_id"),
            "icao": current.get("effective_icao"),
            "feed_identity": current.get("effective_feed_identity"),
            "missing_reason": current.get("effective_identity_missing_reason"),
        },
        "missing_reason": None if market_unit in {"C", "F"} else "market_unit_unavailable",
        "history": history_payload,
    }


def _peak_clock_local(point: dict[str, Any], forecast: dict[str, Any]) -> tuple[str | None, str | None]:
    local = point.get("valid_time_local")
    if local:
        return str(local), "canonical_valid_time_local"
    valid_utc = _utc(point.get("valid_time_utc"))
    if valid_utc is None:
        return None, None
    timezone_name = forecast.get("effective_forecast_timezone")
    if timezone_name:
        try:
            return valid_utc.astimezone(ZoneInfo(str(timezone_name))).isoformat(), "canonical_forecast_timezone"
        except ZoneInfoNotFoundError:
            pass
    offset = forecast.get("effective_forecast_utc_offset_seconds")
    if offset is not None:
        try:
            tzinfo = timezone(timedelta(seconds=int(offset)))
        except (TypeError, ValueError, OverflowError):
            return None, None
        return valid_utc.astimezone(tzinfo).isoformat(), "canonical_forecast_utc_offset_seconds"
    return None, None


def _forecast_features(
    forecast: dict[str, Any],
    decision_ts: str,
    path: dict[str, Any],
    market_unit: str | None,
) -> dict[str, Any]:
    _assert_asof(forecast["available_at_utc"], decision_ts, "forecast available_at_utc")
    normalized_raw = forecast.get("effective_normalized_hourly_curve_json")
    curve_reason = forecast.get("effective_curve_time_missing_reason")
    if not normalized_raw:
        normalized = []
        curve_reason = curve_reason or "normalized_curve_missing"
    else:
        try:
            normalized = json.loads(normalized_raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise TmaxV2StateBuilderError("normalized forecast curve JSON is invalid") from exc
        if not isinstance(normalized, list):
            raise TmaxV2StateBuilderError("normalized forecast curve JSON must be a list")

    decision = _utc(decision_ts)
    if decision is None:
        raise TmaxV2StateBuilderError("decision_ts_utc is invalid")
    points: list[dict[str, Any]] = []
    for item in normalized:
        if not isinstance(item, dict):
            continue
        valid_time = _utc(item.get("valid_time_utc"))
        temp_f = _finite(item.get("temperature_f"))
        points.append(
            {
                "valid_time_local": item.get("valid_time_local") or item.get("time_local"),
                "valid_time_utc": item.get("valid_time_utc"),
                "valid_time_basis": item.get("valid_time_basis"),
                "temperature_f_lineage": temp_f,
                "temperature_market": _temperature_from_f(temp_f, market_unit),
                "market_unit": market_unit,
                "is_future": valid_time is not None and valid_time > decision,
            }
        )
    future_points = [point for point in points if point["is_future"]]
    usable_future = [point for point in future_points if point["temperature_market"] is not None]
    peak = max(usable_future, key=lambda point: point["temperature_market"]) if usable_future else None
    peak_temp = peak["temperature_market"] if peak else None
    if not future_points:
        remaining_reason = curve_reason or "no_future_curve_points_after_decision"
    elif not usable_future:
        remaining_reason = "future_curve_has_no_temperature_in_market_unit"
    else:
        remaining_reason = None
    peak_clock, peak_clock_basis = _peak_clock_local(peak, forecast) if peak else (None, None)

    run_at = forecast["forecast_run_at_utc"]
    run_missing_reason = None
    if run_at is not None and not _same_or_before(run_at, decision_ts):
        run_at = None
        run_missing_reason = "forecast_run_after_decision"
    return {
        "forecast_capture_id": forecast["forecast_capture_id"],
        "forecast_source": forecast["forecast_source"],
        "forecast_model": forecast["forecast_model"],
        "forecast_run_at_utc": run_at,
        "forecast_run_missing_reason": run_missing_reason,
        "forecast_available_at_utc": forecast["available_at_utc"],
        "forecast_age_minutes": _minutes_between(decision_ts, forecast["available_at_utc"]),
        "forecast_timezone": forecast.get("effective_forecast_timezone"),
        "forecast_utc_offset_seconds": forecast.get("effective_forecast_utc_offset_seconds"),
        "curve_time_lineage_status": forecast.get("effective_curve_time_lineage_status"),
        "curve_time_missing_reason": curve_reason,
        "hourly_temperature_curve": points,
        "future_hourly_temperature_curve": future_points,
        "future_point_count": len(future_points),
        "forecast_peak_temperature": peak_temp,
        "forecast_peak_temperature_f_lineage": peak["temperature_f_lineage"] if peak else None,
        "forecast_peak_valid_time_utc": peak["valid_time_utc"] if peak else None,
        "forecast_peak_clock_local": peak_clock,
        "forecast_peak_clock_basis": peak_clock_basis,
        "remaining_energy_from_current": (
            peak_temp - path["current_temperature"]
            if peak_temp is not None and path.get("current_temperature") is not None
            else None
        ),
        "remaining_energy_from_running_max": (
            peak_temp - path["running_max_temperature"]
            if peak_temp is not None and path.get("running_max_temperature") is not None
            else None
        ),
        "remaining_energy_missing_reason": remaining_reason,
        "market_unit": market_unit,
    }


def _envelope(
    value: Any,
    *,
    source: str,
    report: str | None,
    available: str | None,
    missing_reason: str | None = None,
) -> dict[str, Any]:
    return feature_envelope(
        value,
        source_system=source,
        source_report_ts_utc=report,
        first_seen_at_utc=available,
        available_at_utc=available,
        missing_reason=missing_reason,
        feature_version=FEATURE_ARTIFACT_VERSION,
    )


def _missing(source: str, reason: str) -> dict[str, Any]:
    return _envelope(None, source=source, report=None, available=None, missing_reason=reason)


def _station_or_feed(path: dict[str, Any]) -> tuple[str | None, str | None]:
    identity = path.get("identity") or {}
    value = identity.get("station_id") or identity.get("icao") or identity.get("feed_identity")
    reason = identity.get("missing_reason") or ("canonical_observation_identity_missing" if value is None else None)
    return value, reason


def build_tmax_v2_feature_frame(
    conn: sqlite3.Connection, *, tmax_state_id: str | None = None
) -> list[dict[str, Any]]:
    """Build deterministic feature payloads at effective state / ladder grain."""

    params: tuple[Any, ...] = (tmax_state_id,) if tmax_state_id else ()
    state_filter = "AND tmax_state_id = ?" if tmax_state_id else ""
    states = conn.execute(
        f"""
        SELECT * FROM tmax_v2_canonical_state_effective
        WHERE pit_status = 'pit_verified' {state_filter}
        ORDER BY decision_ts_utc, city, target_date, tmax_state_id
        """,
        params,
    ).fetchall()
    if tmax_state_id and not states:
        raise TmaxV2StateBuilderError("no pit_verified effective state for tmax_state_id")

    frames: list[dict[str, Any]] = []
    for state_row in states:
        state = _row_dict(state_row)
        decision_ts = state["decision_ts_utc"]
        ladder = conn.execute(
            "SELECT * FROM tmax_v2_ladder_snapshots_enriched WHERE ladder_snapshot_id = ?",
            (state["ladder_snapshot_id"],),
        ).fetchone()
        forecast = conn.execute(
            "SELECT * FROM tmax_v2_forecast_captures_enriched WHERE forecast_capture_id = ?",
            (state["forecast_capture_id"],),
        ).fetchone()
        if ladder is None or forecast is None:
            raise TmaxV2StateBuilderError("pit_verified state has a missing canonical input")
        ladder_row, forecast_row = _row_dict(ladder), _row_dict(forecast)
        _assert_asof(ladder_row["available_at_utc"], decision_ts, "ladder available_at_utc")
        _assert_asof(ladder_row["source_snapshot_ts_utc"], decision_ts, "ladder source_snapshot_ts_utc")
        market_unit = _normalize_unit(ladder_row.get("effective_market_unit"))
        rungs = conn.execute(
            """SELECT * FROM tmax_v2_ladder_rung_quotes_enriched
               WHERE ladder_snapshot_id = ? ORDER BY absolute_bracket_identity""",
            (state["ladder_snapshot_id"],),
        ).fetchall()
        if len(rungs) != int(ladder_row["rung_count"]):
            raise TmaxV2StateBuilderError("ladder rung count differs from the canonical snapshot")

        history = _observation_history(conn, state)
        path = _path_features(history, decision_ts, market_unit)
        if path.get("current_observation_id") != state["observation_event_id"]:
            raise TmaxV2StateBuilderError("as-of path selection disagrees with canonical observation state")
        ladder_book, market_summary = _market_ladder(
            rungs,
            market_unit=market_unit,
            parent_report_ts=ladder_row["source_snapshot_ts_utc"],
            parent_available_at=ladder_row["available_at_utc"],
            decision_ts=decision_ts,
        )
        geometry = _bucket_geometry(
            ladder_book,
            current_temperature=path.get("current_temperature"),
            running_max_temperature=path.get("running_max_temperature"),
            market_unit=market_unit,
        )
        forecast_features = _forecast_features(
            forecast_row, decision_ts, path, market_unit
        )
        station_or_feed, station_missing_reason = _station_or_feed(path)
        obs_source = history[-1]["source_system"] if history else "tmax_v2_observation_event_lineage_enriched"
        obs_report = path.get("current_observed_at_utc")
        obs_available = path.get("current_available_at_utc")
        forecast_source = forecast_row["source_system"]
        forecast_available = forecast_row["available_at_utc"]
        market_source = ladder_row["source_system"]
        market_report = ladder_row["source_snapshot_ts_utc"]
        market_available = ladder_row["available_at_utc"]
        settlement_source = ladder_row.get("effective_settlement_source_class")
        settlement_missing_reason = (
            None if settlement_source else ladder_row.get("effective_market_metadata_missing_reason")
            or "settlement_source_class_missing"
        )
        unit_missing_reason = None if market_unit else (
            ladder_row.get("effective_market_metadata_missing_reason") or "market_unit_missing"
        )
        path_missing_reason = path.get("missing_reason")
        remaining_reason = forecast_features["remaining_energy_missing_reason"]

        features = {
            "city": _envelope(state["city"], source="tmax_v2_canonical_state_effective", report=decision_ts, available=decision_ts),
            "target_date": _envelope(state["target_date"], source="tmax_v2_canonical_state_effective", report=decision_ts, available=decision_ts),
            "decision_ts_utc": _envelope(decision_ts, source="tmax_v2_canonical_state_effective", report=decision_ts, available=decision_ts),
            "station_or_feed": _envelope(station_or_feed, source=obs_source, report=obs_report, available=obs_available, missing_reason=station_missing_reason),
            "settlement_source": _envelope(settlement_source, source=market_source, report=market_report, available=market_available, missing_reason=settlement_missing_reason),
            "unit": _envelope(market_unit, source=market_source, report=market_report, available=market_available, missing_reason=unit_missing_reason),
            "current_temperature": _envelope(path.get("current_temperature"), source=obs_source, report=obs_report, available=obs_available, missing_reason=path_missing_reason),
            "running_max_temperature": _envelope(path.get("running_max_temperature"), source=obs_source, report=path.get("running_max_observed_at_utc"), available=path.get("running_max_available_at_utc"), missing_reason=path_missing_reason),
            "running_max_observed_at_utc": _envelope(path.get("running_max_observed_at_utc"), source=obs_source, report=path.get("running_max_observed_at_utc"), available=path.get("running_max_available_at_utc"), missing_reason=path_missing_reason),
            "observation_age_minutes": _envelope(path.get("observation_age_minutes"), source=obs_source, report=obs_report, available=obs_available, missing_reason=path_missing_reason),
            "observation_cadence_minutes": _envelope(path.get("observation_cadence_minutes"), source=obs_source, report=obs_report, available=obs_available, missing_reason="fewer_than_two_pit_observations" if path.get("observation_cadence_minutes") is None else None),
            "temperature_trend_1h": _envelope(path.get("temperature_trend_1h"), source=obs_source, report=obs_report, available=obs_available, missing_reason="no_observation_at_or_before_latest_report_minus_1h" if path.get("temperature_trend_1h") is None else None),
            "temperature_trend_3h": _envelope(path.get("temperature_trend_3h"), source=obs_source, report=obs_report, available=obs_available, missing_reason="no_observation_at_or_before_latest_report_minus_3h" if path.get("temperature_trend_3h") is None else None),
            "temperature_decline": _envelope(path.get("temperature_decline"), source=obs_source, report=obs_report, available=obs_available, missing_reason=path_missing_reason),
            "assigned_forecast_model": _envelope(forecast_features["forecast_model"], source=forecast_source, report=forecast_row["snapshot_ts_utc"], available=forecast_available, missing_reason="forecast_model_unknown" if not forecast_features["forecast_model"] else None),
            "forecast_run_at_utc": _envelope(forecast_features["forecast_run_at_utc"], source=forecast_source, report=forecast_row["snapshot_ts_utc"], available=forecast_available, missing_reason=forecast_features["forecast_run_missing_reason"] or ("forecast_run_unknown" if not forecast_features["forecast_run_at_utc"] else None)),
            "forecast_age_minutes": _envelope(forecast_features["forecast_age_minutes"], source=forecast_source, report=forecast_row["snapshot_ts_utc"], available=forecast_available),
            "forecast_hourly_temperature_curve": _envelope(forecast_features["hourly_temperature_curve"], source=forecast_source, report=forecast_row["snapshot_ts_utc"], available=forecast_available, missing_reason=forecast_features["curve_time_missing_reason"] if not forecast_features["hourly_temperature_curve"] else None),
            "forecast_peak_clock_local": _envelope(forecast_features["forecast_peak_clock_local"], source=forecast_source, report=forecast_row["snapshot_ts_utc"], available=forecast_available, missing_reason=remaining_reason if forecast_features["forecast_peak_clock_local"] is None else None),
            "dewpoint_temperature": _missing("tmax_v2_state_builder", "optional_meteo_not_materialized"),
            "relative_humidity_pct": _missing("tmax_v2_state_builder", "optional_meteo_not_materialized"),
            "wind_speed": _missing("tmax_v2_state_builder", "optional_meteo_not_materialized"),
            "wind_direction": _missing("tmax_v2_state_builder", "optional_meteo_not_materialized"),
            "sky_or_cloud_cover": _missing("tmax_v2_state_builder", "optional_meteo_not_materialized"),
            "settlement_ladder": _envelope(ladder_book, source=market_source, report=market_report, available=market_available),
            "ladder_quote_book": _envelope({"ladder_snapshot_id": state["ladder_snapshot_id"], "summary": market_summary}, source=market_source, report=market_report, available=market_available),
            "quote_timestamp_utc": _envelope(market_report, source=market_source, report=market_report, available=market_available),
            "quote_spread": _envelope(market_summary["mean_yes_spread"], source=market_source, report=market_report, available=market_available, missing_reason="no_two_sided_direct_quotes" if market_summary["mean_yes_spread"] is None else None),
            "quote_depth": _envelope(market_summary["total_direct_depth_5c"], source=market_source, report=market_report, available=market_available, missing_reason="no_direct_depth" if market_summary["total_direct_depth_5c"] is None else None),
            "base_probability_version": _missing("tmax_v2_state_builder", "probability_model_not_built"),
            "calibrator_version": _missing("tmax_v2_state_builder", "calibrator_not_built"),
            "feature_artifact_version": _envelope(FEATURE_ARTIFACT_VERSION, source="tmax_v2_state_builder", report=decision_ts, available=decision_ts),
        }
        frames.append(
            {
                "contract_version": FEATURE_CONTRACT_VERSION,
                "feature_artifact_version": FEATURE_ARTIFACT_VERSION,
                "tmax_state_id": state["tmax_state_id"],
                "tmax_state_revision_id": state["tmax_state_revision_id"],
                "ladder_snapshot_id": state["ladder_snapshot_id"],
                "pit_status": state["pit_status"],
                "features": features,
                "derived": {
                    "market_full_ladder_prior": {
                        "ladder_snapshot_id": state["ladder_snapshot_id"],
                        "market_unit": market_unit,
                        "rungs": ladder_book,
                        **market_summary,
                    },
                    "market_geometry": geometry,
                    "path": path,
                    "forecast": forecast_features,
                },
            }
        )
    return frames


def tmax_v2_coverage_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Read-only semantic inventory for builder-eligible canonical states."""

    frames = build_tmax_v2_feature_frame(conn)
    units = Counter(frame["features"]["unit"]["value"] or "unknown" for frame in frames)
    pullbacks = sum(bool(frame["derived"]["path"].get("is_pullback")) for frame in frames)
    energy_nonnull = sum(
        frame["derived"]["forecast"].get("remaining_energy_from_running_max") is not None
        for frame in frames
    )
    partial_ladders = sum(
        frame["derived"]["market_full_ladder_prior"]["normalization_status"] != "complete"
        for frame in frames
    )
    return {
        "states": len(frames),
        "unit_counts": dict(sorted(units.items())),
        "pullback_states": pullbacks,
        "future_only_energy_nonnull_states": energy_nonnull,
        "future_only_energy_nonnull_rate": energy_nonnull / len(frames) if frames else None,
        "partial_ladder_states": partial_ladders,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Tmax V2 state builder")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--state-id")
    parser.add_argument("--coverage", action="store_true")
    args = parser.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    try:
        result: Any = (
            tmax_v2_coverage_summary(conn)
            if args.coverage
            else build_tmax_v2_feature_frame(conn, tmax_state_id=args.state_id)
        )
    finally:
        conn.close()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
