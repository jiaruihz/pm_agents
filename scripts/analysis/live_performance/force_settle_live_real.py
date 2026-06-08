#!/usr/bin/env python3
"""Direct PnL computation for unsettled live_real rows using pm_history.

For any live_real fill where settlement failed (missing_bracket / NULL),
look up the bracket result from pm_history and compute PnL directly:
  BUY_YES: (final_yes - fill_price) * fill_qty - fees
  BUY_NO:  ((1 - final_yes) - fill_price) * fill_qty - fees

Bracket winner: bracket with final_price >= 0.99 → final_yes=1 for that bracket's YES token.
Quasi-99pct rule: final_price >= 0.99 → 1.0; <= 0.01 → 0.0.

Output: combined settled + directly-computed PnL with daily trajectory.
Does NOT write to DB — read-only analysis.
"""
import sqlite3
import json
import os

DB = "runtime/weather.db"
PH = "runtime/weather_edge_v1/market_data/cache/pm_history"


def get_final_yes(city, date, bracket_label):
    """Look up bracket result from pm_history.
    Returns (final_yes: float|None, source: str, raw_fp: float|None)
    """
    ph_path = f"{PH}/{city}_{date}.json"
    if not os.path.exists(ph_path):
        return None, "no_pm_history", None
    try:
        d = json.load(open(ph_path))
    except Exception as e:
        return None, f"parse_error:{e}", None
    if not isinstance(d, dict):
        return None, "unexpected_format", None
    brackets = d.get("brackets", [])
    if not brackets:
        return None, "empty_brackets", None
    our = next((b for b in brackets if b["label"] == bracket_label), None)
    if our is None:
        # Try normalising: "88-89" vs "88–89" etc.
        norm = bracket_label.replace("–", "-").strip()
        our = next((b for b in brackets
                    if b["label"].replace("–", "-").strip() == norm), None)
    if our is None:
        return None, "bracket_label_mismatch", None
    fp = our.get("final_price")
    if fp is None:
        return None, "no_final_price", None
    closed = our.get("closed", False)
    if fp >= 0.99:
        tag = "pm_history" if closed else "quasi99_not_closed"
        return 1.0, tag, round(fp, 4)
    elif fp <= 0.01:
        tag = "pm_history" if closed else "quasi99_not_closed"
        return 0.0, tag, round(fp, 4)
    else:
        return None, "ambiguous", round(fp, 4)


def compute_pnl(side, fill_price, final_yes, fill_qty, fees):
    f = fees or 0.0
    if side == "BUY_YES":
        return (final_yes - fill_price) * fill_qty - f
    if side == "BUY_NO":
        return ((1.0 - final_yes) - fill_price) * fill_qty - f
    return None


def main():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row

    rows = c.execute(
        "SELECT fill_id, city, target_date, side, bracket, "
        "fill_price, fill_qty, cost_usd, fees_usd, "
        "settlement_status, condition_id "
        "FROM fact_trades "
        "WHERE trade_class='live_real' "
        "AND (settlement_status IS NULL OR settlement_status != 'settled') "
        "AND fill_qty IS NOT NULL AND fill_price IS NOT NULL"
    ).fetchall()
    print(f"Non-settled live_real rows: {len(rows)}")

    resolved = []
    unresolved = []
    for r in rows:
        final_yes, source, conf = get_final_yes(
            r["city"], r["target_date"], r["bracket"] or ""
        )
        if final_yes is not None:
            pnl = compute_pnl(
                r["side"], r["fill_price"], final_yes,
                r["fill_qty"], r["fees_usd"]
            )
            resolved.append({
                "date": r["target_date"], "city": r["city"], "side": r["side"],
                "bracket": r["bracket"], "fill_price": r["fill_price"],
                "cost": r["cost_usd"], "final_yes": final_yes,
                "pnl": pnl, "source": source, "conf": conf,
                "was": r["settlement_status"],
            })
        else:
            unresolved.append({
                "date": r["target_date"], "city": r["city"],
                "bracket": r["bracket"], "reason": source, "conf": conf,
            })

    print(f"Resolved: {len(resolved)}   Unresolved: {len(unresolved)}")

    # By date
    by_date = {}
    for r in resolved:
        d = by_date.setdefault(r["date"], {"n": 0, "cost": 0.0, "pnl": 0.0})
        d["n"] += 1
        d["cost"] += r["cost"]
        d["pnl"] += r["pnl"]

    print("\n=== Directly computed PnL for previously-unsettled rows (by date) ===")
    print(f"{'date':12s} {'n':>4s} {'cost':>8s} {'pnl':>8s} {'roi':>7s}")
    tc = tp = 0.0
    for date in sorted(by_date):
        d = by_date[date]
        roi = d["pnl"] / d["cost"] if d["cost"] else 0
        print(f"{date:12s} {d['n']:4d} {d['cost']:8.1f} {d['pnl']:+8.1f} {roi:+.2f}")
        tc += d["cost"]
        tp += d["pnl"]
    roi_total = tp / tc if tc else 0
    print(f"{'TOTAL':12s} {len(resolved):4d} {tc:8.1f} {tp:+8.1f} {roi_total:+.2f}")

    print("\n=== Unresolved (still missing) ===")
    by_reason = {}
    for r in unresolved:
        by_reason.setdefault(r["reason"], []).append(r)
    for reason, grp in sorted(by_reason.items()):
        dates = sorted(set(x["date"] for x in grp))
        print(f"  {reason}: {len(grp)} rows, dates {dates[0]}..{dates[-1]}")

    # Combined view
    sr = c.execute(
        "SELECT SUM(pnl_usd_at_fill) pnl, SUM(cost_usd) cost, COUNT(*) n "
        "FROM fact_trades "
        "WHERE trade_class='live_real' AND settlement_status='settled'"
    ).fetchone()
    s_cost = sr["cost"] or 0
    s_pnl = sr["pnl"] or 0
    print("\n=== Combined: settled + directly-computed  "
          "(口径: live_real, pnl_usd_at_fill / direct formula) ===")
    print(f"  Settled realized  : n={sr['n']:3d}  cost={s_cost:7.1f}  "
          f"pnl={s_pnl:+7.1f}  ROI={s_pnl/s_cost:+.1%}")
    print(f"  Direct computed   : n={len(resolved):3d}  cost={tc:7.1f}  "
          f"pnl={tp:+7.1f}  ROI={roi_total:+.1%}")
    comb_cost = s_cost + tc
    comb_pnl = s_pnl + tp
    print(f"  COMBINED          : n={sr['n']+len(resolved):3d}  "
          f"cost={comb_cost:7.1f}  pnl={comb_pnl:+7.1f}  "
          f"ROI={comb_pnl/comb_cost:+.1%}")
    print(f"  Still unresolved  : n={len(unresolved):3d}  "
          f"(no pm_history yet / bracket ambiguous)")

    # Full daily trajectory including settled
    print("\n=== Full daily trajectory (settled realized + direct-computed) ===")
    daily = {}
    for r in c.execute(
        "SELECT target_date d, SUM(pnl_usd_at_fill) pnl, SUM(cost_usd) cost, COUNT(*) n "
        "FROM fact_trades "
        "WHERE trade_class='live_real' AND settlement_status='settled' "
        "GROUP BY target_date ORDER BY target_date"
    ):
        daily[r["d"]] = {"settled_pnl": r["pnl"], "settled_n": r["n"],
                         "direct_pnl": 0.0, "direct_n": 0}
    for r in resolved:
        e = daily.setdefault(r["date"], {"settled_pnl": 0.0, "settled_n": 0,
                                          "direct_pnl": 0.0, "direct_n": 0})
        e["direct_pnl"] += r["pnl"]
        e["direct_n"] += 1

    cum = 0.0
    print(f"{'date':12s} {'settled':>8s} {'direct':>8s} {'day_tot':>8s} {'cumul':>8s}")
    for date in sorted(daily):
        e = daily[date]
        day = e["settled_pnl"] + e["direct_pnl"]
        cum += day
        print(f"{date:12s} {e['settled_pnl']:+8.1f} {e['direct_pnl']:+8.1f} "
              f"{day:+8.1f} {cum:+8.1f}")


if __name__ == "__main__":
    main()
