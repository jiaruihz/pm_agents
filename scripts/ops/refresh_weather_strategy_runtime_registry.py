#!/usr/bin/env python3
"""Refresh local DB tables for weather strategy live/shadow runtime status.

This script intentionally reads local synced artifacts only. Remote process
truth should be synced or added by a separate, explicit health check; the
registry remains useful even when N100 is not reachable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "runtime/weather.db"
RUNTIME_ROOT = ROOT / "runtime/weather_edge_v1"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def file_mtime(path: Path) -> datetime | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_parse_error": str(exc)}


def count_lines(path: Path | None) -> int | None:
    if path is None or not path.exists():
        return None
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def latest_record_ts(path: Path) -> datetime | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            end = handle.tell()
            block = b""
            step = 4096
            while end > 0 and block.count(b"\n") < 2:
                take = min(step, end)
                end -= take
                handle.seek(end)
                block = handle.read(take) + block
            lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            return None
        record = json.loads(lines[-1].decode("utf-8"))
    except Exception:
        return None
    for key in [
        "created_at_utc",
        "generated_at_utc",
        "snapshot_ts_utc",
        "decision_snapshot_ts_utc",
        "ts_utc",
        "live_attempt_ts_utc",
        "book_fetched_at_utc",
    ]:
        dt = parse_dt(record.get(key))
        if dt:
            return dt
    return None


def sample_last_json(path: Path) -> str:
    if not path.exists() or path.stat().st_size == 0:
        return "{}"
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            end = handle.tell()
            block = b""
            step = 4096
            while end > 0 and block.count(b"\n") < 2:
                take = min(step, end)
                end -= take
                handle.seek(end)
                block = handle.read(take) + block
            lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            return "{}"
        record = json.loads(lines[-1].decode("utf-8"))
        return json.dumps(record, ensure_ascii=False, sort_keys=True)
    except Exception:
        return "{}"


from src.strategies.runtime.specs import StrategySpec  # noqa: E402


def strategy_specs() -> list[StrategySpec]:
    from src.strategies.runtime.specs import load_instance_specs

    return load_instance_specs()


SHADOW_QUEUE = [
    {
        "shadow_id": "current_yes_peak_hazard_v3_direct",
        "display_name": "Current YES peak hazard V3 direct",
        "family": "reheat_risk.current_yes",
        "priority": "high",
        "status": "proposed",
        "source_doc": "docs/analysis/2026-06/2026-06-23-current-yes-future-break-hazard-v3.md",
        "target_runtime_dir": "runtime/weather_edge_v1/current_yes_peak_hazard_shadow_bundle_v1",
        "required_fields_json": ["p_survive_v3", "edge_v3", "plateau_count", "temp_trend_3h_f", "forecast_peak_delta"],
        "notes": "Track V3 live-like, edge>=0.05, and downtrend variants as would-orders.",
    },
    {
        "shadow_id": "current_yes_peak_hazard_v31_residual_score",
        "display_name": "Current YES peak hazard V3.1 residual score",
        "family": "reheat_risk.current_yes",
        "priority": "low",
        "status": "proposed",
        "source_doc": "docs/analysis/2026-06/2026-06-23-current-yes-future-break-hazard-v31.md",
        "target_runtime_dir": "runtime/weather_edge_v1/current_yes_peak_hazard_shadow_bundle_v1",
        "required_fields_json": ["p_survive_v31", "edge_v31", "residual_v31"],
        "notes": "Record score only; not a primary BUY current-YES candidate after V3.1 trade failure.",
    },
    {
        "shadow_id": "current_bracket_no_late_peak_runway_v1",
        "display_name": "Current-bracket NO late peak runway",
        "family": "reheat_risk.current_bracket_no",
        "priority": "high",
        "status": "proposed",
        "source_doc": "docs/analysis/2026-06/2026-06-22-current-yes-climbing-no-peak-runway-regate-v1.md",
        "target_runtime_dir": "runtime/weather_edge_v1/current_bracket_no_shadow_bundle_v1",
        "required_fields_json": ["no_ask", "forecast_peak_delta", "decision_hour_local", "current_running_max"],
        "notes": "Shadow late-day current-bracket NO expression; do not infer NO price from YES.",
    },
    {
        "shadow_id": "current_bracket_no_afternoon_peak_classifier_v1",
        "display_name": "Current-bracket NO afternoon peak classifier",
        "family": "reheat_risk.current_bracket_no",
        "priority": "medium",
        "status": "blocked",
        "source_doc": "docs/analysis/2026-06/2026-06-23-current-bracket-no-afternoon-peak-classifier-v1.md",
        "target_runtime_dir": "runtime/weather_edge_v1/current_bracket_no_shadow_bundle_v1",
        "required_fields_json": ["p_afternoon_peak", "real_no_ask", "depth5", "forecast_point_in_time_status"],
        "blockers_json": ["forecast peak still research backfill, not verified point-in-time"],
        "notes": "Good backtest point estimate, but requires live point-in-time forecast validation before active shadow.",
    },
    {
        "shadow_id": "current_yes_no_d1_d2_ladder_selector_v1",
        "display_name": "Current YES / NO d1-d2 ladder selector",
        "family": "reheat_risk.expression_selector",
        "priority": "medium",
        "status": "proposed",
        "source_doc": "docs/analysis/2026-06/2026-06-21-reheat-risk-yes-no-expression-map.md",
        "target_runtime_dir": "runtime/weather_edge_v1/current_yes_no_ladder_selector_shadow_v1",
        "required_fields_json": ["current_yes_ask", "d1_no_ask", "d2_no_ask", "ladder_cost", "settlement_payoff"],
        "notes": "Compare payoff expression for the same no-reheat/reheat state.",
    },
    {
        "shadow_id": "current_yes_maker_then_taker_execution_v0",
        "display_name": "Current YES maker-then-taker execution",
        "family": "execution.current_yes",
        "priority": "medium",
        "status": "proposed",
        "source_doc": "docs/analysis/2026-06/2026-06-20-current-yes-maker-then-taker-execution-plan-v0.md",
        "target_runtime_dir": "runtime/weather_edge_v1/current_yes_maker_then_taker_shadow_v0",
        "required_fields_json": ["maker_quote", "queue_status", "fill_or_cancel", "taker_fallback_edge"],
        "notes": "Execution shadow, not a new alpha model.",
    },
]


def rel_path(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def path_for(spec: StrategySpec, maybe_path: str | None) -> Path | None:
    if not maybe_path:
        return None
    path = Path(maybe_path)
    if path.is_absolute():
        return path
    if str(path).startswith("runtime/"):
        candidate = ROOT / path
        if candidate.exists():
            return candidate
        mirror_prefix = Path("runtime/weather_edge_v1/remote_pm_agent")
        try:
            native_tail = path.relative_to(mirror_prefix)
        except ValueError:
            return candidate
        native_candidate = ROOT / "runtime/weather_edge_v1" / native_tail
        if native_candidate.exists():
            return native_candidate
        return candidate
    if spec.runtime_dir:
        candidate = ROOT / spec.runtime_dir / path
        if candidate.exists():
            return candidate
        mirror_prefix = Path("runtime/weather_edge_v1/remote_pm_agent")
        try:
            native_tail = Path(spec.runtime_dir).relative_to(mirror_prefix)
        except ValueError:
            return candidate
        native_candidate = ROOT / "runtime/weather_edge_v1" / native_tail / path
        if native_candidate.exists():
            return native_candidate
        return candidate
    return ROOT / path


def health_from(spec: StrategySpec, summary: dict[str, Any], latest_dt: datetime | None, row_counts: dict[str, int]) -> str:
    now = utc_now()
    age_min = (now - latest_dt).total_seconds() / 60 if latest_dt else None
    if spec.lifecycle_status in {"shelved", "stale"}:
        return "shelved" if spec.lifecycle_status == "shelved" else "stale"
    if spec.lifecycle_status == "blocked":
        return "blocked"
    if summary.get("verdict") == "NOT_READY_ACCUMULATE_SHADOW" or summary.get("blockers"):
        return "blocked"
    if summary.get("status") == "stale_snapshot":
        return "stale"
    if age_min is None:
        return spec.default_health
    stale_limit = 90 if spec.execution_mode in {"live", "zero_notional_shadow", "monitor"} else 1440
    if age_min > stale_limit:
        return "stale"
    if row_counts.get("live_order_rows", 0) or row_counts.get("shadow_rows", 0) or row_counts.get("telemetry_rows", 0):
        return "healthy"
    return "idle"


def fact_trade_aggregates(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            strategy_name,
            strategy_id,
            execution_mode,
            trade_class,
            COUNT(*) AS rows,
            SUM(cost_usd) AS cost_usd,
            MIN(target_date) AS first_target_date,
            MAX(target_date) AS last_target_date,
            MAX(fill_ts_utc) AS latest_fill_ts_utc
        FROM fact_trades
        GROUP BY strategy_name, strategy_id, execution_mode, trade_class
        """
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row["strategy_name"] or row["strategy_id"])
        item = out.setdefault(
            key,
            {
                "fact_trade_rows": 0,
                "fact_live_real_rows": 0,
                "fact_cost_usd": 0.0,
                "first_target_date": None,
                "last_target_date": None,
                "latest_fill_ts_utc": None,
            },
        )
        n = int(row["rows"] or 0)
        item["fact_trade_rows"] += n
        if row["trade_class"] == "live_real":
            item["fact_live_real_rows"] += n
        item["fact_cost_usd"] += float(row["cost_usd"] or 0.0)
        if row["first_target_date"] and (item["first_target_date"] is None or row["first_target_date"] < item["first_target_date"]):
            item["first_target_date"] = row["first_target_date"]
        if row["last_target_date"] and (item["last_target_date"] is None or row["last_target_date"] > item["last_target_date"]):
            item["last_target_date"] = row["last_target_date"]
        if row["latest_fill_ts_utc"] and (item["latest_fill_ts_utc"] is None or row["latest_fill_ts_utc"] > item["latest_fill_ts_utc"]):
            item["latest_fill_ts_utc"] = row["latest_fill_ts_utc"]
    return out


def add_artifact(
    artifacts: list[dict[str, Any]],
    strategy_instance: str,
    kind: str,
    path: Path | None,
    refreshed_at: str,
) -> None:
    if path is None or not path.exists():
        return
    source = rel_path(path) or str(path)
    artifact_key = hashlib.sha1(f"{strategy_instance}:{source}:{kind}".encode("utf-8")).hexdigest()
    if any(row.get("artifact_key") == artifact_key for row in artifacts):
        return
    row_count = count_lines(path) if path.suffix == ".jsonl" else None
    artifacts.append(
        {
            "artifact_key": artifact_key,
            "strategy_instance": strategy_instance,
            "artifact_kind": kind,
            "source_path": source,
            "row_count": row_count,
            "size_bytes": path.stat().st_size,
            "mtime_utc": iso(file_mtime(path)),
            "latest_record_ts_utc": iso(latest_record_ts(path)) if path.suffix == ".jsonl" else None,
            "sample_json": sample_last_json(path) if path.suffix == ".jsonl" else "{}",
            "refreshed_at_utc": refreshed_at,
        }
    )


def summary_int(summary: dict[str, Any], keys: list[str]) -> int:
    for key in keys:
        value = summary.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def summary_float(summary: dict[str, Any], caps: dict[str, Any], keys: list[str]) -> float | None:
    for source in (caps, summary):
        for key in keys:
            value = source.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def active_tmux_sessions() -> set[str]:
    if not shutil.which("tmux"):
        return set()
    sessions: set[str] = set()
    sockets = [""] + [s.strip() for s in os.environ.get("WEATHER_RUNTIME_TMUX_SOCKETS", "weather-jrs").split(",") if s.strip()]
    for socket in sockets:
        cmd = ["tmux"]
        if socket:
            cmd.extend(["-L", socket])
        cmd.extend(["list-sessions", "-F", "#{session_name}"])
        proc = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode == 0:
            sessions.update(line.strip() for line in proc.stdout.splitlines() if line.strip())
    return sessions


def refresh(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    refreshed_at = iso(utc_now()) or ""
    fact = fact_trade_aggregates(conn)
    tmux_sessions = active_tmux_sessions()
    registry_rows: list[dict[str, Any]] = []
    artifact_rows: list[dict[str, Any]] = []

    for spec in strategy_specs():
        runtime_dir = ROOT / spec.runtime_dir if spec.runtime_dir else None
        summary_path = path_for(spec, spec.summary_file)
        primary_journal_path = path_for(spec, spec.primary_journal)
        live_order_path = path_for(spec, spec.live_order_file)
        paper_order_path = path_for(spec, spec.paper_order_file)
        telemetry_path = path_for(spec, spec.telemetry_file)
        summary = read_json(summary_path) if summary_path else {}

        paths = [p for p in [summary_path, primary_journal_path, live_order_path, paper_order_path, telemetry_path] if p]
        latest_mtime = max((file_mtime(p) for p in paths if p and p.exists()), default=None)
        summary_ts = parse_dt(summary.get("generated_at_utc") or summary.get("refreshed_at_utc"))
        data_ts = parse_dt(summary.get("snapshot_ts_utc"))
        if data_ts is None and primary_journal_path:
            data_ts = latest_record_ts(primary_journal_path)
        latest_dt = max([dt for dt in [summary_ts, data_ts, latest_mtime] if dt], default=None)

        live_order_rows = count_lines(live_order_path) or 0
        paper_order_rows = count_lines(paper_order_path) or 0
        shadow_rows = 0
        if primary_journal_path and "shadow" in (primary_journal_path.name + str(primary_journal_path.parent)):
            shadow_rows = count_lines(primary_journal_path) or 0
        telemetry_rows = count_lines(telemetry_path) or 0 if telemetry_path else 0
        if primary_journal_path and primary_journal_path.name in {"opportunities.jsonl", "sources.jsonl", "books.jsonl"}:
            shadow_rows = count_lines(primary_journal_path) or 0
        row_counts = {
            "live_order_rows": live_order_rows,
            "shadow_rows": shadow_rows,
            "telemetry_rows": telemetry_rows,
        }
        blockers = summary.get("blockers") or []
        blocker_count = len(blockers) if isinstance(blockers, list) else 1
        health = health_from(spec, summary, latest_dt, row_counts)
        caps = summary.get("caps") if isinstance(summary.get("caps"), dict) else {}
        fact_key = spec.strategy_instance
        fact_row = fact.get(fact_key) or {}
        live_enabled = summary.get("live_enabled")
        if live_enabled is None and spec.expected_live is not None:
            live_enabled = spec.expected_live

        registry_rows.append(
            {
                "strategy_instance": spec.strategy_instance,
                "strategy_id": summary.get("strategy_id") or summary.get("strategy_instance") or spec.strategy_instance,
                "display_name": spec.display_name,
                "family": spec.family,
                "lifecycle_status": spec.lifecycle_status,
                "execution_mode": spec.execution_mode,
                "health_status": health,
                "source_layer": spec.source_layer,
                "runtime_dir": spec.runtime_dir,
                "summary_path": rel_path(summary_path),
                "primary_journal_path": rel_path(primary_journal_path),
                "latest_summary_ts_utc": iso(summary_ts),
                "latest_data_ts_utc": iso(data_ts),
                "latest_artifact_mtime_utc": iso(latest_mtime),
                "heartbeat_age_min": (utc_now() - latest_dt).total_seconds() / 60 if latest_dt else None,
                "candidate_rows": summary_int(
                    summary,
                    [
                        "candidate_rows",
                        "pre_fresh_candidates",
                        "accepted_candidates",
                        "routed_candidates",
                        "selected_rows_before_dedupe",
                    ],
                ),
                "plan_rows": summary_int(summary, ["plans", "plans_written", "execution_eligible", "paper_orders"]),
                "live_order_rows": live_order_rows,
                "paper_order_rows": paper_order_rows,
                "shadow_rows": shadow_rows,
                "telemetry_rows": telemetry_rows,
                "fact_trade_rows": int(fact_row.get("fact_trade_rows") or 0),
                "fact_live_real_rows": int(fact_row.get("fact_live_real_rows") or 0),
                "fact_cost_usd": fact_row.get("fact_cost_usd"),
                "first_target_date": fact_row.get("first_target_date"),
                "last_target_date": fact_row.get("last_target_date"),
                "latest_fill_ts_utc": fact_row.get("latest_fill_ts_utc"),
                "cap_order_notional": summary_float(summary, caps, ["max_order_notional", "max_notional_per_trade", "base_notional"]),
                "cap_city_day_notional": summary_float(summary, caps, ["max_city_day_notional", "max_notional_per_city_day"]),
                "cap_total_day_notional": summary_float(summary, caps, ["max_notional_total_day", "daily_gross_cap"]),
                "live_enabled": None if live_enabled is None else int(bool(live_enabled)),
                "process_status": (
                    "running"
                    if spec.tmux_session and spec.tmux_session in tmux_sessions
                    else "stopped"
                    if spec.tmux_session
                    else "unknown"
                ),
                "blocker_count": blocker_count,
                "blockers_json": json.dumps(blockers, ensure_ascii=False, sort_keys=True),
                "summary_json": json.dumps(summary, ensure_ascii=False, sort_keys=True),
                "notes": spec.notes,
                "refreshed_at_utc": refreshed_at,
            }
        )
        add_artifact(artifact_rows, spec.strategy_instance, "summary", summary_path, refreshed_at)
        add_artifact(artifact_rows, spec.strategy_instance, "primary_journal", primary_journal_path, refreshed_at)
        add_artifact(artifact_rows, spec.strategy_instance, "live_orders", live_order_path, refreshed_at)
        add_artifact(artifact_rows, spec.strategy_instance, "paper_orders", paper_order_path, refreshed_at)
        add_artifact(artifact_rows, spec.strategy_instance, "telemetry", telemetry_path, refreshed_at)
        for kind, path_text in spec.artifact_files:
            add_artifact(artifact_rows, spec.strategy_instance, kind, path_for(spec, path_text), refreshed_at)

    conn.execute("DELETE FROM weather_strategy_runtime_artifacts")
    conn.execute("DELETE FROM weather_strategy_runtime_registry")
    if registry_rows:
        cols = list(registry_rows[0].keys())
        conn.executemany(
            f"INSERT INTO weather_strategy_runtime_registry ({','.join(cols)}) VALUES ({','.join(['?'] * len(cols))})",
            [[row.get(col) for col in cols] for row in registry_rows],
        )
    if artifact_rows:
        cols = list(artifact_rows[0].keys())
        conn.executemany(
            f"INSERT INTO weather_strategy_runtime_artifacts ({','.join(cols)}) VALUES ({','.join(['?'] * len(cols))})",
            [[row.get(col) for col in cols] for row in artifact_rows],
        )

    conn.execute("DELETE FROM weather_strategy_shadow_queue")
    queue_rows = []
    for item in SHADOW_QUEUE:
        row = dict(item)
        for key in ["required_fields_json", "blockers_json"]:
            value = row.get(key, [])
            row[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        row.setdefault("blockers_json", "[]")
        row.setdefault("created_at_utc", refreshed_at)
        row["refreshed_at_utc"] = refreshed_at
        queue_rows.append(row)
    if queue_rows:
        cols = [
            "shadow_id",
            "display_name",
            "family",
            "proposed_execution_mode",
            "priority",
            "status",
            "source_doc",
            "target_runtime_dir",
            "required_fields_json",
            "blockers_json",
            "notes",
            "created_at_utc",
            "refreshed_at_utc",
        ]
        for row in queue_rows:
            row.setdefault("proposed_execution_mode", "zero_notional_shadow")
        conn.executemany(
            f"INSERT INTO weather_strategy_shadow_queue ({','.join(cols)}) VALUES ({','.join(['?'] * len(cols))})",
            [[row.get(col) for col in cols] for row in queue_rows],
        )

    conn.commit()
    return {
        "registry_rows": len(registry_rows),
        "artifact_rows": len(artifact_rows),
        "shadow_queue_rows": len(queue_rows),
        "refreshed_at_utc": refreshed_at,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    try:
        apply_schema_canonical(conn)
        result = refresh(conn)
    finally:
        conn.close()
    payload = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
