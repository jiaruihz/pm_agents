#!/usr/bin/env python3
"""Sizing scheme + entry-band sweep study (anti-overfit).

研究两件事, 都看分布不只总额:
  A. 把固定 $5 换成「按 side x 价位桶」的差异化 sizing, live 历史回测里收益怎么分布。
  B. 换不同入场区间 (L,U), 收益怎么变化/分布。

反过拟合纪律:
  - sizing 权重结构只从 paper (相对 live 是样本外: 时间窗/池都不重叠) 推导,
    再套到 live 上做样本外检验。绝不用 live 自己的盈亏反推权重。
  - 所有 sizing 方案归一到「与 flat $5 相同的总投入资金」, 只比较「配置形状」不比较杠杆。
  - 入场区间用整片网格扫描, 报告整个响应面: 宽平台=稳健, 尖峰=过拟合。
  - 日级 bootstrap 给 ROI 置信区间, 不只报点估计。

口径: docs/WEATHER_ANALYSIS_CONTRACT.md (PnL 唯一源 fact_trades.pnl_usd_at_fill, settled only)
"""
import sqlite3
import random
import math

DB = "runtime/weather.db"
random.seed(20260530)

EDGES = [0.10, 0.25, 0.40, 0.55, 0.70, 0.85, 1.01]
NAMES = ["[00-10)", "[10-25)", "[25-40)", "[40-55)", "[55-70)", "[70-85)", "[85-100]"]
MIDS = {"[00-10)": 0.05, "[10-25)": 0.175, "[25-40)": 0.325, "[40-55)": 0.475,
        "[55-70)": 0.625, "[70-85)": 0.775, "[85-100]": 0.925}


def bucket(p):
    if p is None:
        return "NA"
    for e, n in zip(EDGES, NAMES):
        if p < e:
            return n
    return "[85-100]"


def load(c, tc):
    return c.execute(
        "SELECT side, fill_price, cost_usd, pnl_usd_at_fill, win_by_count, target_date "
        "FROM fact_trades WHERE trade_class=? AND settlement_status='settled' "
        "AND cost_usd IS NOT NULL AND cost_usd>0",
        (tc,),
    ).fetchall()


# ---------- derive sizing weights from PAPER (out-of-sample vs live) ----------
def paper_bucket_stats(paper_rows):
    """Return {(side,bucket): (roi, win_rate, n)} from paper."""
    agg = {}
    for r in paper_rows:
        k = (r["side"], bucket(r["fill_price"]))
        a = agg.setdefault(k, [0.0, 0.0, 0])
        a[0] += r["cost_usd"]
        a[1] += r["pnl_usd_at_fill"]
        a[2] += 1
    out = {}
    win = {}
    for r in paper_rows:
        k = (r["side"], bucket(r["fill_price"]))
        win.setdefault(k, [0, 0])
        win[k][0] += r["win_by_count"] or 0
        win[k][1] += 1
    for k, (cost, pnl, n) in agg.items():
        out[k] = (pnl / cost if cost else 0.0, win[k][0] / win[k][1], n)
    return out


def w_ev_tier(key, pstats):
    """3-tier weight from paper ROI. Coarse on purpose (anti-overfit)."""
    st = pstats.get(key)
    if not st or st[2] < 8:  # too few paper samples -> neutral 0 to be safe
        return 0.0
    roi = st[0]
    if roi >= 0.20:
        return 2.0
    if roi >= 0.05:
        return 1.0
    return 0.0


def w_quarter_kelly(key, pstats, frac=0.25):
    """Quarter-Kelly weight from paper win rate at bucket mid price."""
    st = pstats.get(key)
    if not st or st[2] < 8:
        return 0.0
    p = st[1]
    c = MIDS.get(key[1], 0.5)
    if c <= 0 or c >= 1:
        return 0.0
    f = p - (1 - p) * c / (1 - c)  # full Kelly fraction of bankroll
    return max(0.0, f * frac)


SCHEMES = {
    "S0_flat": lambda key, ps: 1.0,
    "S1_ev_tier": lambda key, ps: w_ev_tier(key, ps),
    "S2_qkelly": lambda key, ps: w_quarter_kelly(key, ps),
}


# ---------- distribution stats on a daily PnL series ----------
def daily_series(rows, dollar_fn):
    """dollar_fn(row)->allocated $. Returns {date: pnl} with pnl scaled to allocation."""
    by_day = {}
    for r in rows:
        d = dollar_fn(r)
        if d <= 0:
            continue
        roi_i = r["pnl_usd_at_fill"] / r["cost_usd"]
        by_day[r["target_date"]] = by_day.get(r["target_date"], 0.0) + d * roi_i
    return by_day


def pct(xs, q):
    if not xs:
        return 0.0
    s = sorted(xs)
    i = q * (len(s) - 1)
    lo = int(math.floor(i))
    hi = int(math.ceil(i))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def max_drawdown(daily_sorted):
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for _, v in daily_sorted:
        cum += v
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return mdd


def dist_stats(by_day, total_capital):
    days = sorted(by_day.items())
    vals = [v for _, v in days]
    n = len(vals)
    total = sum(vals)
    mean = total / n if n else 0
    var = sum((v - mean) ** 2 for v in vals) / (n - 1) if n > 1 else 0
    std = math.sqrt(var)
    sharpe = mean / std if std else 0
    pos = sum(1 for v in vals if v > 0) / n if n else 0
    return {
        "days": n,
        "total_pnl": total,
        "roi_on_capital": total / total_capital if total_capital else 0,
        "mean_day": mean,
        "std_day": std,
        "sharpe_like": sharpe,
        "pos_day_rate": pos,
        "worst_day": min(vals) if vals else 0,
        "best_day": max(vals) if vals else 0,
        "p10": pct(vals, 0.10),
        "p50": pct(vals, 0.50),
        "p90": pct(vals, 0.90),
        "max_drawdown": max_drawdown(days),
    }


def bootstrap_roi_ci(by_day, total_capital, iters=2000):
    days = list(by_day.values())
    if len(days) < 3:
        return (0, 0)
    rois = []
    n = len(days)
    for _ in range(iters):
        s = sum(random.choice(days) for _ in range(n))
        rois.append(s / total_capital if total_capital else 0)
    rois.sort()
    return (pct(rois, 0.05), pct(rois, 0.95))


def run_sizing_study(c):
    paper = load(c, "paper")
    pstats = paper_bucket_stats(paper)
    print("\n" + "=" * 78)
    print("PART A: SIZING SCHEMES on live_real (weights derived from PAPER, OOS)")
    print("=" * 78)
    print("All schemes normalized to SAME total capital as flat $5 (compare shape, not leverage)")

    for eval_tc in ["live_real", "paper"]:
        rows = load(c, eval_tc)
        base_total = 5.0 * len(rows)
        print(f"\n----- evaluated on {eval_tc} (n_fills={len(rows)}, total_capital=${base_total:.0f}) -----")
        hdr = ["scheme", "days", "tot_pnl", "ROI", "sharpe", "pos%", "worst", "best", "p10", "p90", "maxDD", "ROI 90%CI"]
        w = [12, 5, 9, 8, 7, 6, 8, 8, 8, 8, 8, 18]
        print("".join(str(h).rjust(x) for h, x in zip(hdr, w)))
        for sname, wfn in SCHEMES.items():
            # raw weight per fill
            raw = [max(0.0, wfn((r["side"], bucket(r["fill_price"])), pstats)) for r in rows]
            sw = sum(raw)
            if sw <= 0:
                continue

            def dollar_fn(r, _raw=raw, _rows=rows, _sw=sw, _bt=base_total):
                idx = _rows.index(r)
                return _bt * _raw[idx] / _sw

            # build daily via index alignment (avoid .index cost)
            by_day = {}
            for r, rw in zip(rows, raw):
                if rw <= 0:
                    continue
                d = base_total * rw / sw
                by_day[r["target_date"]] = by_day.get(r["target_date"], 0.0) + d * (r["pnl_usd_at_fill"] / r["cost_usd"])
            s = dist_stats(by_day, base_total)
            ci = bootstrap_roi_ci(by_day, base_total)
            vals = [
                sname, s["days"], f"{s['total_pnl']:.1f}", f"{s['roi_on_capital']:+.3f}",
                f"{s['sharpe_like']:+.2f}", f"{s['pos_day_rate']*100:.0f}", f"{s['worst_day']:.1f}",
                f"{s['best_day']:.1f}", f"{s['p10']:.1f}", f"{s['p90']:.1f}", f"{s['max_drawdown']:.1f}",
                f"[{ci[0]:+.3f},{ci[1]:+.3f}]",
            ]
            print("".join(str(v).rjust(x) for v, x in zip(vals, w)))


def run_band_sweep(c):
    print("\n\n" + "=" * 78)
    print("PART B: ENTRY-BAND SWEEP (flat $5, look for plateau vs peak)")
    print("=" * 78)
    Ls = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45]
    Us = [0.60, 0.65, 0.70, 0.75, 0.80, 0.85]
    for eval_tc in ["live_real", "paper"]:
        for side_filter in ["ALL", "BUY_NO"]:
            rows = load(c, eval_tc)
            if side_filter != "ALL":
                rows = [r for r in rows if r["side"] == side_filter]
            print(f"\n----- {eval_tc} / side={side_filter} : ROI grid (n in parens) -----")
            print("       " + "".join(f"U={u:.2f}".rjust(13) for u in Us))
            for L in Ls:
                cells = []
                for U in Us:
                    sub = [r for r in rows if L <= r["fill_price"] < U]
                    cost = sum(r["cost_usd"] for r in sub)
                    pnl = sum(r["pnl_usd_at_fill"] for r in sub)
                    roi = pnl / cost if cost else 0
                    cells.append(f"{roi:+.2f}({len(sub)})".rjust(13))
                print(f"L={L:.2f} " + "".join(cells))


def run_candidate_band_sweep(c):
    """Un-biased band study on the OPPORTUNITY universe (fact_signal_candidates).

    band 过滤器真正看到的是决策窗价 decision_entry_price, 不是成交价。
    fact_trades 的 band 扫描受「成交选择」偏差污染; 这里用全机会集修正。
    契约: counterfactual_pnl 已按授权公式算好(×paper 决策 shares), 但无授权 cost 列,
          故只报 win_by_count(size-free) + cf_pnl 总额/均额, 不自算 ROI。
    可用分母仅 eligible=1 AND final_yes IS NOT NULL AND decision_window_missing=0。
    """
    print("\n\n" + "=" * 78)
    print("PART C: ENTRY-BAND SWEEP on OPPORTUNITY UNIVERSE (fact_signal_candidates)")
    print("=" * 78)
    tot = c.execute("SELECT COUNT(*) n, SUM(decision_window_missing) dwm FROM fact_signal_candidates").fetchone()
    print(f"candidates total={tot['n']}, decision_window_missing={tot['dwm']} ({100*tot['dwm']/tot['n']:.1f}%)")
    rows = c.execute(
        "SELECT side, decision_entry_price AS dp, counterfactual_pnl AS cf, win_by_count, "
        "eligible, paper_ordered, live_filled "
        "FROM fact_signal_candidates "
        "WHERE eligible=1 AND final_yes IS NOT NULL AND decision_window_missing=0 "
        "AND decision_entry_price IS NOT NULL"
    ).fetchall()
    print(f"usable counterfactual denom (eligible+final+window): {len(rows)}")

    Ls = [0.20, 0.25, 0.30, 0.35, 0.40]
    Us = [0.60, 0.65, 0.70, 0.75, 0.85]
    for side_filter in ["ALL", "BUY_NO", "BUY_YES"]:
        sub_all = rows if side_filter == "ALL" else [r for r in rows if r["side"] == side_filter]
        print(f"\n----- side={side_filter} : mean cf_pnl/opp [win_rate] (n) -----")
        print("       " + "".join(f"U={u:.2f}".rjust(20) for u in Us))
        for L in Ls:
            cells = []
            for U in Us:
                s = [r for r in sub_all if L <= r["dp"] < U]
                n = len(s)
                if n == 0:
                    cells.append("-".rjust(20))
                    continue
                mean_cf = sum(r["cf"] or 0 for r in s) / n
                wr = sum(r["win_by_count"] or 0 for r in s) / n
                cells.append(f"{mean_cf:+.2f}[{wr:.2f}]({n})".rjust(20))
            print(f"L={L:.2f} " + "".join(cells))


def run_yes_research(c):
    """BUY_YES re-research: is [0.25-0.30) a real band or a bucket-boundary artifact?

    用户质疑窄价带过拟合。三视角:
      1. 滑窗连续曲线(不靠桶边界): paper BUY_YES, 窗宽 ±0.06, 看是否在 0.25 有断点。
      2. edge 单调性: abs_edge 是否比 price 更干净地解释 YES alpha。
      3. price × edge 二维: 价位带是否只是 edge 的代理。
    口径: paper(大样本结构), PnL=pnl_usd_at_fill, settled。
    """
    print("\n\n" + "=" * 78)
    print("PART D: BUY_YES re-research (sliding window + edge driver + 2D)")
    print("=" * 78)
    rows = c.execute(
        "SELECT fill_price AS p, abs_edge AS e, cost_usd AS cost, pnl_usd_at_fill AS pnl, win_by_count AS win "
        "FROM fact_trades WHERE trade_class='paper' AND settlement_status='settled' "
        "AND side='BUY_YES' AND cost_usd IS NOT NULL AND cost_usd>0"
    ).fetchall()
    print(f"paper BUY_YES settled n={len(rows)}")

    # 1) sliding window over price (half-width 0.06), step 0.025
    print("\n[1] sliding window over price (half-width 0.06): is 0.25 a real break?")
    print("  center   n    cost     pnl     roi    win")
    cen = 0.05
    while cen <= 0.55:
        sub = [r for r in rows if cen - 0.06 <= r["p"] < cen + 0.06]
        if sub:
            cost = sum(r["cost"] for r in sub)
            pnl = sum(r["pnl"] for r in sub)
            win = sum(r["win"] or 0 for r in sub)
            print(f"  {cen:.3f}  {len(sub):4d} {cost:7.1f} {pnl:7.1f}  {pnl/cost if cost else 0:+.2f}  {win/len(sub):.2f}")
        cen += 0.025

    # 2) edge monotonicity (size-free-ish: report roi by abs_edge band)
    print("\n[2] abs_edge monotonicity (BUY_YES): does edge explain alpha cleaner than price?")
    edge_bands = [(0.0, 0.10), (0.10, 0.20), (0.20, 0.30), (0.30, 0.45), (0.45, 2.0)]
    print("  edge band       n    cost     pnl     roi    win")
    for lo, hi in edge_bands:
        sub = [r for r in rows if r["e"] is not None and lo <= r["e"] < hi]
        if not sub:
            continue
        cost = sum(r["cost"] for r in sub)
        pnl = sum(r["pnl"] for r in sub)
        win = sum(r["win"] or 0 for r in sub)
        print(f"  [{lo:.2f},{hi:.2f})  {len(sub):4d} {cost:7.1f} {pnl:7.1f}  {pnl/cost if cost else 0:+.2f}  {win/len(sub):.2f}")

    # 3) price x edge 2D
    print("\n[3] price x abs_edge 2D ROI (n): is the price band just an edge proxy?")
    price_bands = [(0.05, 0.20), (0.20, 0.25), (0.25, 0.40), (0.40, 0.55)]
    e_cols = [(0.10, 0.20), (0.20, 0.30), (0.30, 0.45), (0.45, 2.0)]
    print("price\\edge    " + "".join(f"[{a:.2f},{b:.2f})".rjust(15) for a, b in e_cols))
    for pl, ph in price_bands:
        cells = []
        for el, eh in e_cols:
            sub = [r for r in rows if pl <= r["p"] < ph and r["e"] is not None and el <= r["e"] < eh]
            if not sub:
                cells.append("-".rjust(15))
                continue
            cost = sum(r["cost"] for r in sub)
            pnl = sum(r["pnl"] for r in sub)
            cells.append(f"{pnl/cost if cost else 0:+.2f}({len(sub)})".rjust(15))
        print(f"[{pl:.2f},{ph:.2f})  " + "".join(cells))


def main():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    run_sizing_study(c)
    run_band_sweep(c)
    run_candidate_band_sweep(c)
    run_yes_research(c)


if __name__ == "__main__":
    main()
