#!/usr/bin/env python3
"""Zero-notional current-YES heat-death forward collector.

This is the forward-practice path for the thesis that the remaining heating
runway is exhausted.  It records the full same-day denominator, canonical
``weather_state_v2`` physical features, and direct CLOB quotes for the two
relevant expressions:

* current bracket BUY_YES
* next bracket BUY_NO

The physical profile is diagnostic, not a calibrated probability or live
gate.  This runner never builds orders and never calls an executor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.observation_cache import parse_utc  # noqa: E402
from weather_feature_layer.builders import build_weather_state_frame_with_audits  # noqa: E402
from weather_feature_layer.contracts import (  # noqa: E402
    PIT_PROVENANCE_LIVE_CAPTURE,
    WEATHER_PHYSICAL_FEATURE_FIELDS,
)
from weather_feature_layer.market import parse_bracket, settlement_interval  # noqa: E402
from weather_feature_layer.store import write_feature_frame_store  # noqa: E402
from scripts.ops.weather_market_proxy import market_httpx_client  # noqa: E402


STRATEGY_ID = "current_yes_heat_death_physical_v1"
STRATEGY_INSTANCE = "current_yes_heat_death_shadow_v1"
DECISION_MODE = "compare_current_yes_vs_d1_no_after_physical_confirmation"
BUILDER_VERSION = "current_yes_heat_death_shadow_v1"
FEE_RATE = 0.05
RESEARCH_WINDOW_START_HOUR_LOCAL = 13.0
RESEARCH_WINDOW_END_HOUR_LOCAL = 17.0
CLOB_BOOK_API = "https://clob.polymarket.com/book"

RUNTIME_ROOT = Path(os.environ.get("WEATHER_DATA_FEED_RUNTIME_ROOT", "/Volumes/jrs/weather_data_feed_service_runtime"))
SNAPSHOT_DIR_DEFAULT = RUNTIME_ROOT / "targeted_output" / "paper_snapshots"
OBSERVATION_CACHE_DEFAULT = RUNTIME_ROOT / "output" / "observations" / "latest.json"
FORECAST_CURVE_DIR_DEFAULT = RUNTIME_ROOT / "targeted_output" / "forecast_hourly_curves"
OUTPUT_DIR_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / STRATEGY_INSTANCE
FEATURE_STORE_DEFAULT = ROOT / "runtime" / "weather_feature_store"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        if value != value:
            return None
    except Exception:
        pass
    if hasattr(value, "item"):
        try:
            return json_ready(value.item())
        except Exception:
            pass
    return value


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(json_ready(row), ensure_ascii=False, sort_keys=True) + "\n")


def finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def latest_snapshot(snapshot_dir: Path) -> Path | None:
    files = list(snapshot_dir.glob("snapshot_*.json"))
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def recent_curve_rows(curve_dir: Path, *, limit: int = 16) -> list[dict[str, Any]]:
    files = sorted(curve_dir.glob("**/*.jsonl"), key=lambda path: path.stat().st_mtime)[-limit:]
    rows: list[dict[str, Any]] = []
    for path in files:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
    return rows


def snapshot_decision_asof(snapshot: Mapping[str, Any]) -> str:
    return str(
        snapshot.get("available_at_utc")
        or snapshot.get("published_at_utc")
        or snapshot.get("ts_utc")
        or ""
    )


def pit_observation_cache(payload: Mapping[str, Any], as_of_utc: str) -> tuple[dict[str, Any], Counter[str]]:
    """Keep only cache rows whose fetch completion is provably PIT."""

    as_of = parse_utc(as_of_utc)
    counts: Counter[str] = Counter()
    kept: list[dict[str, Any]] = []
    for raw in payload.get("records") or []:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        fetched = parse_utc(row.get("fetched_at_utc"))
        report = parse_utc(row.get("last_obs_utc") or row.get("source_report_ts_utc"))
        if as_of is None or fetched is None:
            counts["missing_pit_fetch_clock"] += 1
            continue
        if fetched > as_of:
            counts["cache_fetched_after_snapshot"] += 1
            continue
        if report is not None and report > as_of:
            counts["report_after_snapshot"] += 1
            continue
        kept.append(row)
        counts["pit_rows"] += 1
    return {
        "schema_version": payload.get("schema_version"),
        "generated_at_utc": payload.get("generated_at_utc"),
        "decision_as_of_utc": as_of_utc,
        "records": kept,
    }, counts


def observation_evidence_asof(path: Path, as_of_utc: str) -> tuple[dict[str, Any], Counter[str]]:
    """Load immutable observation captures when available, with latest as fallback."""

    latest = read_json(path)
    as_of = parse_utc(as_of_utc)
    rows = [dict(row) for row in latest.get("records") or [] if isinstance(row, Mapping)]
    counts: Counter[str] = Counter()
    history_paths: list[Path] = []
    if as_of is not None:
        for day_offset in (0, -1):
            day = (as_of + timedelta(days=day_offset)).date().isoformat()
            history_paths.append(path.parent / day / "observations.jsonl")
    seen_paths: set[Path] = set()
    candidate_paths = [history_path for history_path in history_paths if history_path.exists()]
    if not candidate_paths:
        candidate_paths = [path.parent / "observations.jsonl"]
    for history_path in candidate_paths:
        if history_path in seen_paths or not history_path.exists():
            continue
        seen_paths.add(history_path)
        with history_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    counts["history_parse_error"] += 1
                    continue
                if isinstance(row, dict):
                    rows.append(row)
                    counts["immutable_history_rows_loaded"] += 1
    payload = {
        "schema_version": latest.get("schema_version"),
        "generated_at_utc": latest.get("generated_at_utc"),
        "records": rows,
    }
    filtered, pit_counts = pit_observation_cache(payload, as_of_utc)
    counts.update(pit_counts)
    if seen_paths:
        counts["immutable_history_files_loaded"] = len(seen_paths)
    return filtered, counts


def _interval(record: Mapping[str, Any]) -> tuple[float, float] | None:
    bracket_text = str(record.get("bracket") or "")
    bracket = parse_bracket(bracket_text)
    if bracket is None or bracket.low is None:
        return None
    question = str(record.get("question") or "").lower()
    if "or below" in question or "or lower" in question or "or less" in question:
        return (-math.inf, bracket.low + 0.5)
    if "or above" in question or "or higher" in question or bracket.high is None:
        return (bracket.low - 0.5, math.inf)
    return settlement_interval(bracket_text)


def _contains(interval: tuple[float, float] | None, value: float) -> bool:
    return bool(interval is not None and interval[0] <= value < interval[1])


def _rung_sort(record: Mapping[str, Any]) -> tuple[float, float, str]:
    interval = _interval(record)
    if interval is None:
        return math.inf, math.inf, str(record.get("bracket") or "")
    return interval[0], interval[1], str(record.get("bracket") or "")


def _direct_quote(record: Mapping[str, Any] | None, side: str) -> dict[str, Any]:
    if not record:
        return {"ask": None, "ask_size": None, "bid": None, "bid_size": None, "book_status": "missing_rung"}
    prefix = side.lower()
    return {
        "ask": finite(record.get(f"{prefix}_best_ask")),
        "ask_size": finite(record.get(f"{prefix}_ask_size")),
        "bid": finite(record.get(f"{prefix}_best_bid")),
        "bid_size": finite(record.get(f"{prefix}_bid_size")),
        "book_status": str(record.get(f"{prefix}_book_status") or ""),
        "book_fetched_at_utc": record.get(f"{prefix}_book_fetched_at_utc"),
        "token_id": str(record.get(f"{prefix}_token_id") or ""),
    }


def _fee_per_share(price: float | None) -> float | None:
    return None if price is None else round(FEE_RATE * price * (1.0 - price), 5)


def _effective_cost(price: float | None) -> float | None:
    fee = _fee_per_share(price)
    return None if price is None or fee is None else round(price + fee, 6)


def _book_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    def levels(name: str, *, reverse: bool) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for raw in payload.get(name) or []:
            if not isinstance(raw, Mapping):
                continue
            price = finite(raw.get("price"))
            size = finite(raw.get("size"))
            if price is not None and size is not None:
                out.append((price, size))
        return sorted(out, key=lambda item: item[0], reverse=reverse)

    bids = levels("bids", reverse=True)
    asks = levels("asks", reverse=False)
    return {
        "bid": bids[0][0] if bids else None,
        "bid_size": bids[0][1] if bids else None,
        "ask": asks[0][0] if asks else None,
        "ask_size": asks[0][1] if asks else None,
    }


def _fetch_token_book(client: httpx.Client, token_id: str) -> dict[str, Any]:
    fetched_at = utc_now()
    if not token_id:
        return {"book_status": "missing_token", "book_fetched_at_utc": fetched_at}
    try:
        response = client.get(CLOB_BOOK_API, params={"token_id": token_id})
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise ValueError("book response is not an object")
        return {
            **_book_summary(payload),
            "tick_size": finite(payload.get("tick_size")),
            "book_status": "ok",
            "book_fetched_at_utc": fetched_at,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ask": None,
            "ask_size": None,
            "bid": None,
            "bid_size": None,
            "book_status": f"fetch_error:{type(exc).__name__}",
            "book_fetched_at_utc": fetched_at,
        }


def refresh_candidate_quotes(
    decisions: list[dict[str, Any]],
    *,
    proxy: str | None,
    timeout_sec: float,
    max_pairs: int,
) -> Counter[str]:
    """Refresh two read-only books for in-window physically confirmed rows."""

    counts: Counter[str] = Counter()
    candidates = [
        row
        for row in decisions
        if bool(row.get("physical_confirmation_base"))
        and not bool(row.get("direct_quote_pair_available"))
    ][: max(0, max_pairs)]
    if not candidates:
        return counts
    with market_httpx_client(proxy, timeout=timeout_sec) as client:
        for row in candidates:
            counts["pair_attempts"] += 1
            current = _fetch_token_book(client, str(row.get("current_yes_token_id") or ""))
            d1 = _fetch_token_book(client, str(row.get("d1_no_token_id") or ""))
            for prefix, quote in (("current_yes", current), ("d1_no", d1)):
                for field in ("ask", "ask_size", "bid", "bid_size", "book_status", "book_fetched_at_utc"):
                    row[f"{prefix}_{field}"] = quote.get(field)
                row[f"{prefix}_fee_per_share"] = _fee_per_share(quote.get("ask"))
                row[f"{prefix}_effective_cost"] = _effective_cost(quote.get("ask"))
            row["direct_quote_pair_available"] = current.get("ask") is not None and d1.get("ask") is not None
            row["direct_quote_refresh_attempted"] = True
            if row["direct_quote_pair_available"]:
                counts["pair_successes"] += 1
            else:
                counts["pair_failures"] += 1
    return counts


def _physical_profile(row: Mapping[str, Any]) -> dict[str, Any]:
    decline_c = None
    running_c = finite(row.get("running_max_c"))
    current_c = finite(row.get("current_temp_c"))
    if running_c is not None and current_c is not None:
        decline_c = running_c - current_c
    peak_delta = finite(row.get("forecast_peak_delta_hours_local"))
    minutes_since_max = next(
        (
            value
            for key in (
                "minutes_since_last_strict_new_high",
                "minutes_since_first_running_max",
                "minutes_since_running_max",
            )
            if (value := finite(row.get(key))) is not None
        ),
        None,
    )
    decision_hour = finite(row.get("decision_hour_local") or row.get("decision_hour_local_float"))
    warming_state = str(row.get("warming_state") or "")
    in_research_window = (
        decision_hour is not None
        and RESEARCH_WINDOW_START_HOUR_LOCAL <= decision_hour <= RESEARCH_WINDOW_END_HOUR_LOCAL
    )

    support: list[str] = []
    precip_state = str(row.get("precip_state") or "")
    if precip_state not in {"", "unknown", "none_observed"}:
        support.append("observed_precipitation")
    if str(row.get("cloud_warming_interaction")) == "cloud_limited_flat_or_cooling":
        support.append("cloud_limited_flat_or_cooling")
    if str(row.get("moisture_cloud_interaction")) in {"humid_cloud_suppression", "cloud_suppression"}:
        support.append("moisture_cloud_suppression")
    if str(row.get("marine_thermal_state")) == "onshore_marine_cooling_risk":
        support.append("onshore_marine_cooling")
    remaining_precip = finite(row.get("forecast_precip_probability_remaining_3h_max_pct"))
    if remaining_precip is not None and remaining_precip >= 50:
        support.append("forecast_precip_remaining_3h")
    remaining_cloud = finite(row.get("forecast_cloud_cover_remaining_3h_mean_pct"))
    if remaining_cloud is not None and remaining_cloud >= 70:
        support.append("forecast_cloud_remaining_3h")
    solar_delta = finite(row.get("solar_elevation_delta_2h_deg"))
    if solar_delta is not None and solar_delta < 0:
        support.append("solar_elevation_falling")

    counter: list[str] = []
    forecast_gap = finite(row.get("forecast_gap_to_running_native"))
    if forecast_gap is not None and forecast_gap > 1:
        counter.append("forecast_room_gt_1_native")
    if warming_state in {"warming", "fast_warming"}:
        counter.append("observed_path_still_warming")
    if peak_delta is not None and peak_delta < -0.25:
        counter.append("forecast_peak_still_ahead")

    fade = decline_c is not None and decline_c >= 0.5
    peak_passed = peak_delta is not None and peak_delta >= 0.25
    path_not_warming = warming_state in {"flat", "cooling"}
    mature_high = minutes_since_max is not None and minutes_since_max >= 60
    mechanism_confirmed = fade and peak_passed and path_not_warming and mature_high
    base_confirmed = in_research_window and mechanism_confirmed
    strong = base_confirmed and len(support) >= 2
    if strong:
        profile = "physical_confirmed_strong"
    elif base_confirmed:
        profile = "physical_confirmed_base"
    elif not in_research_window:
        profile = "outside_research_window"
    elif fade:
        profile = "fade_watch_unconfirmed"
    else:
        profile = "full_denominator"
    return {
        "decline_c": decline_c,
        "physical_confirmation_profile": profile,
        "decision_window_status": (
            "in_research_window_13_17_local" if in_research_window else "outside_research_window_13_17_local"
        ),
        "in_research_window": in_research_window,
        "mechanism_confirmation_without_time_window": mechanism_confirmed,
        "physical_confirmation_base": base_confirmed,
        "physical_confirmation_strong": strong,
        "physical_support_count": len(support),
        "physical_support_reasons": support,
        "physical_counterevidence_reasons": counter,
        "probability_status": "not_fitted_forward_collection",
    }


def build_decisions(
    snapshot: Mapping[str, Any],
    feature_rows: list[dict[str, Any]],
    feature_refs: list[dict[str, Any]],
    *,
    snapshot_file: str,
) -> list[dict[str, Any]]:
    records_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for raw in snapshot.get("records") or []:
        if not isinstance(raw, Mapping):
            continue
        record = dict(raw)
        city = str(record.get("city") or "")
        target_date = str(record.get("target_date") or record.get("event_date") or "")
        if not city or not target_date or str(record.get("city_local_date_at_snapshot") or "") != target_date:
            continue
        records_by_key.setdefault((city, target_date), []).append(record)

    refs_by_key = {
        (str(row.get("city") or ""), str(row.get("target_date") or "")): ref
        for row, ref in zip(feature_rows, feature_refs, strict=True)
    }
    decisions: list[dict[str, Any]] = []
    for state in feature_rows:
        city = str(state.get("city") or "")
        target_date = str(state.get("target_date") or "")
        running = finite(state.get("running_native"))
        ladder = sorted(records_by_key.get((city, target_date), []), key=_rung_sort)
        if running is None or not ladder:
            continue
        current_index = next((idx for idx, record in enumerate(ladder) if _contains(_interval(record), running)), None)
        if current_index is None:
            continue
        current = ladder[current_index]
        current_interval = _interval(current)
        d1 = next(
            (
                record
                for record in ladder[current_index + 1 :]
                if _interval(record) is not None
                and current_interval is not None
                and _interval(record)[0] >= current_interval[1] - 1e-9  # type: ignore[index]
            ),
            None,
        )
        current_yes = _direct_quote(current, "yes")
        d1_no = _direct_quote(d1, "no")
        profile = _physical_profile(state)
        decision_key = {
            "strategy_instance": STRATEGY_INSTANCE,
            "city": city,
            "target_date": target_date,
            "snapshot_file": snapshot_file,
            "current_bracket": current.get("bracket"),
        }
        decision_id = hashlib.sha256(
            json.dumps(decision_key, sort_keys=True).encode("utf-8")
        ).hexdigest()[:24]
        market_fields = {
            "current_bracket": str(current.get("bracket") or ""),
            "current_question": str(current.get("question") or ""),
            "current_condition_id": str(current.get("condition_id") or ""),
            "current_market_id": str(current.get("market_id") or ""),
            "current_yes_ask": current_yes.get("ask"),
            "current_yes_ask_size": current_yes.get("ask_size"),
            "current_yes_bid": current_yes.get("bid"),
            "current_yes_book_status": current_yes.get("book_status"),
            "current_yes_token_id": current_yes.get("token_id"),
            "current_yes_fee_per_share": _fee_per_share(current_yes.get("ask")),
            "current_yes_effective_cost": _effective_cost(current_yes.get("ask")),
            "current_yes_indicative_price": finite(current.get("market_yes_price")),
            "d1_bracket": str(d1.get("bracket") or "") if d1 else "",
            "d1_question": str(d1.get("question") or "") if d1 else "",
            "d1_condition_id": str(d1.get("condition_id") or "") if d1 else "",
            "d1_market_id": str(d1.get("market_id") or "") if d1 else "",
            "d1_no_ask": d1_no.get("ask"),
            "d1_no_ask_size": d1_no.get("ask_size"),
            "d1_no_bid": d1_no.get("bid"),
            "d1_no_book_status": d1_no.get("book_status"),
            "d1_no_token_id": d1_no.get("token_id"),
            "d1_no_fee_per_share": _fee_per_share(d1_no.get("ask")),
            "d1_no_effective_cost": _effective_cost(d1_no.get("ask")),
            "d1_no_indicative_price": (
                None if d1 is None or finite(d1.get("market_yes_price")) is None else 1.0 - finite(d1.get("market_yes_price"))  # type: ignore[operator]
            ),
            "direct_quote_pair_available": current_yes.get("ask") is not None and d1_no.get("ask") is not None,
        }
        decisions.append(
            json_ready(
                {
                    **state,
                    **market_fields,
                    **profile,
                    "record_type": "weather_strategy_shadow_decision",
                    "strategy_id": STRATEGY_ID,
                    "strategy_instance": STRATEGY_INSTANCE,
                    "shadow_decision_id": decision_id,
                    "decision_mode": DECISION_MODE,
                    "mode": "zero_notional_shadow",
                    "zero_notional": True,
                    "no_order_placed": True,
                    "snapshot_file": snapshot_file,
                    "created_at_utc": utc_now(),
                    "feature_frame_ref": refs_by_key.get((city, target_date)),
                }
            )
        )
    return decisions


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    snapshot_path = latest_snapshot(Path(args.snapshot_dir))
    if snapshot_path is None:
        return {"status": "no_snapshot", "generated_at_utc": utc_now()}
    output_dir = Path(args.output_dir)
    state_path = output_dir / "state.json"
    prior_state = read_json(state_path) if state_path.exists() else {}
    if not args.force and prior_state.get("last_snapshot_file") == snapshot_path.name:
        return {
            "status": "already_processed",
            "snapshot_file": snapshot_path.name,
            "generated_at_utc": utc_now(),
        }

    snapshot = read_json(snapshot_path)
    collection_started_at_utc = str(
        snapshot.get("collection_started_at_utc") or snapshot.get("ts_utc") or ""
    )
    as_of_utc = snapshot_decision_asof(snapshot)
    observation_path = Path(args.observation_cache)
    observations, pit_counts = observation_evidence_asof(observation_path, as_of_utc)
    curve_rows = recent_curve_rows(Path(args.forecast_curve_dir), limit=int(args.curve_file_limit))
    same_day_records = [
        dict(row)
        for row in snapshot.get("records") or []
        if isinstance(row, Mapping)
        and str(row.get("target_date") or row.get("event_date") or "")
        == str(row.get("city_local_date_at_snapshot") or "")
    ]
    frame, audits = build_weather_state_frame_with_audits(
        same_day_records,
        observations,
        forecast_curve_rows=curve_rows,
        as_of_ts_utc=as_of_utc,
        source_profile_id="weather_data_feed_observation_cache+forecast_hourly_curve_v4",
        input_snapshot_id=snapshot_path.name,
        pit_provenance=PIT_PROVENANCE_LIVE_CAPTURE,
        builder_version=BUILDER_VERSION,
    )

    feature_rows = frame.to_dict("records")
    feature_refs: list[dict[str, Any]] = []
    store_frame_id = ""
    if not frame.empty:
        stored = write_feature_frame_store(frame, Path(args.feature_store))
        feature_refs = stored.row_refs
        store_frame_id = stored.store_frame_id
    decisions = build_decisions(
        snapshot,
        feature_rows,
        feature_refs,
        snapshot_file=snapshot_path.name,
    )
    quote_refresh_counts: Counter[str] = Counter()
    if not bool(getattr(args, "disable_book_refresh", False)):
        quote_refresh_counts = refresh_candidate_quotes(
            decisions,
            proxy=getattr(args, "book_proxy", None),
            timeout_sec=float(getattr(args, "book_timeout_sec", 4.0)),
            max_pairs=int(getattr(args, "max_book_refresh_pairs", 10)),
        )
    append_jsonl(output_dir / "state_decisions.jsonl", decisions)
    profile_counts = Counter(str(row.get("physical_confirmation_profile") or "unknown") for row in decisions)
    summary = {
        "status": "ok",
        "generated_at_utc": utc_now(),
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_INSTANCE,
        "mode": "zero_notional_shadow",
        "snapshot_file": snapshot_path.name,
        "snapshot_ts_utc": as_of_utc,
        "snapshot_collection_started_at_utc": collection_started_at_utc,
        "snapshot_available_at_utc": as_of_utc,
        "decision_as_of_utc": as_of_utc,
        "observation_cache": str(observation_path),
        "feature_store_frame_id": store_frame_id,
        "feature_rows": len(feature_rows),
        "decision_rows": len(decisions),
        "direct_quote_pairs": sum(bool(row.get("direct_quote_pair_available")) for row in decisions),
        "direct_quote_refresh_counts": dict(sorted(quote_refresh_counts.items())),
        "physical_profile_counts": dict(sorted(profile_counts.items())),
        "pit_observation_counts": dict(sorted(pit_counts.items())),
        "build_audit_counts": dict(sorted(Counter(audit.reason for audit in audits).items())),
        "weather_physical_feature_fields": list(WEATHER_PHYSICAL_FEATURE_FIELDS),
        "probability_status": "not_fitted_forward_collection",
        "live_action": "none",
    }
    write_json(output_dir / str(getattr(args, "summary_filename", "latest_summary.json")), summary)
    append_jsonl(
        output_dir / str(getattr(args, "summary_history_filename", "summary_history.jsonl")),
        [summary],
    )
    write_json(state_path, {"last_snapshot_file": snapshot_path.name, "updated_at_utc": utc_now()})
    return summary


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", nargs="?", choices=("run", "loop"), default="run")
    ap.add_argument("--snapshot-dir", default=str(SNAPSHOT_DIR_DEFAULT))
    ap.add_argument("--observation-cache", default=str(OBSERVATION_CACHE_DEFAULT))
    ap.add_argument("--forecast-curve-dir", default=str(FORECAST_CURVE_DIR_DEFAULT))
    ap.add_argument("--output-dir", default=str(OUTPUT_DIR_DEFAULT))
    ap.add_argument("--summary-filename", default="latest_summary.json")
    ap.add_argument("--summary-history-filename", default="summary_history.jsonl")
    ap.add_argument("--feature-store", default=str(FEATURE_STORE_DEFAULT))
    ap.add_argument("--curve-file-limit", type=int, default=16)
    ap.add_argument("--book-proxy", default=None)
    ap.add_argument("--book-timeout-sec", type=float, default=4.0)
    ap.add_argument("--max-book-refresh-pairs", type=int, default=10)
    ap.add_argument("--disable-book-refresh", action="store_true")
    ap.add_argument("--interval-seconds", type=float, default=60.0)
    ap.add_argument("--force", action="store_true")
    return ap


def main() -> int:
    args = parser().parse_args()
    if args.command == "run":
        print(json.dumps(run_once(args), ensure_ascii=False, sort_keys=True))
        return 0
    while True:
        try:
            summary = run_once(args)
            if summary.get("status") != "already_processed":
                print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
        except Exception as exc:  # noqa: BLE001
            error = {"status": "error", "generated_at_utc": utc_now(), "error": f"{type(exc).__name__}: {exc}"}
            append_jsonl(Path(args.output_dir) / "summary_history.jsonl", [error])
            print(json.dumps(error, ensure_ascii=False, sort_keys=True), flush=True)
        time.sleep(max(10.0, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
