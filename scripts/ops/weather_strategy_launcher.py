#!/usr/bin/env python3
"""Unified local launcher for weather strategy runtime loops.

This is intentionally thin: strategy metadata lives in
refresh_weather_strategy_runtime_registry.strategy_specs(), while individual
strategies still own their runner implementation. The launcher gives us one
operator entrypoint for list/status/start/stop and refreshes the dashboard
registry after process changes.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import refresh_weather_strategy_runtime_registry as registry  # noqa: E402
from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical  # noqa: E402
from src.strategies.runtime.sync import sync_instance_specs  # noqa: E402
from src.strategies.runtime import control  # noqa: E402
from src.strategies.runtime.specs import params_hash, spec_commit  # noqa: E402


def json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    return value


def specs_by_instance() -> dict[str, registry.StrategySpec]:
    return {spec.strategy_instance: spec for spec in registry.strategy_specs()}


def read_summary(spec: registry.StrategySpec) -> dict[str, Any]:
    path = registry.path_for(spec, spec.summary_file)
    return registry.read_json(path) if path else {}


def tmux_running(session: str | None) -> bool | None:
    if not session:
        return None
    return session in registry.active_tmux_sessions()


def refresh_db(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        apply_schema_canonical(conn)
        return registry.refresh(conn)
    finally:
        conn.close()


def print_payload(payload: dict[str, Any]) -> None:
    print(json.dumps(json_ready(payload), ensure_ascii=False, indent=2, sort_keys=True))


def instance_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT instance_id, display_name, family, lifecycle_status, execution_mode,
               desired_status, tmux_session, start_script, spec_commit
        FROM strategy_instance
        ORDER BY instance_id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def cmd_sync(args: argparse.Namespace) -> int:
    conn = sqlite3.connect(args.db_path)
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        apply_schema_canonical(conn)
        result = sync_instance_specs(conn)
    finally:
        conn.close()
    print_payload({"action": "sync", **result})
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    # Read-only: never take a write lock on the live DB for a list. If the
    # control-plane table is missing, hint to sync instead of migrating here.
    conn = sqlite3.connect(args.db_path)
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        try:
            rows = instance_rows(conn)
        except sqlite3.OperationalError:
            rows = []
    finally:
        conn.close()
    if not rows:
        print_payload({"strategies": [], "hint": "run: weather_strategy_launcher.py sync"})
        return 0
    tmux = registry.active_tmux_sessions()
    for row in rows:
        session = row.get("tmux_session")
        row["process_status"] = (
            "running" if session and session in tmux else "stopped" if session else "unknown"
        )
    print_payload({"strategies": rows})
    return 0


def get_spec(instance: str) -> registry.StrategySpec:
    specs = specs_by_instance()
    if instance not in specs:
        raise SystemExit(f"unknown strategy_instance: {instance}")
    return specs[instance]


def cmd_status(args: argparse.Namespace) -> int:
    spec = get_spec(args.strategy_instance)
    summary = read_summary(spec)
    print_payload(
        {
            "strategy_instance": spec.strategy_instance,
            "display_name": spec.display_name,
            "lifecycle_status": spec.lifecycle_status,
            "execution_mode": spec.execution_mode,
            "runtime_dir": spec.runtime_dir,
            "summary_file": spec.summary_file,
            "generated_at_utc": summary.get("generated_at_utc"),
            "snapshot_ts_utc": summary.get("snapshot_ts_utc"),
            "live_enabled": summary.get("live_enabled"),
            "tmux_session": spec.tmux_session,
            "process_status": "running" if tmux_running(spec.tmux_session) else "stopped" if spec.tmux_session else "unknown",
            "start_script": spec.start_script,
        }
    )
    return 0


def require_live_confirmation(spec: registry.StrategySpec, args: argparse.Namespace, action: str) -> None:
    if spec.lifecycle_status == "live" and not args.confirm_live:
        raise SystemExit(f"refusing to {action} live strategy without --confirm-live: {spec.strategy_instance}")
    if spec.lifecycle_status == "live" and not args.reason:
        raise SystemExit(f"refusing to {action} live strategy without --reason: {spec.strategy_instance}")


def _record_action(db_path: Path, spec: registry.StrategySpec, action: str,
                   to_state: str, reason: str | None) -> str | None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        apply_schema_canonical(conn)
        return control.record_control_action(
            conn, instance_id=spec.strategy_instance, action=action,
            to_state=to_state, reason=reason, actor=getpass.getuser(),
            spec_commit=spec_commit(), params_hash=params_hash(spec),
        )
    finally:
        conn.close()


def cmd_start(args: argparse.Namespace) -> int:
    spec = get_spec(args.strategy_instance)
    require_live_confirmation(spec, args, "start")
    if not spec.start_script:
        raise SystemExit(f"strategy has no start_script in registry spec: {spec.strategy_instance}")
    script = ROOT / spec.start_script
    if not script.exists():
        raise SystemExit(f"missing start_script: {script}")
    proc = subprocess.run([str(script)], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    log_id = _record_action(args.db_path, spec, "start", "enabled", args.reason)
    refresh = None if args.no_refresh else refresh_db(args.db_path)
    print_payload(
        {
            "action": "start",
            "strategy_instance": spec.strategy_instance,
            "returncode": proc.returncode,
            "output": proc.stdout.strip(),
            "process_status": "running" if tmux_running(spec.tmux_session) else "stopped" if spec.tmux_session else "unknown",
            "control_log_id": log_id,
            "registry_refresh": refresh,
        }
    )
    return proc.returncode


def cmd_stop(args: argparse.Namespace) -> int:
    spec = get_spec(args.strategy_instance)
    require_live_confirmation(spec, args, "stop")
    if not spec.tmux_session:
        raise SystemExit(f"strategy has no tmux_session in registry spec: {spec.strategy_instance}")
    proc = subprocess.run(["tmux", "kill-session", "-t", spec.tmux_session], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if proc.returncode != 0 and "can't find session" not in proc.stdout.lower():
        rc = proc.returncode
    else:
        rc = 0
    log_id = _record_action(args.db_path, spec, "stop", "paused", args.reason)
    refresh = None if args.no_refresh else refresh_db(args.db_path)
    print_payload(
        {
            "action": "stop",
            "strategy_instance": spec.strategy_instance,
            "returncode": rc,
            "output": proc.stdout.strip(),
            "process_status": "running" if tmux_running(spec.tmux_session) else "stopped",
            "control_log_id": log_id,
            "registry_refresh": refresh,
        }
    )
    return rc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=registry.DEFAULT_DB)
    parser.add_argument("--no-refresh", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("sync")
    status = sub.add_parser("status")
    status.add_argument("strategy_instance")
    start = sub.add_parser("start")
    start.add_argument("strategy_instance")
    start.add_argument("--confirm-live", action="store_true")
    start.add_argument("--reason", default=None)
    stop = sub.add_parser("stop")
    stop.add_argument("strategy_instance")
    stop.add_argument("--confirm-live", action="store_true")
    stop.add_argument("--reason", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "list":
        return cmd_list(args)
    if args.command == "sync":
        return cmd_sync(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "start":
        return cmd_start(args)
    if args.command == "stop":
        return cmd_stop(args)
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
