from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd

from weather_data_feed.input_catalog import JsonlInputCatalog

from .core import CityScore, InputNotReady


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_jsonl(path: Path, predicate) -> dict[str, Any]:
    latest: dict[str, Any] | None = None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if predicate(row):
                latest = row
    if latest is None:
        raise RuntimeError(f"no matching row in {path}")
    return latest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_artifact(spec: dict[str, Any]) -> Path:
    path = Path(spec["path"])
    actual = _sha256(path)
    if actual != spec["sha256"]:
        raise RuntimeError(f"artifact hash mismatch {path}: {actual}")
    return path


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, value))))


def _offset_probability(artifact: dict[str, Any], features: dict[str, Any], market_p: float) -> float:
    names = artifact["features"]
    raw = np.asarray([features.get(name, np.nan) for name in names], dtype=float)
    median = np.asarray([artifact["median"][name] for name in names], dtype=float)
    raw = np.where(np.isfinite(raw), raw, median)
    mean = np.asarray([artifact["mean"][name] for name in names], dtype=float)
    scale = np.asarray([artifact["scale"][name] for name in names], dtype=float)
    scaled = (raw - mean) / scale
    beta = np.asarray(artifact["beta"], dtype=float)
    logit_market = math.log(np.clip(market_p, 1e-6, 1 - 1e-6) / np.clip(1 - market_p, 1e-6, 1))
    return _sigmoid(float(logit_market + beta[0] + scaled @ beta[1:]))


def _path_state(features: dict[str, Any]) -> str:
    slope = float(features.get("temp_slope_30m_cph") or 0.0)
    pullback = float(features.get("pullback_depth_c") or 0.0)
    if slope > 0.3 and pullback < 0.2:
        return "fresh_runway"
    if pullback <= 0.1 and abs(slope) <= 0.3:
        return "plateau"
    if pullback > 0.1 and slope >= -0.3:
        return "pullback"
    return "fade"


def _hgb_probability(artifact: dict[str, Any], features: dict[str, Any]) -> float:
    matrix = np.asarray([[features.get(name, np.nan) for name in artifact["features"]]], dtype=float)
    hazards = np.asarray([m.predict_proba(matrix)[0, 1] for m in artifact["event_hazard_models"]])
    return float(1.0 - np.prod(1.0 - np.clip(hazards, 1e-9, 1 - 1e-9)))


def _fade_probabilities(artifact: dict[str, Any], features: dict[str, Any]) -> dict[str, float]:
    output = {label.removeprefix("label_break_"): 0.0 for label in artifact["labels"]}
    if features["path_state"] != "fade":
        return output
    row = dict(features)
    row["log_minutes_since_strict_high"] = math.log1p(max(0.0, float(row.get("minutes_since_strict_high") or 0.0)))
    matrix = np.asarray([[row.get(name, np.nan) for name in artifact["features"]]], dtype=float)
    probabilities = [artifact["models"][label].predict_proba(matrix)[0, 1] for label in artifact["labels"]]
    coherent = np.maximum.accumulate(probabilities)
    return {key: float(coherent[index]) for index, key in enumerate(output)}


def _fmi_history(
    path: Path, target_date: str, available_through: datetime | None = None
) -> list[dict[str, Any]]:
    by_obs: dict[str, dict[str, Any]] = {}
    found_target = False
    for catalog_row in JsonlInputCatalog.iter_path_reverse(path):
        row = catalog_row.row
        if row.get("city") != "Helsinki" or row.get("source") != "fmi":
            continue
        row_target = row.get("target_date")
        if found_target and row_target != target_date:
            break
        if row_target != target_date:
            continue
        found_target = True
        available = pd.Timestamp(row.get("source_first_seen_at_utc"))
        if available_through is not None and available > pd.Timestamp(available_through):
            continue
        key = str(row.get("observation_time_utc"))
        if key and (
            key not in by_obs
            or str(row.get("source_first_seen_at_utc", ""))
            < str(by_obs[key].get("source_first_seen_at_utc", ""))
        ):
            by_obs[key] = row
    return sorted(by_obs.values(), key=lambda row: row["observation_time_utc"])


def _latest_forecast(root: Path, target_date: str, decision: datetime) -> dict[str, Any]:
    catalog = JsonlInputCatalog(
        day_shard_lookback_days=1,
        day_shard_lookahead_days=0,
        physical_shard_timezone="Asia/Shanghai",
    )
    selected = catalog.latest_from_snapshot_files(
        root,
        pattern="*.jsonl",
        as_of=decision,
        available_field="available_at_utc",
        predicate=lambda row: (
            row.get("city") == "Helsinki" and row.get("target_date") == target_date
        ),
    )
    if selected is None:
        raise RuntimeError(f"no PIT forecast for Helsinki {target_date}")
    return {
        **selected.row,
        "_input_ref": {
            "physical_path": selected.physical_path,
            "physical_line": selected.physical_line,
        },
    }


def _forecast_features(row: dict[str, Any], decision: datetime, boundary: float, current_temp: float) -> dict[str, Any]:
    tz = ZoneInfo("Europe/Helsinki")
    local_now = decision.astimezone(tz).replace(tzinfo=None)
    curve = row["hourly_curve"]
    times = [datetime.fromisoformat(x["time_local"]) for x in curve]
    temps = np.asarray([(float(x["temperature_f"]) - 32) * 5 / 9 for x in curve])
    hours = np.asarray([(t - local_now).total_seconds() / 3600 for t in times])
    current = float(np.interp(0.0, hours, temps))
    future_mask = hours >= 0
    if not future_mask.any():
        raise RuntimeError("forecast curve has no future hours")
    future_temps = temps[future_mask]
    future_hours = hours[future_mask]
    peak_idx = int(np.argmax(future_temps))
    peak = float(future_temps[peak_idx])
    peak_h = float(future_hours[peak_idx])
    cloud = np.asarray([float(x.get("cloud_cover_pct") or 0) for x in curve])[future_mask]
    wind = np.asarray([float(x.get("wind_speed_10m_kt") or 0) * 1.852 for x in curve])[future_mask]
    above = np.maximum(future_temps - boundary, 0)
    return {
        "forecast_available": 1.0,
        "forecast_peak_h": peak_h,
        "forecast_run_age_h": (decision - pd.Timestamp(row["available_at_utc"]).to_pydatetime()).total_seconds() / 3600,
        "forecast_current_innovation_c": current_temp - current,
        "forecast_day_peak_margin_vs_running_c": peak - (boundary - 0.5),
        "forecast_future_peak_margin_vs_running_c": peak - (boundary - 0.5),
        "forecast_future_peak_margin_vs_boundary_c": peak - boundary,
        "forecast_signed_minutes_to_day_peak": peak_h * 60,
        "forecast_minutes_to_future_peak": max(0.0, peak_h * 60),
        "forecast_future_heat_area_above_boundary": float(np.trapezoid(above, future_hours)) if len(above) > 1 else 0.0,
        "forecast_future_hours_above_boundary": float(np.sum(above > 0)),
        "forecast_future_cloud_mean_pct": float(np.mean(cloud)),
        "forecast_future_wind_mean_kmh": float(np.mean(wind)),
        "forecast_day_peak_passed": float(peak_h < 0),
        "forecast_minutes_since_day_peak": max(0.0, -peak_h * 60),
        "forecast_minutes_until_day_peak": max(0.0, peak_h * 60),
        "forecast_future_peak_discount_from_day_peak_c": 0.0,
        "forecast_future_reheat_strength_c": peak - current,
        "forecast_future_peak_drop_to_eod_c": peak - float(future_temps[-1]),
        "forecast_future_heat_integral_c_h": float(np.trapezoid(np.maximum(future_temps-current, 0), future_hours)) if len(future_temps)>1 else 0.0,
        "forecast_future_above_boundary_duration_h": float(np.sum(above > 0)),
    }


def _quote(
    profile: dict[str, Any],
    target_date: str,
    bracket: int,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    catalog = JsonlInputCatalog()
    selected = catalog.latest_from_discovered_journals(
        Path(profile["book_dir"]),
        pattern="*.jsonl",
        as_of=as_of or datetime.now(tz=ZoneInfo("UTC")),
        available_field="book_fetched_at_utc",
        predicate=lambda x: (
            x.get("target_date") == target_date
            and str(x.get("bracket")) == str(bracket)
            and x.get("outcome") == "no"
            and x.get("book_status") == "ok"
        ),
    )
    if selected is None:
        raise InputNotReady(
            "missing_market_expression",
            city="Helsinki",
            target_date=target_date,
            decision_ts_utc=(as_of or datetime.now(tz=ZoneInfo("UTC"))).isoformat(),
            details={"bracket": bracket, "outcome": "no"},
        )
    row = {
        **selected.row,
        "_input_ref": {
            "physical_path": selected.physical_path,
            "physical_line": selected.physical_line,
        },
    }
    summary = row.get("summary", {})
    ask, bid = summary.get("best_ask"), summary.get("best_bid")
    if ask is None and bid is None:
        raise RuntimeError(f"current bracket {bracket} NO book is empty")
    ask_value = None if ask is None else float(ask)
    bid_value = None if bid is None else float(bid)
    quote_state = "two_sided" if bid_value is not None and ask_value is not None else (
        "one_sided_near_binary_ask" if ask_value is not None and ask_value <= 0.01
        else "one_sided_near_binary_bid" if bid_value is not None and bid_value >= 0.99
        else "one_sided_ask_only" if ask_value is not None
        else "one_sided_bid_only"
    )
    snapshot_payload = {
        "condition_id": row.get("condition_id"),
        "token_id": row.get("token_id"),
        "ts_utc": row.get("ts_utc"),
        "source_obs_ts_utc": row.get("source_obs_ts_utc"),
        "raw": row.get("raw"),
    }
    snapshot_id = hashlib.sha256(
        json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        **row,
        "best_ask": ask_value,
        "best_bid": bid_value,
        "quote_state": quote_state,
        "market_probability_status": (
            "two_sided_midpoint"
            if bid_value is not None and ask_value is not None
            else "interval_censored"
        ),
        "market_probability_lower": bid_value if bid_value is not None else 0.0,
        "market_probability_upper": ask_value if ask_value is not None else 1.0,
        "execution_status": "executable_ask" if ask_value is not None else "not_executable_no_ask",
        "book_snapshot_id": snapshot_id,
        "quote_row_ts_utc": row.get("ts_utc"),
    }


def _official_helsinki_as_of(
    journal_dir: Path, target_date: str, as_of: datetime
) -> dict[str, Any] | None:
    catalog = JsonlInputCatalog(
        day_shard_lookback_days=2,
        day_shard_lookahead_days=0,
        physical_shard_timezone="UTC",
    )
    selected = catalog.latest_from_day_shard_journals(
        journal_dir,
        filename="observations.jsonl",
        as_of=as_of,
        available_field="fetched_at_utc",
        predicate=lambda row: (
            row.get("city") == "Helsinki"
            and row.get("target_date") == target_date
            and row.get("status") == "ok"
            and row.get("running_max_c") is not None
            and row.get("last_obs_utc") is not None
            and pd.Timestamp(row.get("last_obs_utc")) <= pd.Timestamp(as_of)
        ),
    )
    if selected is None:
        return None
    return {
        **selected.row,
        "_input_ref": {
            "physical_path": selected.physical_path,
            "physical_line": selected.physical_line,
        },
    }


class HelsinkiRemainingHeatAdapter:
    def score(self, profile: dict[str, Any], now: datetime) -> list[CityScore]:
        forward_start = pd.Timestamp(profile["forward_start_utc"]).to_pydatetime()
        if now < forward_start:
            return []
        artifacts = {key: joblib.load(_verify_artifact(value)) for key, value in profile["artifacts"].items()}
        target_date = now.astimezone(ZoneInfo("Europe/Helsinki")).date().isoformat()
        official = _official_helsinki_as_of(
            Path(profile["observation_journal_dir"]), target_date, now
        )
        if official is None:
            raise InputNotReady(
                "awaiting_official_observation",
                city="Helsinki",
                target_date=target_date,
                decision_ts_utc=now.isoformat(),
                details={"observation_journal_dir": profile["observation_journal_dir"]},
            )
        current_x = int(round(float(official["running_max_c"])))
        quote = _quote(profile, target_date, current_x, now)
        decision = pd.Timestamp(quote["book_fetched_at_utc"]).to_pydatetime()
        if decision < forward_start:
            return []
        book_age = (now - decision).total_seconds()
        if book_age > float(profile["max_book_age_seconds"]):
            raise InputNotReady(
                "stale_market_expression",
                city="Helsinki",
                target_date=target_date,
                decision_ts_utc=decision.isoformat(),
                details={
                    "book_snapshot_id": quote.get("book_snapshot_id"),
                    "book_input_ref": quote.get("_input_ref"),
                    "max_book_age_seconds": float(profile["max_book_age_seconds"]),
                },
            )
        official = _official_helsinki_as_of(
            Path(profile["observation_journal_dir"]), target_date, decision
        )
        if official is None:
            raise InputNotReady(
                "awaiting_official_observation",
                city="Helsinki",
                target_date=target_date,
                decision_ts_utc=decision.isoformat(),
                details={"book_input_ref": quote.get("_input_ref")},
            )
        official_bracket = int(round(float(official["running_max_c"])))
        if official_bracket != current_x:
            raise InputNotReady(
                "anchor_capture_gap",
                city="Helsinki",
                target_date=target_date,
                decision_ts_utc=decision.isoformat(),
                details={
                    "book_expression_anchor": current_x,
                    "official_anchor": official_bracket,
                    "book_input_ref": quote.get("_input_ref"),
                    "official_input_ref": official.get("_input_ref"),
                },
            )
        source_obs_ts = str(quote["source_obs_ts_utc"])
        history = [row for row in _fmi_history(Path(profile["source_journal"]), target_date, decision)
                   if str(row["observation_time_utc"]) <= source_obs_ts]
        if len(history) < 4:
            raise RuntimeError("need at least four unique PIT FMI observations")
        source = history[-1]
        market_p = (
            (quote["best_ask"] + quote["best_bid"]) / 2
            if quote["best_bid"] is not None and quote["best_ask"] is not None
            else None
        )
        temps = np.asarray([float(row["temp_c"]) for row in history])
        local = decision.astimezone(ZoneInfo("Europe/Helsinki"))
        features: dict[str, Any] = {
            "official_running_max_c": float(official["running_max_c"]),
            "fmi_running_max_c": float(np.max(temps)),
            "pullback_depth_c": float(np.max(temps) - temps[-1]),
            "distance_to_next_official_boundary_c": current_x + 0.5 - temps[-1],
            "fmi_official_lattice_basis_c": math.floor(temps[-1] + 0.5) - current_x,
            "source_to_official_level_basis_c": temps[-1] - float(official["current_temp_c"]),
            "source_cadence_gap_min": 10.0,
            "temp_delta_10m": temps[-1] - temps[-2],
            "temp_delta_20m": temps[-1] - temps[-3],
            "temp_slope_30m_cph": (temps[-1] - temps[-4]) * 2.0,
            "temp_slope_60m_cph": temps[-1] - temps[max(0, len(temps)-7)],
            "temp_acceleration_20m": (temps[-1]-temps[-2])-(temps[-2]-temps[-3]),
            "minutes_since_strict_high": float(official.get("minutes_since_last_strict_new_high") or 0),
            "plateau_duration_min": float(official.get("minutes_since_last_running_max") or 0),
            "relative_humidity_pct": official.get("relative_humidity_pct"),
            "dewpoint_depression_c": float(official.get("dewpoint_depression_f") or 0) * 5/9,
            "wind_speed_ms": float(source.get("wind_speed_kt") or 0) * 0.514444,
            "pressure_hpa": source.get("pressure_hpa"),
            "cloud_cover_okta": {"CLR":0,"FEW":2,"SCT":4,"BKN":6,"OVC":8}.get(official.get("sky_cover_code")),
            "precipitation_10m_mm": None,
            "present_weather_code": float(bool(official.get("present_weather_codes"))),
            "official_latest_pullback_c": float(official["running_max_c"])-float(official["current_temp_c"]),
            "official_report_age_min": max(0.0, (decision-pd.Timestamp(official["last_obs_utc"]).to_pydatetime()).total_seconds()/60),
            "fmi_minus_official_latest_temp_c": temps[-1]-float(official["current_temp_c"]),
            "local_hour_sin": math.sin(2*math.pi*(local.hour+local.minute/60)/24),
            "local_hour_cos": math.cos(2*math.pi*(local.hour+local.minute/60)/24),
            "doy_sin": math.sin(2*math.pi*local.timetuple().tm_yday/365.25),
            "doy_cos": math.cos(2*math.pi*local.timetuple().tm_yday/365.25),
        }
        forecast = _latest_forecast(Path(profile["forecast_curve_dir"]), target_date, decision)
        features.update(_forecast_features(forecast, decision, current_x+0.5, temps[-1]))
        features["path_state"] = _path_state(features)
        weather_p = _hgb_probability(artifacts["weather"], features)
        features["weather_market_logit_gap"] = (
            math.log(np.clip(weather_p,1e-6,1-1e-6)/(1-np.clip(weather_p,1e-6,1-1e-6)))
            - math.log(market_p/(1-market_p))
            if market_p is not None
            else None
        )
        fade = _fade_probabilities(artifacts["fade"], features)
        features.update({f"fade_reheat_p_{key}": value for key, value in fade.items()})
        for state in ("pullback", "fade", "plateau"):
            features[f"path_{state}"] = float(features["path_state"] == state)
        outputs = []
        for artifact_key in profile["expression_models"]:
            artifact = artifacts[artifact_key]
            missing = [name for name in artifact["features"] if features.get(name) is None or not np.isfinite(features.get(name, np.nan))]
            probability = _offset_probability(artifact, features, market_p) if market_p is not None else None
            outputs.append(CityScore(
                city="Helsinki", target_date=target_date, decision_ts_utc=decision.isoformat(),
                source_obs_ts_utc=source_obs_ts, current_bracket=current_x,
                market_side="NO", market_probability=market_p, market_entry_price=quote["best_ask"],
                model_probability=probability, model_id=str(artifact.get("model_id") or artifact.get("candidate_name")),
                feature_coverage=1-len(missing)/len(artifact["features"]), missing_features=missing,
                features={name: features.get(name) for name in artifact["features"]}, market=quote,
                lineage={"source":"fmi","source_first_seen_at_utc":source["source_first_seen_at_utc"],
                         "source_payload_hash":source.get("payload_hash"),
                         "source_raw_payload_hash":source.get("raw_payload_hash"),
                         "book_fetched_at_utc":quote["book_fetched_at_utc"],
                         "book_snapshot_id":quote["book_snapshot_id"],
                         "book_input_ref":quote.get("_input_ref"),
                         "official_source":official["source"],"official_last_obs_utc":official["last_obs_utc"],
                         "official_input_ref":official.get("_input_ref"),
                         "forecast_available_at_utc":forecast.get("available_at_utc"),
                         "forecast_values_hash":forecast.get("forecast_values_hash"),
                         "forecast_payload_hash":forecast.get("payload_hash"),
                         "forecast_input_ref":forecast.get("_input_ref"),
                         "model_artifact_sha256":profile["artifacts"][artifact_key]["sha256"],
                         "source_lattice_anchor":int(math.floor(temps[-1] + 0.5)),
                         "official_lattice_anchor":current_x,
                         "market_expression_anchor":current_x,
                         "profile_id":profile.get("profile_id", "helsinki_remaining_heat_v1"),
                         "weather_probability":weather_p,"path_state":features["path_state"]},
                evaluation_status="scored" if market_p is not None else "not_scorable",
                not_scorable_reason=(
                    None if market_p is not None else "one_sided_market_probability_interval"
                ),
            ))
        return outputs
