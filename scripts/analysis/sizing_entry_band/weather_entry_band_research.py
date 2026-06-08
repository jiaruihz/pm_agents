#!/usr/bin/env python3
"""Entry-price band research on fact_trades.

口径来源: docs/WEATHER_ANALYSIS_CONTRACT.md
- 已成交 PnL 唯一源 = fact_trades.pnl_usd_at_fill (settled only)
- 机会粒度 = fact_signal_candidates.counterfactual_pnl (eligible+final+decision_window)

研究当前 0.25-0.75 入场带是否合理, 是否需要按 side / 价位重组。
"""
import sqlite3
import sys

DB = "runtime/weather.db"

EDGES = [0.10, 0.25, 0.40, 0.55, 0.70, 0.85, 1.01]
NAMES = ["[00-10)", "[10-25)", "[25-40)", "[40-55)", "[55-70)", "[70-85)", "[85-100]"]


def bucket(p):
    if p is None:
        return "NA"
    for e, n in zip(EDGES, NAMES):
        if p < e:
            return n
    return "[85-100]"


def fmt_row(cols, widths):
    return "".join(str(c).rjust(w) for c, w in zip(cols, widths))


def agg_table(rows, keyfn, label):
    agg = {}
    for r in rows:
        k = keyfn(r)
        a = agg.setdefault(k, [0, 0.0, 0.0, 0])
        a[0] += 1
        a[1] += r["cost_usd"] or 0
        a[2] += r["pnl_usd_at_fill"] or 0
        a[3] += r["win_by_count"] or 0
    print(f"\n===== {label} =====")
    hdr = ["key", "n", "cost", "pnl", "roi", "wr"]
    w = [22, 5, 9, 9, 7, 6]
    print(fmt_row(hdr, w))
    for k in sorted(agg, key=lambda x: str(x)):
        n, cost, pnl, win = agg[k]
        roi = pnl / cost if cost else 0
        keystr = k if isinstance(k, str) else " ".join(str(x) for x in k)
        print(fmt_row([keystr, n, f"{cost:.1f}", f"{pnl:.1f}", f"{roi:.2f}", f"{win/n:.2f}"], w))


def main():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    for tc in ["live_real", "paper"]:
        rows = c.execute(
            "SELECT side, fill_price, cost_usd, pnl_usd_at_fill, win_by_count "
            "FROM fact_trades WHERE trade_class=? AND settlement_status='settled'",
            (tc,),
        ).fetchall()
        agg_table(rows, lambda r: bucket(r["fill_price"]), f"{tc}: price bucket")
        agg_table(rows, lambda r: (bucket(r["fill_price"]), r["side"]), f"{tc}: price bucket x side")

    # candidate opportunity universe: what alpha lives outside 0.25-0.75?
    print("\n\n##### fact_signal_candidates: decision-price band x side (counterfactual) #####")
    crows = c.execute(
        "SELECT side, decision_entry_price AS dp, counterfactual_pnl AS cf, win_by_count, paper_ordered, live_filled "
        "FROM fact_signal_candidates "
        "WHERE eligible=1 AND final_yes IS NOT NULL AND decision_window_missing=0"
    ).fetchall()
    cagg = {}
    for r in crows:
        k = (bucket(r["dp"]), r["side"])
        a = cagg.setdefault(k, [0, 0.0, 0, 0, 0])
        a[0] += 1
        a[1] += r["cf"] or 0
        a[2] += r["win_by_count"] or 0
        a[3] += r["live_filled"] or 0
        a[4] += r["paper_ordered"] or 0
    w = [22, 5, 10, 6, 8, 8]
    print(fmt_row(["key", "n", "cf_pnl", "wr", "live_fil", "paper_or"], w))
    for k in sorted(cagg, key=lambda x: str(x)):
        n, cf, win, lf, po = cagg[k]
        print(fmt_row([f"{k[0]} {k[1]}", n, f"{cf:.1f}", f"{win/n:.2f}", lf, po], w))

    # Drill the live vs paper divergence bucket: BUY_NO [55-70)
    for dim in ["target_date", "city"]:
        rows = c.execute(
            f"SELECT {dim} AS d, COUNT(*) n, SUM(cost_usd) cost, SUM(pnl_usd_at_fill) pnl, "
            "SUM(win_by_count) win FROM fact_trades "
            "WHERE trade_class='live_real' AND settlement_status='settled' "
            "AND side='BUY_NO' AND fill_price>=0.55 AND fill_price<0.70 "
            f"GROUP BY {dim} ORDER BY pnl ASC",
        ).fetchall()
        print(f"\n##### live_real BUY_NO [55-70) by {dim} #####")
        w = [16, 5, 9, 9, 7, 6]
        print(fmt_row(["d", "n", "cost", "pnl", "roi", "wr"], w))
        for r in rows:
            cost = r["cost"] or 0
            pnl = r["pnl"] or 0
            print(fmt_row([str(r["d"]), r["n"], f"{cost:.1f}", f"{pnl:.1f}",
                           f"{pnl/cost if cost else 0:.2f}", f"{(r['win'] or 0)/r['n']:.2f}"], w))


def bucket_label(p):
    return bucket(p)


# Proposed side x price-bucket weights (size multiplier vs current full size).
PROPOSED_WEIGHTS = {
    ("BUY_NO", "[25-40)"): 1.0,
    ("BUY_NO", "[40-55)"): 1.0,
    ("BUY_NO", "[55-70)"): 1.0,
    ("BUY_NO", "[70-85)"): 0.5,   # trim: structurally break-even
    ("BUY_NO", "[85-100]"): 0.0,  # exclude: negative cf
    ("BUY_NO", "[10-25)"): 0.0,
    ("BUY_NO", "[00-10)"): 0.0,
    ("BUY_YES", "[25-40)"): 0.5,  # small only
    ("BUY_YES", "[40-55)"): 0.0,
    ("BUY_YES", "[55-70)"): 0.0,
    ("BUY_YES", "[70-85)"): 0.0,
    ("BUY_YES", "[85-100]"): 0.0,
    ("BUY_YES", "[10-25)"): 0.0,
    ("BUY_YES", "[00-10)"): 0.0,  # lottery handled separately, excluded from core
}
CORE_KEYS = {("BUY_NO", "[40-55)"), ("BUY_NO", "[55-70)")}


def strategy_compare(c):
    print("\n\n##### STRATEGY COMPARE: current flat band vs proposed side x bucket #####")
    for tc in ["live_real", "paper"]:
        rows = c.execute(
            "SELECT side, fill_price, cost_usd, pnl_usd_at_fill, win_by_count "
            "FROM fact_trades WHERE trade_class=? AND settlement_status='settled'",
            (tc,),
        ).fetchall()
        base_cost = base_pnl = 0.0
        prop_cost = prop_pnl = 0.0
        freed_cost = 0.0
        core_cost = core_pnl = 0.0
        for r in rows:
            k = (r["side"], bucket(r["fill_price"]))
            cost = r["cost_usd"] or 0
            pnl = r["pnl_usd_at_fill"] or 0
            base_cost += cost
            base_pnl += pnl
            w = PROPOSED_WEIGHTS.get(k, 1.0)
            prop_cost += w * cost
            prop_pnl += w * pnl
            freed_cost += (1.0 - w) * cost
            if k in CORE_KEYS:
                core_cost += cost
                core_pnl += pnl
        core_roi = core_pnl / core_cost if core_cost else 0
        # Variant B: redeploy freed capital into core at core ROI (constant notional).
        redeploy_pnl = prop_pnl + freed_cost * core_roi
        redeploy_cost = prop_cost + freed_cost
        print(f"\n--- {tc} ---")
        print(f"  current (all fills)     : cost={base_cost:8.1f}  pnl={base_pnl:8.1f}  roi={base_pnl/base_cost if base_cost else 0:+.3f}")
        print(f"  proposed (filter/trim)  : cost={prop_cost:8.1f}  pnl={prop_pnl:8.1f}  roi={prop_pnl/prop_cost if prop_cost else 0:+.3f}  (deploys {prop_cost/base_cost*100:.0f}% of capital)")
        print(f"  proposed + redeploy core: cost={redeploy_cost:8.1f}  pnl={redeploy_pnl:8.1f}  roi={redeploy_pnl/redeploy_cost if redeploy_cost else 0:+.3f}  (core_roi={core_roi:+.3f}, freed={freed_cost:.1f})")


if __name__ == "__main__":
    _c = sqlite3.connect(DB)
    _c.row_factory = sqlite3.Row
    main()
    strategy_compare(_c)
    sys.exit(0)
