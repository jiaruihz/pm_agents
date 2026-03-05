"""PMM 主循环引擎。

职责：
1) 拉取账户与盘口快照；
2) 计算信号、生成目标报价；
3) 对比 open orders 得到撤/挂单动作；
4) 执行风控（熔断、侧向屏蔽、自动 merge）；
5) 输出指标日志用于回放和参数调优。
"""

import asyncio
import json
import logging
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from src.domains.pmm.config import PMMConfig
from src.domains.pmm.core.anchoring import anchor_quotes_to_book as _anchor_quotes_to_book
from src.domains.pmm.core.signals import (
    depth_near_mid as _depth_near_mid,
    fair_mid as _fair_mid,
    inventory_signal as _inventory_signal,
    momentum as _momentum,
    order_flow_imbalance as _order_flow_imbalance,
    realized_vol as _realized_vol,
    required_spread as _required_spread,
    weighted_mid as _weighted_mid,
)
from src.domains.pmm.core.sizing import target_sizes as _target_sizes
from src.domains.pmm.core.strategy_base import StrategyQuoteInput
from src.domains.pmm.core.strategy_registry import StrategyRegistry
from src.platform.market_data.http_client import ToolServiceClient
from src.platform.market_data.market_ws import MarketWsFeed
from src.platform.market_data.orderbook import best_bid_ask, mid_price, spread as orderbook_spread
from src.platform.market_data.parsers import (
    mid_from_market as _mid_from_market,
    parse_account_state as _parse_account_state,
    parse_partition as _parse_partition,
    pending_credit_total as _pending_credit_total,
)
from src.domains.pmm.execution.order_manager import OrderManager
from src.domains.pmm.execution.paper_broker import PaperBroker
from src.domains.pmm.execution.live_broker import LiveBroker
from src.domains.pmm.engine.context_builder import build_history_context, build_token_context
from src.domains.pmm.engine.telegram_notifier import PMMTelegramNotifier
from src.domains.pmm.risk.safety_guard import SafetyGuard
from src.domains.pmm.strategies.multi_level_v1 import MultiLevelV1Strategy
from src.domains.pmm.strategies.single_level_v1 import SingleLevelV1Strategy
from src.domains.pmm.strategies.smart_money_follow_v1 import SmartMoneyFollowV1Strategy
from src.domains.pmm.strategies.weather_theta_no_v1 import WeatherThetaNoV1Strategy
from src.domains.pmm.utils.converters import best_level as _best_level, normalize_levels as _normalize_levels, to_float as _safe_float, to_int as _safe_int
from src.domains.pmm.utils.metrics import MetricsLogger
from src.domains.pmm.utils.quantize import (
    quantize_quote_dict as _quantize_quote_dict,
    quantize_quote_pair as _quantize_quote_pair,
    quantize_price_dict as _quantize_price_dict,
    quantize_to_tick,
)
from src.platform.strategy_runtime import StrategyRuntimeStore
from src.strategies.registry import load_strategy_rows

logger = logging.getLogger("pmm.tick_engine")


def _log_event(level: int, event: str, **fields: Any) -> None:
    payload = {"event": event, **fields}
    logger.log(level, json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def _pending_open_exposure(open_orders: List[Dict[str, Any]], token_id: str) -> Tuple[float, float]:
    """统计指定 token 在挂单中的买卖侧潜在成交量。"""
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
        size = _safe_float(
            raw.get("remaining_size")
            or raw.get("size")
            or raw.get("original_size")
            or raw.get("amount")
            or 0.0
        )
        if size <= 0:
            continue
        if side == "BUY":
            buy_qty += size
        elif side == "SELL":
            sell_qty += size
    return buy_qty, sell_qty


def _order_id(raw: Dict[str, Any]) -> str:
    """统一提取订单 ID，兼容多种字段名。"""
    return str(raw.get("id") or raw.get("orderID") or raw.get("order_id") or "")


def _reconcile_pending_orders(
    pending_orders: List[Dict[str, Any]],
    open_orders: List[Dict[str, Any]],
    ttl_sec: float,
    now_ts: float,
) -> List[Dict[str, Any]]:
    """对 live 模式下的“在途订单”做保活与过期清理。"""
    if not pending_orders:
        return []
    open_ids = {_order_id(x) for x in open_orders if _order_id(x)}
    keep: List[Dict[str, Any]] = []
    for item in pending_orders:
        oid = _order_id(item)
        if oid and oid in open_ids:
            continue
        created_at = _safe_float(item.get("_pending_created_at"), 0.0)
        if created_at > 0 and (now_ts - created_at) > max(0.1, ttl_sec):
            continue
        keep.append(item)
    return keep


def _drop_pending_by_ids(pending_orders: List[Dict[str, Any]], order_ids: List[str]) -> List[Dict[str, Any]]:
    """从 pending 列表中移除已撤单/已替换的订单。"""
    if not pending_orders or not order_ids:
        return pending_orders
    remove = {str(x) for x in order_ids if str(x)}
    out: List[Dict[str, Any]] = []
    for item in pending_orders:
        oid = _order_id(item)
        if oid and oid in remove:
            continue
        out.append(item)
    return out




async def tick_loop(config: PMMConfig) -> None:
    """执行 PMM 连续 tick 主循环。"""
    if not config.market.token_ids:
        _log_event(logging.ERROR, "config_missing_token_ids")
        return

    # 1) 构建 token 上下文（主交易 token + 可选参考 token）。
    token_ctx = build_token_context(config)
    token_ids = token_ctx.token_ids
    reference_token_ids = token_ctx.reference_token_ids
    all_token_ids = token_ctx.all_token_ids

    # 2) 初始化策略注册表、订单管理器和指标记录器。
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
    strategy_registry.register(
        SmartMoneyFollowV1Strategy(
            anchor_quotes_fn=_anchor_quotes_to_book,
            quantize_pair_fn=_quantize_quote_pair,
            target_sizes_fn=_target_sizes,
        )
    )
    strategy_registry.register(
        WeatherThetaNoV1Strategy(
            quantize_pair_fn=_quantize_quote_pair,
        )
    )
    strategy = strategy_registry.get(config.strategy_key)
    if strategy is None:
        available = ", ".join(strategy_registry.available_keys())
        raise ValueError(
            f"Unsupported strategy_key={config.strategy_key}. "
            f"Available: {available}"
        )
    _log_event(logging.INFO, "strategy_selected", strategy_key=strategy.key)
    baseline_equity: Optional[float] = None
    history_ctx = build_history_context(config, all_token_ids)
    mid_history = history_ctx.mid_history

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
            _log_event(
                logging.WARNING,
                "quote_placeholder_mode_active",
                strategy_key=strategy.key,
                quote_runtime=quote_runtime_meta,
            )
        if strategy.key == "multi_level_v1":
            _log_event(
                logging.INFO,
                "quote_multi_level_active",
                levels=quote_runtime_meta.get("quote_levels_effective"),
            )
        paper_broker: Optional[PaperBroker] = None
        execution_client: Any
        paper_bootstrap_actions: List[Dict[str, Any]] = []
        use_raw_client_retry = False
        # 3) 执行层路由：
        # - live: 真实调用下单接口
        # - paper: 本地撮合模拟（仍使用真实盘口数据）
        if execution_mode == "paper":
            paper_broker = PaperBroker(
                token_ids=token_ids,
                initial_usdc=config.paper_initial_usdc,
                initial_positions=config.paper_initial_positions,
                fill_model=config.paper_fill_model,
                fill_epsilon=config.paper_fill_epsilon,
                queue_share=config.paper_queue_share,
                maker_fee_bps=config.paper_maker_fee_bps,
                taker_fee_bps=config.paper_taker_fee_bps,
                min_fill_age_ticks=config.paper_min_fill_age_ticks,
                cancel_delay_ticks=config.paper_cancel_delay_ticks,
                require_trade_flow_for_at_bbo=config.paper_require_trade_flow_for_at_bbo,
                disable_at_bbo_in_conservative=config.paper_disable_at_bbo_in_conservative,
                conservative_bbo_share_multiplier=config.paper_conservative_bbo_share_multiplier,
            )
            execution_client = paper_broker
            _log_event(
                logging.INFO,
                "paper_mode_enabled",
                initial_usdc=config.paper_initial_usdc,
                fill_model=config.paper_fill_model,
            )
            # 可选启动注资：先做一次 split，避免初始无仓导致 SELL 侧无法挂单。
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
                    _log_event(
                        logging.INFO,
                        "paper_bootstrap_split_ok",
                        split_usdc=config.paper_bootstrap_split_usdc,
                        yes_token_id=yes_token_id,
                        no_token_id=no_token_id,
                    )
                except Exception as exc:
                    paper_bootstrap_actions.append(
                        {
                            "status": "error",
                            "error": str(exc),
                            "split_usdc": float(config.paper_bootstrap_split_usdc),
                        }
                    )
                    _log_event(
                        logging.ERROR,
                        "paper_bootstrap_split_failed",
                        error=str(exc),
                    )
        elif execution_mode == "live":
            # live 模式统一走 LiveBroker，便于接入安全检查和未来扩展。
            safety_guard = SafetyGuard(
                allowed_tokens=set(token_ids),
                max_order_value=max(1.0, float(config.guard_max_order_value)),
                max_position=max(1.0, float(config.max_position)),
                max_long_position=max(1.0, float(config.guard_max_long_position)),
                max_short_position=max(1.0, float(config.guard_max_short_position)),
                max_buy_order_value=max(1.0, float(config.guard_max_buy_order_value)),
                max_sell_order_value=max(1.0, float(config.guard_max_sell_order_value)),
                max_daily_loss=max(0.0, float(config.guard_max_daily_loss)),
                price_floor=float(config.guard_price_floor),
                price_ceiling=float(config.guard_price_ceiling),
            )
            execution_client = LiveBroker(
                http_client=client,
                safety_guard=safety_guard,
                dry_run=config.dry_run,
            )
            _log_event(
                logging.INFO,
                "live_mode_enabled",
                dry_run=config.dry_run,
                guard_tokens=len(token_ids),
                max_order_value=config.guard_max_order_value,
                max_daily_loss=config.guard_max_daily_loss,
            )
        else:
            execution_client = client
            use_raw_client_retry = True
            _log_event(
                logging.WARNING,
                "unknown_execution_mode_fallback",
                execution_mode=execution_mode,
            )
        notifier = PMMTelegramNotifier(
            config=config,
            execution_mode=execution_mode,
            strategy_key=strategy.key,
        )
        await notifier.start()
        instance_id = config.instance_id or f"{strategy.key}-{execution_mode}-{os.getpid()}"
        instance_label = config.instance_label or instance_id
        instance_hb_interval_sec = max(1, int(config.instance_heartbeat_sec))
        instance_snapshot_interval_sec = max(1, int(config.instance_snapshot_interval_sec))
        next_instance_hb_ts = 0.0
        instance_log_file = os.getenv("PMM_LOG_FILE", "").strip()
        instance_store: Optional[StrategyRuntimeStore] = None
        try:
            instance_store = StrategyRuntimeStore(config.strategy_runtime_db_path)
            instance_store.ensure_builtin_strategies(load_strategy_rows())
            run_params_payload = {
                "strategy_params": config.strategy_params,
                "quote_runtime": quote_runtime_meta,
                "tick_interval_sec": float(config.tick_interval_sec),
                "max_ticks": int(config.max_ticks),
                "min_profitability_spread": float(config.min_profitability_spread),
                "price_tick": float(config.price_tick),
                "base_size": float(config.base_size),
                "max_position": float(config.max_position),
                "paper_fill_model": str(config.paper_fill_model),
            }
            runtime_paths_payload = {
                "cwd": os.getcwd(),
                "log_file": instance_log_file,
                "metrics_path": str(config.metrics_path),
                "strategy_runtime_db_path": str(config.strategy_runtime_db_path),
            }
            account_id = os.getenv("PM_ACCOUNT_ID", "").strip()
            wallet_address = os.getenv("PM_ADDRESS", "").strip()
            if not account_id:
                account_id = wallet_address
            instance_store.upsert_start(
                instance_id=instance_id,
                strategy_key=strategy.key,
                label=instance_label,
                execution_mode=execution_mode,
                market_data_source="ws" if ws_feed else "rest",
                account_id=account_id,
                wallet_address=wallet_address,
                token_ids=token_ids,
                max_position=float(config.max_position),
                telegram_enabled=bool(config.telegram_enabled),
                pid=os.getpid(),
                log_file=instance_log_file,
                metrics_path=str(config.metrics_path),
                cwd=os.getcwd(),
                run_params=run_params_payload,
                runtime_paths=runtime_paths_payload,
            )
            _log_event(
                logging.INFO,
                "instance_registered",
                instance_id=instance_id,
                db_path=config.strategy_runtime_db_path,
            )
        except Exception as exc:
            instance_store = None
            _log_event(
                logging.WARNING,
                "instance_register_failed",
                error=str(exc),
                db_path=config.strategy_runtime_db_path,
            )

        async def _exec_call(method, *args, **kwargs):
            # 统一执行入口：broker 路径直接 await；raw client 路径走 retry。
            if use_raw_client_retry:
                return await client.retry(method, *args, **kwargs)
            return await method(*args, **kwargs)

        if ws_feed:
            await ws_feed.start()
            _log_event(logging.INFO, "ws_feed_started", ws_url=config.ws_market_url)

        pending_merge_credits: List[Dict[str, float]] = []
        pending_orders: List[Dict[str, Any]] = []
        total_placed = 0
        total_canceled = 0
        total_errors = 0
        total_fills = 0
        run_error = ""
        try:
            tick_count = 0
            while True:
                # A) 拉取账户快照（余额、挂单、持仓）。
                balance_raw, open_orders_raw, positions_raw = await asyncio.gather(
                    _exec_call(execution_client.get_balance),
                    _exec_call(execution_client.get_orders),
                    _exec_call(execution_client.get_positions, token_ids),
                )

                orderbook_results: Dict[str, Dict[str, Any]] = {}
                if ws_feed:
                    # B1) WS 优先；若缺失/过期则回退 REST 拉取。
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
                    # B2) 纯 REST 路径。
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
                if execution_mode == "live":
                    # live 模式维护在途订单，避免短时状态不一致。
                    pending_orders = _reconcile_pending_orders(
                        pending_orders=pending_orders,
                        open_orders=open_orders,
                        ttl_sec=config.inflight_order_ttl_sec,
                        now_ts=time.time(),
                    )
                pending_usdc_credit = 0.0
                if (
                    paper_broker is None
                    and config.merge_pending_credit_enabled
                    and pending_merge_credits
                ):
                    # merge 在途 credit 仅用于 live，降低链上确认期间的空转。
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
                        # 盘口稀疏或异常时，回退 market 接口估算 mid。
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
                    # paper 模式下用同一份盘口快照驱动本地撮合。
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
                fills_this_tick = len(paper_recent_fills)
                total_fills += fills_this_tick

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
                    # C) 熔断：mid 偏离短窗均值超过阈值则触发。
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
                    # D) 跨市场动量：作为方向性防御信号。
                    ref_momentum_values: List[float] = []
                    for ref_token_id in reference_token_ids:
                        value = _momentum(mid_history[ref_token_id], min_points=config.alpha_min_points)
                        if value != 0.0:
                            ref_momentum_values.append(value)
                    if ref_momentum_values:
                        alpha_reference_momentum = sum(ref_momentum_values) / len(ref_momentum_values)

                if circuit_breaker_triggered:
                    # 熔断触发后先清理敞口，再按配置决定 halt 或继续。
                    reason_lines: List[str] = []
                    for reason in circuit_breaker_reasons[:4]:
                        reason_lines.append(
                            (
                                f"{reason.get('token_id')} "
                                f"dev={_safe_float(reason.get('deviation'), 0.0):.4f} "
                                f"mid={_safe_float(reason.get('mid'), 0.0):.4f}"
                            )
                        )
                    await notifier.send_alert(
                        alert_key="circuit_breaker",
                        event="circuit_breaker_triggered",
                        tick=tick_count,
                        pnl=pnl,
                        detail="; ".join(reason_lines) or "unknown",
                    )
                    try:
                        if config.dry_run:
                            _log_event(
                                logging.WARNING,
                                "dry_run_circuit_breaker_triggered",
                                reasons=circuit_breaker_reasons,
                            )
                        else:
                            await _exec_call(execution_client.cancel_all_orders)
                            canceled = len(open_orders)
                    except Exception as exc:
                        errors += 1
                        _log_event(
                            logging.ERROR,
                            "circuit_breaker_cancel_all_failed",
                            error=str(exc),
                        )
                        await notifier.send_alert(
                            alert_key="circuit_breaker_cancel_all_failed",
                            event="circuit_breaker_cancel_all_failed",
                            tick=tick_count,
                            pnl=pnl,
                            detail=str(exc),
                        )

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
                        _log_event(logging.WARNING, "circuit_breaker_halted")
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

                    # E) 信号栈：库存 -> 波动率 -> 最小价差 -> OFI/动量侧向屏蔽。
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
                    exposure_orders = local_orders + pending_orders if execution_mode == "live" else local_orders
                    open_buy_qty, open_sell_qty = _pending_open_exposure(exposure_orders, token_id)
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
                            open_buy_qty=open_buy_qty,
                            open_sell_qty=open_sell_qty,
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
                                        _log_event(
                                            logging.INFO,
                                            "dry_run_block_cancel",
                                            token_id=token_id,
                                            side=side,
                                            order_ids=blocked_ids,
                                        )
                                    else:
                                        if len(blocked_ids) == 1:
                                            await _exec_call(execution_client.cancel_order, blocked_ids[0])
                                        else:
                                            await _exec_call(execution_client.cancel_orders, blocked_ids)
                                    canceled += len(blocked_ids)
                                    total_canceled += len(blocked_ids)
                                    cancel_set = set(blocked_ids)
                                    local_orders = [
                                        x
                                        for x in local_orders
                                        if str(x.get("id") or x.get("orderID") or x.get("order_id"))
                                        not in cancel_set
                                    ]
                                    if execution_mode == "live":
                                        # live 维护 pending 一致性，避免后续重复处理。
                                        pending_orders = _drop_pending_by_ids(pending_orders, blocked_ids)
                                except Exception as exc:
                                    errors += 1
                                    total_errors += 1
                                    _log_event(
                                        logging.ERROR,
                                        "blocked_side_cancel_failed",
                                        token_id=token_id,
                                        side=side,
                                        error=str(exc),
                                    )
                                    await notifier.send_alert(
                                        alert_key=f"blocked_side_cancel_failed:{token_id}:{side}",
                                        event="blocked_side_cancel_failed",
                                        tick=tick_count,
                                        pnl=pnl,
                                        detail=f"token_id={token_id} side={side} err={exc}",
                                    )
                            continue

                        decision = order_mgr.diff_multi(
                            open_orders=local_orders,
                            pending_orders=pending_orders if execution_mode == "live" else None,
                            token_id=token_id,
                            side=side,
                            targets=side_targets.get(side, []),
                        )
                        if decision.cancel_ids:
                            try:
                                if config.dry_run:
                                    _log_event(
                                        logging.INFO,
                                        "dry_run_cancel",
                                        token_id=token_id,
                                        side=side,
                                        order_ids=decision.cancel_ids,
                                        reason=decision.reason,
                                    )
                                else:
                                    if len(decision.cancel_ids) == 1:
                                        await _exec_call(execution_client.cancel_order, decision.cancel_ids[0])
                                    else:
                                        await _exec_call(execution_client.cancel_orders, decision.cancel_ids)
                                canceled += len(decision.cancel_ids)
                                total_canceled += len(decision.cancel_ids)
                                cancel_set = set(decision.cancel_ids)
                                local_orders = [
                                    x
                                    for x in local_orders
                                    if str(x.get("id") or x.get("orderID") or x.get("order_id"))
                                    not in cancel_set
                                ]
                                if execution_mode == "live":
                                    pending_orders = _drop_pending_by_ids(pending_orders, decision.cancel_ids)
                            except Exception as exc:
                                errors += 1
                                total_errors += 1
                                _log_event(
                                    logging.ERROR,
                                    "cancel_failed",
                                    token_id=token_id,
                                    side=side,
                                    error=str(exc),
                                )
                                await notifier.send_alert(
                                    alert_key=f"cancel_failed:{token_id}:{side}",
                                    event="cancel_failed",
                                    tick=tick_count,
                                    pnl=pnl,
                                    detail=f"token_id={token_id} side={side} err={exc}",
                                )

                        for t in decision.create_targets:
                            price = _safe_float(t.get("price"), 0.0)
                            size = _safe_float(t.get("size"), 0.0)
                            level = _safe_int(t.get("level"), 0)
                            if size < config.min_size or price <= 0:
                                continue
                            try:
                                if config.dry_run:
                                    _log_event(
                                        logging.INFO,
                                        "dry_run_place",
                                        token_id=token_id,
                                        side=side,
                                        quote_level=level,
                                        price=round(price, 6),
                                        size=round(size, 6),
                                        reason=decision.reason,
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
                                    if execution_mode == "live":
                                        placed_order = await _exec_call(
                                            execution_client.place_limit_order,
                                            token_id,
                                            price,
                                            size,
                                            side,
                                            current_position=position,
                                        )
                                    else:
                                        placed_order = await _exec_call(
                                            execution_client.place_limit_order,
                                            token_id,
                                            price,
                                            size,
                                            side,
                                        )
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
                                if execution_mode == "live":
                                    # 新挂单先记入 pending，等待交易所状态回传。
                                    pending_item = {
                                        "id": placed_order.get("id"),
                                        "asset_id": token_id,
                                        "side": side,
                                        "price": price,
                                        "size": size,
                                        "level": level,
                                        "_pending_created_at": time.time(),
                                    }
                                    pending_orders.append(pending_item)
                            except Exception as exc:
                                errors += 1
                                total_errors += 1
                                _log_event(
                                    logging.ERROR,
                                    "place_failed",
                                    token_id=token_id,
                                    side=side,
                                    quote_level=level,
                                    price=round(price, 6),
                                    size=round(size, 6),
                                    error=str(exc),
                                )
                                await notifier.send_alert(
                                    alert_key=f"place_failed:{token_id}:{side}",
                                    event="place_failed",
                                    tick=tick_count,
                                    pnl=pnl,
                                    detail=(
                                        f"token_id={token_id} side={side} "
                                        f"level={level} price={round(price, 6)} size={round(size, 6)} err={exc}"
                                    ),
                                )

                if (
                    config.auto_merge_enabled
                    and config.auto_merge_plans
                    and config.auto_merge_every_ticks > 0
                    and (tick_count + 1) % config.auto_merge_every_ticks == 0
                ):
                    # F) 周期性 merge：回收成对库存，提升资金周转。
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
                                _log_event(
                                    logging.INFO,
                                    "dry_run_merge",
                                    yes_token_id=yes_token_id,
                                    no_token_id=no_token_id,
                                    amount=merge_amount,
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
                                    # 将 merge 数量换算为 USDC 在途 credit（可配置 scale）。
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
                            total_errors += 1
                            _log_event(
                                logging.ERROR,
                                "auto_merge_failed",
                                yes_token_id=yes_token_id,
                                no_token_id=no_token_id,
                                amount=merge_amount,
                                error=str(exc),
                            )
                            await notifier.send_alert(
                                alert_key=f"auto_merge_failed:{yes_token_id}:{no_token_id}",
                                event="auto_merge_failed",
                                tick=tick_count,
                                pnl=pnl,
                                detail=(
                                    f"yes={yes_token_id} no={no_token_id} "
                                    f"amount={merge_amount} err={exc}"
                                ),
                            )

                # G) 落盘指标，供离线分析与参数调优。
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
                        "pending_orders_count": len(pending_orders) if execution_mode == "live" else 0,
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
                _log_event(
                    logging.INFO,
                    "tick_summary",
                    tick=tick_count,
                    execution_mode=execution_mode,
                    equity=round(equity, 6),
                    pnl=round(pnl, 6),
                    open_orders=len(open_orders),
                    placed=placed,
                    canceled=canceled,
                    errors=errors,
                )
                await notifier.maybe_send_periodic_report(
                    tick=tick_count,
                    pnl=pnl,
                    equity=equity,
                    usdc_balance=usdc_balance,
                    positions=positions,
                    mids=mids,
                    open_orders_count=len(open_orders),
                    fills_total=total_fills,
                    placed_total=total_placed,
                    canceled_total=total_canceled,
                )
                now_ts = time.time()
                if instance_store and now_ts >= next_instance_hb_ts:
                    try:
                        instance_store.heartbeat(
                            instance_id=instance_id,
                            tick=tick_count,
                            pnl=pnl,
                            equity=equity,
                            usdc_balance=usdc_balance,
                            open_orders=len(open_orders),
                            fills_total=total_fills,
                            placed_total=total_placed,
                            canceled_total=total_canceled,
                            errors_total=total_errors,
                            state={
                                "execution_mode": execution_mode,
                                "strategy_key": strategy.key,
                                "market_data_source": "ws" if ws_feed else "rest",
                                "positions": positions,
                            },
                            snapshot_interval_sec=instance_snapshot_interval_sec,
                        )
                    except Exception as exc:
                        _log_event(logging.WARNING, "instance_heartbeat_failed", error=str(exc))
                    next_instance_hb_ts = now_ts + float(instance_hb_interval_sec)

                tick_count += 1
                if config.max_ticks > 0 and tick_count >= config.max_ticks:
                    return
                await asyncio.sleep(config.tick_interval_sec)
        except Exception as exc:
            run_error = str(exc)
            raise
        finally:
            # 收尾：确保 WS 连接和 metrics writer 都被干净关闭。
            await notifier.aclose()
            if ws_feed:
                await ws_feed.stop()
            if instance_store is not None:
                try:
                    instance_store.mark_stopped(
                        instance_id=instance_id,
                        status="error" if run_error else "stopped",
                        notes=run_error[:800] if run_error else "",
                    )
                except Exception as exc:
                    _log_event(logging.WARNING, "instance_stop_update_failed", error=str(exc))
                instance_store.close()
            metrics.close()


class TickEngine:
    def __init__(self, config: PMMConfig) -> None:
        self.config = config

    async def run(self) -> None:
        await tick_loop(self.config)
