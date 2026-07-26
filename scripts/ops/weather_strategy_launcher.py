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
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MAX_COUNTED_JOURNAL_BYTES = 32 * 1024 * 1024
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import refresh_weather_strategy_runtime_registry as registry  # noqa: E402
from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical  # noqa: E402
from src.strategies.runtime.sync import sync_instance_specs  # noqa: E402
from src.strategies.runtime import control  # noqa: E402
from src.strategies.runtime import runtime_state  # noqa: E402
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


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def _journal_rows(path: Path | None, fallback: object) -> int:
    """Count small journals exactly without stalling the supervisor on history."""
    if path is None or not path.exists():
        return 0
    if path.stat().st_size > MAX_COUNTED_JOURNAL_BYTES:
        return int(fallback or 0)
    return registry.count_lines(path) or 0


def _runtime_snapshot(
    spec: registry.StrategySpec,
    *,
    tmux_sessions: set[str],
    screen_sessions: set[str],
    existing: dict[str, Any],
) -> dict[str, Any]:
    """Materialize a runner's current raw summary into the control-plane table."""
    summary_path = registry.path_for(spec, spec.summary_file)
    primary_path = registry.path_for(spec, spec.primary_journal)
    live_order_path = registry.path_for(spec, spec.live_order_file)
    paper_order_path = registry.path_for(spec, spec.paper_order_file)
    telemetry_path = registry.path_for(spec, spec.telemetry_file)
    summary = registry.read_json(summary_path) if summary_path else {}
    paths = [path for path in (summary_path, primary_path, live_order_path, paper_order_path, telemetry_path) if path]
    latest_mtime = max((registry.file_mtime(path) for path in paths if path.exists()), default=None)
    summary_ts = registry.parse_dt(summary.get("generated_at_utc") or summary.get("refreshed_at_utc"))
    data_ts = registry.parse_dt(summary.get("snapshot_ts_utc"))
    if data_ts is None and primary_path:
        data_ts = registry.latest_record_ts(primary_path)
    latest_ts = max((dt for dt in (summary_ts, data_ts, latest_mtime) if dt), default=None)

    live_order_rows = _journal_rows(live_order_path, existing.get("live_order_rows"))
    paper_order_rows = _journal_rows(paper_order_path, existing.get("paper_order_rows"))
    primary_rows = _journal_rows(primary_path, existing.get("shadow_rows"))
    telemetry_rows = _journal_rows(telemetry_path, existing.get("telemetry_rows"))
    shadow_rows = primary_rows if primary_path and primary_path.name in {"opportunities.jsonl", "sources.jsonl", "books.jsonl"} else 0
    row_counts = {
        "live_order_rows": live_order_rows,
        "shadow_rows": shadow_rows,
        "telemetry_rows": telemetry_rows,
    }
    blockers = summary.get("blockers") or []
    if not isinstance(blockers, list):
        blockers = [blockers]
    running = bool(
        (spec.tmux_session and spec.tmux_session in tmux_sessions)
        or (spec.screen_session and spec.screen_session in screen_sessions)
    )
    process_status = "running" if running else "stopped" if (spec.tmux_session or spec.screen_session) else "unknown"
    health = registry.health_from(spec, summary, latest_ts, row_counts)
    if process_status == "stopped" and health == "healthy":
        health = "stale"
    live_enabled = summary.get("live_enabled")
    if live_enabled is None:
        live_enabled = spec.expected_live
    fact = existing
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "instance_id": spec.strategy_instance,
        "process_status": process_status,
        "health_status": health,
        "heartbeat_at_utc": _iso(latest_ts),
        "last_tick_ts_utc": _iso(summary_ts or latest_ts),
        "last_data_ts_utc": _iso(data_ts or summary_ts or latest_ts),
        "latest_summary_ts_utc": _iso(summary_ts),
        "latest_artifact_mtime_utc": _iso(latest_mtime),
        "heartbeat_age_min": max(0.0, (datetime.now(timezone.utc) - latest_ts).total_seconds() / 60.0) if latest_ts else None,
        "candidate_rows": registry.summary_int(summary, ["candidate_rows", "pre_fresh_candidates", "accepted_candidates", "routed_candidates", "selected_rows_before_dedupe", "opportunities"]),
        "plan_rows": registry.summary_int(summary, ["plans", "plans_written", "execution_eligible", "paper_orders"]),
        "live_order_rows": live_order_rows,
        "paper_order_rows": paper_order_rows,
        "shadow_rows": shadow_rows,
        "telemetry_rows": telemetry_rows,
        "fact_trade_rows": fact.get("fact_trade_rows") or 0,
        "fact_live_real_rows": fact.get("fact_live_real_rows") or 0,
        "fact_cost_usd": fact.get("fact_cost_usd"),
        "first_target_date": fact.get("first_target_date"),
        "last_target_date": fact.get("last_target_date"),
        "latest_fill_ts_utc": fact.get("latest_fill_ts_utc"),
        "live_enabled": None if live_enabled is None else int(bool(live_enabled)),
        "summary_path": str(summary_path) if summary_path else None,
        "primary_journal_path": str(live_order_path or primary_path) if (live_order_path or primary_path) else None,
        "blocker_count": len(blockers),
        "blockers_json": blockers,
        "summary_json": summary,
        "refreshed_at_utc": now,
    }


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
        SELECT
            si.instance_id, si.display_name, si.family, si.lifecycle_status,
            si.execution_mode, si.desired_status, si.tmux_session,
            si.start_script, si.spec_commit, si.config_id,
            COALESCE(rt.process_status, 'unknown') AS process_status,
            COALESCE(rt.health_status, 'unknown') AS health_status,
            rt.heartbeat_at_utc, rt.last_tick_ts_utc, rt.last_data_ts_utc,
            rt.candidate_rows, rt.plan_rows, rt.live_order_rows,
            rt.blocker_count, rt.refreshed_at_utc
        FROM strategy_instance si
        LEFT JOIN strategy_instance_runtime rt ON rt.instance_id = si.instance_id
        ORDER BY si.instance_id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def cmd_sync(args: argparse.Namespace) -> int:
    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        apply_schema_canonical(conn)
        result = sync_instance_specs(conn)
        conn.commit()
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
    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    try:
        runtime_row = conn.execute(
            "SELECT * FROM strategy_instance_runtime WHERE instance_id=?",
            (args.strategy_instance,),
        ).fetchone()
    finally:
        conn.close()
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
            "process_status": runtime_row["process_status"] if runtime_row else "unknown",
            "runtime": dict(runtime_row) if runtime_row else None,
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
    env = os.environ.copy()
    if spec.lifecycle_status == "live" and args.confirm_live:
        env["WEATHER_STRATEGY_CONFIRM_LIVE"] = "1"
    proc = subprocess.run(
        [str(script)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
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


def cmd_reconcile(args: argparse.Namespace) -> int:
    """Write actual process state for every instance; optionally reconcile drift."""
    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    actions: list[dict[str, Any]] = []
    try:
        apply_schema_canonical(conn)
        sync_instance_specs(conn)
        # Summary/journal scans can be slow. Release the schema write lock
        # before inspecting raw runtime files, then publish snapshots together.
        conn.commit()
        tmux = registry.active_tmux_sessions()
        screen = registry.active_screen_sessions()
        rows = instance_rows(conn)
        existing_rows = conn.execute(
            """SELECT instance_id, fact_trade_rows, fact_live_real_rows, fact_cost_usd,
                      first_target_date, last_target_date, latest_fill_ts_utc,
                      live_order_rows, paper_order_rows, shadow_rows, telemetry_rows
               FROM strategy_instance_runtime"""
        ).fetchall()
        existing_by_instance = {str(row["instance_id"]): dict(row) for row in existing_rows}
        specs = specs_by_instance()
        snapshots: list[dict[str, Any]] = []
        for row in rows:
            iid = str(row["instance_id"])
            desired = str(row["desired_status"])
            lifecycle = str(row["lifecycle_status"])
            session = row.get("tmux_session")
            running = bool(session and session in tmux)
            observed_status = "running" if running else "stopped" if session else "unknown"
            action = "observe"
            rc = 0
            output = ""

            if args.apply and desired == "enabled" and observed_status == "stopped":
                spec = specs.get(iid)
                if spec and spec.start_script:
                    require_live_confirmation(spec, args, "start")
                    proc = subprocess.run(
                        [str(ROOT / spec.start_script)],
                        cwd=ROOT,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        check=False,
                    )
                    rc = proc.returncode
                    output = proc.stdout.strip()
                    action = "start"
                    observed_status = "running" if tmux_running(spec.tmux_session) else "stopped"
                    control.write_control_log(
                        conn,
                        instance_id=iid,
                        actor=getpass.getuser(),
                        action="supervisor_start",
                        from_state=desired,
                        to_state=desired,
                        reason=args.reason,
                        spec_commit=row.get("spec_commit"),
                        params_hash=None,
                    )
                else:
                    action = "start_unavailable"
            elif args.apply and desired in {"paused", "shelved", "blocked"} and observed_status == "running":
                if lifecycle == "live" and not args.confirm_live:
                    action = "stop_requires_confirm_live"
                else:
                    proc = subprocess.run(
                        ["tmux", "kill-session", "-t", str(session)],
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        check=False,
                    )
                    rc = 0 if proc.returncode == 0 or "can't find session" in proc.stdout.lower() else proc.returncode
                    output = proc.stdout.strip()
                    action = "stop"
                    observed_status = "stopped" if rc == 0 else observed_status
                    control.write_control_log(
                        conn,
                        instance_id=iid,
                        actor=getpass.getuser(),
                        action="supervisor_stop",
                        from_state=desired,
                        to_state=desired,
                        reason=args.reason,
                        spec_commit=row.get("spec_commit"),
                        params_hash=None,
                    )

            spec = specs.get(iid)
            if spec:
                snapshot = _runtime_snapshot(
                    spec,
                    tmux_sessions=tmux,
                    screen_sessions=screen,
                    existing=existing_by_instance.get(iid, {}),
                )
                snapshots.append(snapshot)
                observed_status = str(snapshot["process_status"])
            else:
                runtime_state.mark_process_state(
                    conn,
                    instance_id=iid,
                    process_status=observed_status,
                    supervisor_id=socket_id(),
                )
            actions.append({
                "strategy_instance": iid,
                "desired_status": desired,
                "process_status": observed_status,
                "tmux_session": session,
                "action": action,
                "returncode": rc,
                "output": output[-500:] if output else "",
            })
        for snapshot in snapshots:
            runtime_state.push_runtime_state(conn, supervisor_id=socket_id(), **snapshot)
        conn.commit()
    finally:
        conn.close()
    print_payload({"action": "reconcile", "apply": bool(args.apply), "instances": actions})
    return 0


def socket_id() -> str:
    import socket
    return f"weather-supervisor@{socket.gethostname()}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=registry.DEFAULT_DB)
    parser.add_argument("--no-refresh", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("sync")
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--apply", action="store_true")
    reconcile.add_argument("--confirm-live", action="store_true")
    reconcile.add_argument("--reason", default=None)
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
    if args.command == "reconcile":
        return cmd_reconcile(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "start":
        return cmd_start(args)
    if args.command == "stop":
        return cmd_stop(args)
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
