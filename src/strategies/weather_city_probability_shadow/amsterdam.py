from __future__ import annotations

import glob
import json
import math
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


def _official_as_of(profile: dict[str, Any], target_date: str, decision: datetime, source_obs: datetime) -> tuple[int, dict[str, Any]]:
    path = Path(profile["observation_journal_dir"]) / "observations.jsonl"
    latest: tuple[int, dict[str, Any]] | None = None
    cutoff = source_obs - timedelta(minutes=10)
    for line_no, row in _iter_jsonl(path):
        if row.get("city") != "Amsterdam" or row.get("target_date") != target_date:
            continue
        if not row.get("fetched_at_utc") or not row.get("last_obs_utc"):
            continue
        fetched = _parse(row["fetched_at_utc"])
        report = _parse(row["last_obs_utc"])
        if fetched <= decision and report <= cutoff:
            latest = (line_no, row)
    if latest is None:
        raise InputNotReady(
            "missing_official_checkpoint",
            city="Amsterdam",
            target_date=target_date,
            decision_ts_utc=decision.isoformat(),
            details={"official_cutoff_utc": cutoff.isoformat()},
        )
    return latest


def _source_frame(profile: dict[str, Any], target_date: str, decision: datetime) -> tuple[pd.DataFrame, dict[str, Any], int]:
    by_obs: dict[str, tuple[int, dict[str, Any]]] = {}
    for line_no, row in _iter_jsonl(Path(profile["source_journal"])):
        if row.get("city") != "Amsterdam" or row.get("source") != "knmi":
            continue
        if row.get("target_date") != target_date or row.get("information_event_status") != "material":
            continue
        fields = row.get("knmi_station_fields") or {}
        if fields.get("ta") is None or fields.get("tx") is None:
            continue
        available = _available(row)
        if available > decision:
            continue
        key = str(row.get("observation_time_utc"))
        previous = by_obs.get(key)
        if previous is None or available > _available(previous[1]):
            by_obs[key] = (line_no, row)
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
    record = next((row for row in payload.get("records", []) if str(row.get("bracket")) == str(bracket)), None)
    if record is None:
        raise InputNotReady(
            "missing_current_bracket_market",
            city="Amsterdam",
            target_date=target_date,
            decision_ts_utc=decision.isoformat(),
            details={"snapshot": filename, "bracket": bracket},
        )
    bid = record.get("no_best_bid")
    ask = record.get("no_best_ask")
    bid = None if bid is None else float(bid)
    ask = None if ask is None else float(ask)
    snapshot_id = str(
        payload.get("snapshot_id")
        or payload.get("capture_id")
        or Path(filename).stem
    )
    return {
        **record,
        "snapshot_path": filename,
        "book_snapshot_id": snapshot_id,
        "best_bid": bid,
        "best_ask": ask,
        "mid": ((bid + ask) / 2 if bid is not None and ask is not None else None),
    }


def _predict(artifact: dict[str, Any], frame: pd.DataFrame) -> dict[str, float]:
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
        official_line, official = _official_as_of(profile, target_date, decision, _parse(source["observation_time_utc"]))
        current = _half_up(float(official["running_max_c"]))
        row = frame.iloc[-1].copy()
        observed_local = _parse(source["observation_time_utc"]).astimezone(ZoneInfo("Europe/Amsterdam"))
        minute = observed_local.hour * 60 + observed_local.minute
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
        row["source_above_official_d1"] = float(float(row["tx_c"]) >= current + 0.5)
        crossed = frame["tx_c"].astype(float).ge(current + 0.5)
        row["source_above_official_d1_persistence_rows"] = float(crossed.iloc[::-1].cumprod().sum())

        artifact_path = Path(profile["artifacts"]["weather"]["path"])
        artifact = joblib.load(artifact_path)
        base_features = list(artifact["models"]["base_features"])
        for feature in base_features:
            if feature not in row.index:
                row[feature] = np.nan
        score_frame = pd.DataFrame([row], columns=base_features)
        probabilities = _predict(artifact, score_frame)
        quote = _market_quote(profile, str(source["information_event_id"]), target_date, current, now)
        present = sum(pd.notna(score_frame.iloc[0][name]) for name in base_features)
        missing = [name for name in base_features if pd.isna(score_frame.iloc[0][name])]
        scorable = quote["mid"] is not None
        return [CityScore(
            city="Amsterdam",
            target_date=target_date,
            decision_ts_utc=decision.isoformat(),
            source_obs_ts_utc=str(source["observation_time_utc"]),
            current_bracket=current,
            market_side="NO",
            market_probability=quote["mid"],
            market_entry_price=quote["best_ask"],
            model_probability=probabilities["p_break_eod"],
            model_id=str(artifact["model_id"]),
            feature_coverage=present / len(base_features),
            missing_features=missing,
            features={key: float(value) for key, value in probabilities.items()},
            market={
                "condition_id": quote.get("condition_id"),
                "market_id": quote.get("market_id"),
                "token_id": quote.get("no_token_id"),
                "outcome": "NO",
                "book_snapshot_id": quote.get("book_snapshot_id"),
                **{
                    key: quote.get(key)
                    for key in (
                        "no_token_id",
                        "best_bid",
                        "best_ask",
                        "mid",
                        "snapshot_path",
                        "scheduled_offset_seconds",
                    )
                },
            },
            lineage={
                "profile_id": profile["profile_id"],
                "probability_policy": "amsterdam_knmi_remaining_heat_v7",
                "model_artifact_sha256": profile["artifacts"]["weather"]["sha256"],
                "book_snapshot_id": quote.get("book_snapshot_id"),
                "market_feature_clock": "knmi_first_seen_ladder_t0",
                "source_journal": profile["source_journal"],
                "source_line": source_line,
                "source_event_id": source["information_event_id"],
                "official_journal": str(Path(profile["observation_journal_dir"]) / "observations.jsonl"),
                "official_line": official_line,
                "forecast_feature_semantics": "frozen_previous_day1_forcing_unavailable_live_explicit_nan",
            },
            evaluation_status="scored" if scorable else "not_scorable",
            not_scorable_reason=None if scorable else "market_midpoint_interval_censored",
        )]
