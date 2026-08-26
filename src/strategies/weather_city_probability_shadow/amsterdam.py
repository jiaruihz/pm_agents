from __future__ import annotations

import glob
import json
import math
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd

from weather_modeling.knmi_10m_path import add_knmi_10m_path_features
from weather_modeling.solar_geometry import add_solar_geometry_features
from weather_modeling.forecast_path import (
    FORECAST_PATH_FEATURES,
    add_fixed_lead_forecast_path_features,
)
from weather_modeling.amsterdam_market_offset import (
    add_amsterdam_market_offset_features,
)
from weather_model_evaluation.market_offset_probability import (
    predict_fixed_market_offset,
)
from weather_data_feed.input_catalog import JsonlInputCatalog

from .core import CityScore, InputNotReady


def _parse(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _available(row: dict[str, Any]) -> datetime:
    value = (
        row.get("source_first_seen_at_utc")
        or row.get("available_at_utc")
        or row.get("knmi_first_seen_at_utc")
        or row.get("fetched_at_utc")
    )
    return _parse(value)


def _half_up(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _iter_jsonl(path: Path):
    if not path.is_file():
        return
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield line_no, row


def _official_as_of(
    profile: dict[str, Any], target_date: str, decision: datetime, source_obs: datetime
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(profile["observation_journal_dir"])
    cutoff = source_obs - timedelta(minutes=10)
    catalog_row = JsonlInputCatalog().latest_from_day_shard_journals(
        root,
        filename="observations.jsonl",
        as_of=decision,
        available_field="fetched_at_utc",
        predicate=lambda row: (
            row.get("city") == "Amsterdam"
            and row.get("target_date") == target_date
            and bool(row.get("last_obs_utc"))
            and _parse(row["last_obs_utc"]) <= cutoff
        ),
    )
    if catalog_row is None:
        raise InputNotReady(
            "missing_official_checkpoint",
            city="Amsterdam",
            target_date=target_date,
            decision_ts_utc=decision.isoformat(),
            details={"official_cutoff_utc": cutoff.isoformat()},
        )
    return {
        "physical_path": catalog_row.physical_path,
        "physical_line": catalog_row.physical_line,
    }, catalog_row.row


def _source_frame(profile: dict[str, Any], target_date: str, decision: datetime) -> tuple[pd.DataFrame, dict[str, Any], int]:
    source_journal = Path(profile["source_journal"])
    source_root = source_journal if source_journal.is_dir() else source_journal.parent
    source_filename = (
        "knmi_observations.jsonl" if source_journal.is_dir() else source_journal.name
    )
    catalog_rows = JsonlInputCatalog().rows_from_day_shards(
        source_root,
        filename=source_filename,
        as_of=decision,
        predicate=lambda row: (
            row.get("city") == "Amsterdam"
            and row.get("source") == "knmi"
            and row.get("target_date") == target_date
            and row.get("information_event_status") == "material"
        ),
    )
    by_obs: dict[str, tuple[int, dict[str, Any]]] = {}
    for catalog_row in catalog_rows:
        row = catalog_row.row
        fields = row.get("knmi_station_fields") or {}
        if fields.get("ta") is None or fields.get("tx") is None:
            continue
        available = _available(row)
        if available > decision:
            continue
        key = str(row.get("observation_time_utc"))
        previous = by_obs.get(key)
        if previous is None or available > _available(previous[1]):
            by_obs[key] = (catalog_row.physical_line, row)
    if not by_obs:
        raise InputNotReady(
            "missing_knmi_source_day",
            city="Amsterdam",
            target_date=target_date,
            decision_ts_utc=decision.isoformat(),
        )
    ordered = sorted(by_obs.values(), key=lambda item: item[1]["observation_time_utc"])
    records: list[dict[str, Any]] = []
    for _, row in ordered:
        fields = row.get("knmi_station_fields") or {}
        records.append({
            "target_date": target_date,
            "observed_at_utc": row["observation_time_utc"],
            "ta_c": fields.get("ta"),
            "tx_c": fields.get("tx"),
            "solar_w_m2": fields.get("qg"),
            "cloud_okta": fields.get("n"),
            "precip_mm_h": fields.get("rg"),
            "humidity_pct": fields.get("rh"),
            "dewpoint_c": fields.get("td"),
            "wind_speed_mps": fields.get("ff"),
            "wind_gust_mps": fields.get("gff"),
            "wind_direction_deg": fields.get("dd"),
            "pressure_hpa": row.get("pressure_msl_hpa", fields.get("pp")),
        })
    frame = pd.DataFrame(records)
    frame["running_max_c"] = pd.to_numeric(frame["ta_c"], errors="coerce").cummax()
    frame["knmi_ta_running_max_c"] = frame["running_max_c"]
    frame["knmi_tx_running_max_c"] = pd.to_numeric(frame["tx_c"], errors="coerce").cummax()
    frame = add_knmi_10m_path_features(frame)
    frame = add_solar_geometry_features(frame)
    return frame, ordered[-1][1], ordered[-1][0]


def _market_quote(profile: dict[str, Any], source_event_id: str, target_date: str, bracket: int, decision: datetime) -> dict[str, Any]:
    candidates: list[tuple[int, datetime, str, dict[str, Any]]] = []
    pattern = str(Path(profile["ladder_snapshot_dir"]) / target_date / "*.json")
    for filename in glob.glob(pattern):
        try:
            payload = json.loads(Path(filename).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("source_event_id") != source_event_id or payload.get("capture_status") != "complete":
            continue
        captured = _parse(payload.get("capture_started_at_utc"))
        if captured > decision + timedelta(minutes=3):
            continue
        candidates.append((int(payload.get("scheduled_offset_seconds") or 0), captured, filename, payload))
    if not candidates:
        raise InputNotReady(
            "waiting_for_first_seen_ladder",
            city="Amsterdam",
            target_date=target_date,
            decision_ts_utc=decision.isoformat(),
            details={"source_event_id": source_event_id, "bracket": bracket},
        )
    _, _, filename, payload = sorted(candidates, key=lambda item: (item[0], item[1]))[0]
    records = list(payload.get("records", []))
    record = next(
        (row for row in records if str(row.get("bracket")) == str(bracket)),
        None,
    )
    bracket_anchor = "exact"
    if record is None:
        numeric_records = []
        for candidate in records:
            match = re.search(r"-?\d+", str(candidate.get("bracket") or ""))
            if match:
                numeric_records.append((int(match.group()), candidate))
        if numeric_records:
            floor_value, floor_record = min(numeric_records, key=lambda item: item[0])
            ceiling_value, ceiling_record = max(
                numeric_records, key=lambda item: item[0]
            )
            floor_question = str(floor_record.get("question") or "").lower()
            ceiling_question = str(ceiling_record.get("question") or "").lower()
            if bracket < floor_value and "or below" in floor_question:
                record = floor_record
                bracket_anchor = "hard_floor"
            elif bracket > ceiling_value and "or higher" in ceiling_question:
                record = ceiling_record
                bracket_anchor = "hard_ceiling"
    if record is None:
        raise InputNotReady(
            "missing_current_bracket_market",
            city="Amsterdam",
            target_date=target_date,
            decision_ts_utc=decision.isoformat(),
            details={"snapshot": filename, "bracket": bracket},
        )
    resolved_bracket = int(
        re.search(r"-?\d+", str(record.get("bracket"))).group()
    )
    question = str(record.get("question") or "").lower()
    if bracket == resolved_bracket and "or below" in question:
        bracket_anchor = "or_below_current"
    elif bracket == resolved_bracket and "or higher" in question:
        bracket_anchor = "or_higher_current"
    bid = record.get("no_best_bid")
    ask = record.get("no_best_ask")
    bid = None if bid is None else float(bid)
    ask = None if ask is None else float(ask)
    yes_bid = record.get("yes_best_bid")
    yes_ask = record.get("yes_best_ask")
    yes_bid = None if yes_bid is None else float(yes_bid)
    yes_ask = None if yes_ask is None else float(yes_ask)
    snapshot_id = str(
        payload.get("snapshot_id")
        or payload.get("capture_id")
        or Path(filename).stem
    )
    return {
        **record,
        "physical_current_bracket": bracket,
        "resolved_bracket": resolved_bracket,
        "bracket_anchor": bracket_anchor,
        "snapshot_path": filename,
        "book_snapshot_id": snapshot_id,
        "best_bid": bid,
        "best_ask": ask,
        "mid": ((bid + ask) / 2 if bid is not None and ask is not None else None),
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "yes_mid": (
            (yes_bid + yes_ask) / 2
            if yes_bid is not None and yes_ask is not None
            else None
        ),
        "no_asks": list(record.get("no_book_asks") or []),
        "yes_asks": list(record.get("yes_book_asks") or []),
    }


def _pre_event_market_quote(
    profile: dict[str, Any],
    source_event_id: str,
    target_date: str,
    bracket: int,
    decision: datetime,
) -> dict[str, Any]:
    """Return the last captured market state that predates KNMI first-seen.

    The market-offset artifact was trained with a market sample available before
    the weather checkpoint became observable.  Its prior must therefore not be
    substituted with the post-event execution book.
    """

    journal = Path(profile["pre_event_reference_journal"])
    references = [
        payload
        for _, payload in _iter_jsonl(journal)
        if payload.get("source_event_id") == source_event_id
        and payload.get("full_ladder_status") == "found"
    ]
    if not references:
        raise InputNotReady(
            "missing_pre_event_market_reference",
            city="Amsterdam",
            target_date=target_date,
            decision_ts_utc=decision.isoformat(),
            details={"source_event_id": source_event_id},
        )
    reference = references[-1]
    snapshot_path = Path(reference["full_ladder_snapshot_path"])
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if payload.get("source_event_id") != source_event_id:
        raise ValueError("pre-event reference source event mismatch")
    records = [
        row
        for row in payload.get("records", [])
        if row.get("status") == "ok"
        and str(row.get("event_date")) == target_date
    ]
    available = [
        _parse(row.get("available_at_utc") or row.get("fetched_at_utc"))
        for row in records
    ]
    if not records or max(available) >= decision:
        raise InputNotReady(
            "pre_event_market_reference_not_strictly_prior",
            city="Amsterdam",
            target_date=target_date,
            decision_ts_utc=decision.isoformat(),
            details={
                "source_event_id": source_event_id,
                "snapshot_path": str(snapshot_path),
            },
        )

    by_bracket: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        by_bracket.setdefault(str(row.get("bracket")), []).append(row)
    selected_bracket = str(bracket)
    bracket_anchor = "exact"
    if selected_bracket not in by_bracket:
        numeric: list[tuple[int, str]] = []
        for value in by_bracket:
            match = re.search(r"-?\d+", value)
            if match:
                numeric.append((int(match.group()), value))
        if numeric:
            floor_value, floor_key = min(numeric)
            ceiling_value, ceiling_key = max(numeric)
            floor_question = " ".join(
                str(row.get("question") or "") for row in by_bracket[floor_key]
            ).lower()
            ceiling_question = " ".join(
                str(row.get("question") or "") for row in by_bracket[ceiling_key]
            ).lower()
            if bracket < floor_value and "or below" in floor_question:
                selected_bracket = floor_key
                bracket_anchor = "hard_floor"
            elif bracket > ceiling_value and "or higher" in ceiling_question:
                selected_bracket = ceiling_key
                bracket_anchor = "hard_ceiling"
    side_rows = {
        str(row.get("outcome") or "").upper(): row
        for row in by_bracket.get(selected_bracket, [])
    }
    if not {"YES", "NO"}.issubset(side_rows):
        raise InputNotReady(
            "missing_pre_event_current_bracket_market",
            city="Amsterdam",
            target_date=target_date,
            decision_ts_utc=decision.isoformat(),
            details={
                "source_event_id": source_event_id,
                "snapshot_path": str(snapshot_path),
                "bracket": bracket,
            },
        )
    yes_record, no_record = side_rows["YES"], side_rows["NO"]
    question = " ".join(
        str(row.get("question") or "") for row in (yes_record, no_record)
    ).lower()
    resolved_bracket = int(re.search(r"-?\d+", selected_bracket).group())
    if bracket == resolved_bracket and "or below" in question:
        bracket_anchor = "or_below_current"
    elif bracket == resolved_bracket and "or higher" in question:
        bracket_anchor = "or_higher_current"

    def top(row: dict[str, Any], field: str) -> float | None:
        value = (row.get("summary") or {}).get(field)
        return None if value is None else float(value)

    no_bid, no_ask = top(no_record, "best_bid"), top(no_record, "best_ask")
    yes_bid, yes_ask = top(yes_record, "best_bid"), top(yes_record, "best_ask")
    return {
        "resolved_bracket": resolved_bracket,
        "bracket_anchor": bracket_anchor,
        "book_snapshot_id": str(reference.get("full_ladder_snapshot_path")),
        "snapshot_path": str(snapshot_path),
        "latest_available_at_utc": max(available).isoformat(),
        "best_bid": no_bid,
        "best_ask": no_ask,
        "mid": (
            (no_bid + no_ask) / 2
            if no_bid is not None and no_ask is not None
            else None
        ),
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "yes_mid": (
            (yes_bid + yes_ask) / 2
            if yes_bid is not None and yes_ask is not None
            else None
        ),
    }


def _fixed_lead_forecast(
    profile: dict[str, Any], target_date: str, decision: datetime
) -> tuple[pd.DataFrame, dict[str, Any]]:
    root = Path(profile["forecast_previous_day1_dir"])
    candidates: list[tuple[datetime, str, int, dict[str, Any]]] = []
    for path in root.glob("20??-??-??/forecast_hourly_curves_*.jsonl"):
        for line, payload in _iter_jsonl(path):
            if payload.get("city") != "Amsterdam" or payload.get("target_date") != target_date:
                continue
            if payload.get("forecast_source") != "open_meteo_previous_runs_ecmwf_day1":
                continue
            available = _parse(payload.get("available_at_utc"))
            if available <= decision:
                candidates.append((available, str(path), line, payload))
    if not candidates:
        raise InputNotReady(
            "missing_fixed_lead_forecast_path",
            city="Amsterdam",
            target_date=target_date,
            decision_ts_utc=decision.isoformat(),
        )
    _, path, line, payload = max(candidates, key=lambda item: item[0])
    curve = pd.DataFrame(
        {
            "target_date": target_date,
            "forecast_time_local": [row["time_local"] for row in payload["hourly_curve"]],
            "forecast_temperature_c": [
                (float(row["temperature_f"]) - 32.0) * 5.0 / 9.0
                for row in payload["hourly_curve"]
            ],
        }
    )
    return curve, {
        "physical_path": path,
        "physical_line": line,
        "forecast_values_hash": payload.get("forecast_values_hash"),
        "available_at_utc": payload.get("available_at_utc"),
    }


def _predict(artifact: dict[str, Any], frame: pd.DataFrame) -> dict[str, float]:
    if artifact.get("schema_version") == "amsterdam_knmi_cross_survival_model_v1":
        features = list(artifact["features"])
        matrix = frame.loc[:, features].apply(pd.to_numeric, errors="coerce")
        return {
            "p_cross_survives": float(
                artifact["estimator"].predict_proba(matrix)[0, 1]
            )
        }
    if artifact.get("schema_version") in {
        "amsterdam_knmi_remaining_heat_model_v8",
        "amsterdam_knmi_remaining_heat_model_v9",
    }:
        features = list(artifact["features"])
        matrix = frame.loc[:, features].apply(pd.to_numeric, errors="coerce")
        raw = float(artifact["estimator"].predict_proba(matrix)[0, 1])
        calibrator = artifact.get("calibrator")
        if calibrator is not None:
            clipped = float(np.clip(raw, 1e-7, 1 - 1e-7))
            logit = math.log(clipped / (1.0 - clipped))
            selected = float(calibrator.predict_proba([[logit]])[0, 1])
        else:
            selected = raw
        return {
            "p_break_eod": selected,
            "p_raw_eod": raw,
        }
    models = artifact["models"]
    features = list(models["base_features"])
    matrix = frame.loc[:, features].apply(pd.to_numeric, errors="coerce")
    v4 = float(models["v4_binary_model"].predict(frame, features=features)[0])
    structured = models["v5_structured_models"]
    hazards = np.asarray([model.predict_proba(matrix)[0, 1] for model in structured["hazards"]], dtype=float)
    survival = 1.0
    cumulative: list[float] = []
    total = 0.0
    for hazard in np.clip(hazards, 1e-6, 1 - 1e-6):
        total += survival * float(hazard)
        survival *= 1.0 - float(hazard)
        cumulative.append(total)
    v5 = cumulative[-1]
    weight = float(models["binary_blend"]["v5_weight"])
    selected = (1.0 - weight) * v4 + weight * v5
    cumulative[-1] = selected
    cumulative = list(np.maximum.accumulate(np.minimum(cumulative, selected)))
    next_routine = float(structured["next_routine"].predict_proba(matrix)[0, 1])
    return {"p_break_30m": cumulative[0], "p_break_60m": cumulative[1], "p_break_120m": cumulative[2], "p_break_eod": selected, "p_next_routine_confirms": next_routine, "p_v4_eod": v4, "p_v5_eod": v5}


class AmsterdamKnmiRemainingHeatV7Adapter:
    def score(self, profile: dict[str, Any], now: datetime) -> list[CityScore]:
        local_now = now.astimezone(ZoneInfo("Europe/Amsterdam"))
        target_date = local_now.date().isoformat()
        frame, source, source_line = _source_frame(profile, target_date, now)
        decision = _parse(source.get("source_first_seen_at_utc") or source.get("available_at_utc"))
        source_age_seconds = (now - decision).total_seconds()
        if source_age_seconds > float(profile.get("max_source_age_seconds", 900)):
            raise InputNotReady(
                "source_stale",
                city="Amsterdam",
                target_date=target_date,
                decision_ts_utc=now.isoformat(),
                details={
                    "source_event_id": source.get("information_event_id"),
                    "source_age_seconds": source_age_seconds,
                    "max_source_age_seconds": float(
                        profile.get("max_source_age_seconds", 900)
                    ),
                },
            )
        if decision < _parse(profile["forward_start_utc"]):
            raise InputNotReady("before_frozen_forward_start", city="Amsterdam", target_date=target_date, decision_ts_utc=decision.isoformat())
        official_lineage, official = _official_as_of(
            profile, target_date, decision, _parse(source["observation_time_utc"])
        )
        physical_current = _half_up(float(official["running_max_c"]))
        quote = _market_quote(
            profile,
            str(source["information_event_id"]),
            target_date,
            physical_current,
            now,
        )
        current = int(quote.get("resolved_bracket", physical_current))
        row = frame.iloc[-1].copy()
        observed_local = _parse(source["observation_time_utc"]).astimezone(ZoneInfo("Europe/Amsterdam"))
        minute = observed_local.hour * 60 + observed_local.minute
        allowed_source_minutes = profile.get("allowed_source_minutes")
        if (
            allowed_source_minutes is not None
            and observed_local.minute
            not in {int(value) for value in allowed_source_minutes}
        ):
            return []
        allowed_local_hours = profile.get("allowed_local_hours")
        if allowed_local_hours is not None:
            first_hour, last_hour = [int(value) for value in allowed_local_hours]
            if not first_hour <= observed_local.hour <= last_hour:
                return []
        row["decision_minute_local"] = float(minute)
        row["time_sin"] = math.sin(2 * math.pi * minute / 1440)
        row["time_cos"] = math.cos(2 * math.pi * minute / 1440)
        row["official_running_max_c"] = float(official["running_max_c"])
        row["current_bracket_c"] = float(current)
        row["d1_bracket_c"] = float(current + 1)
        row["latest_official_temp_c"] = float(official["current_temp_c"])
        row["official_report_age_minutes"] = (decision - _parse(official["last_obs_utc"])).total_seconds() / 60
        row["distance_to_d1_c"] = current + 0.5 - float(row["ta_c"])
        row["tx_distance_to_d1_c"] = current + 0.5 - float(row["tx_c"])
        row["decline_from_running_max_c"] = float(official["running_max_c"]) - float(row["ta_c"])
        row["minutes_since_running_max"] = float(official.get("minutes_since_running_max") or 0)
        row["tx_minus_ta_c"] = float(row["tx_c"]) - float(row["ta_c"])
        row["knmi_ta_minus_latest_official_c"] = float(row["ta_c"]) - float(official["current_temp_c"])
        row["knmi_tx_minus_latest_official_c"] = float(row["tx_c"]) - float(official["current_temp_c"])
        row["knmi_ta_minus_official_running_max_c"] = float(row["ta_c"]) - float(official["running_max_c"])
        row["knmi_tx_minus_official_running_max_c"] = float(row["tx_c"]) - float(official["running_max_c"])
        row["ta_cross_margin_c"] = float(row["ta_c"]) - float(current)
        row["tx_cross_margin_c"] = float(row["tx_c"]) - float(current)
        row["source_above_official_d1"] = float(float(row["tx_c"]) >= current + 0.5)
        crossed = frame["tx_c"].astype(float).ge(current + 0.5)
        row["source_above_official_d1_persistence_rows"] = float(crossed.iloc[::-1].cumprod().sum())

        if profile.get("required_cross_margin_c") is not None:
            required_margin = float(profile["required_cross_margin_c"])
            crossing_rows = frame[
                pd.to_numeric(frame["ta_c"], errors="coerce").ge(
                    float(current) + required_margin - 1e-9
                )
            ]
            first_cross_obs = (
                None
                if crossing_rows.empty
                else str(crossing_rows.iloc[0]["observed_at_utc"])
            )
            # A non-cross checkpoint is normal absence of this sparse signal,
            # not a coverage blocker.  Returning no scores also prevents later
            # events from re-emitting the same bracket opportunity.
            if first_cross_obs != str(source["observation_time_utc"]):
                return []

        artifact_path = Path(profile["artifacts"]["weather"]["path"])
        artifact = joblib.load(artifact_path)
        artifact_schema = artifact.get("schema_version")
        market_offset_mode = artifact_schema in {
            "fixed_market_logit_offset_v1",
            "fixed_market_logit_offset_v2",
        }
        if market_offset_mode:
            base_declaration = profile["artifacts"].get("base_weather")
            if not base_declaration:
                raise ValueError("market-offset profile requires base_weather artifact")
            base_artifact = joblib.load(Path(base_declaration["path"]))
            if (
                str(base_declaration["sha256"])
                != str(artifact["base_weather_artifact_sha256"])
            ):
                raise ValueError("market-offset base weather artifact hash mismatch")
            if base_artifact.get("model_id") != artifact.get("base_weather_model_id"):
                raise ValueError("market-offset base weather model id mismatch")
            base_features = list(base_artifact["features"])
        elif artifact_schema in {
            "amsterdam_knmi_remaining_heat_model_v8",
            "amsterdam_knmi_remaining_heat_model_v9",
            "amsterdam_knmi_cross_survival_model_v1",
        }:
            base_features = list(artifact["features"])
        else:
            base_features = list(artifact["models"]["base_features"])
        forecast_lineage = None
        if any(feature in base_features for feature in FORECAST_PATH_FEATURES):
            forecast_curve, forecast_lineage = _fixed_lead_forecast(
                profile, target_date, decision
            )
            row_frame = add_fixed_lead_forecast_path_features(
                pd.DataFrame([row]), forecast_curve
            )
            row = row_frame.iloc[0].copy()
        structurally_missing = sorted(set(base_features) - set(row.index))
        if structurally_missing:
            raise InputNotReady(
                "model_feature_contract_incomplete",
                city="Amsterdam",
                target_date=target_date,
                decision_ts_utc=decision.isoformat(),
                details={
                    "missing_feature_columns": structurally_missing,
                    "model_id": artifact.get("model_id"),
                },
            )
        for feature in base_features:
            if feature not in row.index:
                row[feature] = np.nan
        score_frame = pd.DataFrame([row], columns=base_features)
        if market_offset_mode:
            prior_quote = _pre_event_market_quote(
                profile,
                str(source["information_event_id"]),
                target_date,
                current,
                decision,
            )
            if int(prior_quote["resolved_bracket"]) != current:
                raise InputNotReady(
                    "pre_event_execution_bracket_mismatch",
                    city="Amsterdam",
                    target_date=target_date,
                    decision_ts_utc=decision.isoformat(),
                    details={
                        "pre_event_bracket": prior_quote["resolved_bracket"],
                        "execution_bracket": current,
                    },
                )
            supported_anchors = {"exact", "or_below_current"}
            if quote.get("bracket_anchor") not in supported_anchors or prior_quote.get(
                "bracket_anchor"
            ) not in supported_anchors:
                raise InputNotReady(
                    "unsupported_market_offset_bracket_anchor",
                    city="Amsterdam",
                    target_date=target_date,
                    decision_ts_utc=decision.isoformat(),
                    details={
                        "prior_anchor": prior_quote.get("bracket_anchor"),
                        "execution_anchor": quote.get("bracket_anchor"),
                    },
                )
            if prior_quote["mid"] is None:
                raise InputNotReady(
                    "market_prior_midpoint_interval_censored",
                    city="Amsterdam",
                    target_date=target_date,
                    decision_ts_utc=decision.isoformat(),
                    details={
                        "source_event_id": source["information_event_id"],
                        "book_snapshot_id": prior_quote.get("book_snapshot_id"),
                    },
                )
            base_probabilities = _predict(base_artifact, score_frame)
            residual_frame = pd.DataFrame([row])
            residual_frame["p_model"] = base_probabilities["p_break_eod"]
            residual_frame["market_p"] = float(prior_quote["mid"])
            residual_frame = add_amsterdam_market_offset_features(
                residual_frame,
                required_features=list(artifact["feature_columns"]),
            )
            posterior = float(
                predict_fixed_market_offset(
                    artifact,
                    residual_frame,
                    market_probability=residual_frame["market_p"],
                )[0]
            )
            probabilities = {
                "p_break_eod": posterior,
                "p_weather_eod": float(base_probabilities["p_break_eod"]),
                "p_market_prior": float(prior_quote["mid"]),
            }
            score_feature_names = [
                *base_features,
                *list(artifact["feature_columns"]),
            ]
            score_feature_row = residual_frame.iloc[0]
        else:
            probabilities = _predict(artifact, score_frame)
            score_feature_names = base_features
            score_feature_row = score_frame.iloc[0]
        cross_survival_mode = (
            artifact_schema == "amsterdam_knmi_cross_survival_model_v1"
        )
        if (
            profile.get("required_cross_margin_c") is not None
            and not cross_survival_mode
        ):
            raise ValueError("required_cross_margin_c requires cross-survival artifact")
        present = sum(
            pd.notna(score_feature_row[name]) for name in score_feature_names
        )
        missing = [
            name for name in score_feature_names if pd.isna(score_feature_row[name])
        ]
        shared_lineage = {
            "profile_id": profile["profile_id"],
            "probability_policy": str(artifact["model_id"]),
            "model_artifact_sha256": profile["artifacts"]["weather"]["sha256"],
            "base_weather_artifact_sha256": (
                profile["artifacts"]["base_weather"]["sha256"]
                if market_offset_mode
                else None
            ),
            "book_snapshot_id": quote.get("book_snapshot_id"),
            "physical_current_bracket": physical_current,
            "expression_current_bracket": current,
            "bracket_anchor": quote.get("bracket_anchor", "exact"),
            "market_feature_clock": (
                "strictly_pre_knmi_first_seen"
                if market_offset_mode
                else "knmi_first_seen_ladder_t0"
            ),
            "market_feature_role": (
                "prior_offset" if market_offset_mode else "joint_feature"
            ),
            "feature_book_snapshot_id": (
                prior_quote.get("book_snapshot_id")
                if market_offset_mode
                else quote.get("book_snapshot_id")
            ),
            "market_prior_book_snapshot_id": (
                prior_quote.get("book_snapshot_id") if market_offset_mode else None
            ),
            "market_execution_clock": "knmi_first_seen_ladder_t0",
            "execution_book_snapshot_id": quote.get("book_snapshot_id"),
            "source_first_seen_at_utc": decision.isoformat(),
            "source_journal": profile["source_journal"],
            "source_line": source_line,
            "source_event_id": source["information_event_id"],
            "official_journal": official_lineage["physical_path"],
            "official_line": official_lineage["physical_line"],
            "forecast_input_ref": forecast_lineage,
            "feature_schema_coverage": 1.0,
            "forecast_feature_semantics": (
                "immutable_ecmwf_previous_day1_fixed_24h"
                if forecast_lineage is not None
                else "excluded_until_historical_live_source_semantics_match"
            ),
        }
        scores: list[CityScore] = []
        for side in profile.get("expression_sides", ["NO"]):
            normalized_side = str(side).upper()
            if normalized_side not in {"YES", "NO"}:
                raise ValueError(f"unsupported Amsterdam expression side: {side}")
            is_no = normalized_side == "NO"
            if market_offset_mode:
                market_feature_probability = (
                    prior_quote["mid"] if is_no else prior_quote["yes_mid"]
                )
            else:
                market_feature_probability = (
                    quote["mid"] if is_no else quote["yes_mid"]
                )
            # Probability scoring and execution must use the same first-seen t0
            # book.  A market-offset profile may consume an earlier book as a
            # feature prior, but that prior is not the contemporaneous market
            # baseline and must never be exported as such.
            market_probability = quote["mid"] if is_no else quote["yes_mid"]
            market_entry = quote["best_ask"] if is_no else quote["yes_ask"]
            p_leave = probabilities[
                "p_cross_survives" if cross_survival_mode else "p_break_eod"
            ]
            scorable = market_probability is not None
            scores.append(CityScore(
                city="Amsterdam",
                target_date=target_date,
                decision_ts_utc=decision.isoformat(),
                source_obs_ts_utc=str(source["observation_time_utc"]),
                current_bracket=current,
                market_side=normalized_side,
                market_probability=market_probability,
                market_entry_price=market_entry,
                model_probability=p_leave if is_no else 1.0 - p_leave,
                model_id=str(artifact["model_id"]),
                feature_coverage=present / len(score_feature_names),
                missing_features=missing,
                features={key: float(value) for key, value in probabilities.items()},
                market={
                    "condition_id": quote.get("condition_id"),
                    "market_id": quote.get("market_id"),
                    "token_id": quote.get("no_token_id" if is_no else "yes_token_id"),
                    "outcome": normalized_side,
                    "book_snapshot_id": quote.get("book_snapshot_id"),
                    "best_bid": quote.get("best_bid" if is_no else "yes_bid"),
                    "best_ask": market_entry,
                    "mid": market_probability,
                    "snapshot_path": quote.get("snapshot_path"),
                    "scheduled_offset_seconds": quote.get("scheduled_offset_seconds"),
                    "prior_mid": market_feature_probability,
                    "market_feature_probability": market_feature_probability,
                    "market_execution_probability": market_probability,
                    "prior_snapshot_path": (
                        prior_quote.get("snapshot_path")
                        if market_offset_mode
                        else None
                    ),
                    "raw": {
                        "asks": quote.get("no_asks" if is_no else "yes_asks") or []
                    },
                },
                lineage={
                    **shared_lineage,
                    "probability_target": (
                        "previous_bracket_survives_first_cross"
                        if cross_survival_mode
                        else "leave_current_exact_bracket"
                        if is_no else "stay_current_exact_bracket"
                    ),
                },
                evaluation_status="scored" if scorable else "not_scorable",
                not_scorable_reason=(
                    None if scorable else "market_midpoint_interval_censored"
                ),
            ))
        return scores
