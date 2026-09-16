#!/usr/bin/env python3
"""Prospective 5-share tiny-live bridge for the frozen Tmin V1 selector.

The frozen zero-notional runner remains the sole model/selector owner.  This
adapter starts at the source journal EOF, consumes only subsequently appended
``selected=true`` rows from the exact frozen artifact, and asks the shared
executor to revalidate the fresh five-share ask ladder (including fee) before
submitting a BUY YES order.  It never backfills historical shadow selections.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.runtime import order_runtime  # noqa: E402


STRATEGY_INSTANCE = "weather_tmin_no_further_cooling_tiny_live_v1"
STRATEGY_KEY = "weather.tmin.no_further_cooling"
SOURCE_POLICY_ID = "tmin_current_exact_yes_market_innovation_zero_notional_v1"
MODEL_ID = "tmin_market_plus_physical_innovation_v1"
MODEL_ARTIFACT_ID = "969bbbf00b29f57756890ed5e2faa6e3d0ccdc070c83000b76a665d5207424dd"
FROZEN_SHARES = 5.0
DEFAULT_SOURCE = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/"
    "tmin_no_further_cooling_shadow_v1/signal_candidates.jsonl"
)
DEFAULT_OUTPUT = Path(
    "/Volumes/jrs/pm_agents/runtime/weather_edge_v1/"
    "tmin_no_further_cooling_tiny_live_v1"
)
CRITICAL_SOURCE_PATHS = (
    "scripts/ops/weather_tmin_no_further_cooling_tiny_live_v1.py",
    "scripts/ops/start_weather_tmin_no_further_cooling_tiny_live_v1.sh",
    "scripts/ops/weather_order_executor.py",
    "src/strategies/weather_edge_v1/runtime/order_runtime.py",
    "src/strategies/weather_edge_v1/tools/current_yes_core_carry.py",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="seconds")


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=timezone.utc)
    return out.astimezone(timezone.utc)


def finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def stable_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def signal_id_for_candidate(candidate_id: str) -> str:
    return "tmin-v1-" + stable_hash(
        {"candidate_id": candidate_id, "artifact": MODEL_ARTIFACT_ID}
    )


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Replace small cursor/health files atomically across process crashes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


@contextmanager
def exclusive_run_lock(path: Path):
    """Prevent overlapping live consumers during controller restarts."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another Tmin V1 tiny-live cycle holds the execution lock") from exc
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def deployment_metadata() -> dict[str, Any]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *CRITICAL_SOURCE_PATHS],
        cwd=ROOT,
        check=False,
    ).returncode != 0
    return {
        "deployed_repo_sha": head,
        "source_checkout_root": str(ROOT),
        "critical_source_dirty": dirty,
    }


def load_state(path: Path, source: Path) -> dict[str, Any]:
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("source_path") != str(source):
            raise RuntimeError("state source_path mismatch")
        return payload
    size = source.stat().st_size
    state = {
        "schema_version": "tmin_v1_tiny_live_state_v1",
        "source_path": str(source),
        "source_cursor_bytes": size,
        "prospective_start_utc": utc_text(),
        "processed_candidate_ids": [],
    }
    write_json_atomic(path, state)
    return state


def read_complete_lines(source: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    size = source.stat().st_size
    if offset < 0 or offset > size:
        raise RuntimeError(f"source journal cursor invalid: offset={offset} size={size}")
    with source.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()
    if not data:
        return [], offset
    last_newline = data.rfind(b"\n")
    if last_newline < 0:
        return [], offset
    complete = data[: last_newline + 1]
    rows: list[dict[str, Any]] = []
    for raw in complete.splitlines():
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("malformed complete JSONL row in frozen V1 source") from exc
        if not isinstance(row, dict):
            raise RuntimeError("non-object JSONL row in frozen V1 source")
        rows.append(row)
    return rows, offset + len(complete)


def candidate_blocker(row: Mapping[str, Any], *, now: datetime, max_age_sec: float) -> str | None:
    required = {
        "strategy_key": STRATEGY_KEY,
        "policy_id": SOURCE_POLICY_ID,
        "model_id": MODEL_ID,
        "model_artifact_id": MODEL_ARTIFACT_ID,
        "side": "YES",
        "candidate_status": "scored",
    }
    for key, expected in required.items():
        if row.get(key) != expected:
            return f"source_contract_mismatch:{key}"
    if not bool(row.get("selected")):
        return "not_selected_by_frozen_v1"
    if row.get("blocker_reason"):
        return "source_blocked"
    if row.get("market_evidence_status") != "available":
        return "source_market_evidence_unavailable"
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    if not bool(metadata.get("first_positive_edge_once_per_city_date")):
        return "source_selector_contract_missing"
    decision = parse_utc(row.get("decision_ts_utc"))
    if decision is None:
        return "invalid_decision_time"
    age = (now - decision).total_seconds()
    if age < -5.0 or age > max_age_sec:
        return "candidate_age_outside_live_window"
    for key in ("candidate_id", "city", "target_date", "condition_id", "market_id", "token_id", "bracket"):
        if not str(row.get(key) or "").strip():
            return f"missing:{key}"
    p_model = finite(row.get("p_model"))
    ask = finite(row.get("executable_cost"))
    if p_model is None or ask is None or not 0.0 < p_model < 1.0 or not 0.0 < ask < 1.0:
        return "invalid_probability_or_source_ask"
    return None


def prior_live_signal_ids(path: Path) -> set[str]:
    """Include every terminal attempt so an uncertain submission is never retried."""

    return {
        str(row.get("signal_id"))
        for row in order_runtime.read_jsonl(path)
        if row.get("signal_id")
    }


def committed_cost_by_target_date(path: Path) -> dict[str, float]:
    """Reserve the frozen model-price cap for each successfully submitted order."""

    costs: dict[str, float] = {}
    for row in order_runtime.read_jsonl(path):
        if row.get("status") != "submitted":
            continue
        target_date = str(row.get("target_date") or "")
        if not target_date:
            continue
        reserved = finite(row.get("order_notional_cap"))
        if reserved is None or reserved <= 0:
            reserved = finite(row.get("posted_notional"))
        if reserved is not None and reserved > 0:
            costs[target_date] = costs.get(target_date, 0.0) + reserved
    return costs


def append_new_plans(path: Path, plans: Iterable[Mapping[str, Any]]) -> int:
    """Keep the candidate→plan lineage append-only while executor input rotates."""

    existing = {
        str(row.get("plan_id"))
        for row in order_runtime.read_jsonl(path)
        if row.get("plan_id")
    }
    written = 0
    for plan in plans:
        plan_id = str(plan.get("plan_id") or "")
        if not plan_id or plan_id in existing:
            continue
        order_runtime.append_jsonl(path, plan)
        existing.add(plan_id)
        written += 1
    return written


def build_plan(row: Mapping[str, Any], *, now: datetime, ttl_min: float, live_enabled: bool) -> dict[str, Any]:
    p_model = float(row["p_model"])
    source_ask = float(row["executable_cost"])
    candidate_id = str(row["candidate_id"])
    signal_id = signal_id_for_candidate(candidate_id)
    plan_id = "plan-" + stable_hash({"signal_id": signal_id, "shares": FROZEN_SHARES})
    return {
        "record_type": "weather_edge_trade_plan",
        "plan_id": plan_id,
        "signal_id": signal_id,
        "source_candidate_id": candidate_id,
        "created_at_utc": utc_text(now),
        "status": "accepted",
        "risk_status": "passed",
        "strategy": "weather_edge_v1",
        "strategy_instance": STRATEGY_INSTANCE,
        "source_strategy_instance": "weather_tmin_no_further_cooling_shadow_v1",
        "strategy_id": "tmin_no_further_cooling_v1",
        "strategy_family": "tmin_no_further_cooling",
        "probability_source": MODEL_ID,
        "model_version": MODEL_ID,
        "model_artifact_id": MODEL_ARTIFACT_ID,
        "source_policy_id": SOURCE_POLICY_ID,
        "decision_mode": "frozen_v1_first_positive_edge_once_per_city_date",
        "execution_mode": "tiny_live_five_share_fresh_ladder_taker",
        "execution_policy": "current_yes_residual_carry_taker_v1",
        "city": str(row["city"]),
        "target_date": str(row["target_date"]),
        "market_id": str(row["market_id"]),
        "condition_id": str(row["condition_id"]),
        "bracket": str(row["bracket"]),
        "token_id": str(row["token_id"]),
        "expression": "current_exact_yes",
        "signal_side": "BUY_YES",
        "order_side": "BUY",
        "child_order_role": "single",
        "maker_only": False,
        "limit_price": source_ask,
        "market_price": source_ask,
        "model_token_probability": p_model,
        "quote_edge": p_model - source_ask,
        "required_quote_edge": 0.0,
        "quote_tick_size": 0.001,
        "quote_mode": "fresh_five_share_ladder_fee_adjusted_recheck",
        "size": FROZEN_SHARES,
        "fixed_order_shares": FROZEN_SHARES,
        "max_order_shares": FROZEN_SHARES,
        "notional": round(FROZEN_SHARES * source_ask, 6),
        "order_notional_cap": round(FROZEN_SHARES * p_model, 6),
        "paper_enabled": True,
        "live_enabled": live_enabled,
        "source_decision_ts_utc": str(row["decision_ts_utc"]),
        "source_execution_book_snapshot_id": str(row.get("execution_book_snapshot_id") or ""),
        "expires_at_utc": utc_text(now + timedelta(minutes=ttl_min)),
        "order_ttl_min": ttl_min,
        "expiry_policy": "fixed_tmin_v1_tiny_live_ttl",
    }


def choose_plans(
    rows: Iterable[Mapping[str, Any]],
    *,
    now: datetime,
    max_age_sec: float,
    ttl_min: float,
    daily_cost_cap: float,
    live_enabled: bool,
    prior_signal_ids: set[str],
    committed_costs: Mapping[str, float] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    plans: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    target_date_costs = dict(committed_costs or {})
    seen_city_dates: set[tuple[str, str]] = set()
    for row in rows:
        candidate_id = str(row.get("candidate_id") or "")
        blocker = candidate_blocker(row, now=now, max_age_sec=max_age_sec)
        signal_id = signal_id_for_candidate(candidate_id) if candidate_id else ""
        if not blocker and signal_id in prior_signal_ids:
            blocker = "already_in_live_journal"
        city_date = (str(row.get("city") or ""), str(row.get("target_date") or ""))
        if not blocker and city_date in seen_city_dates:
            blocker = "duplicate_city_date_in_source_batch"
        # The fresh ladder is accepted only below p_model after fee, so reserving
        # 5*p_model is a conservative upper bound for the eventual order cost.
        target_date = city_date[1]
        cost = FROZEN_SHARES * float(row.get("p_model") or 0.0)
        accumulated = target_date_costs.get(target_date, 0.0)
        if not blocker and accumulated + cost > daily_cost_cap + 1e-9:
            blocker = "daily_cost_cap"
        audits.append(
            {
                "created_at_utc": utc_text(now),
                "source_candidate_id": candidate_id,
                "city": city_date[0],
                "target_date": city_date[1],
                "status": "planned" if blocker is None else "ignored",
                "reason": blocker,
                "live_enabled": live_enabled,
            }
        )
        if blocker is not None:
            continue
        plans.append(build_plan(row, now=now, ttl_min=ttl_min, live_enabled=live_enabled))
        prior_signal_ids.add(signal_id)
        seen_city_dates.add(city_date)
        target_date_costs[target_date] = accumulated + cost
    return plans, audits


def _run_once_locked(args: argparse.Namespace) -> dict[str, Any]:
    if args.live and not args.confirm_live:
        raise RuntimeError("--live requires --confirm-live")
    if float(args.shares) != FROZEN_SHARES:
        raise RuntimeError("frozen Tmin V1 tiny-live requires exactly 5 shares")
    metadata = deployment_metadata()
    if metadata["critical_source_dirty"]:
        raise RuntimeError("critical source is dirty")
    source = Path(args.source_candidates)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "state.json"
    state = load_state(state_path, source)
    rows, next_cursor = read_complete_lines(source, int(state["source_cursor_bytes"]))
    live_out = output / "live_orders.jsonl"
    live_enabled = bool(args.live and args.confirm_live)
    prior_signal_ids = prior_live_signal_ids(live_out) | {
        signal_id_for_candidate(str(candidate_id))
        for candidate_id in state.get("processed_candidate_ids") or []
    }
    plans, audits = choose_plans(
        rows,
        now=utc_now(),
        max_age_sec=float(args.max_candidate_age_sec),
        ttl_min=float(args.order_ttl_min),
        daily_cost_cap=float(args.max_daily_cost_usd),
        live_enabled=live_enabled,
        prior_signal_ids=prior_signal_ids,
        committed_costs=committed_cost_by_target_date(live_out),
    )
    append_new_plans(output / "trade_plans.jsonl", plans)
    executor_plans = output / "_executor_plans.jsonl"
    order_runtime.write_jsonl(executor_plans, plans)
    for audit in audits:
        order_runtime.append_jsonl(output / "candidate_audit.jsonl", audit)
    execution = order_runtime.run_weather_order_executor(
        root=ROOT,
        plans_path=executor_plans,
        paper_out=output / "paper_orders.jsonl",
        live_out=live_out,
        live=bool(args.live),
        confirm_live=bool(args.confirm_live),
        allow_taker=True,
        cancel_expired=live_enabled,
        no_telegram=True,
        timeout_sec=float(args.executor_timeout_sec),
        market_proxy=str(args.market_proxy or ""),
    )
    if int(execution["returncode"]) != 0:
        raise RuntimeError(f"executor failed rc={execution['returncode']}: {execution['output_tail'][-1000:]}")
    processed = [str(row.get("candidate_id")) for row in rows if row.get("candidate_id")]
    state["source_cursor_bytes"] = next_cursor
    state["processed_candidate_ids"] = sorted(set(state.get("processed_candidate_ids") or []) | set(processed))[-5000:]
    state["updated_at_utc"] = utc_text()
    write_json_atomic(state_path, state)
    parsed = execution.get("parsed") or {}
    plan_signal_ids = {str(plan["signal_id"]) for plan in plans}
    cycle_live_rows = [
        row
        for row in order_runtime.read_jsonl(live_out)
        if str(row.get("signal_id") or "") in plan_signal_ids
    ]
    submitted_this_cycle = sum(row.get("status") == "submitted" for row in cycle_live_rows)
    summary = {
        "status": "ok",
        "generated_at_utc": utc_text(),
        "strategy_instance": STRATEGY_INSTANCE,
        "execution_mode": "live" if live_enabled else "paper_would_order",
        "live_enabled": live_enabled,
        "source_policy_id": SOURCE_POLICY_ID,
        "model_id": MODEL_ID,
        "model_artifact_id": MODEL_ARTIFACT_ID,
        "prospective_start_utc": state["prospective_start_utc"],
        "source_cursor_bytes": next_cursor,
        "source_rows_seen": len(rows),
        "plans": len(plans),
        "shares_per_order": FROZEN_SHARES,
        "max_daily_cost_usd": float(args.max_daily_cost_usd),
        "orders_submitted": submitted_this_cycle,
        "execution_records": int(parsed.get("live_orders", 0) or 0),
        "live_errors": int(parsed.get("live_errors", 0) or 0),
        "safety": {
            "historical_backfill": False,
            "fresh_ladder_revalidation": True,
            "first_positive_once_per_city_date": True,
        },
        **metadata,
    }
    write_json_atomic(output / "latest_summary.json", summary)
    order_runtime.append_jsonl(output / "summary_history.jsonl", summary)
    return summary


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output_dir)
    with exclusive_run_lock(output / "run.lock"):
        return _run_once_locked(args)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("run", "loop"))
    ap.add_argument("--source-candidates", default=str(DEFAULT_SOURCE))
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    ap.add_argument("--shares", type=float, default=FROZEN_SHARES)
    ap.add_argument("--max-candidate-age-sec", type=float, default=180.0)
    ap.add_argument("--order-ttl-min", type=float, default=15.0)
    ap.add_argument("--max-daily-cost-usd", type=float, default=35.0)
    ap.add_argument("--interval-seconds", type=float, default=10.0)
    ap.add_argument("--executor-timeout-sec", type=float, default=90.0)
    ap.add_argument("--market-proxy", default="")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--confirm-live", action="store_true")
    return ap


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
            error = {
                "status": "error",
                "generated_at_utc": utc_text(),
                "strategy_instance": STRATEGY_INSTANCE,
                "execution_mode": "live" if args.live and args.confirm_live else "paper_would_order",
                "live_enabled": bool(args.live and args.confirm_live),
                "error": f"{type(exc).__name__}: {exc}",
            }
            write_json_atomic(Path(args.output_dir) / "latest_summary.json", error)
            print(json.dumps(error, ensure_ascii=False, sort_keys=True), flush=True)
        time.sleep(max(1.0, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
