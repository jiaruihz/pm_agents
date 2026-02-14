from __future__ import annotations

import asyncio
import copy
import csv
import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from src.domains.pmm.config import PMMConfig
from src.domains.pmm.core.anchoring import anchor_quotes_to_book
from src.domains.pmm.core.signals import fair_mid, inventory_signal, realized_vol, required_spread
from src.domains.pmm.core.sizing import target_sizes
from src.domains.pmm.core.strategy_base import StrategyQuoteInput
from src.domains.pmm.core.strategy_registry import StrategyRegistry
from src.platform.market_data.orderbook import best_bid_ask, spread as orderbook_spread
from src.domains.pmm.execution.order_manager import OrderManager
from src.domains.pmm.execution.paper_broker import PaperBroker
from src.domains.pmm.strategies.multi_level_v1 import MultiLevelV1Strategy
from src.domains.pmm.strategies.single_level_v1 import SingleLevelV1Strategy
from src.domains.pmm.utils.quantize import quantize_quote_pair
from src.domains.pmm.backtest.scenario_validator import validate_scenario_payload


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


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


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _build_leaderboard_rows(
    run_summaries: List[Dict[str, Any]],
    include_fill_model: bool = False,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for s in run_summaries:
        row = {
            "scenario": str(s.get("scenario_base", s.get("scenario_id", ""))),
            "fill_model": str(s.get("fill_model", "")),
            "pnl": float(s.get("pnl_end", 0.0)),
            "fills": int(s.get("total_fills", 0)),
            "orders": int(s.get("total_placed", 0)),
            "fill_pct": float(s.get("fill_rate_per_order", 0.0)),
            "mdd": float(s.get("max_drawdown", 0.0)),
        }
        if include_fill_model:
            rows.append(row)
        else:
            row.pop("fill_model", None)
            rows.append(row)
    rows.sort(key=lambda x: float(x.get("pnl", 0.0)), reverse=True)
    return rows


def _render_table(rows: List[Dict[str, Any]], include_fill_model: bool = False) -> str:
    if include_fill_model:
        header = "Scenario                                      FillModel      PnL  Fills  Orders  Fill%     MDD"
        sep = "-" * len(header)
        lines = [header, sep]
        for r in rows:
            lines.append(
                f"{r['scenario'][:42]:42}  "
                f"{r['fill_model'][:12]:12}  "
                f"{r['pnl']:8.2f}  "
                f"{r['fills']:5d}  "
                f"{r['orders']:6d}  "
                f"{_fmt_pct(r['fill_pct']):>6}  "
                f"{_fmt_pct(r['mdd']):>7}"
            )
        return "\n".join(lines)

    header = "Scenario                                      PnL  Fills  Orders  Fill%     MDD"
    sep = "-" * len(header)
    lines = [header, sep]
    for r in rows:
        lines.append(
            f"{r['scenario'][:42]:42}  "
            f"{r['pnl']:8.2f}  "
            f"{r['fills']:5d}  "
            f"{r['orders']:6d}  "
            f"{_fmt_pct(r['fill_pct']):>6}  "
            f"{_fmt_pct(r['mdd']):>7}"
        )
    return "\n".join(lines)


def _apply_strategy_overrides(cfg: PMMConfig, overrides: Dict[str, Any]) -> None:
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)


def _rounded(value: float, digits: int, eps: float = 1e-12) -> float:
    x = float(value)
    if abs(x) < eps:
        return 0.0
    return round(x, digits)


def _pending_open_exposure(open_orders: List[Dict[str, Any]], token_id: str) -> tuple[float, float]:
    buy_qty = 0.0
    sell_qty = 0.0
    for raw in open_orders:
        order_token = str(raw.get("asset_id") or raw.get("assetId") or raw.get("token_id") or "")
        if order_token != token_id:
            continue
        side = str(raw.get("side", "")).upper()
        if side == "0":
            side = "BUY"
        elif side == "1":
            side = "SELL"
        try:
            size = float(
                raw.get("remaining_size")
                or raw.get("size")
                or raw.get("original_size")
                or raw.get("amount")
                or 0.0
            )
        except Exception:
            size = 0.0
        if size <= 0:
            continue
        if side == "BUY":
            buy_qty += size
        elif side == "SELL":
            sell_qty += size
    return buy_qty, sell_qty


def _load_profiles(profiles_file: str) -> List[Dict[str, Any]]:
    profiles = json.loads(Path(profiles_file).read_text(encoding="utf-8"))
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("profiles file must be a non-empty json array")
    normalized: List[Dict[str, Any]] = []
    for idx, profile in enumerate(profiles):
        if not isinstance(profile, dict):
            continue
        profile_name = str(profile.get("name", f"profile_{idx}")).strip() or f"profile_{idx}"
        profile_overrides = profile.get("strategy_overrides", {})
        if not isinstance(profile_overrides, dict):
            profile_overrides = {}
        normalized.append(
            {
                "name": profile_name,
                "strategy_overrides": profile_overrides,
            }
        )
    if not normalized:
        raise ValueError("profiles file has no valid profile objects")
    return normalized


@dataclass
class ReplayResult:
    scenario_id: str
    summary: Dict[str, Any]
    metrics_path: str
    actions_path: str
    summary_path: str


async def _run_single_async(
    scenario: Dict[str, Any],
    out_dir: Path,
    validation_report: Dict[str, Any] | None = None,
) -> ReplayResult:
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
    quote_runtime_meta = cfg.quote_runtime_meta()
    strategy_registry = StrategyRegistry()
    strategy_registry.register(
        SingleLevelV1Strategy(
            anchor_quotes_fn=anchor_quotes_to_book,
            quantize_pair_fn=quantize_quote_pair,
            target_sizes_fn=target_sizes,
        )
    )
    strategy_registry.register(
        MultiLevelV1Strategy(
            anchor_quotes_fn=anchor_quotes_to_book,
            quantize_pair_fn=quantize_quote_pair,
            target_sizes_fn=target_sizes,
        )
    )
    strategy = strategy_registry.get(cfg.strategy_key)
    if strategy is None:
        available = ", ".join(strategy_registry.available_keys())
        raise ValueError(
            f"Unsupported strategy_key={cfg.strategy_key} in backtest. Available: {available}"
        )

    broker = PaperBroker(
        token_ids=token_ids,
        initial_usdc=initial_usdc,
        initial_positions=initial_positions,
        fill_model=cfg.paper_fill_model,
        fill_epsilon=cfg.paper_fill_epsilon,
        queue_share=cfg.paper_queue_share,
        maker_fee_bps=cfg.paper_maker_fee_bps,
        taker_fee_bps=cfg.paper_taker_fee_bps,
        min_fill_age_ticks=cfg.paper_min_fill_age_ticks,
        cancel_delay_ticks=cfg.paper_cancel_delay_ticks,
        require_trade_flow_for_at_bbo=cfg.paper_require_trade_flow_for_at_bbo,
        disable_at_bbo_in_conservative=cfg.paper_disable_at_bbo_in_conservative,
        conservative_bbo_share_multiplier=cfg.paper_conservative_bbo_share_multiplier,
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
    total_fill_events = 0
    total_filled_qty = 0.0
    total_fees = 0.0
    filled_order_ids: set[str] = set()
    total_errors = 0

    for tick_idx, tick in enumerate(ticks):
        event_label = str(tick.get("event", "normal"))
        orderbooks: Dict[str, Dict[str, Any]] = tick.get("orderbooks", {})
        trade_flow: Dict[str, Dict[str, Any]] = tick.get("trade_flow", {})

        balance = await broker.get_balance()
        open_orders = await broker.get_orders()
        positions = await broker.get_positions(token_ids)
        usdc_balance = float(balance.get("usdc_balance", 0.0))
        usdc_total = float(balance.get("usdc_total", usdc_balance))

        mids: Dict[str, float] = {}
        spreads: Dict[str, float] = {}
        book_tops: Dict[str, Dict[str, float]] = {}
        for token_id in token_ids:
            ob = orderbooks.get(token_id, {})
            mid = fair_mid(ob, cfg.mid_price_mode) if ob else 0.0
            mids[token_id] = mid
            spreads[token_id] = orderbook_spread(ob) if ob else 0.0
            book_tops[token_id] = best_bid_ask(ob) if ob else {"best_bid": 0.0, "best_ask": 0.0}
            if mid > 0:
                mid_history[token_id].append(mid)

        # Match previously resting orders first using current tick market data.
        # This avoids same-tick place->fill artifacts and is closer to exchange timing.
        fills_count = 0
        pre_tick_fills = await broker.on_market_data(orderbooks, trade_flow=trade_flow)
        if pre_tick_fills:
            fills_count = len(pre_tick_fills)
            total_fill_events += fills_count
            for fill in pre_tick_fills:
                fill_order_id = str(fill.get("order_id", ""))
                if fill_order_id:
                    filled_order_ids.add(fill_order_id)
                total_filled_qty += float(fill.get("size", 0.0) or 0.0)
                total_fees += float(fill.get("fee", 0.0) or 0.0)
                action_events.append(
                    {
                        "tick": tick_idx,
                        "event": event_label,
                        "type": "fill",
                        **fill,
                    }
                )
            balance = await broker.get_balance()
            open_orders = await broker.get_orders()
            positions = await broker.get_positions(token_ids)
            usdc_balance = float(balance.get("usdc_balance", 0.0))
            usdc_total = float(balance.get("usdc_total", usdc_balance))

        equity = usdc_total + sum(positions.get(tid, 0.0) * mids.get(tid, 0.0) for tid in token_ids)
        if baseline_equity is None:
            baseline_equity = equity
        pnl = equity - baseline_equity
        equity_curve.append(equity)

        placed = 0
        canceled = 0
        errors = 0
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

                inv_signal = inventory_signal(
                    token_id=token_id,
                    token_ids=token_ids,
                    positions=positions,
                    max_position=cfg.max_position,
                    sigmoid_k=cfg.inventory_sigmoid_k,
                )
                inventory_signals[token_id] = inv_signal

                rv = realized_vol(mid_history[token_id])
                req_spread = required_spread(cfg, rv, inv_signal)
                required_spreads[token_id] = req_spread
                adaptive_spread = max(cfg.base_spread, req_spread)
                adaptive_spreads[token_id] = adaptive_spread

                natural_spread = spreads.get(token_id, 0.0)
                min_live_spread = max(cfg.min_profitability_spread, req_spread)
                market_too_thin = natural_spread > 0 and natural_spread < min_live_spread
                block_buy = market_too_thin
                block_sell = market_too_thin

                top = book_tops.get(token_id, {"best_bid": 0.0, "best_ask": 0.0})
                position = positions.get(token_id, 0.0)
                open_buy_qty, open_sell_qty = _pending_open_exposure(local_orders, token_id)
                quote_targets = strategy.generate_quotes(
                    StrategyQuoteInput(
                        token_id=token_id,
                        mid=mid,
                        adaptive_spread=adaptive_spread,
                        inventory_signal=inv_signal,
                        best_bid=top.get("best_bid", 0.0),
                        best_ask=top.get("best_ask", 0.0),
                        position=position,
                        effective_usdc_balance=usdc_balance,
                        open_buy_qty=open_buy_qty,
                        open_sell_qty=open_sell_qty,
                    ),
                    config=cfg,
                )
                target_bid = 0.0
                target_ask = 0.0
                final_bid = 0.0
                final_ask = 0.0
                side_targets: Dict[str, List[Dict[str, Any]]] = {"BUY": [], "SELL": []}
                for q in quote_targets:
                    q_target_price = q.target_price if q.target_price > 0 else q.price
                    if q.side == "BUY":
                        target_bid = max(target_bid, q_target_price)
                        final_bid = max(final_bid, q.price)
                    else:
                        target_ask = q_target_price if target_ask <= 0 else min(target_ask, q_target_price)
                        final_ask = q.price if final_ask <= 0 else min(final_ask, q.price)
                    side_targets[q.side].append(
                        {
                            "price": q.price,
                            "size": q.size,
                            "level": q.level,
                            "target_price": q_target_price,
                        }
                    )
                target_quotes[token_id] = {"bid": target_bid, "ask": target_ask}
                final_quotes[token_id] = {"bid": final_bid, "ask": final_ask}

                for side in ("BUY", "SELL"):
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

                    decision = order_mgr.diff_multi(
                        open_orders=local_orders,
                        token_id=token_id,
                        side=side,
                        targets=side_targets.get(side, []),
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
                    for t in decision.create_targets:
                        price = float(t.get("price", 0.0) or 0.0)
                        size = float(t.get("size", 0.0) or 0.0)
                        level = int(t.get("level", 0) or 0)
                        if size < cfg.min_size or price <= 0:
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
                                    "level": level,
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
                                    "level": level,
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

        open_orders_after = await broker.get_orders()
        metrics_events.append(
            {
                "tick": tick_idx,
                "event": event_label,
                "strategy_key": strategy.key,
                "quote_runtime": quote_runtime_meta,
                "mids": mids,
                "spreads": spreads,
                "positions": positions,
                "usdc_balance": usdc_balance,
                "usdc_total": usdc_total,
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
                "trade_flow": trade_flow,
                "circuit_breaker_triggered": circuit_breaker_triggered,
                "circuit_breaker_reasons": circuit_breaker_reasons,
            }
        )

    summary = {
        "scenario_id": scenario_id,
        "description": str(scenario.get("description", "")),
        "strategy_key": strategy.key,
        "quote_runtime": quote_runtime_meta,
        "strategy_overrides": scenario.get("strategy_overrides", {}),
        "ticks": len(metrics_events),
        "token_ids": token_ids,
        "pnl_end": (equity_curve[-1] - equity_curve[0]) if equity_curve else 0.0,
        "equity_start": equity_curve[0] if equity_curve else 0.0,
        "equity_end": equity_curve[-1] if equity_curve else 0.0,
        "max_drawdown": _max_drawdown(equity_curve),
        "total_placed": total_placed,
        "total_canceled": total_canceled,
        "total_fills": len(filled_order_ids),
        "total_fill_events": total_fill_events,
        "total_filled_qty": total_filled_qty,
        "total_fees": total_fees,
        "total_errors": total_errors,
        "fill_rate_per_order": (len(filled_order_ids) / max(1, total_placed)),
        "avg_pnl_per_tick": (
            sum(x.get("pnl", 0.0) for x in metrics_events) / max(1, len(metrics_events))
        ),
    }
    if validation_report is not None:
        summary["scenario_validation"] = {
            "ok": bool(validation_report.get("ok", False)),
            "errors_count": int(validation_report.get("errors_count", 0)),
            "warnings_count": int(validation_report.get("warnings_count", 0)),
            "stats": validation_report.get("stats", {}),
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
    report = validate_scenario_payload(payload, strict=True)
    return asyncio.run(_run_single_async(payload, Path(out_dir), validation_report=report))


def run_scenario_payload(scenario: Dict[str, Any], out_dir: str) -> ReplayResult:
    report = validate_scenario_payload(scenario, strict=True)
    return asyncio.run(_run_single_async(scenario, Path(out_dir), validation_report=report))


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
        leaderboard_rows = _build_leaderboard_rows([x.summary for x in results], include_fill_model=False)
        all_summary["leaderboard_rows"] = leaderboard_rows

    report_path = Path(out_dir) / "summary_all.json"
    _write_json(report_path, all_summary)
    if results:
        table_text = _render_table(all_summary["leaderboard_rows"], include_fill_model=False)
        _write_text(Path(out_dir) / "summary_all_table.txt", table_text + "\n")
    return all_summary


def run_scenarios_dir_with_fill_models(
    scenarios_dir: str,
    out_dir: str,
    fill_models: List[str],
) -> Dict[str, Any]:
    src = Path(scenarios_dir)
    files = sorted([x for x in src.glob("*.json") if x.is_file()])
    normalized_models = [str(x).strip().lower() for x in fill_models if str(x).strip()]
    normalized_models = [x for x in normalized_models if x in {"conservative", "optimistic"}]
    if not normalized_models:
        normalized_models = ["conservative", "optimistic"]

    run_summaries: List[Dict[str, Any]] = []
    for model in normalized_models:
        for file_path in files:
            scenario = json.loads(file_path.read_text(encoding="utf-8"))
            base_id = str(scenario.get("scenario_id", file_path.stem))
            merged = {}
            merged.update(scenario.get("strategy_overrides", {}) or {})
            merged["paper_fill_model"] = model
            scenario["strategy_overrides"] = merged
            scenario["scenario_id"] = f"{base_id}__fill_{model}"
            result = run_scenario_payload(scenario, out_dir=out_dir)
            row = dict(result.summary)
            row["scenario_base"] = base_id
            row["fill_model"] = model
            run_summaries.append(row)

    report = {
        "count": len(run_summaries),
        "fill_models": normalized_models,
        "runs": run_summaries,
        "leaderboard_rows": _build_leaderboard_rows(run_summaries, include_fill_model=True),
    }

    summary_by_scenario: Dict[str, Dict[str, Any]] = {}
    for row in run_summaries:
        base = str(row.get("scenario_base", row.get("scenario_id", "")))
        item = summary_by_scenario.setdefault(base, {"scenario": base, "models": {}})
        item["models"][str(row.get("fill_model", ""))] = {
            "pnl_end": float(row.get("pnl_end", 0.0)),
            "fills": int(row.get("total_fills", 0)),
            "orders": int(row.get("total_placed", 0)),
            "fill_rate_per_order": float(row.get("fill_rate_per_order", 0.0)),
            "max_drawdown": float(row.get("max_drawdown", 0.0)),
        }
    report["by_scenario"] = list(summary_by_scenario.values())

    out_path = Path(out_dir)
    _write_json(out_path / "summary_all_fill_models.json", report)
    table_text = _render_table(report["leaderboard_rows"], include_fill_model=True)
    _write_text(out_path / "summary_all_fill_models_table.txt", table_text + "\n")
    _write_csv(
        out_path / "summary_all_fill_models_table.csv",
        report["leaderboard_rows"],
        fieldnames=["scenario", "fill_model", "pnl", "fills", "orders", "fill_pct", "mdd"],
    )
    return report


def run_scenario_compare(
    scenario_file: str,
    profiles_file: str,
    out_dir: str,
) -> Dict[str, Any]:
    base_scenario = json.loads(Path(scenario_file).read_text(encoding="utf-8"))
    profiles = _load_profiles(profiles_file)

    base_id = str(base_scenario.get("scenario_id", "scenario"))
    compare_out = Path(out_dir)
    compare_out.mkdir(parents=True, exist_ok=True)
    run_summaries: List[Dict[str, Any]] = []

    for profile in profiles:
        profile_name = profile["name"]
        profile_overrides = profile["strategy_overrides"]
        scenario = copy.deepcopy(base_scenario)
        merged = {}
        merged.update(base_scenario.get("strategy_overrides", {}) or {})
        merged.update(profile_overrides)
        scenario["strategy_overrides"] = merged
        scenario["scenario_id"] = f"{base_id}__{profile_name}"
        scenario["description"] = (
            f"{base_scenario.get('description', '')} | compare_profile={profile_name}"
        ).strip()

        result = run_scenario_payload(scenario, out_dir=str(compare_out))
        row = dict(result.summary)
        row["profile_name"] = profile_name
        run_summaries.append(row)

    by_pnl = sorted(run_summaries, key=lambda x: float(x.get("pnl_end", 0.0)))
    compare_report = {
        "base_scenario_file": str(scenario_file),
        "profiles_file": str(profiles_file),
        "count": len(run_summaries),
        "runs": run_summaries,
        "best_by_pnl": by_pnl[-1]["scenario_id"] if by_pnl else None,
        "worst_by_pnl": by_pnl[0]["scenario_id"] if by_pnl else None,
        "best_pnl_value": _rounded(float(by_pnl[-1].get("pnl_end", 0.0)), 3) if by_pnl else 0.0,
        "worst_pnl_value": _rounded(float(by_pnl[0].get("pnl_end", 0.0)), 3) if by_pnl else 0.0,
    }
    report_path = compare_out / "compare_summary.json"
    _write_json(report_path, compare_report)
    return compare_report


def run_scenarios_compare_all(
    scenarios_dir: str,
    profiles_file: str,
    out_dir: str,
) -> Dict[str, Any]:
    src = Path(scenarios_dir)
    files = sorted([x for x in src.glob("*.json") if x.is_file()])
    profiles = _load_profiles(profiles_file)
    compare_out = Path(out_dir)
    compare_out.mkdir(parents=True, exist_ok=True)

    runs: List[Dict[str, Any]] = []
    for scenario_file in files:
        base_scenario = json.loads(scenario_file.read_text(encoding="utf-8"))
        base_id = str(base_scenario.get("scenario_id", scenario_file.stem))
        for profile in profiles:
            profile_name = profile["name"]
            profile_overrides = profile["strategy_overrides"]
            scenario = copy.deepcopy(base_scenario)
            merged = {}
            merged.update(base_scenario.get("strategy_overrides", {}) or {})
            merged.update(profile_overrides)
            scenario["strategy_overrides"] = merged
            scenario["scenario_id"] = f"{base_id}__{profile_name}"
            scenario["description"] = (
                f"{base_scenario.get('description', '')} | compare_profile={profile_name}"
            ).strip()

            result = run_scenario_payload(scenario, out_dir=str(compare_out))
            summary = dict(result.summary)
            quote_runtime = summary.get("quote_runtime", {}) if isinstance(summary.get("quote_runtime"), dict) else {}
            runs.append(
                {
                    "scenario_id": base_id,
                    "scenario_run_id": str(summary.get("scenario_id", "")),
                    "profile_name": profile_name,
                    "strategy_key": str(summary.get("strategy_key", "")),
                    "quote_levels_effective": int(quote_runtime.get("quote_levels_effective", 1)),
                    "pnl_end": _rounded(float(summary.get("pnl_end", 0.0)), 3),
                    "max_drawdown": _rounded(float(summary.get("max_drawdown", 0.0)), 6),
                    "total_placed": int(summary.get("total_placed", 0)),
                    "total_canceled": int(summary.get("total_canceled", 0)),
                    "total_fills": int(summary.get("total_fills", 0)),
                    "fill_rate_per_order": _rounded(float(summary.get("fill_rate_per_order", 0.0)), 6),
                }
            )

    matrix_rows = sorted(runs, key=lambda x: (x["scenario_id"], x["profile_name"]))
    matrix_path = compare_out / "compare_matrix.csv"
    _write_csv(
        matrix_path,
        matrix_rows,
        fieldnames=[
            "scenario_id",
            "scenario_run_id",
            "profile_name",
            "strategy_key",
            "quote_levels_effective",
            "pnl_end",
            "max_drawdown",
            "total_placed",
            "total_canceled",
            "total_fills",
            "fill_rate_per_order",
        ],
    )

    agg: Dict[str, Dict[str, Any]] = {}
    for row in matrix_rows:
        key = str(row["profile_name"])
        item = agg.setdefault(
            key,
            {
                "profile_name": key,
                "strategy_key": str(row["strategy_key"]),
                "scenarios": 0,
                "sum_pnl_end": 0.0,
                "sum_max_drawdown": 0.0,
                "sum_total_placed": 0,
                "sum_total_fills": 0,
            },
        )
        item["scenarios"] += 1
        item["sum_pnl_end"] += float(row["pnl_end"])
        item["sum_max_drawdown"] += float(row["max_drawdown"])
        item["sum_total_placed"] += int(row["total_placed"])
        item["sum_total_fills"] += int(row["total_fills"])

    aggregate_rows: List[Dict[str, Any]] = []
    for item in agg.values():
        scenarios_count = max(1, int(item["scenarios"]))
        sum_orders = int(item["sum_total_placed"])
        sum_fills = int(item["sum_total_fills"])
        aggregate_rows.append(
            {
                "profile_name": item["profile_name"],
                "strategy_key": item["strategy_key"],
                "scenarios": int(item["scenarios"]),
                "avg_pnl_end": _rounded(float(item["sum_pnl_end"]) / scenarios_count, 3),
                "avg_max_drawdown": _rounded(float(item["sum_max_drawdown"]) / scenarios_count, 6),
                "sum_total_placed": sum_orders,
                "sum_total_fills": sum_fills,
                "overall_fill_rate": _rounded((sum_fills / max(1, sum_orders)), 6),
            }
        )
    aggregate_rows.sort(key=lambda x: float(x["avg_pnl_end"]), reverse=True)
    aggregate_path = compare_out / "compare_aggregate_by_profile.csv"
    _write_csv(
        aggregate_path,
        aggregate_rows,
        fieldnames=[
            "profile_name",
            "strategy_key",
            "scenarios",
            "avg_pnl_end",
            "avg_max_drawdown",
            "sum_total_placed",
            "sum_total_fills",
            "overall_fill_rate",
        ],
    )

    summary = {
        "scenarios_count": len(files),
        "profiles_count": len(profiles),
        "rows_count": len(matrix_rows),
        "matrix_csv": str(matrix_path),
        "aggregate_csv": str(aggregate_path),
        "top_profile_by_avg_pnl": aggregate_rows[0]["profile_name"] if aggregate_rows else None,
        "top_profile_avg_pnl": _rounded(float(aggregate_rows[0]["avg_pnl_end"]), 3) if aggregate_rows else 0.0,
        "bottom_profile_by_avg_pnl": aggregate_rows[-1]["profile_name"] if aggregate_rows else None,
        "bottom_profile_avg_pnl": _rounded(float(aggregate_rows[-1]["avg_pnl_end"]), 3) if aggregate_rows else 0.0,
    }
    _write_json(compare_out / "summary.json", summary)
    return summary
