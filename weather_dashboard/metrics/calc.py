"""
calc.py

Compute PnL and risk metrics for a given run_id.

Metrics returned:
  num_trades       - filled trades
  total_pnl_usd    - sum of trade PnL
  win_rate         - fraction of trades with pnl > 0
  avg_pnl_usd      - mean trade PnL
  median_pnl_usd   - median trade PnL
  pnl_trimmed_1pct - mean PnL excluding top/bottom 1% (concentration defense)
  top1_pnl_share   - top-1 trade PnL / |total_pnl| (lottery-zone flag)
  top5_pnl_share   - top-5 trades PnL / |total_pnl|
  total_cost_usd   - total capital deployed
  roi              - total_pnl / total_cost (None if cost=0)
  settled_trades   - trades with known settlement
  unsettled_trades - trades without settlement yet
"""

from decimal import Decimal, InvalidOperation
from statistics import median, mean
from typing import Optional


def _d(val) -> Optional[Decimal]:
    """Safely parse a Decimal from TEXT or None."""
    if val is None or str(val).strip() == "":
        return None
    try:
        return Decimal(str(val))
    except InvalidOperation:
        return None


def _trade_pnl(filled_shares: str, filled_price: str, fees_usd: str,
               final_yes: Optional[int], order_side: str) -> Optional[Decimal]:
    """
    Compute PnL for one trade given settlement.
    Polymarket contracts pay $1 for YES win, $0 for NO win.

    order_side: 'BUY_YES' or 'BUY_NO'
    final_yes: 1 (YES resolves), 0 (NO resolves), None (unsettled)
    """
    if final_yes is None:
        return None

    shares = _d(filled_shares)
    price = _d(filled_price)
    fees = _d(fees_usd) or Decimal("0")

    if shares is None or price is None:
        return None

    cost = shares * price
    if order_side == "BUY_YES":
        payout = shares * Decimal("1") if final_yes == 1 else Decimal("0")
    else:  # BUY_NO
        payout = shares * Decimal("1") if final_yes == 0 else Decimal("0")

    return payout - cost - fees


def compute_metrics(conn, run_id: str) -> dict:
    """
    Query fills + orders + settlements for run_id and return metrics dict.
    """
    rows = conn.execute(
        """
        SELECT
            f.filled_shares,
            f.filled_price,
            f.fees_usd,
            o.side       AS order_side,
            o.cost_usd,
            s.final_yes
        FROM fills f
        JOIN orders o ON f.order_id = o.order_id
        JOIN plans p  ON o.plan_id  = p.plan_id
        JOIN signals sig ON p.signal_id = sig.signal_id
        LEFT JOIN settlements s
               ON sig.target_date = s.target_date
              AND sig.bracket      = s.bracket
        WHERE o.run_id = ?
          AND f.status = 'filled'
        """,
        (run_id,),
    ).fetchall()

    if not rows:
        return {
            "num_trades": 0, "total_pnl_usd": None, "win_rate": None,
            "avg_pnl_usd": None, "median_pnl_usd": None,
            "pnl_trimmed_1pct": None, "top1_pnl_share": None,
            "top5_pnl_share": None, "total_cost_usd": None, "roi": None,
            "settled_trades": 0, "unsettled_trades": 0,
        }

    pnls = []
    costs = []
    settled = 0
    unsettled = 0

    for row in rows:
        pnl = _trade_pnl(
            row["filled_shares"], row["filled_price"], row["fees_usd"],
            row["final_yes"], row["order_side"],
        )
        cost = _d(row["cost_usd"])
        if cost is not None:
            costs.append(float(cost))

        if pnl is not None:
            pnls.append(float(pnl))
            settled += 1
        else:
            unsettled += 1

    num_trades = len(rows)
    total_cost = sum(costs) if costs else None

    if not pnls:
        return {
            "num_trades": num_trades,
            "total_pnl_usd": None, "win_rate": None,
            "avg_pnl_usd": None, "median_pnl_usd": None,
            "pnl_trimmed_1pct": None, "top1_pnl_share": None,
            "top5_pnl_share": None,
            "total_cost_usd": round(total_cost, 4) if total_cost else None,
            "roi": None,
            "settled_trades": settled, "unsettled_trades": unsettled,
        }

    total_pnl = sum(pnls)
    win_rate = sum(1 for p in pnls if p > 0) / len(pnls)
    avg_pnl = mean(pnls)
    med_pnl = median(pnls)

    # Trimmed mean: exclude top/bottom 1%
    sorted_pnls = sorted(pnls)
    trim_n = max(1, int(len(sorted_pnls) * 0.01))
    trimmed = sorted_pnls[trim_n:-trim_n] if len(sorted_pnls) > 2 * trim_n else sorted_pnls
    pnl_trimmed = mean(trimmed) if trimmed else avg_pnl

    # Concentration metrics
    abs_total = abs(total_pnl) if total_pnl != 0 else None
    sorted_desc = sorted(pnls, reverse=True)
    top1_share = (sorted_desc[0] / abs_total) if abs_total else None
    top5_share = (sum(sorted_desc[:5]) / abs_total) if abs_total else None

    roi = (total_pnl / total_cost) if (total_cost and total_cost != 0) else None

    return {
        "num_trades": num_trades,
        "total_pnl_usd": round(total_pnl, 4),
        "win_rate": round(win_rate, 4),
        "avg_pnl_usd": round(avg_pnl, 4),
        "median_pnl_usd": round(med_pnl, 4),
        "pnl_trimmed_1pct": round(pnl_trimmed, 4),
        "top1_pnl_share": round(top1_share, 4) if top1_share is not None else None,
        "top5_pnl_share": round(top5_share, 4) if top5_share is not None else None,
        "total_cost_usd": round(total_cost, 4) if total_cost is not None else None,
        "roi": round(roi, 4) if roi is not None else None,
        "settled_trades": settled,
        "unsettled_trades": unsettled,
    }
