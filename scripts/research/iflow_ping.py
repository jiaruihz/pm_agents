#!/usr/bin/env python3
import argparse
import asyncio
import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.domains.research.config import get_settings
from src.agents.llm.client import LLMClient, extract_content


async def _run(prompt: str) -> str:
    client = LLMClient()
    try:
        resp = await client.chat([{"role": "user", "content": prompt}])
        return extract_content(resp)
    finally:
        await client.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Ping LLM provider (iflow) via LLMClient")
    parser.add_argument("--prompt", default="Ping test: reply with a short OK.", help="Prompt to send")
    args = parser.parse_args()

    settings = get_settings()
    print(f"provider={settings.llm_provider} base_url={settings.iflow_base_url or settings.llm_base_url} model={settings.iflow_model or settings.llm_model}")
    content = asyncio.run(_run(args.prompt))
    print("response:", content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
