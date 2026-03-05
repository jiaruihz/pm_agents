"""Unified Telegram notification helpers across strategies."""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

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
) -> str:
    lines = [
        "[PMM LIVE REPORT]",
        f"symbol={symbol} strategy={strategy_key} mode={mode} tick={tick}",
        (
            "equity="
            f"{equity:.4f} pnl={pnl:.4f} usdc={usdc_balance:.4f} open_orders={open_orders_count}"
        ),
        (
            "fills_total="
            f"{int(fills_total)} fills_since_last_report={int(fills_since_last_report)} "
            f"placed_total={int(placed_total)} canceled_total={int(canceled_total)}"
        ),
        "positions:",
    ]
    if not positions:
        lines.append("- none")
        return "\n".join(lines)

    for token_id in sorted(positions.keys()):
        qty = _to_float(positions.get(token_id), 0.0)
        mid = _to_float(mids.get(token_id), 0.0)
        notional = qty * mid
        lines.append(
            f"- {token_id}: qty={qty:.4f}, mid={mid:.4f}, notional={notional:.4f}"
        )
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
    ) -> None:
        if not self.enabled:
            return
        now = time.time()
        if now - self._last_report_ts < self._report_interval_sec:
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
