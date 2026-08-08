#!/usr/bin/env python3
"""Monitor active weather live/shadow loops for data and execution failures.

The monitor is intentionally read-only. It does not change strategy decisions,
place orders, or mutate runner state. It watches the runtime pulse files that
the runners already produce and turns "quietly did nothing" into an explicit
status that the dashboard, logs, and optional Telegram alerts can consume.
Canonical DB and analysis-mirror freshness are owned by the separate
weather_analysis_freshness_monitor.py process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TOKEN_RESOLUTION_BLOCKERS = frozenset(
    {
        "missing_yes_token_id",
        "token_resolution_failed",
        "token_resolution_timeout",
    }
)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec  # noqa: E402


_PRODUCTION_SPEC = load_production_spec()
DEFAULT_RUNTIME_ROOT = _PRODUCTION_SPEC.pm_runtime_root / "weather_edge_v1"
DEFAULT_RUNTIME_DIR = DEFAULT_RUNTIME_ROOT / "runtime_monitor"


@dataclass(frozen=True)
class WatchSpec:
    instance: str
    display_name: str
    runtime_dir: Path
    mode: str
    expected_live: bool = False
    summary_file: str = "latest_summary.json"
    history_file: str = "summary_history.jsonl"
    live_orders_file: str | None = None
    extra_live_orders_files: tuple[str, ...] = ()
    stale_after_min: float = 20.0
    bad_after_min: float = 90.0
    history_window_min: float = 180.0
    snapshot_bad_after_min: float = 60.0
    target_date_lag_warn_days: int | None = None
    no_live_order_warn_hours: float | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z") if dt else None


def parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - monitor should surface parse failures.
        return {"_parse_error": f"{type(exc).__name__}: {exc}"}
    return data if isinstance(data, dict) else {"_parse_error": "json root is not an object"}


def read_recent_jsonl(path: Path, *, cutoff: datetime | None = None, max_lines: int = 1000) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            end = handle.tell()
            block = b""
            step = 8192
            while end > 0 and block.count(b"\n") <= max_lines:
                take = min(step, end)
                end -= take
                handle.seek(end)
                block = handle.read(take) + block
        raw_lines = [line for line in block.splitlines() if line.strip()][-max_lines:]
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for raw in raw_lines:
        try:
            row = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        if cutoff is not None:
            ts = generated_ts(row)
            if ts is None or ts < cutoff:
                continue
        rows.append(row)
    return rows


def generated_ts(row: dict[str, Any]) -> datetime | None:
    for key in (
        "generated_at_utc",
        "created_at_utc",
        "submitted_at_utc",
        "order_ts_utc",
        "snapshot_ts_utc",
        "fill_ts_utc",
        "ts_utc",
    ):
        dt = parse_dt(row.get(key))
        if dt:
            return dt
    return None


def count_lines(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def latest_jsonl_record(
    path: Path | None,
    *,
    statuses: frozenset[str] | None = None,
) -> dict[str, Any] | None:
    if path is None or not path.exists() or path.stat().st_size == 0:
        return None
    rows = read_recent_jsonl(path, max_lines=1000)
    if statuses is not None:
        rows = [row for row in rows if str(row.get("status") or "").lower() in statuses]
    return rows[-1] if rows else None


def resolve_runtime_path(path_text: str | None, fallback: Path | None = None) -> Path | None:
    if path_text:
        path = Path(path_text)
        return path if path.is_absolute() else ROOT / path
    return fallback


def latest_record_across(
    paths: list[Path],
    *,
    statuses: frozenset[str] | None = None,
) -> tuple[dict[str, Any] | None, Path | None]:
    best_record: dict[str, Any] | None = None
    best_path: Path | None = None
    best_ts: datetime | None = None
    for path in paths:
        record = latest_jsonl_record(path, statuses=statuses)
        ts = generated_ts(record) if record else None
        if ts is not None and (best_ts is None or ts > best_ts):
            best_record = record
            best_path = path
            best_ts = ts
    return best_record, best_path


def classify_executor_output(text: str) -> str:
    if "ConnectTimeout" in text or "Request exception" in text:
        return "clob_connect_timeout"
    if "Invalid API key" in text or "Unauthorized" in text or "403" in text:
        return "clob_auth_error"
    if "Insufficient" in text or "balance" in text.lower():
        return "balance_or_allowance_error"
    if "Traceback" in text:
        return "executor_exception"
    return "executor_failed"


def summary_ts(summary: dict[str, Any]) -> datetime | None:
    return parse_dt(summary.get("generated_at_utc") or summary.get("refreshed_at_utc"))


def snapshot_age(summary: dict[str, Any]) -> float | None:
    if summary.get("snapshot_age_min") is not None:
        try:
            return float(summary["snapshot_age_min"])
        except (TypeError, ValueError):
            return None
    meta = summary.get("meta") if isinstance(summary.get("meta"), dict) else {}
    if meta.get("snapshot_age_min") is None:
        return None
    try:
        return float(meta["snapshot_age_min"])
    except (TypeError, ValueError):
        return None


def latest_target_date(summary: dict[str, Any], history: list[dict[str, Any]]) -> date | None:
    candidates: list[date] = []
    for source in [summary, *history[-50:]]:
        value = source.get("target_dates")
        if isinstance(value, list):
            for item in value:
                d = parse_date(item)
                if d:
                    candidates.append(d)
        for key in ("target_date", "effective_min_target_date"):
            d = parse_date(source.get(key))
            if d:
                candidates.append(d)
        meta = source.get("meta") if isinstance(source.get("meta"), dict) else {}
        for audit in meta.get("audits") or []:
            if isinstance(audit, dict):
                d = parse_date(audit.get("target_date"))
                if d:
                    candidates.append(d)
    return max(candidates) if candidates else None


def add_alert(
    alerts: list[dict[str, Any]],
    *,
    severity: str,
    instance: str,
    kind: str,
    message: str,
    detail: dict[str, Any] | None = None,
) -> None:
    detail = detail or {}
    key_base = json.dumps(
        {"instance": instance, "kind": kind, "detail_key": detail.get("detail_key")},
        sort_keys=True,
        ensure_ascii=False,
    )
    alerts.append(
        {
            "alert_key": hashlib.sha1(key_base.encode("utf-8")).hexdigest()[:20],
            "severity": severity,
            "strategy_instance": instance,
            "kind": kind,
            "message": message,
            "detail": detail,
        }
    )


def int_value(row: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def summarize_history(rows: list[dict[str, Any]]) -> dict[str, Any]:
    skip_reasons: Counter[str] = Counter()
    blocker_counts: Counter[str] = Counter()
    audit_counts: Counter[str] = Counter()
    executor_failures: Counter[str] = Counter()
    latest_executor_failure: dict[str, Any] | None = None
    out = {
        "cycles": len(rows),
        "candidate_rows": 0,
        "routed_candidates": 0,
        "execution_eligible": 0,
        "plans_written": 0,
        "executor_runs": 0,
        "executor_failures": 0,
        "live_written": 0,
        "live_errors": 0,
        "live_guard_blocks": 0,
        "paper_written": 0,
        "plans_read": 0,
        "empty_snapshot_cycles": 0,
    }
    for row in rows:
        out["candidate_rows"] += int_value(row, "candidate_rows")
        out["routed_candidates"] += int_value(row, "routed_candidates")
        out["execution_eligible"] += int_value(row, "execution_eligible")
        out["plans_written"] += int_value(row, "plans_written", "plans", "planned_count")
        for reason, count in (row.get("skip_reasons") or {}).items():
            skip_reasons[str(reason or "unspecified")] += int(count or 0)
        for reason, count in (row.get("blocker_counts") or {}).items():
            blocker_counts[str(reason or "unspecified")] += int(count or 0)
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        for name, count in (meta.get("audit_counts") or row.get("audit_counts") or {}).items():
            audit_counts[str(name)] += int(count or 0)
        if int((meta.get("audit_counts") or {}).get("empty_snapshot") or 0) > 0:
            out["empty_snapshot_cycles"] += 1
        executor_result = row.get("executor_result") if isinstance(row.get("executor_result"), dict) else None
        if executor_result is None:
            continue
        out["executor_runs"] += 1
        returncode = executor_result.get("returncode")
        parsed = executor_result.get("parsed") if isinstance(executor_result.get("parsed"), dict) else {}
        out["live_written"] += int_value(parsed, "live_written", "live_orders")
        out["live_errors"] += int_value(parsed, "live_errors")
        out["live_guard_blocks"] += int_value(parsed, "live_guard_blocks")
        out["paper_written"] += int_value(parsed, "paper_written", "paper_orders")
        out["plans_read"] += int_value(parsed, "plans_read")
        if returncode not in (0, "0", None):
            out["executor_failures"] += 1
            output_tail = str(executor_result.get("output_tail") or "")
            err_class = classify_executor_output(output_tail)
            executor_failures[err_class] += 1
            latest_executor_failure = {
                "generated_at_utc": row.get("generated_at_utc"),
                "returncode": returncode,
                "error_class": err_class,
                "output_tail": output_tail[-1200:],
            }
    out["top_skip_reasons"] = skip_reasons.most_common(5)
    out["top_blocker_counts"] = blocker_counts.most_common(8)
    out["token_resolution_blockers"] = {
        name: blocker_counts.get(name, 0) for name in sorted(TOKEN_RESOLUTION_BLOCKERS)
    }
    out["token_resolution_blocker_total"] = sum(out["token_resolution_blockers"].values())
    out["top_audit_counts"] = audit_counts.most_common(5)
    out["executor_failure_classes"] = executor_failures.most_common()
    out["latest_executor_failure"] = latest_executor_failure
    return out


def evaluate_spec(spec: WatchSpec, now: datetime) -> dict[str, Any]:
    summary_path = spec.runtime_dir / spec.summary_file
    history_path = spec.runtime_dir / spec.history_file
    default_live_orders = spec.runtime_dir / "live_orders.jsonl"
    live_order_paths = [
        path
        for path in [
            resolve_runtime_path(spec.live_orders_file, default_live_orders),
            *(resolve_runtime_path(item) for item in spec.extra_live_orders_files),
        ]
        if path is not None
    ]

    summary = read_json(summary_path)
    cutoff = now - timedelta(minutes=spec.history_window_min)
    history_tail_lines = min(1000, max(80, int(spec.history_window_min * 2) + 20))
    history = read_recent_jsonl(history_path, cutoff=cutoff, max_lines=history_tail_lines)
    history_stats = summarize_history(history)
    alerts: list[dict[str, Any]] = []
    latest_live = None
    latest_live_path = None
    latest_live_ts = None
    live_order_age_hours = None
    if spec.expected_live:
        # Only an accepted exchange submission is economic order activity.
        # Blocked lifecycle attempts must not keep a strategy looking healthy.
        latest_live, latest_live_path = latest_record_across(
            live_order_paths,
            statuses=frozenset({"submitted"}),
        )
        latest_live_ts = generated_ts(latest_live) if latest_live else None
        live_order_age_hours = (now - latest_live_ts).total_seconds() / 3600 if latest_live_ts else None

    ts = summary_ts(summary)
    heartbeat_age_min = (now - ts).total_seconds() / 60 if ts else None
    if not summary:
        add_alert(
            alerts,
            severity="critical",
            instance=spec.instance,
            kind="missing_summary",
            message=f"{spec.display_name}: latest_summary.json missing or empty",
            detail={"path": rel(summary_path)},
        )
    elif summary.get("_parse_error"):
        add_alert(
            alerts,
            severity="critical",
            instance=spec.instance,
            kind="summary_parse_error",
            message=f"{spec.display_name}: latest_summary.json cannot be parsed",
            detail={"path": rel(summary_path), "error": summary.get("_parse_error")},
        )
    elif heartbeat_age_min is None or heartbeat_age_min > spec.bad_after_min:
        add_alert(
            alerts,
            severity="critical",
            instance=spec.instance,
            kind="stale_summary",
            message=f"{spec.display_name}: heartbeat stale ({heartbeat_age_min:.1f} min)" if heartbeat_age_min is not None else f"{spec.display_name}: heartbeat timestamp missing",
            detail={"generated_at_utc": summary.get("generated_at_utc"), "age_min": heartbeat_age_min},
        )
    elif heartbeat_age_min > spec.stale_after_min:
        add_alert(
            alerts,
            severity="warning",
            instance=spec.instance,
            kind="aging_summary",
            message=f"{spec.display_name}: heartbeat aging ({heartbeat_age_min:.1f} min)",
            detail={"generated_at_utc": summary.get("generated_at_utc"), "age_min": heartbeat_age_min},
        )

    snap_age = snapshot_age(summary)
    if snap_age is not None and snap_age > spec.snapshot_bad_after_min:
        add_alert(
            alerts,
            severity="warning",
            instance=spec.instance,
            kind="stale_snapshot",
            message=f"{spec.display_name}: market snapshot age {snap_age:.1f} min",
            detail={"snapshot_age_min": snap_age},
        )

    if not history:
        add_alert(
            alerts,
            severity="warning",
            instance=spec.instance,
            kind="no_recent_history",
            message=f"{spec.display_name}: no summary_history rows in last {spec.history_window_min:g} min",
            detail={"history_path": rel(history_path), "window_min": spec.history_window_min},
        )

    if history_stats["executor_failures"] > 0:
        latest = history_stats.get("latest_executor_failure") or {}
        latest_executor = summary.get("executor_result") if isinstance(summary.get("executor_result"), dict) else {}
        latest_returncode = latest_executor.get("returncode")
        failure_is_current = latest_returncode not in (0, "0", None)
        add_alert(
            alerts,
            severity="critical" if failure_is_current else "warning",
            instance=spec.instance,
            kind="executor_failure" if failure_is_current else "executor_failure_recovered",
            message=(
                f"{spec.display_name}: executor failed {history_stats['executor_failures']} "
                f"time(s) in the last {spec.history_window_min:g} min"
            ),
            detail={
                "detail_key": latest.get("error_class"),
                "failure_classes": history_stats.get("executor_failure_classes"),
                "latest_failure": latest,
                "latest_executor_returncode": latest_returncode,
            },
        )

    if spec.expected_live and history_stats["live_guard_blocks"] >= 3:
        add_alert(
            alerts,
            severity="critical",
            instance=spec.instance,
            kind="repeated_live_guard_blocks",
            message=(
                f"{spec.display_name}: live execution guard blocked "
                f"{history_stats['live_guard_blocks']} attempt(s) in the recent window"
            ),
            detail={
                "live_guard_blocks": history_stats["live_guard_blocks"],
                "top_blocker_counts": history_stats.get("top_blocker_counts"),
            },
        )

    if spec.expected_live and history_stats.get("token_resolution_blocker_total", 0) > 0:
        add_alert(
            alerts,
            severity="critical",
            instance=spec.instance,
            kind="token_resolution_blockers",
            message=(
                f"{spec.display_name}: token resolution blocked "
                f"{history_stats['token_resolution_blocker_total']} candidate(s) in recent window"
            ),
            detail={
                "detail_key": "token_resolution_blockers",
                "token_resolution_blockers": history_stats.get("token_resolution_blockers"),
                "top_blocker_counts": history_stats.get("top_blocker_counts"),
            },
        )

    recent_live_order_seen = latest_live_ts is not None and latest_live_ts >= cutoff
    if (
        spec.expected_live
        and history_stats["plans_written"] > 0
        and history_stats["live_written"] == 0
        and not recent_live_order_seen
    ):
        severity = "critical" if history_stats["executor_failures"] > 0 else "warning"
        add_alert(
            alerts,
            severity=severity,
            instance=spec.instance,
            kind="plans_without_live_orders",
            message=(
                f"{spec.display_name}: {history_stats['plans_written']} plan(s) but "
                f"0 live orders in recent window"
            ),
            detail={
                "plans_written": history_stats["plans_written"],
                "executor_failures": history_stats["executor_failures"],
                "live_errors": history_stats["live_errors"],
            },
        )

    if history_stats["empty_snapshot_cycles"] >= 3:
        add_alert(
            alerts,
            severity="warning",
            instance=spec.instance,
            kind="empty_snapshot_cycles",
            message=f"{spec.display_name}: empty snapshot seen in {history_stats['empty_snapshot_cycles']} recent cycles",
            detail={"top_audit_counts": history_stats["top_audit_counts"]},
        )

    latest_target = latest_target_date(summary, history)
    target_lag_days = None
    if latest_target is not None:
        target_lag_days = (now.date() - latest_target).days
    if (
        spec.target_date_lag_warn_days is not None
        and target_lag_days is not None
        and target_lag_days > spec.target_date_lag_warn_days
    ):
        add_alert(
            alerts,
            severity="warning",
            instance=spec.instance,
            kind="stale_target_date",
            message=f"{spec.display_name}: latest target_date is {latest_target.isoformat()}",
            detail={"latest_target_date": latest_target.isoformat(), "target_lag_days": target_lag_days},
        )

    if (
        spec.expected_live
        and spec.no_live_order_warn_hours is not None
        and (live_order_age_hours is None or live_order_age_hours > spec.no_live_order_warn_hours)
    ):
        add_alert(
            alerts,
            severity="warning",
            instance=spec.instance,
            kind="no_recent_live_orders",
            message=(
                f"{spec.display_name}: no live order for "
                f"{live_order_age_hours:.1f}h" if live_order_age_hours is not None else f"{spec.display_name}: no live order file rows"
            ),
            detail={
                "live_orders_path": rel(latest_live_path) if latest_live_path else None,
                "live_order_paths": [rel(path) for path in live_order_paths],
                "latest_live_order_ts_utc": iso(latest_live_ts),
                "top_skip_reasons": history_stats["top_skip_reasons"],
            },
        )

    severity_rank = {"critical": 3, "warning": 2, "info": 1}
    max_severity = max((severity_rank.get(a["severity"], 0) for a in alerts), default=0)
    if max_severity >= 3:
        status = "critical"
    elif max_severity == 2:
        status = "warning"
    elif spec.expected_live and history_stats["plans_written"] == 0 and history_stats["routed_candidates"] > 0:
        status = "idle_by_policy"
    else:
        status = "healthy"

    return {
        "strategy_instance": spec.instance,
        "display_name": spec.display_name,
        "mode": spec.mode,
        "expected_live": spec.expected_live,
        "status": status,
        "summary_path": rel(summary_path),
        "history_path": rel(history_path),
        "live_orders_path": rel(latest_live_path) if latest_live_path else None,
        "live_order_paths": [rel(path) for path in live_order_paths] if spec.expected_live else [],
        "generated_at_utc": summary.get("generated_at_utc"),
        "heartbeat_age_min": heartbeat_age_min,
        "snapshot_age_min": snap_age,
        "latest_target_date": latest_target.isoformat() if latest_target else None,
        "target_lag_days": target_lag_days,
        "latest_live_order_ts_utc": iso(latest_live_ts),
        "live_order_age_hours": live_order_age_hours,
        "history_window_min": spec.history_window_min,
        "history": history_stats,
        "latest_summary_excerpt": {
            "candidate_rows": summary.get("candidate_rows"),
            "routed_candidates": summary.get("routed_candidates"),
            "execution_eligible": summary.get("execution_eligible"),
            "plans_written": summary.get("plans_written"),
            "live_enabled": summary.get("live_enabled"),
            "live_requested": summary.get("live_requested"),
            "rows_written_this_cycle": summary.get("rows_written_this_cycle"),
            "selected_rows_this_cycle": summary.get("selected_rows_this_cycle"),
            "blocked_rows_this_cycle": summary.get("blocked_rows_this_cycle"),
            "target_dates": summary.get("target_dates"),
            "skip_reasons": summary.get("skip_reasons"),
            "blocker_counts": summary.get("blocker_counts"),
        },
        "alerts": alerts,
    }


def default_specs(root: Path) -> list[WatchSpec]:
    specs = [
        WatchSpec(
            instance="regime_routed_no_shadow_v1",
            display_name="Regime-routed NO shadow",
            runtime_dir=root / "regime_routed_no_shadow_v1",
            mode="zero_notional_shadow",
            expected_live=False,
            stale_after_min=15,
            bad_after_min=90,
            history_window_min=180,
            snapshot_bad_after_min=60,
            target_date_lag_warn_days=1,
        ),
        WatchSpec(
            instance="low_price_yes_lottery_shadow_v1",
            display_name="Low-price YES lottery shadow",
            runtime_dir=root / "low_price_yes_lottery_tiny_live_v1",
            mode="zero_notional_shadow",
            expected_live=False,
            stale_after_min=45,
            bad_after_min=180,
            history_window_min=360,
            target_date_lag_warn_days=1,
        ),
        WatchSpec(
            instance="tmax_distribution_edge_first_lock_no_current_yes_shadow_v1",
            display_name="Tmax first-lock no-current-YES shadow",
            runtime_dir=root / "tmax_distribution_edge_first_lock_no_current_yes_shadow_v1",
            mode="zero_notional_shadow",
            expected_live=False,
            stale_after_min=30,
            bad_after_min=90,
            history_window_min=180,
            snapshot_bad_after_min=60,
        ),
        WatchSpec(
            instance="d1_yes_high_mid_shadow_v1",
            display_name="d1 YES high-mid favorite low-estimation shadow",
            runtime_dir=root / "d1_yes_high_mid_shadow_v1",
            mode="zero_notional_shadow",
            expected_live=False,
            stale_after_min=45,
            bad_after_min=180,
            history_window_min=360,
            target_date_lag_warn_days=1,
        ),
        WatchSpec(
            instance="d1_yes_high_mid_live_v1",
            display_name="d1 YES high-mid live (Taipei shadow)",
            runtime_dir=root / "d1_yes_high_mid_live_v1",
            mode="tiny_live_taker_5shares_taipei_shadow",
            expected_live=True,
            live_orders_file=str(root / "d1_yes_high_mid_live_v1" / "live_orders.jsonl"),
            stale_after_min=15,
            bad_after_min=45,
            history_window_min=360,
            target_date_lag_warn_days=1,
            no_live_order_warn_hours=24,
        ),
        WatchSpec(
            instance="current_yes_heat_death_shadow_v1",
            display_name="Current-YES heat-death signal producer",
            runtime_dir=root / "current_yes_heat_death_shadow_v1",
            mode="zero_notional_shadow",
            expected_live=False,
            stale_after_min=30,
            bad_after_min=60,
            history_window_min=120,
        ),
        WatchSpec(
            instance="current_yes_core_carry_tiny_live_v2",
            display_name="Current-YES frozen core carry v2 tiny-live",
            runtime_dir=root / "current_yes_core_carry_tiny_live_v2",
            mode="tiny_live",
            expected_live=True,
            live_orders_file=str(
                root / "current_yes_core_carry_tiny_live_v2" / "live_orders.jsonl"
            ),
            stale_after_min=5,
            bad_after_min=15,
            history_window_min=120,
        ),
    ]
    managed_ids = {runtime.instance_id for runtime in load_production_spec().managed_runtimes}
    return [spec for spec in specs if spec.instance in managed_ids]


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"active_alert_keys": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"active_alert_keys": {}}
    return data if isinstance(data, dict) else {"active_alert_keys": {}}


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def update_alert_journal(runtime_dir: Path, summary: dict[str, Any]) -> list[dict[str, Any]]:
    now_text = summary["generated_at_utc"]
    state_path = runtime_dir / "alert_state.json"
    journal_path = runtime_dir / "alerts.jsonl"
    state = load_state(state_path)
    active_before = state.get("active_alert_keys") if isinstance(state.get("active_alert_keys"), dict) else {}
    current_alerts = {
        alert["alert_key"]: alert
        for probe in summary.get("probes", [])
        for alert in probe.get("alerts", [])
    }
    transitions: list[dict[str, Any]] = []
    for key, alert in current_alerts.items():
        if key not in active_before:
            transitions.append({**alert, "event": "opened", "event_ts_utc": now_text})
    for key, previous in active_before.items():
        if key not in current_alerts:
            transitions.append({**previous, "event": "resolved", "event_ts_utc": now_text})

    append_jsonl(journal_path, transitions)
    write_json(
        state_path,
        {
            "updated_at_utc": now_text,
            "active_alert_keys": current_alerts,
        },
    )
    return transitions


def send_telegram(transitions: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    if os.environ.get("WEATHER_RUNTIME_MONITOR_TELEGRAM") != "1":
        return
    opened = [a for a in transitions if a.get("event") == "opened" and a.get("severity") in {"critical", "warning"}]
    if not opened:
        return
    try:
        from src.platform.notification.telegram import send_telegram_message_sync
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] telegram helper unavailable: {type(exc).__name__}: {exc}", flush=True)
        return
    lines = [
        "【Weather runtime monitor】",
        f"status={summary.get('status')} critical={summary.get('critical_alerts')} warning={summary.get('warning_alerts')}",
    ]
    for alert in opened[:8]:
        lines.append(f"- [{alert.get('severity')}] {alert.get('message')}")
    try:
        send_telegram_message_sync("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] telegram send failed: {type(exc).__name__}: {exc}", flush=True)


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    now = utc_now()
    runtime_root = args.runtime_root
    specs = default_specs(runtime_root)
    if args.instance:
        requested = set(args.instance)
        specs = [s for s in specs if s.instance in requested]
    probes = [evaluate_spec(spec, now) for spec in specs]
    all_alerts = [alert for probe in probes for alert in probe["alerts"]]
    critical = sum(1 for alert in all_alerts if alert.get("severity") == "critical")
    warning = sum(1 for alert in all_alerts if alert.get("severity") == "warning")
    status = "critical" if critical else "warning" if warning else "healthy"
    summary = {
        "generated_at_utc": iso(now),
        "strategy_instance": "weather_runtime_monitor",
        "strategy_family": "data_quality.runtime_monitor",
        "execution_mode": "monitor",
        "status": status,
        "critical_alerts": critical,
        "warning_alerts": warning,
        "alert_count": len(all_alerts),
        "probes": probes,
        "no_order_placed": True,
    }
    transitions = update_alert_journal(args.runtime_dir, summary)
    summary["alert_transitions_this_cycle"] = len(transitions)
    summary["opened_alerts_this_cycle"] = sum(1 for row in transitions if row.get("event") == "opened")
    summary["resolved_alerts_this_cycle"] = sum(1 for row in transitions if row.get("event") == "resolved")
    write_json(args.runtime_dir / "latest_summary.json", summary)
    append_jsonl(args.runtime_dir / "summary_history.jsonl", [summary])
    send_telegram(transitions, summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME_DIR)
    parser.add_argument("--instance", action="append", help="Limit monitoring to one strategy_instance; repeatable.")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=300.0)
    parser.add_argument("--exit-nonzero-on-alert", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    while True:
        summary = run_once(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
        if not args.loop:
            return 2 if args.exit_nonzero_on_alert and summary["critical_alerts"] else 0
        time.sleep(max(1.0, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
