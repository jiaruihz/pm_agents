#!/usr/bin/env python3
"""Zero-notional observation ledger for lower-threshold all-YES baskets.

This script never places orders and never feeds the dry-run live executor. It
records same-family all-YES opportunities below the formal live-prep threshold
so settlement quality can be studied before considering any future threshold
change.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops.all_yes_underround_guards import BasketGuardConfig, check_candidate, decision_to_dict


RUN_DIR_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "all_yes_underround_paper_v0"
SCAN_JSON_DEFAULT = RUN_DIR_DEFAULT / "latest_observation_scan.json"
STRATEGY_ID = "all_yes_underround_basket_v0"
OBSERVATION_INSTANCE = "all_yes_underround_observation_010_020_v0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["cycle", "monitor"])
    parser.add_argument("--scan-json", default=str(SCAN_JSON_DEFAULT))
    parser.add_argument("--run-dir", default=str(RUN_DIR_DEFAULT))
    parser.add_argument("--shares-per-leg", type=float, default=5.0)
    parser.add_argument("--max-baskets-per-cycle", type=int, default=4)
    parser.add_argument("--max-basket-cost-usd", type=float, default=5.0)
    parser.add_argument("--min-underround", type=float, default=0.01)
    parser.add_argument("--formal-min-underround", type=float, default=0.02)
    parser.add_argument("--max-spread", type=float, default=0.05)
    parser.add_argument("--max-snapshot-age-seconds", type=float, default=180.0)
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def observation_id(scan: dict[str, Any], candidate: dict[str, Any]) -> str:
    snapshot_ts = scan.get("snapshot_summary", {}).get("snapshot_ts_utc_max")
    return "|".join(
        [
            OBSERVATION_INSTANCE,
            str(snapshot_ts),
            str(candidate.get("event_date")),
            str(candidate.get("city")),
            str(candidate.get("event_slug")),
        ]
    )


def opportunity_key(candidate: dict[str, Any]) -> str:
    return "|".join([str(candidate.get("event_date")), str(candidate.get("city")), str(candidate.get("event_slug"))])


def in_observation_band(candidate: dict[str, Any], *, min_underround: float, formal_min_underround: float) -> bool:
    underround = candidate.get("underround")
    if underround is None:
        return False
    value = float(underround)
    return min_underround <= value < formal_min_underround


def cycle(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir)
    basket_path = run_dir / "observation_baskets.jsonl"
    leg_path = run_dir / "observation_leg_quotes.jsonl"
    scan = read_json(Path(args.scan_json))
    existing_rows = read_jsonl(basket_path)
    existing_ids = {str(row.get("observation_id")) for row in existing_rows}
    existing_opportunities = {str(row.get("opportunity_key")) for row in existing_rows}
    guard_cfg = BasketGuardConfig(
        min_underround=args.min_underround,
        shares_per_leg=args.shares_per_leg,
        max_basket_cost_usd=args.max_basket_cost_usd,
        max_yes_spread=args.max_spread,
        max_snapshot_age_seconds=args.max_snapshot_age_seconds,
    )

    appended_baskets: list[dict[str, Any]] = []
    appended_legs: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    skipped_formal: list[dict[str, Any]] = []
    candidates = list(scan.get("paper_shadow_candidates") or [])
    for candidate in candidates:
        oid = observation_id(scan, candidate)
        key = opportunity_key(candidate)
        if not in_observation_band(
            candidate,
            min_underround=args.min_underround,
            formal_min_underround=args.formal_min_underround,
        ):
            if candidate.get("underround") is not None and float(candidate.get("underround")) >= args.formal_min_underround:
                skipped_formal.append({"observation_id": oid, "city": candidate.get("city"), "underround": candidate.get("underround")})
            continue
        guard = check_candidate(cfg=guard_cfg, repo_root=ROOT, candidate=candidate, decision_ts_utc=now_utc())
        if not guard.allow:
            rejected.append({"observation_id": oid, "city": candidate.get("city"), "guard": decision_to_dict(guard)})
            continue
        if len(appended_baskets) >= args.max_baskets_per_cycle:
            continue
        if oid in existing_ids or key in existing_opportunities:
            continue
        recorded_at = now_utc()
        basket = {
            "recorded_at_utc": recorded_at,
            "strategy_id": STRATEGY_ID,
            "strategy_instance": OBSERVATION_INSTANCE,
            "execution_mode": "zero_notional_observation",
            "no_order_placed": True,
            "observation_id": oid,
            "opportunity_key": key,
            "source_scan": str(Path(args.scan_json)),
            "source_snapshot": scan.get("snapshot_path"),
            "snapshot_ts_utc": scan.get("snapshot_summary", {}).get("snapshot_ts_utc_max"),
            "orderbook_fetched_at_utc_min": candidate.get("orderbook_fetched_at_utc_min"),
            "orderbook_fetched_at_utc_max": candidate.get("orderbook_fetched_at_utc_max"),
            "event_date": candidate.get("event_date"),
            "city": candidate.get("city"),
            "event_slug": candidate.get("event_slug"),
            "underround": candidate.get("underround"),
            "formal_min_underround": args.formal_min_underround,
            "observation_min_underround": args.min_underround,
            "total_yes_ask_cost": candidate.get("total_yes_ask_cost"),
            "legs": len(guard.leg_orders),
            "shares_per_leg_reference": guard_cfg.shares_per_leg,
            "reference_basket_cost_usd": guard.basket_cost_usd,
            "reference_gross_profit_usd": guard.expected_profit_usd,
            "guard": decision_to_dict(guard),
        }
        appended_baskets.append(basket)
        existing_opportunities.add(key)
        for index, leg in enumerate(guard.leg_orders):
            appended_legs.append(
                {
                    "recorded_at_utc": recorded_at,
                    "strategy_id": STRATEGY_ID,
                    "strategy_instance": OBSERVATION_INSTANCE,
                    "observation_id": oid,
                    "opportunity_key": key,
                    "leg_index": index,
                    "event_date": candidate.get("event_date"),
                    "city": candidate.get("city"),
                    "event_slug": candidate.get("event_slug"),
                    "condition_id": leg.get("condition_id"),
                    "bracket": leg.get("bracket"),
                    "side": "BUY_YES",
                    "price": leg.get("price"),
                    "shares_reference": leg.get("shares"),
                    "notional_reference_usd": leg.get("notional_usd"),
                    "available_ask_size": leg.get("available_ask_size"),
                    "no_order_placed": True,
                }
            )

    append_jsonl(basket_path, appended_baskets)
    append_jsonl(leg_path, appended_legs)
    result = {
        "command": "cycle",
        "generated_at_utc": now_utc(),
        "strategy_id": STRATEGY_ID,
        "strategy_instance": OBSERVATION_INSTANCE,
        "live_now": False,
        "run_dir": str(run_dir),
        "scan_json": str(Path(args.scan_json)),
        "scanner_candidate_count": len(candidates),
        "observation_band": {
            "min_underround": args.min_underround,
            "formal_min_underround": args.formal_min_underround,
        },
        "appended_baskets": len(appended_baskets),
        "appended_leg_quotes": len(appended_legs),
        "rejected": rejected,
        "skipped_formal_candidates": skipped_formal,
        "total_observation_baskets": len(read_jsonl(basket_path)),
        "verdict": "ZERO_NOTIONAL_OBSERVATION_ONLY",
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "last_observation_cycle.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def monitor(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir)
    last_cycle = read_json(run_dir / "last_observation_cycle.json")
    baskets = read_jsonl(run_dir / "observation_baskets.jsonl")
    by_event_date: dict[str, int] = {}
    by_city: dict[str, int] = {}
    for basket in baskets:
        event_date = str(basket.get("event_date"))
        city = str(basket.get("city"))
        by_event_date[event_date] = by_event_date.get(event_date, 0) + 1
        by_city[city] = by_city.get(city, 0) + 1
    result = {
        "command": "monitor",
        "generated_at_utc": now_utc(),
        "strategy_id": STRATEGY_ID,
        "strategy_instance": OBSERVATION_INSTANCE,
        "live_now": False,
        "run_dir": str(run_dir),
        "observation_baskets": len(baskets),
        "observation_active_event_dates": len(by_event_date),
        "observation_by_event_date": dict(sorted(by_event_date.items())),
        "observation_by_city": dict(sorted(by_city.items())),
        "last_cycle": last_cycle,
        "verdict": "ZERO_NOTIONAL_OBSERVATION_ONLY",
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "observation_monitor.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    append_jsonl(run_dir / "observation_monitor_history.jsonl", [result])
    return result


def main() -> None:
    args = parse_args()
    result = cycle(args) if args.command == "cycle" else monitor(args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
