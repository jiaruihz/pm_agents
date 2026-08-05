"""Run-aware D-1/D-2 forecast and full-ladder research contracts.

This module is deliberately pure.  Collectors may use it to materialize
append-only evidence, while replay and research use the same validators and
stable hashes.  Missing provider run lineage is a structured blocker; an
estimated synoptic cycle is never accepted as a real model run.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timezone
from statistics import mean, median
from typing import Any, Iterable, Mapping, Sequence

from weather_data_feed.market_brackets import MarketBracket, parse_market_bracket


FORECAST_ROW_SCHEMA = "weather_forecast_run_row_v2"
FORECAST_BATCH_SCHEMA = "weather_forecast_batch_v2"
FULL_LADDER_SCHEMA = "weather_full_ladder_checkpoint_v2"
EXACT_SINGLE_RUN_ENDPOINT = "https://single-runs-api.open-meteo.com/v1/forecast"


def stable_content_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def parse_utc(value: Any, *, field: str) -> datetime:
    if value in (None, ""):
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def run_lineage_evidence(
    *,
    payload_run_at_utc: str | None = None,
    request_run_at_utc: str | None = None,
    request_endpoint: str | None = None,
    request_succeeded: bool = False,
    fallback_applied: bool = False,
    raw_payload_hash: str | None = None,
    request_hash: str | None = None,
) -> dict[str, Any]:
    """Resolve auditable provider run evidence without cycle estimation."""
    if payload_run_at_utc:
        run_at = parse_utc(payload_run_at_utc, field="payload_run_at_utc")
        return {
            "forecast_run_at_utc": utc_text(run_at),
            "forecast_run_evidence": "provider_payload",
            "forecast_run_lineage_status": "identified",
            "blocker": None,
        }
    exact_request = (
        request_run_at_utc
        and request_endpoint == EXACT_SINGLE_RUN_ENDPOINT
        and request_succeeded
        and not fallback_applied
        and raw_payload_hash
        and request_hash
    )
    if exact_request:
        run_at = parse_utc(request_run_at_utc, field="request_run_at_utc")
        return {
            "forecast_run_at_utc": utc_text(run_at),
            "forecast_run_evidence": "exact_single_run_request",
            "forecast_run_lineage_status": "identified",
            "blocker": None,
        }
    reasons: list[str] = []
    if request_run_at_utc and request_endpoint != EXACT_SINGLE_RUN_ENDPOINT:
        reasons.append("endpoint_not_exact_single_run")
    if request_run_at_utc and not request_succeeded:
        reasons.append("request_not_successful")
    if fallback_applied:
        reasons.append("fallback_applied")
    if request_run_at_utc and not raw_payload_hash:
        reasons.append("missing_raw_payload_hash")
    if request_run_at_utc and not request_hash:
        reasons.append("missing_request_hash")
    if not payload_run_at_utc and not request_run_at_utc:
        reasons.append("provider_run_timestamp_unavailable")
    return {
        "forecast_run_at_utc": None,
        "forecast_run_evidence": None,
        "forecast_run_lineage_status": "blocked",
        "blocker": {
            "code": "provider_run_timestamp_unverified",
            "reasons": reasons or ["run_evidence_incomplete"],
        },
    }


def _target_start_utc(target_date: str) -> datetime:
    return datetime.combine(date.fromisoformat(target_date), time.min, tzinfo=timezone.utc)


def build_forecast_row(
    *,
    model_key: str,
    city: str,
    target_date: str,
    forecast_max_f: float,
    source_fetched_at_utc: str,
    detected_at_utc: str,
    first_seen_at_utc: str,
    available_at_utc: str,
    raw_payload_hash: str,
    producer_build_identity: str,
    capture_id: str,
    batch_capture_id: str,
    horizon_days_local: int | None,
    forecast_run_at_utc: str | None,
    forecast_run_evidence: str | None,
    forecast_run_lineage_status: str,
    lineage_blocker: Mapping[str, Any] | None = None,
    assigned_model: bool = False,
    previous_run_ts: str | None = None,
    previous_run_forecast_max_f: float | None = None,
    previous_content_hash: str | None = None,
    previous_content_forecast_max_f: float | None = None,
    revision_of_content_id: str | None = None,
) -> dict[str, Any]:
    fetched = parse_utc(source_fetched_at_utc, field="source_fetched_at_utc")
    detected = parse_utc(detected_at_utc, field="detected_at_utc")
    first_seen = parse_utc(first_seen_at_utc, field="first_seen_at_utc")
    available = parse_utc(available_at_utc, field="available_at_utc")
    if not fetched <= detected <= available:
        raise ValueError("clock constraint requires source_fetched <= detected <= available")
    if first_seen > available:
        raise ValueError("clock constraint requires first_seen <= available")
    run_at = parse_utc(forecast_run_at_utc, field="forecast_run_at_utc") if forecast_run_at_utc else None
    if forecast_run_lineage_status == "identified" and run_at is None:
        raise ValueError("identified run lineage requires forecast_run_at_utc")
    if run_at is not None and forecast_run_evidence not in {
        "provider_payload",
        "exact_single_run_request",
    }:
        raise ValueError("forecast run timestamp lacks accepted evidence")
    content_hash = stable_content_hash(
        {
            "model_key": model_key,
            "city": city,
            "target_date": target_date,
            "forecast_run_at_utc": utc_text(run_at) if run_at else None,
            "forecast_max_f": round(float(forecast_max_f), 6),
            "raw_payload_hash": raw_payload_hash,
        }
    )
    same_run_revision = bool(previous_content_hash and previous_content_hash != content_hash)
    run_transition = bool(previous_run_ts and run_at and parse_utc(previous_run_ts, field="previous_run_ts") != run_at)
    return {
        "schema_version": FORECAST_ROW_SCHEMA,
        "model_key": model_key,
        "city": city,
        "target_date": target_date,
        "horizon_days_local": int(horizon_days_local) if horizon_days_local is not None else None,
        "forecast_max_f": round(float(forecast_max_f), 6),
        "forecast_run_at_utc": utc_text(run_at) if run_at else None,
        "forecast_run_evidence": forecast_run_evidence,
        "forecast_run_lineage_status": forecast_run_lineage_status,
        "lineage_blocker": dict(lineage_blocker) if lineage_blocker else None,
        "source_fetched_at_utc": utc_text(fetched),
        "detected_at_utc": utc_text(detected),
        "first_seen_at_utc": utc_text(first_seen),
        "available_at_utc": utc_text(available),
        "lead_hours": round((_target_start_utc(target_date) - available).total_seconds() / 3600.0, 6),
        "model_run_age_hours": round((available - run_at).total_seconds() / 3600.0, 6) if run_at else None,
        "raw_payload_hash": raw_payload_hash,
        "producer_build_identity": producer_build_identity,
        "capture_id": capture_id,
        "batch_capture_id": batch_capture_id,
        "content_hash": content_hash,
        "assigned_model": bool(assigned_model),
        # Provider run transition.  Never falls back to same-run content change.
        "previous_run_ts": previous_run_ts if run_transition else None,
        "previous_run_forecast_max_f": (
            round(float(previous_run_forecast_max_f), 6)
            if run_transition and previous_run_forecast_max_f is not None
            else None
        ),
        "run_to_run_delta_f": (
            round(float(forecast_max_f) - float(previous_run_forecast_max_f), 6)
            if run_transition and previous_run_forecast_max_f is not None
            else None
        ),
        # Same provider run, changed content.  Never falls back to run transition.
        "previous_content_hash": previous_content_hash if same_run_revision and not run_transition else None,
        "content_revision_delta_f": (
            round(float(forecast_max_f) - float(previous_content_forecast_max_f), 6)
            if same_run_revision and not run_transition and previous_content_forecast_max_f is not None
            else None
        ),
        "revision_of_content_id": revision_of_content_id if same_run_revision and not run_transition else None,
    }


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    left = int(position)
    fraction = position - left
    right = min(left + 1, len(ordered) - 1)
    return ordered[left] + fraction * (ordered[right] - ordered[left])


def summarize_forecast_batch(
    rows: Iterable[Mapping[str, Any]],
    *,
    expected_model_keys: Iterable[str],
) -> dict[str, Any]:
    source_rows = [dict(row) for row in rows]
    if not source_rows:
        raise ValueError("forecast batch cannot be empty")
    identity = {
        (row.get("batch_capture_id"), row.get("city"), row.get("target_date"))
        for row in source_rows
    }
    if len(identity) != 1:
        raise ValueError("forecast batch rows must share batch_capture_id/city/target_date")
    values = {
        str(row["model_key"]): float(row["forecast_max_f"])
        for row in source_rows
    }
    expected = sorted(set(str(value) for value in expected_model_keys))
    present_values = list(values.values())
    assigned = [row for row in source_rows if row.get("assigned_model")]
    assigned_value = float(assigned[0]["forecast_max_f"]) if len(assigned) == 1 else None
    center = median(present_values)
    content = {
        "model_values": dict(sorted(values.items())),
        "missing_model_keys": sorted(set(expected) - set(values)),
    }
    batch_capture_id, city, target_date = next(iter(identity))
    return {
        "schema_version": FORECAST_BATCH_SCHEMA,
        "batch_capture_id": batch_capture_id,
        "city": city,
        "target_date": target_date,
        **content,
        "model_count": len(values),
        "mean_f": round(mean(present_values), 6),
        "median_f": round(center, 6),
        "q25_f": round(_quantile(present_values, 0.25), 6),
        "q75_f": round(_quantile(present_values, 0.75), 6),
        "min_f": round(min(present_values), 6),
        "max_f": round(max(present_values), 6),
        "spread_f": round(max(present_values) - min(present_values), 6),
        "iqr_f": round(_quantile(present_values, 0.75) - _quantile(present_values, 0.25), 6),
        "assigned_model_value_f": round(assigned_value, 6) if assigned_value is not None else None,
        "assigned_minus_consensus_f": round(assigned_value - center, 6) if assigned_value is not None else None,
        "batch_content_hash": stable_content_hash(content),
    }


def _sorted_ladder(rows: Iterable[Mapping[str, Any]]) -> list[tuple[MarketBracket, dict[str, Any]]]:
    parsed: list[tuple[MarketBracket, dict[str, Any]]] = []
    for source in rows:
        row = dict(source)
        bracket = parse_market_bracket(str(row.get("bracket") or ""), str(row.get("question") or ""))
        if bracket is None:
            raise ValueError(f"unparseable bracket: {row.get('bracket')!r}")
        parsed.append((bracket, row))
    return sorted(parsed, key=lambda item: float("-inf") if item[0].bottom else float(item[0].low))


def materialize_full_ladder_checkpoint(
    rows: Iterable[Mapping[str, Any]],
    *,
    city: str,
    target_date: str,
    event_id: str,
    checkpoint_ts_utc: str,
    feature_book_snapshot_id: str | None,
    horizon_days: int,
) -> dict[str, Any]:
    ordered = _sorted_ladder(rows)
    blockers: list[dict[str, Any]] = []
    if not ordered:
        blockers.append({"code": "empty_ladder"})
    brackets = [item[0] for item in ordered]
    if sum(item.bottom for item in brackets) != 1:
        blockers.append({"code": "bottom_rung_not_unique"})
    if sum(item.top for item in brackets) != 1:
        blockers.append({"code": "top_rung_not_unique"})
    for left, right in zip(brackets, brackets[1:]):
        if left.high is None or right.low is None or abs(float(right.low) - float(left.high) - 1.0) > 1e-9:
            blockers.append({"code": "native_lattice_gap", "left": left.label, "right": right.label})
    raw_mass: list[float] = []
    quote_sources: list[str] = []
    for _, row in ordered:
        bid, ask = row.get("yes_best_bid"), row.get("yes_best_ask")
        if bid is not None and ask is not None:
            raw_mass.append((float(bid) + float(ask)) / 2.0)
            quote_sources.append("two_sided_mid")
        else:
            raw_mass.append(float("nan"))
            quote_sources.append("missing_two_sided_mid")
            blockers.append({"code": "missing_two_sided_mid", "bracket": row.get("bracket")})
    finite_mass = [value for value in raw_mass if value == value]
    normalization = sum(finite_mass)
    normalized = [value / normalization if value == value and normalization > 0 else None for value in raw_mass]
    manifest = [
        {
            "position": index,
            **bracket.as_dict(),
            "condition_id": row.get("condition_id"),
            "token_id": row.get("token_id"),
            "raw_market_mass": None if raw_mass[index] != raw_mass[index] else raw_mass[index],
            "normalized_market_probability": normalized[index],
            "quote_source": quote_sources[index],
            "book_status": row.get("book_status") or quote_sources[index],
        }
        for index, (bracket, row) in enumerate(ordered)
    ]
    if horizon_days == 2 and not feature_book_snapshot_id:
        blockers.append({"code": "d2_market_ladder_unavailable"})
    return {
        "schema_version": FULL_LADDER_SCHEMA,
        "event_id": event_id,
        "city": city,
        "target_date": target_date,
        "horizon_days": int(horizon_days),
        "checkpoint_ts_utc": utc_text(parse_utc(checkpoint_ts_utc, field="checkpoint_ts_utc")),
        "feature_book_snapshot_id": feature_book_snapshot_id,
        "rung_manifest": manifest,
        "native_lattice_ordering": [item[0].label for item in ordered],
        "bottom_open_semantics": "final_native_tmax_at_or_below_high",
        "top_open_semantics": "final_native_tmax_at_or_above_low",
        "rung_completeness": not any(item["code"] in {"empty_ladder", "bottom_rung_not_unique", "top_rung_not_unique", "native_lattice_gap"} for item in blockers),
        "market_distribution_complete": not any(item["code"] == "missing_two_sided_mid" for item in blockers),
        "raw_market_mass_sum": round(normalization, 12),
        "market_normalization_factor": round(normalization, 12) if normalization > 0 else None,
        "ladder_hash": stable_content_hash(manifest),
        "evidence_status": "complete" if not blockers else "blocked",
        "evidence_blockers": blockers,
    }
