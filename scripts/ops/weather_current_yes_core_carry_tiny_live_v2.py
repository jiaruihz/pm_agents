#!/usr/bin/env python3
"""Tiny-live adapter for the current-YES residual carry probe.

The signal/checkpoint contract remains owned by
``weather_current_yes_core_carry_pre_live_v1.py`` and the no-age/no-peak-clock
v3 artifact.
For each first positive-EV city-day signal this adapter submits two separately
attributed children:

* taker: fresh full-ladder ten-share EV is revalidated immediately before send;
* maker: five shares at best bid + one tick, held in queue for five minutes,
  then repriced at most once at the midpoint stage and once at the near-ask
  stage while retaining one cent of model edge.

Maker replacements never cross the ask and never convert to taker. They are
cancelled 90 seconds before the next expected source report, when an unexpected
observation epoch arrives, or when the 15-minute parent TTL expires.  The clock
is intentionally based on source-report time rather than this collector's later
availability: another participant may receive the report first.  A maker first
seen inside that blackout is skipped for live and retained only as an explicit
post-update shadow counterfactual; it is never silently or automatically
re-armed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import weather_current_yes_core_carry_pre_live_v1 as signal_runner  # noqa: E402
from scripts.ops import weather_current_yes_heat_death_shadow_v1 as weather_state  # noqa: E402
from scripts.ops.weather_core_carry_order_runtime import (  # noqa: E402
    execute_core_carry_plans,
)
from scripts.ops.weather_core_carry_near_core_ws_controller import (  # noqa: E402
    TERMINAL as NEAR_CORE_TERMINAL_ORDER_STATUSES,
    terminal_safety_cancel_order_ids,
)
from scripts.ops.weather_market_proxy import market_httpx_client  # noqa: E402
from src.platform.market_data.capture_demand import CaptureDemand  # noqa: E402
from src.strategies.runtime import runtime_state  # noqa: E402
from src.strategies.weather_edge_v1.execution.engine import (  # noqa: E402
    allocate_profile_shares,
    build_core_carry_legacy_plan_compatibility,
)
from src.strategies.weather_edge_v1.execution import near_core_maker_probe  # noqa: E402
from src.strategies.weather_edge_v1.execution.profiles import (  # noqa: E402
    execution_config_id_for_profile,
    get_execution_profile,
)
from src.strategies.weather_edge_v1.tools.current_yes_core_carry import (  # noqa: E402
    evaluate_entry,
    load_artifact,
    maker_resting_price,
)
from weather_data_feed.observation_cache import index_observation_cache  # noqa: E402
from weather_clock_contract import parse_utc_or_none  # noqa: E402


STRATEGY_ID = "current_yes_core_carry_v3"
STRATEGY_INSTANCE = "current_yes_core_carry_tiny_live_v2"
CONFIG_ID = "current_yes_core_carry_model_v3_split_10_taker_5_maker_edge_cap_v3"
EXECUTION_PROFILE = "split_taker_maker_edge_capped_no_fallback_v3"
DEPLOYMENT_CONTRACT_VERSION = "core_carry_v3_10t5m_plus_continuous_near5_v4"
MODEL_VERSION = "current_yes_core_carry_model_v3_no_peak_clock"
FROZEN_TAKER_SHARES = 10.0
FROZEN_MAKER_SHARES = 5.0
OUTPUT_DIR = ROOT / "runtime/weather_edge_v1" / STRATEGY_INSTANCE
ARTIFACT_PATH = ROOT / "src/strategies/weather_edge_v1/config/current_yes_core_carry_model_v3.json"
LEGACY_FAMILY_LIVE_ORDER_FILES = (
    ROOT
    / "runtime/weather_edge_v1/current_yes_heat_death_tiny_live_h1_late_carry_v1/live_orders.jsonl",
    ROOT
    / "runtime/weather_edge_v1/current_yes_heat_death_tiny_live_h2_early_dislocation_v1/live_orders.jsonl",
    ROOT / "runtime/weather_edge_v1/current_yes_heat_death_tiny_live_v1/live_orders.jsonl",
)
BJ = timezone(timedelta(hours=8))
RETRYABLE_MAKER_POST_FAILURES = {
    "current_yes_residual_maker_no_resting_price",
    "maker_only_no_resting_price",
    "maker_only_price_would_cross",
    "post_only_crosses_book",
}
TRANSIENT_ENTRY_BLOCKERS = {"near_core_cancel_must_confirm_before_core_submit"}
CRITICAL_SOURCE_PATHS = (
    "scripts/ops/weather_current_yes_core_carry_tiny_live_v2.py",
    "scripts/ops/weather_current_yes_core_carry_pre_live_v1.py",
    "scripts/ops/weather_current_yes_heat_death_shadow_v1.py",
    "scripts/ops/weather_core_carry_order_runtime.py",
    "scripts/ops/weather_core_carry_near_core_ws_controller.py",
    "scripts/ops/weather_polymarket_live_transport.py",
    "weather_data_feed/observation_cache.py",
    "weather_data_feed/physical_features.py",
    "weather_feature_layer/builders.py",
    "src/strategies/weather_edge_v1/execution/contracts.py",
    "src/strategies/weather_edge_v1/execution/engine.py",
    "src/strategies/weather_edge_v1/execution/profiles.py",
    "src/strategies/weather_edge_v1/execution/lifecycle.py",
    "src/strategies/weather_edge_v1/execution/near_core_maker_probe.py",
    "src/strategies/weather_edge_v1/execution/venue/polymarket.py",
    "src/strategies/weather_edge_v1/runtime/execution_journal.py",
    "src/strategies/weather_edge_v1/runtime/order_runtime.py",
    "src/strategies/weather_edge_v1/tools/current_yes_core_carry.py",
    "src/strategies/weather_edge_v1/config/current_yes_core_carry_model_v3.json",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def deployment_metadata() -> dict[str, Any]:
    sha = ""
    dirty = True
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = (
            subprocess.run(
                ["git", "diff", "--quiet", "HEAD", "--", *CRITICAL_SOURCE_PATHS],
                cwd=ROOT,
                check=False,
            ).returncode
            != 0
        )
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "deployment_contract_version": DEPLOYMENT_CONTRACT_VERSION,
        "deployed_repo_sha": sha,
        "critical_source_dirty": dirty,
        "source_checkout_root": str(ROOT),
    }


DEPLOYMENT_METADATA = deployment_metadata()


def assert_runtime_contract() -> None:
    """Fail closed before scoring or ordering if the deployed PIT semantics drift."""

    if not DEPLOYMENT_METADATA["deployed_repo_sha"]:
        raise RuntimeError("deployment contract failed: repository SHA unavailable")
    if DEPLOYMENT_METADATA["critical_source_dirty"]:
        raise RuntimeError("deployment contract failed: critical strategy source is dirty")
    selected = index_observation_cache(
        {
            "decision_as_of_utc": "2026-07-26T12:38:10Z",
            "records": [
                {
                    "city": "London",
                    "target_date": "2026-07-26",
                    "fetched_at_utc": "2026-07-26T12:33:00Z",
                    "last_obs_utc": "2026-07-26T12:20:00Z",
                    "current_temp_c": 26.0,
                    "age_min": 13.0,
                },
                {
                    "city": "London",
                    "target_date": "2026-07-26",
                    "fetched_at_utc": "2026-07-25T23:58:00Z",
                    "last_obs_utc": "2026-07-25T23:50:00Z",
                    "current_temp_c": 20.0,
                    "age_min": 8.0,
                },
            ],
        }
    ).get(("London", "2026-07-26"))
    expected_age = 18.0 + 10.0 / 60.0
    if (
        not selected
        or selected.get("current_temp_c") != 26.0
        or abs(float(selected.get("obs_age_minutes") or 0.0) - expected_age) > 1e-9
        or selected.get("obs_age_clock_source") != "decision_asof_minus_source_report"
    ):
        raise RuntimeError("deployment contract failed: observation PIT clock semantics drifted")


def parse_utc(value: Any) -> datetime | None:
    return parse_utc_or_none(value)


def stable_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


DIRECT_BOOK_CLOCK_STATUS = "direct_clob_response_clock_v1"


def near_core_book_freshness(
    row: Mapping[str, Any],
    *,
    now: datetime,
    max_age_sec: float,
) -> dict[str, Any]:
    """Fail closed on the direct CLOB quote clock used to build an entry.

    Rows written before the direct response-clock lineage was added carry a
    paper-snapshot response clock next to a newer direct-fetch BBO.  For those
    legacy rows the conservative direct-fetch timestamp is the only clock that
    belongs to the scored bid/ask.
    """

    lineage_status = str(row.get("current_yes_book_clock_lineage_status") or "")
    if lineage_status == DIRECT_BOOK_CLOCK_STATUS:
        clock_value = row.get("current_yes_book_response_received_at_utc")
        clock_source = "direct_response_received_at_utc"
    else:
        clock_value = row.get("current_yes_book_fetched_at_utc")
        clock_source = "legacy_direct_fetch_clock_utc"
    clock = parse_utc(clock_value)
    book_status = str(row.get("current_yes_book_status") or "")
    age_sec = (
        (now.astimezone(timezone.utc) - clock).total_seconds()
        if clock is not None
        else None
    )
    common = {
        "near_core_book_clock_utc": clock.isoformat() if clock is not None else "",
        "near_core_book_clock_source": clock_source,
        "near_core_book_status": book_status,
        "near_core_book_max_age_sec": float(max_age_sec),
    }
    if book_status != "ok":
        return {
            **common,
            "near_core_book_age_sec": (
                round(age_sec, 6) if age_sec is not None else None
            ),
            "near_core_book_fresh": False,
            "near_core_book_freshness_status": "direct_book_status_not_ok",
        }
    if clock is None:
        return {
            **common,
            "near_core_book_age_sec": None,
            "near_core_book_fresh": False,
            "near_core_book_freshness_status": "missing_direct_book_clock",
        }
    assert age_sec is not None
    if age_sec < 0.0:
        status = "direct_book_clock_in_future"
        fresh = False
    elif age_sec > float(max_age_sec):
        status = "stale_direct_book_quote"
        fresh = False
    else:
        status = "fresh_direct_book_quote"
        fresh = True
    return {
        **common,
        "near_core_book_age_sec": round(age_sec, 6),
        "near_core_book_fresh": fresh,
        "near_core_book_freshness_status": status,
    }

def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


DECISION_PACKET_SCHEMA_VERSION = "current_yes_core_carry_decision_packet_v1"
CAPTURE_DEMAND_TTL_MINUTES = 30
CAPTURE_DEMAND_MAX_LADDER_TOKENS = 24
CAPTURE_DEMAND_STRATEGY_KEY = "reheat_risk.current_yes"


def _decision_packet_json_default(value: Any) -> str:
    """Preserve Decimal values losslessly in the immutable journal."""

    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def append_decision_packet(path: Path, packet: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                dict(packet),
                ensure_ascii=False,
                sort_keys=True,
                default=_decision_packet_json_default,
            )
            + "\n"
        )


def _packet_value(
    row: Mapping[str, Any],
    *fields: str,
) -> dict[str, Any]:
    """Return only an already-loaded value, with missing lineage explicit."""

    for field in fields:
        value = row.get(field)
        if value is not None and value != "":
            return {"status": "available", "source_field": field, "value": value}
    return {
        "status": "unavailable",
        "source_field": None,
        "value": None,
        "reason": "missing_in_decision_row",
    }


def _quote_usable(row: Mapping[str, Any]) -> bool:
    ask = finite(row.get("current_yes_ask"))
    return ask is not None and ask >= 0.01


def _checkpoint_packet_ref(row: Mapping[str, Any] | None, *, reason: str) -> dict[str, Any]:
    if row is None:
        return {"status": "unavailable", "reason": reason, "checkpoint": None}
    return {
        "status": "available",
        "reason": None,
        "checkpoint": {
            "checkpoint_key": row.get("checkpoint_key"),
            "created_at_utc": row.get("created_at_utc"),
            "decision_snapshot_ts_utc": row.get("decision_snapshot_ts_utc"),
            "as_of_ts_utc": row.get("as_of_ts_utc"),
            "model_probability_hold": row.get("model_probability_hold"),
            "current_yes_bid": row.get("current_yes_bid"),
            "current_yes_ask": row.get("current_yes_ask"),
            "eligible": row.get("eligible"),
            "reasons": row.get("reasons"),
            "quote_usable": _quote_usable(row),
        },
    }


def decision_packet_id(row: Mapping[str, Any]) -> str:
    """Stable first-signal identity; it deliberately excludes mutable book data."""

    return "decision-packet-" + stable_hash(
        {
            "schema_version": DECISION_PACKET_SCHEMA_VERSION,
            "strategy_instance": STRATEGY_INSTANCE,
            "config_id": CONFIG_ID,
            "city": str(row.get("city") or ""),
            "target_date": str(row.get("target_date") or ""),
        }
    )


def _prior_checkpoints(trigger: Mapping[str, Any], scores: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Use persisted decision rows only; never reconstruct or fetch evidence."""

    city_day = (str(trigger.get("city") or ""), str(trigger.get("target_date") or ""))
    trigger_clock = parse_utc(
        trigger.get("decision_snapshot_ts_utc") or trigger.get("as_of_ts_utc") or trigger.get("created_at_utc")
    )
    result: list[dict[str, Any]] = []
    for candidate in scores:
        if (str(candidate.get("city") or ""), str(candidate.get("target_date") or "")) != city_day:
            continue
        candidate_clock = parse_utc(
            candidate.get("decision_snapshot_ts_utc")
            or candidate.get("as_of_ts_utc")
            or candidate.get("created_at_utc")
        )
        if candidate_clock is None or (trigger_clock is not None and candidate_clock >= trigger_clock):
            continue
        result.append(dict(candidate))
    return result


def build_decision_packet(
    trigger: Mapping[str, Any],
    *,
    historical_scores: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Create a self-contained immutable record from decision-time state only."""

    prior = _prior_checkpoints(trigger, historical_scores)
    last_checkpoint = prior[-1] if prior else None
    negatives = [row for row in prior if not bool(row.get("eligible"))]
    last_negative = negatives[-1] if negatives else None
    informative_quote_usable = [
        row
        for row in negatives
        if finite(row.get("model_probability_hold")) is not None and _quote_usable(row)
    ]
    last_usable_negative = informative_quote_usable[-1] if informative_quote_usable else None
    snapshot_clock = _packet_value(
        trigger, "decision_snapshot_ts_utc", "snapshot_available_at_utc", "snapshot_ts_utc"
    )
    return {
        "schema_version": DECISION_PACKET_SCHEMA_VERSION,
        "packet_id": decision_packet_id(trigger),
        "record_type": "current_yes_core_carry_decision_packet",
        "strategy_identity": {
            "strategy_id": STRATEGY_ID,
            "strategy_instance": STRATEGY_INSTANCE,
            "config_id": CONFIG_ID,
            "model_version": MODEL_VERSION,
            "artifact_hash": trigger.get("artifact_hash"),
            "deployment_contract_version": DEPLOYMENT_CONTRACT_VERSION,
        },
        "signal": {
            "signal_id": signal_id(trigger),
            "city": trigger.get("city"),
            "target_date": trigger.get("target_date"),
            "bracket": trigger.get("current_bracket"),
            "condition_id": trigger.get("condition_id") or trigger.get("current_condition_id"),
            "token_id": trigger.get("token_id") or trigger.get("current_yes_token_id"),
            "positive_taker_ev": True,
        },
        "decision_clocks": {
            "created_at_utc": _packet_value(trigger, "created_at_utc"),
            "as_of_ts_utc": _packet_value(trigger, "as_of_ts_utc", "decision_as_of_utc"),
            "snapshot_ts_utc": snapshot_clock,
        },
        "trigger": {"status": "available", "payload": dict(trigger)},
        "prior_checkpoints": {
            "last_checkpoint": _checkpoint_packet_ref(
                last_checkpoint, reason="no_prior_checkpoint_in_decision_history"
            ),
            "last_negative_checkpoint": _checkpoint_packet_ref(
                last_negative, reason="no_prior_negative_checkpoint_in_decision_history"
            ),
            "last_informative_quote_usable_negative_checkpoint": _checkpoint_packet_ref(
                last_usable_negative,
                reason="no_prior_informative_quote_usable_negative_checkpoint",
            ),
        },
        "event_references": {
            "observation": {
                "event_ts_utc": _packet_value(trigger, "source_report_ts_utc", "obs_last_obs_utc"),
                "available_at_utc": _packet_value(trigger, "obs_ingested_at_utc", "observation_available_at_utc", "available_at_utc"),
                "receive_at_utc": _packet_value(trigger, "obs_received_at_utc"),
                "row_id": _packet_value(trigger, "observation_history_id", "obs_row_id"),
            },
            "book": {
                "exchange_ts_utc": _packet_value(trigger, "book_exchange_ts_utc", "current_yes_book_exchange_ts_utc"),
                "available_at_utc": _packet_value(trigger, "book_available_at_utc", "current_yes_book_parsed_at_utc"),
                "receive_at_utc": _packet_value(trigger, "book_received_at_utc", "current_yes_book_response_received_at_utc"),
                "request_started_at_utc": _packet_value(trigger, "current_yes_book_request_started_at_utc"),
                "capture_id": _packet_value(trigger, "book_capture_id", "current_yes_book_batch_capture_id"),
                "snapshot_id": _packet_value(trigger, "book_snapshot_id"),
                "archive_path": _packet_value(trigger, "current_yes_book_archive_path"),
                "clock_lineage_status": _packet_value(trigger, "current_yes_book_clock_lineage_status"),
            },
            "forecast": {
                "values_hash": _packet_value(trigger, "forecast_values_hash"),
                "first_seen_utc": _packet_value(trigger, "forecast_first_seen_utc"),
                "receive_source": _packet_value(trigger, "forecast_receive_source", "forecast_curve_evidence"),
                "source": _packet_value(trigger, "forecast_source"),
                "model": _packet_value(trigger, "forecast_model"),
                "archive_path": _packet_value(trigger, "forecast_curve_archive_path"),
                "model_init_utc_estimated": _packet_value(trigger, "model_init_utc_estimated"),
                "model_init_basis": _packet_value(trigger, "model_init_basis"),
                "valid_from_local": _packet_value(trigger, "forecast_valid_from_local"),
                "valid_to_local": _packet_value(trigger, "forecast_valid_to_local"),
                "lineage_status": _packet_value(trigger, "forecast_lineage_status", "forecast_run_lineage_status"),
                "first_seen_lookup_status": _packet_value(trigger, "forecast_first_seen_lookup_status"),
                "issue_ts_utc": _packet_value(trigger, "forecast_issue_ts_utc"),
            },
        },
        "snapshot_references": {
            "feature_row_id": _packet_value(trigger, "fact_signal_candidate_id", "feature_row_id"),
            "snapshot_file": _packet_value(trigger, "snapshot_file"),
            "data_epoch_refs_json": _packet_value(trigger, "data_epoch_refs_json"),
            "snapshot_capture_id": _packet_value(trigger, "snapshot_capture_id"),
            "snapshot_producer_build_id": _packet_value(trigger, "snapshot_producer_build_id"),
            "book_snapshot": _packet_value(trigger, "book_snapshot_id", "book_snapshot_file", "current_yes_book_archive_path"),
        },
        "order_linkage": {
            "status": "unavailable_at_decision",
            "reason": "packet_is_written_before_order_planning_and_execution",
            "execution_ids": [],
            "order_ids": [],
        },
    }


def write_new_decision_packets(output_dir: Path) -> dict[str, Any]:
    """Append first positive signals, while preserving the execution path on failure."""

    journal = output_dir / "decision_packets.jsonl"
    existing = {str(row.get("packet_id") or "") for row in iter_jsonl(journal)}
    scores = list(iter_jsonl(output_dir / "pre_live_scores.jsonl"))
    written = 0
    written_packets: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    for would in iter_jsonl(output_dir / "would_orders.jsonl"):
        checkpoint_key = str(would.get("checkpoint_key") or "")
        trigger = next(
            (row for row in reversed(scores) if str(row.get("checkpoint_key") or "") == checkpoint_key),
            None,
        )
        if trigger is None:
            alerts.append({"status": "alert", "reason": "decision_packet_trigger_score_missing", "checkpoint_key": checkpoint_key})
            continue
        packet = build_decision_packet(trigger, historical_scores=scores)
        if packet["packet_id"] in existing:
            continue
        try:
            append_decision_packet(journal, packet)
        except OSError as exc:
            alerts.append(
                {
                    "status": "alert",
                    "reason": "decision_packet_write_failed",
                    "packet_id": packet["packet_id"],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        existing.add(packet["packet_id"])
        written += 1
        written_packets.append(packet)
    for alert in alerts:
        try:
            append_jsonl(output_dir / "decision_packet_alerts.jsonl", alert)
        except OSError:
            pass
    return {"written": written, "alerts": alerts, "written_packets": written_packets}


def write_capture_demands(
    output_dir: Path,
    *,
    packets: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Request a bounded full-ladder YES tape window for newly written packets."""

    journal = output_dir / "capture_demands.jsonl"
    existing = {str(row.get("demand_id") or "") for row in iter_jsonl(journal)}
    written = 0
    alerts: list[dict[str, Any]] = []
    for packet in packets:
        trigger = dict((packet.get("trigger") or {}).get("payload") or {})
        ladder = [dict(row) for row in trigger.get("full_ladder_yes_tokens") or [] if isinstance(row, Mapping)]
        if not ladder:
            alerts.append({
                "status": "alert",
                "reason": "capture_demand_full_ladder_tokens_missing",
                "packet_id": packet.get("packet_id"),
            })
            continue
        if len(ladder) > CAPTURE_DEMAND_MAX_LADDER_TOKENS:
            alerts.append({
                "status": "alert",
                "reason": "capture_demand_full_ladder_token_budget_exceeded",
                "packet_id": packet.get("packet_id"),
                "token_count": len(ladder),
                "max_tokens": CAPTURE_DEMAND_MAX_LADDER_TOKENS,
            })
            continue
        requested = parse_utc(trigger.get("created_at_utc")) or datetime.now(timezone.utc)
        expires = requested + timedelta(minutes=CAPTURE_DEMAND_TTL_MINUTES)
        for rung in ladder:
            token_id = str(rung.get("token_id") or "")
            condition_id = str(rung.get("condition_id") or "")
            if not token_id or not condition_id:
                alerts.append({
                    "status": "alert",
                    "reason": "capture_demand_ladder_identity_missing",
                    "packet_id": packet.get("packet_id"),
                    "bracket": rung.get("bracket"),
                })
                continue
            try:
                demand = CaptureDemand.create(
                    consumer_id=STRATEGY_INSTANCE,
                    strategy_key=CAPTURE_DEMAND_STRATEGY_KEY,
                    condition_id=condition_id,
                    token_id=token_id,
                    reason="core_carry_first_positive_full_ladder_tape",
                    priority="P0",
                    requested_at_utc=requested.isoformat(),
                    expires_at_utc=expires.isoformat(),
                    desired_transport="WS",
                    requested_checkpoints_seconds=(0, 60, 300, 900, 1800),
                    trigger_event_id=str(packet.get("packet_id") or ""),
                    metadata={
                        "city": trigger.get("city"),
                        "target_date": trigger.get("target_date"),
                        "bracket": rung.get("bracket"),
                        "market_id": rung.get("market_id"),
                        "ladder_scope": "all_yes_outcome_tokens",
                        "requested_window_seconds": CAPTURE_DEMAND_TTL_MINUTES * 60,
                        "stop_condition": "demand_expiry",
                        "active_token_budget": CAPTURE_DEMAND_MAX_LADDER_TOKENS,
                        "retention_owner": "canonical_market_books_ws_raw",
                    },
                )
            except ValueError as exc:
                alerts.append({
                    "status": "alert",
                    "reason": "capture_demand_contract_invalid",
                    "packet_id": packet.get("packet_id"),
                    "bracket": rung.get("bracket"),
                    "error": str(exc),
                })
                continue
            if demand.demand_id in existing:
                continue
            try:
                append_jsonl(journal, demand.to_dict())
            except OSError as exc:
                alerts.append({
                    "status": "alert",
                    "reason": "capture_demand_write_failed",
                    "packet_id": packet.get("packet_id"),
                    "demand_id": demand.demand_id,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            existing.add(demand.demand_id)
            written += 1
    for alert in alerts:
        try:
            append_jsonl(output_dir / "capture_demand_alerts.jsonl", alert)
        except OSError:
            pass
    return {"written": written, "alerts": alerts}


def line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def repo_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def publish_runtime_state(
    args: argparse.Namespace,
    output_dir: Path,
    summary: Mapping[str, Any],
    signal_summary: Mapping[str, Any],
) -> None:
    db_path = Path(args.runtime_db)
    now = str(summary.get("generated_at_utc") or utc_now())
    candidate_rows = int(
        signal_summary.get("checkpoint_candidates")
        or signal_summary.get("scores_written")
        or 0
    )
    plan_rows = int(summary.get("entry_plans") or 0) + int(
        summary.get("maker_lifecycle_plans") or 0
    )
    with sqlite3.connect(db_path, timeout=0.25) as conn:
        conn.execute("PRAGMA busy_timeout=250")
        runtime_state.push_runtime_state(
            conn,
            instance_id=STRATEGY_INSTANCE,
            process_status="running",
            health_status="healthy" if summary.get("status") == "ok" else "blocked",
            pid=os.getpid(),
            heartbeat_at_utc=now,
            last_tick_ts_utc=now,
            last_data_ts_utc=str(signal_summary.get("generated_at_utc") or now),
            latest_summary_ts_utc=now,
            latest_artifact_mtime_utc=now,
            heartbeat_age_min=0.0,
            candidate_rows=candidate_rows,
            plan_rows=plan_rows,
            live_order_rows=line_count(output_dir / "live_orders.jsonl"),
            paper_order_rows=line_count(output_dir / "paper_orders.jsonl"),
            telemetry_rows=line_count(output_dir / "summary_history.jsonl") + 1,
            live_enabled=int(bool(summary.get("live_enabled"))),
            summary_path=repo_path(output_dir / "latest_summary.json"),
            primary_journal_path=repo_path(output_dir / "live_orders.jsonl"),
            blocker_count=0,
            blockers_json=[],
            summary_json=dict(summary),
            refreshed_at_utc=now,
        )
        conn.commit()


def publish_runtime_state_best_effort(
    args: argparse.Namespace,
    output_dir: Path,
    summary: dict[str, Any],
    signal_summary: Mapping[str, Any],
) -> None:
    """Keep telemetry contention from changing the trading-loop result."""

    try:
        publish_runtime_state(args, output_dir, summary, signal_summary)
    except sqlite3.OperationalError as exc:
        if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
            summary["status"] = "runtime_state_error"
            summary["runtime_state_error"] = f"{type(exc).__name__}: {exc}"
            return
        summary["runtime_state_publish_status"] = "deferred_db_busy"
        summary["runtime_state_publish_error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001
        summary["status"] = "runtime_state_error"
        summary["runtime_state_error"] = f"{type(exc).__name__}: {exc}"
    else:
        summary["runtime_state_publish_status"] = "published"


def live_order_id(row: Mapping[str, Any]) -> str:
    response = row.get("exchange_response") if isinstance(row.get("exchange_response"), Mapping) else {}
    place = response.get("place") if isinstance(response.get("place"), Mapping) else {}
    for payload in (row, place, response):
        for key in (
            "venue_order_id",
            "order_id",
            "orderID",
            "clob_order_id",
            "root_order_id",
            "id",
        ):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
    return ""


def signal_id(row: Mapping[str, Any]) -> str:
    return "current-yes-core-carry-" + stable_hash(
        {
            "strategy_instance": STRATEGY_INSTANCE,
            "city": str(row.get("city") or ""),
            "target_date": str(row.get("target_date") or ""),
        }
    )


def latest_rows_by_checkpoint(path: Path) -> dict[str, dict[str, Any]]:
    index_path = weather_epoch_index_path(path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(index_path, timeout=30.0) as conn:
        _prepare_weather_epoch_index(conn)
        _refresh_weather_epoch_index(path, conn)
        return {
            str(checkpoint_key): json.loads(payload_json)
            for checkpoint_key, payload_json in conn.execute(
                "SELECT checkpoint_key, payload_json FROM latest_checkpoint_rows"
            )
        }


WEATHER_EPOCH_INDEX_SCHEMA_VERSION = "weather_epoch_index_v2"


def weather_epoch_index_path(source_path: Path) -> Path:
    if source_path.name == "state_decisions.jsonl":
        return source_path.with_name("runtime_state_index.sqlite3")
    return source_path.with_name(f"{source_path.stem}_index.sqlite3")


def _weather_epoch_index_metadata(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in conn.execute("SELECT key, value FROM index_metadata")
    }


def _prepare_weather_epoch_index(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS index_metadata "
        "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS latest_weather_epochs (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            decision_snapshot_ts_utc TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (city, target_date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS latest_checkpoint_rows (
            checkpoint_key TEXT PRIMARY KEY,
            decision_snapshot_ts_utc TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )


def _set_weather_epoch_index_metadata(
    conn: sqlite3.Connection, values: Mapping[str, Any]
) -> None:
    conn.executemany(
        """
        INSERT INTO index_metadata(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ((str(key), str(value)) for key, value in values.items()),
    )


def _refresh_weather_epoch_index(
    source_path: Path, conn: sqlite3.Connection
) -> None:
    try:
        stat = source_path.stat()
    except FileNotFoundError:
        conn.execute("DELETE FROM latest_weather_epochs")
        conn.execute("DELETE FROM latest_checkpoint_rows")
        conn.execute("DELETE FROM index_metadata")
        conn.commit()
        return

    metadata = _weather_epoch_index_metadata(conn)
    source_identity = f"{stat.st_dev}:{stat.st_ino}"
    try:
        indexed_offset = int(metadata.get("indexed_offset", "0"))
    except ValueError:
        indexed_offset = 0
    reset_required = (
        metadata.get("schema_version") != WEATHER_EPOCH_INDEX_SCHEMA_VERSION
        or metadata.get("source_identity") != source_identity
        or stat.st_size < indexed_offset
    )
    if reset_required:
        conn.execute("DELETE FROM latest_weather_epochs")
        conn.execute("DELETE FROM latest_checkpoint_rows")
        conn.execute("DELETE FROM index_metadata")
        indexed_offset = 0

    complete_offset = indexed_offset
    with source_path.open("rb") as handle:
        handle.seek(indexed_offset)
        while True:
            raw = handle.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):
                break
            complete_offset = handle.tell()
            try:
                row = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict):
                continue
            city = str(row.get("city") or "")
            target_date = str(row.get("target_date") or "")
            stamp = parse_utc(
                row.get("decision_snapshot_ts_utc") or row.get("created_at_utc")
            )
            if not city or not target_date or stamp is None:
                continue
            stamp_text = stamp.isoformat()
            conn.execute(
                """
                INSERT INTO latest_weather_epochs(
                    city, target_date, decision_snapshot_ts_utc, payload_json
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(city, target_date) DO UPDATE SET
                    decision_snapshot_ts_utc = excluded.decision_snapshot_ts_utc,
                    payload_json = excluded.payload_json
                WHERE excluded.decision_snapshot_ts_utc
                    > latest_weather_epochs.decision_snapshot_ts_utc
                """,
                (
                    city,
                    target_date,
                    stamp_text,
                    json.dumps(row, ensure_ascii=False, sort_keys=True),
                ),
            )
            checkpoint_key = str(row.get("checkpoint_key") or "")
            if checkpoint_key:
                conn.execute(
                    """
                    INSERT INTO latest_checkpoint_rows(
                        checkpoint_key, decision_snapshot_ts_utc, payload_json
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(checkpoint_key) DO UPDATE SET
                        decision_snapshot_ts_utc = excluded.decision_snapshot_ts_utc,
                        payload_json = excluded.payload_json
                    WHERE excluded.decision_snapshot_ts_utc
                        >= latest_checkpoint_rows.decision_snapshot_ts_utc
                    """,
                    (
                        checkpoint_key,
                        stamp_text,
                        json.dumps(row, ensure_ascii=False, sort_keys=True),
                    ),
                )

    _set_weather_epoch_index_metadata(
        conn,
        {
            "schema_version": WEATHER_EPOCH_INDEX_SCHEMA_VERSION,
            "source_path": str(source_path.resolve()),
            "source_identity": source_identity,
            "indexed_offset": complete_offset,
        },
    )
    conn.commit()


def latest_weather_epochs(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    index_path = weather_epoch_index_path(path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(index_path, timeout=30.0) as conn:
        _prepare_weather_epoch_index(conn)
        _refresh_weather_epoch_index(path, conn)
        return {
            (str(city), str(target_date)): json.loads(payload_json)
            for city, target_date, payload_json in conn.execute(
                "SELECT city, target_date, payload_json FROM latest_weather_epochs"
            )
        }


def latest_scores_by_city_day(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    return latest_weather_epochs(path)


def maker_signal_ids_with_venue_exposure(path: Path) -> set[str]:
    """Conservatively identify signals whose five-share maker sleeve existed.

    A submitted/filled maker or an authenticated positive matched quantity owns
    some or all of the sleeve.  Post-update re-arm is therefore restricted to
    entry-time clock skips; partial/cancelled maker reconciliation is a separate
    lifecycle problem and cannot silently create extra exposure here.
    """

    exposed: set[str] = set()
    for row in iter_jsonl(path):
        if not bool(row.get("maker_only")):
            continue
        signal = str(row.get("signal_id") or "")
        response = row.get("exchange_response") if isinstance(
            row.get("exchange_response"), Mapping
        ) else {}
        authoritative = response.get("authoritative_order_state") if isinstance(
            response.get("authoritative_order_state"), Mapping
        ) else {}
        matched = finite(authoritative.get("matched_shares")) or 0.0
        status = str(row.get("status") or "").lower()
        if signal and (live_order_id(row) or matched > 0 or status in {"submitted", "filled"}):
            exposed.add(signal)
    return exposed


def post_update_shadow_attempts(path: Path) -> dict[str, dict[str, Any]]:
    attempts: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(path):
        signal = str(row.get("signal_id") or "")
        if (
            signal
            and str(row.get("status") or "") == "planned"
            and (finite(row.get("maker_shadow_revalidation_shares")) or 0.0) > 0
            and str(row.get("maker_clock_status") or "")
            == "pre_source_report_blackout"
        ):
            attempts[signal] = row
    return attempts


def evaluate_post_update_maker_rearm(
    original: Mapping[str, Any],
    latest: Mapping[str, Any],
    quote: Mapping[str, Any],
    *,
    now: datetime,
    order_ttl_min: float,
    maker_exposure_exists: bool,
) -> dict[str, Any]:
    """Evaluate one zero-notional, same-budget post-update maker re-arm."""

    original_epoch = parse_utc(
        original.get("original_source_report_ts_utc")
        or original.get("data_epoch_ref")
        or original.get("source_report_ts_utc")
    )
    latest_epoch = parse_utc(latest.get("source_report_ts_utc"))
    original_token = str(original.get("token_id") or "")
    latest_token = str(
        latest.get("current_yes_token_id") or latest.get("token_id") or ""
    )
    original_bracket = str(original.get("bracket") or "")
    latest_bracket = str(latest.get("current_bracket") or "")
    probability = finite(latest.get("model_probability_hold"))
    best_bid = finite(quote.get("bid")) or 0.0
    best_ask = finite(quote.get("ask")) or 0.0
    tick = finite(quote.get("tick_size")) or finite(
        latest.get("current_yes_tick_size")
    ) or 0.001
    reasons: list[str] = []
    if maker_exposure_exists:
        reasons.append("maker_sleeve_already_has_venue_exposure")
    if original_epoch is None or latest_epoch is None:
        reasons.append("source_report_epoch_missing")
    elif latest_epoch <= original_epoch:
        reasons.append("first_new_source_report_not_seen")
    if not original_token or latest_token != original_token:
        reasons.append("exact_bracket_token_changed")
    if not original_bracket or latest_bracket != original_bracket:
        reasons.append("exact_bracket_changed")
    if not bool(latest.get("checkpoint_eligible")):
        reasons.append("checkpoint_not_eligible")
    if str(latest.get("model_input_support_status") or "") not in {
        "ok",
        "within_training_support",
    }:
        reasons.append("model_input_support_not_ok")
    if probability is None:
        reasons.append("model_probability_unavailable")
    if str(quote.get("book_status") or "") != "ok" or best_bid <= 0 or best_ask <= best_bid:
        reasons.append("fresh_two_sided_book_unavailable")

    clock = maker_clock_assessment(latest, now=now, order_ttl_min=order_ttl_min)
    if not bool(clock.get("maker_live_eligible")):
        reasons.append("post_update_source_clock_not_eligible")

    cap = 0.0
    limit = 0.0
    if probability is not None and best_ask > best_bid and tick > 0:
        cap = maker_edge_price_cap(
            best_ask=best_ask,
            tick_size=tick,
            model_probability=probability,
        )
        limit = maker_resting_price(
            best_bid=best_bid,
            best_ask=best_ask,
            tick_size=tick,
            price_cap=cap,
        )
    if limit <= 0:
        reasons.append("positive_edge_post_only_price_unavailable")

    reasons = sorted(set(reasons))
    would_rearm = not reasons
    return {
        "record_type": "current_yes_core_carry_post_update_maker_revalidation",
        "policy_version": "same_budget_first_post_update_rearm_shadow_v1",
        "created_at_utc": now.isoformat(timespec="seconds"),
        "signal_id": str(original.get("signal_id") or ""),
        "city": str(original.get("city") or latest.get("city") or ""),
        "target_date": str(
            original.get("target_date") or latest.get("target_date") or ""
        ),
        "original_checkpoint_key": str(original.get("checkpoint_key") or ""),
        "revalidation_checkpoint_key": str(latest.get("checkpoint_key") or ""),
        "original_source_report_ts_utc": (
            original_epoch.isoformat(timespec="seconds") if original_epoch else ""
        ),
        "revalidation_source_report_ts_utc": (
            latest_epoch.isoformat(timespec="seconds") if latest_epoch else ""
        ),
        "token_id": latest_token,
        "bracket": latest_bracket,
        "model_probability_hold": probability,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "tick_size": tick,
        "maker_price_cap": round(cap, 6),
        "maker_limit_price": round(limit, 6),
        "maker_rearm_shares": FROZEN_MAKER_SHARES if would_rearm else 0.0,
        "maker_exposure_exists": maker_exposure_exists,
        "would_rearm": would_rearm,
        "revalidation_status": "positive_ev_rearm" if would_rearm else "blocked",
        "revalidation_reasons": reasons,
        "zero_notional": True,
        "notional": 0.0,
        "trade_intent_created": False,
        "order_created": False,
        **clock,
    }


def collect_post_update_maker_revalidations(
    args: argparse.Namespace,
    output_dir: Path,
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    """Record the first observable post-update decision; never execute it."""

    journal = output_dir / "maker_post_update_revalidations.jsonl"
    completed_signals = {
        str(row.get("signal_id") or "") for row in iter_jsonl(journal)
    }
    attempts = post_update_shadow_attempts(output_dir / "entry_attempts.jsonl")
    latest_scores = latest_scores_by_city_day(output_dir / "pre_live_scores.jsonl")
    exposed = maker_signal_ids_with_venue_exposure(output_dir / "live_orders.jsonl")
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for signal, original in attempts.items():
        if signal in completed_signals:
            continue
        key = (str(original.get("city") or ""), str(original.get("target_date") or ""))
        latest = latest_scores.get(key)
        original_epoch = parse_utc(
            original.get("original_source_report_ts_utc")
            or original.get("data_epoch_ref")
        )
        latest_epoch = parse_utc((latest or {}).get("source_report_ts_utc"))
        if (
            latest is None
            or original_epoch is None
            or latest_epoch is None
            or latest_epoch <= original_epoch
        ):
            continue
        candidates.append((original, latest))

    if not candidates:
        return []
    records: list[dict[str, Any]] = []
    with market_httpx_client(args.book_proxy, timeout=float(args.book_timeout_sec)) as client:
        for original, latest in candidates:
            token = str(latest.get("current_yes_token_id") or latest.get("token_id") or "")
            quote = weather_state._fetch_token_book(client, token) if token else {}  # noqa: SLF001
            record = evaluate_post_update_maker_rearm(
                original,
                latest,
                quote,
                now=now,
                order_ttl_min=float(args.order_ttl_min),
                maker_exposure_exists=str(original.get("signal_id") or "") in exposed,
            )
            record["revalidation_id"] = "maker-rearm-shadow-" + stable_hash(
                {
                    "signal_id": record["signal_id"],
                    "source_report_ts_utc": record["revalidation_source_report_ts_utc"],
                }
            )
            append_jsonl(journal, record)
            records.append(record)
    return records


def maker_lifecycle_root(row: Mapping[str, Any]) -> str:
    root_created = str(row.get("maker_lifecycle_root_created_at_utc") or "")
    signal = str(row.get("signal_id") or "")
    comparison = str(row.get("comparison_group_id") or "")
    return "|".join((signal, comparison, root_created))


def normalized_maker_lifecycle_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Honor confirmed cancels written by the legacy projection bug.

    Older runtimes appended an authenticated, confirmed cancel response with
    ``status=submitted``.  Keep the source journal append-only, but normalize
    that terminal evidence while selecting the current lifecycle head.
    """

    normalized = dict(row)
    if str(row.get("status") or "").lower() != "submitted":
        return normalized
    response = (
        row.get("exchange_response")
        if isinstance(row.get("exchange_response"), Mapping)
        else {}
    )
    cancel = response.get("cancel") if isinstance(response.get("cancel"), Mapping) else {}
    raw = cancel.get("raw_response") if isinstance(cancel.get("raw_response"), Mapping) else {}
    cancel_status = str(cancel.get("status") or raw.get("status") or "").lower()
    if cancel_status in {"cancelled", "canceled", "expired"}:
        normalized["status"] = "cancelled" if cancel_status != "expired" else "expired"
    return normalized


def maker_lifecycle_heads(path: Path) -> list[dict[str, Any]]:
    """Return the latest journal row for every maker intent."""

    heads: dict[str, tuple[datetime, int, dict[str, Any]]] = {}
    for index, row in enumerate(iter_jsonl(path)):
        if (
            str(row.get("strategy_instance") or "") != STRATEGY_INSTANCE
            or not bool(row.get("maker_only"))
        ):
            continue
        row = normalized_maker_lifecycle_row(row)
        root = maker_lifecycle_root(row)
        created = parse_utc(row.get("created_at_utc"))
        if not root or created is None:
            continue
        prior = heads.get(root)
        if prior is None or (created, index) > (prior[0], prior[1]):
            heads[root] = (created, index, row)
    return [item[2] for item in heads.values()]


def execution_journal_order_states(path: Path) -> dict[str, dict[str, Any]]:
    """Recover venue-order terminal state from side-effect outcomes.

    The execution journal is written before the legacy ``live_orders`` projection.
    A process interruption between those writes must not leave an already-cancelled
    order looking active forever.
    """

    states: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(path):
        if str(row.get("event_type") or "") != "outcome":
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        kind = str(payload.get("kind") or "")
        status = str(payload.get("status") or "").lower()
        raw = payload.get("raw_response") if isinstance(payload.get("raw_response"), Mapping) else {}
        recorded_at = str(row.get("recorded_at_utc") or "")
        if kind == "submit" and status in {"submitted", "accepted", "filled", "reconciled"}:
            order_id = str(
                payload.get("venue_order_id")
                or raw.get("orderID")
                or raw.get("order_id")
                or raw.get("id")
                or ""
            )
            if order_id:
                states[order_id] = {
                    "status": "filled" if status == "filled" else "submitted",
                    "recorded_at_utc": recorded_at,
                    "journal_payload": dict(payload),
                }
        if kind not in {"cancel", "cancel_before_replacement"}:
            continue
        if status not in {"cancelled", "canceled", "expired", "reconciled"}:
            continue
        cancelled = raw.get("canceled") or raw.get("cancelled") or ()
        for value in cancelled:
            order_id = str(value or "")
            if order_id:
                states[order_id] = {
                    "status": "cancelled",
                    "recorded_at_utc": recorded_at,
                    "journal_payload": dict(payload),
                }
    return states


def recover_journal_terminal_makers(output_dir: Path) -> int:
    """Project journal-confirmed terminal heads into the legacy order stream."""

    live_orders = output_dir / "live_orders.jsonl"
    states = execution_journal_order_states(output_dir / "execution_journal.jsonl")
    existing_execution_ids = {
        str(row.get("execution_id") or "") for row in iter_jsonl(live_orders)
    }
    written = 0
    for head in maker_lifecycle_heads(live_orders):
        if str(head.get("status") or "") != "submitted":
            continue
        order_id = live_order_id(head)
        evidence = states.get(order_id)
        if not order_id or not evidence or evidence["status"] not in {"cancelled", "filled"}:
            continue
        execution_id = "journal-recovery-" + stable_hash(
            {"order_id": order_id, "terminal_status": evidence["status"]}
        )
        if execution_id in existing_execution_ids:
            continue
        terminal = {
            **dict(head),
            "record_type": "weather_edge_live_order",
            "created_at_utc": evidence["recorded_at_utc"] or utc_now(),
            "status": evidence["status"],
            "child_order_role": "core_carry_maker_terminal",
            "execution_action": "core_carry_maker_terminal",
            "execution_id": execution_id,
            "plan_id": "plan-" + execution_id,
            "replacement_of_order_id": order_id,
            "source_order_id": order_id,
            "exchange_response": {
                "quote_status": evidence["status"],
                "quote_reason": "execution_journal_terminal_recovery",
                "authoritative_execution_journal": evidence["journal_payload"],
            },
        }
        append_jsonl(live_orders, terminal)
        existing_execution_ids.add(execution_id)
        written += 1
    return written


def retryable_maker_post_failure(row: Mapping[str, Any]) -> bool:
    if str(row.get("status") or "") != "error":
        return False
    response = row.get("exchange_response") if isinstance(row.get("exchange_response"), Mapping) else {}
    classification = str(response.get("error_classification") or "")
    return classification in RETRYABLE_MAKER_POST_FAILURES


def family_live_order_files(output_dir: Path) -> tuple[Path, ...]:
    return (*LEGACY_FAMILY_LIVE_ORDER_FILES, output_dir / "live_orders.jsonl")


def submitted_city_days(paths: Iterable[Path]) -> set[tuple[str, str]]:
    return {
        (str(row.get("city") or ""), str(row.get("target_date") or ""))
        for path in paths
        for row in iter_jsonl(path)
        if str(row.get("status") or "") == "submitted"
        and str(row.get("strategy_instance") or "")
        != near_core_maker_probe.STRATEGY_INSTANCE
        and str(row.get("city") or "")
        and str(row.get("target_date") or "")
    }


def attempted_signal_ids(output_dir: Path) -> set[str]:
    attempted = {
        str(row.get("signal_id") or "")
        for row in iter_jsonl(output_dir / "entry_attempts.jsonl")
        if str(row.get("signal_id") or "")
        and str(row.get("status") or "") == "blocked"
        and str(row.get("reason") or "") not in TRANSIENT_ENTRY_BLOCKERS
    }
    attempted.update(
        str(row.get("signal_id") or "")
        for row in iter_jsonl(output_dir / "live_orders.jsonl")
        if str(row.get("signal_id") or "")
    )
    return attempted


def daily_family_usage(paths: Iterable[Path], now: datetime) -> tuple[int, float]:
    day = now.astimezone(BJ).date()
    city_days: set[tuple[str, str]] = set()
    posted = 0.0
    for path in paths:
        for row in iter_jsonl(path):
            if str(row.get("status") or "") != "submitted":
                continue
            if (
                str(row.get("strategy_instance") or "")
                == near_core_maker_probe.STRATEGY_INSTANCE
            ):
                continue
            created = parse_utc(row.get("created_at_utc"))
            if created is None or created.astimezone(BJ).date() != day:
                continue
            if str(row.get("execution_action") or "").startswith("core_carry_maker_"):
                continue
            city = str(row.get("city") or "")
            target_date = str(row.get("target_date") or "")
            if city and target_date:
                city_days.add((city, target_date))
            posted += finite(row.get("posted_notional")) or finite(row.get("notional")) or 0.0
    return len(city_days), posted


def entry_cost_reservation(args: argparse.Namespace, row: Mapping[str, Any]) -> float:
    """Conservatively reserve both children at the current taker ask."""

    ask = finite(row.get("current_yes_ask")) or 1.0
    return (float(args.taker_shares) + float(args.maker_shares)) * ask


def entry_plan_cost_reservation(plans: Iterable[Mapping[str, Any]]) -> float:
    """Reserve only children that can actually reach the executor."""

    return sum(finite(plan.get("order_notional_cap")) or 0.0 for plan in plans)


def max_live_child_notional_usd(args: argparse.Namespace) -> float:
    """Maximum principal for one child at the binary-market price ceiling."""

    return max(
        float(args.taker_shares),
        float(args.maker_shares),
        float(getattr(args, "near_core_maker_shares", 0.0)),
    )


def maker_profile_parameter(name: str) -> float:
    profile = get_execution_profile(EXECUTION_PROFILE)
    value = finite(profile.fixed_parameters.get(name))
    if value is None:
        raise RuntimeError(f"missing numeric maker profile parameter: {name}")
    return value


def maker_max_reprices() -> int:
    profile = get_execution_profile(EXECUTION_PROFILE)
    maker_leg = next((leg for leg in profile.legs if leg.role == "maker"), None)
    if maker_leg is None or maker_leg.max_reprices is None:
        raise RuntimeError("core carry maker profile requires a finite max_reprices")
    return int(maker_leg.max_reprices)


def maker_edge_price_cap(
    *,
    best_ask: float,
    tick_size: float,
    model_probability: float,
) -> float:
    retained_edge = maker_profile_parameter("retained_edge")
    improvement_ticks = maker_profile_parameter("minimum_taker_improvement_ticks")
    raw_cap = min(
        model_probability - retained_edge,
        best_ask - improvement_ticks * tick_size,
    )
    if raw_cap <= 0 or tick_size <= 0:
        return 0.0
    return math.floor((raw_cap + 1e-12) / tick_size) * tick_size


def maker_clock_assessment(
    row: Mapping[str, Any],
    *,
    now: datetime,
    order_ttl_min: float,
) -> dict[str, Any]:
    profile = get_execution_profile(EXECUTION_PROFILE)
    source_epoch = parse_utc(row.get("source_report_ts_utc"))
    cadence_min = finite(row.get("observation_cadence_min"))
    common = {
        "maker_clock_basis": str(profile.fixed_parameters.get("clock_basis") or ""),
        "maker_post_update_live_rearm": bool(
            profile.fixed_parameters.get("post_update_live_rearm")
        ),
        "maker_post_update_shadow_revalidation": bool(
            profile.fixed_parameters.get("post_update_shadow_revalidation")
        ),
        "post_update_reprice_required": False,
    }
    if source_epoch is None or cadence_min is None or cadence_min <= 0:
        return {
            **common,
            "maker_clock_status": "invalid_source_clock",
            "maker_live_eligible": False,
            "maker_live_skip_reason": "missing_source_epoch_or_cadence",
            "maker_shadow_policy": "not_scorable_missing_source_clock",
            "seconds_to_next_source_report": None,
        }
    next_update = source_epoch + timedelta(minutes=cadence_min)
    cancel_before_update = next_update - timedelta(seconds=profile.cancel_buffer_sec)
    ttl_deadline = now + timedelta(minutes=order_ttl_min)
    deadline = min(cancel_before_update, ttl_deadline)
    eligible = deadline > now
    seconds_to_next_report = (next_update - now).total_seconds()
    clock_fields = {
        **common,
        "data_update_source": str(row.get("obs_source") or "weather_observation"),
        "data_epoch_ref": str(row.get("source_report_ts_utc") or ""),
        "data_epoch_ts_utc": source_epoch.isoformat(timespec="seconds"),
        "next_data_update_due_utc": next_update.isoformat(timespec="seconds"),
        "next_source_report_due_utc": next_update.isoformat(timespec="seconds"),
        "cancel_before_data_update_utc": cancel_before_update.isoformat(timespec="seconds"),
        "cancel_before_source_report_utc": cancel_before_update.isoformat(timespec="seconds"),
        "cancel_buffer_sec": profile.cancel_buffer_sec,
        "cancel_reason": "pre_data_update",
        "maker_clock_guard_reason": "pre_source_report",
        "seconds_to_next_source_report": round(seconds_to_next_report, 6),
        "maker_clock_status": (
            "eligible_before_source_report" if eligible else "pre_source_report_blackout"
        ),
        "maker_live_eligible": eligible,
        "maker_live_skip_reason": "" if eligible else "source_report_deadline_elapsed",
        "maker_shadow_policy": (
            "none_live_maker_active"
            if eligible
            else "first_post_update_positive_ev_replay_only_v1"
        ),
    }
    if deadline <= now:
        return clock_fields
    return {
        **clock_fields,
        "expires_at_utc": deadline.isoformat(timespec="seconds"),
        "maker_lifecycle_deadline_utc": deadline.isoformat(timespec="seconds"),
    }


def maker_deadline_fields(
    row: Mapping[str, Any],
    *,
    now: datetime,
    order_ttl_min: float,
) -> dict[str, Any] | None:
    assessment = maker_clock_assessment(
        row,
        now=now,
        order_ttl_min=order_ttl_min,
    )
    return assessment if bool(assessment.get("maker_live_eligible")) else None


def staged_maker_resting_price(
    order: Mapping[str, Any],
    *,
    best_bid: float,
    best_ask: float,
    tick_size: float,
    price_cap: float,
    now: datetime,
) -> tuple[float, str]:
    base = maker_resting_price(
        best_bid=best_bid,
        best_ask=best_ask,
        tick_size=tick_size,
        price_cap=price_cap,
    )
    root_created = parse_utc(
        order.get("maker_lifecycle_root_created_at_utc") or order.get("created_at_utc")
    )
    age_sec = max(0.0, (now - root_created).total_seconds()) if root_created else 0.0
    midpoint_after = maker_profile_parameter("stage_midpoint_after_sec")
    near_ask_after = maker_profile_parameter("stage_near_ask_after_sec")
    if age_sec >= near_ask_after:
        target = min(best_ask - tick_size, price_cap)
        stage = "near_ask"
    elif age_sec >= midpoint_after:
        midpoint = math.floor((((best_bid + best_ask) / 2.0) + 1e-12) / tick_size) * tick_size
        target = min(max(base, midpoint), best_ask - tick_size, price_cap)
        stage = "midpoint"
    else:
        target = base
        stage = "queue"
    if target <= 0 or target >= best_ask:
        return 0.0, stage
    return round(target, 6), stage


def base_plan_fields(
    row: Mapping[str, Any],
    *,
    child_order_role: str,
    shares: float,
    live_enabled: bool,
    now: datetime,
    order_ttl_min: float,
) -> dict[str, Any]:
    bid = finite(row.get("current_yes_bid")) or 0.0
    ask = finite(row.get("current_yes_ask")) or 0.0
    tick = finite(row.get("current_yes_tick_size")) or 0.001
    probability = finite(row.get("model_probability_hold")) or 0.0
    maker_cap = maker_edge_price_cap(
        best_ask=ask,
        tick_size=tick,
        model_probability=probability,
    )
    sid = signal_id(row)
    maker = child_order_role == "maker"
    limit = (
        maker_resting_price(
            best_bid=bid,
            best_ask=ask,
            tick_size=tick,
            price_cap=maker_cap,
        )
        if maker
        else ask
    )
    maker_clock = (
        maker_clock_assessment(row, now=now, order_ttl_min=order_ttl_min)
        if maker
        else {}
    )
    maker_timing = maker_clock if bool(maker_clock.get("maker_live_eligible")) else None
    expires = now + timedelta(minutes=order_ttl_min)
    return {
        "strategy": "weather_edge_v1",
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_INSTANCE,
        "config_id": CONFIG_ID,
        "strategy_family": "reheat_risk.current_yes",
        "decision_mode": "frozen_core_v3_first_positive_ten_share_taker_ev",
        "execution_mode": "tiny_live_split_10_taker_5_maker",
        "execution_profile": EXECUTION_PROFILE,
        "comparison_group_id": stable_hash({"signal_id": sid, "token_id": row.get("token_id")}),
        "city": str(row.get("city") or ""),
        "city_pool": "all_canonical_weather_state_v2",
        "target_date": str(row.get("target_date") or ""),
        "market_id": str(row.get("current_market_id") or ""),
        "question": str(row.get("current_question") or ""),
        "condition_id": str(row.get("condition_id") or row.get("current_condition_id") or ""),
        "bracket": str(row.get("current_bracket") or ""),
        "token_id": str(row.get("token_id") or row.get("current_yes_token_id") or ""),
        "signal_side": "BUY_YES",
        "order_side": "BUY",
        "child_order_role": child_order_role,
        "execution_policy": (
            "current_yes_residual_carry_maker_v2"
            if maker
            else "current_yes_residual_carry_taker_v1"
        ),
        "order_lifecycle_policy": (
            "maker_staged_chase_until_pre_data_update_or_ttl_v2"
            if maker
            else "taker_now"
        ),
        "maker_only": maker,
        "allow_duplicate_signal_id": True,
        "market_price": round(ask, 6),
        "best_bid": round(bid, 6),
        "best_ask": round(ask, 6),
        "limit_price": round(limit, 6),
        "quote_best_bid": round(bid, 6),
        "quote_best_ask": round(ask, 6),
        "quote_tick_size": round(tick, 6),
        "quote_mode": (
            "fresh_bid_improve_one_tick_post_only_retained_edge_capped"
            if maker
            else "fresh_full_ladder_taker_ev_recheck"
        ),
        "size": round(shares, 6),
        "fixed_order_shares": round(shares, 6),
        "max_order_shares": round(shares, 6),
        "notional": round(shares * max(0.0, limit), 6),
        "order_notional_cap": round(shares * max(0.0, maker_cap if maker else ask), 6),
        "sizing_mode": "fixed_shares",
        "paper_enabled": True,
        "live_enabled": bool(live_enabled),
        "model_token_probability": round(probability, 12),
        "required_quote_edge": 0.0,
        "model_version": MODEL_VERSION,
        "artifact_hash": str(row.get("artifact_hash") or ""),
        "checkpoint_key": str(row.get("checkpoint_key") or ""),
        "decision_snapshot_ts_utc": str(row.get("decision_snapshot_ts_utc") or ""),
        "source_snapshot_file": str(row.get("snapshot_file") or ""),
        "source_report_ts_utc": str(row.get("source_report_ts_utc") or ""),
        "obs_status": str(row.get("obs_status") or ""),
        "station_gap_state": str(row.get("station_gap_state") or ""),
        "expires_at_utc": (
            maker_timing["expires_at_utc"]
            if maker_timing
            else expires.isoformat(timespec="seconds")
        ),
        "maker_price_cap": round(maker_cap, 6) if maker else 0.0,
        "maker_price_cap_policy": (
            "model_probability_retained_edge_and_taker_improvement"
            if maker
            else ""
        ),
        "maker_retained_edge": (
            maker_profile_parameter("retained_edge") if maker else 0.0
        ),
        "maker_minimum_taker_improvement_ticks": (
            maker_profile_parameter("minimum_taker_improvement_ticks")
            if maker
            else 0.0
        ),
        "maker_lifecycle_root_created_at_utc": now.isoformat(timespec="seconds") if maker else "",
        "maker_lifecycle_deadline_utc": (
            maker_timing["maker_lifecycle_deadline_utc"]
            if maker_timing
            else ""
        ),
        "maker_lifecycle_reprice_count": 0,
        **maker_clock,
    }


def build_entry_plans(
    row: Mapping[str, Any],
    *,
    live_enabled: bool,
    now: datetime,
    taker_shares: float,
    maker_shares: float,
    order_ttl_min: float,
) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    sid = signal_id(row)
    profile = get_execution_profile(EXECUTION_PROFILE)
    allocation = allocate_profile_shares(
        profile=profile,
        total_shares=taker_shares + maker_shares,
        leg_share_overrides={"taker": taker_shares, "maker": maker_shares},
    )
    for role, allocated_shares in allocation:
        shares = float(allocated_shares)
        fields = base_plan_fields(
            row,
            child_order_role=role,
            shares=shares,
            live_enabled=live_enabled,
            now=now,
            order_ttl_min=order_ttl_min,
        )
        if role == "maker" and (
            not bool(fields.get("maker_live_eligible"))
            or (finite(fields["limit_price"]) or 0.0) <= 0
        ):
            continue
        plans.append(
            {
                "record_type": "weather_edge_trade_plan",
                "plan_id": "plan-" + stable_hash({**fields, "signal_id": sid, "role": role}),
                "signal_id": sid,
                "opportunity_id": sid,
                "created_at_utc": now.isoformat(timespec="seconds"),
                "status": "accepted",
                "risk_status": "passed",
                "risk_reason": "",
                **fields,
            }
        )
    if plans:
        compatibility = build_core_carry_legacy_plan_compatibility(legacy_plans=plans)
        for plan, intent in zip(plans, compatibility.intents):
            plan.update(
                {
                    "execution_schema_version": intent.execution_schema_version,
                    "resolved_execution_profile": intent.resolved_execution_profile,
                    "execution_config_id": intent.execution_config_id,
                    "plan_dedupe_key": intent.plan_dedupe_key,
                    "live_exposure_key": intent.live_exposure_key,
                }
            )
    return plans


def continuous_market_rescores(
    args: argparse.Namespace,
    output_dir: Path,
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    """Re-score fresh books while the latest hourly Core thesis is armed."""

    scores = latest_scores_by_city_day(output_dir / "pre_live_scores.jsonl")
    epochs = latest_weather_epochs(output_dir / "state_decisions.jsonl")
    artifact = load_artifact(args.artifact)
    rows: list[dict[str, Any]] = []
    def checkpoint_clock(item: tuple[tuple[str, str], dict[str, Any]]) -> datetime:
        _city_day, checkpoint = item
        return parse_utc(
            checkpoint.get("decision_snapshot_ts_utc")
            or checkpoint.get("created_at_utc")
        ) or datetime.min.replace(tzinfo=timezone.utc)

    all_candidates = sorted(scores.items(), key=checkpoint_clock, reverse=True)
    max_books = max(0, int(args.continuous_market_max_books_per_cycle))
    candidates = all_candidates[:max_books]
    skipped = all_candidates[max_books:]
    append_jsonl(
        output_dir / "continuous_market_rescore_cycles.jsonl",
        {
            "record_type": "current_yes_core_carry_continuous_rescore_cycle",
            "created_at_utc": now.isoformat(timespec="milliseconds"),
            "candidate_city_days": len(all_candidates),
            "selected_city_days": len(candidates),
            "skipped_city_days": len(skipped),
            "selection_policy": "newest_hourly_thesis_first",
            "skipped_city_day_keys": [
                f"{city}|{target_date}" for (city, target_date), _row in skipped
            ],
        },
    )
    with market_httpx_client(args.book_proxy, timeout=float(args.book_timeout_sec)) as client:
        for city_day, checkpoint in candidates:
            if checkpoint.get("model_probability_hold") is None:
                continue
            # The hourly row is the frozen economic thesis. The state stream
            # may intentionally be sparse, so inherit the entire checkpoint
            # before overlaying only its latest weather/market identity fields.
            state = {**checkpoint, **dict(epochs.get(city_day) or {})}
            checkpoint_token = str(
                checkpoint.get("current_yes_token_id") or checkpoint.get("token_id") or ""
            )
            current_token = str(
                state.get("current_yes_token_id") or state.get("token_id") or ""
            )
            checkpoint_bracket = str(
                checkpoint.get("current_bracket") or checkpoint.get("bracket") or ""
            )
            current_bracket = str(
                state.get("current_bracket") or state.get("bracket") or ""
            )
            if (
                not checkpoint_token
                or current_token != checkpoint_token
                or current_bracket != checkpoint_bracket
            ):
                continue
            freshness_ok, _freshness_reason = signal_runner.observation_freshness_valid(state)
            if not freshness_ok:
                continue
            book = signal_runner.fetch_full_book(client, current_token)
            book_identity = "direct-clob-" + stable_hash(
                {
                    "token_id": current_token,
                    "fetched_at_utc": book.get("fetched_at_utc"),
                    "bids": book.get("bids") or [],
                    "asks": book.get("asks") or [],
                }
            )
            scored_input = {
                **state,
                "checkpoint_key": str(checkpoint.get("checkpoint_key") or ""),
                "checkpoint_eligible": True,
                "current_yes_bid": book.get("bid"),
                "current_yes_ask": book.get("ask"),
                "current_yes_bid_size": book.get("bid_size"),
                "current_yes_ask_size": book.get("ask_size"),
                "current_yes_tick_size": book.get("tick_size"),
                "current_yes_book_status": book.get("status"),
                **signal_runner.direct_book_clock_fields(book),
            }
            result = evaluate_entry(scored_input, book.get("asks") or [], artifact)
            scored_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            row = {
                **scored_input,
                **result,
                "record_type": "current_yes_core_carry_continuous_market_rescore",
                "created_at_utc": scored_at,
                "market_rescore_at_utc": scored_at,
                "cycle_started_at_utc": now.isoformat(timespec="microseconds"),
                "rescore_mode": "continuous_market_reprice",
                "parent_checkpoint_key": checkpoint.get("checkpoint_key"),
                "parent_checkpoint_created_at_utc": checkpoint.get("created_at_utc"),
                "feature_book_snapshot_id": book_identity,
                "execution_book_snapshot_id": book_identity,
                "direct_book_asks": book.get("asks") or [],
                "direct_book_bids": book.get("bids") or [],
                "zero_notional": True,
                "no_order_placed": True,
            }
            row["market_rescore_id"] = "market-rescore-" + stable_hash(
                {
                    "city": city_day[0],
                    "target_date": city_day[1],
                    "checkpoint_key": row.get("checkpoint_key"),
                    "book": book_identity,
                }
            )
            append_jsonl(output_dir / "continuous_market_rescores.jsonl", row)
            rows.append(row)
    return rows


def near_core_signal_id(row: Mapping[str, Any]) -> str:
    return "current-yes-core-carry-near-core-" + stable_hash(
        {
            "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
            "city": str(row.get("city") or ""),
            "target_date": str(row.get("target_date") or ""),
            "checkpoint_key": str(row.get("checkpoint_key") or ""),
        }
    )


def latest_near_core_candidates(
    rows: Iterable[Mapping[str, Any]], *, now: datetime, max_age_sec: float
) -> list[dict[str, Any]]:
    latest: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for source in rows:
        row = dict(source)
        if not near_core_maker_probe.is_candidate(row):
            continue
        clock = parse_utc(
            row.get("market_rescore_at_utc") or row.get("created_at_utc")
        )
        if clock is None or clock > now:
            continue
        age = (now - clock).total_seconds()
        if age < 0 or age > max_age_sec:
            continue
        key = str(row.get("checkpoint_key") or "")
        prior = latest.get(key)
        if prior is None or clock > prior[0]:
            latest[key] = (clock, row)
    # The newest checkpoint owns the one city-day order.  Older simultaneous
    # checkpoints remain explicit blocked denominator rows.
    return [
        item[1]
        for item in sorted(latest.values(), key=lambda item: item[0], reverse=True)
    ]


def build_near_core_entry_plan(
    row: Mapping[str, Any],
    *,
    live_enabled: bool,
    now: datetime,
    order_ttl_min: float,
) -> dict[str, Any] | None:
    fields = base_plan_fields(
        row,
        child_order_role="maker",
        shares=near_core_maker_probe.FIXED_SHARES,
        live_enabled=live_enabled,
        now=now,
        order_ttl_min=order_ttl_min,
    )
    if (
        not bool(fields.get("maker_live_eligible"))
        or (finite(fields.get("limit_price")) or 0.0) <= 0
    ):
        return None
    sid = near_core_signal_id(row)
    fields.update(
        {
            "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
            "config_id": near_core_maker_probe.CONFIG_ID,
            "execution_profile": near_core_maker_probe.EXECUTION_PROFILE,
            "source_sleeve": near_core_maker_probe.SOURCE_SLEEVE,
            "near_core_experiment_id": near_core_maker_probe.EXPERIMENT_ID,
            "maker_experiment_id": near_core_maker_probe.EXPERIMENT_ID,
            "decision_mode": "continuous_core_v3_sole_non_positive_taker_ev",
            "execution_mode": "near_core_continuous_fixed_rest_5_share",
            "execution_policy": "core_carry_near_core_continuous_fixed_rest_maker_v2",
            "order_lifecycle_policy": "near_core_fixed_rest_safety_cancel_only_v2",
            "maker_budget_mode": "additive_near_core_fixed_5_share",
            "client_order_prefix": near_core_maker_probe.CLIENT_ORDER_PREFIX,
            "blocker": near_core_maker_probe.SOLE_BLOCKER,
            "near_core_policy_arm": "CONTINUOUS_MARKET_REPRICE_FIXED_REST",
            "economic_ws_cancel_enabled": False,
            "fixed_order_shares": near_core_maker_probe.FIXED_SHARES,
            "max_order_shares": near_core_maker_probe.FIXED_SHARES,
        }
    )
    plan = {
        "record_type": "weather_edge_trade_plan",
        "plan_id": "plan-"
        + stable_hash(
            {
                "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
                "signal_id": sid,
                "role": "maker",
                "checkpoint_key": row.get("checkpoint_key"),
            }
        ),
        "signal_id": sid,
        "opportunity_id": sid,
        "created_at_utc": now.isoformat(timespec="seconds"),
        "status": "accepted",
        "risk_status": "passed",
        "risk_reason": "",
        **fields,
    }
    plan["comparison_group_id"] = stable_hash(
        {
            "source_sleeve": near_core_maker_probe.SOURCE_SLEEVE,
            "signal_id": sid,
            "token_id": plan.get("token_id"),
        }
    )
    compatibility = build_core_carry_legacy_plan_compatibility(legacy_plans=[plan])
    intent = compatibility.intents[0]
    plan.update(
        {
            "execution_schema_version": intent.execution_schema_version,
            "resolved_execution_profile": intent.resolved_execution_profile,
            "execution_config_id": intent.execution_config_id,
            "plan_dedupe_key": intent.plan_dedupe_key,
            "live_exposure_key": intent.live_exposure_key,
        }
    )
    return plan


def near_core_maker_lifecycle_heads(path: Path) -> list[dict[str, Any]]:
    heads: dict[str, tuple[datetime, int, dict[str, Any]]] = {}
    for index, row in enumerate(iter_jsonl(path)):
        if (
            str(row.get("strategy_instance") or "")
            != near_core_maker_probe.STRATEGY_INSTANCE
            or not bool(row.get("maker_only"))
        ):
            continue
        row = normalized_maker_lifecycle_row(row)
        root = maker_lifecycle_root(row)
        created = parse_utc(row.get("created_at_utc"))
        if not root or created is None:
            continue
        prior = heads.get(root)
        if prior is None or (created, index) > (prior[0], prior[1]):
            heads[root] = (created, index, row)
    return [item[2] for item in heads.values()]


def recover_near_core_journal_terminal_makers(output_dir: Path) -> int:
    """Recover a terminal near-Core projection after a journal/write crash."""

    live_orders = output_dir / "live_orders.jsonl"
    states = execution_journal_order_states(output_dir / "execution_journal.jsonl")
    existing_execution_ids = {
        str(row.get("execution_id") or "") for row in iter_jsonl(live_orders)
    }
    written = 0
    for head in near_core_maker_lifecycle_heads(live_orders):
        if str(head.get("status") or "") != "submitted":
            continue
        order_id = live_order_id(head)
        evidence = states.get(order_id)
        if not order_id or not evidence or evidence["status"] not in {"cancelled", "filled"}:
            continue
        execution_id = "near-core-journal-recovery-" + stable_hash(
            {"order_id": order_id, "terminal_status": evidence["status"]}
        )
        if execution_id in existing_execution_ids:
            continue
        terminal = {
            **dict(head),
            "record_type": "weather_edge_live_order",
            "created_at_utc": evidence["recorded_at_utc"] or utc_now(),
            "status": evidence["status"],
            "child_order_role": "near_core_maker_terminal",
            "execution_action": "near_core_maker_terminal",
            "execution_id": execution_id,
            "plan_id": "plan-" + execution_id,
            "replacement_of_order_id": order_id,
            "source_order_id": order_id,
            "exchange_response": {
                "quote_status": evidence["status"],
                "quote_reason": "execution_journal_terminal_recovery",
                "authoritative_execution_journal": evidence["journal_payload"],
            },
        }
        append_jsonl(live_orders, terminal)
        existing_execution_ids.add(execution_id)
        written += 1
    return written


def near_core_daily_usage(output_dir: Path, now: datetime) -> tuple[int, float]:
    day = now.astimezone(BJ).date()
    city_days: set[tuple[str, str]] = set()
    posted = 0.0
    for row in iter_jsonl(output_dir / "live_orders.jsonl"):
        if (
            str(row.get("strategy_instance") or "")
            != near_core_maker_probe.STRATEGY_INSTANCE
            or str(row.get("status") or "") != "submitted"
        ):
            continue
        created = parse_utc(row.get("created_at_utc"))
        if created is None or created.astimezone(BJ).date() != day:
            continue
        key = near_core_maker_probe.city_day(row)
        if all(key):
            city_days.add(key)
        posted += finite(row.get("posted_notional")) or finite(row.get("notional")) or 0.0
    return len(city_days), posted


def near_core_entry_plans(
    args: argparse.Namespace,
    output_dir: Path,
    *,
    now: datetime,
    core_actionable_city_days: set[tuple[str, str]],
    market_rescores: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not bool(args.near_core_maker_probe_enabled):
        return [], []
    live_rows = list(iter_jsonl(output_dir / "live_orders.jsonl"))
    existing_family = submitted_city_days(family_live_order_files(output_dir))
    near_consumed = near_core_maker_probe.consumed_city_days(live_rows)
    used_city_days, used_cost = near_core_daily_usage(output_dir, now)
    plans: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    for row in latest_near_core_candidates(
        market_rescores,
        now=now,
        max_age_sec=float(args.near_core_candidate_max_age_sec),
    ):
        key = near_core_maker_probe.city_day(row)
        book_freshness = near_core_book_freshness(
            row,
            now=now,
            max_age_sec=float(args.near_core_book_max_age_sec),
        )
        reason = ""
        if not bool(book_freshness["near_core_book_fresh"]):
            reason = str(book_freshness["near_core_book_freshness_status"])
        elif key in core_actionable_city_days:
            reason = "existing_core_actionable_priority"
        elif key in existing_family:
            reason = "existing_core_family_exposure"
        elif key in near_consumed:
            reason = "near_core_city_day_exposure_already_consumed"
        elif (
            int(args.near_core_max_city_days_per_bj_day) > 0
            and used_city_days >= int(args.near_core_max_city_days_per_bj_day)
        ):
            reason = "near_core_daily_city_day_cap"
        plan = None if reason else build_near_core_entry_plan(
            row,
            live_enabled=bool(
                args.live
                and args.confirm_live
                and args.confirm_near_core_maker_probe_live
            ),
            now=now,
            order_ttl_min=float(args.near_core_order_ttl_min),
        )
        if not reason and plan is None:
            reason = "near_core_maker_safety_not_eligible"
        planned_cost = entry_plan_cost_reservation([plan] if plan else [])
        if (
            not reason
            and used_cost + planned_cost > float(args.near_core_max_daily_cost_usd)
        ):
            reason = "near_core_daily_cost_cap"
            plan = None
            planned_cost = 0.0
        ledger.append(
            {
                "schema_version": near_core_maker_probe.LEDGER_SCHEMA_VERSION,
                "record_type": "core_carry_near_core_maker_decision",
                "ledger_id": "near-core-ledger-"
                + stable_hash(
                    {
                        "checkpoint_key": row.get("checkpoint_key"),
                        "market_rescore_id": row.get("market_rescore_id"),
                        "status": "blocked" if reason else "planned",
                        "reason": reason,
                    }
                ),
                "created_at_utc": now.isoformat(timespec="seconds"),
                "source_sleeve": near_core_maker_probe.SOURCE_SLEEVE,
                "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
                "experiment_id": near_core_maker_probe.EXPERIMENT_ID,
                "eligible_checkpoint": True,
                "city": key[0],
                "target_date": key[1],
                "checkpoint_key": row.get("checkpoint_key"),
                "signal_id": near_core_signal_id(row),
                "market_rescore_id": row.get("market_rescore_id"),
                "feature_book_snapshot_id": row.get("feature_book_snapshot_id"),
                "execution_book_snapshot_id": row.get("execution_book_snapshot_id"),
                "model_probability_hold": row.get("model_probability_hold"),
                "model_edge_after_fee_and_depth": row.get(
                    "model_edge_after_fee_and_depth"
                ),
                "current_yes_bid": row.get("current_yes_bid"),
                "current_yes_ask": row.get("current_yes_ask"),
                "sole_blocker": near_core_maker_probe.SOLE_BLOCKER,
                "status": "blocked" if reason else "planned",
                "reason": reason,
                "plan_id": plan.get("plan_id") if plan else "",
                "fixed_shares": near_core_maker_probe.FIXED_SHARES,
                "client_order_prefix": near_core_maker_probe.CLIENT_ORDER_PREFIX,
                "policy_arm": "CONTINUOUS_MARKET_REPRICE_FIXED_REST",
                "economic_ws_cancel_enabled": False,
                "live_enabled": bool(plan and plan.get("live_enabled")),
                "live_authorized": bool(args.live and args.confirm_live and args.confirm_near_core_maker_probe_live),
                **book_freshness,
            }
        )
        if plan is not None:
            plans.append(plan)
            near_consumed.add(key)
            used_city_days += 1
            used_cost += planned_cost
    return plans, ledger


def near_core_lifecycle_plans(
    args: argparse.Namespace,
    output_dir: Path,
    *,
    now: datetime,
    core_actionable_city_days: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[tuple[str, str]]]:
    epochs = latest_weather_epochs(output_dir / "state_decisions.jsonl")
    plans: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    superseded: set[tuple[str, str]] = set()
    terminal_order_ids = terminal_safety_cancel_order_ids(
        output_dir / "near_core_safety_cancels.jsonl"
    )
    for row in iter_jsonl(output_dir / "near_core_private_order_updates.jsonl"):
        for payload in _nested_mappings(row.get("payload")):
            order_id = str(
                payload.get("id")
                or payload.get("order_id")
                or payload.get("orderID")
                or ""
            )
            status = str(payload.get("status") or payload.get("state") or "").lower()
            if order_id and status in NEAR_CORE_TERMINAL_ORDER_STATUSES:
                terminal_order_ids.add(order_id)
    candidates = [
        row
        for row in near_core_maker_lifecycle_heads(output_dir / "live_orders.jsonl")
        if str(row.get("status") or "") == "submitted"
        and bool(live_order_id(row))
        and live_order_id(row) not in terminal_order_ids
    ]
    with market_httpx_client(args.book_proxy, timeout=float(args.book_timeout_sec)) as client:
        for order in candidates:
            key = near_core_maker_probe.city_day(order)
            deadline = parse_utc(
                order.get("maker_lifecycle_deadline_utc") or order.get("expires_at_utc")
            )
            source_ref = str(
                order.get("data_epoch_ref") or order.get("source_report_ts_utc") or ""
            )
            latest = epochs.get(key)
            latest_ref = str((latest or {}).get("source_report_ts_utc") or "")
            action = ""
            blocker = "fixed_rest_no_economic_action"
            if key in core_actionable_city_days:
                action = "near_core_maker_cancel_core_supersession"
                blocker = "existing_core_became_actionable"
                superseded.add(key)
            elif not bool(args.near_core_maker_probe_enabled):
                action = "near_core_maker_cancel_feature_disabled"
                blocker = "near_core_feature_disabled"
            elif deadline is None or now >= deadline:
                action = "near_core_maker_cancel_safety_deadline"
                blocker = "near_core_common_deadline_elapsed"
            elif not latest_ref or latest_ref != source_ref:
                action = "near_core_maker_cancel_weather_state"
                blocker = (
                    "latest_weather_state_unavailable"
                    if not latest_ref
                    else "weather_state_changed_requires_fresh_candidate"
                )
            else:
                quote = weather_state._fetch_token_book(  # noqa: SLF001
                    client, str(order.get("token_id") or "")
                )
                bid = finite(quote.get("bid")) or 0.0
                ask = finite(quote.get("ask")) or 0.0
                if (
                    str(quote.get("book_status") or "") != "ok"
                    or bid <= 0
                    or ask <= bid
                ):
                    action = "near_core_maker_cancel_stale_market_state"
                    blocker = "no_fresh_valid_two_sided_book"
            decision = {
                "schema_version": near_core_maker_probe.LEDGER_SCHEMA_VERSION,
                "record_type": "core_carry_near_core_maker_lifecycle_decision",
                "ledger_id": "near-core-ledger-"
                + stable_hash(
                    {
                        "source_order_id": live_order_id(order),
                        "action": action,
                        "latest_ref": latest_ref,
                    }
                ),
                "created_at_utc": now.isoformat(timespec="seconds"),
                "source_sleeve": near_core_maker_probe.SOURCE_SLEEVE,
                "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
                "experiment_id": near_core_maker_probe.EXPERIMENT_ID,
                "eligible_checkpoint": False,
                "city": key[0],
                "target_date": key[1],
                "signal_id": order.get("signal_id"),
                "source_order_id": live_order_id(order),
                "status": "cancel_planned" if action else "resting",
                "action": action,
                "reason": blocker,
                "economic_ws_cancel_enabled": False,
            }
            decisions.append(decision)
            if action:
                plans.append(
                    build_maker_lifecycle_plan(
                        order,
                        action=action,
                        limit_price=0.0,
                        cancel_only=True,
                        cancel_source_order=True,
                        now=now,
                        live_enabled=bool(args.live and args.confirm_live),
                    )
                )
    return plans, decisions, superseded


def append_near_core_ledger(output_dir: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path = output_dir / "near_core_maker_v2_ledger.jsonl"
    existing = {str(row.get("ledger_id") or "") for row in iter_jsonl(path)}
    written = 0
    for source in rows:
        row = dict(source)
        ledger_id = str(row.get("ledger_id") or "")
        if not ledger_id or ledger_id in existing:
            continue
        append_jsonl(path, row)
        existing.add(ledger_id)
        written += 1
    return written


def _nested_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _nested_mappings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _nested_mappings(child)


def _authoritative_near_core_order_events(
    output_dir: Path,
    *,
    orders_by_order_id: Mapping[str, Mapping[str, Any]],
    orders_by_client_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project private WS and safety-cancel truth onto known near orders."""

    events: list[dict[str, Any]] = []

    def add_state(
        state: Mapping[str, Any], *, source: str, received_at_utc: Any
    ) -> None:
        order_id = str(
            state.get("order_id")
            or state.get("orderID")
            or state.get("orderId")
            or state.get("id")
            or ""
        )
        client_id = str(
            state.get("client_order_id")
            or state.get("clientOrderId")
            or state.get("clientOrderID")
            or ""
        )
        base = orders_by_order_id.get(order_id) or orders_by_client_id.get(client_id)
        if base is None:
            return
        matched = near_core_maker_probe.matched_shares(
            {
                "authoritative_matched_shares": state.get("size_matched")
                or state.get("sizeMatched")
                or state.get("matched_shares")
                or state.get("matchedSize"),
            }
        )
        status = str(
            state.get("status") or state.get("state") or "authoritative_update"
        ).lower()
        event_key = {
            "source": source,
            "order_id": order_id or live_order_id(base),
            "client_order_id": client_id or base.get("client_order_id"),
            "received_at_utc": received_at_utc,
            "status": status,
            "matched_shares": matched,
        }
        events.append(
            {
                "base_order": dict(base),
                "source": source,
                "received_at_utc": received_at_utc,
                "order_id": event_key["order_id"],
                "client_order_id": event_key["client_order_id"],
                "status": status,
                "matched_shares": matched,
                "authoritative_order_state": dict(state),
                "event_hash": stable_hash(event_key),
            }
        )

    for event in iter_jsonl(output_dir / "near_core_private_order_updates.jsonl"):
        received = event.get("received_at_utc") or event.get("created_at_utc")
        for state in _nested_mappings(event.get("payload")):
            add_state(state, source="private_user_ws", received_at_utc=received)

    for event in iter_jsonl(output_dir / "near_core_safety_cancels.jsonl"):
        state = event.get("authoritative_order_state")
        if isinstance(state, Mapping):
            enriched = {
                **dict(state),
                "order_id": state.get("order_id") or event.get("order_id"),
            }
            add_state(
                enriched,
                source="safety_cancel_fetch_order",
                received_at_utc=event.get("created_at_utc"),
            )
    return events


def sync_near_core_order_ledger(output_dir: Path) -> int:
    rows: list[dict[str, Any]] = []
    orders_by_order_id: dict[str, dict[str, Any]] = {}
    orders_by_client_id: dict[str, dict[str, Any]] = {}
    for order in iter_jsonl(output_dir / "live_orders.jsonl"):
        if (
            str(order.get("strategy_instance") or "")
            != near_core_maker_probe.STRATEGY_INSTANCE
        ):
            continue
        execution_id = str(order.get("execution_id") or "")
        if not execution_id:
            continue
        order_id = live_order_id(order)
        client_id = str(order.get("client_order_id") or "")
        if order_id:
            orders_by_order_id[order_id] = dict(order)
        if client_id:
            orders_by_client_id[client_id] = dict(order)
        rows.append(
            {
                "schema_version": near_core_maker_probe.LEDGER_SCHEMA_VERSION,
                "record_type": "core_carry_near_core_maker_order_state",
                "ledger_id": "near-core-order-" + execution_id,
                "created_at_utc": order.get("created_at_utc"),
                "source_sleeve": near_core_maker_probe.SOURCE_SLEEVE,
                "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
                "experiment_id": near_core_maker_probe.EXPERIMENT_ID,
                "eligible_checkpoint": False,
                "city": order.get("city"),
                "target_date": order.get("target_date"),
                "signal_id": order.get("signal_id"),
                "plan_id": order.get("plan_id"),
                "execution_id": execution_id,
                "client_order_id": order.get("client_order_id"),
                "order_id": order_id,
                "status": order.get("status"),
                "matched_shares": near_core_maker_probe.matched_shares(order),
                "economic_ws_cancel_enabled": False,
            }
        )
    for event in _authoritative_near_core_order_events(
        output_dir,
        orders_by_order_id=orders_by_order_id,
        orders_by_client_id=orders_by_client_id,
    ):
        order = event["base_order"]
        rows.append(
            {
                "schema_version": near_core_maker_probe.LEDGER_SCHEMA_VERSION,
                "record_type": "core_carry_near_core_maker_authoritative_order_state",
                "ledger_id": "near-core-authoritative-" + str(event["event_hash"]),
                "created_at_utc": event.get("received_at_utc"),
                "source_sleeve": near_core_maker_probe.SOURCE_SLEEVE,
                "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
                "experiment_id": near_core_maker_probe.EXPERIMENT_ID,
                "eligible_checkpoint": False,
                "city": order.get("city"),
                "target_date": order.get("target_date"),
                "signal_id": order.get("signal_id"),
                "plan_id": order.get("plan_id"),
                "execution_id": order.get("execution_id"),
                "client_order_id": event.get("client_order_id"),
                "order_id": event.get("order_id"),
                "status": event.get("status"),
                "matched_shares": event.get("matched_shares"),
                "authoritative_order_state": event.get("authoritative_order_state"),
                "order_state_source": event.get("source"),
                "economic_ws_cancel_enabled": False,
            }
        )
    return append_near_core_ledger(output_dir, rows)


def write_near_core_runtime_artifacts(
    args: argparse.Namespace, output_dir: Path
) -> dict[str, Any]:
    ledger = list(iter_jsonl(output_dir / "near_core_maker_v2_ledger.jsonl"))
    manifest = {
        "schema_version": near_core_maker_probe.MANIFEST_SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "source_sleeve": near_core_maker_probe.SOURCE_SLEEVE,
        "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
        "config_id": near_core_maker_probe.CONFIG_ID,
        "experiment_id": near_core_maker_probe.EXPERIMENT_ID,
        "feature_enabled": bool(args.near_core_maker_probe_enabled),
        "live_authorized": bool(
            args.live
            and args.confirm_live
            and args.confirm_near_core_maker_probe_live
        ),
        "fixed_shares": near_core_maker_probe.FIXED_SHARES,
        "client_order_prefix": near_core_maker_probe.CLIENT_ORDER_PREFIX,
        "risk_budget": {
            "max_city_days_per_bj_day": int(args.near_core_max_city_days_per_bj_day),
            "city_day_count_cap_enabled": int(
                args.near_core_max_city_days_per_bj_day
            ) > 0,
            "max_daily_cost_usd": float(args.near_core_max_daily_cost_usd),
        },
        "policy_arm": "CONTINUOUS_MARKET_REPRICE_FIXED_REST",
        "rescore_interval_sec": float(args.interval_seconds),
        "additive_to_core_10_plus_5": True,
        "economic_ws_cancel_enabled": False,
        "ws_control_readiness_manifest": str(
            args.near_core_ws_control_manifest or ""
        ),
        "ledger_path": str(output_dir / "near_core_maker_v2_ledger.jsonl"),
        **DEPLOYMENT_METADATA,
    }
    report = near_core_maker_probe.report(ledger)
    gate = near_core_maker_probe.promotion_gate(ledger)
    write_json(output_dir / "near_core_maker_v2_manifest.json", manifest)
    write_json(output_dir / "near_core_maker_v2_latest_report.json", report)
    write_json(output_dir / "near_core_maker_v2_promotion_gate.json", gate)
    return {"manifest": manifest, "report": report, "promotion_gate": gate}

def execute_plans(args: argparse.Namespace, plans: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    plans_path = output_dir / "current_plans.jsonl"
    write_jsonl(plans_path, plans)
    result = execute_core_carry_plans(
        plans=plans,
        output_dir=output_dir,
        live=bool(args.live),
        market_proxy=args.market_proxy,
        max_child_shares=max_live_child_notional_usd(args),
        max_batch_cost_usd=(
            float(args.max_daily_cost_usd)
            + (
                float(args.near_core_max_daily_cost_usd)
                if bool(args.near_core_maker_probe_enabled)
                else 0.0
            )
        ),
        code_commit=str(DEPLOYMENT_METADATA["deployed_repo_sha"]),
    )
    return {
        "exit_code": 0 if int(result.get("live_errors") or 0) == 0 else 1,
        "result": result,
        "stderr": "" if int(result.get("live_errors") or 0) == 0 else "shared runtime execution error",
    }


def build_maker_lifecycle_plan(
    order: Mapping[str, Any],
    *,
    action: str,
    limit_price: float,
    cancel_only: bool,
    cancel_source_order: bool,
    now: datetime,
    live_enabled: bool,
    reprice_stage: str = "",
) -> dict[str, Any]:
    live_source_id = live_order_id(order)
    lineage_source_id = live_source_id or str(order.get("source_order_id") or "")
    shares = finite(order.get("size")) or 5.0
    fields = {
        **dict(order),
        "record_type": "weather_edge_trade_plan",
        "created_at_utc": now.isoformat(timespec="seconds"),
        "child_order_role": action,
        "execution_action": action,
        "execution_policy": str(
            order.get("execution_policy") or "current_yes_residual_carry_maker_v2"
        ),
        "order_lifecycle_policy": str(
            order.get("order_lifecycle_policy")
            or "maker_staged_chase_until_pre_data_update_or_ttl_v2"
        ),
        "limit_price": round(limit_price, 6),
        "notional": round(shares * limit_price, 6),
        "order_notional_cap": round(shares * limit_price, 6),
        "maker_only": True,
        "paper_enabled": False,
        "live_enabled": bool(live_enabled),
        "cancel_before_order_id": live_source_id if cancel_source_order else "",
        "source_order_id": lineage_source_id,
        "source_execution_id": str(order.get("execution_id") or ""),
        "source_plan_id": str(order.get("plan_id") or ""),
        "source_posted_price": finite(order.get("posted_price")) or finite(order.get("limit_price")) or 0.0,
        "source_remaining_shares": shares,
        "replacement_requires_order_state": bool(cancel_source_order and not cancel_only),
        "cancel_only": cancel_only,
        "allow_duplicate_signal_id": True,
        "maker_lifecycle_reprice_count": int(
            finite(order.get("maker_lifecycle_reprice_count")) or 0
        )
        + (1 if action == "core_carry_maker_reprice" else 0),
        "maker_last_reprice_stage": (
            reprice_stage
            if action == "core_carry_maker_reprice"
            else str(order.get("maker_last_reprice_stage") or "")
        ),
    }
    fields["plan_id"] = "plan-" + stable_hash(
        {
            "source_order_id": lineage_source_id,
            "action": action,
            "limit_price": limit_price,
            "created_at_utc": fields["created_at_utc"],
        }
    )
    fields["status"] = "accepted"
    fields["risk_status"] = "passed"
    fields["risk_reason"] = ""
    return fields


def maker_lifecycle_plans(
    args: argparse.Namespace,
    output_dir: Path,
    *,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    live_orders = output_dir / "live_orders.jsonl"
    epochs = latest_weather_epochs(output_dir / "state_decisions.jsonl")
    candidates: list[tuple[dict[str, Any], bool]] = []
    for row in maker_lifecycle_heads(live_orders):
        active_order = str(row.get("status") or "") == "submitted" and bool(live_order_id(row))
        detached_retry = retryable_maker_post_failure(row)
        if not active_order and not detached_retry:
            continue
        created = parse_utc(row.get("created_at_utc"))
        if created is None or (now - created).total_seconds() < float(args.maker_refresh_sec):
            continue
        candidates.append((row, active_order))

    plans: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    with market_httpx_client(args.book_proxy, timeout=float(args.book_timeout_sec)) as client:
        for order, active_order in candidates:
            city_day = (str(order.get("city") or ""), str(order.get("target_date") or ""))
            latest = epochs.get(city_day)
            source_epoch = str(order.get("source_report_ts_utc") or "")
            latest_epoch = str((latest or {}).get("source_report_ts_utc") or "")
            deadline = parse_utc(order.get("maker_lifecycle_deadline_utc") or order.get("expires_at_utc"))
            action = ""
            blocker = ""
            next_price = 0.0
            best_bid = 0.0
            best_ask = 0.0
            cancel_only = False
            reprice_stage = ""
            reprice_count = int(
                finite(order.get("maker_lifecycle_reprice_count")) or 0
            )
            last_reprice_stage = str(order.get("maker_last_reprice_stage") or "")
            max_reprices = maker_max_reprices()
            if deadline is None or now >= deadline:
                if active_order:
                    cancel_before_update = parse_utc(
                        order.get("cancel_before_data_update_utc")
                    )
                    action = (
                        "core_carry_maker_cancel_pre_data_update"
                        if cancel_before_update is not None
                        and now >= cancel_before_update
                        else "core_carry_maker_cancel_ttl"
                    )
                    cancel_only = True
                else:
                    blocker = "detached_maker_retry_ttl_expired"
            elif not latest or not latest_epoch or latest_epoch != source_epoch:
                if not latest or not latest_epoch:
                    blocker = "latest_observation_state_unavailable"
                else:
                    blocker = "new_observation_requires_fresh_entry"
                if active_order:
                    action = "core_carry_maker_cancel_new_observation"
                    cancel_only = True
            else:
                quote = weather_state._fetch_token_book(client, str(order.get("token_id") or ""))  # noqa: SLF001
                best_bid = finite(quote.get("bid")) or 0.0
                best_ask = finite(quote.get("ask")) or 0.0
                tick = finite(quote.get("tick_size")) or finite(order.get("quote_tick_size")) or 0.001
                cap = min(
                    finite(order.get("maker_price_cap")) or 0.0,
                    finite(order.get("model_token_probability")) or 0.0,
                )
                posted = finite(order.get("posted_price")) or finite(order.get("limit_price")) or 0.0
                if str(quote.get("book_status") or "") != "ok" or best_bid <= 0 or best_ask <= best_bid:
                    blocker = "bad_fresh_book"
                else:
                    next_price, reprice_stage = staged_maker_resting_price(
                        order,
                        best_bid=best_bid,
                        best_ask=best_ask,
                        tick_size=tick,
                        price_cap=cap,
                        now=now,
                    )
                    if not active_order and next_price > 0:
                        action = "core_carry_maker_repost"
                    elif reprice_stage == "queue":
                        blocker = "queue_preserving_stage"
                    elif reprice_count >= max_reprices:
                        blocker = "maker_reprice_limit_reached"
                    elif last_reprice_stage == reprice_stage:
                        blocker = "maker_reprice_stage_already_used"
                    elif next_price > posted + tick / 2.0:
                        action = "core_carry_maker_reprice"
                    elif best_bid <= posted + tick / 2.0:
                        blocker = "own_or_same_level_best_bid_keep_queue"
                    else:
                        blocker = "already_at_best_allowed_price"
            decision = {
                "record_type": "current_yes_core_carry_maker_lifecycle_decision",
                "created_at_utc": now.isoformat(timespec="seconds"),
                "source_order_id": live_order_id(order),
                "city": city_day[0],
                "target_date": city_day[1],
                "source_report_ts_utc": source_epoch,
                "latest_source_report_ts_utc": latest_epoch,
                "posted_price": finite(order.get("posted_price")) or 0.0,
                "maker_price_cap": finite(order.get("maker_price_cap")) or 0.0,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "next_price": next_price,
                "action": action,
                "blocker": blocker,
                "active_exchange_order": active_order,
                "new_observation_revalidation_status": "",
                "new_observation_revalidation_reasons": [],
                "reprice_stage": reprice_stage,
                "maker_reprice_count": reprice_count,
                "maker_max_reprices": max_reprices,
                "maker_last_reprice_stage": last_reprice_stage,
            }
            decisions.append(decision)
            if action:
                plan = build_maker_lifecycle_plan(
                    order,
                    action=action,
                    limit_price=next_price,
                    cancel_only=cancel_only,
                    cancel_source_order=active_order,
                    reprice_stage=reprice_stage,
                    now=now,
                    live_enabled=bool(args.live and args.confirm_live),
                )
                plans.append(plan)
    return plans, decisions


def new_entry_plans(
    args: argparse.Namespace,
    output_dir: Path,
    *,
    now: datetime,
    market_rescores: Iterable[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    scores = latest_rows_by_checkpoint(output_dir / "pre_live_scores.jsonl")
    attempted = attempted_signal_ids(output_dir)
    family_paths = family_live_order_files(output_dir)
    family_city_days = submitted_city_days(family_paths)
    used_city_days, used_cost = daily_family_usage(family_paths, now)
    plans: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    current_by_city_day = {
        (str(row.get("city") or ""), str(row.get("target_date") or "")): dict(row)
        for row in market_rescores
        if str(row.get("city") or "") and str(row.get("target_date") or "")
    }
    candidates: dict[tuple[str, str], tuple[dict[str, Any], bool]] = {}
    for would in iter_jsonl(output_dir / "would_orders.jsonl"):
        row = scores.get(str(would.get("checkpoint_key") or ""))
        if row is None:
            continue
        city_day = (str(row.get("city") or ""), str(row.get("target_date") or ""))
        if city_day not in current_by_city_day:
            candidates[city_day] = (dict(row), bool(would.get("family_city_day_conflict")))
    for city_day, row in current_by_city_day.items():
        if bool(row.get("eligible")):
            candidates[city_day] = (row, False)
        else:
            candidates.pop(city_day, None)
    for _city_day_key, (row, source_family_conflict) in sorted(candidates.items()):
        sid = signal_id(row)
        city_day = (str(row.get("city") or ""), str(row.get("target_date") or ""))
        if sid in attempted:
            continue
        reason = ""
        if source_family_conflict or city_day in family_city_days:
            reason = "family_city_day_conflict"
        elif used_city_days >= int(args.max_city_days_per_bj_day):
            reason = "daily_city_day_cap"
        entry_plans = (
            []
            if reason
            else build_entry_plans(
                row,
                live_enabled=bool(args.live and args.confirm_live),
                now=now,
                taker_shares=float(args.taker_shares),
                maker_shares=float(args.maker_shares),
                order_ttl_min=float(args.order_ttl_min),
            )
        )
        if not reason and not any(plan.get("child_order_role") == "taker" for plan in entry_plans):
            reason = "missing_taker_plan"
            entry_plans = []
        planned_cost = entry_plan_cost_reservation(entry_plans)
        if (
            not reason
            and used_cost + planned_cost > float(args.max_daily_cost_usd)
        ):
            reason = "daily_cost_cap"
            entry_plans = []
            planned_cost = 0.0
        maker_clock = maker_clock_assessment(
            row,
            now=now,
            order_ttl_min=float(args.order_ttl_min),
        )
        maker_planned = any(
            plan.get("child_order_role") == "maker" for plan in entry_plans
        )
        attempts.append(
            {
                "record_type": "current_yes_core_carry_entry_attempt",
                "created_at_utc": now.isoformat(timespec="seconds"),
                "signal_id": sid,
                "city": city_day[0],
                "target_date": city_day[1],
                "checkpoint_key": row.get("checkpoint_key"),
                "trigger_mode": str(row.get("rescore_mode") or "hourly_checkpoint"),
                "market_rescore_id": row.get("market_rescore_id"),
                "feature_book_snapshot_id": row.get("feature_book_snapshot_id"),
                "execution_book_snapshot_id": row.get("execution_book_snapshot_id"),
                "model_probability_hold": row.get("model_probability_hold"),
                "token_id": str(
                    row.get("token_id") or row.get("current_yes_token_id") or ""
                ),
                "condition_id": str(
                    row.get("condition_id") or row.get("current_condition_id") or ""
                ),
                "market_id": str(row.get("current_market_id") or ""),
                "bracket": str(row.get("current_bracket") or ""),
                "original_source_report_ts_utc": str(
                    row.get("source_report_ts_utc") or ""
                ),
                "original_best_bid": finite(row.get("current_yes_bid")),
                "original_best_ask": finite(row.get("current_yes_ask")),
                "status": "blocked" if reason else "planned",
                "reason": reason,
                "live_enabled": bool(args.live and args.confirm_live),
                "maker_requested_shares": float(args.maker_shares),
                "maker_planned_shares": (
                    float(args.maker_shares) if maker_planned else 0.0
                ),
                "maker_live_action": (
                    "entry_blocked"
                    if reason
                    else ("post" if maker_planned else "skip_terminal")
                ),
                "maker_shadow_revalidation_shares": (
                    float(args.maker_shares)
                    if not reason
                    and not maker_planned
                    and maker_clock.get("maker_clock_status")
                    == "pre_source_report_blackout"
                    else 0.0
                ),
                **maker_clock,
            }
        )
        attempted.add(sid)
        if entry_plans:
            plans.extend(entry_plans)
            used_city_days += 1
            used_cost += planned_cost
    return plans, attempts


def configure_signal_runner(output_dir: Path) -> None:
    signal_runner.STRATEGY_ID = STRATEGY_ID
    signal_runner.STRATEGY_INSTANCE = STRATEGY_INSTANCE
    signal_runner.DECISION_MODE = "frozen_core_probability_first_positive_ten_share_taker_ev"
    signal_runner.OUTPUT_DIR = output_dir
    signal_runner.ARTIFACT_PATH = ARTIFACT_PATH


def validate_runtime_arguments(args: argparse.Namespace) -> None:
    if args.live and not args.confirm_live:
        raise RuntimeError("--live requires --confirm-live")
    if (
        args.live
        and args.near_core_maker_probe_enabled
        and not args.confirm_near_core_maker_probe_live
    ):
        raise RuntimeError(
            "live near-Core maker requires --confirm-near-core-maker-probe-live"
        )
    if (
        float(args.taker_shares) != FROZEN_TAKER_SHARES
        or float(args.maker_shares) != FROZEN_MAKER_SHARES
    ):
        raise RuntimeError("frozen Core split requires exactly 10 taker + 5 maker shares")
    if float(args.near_core_maker_shares) != near_core_maker_probe.FIXED_SHARES:
        raise RuntimeError("near-Core maker requires exactly 5 additive shares")
    if float(args.near_core_candidate_max_age_sec) <= 0:
        raise RuntimeError("--near-core-candidate-max-age-sec must be positive")
    if float(args.near_core_book_max_age_sec) <= 0:
        raise RuntimeError("--near-core-book-max-age-sec must be positive")
    if int(args.continuous_market_max_books_per_cycle) <= 0:
        raise RuntimeError("--continuous-market-max-books-per-cycle must be positive")
    if int(args.near_core_max_city_days_per_bj_day) <= 0:
        raise RuntimeError("--near-core-max-city-days-per-bj-day must be positive")
    if float(args.near_core_max_daily_cost_usd) <= 0:
        raise RuntimeError("--near-core-max-daily-cost-usd must be positive")


def near_core_control_failures(
    args: argparse.Namespace, *, now: datetime | None = None
) -> list[str]:
    if not bool(args.live and args.near_core_maker_probe_enabled):
        return []
    path = Path(str(args.near_core_ws_control_manifest or ""))
    if not path.is_file():
        return ["ws_control_manifest_missing"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["ws_control_manifest_unreadable"]
    return near_core_maker_probe.ws_control_readiness_failures(
        payload,
        now=now or datetime.now(timezone.utc),
    )


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    assert_runtime_contract()
    validate_runtime_arguments(args)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    configure_signal_runner(output_dir)
    signal_summary = signal_runner.run_once(args)
    now = datetime.now(timezone.utc)
    decision_packets = write_new_decision_packets(output_dir)
    capture_demands = write_capture_demands(
        output_dir,
        packets=decision_packets["written_packets"],
    )
    market_rescores = continuous_market_rescores(args, output_dir, now=now)
    # All decision clocks below are sampled after their preceding I/O.
    # Cycle-start time is provenance only, never quote age or a new order TTL.
    # Continuous rescoring can take longer than the readiness freshness window.
    # Validate against a clock sampled after that work, not the cycle-start clock.
    control_failures = near_core_control_failures(args)
    runtime_args = argparse.Namespace(**vars(args))
    if control_failures:
        runtime_args.near_core_maker_probe_enabled = False
    journal_terminal_recoveries = recover_journal_terminal_makers(output_dir)
    near_core_journal_terminal_recoveries = (
        recover_near_core_journal_terminal_makers(output_dir)
    )
    entry_plans, attempts = new_entry_plans(
        args,
        output_dir,
        now=datetime.now(timezone.utc),
        market_rescores=market_rescores,
    )
    core_actionable_city_days = {
        near_core_maker_probe.city_day(plan) for plan in entry_plans
    }
    near_lifecycle_plans, near_lifecycle_decisions, superseded_city_days = (
        near_core_lifecycle_plans(
            runtime_args,
            output_dir,
            now=datetime.now(timezone.utc),
            core_actionable_city_days=core_actionable_city_days,
        )
    )
    if superseded_city_days:
        entry_plans = [
            plan
            for plan in entry_plans
            if near_core_maker_probe.city_day(plan) not in superseded_city_days
        ]
        for attempt in attempts:
            if near_core_maker_probe.city_day(attempt) not in superseded_city_days:
                continue
            attempt.update(
                {
                    "status": "blocked",
                    "reason": "near_core_cancel_must_confirm_before_core_submit",
                    "maker_live_action": "defer_until_near_core_cancel_terminal",
                    "maker_planned_shares": 0.0,
                }
            )
    remaining_core_city_days = {
        near_core_maker_probe.city_day(plan) for plan in entry_plans
    }
    near_entry_plans, near_entry_decisions = near_core_entry_plans(
        runtime_args,
        output_dir,
        now=datetime.now(timezone.utc),
        core_actionable_city_days=remaining_core_city_days,
        market_rescores=market_rescores,
    )
    post_update_revalidations = collect_post_update_maker_revalidations(
        args,
        output_dir,
        now=datetime.now(timezone.utc),
    )
    lifecycle_plans, lifecycle_decisions = maker_lifecycle_plans(
        args, output_dir, now=datetime.now(timezone.utc)
    )
    for decision in lifecycle_decisions:
        append_jsonl(output_dir / "maker_lifecycle_decisions.jsonl", decision)
    append_near_core_ledger(
        output_dir, [*near_lifecycle_decisions, *near_entry_decisions]
    )
    plans = [
        *near_lifecycle_plans,
        *lifecycle_plans,
        *entry_plans,
        *near_entry_plans,
    ]
    execution = execute_plans(args, plans, output_dir)
    for attempt in attempts:
        append_jsonl(output_dir / "entry_attempts.jsonl", attempt)
    near_core_order_ledger_rows = sync_near_core_order_ledger(output_dir)
    near_core_artifacts = write_near_core_runtime_artifacts(runtime_args, output_dir)
    summary = {
        "status": "ok" if execution["exit_code"] == 0 else "executor_error",
        "generated_at_utc": utc_now(),
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_INSTANCE,
        "config_id": CONFIG_ID,
        "execution_profile": EXECUTION_PROFILE,
        "resolved_execution_profile": EXECUTION_PROFILE,
        "execution_config_id": execution_config_id_for_profile(EXECUTION_PROFILE),
        "mode": "tiny_live" if args.live else "paper_would_order",
        "live_enabled": bool(args.live and args.confirm_live),
        "artifact_hash": load_artifact(ARTIFACT_PATH)["artifact_hash"],
        "signal_status": signal_summary.get("status"),
        "signal_snapshot_file": signal_summary.get("snapshot_file"),
        "decision_packets_written": decision_packets["written"],
        "decision_packet_alerts": decision_packets["alerts"],
        "capture_demands_written": capture_demands["written"],
        "capture_demand_alerts": capture_demands["alerts"],
        "continuous_market_rescores": len(market_rescores),
        "continuous_market_rescore_interval_sec": float(args.interval_seconds),
        "continuous_market_core_actionable": sum(
            1 for row in market_rescores if bool(row.get("eligible"))
        ),
        "continuous_market_near_candidates": sum(
            1 for row in market_rescores if near_core_maker_probe.is_candidate(row)
        ),
        "entry_attempts": len(attempts),
        "entry_plans": len(entry_plans),
        "near_core_entry_plans": len(near_entry_plans),
        "near_core_lifecycle_plans": len(near_lifecycle_plans),
        "near_core_lifecycle_decisions": len(near_lifecycle_decisions),
        "near_core_superseded_core_city_days": sorted(
            f"{city}|{target_date}" for city, target_date in superseded_city_days
        ),
        "near_core_order_ledger_rows_written": near_core_order_ledger_rows,
        "near_core_feature_configured": bool(args.near_core_maker_probe_enabled),
        "near_core_feature_enabled": bool(runtime_args.near_core_maker_probe_enabled),
        "near_core_control_failures": control_failures,
        "near_core_live_authorized": bool(
            args.live
            and args.confirm_live
            and args.confirm_near_core_maker_probe_live
        ),
        "near_core_fixed_shares": float(args.near_core_maker_shares),
        "near_core_additive_to_core_10_plus_5": True,
        "near_core_manifest_file": str(output_dir / "near_core_maker_v2_manifest.json"),
        "near_core_report_file": str(output_dir / "near_core_maker_v2_latest_report.json"),
        "near_core_promotion_gate": near_core_artifacts["promotion_gate"],
        "maker_lifecycle_plans": len(lifecycle_plans),
        "maker_lifecycle_decisions": len(lifecycle_decisions),
        "maker_post_update_revalidations": len(post_update_revalidations),
        "maker_post_update_would_rearm": sum(
            1 for row in post_update_revalidations if row.get("would_rearm")
        ),
        "maker_post_update_zero_notional": True,
        "journal_terminal_recoveries": journal_terminal_recoveries,
        "near_core_journal_terminal_recoveries": (
            near_core_journal_terminal_recoveries
        ),
        "taker_shares": float(args.taker_shares),
        "maker_shares": float(args.maker_shares),
        "maker_refresh_sec": float(args.maker_refresh_sec),
        "maker_reprice_limit": maker_max_reprices(),
        "maker_cancel_buffer_sec": get_execution_profile(
            EXECUTION_PROFILE
        ).cancel_buffer_sec,
        "maker_retained_edge": maker_profile_parameter("retained_edge"),
        "maker_stage_midpoint_after_sec": maker_profile_parameter(
            "stage_midpoint_after_sec"
        ),
        "maker_stage_near_ask_after_sec": maker_profile_parameter(
            "stage_near_ask_after_sec"
        ),
        "max_city_days_per_bj_day": int(args.max_city_days_per_bj_day),
        "max_daily_cost_usd": float(args.max_daily_cost_usd),
        "near_core_max_city_days_per_bj_day": int(
            args.near_core_max_city_days_per_bj_day
        ),
        "near_core_max_daily_cost_usd": float(args.near_core_max_daily_cost_usd),
        "execution": execution,
        **DEPLOYMENT_METADATA,
    }
    publish_runtime_state_best_effort(args, output_dir, summary, signal_summary)
    write_json(output_dir / "latest_summary.json", summary)
    append_jsonl(output_dir / "summary_history.jsonl", summary)
    return summary


def parser() -> argparse.ArgumentParser:
    ap = signal_runner.parser()
    ap.description = __doc__
    ap.set_defaults(
        output_dir=str(OUTPUT_DIR),
        artifact=str(ARTIFACT_PATH),
        interval_seconds=15.0,
        summary_filename="signal_latest_summary.json",
        summary_history_filename="signal_summary_history.jsonl",
    )
    ap.add_argument("--taker-shares", type=float, default=FROZEN_TAKER_SHARES)
    ap.add_argument("--maker-shares", type=float, default=FROZEN_MAKER_SHARES)
    ap.add_argument("--maker-refresh-sec", type=float, default=15.0)
    ap.add_argument("--order-ttl-min", type=float, default=15.0)
    ap.add_argument("--max-city-days-per-bj-day", type=int, default=10)
    ap.add_argument("--max-daily-cost-usd", type=float, default=100.0)
    ap.add_argument("--continuous-market-max-books-per-cycle", type=int, default=64)
    ap.add_argument("--near-core-maker-probe-enabled", action="store_true")
    ap.add_argument(
        "--confirm-near-core-maker-probe-live", action="store_true"
    )
    ap.add_argument(
        "--near-core-maker-shares",
        type=float,
        default=near_core_maker_probe.FIXED_SHARES,
    )
    ap.add_argument("--near-core-order-ttl-min", type=float, default=15.0)
    ap.add_argument("--near-core-candidate-max-age-sec", type=float, default=30.0)
    ap.add_argument("--near-core-book-max-age-sec", type=float, default=10.0)
    ap.add_argument(
        "--near-core-max-city-days-per-bj-day", type=int, default=10
    )
    ap.add_argument("--near-core-max-daily-cost-usd", type=float, default=50.0)
    ap.add_argument("--near-core-ws-control-manifest", default="")
    ap.add_argument("--executor-timeout-sec", type=float, default=60.0)
    ap.add_argument("--market-proxy", default=None)
    ap.add_argument("--runtime-db", default=str(ROOT / "runtime/weather.db"))
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--confirm-live", action="store_true")
    return ap


def publish_loop_error(args: argparse.Namespace, exc: Exception) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    error = {
        "status": "error",
        "generated_at_utc": utc_now(),
        "strategy_instance": STRATEGY_INSTANCE,
        "mode": "tiny_live" if args.live else "paper_would_order",
        "live_enabled": bool(args.live and args.confirm_live),
        "error": f"{type(exc).__name__}: {exc}",
    }
    publish_runtime_state_best_effort(args, output_dir, error, {})
    write_json(output_dir / "latest_summary.json", error)
    append_jsonl(output_dir / "summary_history.jsonl", error)
    return error


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ModuleNotFoundError:
        pass
    args = parser().parse_args()
    if args.command == "run":
        print(json.dumps(run_once(args), ensure_ascii=False, sort_keys=True))
        return 0
    while True:
        try:
            print(json.dumps(run_once(args), ensure_ascii=False, sort_keys=True), flush=True)
        except Exception as exc:  # noqa: BLE001
            error = publish_loop_error(args, exc)
            print(json.dumps(error, ensure_ascii=False, sort_keys=True), flush=True)
        time.sleep(max(10.0, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
