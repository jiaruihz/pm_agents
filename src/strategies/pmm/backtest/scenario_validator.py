from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _normalize_levels(levels: Any) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    if not isinstance(levels, list):
        return out
    for item in levels:
        if isinstance(item, dict):
            price = _to_float(item.get("price"))
            size = _to_float(item.get("size"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            price = _to_float(item[0])
            size = _to_float(item[1])
        else:
            continue
        out.append((price, size))
    return out


def _is_desc(prices: List[float]) -> bool:
    return all(prices[i] >= prices[i + 1] for i in range(len(prices) - 1))


def _is_asc(prices: List[float]) -> bool:
    return all(prices[i] <= prices[i + 1] for i in range(len(prices) - 1))


def validate_scenario_payload(payload: Dict[str, Any], strict: bool = False) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    stats: Dict[str, Any] = {
        "ticks": 0,
        "tokens": 0,
        "tokens_with_full_book_coverage": 0,
        "ticks_with_crossed_books": 0,
        "ticks_with_missing_trade_flow": 0,
        "ticks_with_non_monotonic_levels": 0,
        "ticks_with_non_positive_price_or_size": 0,
    }
    missing_trade_flow_ticks = 0
    bad_trade_flow_obj_ticks = 0
    missing_book_counts: Dict[str, int] = {}

    if not isinstance(payload, dict):
        errors.append("scenario payload is not an object")
        report = {"ok": False, "errors": errors, "warnings": warnings, "stats": stats}
        if strict:
            raise ValueError("; ".join(errors))
        return report

    scenario_id = payload.get("scenario_id")
    if not isinstance(scenario_id, str) or not scenario_id.strip():
        errors.append("scenario_id must be a non-empty string")

    token_ids = payload.get("token_ids")
    if not isinstance(token_ids, list) or len(token_ids) < 2:
        errors.append("token_ids must be a list with at least two ids")
        token_ids = []
    token_ids = [str(x).strip() for x in token_ids if str(x).strip()]
    stats["tokens"] = len(token_ids)

    ticks = payload.get("ticks")
    if not isinstance(ticks, list) or not ticks:
        errors.append("ticks must be a non-empty list")
        ticks = []
    stats["ticks"] = len(ticks)

    # Coverage counters by token.
    covered_tokens = {tid: 0 for tid in token_ids}

    for i, tick in enumerate(ticks):
        if not isinstance(tick, dict):
            errors.append(f"tick[{i}] is not an object")
            continue

        orderbooks = tick.get("orderbooks")
        if not isinstance(orderbooks, dict):
            errors.append(f"tick[{i}].orderbooks must be an object")
            continue

        trade_flow = tick.get("trade_flow")
        if not isinstance(trade_flow, dict):
            stats["ticks_with_missing_trade_flow"] += 1
            missing_trade_flow_ticks += 1
            trade_flow = {}

        has_crossed = False
        has_non_mono = False
        has_bad_level = False

        for tid in token_ids:
            ob = orderbooks.get(tid)
            if not isinstance(ob, dict):
                missing_book_counts[tid] = missing_book_counts.get(tid, 0) + 1
                continue
            covered_tokens[tid] += 1
            bids = _normalize_levels(ob.get("bids", []))
            asks = _normalize_levels(ob.get("asks", []))

            bid_prices = [x[0] for x in bids]
            ask_prices = [x[0] for x in asks]
            if bid_prices and not _is_desc(bid_prices):
                has_non_mono = True
            if ask_prices and not _is_asc(ask_prices):
                has_non_mono = True

            for px, sz in bids + asks:
                if px <= 0 or sz < 0:
                    has_bad_level = True
                    break

            if bids and asks:
                best_bid = bids[0][0]
                best_ask = asks[0][0]
                if best_bid >= best_ask:
                    has_crossed = True

            tf = trade_flow.get(tid, {})
            if not isinstance(tf, dict):
                bad_trade_flow_obj_ticks += 1
            else:
                _ = _to_float(tf.get("buy_taker_qty"), 0.0)
                _ = _to_float(tf.get("sell_taker_qty"), 0.0)

        if has_crossed:
            stats["ticks_with_crossed_books"] += 1
        if has_non_mono:
            stats["ticks_with_non_monotonic_levels"] += 1
        if has_bad_level:
            stats["ticks_with_non_positive_price_or_size"] += 1

    if token_ids:
        full_cover = 0
        for tid in token_ids:
            if covered_tokens.get(tid, 0) == len(ticks):
                full_cover += 1
            else:
                warnings.append(f"token {tid} orderbook coverage {covered_tokens.get(tid, 0)}/{len(ticks)}")
        stats["tokens_with_full_book_coverage"] = full_cover

    if missing_trade_flow_ticks > 0:
        warnings.append(f"missing trade_flow object in {missing_trade_flow_ticks} ticks")
    if bad_trade_flow_obj_ticks > 0:
        warnings.append(f"non-object trade_flow[token] entries found: {bad_trade_flow_obj_ticks}")
    for tid, cnt in sorted(missing_book_counts.items()):
        warnings.append(f"missing orderbook for token {tid} in {cnt} ticks")

    # Hard errors based on severe stats.
    if stats["ticks_with_non_positive_price_or_size"] > 0:
        errors.append("found non-positive price or negative size levels")
    if stats["ticks_with_non_monotonic_levels"] > 0:
        errors.append("found non-monotonic orderbook levels")

    report = {
        "ok": len(errors) == 0,
        "errors_count": len(errors),
        "warnings_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
    }
    if strict and errors:
        raise ValueError("; ".join(errors))
    return report


def validate_scenario_file(scenario_file: str, strict: bool = False) -> Dict[str, Any]:
    path = Path(scenario_file)
    payload = json.loads(path.read_text(encoding="utf-8"))
    report = validate_scenario_payload(payload, strict=strict)
    report["scenario_file"] = str(path)
    return report


def validate_scenarios_dir(
    scenarios_dir: str,
    strict: bool = False,
    fail_fast: bool = False,
) -> Dict[str, Any]:
    src = Path(scenarios_dir)
    files = sorted([x for x in src.glob("*.json") if x.is_file()])
    items: List[Dict[str, Any]] = []
    total_errors = 0
    total_warnings = 0
    ok_count = 0

    for f in files:
        item = validate_scenario_file(str(f), strict=False)
        items.append(item)
        total_errors += int(item.get("errors_count", 0))
        total_warnings += int(item.get("warnings_count", 0))
        if item.get("ok"):
            ok_count += 1
        elif fail_fast:
            break

    report = {
        "scenarios_dir": str(src),
        "count": len(items),
        "ok_count": ok_count,
        "failed_count": len(items) - ok_count,
        "total_errors": total_errors,
        "total_warnings": total_warnings,
        "items": items,
    }
    if strict and total_errors > 0:
        raise ValueError(f"validation failed: total_errors={total_errors}")
    return report
