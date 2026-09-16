#!/usr/bin/env python3
import argparse
import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.platform.notification.telegram import send_telegram_message_sync


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a test Telegram message using project config.")
    parser.add_argument("--text", default="pm_agent telegram test", help="Message text")
    parser.add_argument("--chat-id", default=None, help="Override TELEGRAM_CHAT_ID from .env")
    parser.add_argument(
        "--parse-mode",
        default=None,
        choices=["Markdown", "MarkdownV2", "HTML"],
        help="Telegram parse mode",
    )
    parser.add_argument("--silent", action="store_true", help="Send silently without push notification")
    args = parser.parse_args()

    res = send_telegram_message_sync(
        text=args.text,
        chat_id=args.chat_id,
        parse_mode=args.parse_mode,
        disable_notification=args.silent,
    )
    result = res.get("result", {}) if isinstance(res, dict) else {}
    chat = result.get("chat", {}) if isinstance(result, dict) else {}
    print(f"TELEGRAM_OK chat_id={chat.get('id')} message_id={result.get('message_id')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
