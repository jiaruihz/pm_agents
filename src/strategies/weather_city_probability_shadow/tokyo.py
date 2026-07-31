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

from .core import CityScore


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


def _latest_current_book(profile: dict[str, Any]) -> dict[str, Any] | None:
    forward = _parse_ts(profile["forward_start_utc"])
    candidates: list[dict[str, Any]] = []
    for path in sorted(Path(profile["book_dir"]).glob("*.jsonl"), reverse=True)[:3]:
        for row in _jsonl(path):
            if (
                row.get("city") == "Tokyo"
                and row.get("source") == "jma_amedas"
                and row.get("outcome") == "no"
                and int(row.get("relative_offset", 999)) == 0
                and row.get("book_status") == "ok"
                and _parse_ts(str(row["book_fetched_at_utc"])) >= forward
            ):
                candidates.append(row)
    if not candidates:
        return None
    return max(candidates, key=lambda row: _parse_ts(str(row["book_fetched_at_utc"])))


def _jma_history(path: Path, target_date: str, through: datetime) -> list[dict[str, Any]]:
    earliest: dict[str, dict[str, Any]] = {}
    for row in _jsonl(path):
        if (
            row.get("city") != "Tokyo"
            or row.get("source") != "jma_amedas"
            or row.get("target_date") != target_date
            or row.get("source_status") != "ok"
        ):
            continue
        obs = _parse_ts(str(row["observation_time_utc"]))
        if obs > through:
            continue
        key = obs.isoformat()
        if key not in earliest or _parse_ts(str(row["source_first_seen_at_utc"])) < _parse_ts(
            str(earliest[key]["source_first_seen_at_utc"])
        ):
            earliest[key] = row
    return sorted(earliest.values(), key=lambda row: _parse_ts(str(row["observation_time_utc"])))


def _official_history(
    journal_dir: Path, target_date: str, decision: datetime
) -> list[dict[str, Any]]:
    path = journal_dir / target_date / "observations.jsonl"
    if not path.exists():
        raise RuntimeError(f"missing official observation journal: {path}")
    by_observation: dict[str, dict[str, Any]] = {}
    for row in _jsonl(path):
        if row.get("city") != "Tokyo" or row.get("target_date") != target_date:
            continue
        fetched = _parse_ts(str(row["fetched_at_utc"]))
        observed = _parse_ts(str(row["last_obs_utc"]))
        if fetched > decision or observed > decision:
            continue
        key = observed.isoformat()
        if key not in by_observation or fetched > _parse_ts(str(by_observation[key]["fetched_at_utc"])):
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


def _market_prices(no_quote: dict[str, Any]) -> dict[str, float]:
    summary = no_quote.get("summary") or {}
    no_ask = _finite(summary.get("best_ask"))
    no_bid = _finite(summary.get("best_bid"))
    if no_ask is None or no_bid is None:
        raise RuntimeError("Tokyo current NO book lacks a two-sided quote")
    return {
        "no_ask": no_ask,
        "no_bid": no_bid,
        "no_mid": (no_ask + no_bid) / 2.0,
        "yes_ask": 1.0 - no_bid,
        "yes_bid": 1.0 - no_ask,
        "yes_mid": 1.0 - (no_ask + no_bid) / 2.0,
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
    path: Path, target_date: str, bracket: int, source_obs: datetime
) -> float | None:
    if not path.exists():
        return None
    candidates = []
    for row in _jsonl(path):
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
    return _sigmoid(_logit(market_p) + float(beta[0] + scaled @ beta[1:]))


class TokyoMarketAnchorAdapter:
    """Frozen Tokyo v6 scorer. It reads PIT journals and contains no order client."""

    def score(self, profile: dict[str, Any], now: datetime) -> list[CityScore]:
        book = _latest_current_book(profile)
        if book is None:
            return []
        decision = _parse_ts(str(book["book_fetched_at_utc"]))
        age = (now.astimezone(UTC) - decision).total_seconds()
        if age > float(profile["max_book_age_seconds"]):
            raise RuntimeError(f"Tokyo active current book is stale by {age:.1f}s")
        target_date = str(book["target_date"])
        source_obs = _parse_ts(str(book["source_obs_ts_utc"]))
        jma = _jma_history(Path(profile["source_journal"]), target_date, source_obs)
        if not jma or _parse_ts(str(jma[-1]["observation_time_utc"])) != source_obs:
            raise RuntimeError("active book source observation has no exact JMA first-seen row")
        source = jma[-1]
        source_first_seen = _parse_ts(str(source["source_first_seen_at_utc"]))
        if source_first_seen > decision:
            raise RuntimeError("JMA first-seen is after the decision book clock")
        local_hour = source_obs.astimezone(TOKYO).hour + source_obs.astimezone(TOKYO).minute / 60.0
        # The production high-frequency collector is active from 06:00 local.
        # Keep the frozen forward denominator inside the actually captured clock.
        if not 6.0 <= local_hour < 18.0:
            return []
        official = _official_history(Path(profile["observation_journal_dir"]), target_date, decision)
        if not official:
            raise RuntimeError("no PIT official Tokyo observation at the decision clock")
        bracket = _round_native_c(float(official[-1]["running_max_c"]))
        if bracket != int(book["reference_market_value"]):
            raise RuntimeError(
                f"official/book current bracket mismatch: official={bracket} book={book['reference_market_value']}"
            )
        prices = _market_prices(book)
        weather_artifact, weather_metadata = _load_artifact(profile["artifacts"]["weather"])
        offset_artifact, _ = _load_artifact(profile["artifacts"]["offset"])
        weather_features = _weather_features(jma, official, decision)
        weather_stay, weather_missing = _weather_stay_probability(
            weather_artifact, weather_metadata, weather_features
        )
        previous_weather = _previous_weather_probability(
            Path(profile["evaluation_journal"]), target_date, bracket, source_obs
        )
        offset_features = {
            "weather_market_logit_gap": float(np.clip(_logit(weather_stay) - _logit(prices["yes_mid"]), -6.0, 6.0)),
            "weather_logit_innovation": 0.0 if previous_weather is None else float(np.clip(_logit(weather_stay) - _logit(previous_weather), -4.0, 4.0)),
            "remaining_to_18h": float(weather_features["remaining_to_18h"]),
            "jma_pullback_from_running_max_c": float(weather_features["jma_pullback_from_running_max_c"]),
            "jma_temp_slope_60m_cph": weather_features["jma_temp_slope_60m_cph"],
            "solar_elevation_deg": float(weather_features["solar_elevation_deg"]),
        }
        model_stay = _offset_probability(offset_artifact, offset_features, prices["yes_mid"])
        compact_market = {
            key: book.get(key)
            for key in ("condition_id", "market_id", "token_id", "question", "book_fetched_at_utc", "book_status")
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
            "weather_probability_stay": weather_stay,
            "weather_feature_coverage": 1.0 - len(weather_missing) / len(weather_metadata["features"]),
            "weather_missing_features": weather_missing,
            "previous_same_bracket_weather_probability_stay": previous_weather,
            "clean_forward_start": offset_artifact["clean_forward_start"],
            "training_end": offset_artifact["training_end"],
        }
        feature_missing = [name for name, value in offset_features.items() if _finite(value) is None]
        common = dict(
            city="Tokyo",
            target_date=target_date,
            decision_ts_utc=decision.isoformat(),
            source_obs_ts_utc=source_obs.isoformat(),
            current_bracket=bracket,
            model_id=str(offset_artifact["model_id"]),
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
                market_probability=prices["yes_mid"],
                market_entry_price=prices["yes_ask"],
                model_probability=model_stay,
            ),
            CityScore(
                **common,
                market_side="NO",
                market_probability=prices["no_mid"],
                market_entry_price=prices["no_ask"],
                model_probability=1.0 - model_stay,
            ),
        ]
