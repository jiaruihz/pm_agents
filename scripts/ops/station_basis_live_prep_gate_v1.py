#!/usr/bin/env python3
"""Aggregate station-basis v1 live-prep blockers.

This is a read-only go/no-go artifact. It intentionally stops at
READY_FOR_DEPLOY_REVIEW; any real N100/live wiring remains a separate
weather-strategy-deploy flow.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "scripts/ops"
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))

import station_basis_eval_v1  # noqa: E402


DATA_ROOT = Path(os.environ.get("STATION_BASIS_DATA_ROOT") or os.environ.get("DATA_PROJECT_DIR") or ROOT)
SHADOW = DATA_ROOT / "runtime/weather_edge_v1/station_basis_shadow_v1"
EXEC = DATA_ROOT / "runtime/weather_edge_v1/station_basis_exec_v1"
CLOB_GATE = DATA_ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
OUT = SHADOW / "live_prep_gate.json"
MAX_MONITOR_AGE_MIN = 45


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def age_minutes(value: str | None, now: datetime) -> float | None:
    ts = parse_dt(value)
    if ts is None:
        return None
    return round((now - ts).total_seconds() / 60, 1)


def add_blocker(blockers: list[dict[str, Any]], code: str, message: str, **detail: Any) -> None:
    row = {"code": code, "message": message}
    row.update(detail)
    blockers.append(row)


def add_pass(passed: list[dict[str, Any]], code: str, message: str, **detail: Any) -> None:
    row = {"code": code, "message": message}
    row.update(detail)
    passed.append(row)


def clob_gate_status(blockers: list[dict[str, Any]], passed: list[dict[str, Any]]) -> dict[str, Any]:
    gate = read_json(CLOB_GATE)
    if not gate:
        add_blocker(blockers, "clob_coverage_gate_missing", "CLOB fill coverage gate artifact is missing.")
        return {"path": str(CLOB_GATE), "missing": True}
    status = {
        "path": str(CLOB_GATE),
        "gate_pass": gate.get("gate_pass"),
        "fail_reasons": gate.get("fail_reasons", []),
        "db_fill_cost_minus_fact_cost": gate.get("db_fill_cost_minus_fact_cost"),
        "order_caps": gate.get("order_caps"),
    }
    if gate.get("gate_pass") is True:
        add_pass(passed, "clob_coverage_gate_pass", "CLOB fill coverage gate passes.", **status)
    else:
        add_blocker(
            blockers,
            "clob_coverage_gate_false",
            "CLOB fill coverage gate is false; do not publish live_real PnL/ROI/rank/curve.",
            **status,
        )
    return status


def eval_status(blockers: list[dict[str, Any]], passed: list[dict[str, Any]]) -> dict[str, Any]:
    payload = station_basis_eval_v1.evaluate()
    if payload.get("verdict") == "READY_FOR_LIVE_PREP_REVIEW":
        add_pass(passed, "forward_eval_ready", "Station-basis v1 forward evaluation passes live-prep thresholds.")
    else:
        add_blocker(
            blockers,
            "forward_eval_not_ready",
            "Station-basis v1 forward shadow has not passed live-prep thresholds.",
            verdict=payload.get("verdict"),
        )

    continuity = payload.get("continuity") or {}
    if continuity.get("ready"):
        add_pass(
            passed,
            "shadow_continuity_ready",
            "Shadow cycle continuity is within recency/gap limits.",
            latest_cycle_utc=continuity.get("latest_cycle_utc"),
            latest_age_minutes=continuity.get("latest_age_minutes"),
        )
    else:
        add_blocker(
            blockers,
            "shadow_continuity_not_ready",
            "Shadow cycle continuity is stale or has large gaps.",
            continuity=continuity,
        )

    for rule, result in (payload.get("live_core_results") or {}).items():
        summary = result.get("summary") or {}
        failures = result.get("failures") or []
        if result.get("pass"):
            add_pass(
                passed,
                f"{rule}_forward_rule_pass",
                f"{rule} forward live-core thresholds pass.",
                summary=summary,
            )
            continue
        add_blocker(
            blockers,
            f"{rule}_forward_rule_fail",
            f"{rule} forward live-core thresholds fail: {','.join(failures)}.",
            failures=failures,
            summary=summary,
        )
    return payload


def dry_run_status(blockers: list[dict[str, Any]], passed: list[dict[str, Any]], entries: list[dict[str, Any]]) -> dict[str, Any]:
    orders = read_jsonl(EXEC / "orders.jsonl")
    cursor = read_json(EXEC / "cursor.json")
    risk = read_json(EXEC / "risk_config.json")
    allowed = [row for row in orders if row.get("allow")]
    status = {
        "path": str(EXEC),
        "orders": len(orders),
        "allowed_orders": len(allowed),
        "cursor_processed": cursor.get("processed", 0),
        "risk_config": risk,
    }
    if not risk:
        add_blocker(blockers, "dry_run_risk_config_missing", "v1 dry-run risk config is missing.")
    elif risk.get("kill_switch_path") == "runtime/weather_edge_v1/station_basis_shadow_v1/PAUSE":
        add_pass(passed, "dry_run_kill_switch_scoped", "v1 dry-run kill switch points to the v1 shadow PAUSE file.")
    else:
        add_blocker(
            blockers,
            "dry_run_kill_switch_wrong_scope",
            "v1 dry-run kill switch is not scoped to the v1 shadow PAUSE file.",
            kill_switch_path=risk.get("kill_switch_path"),
        )

    if entries and cursor.get("processed", 0) >= len(entries):
        add_pass(
            passed,
            "dry_run_cursor_caught_up",
            "v1 dry-run executor cursor has processed all current shadow entries.",
            entries=len(entries),
            cursor_processed=cursor.get("processed", 0),
        )
    elif entries:
        add_blocker(
            blockers,
            "dry_run_cursor_lagging",
            "v1 dry-run executor cursor is behind shadow entries.",
            entries=len(entries),
            cursor_processed=cursor.get("processed", 0),
        )
    else:
        add_blocker(blockers, "no_shadow_entries", "v1 has no shadow entries yet.")

    if allowed:
        add_pass(passed, "dry_run_order_ledger_exists", "v1 dry-run order ledger has allowed risk-audited entries.", allowed_orders=len(allowed))
    elif entries:
        add_blocker(blockers, "dry_run_no_allowed_orders", "v1 has shadow entries but no allowed dry-run orders.")
    return status


def pending_monitor_status(now: datetime, blockers: list[dict[str, Any]], passed: list[dict[str, Any]]) -> dict[str, Any]:
    monitor = read_json(SHADOW / "pending_monitor.json")
    history = read_jsonl(SHADOW / "pending_monitor_history.jsonl")
    if not monitor:
        add_blocker(blockers, "pending_monitor_missing", "v1 pending monitor artifact is missing.")
        return {"missing": True, "history_rows": len(history)}

    generated_at = monitor.get("generated_at_utc")
    monitor_age = age_minutes(generated_at, now)
    status = {
        "path": str(SHADOW / "pending_monitor.json"),
        "history_path": str(SHADOW / "pending_monitor_history.jsonl"),
        "generated_at_utc": generated_at,
        "age_minutes": monitor_age,
        "pending": monitor.get("pending"),
        "status_counts": monitor.get("status_counts", {}),
        "thesis_counts": monitor.get("thesis_counts", {}),
        "history_rows": len(history),
    }
    if monitor_age is None or monitor_age > MAX_MONITOR_AGE_MIN:
        add_blocker(
            blockers,
            "pending_monitor_stale",
            "v1 pending monitor is stale.",
            age_minutes=monitor_age,
            max_age_minutes=MAX_MONITOR_AGE_MIN,
        )
    else:
        add_pass(passed, "pending_monitor_fresh", "v1 pending monitor is fresh.", age_minutes=monitor_age)

    thesis_counts = monitor.get("thesis_counts") or {}
    breached = sum(int(count) for thesis, count in thesis_counts.items() if "breached" in thesis)
    if breached:
        add_blocker(
            blockers,
            "pending_thesis_breached",
            "At least one pending v1 BUY_NO thesis is already breached by current running value.",
            thesis_counts=thesis_counts,
        )
    elif monitor.get("pending", 0):
        add_pass(passed, "pending_thesis_not_breached", "Open v1 entries have no current thesis breach.", thesis_counts=thesis_counts)

    bad_status = {key: value for key, value in (monitor.get("status_counts") or {}).items() if key != "ok" and value}
    if bad_status:
        add_blocker(blockers, "pending_monitor_non_ok", "Pending monitor has non-ok rows.", status_counts=bad_status)
    elif monitor.get("pending", 0):
        add_pass(passed, "pending_monitor_rows_ok", "Pending monitor rows are ok.")
    return status


def readiness_status(now: datetime, blockers: list[dict[str, Any]], passed: list[dict[str, Any]]) -> dict[str, Any]:
    readiness = read_json(SHADOW / "readiness.json")
    if not readiness:
        add_blocker(blockers, "readiness_probe_missing", "v1 readiness probe artifact is missing.")
        return {"missing": True}
    generated_at = readiness.get("generated_at_utc")
    status = {
        "path": str(SHADOW / "readiness.json"),
        "generated_at_utc": generated_at,
        "age_minutes": age_minutes(generated_at, now),
        "status_counts": readiness.get("status_counts", {}),
    }
    if any(key.endswith("failed") for key in (readiness.get("status_counts") or {})):
        add_blocker(blockers, "readiness_probe_failures", "v1 readiness probe has failed rows.", status_counts=readiness.get("status_counts", {}))
    else:
        add_pass(passed, "readiness_probe_available", "v1 readiness probe is available and has no failed rows.", status_counts=readiness.get("status_counts", {}))
    return status


def build_payload() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    blockers: list[dict[str, Any]] = []
    passed: list[dict[str, Any]] = []
    entries = read_jsonl(SHADOW / "entries.jsonl")
    settlements = read_jsonl(SHADOW / "settlements.jsonl")

    if entries:
        add_pass(passed, "shadow_entries_exist", "v1 shadow ledger has entries.", entries=len(entries))
    else:
        add_blocker(blockers, "shadow_entries_missing", "v1 shadow ledger has no entries.")

    eval_payload = eval_status(blockers, passed)
    clob = clob_gate_status(blockers, passed)
    dry_run = dry_run_status(blockers, passed, entries)
    pending = pending_monitor_status(now, blockers, passed)
    readiness = readiness_status(now, blockers, passed)

    if settlements:
        add_pass(passed, "forward_settlements_exist", "v1 has settled forward entries.", settled=len(settlements))
    else:
        add_blocker(blockers, "forward_settlements_missing", "v1 has no settled forward entries yet.")

    core_blockers = [
        blocker for blocker in blockers
        if blocker["code"] not in {"clob_coverage_gate_false"}
    ]
    verdict = "READY_FOR_DEPLOY_REVIEW" if not blockers else "NOT_READY_ACCUMULATE_SHADOW"
    if core_blockers and clob.get("gate_pass") is False:
        verdict = "NOT_READY_ACCUMULATE_SHADOW_AND_FIX_CLOB_GATE"
    elif not core_blockers and clob.get("gate_pass") is False:
        verdict = "NOT_READY_FIX_CLOB_GATE"

    return {
        "generated_at_utc": now.isoformat(),
        "strategy_id": "station_basis_taker_live5_h16_yes_no_d1_v1",
        "verdict": verdict,
        "live_now": False,
        "entries": len(entries),
        "settled": len(settlements),
        "pending": max(0, len(entries) - len(settlements)),
        "blockers": blockers,
        "passed": passed,
        "components": {
            "eval_v1": eval_payload,
            "clob_coverage_gate": clob,
            "dry_run_execution": dry_run,
            "pending_monitor": pending,
            "readiness": readiness,
        },
        "next_actions": [
            "Keep v1 zero-notional shadow running until yes_bucket and no_d1_exh each have >=40 price-eligible settled forward rows.",
            "Keep current pending monitor active; resolve any breached BUY_NO thesis before counting it as live-prep evidence.",
            "Repair CLOB fill coverage before publishing any live_real PnL/ROI/rank/curve.",
            "If this gate ever reaches READY_FOR_DEPLOY_REVIEW, use weather-strategy-deploy for any N100/live change.",
        ],
    }


def main() -> int:
    payload = build_payload()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(OUT), "verdict": payload["verdict"], "blockers": len(payload["blockers"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
