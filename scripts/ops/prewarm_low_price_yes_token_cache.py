#!/usr/bin/env python3
"""Prewarm HeadA low-price YES token cache without placing orders.

This is a pipeline hardening utility for the live HeadA runner. It reads the
same canonical candidate denominator as the live runner and resolves YES token
ids into the runner cache ahead of execution cycles. It never fetches books,
writes plans, or submits orders.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
LIVE_SCRIPT = ROOT / "scripts/ops/low_price_yes_lottery_tiny_live.py"
OUT_PATH = ROOT / "runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1/token_prewarm_summary.json"
HISTORY_PATH = ROOT / "runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1/token_prewarm_history.jsonl"


def load_live_module():
    spec = importlib.util.spec_from_file_location("low_price_yes_lottery_tiny_live", LIVE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {LIVE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(ROOT / "runtime/weather.db"))
    parser.add_argument("--min-event-date", default=None)
    parser.add_argument("--max-event-date", default=None)
    parser.add_argument("--min-ask", type=float, default=0.05)
    parser.add_argument("--max-ask", type=float, default=0.20)
    parser.add_argument("--min-edge", type=float, default=0.20)
    parser.add_argument("--max-candidates", type=int, default=80)
    parser.add_argument("--timeout-sec", type=float, default=15.0)
    parser.add_argument("--local-only", action="store_true", help="Use cache/local paper snapshots only; do not call Gamma.")
    parser.add_argument("--output", type=Path, default=OUT_PATH)
    args = parser.parse_args()

    live = load_live_module()
    now = datetime.now(timezone.utc).isoformat()
    runner_args = SimpleNamespace(
        db=args.db,
        min_event_date=args.min_event_date,
        max_event_date=args.max_event_date,
        min_ask=args.min_ask,
        max_ask=args.max_ask,
        min_edge=args.min_edge,
        max_candidates_per_run=args.max_candidates,
        allow_settled=False,
    )
    cache = live.load_token_cache()
    with live.connect(Path(args.db)) as conn:
        min_event_date = live.effective_min_event_date(conn, runner_args)
        raw_counts = live.count_raw(conn, runner_args, min_event_date)
        candidates = live.load_candidates(conn, runner_args, min_event_date)

    status_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    for row in candidates:
        started_cache_size = len(cache)
        try:
            with live.hard_timeout(float(args.timeout_sec), "low-price token prewarm"):
                payload = live.cached_yes_token_only(row, cache) if args.local_only else live.resolve_yes_token(row, cache)
            yes_token = live.safe_str(payload.get("yes_token_id"))
            status = "resolved" if yes_token else "missing_yes_token_id"
            error = payload.get("gamma_error") or ""
        except live.HardTimeoutError as exc:
            payload = {}
            yes_token = ""
            status = "token_resolution_timeout"
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - prewarm should journal failures explicitly.
            payload = {}
            yes_token = ""
            status = "token_resolution_failed"
            error = f"{type(exc).__name__}: {exc}"
        source = live.safe_str(payload.get("source")) if payload else ""
        status_counts[status] += 1
        source_counts[source or "none"] += 1
        rows.append(
            {
                "status": status,
                "source": source,
                "city": row.get("city"),
                "target_date": row.get("event_date"),
                "bracket": row.get("bracket"),
                "condition_id": row.get("condition_id"),
                "market_id": row.get("market_id"),
                "snapshot_ask": row.get("decision_entry_price"),
                "edge": row.get("edge"),
                "yes_token_id_present": bool(yes_token),
                "cache_added": len(cache) > started_cache_size,
                "error": error,
            }
        )

    summary = {
        "generated_at_utc": now,
        "strategy_instance": live.STRATEGY_INSTANCE,
        "strategy_family": live.STRATEGY_FAMILY,
        "no_order_placed": True,
        "db": args.db,
        "effective_min_event_date": min_event_date,
        "effective_max_event_date": args.max_event_date,
        "raw_matching_rows": raw_counts,
        "candidate_rows": len(candidates),
        "status_counts": dict(status_counts),
        "source_counts": dict(source_counts),
        "token_cache_path": str(live.TOKEN_CACHE_OUT.relative_to(ROOT)),
        "output_path": str(args.output.relative_to(ROOT)) if args.output.is_absolute() else str(args.output),
        "rows": rows,
    }
    write_json(args.output, summary)
    append_jsonl(HISTORY_PATH, summary)
    print(json.dumps({k: summary[k] for k in ["generated_at_utc", "candidate_rows", "status_counts", "source_counts", "output_path"]}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not status_counts.get("token_resolution_failed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
