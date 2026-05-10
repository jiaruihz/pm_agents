"""Unified Telegram notification helpers across strategies."""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from src.strategies.pmm.config import PMMConfig
from src.strategies.rule_lawyer.config import get_settings
from src.platform.clients import TelegramClient

logger = logging.getLogger("pmm.notifier")


async def send_telegram_message(
    text: str,
    chat_id: Optional[str] = None,
    parse_mode: Optional[str] = None,
    disable_notification: bool = False,
) -> Dict[str, Any]:
    settings = get_settings()
    bot_token = settings.telegram_bot_token
    target_chat_id = chat_id or settings.telegram_chat_id
    if not bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not configured")
    if not target_chat_id:
        raise ValueError("TELEGRAM_CHAT_ID is not configured and chat_id was not provided")

    async with TelegramClient(bot_token=bot_token, api_base_url=settings.telegram_api_base_url) as client:
        return await client.send_message(
            chat_id=target_chat_id,
            text=text,
            parse_mode=parse_mode,
            disable_notification=disable_notification,
        )


def send_telegram_message_sync(
    text: str,
    chat_id: Optional[str] = None,
    parse_mode: Optional[str] = None,
    disable_notification: bool = False,
) -> Dict[str, Any]:
    return asyncio.run(
        send_telegram_message(
            text=text,
            chat_id=chat_id,
            parse_mode=parse_mode,
            disable_notification=disable_notification,
        )
    )


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _short_id(value: str) -> str:
    raw = str(value or "").strip()
    if len(raw) <= 14:
        return raw
    return f"{raw[:6]}...{raw[-4:]}"


def build_live_report_message(
    *,
    symbol: str,
    strategy_key: str,
    mode: str = "live",
    tick: int,
    pnl: float,
    equity: float,
    usdc_balance: float,
    positions: Dict[str, float],
    mids: Dict[str, float],
    open_orders_count: int,
    fills_total: int = 0,
    fills_since_last_report: int = 0,
    placed_total: int = 0,
    canceled_total: int = 0,
    pending_items: Optional[List[Dict[str, Any]]] = None,
    position_items: Optional[List[Dict[str, Any]]] = None,
    weather_watch_items: Optional[List[Dict[str, Any]]] = None,
) -> str:
    lines = [
        "【天气策略小时汇报】",
        "",
        "账户概况",
        f"- 当前权益：{equity:.2f} USDC",
        f"- 浮动盈亏：{pnl:+.2f} USDC",
        f"- 可用余额：{usdc_balance:.2f} USDC",
        f"- 未成交挂单：{int(open_orders_count)} 笔",
        f"- 累计成交导入：{int(fills_total)} 笔，本周期新增：{int(fills_since_last_report)} 笔",
        f"- 累计挂单记录：{int(placed_total)} 笔，累计撤单：{int(canceled_total)} 笔",
        "",
        "当前持仓",
    ]
    if not positions:
        lines.append("- 无")
    elif position_items:
        for item in position_items:
            token_id = str(item.get("token_id") or "")
            label = str(item.get("title") or item.get("slug") or "").strip() or _short_id(token_id)
            outcome = str(item.get("outcome") or "No").strip() or "No"
            qty = _to_float(item.get("qty"), 0.0)
            mid = _to_float(item.get("cur_price"), _to_float(mids.get(token_id), 0.0))
            notional = _to_float(item.get("current_value"), qty * mid)
            lines.append(f"- {label} [{outcome}]：{qty:.4f} 股，现价 {mid:.3f}，市值 {notional:.2f}")
    else:
        for token_id in sorted(positions.keys()):
            qty = _to_float(positions.get(token_id), 0.0)
            mid = _to_float(mids.get(token_id), 0.0)
            notional = qty * mid
            lines.append(f"- {_short_id(token_id)}：{qty:.4f} 股，现价 {mid:.3f}，市值 {notional:.2f}")

    if pending_items:
        lines.extend(["", "当前挂单"])
        for item in pending_items:
            token_id = str(item.get("token_id") or "")
            title = str(item.get("title") or item.get("slug") or "").strip()
            label = title or _short_id(token_id)
            outcome = str(item.get("outcome") or "No").strip() or "No"
            pending_qty = _to_float(item.get("pending_qty"), 0.0)
            best_bid = _to_float(item.get("best_bid"), 0.0)
            best_ask = _to_float(item.get("best_ask"), 0.0)
            levels = item.get("levels") or []
            levels_text = " / ".join(str(x) for x in levels[:4]) if isinstance(levels, list) else ""
            market_text = f"{best_bid:.3f} / {best_ask:.3f}" if best_bid > 0 or best_ask > 0 else "-"
            lines.append(
                f"- {label} [{outcome}]：共 {pending_qty:.4f} 股，挂价 {levels_text or '-'}，市场价 {market_text}"
            )

    if weather_watch_items:
        lines.extend(["", "天气观察"])
        for item in weather_watch_items[:8]:
            label = str(item.get("market_title") or item.get("token_id") or "").strip()
            latest = dict(item.get("latest_forecast") or {})
            action = dict(item.get("action_suggestion") or {})
            drift = dict(item.get("drift_alert") or {})
            unit = str(latest.get("market_unit") or "")
            drift_text = "已触发漂移提醒" if drift.get("triggered") else "暂未触发漂移"
            lines.append(
                f"- {label}：最新最高温 {_to_float(latest.get('max_temp_market_unit'), 0.0):.1f}{unit}，"
                f"主区间 {latest.get('main_range_low')}..{latest.get('main_range_high')}{unit}，"
                f"方向 {latest.get('forecast_direction')}，建议 {action.get('action')}，{drift_text}"
            )

    lines.extend(["", f"运行信息：mode={mode} tick={tick} symbol={symbol} strategy={strategy_key}"])
    return "\n".join(lines)


def build_alert_message(
    *,
    symbol: str,
    strategy_key: str,
    event: str,
    tick: Optional[int],
    detail: str,
    pnl: Optional[float] = None,
) -> str:
    if str(event).startswith("weather_"):
        lines = [
            "【天气止损提醒】",
            "",
            detail,
        ]
        if pnl is not None:
            lines.append(f"当前策略浮盈亏：{pnl:+.2f} USDC")
        lines.extend(["", f"运行信息：tick={tick if tick is not None else '-'} symbol={symbol} strategy={strategy_key}"])
        return "\n".join(lines)

    lines = [
        "[PMM ALERT]",
        f"symbol={symbol} strategy={strategy_key} event={event}",
    ]
    if tick is not None:
        lines.append(f"tick={tick}")
    if pnl is not None:
        lines.append(f"pnl={pnl:.4f}")
    lines.append(f"detail={detail}")
    return "\n".join(lines)


def build_order_message(
    *,
    symbol: str,
    strategy_key: str,
    event: str,
    token_id: str,
    side: str,
    price: float,
    size: float,
    order_id: str = "",
    detail: str = "",
    market_label: str = "",
    outcome: str = "",
) -> str:
    lines = [
        "[PMM ORDER]",
        f"symbol={symbol} strategy={strategy_key} event={event}",
    ]
    market_text = market_label.strip() or _short_id(token_id)
    outcome_text = outcome.strip()
    lines.append(f"market={market_text}")
    if outcome_text:
        lines.append(f"outcome={outcome_text}")
    lines.append(f"side={side} price={price:.6f} size={size:.6f}")
    if order_id:
        lines.append(f"order_id={order_id}")
    if detail:
        lines.append(f"detail={detail}")
    return "\n".join(lines)


class PMMTelegramNotifier:
    """Send startup, alert and periodic live reports to Telegram."""

    def __init__(self, config: PMMConfig, execution_mode: str, strategy_key: str) -> None:
        self._enabled = bool(config.telegram_enabled)
        self._symbol = config.market.symbol
        self._strategy_key = strategy_key
        self._is_live = execution_mode.lower().strip() == "live"
        self._chat_id = (config.telegram_chat_id or "").strip()
        self._bot_token = (config.telegram_bot_token or "").strip()
        self._api_base_url = config.telegram_api_base_url
        self._send_startup = bool(config.telegram_send_startup)
        self._report_interval_sec = max(60, int(config.telegram_report_interval_sec))
        self._alert_cooldown_sec = max(0, int(config.telegram_alert_cooldown_sec))
        self._client: Optional[TelegramClient] = None
        self._last_report_ts: float = 0.0
        self._last_report_fills_total: int = 0
        self._last_alert_by_key: Dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    async def start(self) -> None:
        if not self._enabled:
            return
        if not self._bot_token or not self._chat_id:
            logger.warning(
                "telegram notifier disabled: missing PMM_TELEGRAM_BOT_TOKEN or PMM_TELEGRAM_CHAT_ID"
            )
            self._enabled = False
            return

        try:
            self._client = TelegramClient(
                bot_token=self._bot_token,
                api_base_url=self._api_base_url,
                timeout_seconds=3.0,
            )
        except Exception as exc:
            logger.warning("telegram notifier init failed: %s", exc)
            self._enabled = False
            return

        self._last_report_ts = time.time()
        self._last_report_fills_total = 0
        if self._send_startup:
            mode = "live" if self._is_live else "paper"
            await self._send_text(
                (
                    "[PMM STARTED]\n"
                    f"symbol={self._symbol} strategy={self._strategy_key} mode={mode}"
                ),
                disable_notification=True,
            )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def maybe_send_periodic_report(
        self,
        *,
        tick: int,
        pnl: float,
        equity: float,
        usdc_balance: float,
        positions: Dict[str, float],
        mids: Dict[str, float],
        open_orders_count: int,
        fills_total: int = 0,
        placed_total: int = 0,
        canceled_total: int = 0,
        pending_items: Optional[List[Dict[str, Any]]] = None,
        position_items: Optional[List[Dict[str, Any]]] = None,
        weather_watch_items: Optional[List[Dict[str, Any]]] = None,
        force: bool = False,
    ) -> None:
        if not self.enabled:
            return
        now = time.time()
        if (not force) and now - self._last_report_ts < self._report_interval_sec:
            return
        fills_since_last_report = max(0, int(fills_total) - int(self._last_report_fills_total))
        text = build_live_report_message(
            symbol=self._symbol,
            strategy_key=self._strategy_key,
            mode="live" if self._is_live else "paper",
            tick=tick,
            pnl=pnl,
            equity=equity,
            usdc_balance=usdc_balance,
            positions=positions,
            mids=mids,
            open_orders_count=open_orders_count,
            fills_total=fills_total,
            fills_since_last_report=fills_since_last_report,
            placed_total=placed_total,
            canceled_total=canceled_total,
            pending_items=pending_items,
            position_items=position_items,
            weather_watch_items=weather_watch_items,
        )
        self._last_report_ts = now
        self._last_report_fills_total = int(fills_total)
        await self._send_text(text, disable_notification=True)

    async def send_alert(
        self,
        *,
        alert_key: str,
        event: str,
        detail: str,
        tick: Optional[int] = None,
        pnl: Optional[float] = None,
    ) -> None:
        if not self.enabled:
            return
        now = time.time()
        last = self._last_alert_by_key.get(alert_key, 0.0)
        if now - last < self._alert_cooldown_sec:
            return
        self._last_alert_by_key[alert_key] = now
        text = build_alert_message(
            symbol=self._symbol,
            strategy_key=self._strategy_key,
            event=event,
            tick=tick,
            detail=detail,
            pnl=pnl,
        )
        await self._send_text(text, disable_notification=False)

    async def send_order_update(
        self,
        *,
        event: str,
        token_id: str,
        side: str,
        price: float,
        size: float,
        order_id: str = "",
        detail: str = "",
        market_label: str = "",
        outcome: str = "",
        disable_notification: bool = False,
    ) -> None:
        if not self.enabled:
            return
        text = build_order_message(
            symbol=self._symbol,
            strategy_key=self._strategy_key,
            event=event,
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            order_id=order_id,
            detail=detail,
            market_label=market_label,
            outcome=outcome,
        )
        await self._send_text(text, disable_notification=disable_notification)

    async def _send_text(self, text: str, disable_notification: bool) -> bool:
        if self._client is None:
            return False
        try:
            await self._client.send_message(
                chat_id=self._chat_id,
                text=text,
                disable_notification=disable_notification,
            )
            return True
        except Exception as exc:
            logger.warning("telegram notifier send failed: %s", exc)
            return False
