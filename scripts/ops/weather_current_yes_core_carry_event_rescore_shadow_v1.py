#!/usr/bin/env python3
"""Zero-notional event-driven checkpoint shadow for Current-YES Core Carry.

The runner tails the already-built PIT weather states owned by the live Core
Carry runtime.  It creates a new checkpoint only when one or more observable
state identities change:

* a new official observation/METAR report epoch;
* a new forecast hourly-curve content hash;
* a new exact-bracket/token identity.

Every transition is retained in the signal funnel.  In-domain transitions are
scored with the frozen Core model and a research-only 0.50 market-mid floor,
using the fresh executable ten-share ask ladder.  This process has no execution
adapter, creates no TradeIntent and always remains zero-notional.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import weather_current_yes_core_carry_pre_live_v1 as core_signal  # noqa: E402
from scripts.ops.weather_market_proxy import market_httpx_client  # noqa: E402
from src.strategies.weather_edge_v1.tools.current_yes_core_carry import (  # noqa: E402
    evaluate_entry,
    load_artifact,
)


STRATEGY_ID = "current_yes_core_carry_event_rescore_v1"
STRATEGY_INSTANCE = "current_yes_core_carry_event_rescore_shadow_v1"
SCHEMA_VERSION = "current_yes_core_carry_event_rescore_shadow_v1"
CANDIDATE_GRAIN_VERSION = "core_carry_first_seen_state_transition_v1"
RESEARCH_MARKET_MID_FLOOR = 0.50
LIVE_AUTHORIZED_MARKET_MID_FLOOR = 0.80
RESEARCH_TAKER_SHARES = 10.0
DEFAULT_CORE_RUNTIME = Path(
    "/Volumes/jrs/pm_agents/runtime/weather_edge_v1/"
    "current_yes_core_carry_tiny_live_v2"
)
DEFAULT_OUTPUT_DIR = Path(
    "/Volumes/jrs/pm_agents/runtime/weather_edge_v1/"
    "current_yes_core_carry_event_rescore_shadow_v1"
)
DEFAULT_ARTIFACT = (
    ROOT
    / "src/strategies/weather_edge_v1/config/"
    "current_yes_core_carry_model_v3.json"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def stable_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def append_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    materialized = [dict(row) for row in rows]
    if not materialized:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(materialized)


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_json(path: Path, default: Mapping[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default)
    return payload if isinstance(payload, dict) else dict(default)


def source_code_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def read_appended_jsonl(path: Path, offset: int) -> tuple[list[dict[str, Any]], int, str]:
    """Read complete JSONL records appended since ``offset``.

    The writer may be in the middle of its last line.  The incomplete suffix is
    left for the next poll and never parsed as a coverage failure.
    """

    if not path.exists():
        return [], max(0, offset), "source_missing"
    size = path.stat().st_size
    if offset < 0 or offset > size:
        return [], size, "source_truncated_reset_to_end"
    if offset == size:
        return [], offset, "no_new_bytes"
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()
    newline = data.rfind(b"\n")
    if newline < 0:
        return [], offset, "partial_line_pending"
    complete = data[: newline + 1]
    next_offset = offset + len(complete)
    rows: list[dict[str, Any]] = []
    for raw in complete.splitlines():
        try:
            row = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows, next_offset, "ok"


def forecast_curve_hash(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("forecast_values_hash") or "").strip()
    if explicit:
        return explicit
    curve = row.get("hourly_curve")
    if not isinstance(curve, list) or not curve:
        return ""
    return stable_hash({"hourly_curve": curve})[:16]


def state_signature(row: Mapping[str, Any]) -> dict[str, str]:
    return {
        "observation_epoch": str(row.get("source_report_ts_utc") or ""),
        "observation_source": str(row.get("obs_source") or ""),
        "forecast_curve_hash": forecast_curve_hash(row),
        "forecast_source": str(row.get("forecast_source") or ""),
        "bracket": str(row.get("current_bracket") or ""),
        "token_id": str(row.get("current_yes_token_id") or ""),
    }


def transition_types(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> list[str]:
    if previous is None:
        return []
    out: list[str] = []
    old_obs = str(previous.get("observation_epoch") or "")
    new_obs = str(current.get("observation_epoch") or "")
    if new_obs and new_obs != old_obs:
        source = str(current.get("observation_source") or "").lower()
        out.append("new_metar" if "metar" in source or "aviation" in source else "new_observation")
    old_forecast = str(previous.get("forecast_curve_hash") or "")
    new_forecast = str(current.get("forecast_curve_hash") or "")
    if new_forecast and new_forecast != old_forecast:
        out.append("forecast_revision")
    old_bracket = (
        str(previous.get("bracket") or ""),
        str(previous.get("token_id") or ""),
    )
    new_bracket = (
        str(current.get("bracket") or ""),
        str(current.get("token_id") or ""),
    )
    if any(new_bracket) and new_bracket != old_bracket:
        out.append("exact_bracket_transition")
    return out


def submitted_core_city_days(path: Path) -> set[str]:
    city_days: set[str] = set()
    if not path.exists():
        return city_days
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("status") or "") != "submitted":
                continue
            city = str(row.get("city") or "")
            target_date = str(row.get("target_date") or "")
            if city and target_date:
                city_days.add(f"{city}|{target_date}")
    return city_days


def event_domain(row: Mapping[str, Any]) -> tuple[bool, list[str], int | None, int | None]:
    reasons: list[str] = []
    hour, minute = core_signal.checkpoint_clock(row)
    if hour is None or minute is None:
        reasons.append("missing_local_event_clock")
    elif not 13 <= hour <= 17:
        reasons.append("outside_core_local_hour_window")
    freshness, freshness_reason = core_signal.observation_freshness_valid(row)
    if not freshness:
        reasons.append(freshness_reason)
    if not str(row.get("current_yes_token_id") or ""):
        reasons.append("missing_current_yes_token")
    return not reasons, sorted(set(reasons)), hour, minute


def research_artifact(path: Path) -> tuple[dict[str, Any], str]:
    frozen = load_artifact(path)
    candidate = copy.deepcopy(frozen)
    candidate["entry_policy"]["market_mid_floor"] = RESEARCH_MARKET_MID_FLOOR
    candidate["entry_policy"]["taker_shares"] = RESEARCH_TAKER_SHARES
    return candidate, str(frozen["artifact_hash"])


def market_band(mid: float | None) -> str:
    if mid is None:
        return "unavailable"
    if mid < RESEARCH_MARKET_MID_FLOOR:
        return "below_0_50"
    if mid < LIVE_AUTHORIZED_MARKET_MID_FLOOR:
        return "mid_0_50_to_0_80"
    return "live_authorized_0_80_plus"


def score_transition(
    row: Mapping[str, Any],
    *,
    event_types: list[str],
    previous_signature: Mapping[str, Any],
    current_signature: Mapping[str, Any],
    artifact: Mapping[str, Any],
    model_artifact_hash: str,
    book: Mapping[str, Any] | None,
    scorable: bool,
    scorable_reasons: list[str],
    hour: int | None,
    minute: int | None,
    family_city_days: set[str],
) -> dict[str, Any]:
    city = str(row.get("city") or "")
    target_date = str(row.get("target_date") or "")
    event_id = "core-event-" + stable_hash(
        {
            "city": city,
            "target_date": target_date,
            "snapshot": row.get("snapshot_file"),
            "decision_snapshot_ts_utc": row.get("decision_snapshot_ts_utc"),
            "event_types": event_types,
            "current_signature": dict(current_signature),
        }
    )[:24]
    result: dict[str, Any]
    enriched = dict(row)
    if scorable and book is not None:
        enriched.update(
            {
                "current_yes_bid": book.get("bid"),
                "current_yes_ask": book.get("ask"),
                "current_yes_bid_size": book.get("bid_size"),
                "current_yes_ask_size": book.get("ask_size"),
                "current_yes_tick_size": book.get("tick_size"),
                "current_yes_book_status": book.get("status"),
                "current_yes_book_fetched_at_utc": book.get("fetched_at_utc"),
                "checkpoint_eligible": True,
                "checkpoint_key": event_id,
            }
        )
        result = evaluate_entry(enriched, book.get("asks") or [], artifact)
    else:
        result = {
            "market_mid": None,
            "model_probability_hold": None,
            "model_edge_after_fee_and_depth": None,
            "eligible": False,
            "decision_status": "not_scorable",
            "probability_status": "not_scored_event_coverage_or_domain",
            "reasons": list(scorable_reasons),
            "taker_ladder": {
                "quantity": RESEARCH_TAKER_SHARES,
                "executable": False,
            },
        }
    mid = finite(result.get("market_mid"))
    city_day = f"{city}|{target_date}"
    return {
        **enriched,
        **result,
        "record_type": "current_yes_core_carry_event_rescore_shadow_score",
        "schema_version": SCHEMA_VERSION,
        "candidate_grain_version": CANDIDATE_GRAIN_VERSION,
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_INSTANCE,
        "event_checkpoint_id": event_id,
        "event_types": event_types,
        "event_type_count": len(event_types),
        "previous_state_signature": dict(previous_signature),
        "current_state_signature": dict(current_signature),
        "forecast_revision_hash_basis": "forecast_values_hash_or_canonical_hourly_curve_sha256",
        "event_available_at_utc": row.get("decision_snapshot_ts_utc"),
        "checkpoint_hour_local": hour,
        "checkpoint_minute_local": minute,
        "event_domain_scorable": scorable,
        "event_domain_blockers": list(scorable_reasons),
        "market_mid_band": market_band(mid),
        "research_market_mid_floor": RESEARCH_MARKET_MID_FLOOR,
        "live_authorized_market_mid_floor": LIVE_AUTHORIZED_MARKET_MID_FLOOR,
        "research_taker_shares": RESEARCH_TAKER_SHARES,
        "model_artifact_hash": model_artifact_hash,
        "selector_config_id": "core_event_first_positive_10t_mid050_v1",
        "family_city_day_already_exposed": city_day in family_city_days,
        "incremental_vs_current_live_city_day": city_day not in family_city_days,
        "zero_notional": True,
        "notional": 0.0,
        "trade_intent_created": False,
        "order_created": False,
        "created_at_utc": utc_now(),
    }


def initial_state(source_size: int) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_offset": source_size,
        "state_signatures": {},
        "selected_city_days": [],
        "started_at_utc": utc_now(),
        "bootstrap_mode": "start_at_source_end_then_seed_each_city_day",
    }


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    core_runtime = Path(args.core_runtime)
    source = core_runtime / "state_decisions.jsonl"
    live_orders = core_runtime / "live_orders.jsonl"
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "state.json"
    artifact, model_artifact_hash = research_artifact(Path(args.artifact))

    if not state_path.exists():
        source_size = source.stat().st_size if source.exists() else 0
        state = initial_state(source_size)
        write_json_atomic(state_path, state)
        summary = {
            "status": "ok",
            "generated_at_utc": utc_now(),
            "strategy_id": STRATEGY_ID,
            "strategy_instance": STRATEGY_INSTANCE,
            "mode": "zero_notional_event_shadow",
            "source_status": "bootstrap_at_end" if source.exists() else "source_missing",
            "source_path": str(source),
            "source_offset": source_size,
            "rows_read": 0,
            "event_transitions": 0,
            "event_scores_written": 0,
            "positive_candidates_written": 0,
            "model_artifact_hash": model_artifact_hash,
            "source_code_sha": source_code_sha(),
            "live_action": "none",
            "real_orders": 0,
            "real_fills": 0,
        }
        write_json_atomic(output_dir / "latest.json", summary)
        append_jsonl(output_dir / "summary_history.jsonl", [summary])
        return summary

    state = read_json(state_path, initial_state(0))
    rows, next_offset, source_status = read_appended_jsonl(
        source, int(state.get("source_offset") or 0)
    )
    signatures = {
        str(key): dict(value)
        for key, value in dict(state.get("state_signatures") or {}).items()
        if isinstance(value, Mapping)
    }
    selected_city_days = set(map(str, state.get("selected_city_days") or []))
    utc_day = datetime.now(timezone.utc).date().isoformat()
    daily_book_usage = {
        str(key): int(value)
        for key, value in dict(state.get("book_requests_by_utc_day") or {}).items()
    }
    daily_books_used = int(daily_book_usage.get(utc_day, 0))
    family_city_days = submitted_core_city_days(live_orders)
    event_rows: list[tuple[dict[str, Any], list[str], dict[str, Any], dict[str, Any]]] = []
    counts: Counter[str] = Counter()

    for row in rows:
        city = str(row.get("city") or "")
        target_date = str(row.get("target_date") or "")
        if not city or not target_date:
            counts["missing_city_or_target_date"] += 1
            continue
        city_day = f"{city}|{target_date}"
        current = state_signature(row)
        previous = signatures.get(city_day)
        events = transition_types(previous, current)
        if previous is None:
            counts["bootstrap_city_day"] += 1
        elif events:
            event_rows.append((row, events, dict(previous), current))
            for event in events:
                counts[f"event:{event}"] += 1
        else:
            counts["unchanged_state"] += 1
        signatures[city_day] = current

    scored: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    books_used = 0
    client_context = market_httpx_client(
        args.market_proxy, timeout=float(args.book_timeout_sec)
    )
    with client_context as client:
        for row, events, previous, current in event_rows:
            scorable, reasons, hour, minute = event_domain(row)
            book: Mapping[str, Any] | None = None
            if scorable and books_used >= int(args.max_books_per_run):
                scorable = False
                reasons = [*reasons, "event_book_budget_exhausted"]
                counts["event_book_budget_exhausted"] += 1
            elif scorable and daily_books_used >= int(args.max_books_per_utc_day):
                scorable = False
                reasons = [*reasons, "event_daily_book_budget_exhausted"]
                counts["event_daily_book_budget_exhausted"] += 1
            elif scorable:
                books_used += 1
                daily_books_used += 1
                book = core_signal.fetch_full_book(
                    client, str(row.get("current_yes_token_id") or "")
                )
                counts[f"book:{book.get('status')}"] += 1
            score = score_transition(
                row,
                event_types=events,
                previous_signature=previous,
                current_signature=current,
                artifact=artifact,
                model_artifact_hash=model_artifact_hash,
                book=book,
                scorable=scorable,
                scorable_reasons=reasons,
                hour=hour,
                minute=minute,
                family_city_days=family_city_days,
            )
            city_day = f"{score.get('city')}|{score.get('target_date')}"
            first_positive = bool(score.get("eligible")) and city_day not in selected_city_days
            score["first_positive_event_city_day"] = first_positive
            score["event_city_day_previously_selected"] = city_day in selected_city_days
            if first_positive:
                selected_city_days.add(city_day)
                candidate = {
                    "record_type": "current_yes_core_carry_event_rescore_shadow_candidate",
                    "schema_version": SCHEMA_VERSION,
                    "candidate_grain_version": CANDIDATE_GRAIN_VERSION,
                    "strategy_id": STRATEGY_ID,
                    "strategy_instance": STRATEGY_INSTANCE,
                    "signal_candidate_id": "candidate-" + str(score["event_checkpoint_id"]),
                    "event_checkpoint_id": score["event_checkpoint_id"],
                    "city": score.get("city"),
                    "target_date": score.get("target_date"),
                    "bracket": score.get("current_bracket"),
                    "token_id": score.get("current_yes_token_id"),
                    "event_types": events,
                    "decision_snapshot_ts_utc": score.get("decision_snapshot_ts_utc"),
                    "market_mid": score.get("market_mid"),
                    "market_mid_band": score.get("market_mid_band"),
                    "model_probability_hold": score.get("model_probability_hold"),
                    "effective_cost_per_share": (
                        score.get("taker_ladder") or {}
                    ).get("effective_cost_per_share"),
                    "model_edge_after_fee_and_depth": score.get(
                        "model_edge_after_fee_and_depth"
                    ),
                    "family_city_day_already_exposed": score.get(
                        "family_city_day_already_exposed"
                    ),
                    "incremental_vs_current_live_city_day": score.get(
                        "incremental_vs_current_live_city_day"
                    ),
                    "zero_notional": True,
                    "notional": 0.0,
                    "trade_intent_created": False,
                    "order_created": False,
                    "created_at_utc": utc_now(),
                }
                candidates.append(candidate)
            scored.append(score)

    append_jsonl(output_dir / "event_scores.jsonl", scored)
    append_jsonl(output_dir / "signal_candidates.jsonl", candidates)
    state.update(
        {
            "schema_version": SCHEMA_VERSION,
            "source_offset": next_offset,
            "state_signatures": signatures,
            "selected_city_days": sorted(selected_city_days),
            "book_requests_by_utc_day": {
                utc_day: daily_books_used,
            },
            "updated_at_utc": utc_now(),
            "model_artifact_hash": model_artifact_hash,
        }
    )
    write_json_atomic(state_path, state)
    band_counts = Counter(str(row.get("market_mid_band") or "") for row in scored)
    summary = {
        "status": "ok",
        "generated_at_utc": utc_now(),
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_INSTANCE,
        "mode": "zero_notional_event_shadow",
        "source_status": source_status,
        "source_path": str(source),
        "source_offset": next_offset,
        "rows_read": len(rows),
        "event_transitions": len(event_rows),
        "event_scores_written": len(scored),
        "positive_candidates_written": len(candidates),
        "books_used": books_used,
        "max_books_per_run": int(args.max_books_per_run),
        "books_used_utc_day": daily_books_used,
        "max_books_per_utc_day": int(args.max_books_per_utc_day),
        "event_counts": dict(sorted(counts.items())),
        "market_mid_band_counts": dict(sorted(band_counts.items())),
        "tracked_city_days": len(signatures),
        "selected_city_days": len(selected_city_days),
        "research_market_mid_floor": RESEARCH_MARKET_MID_FLOOR,
        "live_authorized_market_mid_floor": LIVE_AUTHORIZED_MARKET_MID_FLOOR,
        "research_taker_shares": RESEARCH_TAKER_SHARES,
        "model_artifact_hash": model_artifact_hash,
        "source_code_sha": source_code_sha(),
        "live_action": "none",
        "real_orders": 0,
        "real_fills": 0,
    }
    write_json_atomic(output_dir / "latest.json", summary)
    append_jsonl(output_dir / "summary_history.jsonl", [summary])
    return summary


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--core-runtime", default=str(DEFAULT_CORE_RUNTIME))
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    ap.add_argument("--artifact", default=str(DEFAULT_ARTIFACT))
    ap.add_argument("--market-proxy", default=None)
    ap.add_argument("--book-timeout-sec", type=float, default=5.0)
    ap.add_argument("--max-books-per-run", type=int, default=40)
    ap.add_argument("--max-books-per-utc-day", type=int, default=2000)
    ap.add_argument("--interval-seconds", type=float, default=15.0)
    ap.add_argument("--once", action="store_true")
    return ap


def main() -> int:
    args = parser().parse_args()
    while True:
        try:
            summary = run_once(args)
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
        except Exception as exc:  # noqa: BLE001
            output_dir = Path(args.output_dir)
            error = {
                "status": "error",
                "generated_at_utc": utc_now(),
                "strategy_id": STRATEGY_ID,
                "strategy_instance": STRATEGY_INSTANCE,
                "mode": "zero_notional_event_shadow",
                "error": f"{type(exc).__name__}: {exc}",
                "live_action": "none",
                "real_orders": 0,
                "real_fills": 0,
            }
            write_json_atomic(output_dir / "latest.json", error)
            append_jsonl(output_dir / "summary_history.jsonl", [error])
            print(json.dumps(error, ensure_ascii=False, sort_keys=True), flush=True)
        if args.once:
            return 0
        time.sleep(max(5.0, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
