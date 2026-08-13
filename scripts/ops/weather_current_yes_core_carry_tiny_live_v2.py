#!/usr/bin/env python3
"""Tiny-live adapter for the current-YES residual carry probe.

The signal/checkpoint contract remains owned by
``weather_current_yes_core_carry_pre_live_v1.py`` and the no-age/no-peak-clock
v3 artifact.
For each first positive-EV city-day signal this adapter submits three separately
attributed children:

* taker: fresh full-ladder ten-share EV is revalidated immediately before send;
* staged maker: five shares at best bid + one tick, held in queue for five minutes,
  then repriced at most once at the midpoint stage and once at the near-ask
  stage while retaining one cent of model edge.
* pullback maker: five shares at the entry ask minus two cents, held without
  repricing for at most 15 minutes.

Maker replacements never cross the ask and never convert to taker. They are
cancelled 90 seconds before the next expected source report, when an unexpected
observation epoch arrives, or when the 15-minute parent TTL expires.  The clock
is intentionally based on source-report time rather than this collector's later
availability: another participant may receive the report first.  A maker first
seen inside that blackout is deferred until a new weather epoch is observed,
then re-scored against the frozen Core model and a fresh executable ladder.  It
is armed only when the exact-bracket token is unchanged, taker net-EV remains
positive, its limit is below both the current and parent taker ask, and the
original city-day remains below the fixed twenty-share cap.
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
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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
from scripts.ops.weather_market_proxy import market_httpx_client  # noqa: E402
from src.strategies.runtime import runtime_state  # noqa: E402
from src.strategies.weather_edge_v1.execution.engine import (  # noqa: E402
    allocate_profile_shares,
    build_core_carry_legacy_plan_compatibility,
)
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


STRATEGY_ID = "current_yes_core_carry_v3"
STRATEGY_INSTANCE = "current_yes_core_carry_tiny_live_v2"
CONFIG_ID = "current_yes_core_carry_model_v3_split_10_taker_5_staged_5_pullback_rearm_v6"
EXECUTION_PROFILE = "split_taker_two_maker_event_rearmed_no_fallback_v6"
DEPLOYMENT_CONTRACT_VERSION = "core_carry_v3_shared_order_runtime_10t5m5m_dual_maker_rearm_v6"
MODEL_VERSION = "current_yes_core_carry_model_v3_no_peak_clock"
FROZEN_TAKER_SHARES = 10.0
FROZEN_MAKER_SHARES = 5.0
FROZEN_PULLBACK_MAKER_SHARES = 5.0
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
CRITICAL_SOURCE_PATHS = (
    "scripts/ops/weather_current_yes_core_carry_tiny_live_v2.py",
    "scripts/ops/weather_current_yes_core_carry_pre_live_v1.py",
    "scripts/ops/weather_current_yes_heat_death_shadow_v1.py",
    "scripts/ops/weather_core_carry_order_runtime.py",
    "scripts/ops/weather_polymarket_live_transport.py",
    "weather_data_feed/observation_cache.py",
    "weather_data_feed/physical_features.py",
    "weather_feature_layer/builders.py",
    "src/strategies/weather_edge_v1/execution/contracts.py",
    "src/strategies/weather_edge_v1/execution/engine.py",
    "src/strategies/weather_edge_v1/execution/profiles.py",
    "src/strategies/weather_edge_v1/execution/lifecycle.py",
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
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def stable_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


MAKER_STATE_SCHEMA_VERSION = "core_carry_maker_weather_state_v1"
MAKER_STATE_EPOCH_PREFIX = "core-weather-state-v1:"


def forecast_curve_hash(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("forecast_values_hash") or "").strip()
    if explicit:
        return explicit
    curve = row.get("hourly_curve")
    if not isinstance(curve, list) or not curve:
        return ""
    return stable_hash({"hourly_curve": curve})


def weather_state_signature(row: Mapping[str, Any]) -> dict[str, str]:
    return {
        "observation_epoch": str(row.get("source_report_ts_utc") or ""),
        "observation_source": str(row.get("obs_source") or ""),
        "forecast_curve_hash": forecast_curve_hash(row),
        "forecast_source": str(row.get("forecast_source") or ""),
        "bracket": str(row.get("current_bracket") or row.get("bracket") or ""),
        "token_id": str(
            row.get("current_yes_token_id") or row.get("token_id") or ""
        ),
    }


def weather_state_epoch_ref(row: Mapping[str, Any]) -> str:
    signature = weather_state_signature(row)
    if not signature["observation_epoch"] or not signature["forecast_curve_hash"]:
        return ""
    return MAKER_STATE_EPOCH_PREFIX + stable_hash(signature)


def weather_state_transition_types(
    order: Mapping[str, Any], latest: Mapping[str, Any]
) -> list[str]:
    previous = {
        "observation_epoch": str(order.get("source_report_ts_utc") or ""),
        "observation_source": str(order.get("maker_observation_source") or ""),
        "forecast_curve_hash": str(order.get("maker_forecast_curve_hash") or ""),
        "forecast_source": str(order.get("maker_forecast_source") or ""),
        "bracket": str(order.get("bracket") or ""),
        "token_id": str(order.get("token_id") or ""),
    }
    current = weather_state_signature(latest)
    transitions: list[str] = []
    if (
        current["observation_epoch"] != previous["observation_epoch"]
        or current["observation_source"] != previous["observation_source"]
    ):
        source = current["observation_source"].lower()
        transitions.append(
            "new_metar"
            if "metar" in source or "aviation" in source
            else "new_observation"
        )
    if (
        current["forecast_curve_hash"] != previous["forecast_curve_hash"]
        or current["forecast_source"] != previous["forecast_source"]
    ):
        transitions.append("forecast_revision")
    if (current["bracket"], current["token_id"]) != (
        previous["bracket"],
        previous["token_id"],
    ):
        transitions.append("exact_bracket_transition")
    return transitions


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


def line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def next_runtime_telemetry_rows(conn: sqlite3.Connection) -> int:
    """Advance the operational counter without rescanning the growing journal."""

    row = conn.execute(
        "SELECT telemetry_rows FROM strategy_instance_runtime WHERE instance_id = ?",
        (STRATEGY_INSTANCE,),
    ).fetchone()
    return int(row[0] or 0) + 1 if row else 1


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
            telemetry_rows=next_runtime_telemetry_rows(conn),
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
        for key in ("order_id", "orderID", "clob_order_id", "id"):
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
    latest: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(path):
        key = str(row.get("checkpoint_key") or "")
        if key:
            latest[key] = row
    return latest


def latest_weather_epochs(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}
    for row in iter_jsonl(path):
        city = str(row.get("city") or "")
        target_date = str(row.get("target_date") or "")
        stamp = parse_utc(row.get("decision_snapshot_ts_utc"))
        if not city or not target_date or stamp is None:
            continue
        key = (city, target_date)
        if key not in latest or stamp > latest[key][0]:
            latest[key] = (stamp, row)
    return {key: row for key, (_stamp, row) in latest.items()}


def maker_lifecycle_root(row: Mapping[str, Any]) -> str:
    root_created = str(row.get("maker_lifecycle_root_created_at_utc") or "")
    signal = str(row.get("signal_id") or "")
    comparison = str(row.get("comparison_group_id") or "")
    maker_arm = str(row.get("maker_arm") or "legacy_staged")
    return "|".join((signal, comparison, maker_arm, root_created))


def maker_lifecycle_heads(path: Path) -> list[dict[str, Any]]:
    """Return the latest journal row for every maker intent."""

    heads: dict[str, tuple[datetime, int, dict[str, Any]]] = {}
    for index, row in enumerate(iter_jsonl(path)):
        if (
            str(row.get("strategy_instance") or "") != STRATEGY_INSTANCE
            or not bool(row.get("maker_only"))
        ):
            continue
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
    if str(row.get("maker_arm") or "") == "pullback":
        return False
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
        and str(row.get("city") or "")
        and str(row.get("target_date") or "")
    }


def attempted_signal_ids(output_dir: Path) -> set[str]:
    attempted = {
        str(row.get("signal_id") or "")
        for row in iter_jsonl(output_dir / "entry_attempts.jsonl")
        if str(row.get("signal_id") or "")
        and str(row.get("status") or "") == "blocked"
    }
    attempted.update(
        str(row.get("signal_id") or "")
        for row in iter_jsonl(output_dir / "live_orders.jsonl")
        if str(row.get("signal_id") or "")
    )
    return attempted


def attempted_child_roles(
    output_dir: Path, *, now: datetime | None = None
) -> dict[str, set[str]]:
    """Return terminally attempted entry children, without collapsing the batch.

    A taker outcome must not consume a maker child that never reached a durable
    order outcome.  Conversely, any projected maker outcome is owned by the
    maker lifecycle and must not be recreated by the entry path.
    """

    attempted: dict[str, set[str]] = {}
    for row in iter_jsonl(output_dir / "entry_attempts.jsonl"):
        sid = str(row.get("signal_id") or "")
        if not sid:
            continue
        if str(row.get("status") or "") == "blocked":
            attempted.setdefault(sid, set()).update(
                {"taker", "maker_staged", "maker_pullback"}
            )
        elif str(row.get("maker_live_action") or "") == "skip_terminal":
            if bool(row.get("maker_rearm_terminal")):
                attempted.setdefault(sid, set()).update(
                    {"maker_staged", "maker_pullback"}
                )
                continue
            created = parse_utc(row.get("created_at_utc"))
            rearm_enabled = bool(
                get_execution_profile(EXECUTION_PROFILE).fixed_parameters.get(
                    "post_update_live_rearm"
                )
            )
            rearm_max_age = maker_profile_parameter("post_update_rearm_max_age_sec")
            if (
                rearm_enabled
                and now is not None
                and created is not None
                and 0 <= (now - created).total_seconds() <= rearm_max_age
            ):
                # A pre-v6 terminal skip inside the bounded migration window is
                # reinterpreted as deferred.  It still requires a newer weather
                # epoch and a fresh positive-EV score before any order exists.
                continue
            arm = str(row.get("maker_arm") or "")
            if arm in {"staged", "pullback"}:
                attempted.setdefault(sid, set()).add(f"maker_{arm}")
            else:
                attempted.setdefault(sid, set()).update(
                    {"maker_staged", "maker_pullback"}
                )
    for row in iter_jsonl(output_dir / "live_orders.jsonl"):
        sid = str(row.get("signal_id") or "")
        if not sid:
            continue
        role = str(row.get("child_order_role") or "")
        if role == "taker":
            attempted.setdefault(sid, set()).add("taker")
        elif role == "maker":
            # Legacy single-maker signals must not receive a retrospective
            # pullback child when the dual-maker profile is deployed.
            attempted.setdefault(sid, set()).update(
                {"maker_staged", "maker_pullback"}
            )
        elif role in {"maker_staged", "maker_pullback"}:
            attempted.setdefault(sid, set()).add(role)
        elif role.startswith("core_carry_maker_"):
            arm = str(row.get("maker_arm") or "")
            if arm in {"staged", "pullback"}:
                attempted.setdefault(sid, set()).add(f"maker_{arm}")
            else:
                attempted.setdefault(sid, set()).update(
                    {"maker_staged", "maker_pullback"}
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
    return (
        float(args.taker_shares)
        + float(args.maker_shares)
        + float(args.pullback_maker_shares)
    ) * ask


def entry_plan_cost_reservation(plans: Iterable[Mapping[str, Any]]) -> float:
    """Reserve only children that can actually reach the executor."""

    return sum(finite(plan.get("order_notional_cap")) or 0.0 for plan in plans)


def max_live_child_notional_usd(args: argparse.Namespace) -> float:
    """Maximum principal for one child at the binary-market price ceiling."""

    return max(
        float(args.taker_shares),
        float(args.maker_shares),
        float(args.pullback_maker_shares),
    )


def maker_profile_parameter(name: str) -> float:
    profile = get_execution_profile(EXECUTION_PROFILE)
    value = finite(profile.fixed_parameters.get(name))
    if value is None:
        raise RuntimeError(f"missing numeric maker profile parameter: {name}")
    return value


def maker_max_reprices(maker_arm: str = "staged") -> int:
    profile = get_execution_profile(EXECUTION_PROFILE)
    role = "maker_pullback" if maker_arm == "pullback" else "maker_staged"
    maker_leg = next((leg for leg in profile.legs if leg.role == role), None)
    if maker_leg is None or maker_leg.max_reprices is None:
        raise RuntimeError("core carry maker profile requires a finite max_reprices")
    return int(maker_leg.max_reprices)


def maker_edge_price_cap(
    *,
    best_ask: float,
    tick_size: float,
    model_probability: float,
    parent_taker_ask: float | None = None,
) -> float:
    retained_edge = maker_profile_parameter("retained_edge")
    improvement_ticks = maker_profile_parameter("minimum_taker_improvement_ticks")
    tick = Decimal(str(tick_size))
    caps = [
        Decimal(str(model_probability)) - Decimal(str(retained_edge)),
        Decimal(str(best_ask)) - Decimal(str(improvement_ticks)) * tick,
    ]
    if parent_taker_ask is not None and parent_taker_ask > 0:
        caps.append(
            Decimal(str(parent_taker_ask))
            - Decimal(str(improvement_ticks)) * tick
        )
    raw_cap = min(caps)
    if raw_cap <= 0 or tick <= 0:
        return 0.0
    return float((raw_cap // tick) * tick)


def pullback_maker_resting_price(
    *, best_ask: float, tick_size: float, price_cap: float
) -> float:
    tick = Decimal(str(tick_size))
    if tick <= 0:
        return 0.0
    raw = min(
        Decimal(str(best_ask)) - Decimal(str(maker_profile_parameter("pullback_offset"))),
        Decimal(str(price_cap)),
    )
    if raw <= 0:
        return 0.0
    return float((raw // tick) * tick)


def maker_clock_assessment(
    row: Mapping[str, Any],
    *,
    now: datetime,
    order_ttl_min: float,
) -> dict[str, Any]:
    profile = get_execution_profile(EXECUTION_PROFILE)
    source_epoch = parse_utc(row.get("source_report_ts_utc"))
    cadence_min = finite(row.get("observation_cadence_min"))
    state_signature = weather_state_signature(row)
    state_epoch_ref = weather_state_epoch_ref(row)
    common = {
        "maker_clock_basis": str(profile.fixed_parameters.get("clock_basis") or ""),
        "maker_post_update_live_rearm": bool(
            profile.fixed_parameters.get("post_update_live_rearm")
        ),
        "maker_post_update_shadow_revalidation": bool(
            profile.fixed_parameters.get("post_update_shadow_revalidation")
        ),
        "post_update_reprice_required": False,
        "maker_state_schema_version": MAKER_STATE_SCHEMA_VERSION,
        "maker_observation_source": state_signature["observation_source"],
        "maker_forecast_curve_hash": state_signature["forecast_curve_hash"],
        "maker_forecast_source": state_signature["forecast_source"],
        "maker_state_epoch_components": str(
            profile.fixed_parameters.get("state_epoch_components") or ""
        ),
        "maker_replacement_price_policy": str(
            profile.fixed_parameters.get("replacement_price_policy") or ""
        ),
    }
    if (
        source_epoch is None
        or cadence_min is None
        or cadence_min <= 0
        or not state_epoch_ref
    ):
        return {
            **common,
            "maker_clock_status": "invalid_source_clock",
            "maker_live_eligible": False,
            "maker_live_skip_reason": (
                "missing_event_state_signature"
                if not state_epoch_ref
                else "missing_source_epoch_or_cadence"
            ),
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
        "data_epoch_ref": state_epoch_ref,
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
        parent_taker_ask=finite(row.get("maker_rearm_parent_taker_ask")),
    )
    sid = signal_id(row)
    maker = child_order_role == "maker" or child_order_role.startswith("maker_")
    maker_arm = (
        "pullback" if child_order_role == "maker_pullback" else "staged"
    ) if maker else ""
    limit = (
        pullback_maker_resting_price(
            best_ask=ask,
            tick_size=tick,
            price_cap=maker_cap,
        )
        if maker_arm == "pullback"
        else maker_resting_price(
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
        "execution_mode": "tiny_live_split_10_taker_5_staged_5_pullback",
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
        "maker_arm": maker_arm,
        "maker_experiment_id": str(
            get_execution_profile(EXECUTION_PROFILE).fixed_parameters.get(
                "maker_experiment_id"
            )
            or ""
        ) if maker else "",
        "execution_policy": (
            "current_yes_residual_carry_pullback_maker_v1"
            if maker_arm == "pullback"
            else "current_yes_residual_carry_staged_maker_v3"
            if maker
            else "current_yes_residual_carry_taker_v1"
        ),
        "order_lifecycle_policy": (
            "maker_event_validated_static_pullback_until_update_or_ttl_v1"
            if maker_arm == "pullback"
            else "maker_event_validated_staged_until_update_or_ttl_v3"
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
            "entry_ask_minus_2c_static_post_only_edge_capped"
            if maker_arm == "pullback"
            else "fresh_bid_improve_one_tick_post_only_retained_edge_capped"
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
        "maker_rearm_parent_taker_ask": (
            finite(row.get("maker_rearm_parent_taker_ask")) or 0.0
        ),
        "maker_rearm_state_ref": str(row.get("maker_rearm_state_ref") or ""),
        "maker_rearm_model_edge_after_fee_and_depth": (
            finite(row.get("maker_rearm_model_edge_after_fee_and_depth")) or 0.0
        ),
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
        "maker_last_reprice_stage": "",
        **maker_clock,
    }


def build_entry_plans(
    row: Mapping[str, Any],
    *,
    live_enabled: bool,
    now: datetime,
    taker_shares: float,
    maker_shares: float,
    pullback_maker_shares: float = FROZEN_PULLBACK_MAKER_SHARES,
    order_ttl_min: float,
) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    sid = signal_id(row)
    profile = get_execution_profile(EXECUTION_PROFILE)
    allocation = allocate_profile_shares(
        profile=profile,
        total_shares=taker_shares + maker_shares + pullback_maker_shares,
        leg_share_overrides={
            "taker": taker_shares,
            "maker_staged": maker_shares,
            "maker_pullback": pullback_maker_shares,
        },
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
        if role.startswith("maker_") and (
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


def execute_plans(args: argparse.Namespace, plans: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    plans_path = output_dir / "current_plans.jsonl"
    write_jsonl(plans_path, plans)
    result = execute_core_carry_plans(
        plans=plans,
        output_dir=output_dir,
        live=bool(args.live),
        market_proxy=args.market_proxy,
        max_child_shares=max_live_child_notional_usd(args),
        max_batch_cost_usd=float(args.max_daily_cost_usd),
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
    decision_best_bid: float = 0.0,
    decision_best_ask: float = 0.0,
    decision_tick_size: float = 0.0,
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
            order.get("execution_policy")
            or "current_yes_residual_carry_staged_maker_v3"
        ),
        "order_lifecycle_policy": str(
            order.get("order_lifecycle_policy")
            or "maker_event_validated_staged_until_update_or_ttl_v3"
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
        "maker_reprice_decision_best_bid": round(decision_best_bid, 6),
        "maker_reprice_decision_best_ask": round(decision_best_ask, 6),
        "maker_reprice_decision_tick_size": round(decision_tick_size, 6),
        "maker_replacement_max_quote_drift_ticks": (
            maker_profile_parameter("replacement_max_quote_drift_ticks")
        ),
    }
    fields["plan_id"] = "plan-" + stable_hash(
        {
            "source_order_id": lineage_source_id,
            "maker_arm": str(order.get("maker_arm") or "staged"),
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
            source_state_ref = str(
                order.get("data_epoch_ref") or order.get("source_report_ts_utc") or ""
            )
            latest_state_ref = weather_state_epoch_ref(latest or {})
            event_state_managed = (
                str(order.get("maker_state_schema_version") or "")
                == MAKER_STATE_SCHEMA_VERSION
                and source_state_ref.startswith(MAKER_STATE_EPOCH_PREFIX)
            )
            state_transitions = (
                weather_state_transition_types(order, latest)
                if event_state_managed and latest
                else []
            )
            deadline = parse_utc(order.get("maker_lifecycle_deadline_utc") or order.get("expires_at_utc"))
            action = ""
            blocker = ""
            next_price = 0.0
            best_bid = 0.0
            best_ask = 0.0
            tick = 0.0
            cancel_only = False
            reprice_stage = ""
            reprice_count = int(
                finite(order.get("maker_lifecycle_reprice_count")) or 0
            )
            last_reprice_stage = str(order.get("maker_last_reprice_stage") or "")
            maker_arm = str(order.get("maker_arm") or "staged")
            max_reprices = maker_max_reprices(maker_arm)
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
            elif event_state_managed and (
                not latest_state_ref or latest_state_ref != source_state_ref
            ):
                blocker = (
                    "latest_weather_state_unavailable"
                    if not latest_state_ref
                    else "weather_state_changed_requires_fresh_score"
                )
                if active_order:
                    action = "core_carry_maker_cancel_weather_state"
                    cancel_only = True
            elif not event_state_managed and (
                not latest or not latest_epoch or latest_epoch != source_epoch
            ):
                if not latest or not latest_epoch:
                    blocker = "latest_observation_state_unavailable"
                else:
                    blocker = "new_observation_requires_fresh_entry"
                if active_order:
                    action = "core_carry_maker_cancel_new_observation"
                    cancel_only = True
            elif maker_arm == "pullback":
                blocker = "pullback_static_resting_no_reprice"
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
                "source_weather_state_ref": source_state_ref,
                "latest_weather_state_ref": latest_state_ref,
                "weather_state_transition_types": state_transitions,
                "event_state_managed": event_state_managed,
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
                "maker_arm": maker_arm,
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
                    decision_best_bid=best_bid,
                    decision_best_ask=best_ask,
                    decision_tick_size=tick,
                    now=now,
                    live_enabled=bool(args.live and args.confirm_live),
                )
                plans.append(plan)
    return plans, decisions


def maker_rearm_attempts(output_dir: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """Return the first defer and latest rearm evaluation for each signal."""

    attempts: dict[str, dict[str, dict[str, Any]]] = {}
    for row in iter_jsonl(output_dir / "entry_attempts.jsonl"):
        sid = str(row.get("signal_id") or "")
        if not sid:
            continue
        action = str(row.get("maker_live_action") or "")
        if action not in {"skip_terminal", "defer_post_update_rearm"}:
            continue
        bucket = attempts.setdefault(sid, {})
        if "deferred" not in bucket:
            bucket["deferred"] = row
        bucket["latest"] = row
    return attempts


def post_update_maker_rearm_score(
    *,
    args: argparse.Namespace,
    output_dir: Path,
    original_score: Mapping[str, Any],
    deferred_attempt: Mapping[str, Any],
    latest_attempt: Mapping[str, Any],
    now: datetime,
) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
    """Re-score a deferred Core maker on a strictly newer weather epoch.

    This does not create a new Core signal.  It only gives the missing maker
    children of an already selected city-day one bounded chance per new state.
    """

    created = parse_utc(deferred_attempt.get("created_at_utc"))
    max_age_sec = maker_profile_parameter("post_update_rearm_max_age_sec")
    common = {
        "maker_rearm_parent_taker_ask": (
            finite(original_score.get("current_yes_ask")) or 0.0
        ),
        "maker_rearm_original_state_ref": str(
            deferred_attempt.get("data_epoch_ref") or ""
        ),
        "maker_rearm_max_age_sec": max_age_sec,
    }
    if created is None or (now - created).total_seconds() > max_age_sec:
        return "terminal", None, {
            **common,
            "maker_rearm_status": "expired",
            "maker_rearm_terminal": True,
            "maker_rearm_reason": "post_update_rearm_window_expired",
        }

    city_day = (
        str(original_score.get("city") or ""),
        str(original_score.get("target_date") or ""),
    )
    latest = latest_weather_epochs(output_dir / "state_decisions.jsonl").get(city_day)
    if latest is None:
        return "waiting", None, {
            **common,
            "maker_rearm_status": "waiting",
            "maker_rearm_reason": "latest_weather_state_unavailable",
        }
    original_ref = str(deferred_attempt.get("data_epoch_ref") or "")
    latest_ref = weather_state_epoch_ref(latest)
    if not latest_ref or latest_ref == original_ref:
        return "waiting", None, {
            **common,
            "maker_rearm_status": "waiting",
            "maker_rearm_state_ref": latest_ref,
            "maker_rearm_reason": "new_weather_epoch_not_observed",
        }
    if str(latest_attempt.get("maker_rearm_evaluated_state_ref") or "") == latest_ref:
        return "waiting", None, {
            **common,
            "maker_rearm_status": "waiting",
            "maker_rearm_state_ref": latest_ref,
            "maker_rearm_reason": "weather_epoch_already_re_scored",
        }

    original_token = str(
        original_score.get("current_yes_token_id")
        or original_score.get("token_id")
        or ""
    )
    latest_token = str(
        latest.get("current_yes_token_id") or latest.get("token_id") or ""
    )
    original_bracket = str(
        original_score.get("current_bracket") or original_score.get("bracket") or ""
    )
    latest_bracket = str(
        latest.get("current_bracket") or latest.get("bracket") or ""
    )
    if latest_token != original_token or latest_bracket != original_bracket:
        return "terminal", None, {
            **common,
            "maker_rearm_status": "invalidated",
            "maker_rearm_terminal": True,
            "maker_rearm_evaluated_state_ref": latest_ref,
            "maker_rearm_reason": "exact_bracket_or_token_changed",
        }

    freshness_ok, freshness_reason = signal_runner.observation_freshness_valid(latest)
    if not freshness_ok:
        return "evaluated", None, {
            **common,
            "maker_rearm_status": "not_eligible",
            "maker_rearm_evaluated_state_ref": latest_ref,
            "maker_rearm_reason": freshness_reason,
        }

    with market_httpx_client(
        args.book_proxy, timeout=float(args.book_timeout_sec)
    ) as client:
        book = signal_runner.fetch_full_book(client, latest_token)
    enriched = {
        **dict(latest),
        "current_yes_bid": book.get("bid"),
        "current_yes_ask": book.get("ask"),
        "current_yes_bid_size": book.get("bid_size"),
        "current_yes_ask_size": book.get("ask_size"),
        "current_yes_tick_size": book.get("tick_size"),
        "current_yes_book_status": book.get("status"),
        "current_yes_book_fetched_at_utc": book.get("fetched_at_utc"),
        "checkpoint_key": str(original_score.get("checkpoint_key") or ""),
        "artifact_hash": str(original_score.get("artifact_hash") or ""),
    }
    result = evaluate_entry(
        enriched,
        book.get("asks") or [],
        load_artifact(Path(args.artifact)),
    )
    meta = {
        **common,
        "maker_rearm_status": "eligible" if bool(result.get("eligible")) else "not_eligible",
        "maker_rearm_evaluated_state_ref": latest_ref,
        "maker_rearm_state_ref": latest_ref,
        "maker_rearm_reason": (
            "fresh_positive_taker_net_ev"
            if bool(result.get("eligible"))
            else ",".join(map(str, result.get("reasons") or ["core_not_eligible"]))
        ),
        "maker_rearm_model_edge_after_fee_and_depth": (
            finite(result.get("model_edge_after_fee_and_depth")) or 0.0
        ),
        "maker_rearm_current_taker_ask": finite(book.get("ask")) or 0.0,
    }
    if not bool(result.get("eligible")):
        return "evaluated", None, meta
    return "eligible", {**enriched, **result, **meta}, meta


def new_entry_plans(
    args: argparse.Namespace,
    output_dir: Path,
    *,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    scores = latest_rows_by_checkpoint(output_dir / "pre_live_scores.jsonl")
    attempted_roles = attempted_child_roles(output_dir, now=now)
    rearm_attempts = maker_rearm_attempts(output_dir)
    family_paths = family_live_order_files(output_dir)
    family_city_days = submitted_city_days(family_paths)
    used_city_days, used_cost = daily_family_usage(family_paths, now)
    plans: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    for would in iter_jsonl(output_dir / "would_orders.jsonl"):
        row = scores.get(str(would.get("checkpoint_key") or ""))
        if row is None:
            continue
        sid = signal_id(row)
        city_day = (str(row.get("city") or ""), str(row.get("target_date") or ""))
        existing_roles = attempted_roles.get(sid, set())
        expected_roles = {"taker", "maker_staged", "maker_pullback"}
        if expected_roles.issubset(existing_roles):
            continue
        planning_row: Mapping[str, Any] = row
        rearm_meta: dict[str, Any] = {}
        missing_maker_roles = {
            "maker_staged",
            "maker_pullback",
        } - existing_roles
        if "taker" in existing_roles and missing_maker_roles:
            history = rearm_attempts.get(sid, {})
            deferred = history.get("deferred")
            latest_attempt = history.get("latest", deferred or {})
            if deferred is None:
                # Missing maker children without an explicit clock defer are not
                # a post-update rearm.  Preserve the existing same-epoch recovery
                # path for a taker-only partial batch.
                rearm_status, rearmed_row = "same_epoch_recovery", None
            else:
                rearm_status, rearmed_row, rearm_meta = post_update_maker_rearm_score(
                    args=args,
                    output_dir=output_dir,
                    original_score=row,
                    deferred_attempt=deferred,
                    latest_attempt=latest_attempt,
                    now=now,
                )
            if rearm_status == "waiting":
                continue
            if rearm_status in {"evaluated", "terminal"}:
                attempts.append(
                    {
                        "record_type": "current_yes_core_carry_entry_attempt",
                        "created_at_utc": now.isoformat(timespec="seconds"),
                        "signal_id": sid,
                        "city": city_day[0],
                        "target_date": city_day[1],
                        "checkpoint_key": row.get("checkpoint_key"),
                        "model_probability_hold": row.get("model_probability_hold"),
                        "status": "planned",
                        "reason": "",
                        "live_enabled": bool(args.live and args.confirm_live),
                        "maker_requested_shares": float(args.maker_shares)
                        + float(args.pullback_maker_shares),
                        "maker_planned_shares": 0.0,
                        "staged_maker_planned": False,
                        "pullback_maker_planned": False,
                        "maker_live_action": (
                            "skip_terminal"
                            if rearm_status == "terminal"
                            else "defer_post_update_rearm"
                        ),
                        "maker_post_update_live_rearm": True,
                        **rearm_meta,
                    }
                )
                continue
            if rearm_status == "eligible" and rearmed_row is None:
                continue
            if rearmed_row is not None:
                planning_row = rearmed_row
        reason = ""
        if not existing_roles and (
            bool(would.get("family_city_day_conflict")) or city_day in family_city_days
        ):
            reason = "family_city_day_conflict"
        elif not existing_roles and used_city_days >= int(args.max_city_days_per_bj_day):
            reason = "daily_city_day_cap"
        entry_plans = (
            []
            if reason
            else build_entry_plans(
                planning_row,
                live_enabled=bool(args.live and args.confirm_live),
                now=now,
                taker_shares=float(args.taker_shares),
                maker_shares=float(args.maker_shares),
                pullback_maker_shares=float(args.pullback_maker_shares),
                order_ttl_min=float(args.order_ttl_min),
            )
        )
        entry_plans = [
            plan
            for plan in entry_plans
            if str(plan.get("child_order_role") or "") not in existing_roles
        ]
        if (
            not reason
            and "taker" not in existing_roles
            and not any(plan.get("child_order_role") == "taker" for plan in entry_plans)
        ):
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
            planning_row,
            now=now,
            order_ttl_min=float(args.order_ttl_min),
        )
        maker_planned_roles = {
            str(plan.get("child_order_role") or "")
            for plan in entry_plans
            if str(plan.get("child_order_role") or "").startswith("maker_")
        }
        attempts.append(
            {
                "record_type": "current_yes_core_carry_entry_attempt",
                "created_at_utc": now.isoformat(timespec="seconds"),
                "signal_id": sid,
                "city": city_day[0],
                "target_date": city_day[1],
                "checkpoint_key": row.get("checkpoint_key"),
                "model_probability_hold": row.get("model_probability_hold"),
                "status": "blocked" if reason else "planned",
                "reason": reason,
                "live_enabled": bool(args.live and args.confirm_live),
                "maker_requested_shares": float(args.maker_shares)
                + float(args.pullback_maker_shares),
                "maker_planned_shares": sum(
                    float(plan.get("size") or 0.0)
                    for plan in entry_plans
                    if str(plan.get("child_order_role") or "").startswith("maker_")
                ),
                "staged_maker_planned": "maker_staged" in maker_planned_roles,
                "pullback_maker_planned": "maker_pullback" in maker_planned_roles,
                "maker_live_action": (
                    "entry_blocked"
                    if reason
                    else (
                        "post"
                        if maker_planned_roles
                        else (
                            "defer_post_update_rearm"
                            if bool(maker_clock.get("maker_post_update_live_rearm"))
                            and maker_clock.get("maker_clock_status")
                            == "pre_source_report_blackout"
                            else "skip_terminal"
                        )
                    )
                ),
                "maker_shadow_revalidation_shares": (
                    float(args.maker_shares) + float(args.pullback_maker_shares)
                    if not reason
                    and not maker_planned_roles
                    and maker_clock.get("maker_clock_status")
                    == "pre_source_report_blackout"
                    else 0.0
                ),
                **maker_clock,
                **rearm_meta,
            }
        )
        attempted_roles.setdefault(sid, set()).update(
            str(plan.get("child_order_role") or "") for plan in entry_plans
        )
        defer_missing_makers = bool(
            maker_clock.get("maker_post_update_live_rearm")
        ) and maker_clock.get("maker_clock_status") == "pre_source_report_blackout"
        if not reason and not defer_missing_makers:
            for maker_role in {"maker_staged", "maker_pullback"} - maker_planned_roles:
                attempted_roles[sid].add(maker_role)
        if entry_plans:
            plans.extend(entry_plans)
            if not existing_roles:
                used_city_days += 1
            used_cost += planned_cost
    return plans, attempts


def configure_signal_runner(output_dir: Path) -> None:
    signal_runner.STRATEGY_ID = STRATEGY_ID
    signal_runner.STRATEGY_INSTANCE = STRATEGY_INSTANCE
    signal_runner.DECISION_MODE = "frozen_core_probability_first_positive_ten_share_taker_ev"
    signal_runner.OUTPUT_DIR = output_dir
    signal_runner.ARTIFACT_PATH = ARTIFACT_PATH


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    assert_runtime_contract()
    if args.live and not args.confirm_live:
        raise RuntimeError("--live requires --confirm-live")
    if (
        float(args.taker_shares) != FROZEN_TAKER_SHARES
        or float(args.maker_shares) != FROZEN_MAKER_SHARES
        or float(args.pullback_maker_shares) != FROZEN_PULLBACK_MAKER_SHARES
    ):
        raise RuntimeError(
            "frozen tiny-live split requires exactly 10 taker + 5 staged maker + 5 pullback maker shares"
        )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    configure_signal_runner(output_dir)
    signal_summary = signal_runner.run_once(args)
    now = datetime.now(timezone.utc)
    journal_terminal_recoveries = recover_journal_terminal_makers(output_dir)
    entry_plans, attempts = new_entry_plans(args, output_dir, now=now)
    lifecycle_plans, lifecycle_decisions = maker_lifecycle_plans(args, output_dir, now=now)
    for decision in lifecycle_decisions:
        append_jsonl(output_dir / "maker_lifecycle_decisions.jsonl", decision)
    plans = [*lifecycle_plans, *entry_plans]
    execution = execute_plans(args, plans, output_dir)
    for attempt in attempts:
        append_jsonl(output_dir / "entry_attempts.jsonl", attempt)
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
        "entry_attempts": len(attempts),
        "entry_plans": len(entry_plans),
        "maker_lifecycle_plans": len(lifecycle_plans),
        "maker_lifecycle_decisions": len(lifecycle_decisions),
        "journal_terminal_recoveries": journal_terminal_recoveries,
        "taker_shares": float(args.taker_shares),
        "maker_shares": float(args.maker_shares),
        "pullback_maker_shares": float(args.pullback_maker_shares),
        "maximum_signal_shares": (
            float(args.taker_shares)
            + float(args.maker_shares)
            + float(args.pullback_maker_shares)
        ),
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
    ap.add_argument(
        "--pullback-maker-shares",
        type=float,
        default=FROZEN_PULLBACK_MAKER_SHARES,
    )
    ap.add_argument("--maker-refresh-sec", type=float, default=15.0)
    ap.add_argument("--order-ttl-min", type=float, default=15.0)
    ap.add_argument("--max-city-days-per-bj-day", type=int, default=10)
    ap.add_argument("--max-daily-cost-usd", type=float, default=100.0)
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
