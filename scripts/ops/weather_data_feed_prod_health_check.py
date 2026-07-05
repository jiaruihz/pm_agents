#!/usr/bin/env python3
"""Production health checks for the shared weather data feed.

This checks the data products consumed by live strategies. It does not place
orders and does not mutate runtime files.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops.weather_data_feed_parity_check import check_snapshot, latest_snapshot, load_snapshot


MAC_DATA_FEED_RUNTIME = ROOT.parent / "weather_data_feed_service_runtime"
DEFAULT_SNAPSHOT_DIRS = (
    MAC_DATA_FEED_RUNTIME / "targeted_output/paper_snapshots",
    ROOT.parent / "weather-predict/output/paper_snapshots",
    ROOT / "runtime/weather_edge_v1/market_data/paper_snapshots",
)
DEFAULT_ORDERBOOK_DIRS = (
    MAC_DATA_FEED_RUNTIME / "targeted_output/orderbook_snapshots",
    ROOT.parent / "weather-predict/output/orderbook_snapshots",
    ROOT / "runtime/weather_edge_v1/market_data/orderbook_snapshots",
)
DEFAULT_TELEMETRY_FILES = (
    ROOT / "runtime/weather_edge_v1/theta_current_yes_fade_confirmed_tiny_live_v1/forward_telemetry.jsonl",
    ROOT / "runtime/weather_edge_v1/theta_current_yes_peak_forming_micro_tiny_live_v1/forward_telemetry.jsonl",
)
DEFAULT_SUMMARY_FILES = (
    ROOT / "runtime/weather_edge_v1/theta_current_yes_fade_confirmed_tiny_live_v1/latest_summary.json",
    ROOT / "runtime/weather_edge_v1/theta_current_yes_peak_forming_micro_tiny_live_v1/latest_summary.json",
)
DEFAULT_LIVE_DIR = ROOT / "runtime/weather_edge_v1/live"
CURRENT_YES_LIVE_ORDER_PATTERNS = (
    "theta_current_yes_fade_confirmed_tiny_live_v1_orders.jsonl",
    "theta_current_yes_peak_forming_micro_tiny_live_v1_orders.jsonl",
    "low_price_yes_lottery_tiny_live_v1_orders.jsonl",
    "low_price_yes_take_profit_exit_v1_orders.jsonl",
)
SNAPSHOT_SCHEMA_VERSION = "weather_data_feed_snapshot_v1"


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def latest_existing_snapshot_dir() -> Path:
    for path in DEFAULT_SNAPSHOT_DIRS:
        if path.exists():
            return path
    return DEFAULT_SNAPSHOT_DIRS[0]


def latest_existing_orderbook_dir() -> Path:
    for path in DEFAULT_ORDERBOOK_DIRS:
        if path.exists():
            return path
    return DEFAULT_ORDERBOOK_DIRS[0]


def latest_orderbook_snapshot(root: Path) -> Path | None:
    files = list(root.rglob("orderbook_snapshot_*.jsonl.gz"))
    if not files:
        files = list(root.rglob("orderbook_snapshot_*.jsonl"))
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: deque[dict[str, Any]] = deque(maxlen=max(1, limit))
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                rows.append({"_line_no": line_no, "_parse_error": "json_decode_error"})
                continue
            if isinstance(row, dict):
                row["_line_no"] = line_no
                rows.append(row)
    return list(rows)


def row_key(row: dict[str, Any], fields: Iterable[str]) -> tuple[str, ...]:
    return tuple(str(row.get(field) or "") for field in fields)


def duplicate_examples(rows: list[dict[str, Any]], fields: tuple[str, ...], limit: int = 10) -> tuple[int, list[dict[str, Any]]]:
    counter = Counter(row_key(row, fields) for row in rows)
    duplicate_keys = {key for key, count in counter.items() if count > 1 and any(key)}
    examples = []
    for row in rows:
        key = row_key(row, fields)
        if key in duplicate_keys:
            examples.append({"key": dict(zip(fields, key)), "line_no": row.get("_line_no")})
            if len(examples) >= limit:
                break
    return sum(counter[key] - 1 for key in duplicate_keys), examples


def check_snapshot_duplicates(snapshot_path: Path, *, now_utc: datetime, max_age_min: float) -> dict[str, Any]:
    payload = load_snapshot(snapshot_path)
    rows = [row for row in payload.get("records", []) if isinstance(row, dict)]
    duplicate_count, examples = duplicate_examples(
        rows,
        ("city", "target_date", "token_id", "bracket"),
    )
    row_ts = [parse_utc(row.get("snapshot_ts_utc")) for row in rows]
    valid_ts = [dt for dt in row_ts if dt is not None]
    latest_ts = max(valid_ts) if valid_ts else parse_utc(payload.get("snapshot_ts_utc") or payload.get("ts_utc"))
    age_min = None
    if latest_ts is not None:
        age_min = round((now_utc - latest_ts).total_seconds() / 60.0, 3)
    city_target_counter = Counter((str(row.get("city") or ""), str(row.get("target_date") or "")) for row in rows)
    cities_with_many_targets = sorted(
        city
        for city in {city for city, _target in city_target_counter}
        if len({target for c, target in city_target_counter if c == city and target}) > 2
    )
    return {
        "path": str(snapshot_path),
        "duplicate_record_count": duplicate_count,
        "duplicate_examples": examples,
        "latest_snapshot_ts_utc": latest_ts.isoformat() if latest_ts else "",
        "snapshot_age_min": age_min,
        "snapshot_stale": bool(age_min is not None and age_min > max_age_min),
        "cities_with_more_than_two_target_dates": cities_with_many_targets[:20],
    }


def check_orderbook_snapshots(orderbook_dir: Path, *, now_utc: datetime, max_age_min: float) -> dict[str, Any]:
    latest = latest_orderbook_snapshot(orderbook_dir)
    if latest is None:
        return {
            "dir": str(orderbook_dir),
            "exists": orderbook_dir.exists(),
            "latest_path": "",
            "snapshot_age_min": None,
            "missing": True,
            "stale": False,
        }
    latest_mtime = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)
    age_min = round((now_utc - latest_mtime).total_seconds() / 60.0, 3)
    return {
        "dir": str(orderbook_dir),
        "exists": orderbook_dir.exists(),
        "latest_path": str(latest),
        "latest_mtime_utc": latest_mtime.isoformat(),
        "snapshot_age_min": age_min,
        "missing": False,
        "stale": bool(age_min > max_age_min),
    }


def check_telemetry(path: Path, *, tail_rows: int) -> dict[str, Any]:
    rows = read_jsonl_tail(path, tail_rows)
    parse_errors = [row for row in rows if row.get("_parse_error")]
    required = ("record_type", "created_at_utc", "strategy_instance", "city", "target_date", "decision_status")
    missing = Counter()
    for row in rows:
        if row.get("_parse_error"):
            continue
        for field in required:
            if not str(row.get(field) or "").strip():
                missing[field] += 1
    run_id_counts = Counter(str(row.get("telemetry_run_id") or "") for row in rows if row.get("telemetry_run_id"))
    duplicate_decisions, decision_examples = duplicate_examples(
        rows,
        ("strategy_instance", "created_at_utc", "city", "target_date", "market_id", "current_bracket", "decision_status"),
    )
    status_counts = Counter(str(row.get("decision_status") or "") for row in rows if not row.get("_parse_error"))
    return {
        "path": str(path),
        "exists": path.exists(),
        "checked_rows": len(rows),
        "parse_error_count": len(parse_errors),
        "missing_required_fields": dict(sorted(missing.items())),
        "telemetry_run_id_count": len(run_id_counts),
        "max_rows_per_telemetry_run_id": max(run_id_counts.values(), default=0),
        "duplicate_decision_count": duplicate_decisions,
        "duplicate_examples": decision_examples[:10],
        "decision_status_counts": dict(status_counts.most_common(20)),
    }


def check_live_orders(
    live_dir: Path,
    *,
    tail_rows: int,
    all_files: bool = False,
    extra_files: list[Path] | None = None,
) -> dict[str, Any]:
    if not live_dir.exists():
        files = []
    elif all_files:
        files = sorted(live_dir.glob("*orders.jsonl"))
    else:
        files = [live_dir / pattern for pattern in CURRENT_YES_LIVE_ORDER_PATTERNS if (live_dir / pattern).exists()]
    if extra_files:
        files.extend(path for path in extra_files if path.exists() and path not in files)
    rows: list[dict[str, Any]] = []
    for path in files:
        for row in read_jsonl_tail(path, tail_rows):
            row["_file"] = str(path)
            rows.append(row)
    duplicate_orders, order_examples = duplicate_examples(
        [row for row in rows if row.get("order_id")],
        ("order_id",),
    )
    duplicate_intents, intent_examples = duplicate_examples(
        rows,
        ("strategy_instance", "city", "target_date", "token_id", "signal_side", "order_side"),
    )
    parse_errors = sum(1 for row in rows if row.get("_parse_error"))
    return {
        "live_dir": str(live_dir),
        "scope": "all_live_order_files" if all_files else "current_yes_split_live_order_files",
        "files": [str(path) for path in files],
        "checked_rows": len(rows),
        "parse_error_count": parse_errors,
        "duplicate_order_id_count": duplicate_orders,
        "duplicate_strategy_city_token_count": duplicate_intents,
        "duplicate_examples": (order_examples + intent_examples)[:10],
    }


def check_summaries(paths: list[Path]) -> list[dict[str, Any]]:
    out = []
    for path in paths:
        if not path.exists():
            out.append({"path": str(path), "exists": False})
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        out.append(
            {
                "path": str(path),
                "exists": True,
                "generated_at_utc": payload.get("generated_at_utc"),
                "status": payload.get("status"),
                "snapshot_ts_utc": payload.get("snapshot_ts_utc"),
                "snapshot_age_min": payload.get("snapshot_age_min"),
                "plans": payload.get("plans"),
                "live_enabled": payload.get("live_enabled"),
            }
        )
    return out


def overall_status(sections: dict[str, Any]) -> str:
    parity = sections["snapshot_parity"]
    snapshot = sections["snapshot_duplicates"]
    orderbook = sections.get("orderbook_snapshots", {})
    telemetry = sections["telemetry"]
    live_orders = sections["live_orders"]
    hard_fail = (
        parity.get("status") != "ok"
        or orderbook.get("missing")
        or snapshot.get("duplicate_record_count", 0) > 0
        or any(item.get("parse_error_count", 0) > 0 for item in telemetry)
        or live_orders.get("parse_error_count", 0) > 0
        or live_orders.get("duplicate_order_id_count", 0) > 0
    )
    if hard_fail:
        return "fail"
    warn = (
        snapshot.get("snapshot_stale")
        or orderbook.get("stale")
        or any(item.get("duplicate_decision_count", 0) > 0 for item in telemetry)
        or any(summary.get("status") == "stale_snapshot" for summary in sections["summaries"])
    )
    return "warn" if warn else "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check production weather data feed outputs for stale, bad, or duplicate data.")
    parser.add_argument("--snapshot", default="")
    parser.add_argument("--snapshot-dir", default=str(latest_existing_snapshot_dir()))
    parser.add_argument("--orderbook-dir", default=str(latest_existing_orderbook_dir()))
    parser.add_argument("--runtime-root", default=str(ROOT / "runtime/weather_edge_v1"))
    parser.add_argument("--max-snapshot-age-min", type=float, default=45.0)
    parser.add_argument("--max-orderbook-age-min", type=float, default=75.0)
    parser.add_argument("--tail-telemetry-rows", type=int, default=5000)
    parser.add_argument("--tail-live-order-rows", type=int, default=2000)
    parser.add_argument("--all-live-order-files", action="store_true")
    args = parser.parse_args()

    now_utc = datetime.now(timezone.utc)
    snapshot_path = Path(args.snapshot) if args.snapshot else latest_snapshot(Path(args.snapshot_dir))
    runtime_root = Path(args.runtime_root)
    telemetry_files = [
        runtime_root / "theta_current_yes_fade_confirmed_tiny_live_v1/forward_telemetry.jsonl",
        runtime_root / "theta_current_yes_peak_forming_micro_tiny_live_v1/forward_telemetry.jsonl",
    ]
    summary_files = [
        runtime_root / "theta_current_yes_fade_confirmed_tiny_live_v1/latest_summary.json",
        runtime_root / "theta_current_yes_peak_forming_micro_tiny_live_v1/latest_summary.json",
    ]
    active_live_order_files = [
        runtime_root / "regime_routed_no_tiny_live_v1/live_orders.jsonl",
    ]
    sections = {
        "snapshot_parity": check_snapshot(snapshot_path),
        "snapshot_duplicates": check_snapshot_duplicates(snapshot_path, now_utc=now_utc, max_age_min=args.max_snapshot_age_min),
        "orderbook_snapshots": check_orderbook_snapshots(
            Path(args.orderbook_dir),
            now_utc=now_utc,
            max_age_min=args.max_orderbook_age_min,
        ),
        "telemetry": [check_telemetry(path, tail_rows=args.tail_telemetry_rows) for path in telemetry_files],
        "live_orders": check_live_orders(
            runtime_root / "live",
            tail_rows=args.tail_live_order_rows,
            all_files=args.all_live_order_files,
            extra_files=active_live_order_files,
        ),
        "summaries": check_summaries(summary_files),
    }
    report = {
        "status": overall_status(sections),
        "checked_at_utc": now_utc.isoformat(),
        "snapshot_schema_expected": SNAPSHOT_SCHEMA_VERSION,
        **sections,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
