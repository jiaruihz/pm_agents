#!/usr/bin/env python3
"""Replay historical d1 shadow promotion rows with corrected ladder anchoring."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = ROOT / "scripts/ops/d1_yes_high_mid_shadow_v1.py"
SPEC = importlib.util.spec_from_file_location("d1_yes_high_mid_semantics_audit", RUNNER_PATH)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", default=str(ROOT / "runtime/weather_edge_v1/d1_yes_high_mid_shadow_v1/shadow_events.jsonl"))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    promotions: dict[tuple[str, str], dict] = {}
    for row in runner.order_runtime.read_jsonl(Path(args.journal)):
        if row.get("track") != "promotion":
            continue
        promotions.setdefault((str(row.get("city")), str(row.get("target_date"))), row)

    cache: dict[str, dict] = {}
    rows: list[dict] = []
    for (city, target_date), old in sorted(promotions.items()):
        source = str(old.get("orderbook_file") or "")
        old_current = old.get("current_bracket")
        old_d1 = old.get("d1_bracket")
        replay_status = "source_replayed"
        if source:
            if source not in cache:
                cache[source] = runner.load_ladder(Path(source))
            ladder = cache[source].get((city, target_date), {})
            new_current, new_d1, _, _ = runner.find_current_and_d1(
                ladder,
                float(old.get("running_max_native")),
                str(old.get("unit") or ""),
            )
        else:
            current_b = runner.parse_bracket(old_current)
            d1_b = runner.parse_bracket(old_d1)
            adjacent = bool(
                current_b and d1_b and current_b.high is not None and d1_b.low is not None
                and float(d1_b.low) - float(current_b.high) == 1.0
            )
            new_current = old_current if adjacent else None
            new_d1 = old_d1 if adjacent else None
            replay_status = "adjacency_proved_from_recorded_brackets" if adjacent else "missing_source_unresolved"
        rows.append(
            {
                "city": city,
                "target_date": target_date,
                "entry_cycle_ts_utc": old.get("cycle_ts_utc"),
                "running_max_native": old.get("running_max_native"),
                "running_value": old.get("running_value"),
                "old_current_bracket": old_current,
                "old_d1_bracket": old_d1,
                "new_current_bracket": new_current,
                "new_d1_bracket": new_d1,
                "affected": old_current != new_current or old_d1 != new_d1,
                "replay_status": replay_status,
                "old_same_current_d1": bool(old_current and old_current == old_d1),
                "would_have_been_live_eligible_semantics": bool(new_current and new_d1 and new_current != new_d1),
                "orderbook_file": source,
            }
        )

    payload = {
        "audit": "d1_yes_high_mid_shadow_semantics_v1",
        "pollution_window_utc": {
            "first": min((r.get("entry_cycle_ts_utc") for r in rows), default=None),
            "last": max((r.get("entry_cycle_ts_utc") for r in rows), default=None),
        },
        "promotion_rows": len(rows),
        "affected_rows": sum(bool(r["affected"]) for r in rows),
        "old_same_current_d1_rows": sum(bool(r["old_same_current_d1"]) for r in rows),
        "corrected_semantics_live_eligible_rows": sum(bool(r["would_have_been_live_eligible_semantics"]) for r in rows),
        "financial_impact_usd": 0.0,
        "financial_impact_reason": "historical instance was zero-notional shadow",
        "rows": rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
