from typing import Any, Dict, List

import pytest

from src.strategies.pmm.config import PMMConfig
from src.platform.notification.telegram import (
    PMMTelegramNotifier,
    build_alert_message,
    build_live_report_message,
    build_order_message,
)


class _DummyTelegramClient:
    def __init__(self, *args, **kwargs) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def send_message(
        self,
        chat_id: str,
        text: str,
        parse_mode=None,
        disable_notification: bool = False,
    ) -> Dict[str, Any]:
        self.calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_notification": disable_notification,
            }
        )
        return {"ok": True}

    async def aclose(self) -> None:
        return None


def test_build_live_report_message_contains_pnl_and_positions():
    msg = build_live_report_message(
        symbol="TEST",
        strategy_key="single_level_v1",
        tick=42,
        pnl=12.34,
        equity=1012.34,
        usdc_balance=800.0,
        positions={"t2": -2.0, "t1": 3.0},
        mids={"t1": 0.51, "t2": 0.49},
        open_orders_count=7,
    )
    assert "【天气策略小时汇报】" in msg
    assert "strategy=single_level_v1" in msg
    assert "浮动盈亏：+12.34 USDC" in msg
    assert "- t1：3.0000 股" in msg
    assert "- t2：-2.0000 股" in msg


def test_build_alert_message_contains_event_and_detail():
    msg = build_alert_message(
        symbol="TEST",
        strategy_key="multi_level_v1",
        event="place_failed",
        tick=11,
        detail="boom",
        pnl=-1.23,
    )
    assert "[PMM ALERT]" in msg
    assert "event=place_failed" in msg
    assert "tick=11" in msg
    assert "pnl=-1.2300" in msg
    assert "detail=boom" in msg


def test_build_order_message_contains_order_payload():
    msg = build_order_message(
        symbol="TEST",
        strategy_key="weather_edge_v1",
        event="order_placed",
        token_id="tid-1",
        side="BUY",
        price=0.95,
        size=20.0,
        order_id="oid-1",
        detail="maker_only",
    )
    assert "[PMM ORDER]" in msg
    assert "event=order_placed" in msg
    assert "market=tid-1" in msg
    assert "side=BUY price=0.950000 size=20.000000" in msg
    assert "order_id=oid-1" in msg
    assert "detail=maker_only" in msg


@pytest.mark.asyncio
async def test_periodic_report_interval_and_alert_cooldown(monkeypatch):
    client = _DummyTelegramClient()
    clock = {"now": 1000.0}

    def _now() -> float:
        return clock["now"]

    monkeypatch.setattr("src.platform.notification.telegram.TelegramClient", lambda *a, **k: client)
    monkeypatch.setattr("src.platform.notification.telegram.time.time", _now)

    cfg = PMMConfig(
        telegram_enabled=True,
        telegram_bot_token="bot-token",
        telegram_chat_id="chat-id",
        telegram_report_interval_sec=120,
        telegram_alert_cooldown_sec=60,
        telegram_send_startup=False,
    )
    notifier = PMMTelegramNotifier(cfg, execution_mode="live", strategy_key="single_level_v1")
    await notifier.start()

    await notifier.maybe_send_periodic_report(
        tick=1,
        pnl=1.0,
        equity=1001.0,
        usdc_balance=900.0,
        positions={"t1": 1.0},
        mids={"t1": 0.5},
        open_orders_count=1,
    )
    assert len(client.calls) == 0

    clock["now"] = 1125.0
    await notifier.maybe_send_periodic_report(
        tick=2,
        pnl=2.0,
        equity=1002.0,
        usdc_balance=901.0,
        positions={"t1": 1.0},
        mids={"t1": 0.5},
        open_orders_count=2,
    )
    assert len(client.calls) == 1
    assert "【天气策略小时汇报】" in client.calls[-1]["text"]

    await notifier.send_alert(
        alert_key="place_failed:t1:BUY",
        event="place_failed",
        detail="err1",
        tick=2,
        pnl=2.0,
    )
    assert len(client.calls) == 2
    assert "[PMM ALERT]" in client.calls[-1]["text"]

    clock["now"] = 1150.0
    await notifier.send_alert(
        alert_key="place_failed:t1:BUY",
        event="place_failed",
        detail="err2",
        tick=3,
        pnl=1.5,
    )
    assert len(client.calls) == 2

    clock["now"] = 1190.0
    await notifier.send_alert(
        alert_key="place_failed:t1:BUY",
        event="place_failed",
        detail="err3",
        tick=4,
        pnl=1.1,
    )
    assert len(client.calls) == 3

    await notifier.send_order_update(
        event="order_placed",
        token_id="t1",
        side="BUY",
        price=0.95,
        size=10.0,
        order_id="oid-123",
        detail="level=0",
    )
    assert len(client.calls) == 4
    assert "[PMM ORDER]" in client.calls[-1]["text"]

    await notifier.aclose()
