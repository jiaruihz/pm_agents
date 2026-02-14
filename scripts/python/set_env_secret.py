#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import re
from pathlib import Path


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def main() -> int:
    parser = argparse.ArgumentParser(description="Set one secret key in .env via hidden prompt.")
    parser.add_argument("key", help="Env key to set, e.g. PM_API_KEY")
    parser.add_argument("--env-file", default=".env", help="Path to env file (default: .env)")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f"[ERROR] env file not found: {env_path}")
        return 2

    secret = getpass.getpass(f"Enter {args.key}: ").strip()
    if not secret:
        print("[ERROR] empty value; aborted")
        return 2

    lines = env_path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^{re.escape(args.key)}=")
    new_line = f'{args.key}="{_escape(secret)}"'
    updated = False
    out: list[str] = []
    for line in lines:
        if pattern.match(line):
            out.append(new_line)
            updated = True
        else:
            out.append(line)
    if not updated:
        out.append(new_line)

    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[OK] {args.key} updated in {env_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
