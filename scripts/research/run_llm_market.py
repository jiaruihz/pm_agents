#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import sqlite3
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.domains.research.config import get_settings
from src.domains.research.parser import parse_market_with_llm, save_market_rule_parses_records


def _load_market(market_id: str) -> dict:
    settings = get_settings()
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT market_id, slug, question, description, rules, category, end_at_utc "
        "FROM markets WHERE market_id=? LIMIT 1",
        (market_id,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run LLM parse for a single market_id.")
    parser.add_argument("market_id", help="Market ID from the markets table")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification (proxy MITM)")
    parser.add_argument("--proxy", default="", help="Proxy URL (e.g. http://127.0.0.1:7897)")
    args = parser.parse_args()

    os.environ.setdefault("LLM_BASE_URL", "https://api.deepseek.com")
    os.environ.setdefault("LLM_MODEL", "deepseek-chat")
    if args.insecure:
        os.environ["PYTHONHTTPSVERIFY"] = "0"
        os.environ["CURL_CA_BUNDLE"] = ""
    if args.proxy:
        os.environ.setdefault("HTTP_PROXY", args.proxy)
        os.environ.setdefault("HTTPS_PROXY", args.proxy)
        os.environ.setdefault("http_proxy", args.proxy)
        os.environ.setdefault("https_proxy", args.proxy)
        os.environ.setdefault("ALL_PROXY", args.proxy)
        os.environ.setdefault("all_proxy", args.proxy)

    market = _load_market(args.market_id)
    if not market:
        print(f"market_id not found: {args.market_id}")
        return 1

    parsed = asyncio.run(parse_market_with_llm(market))
    if not parsed:
        print("LLM parse failed or invalid JSON.")
        return 2

    save_market_rule_parses_records(market["market_id"], parsed, parsed.dict())
    print(json.dumps(parsed.dict(), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
