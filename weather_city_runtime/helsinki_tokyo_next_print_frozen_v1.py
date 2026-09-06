"""Helsinki/Tokyo WCIR next-print zero-notional action shadow runtime.

The runtime has no broker, venue, credential, order, plan, or live-authority
imports. It reuses the shared canonical market-book and capture pipelines,
scores the frozen B2/M1/M2 arms, emits zero-size SignalCandidate/TradeIntent
facts, and materializes proper-score plus counterfactual execution evidence.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd

from scripts.analysis.forecast_quality.wcir_amsterdam_pilot_rev2 import CORE_FEATURES
from scripts.analysis.forecast_quality.wcir_unified_amsterdam_pilot import temperature_scale
from src.platform.market_data.capture_demand import CaptureDemand
from src.platform.market_data.capture_receipt import CaptureReceipt
from weather_city_runtime.contracts import SignalCandidate, TradeIntent
from weather_clock_contract import parse_utc
from weather_modeling.next_print_feature_builder_v1 import NextPrintFeatureBuilderV1, stable_hash

from .amsterdam_frozen_shadow_v2 import (
    ImmutableJsonl,
    _write_json_atomic,
    read_jsonl,
    read_source_events,
    replay_markout_rows,
    settlement_for_identity,
)


UTC = timezone.utc
STRATEGY_KEY = "weather_helsinki_tokyo_wcir_next_print_frozen_v1"
MODEL_ARTIFACT_EXECUTION_MODE = "zero_notional_probability_only"
EXECUTION_MODE = "zero_notional_action_shadow"
ACTION_SHADOW_OVERLAY_FILENAME = "ACTION_SHADOW_OVERLAY_V2.json"
ACTION_SHADOW_EPOCH = "wcir_ht_next_print_action_shadow_v2_20260904a"
ACTION_SHADOW_START_UTC = "2026-09-03T16:45:00Z"
MODEL_IDS = {
    "B2": "B2_latest_fast_rounded",
    "M1": "ams_next_print_m1_ordinal_logit",
    "M2": "ams_next_print_m2_monotonic_additive",
}
SUPPORT = np.arange(-10, 11, dtype=int)
CHECKPOINTS = (0, 5, 15, 30, 60, 120)
ENTRY_LATENCY_SECONDS = 5.0
ACTION_THRESHOLD = 0.5
SHARES = 5.0
MAX_TAKER_VWAP = 0.92
MATERIALIZER_VERSION = "helsinki_tokyo_next_print_frozen_ws_v1"
FEATURE_RETRY_TIMEOUT_SECONDS = 300
PRODUCER_HEALTH_MAX_AGE_SECONDS = 180
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

CITY = {
    "Helsinki": {
        "source": "fmi",
        "source_station": "100968",
        "official_station": "EFHK",
        "timezone": "Europe/Helsinki",
        "routine_minutes": (20, 50),
    },
    "Tokyo": {
        "source": "jma_amedas",
        "source_station": "44166",
        "official_station": "RJTT",
        "timezone": "Asia/Tokyo",
        "routine_minutes": (0, 30),
    },
}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse(value: Any, *, field: str) -> datetime:
    parsed = parse_utc(str(value), field=field)
    assert parsed is not None
    return parsed


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        ).encode()
    ).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _half_up(value: float) -> int:
    return int(math.floor(float(value) + 0.5))


def _safe_rate(numerator: float | int, denominator: float | int) -> float | None:
    return None if not denominator else float(numerator) / float(denominator)


def _trigger_event_id(row: Mapping[str, Any]) -> str:
    return stable_hash(
        {
            "event_key": str(row.get("event_key") or ""),
            "source_detect_ts_utc": str(row.get("source_detect_ts_utc") or ""),
            "token_id": str(row.get("token_id") or ""),
        }
    )


def _market_identity_seed(row: Mapping[str, Any]) -> dict[str, Any]:
    bracket = row.get("t_minus_1_no_bracket_c")
    return {
        "trigger_event_id": _trigger_event_id(row),
        "polymarket_event_id": str(row.get("polymarket_event_id") or ""),
        "polymarket_event_slug": str(row.get("polymarket_event_slug") or row.get("event_slug") or ""),
        "market_id": str(row.get("market_id") or ""),
        "condition_id": str(row.get("condition_id") or ""),
        "token_id": str(row.get("token_id") or ""),
        "checkpoint_timestamp": str(row.get("ts_utc") or row.get("source_detect_ts_utc") or ""),
        "identity_source": "fast_source_prev_no_trial_v2_market_seed",
        "side": "NO",
        "native_bracket": "" if bracket is None else str(bracket),
        "target_date": str(row.get("target_date") or ""),
    }


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def fetch_gamma_market(market_id: str) -> Mapping[str, Any]:
    """Fetch read-only Gamma metadata; this has no CLOB/order authority."""
    base = str(os.environ.get("WEATHER_WCIR_HT_GAMMA_BASE_URL") or GAMMA_BASE_URL).rstrip("/")
    request = Request(
        f"{base}/markets/{quote(str(market_id), safe='')}",
        headers={"Accept": "application/json", "User-Agent": "pm-agents-wcir-frozen/1"},
        method="GET",
    )
    with urlopen(request, timeout=8.0) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Gamma market response is not an object")
    return payload


MarketMetadataFetcher = Callable[[str], Mapping[str, Any]]


def resolve_exact_market_identity(
    seed: Mapping[str, Any],
    *,
    available_at_utc: str,
    fetcher: MarketMetadataFetcher | None = None,
) -> dict[str, Any]:
    """Resolve and cross-check archive-native event/market/condition/token identity."""
    market_id = str(seed.get("market_id") or "")
    condition_id = str(seed.get("condition_id") or "")
    token_id = str(seed.get("token_id") or "")
    trigger_event_id = str(seed.get("trigger_event_id") or "")
    checkpoint = str(seed.get("checkpoint_timestamp") or "")
    if not all((market_id, condition_id, token_id, trigger_event_id, checkpoint)):
        raise ValueError("exact market identity seed is incomplete")
    supplied_event_id = str(seed.get("polymarket_event_id") or "")
    payload = (fetcher or fetch_gamma_market)(market_id)
    if str(payload.get("id") or "") != market_id:
        raise ValueError("Gamma market_id mismatch")
    payload_condition = str(payload.get("conditionId") or payload.get("condition_id") or "")
    if payload_condition != condition_id:
        raise ValueError("Gamma condition_id mismatch")
    tokens = {str(item) for item in _json_list(payload.get("clobTokenIds") or payload.get("clob_token_ids"))}
    if token_id not in tokens:
        raise ValueError("Gamma token_id mismatch")
    events = [item for item in (payload.get("events") or ()) if isinstance(item, Mapping)]
    event_ids = {str(item.get("id") or "") for item in events if str(item.get("id") or "")}
    if len(event_ids) != 1:
        raise ValueError("Gamma event identity is missing or ambiguous")
    event_id = next(iter(event_ids))
    if supplied_event_id and supplied_event_id != event_id:
        raise ValueError("Gamma polymarket_event_id mismatch")
    event_slugs = {str(item.get("slug") or "") for item in events if str(item.get("slug") or "")}
    return {
        **dict(seed),
        "event_id": event_id,
        "polymarket_event_id": event_id,
        "polymarket_event_slug": next(iter(event_slugs)) if len(event_slugs) == 1 else "",
        "exact_identity_complete": True,
        "identity_source": "gamma_market_by_id_cross_checked",
        "identity_available_at_utc": str(available_at_utc),
        "raw_market_payload_sha256": _hash(payload),
    }


def resolve_market_book_identity(
    seed: Mapping[str, Any],
    market_latest: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve the exact token through the existing canonical market-book feed."""
    records = [row for row in market_latest.get("records") or () if isinstance(row, Mapping)]
    matches = [
        dict(row)
        for row in records
        if str(row.get("market_id") or "") == str(seed.get("market_id") or "")
        and str(row.get("condition_id") or "") == str(seed.get("condition_id") or "")
        and str(row.get("token_id") or "") == str(seed.get("token_id") or "")
        and str(row.get("outcome") or "").upper() == str(seed.get("side") or "").upper()
        and str(row.get("bracket") or "") == str(seed.get("native_bracket") or "")
    ]
    if not matches:
        raise ValueError("canonical market-book exact token is missing")
    event_ids = {str(row.get("event_id") or "") for row in matches if row.get("event_id")}
    if len(event_ids) != 1:
        raise ValueError("canonical market-book event identity is missing or ambiguous")
    event_id = next(iter(event_ids))
    supplied_event_id = str(seed.get("polymarket_event_id") or "")
    if supplied_event_id and supplied_event_id != event_id:
        raise ValueError("canonical market-book polymarket_event_id mismatch")
    slugs = {str(row.get("event_slug") or "") for row in matches if row.get("event_slug")}
    available_values = [
        str(row.get("available_at_utc") or market_latest.get("available_at_utc") or "")
        for row in matches
    ]
    available_values = [value for value in available_values if value]
    if not available_values:
        raise ValueError("canonical market-book availability clock is missing")
    available_at = min(available_values, key=lambda value: _parse(value, field="market_book_available_at_utc"))
    evidence = {
        "schema_version": market_latest.get("schema_version"),
        "producer": market_latest.get("producer"),
        "producer_build_id": market_latest.get("producer_build_id"),
        "batch_capture_id": market_latest.get("batch_capture_id"),
        "records": matches,
    }
    return (
        {
            **dict(seed),
            "event_id": event_id,
            "polymarket_event_id": event_id,
            "polymarket_event_slug": next(iter(slugs)) if len(slugs) == 1 else "",
            "exact_identity_complete": True,
            "identity_source": "canonical_market_books_exact_token",
            "identity_available_at_utc": available_at,
            "raw_market_payload_sha256": _hash(evidence),
        },
        evidence,
    )


def validate_exact_expression(row: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Validate the preregistered event and exact-token mapping."""
    city = str(row.get("city") or "")
    contract = CITY.get(city)
    reasons: list[str] = []
    if contract is None:
        return "RESEARCH_INELIGIBLE", ["CITY_OUT_OF_SCOPE"]
    if str(row.get("schema_version") or "") != "fast_source_prev_no_trial_v2":
        reasons.append("UPSTREAM_SCHEMA_NOT_V2")
    if not str(row.get("event_key") or ""):
        reasons.append("TRIGGER_EVENT_KEY_MISSING")
    if str(row.get("source") or "") != contract["source"]:
        reasons.append("FAST_SOURCE_MISMATCH")
    if str(row.get("station") or "") != contract["source_station"]:
        reasons.append("FAST_SOURCE_STATION_MISMATCH")
    if row.get("source_cross_confirmed") is not True:
        reasons.append("SOURCE_CROSS_NOT_CONFIRMED")
    try:
        if int(row["source_round_c"]) <= int(row["metar_running_max_round_c"]):
            reasons.append("SOURCE_NOT_ABOVE_RUNNING_MAX")
        if int(row["t_minus_1_no_bracket_c"]) != int(row["metar_running_max_round_c"]):
            reasons.append("PREVIOUS_RUNNING_MAX_BRACKET_MISMATCH")
    except (KeyError, TypeError, ValueError):
        reasons.append("NATIVE_BRACKET_FIELDS_MISSING")
    identity = _market_identity_seed(row)
    missing = [
        name
        for name in (
            "trigger_event_id",
            "market_id",
            "condition_id",
            "token_id",
            "checkpoint_timestamp",
        )
        if not identity[name]
    ]
    if missing:
        reasons.append("EXACT_IDENTITY_MISSING:" + ",".join(missing))
    try:
        detected = _parse(row.get("source_detect_ts_utc"), field="source_detect_ts_utc")
        decision = _parse(identity["checkpoint_timestamp"], field="checkpoint_timestamp")
        if decision < detected:
            reasons.append("NONCAUSAL_DECISION_CLOCK")
    except (TypeError, ValueError):
        reasons.append("DECISION_CLOCK_MISSING_OR_INVALID")
    if reasons:
        research = {
            "UPSTREAM_SCHEMA_NOT_V2",
            "FAST_SOURCE_MISMATCH",
            "FAST_SOURCE_STATION_MISMATCH",
            "SOURCE_CROSS_NOT_CONFIRMED",
            "SOURCE_NOT_ABOVE_RUNNING_MAX",
            "PREVIOUS_RUNNING_MAX_BRACKET_MISMATCH",
            "TRIGGER_EVENT_KEY_MISSING",
        }
        category = "RESEARCH_INELIGIBLE" if any(reason.split(":", 1)[0] in research for reason in reasons) else "DATA_FAIL_CLOSED"
        return category, reasons
    return "ELIGIBLE", []


class FrozenModels:
    """Hash-checked model/config bundle loaded only from the release review."""

    def __init__(self, review_root: Path) -> None:
        self.review_root = review_root
        self.config_path = review_root / "FROZEN_DEPLOYMENT_CONFIG.json"
        self.action_path = review_root / "FROZEN_ACTION_CONTRACT.json"
        self.action_shadow_overlay_path = review_root / ACTION_SHADOW_OVERLAY_FILENAME
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.action = json.loads(self.action_path.read_text(encoding="utf-8"))
        self.action_shadow_overlay = json.loads(
            self.action_shadow_overlay_path.read_text(encoding="utf-8")
        )
        if self.config.get("strategy_key") != STRATEGY_KEY:
            raise RuntimeError("frozen strategy key mismatch")
        if self.config.get("execution_mode") != MODEL_ARTIFACT_EXECUTION_MODE:
            raise RuntimeError("frozen execution mode mismatch")
        if self.config.get("live_authority") is not False:
            raise RuntimeError("frozen config unexpectedly grants live authority")
        if self.config.get("action_contract_sha256") != _sha256(self.action_path):
            raise RuntimeError("frozen action contract hash mismatch")
        overlay = self.action_shadow_overlay
        if overlay.get("schema_version") != "wcir_ht_action_shadow_overlay_v2":
            raise RuntimeError("action-shadow overlay schema mismatch")
        if overlay.get("strategy_key") != STRATEGY_KEY:
            raise RuntimeError("action-shadow overlay strategy key mismatch")
        if overlay.get("execution_mode") != EXECUTION_MODE:
            raise RuntimeError("action-shadow overlay execution mode mismatch")
        if overlay.get("forward_epoch_id") != ACTION_SHADOW_EPOCH:
            raise RuntimeError("action-shadow overlay epoch mismatch")
        if overlay.get("forward_start_utc") != ACTION_SHADOW_START_UTC:
            raise RuntimeError("action-shadow overlay start mismatch")
        if overlay.get("live_authority") is not False:
            raise RuntimeError("action-shadow overlay unexpectedly grants live authority")
        if overlay.get("base_action_contract_sha256") != self.config.get(
            "action_contract_sha256"
        ):
            raise RuntimeError("action-shadow overlay/base contract mismatch")
        zero_notional = overlay.get("zero_notional", {})
        if any(
            zero_notional.get(field) != 0
            for field in (
                "actual_notional_usd",
                "exchange_calls",
                "fills",
                "orders",
                "plans",
                "requested_size",
            )
        ):
            raise RuntimeError("action-shadow overlay is not zero-notional")
        self.epoch = str(self.config["forward_epoch_id"])
        self.forward_start = _parse(self.config["forward_start_utc"], field="forward_start_utc")
        self.artifacts: dict[str, dict[str, Any]] = {}
        for city in CITY:
            path = review_root / "artifacts" / f"{city.lower()}_b2_m1_m2_frozen_v1.joblib"
            expected = str(self.config["artifact_sha256"][city])
            if _sha256(path) != expected:
                raise RuntimeError(f"{city} frozen model artifact hash mismatch")
            artifact = joblib.load(path)
            if artifact.get("city") != city:
                raise RuntimeError(f"{city} frozen artifact city mismatch")
            if artifact.get("action_contract_sha256") != self.config["action_contract_sha256"]:
                raise RuntimeError(f"{city} frozen artifact/action mismatch")
            if artifact.get("feature_builder") != NextPrintFeatureBuilderV1.version:
                raise RuntimeError(f"{city} frozen feature-builder mismatch")
            self.artifacts[city] = artifact

    @property
    def hashes(self) -> dict[str, Any]:
        return {
            "action_contract_sha256": self.config["action_contract_sha256"],
            "action_shadow_overlay_sha256": _sha256(
                self.action_shadow_overlay_path
            ),
            "artifact_sha256": dict(self.config["artifact_sha256"]),
            "feature_builder": NextPrintFeatureBuilderV1.version,
        }

    def predict(self, city: str, frame: pd.DataFrame) -> dict[str, np.ndarray]:
        artifact = self.artifacts[city]
        residual = np.asarray(artifact["b2_residual_probability"], dtype=float)
        point = (
            np.floor(frame["latest_fast_native_value"].to_numpy(float) + 0.5)
            - frame["last_official_native_value"].to_numpy(float)
        )
        b2 = np.zeros((len(frame), len(SUPPORT)), dtype=float)
        for row_index, prediction in enumerate(np.rint(point).astype(int)):
            for residual_value, probability in zip(SUPPORT, residual):
                value = int(np.clip(prediction + residual_value, SUPPORT[0], SUPPORT[-1]))
                b2[row_index, value - SUPPORT[0]] += probability
        b2 /= b2.sum(axis=1, keepdims=True)
        m1 = temperature_scale(
            artifact["m1"].predict_pmf(frame), float(artifact["m1_temperature"])
        )
        m2 = artifact["m2"].predict_pmf(frame)
        return {"B2": b2, "M1": m1, "M2": m2}


def _read_cursor(path: Path, cursor_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read complete newly appended lines and return an uncommitted cursor."""
    if not path.is_file():
        return [], {
            "path": str(path.resolve()),
            "missing": True,
            "offset": 0,
            "size_at_read": 0,
            "updated_at_utc": _now(),
        }
    state: dict[str, Any] = {}
    if cursor_path.is_file():
        state = json.loads(cursor_path.read_text(encoding="utf-8"))
    stat = path.stat()
    offset = int(state.get("offset") or 0)
    if state.get("device") != stat.st_dev or state.get("inode") != stat.st_ino or offset > stat.st_size:
        offset = 0
    rows: list[dict[str, Any]] = []
    completed_offset = offset
    with path.open("rb") as handle:
        handle.seek(offset)
        while True:
            start = handle.tell()
            line = handle.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                completed_offset = start
                break
            completed_offset = handle.tell()
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"non-object event row at offset {start}")
            rows.append(row)
    return rows, {
        "path": str(path.resolve()),
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "offset": completed_offset,
        "size_at_read": stat.st_size,
        "updated_at_utc": _now(),
    }


def _raw_day_path(root: Path, event: Mapping[str, Any]) -> pd.DataFrame:
    city = str(event["city"])
    contract = CITY[city]
    target_date = str(event["target_date"])
    observed_end = pd.Timestamp(event["source_obs_ts_utc"])
    observed_end = observed_end.tz_localize("UTC") if observed_end.tzinfo is None else observed_end.tz_convert("UTC")
    observed_start = pd.Timestamp(
        f"{target_date} 05:50:00",
        tz=ZoneInfo(str(contract["timezone"])),
    ).tz_convert("UTC")
    physical_dates = pd.date_range(observed_start.normalize(), observed_end.normalize(), freq="D")
    rows: list[dict[str, Any]] = []
    raw_rows = (
        raw
        for day in physical_dates
        for raw in read_jsonl(root / day.strftime("%Y-%m-%d") / "high_frequency_observations.jsonl")
    )
    for raw in raw_rows:
        if (
            raw.get("city") != city
            or raw.get("source") != contract["source"]
            or str(raw.get("station_id") or raw.get("station") or "") != contract["source_station"]
            or raw.get("temp_c") is None
        ):
            continue
        observed = raw.get("observation_time_utc") or raw.get("source_event_ts_utc")
        available = raw.get("available_at_utc") or raw.get("first_seen_at_utc")
        identity = raw.get("information_event_id") or raw.get("raw_row_hash")
        if not observed or not available or not identity:
            continue
        observed_ts = pd.Timestamp(observed)
        observed_ts = observed_ts.tz_localize("UTC") if observed_ts.tzinfo is None else observed_ts.tz_convert("UTC")
        if observed_ts < observed_start or observed_ts > observed_end:
            continue
        rows.append(
            {
                "target_date": target_date,
                "observed_at": observed,
                "available_at": available,
                "latest_fast_native_value": raw["temp_c"],
                "source_observation_id": str(identity),
                "raw_row_hash": raw.get("raw_row_hash"),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["target_date", "observed_at", "available_at", "latest_fast_native_value", "source_observation_id"])
    frame = pd.DataFrame(rows)
    frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True, errors="coerce")
    frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True, errors="coerce")
    frame["latest_fast_native_value"] = pd.to_numeric(frame["latest_fast_native_value"], errors="coerce")
    return frame.dropna(subset=["observed_at", "available_at", "latest_fast_native_value", "source_observation_id"])


def build_prediction(
    event: Mapping[str, Any],
    source_path: pd.DataFrame,
    models: FrozenModels,
    *,
    score_ready_at_utc: str | datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
    """Build one causal feature vintage and its three frozen model predictions."""
    trigger_event_id = _trigger_event_id(event)
    category, reasons = validate_exact_expression(event)
    audit: dict[str, Any] = {
        "trigger_event_id": trigger_event_id,
        "event_key": str(event.get("event_key") or ""),
        "city": str(event.get("city") or ""),
        "target_date": str(event.get("target_date") or ""),
        "classification": category,
        "reasons": reasons,
        "raw_event_sha256": _hash(dict(event)),
        "raw_event": dict(event),
        "orders": 0,
        "fills": 0,
        "notional": 0,
    }
    if category != "ELIGIBLE":
        return audit, None, []
    source_path = source_path.copy()
    audit["source_path_state_hash"] = _hash(source_path.to_dict("records"))
    required_path_columns = {
        "observed_at",
        "available_at",
        "latest_fast_native_value",
        "source_observation_id",
    }
    missing_path_columns = sorted(required_path_columns.difference(source_path.columns))
    if missing_path_columns:
        audit.update(
            classification="DATA_FAIL_CLOSED",
            reasons=[f"SOURCE_PATH_COLUMNS_MISSING:{','.join(missing_path_columns)}"],
        )
        return audit, None, []
    source_path["observed_at"] = pd.to_datetime(source_path["observed_at"], utc=True, errors="coerce")
    source_path["available_at"] = pd.to_datetime(source_path["available_at"], utc=True, errors="coerce")
    source_path["latest_fast_native_value"] = pd.to_numeric(
        source_path["latest_fast_native_value"], errors="coerce"
    )
    if source_path[list(required_path_columns)].isna().any(axis=None):
        audit.update(classification="DATA_FAIL_CLOSED", reasons=["SOURCE_PATH_VALUE_INVALID"])
        return audit, None, []
    city = str(event["city"])
    contract = CITY[city]
    try:
        observed = pd.Timestamp(event["source_obs_ts_utc"])
        observed = observed.tz_localize("UTC") if observed.tzinfo is None else observed.tz_convert("UTC")
        detect = pd.Timestamp(event["source_detect_ts_utc"])
        detect = detect.tz_localize("UTC") if detect.tzinfo is None else detect.tz_convert("UTC")
        decision = pd.Timestamp(event.get("ts_utc") or event["source_detect_ts_utc"])
        decision = decision.tz_localize("UTC") if decision.tzinfo is None else decision.tz_convert("UTC")
        source_value = float(event["source_temp_c"])
        last_official = float(event["latest_metar_round_c"])
        running_max = float(event["metar_running_max_round_c"])
    except (KeyError, TypeError, ValueError) as exc:
        audit.update(classification="DATA_FAIL_CLOSED", reasons=[f"FEATURE_CONTEXT_INVALID:{exc}"])
        return audit, None, []
    local = observed.tz_convert(str(contract["timezone"]))
    minute = local.hour * 60 + local.minute
    if minute < 350:
        audit.update(classification="RESEARCH_INELIGIBLE", reasons=["BEFORE_FROZEN_0550_LOCAL_PATH"])
        return audit, None, []
    expected_start = pd.Timestamp(f"{event['target_date']} 05:50:00", tz=ZoneInfo(str(contract["timezone"]))).tz_convert("UTC")
    day_path = source_path.loc[
        source_path["observed_at"].le(observed)
        & source_path["available_at"].le(decision)
    ].copy()
    complete = bool(
        len(day_path)
        and day_path["observed_at"].min() == expected_start
        and day_path["observed_at"].max() == observed
        and day_path["observed_at"].eq(observed).any()
    )
    context_ids = [str(event.get("event_key") or trigger_event_id), f"official_report:{event.get('latest_metar_report_ts_utc')}"]
    context = {
        "last_official_native_value": {"value": last_official, "available_at": detect, "source_observation_ids": context_ids},
        "official_running_max": {"value": running_max, "available_at": detect, "source_observation_ids": context_ids},
        "fast_minus_last_official": {"value": source_value - last_official, "available_at": detect, "source_observation_ids": context_ids},
        "fast_minus_running_max": {"value": source_value - running_max, "available_at": detect, "source_observation_ids": context_ids},
        "distance_to_up_native_boundary": {"value": math.ceil(source_value) - source_value, "available_at": detect, "source_observation_ids": context_ids[:1]},
        "distance_to_down_native_boundary": {"value": source_value - math.floor(source_value), "available_at": detect, "source_observation_ids": context_ids[:1]},
        "local_time_sin": {"value": math.sin(2 * math.pi * minute / 1440), "available_at": detect, "source_observation_ids": context_ids[:1]},
        "local_time_cos": {"value": math.cos(2 * math.pi * minute / 1440), "available_at": detect, "source_observation_ids": context_ids[:1]},
    }
    build = NextPrintFeatureBuilderV1.build_vintage(
        day_path,
        decision_vintage_id=trigger_event_id,
        feature_cutoff_at=decision,
        decision_ready_at=decision,
        observation_cutoff_at=observed,
        availability_class="PROSPECTIVE_SHADOW",
        feature_names=CORE_FEATURES,
        source_path_complete=complete,
        expected_cadence_minutes=10,
        feature_context=context,
    )
    endpoint = build.feature_vector.get("latest_fast_native_value")
    if build.status != "OK" or endpoint is None or not math.isclose(float(endpoint), source_value, abs_tol=1e-9):
        reason = build.status if build.status != "OK" else "ENDPOINT_VALUE_MISMATCH"
        audit.update(
            classification="DATA_FAIL_CLOSED",
            reasons=[reason],
            feature_status=build.status,
            source_path_row_count=build.source_path_row_count,
            observed_gap_count=build.observed_gap_count,
        )
        return audit, None, build.lineage.to_dict("records")
    frame = pd.DataFrame([{**dict(event), **build.feature_vector}])
    pmfs = models.predict(city, frame)
    threshold_delta = running_max - last_official
    summaries: dict[str, Any] = {}
    prediction: dict[str, Any] = {
        "prediction_row_id": trigger_event_id,
        "decision_vintage_id": trigger_event_id,
        "trigger_event_id": trigger_event_id,
        "event_key": str(event["event_key"]),
        "city": city,
        "target_date": str(event["target_date"]),
        "forward_epoch_id": models.epoch,
        "feature_builder": NextPrintFeatureBuilderV1.version,
        "feature_vector": build.feature_vector,
        "feature_vector_hash": build.feature_vector_hash,
        "source_path_row_count": build.source_path_row_count,
        "source_path_start": build.source_path_start,
        "source_path_end": build.source_path_end,
        "max_feature_available_at": build.max_feature_available_at,
        "raw_source_lineage": {
            "target_date": str(event["target_date"]),
            "observation_time_utc": observed.isoformat(),
            "available_at_utc": decision.isoformat(),
            "source_detect_ts_utc": detect.isoformat(),
            "source": event["source"],
            "source_station": event["station"],
            "prior_official": {
                "station": contract["official_station"],
                "report_at_utc": event.get("latest_metar_report_ts_utc"),
                "last_official_native_value": last_official,
                "official_running_max": running_max,
            },
        },
        "market_identity_seed": _market_identity_seed(event),
        "orders": 0,
        "fills": 0,
        "notional": 0,
    }
    for model, pmf_rows in pmfs.items():
        pmf = np.asarray(pmf_rows[0], dtype=float)
        if len(pmf) != len(SUPPORT) or not math.isclose(float(pmf.sum()), 1.0, abs_tol=1e-9):
            raise RuntimeError(f"invalid {city} {model} PMF")
        p_new = float(pmf[SUPPORT > threshold_delta].sum())
        summaries[model] = {
            "model_id": MODEL_IDS[model],
            "pmf": pmf.tolist(),
            "argmax_delta_tick": int(SUPPORT[int(np.argmax(pmf))]),
            "expected_delta_tick": float(pmf @ SUPPORT),
            "p_up": float(pmf[SUPPORT > 0].sum()),
            "p_down": float(pmf[SUPPORT < 0].sum()),
            "p_unchanged": float(pmf[SUPPORT == 0].sum()),
            "p_new_running_max": p_new,
            "selector_pass": p_new >= ACTION_THRESHOLD,
            "is_exact_token_fair_probability": False,
        }
        prediction[f"{model}_PMF"] = pmf.tolist()
    prediction["models"] = summaries
    ready = (
        pd.Timestamp(score_ready_at_utc)
        if score_ready_at_utc is not None
        else pd.Timestamp(_now())
    )
    ready = ready.tz_localize("UTC") if ready.tzinfo is None else ready.tz_convert("UTC")
    if ready < decision:
        ready = decision
    prediction["model_score_ready_at_utc"] = ready.isoformat().replace("+00:00", "Z")
    audit.update(
        classification="ELIGIBLE",
        reasons=[],
        feature_status="OK",
        feature_vector_hash=build.feature_vector_hash,
        source_path_row_count=build.source_path_row_count,
        observed_gap_count=build.observed_gap_count,
    )
    return audit, prediction, build.lineage.to_dict("records")


def label_prediction(prediction: Mapping[str, Any], rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Link the first distinct eligible routine EFHK/RJTT print, fail closed."""
    pid = str(prediction["prediction_row_id"])
    city = str(prediction["city"])
    contract = CITY[city]
    lineage = prediction["raw_source_lineage"]
    source_observed = _parse(lineage["observation_time_utc"], field="source_observation_time")
    decision = _parse(lineage["available_at_utc"], field="decision_time")
    prior_report_raw = lineage["prior_official"].get("report_at_utc")
    prior_report = _parse(prior_report_raw, field="prior_official_report")
    prior_value = float(lineage["prior_official"]["last_official_native_value"])
    running_max = float(lineage["prior_official"]["official_running_max"])
    by_report: dict[datetime, list[tuple[datetime, Mapping[str, Any]]]] = defaultdict(list)
    for row in rows:
        station = str(row.get("station_id") or row.get("station") or "")
        if (
            row.get("city") != city
            or row.get("source") != "aviationweather_metar"
            or station != contract["official_station"]
            or row.get("temp_c") is None
        ):
            continue
        report_raw = row.get("source_report_ts_utc") or row.get("source_event_ts_utc")
        available_raw = row.get("first_seen_at_utc") or row.get("local_detect_ts_utc")
        if not report_raw or not available_raw:
            continue
        try:
            report = _parse(report_raw, field="official_report")
            available = _parse(available_raw, field="official_first_seen")
        except ValueError:
            continue
        if report.minute not in contract["routine_minutes"]:
            continue
        if report <= source_observed or report <= prior_report:
            continue
        by_report[report].append((available, row))
    base = {
        "prediction_row_id": pid,
        "trigger_event_id": str(prediction["trigger_event_id"]),
        "city": city,
        "orders": 0,
        "fills": 0,
        "notional": 0,
    }
    if not by_report:
        reason = "NEXT_ROUTINE_OFFICIAL_PRINT_NOT_YET_CAPTURED"
        return {**base, "label_row_id": _hash({"prediction": pid, "reason": reason}), "status": "pending", "missing_reason": reason, "future_next_print_label": None}
    report = min(by_report)
    if (report - source_observed).total_seconds() > 1200:
        reason = "NEXT_ROUTINE_OUTSIDE_FROZEN_1200S_WINDOW"
        return {**base, "label_row_id": _hash({"prediction": pid, "report": report.isoformat(), "reason": reason}), "status": "missing", "missing_reason": reason, "future_next_print_label": None}
    candidates = sorted(by_report[report], key=lambda item: (item[0], str(item[1].get("information_event_id") or "")))
    earliest_available = candidates[0][0]
    if earliest_available <= decision:
        # Select the physical target before testing availability. Skipping a
        # known target would silently search for a different later report.
        reason = "TARGET_ALREADY_AVAILABLE_AT_DECISION"
        return {**base, "label_row_id": _hash({"prediction": pid, "report": report.isoformat(), "reason": reason}), "status": "missing", "missing_reason": reason, "future_next_print_label": None}
    earliest = [row for available, row in candidates if available == earliest_available]
    values = {_half_up(float(row["temp_c"])) for row in earliest}
    if len(values) != 1:
        reason = "AMBIGUOUS_EQUAL_RANK_OFFICIAL_REVISION"
        return {**base, "label_row_id": _hash({"prediction": pid, "report": report.isoformat(), "reason": reason}), "status": "missing", "missing_reason": reason, "future_next_print_label": None}
    row = earliest[0]
    native = next(iter(values))
    delta = int(native - _half_up(prior_value))
    official_id = str(row.get("information_event_id") or row.get("content_key") or _hash(dict(row)))
    return {
        **base,
        "label_row_id": _hash({"prediction": pid, "official_print_id": official_id}),
        "status": "linked",
        "missing_reason": None,
        "future_next_print_label": delta,
        "next_official_delta_native_tick": delta,
        "next_official_native_value": native,
        "next_print_new_running_max": native > running_max,
        "official_print_id": official_id,
        "official_print_group_id": official_id,
        "official_print_time_utc": report.isoformat().replace("+00:00", "Z"),
        "official_first_seen_at_utc": earliest_available.isoformat().replace("+00:00", "Z"),
        "official_station": contract["official_station"],
        "official_source": "aviationweather_metar",
        "official_raw_row_hash": row.get("raw_row_hash"),
        "official_raw_payload_hash": row.get("raw_payload_hash"),
        "matching_window_seconds": 1200,
    }


def _capture_demand(
    prediction: Mapping[str, Any],
    identity: Mapping[str, Any],
    selected_models: Sequence[str],
) -> dict[str, Any]:
    decision = _parse(prediction["raw_source_lineage"]["available_at_utc"], field="decision_time")
    score_ready = _parse(prediction["model_score_ready_at_utc"], field="model_score_ready_at_utc")
    identity_ready = _parse(identity["identity_available_at_utc"], field="identity_available_at_utc")
    requested = max(decision, score_ready, identity_ready)
    demand = CaptureDemand.create(
        consumer_id=STRATEGY_KEY,
        strategy_key=STRATEGY_KEY,
        condition_id=str(identity["condition_id"]),
        token_id=str(identity["token_id"]),
        reason="next_print_previous_running_max_exact_no_diagnostic",
        priority="P1",
        requested_at_utc=requested.isoformat().replace("+00:00", "Z"),
        expires_at_utc=(requested + timedelta(minutes=120)).isoformat().replace("+00:00", "Z"),
        desired_transport="REST_WS",
        requested_checkpoints_seconds=CHECKPOINTS,
        trigger_event_id=str(prediction["trigger_event_id"]),
        metadata={
            "forward_epoch_id": prediction["forward_epoch_id"],
            "city": prediction["city"],
            "target_date": prediction["target_date"],
            "market_id": identity["market_id"],
            "polymarket_event_id": identity["polymarket_event_id"],
            "bracket": identity["native_bracket"],
            "side": "NO",
            "model_arms": list(selected_models),
            "identity_source": identity["identity_source"],
            "checkpoint_timestamp": identity["checkpoint_timestamp"],
            "orders": 0,
            "fills": 0,
            "notional": 0,
        },
    )
    return demand.to_dict()


def _date_group_metric(
    rows: Sequence[Mapping[str, Any]], metric: str
) -> tuple[float | None, dict[str, float]]:
    by_date_group: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        by_date_group[(str(row["target_date"]), str(row["official_print_group_id"]))].append(
            float(row[metric])
        )
    by_date: dict[str, list[float]] = defaultdict(list)
    for (target_date, _), values in by_date_group.items():
        by_date[target_date].append(float(np.mean(values)))
    daily = {target_date: float(np.mean(values)) for target_date, values in by_date.items()}
    return (None if not daily else float(np.mean(list(daily.values())))), daily


def _summarize_score_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    metrics = {
        name: _date_group_metric(rows, column)[0]
        for name, column in (
            ("exact_delta_accuracy", "exact_delta"),
            ("next_print_new_running_max_accuracy", "new_max_hit"),
            ("rps", "rps"),
            ("logloss", "logloss"),
        )
    }
    return {
        "raw_rows": len(rows),
        "exact_delta_hits": sum(int(row["exact_delta"]) for row in rows),
        "next_print_new_running_max_hits": sum(int(row["new_max_hit"]) for row in rows),
        "official_print_groups": len(
            {(str(row["target_date"]), str(row["official_print_group_id"])) for row in rows}
        ),
        "target_dates": len({str(row["target_date"]) for row in rows}),
        "weighting": "target_date_equal_then_official_print_group_equal_then_row_equal",
        **metrics,
    }


def _paired_rps_delta(
    baseline: Sequence[Mapping[str, Any]], challenger: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    baseline_by_id = {str(row["prediction_row_id"]): row for row in baseline}
    challenger_by_id = {str(row["prediction_row_id"]): row for row in challenger}
    common = sorted(set(baseline_by_id).intersection(challenger_by_id))
    base_rows = [baseline_by_id[key] for key in common]
    challenger_rows = [challenger_by_id[key] for key in common]
    _, base_daily = _date_group_metric(base_rows, "rps")
    _, challenger_daily = _date_group_metric(challenger_rows, "rps")
    dates = sorted(set(base_daily).intersection(challenger_daily))
    daily_delta = {day: challenger_daily[day] - base_daily[day] for day in dates}
    result: dict[str, Any] = {
        "paired_rows": len(common),
        "target_dates": len(dates),
        "bootstrap_unit": "target_date",
        "candidate_minus_baseline": (
            None if not dates else float(np.mean([daily_delta[day] for day in dates]))
        ),
        "ci95": None,
        "reps": 0,
        "status": "NOT_ESTIMABLE_FEWER_THAN_TWO_TARGET_DATES",
    }
    if len(dates) < 2:
        return result
    rng = np.random.default_rng(20260830)
    samples = [
        float(np.mean([daily_delta[str(day)] for day in rng.choice(dates, len(dates), replace=True)]))
        for _ in range(2000)
    ]
    result.update(
        ci95=[float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))],
        reps=2000,
        status="OK",
    )
    return result


def _score_metrics(
    predictions: Sequence[Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    action_keys = {(str(row["prediction_row_id"]), str(row["model"])) for row in actions}
    model_rows: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for model in MODEL_IDS:
        rows: list[dict[str, Any]] = []
        selected_rows: list[dict[str, Any]] = []
        for prediction in predictions:
            pid = str(prediction["prediction_row_id"])
            label = labels.get(pid)
            if not label or label.get("status") != "linked":
                continue
            tick = int(label["future_next_print_label"])
            if tick < SUPPORT[0] or tick > SUPPORT[-1]:
                continue
            pmf = np.asarray(prediction[f"{model}_PMF"], dtype=float)
            cdf = np.cumsum(pmf)
            observed = (SUPPORT >= tick).astype(float)
            row = {
                "prediction_row_id": pid,
                "target_date": prediction["target_date"],
                "official_print_group_id": label["official_print_group_id"],
                "exact_delta": int(SUPPORT[int(np.argmax(pmf))] == tick),
                "rps": float(np.sum((cdf[:-1] - observed[:-1]) ** 2) / (len(SUPPORT) - 1)),
                "logloss": float(-math.log(max(float(pmf[tick - SUPPORT[0]]), 1e-12))),
                "new_max_hit": int(
                    (float(prediction["models"][model]["p_new_running_max"]) >= ACTION_THRESHOLD)
                    == bool(label["next_print_new_running_max"])
                ),
            }
            rows.append(row)
            if (pid, model) in action_keys:
                selected_rows.append(row)
        model_rows[model] = {
            "all_feature_eligible": rows,
            "unique_expression_signals": selected_rows,
        }
    summaries = {
        model: {
            cohort: _summarize_score_rows(rows)
            for cohort, rows in cohorts.items()
        }
        for model, cohorts in model_rows.items()
    }
    comparisons = {
        f"{model}_minus_B2": {
            "all_feature_eligible": _paired_rps_delta(
                model_rows["B2"]["all_feature_eligible"],
                model_rows[model]["all_feature_eligible"],
            ),
            "challenger_selected_opportunity_matched": _paired_rps_delta(
                model_rows["B2"]["all_feature_eligible"],
                model_rows[model]["unique_expression_signals"],
            ),
        }
        for model in ("M1", "M2")
    }
    return {"models": summaries, "challenger_rps_delta": comparisons}


def _date_block_roi(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in rows if row.get("status") == "EXECUTABLE_SETTLED"]
    by_date: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in eligible:
        by_date[str(row["target_date"])].append(row)
    dates = sorted(by_date)
    total_cost = sum(float(row["entry_effective_cost_usd"]) for row in eligible)
    total_pnl = sum(float(row["net_pnl_usd"]) for row in eligible)
    result = {
        "rows": len(eligible),
        "target_dates": len(dates),
        "wins": sum(float(row["net_pnl_usd"]) > 0 for row in eligible),
        "losses": sum(float(row["net_pnl_usd"]) <= 0 for row in eligible),
        "entry_cost_usd": total_cost,
        "net_pnl_usd": total_pnl,
        "roi": _safe_rate(total_pnl, total_cost),
        "target_date_block_bootstrap_ci95": None,
        "bootstrap_status": "NOT_ESTIMABLE_FEWER_THAN_TWO_TARGET_DATES",
    }
    if len(dates) < 2:
        return result
    rng = np.random.default_rng(20260830)
    samples: list[float] = []
    for _ in range(2000):
        chosen = rng.choice(dates, len(dates), replace=True)
        sample_rows = [row for day in chosen for row in by_date[str(day)]]
        cost = sum(float(row["entry_effective_cost_usd"]) for row in sample_rows)
        pnl = sum(float(row["net_pnl_usd"]) for row in sample_rows)
        if cost:
            samples.append(pnl / cost)
    result["target_date_block_bootstrap_ci95"] = [float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))]
    result["bootstrap_status"] = "OK"
    result["bootstrap_reps"] = 2000
    return result


class HelsinkiTokyoNextPrintFrozenRuntime:
    """Append-only, retry-safe forward scorer and executable replay materializer."""

    def __init__(
        self,
        root: Path,
        *,
        review_root: Path,
        event_path: Path,
        source_path_root: Path,
        official_path_root: Path,
        settlement_db: Path | None = None,
        ws_runtime_root: Path | None = None,
        producer_health_path: Path | None = None,
        market_latest_path: Path | None = None,
        forward_epoch_id: str | None = None,
        forward_start_utc: str | None = None,
        market_metadata_fetcher: MarketMetadataFetcher | None = None,
    ) -> None:
        self.root = root
        self.models = FrozenModels(review_root)
        self.forward_epoch = str(forward_epoch_id or self.models.epoch)
        self.forward_start = _parse(
            forward_start_utc or self.models.config["forward_start_utc"],
            field="forward_start_utc",
        )
        self.event_path = event_path
        self.source_path_root = source_path_root
        self.official_path_root = official_path_root
        self.settlement_db = settlement_db
        self.ws_runtime_root = ws_runtime_root
        self.producer_health_path = producer_health_path
        self.market_latest_path = market_latest_path
        self.market_metadata_fetcher = market_metadata_fetcher or fetch_gamma_market
        self.raw_events = ImmutableJsonl(root / "raw_events.jsonl", "trigger_event_id")
        self.opportunities = ImmutableJsonl(
            root / "opportunity_attempts.jsonl", "attempt_id"
        )
        self.predictions = ImmutableJsonl(root / "predictions.jsonl", "prediction_row_id")
        self.lineage = ImmutableJsonl(root / "feature_lineage.jsonl", "lineage_row_id")
        self.market_payloads = ImmutableJsonl(
            root / "market_metadata_payloads.jsonl", "raw_market_payload_sha256"
        )
        self.market_identities = ImmutableJsonl(
            root / "market_identities.jsonl", "prediction_row_id"
        )
        self.decisions = ImmutableJsonl(root / "policy_decisions.jsonl", "decision_row_id")
        self.actions = ImmutableJsonl(root / "actions.jsonl", "action_id")
        self.demands = ImmutableJsonl(root / "capture_demands.jsonl", "demand_id")
        self.subscription_readiness = ImmutableJsonl(
            root / "subscription_readiness.jsonl", "demand_id"
        )
        self.labels = ImmutableJsonl(root / "labels.jsonl", "label_row_id")
        self.settlements = ImmutableJsonl(root / "settlements.jsonl", "settlement_row_id")
        self.markouts = ImmutableJsonl(root / "markouts.jsonl", "markout_row_id")
        self.signal_candidates = ImmutableJsonl(
            root / "signal_candidates.jsonl", "candidate_id"
        )
        self.trade_intents = ImmutableJsonl(root / "trade_intents.jsonl", "intent_id")

    @staticmethod
    def _as_utc(value: datetime | None = None) -> datetime:
        result = value or datetime.now(UTC)
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)

    @staticmethod
    def _utc_text(value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def _new_events(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rows, cursor = _read_cursor(self.event_path, self.root / "input_cursor.json")
        output: list[dict[str, Any]] = []
        for row in rows:
            city = str(row.get("city") or "")
            if city not in CITY or str(row.get("source") or "") != CITY[city]["source"]:
                continue
            try:
                detected = _parse(
                    row.get("source_detect_ts_utc"), field="source_detect_ts_utc"
                )
            except (TypeError, ValueError):
                continue
            if detected < self.forward_start:
                continue
            output.append({**dict(row), "trigger_event_id": _trigger_event_id(row)})
        return output, cursor

    def _latest_attempts(self) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for row in self.opportunities.rows.values():
            latest[str(row["trigger_event_id"])] = row
        return latest

    @staticmethod
    def _path_failure_is_retryable(audit: Mapping[str, Any]) -> bool:
        retryable = {
            "INCOMPLETE_CAPTURED_SOURCE_PATH",
            "SOURCE_PATH_COLUMNS_MISSING",
            "SOURCE_PATH_VALUE_INVALID",
        }
        roots = {str(reason).split(":", 1)[0] for reason in audit.get("reasons") or ()}
        return bool(roots) and roots.issubset(retryable)

    def ingest(self, *, now_utc: datetime | None = None) -> dict[str, int]:
        now = self._as_utc(now_utc)
        now_text = self._utc_text(now)
        result = {
            "new_input_rows": 0,
            "raw_events": 0,
            "opportunity_attempts": 0,
            "retry_pending": 0,
            "retry_timeouts": 0,
            "predictions": 0,
            "lineage": 0,
        }
        events, cursor = self._new_events()
        result["new_input_rows"] = len(events)
        for event in events:
            result["raw_events"] += int(self.raw_events.put(event))
        # Once the source row is durable locally, advancing the cursor cannot
        # lose it. Any later failure is replayed from raw_events.jsonl.
        _write_json_atomic(self.root / "input_cursor.json", cursor)

        latest = self._latest_attempts()
        attempts_by_trigger = Counter(
            str(row["trigger_event_id"]) for row in self.opportunities.rows.values()
        )
        for trigger_event_id, event in self.raw_events.rows.items():
            if trigger_event_id in self.predictions.rows:
                continue
            prior = latest.get(trigger_event_id)
            if prior is not None and prior.get("terminal") is True:
                continue
            source_path = _raw_day_path(self.source_path_root, event)
            audit, prediction, lineage = build_prediction(
                event,
                source_path,
                self.models,
                score_ready_at_utc=now,
            )
            if prediction is not None:
                prediction["forward_epoch_id"] = self.forward_epoch
            source_state_hash = str(
                audit.get("source_path_state_hash")
                or _hash(source_path.to_dict("records"))
            )
            reasons = [str(reason) for reason in audit.get("reasons") or ()]
            retryable = (
                audit.get("classification") == "DATA_FAIL_CLOSED"
                and self._path_failure_is_retryable(audit)
            )
            terminal = audit.get("classification") in {
                "ELIGIBLE",
                "RESEARCH_INELIGIBLE",
            } or (audit.get("classification") == "DATA_FAIL_CLOSED" and not retryable)
            timed_out = False
            if retryable:
                decision = _parse(
                    event.get("ts_utc") or event.get("source_detect_ts_utc"),
                    field="retry_decision_time",
                )
                if now >= decision + timedelta(seconds=FEATURE_RETRY_TIMEOUT_SECONDS):
                    reasons = [*reasons, f"FEATURE_RETRY_TIMEOUT_{FEATURE_RETRY_TIMEOUT_SECONDS}S"]
                    terminal = True
                    timed_out = True
                else:
                    result["retry_pending"] += 1
            fingerprint = {
                "trigger_event_id": trigger_event_id,
                "source_path_state_hash": source_state_hash,
                "classification": audit.get("classification"),
                "reasons": reasons,
                "terminal": terminal,
            }
            attempt_id = _hash(fingerprint)
            existing = self.opportunities.rows.get(attempt_id)
            attempt = {
                **audit,
                **fingerprint,
                "attempt_id": attempt_id,
                "attempt_number": (
                    int(existing["attempt_number"])
                    if existing is not None
                    else attempts_by_trigger[trigger_event_id] + 1
                ),
                "attempted_at_utc": (
                    str(existing["attempted_at_utc"])
                    if existing is not None
                    else now_text
                ),
                "retry_timeout_seconds": FEATURE_RETRY_TIMEOUT_SECONDS,
            }
            added = self.opportunities.put(attempt)
            result["opportunity_attempts"] += int(added)
            if added:
                attempts_by_trigger[trigger_event_id] += 1
                latest[trigger_event_id] = attempt
            result["retry_timeouts"] += int(timed_out and added)
            if prediction is None:
                continue
            for item in lineage:
                body = {
                    **item,
                    "trigger_event_id": trigger_event_id,
                    "city": audit["city"],
                    "target_date": audit["target_date"],
                }
                body["lineage_row_id"] = _hash(
                    {
                        "trigger_event_id": trigger_event_id,
                        "feature_name": body.get("feature_name"),
                    }
                )
                result["lineage"] += int(self.lineage.put(body))
            result["predictions"] += int(self.predictions.put(prediction))
        return result

    def materialize_market_identities(
        self, *, now_utc: datetime | None = None
    ) -> dict[str, Any]:
        now = self._as_utc(now_utc)
        now_text = self._utc_text(now)
        result: dict[str, Any] = {
            "resolved": 0,
            "resolved_from_market_books": 0,
            "resolved_from_gamma": 0,
            "unresolved": 0,
            "http_calls": 0,
            "payloads": 0,
            "reasons": {},
        }
        payload_by_market: dict[str, dict[str, Any]] = {}
        for row in self.market_payloads.rows.values():
            if row.get("source") == "gamma_public_market_by_id":
                payload_by_market[str(row["market_id"])] = row
        market_latest: Mapping[str, Any] | None = None
        market_latest_error: str | None = None
        if self.market_latest_path is not None:
            try:
                loaded = json.loads(self.market_latest_path.read_text(encoding="utf-8"))
                if not isinstance(loaded, Mapping):
                    raise ValueError("canonical market-book snapshot is not an object")
                market_latest = loaded
            except Exception as exc:
                market_latest_error = f"{type(exc).__name__}:{exc}"
        unresolved: list[dict[str, Any]] = []
        for pid, prediction in self.predictions.rows.items():
            if pid in self.market_identities.rows:
                continue
            seed = dict(prediction["market_identity_seed"])
            market_id = str(seed.get("market_id") or "")
            payload_row = payload_by_market.get(market_id)
            try:
                identity: dict[str, Any] | None = None
                local_error: str | None = market_latest_error
                if market_latest is not None:
                    try:
                        identity, evidence = resolve_market_book_identity(seed, market_latest)
                        payload_hash = str(identity["raw_market_payload_sha256"])
                        local_payload_row = {
                            "raw_market_payload_sha256": payload_hash,
                            "market_id": market_id,
                            "captured_at_utc": identity["identity_available_at_utc"],
                            "source": "canonical_market_books_latest",
                            "payload": evidence,
                        }
                        result["payloads"] += int(self.market_payloads.put(local_payload_row))
                        result["resolved_from_market_books"] += 1
                    except Exception as exc:
                        local_error = f"{type(exc).__name__}:{exc}"
                if identity is None and payload_row is None:
                    payload = dict(self.market_metadata_fetcher(market_id))
                    result["http_calls"] += 1
                    payload_hash = _hash(payload)
                    payload_row = self.market_payloads.rows.get(payload_hash)
                    if payload_row is None:
                        payload_row = {
                            "raw_market_payload_sha256": payload_hash,
                            "market_id": market_id,
                            "captured_at_utc": now_text,
                            "source": "gamma_public_market_by_id",
                            "payload": payload,
                        }
                        result["payloads"] += int(self.market_payloads.put(payload_row))
                    payload_by_market[market_id] = payload_row
                if identity is None:
                    assert payload_row is not None
                    available_at = str(payload_row["captured_at_utc"])
                    identity = resolve_exact_market_identity(
                        seed,
                        available_at_utc=available_at,
                        fetcher=lambda _market_id, payload=payload_row["payload"]: payload,
                    )
                    result["resolved_from_gamma"] += 1
                identity_row = {
                    **identity,
                    "prediction_row_id": pid,
                    "identity_resolution_id": _hash(
                        {
                            "prediction_row_id": pid,
                            "raw_market_payload_sha256": identity[
                                "raw_market_payload_sha256"
                            ],
                        }
                    ),
                }
                result["resolved"] += int(self.market_identities.put(identity_row))
            except Exception as exc:  # fail closed; the next loop retries read-only metadata
                reason = f"{type(exc).__name__}:{exc}"
                if local_error:
                    reason = f"market_books={local_error};gamma={reason}"
                unresolved.append(
                    {
                        "prediction_row_id": pid,
                        "trigger_event_id": prediction["trigger_event_id"],
                        "city": prediction["city"],
                        "target_date": prediction["target_date"],
                        "market_id": market_id,
                        "reason": reason,
                    }
                )
                result["unresolved"] += 1
                result["reasons"][reason] = int(result["reasons"].get(reason, 0)) + 1
        snapshot = {
            **result,
            "generated_at_utc": now_text,
            "unresolved_rows": unresolved,
        }
        _write_json_atomic(self.root / "market_identity_resolution_latest.json", snapshot)
        return result

    def materialize_policy(self) -> dict[str, int]:
        """Freeze first-condition selector decisions only after exact identity resolves."""
        result = {"decisions": 0, "actions": 0, "demands": 0}
        ordered = sorted(
            self.predictions.rows.values(),
            key=lambda row: (
                str(row.get("raw_source_lineage", {}).get("available_at_utc") or ""),
                str(row.get("prediction_row_id") or ""),
            ),
        )
        first_by_condition: dict[str, str] = {}
        for prediction in ordered:
            condition = str(prediction["market_identity_seed"]["condition_id"])
            first_by_condition.setdefault(condition, str(prediction["prediction_row_id"]))
        for prediction in ordered:
            pid = str(prediction["prediction_row_id"])
            identity = self.market_identities.rows.get(pid)
            if identity is None:
                continue
            condition_id = str(identity["condition_id"])
            is_first_expression = first_by_condition.get(condition_id) == pid
            action_ready = max(
                _parse(
                    prediction["model_score_ready_at_utc"],
                    field="model_score_ready_at_utc",
                ),
                _parse(
                    identity["identity_available_at_utc"],
                    field="identity_available_at_utc",
                ),
            )
            linked_label = next(
                (
                    row
                    for row in self.labels.rows.values()
                    if str(row.get("prediction_row_id") or "") == pid
                    and row.get("status") == "linked"
                ),
                None,
            )
            action_ready_after_print = bool(
                linked_label
                and action_ready
                >= _parse(
                    linked_label["official_first_seen_at_utc"],
                    field="official_first_seen_at_utc",
                )
            )
            selected: list[str] = []
            for model, model_row in prediction["models"].items():
                selector_pass = bool(model_row["selector_pass"])
                action_id = _hash(
                    {
                        "epoch": self.forward_epoch,
                        "model": model,
                        "condition_id": condition_id,
                    }
                )
                if not selector_pass:
                    classification, reason = (
                        "POLICY_ABSTAIN",
                        "P_NEW_RUNNING_MAX_BELOW_0P5",
                    )
                elif action_ready_after_print:
                    classification, reason = (
                        "DATA_FAIL_CLOSED",
                        "ACTION_READY_NOT_BEFORE_NEXT_OFFICIAL_PRINT",
                    )
                elif not is_first_expression:
                    classification, reason = (
                        "POLICY_ABSTAIN",
                        "DUPLICATE_MODEL_CONDITION_EXPRESSION",
                    )
                else:
                    classification, reason = "SELECTED_DIAGNOSTIC", None
                    selected.append(model)
                    action = {
                        "action_id": action_id,
                        "prediction_row_id": pid,
                        "trigger_event_id": prediction["trigger_event_id"],
                        "polymarket_event_id": identity["polymarket_event_id"],
                        "event_key": prediction["event_key"],
                        "city": prediction["city"],
                        "target_date": prediction["target_date"],
                        "model": model,
                        "model_id": model_row["model_id"],
                        "p_new_running_max": model_row["p_new_running_max"],
                        "selector_threshold": ACTION_THRESHOLD,
                        "action": "DIAGNOSTIC_BUY_NO_PREVIOUS_OFFICIAL_RUNNING_MAX",
                        "market_identity": dict(identity),
                        "model_score_ready_at_utc": prediction[
                            "model_score_ready_at_utc"
                        ],
                        "identity_available_at_utc": identity[
                            "identity_available_at_utc"
                        ],
                        "action_ready_at_utc": self._utc_text(action_ready),
                        "entry_latency_seconds": ENTRY_LATENCY_SECONDS,
                        "shares": SHARES,
                        "max_taker_vwap": MAX_TAKER_VWAP,
                        "is_exact_token_fair_probability": False,
                        "orders": 0,
                        "fills": 0,
                        "notional": 0,
                    }
                    result["actions"] += int(self.actions.put(action))
                decision = {
                    "decision_row_id": _hash({"prediction": pid, "model": model}),
                    "prediction_row_id": pid,
                    "trigger_event_id": prediction["trigger_event_id"],
                    "polymarket_event_id": identity["polymarket_event_id"],
                    "city": prediction["city"],
                    "target_date": prediction["target_date"],
                    "model": model,
                    "p_new_running_max": model_row["p_new_running_max"],
                    "selector_pass": selector_pass,
                    "classification": classification,
                    "reason": reason,
                    "action_id": (
                        action_id if classification == "SELECTED_DIAGNOSTIC" else None
                    ),
                    "orders": 0,
                    "fills": 0,
                    "notional": 0,
                }
                result["decisions"] += int(self.decisions.put(decision))
            if selected:
                result["demands"] += int(
                    self.demands.put(_capture_demand(prediction, identity, selected))
                )
        return result

    def _subscription_epoch_rows(self) -> list[dict[str, Any]]:
        if self.ws_runtime_root is None:
            return []
        root = (
            self.ws_runtime_root
            / "market_books"
            / "ws_incremental"
            / "subscription_epochs"
        )
        rows: list[dict[str, Any]] = []
        for path in sorted(root.glob("subscription_epochs_????-??-??.jsonl")):
            for row in read_jsonl(path):
                rows.append({**row, "_source_path": str(path)})
        return sorted(
            rows,
            key=lambda row: (
                str(row.get("started_at_utc") or ""),
                str(row.get("subscription_epoch_id") or ""),
            ),
        )

    def materialize_subscription_readiness(
        self, *, now_utc: datetime | None = None
    ) -> dict[str, int]:
        now = self._as_utc(now_utc)
        result = {"ready": 0, "pending": 0, "epochs_scanned": 0}
        pending_demands = [
            (demand_id, demand)
            for demand_id, demand in self.demands.rows.items()
            if demand_id not in self.subscription_readiness.rows
        ]
        if not pending_demands:
            return result
        epochs = self._subscription_epoch_rows()
        result["epochs_scanned"] = len(epochs)
        for demand_id, demand in pending_demands:
            requested = _parse(demand["requested_at_utc"], field="capture_requested_at")
            match: dict[str, Any] | None = None
            for epoch in epochs:
                try:
                    started = _parse(
                        epoch.get("started_at_utc"), field="subscription_started_at"
                    )
                except (TypeError, ValueError):
                    continue
                if started < requested or started > now:
                    continue
                token_id = str(demand["token_id"])
                token_row = (epoch.get("token_rows") or {}).get(token_id) or {}
                mapped_ids = {
                    str(value)
                    for value in token_row.get("capture_demand_ids") or ()
                    if value
                }
                if token_row.get("capture_demand_id"):
                    mapped_ids.add(str(token_row["capture_demand_id"]))
                captured = next(
                    (
                        row
                        for row in epoch.get("capture_demands") or ()
                        if str(row.get("demand_id") or "") == demand_id
                    ),
                    None,
                )
                if captured is None and demand_id in mapped_ids:
                    captured = {
                        **demand,
                        "resolution_status": "resolved_direct_token",
                    }
                if captured is None:
                    continue
                if str(captured.get("resolution_status") or "") != "resolved_direct_token":
                    continue
                if str(token_row.get("condition_id") or "") != str(
                    demand["condition_id"]
                ):
                    continue
                receipt = CaptureReceipt.from_epoch(captured, epoch).to_dict()
                decision = _parse(
                    self.predictions.rows[str(demand["trigger_event_id"])][
                        "raw_source_lineage"
                    ]["available_at_utc"],
                    field="decision_time",
                )
                score_ready = _parse(
                    self.predictions.rows[str(demand["trigger_event_id"])][
                        "model_score_ready_at_utc"
                    ],
                    field="model_score_ready_at_utc",
                )
                identity_ready = _parse(
                    self.market_identities.rows[str(demand["trigger_event_id"])][
                        "identity_available_at_utc"
                    ],
                    field="identity_available_at_utc",
                )
                entry_ready = max(
                    decision + timedelta(seconds=ENTRY_LATENCY_SECONDS),
                    score_ready,
                    identity_ready,
                    started,
                )
                match = {
                    "demand_id": demand_id,
                    "prediction_row_id": str(demand["trigger_event_id"]),
                    "trigger_event_id": str(demand["trigger_event_id"]),
                    "condition_id": demand["condition_id"],
                    "token_id": demand["token_id"],
                    "capture_subscription_ready_at_utc": self._utc_text(started),
                    "entry_ready_at_utc": self._utc_text(entry_ready),
                    "subscription_epoch_id": receipt["subscription_epoch_id"],
                    "capture_receipt_id": receipt["receipt_id"],
                    "token_map_id": epoch.get("token_map_id"),
                    "subscription_epoch_row_sha256": _hash(
                        {key: value for key, value in epoch.items() if key != "_source_path"}
                    ),
                    "subscription_epoch_source_path": epoch.get("_source_path"),
                    "orders": 0,
                    "fills": 0,
                    "notional": 0,
                }
                break
            if match is None:
                result["pending"] += 1
            else:
                result["ready"] += int(self.subscription_readiness.put(match))
        return result

    def materialize(self, *, now_utc: datetime | None = None) -> dict[str, int]:
        result = {"labels": 0, "settlements": 0, "markouts": 0}
        linked_by_prediction: dict[str, dict[str, Any]] = {}
        for row in self.labels.rows.values():
            if row.get("status") == "linked":
                pid = str(row["prediction_row_id"])
                if pid in linked_by_prediction and linked_by_prediction[pid] != row:
                    raise ValueError(f"multiple terminal labels for prediction: {pid}")
                linked_by_prediction[pid] = row
        target_dates = {
            str(row["target_date"])
            for pid, row in self.predictions.rows.items()
            if pid not in linked_by_prediction
        }
        official_rows = read_source_events(
            self.official_path_root, target_dates=target_dates
        )
        for pid, prediction in self.predictions.rows.items():
            label = linked_by_prediction.get(pid)
            if label is None:
                label = label_prediction(prediction, official_rows)
                result["labels"] += int(self.labels.put(label))
                if label.get("status") == "linked":
                    linked_by_prediction[pid] = label
            identity = self.market_identities.rows.get(pid)
            if identity is None:
                continue
            settlement = settlement_for_identity(prediction, identity, self.settlement_db)
            result["settlements"] += int(self.settlements.put(settlement))
        ready_by_prediction = {
            str(row["prediction_row_id"]): str(row["entry_ready_at_utc"])
            for row in self.subscription_readiness.rows.values()
        }
        ready_predictions = [
            row
            for pid, row in self.predictions.rows.items()
            if pid in ready_by_prediction and pid in self.market_identities.rows
        ]
        identities = {
            str(row["prediction_row_id"]): [
                self.market_identities.rows[str(row["prediction_row_id"])]
            ]
            for row in ready_predictions
        }
        replay_rows, replay_summary = replay_markout_rows(
            ready_predictions,
            identities,
            linked_by_prediction,
            ws_runtime_root=self.ws_runtime_root,
            now_utc=now_utc,
            existing_rows=self.markouts.rows.values(),
            entry_latency_seconds=ENTRY_LATENCY_SECONDS,
            materializer_version=MATERIALIZER_VERSION,
            entry_ready_by_prediction=ready_by_prediction,
        )
        for row in replay_rows:
            result["markouts"] += int(self.markouts.put(row))
        _write_json_atomic(self.root / "markout_materializer_latest.json", replay_summary)
        return result

    def materialize_standard_lineage(self) -> dict[str, int]:
        """Bridge selected executable actions into the shared zero-notional facts."""
        result = {"signal_candidates": 0, "trade_intents": 0}
        entry_by_prediction: dict[str, Mapping[str, Any]] = {}
        for row in self.markouts.rows.values():
            if (
                row.get("materializer_version") != MATERIALIZER_VERSION
                or row.get("checkpoint") != "entry_after_p95_latency"
                or row.get("primary_status") != "VALID_TWO_SIDED_DEPTH"
            ):
                continue
            sweep = (row.get("sweeps") or {}).get("buy_5")
            if not sweep or not sweep.get("fully_executable"):
                continue
            pid = str(row["prediction_row_id"])
            current = entry_by_prediction.get(pid)
            if current is None or str(row.get("book_snapshot_id") or "") < str(
                current.get("book_snapshot_id") or ""
            ):
                entry_by_prediction[pid] = row
        for action in self.actions.rows.values():
            pid = str(action["prediction_row_id"])
            entry = entry_by_prediction.get(pid)
            if entry is None:
                continue
            sweep = entry["sweeps"]["buy_5"]
            average_price = float(sweep["average_price"])
            if average_price > MAX_TAKER_VWAP:
                continue
            identity = action["market_identity"]
            city = str(action["city"])
            target_id = _hash(
                {
                    "condition_id": identity["condition_id"],
                    "token_id": identity["token_id"],
                    "side": "NO",
                    "bracket": identity["native_bracket"],
                }
            )
            candidate = SignalCandidate.create(
                checkpoint_id=pid,
                trigger_event_id=str(action["trigger_event_id"]),
                city=city,
                target_date=str(action["target_date"]),
                decision_ts_utc=str(action["action_ready_at_utc"]),
                target_id=target_id,
                target_kind="market_expression",
                expression_id=target_id,
                condition_id=str(identity["condition_id"]),
                market_id=str(identity["market_id"]),
                token_id=str(identity["token_id"]),
                bracket=str(identity["native_bracket"]),
                side="NO",
                p_model=None,
                market_p=float(entry["best_ask"]),
                executable_cost=average_price,
                model_id=str(action["model_id"]),
                model_artifact_id=str(self.models.config["artifact_sha256"][city]),
                feature_set_id=NextPrintFeatureBuilderV1.version,
                feature_book_snapshot_id=None,
                execution_book_snapshot_id=str(entry["book_snapshot_id"]),
                strategy_key=STRATEGY_KEY,
                policy_id="wcir_ht_prevmax_no_diagnostic_v2",
                candidate_status="scored",
                blocker_reason=None,
                selected=True,
                market_evidence_status="complete_two_sided_exact_token_ws",
                input_refs=(
                    {
                        "role": "exact_token_execution_book",
                        "snapshot_id": str(entry["book_snapshot_id"]),
                        "raw_lineage_id": str(entry.get("raw_lineage_id") or ""),
                    },
                ),
                metadata={
                    "p_new_running_max": float(action["p_new_running_max"]),
                    "is_exact_token_fair_probability": False,
                    "selector_threshold": ACTION_THRESHOLD,
                    "counterfactual_shares": SHARES,
                    "entry_taker_fee_usd": float(sweep["taker_fee_usd"]),
                    "zero_notional": True,
                    "no_order_placed": True,
                },
            )
            result["signal_candidates"] += int(
                self.signal_candidates.put(candidate.to_dict())
            )
            intent = TradeIntent.create(
                candidate_id=candidate.candidate_id,
                condition_id=str(identity["condition_id"]),
                token_id=str(identity["token_id"]),
                side="BUY",
                requested_size=0.0,
                sizing_profile="zero_notional_v1",
                execution_profile="exact_no_five_share_taker_diagnostic_v1",
                max_cost=MAX_TAKER_VWAP,
                ttl_seconds=120,
                dedupe_key=f"{STRATEGY_KEY}|{self.forward_epoch}|{candidate.candidate_id}",
                exposure_bucket=f"{city}|{action['target_date']}",
                mode="zero_notional",
                metadata={
                    "record_only": True,
                    "no_plan": True,
                    "no_order": True,
                    "counterfactual_shares": SHARES,
                },
            )
            result["trade_intents"] += int(self.trade_intents.put(intent.to_dict()))
        return result

    def _economics(self) -> list[dict[str, Any]]:
        linked_settlements: dict[tuple[str, str], Mapping[str, Any]] = {}
        for row in self.settlements.rows.values():
            if row.get("status") == "linked":
                linked_settlements[
                    (str(row["prediction_row_id"]), str(row.get("token_id") or ""))
                ] = row
        readiness = {
            str(row["prediction_row_id"]): row
            for row in self.subscription_readiness.rows.values()
        }
        linked_labels = {
            str(row["prediction_row_id"]): row
            for row in self.labels.rows.values()
            if row.get("status") == "linked"
        }
        entries: dict[tuple[str, str], Mapping[str, Any]] = {}
        for row in self.markouts.rows.values():
            if (
                row.get("materializer_version") != MATERIALIZER_VERSION
                or row.get("checkpoint") != "entry_after_p95_latency"
            ):
                continue
            key = (str(row["prediction_row_id"]), str(row.get("token_id") or ""))
            current = entries.get(key)
            rank = (
                row.get("primary_status") == "VALID_TWO_SIDED_DEPTH",
                str(row.get("book_snapshot_id") or ""),
            )
            current_rank = (
                (-1, "")
                if current is None
                else (
                    current.get("primary_status") == "VALID_TWO_SIDED_DEPTH",
                    str(current.get("book_snapshot_id") or ""),
                )
            )
            if rank > current_rank:
                entries[key] = row
        output: list[dict[str, Any]] = []
        for action in self.actions.rows.values():
            pid = str(action["prediction_row_id"])
            token = str(action["market_identity"]["token_id"])
            entry = entries.get((pid, token))
            settlement = linked_settlements.get((pid, token))
            ready = readiness.get(pid)
            body: dict[str, Any] = {
                "action_id": action["action_id"],
                "prediction_row_id": pid,
                "trigger_event_id": action["trigger_event_id"],
                "polymarket_event_id": action["polymarket_event_id"],
                "city": action["city"],
                "target_date": action["target_date"],
                "model": action["model"],
                "condition_id": action["market_identity"]["condition_id"],
                "token_id": token,
                "shares": SHARES,
                "orders": 0,
                "fills": 0,
                "actual_notional_usd": 0.0,
            }
            label = linked_labels.get(pid)
            if label is not None and _parse(
                action["action_ready_at_utc"], field="action_ready_at_utc"
            ) >= _parse(
                label["official_first_seen_at_utc"],
                field="official_first_seen_at_utc",
            ):
                output.append(
                    {
                        **body,
                        "status": "DATA_FAIL_CLOSED",
                        "reason": "ACTION_READY_NOT_BEFORE_NEXT_OFFICIAL_PRINT",
                    }
                )
                continue
            if ready is None:
                output.append(
                    {
                        **body,
                        "status": "DATA_FAIL_CLOSED",
                        "reason": "CAPTURE_SUBSCRIPTION_NOT_READY",
                    }
                )
                continue
            body["entry_ready_at_utc"] = ready["entry_ready_at_utc"]
            body["capture_subscription_ready_at_utc"] = ready[
                "capture_subscription_ready_at_utc"
            ]
            if entry is None or entry.get("primary_status") != "VALID_TWO_SIDED_DEPTH":
                output.append(
                    {
                        **body,
                        "status": "DATA_FAIL_CLOSED",
                        "reason": "EXECUTABLE_ENTRY_BOOK_MISSING",
                    }
                )
                continue
            sweep = (entry.get("sweeps") or {}).get("buy_5")
            if not sweep or not sweep.get("fully_executable"):
                output.append(
                    {
                        **body,
                        "status": "DATA_FAIL_CLOSED",
                        "reason": "FIVE_SHARE_ASK_SWEEP_NOT_EXECUTABLE",
                    }
                )
                continue
            average = float(sweep["average_price"])
            if average > MAX_TAKER_VWAP:
                output.append(
                    {
                        **body,
                        "status": "POLICY_ABSTAIN",
                        "reason": "VWAP_ABOVE_0P92",
                        "entry_average_price": average,
                    }
                )
                continue
            if settlement is None:
                output.append(
                    {
                        **body,
                        "status": "DATA_FAIL_CLOSED",
                        "reason": "EXACT_SETTLEMENT_PENDING",
                        "entry_average_price": average,
                    }
                )
                continue
            final_price = float(settlement["settlement"]["final_price"])
            cost = float(sweep["effective_value_usd"])
            payout = SHARES * final_price
            pnl = payout - cost
            output.append(
                {
                    **body,
                    "status": "EXECUTABLE_SETTLED",
                    "reason": None,
                    "entry_checkpoint_at_utc": entry.get("checkpoint_at_utc"),
                    "entry_book_snapshot_id": entry.get("book_snapshot_id"),
                    "entry_average_price": average,
                    "entry_gross_cost_usd": float(sweep["gross_value_usd"]),
                    "entry_taker_fee_usd": float(sweep["taker_fee_usd"]),
                    "entry_effective_cost_usd": cost,
                    "settlement_final_price": final_price,
                    "counterfactual_payout_usd": payout,
                    "net_pnl_usd": pnl,
                    "roi": pnl / cost,
                    "win": pnl > 0,
                }
            )
        return output

    @staticmethod
    def _last_complete_json_row(path: Path) -> dict[str, Any] | None:
        if not path.is_file() or not path.stat().st_size:
            return None
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 1024 * 1024))
            lines = handle.readlines()
        for raw in reversed(lines):
            if raw.endswith(b"\n") and raw.strip():
                row = json.loads(raw)
                return row if isinstance(row, dict) else None
        return None

    def _input_health(self, *, now_utc: datetime) -> dict[str, Any]:
        issues: list[str] = []
        cursor = {}
        cursor_path = self.root / "input_cursor.json"
        if cursor_path.is_file():
            cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
        event: dict[str, Any] = {
            "path": str(self.event_path),
            "exists": self.event_path.is_file(),
        }
        if self.event_path.is_file():
            stat = self.event_path.stat()
            last_row = self._last_complete_json_row(self.event_path)
            event.update(
                inode=stat.st_ino,
                device=stat.st_dev,
                size_bytes=stat.st_size,
                mtime_utc=self._utc_text(datetime.fromtimestamp(stat.st_mtime, UTC)),
                append_age_seconds=max(0.0, now_utc.timestamp() - stat.st_mtime),
                latest_complete_row_schema=(
                    None if last_row is None else last_row.get("schema_version")
                ),
                cursor_offset=int(cursor.get("offset") or 0),
                cursor_lag_bytes=max(0, stat.st_size - int(cursor.get("offset") or 0)),
                cursor_inode_matches=(
                    cursor.get("inode") == stat.st_ino
                    and cursor.get("device") == stat.st_dev
                ),
            )
            if last_row is not None and last_row.get("schema_version") != "fast_source_prev_no_trial_v2":
                issues.append("EVENT_JOURNAL_SCHEMA_MISMATCH")
        else:
            issues.append("EVENT_JOURNAL_MISSING")
        producer: dict[str, Any] = {
            "path": None if self.producer_health_path is None else str(self.producer_health_path),
            "exists": bool(self.producer_health_path and self.producer_health_path.is_file()),
        }
        if self.producer_health_path is None or not self.producer_health_path.is_file():
            issues.append("PRODUCER_HEALTH_MISSING")
        else:
            try:
                payload = json.loads(self.producer_health_path.read_text(encoding="utf-8"))
                generated = _parse(payload.get("generated_at_utc"), field="producer_generated_at")
                age = max(0.0, (now_utc - generated).total_seconds())
                producer.update(
                    status=payload.get("status"),
                    schema_version=payload.get("schema_version"),
                    strategy_id=payload.get("strategy_id"),
                    generated_at_utc=self._utc_text(generated),
                    age_seconds=age,
                    inode=self.producer_health_path.stat().st_ino,
                    mtime_utc=self._utc_text(
                        datetime.fromtimestamp(
                            self.producer_health_path.stat().st_mtime, UTC
                        )
                    ),
                )
                if payload.get("status") != "ok":
                    issues.append("PRODUCER_STATUS_NOT_OK")
                if payload.get("schema_version") != "fast_source_prev_no_trial_latest_v2":
                    issues.append("PRODUCER_HEALTH_SCHEMA_MISMATCH")
                if age > PRODUCER_HEALTH_MAX_AGE_SECONDS:
                    issues.append("PRODUCER_HEALTH_STALE")
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                producer["error"] = f"{type(exc).__name__}:{exc}"
                issues.append("PRODUCER_HEALTH_INVALID")
        return {
            "status": "ok" if not issues else "warming",
            "issues": sorted(set(issues)),
            "max_producer_age_seconds": PRODUCER_HEALTH_MAX_AGE_SECONDS,
            "event_journal": event,
            "producer_health": producer,
        }

    def health(self, *, now_utc: datetime | None = None) -> dict[str, Any]:
        now = self._as_utc(now_utc)
        predictions = list(self.predictions.rows.values())
        actions = list(self.actions.rows.values())
        identities = list(self.market_identities.rows.values())
        linked_labels = {
            str(row["prediction_row_id"]): row
            for row in self.labels.rows.values()
            if row.get("status") == "linked"
        }
        economics = self._economics()
        _write_json_atomic(
            self.root / "counterfactual_economics_snapshot.json", economics, pretty=False
        )
        raw_events = list(self.raw_events.rows.values())
        latest_attempts = list(self._latest_attempts().values())
        decision_rows = list(self.decisions.rows.values())
        readiness_rows = list(self.subscription_readiness.rows.values())
        identity_snapshot_path = self.root / "market_identity_resolution_latest.json"
        identity_snapshot = (
            json.loads(identity_snapshot_path.read_text(encoding="utf-8"))
            if identity_snapshot_path.is_file()
            else {"unresolved_rows": []}
        )
        city_metrics: dict[str, Any] = {}
        for city in CITY:
            city_raw = [row for row in raw_events if row["city"] == city]
            city_attempts = [row for row in latest_attempts if row["city"] == city]
            city_predictions = [row for row in predictions if row["city"] == city]
            city_actions = [row for row in actions if row["city"] == city]
            city_decisions = [row for row in decision_rows if row["city"] == city]
            city_economics = [row for row in economics if row["city"] == city]
            city_identities = [row for row in identities if self.predictions.rows[row["prediction_row_id"]]["city"] == city]
            city_readiness = [
                row
                for row in readiness_rows
                if self.predictions.rows[row["prediction_row_id"]]["city"] == city
            ]
            score = _score_metrics(city_predictions, linked_labels, city_actions)
            reason_histogram = Counter()
            for row in city_attempts:
                if row.get("classification") != "ELIGIBLE":
                    for reason in row.get("reasons") or [row.get("classification")]:
                        reason_histogram[f"{row.get('classification')}:{reason}"] += 1
            for row in identity_snapshot.get("unresolved_rows") or ():
                if row.get("city") == city:
                    reason_histogram[f"DATA_FAIL_CLOSED:EXACT_IDENTITY_UNRESOLVED:{row.get('reason')}"] += 1
            for row in city_decisions:
                if row.get("classification") != "SELECTED_DIAGNOSTIC":
                    reason_histogram[f"{row.get('classification')}:{row.get('reason')}"] += 1
            for row in city_economics:
                if row.get("status") != "EXECUTABLE_SETTLED":
                    reason_histogram[f"{row.get('status')}:{row.get('reason')}"] += 1
            city_metrics[city] = {
                "raw_opportunity_events": len(city_raw),
                "opportunity_attempts": sum(
                    row["city"] == city for row in self.opportunities.rows.values()
                ),
                "feature_retry_pending_events": sum(
                    row.get("terminal") is False for row in city_attempts
                ),
                "feature_eligible_events": len(city_predictions),
                "exact_identity_resolved_events": len(city_identities),
                "exact_identity_coverage": _safe_rate(
                    len(city_identities), len(city_predictions)
                ),
                "selector_triggers": {
                    model: sum(
                        row["model"] == model and row["selector_pass"]
                        for row in city_decisions
                    )
                    for model in MODEL_IDS
                },
                "unique_expression_signals": {
                    model: sum(row["model"] == model for row in city_actions)
                    for model in MODEL_IDS
                },
                "capture_demands": sum(
                    row.get("metadata", {}).get("city") == city
                    for row in self.demands.rows.values()
                ),
                "capture_subscription_ready": len(city_readiness),
                "label_linked_events": sum(
                    pid in linked_labels
                    for pid in (
                        str(row["prediction_row_id"]) for row in city_predictions
                    )
                ),
                "model_quality": score["models"],
                "challenger_rps_delta": score["challenger_rps_delta"],
                "economics": {
                    model: _date_block_roi(
                        [row for row in city_economics if row["model"] == model]
                    )
                    for model in MODEL_IDS
                },
                "counterfactual_status_histogram": dict(
                    sorted(Counter(str(row["status"]) for row in city_economics).items())
                ),
                "reason_histogram": dict(sorted(reason_histogram.items())),
            }
        overall_economics = {
            model: _date_block_roi([row for row in economics if row["model"] == model])
            for model in MODEL_IDS
        }
        input_health = self._input_health(now_utc=now)
        return {
            "status": input_health["status"],
            "strategy_key": STRATEGY_KEY,
            "execution_mode": EXECUTION_MODE,
            "policy_status": "zero_notional_action_shadow",
            "action_shadow_enabled": True,
            "live_authority": False,
            "forward_epoch_id": self.forward_epoch,
            "forward_start_utc": self._utc_text(self.forward_start),
            "generated_at_utc": self._utc_text(now),
            "frozen_hashes": self.models.hashes,
            "input_health": input_health,
            "trigger_definition": "unique fast_source_prev_no_trial_v2 event_key",
            "action_definition": "first selector-pass decision per model and exact condition_id after canonical market-book identity resolution; Gamma is fallback only",
            "economic_definition": "5-share exact NO token, first strict WS ask after max(decision+5s, model score, identity, subscription epoch), official taker fee, exact token settlement",
            "proper_score_weighting": "target_date equal, then official_print_group equal, then row equal",
            "cities": city_metrics,
            "overall_economics": overall_economics,
            "raw_opportunity_events": len(raw_events),
            "feature_eligible_events": len(predictions),
            "capture_demand_count": len(self.demands.rows),
            "capture_subscription_ready_count": len(readiness_rows),
            "exact_identity_prediction_count": len(identities),
            "exact_identity_action_count": len(actions),
            "market_metadata_payload_count": len(self.market_payloads.rows),
            "safety": {
                "signal_candidates": len(self.signal_candidates.rows),
                "trade_intents": len(self.trade_intents.rows),
                "plans": 0,
                "orders": 0,
                "fills": 0,
                "exchange_calls": 0,
                "actual_notional_usd": 0.0,
            },
            "orders": 0,
            "fills": 0,
            "notional": 0,
            "actual_notional": 0.0,
        }

    def run_once(self, *, now_utc: datetime | None = None) -> dict[str, Any]:
        now = self._as_utc(now_utc)
        ingested = self.ingest(now_utc=now)
        identities = self.materialize_market_identities(now_utc=now)
        before_policy = self.materialize(now_utc=now)
        policy = self.materialize_policy()
        subscriptions = self.materialize_subscription_readiness(now_utc=now)
        after_policy = self.materialize(now_utc=now)
        lineage = self.materialize_standard_lineage()
        materialized = {
            key: int(before_policy.get(key, 0)) + int(after_policy.get(key, 0))
            for key in set(before_policy).union(after_policy)
        }
        health = self.health(now_utc=now)
        _write_json_atomic(self.root / "latest.json", health)
        return {
            "ingested": ingested,
            "identities": identities,
            "policy": policy,
            "subscriptions": subscriptions,
            "materialized": materialized,
            "standard_lineage": lineage,
            "health": health,
        }
