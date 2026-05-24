"""
calc.py

Compute PnL and risk metrics for a given run_id.

Metrics returned:
  num_trades        - filled/simulated trades
  total_pnl_usd     - sum of settled trade PnL
  win_rate          - fraction of settled trades with pnl > 0
  avg_pnl_usd       - mean settled trade PnL
  median_pnl_usd    - median settled trade PnL
  pnl_trimmed_1pct  - mean PnL excluding top/bottom 1% (concentration defense)
  top1_pnl_share    - top-1 trade PnL / |total_pnl| (lottery-zone flag)
  top5_pnl_share    - top-5 trades PnL / |total_pnl|
  total_cost_usd    - total capital deployed across all fills
  roi               - total_pnl / total_cost (None if cost=0)
  settled_trades    - trades with known settlement
  unsettled_trades  - trades without settlement yet
  settled_ratio     - settled_trades / num_trades
  worst_loss_usd    - single worst trade PnL
  best_win_usd      - single best trade PnL
  expectancy_usd    - avg_win * win_rate - avg_loss * loss_rate (per-trade EV)
  max_drawdown_usd  - max peak-to-trough cumulative PnL drop (sorted by entry)
  fees_paid_usd     - total fees across all fills
  loss_rate         - fraction of settled trades with pnl < 0
  avg_win_usd       - mean PnL of winning trades
  avg_loss_usd      - mean PnL of losing trades (negative value)
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


def _has_column(conn, table_name: str, column_name: str) -> bool:
    return any(
        (row["name"] if hasattr(row, "keys") else row[1]) == column_name
        for row in conn.execute(f"PRAGMA table_info({table_name})")
    )


def _trade_pnl(filled_shares: str, filled_price: str, fees_usd: str,
               final_price=None, order_side: str = "", **legacy_kwargs) -> Optional[Decimal]:
    """
    Compute PnL for one trade given settlement.
    Polymarket contracts pay $1 for YES win, $0 for NO win.

    order_side: 'BUY_YES' or 'BUY_NO'
    final_price: YES token final price, usually 0.0 or 1.0
    """
    if final_price is None and "final_yes" in legacy_kwargs:
        final_price = legacy_kwargs["final_yes"]

    resolved = _d(final_price)
    if resolved is None:
        return None

    shares = _d(filled_shares)
    price = _d(filled_price)
    fees = _d(fees_usd) or Decimal("0")

    if shares is None or price is None:
        return None

    cost = shares * price
    if order_side == "BUY_YES":
        payout = shares * resolved
    else:  # BUY_NO
        payout = shares * (Decimal("1") - resolved)

    return payout - cost - fees


def _max_drawdown(pnls: list[float]) -> float:
    """Max peak-to-trough drop in cumulative PnL (trade order = insertion order)."""
    if not pnls:
        return 0.0
    peak = 0.0
    cum = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return max_dd


_SETTLEMENTS_DEDUP_SQL = """
    (
        SELECT target_date, condition_id, bracket, MAX(final_price) AS final_price
        FROM settlements
        GROUP BY target_date, condition_id, bracket
    )
"""


def compute_metrics(conn, run_id: str) -> dict:
    """
    Query fills + orders + settlements for run_id and return metrics dict.
    """
    if _has_column(conn, "orders", "execution_id"):
        rows = conn.execute(
            f"""
            SELECT
                f.filled_shares,
                f.filled_price,
                f.fees_usd,
                o.order_side,
                o.cost_usd,
                s.final_price
            FROM fills f
            JOIN orders o ON f.execution_id = o.execution_id
            JOIN plans p  ON o.plan_id  = p.plan_id
            JOIN signals sig ON p.signal_id = sig.signal_id
            LEFT JOIN {_SETTLEMENTS_DEDUP_SQL} s
                   ON sig.target_date = s.target_date
                  AND sig.condition_id = s.condition_id
                  AND sig.bracket      = s.bracket
            WHERE o.run_id = ?
              AND f.status IN ('filled', 'simulated')
            """,
            (run_id,),
        ).fetchall()
    else:
        # Legacy v1 compatibility for old tests/manual forensics.
        rows = conn.execute(
            f"""
            SELECT
                f.filled_shares,
                f.filled_price,
                f.fees_usd,
                o.side AS order_side,
                o.cost_usd,
                s.final_yes AS final_price
            FROM fills f
            JOIN orders o ON f.order_id = o.order_id
            JOIN plans p  ON o.plan_id  = p.plan_id
            JOIN signals sig ON p.signal_id = sig.signal_id
            LEFT JOIN {_SETTLEMENTS_DEDUP_SQL} s
                   ON sig.target_date = s.target_date
                  AND sig.bracket      = s.bracket
            WHERE o.run_id = ?
              AND f.status IN ('filled', 'simulated')
            """,
            (run_id,),
        ).fetchall()

    _empty = {
        "num_trades": 0, "total_pnl_usd": None, "win_rate": None,
        "avg_pnl_usd": None, "median_pnl_usd": None,
        "pnl_trimmed_1pct": None, "top1_pnl_share": None,
        "top5_pnl_share": None, "total_cost_usd": None, "roi": None,
        "settled_trades": 0, "unsettled_trades": 0, "settled_ratio": None,
        "worst_loss_usd": None, "best_win_usd": None, "expectancy_usd": None,
        "max_drawdown_usd": None, "fees_paid_usd": None,
        "loss_rate": None, "avg_win_usd": None, "avg_loss_usd": None,
    }

    if not rows:
        return _empty

    pnls: list[float] = []
    costs: list[float] = []
    fees_total: float = 0.0
    settled = 0
    unsettled = 0

    for row in rows:
        pnl = _trade_pnl(
            row["filled_shares"], row["filled_price"], row["fees_usd"],
            row["final_price"], row["order_side"],
        )
        cost = _d(row["cost_usd"])
        if cost is not None:
            costs.append(float(cost))

        fee = _d(row["fees_usd"])
        if fee is not None:
            fees_total += float(fee)

        if pnl is not None:
            pnls.append(float(pnl))
            settled += 1
        else:
            unsettled += 1

    num_trades = len(rows)
    total_cost = sum(costs) if costs else None
    settled_ratio = round(settled / num_trades, 4) if num_trades else None

    if not pnls:
        return {
            **_empty,
            "num_trades": num_trades,
            "total_cost_usd": round(total_cost, 4) if total_cost else None,
            "settled_trades": settled,
            "unsettled_trades": unsettled,
            "settled_ratio": settled_ratio,
            "fees_paid_usd": round(fees_total, 4) if fees_total else None,
        }

    total_pnl = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / len(pnls)
    loss_rate = len(losses) / len(pnls)
    avg_pnl = mean(pnls)
    med_pnl = median(pnls)
    avg_win = mean(wins) if wins else None
    avg_loss = mean(losses) if losses else None

    # Expectancy: avg_win * P(win) + avg_loss * P(loss)
    expectancy = None
    if avg_win is not None and avg_loss is not None:
        expectancy = avg_win * win_rate + avg_loss * loss_rate
    elif avg_win is not None:
        expectancy = avg_win * win_rate
    elif avg_loss is not None:
        expectancy = avg_loss * loss_rate

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
    max_dd = _max_drawdown(pnls)

    def _r(v, n=4):
        return round(v, n) if v is not None else None

    return {
        "num_trades": num_trades,
        "total_pnl_usd": _r(total_pnl),
        "win_rate": _r(win_rate),
        "avg_pnl_usd": _r(avg_pnl),
        "median_pnl_usd": _r(med_pnl),
        "pnl_trimmed_1pct": _r(pnl_trimmed),
        "top1_pnl_share": _r(top1_share),
        "top5_pnl_share": _r(top5_share),
        "total_cost_usd": _r(total_cost),
        "roi": _r(roi),
        "settled_trades": settled,
        "unsettled_trades": unsettled,
        "settled_ratio": settled_ratio,
        "worst_loss_usd": _r(min(pnls)),
        "best_win_usd": _r(max(pnls)),
        "expectancy_usd": _r(expectancy),
        "max_drawdown_usd": _r(max_dd),
        "fees_paid_usd": _r(fees_total) if fees_total else None,
        "loss_rate": _r(loss_rate),
        "avg_win_usd": _r(avg_win),
        "avg_loss_usd": _r(avg_loss),
    }
