#!/usr/bin/env python3
"""Detailed PnL curve for live_real.

三层口径合并输出（从严到宽）:
  Layer 1: settlement_status='settled'  → pnl_usd_at_fill (权威)
  Layer 2: missing_bracket / NULL       → pm_history 直接计算 (quasi-settled)
  Layer 3: 今日 open positions          → unrealized_pnl_mid mark-to-market [UNSETTLED]

输出:
  A. 逐日明细(三列: settled / quasi_settled / mtm)
  B. 累计 PnL 曲线
  C. by-side 切片
  D. 今日仓位 MTM 明细
"""
import sqlite3
import json
import os

DB = "runtime/weather.db"
PH = "runtime/weather_edge_v1/market_data/cache/pm_history"


def get_final_yes(city, date, bracket_label):
    ph_path = f"{PH}/{city}_{date}.json"
    if not os.path.exists(ph_path):
        return None, "no_pm_history"
    try:
        d = json.load(open(ph_path))
    except Exception:
        return None, "parse_error"
    if not isinstance(d, dict):
        return None, "null_or_invalid"
    brackets = d.get("brackets", [])
    if not brackets:
        return None, "empty_brackets"
    label = (bracket_label or "").replace("–", "-").strip()
    our = next((b for b in brackets
                if b["label"].replace("–", "-").strip() == label), None)
    if our is None:
        return None, "bracket_mismatch"
    fp = our.get("final_price")
    if fp is None:
        return None, "no_price"
    if fp >= 0.99:
        return 1.0, "quasi99" if not our.get("closed") else "pm_history"
    if fp <= 0.01:
        return 0.0, "quasi99" if not our.get("closed") else "pm_history"
    return None, f"ambiguous({fp:.3f})"


def pnl_formula(side, fill_price, final_yes, fill_qty, fees):
    f = fees or 0.0
    if side == "BUY_YES":
        return (final_yes - fill_price) * fill_qty - f
    if side == "BUY_NO":
        return ((1.0 - final_yes) - fill_price) * fill_qty - f
    return None


def main():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row

    # ── Layer 1: settled ──────────────────────────────────────────────────────
    settled_rows = c.execute(
        "SELECT target_date, side, cost_usd, pnl_usd_at_fill pnl "
        "FROM fact_trades WHERE trade_class='live_real' AND settlement_status='settled'"
    ).fetchall()

    # ── Layer 2: quasi-settle non-settled (missing_bracket + NULL) ────────────
    unstl = c.execute(
        "SELECT target_date, side, bracket, fill_price, fill_qty, cost_usd, fees_usd, "
        "settlement_status, city "
        "FROM fact_trades "
        "WHERE trade_class='live_real' "
        "AND (settlement_status IS NULL OR settlement_status != 'settled') "
        "AND fill_qty IS NOT NULL AND fill_price IS NOT NULL"
    ).fetchall()

    quasi = []
    no_resolve = []
    for r in unstl:
        fy, src = get_final_yes(r["city"], r["target_date"], r["bracket"])
        if fy is not None:
            p = pnl_formula(r["side"], r["fill_price"], fy,
                            r["fill_qty"], r["fees_usd"])
            quasi.append({"date": r["target_date"], "side": r["side"],
                          "cost": r["cost_usd"], "pnl": p, "src": src})
        else:
            no_resolve.append({"date": r["target_date"], "city": r["city"],
                               "reason": src, "cost": r["cost_usd"]})

    # ── Layer 3: open positions MTM ───────────────────────────────────────────
    mtm_rows = c.execute(
        "SELECT target_date, side, city, cost_usd, unrealized_pnl_mid mtm, val_mid "
        "FROM fact_trades "
        "WHERE trade_class='live_real' AND settlement_status IS NULL "
        "AND unrealized_pnl_mid IS NOT NULL"
    ).fetchall()

    # ── Build daily buckets ───────────────────────────────────────────────────
    daily = {}

    def add(d, bucket, side, cost, pnl):
        e = daily.setdefault(d, {
            "s_cost": 0, "s_pnl": 0, "q_cost": 0, "q_pnl": 0,
            "m_cost": 0, "m_pnl": 0,
            "s_no": 0, "s_yes": 0, "q_no": 0, "q_yes": 0,
            "m_no": 0, "m_yes": 0,
        })
        e[f"{bucket}_cost"] += cost or 0
        e[f"{bucket}_pnl"] += pnl or 0
        if side == "BUY_NO":
            e[f"{bucket}_no"] += pnl or 0
        else:
            e[f"{bucket}_yes"] += pnl or 0

    for r in settled_rows:
        add(r["target_date"], "s", r["side"], r["cost_usd"], r["pnl"])
    for r in quasi:
        add(r["date"], "q", r["side"], r["cost"], r["pnl"])
    for r in mtm_rows:
        add(r["target_date"], "m", r["side"], r["cost_usd"], r["mtm"])

    # ── Print: daily detail ───────────────────────────────────────────────────
    print("=" * 90)
    print("口径: live_real | settled=pnl_usd_at_fill | quasi=pm_history直算 | mtm=[UNSETTLED]")
    print(f"DB: {DB}")
    print("=" * 90)

    print(f"\n{'日期':12s} {'settled':>8s} {'quasi':>8s} {'mtm[U]':>8s} "
          f"{'合计':>8s} {'累计':>8s}  "
          f"NO_set  YES_set  NO_qsi  YES_qsi")
    print("-" * 90)
    cum = 0.0
    tot = {"s_cost": 0, "s_pnl": 0, "q_cost": 0, "q_pnl": 0, "m_cost": 0, "m_pnl": 0}
    for date in sorted(daily):
        e = daily[date]
        day = e["s_pnl"] + e["q_pnl"]  # MTM excluded from "合计"
        cum += day
        mtm_str = f"{e['m_pnl']:+.1f}" if e["m_cost"] > 0 else "    —  "
        print(f"{date:12s} {e['s_pnl']:+8.1f} {e['q_pnl']:+8.1f} {mtm_str:>8s} "
              f"{day:+8.1f} {cum:+8.1f}  "
              f"{e['s_no']:+6.1f}  {e['s_yes']:+6.1f}  "
              f"{e['q_no']:+6.1f}  {e['q_yes']:+6.1f}")
        for k in tot:
            tot[k] += e[k]
    print("-" * 90)
    s_roi = tot["s_pnl"] / tot["s_cost"] if tot["s_cost"] else 0
    q_roi = tot["q_pnl"] / tot["q_cost"] if tot["q_cost"] else 0
    total_cost = tot["s_cost"] + tot["q_cost"]
    total_pnl = tot["s_pnl"] + tot["q_pnl"]
    print(f"{'SETTLED合计':12s} {tot['s_pnl']:+8.1f}  n={len(settled_rows)}  "
          f"cost={tot['s_cost']:.1f}  ROI={s_roi:+.1%}")
    print(f"{'QUASI合计':12s} {tot['q_pnl']:+8.1f}  n={len(quasi)}  "
          f"cost={tot['q_cost']:.1f}  ROI={q_roi:+.1%}")
    print(f"{'COMBINED':12s} {total_pnl:+8.1f}  n={len(settled_rows)+len(quasi)}  "
          f"cost={total_cost:.1f}  ROI={total_pnl/total_cost:+.1%}  ← 主结论")
    if tot["m_cost"] > 0:
        print(f"{'MTM[UNSETTLED]':12s} {tot['m_pnl']:+8.1f}  "
              f"cost={tot['m_cost']:.1f}  [估值,不计入ROI]")

    # ── Unresolved ────────────────────────────────────────────────────────────
    if no_resolve:
        by_r = {}
        for r in no_resolve:
            by_r.setdefault(r["reason"], {"n": 0, "cost": 0.0, "dates": set()})
            by_r[r["reason"]]["n"] += 1
            by_r[r["reason"]]["cost"] += r["cost"]
            by_r[r["reason"]]["dates"].add(r["date"])
        print(f"\n尚未能计算 ({len(no_resolve)} 笔):")
        for reason, v in sorted(by_r.items()):
            dates = sorted(v["dates"])
            print(f"  {reason}: {v['n']} 笔 cost={v['cost']:.1f} "
                  f"日期 {dates[0]}..{dates[-1]}")

    # ── Today's open MTM detail ───────────────────────────────────────────────
    today_mtm = [r for r in mtm_rows]
    if today_mtm:
        print(f"\n今日开仓 MTM 明细 [UNSETTLED] ({len(today_mtm)} 笔):")
        by_city = {}
        for r in today_mtm:
            k = (r["target_date"], r["city"], r["side"])
            e = by_city.setdefault(k, {"cost": 0, "mtm": 0, "n": 0, "val_mid": r["val_mid"]})
            e["cost"] += r["cost_usd"]
            e["mtm"] += r["mtm"]
            e["n"] += 1
        print(f"  {'date':10s} {'city':15s} {'side':8s} {'n':>3s} "
              f"{'cost':>7s} {'mtm':>7s} {'val_mid':>8s}")
        for (d, city, side), e in sorted(by_city.items(), key=lambda x: x[1]["mtm"]):
            print(f"  {d:10s} {city:15s} {side:8s} {e['n']:3d} "
                  f"{e['cost']:7.1f} {e['mtm']:+7.2f} {e['val_mid'] or 0:8.3f}")
        tc = sum(e["cost"] for e in by_city.values())
        tp = sum(e["mtm"] for e in by_city.values())
        print(f"  {'TOTAL':>37s} cost={tc:.1f}  mtm={tp:+.2f}")


if __name__ == "__main__":
    main()
