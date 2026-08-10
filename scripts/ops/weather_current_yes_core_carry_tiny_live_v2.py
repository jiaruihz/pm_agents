#!/usr/bin/env python3
"""Tiny-live adapter for the frozen current-YES residual carry v2.

The signal/checkpoint contract remains owned by
``weather_current_yes_core_carry_pre_live_v1.py`` and the no-age v2 artifact.
For each first positive-EV city-day signal this adapter submits two separately
attributed five-share children:

* taker: fresh full-ladder five-share EV is revalidated immediately before send;
* maker: best bid + one tick, chased upward every 15 seconds, capped by the
  trigger-time mid (floored to tick) and model probability.

Maker replacements never cross the ask and never convert to taker.  They stop
at the cap and are cancelled when their observation epoch changes or the
15-minute order TTL expires.  Reprice count is intentionally unlimited inside
those physical/time/price boundaries.
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
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import weather_current_yes_core_carry_pre_live_v1 as signal_runner  # noqa: E402
from scripts.ops import weather_current_yes_heat_death_shadow_v1 as weather_state  # noqa: E402
from scripts.ops.weather_market_proxy import market_httpx_client  # noqa: E402
from src.strategies.runtime import runtime_state  # noqa: E402
from src.strategies.weather_edge_v1.execution.engine import (  # noqa: E402
    CoreCarryLegacyPlanCompatibility,
    LegacyPlanFieldDifference,
    build_core_carry_legacy_plan_compatibility,
    compare_core_carry_legacy_plan_fields,
)
from src.strategies.weather_edge_v1.tools.current_yes_core_carry import (  # noqa: E402
    load_artifact,
)


STRATEGY_ID = "current_yes_core_carry_v2"
STRATEGY_INSTANCE = "current_yes_core_carry_tiny_live_v2"
CONFIG_ID = "current_yes_core_carry_model_v2_split_5_taker_5_maker"
EXECUTION_PROFILE = "current_yes_residual_split_5_taker_5_maker_v1"
OUTPUT_DIR = ROOT / "runtime/weather_edge_v1" / STRATEGY_INSTANCE
ARTIFACT_PATH = (
    ROOT / "src/strategies/weather_edge_v1/config/current_yes_core_carry_model_v2.json"
)
LEGACY_FAMILY_LIVE_ORDER_FILES = (
    ROOT
    / "runtime/weather_edge_v1/current_yes_heat_death_tiny_live_h1_late_carry_v1/live_orders.jsonl",
    ROOT
    / "runtime/weather_edge_v1/current_yes_heat_death_tiny_live_h2_early_dislocation_v1/live_orders.jsonl",
    ROOT / "runtime/weather_edge_v1/current_yes_heat_death_tiny_live_v1/live_orders.jsonl",
)
BJ = timezone(timedelta(hours=8))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


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
    initial_mid = (bid + ask) / 2.0
    mid_cap = math.floor((initial_mid + 1e-12) / tick) * tick
    maker_cap = min(mid_cap, probability)
    sid = signal_id(row)
    maker = child_order_role == "maker"
    limit = min(bid + tick, ask - tick, maker_cap) if maker else ask
    expires = now + timedelta(minutes=order_ttl_min)
    return {
        "strategy": "weather_edge_v1",
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_INSTANCE,
        "config_id": CONFIG_ID,
        "strategy_family": "reheat_risk.current_yes",
        "decision_mode": "frozen_core_first_positive_five_share_taker_ev",
        "execution_mode": "tiny_live_split_5_taker_5_maker",
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
            "current_yes_residual_carry_maker_v1"
            if maker
            else "current_yes_residual_carry_taker_v1"
        ),
        "order_lifecycle_policy": (
            "maker_chase_until_observation_or_ttl_v1" if maker else "taker_now"
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
            "fresh_bid_improve_one_tick_post_only_mid_model_capped"
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
        "model_version": "current_yes_core_carry_model_v2",
        "artifact_hash": str(row.get("artifact_hash") or ""),
        "checkpoint_key": str(row.get("checkpoint_key") or ""),
        "decision_snapshot_ts_utc": str(row.get("decision_snapshot_ts_utc") or ""),
        "source_snapshot_file": str(row.get("snapshot_file") or ""),
        "source_report_ts_utc": str(row.get("source_report_ts_utc") or ""),
        "obs_status": str(row.get("obs_status") or ""),
        "station_gap_state": str(row.get("station_gap_state") or ""),
        "expires_at_utc": expires.isoformat(timespec="seconds"),
        "maker_price_cap": round(maker_cap, 6) if maker else 0.0,
        "maker_lifecycle_root_created_at_utc": now.isoformat(timespec="seconds") if maker else "",
        "maker_lifecycle_deadline_utc": expires.isoformat(timespec="seconds") if maker else "",
        "maker_lifecycle_reprice_count": 0,
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
    for role, shares in (("taker", taker_shares), ("maker", maker_shares)):
        fields = base_plan_fields(
            row,
            child_order_role=role,
            shares=shares,
            live_enabled=live_enabled,
            now=now,
            order_ttl_min=order_ttl_min,
        )
        if role == "maker" and finite(fields["limit_price"]) is not None and fields["limit_price"] <= fields["best_bid"]:
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
    return plans


def build_shared_core_carry_plan_parity(
    legacy_plans: list[dict[str, Any]],
) -> CoreCarryLegacyPlanCompatibility:
    """Test-only, in-memory bridge; ``build_entry_plans`` remains authoritative."""

    return build_core_carry_legacy_plan_compatibility(legacy_plans=legacy_plans)


def compare_shared_core_carry_plan_parity(
    legacy_plans: list[dict[str, Any]],
    compatibility: CoreCarryLegacyPlanCompatibility,
) -> tuple[LegacyPlanFieldDifference, ...]:
    """Test-only in-memory comparator; it never writes journals or plans."""

    return compare_core_carry_legacy_plan_fields(
        legacy_plans=legacy_plans,
        compatibility=compatibility,
    )


def execute_plans(args: argparse.Namespace, plans: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    plans_path = output_dir / "current_plans.jsonl"
    write_jsonl(plans_path, plans)
    command = [
        sys.executable,
        str(ROOT / "scripts/ops/weather_order_executor.py"),
        "--plans",
        str(plans_path),
        "--paper-out",
        str(output_dir / "paper_orders.jsonl"),
        "--live-out",
        str(output_dir / "live_orders.jsonl"),
        "--no-telegram",
    ]
    if args.market_proxy is not None:
        command.extend(["--market-proxy", str(args.market_proxy)])
    if args.live:
        command.extend(["--live", "--confirm-live", "--allow-taker", "--cancel-expired"])
    env = os.environ.copy()
    env["WEATHER_EXECUTOR_MAX_LIVE_ORDER_NOTIONAL_USD"] = "5.00"
    env["WEATHER_EXECUTOR_MAX_LIVE_BATCH_NOTIONAL_USD"] = str(args.max_daily_cost_usd)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=float(args.executor_timeout_sec),
        check=False,
    )
    try:
        result = json.loads(completed.stdout) if completed.stdout.strip() else {}
    except json.JSONDecodeError:
        result = {"stdout": completed.stdout.strip()}
    return {"exit_code": completed.returncode, "result": result, "stderr": completed.stderr.strip()}


def handled_maker_source_ids(path: Path) -> set[str]:
    handled: set[str] = set()
    for row in iter_jsonl(path):
        source_id = str(row.get("source_order_id") or "")
        action = str(row.get("execution_action") or "")
        if not source_id or not action.startswith("core_carry_maker_"):
            continue
        response = row.get("exchange_response") if isinstance(row.get("exchange_response"), Mapping) else {}
        if str(response.get("error_classification") or "") in {
            "pre_place_cancel_not_confirmed",
            "cancel_only_not_confirmed",
        }:
            continue
        handled.add(source_id)
    return handled


def build_maker_lifecycle_plan(
    order: Mapping[str, Any],
    *,
    action: str,
    limit_price: float,
    cancel_only: bool,
    now: datetime,
    live_enabled: bool,
) -> dict[str, Any]:
    source_id = live_order_id(order)
    shares = finite(order.get("size")) or 5.0
    fields = {
        **dict(order),
        "record_type": "weather_edge_trade_plan",
        "created_at_utc": now.isoformat(timespec="seconds"),
        "child_order_role": action,
        "execution_action": action,
        "execution_policy": "current_yes_residual_carry_maker_v1",
        "order_lifecycle_policy": "maker_chase_until_observation_or_ttl_v1",
        "limit_price": round(limit_price, 6),
        "notional": round(shares * limit_price, 6),
        "order_notional_cap": round(shares * limit_price, 6),
        "maker_only": True,
        "paper_enabled": False,
        "live_enabled": bool(live_enabled),
        "cancel_before_order_id": source_id,
        "source_order_id": source_id,
        "source_execution_id": str(order.get("execution_id") or ""),
        "source_plan_id": str(order.get("plan_id") or ""),
        "source_posted_price": finite(order.get("posted_price")) or finite(order.get("limit_price")) or 0.0,
        "source_remaining_shares": shares,
        "replacement_requires_order_state": not cancel_only,
        "cancel_only": cancel_only,
        "allow_duplicate_signal_id": True,
        "maker_lifecycle_reprice_count": int(finite(order.get("maker_lifecycle_reprice_count")) or 0)
        + (0 if cancel_only else 1),
    }
    fields["plan_id"] = "plan-" + stable_hash(
        {
            "source_order_id": source_id,
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
    handled = handled_maker_source_ids(live_orders)
    epochs = latest_weather_epochs(output_dir / "state_decisions.jsonl")
    candidates: list[dict[str, Any]] = []
    for row in iter_jsonl(live_orders):
        if str(row.get("status") or "") != "submitted" or not bool(row.get("maker_only")):
            continue
        order_id = live_order_id(row)
        if not order_id or order_id in handled:
            continue
        created = parse_utc(row.get("created_at_utc"))
        if created is None or (now - created).total_seconds() < float(args.maker_refresh_sec):
            continue
        candidates.append(row)

    plans: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    with market_httpx_client(args.book_proxy, timeout=float(args.book_timeout_sec)) as client:
        for order in candidates:
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
            if deadline is None or now >= deadline:
                action = "core_carry_maker_cancel_ttl"
                cancel_only = True
            elif not latest or not latest_epoch or latest_epoch != source_epoch:
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
                    next_price = min(best_bid + tick, best_ask - tick, cap)
                    if next_price > posted + tick - 1e-9:
                        action = "core_carry_maker_reprice"
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
            }
            decisions.append(decision)
            if action:
                plans.append(
                    build_maker_lifecycle_plan(
                        order,
                        action=action,
                        limit_price=next_price,
                        cancel_only=cancel_only,
                        now=now,
                        live_enabled=bool(args.live and args.confirm_live),
                    )
                )
    return plans, decisions


def new_entry_plans(
    args: argparse.Namespace,
    output_dir: Path,
    *,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    scores = latest_rows_by_checkpoint(output_dir / "pre_live_scores.jsonl")
    attempted = attempted_signal_ids(output_dir)
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
        if sid in attempted:
            continue
        reason = ""
        if bool(would.get("family_city_day_conflict")) or city_day in family_city_days:
            reason = "family_city_day_conflict"
        elif used_city_days >= int(args.max_city_days_per_bj_day):
            reason = "daily_city_day_cap"
        elif used_cost + 10.0 * (finite(row.get("current_yes_ask")) or 1.0) > float(args.max_daily_cost_usd):
            reason = "daily_cost_cap"
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
            }
        )
        attempted.add(sid)
        if entry_plans:
            plans.extend(entry_plans)
            used_city_days += 1
            used_cost += 10.0 * (finite(row.get("current_yes_ask")) or 1.0)
    return plans, attempts


def configure_signal_runner(output_dir: Path) -> None:
    signal_runner.STRATEGY_ID = STRATEGY_ID
    signal_runner.STRATEGY_INSTANCE = STRATEGY_INSTANCE
    signal_runner.OUTPUT_DIR = output_dir
    signal_runner.ARTIFACT_PATH = ARTIFACT_PATH


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    if args.live and not args.confirm_live:
        raise RuntimeError("--live requires --confirm-live")
    if float(args.taker_shares) != 5.0 or float(args.maker_shares) != 5.0:
        raise RuntimeError("frozen tiny-live split requires exactly 5 taker + 5 maker shares")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    configure_signal_runner(output_dir)
    signal_summary = signal_runner.run_once(args)
    now = datetime.now(timezone.utc)
    entry_plans, attempts = new_entry_plans(args, output_dir, now=now)
    for attempt in attempts:
        append_jsonl(output_dir / "entry_attempts.jsonl", attempt)
    lifecycle_plans, lifecycle_decisions = maker_lifecycle_plans(args, output_dir, now=now)
    for decision in lifecycle_decisions:
        append_jsonl(output_dir / "maker_lifecycle_decisions.jsonl", decision)
    plans = [*lifecycle_plans, *entry_plans]
    execution = execute_plans(args, plans, output_dir)
    summary = {
        "status": "ok" if execution["exit_code"] == 0 else "executor_error",
        "generated_at_utc": utc_now(),
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_INSTANCE,
        "config_id": CONFIG_ID,
        "mode": "tiny_live" if args.live else "paper_would_order",
        "live_enabled": bool(args.live and args.confirm_live),
        "artifact_hash": load_artifact(ARTIFACT_PATH)["artifact_hash"],
        "signal_status": signal_summary.get("status"),
        "signal_snapshot_file": signal_summary.get("snapshot_file"),
        "entry_attempts": len(attempts),
        "entry_plans": len(entry_plans),
        "maker_lifecycle_plans": len(lifecycle_plans),
        "maker_lifecycle_decisions": len(lifecycle_decisions),
        "taker_shares": float(args.taker_shares),
        "maker_shares": float(args.maker_shares),
        "maker_refresh_sec": float(args.maker_refresh_sec),
        "maker_reprice_limit": None,
        "max_city_days_per_bj_day": int(args.max_city_days_per_bj_day),
        "max_daily_cost_usd": float(args.max_daily_cost_usd),
        "execution": execution,
    }
    publish_runtime_state_best_effort(args, output_dir, summary, signal_summary)
    write_json(output_dir / "latest_summary.json", summary)
    append_jsonl(output_dir / "summary_history.jsonl", summary)
    return summary


def parser() -> argparse.ArgumentParser:
    ap = signal_runner.parser()
    ap.description = __doc__
    ap.set_defaults(output_dir=str(OUTPUT_DIR), artifact=str(ARTIFACT_PATH), interval_seconds=15.0)
    ap.add_argument("--taker-shares", type=float, default=5.0)
    ap.add_argument("--maker-shares", type=float, default=5.0)
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
