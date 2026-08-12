from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd

from weather_data_feed.input_catalog import JsonlInputCatalog
from weather_data_feed.helsinki_remaining_heat_features import (
    build_fmi_remaining_heat_features,
    build_forecast_remaining_heat_features,
)

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


def _load_artifact(spec: dict[str, Any]) -> dict[str, Any]:
    path = _verify_artifact(spec)
    if path.suffix.lower() == ".json":
        payload = _read_json(path)
    else:
        payload = joblib.load(path)
    if not isinstance(payload, dict):
        raise RuntimeError(f"unsupported Helsinki artifact payload: {path}")
    return payload


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, value))))


def _offset_probability(artifact: dict[str, Any], features: dict[str, Any], market_p: float) -> float:
    if artifact.get("kind") == "fitted_weather_reliability":
        scale = float(artifact["gap_scale"])
        reliability = float(artifact["weather_reliability"])
        weather_p = float(features["weather_probability"])
        market_logit = math.log(
            np.clip(market_p, 1e-6, 1 - 1e-6)
            / np.clip(1 - market_p, 1e-6, 1)
        )
        weather_logit = math.log(
            np.clip(weather_p, 1e-6, 1 - 1e-6)
            / np.clip(1 - weather_p, 1e-6, 1)
        )
        correction = reliability * scale * math.tanh(
            (weather_logit - market_logit) / scale
        )
        return _sigmoid(market_logit + correction)
    if artifact.get("kind") == "bounded_weather_market_residual":
        cap = float(artifact["logit_cap"])
        weather_p = float(features["weather_probability"])
        market_logit = math.log(
            np.clip(market_p, 1e-6, 1 - 1e-6)
            / np.clip(1 - market_p, 1e-6, 1)
        )
        weather_logit = math.log(
            np.clip(weather_p, 1e-6, 1 - 1e-6)
            / np.clip(1 - weather_p, 1e-6, 1)
        )
        correction = cap * math.tanh((weather_logit - market_logit) / cap)
        return _sigmoid(market_logit + correction)
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
    as_of = available_through or datetime.now(tz=UTC)
    paths = [path] if path.is_file() else JsonlInputCatalog().day_shard_paths(
        path,
        filename="high_frequency_observations.jsonl",
        as_of=as_of,
    )
    for catalog_row in (
        row
        for history_path in reversed(paths)
        for row in JsonlInputCatalog.iter_path_reverse(history_path)
    ):
        row = catalog_row.row
        if row.get("city") != "Helsinki" or row.get("source") != "fmi":
            continue
        row_target = row.get("target_date")
        if row_target != target_date:
            continue
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


def _quote(
    profile: dict[str, Any],
    target_date: str,
    bracket: int,
    as_of: datetime | None = None,
    source_obs_ts_utc: str | None = None,
) -> dict[str, Any]:
    catalog = JsonlInputCatalog()
    selected = catalog.latest_from_discovered_journals(
        Path(profile["book_dir"]),
        pattern="*.jsonl",
        as_of=as_of or datetime.now(tz=ZoneInfo("UTC")),
        # `ts_utc` is written only after the HTTP response has been received and
        # the active-ladder row has been assembled.  The historical
        # `book_fetched_at_utc` field was request-start time and is not a valid
        # PIT availability clock.
        available_field="ts_utc",
        predicate=lambda x: (
            x.get("target_date") == target_date
            and str(x.get("bracket")) == str(bracket)
            and x.get("outcome") == "no"
            and x.get("book_status") == "ok"
            and (
                source_obs_ts_utc is None
                or str(x.get("source_obs_ts_utc")) == source_obs_ts_utc
            )
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
    market_unit = str(row.get("market_unit") or "").upper()
    if market_unit and market_unit != "C":
        raise RuntimeError(
            f"Helsinki market lattice must be Celsius, got {market_unit}"
        )
    snapshot_payload = {
        "condition_id": row.get("condition_id"),
        "token_id": row.get("token_id"),
        "yes_token_id": row.get("yes_token_id"),
        "no_token_id": row.get("no_token_id") or row.get("token_id"),
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
        "book_available_at_utc": row.get("ts_utc"),
        "quote_row_ts_utc": row.get("ts_utc"),
        "yes_token_id": row.get("yes_token_id"),
        "no_token_id": row.get("no_token_id") or row.get("token_id"),
    }


def _yes_quote_from_no_quote(quote: dict[str, Any]) -> dict[str, Any]:
    """Return the executable complementary YES view of one binary CLOB book."""

    raw = quote.get("raw") or {}
    yes_asks = [
        {"price": 1.0 - float(row["price"]), "size": float(row["size"])}
        for row in raw.get("bids") or []
    ]
    yes_bids = [
        {"price": 1.0 - float(row["price"]), "size": float(row["size"])}
        for row in raw.get("asks") or []
    ]
    yes_asks.sort(key=lambda row: row["price"])
    yes_bids.sort(key=lambda row: row["price"], reverse=True)
    best_ask = yes_asks[0]["price"] if yes_asks else None
    best_bid = yes_bids[0]["price"] if yes_bids else None
    yes_token_id = str(quote.get("yes_token_id") or "") or None
    parent_snapshot_id = quote.get("book_snapshot_id")
    yes_snapshot_id = hashlib.sha256(
        json.dumps(
            {
                "parent_book_snapshot_id": parent_snapshot_id,
                "condition_id": quote.get("condition_id"),
                "token_id": yes_token_id,
                "outcome": "yes",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        **quote,
        "outcome": "yes",
        "token_id": yes_token_id,
        "best_ask": best_ask,
        "best_bid": best_bid,
        "raw": {**raw, "asks": yes_asks, "bids": yes_bids},
        "book_snapshot_id": yes_snapshot_id,
        "complement_parent_book_snapshot_id": parent_snapshot_id,
        "execution_status": (
            "executable_ask" if best_ask is not None else "not_executable_yes_ask"
        ),
        "market_probability_lower": 1.0
        - float(quote.get("market_probability_upper", quote.get("best_ask", 1.0))),
        "market_probability_upper": 1.0
        - float(quote.get("market_probability_lower", quote.get("best_bid", 0.0))),
        "quote_state": (
            "two_sided"
            if best_ask is not None and best_bid is not None
            else "one_sided_ask_only"
            if best_ask is not None
            else "one_sided_bid_only"
            if best_bid is not None
            else "empty"
        ),
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
        artifacts = {
            key: _load_artifact(value)
            for key, value in profile["artifacts"].items()
        }
        target_date = now.astimezone(ZoneInfo("Europe/Helsinki")).date().isoformat()
        source_history_now = _fmi_history(
            Path(profile["source_journal"]), target_date, now
        )
        if len(source_history_now) < 4:
            raise InputNotReady(
                "insufficient_pit_source_history",
                city="Helsinki",
                target_date=target_date,
                decision_ts_utc=now.isoformat(),
                details={
                    "source": "fmi",
                    "available_unique_observations": len(source_history_now),
                    "required_unique_observations": 4,
                    "source_journal": profile["source_journal"],
                },
            )
        trigger_source = source_history_now[-1]
        source_obs_ts = str(trigger_source["observation_time_utc"])
        official_now = _official_helsinki_as_of(
            Path(profile["observation_journal_dir"]), target_date, now
        )
        if official_now is None:
            raise InputNotReady(
                "awaiting_official_observation",
                city="Helsinki",
                target_date=target_date,
                decision_ts_utc=now.isoformat(),
                details={
                    "observation_journal_dir": profile["observation_journal_dir"]
                },
            )
        # FMI first-seen decides *when* to recompute.  The exact-bracket
        # expression remains anchored to the PIT official running maximum.
        current_x = int(round(float(official_now["running_max_c"])))
        quote = _quote(
            profile,
            target_date,
            current_x,
            now,
            source_obs_ts_utc=source_obs_ts,
        )
        decision = pd.Timestamp(quote["book_available_at_utc"]).to_pydatetime()
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
                "anchor_clock_mismatch",
                city="Helsinki",
                target_date=target_date,
                decision_ts_utc=decision.isoformat(),
                details={
                    "book_expression_anchor": current_x,
                    "official_anchor_at_book_availability": official_bracket,
                    "book_input_ref": quote.get("_input_ref"),
                    "official_input_ref": official.get("_input_ref"),
                },
            )
        history = [
            row
            for row in _fmi_history(
                Path(profile["source_journal"]), target_date, decision
            )
            if str(row["observation_time_utc"]) <= source_obs_ts
        ]
        if len(history) < 4:
            raise InputNotReady(
                "insufficient_pit_source_history",
                city="Helsinki",
                target_date=target_date,
                decision_ts_utc=decision.isoformat(),
                details={
                    "source": "fmi",
                    "source_obs_ts_utc": source_obs_ts,
                    "available_unique_observations": len(history),
                    "required_unique_observations": 4,
                    "source_journal": profile["source_journal"],
                },
            )
        source = history[-1]
        if str(source["observation_time_utc"]) != source_obs_ts:
            raise InputNotReady(
                "source_book_clock_mismatch",
                city="Helsinki",
                target_date=target_date,
                decision_ts_utc=decision.isoformat(),
                details={
                    "book_source_obs_ts_utc": source_obs_ts,
                    "latest_pit_source_obs_ts_utc": source.get("observation_time_utc"),
                    "book_input_ref": quote.get("_input_ref"),
                },
            )
        source_first_seen = pd.Timestamp(
            source["source_first_seen_at_utc"]
        ).to_pydatetime()
        source_to_book_lag = (decision - source_first_seen).total_seconds()
        max_source_to_book_lag = float(
            profile.get("max_source_to_book_lag_seconds", 120.0)
        )
        if source_to_book_lag < 0 or source_to_book_lag > max_source_to_book_lag:
            raise InputNotReady(
                "source_book_clock_gap",
                city="Helsinki",
                target_date=target_date,
                decision_ts_utc=decision.isoformat(),
                details={
                    "source_first_seen_at_utc": source["source_first_seen_at_utc"],
                    "book_available_at_utc": quote["book_available_at_utc"],
                    "source_to_book_lag_seconds": source_to_book_lag,
                    "max_source_to_book_lag_seconds": max_source_to_book_lag,
                },
            )
        market_p = (
            (quote["best_ask"] + quote["best_bid"]) / 2
            if quote["best_bid"] is not None and quote["best_ask"] is not None
            else None
        )
        temps = np.asarray([float(row["temp_c"]) for row in history])
        features: dict[str, Any] = build_fmi_remaining_heat_features(
            history,
            official_running_max_c=float(official["running_max_c"]),
        )
        forecast = _latest_forecast(Path(profile["forecast_curve_dir"]), target_date, decision)
        features.update(
            build_forecast_remaining_heat_features(
                forecast,
                decision=decision,
                official_running_max_c=float(official["running_max_c"]),
                current_temp_c=float(temps[-1]),
            )
        )
        features["path_state"] = _path_state(features)
        weather_p = _hgb_probability(artifacts["weather"], features)
        features["weather_probability"] = weather_p
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
            artifact_features = artifact.get("features") or []
            weather_features = artifacts["weather"].get("features") or []
            declared_features = list(dict.fromkeys([*weather_features, *artifact_features]))
            missing = [
                name
                for name in declared_features
                if features.get(name) is None
                or not np.isfinite(features.get(name, np.nan))
            ]
            probability = _offset_probability(artifact, features, market_p) if market_p is not None else None
            expression_sides = artifact.get("expression_sides") or ["NO"]
            for market_side in expression_sides:
                side_quote = (
                    quote
                    if market_side == "NO"
                    else _yes_quote_from_no_quote(quote)
                )
                if market_side == "YES" and not side_quote.get("token_id"):
                    raise InputNotReady(
                        "missing_yes_token_identity",
                        city="Helsinki",
                        target_date=target_date,
                        decision_ts_utc=decision.isoformat(),
                        details={
                            "condition_id": side_quote.get("condition_id"),
                            "book_snapshot_id": side_quote.get("book_snapshot_id"),
                            "book_input_ref": quote.get("_input_ref"),
                        },
                    )
                side_market_p = (
                    market_p
                    if market_side == "NO" or market_p is None
                    else 1 - market_p
                )
                side_probability = (
                    probability
                    if market_side == "NO" or probability is None
                    else 1 - probability
                )
                outputs.append(
                    CityScore(
                        city="Helsinki",
                        target_date=target_date,
                        decision_ts_utc=decision.isoformat(),
                        source_obs_ts_utc=source_obs_ts,
                        current_bracket=current_x,
                        market_side=market_side,
                        market_probability=side_market_p,
                        market_entry_price=side_quote["best_ask"],
                        model_probability=side_probability,
                        model_id=str(
                            artifact.get("model_id")
                            or artifact.get("candidate_name")
                        ),
                        feature_coverage=(
                            1 - len(missing) / len(declared_features)
                            if declared_features
                            else 1.0
                        ),
                        missing_features=missing,
                        features={
                            name: features.get(name) for name in declared_features
                        },
                        market=side_quote,
                        lineage={"source":"fmi","source_first_seen_at_utc":source["source_first_seen_at_utc"],
                         "source_payload_hash":source.get("payload_hash"),
                         "source_raw_payload_hash":source.get("raw_payload_hash"),
                         "book_request_started_at_utc":quote.get("request_started_at_utc"),
                         "book_fetched_at_utc":quote.get("book_fetched_at_utc"),
                         "book_available_at_utc":quote["book_available_at_utc"],
                         "source_to_book_lag_seconds":source_to_book_lag,
                         "book_snapshot_id":side_quote["book_snapshot_id"],
                         "feature_book_snapshot_id":quote["book_snapshot_id"],
                         "execution_book_snapshot_id":side_quote["book_snapshot_id"],
                         "book_input_ref":quote.get("_input_ref"),
                         "official_source":official["source"],"official_last_obs_utc":official["last_obs_utc"],
                         "official_input_ref":official.get("_input_ref"),
                         "forecast_available_at_utc":forecast.get("available_at_utc"),
                         "forecast_values_hash":forecast.get("forecast_values_hash"),
                         "forecast_payload_hash":forecast.get("payload_hash"),
                         "forecast_input_ref":forecast.get("_input_ref"),
                         "model_artifact_sha256":profile["artifacts"][artifact_key]["sha256"],
                         "source_lattice_anchor":int(math.floor(temps[-1] + 0.5)),
                         "official_lattice_anchor":int(round(float(official["running_max_c"]))),
                         "market_expression_anchor":current_x,
                         "market_feature_role":"prior",
                         "market_feature_clock":"decision_current",
                         "trigger_role":"fmi_source_first_seen",
                         "profile_id":profile.get("profile_id", "helsinki_remaining_heat_v1"),
                         "weather_probability":weather_p,"path_state":features["path_state"]},
                        evaluation_status=(
                            "scored" if market_p is not None else "not_scorable"
                        ),
                        not_scorable_reason=(
                            None
                            if market_p is not None
                            else "one_sided_market_probability_interval"
                        ),
                    )
                )
        return outputs
