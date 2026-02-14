"""Telegram Bot API client."""

from typing import Any, Dict, Optional

import httpx


class TelegramClient:
    """Minimal async client for Telegram Bot API."""

    def __init__(
        self,
        bot_token: str,
        api_base_url: str = "https://api.telegram.org",
        timeout_seconds: float = 15.0,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        token = (bot_token or "").strip()
        if not token:
            raise ValueError("bot_token is required")
        self._bot_token = token
        self._api_base_url = api_base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def __aenter__(self) -> "TelegramClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send_message(
        self,
        chat_id: str,
        text: str,
        parse_mode: Optional[str] = None,
        disable_notification: bool = False,
    ) -> Dict[str, Any]:
        chat = (chat_id or "").strip()
        if not chat:
            raise ValueError("chat_id is required")
        if text is None or text == "":
            raise ValueError("text is required")

        payload: Dict[str, Any] = {
            "chat_id": chat,
            "text": text,
            "disable_notification": disable_notification,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        endpoint = f"{self._api_base_url}/bot{self._bot_token}/sendMessage"
        resp = await self._client.post(endpoint, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise RuntimeError(f"Telegram API returned invalid response: {data}")
        return data
