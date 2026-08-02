from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import joblib
import numpy as np

from weather_data_feed.input_catalog import JsonlInputCatalog
from weather_data_feed.market_brackets import bracket_contains, parse_label_dict

from .core import CityScore, InputNotReady, iter_compatible_evaluations


UTC = timezone.utc
TOKYO = ZoneInfo("Asia/Tokyo")
EPS = 1e-8


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verified_path(spec: dict[str, Any]) -> Path:
    path = Path(spec["path"])
    actual = _sha256(path)
    if actual != spec["sha256"]:
        raise RuntimeError(f"artifact hash mismatch {path}: {actual}")
    return path


def _load_artifact(spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact = joblib.load(_verified_path(spec))
    metadata: dict[str, Any] = {}
    if spec.get("spec_path"):
        spec_path = Path(spec["spec_path"])
        if spec.get("spec_sha256") and _sha256(spec_path) != spec["spec_sha256"]:
            raise RuntimeError(f"model spec hash mismatch {spec_path}")
        metadata = json.loads(spec_path.read_text(encoding="utf-8"))
    return artifact, metadata


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _round_native_c(value: float) -> int:
    return math.floor(value + 0.5)


def _logit(value: float) -> float:
    clipped = float(np.clip(value, 1e-6, 1 - 1e-6))
    return math.log(clipped / (1.0 - clipped))


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, value))))


def _jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def _latest_book_capture(profile: dict[str, Any]) -> list[dict[str, Any]]:
    forward = _parse_ts(profile["forward_start_utc"])
    candidates: list[dict[str, Any]] = []
    for path in sorted(Path(profile["book_dir"]).glob("*.jsonl"), reverse=True)[:3]:
        for row in _jsonl(path):
            if (
                row.get("city") == "Tokyo"
                and row.get("source") == "jma_amedas"
                and row.get("outcome") == "no"
                and row.get("book_status") == "ok"
                and _parse_ts(str(row["book_fetched_at_utc"])) >= forward
            ):
                candidates.append(row)
    if not candidates:
        return []
    latest = max(candidates, key=lambda row: _parse_ts(str(row["book_fetched_at_utc"])))
    cycle_id = latest.get("capture_cycle_id")
    if cycle_id:
        return [row for row in candidates if row.get("capture_cycle_id") == cycle_id]
    latest_ts = _parse_ts(str(latest["book_fetched_at_utc"]))
    return [
        row for row in candidates
        if row.get("target_date") == latest.get("target_date")
        and row.get("source_obs_ts_utc") == latest.get("source_obs_ts_utc")
        and row.get("reference_market_value") == latest.get("reference_market_value")
        and 0 <= (latest_ts - _parse_ts(str(row["book_fetched_at_utc"]))).total_seconds() <= 45
    ]


def _latest_current_book(profile: dict[str, Any]) -> dict[str, Any] | None:
    rows = _latest_book_capture(profile)
    centered = [row for row in rows if row.get("relative_offset") == 0]
    return max(centered or rows, default=None, key=lambda row: _parse_ts(str(row["book_fetched_at_utc"])))


def _book_contains_anchor(book: dict[str, Any], anchor: int) -> bool:
    parsed = parse_label_dict(str(book.get("bracket") or ""), str(book.get("question") or ""))
    return bool(parsed and bracket_contains(parsed, anchor))


def _jma_history(
    path: Path,
    target_date: str,
    observation_through: datetime,
    available_through: datetime | None = None,
) -> list[dict[str, Any]]:
    available_through = available_through or datetime.max.replace(tzinfo=UTC)
    earliest: dict[str, dict[str, Any]] = {}
    found_target = False
    for catalog_row in JsonlInputCatalog.iter_path_reverse(path):
        row = catalog_row.row
        if (
            row.get("city") != "Tokyo"
            or row.get("source") != "jma_amedas"
            or row.get("source_status") != "ok"
        ):
            continue
        row_target = row.get("target_date")
        if found_target and row_target != target_date:
            break
        if row_target != target_date:
            continue
        found_target = True
        obs = _parse_ts(str(row["observation_time_utc"]))
        available = _parse_ts(str(row["source_first_seen_at_utc"]))
        if obs > observation_through or available > available_through:
            continue
        key = obs.isoformat()
        if key not in earliest or available < _parse_ts(
            str(earliest[key]["source_first_seen_at_utc"])
        ):
            earliest[key] = row
    return sorted(earliest.values(), key=lambda row: _parse_ts(str(row["observation_time_utc"])))


def _official_history(
    journal_dir: Path, target_date: str, decision: datetime
) -> list[dict[str, Any]]:
    catalog = JsonlInputCatalog(
        day_shard_lookback_days=2,
        day_shard_lookahead_days=0,
        physical_shard_timezone="UTC",
    )
    by_observation: dict[str, dict[str, Any]] = {}
    found_target = False
    paths = catalog.day_shard_paths(
        journal_dir, filename="observations.jsonl", as_of=decision
    )
    for catalog_row in (
        item
        for path in reversed(paths)
        for item in JsonlInputCatalog.iter_path_reverse(path)
    ):
        row = catalog_row.row
        if row.get("city") != "Tokyo":
            continue
        row_target = row.get("target_date")
        if found_target and row_target != target_date:
            break
        if row_target != target_date:
            continue
        found_target = True
        if not row.get("fetched_at_utc"):
            continue
        fetched = _parse_ts(str(row["fetched_at_utc"]))
        if not row.get("last_obs_utc"):
            continue
        observed = _parse_ts(str(row["last_obs_utc"]))
        if fetched > decision or observed > decision:
            continue
        row = {
            **row,
            "_input_ref": {
                "physical_path": catalog_row.physical_path,
                "physical_line": catalog_row.physical_line,
            },
        }
        key = observed.isoformat()
        if key not in by_observation:
            by_observation[key] = row
    return sorted(by_observation.values(), key=lambda row: _parse_ts(str(row["last_obs_utc"])))


def _lag_value(history: list[dict[str, Any]], current: datetime, minutes: int) -> float | None:
    cutoff = current - timedelta(minutes=minutes)
    for row in history:
        observed = _parse_ts(str(row["observation_time_utc"]))
        value = _finite(row.get("temp_c"))
        if cutoff <= observed < current and value is not None:
            return value
    return None


def _solar_elevation(timestamp: datetime) -> float:
    local = timestamp.astimezone(TOKYO)
    day = local.timetuple().tm_yday
    declination = math.radians(23.44 * math.sin(2 * math.pi * (284 + day) / 365.0))
    latitude = math.radians(35.55)
    hour_angle = math.radians(15.0 * ((local.hour + local.minute / 60.0) - 11.75))
    sin_altitude = (
        math.sin(latitude) * math.sin(declination)
        + math.cos(latitude) * math.cos(declination) * math.cos(hour_angle)
    )
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_altitude))))


def _pressure_hpa(raw_metar: str) -> float | None:
    match = re.search(r"\bQ(\d{4})\b", raw_metar)
    if match:
        return float(match.group(1))
    match = re.search(r"\bA(\d{4})\b", raw_metar)
    return float(match.group(1)) / 100.0 * 33.8639 if match else None


def _cloud_fraction(raw_metar: str) -> float | None:
    mapping = {"SKC": 0.0, "CLR": 0.0, "NSC": 0.0, "FEW": 0.25, "SCT": 0.5, "BKN": 0.875, "OVC": 1.0, "VV": 1.0}
    values = [mapping[code] for code in re.findall(r"\b(SKC|CLR|NSC|FEW|SCT|BKN|OVC|VV)", raw_metar)]
    if "CAVOK" in raw_metar:
        values.append(0.0)
    return max(values) if values else None


def _visibility_m(raw_metar: str) -> float | None:
    if "CAVOK" in raw_metar:
        return 10000.0
    match = re.search(r"\s(\d{4})\s", raw_metar)
    return float(match.group(1)) if match else None


def _market_prices(no_quote: dict[str, Any]) -> dict[str, float | str | None]:
    summary = no_quote.get("summary") or {}
    no_ask = _finite(summary.get("best_ask"))
    no_bid = _finite(summary.get("best_bid"))
    no_mid = (no_ask + no_bid) / 2.0 if no_ask is not None and no_bid is not None else None
    return {
        "no_ask": no_ask,
        "no_bid": no_bid,
        "no_mid": no_mid,
        "yes_ask": None if no_bid is None else 1.0 - no_bid,
        "yes_bid": None if no_ask is None else 1.0 - no_ask,
        "yes_mid": None if no_mid is None else 1.0 - no_mid,
        "market_probability_status": (
            "two_sided_midpoint" if no_mid is not None else "interval_censored"
        ),
        "quote_state": (
            "two_sided"
            if no_mid is not None
            else "one_sided_ask_only"
            if no_ask is not None
            else "one_sided_bid_only"
            if no_bid is not None
            else "empty"
        ),
    }


def _weather_features(
    jma: list[dict[str, Any]], official: list[dict[str, Any]], decision: datetime
) -> dict[str, float | None]:
    source = jma[-1]
    source_time = _parse_ts(str(source["observation_time_utc"]))
    temperatures = [float(row["temp_c"]) for row in jma]
    current_temp = temperatures[-1]
    running_max = max(temperatures)
    latest_official = official[-1]
    current_bracket = _round_native_c(float(latest_official["running_max_c"]))
    local = source_time.astimezone(TOKYO)
    local_hour = local.hour + local.minute / 60.0
    doy = local.timetuple().tm_yday
    delta = current_temp - temperatures[-2] if len(temperatures) >= 2 else None
    warming_run = 0
    for index in range(len(temperatures) - 1, 0, -1):
        if temperatures[index] > temperatures[index - 1]:
            warming_run += 1
        else:
            break
    strict_high_index = next(index for index, value in enumerate(temperatures) if value == running_max)
    strict_high_time = _parse_ts(str(jma[strict_high_index]["observation_time_utc"]))
    lag30 = _lag_value(jma, source_time, 30)
    lag60 = _lag_value(jma, source_time, 60)
    official_temp = float(latest_official["current_temp_c"])
    wind_speed = _finite(latest_official.get("wind_speed_kt"))
    wind_dir = _finite(latest_official.get("wind_dir_deg"))
    dewpoint_f = _finite(latest_official.get("dwpf_now"))
    dewpoint_c = None if dewpoint_f is None else (dewpoint_f - 32.0) * 5.0 / 9.0
    raw_metar = str(latest_official.get("raw_metar") or "")
    pressure = _pressure_hpa(raw_metar)
    prior_pressure = _pressure_hpa(str(official[-2].get("raw_metar") or "")) if len(official) >= 2 else None
    return {
        "jma_temp_c": current_temp,
        "jma_temp_delta_10m": delta,
        "jma_temp_slope_30m_cph": None if lag30 is None else (current_temp - lag30) * 2.0,
        "jma_temp_slope_60m_cph": None if lag60 is None else current_temp - lag60,
        "jma_running_max_c": running_max,
        "distance_to_next_jma_lattice_c": _round_native_c(running_max) + 0.5 - running_max,
        "minutes_since_jma_strict_high": (source_time - strict_high_time).total_seconds() / 60.0,
        "jma_warming_run_count": float(warming_run),
        "prior_metar_temp_c": official_temp,
        "prior_metar_running_max_c": float(latest_official["running_max_c"]),
        "jma_minus_prior_metar_c": current_temp - official_temp,
        "jma_lattice_minus_prior_metar_c": _round_native_c(current_temp) - official_temp,
        "prior_metar_age_min": (decision - _parse_ts(str(latest_official["last_obs_utc"]))).total_seconds() / 60.0,
        "local_hour_sin": math.sin(2 * math.pi * local_hour / 24.0),
        "local_hour_cos": math.cos(2 * math.pi * local_hour / 24.0),
        "doy_sin": math.sin(2 * math.pi * doy / 365.25),
        "doy_cos": math.cos(2 * math.pi * doy / 365.25),
        "solar_elevation_deg": _solar_elevation(source_time),
        "jma_wind_speed_kt": None,
        "jma_wind_gust_kt": None,
        "jma_gust_factor_kt": None,
        "jma_wind_u_kt": None,
        "jma_wind_v_kt": None,
        "jma_wind_speed_delta_30m_kt": None,
        "jma_wind_dir_change_30m_deg": None,
        "jma_precipitation_10m_mm": None,
        "jma_precipitation_30m_mm": None,
        "metar_dewpoint_c": dewpoint_c,
        "metar_relative_humidity_pct": _finite(latest_official.get("relative_humidity_pct")),
        "metar_dewpoint_depression_c": None if dewpoint_c is None else official_temp - dewpoint_c,
        "metar_wind_speed_kt": wind_speed,
        "metar_wind_u_kt": None if wind_speed is None or wind_dir is None else -wind_speed * math.sin(math.radians(wind_dir)),
        "metar_wind_v_kt": None if wind_speed is None or wind_dir is None else -wind_speed * math.cos(math.radians(wind_dir)),
        "metar_pressure_hpa": pressure,
        "metar_pressure_delta": None if pressure is None or prior_pressure is None else pressure - prior_pressure,
        "metar_cloud_cover_fraction": _cloud_fraction(raw_metar),
        "metar_ceiling_ft_agl": _finite(latest_official.get("ceiling_ft_agl")),
        "metar_precipitating": float(bool(latest_official.get("precip_observed"))),
        "metar_visibility_m": _visibility_m(raw_metar),
        "current_bracket": float(current_bracket),
        "jma_current_minus_current_bracket": current_temp - current_bracket,
        "jma_running_max_minus_current_bracket": running_max - current_bracket,
        "jma_pullback_from_running_max_c": current_temp - running_max,
        "jma_above_next_boundary_c": current_temp - (current_bracket + 0.5),
        "remaining_to_18h": 18.0 - local_hour,
        "metar_pullback_from_current_bracket_c": official_temp - current_bracket,
    }


def _weather_stay_probability(
    artifact: dict[str, Any], metadata: dict[str, Any], features: dict[str, float | None]
) -> tuple[float, list[str]]:
    names = list(metadata["features"])
    missing = [name for name in names if _finite(features.get(name)) is None]
    matrix = np.asarray([[np.nan if _finite(features.get(name)) is None else float(features[name]) for name in names]])
    raw = artifact["model"].predict_proba(matrix)[0]
    classes = [int(value) for value in artifact["model"].named_steps["model"].classes_]
    p_break = float(raw[classes.index(1)])
    temperature = float(artifact["temperature"])
    p_break = _sigmoid(_logit(p_break) / temperature)
    return 1.0 - p_break, missing


def _previous_weather_probability(
    paths: list[Path], target_date: str, bracket: int, source_obs: datetime
) -> float | None:
    candidates = []
    for row in iter_compatible_evaluations(paths):
        if (
            row.get("city") == "Tokyo"
            and row.get("target_date") == target_date
            and int(row.get("current_bracket", -999)) == bracket
            and row.get("market_side") == "YES"
            and _parse_ts(str(row["source_obs_ts_utc"])) < source_obs
        ):
            probability = _finite((row.get("lineage") or {}).get("weather_probability_stay"))
            if probability is not None:
                candidates.append((_parse_ts(str(row["source_obs_ts_utc"])), probability))
    return max(candidates, default=(None, None), key=lambda item: item[0])[1]


def _offset_probability(artifact: dict[str, Any], features: dict[str, float], market_p: float) -> float:
    names = list(artifact["features"])
    raw = np.asarray([features.get(name, np.nan) for name in names], dtype=float)
    median = np.asarray(artifact["median"], dtype=float)
    raw = np.where(np.isfinite(raw), raw, median)
    scaled = (raw - np.asarray(artifact["mean"], dtype=float)) / np.asarray(artifact["scale"], dtype=float)
    beta = np.asarray(artifact["beta"], dtype=float)
    floor = float(artifact.get("market_logit_floor", 1e-6))
    clipped_market = float(np.clip(market_p, floor, 1.0 - floor))
    return _sigmoid(_logit(clipped_market) + float(beta[0] + scaled @ beta[1:]))


class TokyoMarketAnchorAdapter:
    """Tokyo market-anchor scorer. It reads PIT journals and has no order client."""

    def score(self, profile: dict[str, Any], now: datetime) -> list[CityScore]:
        capture = _latest_book_capture(profile)
        if not capture:
            return []
        capture = sorted(
            capture,
            key=lambda row: _parse_ts(str(row["book_fetched_at_utc"])),
            reverse=True,
        )
        book = None
        official = []
        bracket = None
        for candidate in capture:
            candidate_decision = _parse_ts(str(candidate["book_fetched_at_utc"]))
            candidate_official = _official_history(
                Path(profile["observation_journal_dir"]),
                str(candidate["target_date"]),
                candidate_decision,
            )
            if not candidate_official:
                continue
            candidate_anchor = _round_native_c(float(candidate_official[-1]["running_max_c"]))
            if _book_contains_anchor(candidate, candidate_anchor):
                book, official, bracket = candidate, candidate_official, candidate_anchor
                break
        latest = capture[0]
        if book is None:
            latest_decision = _parse_ts(str(latest["book_fetched_at_utc"]))
            latest_official = _official_history(
                Path(profile["observation_journal_dir"]),
                str(latest["target_date"]),
                latest_decision,
            )
            if not latest_official:
                raise InputNotReady(
                    "awaiting_official_observation",
                    city="Tokyo",
                    target_date=str(latest["target_date"]),
                    decision_ts_utc=latest_decision.isoformat(),
                    details={"observation_journal_dir": profile["observation_journal_dir"]},
                )
            official_anchor = _round_native_c(float(latest_official[-1]["running_max_c"]))
            raise InputNotReady(
                "anchor_capture_gap",
                city="Tokyo",
                target_date=str(latest["target_date"]),
                decision_ts_utc=latest_decision.isoformat(),
                details={
                    "official_anchor": official_anchor,
                    "capture_cycle_id": latest.get("capture_cycle_id"),
                    "captured_brackets": sorted({str(row.get("bracket") or "") for row in capture}),
                    "capture_anchor_values": latest.get("capture_anchor_values"),
                    "official_input_ref": latest_official[-1].get("_input_ref"),
                },
            )
        decision = _parse_ts(str(book["book_fetched_at_utc"]))
        source_obs = _parse_ts(str(book["source_obs_ts_utc"]))
        local_time = source_obs.astimezone(TOKYO)
        local_hour = local_time.hour + local_time.minute / 60.0
        # Staleness only has meaning inside the frozen Tokyo scoring window.
        # After the window closes, the ladder intentionally stops producing
        # current books; treating its last capture as an error creates one
        # false incident on every runner cycle overnight.
        if not 6.0 <= local_hour < 18.0:
            return []
        age = (now.astimezone(UTC) - decision).total_seconds()
        if age > float(profile["max_book_age_seconds"]):
            raise InputNotReady(
                "stale_market_expression",
                city="Tokyo",
                target_date=str(book["target_date"]),
                decision_ts_utc=decision.isoformat(),
                details={
                    "book_snapshot_id": book.get("book_snapshot_id"),
                    "book_input_ref": book.get("_input_ref"),
                    "max_book_age_seconds": float(profile["max_book_age_seconds"]),
                },
            )
        target_date = str(book["target_date"])
        jma = _jma_history(
            Path(profile["source_journal"]), target_date, source_obs, decision
        )
        if not jma or _parse_ts(str(jma[-1]["observation_time_utc"])) != source_obs:
            raise RuntimeError("active book source observation has no exact JMA first-seen row")
        source = jma[-1]
        source_first_seen = _parse_ts(str(source["source_first_seen_at_utc"]))
        if source_first_seen > decision:
            raise RuntimeError("JMA first-seen is after the decision book clock")
        assert bracket is not None
        prices = _market_prices(book)
        if prices["quote_state"] == "empty":
            raise InputNotReady(
                "empty_market_book",
                city="Tokyo",
                target_date=target_date,
                decision_ts_utc=decision.isoformat(),
                details={"current_bracket": bracket},
            )
        weather_features = _weather_features(jma, official, decision)
        yes_mid = _finite(prices["yes_mid"])
        probability_policy = str(
            profile.get("probability_policy", "state_entry_routed_market_residual_v7")
        )
        if probability_policy == "overshoot_market_residual_v2":
            overshoot_artifact, _ = _load_artifact(profile["artifacts"]["overshoot"])
            offset_features = {
                name: weather_features.get(name)
                for name in overshoot_artifact["features"]
            }
            no_mid = _finite(prices["no_mid"])
            model_no = (
                None
                if no_mid is None
                else _offset_probability(overshoot_artifact, offset_features, no_mid)
            )
            model_stay = None if model_no is None else 1.0 - model_no
            weather_stay = None
            weather_missing: list[str] = []
            previous_weather = None
            is_observed_state_entry = None
            residual_model_stay = model_stay
            probability_lineage = {
                "probability_target": "leave_current_exact_bracket",
                "probability_policy": probability_policy,
                "market_probability_semantics": overshoot_artifact[
                    "market_probability_semantics"
                ],
                "training_clock_class": overshoot_artifact["training_clock_class"],
                "clean_forward_start": overshoot_artifact["clean_frozen_start"],
                "training_end": overshoot_artifact["training_end"],
            }
        else:
            weather_artifact, weather_metadata = _load_artifact(
                profile["artifacts"]["weather"]
            )
            offset_artifact, _ = _load_artifact(profile["artifacts"]["offset"])
            weather_stay, weather_missing = _weather_stay_probability(
                weather_artifact, weather_metadata, weather_features
            )
            evaluation_journals = [
                Path(path)
                for path in profile.get(
                    "evaluation_journals",
                    [profile.get("evaluation_journal", "")],
                )
                if path
            ]
            previous_weather = _previous_weather_probability(
                evaluation_journals, target_date, bracket, source_obs
            )
            offset_features = {
                "weather_market_logit_gap": (
                    None
                    if yes_mid is None
                    else float(np.clip(_logit(weather_stay) - _logit(yes_mid), -6.0, 6.0))
                ),
                "weather_logit_innovation": 0.0 if previous_weather is None else float(np.clip(_logit(weather_stay) - _logit(previous_weather), -4.0, 4.0)),
                "remaining_to_18h": float(weather_features["remaining_to_18h"]),
                "jma_pullback_from_running_max_c": float(weather_features["jma_pullback_from_running_max_c"]),
                "jma_temp_slope_60m_cph": weather_features["jma_temp_slope_60m_cph"],
                "solar_elevation_deg": float(weather_features["solar_elevation_deg"]),
            }
            residual_model_stay = (
                None
                if yes_mid is None
                else _offset_probability(offset_artifact, offset_features, yes_mid)
            )
            # Pre-2026-08-01 OOF shows that the learned correction improves ordinary
            # checkpoints but degrades the first observed checkpoint of a new
            # bracket. Route that state to the PIT market anchor.
            is_observed_state_entry = previous_weather is None
            model_stay = (
                yes_mid
                if yes_mid is not None and is_observed_state_entry
                else residual_model_stay
            )
            probability_lineage = {
                "weather_probability_stay": weather_stay,
                "weather_feature_coverage": 1.0 - len(weather_missing) / len(weather_metadata["features"]),
                "weather_missing_features": weather_missing,
                "previous_same_bracket_weather_probability_stay": previous_weather,
                "is_observed_state_entry": is_observed_state_entry,
                "residual_model_probability_stay": residual_model_stay,
                "state_entry_probability_policy": "pit_market_anchor",
                "ordinary_checkpoint_probability_policy": "weather_market_residual_v6",
                "clean_forward_start": offset_artifact["clean_forward_start"],
                "training_end": offset_artifact["training_end"],
            }
        compact_market = {
            key: book.get(key)
            for key in (
                "condition_id",
                "market_id",
                "token_id",
                "outcome",
                "question",
                "book_fetched_at_utc",
                "book_status",
            )
        }
        compact_market.update(prices)
        lineage = {
            "availability_clock_class": "collector_exact_hash_verified",
            "source": "jma_amedas",
            "source_first_seen_at_utc": source["source_first_seen_at_utc"],
            "source_payload_hash": source.get("payload_hash"),
            "source_raw_payload_hash": source.get("raw_payload_hash"),
            "official_source": official[-1].get("source"),
            "official_last_obs_utc": official[-1].get("last_obs_utc"),
            "official_snapshot_fetched_at_utc": official[-1].get("fetched_at_utc"),
            "official_input_ref": official[-1].get("_input_ref"),
            "source_lattice_anchor": int(book["reference_market_value"]),
            "official_lattice_anchor": bracket,
            "market_expression_anchor": bracket,
            "capture_cycle_id": book.get("capture_cycle_id"),
            "capture_anchor_values": book.get("capture_anchor_values"),
            **probability_lineage,
        }
        feature_missing = [name for name, value in offset_features.items() if _finite(value) is None]
        common = dict(
            city="Tokyo",
            target_date=target_date,
            decision_ts_utc=decision.isoformat(),
            source_obs_ts_utc=source_obs.isoformat(),
            current_bracket=bracket,
            model_id=str(
                profile.get(
                    "model_id",
                    "tokyo_state_entry_routed_market_residual_v7",
                )
            ),
            feature_coverage=1.0 - len(feature_missing) / len(offset_features),
            missing_features=feature_missing,
            features=offset_features,
            market=compact_market,
            lineage=lineage,
        )
        return [
            CityScore(
                **common,
                market_side="YES",
                market_probability=_finite(prices["yes_mid"]),
                market_entry_price=_finite(prices["yes_ask"]),
                model_probability=model_stay,
                evaluation_status="scored" if model_stay is not None else "not_scorable",
                not_scorable_reason=(
                    None if model_stay is not None else "one_sided_market_probability_interval"
                ),
            ),
            CityScore(
                **common,
                market_side="NO",
                market_probability=_finite(prices["no_mid"]),
                market_entry_price=_finite(prices["no_ask"]),
                model_probability=None if model_stay is None else 1.0 - model_stay,
                evaluation_status="scored" if model_stay is not None else "not_scorable",
                not_scorable_reason=(
                    None if model_stay is not None else "one_sided_market_probability_interval"
                ),
            ),
        ]
