#!/usr/bin/env python3
"""Helper for the Core Carry maker-lineage runner's microstructure mode."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec  # noqa: E402
from scripts.analysis.versioned_artifact_output import (  # noqa: E402
    prepare_new_run_output,
    resolve_run_output,
)
from weather_data_feed.production_paths import current_strategy_snapshots  # noqa: E402


RUNTIME = load_production_spec().pm_runtime_root / "weather_edge_v1/current_yes_core_carry_tiny_live_v2"
SNAPSHOTS = current_strategy_snapshots()
PROFILE = "split_taker_maker_edge_capped_no_fallback_v2"
ARTIFACT_FAMILY = "core_carry_maker_microstructure_v1"


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.open() if line.strip()]


def terminal_state(order_rows: list[dict[str, Any]]) -> tuple[bool, float, int, str]:
    for row in order_rows:
        if row.get("child_order_role") != "core_carry_maker_terminal" or row.get("status") != "filled":
            continue
        auth = (row.get("exchange_response") or {}).get("authoritative_order_state") or {}
        return True, float(auth.get("posted_price") or 0), int(auth.get("reprice_count") or 0), str(row.get("created_at_utc") or "")
    return False, 0.0, 0, ""


def snapshot_book(root: dict[str, Any]) -> dict[str, float]:
    path = SNAPSHOTS / str(root.get("source_snapshot_path") or "")
    if not path.exists():
        return {}
    payload = json.loads(path.read_text())
    token = str(root.get("token_id") or "")
    matches = [r for r in payload.get("records", []) if str(r.get("yes_token_id") or "") == token]
    if not matches:
        return {}
    rec = min(matches, key=lambda r: abs((pd.Timestamp(r.get("snapshot_ts_utc")) - pd.Timestamp(root["created_at_utc"])).total_seconds()))
    return {
        "initial_bid_size": float(rec.get("yes_bid_size") or 0),
        "initial_ask_size": float(rec.get("yes_ask_size") or 0),
        "bid_depth_5c": float(rec.get("yes_depth_bid_5c") or 0),
        "ask_depth_5c": float(rec.get("yes_depth_ask_5c") or 0),
    }


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "intents": len(frame),
        "fills": int(frame.filled.sum()),
        "fill_rate": float(frame.filled.mean()),
        "filled_shares": float(frame.filled.sum() * 5),
        "initial_cap_bound": int(frame.initial_cap_bound.sum()),
        "initial_cap_bound_rate": float(frame.initial_cap_bound.mean()),
        "zero_reprice_intents": int(frame.reprices.eq(0).sum()),
        "intents_over_two_reprices": int(frame.reprices.gt(2).sum()),
        "total_reprices": int(frame.reprices.sum()),
        "median_initial_bid_size_filled": float(frame.loc[frame.filled, "initial_bid_size"].median()),
        "median_initial_bid_size_unfilled": float(frame.loc[~frame.filled, "initial_bid_size"].median()),
        "median_spread_filled": float(frame.loc[frame.filled, "spread"].median()),
        "median_spread_unfilled": float(frame.loc[~frame.filled, "spread"].median()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    resolved_output = resolve_run_output(
        ARTIFACT_FAMILY,
        run_id=args.run_id,
        explicit_output=args.output_dir,
    )
    orders = rows(RUNTIME / "live_orders.jsonl")
    decisions = rows(RUNTIME / "maker_lifecycle_decisions.jsonl")
    roots = [r for r in orders if r.get("child_order_role") == "maker" and r.get("execution_profile") == PROFILE]
    out: list[dict[str, Any]] = []
    for root in roots:
        key = (root.get("city"), root.get("target_date"), root.get("token_id"))
        children = [r for r in orders if (r.get("city"), r.get("target_date"), r.get("token_id")) == key and r.get("maker_only")]
        filled, fill_price, fill_reprices, fill_ts = terminal_state(children)
        t0 = datetime.fromisoformat(root["created_at_utc"])
        local_decisions = []
        for row in decisions:
            if (row.get("city"), row.get("target_date")) != key[:2]:
                continue
            elapsed = (datetime.fromisoformat(row["created_at_utc"]) - t0).total_seconds()
            if 0 <= elapsed <= 1200:
                local_decisions.append(row)
        bid = float(root.get("best_bid") or 0)
        ask = float(root.get("best_ask") or 0)
        post = float(root.get("posted_price") or root.get("limit_price") or 0)
        cap = float(root.get("maker_price_cap") or 0)
        record = {
            "city": key[0], "target_date": key[1], "created_at_utc": root["created_at_utc"],
            "bid": bid, "ask": ask, "post": post, "cap": cap, "spread": ask - bid,
            "ask_improvement": ask - post if post else 0,
            "initial_cap_bound": post > 0 and post >= cap - 0.0005,
            "reprices": sum(r.get("execution_action") == "core_carry_maker_reprice" for r in children),
            "reposts": sum(r.get("execution_action") == "core_carry_maker_repost" for r in children),
            "filled": filled, "fill_price": fill_price, "fill_reprices": fill_reprices,
            "fill_minutes": ((datetime.fromisoformat(fill_ts) - t0).total_seconds() / 60) if fill_ts else None,
            "decision_count": len(local_decisions),
            "decision_action_counts": json.dumps(Counter((r.get("action") or r.get("blocker") or "") for r in local_decisions), sort_keys=True),
            **snapshot_book(root),
        }
        out.append(record)
    frame = pd.DataFrame(out).sort_values(["created_at_utc", "city"])
    recent = frame[frame.created_at_utc.ge("2026-08-03")]
    result = {"profile": PROFILE, "full_profile_window": summarize(frame), "current_10_plus_5_window": summarize(recent)}
    output_dir = prepare_new_run_output(resolved_output)
    frame.to_csv(output_dir / "maker_intents.csv", index=False)
    (output_dir / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
