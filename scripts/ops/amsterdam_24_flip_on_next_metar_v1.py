#!/usr/bin/env python3
"""One-shot live flip for the Amsterdam 2026-07-16 24C bracket.

Watch the first EHAM report strictly newer than the configured baseline.  If
that report prints 25C, sell the known 24 YES position and buy 15 shares of
24 NO.  The durable state and executor lineage make restarts idempotent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OBS_PATH = Path("/Volumes/jrs/weather_data_feed_service_runtime/output/observations/latest.json")
RUNTIME = ROOT / "runtime/weather_edge_v1/amsterdam_24_flip_next_metar_v1"
BASELINE = "2026-07-16T15:55:00+00:00"
YES_TOKEN = "71862495146212456074661308346657933097593474061277291368819432121368142184539"
NO_TOKEN = "11520868718246954465153379047015619305097917493344767581705250693290244884225"
MARKET_ID = "2917220"
CONDITION_ID = "0xe4af6102c59ef41b2bb0f6780fbbda0a17c8065d3db0ffc84799af7886d3a96c"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_event(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def eham_record(path: Path) -> dict[str, Any] | None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    for row in payload.get("records", []):
        if row.get("station") == "EHAM" or row.get("city") == "Amsterdam":
            return row
    return None


def stable_id(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def plan(*, report_ts: str, token_id: str, signal_side: str, order_side: str, size: float, limit: float) -> dict[str, Any]:
    identity = {"report_ts": report_ts, "token_id": token_id, "signal_side": signal_side, "order_side": order_side}
    return {
        "record_type": "weather_edge_trade_plan",
        "plan_id": "plan-" + stable_id(identity),
        "signal_id": "signal-" + stable_id(identity),
        "created_at_utc": utc_now(),
        "status": "accepted",
        "risk_status": "passed",
        "strategy": "weather_edge_v1",
        "strategy_id": "amsterdam_24_flip_next_metar_v1",
        "strategy_instance": "amsterdam_24_flip_next_metar_v1",
        "source_strategy_instance": "current_yes_heat_death_tiny_live_h2_early_dislocation_v1",
        "strategy_family": "operator_authorized_one_shot_exposure_flip",
        "profile": "amsterdam_24_flip_next_metar_v1",
        "combo": signal_side.lower(),
        "city": "Amsterdam",
        "city_pool": "operator_one_shot",
        "target_date": "2026-07-16",
        "market_id": MARKET_ID,
        "condition_id": CONDITION_ID,
        "event_slug": "highest-temperature-in-amsterdam-on-july-16",
        "market_slug": "highest-temperature-in-amsterdam-on-july-16-2026-24c",
        "question": "Will the highest temperature in Amsterdam be 24C on July 16?",
        "bracket": "24",
        "token_id": token_id,
        "signal_side": signal_side,
        "order_side": order_side,
        "market_price": limit,
        "limit_price": limit,
        "best_bid": 0.0,
        "best_ask": 0.0,
        "quote_status": "accepted",
        "quote_reason": "operator_authorized_next_eham_printed_25",
        "quote_edge": 0.0,
        "required_quote_edge": 0.0,
        "model_token_probability": 0.0,
        "quote_tick_size": 0.001,
        "quote_mode": "operator_taker_limit",
        "child_order_role": "single",
        "maker_only": False,
        "execution_policy": "operator_taker_limit_v1",
        "sizing_mode": "fixed_shares",
        "fixed_order_shares": size,
        "max_order_shares": size,
        "size": size,
        "notional": round(size * limit, 6),
        "order_notional_cap": round(size * limit, 6),
        "paper_enabled": True,
        "live_enabled": True,
        "obs_source": "aviationweather_metar",
        "source_observation_ts_utc": report_ts,
        "shadow_reason": "explicit_user_authorization_2026-07-17",
    }


def run_trigger(report: dict[str, Any], state_path: Path, events_path: Path) -> int:
    report_ts = str(report["last_obs_utc"])
    plans = [
        plan(report_ts=report_ts, token_id=YES_TOKEN, signal_side="SELL_YES", order_side="SELL", size=5.0, limit=0.001),
        plan(report_ts=report_ts, token_id=NO_TOKEN, signal_side="BUY_NO", order_side="BUY", size=15.0, limit=0.999),
    ]
    plans_path = RUNTIME / "plans.jsonl"
    plans_path.parent.mkdir(parents=True, exist_ok=True)
    plans_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in plans), encoding="utf-8")
    write_json(state_path, {"status": "executing", "report": report, "updated_at_utc": utc_now()})
    cmd = [
        sys.executable,
        "scripts/ops/weather_order_executor.py",
        "--plans", str(plans_path),
        "--paper-out", str(RUNTIME / "paper_orders.jsonl"),
        "--live-out", str(RUNTIME / "live_orders.jsonl"),
        "--live", "--confirm-live", "--allow-taker", "--no-telegram",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    event = {"event": "executor_finished", "at_utc": utc_now(), "returncode": proc.returncode, "output": proc.stdout[-12000:]}
    append_event(events_path, event)
    write_json(state_path, {"status": "completed" if proc.returncode == 0 else "executor_error", "report": report, **event})
    print(proc.stdout, flush=True)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default=BASELINE)
    parser.add_argument("--poll-sec", type=float, default=2.0)
    parser.add_argument("--obs-path", type=Path, default=OBS_PATH)
    args = parser.parse_args()
    state_path = RUNTIME / "state.json"
    events_path = RUNTIME / "events.jsonl"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("status") in {"completed", "no_trigger", "executing"}:
            print(json.dumps({"already_terminal": state}, ensure_ascii=False), flush=True)
            return 0
    baseline = parse_utc(args.baseline)
    write_json(state_path, {"status": "armed", "baseline_obs_utc": args.baseline, "armed_at_utc": utc_now()})
    append_event(events_path, {"event": "armed", "baseline_obs_utc": args.baseline, "at_utc": utc_now()})
    while True:
        try:
            row = eham_record(args.obs_path)
            if row and row.get("status") == "ok" and parse_utc(str(row["last_obs_utc"])) > baseline:
                observed = float(row.get("current_temp_c"))
                append_event(events_path, {"event": "next_report_seen", "at_utc": utc_now(), "report": row})
                if abs(observed - 25.0) < 1e-9:
                    return run_trigger(row, state_path, events_path)
                write_json(state_path, {"status": "no_trigger", "report": row, "completed_at_utc": utc_now()})
                print(json.dumps({"no_trigger": row}, ensure_ascii=False), flush=True)
                return 0
        except Exception as exc:
            append_event(events_path, {"event": "poll_error", "at_utc": utc_now(), "error": f"{type(exc).__name__}: {exc}"})
        time.sleep(max(0.5, args.poll_sec))


if __name__ == "__main__":
    raise SystemExit(main())
