from types import SimpleNamespace

import pytest

from src.platform.notification.telegram import send_telegram_message


@pytest.mark.asyncio
async def test_send_telegram_message_uses_settings(monkeypatch):
    captured = {}

    class DummyTelegramClient:
        def __init__(self, bot_token: str, api_base_url: str) -> None:
            captured["bot_token"] = bot_token
            captured["api_base_url"] = api_base_url

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def send_message(self, chat_id, text, parse_mode=None, disable_notification=False):
            captured["chat_id"] = chat_id
            captured["text"] = text
            captured["parse_mode"] = parse_mode
            captured["disable_notification"] = disable_notification
            return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(
        "src.platform.notification.telegram.get_settings",
        lambda: SimpleNamespace(
            telegram_bot_token="bot-123",
            telegram_chat_id="chat-888",
            telegram_api_base_url="https://api.telegram.org",
        ),
    )
    monkeypatch.setattr("src.platform.notification.telegram.TelegramClient", DummyTelegramClient)

    res = await send_telegram_message("ping", parse_mode="Markdown", disable_notification=True)
    assert res["ok"] is True
    assert captured["bot_token"] == "bot-123"
    assert captured["chat_id"] == "chat-888"
    assert captured["text"] == "ping"
    assert captured["parse_mode"] == "Markdown"
    assert captured["disable_notification"] is True


@pytest.mark.asyncio
async def test_send_telegram_message_missing_config(monkeypatch):
    monkeypatch.setattr(
        "src.platform.notification.telegram.get_settings",
        lambda: SimpleNamespace(
            telegram_bot_token="",
            telegram_chat_id="",
            telegram_api_base_url="https://api.telegram.org",
        ),
    )
    with pytest.raises(ValueError):
        await send_telegram_message("hello")
