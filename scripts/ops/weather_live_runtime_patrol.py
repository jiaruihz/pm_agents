#!/usr/bin/env python3
"""Watch the fast-source live runner and stop repeated deterministic submit failures."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_RUNTIME_ROOT = Path("/Volumes/jrs/weather_data_feed_service_runtime")
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
RUNNER_PATTERN = "scripts/ops/weather_fast_source_prev_no_trial.py --loop"
DETERMINISTIC_SUBMIT_ERRORS = (
    "invalid expiration value",
    "invalid signature",
    "invalid api key",
    "not enough balance / allowance",
)


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def read_recent_orders(path: Path, *, cutoff: datetime) -> list[dict[str, Any]]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in content.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = parse_utc(row.get("live_attempt_ts_utc") or row.get("ts_utc"))
        if ts is not None and ts >= cutoff:
            rows.append(row)
    return rows


def runner_pids() -> list[int]:
    result = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, check=False)
    pids: list[int] = []
    for line in result.stdout.splitlines():
        value, _, command = line.strip().partition(" ")
        if not value.isdigit() or RUNNER_PATTERN not in command:
            continue
        if "python" not in command.lower() or "sh -c" in command or "login -" in command:
            continue
        pids.append(int(value))
    return pids


def deterministic_error(row: dict[str, Any]) -> str:
    message = str(row.get("error") or row.get("live_submit_error") or "").lower()
    return next((needle for needle in DETERMINISTIC_SUBMIT_ERRORS if needle in message), "")


def evaluate(
    *,
    now: datetime,
    latest: dict[str, Any],
    recent_orders: list[dict[str, Any]],
    pids: list[int],
    max_latest_age_sec: float,
    failure_threshold: int,
    fast_source_state: dict[str, Any] | None = None,
    max_fast_source_age_sec: float = 180.0,
) -> dict[str, Any]:
    generated = parse_utc(latest.get("generated_at_utc"))
    latest_age_sec = (now - generated).total_seconds() if generated else None
    source_updated = parse_utc(
        (fast_source_state or {}).get("updated_at_utc")
        or (fast_source_state or {}).get("generated_at_utc")
    )
    fast_source_age_sec = (now - source_updated).total_seconds() if source_updated else None
    failures = [row for row in recent_orders if str(row.get("live_submit_status") or "") == "submit_failed"]
    deterministic = [row for row in failures if deterministic_error(row)]
    error_counts: dict[str, int] = {}
    for row in deterministic:
        key = deterministic_error(row)
        error_counts[key] = error_counts.get(key, 0) + 1

    reasons: list[str] = []
    if not pids:
        reasons.append("runner_process_missing")
    if latest_age_sec is None or latest_age_sec > max_latest_age_sec:
        reasons.append("runner_latest_stale")
    if fast_source_state is not None and (
        fast_source_age_sec is None or fast_source_age_sec > max_fast_source_age_sec
    ):
        reasons.append("fast_source_state_stale")
    repeated_error = next((key for key, count in error_counts.items() if count >= failure_threshold), "")
    if repeated_error:
        reasons.append(f"repeated_deterministic_submit_failure:{repeated_error}")
    return {
        "status": "critical" if reasons else "ok",
        "checked_at_utc": now.isoformat(),
        "runner_pids": pids,
        "latest_age_sec": round(latest_age_sec, 3) if latest_age_sec is not None else None,
        "fast_source_age_sec": round(fast_source_age_sec, 3) if fast_source_age_sec is not None else None,
        "recent_order_attempts": len(recent_orders),
        "recent_submit_failures": len(failures),
        "deterministic_submit_error_counts": error_counts,
        "reasons": reasons,
        "stop_runner_required": bool(repeated_error and pids),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def notification_kind(health: dict[str, Any], state: dict[str, Any]) -> str:
    current = str(health.get("status") or "")
    previous = str(state.get("last_status") or "")
    fingerprint = "|".join(str(value) for value in health.get("reasons") or [])
    if current == "critical" and fingerprint != str(state.get("last_notified_fingerprint") or ""):
        return "critical"
    if current == "ok" and previous == "critical":
        return "recovered"
    return ""


def telegram_message(kind: str, health: dict[str, Any]) -> str:
    if kind == "recovered":
        return "【weather live 巡检】链路已恢复，runner 与 latest 均正常。"
    lines = [
        "【weather live 巡检】CRITICAL，已禁止静默重试",
        "原因：" + ", ".join(str(value) for value in health.get("reasons") or ["unknown"]),
        f"近 5 分钟：attempts={health.get('recent_order_attempts')} failures={health.get('recent_submit_failures')}",
    ]
    stopped = health.get("runner_stopped_pids") or []
    if stopped:
        lines.append("已熔断 live runner：" + ",".join(str(value) for value in stopped))
    lines.append("请在 Codex 当前天气任务发送：检查并修复 weather live 巡检告警")
    return "\n".join(lines)


def notify_telegram(health: dict[str, Any], *, state_path: Path) -> dict[str, Any]:
    state = read_json(state_path)
    kind = notification_kind(health, state)
    result: dict[str, Any] = {"kind": kind, "attempted": False, "sent": False}
    if kind:
        result["attempted"] = True
        try:
            from src.platform.notification.telegram import send_telegram_message_sync

            response = send_telegram_message_sync(telegram_message(kind, health))
            result["sent"] = True
            result["message_id"] = (response.get("result") or response).get("message_id") if isinstance(response, dict) else None
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
    fingerprint = "|".join(str(value) for value in health.get("reasons") or [])
    state["last_status"] = health.get("status")
    state["updated_at_utc"] = health.get("checked_at_utc")
    if result["sent"] and kind == "critical":
        state["last_notified_fingerprint"] = fingerprint
    elif result["sent"] and kind == "recovered":
        state["last_notified_fingerprint"] = ""
    write_json(state_path, state)
    return result


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    latest = read_json(Path(args.runner_dir) / "latest.json")
    fast_source_state = read_json(Path(args.fast_source_state))
    orders = read_recent_orders(Path(args.runner_dir) / "orders.jsonl", cutoff=now - timedelta(minutes=args.lookback_min))
    health = evaluate(
        now=now,
        latest=latest,
        recent_orders=orders,
        pids=runner_pids(),
        max_latest_age_sec=args.max_latest_age_sec,
        failure_threshold=args.failure_threshold,
        fast_source_state=fast_source_state,
        max_fast_source_age_sec=args.max_fast_source_age_sec,
    )
    stopped: list[int] = []
    if args.stop_runner_on_submit_failure and health["stop_runner_required"]:
        for pid in health["runner_pids"]:
            try:
                os.kill(pid, signal.SIGTERM)
                stopped.append(pid)
            except ProcessLookupError:
                pass
        health["runner_stopped_pids"] = stopped
    if args.telegram:
        health["telegram_notification"] = notify_telegram(health, state_path=Path(args.notification_state))
    write_json(Path(args.output), health)
    return health


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner-dir", default=str(DEFAULT_RUNTIME_ROOT / "output/fast_source_prev_no_trial"))
    parser.add_argument("--output", default=str(DEFAULT_RUNTIME_ROOT / "output/live_runtime_patrol/latest.json"))
    parser.add_argument("--lookback-min", type=float, default=5.0)
    parser.add_argument("--max-latest-age-sec", type=float, default=180.0)
    parser.add_argument("--failure-threshold", type=int, default=3)
    parser.add_argument(
        "--fast-source-state",
        default=str(DEFAULT_RUNTIME_ROOT / "output/high_frequency_observations/state.json"),
    )
    parser.add_argument("--max-fast-source-age-sec", type=float, default=180.0)
    parser.add_argument("--stop-runner-on-submit-failure", action="store_true")
    parser.add_argument("--telegram", action="store_true")
    parser.add_argument(
        "--notification-state",
        default=str(DEFAULT_RUNTIME_ROOT / "output/live_runtime_patrol/notification_state.json"),
    )
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-sec", type=float, default=60.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    while True:
        health = run_once(args)
        print(json.dumps(health, ensure_ascii=False, sort_keys=True), flush=True)
        if not args.loop:
            return 0 if health["status"] == "ok" else 1
        time.sleep(max(10.0, args.interval_sec))


if __name__ == "__main__":
    raise SystemExit(main())
