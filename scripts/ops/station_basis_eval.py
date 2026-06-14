#!/usr/bin/env python3
"""Go/no-go evaluation + data-continuity audit for the station-basis shadow.

Reads the shadow ledgers and answers:
1. Realized shadow performance by rule/group vs backtest expectation.
2. Data continuity: gaps in the cycle log during each city's active local
   window (Mac sleep drops collection — shadow validation is only trustworthy
   over windows that were actually covered).
3. A blunt go/no-go verdict: enough settled samples AND realized sign/CI
   consistent with backtest before any live consideration.

Read-only. No network, no orders.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHADOW = ROOT / "runtime/weather_edge_v1/station_basis_shadow"

# backtest expectations (docs 2026-06-12/13) — the bar shadow must roughly meet
BACKTEST = {
    "yes_bucket": {"roi": 0.17, "label": "YES官方档 (backtest +17%, t=4)"},
    "no_d1_exh": {"roi": 0.055, "label": "NO d1 衰竭 (backtest +5.5%, t=3)"},
    "no_d2_exh": {"roi": 0.02, "label": "NO d2 衰竭 (backtest ~+2%)"},
}
MIN_SETTLED_FOR_GO = 40  # per-rule settled count before a verdict is meaningful

REPAIRED = {"Paris", "London", "Milan", "Chicago", "KualaLumpur", "PanamaCity", "Jakarta"}


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def performance() -> None:
    setts = read_jsonl(SHADOW / "settlements.jsonl")
    entries = read_jsonl(SHADOW / "entries.jsonl")
    print(f"entries={len(entries)} settled={len(setts)} pending={len(entries) - len(setts)}\n")
    if not setts:
        print("no settlements yet — let shadow accumulate")
        return

    # aggregate by rule and by rule x group
    agg: dict[tuple, dict] = defaultdict(lambda: {"n": 0, "wins": 0, "cost": 0.0, "pnl": 0.0, "daily": defaultdict(float)})
    for r in setts:
        cost = r["ask"] * r["shares"]
        for key in [(r["rule"], "ALL"), (r["rule"], "repaired" if r["city"] in REPAIRED else "other")]:
            a = agg[key]
            a["n"] += 1
            a["wins"] += int(r["win"])
            a["cost"] += cost
            a["pnl"] += r["pnl"]
            a["daily"][r["target_date"]] += r["pnl"]

    print(f"{'rule':14} {'grp':9} {'n':>4} {'win':>5} {'roi':>8} {'t':>6}  vs backtest")
    for (rule, grp), a in sorted(agg.items()):
        roi = a["pnl"] / a["cost"] if a["cost"] else float("nan")
        daily = list(a["daily"].values())
        t = (
            statistics_t(daily)
            if len(daily) > 1
            else float("nan")
        )
        exp = BACKTEST.get(rule, {}).get("roi")
        flag = ""
        if grp == "repaired" and exp is not None and a["n"] >= 10:
            flag = "✓ sign ok" if (roi > 0) == (exp > 0) else "✗ SIGN DIVERGES"
        print(f"{rule:14} {grp:9} {a['n']:>4} {a['wins'] / a['n']:>5.2f} {roi:>+8.1%} {t:>+6.2f}  {flag}")


def statistics_t(daily: list[float]) -> float:
    n = len(daily)
    m = sum(daily) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in daily) / (n - 1)) if n > 1 else 0.0
    return m / sd * math.sqrt(n) if sd > 0 else float("nan")


def continuity() -> None:
    cycles = read_jsonl(SHADOW / "cycles.jsonl")
    if not cycles:
        print("\nno cycles logged")
        return
    ts = sorted(
        datetime.fromisoformat(c["ts_utc"]) for c in cycles if c.get("ts_utc")
    )
    span_h = (ts[-1] - ts[0]).total_seconds() / 3600
    # gaps > 45 min between consecutive cycle batches (loop is 15-min)
    gaps = []
    for a, b in zip(ts, ts[1:]):
        dt_min = (b - a).total_seconds() / 60
        if dt_min > 45:
            gaps.append((a, b, dt_min))
    print(f"\n=== data continuity ===")
    print(f"cycle span: {ts[0].isoformat()} .. {ts[-1].isoformat()} ({span_h:.1f}h)")
    print(f"gaps >45min: {len(gaps)}")
    for a, b, m in gaps[-8:]:
        print(f"  GAP {m/60:.1f}h  {a.strftime('%m-%d %H:%M')} -> {b.strftime('%m-%d %H:%M')} UTC")
    if gaps:
        print("  ^ Mac sleep likely; shadow misses entries in these windows. "
              "For trustworthy validation run on always-on host (N100).")


def verdict() -> None:
    setts = read_jsonl(SHADOW / "settlements.jsonl")
    by_rule: dict[str, list[dict]] = defaultdict(list)
    for r in setts:
        if r["city"] in REPAIRED:
            by_rule[r["rule"]].append(r)
    print("\n=== go/no-go (repaired cities only) ===")
    ready = True
    for rule, exp in BACKTEST.items():
        rows = by_rule.get(rule, [])
        n = len(rows)
        if n < MIN_SETTLED_FOR_GO:
            print(f"  {rule:14} settled={n:>3}/{MIN_SETTLED_FOR_GO}  → 样本不足，继续观察")
            ready = False
            continue
        cost = sum(r["ask"] * r["shares"] for r in rows)
        pnl = sum(r["pnl"] for r in rows)
        roi = pnl / cost if cost else float("nan")
        ok = (roi > 0) == (exp["roi"] > 0)
        print(f"  {rule:14} settled={n:>3}  roi={roi:+.1%} (exp {exp['roi']:+.0%})  {'✓' if ok else '✗ DIVERGES'}")
        ready = ready and ok
    print(f"\n  VERDICT: {'READY for live-prep review' if ready else 'NOT READY — accumulate more shadow'}")


def main() -> int:
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("all", "perf"):
        performance()
    if cmd in ("all", "continuity"):
        continuity()
    if cmd in ("all", "verdict"):
        verdict()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
