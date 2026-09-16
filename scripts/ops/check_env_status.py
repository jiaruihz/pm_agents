#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import dotenv_values


DEFAULT_KEYS = [
    "PM_API_BASE_URL",
    "PM_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether required .env keys are set without printing secret values.")
    parser.add_argument("--env-file", default=".env", help="Path to env file (default: .env)")
    parser.add_argument(
        "--keys",
        nargs="*",
        default=DEFAULT_KEYS,
        help="Env keys to check",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f"[ERROR] env file not found: {env_path}")
        return 2

    values = dotenv_values(env_path)
    missing = 0
    for key in args.keys:
        raw = values.get(key)
        ok = bool(str(raw).strip()) if raw is not None else False
        status = "SET" if ok else "MISSING"
        print(f"{key}: {status}")
        if not ok:
            missing += 1

    if missing:
        print(f"[WARN] missing keys: {missing}")
        return 1
    print("[OK] all checked keys are set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
