#!/usr/bin/env python3
"""Tiny-live executor for the current-YES heat-death signal, split by entry regime.

The signal remains owned by ``weather_current_yes_heat_death_shadow_v1.py``.
This adapter only turns the latest fresh ``physical_confirmation_strong`` row
into one fixed-share current-bracket BUY_YES order per city/target-date.  It is
an explicitly small forward probe, not a claim that the strategy is confirmed.

Per the preregistered promotion criteria
(docs/analysis/2026-07/2026-07-15-heat-death-live-promotion-preregistration-v1.md)
the probe runs as two separately attributed instances split by entry ask:

* ``h1_late_carry``       ask in [0.95, 0.99] — late-carry premium head
* ``h2_early_dislocation`` ask in [0.50, 0.93] — Busan-style repricing head

0.93-0.95 is the preregistered buffer band: neither head trades it.  The H2
floor 0.50 is a money-safety guard for an unattended probe: an ask far below
the signal-implied probability usually means bracket/data mismatch, not free
money.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import weather_current_yes_heat_death_shadow_v1 as shadow


STRATEGY_ID = "current_yes_heat_death_physical_v1"
HEADS: dict[str, dict[str, Any]] = {
    "h1_late_carry": {
        "instance": "current_yes_heat_death_tiny_live_h1_late_carry_v1",
        "decision_mode": "late_carry_heat_death_strong_current_yes",
        "combo": "current_yes_heat_death_late_carry_v1",
        "min_ask": 0.95,
        "max_ask": 0.99,
    },
    "h2_early_dislocation": {
        "instance": "current_yes_heat_death_tiny_live_h2_early_dislocation_v1",
        "decision_mode": "early_dislocation_heat_death_strong_current_yes",
        "combo": "current_yes_heat_death_early_dislocation_v1",
        "min_ask": 0.50,
        "max_ask": 0.93,
    },
}
# Set from --head at startup; no default on purpose (explicit failure over
# silently trading the wrong regime).
STRATEGY_INSTANCE = ""
ACTIVE_HEAD = ""
DEFAULT_SHADOW_DECISIONS = (
    ROOT / "runtime/weather_edge_v1/current_yes_heat_death_shadow_v1/state_decisions.jsonl"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


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
    return hashlib.sha256(json.dumps(dict(payload), sort_keys=True).encode("utf-8")).hexdigest()[:24]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def signal_id(row: Mapping[str, Any]) -> str:
    # Deliberately excludes snapshot and bracket: one paid probe per city-day.
    return stable_hash(
        {
            "strategy_instance": STRATEGY_INSTANCE,
            "city": str(row.get("city") or ""),
            "target_date": str(row.get("target_date") or ""),
        }
    )


def submitted_signal_ids(live_orders: Path) -> set[str]:
    return {
        str(row.get("signal_id"))
        for row in read_jsonl(live_orders)
        if str(row.get("status") or "") == "submitted" and str(row.get("signal_id") or "")
    }


def submitted_today_count(live_orders: Path, *, now: datetime) -> int:
    count = 0
    for row in read_jsonl(live_orders):
        if str(row.get("status") or "") != "submitted":
            continue
        created = parse_utc(row.get("created_at_utc"))
        if created is not None and created.date() == now.astimezone(timezone.utc).date():
            count += 1
    return count


def latest_strong_rows(
    decisions_path: Path,
    *,
    max_snapshot_age_min: float,
    now: datetime,
) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in read_jsonl(decisions_path):
        if not bool(row.get("physical_confirmation_strong")):
            continue
        city = str(row.get("city") or "")
        target_date = str(row.get("target_date") or "")
        snapshot_ts = parse_utc(row.get("decision_snapshot_ts_utc"))
        if not city or not target_date or snapshot_ts is None:
            continue
        age_min = (now - snapshot_ts).total_seconds() / 60.0
        if age_min < -1.0 or age_min > max_snapshot_age_min:
            continue
        key = (city, target_date)
        prior = latest.get(key)
        if prior is None or str(row.get("decision_snapshot_ts_utc")) > str(prior.get("decision_snapshot_ts_utc")):
            latest[key] = row
    return list(latest.values())


def refresh_current_yes_quotes(
    rows: list[dict[str, Any]],
    *,
    proxy: str | None,
    timeout_sec: float,
) -> list[dict[str, Any]]:
    refreshed: list[dict[str, Any]] = []
    if not rows:
        return refreshed
    with shadow.market_httpx_client(proxy, timeout=timeout_sec) as client:
        for raw in rows:
            row = dict(raw)
            quote = shadow._fetch_token_book(client, str(row.get("current_yes_token_id") or ""))
            row["fresh_current_yes_ask"] = quote.get("ask")
            row["fresh_current_yes_ask_size"] = quote.get("ask_size")
            row["fresh_current_yes_bid"] = quote.get("bid")
            row["fresh_current_yes_bid_size"] = quote.get("bid_size")
            row["fresh_current_yes_book_status"] = quote.get("book_status")
            row["fresh_current_yes_book_fetched_at_utc"] = quote.get("book_fetched_at_utc")
            refreshed.append(row)
    return refreshed


def build_plan(row: Mapping[str, Any], *, shares: float, live_enabled: bool, ttl_min: float) -> dict[str, Any]:
    ask = float(row["fresh_current_yes_ask"])
    bid = finite(row.get("fresh_current_yes_bid")) or 0.0
    sid = signal_id(row)
    now = datetime.now(timezone.utc)
    base = {
        "strategy": "weather_edge_v1",
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_INSTANCE,
        "strategy_family": "reheat_risk",
        "decision_mode": HEADS[ACTIVE_HEAD]["decision_mode"],
        "execution_mode": "tiny_live_taker_probe",
        "profile": "physical_confirmation_strong_forward_probe",
        "combo": HEADS[ACTIVE_HEAD]["combo"],
        "entry_regime_head": ACTIVE_HEAD,
        "city": str(row.get("city") or ""),
        "city_pool": "all_canonical_weather_state_v2",
        "target_date": str(row.get("target_date") or ""),
        "market_id": str(row.get("current_market_id") or ""),
        "question": str(row.get("current_question") or ""),
        "bracket": str(row.get("current_bracket") or ""),
        "token_id": str(row.get("current_yes_token_id") or ""),
        "signal_side": "BUY_YES",
        "order_side": "BUY",
        "market_price": round(ask, 6),
        "best_bid": round(bid, 6),
        "best_ask": round(ask, 6),
        "spread": round(max(0.0, ask - bid), 6) if bid > 0 else 0.0,
        "limit_price": round(ask, 6),
        "quote_status": "accepted",
        "quote_reason": "fresh_top_ask_has_fixed_share_depth",
        "quote_best_bid": round(bid, 6),
        "quote_best_ask": round(ask, 6),
        "quote_spread": round(max(0.0, ask - bid), 6) if bid > 0 else 0.0,
        "quote_tick_size": 0.001,
        "quote_mode": "fresh_book_guarded_taker",
        "maker_only": False,
        "order_notional_cap": round(shares * ask, 6),
        "size": round(shares, 6),
        "notional": round(shares * ask, 6),
        "execution_policy": "current_yes_heat_death_taker_probe_v1",
        "tick_size": 0.001,
        "sizing_mode": "fixed_shares",
        "fixed_order_shares": round(shares, 6),
        "max_order_shares": round(shares, 6),
        "paper_enabled": True,
        "live_enabled": bool(live_enabled),
        "source_shadow_decision_id": str(row.get("shadow_decision_id") or ""),
        "source_snapshot_file": str(row.get("snapshot_file") or ""),
        "decision_snapshot_ts_utc": str(row.get("decision_snapshot_ts_utc") or ""),
        "fresh_book_fetched_at_utc": str(row.get("fresh_current_yes_book_fetched_at_utc") or ""),
        "physical_support_count": row.get("physical_support_count"),
        "physical_support_reasons": row.get("physical_support_reasons"),
        "decline_c": row.get("decline_c"),
        "minutes_since_running_max": row.get("minutes_since_running_max"),
        "forecast_peak_delta_hours_local": row.get("forecast_peak_delta_hours_local"),
        "expires_at_utc": (now + timedelta(minutes=ttl_min)).isoformat(timespec="seconds"),
    }
    return {
        "record_type": "weather_edge_trade_plan",
        "plan_id": stable_hash({**base, "signal_id": sid}),
        "signal_id": sid,
        "opportunity_id": sid,
        "created_at_utc": utc_now(),
        "status": "accepted",
        "risk_status": "passed",
        "risk_reason": "",
        **base,
    }


def choose_plans(
    rows: list[dict[str, Any]],
    *,
    live_orders: Path,
    shares: float,
    min_ask: float,
    max_ask: float,
    min_top_ask_shares: float,
    max_orders_per_utc_day: int,
    live_enabled: bool,
    ttl_min: float,
    now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    counts = {
        "fresh_strong_rows": len(rows),
        "already_submitted_city_days": 0,
        "missing_or_bad_book": 0,
        "ask_above_cap": 0,
        "ask_below_floor": 0,
        "insufficient_top_ask_depth": 0,
        "daily_cap": 0,
    }
    submitted = submitted_signal_ids(live_orders)
    remaining = max(0, int(max_orders_per_utc_day) - submitted_today_count(live_orders, now=now))
    eligible: list[dict[str, Any]] = []
    for row in rows:
        if signal_id(row) in submitted:
            counts["already_submitted_city_days"] += 1
            continue
        ask = finite(row.get("fresh_current_yes_ask"))
        ask_size = finite(row.get("fresh_current_yes_ask_size"))
        if ask is None or ask_size is None or str(row.get("fresh_current_yes_book_status") or "") != "ok":
            counts["missing_or_bad_book"] += 1
            continue
        if ask > max_ask:
            counts["ask_above_cap"] += 1
            continue
        if ask < min_ask:
            counts["ask_below_floor"] += 1
            continue
        if ask_size + 1e-9 < min_top_ask_shares:
            counts["insufficient_top_ask_depth"] += 1
            continue
        eligible.append(row)
    eligible.sort(key=lambda row: (float(row["fresh_current_yes_ask"]), str(row.get("city") or "")))
    if len(eligible) > remaining:
        counts["daily_cap"] = len(eligible) - remaining
    plans = [build_plan(row, shares=shares, live_enabled=live_enabled, ttl_min=ttl_min) for row in eligible[:remaining]]
    return plans, counts


def execute_plans(args: argparse.Namespace, plans_path: Path, output_dir: Path, *, max_ask: float) -> dict[str, Any]:
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
    env["WEATHER_EXECUTOR_MAX_LIVE_ORDER_NOTIONAL_USD"] = str(float(args.fixed_order_shares) * max_ask)
    env["WEATHER_EXECUTOR_MAX_LIVE_BATCH_NOTIONAL_USD"] = str(
        float(args.fixed_order_shares) * max_ask * int(args.max_orders_per_utc_day)
    )
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
        payload = json.loads(completed.stdout) if completed.stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {"stdout": completed.stdout.strip()}
    return {
        "exit_code": completed.returncode,
        "result": payload,
        "stderr": completed.stderr.strip(),
    }


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    global STRATEGY_INSTANCE, ACTIVE_HEAD
    if args.live and not args.confirm_live:
        raise RuntimeError("--live requires --confirm-live")
    head = HEADS[args.head]
    ACTIVE_HEAD = args.head
    STRATEGY_INSTANCE = str(head["instance"])
    min_ask = float(args.min_ask) if args.min_ask is not None else float(head["min_ask"])
    max_ask = float(args.max_ask) if args.max_ask is not None else float(head["max_ask"])
    if not (0.0 < min_ask < max_ask < 1.0):
        raise RuntimeError(f"invalid ask band for {args.head}: [{min_ask}, {max_ask}]")
    output_dir = Path(args.output_dir) if args.output_dir else ROOT / f"runtime/weather_edge_v1/{head['instance']}"
    output_dir.mkdir(parents=True, exist_ok=True)
    decisions_path = Path(args.shadow_decisions)
    live_orders = output_dir / "live_orders.jsonl"
    now = datetime.now(timezone.utc)
    rows = latest_strong_rows(
        decisions_path,
        max_snapshot_age_min=float(args.max_snapshot_age_min),
        now=now,
    )
    refreshed = refresh_current_yes_quotes(rows, proxy=args.market_proxy, timeout_sec=float(args.book_timeout_sec))
    plans, funnel = choose_plans(
        refreshed,
        live_orders=live_orders,
        shares=float(args.fixed_order_shares),
        min_ask=min_ask,
        max_ask=max_ask,
        min_top_ask_shares=float(args.fixed_order_shares),
        max_orders_per_utc_day=int(args.max_orders_per_utc_day),
        live_enabled=bool(args.live and args.confirm_live),
        ttl_min=float(args.order_ttl_min),
        now=now,
    )
    plans_path = output_dir / "current_plans.jsonl"
    write_jsonl(plans_path, plans)
    execution = execute_plans(args, plans_path, output_dir, max_ask=max_ask)
    summary = {
        "status": "ok" if execution["exit_code"] == 0 else "executor_error",
        "generated_at_utc": utc_now(),
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_INSTANCE,
        "entry_regime_head": ACTIVE_HEAD,
        "mode": "tiny_live_forward_probe" if args.live else "paper_would_order",
        "live_enabled": bool(args.live and args.confirm_live),
        "shadow_decisions": str(decisions_path),
        "fixed_order_shares": float(args.fixed_order_shares),
        "max_orders_per_utc_day": int(args.max_orders_per_utc_day),
        "min_ask": min_ask,
        "max_ask": max_ask,
        "candidate_funnel": funnel,
        "plans": len(plans),
        "planned_city_days": [f"{row['city']}:{row['target_date']}" for row in plans],
        "execution": execution,
    }
    write_json(output_dir / "latest_summary.json", summary)
    append_jsonl(output_dir / "summary_history.jsonl", summary)
    return summary


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", nargs="?", choices=("run", "loop"), default="run")
    ap.add_argument("--head", required=True, choices=sorted(HEADS))
    ap.add_argument("--shadow-decisions", default=str(DEFAULT_SHADOW_DECISIONS))
    ap.add_argument("--output-dir", default=None, help="defaults to runtime/weather_edge_v1/<head instance>")
    ap.add_argument("--fixed-order-shares", type=float, default=10.0)
    ap.add_argument("--max-orders-per-utc-day", type=int, default=3)
    ap.add_argument("--min-ask", type=float, default=None, help="defaults to the head's preregistered floor")
    ap.add_argument("--max-ask", type=float, default=None, help="defaults to the head's preregistered cap")
    ap.add_argument("--max-snapshot-age-min", type=float, default=20.0)
    ap.add_argument("--order-ttl-min", type=float, default=15.0)
    ap.add_argument("--book-timeout-sec", type=float, default=5.0)
    ap.add_argument("--executor-timeout-sec", type=float, default=60.0)
    ap.add_argument("--market-proxy", default=None)
    ap.add_argument("--interval-seconds", type=float, default=30.0)
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
            print(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        time.sleep(max(10.0, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
