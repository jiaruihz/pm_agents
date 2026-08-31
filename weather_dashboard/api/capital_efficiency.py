"""Capital-efficiency calculations for public Polymarket portfolios.

The module is deliberately independent from FastAPI and network access so the
money and date arithmetic can be regression-tested with fixed snapshots.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional


ACTIVE_VALUE_FLOOR_USD = 0.01


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _parse_end_date(raw: Any) -> Optional[date]:
    if not raw:
        return None
    text = str(raw).strip()
    try:
        if len(text) == 10:
            return date.fromisoformat(text)
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _book_index(books: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(book.get("asset_id")): book
        for book in books
        if isinstance(book, dict) and book.get("asset_id") is not None
    }


def _visible_exit(book: Optional[dict[str, Any]], size: float) -> dict[str, Optional[float]]:
    """Walk visible bids and estimate proceeds for selling ``size`` shares."""
    if not book or size <= 0:
        return {
            "best_bid": None,
            "best_ask": None,
            "visible_exit_value": None,
            "visible_exit_vwap": None,
            "exit_coverage_ratio": 0.0,
        }

    bids = sorted(
        (
            (_number(level.get("price")), _number(level.get("size")))
            for level in (book.get("bids") or [])
            if isinstance(level, dict)
        ),
        reverse=True,
    )
    asks = sorted(
        (
            (_number(level.get("price")), _number(level.get("size")))
            for level in (book.get("asks") or [])
            if isinstance(level, dict)
        )
    )
    bids = [(price, qty) for price, qty in bids if price > 0 and qty > 0]
    asks = [(price, qty) for price, qty in asks if price > 0 and qty > 0]

    remaining = size
    proceeds = 0.0
    covered = 0.0
    for price, quantity in bids:
        fill = min(remaining, quantity)
        proceeds += fill * price
        covered += fill
        remaining -= fill
        if remaining <= 1e-9:
            break

    return {
        "best_bid": bids[0][0] if bids else None,
        "best_ask": asks[0][0] if asks else None,
        "visible_exit_value": proceeds if covered > 0 else None,
        "visible_exit_vwap": proceeds / covered if covered > 0 else None,
        "exit_coverage_ratio": min(covered / size, 1.0),
    }


def _maturity_bucket(days: Optional[int], overdue: bool) -> str:
    if overdue:
        return "overdue"
    if days is None:
        return "unknown"
    if days <= 7:
        return "0-7d"
    if days <= 30:
        return "8-30d"
    if days <= 90:
        return "31-90d"
    if days <= 180:
        return "91-180d"
    return "181d+"


def build_capital_efficiency_report(
    *,
    wallet_address: str,
    positions: Iterable[dict[str, Any]],
    books: Iterable[dict[str, Any]] = (),
    observed_at: Optional[datetime] = None,
    annual_hurdle_rate: float = 0.10,
    available_cash_usd: Optional[float] = None,
    reserved_cash_usd: Optional[float] = None,
    profile: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build a mark-to-market, exit-liquidity and maturity report.

    ``endDate`` is treated as an estimated capital-release date. Polymarket can
    settle later, so overdue positions stay committed until they are redeemable.
    """
    now = observed_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    today = now.date()
    hurdle = max(_number(annual_hurdle_rate), 0.0)
    books_by_asset = _book_index(books)

    active_rows: list[dict[str, Any]] = []
    non_active_rows: list[dict[str, Any]] = []
    schedule_acc: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    bucket_acc: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for raw in positions:
        if not isinstance(raw, dict):
            continue
        size = max(_number(raw.get("size")), 0.0)
        avg_price = max(_number(raw.get("avgPrice")), 0.0)
        mark_price = max(_number(raw.get("curPrice")), 0.0)
        mark_value = max(_number(raw.get("currentValue"), size * mark_price), 0.0)
        initial_value = max(_number(raw.get("initialValue"), size * avg_price), 0.0)
        gross_initial_value = max(
            _number(raw.get("grossInitialValue"), initial_value + _number(raw.get("entryFeesUsdc"))),
            0.0,
        )
        entry_fees = max(_number(raw.get("entryFeesUsdc")), 0.0)
        redeemable = bool(raw.get("redeemable"))
        end = _parse_end_date(raw.get("endDate"))
        overdue = bool(end and end < today and not redeemable)
        calendar_days = (end - today).days if end else None
        if calendar_days is not None and calendar_days >= 0:
            days_to_end: Optional[int] = max(calendar_days, 1)
        elif overdue:
            days_to_end = 0
        else:
            days_to_end = None

        is_active = not redeemable and mark_value > ACTIVE_VALUE_FLOOR_USD
        if redeemable:
            status = "redeemable"
        elif overdue:
            status = "pending_resolution"
        elif mark_value <= ACTIVE_VALUE_FLOOR_USD:
            status = "dust"
        elif end is None:
            status = "unknown_end"
        else:
            status = "active"

        exit_quote = _visible_exit(books_by_asset.get(str(raw.get("asset"))), size)
        gross_payout = size
        remaining_upside = max(gross_payout - mark_value, 0.0)
        gross_return_if_win = remaining_upside / mark_value if mark_value > 0 else None
        annualized_simple = (
            gross_return_if_win * 365.0 / days_to_end
            if gross_return_if_win is not None and days_to_end and not overdue
            else None
        )
        required_confidence = (
            mark_price * (1.0 + hurdle * days_to_end / 365.0)
            if mark_price > 0 and days_to_end and not overdue
            else None
        )
        full_exit_value = (
            exit_quote["visible_exit_value"]
            if _number(exit_quote["exit_coverage_ratio"]) >= 0.999
            else None
        )
        exit_slippage = (
            mark_value - _number(full_exit_value)
            if full_exit_value is not None
            else None
        )

        row = {
            "condition_id": raw.get("conditionId"),
            "token_id": raw.get("asset"),
            "title": raw.get("title"),
            "slug": raw.get("slug"),
            "event_slug": raw.get("eventSlug"),
            "outcome": raw.get("outcome"),
            "size": size,
            "avg_price": avg_price,
            "mark_price": mark_price,
            "mark_value_usd": mark_value,
            "cost_basis_usd": gross_initial_value,
            "entry_fees_usd": entry_fees,
            "unrealized_pnl_usd": mark_value - gross_initial_value,
            "redeemable": redeemable,
            "end_date": end.isoformat() if end else None,
            "days_to_end": days_to_end,
            "status": status,
            "maturity_bucket": _maturity_bucket(days_to_end, overdue),
            "gross_payout_if_win_usd": gross_payout,
            "remaining_upside_if_win_usd": remaining_upside,
            "gross_return_if_win": gross_return_if_win,
            "annualized_simple_return_if_win": annualized_simple,
            "required_confidence_for_hurdle": required_confidence,
            "hurdle_feasible": required_confidence <= 1.0 if required_confidence is not None else None,
            **exit_quote,
            "full_exit_value_usd": full_exit_value,
            "exit_slippage_vs_mark_usd": exit_slippage,
        }

        if is_active:
            active_rows.append(row)
            schedule_key = row["end_date"] or "unknown"
            schedule_acc[schedule_key]["positions_count"] += 1
            schedule_acc[schedule_key]["mark_value_usd"] += mark_value
            schedule_acc[schedule_key]["gross_payout_if_all_win_usd"] += gross_payout
            schedule_acc[schedule_key]["remaining_upside_if_all_win_usd"] += remaining_upside
            bucket = row["maturity_bucket"]
            bucket_acc[bucket]["positions_count"] += 1
            bucket_acc[bucket]["mark_value_usd"] += mark_value
            bucket_acc[bucket]["remaining_upside_if_all_win_usd"] += remaining_upside
        else:
            non_active_rows.append(row)

    active_rows.sort(
        key=lambda row: (
            row["end_date"] is None,
            row["end_date"] or "9999-12-31",
            -_number(row["mark_value_usd"]),
        )
    )
    non_active_rows.sort(key=lambda row: -_number(row["mark_value_usd"]))

    active_value = sum(_number(row["mark_value_usd"]) for row in active_rows)
    active_cost = sum(_number(row["cost_basis_usd"]) for row in active_rows)
    redeemable_value = sum(
        _number(row["mark_value_usd"]) for row in non_active_rows if row["status"] == "redeemable"
    )
    payout = sum(_number(row["gross_payout_if_win_usd"]) for row in active_rows)
    upside = sum(_number(row["remaining_upside_if_win_usd"]) for row in active_rows)
    dated_rows = [row for row in active_rows if _number(row.get("days_to_end")) > 0]
    dated_value = sum(_number(row["mark_value_usd"]) for row in dated_rows)
    capital_days = sum(
        _number(row["mark_value_usd"]) * _number(row["days_to_end"])
        for row in dated_rows
    )
    dated_upside = sum(_number(row["remaining_upside_if_win_usd"]) for row in dated_rows)
    below_hurdle_rows = [
        row
        for row in active_rows
        if row["annualized_simple_return_if_win"] is not None
        and _number(row["annualized_simple_return_if_win"]) < hurdle
    ]
    below_hurdle_value = sum(_number(row["mark_value_usd"]) for row in below_hurdle_rows)

    covered_mark_value = 0.0
    visible_exit_value = 0.0
    fully_quoted_mark_value = 0.0
    full_exit_value = 0.0
    for row in active_rows:
        coverage = _number(row["exit_coverage_ratio"])
        covered_mark_value += _number(row["mark_value_usd"]) * coverage
        if row["visible_exit_value"] is not None:
            visible_exit_value += _number(row["visible_exit_value"])
        if row["full_exit_value_usd"] is not None:
            fully_quoted_mark_value += _number(row["mark_value_usd"])
            full_exit_value += _number(row["full_exit_value_usd"])

    cash_complete = available_cash_usd is not None and reserved_cash_usd is not None
    available_cash = max(_number(available_cash_usd), 0.0) if available_cash_usd is not None else None
    reserved_cash = max(_number(reserved_cash_usd), 0.0) if reserved_cash_usd is not None else None
    accounted_capital = None
    utilization = None
    if cash_complete:
        accounted_capital = active_value + redeemable_value + _number(available_cash) + _number(reserved_cash)
        utilized_capital = active_value + _number(reserved_cash)
        utilization = utilized_capital / accounted_capital if accounted_capital > 0 else 0.0

    schedule = []
    for end_date, values in sorted(schedule_acc.items(), key=lambda item: (item[0] == "unknown", item[0])):
        value = values["mark_value_usd"]
        date_value = _parse_end_date(end_date)
        raw_days = (date_value - today).days if date_value else None
        days = max(raw_days, 1) if raw_days is not None and raw_days >= 0 else None
        simple_return = values["remaining_upside_if_all_win_usd"] / value if value > 0 else None
        schedule.append(
            {
                "end_date": None if end_date == "unknown" else end_date,
                "days_to_end": days,
                **dict(values),
                "portfolio_share": value / active_value if active_value > 0 else 0.0,
                "gross_return_if_all_win": simple_return,
                "annualized_simple_return_if_all_win": (
                    simple_return * 365.0 / days if simple_return is not None and days else None
                ),
            }
        )

    bucket_order = ["overdue", "0-7d", "8-30d", "31-90d", "91-180d", "181d+", "unknown"]
    maturity_buckets = [
        {
            "bucket": bucket,
            **dict(bucket_acc[bucket]),
            "portfolio_share": bucket_acc[bucket]["mark_value_usd"] / active_value if active_value > 0 else 0.0,
        }
        for bucket in bucket_order
        if bucket in bucket_acc
    ]

    profile = profile or {}
    display_name = profile.get("name") or profile.get("pseudonym") or ""
    return {
        "wallet_address": wallet_address.lower(),
        "display_name": display_name,
        "observed_at_utc": now.isoformat().replace("+00:00", "Z"),
        "annual_hurdle_rate": hurdle,
        "cash_inputs_complete": cash_complete,
        "inputs": {
            "available_cash_usd": available_cash,
            "reserved_cash_usd": reserved_cash,
        },
        "summary": {
            "active_positions_count": len(active_rows),
            "active_mark_value_usd": active_value,
            "active_cost_basis_usd": active_cost,
            "unrealized_pnl_vs_cost_usd": active_value - active_cost,
            "redeemable_value_usd": redeemable_value,
            "gross_payout_if_all_win_usd": payout,
            "remaining_upside_if_all_win_usd": upside,
            "weighted_avg_days_to_end": capital_days / dated_value if dated_value > 0 else None,
            "capital_days_usd": capital_days,
            "portfolio_simple_annualized_return_if_all_win": (
                dated_upside * 365.0 / capital_days if capital_days > 0 else None
            ),
            "below_hurdle_positions_count": len(below_hurdle_rows),
            "below_hurdle_mark_value_usd": below_hurdle_value,
            "below_hurdle_portfolio_share": below_hurdle_value / active_value if active_value > 0 else 0.0,
            "visible_exit_value_usd": visible_exit_value,
            "book_value_coverage_ratio": covered_mark_value / active_value if active_value > 0 else 0.0,
            "fully_quoted_mark_value_usd": fully_quoted_mark_value,
            "full_exit_value_usd": full_exit_value,
            "full_exit_slippage_usd": fully_quoted_mark_value - full_exit_value,
            "accounted_capital_usd": accounted_capital,
            "capital_utilization": utilization,
        },
        "maturity_schedule": schedule,
        "maturity_buckets": maturity_buckets,
        "positions": active_rows,
        "non_active_positions": non_active_rows,
        "data_quality": {
            "positions_received": len(active_rows) + len(non_active_rows),
            "positions_with_books": sum(1 for row in active_rows if row["visible_exit_value"] is not None),
            "unknown_end_date_count": sum(1 for row in active_rows if row["end_date"] is None),
            "pending_resolution_count": sum(1 for row in active_rows if row["status"] == "pending_resolution"),
            "cash_and_open_orders_publicly_complete": False,
            "end_date_is_release_estimate": True,
        },
        "methodology": {
            "mark_value": "Data API currentValue/curPrice snapshot",
            "exit_value": "visible CLOB bids walked to the held share size; no fee or price-impact beyond visible depth",
            "annualized_return": "simple annualization of remaining return if the held outcome wins",
            "required_confidence": "mark_price * (1 + annual_hurdle_rate * days_to_end / 365)",
        },
    }
