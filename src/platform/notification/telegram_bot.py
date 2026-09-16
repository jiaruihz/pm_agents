"""Telegram bot listener to dispatch system alerts."""

import asyncio
import logging
from typing import Optional

from src.platform.engine.events import AlertEvent
from src.platform.engine.dispatcher import EventDispatcher
from src.platform.clients.telegram_client import TelegramClient

logger = logging.getLogger(__name__)


class TelegramNotificationListener:
    """
    Subscribes to AlertEvent on the dispatcher and forwards them to Telegram.
    Runs asynchronously so slow network requests don't block the engine.
    """

    def __init__(
        self,
        dispatcher: EventDispatcher,
        bot_token: str,
        chat_id: str,
    ) -> None:
        self.dispatcher = dispatcher
        self.bot_token = bot_token
        self.chat_id = chat_id
        
        self.client: Optional[TelegramClient] = None
        
        if self.bot_token and self.chat_id:
            self.dispatcher.subscribe(AlertEvent, self._handle_alert)
        else:
            logger.warning("Telegram Notification Listener started without token/chat_id. Alerts will be ignored.")

    async def start(self) -> None:
        if not self.bot_token or not self.chat_id:
            return
            
        if self.client is None:
            self.client = TelegramClient(bot_token=self.bot_token)
            
        logger.info("TelegramNotificationListener started.")

    async def stop(self) -> None:
        if self.client:
            await self.client.aclose()
            self.client = None
        logger.info("TelegramNotificationListener stopped.")

    async def _handle_alert(self, event: AlertEvent) -> None:
        """Process an alert event asynchronously."""
        if not self.client:
            return

        instance_part = f" [Instance: {event.instance_id}]" if event.instance_id else ""
        text = f"🚨 <b>[{event.level}]</b>{instance_part}\n\n{event.message}"

        try:
            # We don't await here directly in the handler loop so we don't block the dispatcher
            # (Though dispatcher runs handlers concurrently via gather, it's safe to await here,
            # but using asyncio.create_task ensures complete fire-and-forget for slow IO)
            asyncio.create_task(
                self.client.send_message(
                    chat_id=self.chat_id,
                    text=text,
                    parse_mode="HTML"
                )
            )
        except Exception as e:
            logger.error(f"Failed to schedule Telegram alert: {e}", exc_info=True)
