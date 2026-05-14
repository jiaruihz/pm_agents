#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.live_state import pause_live, read_live_state, resume_live, status_text


DEFAULT_STATE_DIR = ROOT / "runtime" / "weather_edge_v1" / "live_cycle"


def _telegram_post(token: str, method: str, payload: Dict[str, Any], *, timeout: int = 20) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, dict) else {}


def _send_text(token: str, chat_id: str, text: str) -> None:
    _telegram_post(token, "sendMessage", {"chat_id": chat_id, "text": text}, timeout=12)


def _read_offset(offset_path: Path) -> int:
    if not offset_path.exists():
        return 0
    try:
        return int(offset_path.read_text(encoding="utf-8").strip())
    except Exception:
        return 0


def _write_offset(offset_path: Path, offset: int) -> None:
    offset_path.parent.mkdir(parents=True, exist_ok=True)
    offset_path.write_text(str(offset), encoding="utf-8")


def _command_reply(text: str, state_dir: Path) -> str:
    normalized = text.strip().lower()
    if normalized in {"/pause_weather", "pause", "暂停", "暂停实盘"}:
        pause_live(state_dir, reason="Telegram 命令暂停", source="telegram_control")
        return "已暂停天气策略实盘。后续循环仍会同步数据和生成计划，但不会提交真实订单。"
    if normalized in {"/resume_weather", "resume", "继续", "恢复", "恢复实盘"}:
        resume_live(state_dir)
        return "已恢复天气策略实盘。下一轮如果有合格计划，会只按挂单方式尝试提交，不主动吃单。"
    if normalized in {"/status_weather", "status", "状态"}:
        return status_text(read_live_state(state_dir))
    return ""


def poll_once(
    *,
    token: str,
    chat_id: str,
    state_dir: Path,
    offset_path: Path,
    timeout_sec: int,
    initialize_only: bool = False,
) -> Dict[str, Any]:
    offset = _read_offset(offset_path)
    payload: Dict[str, Any] = {"timeout": timeout_sec, "allowed_updates": ["message"]}
    if offset > 0:
        payload["offset"] = offset
    data = _telegram_post(token, "getUpdates", payload, timeout=timeout_sec + 5)
    updates = data.get("result") if isinstance(data, dict) else []
    if not isinstance(updates, list):
        updates = []
    if not updates:
        return {"updates": 0, "commands": [], "offset": offset}

    max_update_id = max(int(x.get("update_id", 0)) for x in updates if isinstance(x, dict))
    _write_offset(offset_path, max_update_id + 1)
    if offset <= 0 and initialize_only:
        return {"updates": len(updates), "commands": [], "offset": max_update_id + 1, "initialized": True}

    commands: List[str] = []
    for update in updates:
        if not isinstance(update, dict):
            continue
        message = update.get("message") or {}
        if not isinstance(message, dict):
            continue
        chat = message.get("chat") or {}
        if str(chat.get("id") or "") != str(chat_id):
            continue
        text = str(message.get("text") or "").strip()
        if not text:
            continue
        reply = _command_reply(text, state_dir)
        if not reply:
            continue
        commands.append(text)
        _send_text(token, chat_id, reply)
    return {"updates": len(updates), "commands": commands, "offset": max_update_id + 1}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Realtime Telegram control loop for weather live state.")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--offset-file", default="")
    parser.add_argument("--poll-timeout-sec", type=int, default=20)
    parser.add_argument("--sleep-sec", type=float, default=2.0)
    parser.add_argument("--once", action="store_true", help="Poll once and exit.")
    parser.add_argument(
        "--process-existing",
        action="store_true",
        help="Process existing Telegram updates when no offset file exists.",
    )
    return parser


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ModuleNotFoundError:
        pass

    args = _parser().parse_args()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token:
        raise SystemExit("missing TELEGRAM_BOT_TOKEN")
    if not chat_id:
        raise SystemExit("missing TELEGRAM_CHAT_ID")

    state_dir = Path(args.state_dir)
    offset_path = Path(args.offset_file) if args.offset_file else state_dir / "telegram_update_offset.txt"
    initialize_only = not bool(args.process_existing)

    while True:
        try:
            result = poll_once(
                token=token,
                chat_id=chat_id,
                state_dir=state_dir,
                offset_path=offset_path,
                timeout_sec=int(args.poll_timeout_sec),
                initialize_only=initialize_only,
            )
            initialize_only = False
            if result.get("commands"):
                print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        except Exception as exc:
            print(f"[WARN] telegram control poll failed: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(float(args.sleep_sec))
        if args.once:
            return 0
        time.sleep(float(args.sleep_sec))


if __name__ == "__main__":
    raise SystemExit(main())
