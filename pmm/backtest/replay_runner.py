from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from pmm.config import PMMConfig
from pmm.order_manager import OrderManager
from pmm.orderbook import best_bid_ask, spread as orderbook_spread
from pmm.paper_broker import PaperBroker
from pmm.pricing import compute_quotes
import pmm.tick_loop as live


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, events: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for item in events:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def _max_drawdown(equity_curve: List[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    worst = 0.0
    for x in equity_curve:
        peak = max(peak, x)
        dd = (peak - x) / max(1e-9, peak)
        worst = max(worst, dd)
    return worst


def _apply_strategy_overrides(cfg: PMMConfig, overrides: Dict[str, Any]) -> None:
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)


@dataclass
class ReplayResult:
    scenario_id: str
    summary: Dict[str, Any]
    metrics_path: str
    actions_path: str
    summary_path: str


async def _run_single_async(scenario: Dict[str, Any], out_dir: Path) -> ReplayResult:
    scenario_id = str(scenario.get("scenario_id", "unknown_scenario"))
    token_ids = [str(x) for x in scenario.get("token_ids", [])]
    if len(token_ids) < 2:
        raise ValueError("scenario.token_ids must include at least 2 ids (YES/NO)")

    initial_state = scenario.get("initial_state", {})
    initial_usdc = float(initial_state.get("usdc", 1000.0))
    initial_positions = initial_state.get("positions", {})
    ticks = scenario.get("ticks", [])
    if not isinstance(ticks, list) or not ticks:
        raise ValueError("scenario.ticks must be a non-empty list")

    cfg = PMMConfig()
    cfg.execution_mode = "paper"
    cfg.market.token_ids = token_ids
    _apply_strategy_overrides(cfg, scenario.get("strategy_overrides", {}))

    broker = PaperBroker(
        token_ids=token_ids,
        initial_usdc=initial_usdc,
        initial_positions=initial_positions,
        fill_model=cfg.paper_fill_model,
        fill_epsilon=cfg.paper_fill_epsilon,
        queue_share=cfg.paper_queue_share,
    )
    order_mgr = OrderManager(deadband=cfg.deadband)

    window_points = max(
        2,
        int(max(1, max(cfg.circuit_breaker_window_sec, cfg.alpha_window_sec)) / max(0.1, cfg.tick_interval_sec)),
    )
    mid_history: Dict[str, deque[float]] = {tid: deque(maxlen=window_points) for tid in token_ids}

    baseline_equity: float | None = None
    metrics_events: List[Dict[str, Any]] = []
    action_events: List[Dict[str, Any]] = []
    equity_curve: List[float] = []
    total_placed = 0
    total_canceled = 0
    total_fills = 0
    total_errors = 0

    for tick_idx, tick in enumerate(ticks):
        event_label = str(tick.get("event", "normal"))
        orderbooks: Dict[str, Dict[str, Any]] = tick.get("orderbooks", {})

        balance = await broker.get_balance()
        open_orders = await broker.get_orders()
        positions = await broker.get_positions(token_ids)
        usdc_balance = float(balance.get("usdc_balance", 0.0))

        mids: Dict[str, float] = {}
        spreads: Dict[str, float] = {}
        book_tops: Dict[str, Dict[str, float]] = {}
        for token_id in token_ids:
            ob = orderbooks.get(token_id, {})
            mid = live._fair_mid(ob, cfg.mid_price_mode) if ob else 0.0
            mids[token_id] = mid
            spreads[token_id] = orderbook_spread(ob) if ob else 0.0
            book_tops[token_id] = best_bid_ask(ob) if ob else {"best_bid": 0.0, "best_ask": 0.0}
            if mid > 0:
                mid_history[token_id].append(mid)

        equity = usdc_balance + sum(positions.get(tid, 0.0) * mids.get(tid, 0.0) for tid in token_ids)
        if baseline_equity is None:
            baseline_equity = equity
        pnl = equity - baseline_equity
        equity_curve.append(equity)

        placed = 0
        canceled = 0
        errors = 0
        fills_count = 0
        circuit_breaker_triggered = False
        circuit_breaker_reasons: List[Dict[str, float]] = []
        inventory_signals: Dict[str, float] = {}
        target_quotes: Dict[str, Dict[str, float]] = {}
        final_quotes: Dict[str, Dict[str, float]] = {}
        required_spreads: Dict[str, float] = {}
        adaptive_spreads: Dict[str, float] = {}
        side_blocks: Dict[str, List[str]] = {"BUY": [], "SELL": []}
        local_orders = list(open_orders)

        if cfg.circuit_breaker_enabled:
            for token_id in token_ids:
                mid = mids.get(token_id, 0.0)
                if mid <= 0:
                    continue
                hist = mid_history[token_id]
                if len(hist) < max(2, cfg.circuit_breaker_min_points):
                    continue
                moving_average_mid = sum(hist) / len(hist)
                deviation = abs(mid - moving_average_mid) / max(1e-9, moving_average_mid)
                if deviation >= cfg.circuit_breaker_threshold:
                    circuit_breaker_triggered = True
                    circuit_breaker_reasons.append(
                        {
                            "token_id": token_id,
                            "mid": mid,
                            "moving_average_mid": moving_average_mid,
                            "deviation": deviation,
                        }
                    )

        if circuit_breaker_triggered:
            canceled_ids = []
            try:
                all_open_ids = [
                    str(x.get("id") or x.get("order_id") or x.get("orderID") or "")
                    for x in local_orders
                ]
                await broker.cancel_all_orders()
                canceled_ids = [x for x in all_open_ids if x]
                canceled += len(canceled_ids)
                local_orders = []
            except Exception as exc:
                errors += 1
                total_errors += 1
                action_events.append(
                    {
                        "tick": tick_idx,
                        "event": event_label,
                        "type": "error",
                        "action": "cancel_all",
                        "message": str(exc),
                    }
                )
            if canceled_ids:
                action_events.append(
                    {
                        "tick": tick_idx,
                        "event": event_label,
                        "type": "cancel_all",
                        "order_ids": canceled_ids,
                    }
                )
        else:
            for token_id in token_ids:
                mid = mids.get(token_id, 0.0)
                if mid <= 0:
                    continue

                inv_signal = live._inventory_signal(
                    token_id=token_id,
                    token_ids=token_ids,
                    positions=positions,
                    max_position=cfg.max_position,
                    sigmoid_k=cfg.inventory_sigmoid_k,
                )
                inventory_signals[token_id] = inv_signal

                rv = live._realized_vol(mid_history[token_id])
                req_spread = live._required_spread(cfg, rv, inv_signal)
                required_spreads[token_id] = req_spread
                adaptive_spread = max(cfg.base_spread, req_spread)
                adaptive_spreads[token_id] = adaptive_spread

                natural_spread = spreads.get(token_id, 0.0)
                min_live_spread = max(cfg.min_profitability_spread, req_spread)
                market_too_thin = natural_spread > 0 and natural_spread < min_live_spread
                block_buy = market_too_thin
                block_sell = market_too_thin

                quote = compute_quotes(
                    mid=mid,
                    spread=adaptive_spread,
                    inventory=inv_signal,
                    skew_factor=cfg.skew_factor,
                )
                target_quotes[token_id] = {"bid": quote.bid, "ask": quote.ask}
                top = book_tops.get(token_id, {"best_bid": 0.0, "best_ask": 0.0})
                bid_price, ask_price = live._anchor_quotes_to_book(
                    target_bid=quote.bid,
                    target_ask=quote.ask,
                    best_bid=top.get("best_bid", 0.0),
                    best_ask=top.get("best_ask", 0.0),
                    join_epsilon=cfg.join_epsilon,
                    fair_value=mid,
                    min_edge=cfg.min_edge,
                )
                bid_price, ask_price = live._quantize_quote_pair(
                    bid_price, ask_price, tick=cfg.price_tick, mode=cfg.price_tick_mode
                )
                final_quotes[token_id] = {"bid": bid_price, "ask": ask_price}

                position = positions.get(token_id, 0.0)
                buy_size, sell_size = live._target_sizes(cfg, position, usdc_balance, bid_price)

                for side, price, size in (("BUY", bid_price, buy_size), ("SELL", ask_price, sell_size)):
                    side_blocked = (side == "BUY" and block_buy) or (side == "SELL" and block_sell)
                    if side_blocked:
                        side_blocks[side].append(token_id)
                        blocked_ids = order_mgr.side_order_ids(
                            open_orders=local_orders, token_id=token_id, side=side
                        )
                        if blocked_ids:
                            try:
                                if len(blocked_ids) == 1:
                                    await broker.cancel_order(blocked_ids[0])
                                else:
                                    await broker.cancel_orders(blocked_ids)
                                canceled += len(blocked_ids)
                                total_canceled += len(blocked_ids)
                                action_events.append(
                                    {
                                        "tick": tick_idx,
                                        "event": event_label,
                                        "type": "cancel",
                                        "reason": "side_blocked",
                                        "token_id": token_id,
                                        "side": side,
                                        "order_ids": blocked_ids,
                                    }
                                )
                            except Exception as exc:
                                errors += 1
                                total_errors += 1
                                action_events.append(
                                    {
                                        "tick": tick_idx,
                                        "event": event_label,
                                        "type": "error",
                                        "action": "cancel",
                                        "token_id": token_id,
                                        "side": side,
                                        "message": str(exc),
                                    }
                                )
                            cancel_set = set(blocked_ids)
                            local_orders = [
                                x
                                for x in local_orders
                                if str(x.get("id") or x.get("orderID") or x.get("order_id")) not in cancel_set
                            ]
                        continue

                    decision = order_mgr.diff(
                        open_orders=local_orders,
                        token_id=token_id,
                        side=side,
                        target_price=price,
                        target_size=size,
                    )
                    if decision.cancel_ids:
                        try:
                            if len(decision.cancel_ids) == 1:
                                await broker.cancel_order(decision.cancel_ids[0])
                            else:
                                await broker.cancel_orders(decision.cancel_ids)
                            canceled += len(decision.cancel_ids)
                            total_canceled += len(decision.cancel_ids)
                            action_events.append(
                                {
                                    "tick": tick_idx,
                                    "event": event_label,
                                    "type": "cancel",
                                    "reason": decision.reason,
                                    "token_id": token_id,
                                    "side": side,
                                    "order_ids": decision.cancel_ids,
                                }
                            )
                            cancel_set = set(decision.cancel_ids)
                            local_orders = [
                                x
                                for x in local_orders
                                if str(x.get("id") or x.get("orderID") or x.get("order_id")) not in cancel_set
                            ]
                        except Exception as exc:
                            errors += 1
                            total_errors += 1
                            action_events.append(
                                {
                                    "tick": tick_idx,
                                    "event": event_label,
                                    "type": "error",
                                    "action": "cancel",
                                    "token_id": token_id,
                                    "side": side,
                                    "message": str(exc),
                                }
                            )
                    if (not decision.create) or size < cfg.min_size:
                        continue
                    try:
                        placed_order = await broker.place_limit_order(token_id, price, size, side)
                        placed += 1
                        total_placed += 1
                        local_orders.append(
                            {
                                "id": placed_order.get("id"),
                                "asset_id": token_id,
                                "side": side,
                                "price": price,
                                "size": size,
                            }
                        )
                        action_events.append(
                            {
                                "tick": tick_idx,
                                "event": event_label,
                                "type": "place",
                                "reason": decision.reason,
                                "token_id": token_id,
                                "side": side,
                                "price": price,
                                "size": size,
                                "order_id": placed_order.get("id"),
                            }
                        )
                    except Exception as exc:
                        errors += 1
                        total_errors += 1
                        action_events.append(
                            {
                                "tick": tick_idx,
                                "event": event_label,
                                "type": "error",
                                "action": "place",
                                "token_id": token_id,
                                "side": side,
                                "message": str(exc),
                            }
                        )

        fills = await broker.on_market_data(orderbooks)
        if fills:
            fills_count = len(fills)
            total_fills += fills_count
            for fill in fills:
                action_events.append(
                    {
                        "tick": tick_idx,
                        "event": event_label,
                        "type": "fill",
                        **fill,
                    }
                )

        open_orders_after = await broker.get_orders()
        metrics_events.append(
            {
                "tick": tick_idx,
                "event": event_label,
                "mids": mids,
                "spreads": spreads,
                "positions": positions,
                "usdc_balance": usdc_balance,
                "equity": equity,
                "pnl": pnl,
                "open_orders_count": len(open_orders_after),
                "placed": placed,
                "canceled": canceled,
                "fills": fills_count,
                "errors": errors,
                "inventory_signals": inventory_signals,
                "required_spreads": required_spreads,
                "adaptive_spreads": adaptive_spreads,
                "target_quotes": target_quotes,
                "final_quotes": final_quotes,
                "side_blocks": side_blocks,
                "circuit_breaker_triggered": circuit_breaker_triggered,
                "circuit_breaker_reasons": circuit_breaker_reasons,
            }
        )

    summary = {
        "scenario_id": scenario_id,
        "description": str(scenario.get("description", "")),
        "ticks": len(metrics_events),
        "token_ids": token_ids,
        "pnl_end": (equity_curve[-1] - equity_curve[0]) if equity_curve else 0.0,
        "equity_start": equity_curve[0] if equity_curve else 0.0,
        "equity_end": equity_curve[-1] if equity_curve else 0.0,
        "max_drawdown": _max_drawdown(equity_curve),
        "total_placed": total_placed,
        "total_canceled": total_canceled,
        "total_fills": total_fills,
        "total_errors": total_errors,
        "fill_rate_per_order": (total_fills / max(1, total_placed)),
        "avg_pnl_per_tick": (
            sum(x.get("pnl", 0.0) for x in metrics_events) / max(1, len(metrics_events))
        ),
    }

    scenario_dir = out_dir / scenario_id
    metrics_path = scenario_dir / "metrics.jsonl"
    actions_path = scenario_dir / "actions.jsonl"
    summary_path = scenario_dir / "summary.json"
    _write_jsonl(metrics_path, metrics_events)
    _write_jsonl(actions_path, action_events)
    _write_json(summary_path, summary)
    return ReplayResult(
        scenario_id=scenario_id,
        summary=summary,
        metrics_path=str(metrics_path),
        actions_path=str(actions_path),
        summary_path=str(summary_path),
    )


def run_scenario_file(scenario_file: str, out_dir: str) -> ReplayResult:
    payload = json.loads(Path(scenario_file).read_text(encoding="utf-8"))
    return asyncio.run(_run_single_async(payload, Path(out_dir)))


def run_scenarios_dir(scenarios_dir: str, out_dir: str) -> Dict[str, Any]:
    src = Path(scenarios_dir)
    files = sorted([x for x in src.glob("*.json") if x.is_file()])
    results: List[ReplayResult] = []
    for file_path in files:
        results.append(run_scenario_file(str(file_path), out_dir=out_dir))

    all_summary = {
        "count": len(results),
        "scenarios": [x.summary for x in results],
        "best_pnl_scenario": None,
        "worst_pnl_scenario": None,
        "avg_pnl_end": 0.0,
        "avg_max_drawdown": 0.0,
        "total_fills": 0,
        "total_orders": 0,
    }
    if results:
        by_pnl = sorted(results, key=lambda x: float(x.summary.get("pnl_end", 0.0)))
        all_summary["best_pnl_scenario"] = by_pnl[-1].summary.get("scenario_id")
        all_summary["worst_pnl_scenario"] = by_pnl[0].summary.get("scenario_id")
        all_summary["avg_pnl_end"] = sum(float(x.summary.get("pnl_end", 0.0)) for x in results) / len(results)
        all_summary["avg_max_drawdown"] = sum(
            float(x.summary.get("max_drawdown", 0.0)) for x in results
        ) / len(results)
        all_summary["total_fills"] = int(sum(int(x.summary.get("total_fills", 0)) for x in results))
        all_summary["total_orders"] = int(sum(int(x.summary.get("total_placed", 0)) for x in results))

    report_path = Path(out_dir) / "summary_all.json"
    _write_json(report_path, all_summary)
    return all_summary

