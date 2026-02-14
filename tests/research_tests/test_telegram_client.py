import json

import httpx
import pytest

from src.platform.clients.telegram_client import TelegramClient


@pytest.mark.asyncio
async def test_send_message_success():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://api.telegram.org/bottest-token/sendMessage"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["chat_id"] == "12345"
        assert payload["text"] == "hello"
        assert payload["disable_notification"] is True
        assert payload["parse_mode"] == "HTML"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"message_id": 99, "chat": {"id": 12345}},
            },
        )

    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tg = TelegramClient(bot_token="test-token", client=async_client)
    res = await tg.send_message("12345", "hello", parse_mode="HTML", disable_notification=True)
    assert res["result"]["message_id"] == 99
    await async_client.aclose()


@pytest.mark.asyncio
async def test_send_message_api_failure():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "bad request"})

    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tg = TelegramClient(bot_token="token", client=async_client)
    with pytest.raises(RuntimeError):
        await tg.send_message("123", "hello")
    await async_client.aclose()
