"""Notification helpers for research workflows."""

import asyncio
from typing import Any, Dict, Optional

from src.platform.clients import TelegramClient

from .config import get_settings


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
