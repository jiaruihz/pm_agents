import asyncio
import math
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from pmm.config import PMMConfig
from pmm.core.anchoring import anchor_quotes_to_book as _anchor_quotes_to_book
from pmm.core.signals import (
    depth_near_mid as _depth_near_mid,
    fair_mid as _fair_mid,
    inventory_signal as _inventory_signal,
    momentum as _momentum,
    order_flow_imbalance as _order_flow_imbalance,
    realized_vol as _realized_vol,
    required_spread as _required_spread,
    weighted_mid as _weighted_mid,
)
from pmm.core.sizing import target_sizes as _target_sizes
from pmm.core.strategy_base import StrategyQuoteInput
from pmm.core.strategy_registry import StrategyRegistry
from pmm.data.http_client import ToolServiceClient
from pmm.data.market_ws import MarketWsFeed
from pmm.data.orderbook import best_bid_ask, mid_price, spread as orderbook_spread
from pmm.data.parsers import (
    mid_from_market as _mid_from_market,
    parse_account_state as _parse_account_state,
    parse_partition as _parse_partition,
    pending_credit_total as _pending_credit_total,
)
from pmm.execution.order_manager import OrderManager
from pmm.execution.paper_broker import PaperBroker
from pmm.strategies.multi_level_v1 import MultiLevelV1Strategy
from pmm.strategies.single_level_v1 import SingleLevelV1Strategy
from pmm.utils.converters import best_level as _best_level, normalize_levels as _normalize_levels, to_float as _safe_float, to_int as _safe_int
from pmm.utils.metrics import MetricsLogger
from pmm.utils.quantize import (
    quantize_quote_dict as _quantize_quote_dict,
    quantize_quote_pair as _quantize_quote_pair,
    quantize_price_dict as _quantize_price_dict,
    quantize_to_tick,
)




async def tick_loop(config: PMMConfig) -> None:
    if not config.market.token_ids:
        print("PMM_TOKEN_IDS 未设置，无法运行。")
        return

    token_ids = list(config.market.token_ids)
    reference_token_ids = [x for x in config.alpha_reference_token_ids if x and x not in token_ids]
    all_token_ids = token_ids + reference_token_ids

    # Core strategy state.
    order_mgr = OrderManager(deadband=config.deadband)
    metrics = MetricsLogger(config.metrics_path)
    strategy_registry = StrategyRegistry()
    strategy_registry.register(
        SingleLevelV1Strategy(
            anchor_quotes_fn=_anchor_quotes_to_book,
            quantize_pair_fn=_quantize_quote_pair,
            target_sizes_fn=_target_sizes,
        )
    )
    strategy_registry.register(
        MultiLevelV1Strategy(
            anchor_quotes_fn=_anchor_quotes_to_book,
            quantize_pair_fn=_quantize_quote_pair,
            target_sizes_fn=_target_sizes,
        )
    )
    strategy = strategy_registry.get(config.strategy_key)
    if strategy is None:
        available = ", ".join(strategy_registry.available_keys())
        raise ValueError(
            f"Unsupported strategy_key={config.strategy_key}. "
            f"Available: {available}"
        )
    print(f"[STRATEGY] using {strategy.key}")
    baseline_equity: Optional[float] = None
    history_window_sec = max(config.circuit_breaker_window_sec, config.alpha_window_sec)
    window_points = max(2, int(max(1, history_window_sec) / max(0.1, config.tick_interval_sec)))
    mid_history: Dict[str, deque[float]] = {
        token_id: deque(maxlen=window_points) for token_id in all_token_ids
    }

    use_ws = config.market_data_source.lower() == "ws"
    ws_feed: Optional[MarketWsFeed] = None
    if use_ws:
        ws_feed = MarketWsFeed(
            ws_url=config.ws_market_url,
            token_ids=all_token_ids,
            detail_level=config.ws_detail_level,
            app_ping_interval_sec=config.ws_app_ping_interval_sec,
            reconnect_delay_sec=config.ws_reconnect_delay_sec,
            stale_after_sec=config.ws_stale_after_sec,
            level_limit=config.ws_level_limit,
        )

    async with ToolServiceClient(config.api_base_url, config.api_key) as client:
        execution_mode = config.execution_mode.lower().strip()
        quote_runtime_meta = config.quote_runtime_meta()
        if quote_runtime_meta.get("multi_level_placeholder_active") and strategy.key != "multi_level_v1":
            print(
                "[QUOTE] multi-level requested but placeholder mode is active; "
                "runtime still uses single-level quoting."
            )
        if strategy.key == "multi_level_v1":
            print(
                "[QUOTE] multi-level active "
                f"levels={quote_runtime_meta.get('quote_levels_effective')}"
            )
        paper_broker: Optional[PaperBroker] = None
        execution_client: Any = client
        paper_bootstrap_actions: List[Dict[str, Any]] = []
        # Execution layer switch:
        # - live: send real API orders
        # - paper: local matching with real market data
        if execution_mode == "paper":
            paper_broker = PaperBroker(
                token_ids=token_ids,
                initial_usdc=config.paper_initial_usdc,
                initial_positions=config.paper_initial_positions,
                fill_model=config.paper_fill_model,
                fill_epsilon=config.paper_fill_epsilon,
                queue_share=config.paper_queue_share,
            )
            execution_client = paper_broker
            print(
                "[PAPER] enabled "
                f"initial_usdc={config.paper_initial_usdc} fill_model={config.paper_fill_model}"
            )
            # Optional bootstrap: simulate `split()` so we start with YES+NO inventory.
            # Without this, SELL quoting may fail due to zero paper positions.
            if config.paper_bootstrap_split_usdc > 0 and len(token_ids) >= 2:
                try:
                    yes_token_id = token_ids[0]
                    no_token_id = token_ids[1]
                    resp = await paper_broker.split_pair(
                        yes_token_id,
                        no_token_id,
                        config.paper_bootstrap_split_usdc,
                    )
                    paper_bootstrap_actions.append(resp)
                    print(
                        "[PAPER] bootstrap split ok "
                        f"split_usdc={config.paper_bootstrap_split_usdc} "
                        f"yes={yes_token_id} no={no_token_id}"
                    )
                except Exception as exc:
                    paper_bootstrap_actions.append(
                        {
                            "status": "error",
                            "error": str(exc),
                            "split_usdc": float(config.paper_bootstrap_split_usdc),
                        }
                    )
                    print(f"[PAPER][ERROR] bootstrap split failed: {exc}")

        async def _exec_call(method, *args):
            if paper_broker is not None:
                return await method(*args)
            return await client.retry(method, *args)

        if ws_feed:
            await ws_feed.start()
            print(f"[WS] market feed started: {config.ws_market_url}")

        pending_merge_credits: List[Dict[str, float]] = []
        try:
            tick_count = 0
            while True:
                # 1) Account snapshot from selected execution layer.
                balance_raw, open_orders_raw, positions_raw = await asyncio.gather(
                    _exec_call(execution_client.get_balance),
                    _exec_call(execution_client.get_orders),
                    _exec_call(execution_client.get_positions, token_ids),
                )

                orderbook_results: Dict[str, Dict[str, Any]] = {}
                if ws_feed:
                    # 2a) WS-first market data path. If stale/missing, fallback to REST snapshot.
                    missing_token_ids: List[str] = []
                    for token_id in all_token_ids:
                        ob = ws_feed.get_orderbook(token_id)
                        if ob and not ws_feed.is_stale(token_id):
                            orderbook_results[token_id] = ob
                        else:
                            missing_token_ids.append(token_id)

                    if missing_token_ids:
                        fallback = await asyncio.gather(
                            *[client.retry(client.get_orderbook, tid) for tid in missing_token_ids],
                            return_exceptions=True,
                        )
                        for token_id, ob in zip(missing_token_ids, fallback):
                            if isinstance(ob, dict):
                                orderbook_results[token_id] = ob
                                ws_feed.books.set_snapshot(
                                    token_id,
                                    bids=ob.get("bids", []),
                                    asks=ob.get("asks", []),
                                )
                            else:
                                orderbook_results[token_id] = {}
                else:
                    # 2b) REST-only market data path.
                    fallback = await asyncio.gather(
                        *[client.retry(client.get_orderbook, tid) for tid in all_token_ids],
                        return_exceptions=True,
                    )
                    for token_id, ob in zip(all_token_ids, fallback):
                        orderbook_results[token_id] = ob if isinstance(ob, dict) else {}

                usdc_balance, open_orders, positions = _parse_account_state(
                    balance_raw,
                    open_orders_raw,
                    positions_raw,
                    token_ids,
                )
                pending_usdc_credit = 0.0
                if (
                    paper_broker is None
                    and config.merge_pending_credit_enabled
                    and pending_merge_credits
                ):
                    # Pending credit is only for live mode to reduce merge-confirmation idle time.
                    pending_usdc_credit = _pending_credit_total(
                        pending_merge_credits, time.time()
                    )
                effective_usdc_for_sizing = usdc_balance + (
                    pending_usdc_credit * max(0.0, config.merge_pending_credit_ratio)
                )

                mids: Dict[str, float] = {}
                spreads: Dict[str, float] = {}
                book_tops: Dict[str, Dict[str, float]] = {}
                orderbooks: Dict[str, Dict[str, Any]] = {}
                for token_id in all_token_ids:
                    orderbook = orderbook_results.get(token_id, {})
                    mid = _fair_mid(orderbook, config.mid_price_mode) if orderbook else 0.0
                    spreads[token_id] = orderbook_spread(orderbook) if orderbook else 0.0
                    book_tops[token_id] = (
                        best_bid_ask(orderbook) if orderbook else {"best_bid": 0.0, "best_ask": 0.0}
                    )
                    if mid <= 0:
                        # Emergency fallback for sparse book / malformed feed.
                        try:
                            market = await client.retry(client.get_market, token_id)
                            mid = _mid_from_market(market) or 0.0
                        except Exception:
                            mid = 0.0
                    mids[token_id] = mid
                    orderbooks[token_id] = orderbook
                    if mid > 0:
                        mid_history[token_id].append(mid)

                paper_recent_fills: List[Dict[str, Any]] = []
                if paper_broker is not None:
                    # In paper mode, use the same real orderbook snapshot to drive local matching.
                    await paper_broker.on_market_data(
                        {token_id: orderbooks.get(token_id, {}) for token_id in token_ids}
                    )
                    paper_recent_fills = paper_broker.pop_recent_fills()
                    if paper_recent_fills:
                        balance_raw, open_orders_raw, positions_raw = await asyncio.gather(
                            _exec_call(execution_client.get_balance),
                            _exec_call(execution_client.get_orders),
                            _exec_call(execution_client.get_positions, token_ids),
                        )
                        usdc_balance, open_orders, positions = _parse_account_state(
                            balance_raw,
                            open_orders_raw,
                            positions_raw,
                            token_ids,
                        )

                equity = usdc_balance + sum(
                    positions.get(token_id, 0.0) * mids.get(token_id, 0.0) for token_id in token_ids
                )
                if baseline_equity is None:
                    baseline_equity = equity
                pnl = equity - baseline_equity
                net_inventory = 0.0
                if len(token_ids) >= 2:
                    net_inventory = positions.get(token_ids[0], 0.0) - positions.get(token_ids[1], 0.0)

                placed = 0
                canceled = 0
                errors = 0
                merge_actions: List[Dict[str, Any]] = []
                circuit_breaker_triggered = False
                circuit_breaker_reasons: List[Dict[str, float]] = []
                local_orders = list(open_orders)
                inventory_signals: Dict[str, float] = {}
                target_quotes: Dict[str, Dict[str, float]] = {}
                final_quotes: Dict[str, Dict[str, float]] = {}
                ofi_imbalances: Dict[str, float] = {}
                realized_volatility: Dict[str, float] = {}
                required_spreads: Dict[str, float] = {}
                adaptive_spreads: Dict[str, float] = {}
                side_blocks: Dict[str, List[str]] = {"BUY": [], "SELL": []}
                alpha_reference_momentum = 0.0

                if config.circuit_breaker_enabled:
                    # 3) Circuit breaker: abnormal deviation vs short moving average.
                    for token_id in token_ids:
                        mid = mids.get(token_id, 0.0)
                        if mid <= 0:
                            continue
                        hist = mid_history[token_id]
                        if len(hist) < max(2, config.circuit_breaker_min_points):
                            continue
                        moving_average_mid = sum(hist) / len(hist)
                        deviation = abs(mid - moving_average_mid) / max(1e-9, moving_average_mid)
                        if deviation >= config.circuit_breaker_threshold:
                            circuit_breaker_triggered = True
                            circuit_breaker_reasons.append(
                                {
                                    "token_id": token_id,
                                    "mid": mid,
                                    "moving_average_mid": moving_average_mid,
                                    "deviation": deviation,
                                }
                            )

                if config.alpha_enabled and reference_token_ids:
                    # 4) Cross-market momentum signal used as directional defense.
                    ref_momentum_values: List[float] = []
                    for ref_token_id in reference_token_ids:
                        value = _momentum(mid_history[ref_token_id], min_points=config.alpha_min_points)
                        if value != 0.0:
                            ref_momentum_values.append(value)
                    if ref_momentum_values:
                        alpha_reference_momentum = sum(ref_momentum_values) / len(ref_momentum_values)

                if circuit_breaker_triggered:
                    # Defensive mode: clear exposure first.
                    try:
                        if config.dry_run:
                            print(
                                "[DRY_RUN][CB] triggered; would cancel all orders. "
                                f"reasons={circuit_breaker_reasons}"
                            )
                        else:
                            await _exec_call(execution_client.cancel_all_orders)
                            canceled = len(open_orders)
                    except Exception as exc:
                        errors += 1
                        print(f"[ERROR] circuit breaker cancel-all failed: {exc}")

                    metrics.log(
                        {
                            "tick": tick_count,
                            "execution_mode": execution_mode,
                            "strategy_key": strategy.key,
                            "quote_runtime": quote_runtime_meta,
                            "market_data_source": "ws" if ws_feed else "rest",
                            "usdc_balance": usdc_balance,
                            "effective_usdc_for_sizing": effective_usdc_for_sizing,
                            "pending_usdc_credit": pending_usdc_credit,
                            "positions": positions,
                            "net_inventory": net_inventory,
                            "mids": _quantize_price_dict(mids, config.price_tick, config.price_tick_mode),
                            "spreads": _quantize_price_dict(spreads, config.price_tick, config.price_tick_mode),
                            "equity": equity,
                            "pnl": pnl,
                            "open_orders_count": len(open_orders),
                            "placed": placed,
                            "canceled": canceled,
                            "errors": errors,
                            "inventory_signals": inventory_signals,
                            "target_quotes": _quantize_quote_dict(
                                target_quotes, config.price_tick, config.price_tick_mode
                            ),
                            "final_quotes": _quantize_quote_dict(
                                final_quotes, config.price_tick, config.price_tick_mode
                            ),
                            "ofi_imbalances": ofi_imbalances,
                            "realized_volatility": realized_volatility,
                            "required_spreads": required_spreads,
                            "adaptive_spreads": adaptive_spreads,
                            "alpha_reference_momentum": alpha_reference_momentum,
                            "side_blocks": side_blocks,
                            "merge_actions": merge_actions,
                            "paper_recent_fills": paper_recent_fills,
                            "paper_bootstrap_actions": paper_bootstrap_actions,
                            "circuit_breaker_triggered": True,
                            "circuit_breaker_reasons": circuit_breaker_reasons,
                        }
                    )
                    if config.circuit_breaker_halt_on_trigger:
                        print("[CB] Halted market making after trigger.")
                        return
                    tick_count += 1
                    if config.max_ticks > 0 and tick_count >= config.max_ticks:
                        return
                    await asyncio.sleep(config.tick_interval_sec)
                    continue

                for token_id in token_ids:
                    mid = mids.get(token_id, 0.0)
                    if mid <= 0:
                        continue

                    # 5) Compute signal stack: inventory -> volatility -> required spread -> OFI/alpha blocks.
                    inv_signal = _inventory_signal(
                        token_id=token_id,
                        token_ids=token_ids,
                        positions=positions,
                        max_position=config.max_position,
                        sigmoid_k=config.inventory_sigmoid_k,
                    )
                    inventory_signals[token_id] = inv_signal

                    rv = _realized_vol(mid_history[token_id])
                    realized_volatility[token_id] = rv
                    req_spread = _required_spread(config, rv, inv_signal)
                    required_spreads[token_id] = req_spread
                    adaptive_spread = max(config.base_spread, req_spread)
                    adaptive_spreads[token_id] = adaptive_spread

                    natural_spread = spreads.get(token_id, 0.0)
                    min_live_spread = max(config.min_profitability_spread, req_spread)
                    market_too_thin = natural_spread > 0 and natural_spread < min_live_spread

                    ofi = 0.0
                    if config.alpha_enabled and config.alpha_ofi_enabled:
                        ofi = _order_flow_imbalance(orderbooks.get(token_id, {}), delta=config.alpha_ofi_delta)
                    ofi_imbalances[token_id] = ofi

                    block_buy = False
                    block_sell = False
                    if market_too_thin:
                        block_buy = True
                        block_sell = True
                    if config.alpha_enabled and config.alpha_ofi_enabled:
                        if ofi >= config.alpha_ofi_imbalance_threshold:
                            block_sell = True
                        elif ofi <= -config.alpha_ofi_imbalance_threshold:
                            block_buy = True
                    if config.alpha_enabled and abs(alpha_reference_momentum) >= config.alpha_ref_momentum_threshold:
                        if alpha_reference_momentum > 0:
                            block_sell = True
                        else:
                            block_buy = True

                    top = book_tops.get(token_id, {"best_bid": 0.0, "best_ask": 0.0})
                    position = positions.get(token_id, 0.0)
                    quote_targets = strategy.generate_quotes(
                        StrategyQuoteInput(
                            token_id=token_id,
                            mid=mid,
                            adaptive_spread=adaptive_spread,
                            inventory_signal=inv_signal,
                            best_bid=top.get("best_bid", 0.0),
                            best_ask=top.get("best_ask", 0.0),
                            position=position,
                            effective_usdc_balance=effective_usdc_for_sizing,
                        ),
                        config=config,
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
                                open_orders=local_orders,
                                token_id=token_id,
                                side=side,
                            )
                            if blocked_ids:
                                try:
                                    if config.dry_run:
                                        print(f"[DRY_RUN][BLOCK] CANCEL {token_id} {side} ids={blocked_ids}")
                                    else:
                                        if len(blocked_ids) == 1:
                                            await _exec_call(execution_client.cancel_order, blocked_ids[0])
                                        else:
                                            await _exec_call(execution_client.cancel_orders, blocked_ids)
                                    canceled += len(blocked_ids)
                                    cancel_set = set(blocked_ids)
                                    local_orders = [
                                        x
                                        for x in local_orders
                                        if str(x.get("id") or x.get("orderID") or x.get("order_id"))
                                        not in cancel_set
                                    ]
                                except Exception as exc:
                                    errors += 1
                                    print(
                                        f"[ERROR] blocked-side cancel failed token={token_id} "
                                        f"side={side}: {exc}"
                                    )
                            continue

                        decision = order_mgr.diff_multi(
                            open_orders=local_orders,
                            token_id=token_id,
                            side=side,
                            targets=side_targets.get(side, []),
                        )
                        if decision.cancel_ids:
                            try:
                                if config.dry_run:
                                    print(
                                        f"[DRY_RUN] CANCEL {token_id} {side} "
                                        f"ids={decision.cancel_ids} reason={decision.reason}"
                                    )
                                else:
                                    if len(decision.cancel_ids) == 1:
                                        await _exec_call(execution_client.cancel_order, decision.cancel_ids[0])
                                    else:
                                        await _exec_call(execution_client.cancel_orders, decision.cancel_ids)
                                canceled += len(decision.cancel_ids)
                                cancel_set = set(decision.cancel_ids)
                                local_orders = [
                                    x
                                    for x in local_orders
                                    if str(x.get("id") or x.get("orderID") or x.get("order_id"))
                                    not in cancel_set
                                ]
                            except Exception as exc:
                                errors += 1
                                print(f"[ERROR] cancel failed token={token_id} side={side}: {exc}")

                        for t in decision.create_targets:
                            price = _safe_float(t.get("price"), 0.0)
                            size = _safe_float(t.get("size"), 0.0)
                            level = _safe_int(t.get("level"), 0)
                            if size < config.min_size or price <= 0:
                                continue
                            try:
                                if config.dry_run:
                                    print(
                                        f"[DRY_RUN] PLACE {side} {token_id} "
                                        f"level={level} price={price:.4f} size={size:.4f} "
                                        f"reason={decision.reason}"
                                    )
                                    placed_order = {
                                        "id": f"dry_{token_id}_{side}_{level}_{tick_count}",
                                        "asset_id": token_id,
                                        "side": side,
                                        "price": price,
                                        "size": size,
                                        "level": level,
                                    }
                                else:
                                    placed_order = await _exec_call(
                                        execution_client.place_limit_order,
                                        token_id,
                                        price,
                                        size,
                                        side,
                                    )
                                placed += 1
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
                            except Exception as exc:
                                errors += 1
                                print(f"[ERROR] place failed token={token_id} side={side}: {exc}")

                if (
                    config.auto_merge_enabled
                    and config.auto_merge_plans
                    and config.auto_merge_every_ticks > 0
                    and (tick_count + 1) % config.auto_merge_every_ticks == 0
                ):
                    # 7) Periodic capital recycle for paired YES/NO inventory.
                    for plan in config.auto_merge_plans:
                        yes_token_id = str(plan.get("yes_token_id", "")).strip()
                        no_token_id = str(plan.get("no_token_id", "")).strip()
                        condition_id = str(plan.get("condition_id", "")).strip()
                        min_amount = _safe_int(plan.get("min_amount"), config.auto_merge_default_min_amount)
                        partition = _parse_partition(plan.get("partition"))
                        collateral_token = str(plan.get("collateral_token", "")).strip()
                        parent_collection_id = str(plan.get("parent_collection_id", "")).strip()

                        if not yes_token_id or not no_token_id:
                            continue
                        if paper_broker is None and not condition_id:
                            continue

                        yes_pos = _safe_float(positions.get(yes_token_id), 0.0)
                        no_pos = _safe_float(positions.get(no_token_id), 0.0)
                        merge_amount = int(min(yes_pos, no_pos))
                        if merge_amount < max(1, min_amount):
                            continue

                        action: Dict[str, Any] = {
                            "yes_token_id": yes_token_id,
                            "no_token_id": no_token_id,
                            "condition_id": condition_id,
                            "amount": merge_amount,
                            "partition": partition,
                        }
                        try:
                            if config.dry_run:
                                action["status"] = "dry_run"
                                print(
                                    "[DRY_RUN][MERGE] "
                                    f"yes={yes_token_id} no={no_token_id} amount={merge_amount}"
                                )
                            elif paper_broker is not None:
                                resp = await _exec_call(
                                    execution_client.merge_pair,
                                    yes_token_id,
                                    no_token_id,
                                    merge_amount,
                                )
                                action["status"] = "ok"
                                action["response"] = resp
                            else:
                                resp = await _exec_call(
                                    execution_client.merge_positions,
                                    condition_id,
                                    partition,
                                    merge_amount,
                                    collateral_token,
                                    parent_collection_id,
                                )
                                if config.merge_pending_credit_enabled:
                                    # Convert merge amount units to USDC credit (scale is configurable).
                                    pending_usdc_credit_amount = max(
                                        0.0,
                                        float(merge_amount)
                                        / max(1, int(config.merge_amount_scale)),
                                    )
                                    if pending_usdc_credit_amount > 0:
                                        pending_merge_credits.append(
                                            {
                                                "amount": pending_usdc_credit_amount,
                                                "expire_at": time.time()
                                                + max(1, config.merge_pending_credit_ttl_sec),
                                            }
                                        )
                                action["status"] = "ok"
                                action["response"] = resp
                            merge_actions.append(action)
                        except Exception as exc:
                            action["status"] = "error"
                            action["error"] = str(exc)
                            merge_actions.append(action)
                            errors += 1
                            print(f"[ERROR] auto-merge failed: {exc}")

                # 8) Persist strategy telemetry for offline analysis / parameter tuning.
                metrics.log(
                    {
                        "tick": tick_count,
                        "execution_mode": execution_mode,
                        "strategy_key": strategy.key,
                        "quote_runtime": quote_runtime_meta,
                        "market_data_source": "ws" if ws_feed else "rest",
                        "usdc_balance": usdc_balance,
                        "effective_usdc_for_sizing": effective_usdc_for_sizing,
                        "pending_usdc_credit": pending_usdc_credit,
                        "positions": positions,
                        "net_inventory": net_inventory,
                        "mids": _quantize_price_dict(mids, config.price_tick, config.price_tick_mode),
                        "spreads": _quantize_price_dict(spreads, config.price_tick, config.price_tick_mode),
                        "equity": equity,
                        "pnl": pnl,
                        "open_orders_count": len(open_orders),
                        "placed": placed,
                        "canceled": canceled,
                        "errors": errors,
                        "inventory_signals": inventory_signals,
                        "target_quotes": _quantize_quote_dict(
                            target_quotes, config.price_tick, config.price_tick_mode
                        ),
                        "final_quotes": _quantize_quote_dict(
                            final_quotes, config.price_tick, config.price_tick_mode
                        ),
                        "ofi_imbalances": ofi_imbalances,
                        "realized_volatility": realized_volatility,
                        "required_spreads": required_spreads,
                        "adaptive_spreads": adaptive_spreads,
                        "alpha_reference_momentum": alpha_reference_momentum,
                        "side_blocks": side_blocks,
                        "merge_actions": merge_actions,
                        "paper_recent_fills": paper_recent_fills,
                        "paper_bootstrap_actions": paper_bootstrap_actions,
                        "circuit_breaker_triggered": False,
                        "circuit_breaker_reasons": [],
                    }
                )
                print(
                    f"[TICK {tick_count}] mode={execution_mode} "
                    f"equity={equity:.4f} pnl={pnl:.4f} "
                    f"orders(open={len(open_orders)}, place={placed}, cancel={canceled}, err={errors})"
                )

                tick_count += 1
                if config.max_ticks > 0 and tick_count >= config.max_ticks:
                    return
                await asyncio.sleep(config.tick_interval_sec)
        finally:
            # Ensure WS task is terminated cleanly.
            if ws_feed:
                await ws_feed.stop()
