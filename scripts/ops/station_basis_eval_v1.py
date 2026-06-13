#!/usr/bin/env python3
"""Strict go/no-go evaluation for station-basis shadow v1.

v1 has two live-core rules:
- yes_bucket: five-city official-bucket YES at local 16h only.
- no_d1_exh: five-city d1 NO after official-station exhaustion.

no_d2_exh is still recorded in the v1 ledger, but it is an observe-only sleeve
until it has stronger forward evidence.
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("STATION_BASIS_DATA_ROOT") or os.environ.get("DATA_PROJECT_DIR") or ROOT)
SHADOW = DATA_ROOT / "runtime/weather_edge_v1/station_basis_shadow_v1"

LIVE_CORE = {
    "yes_bucket": {
        "label": "YES official bucket h16, five-city live5",
        "historical_roi": 1.207,
        "min_settled": 40,
        "min_roi": 0.15,
        "min_positive_day_rate": 0.55,
        "max_live_core_ask": 0.90,
    },
    "no_d1_exh": {
        "label": "NO d1 exhaustion, five-city live5",
        "historical_roi": 0.128,
        "min_settled": 40,
        "min_roi": 0.03,
        "min_positive_day_rate": 0.55,
        "max_live_core_ask": 0.90,
    },
}
OBSERVE_ONLY = {
    "no_d2_exh": {
        "label": "NO d2 exhaustion observe-only",
        "historical_roi": 0.043,
        "reason": "historical t-stat is weak; do not block or enable live.",
    }
}
MAX_CYCLE_GAP_MIN = 45
MAX_LATEST_CYCLE_AGE_MIN = 35


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def t_stat(values: list[float]) -> float | None:
    if len(values) <= 1:
        return None
    mean = sum(values) / len(values)
    sd = math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))
    return mean / sd * math.sqrt(len(values)) if sd > 0 else None


def summarize_rule(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"settled": 0}
    daily: dict[str, float] = defaultdict(float)
    cost = 0.0
    pnl = 0.0
    wins = 0
    cities = set()
    for row in rows:
        row_cost = float(row["ask"]) * float(row["shares"])
        cost += row_cost
        pnl += float(row["pnl"])
        wins += int(bool(row["win"]))
        cities.add(row["city"])
        daily[str(row["target_date"])] += float(row["pnl"])
    daily_values = list(daily.values())
    positive_days = sum(1 for value in daily_values if value > 0)
    return {
        "settled": len(rows),
        "cities": sorted(cities),
        "days": len(daily_values),
        "positive_days": positive_days,
        "positive_day_rate": positive_days / len(daily_values) if daily_values else None,
        "win_rate": wins / len(rows),
        "cost": round(cost, 6),
        "pnl": round(pnl, 6),
        "roi": pnl / cost if cost else None,
        "daily_t": t_stat(daily_values),
    }


def live_core_price_rows(rows: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    max_ask = float(config["max_live_core_ask"])
    return [row for row in rows if float(row.get("ask", 1.0)) <= max_ask]


def continuity() -> dict[str, Any]:
    cycles = read_jsonl(SHADOW / "cycles.jsonl")
    if not cycles:
        return {"cycles": 0, "ready": False, "reason": "no_cycles"}
    ts = sorted(datetime.fromisoformat(row["ts_utc"]) for row in cycles if row.get("ts_utc"))
    gaps = []
    for left, right in zip(ts, ts[1:]):
        gap_min = (right - left).total_seconds() / 60
        if gap_min > MAX_CYCLE_GAP_MIN:
            gaps.append({"from": left.isoformat(), "to": right.isoformat(), "minutes": round(gap_min, 1)})
    age_min = (datetime.now(timezone.utc) - ts[-1]).total_seconds() / 60
    return {
        "cycles": len(cycles),
        "first_cycle_utc": ts[0].isoformat(),
        "latest_cycle_utc": ts[-1].isoformat(),
        "latest_age_minutes": round(age_min, 1),
        "gaps_over_45m": gaps,
        "ready": not gaps and age_min <= MAX_LATEST_CYCLE_AGE_MIN,
    }


def evaluate() -> dict[str, Any]:
    entries = read_jsonl(SHADOW / "entries.jsonl")
    settlements = read_jsonl(SHADOW / "settlements.jsonl")
    by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in settlements:
        by_rule[str(row["rule"])].append(row)
    summaries = {rule: summarize_rule(rows) for rule, rows in by_rule.items()}
    for rule in set(LIVE_CORE) | set(OBSERVE_ONLY):
        summaries.setdefault(rule, {"settled": 0})

    core_results = {}
    ready = True
    for rule, config in LIVE_CORE.items():
        all_summary = summaries[rule]
        eligible_rows = live_core_price_rows(by_rule.get(rule, []), config)
        summary = summarize_rule(eligible_rows)
        summary["all_settled"] = all_summary.get("settled", 0)
        summary["price_excluded_settled"] = all_summary.get("settled", 0) - summary.get("settled", 0)
        summary["max_live_core_ask"] = config["max_live_core_ask"]
        failures = []
        if summary.get("settled", 0) < config["min_settled"]:
            failures.append(f"settled<{config['min_settled']}")
        roi = summary.get("roi")
        if roi is None or roi < config["min_roi"]:
            failures.append(f"roi<{config['min_roi']:+.0%}")
        pdr = summary.get("positive_day_rate")
        if pdr is None or pdr < config["min_positive_day_rate"]:
            failures.append(f"positive_day_rate<{config['min_positive_day_rate']:.0%}")
        core_results[rule] = {"summary": summary, "failures": failures, "pass": not failures}
        ready = ready and not failures

    cont = continuity()
    ready = ready and bool(cont.get("ready"))
    return {
        "entries": len(entries),
        "settled": len(settlements),
        "pending": len(entries) - len(settlements),
        "continuity": cont,
        "rules": summaries,
        "live_core_results": core_results,
        "observe_only": OBSERVE_ONLY,
        "verdict": "READY_FOR_LIVE_PREP_REVIEW" if ready else "NOT_READY_ACCUMULATE_SHADOW",
    }


def pct(value: float | None) -> str:
    return "NA" if value is None else f"{value:+.1%}"


def print_report(payload: dict[str, Any]) -> None:
    print(f"entries={payload['entries']} settled={payload['settled']} pending={payload['pending']}")
    cont = payload["continuity"]
    print("\n=== continuity ===")
    print(
        f"cycles={cont.get('cycles')} latest={cont.get('latest_cycle_utc')} "
        f"age={cont.get('latest_age_minutes')}m gaps>{MAX_CYCLE_GAP_MIN}m={len(cont.get('gaps_over_45m', []))} "
        f"ready={cont.get('ready')}"
    )
    print("\n=== live core ===")
    for rule, result in payload["live_core_results"].items():
        s = result["summary"]
        print(
            f"{rule:12} settled={s.get('settled', 0):3d} days={s.get('days', 0):2d} "
            f"price_excl={s.get('price_excluded_settled', 0):2d} max_ask={s.get('max_live_core_ask', 'NA')} "
            f"roi={pct(s.get('roi')):>8} pos_days={s.get('positive_days', 0)}/{s.get('days', 0)} "
            f"t={s.get('daily_t') if s.get('daily_t') is not None else 'NA'} "
            f"{'PASS' if result['pass'] else 'FAIL ' + ','.join(result['failures'])}"
        )
    print("\n=== observe only ===")
    for rule in OBSERVE_ONLY:
        s = payload["rules"].get(rule, {"settled": 0})
        print(f"{rule:12} settled={s.get('settled', 0):3d} roi={pct(s.get('roi'))}")
    print(f"\nVERDICT: {payload['verdict']}")


def main() -> int:
    payload = evaluate()
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_report(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
