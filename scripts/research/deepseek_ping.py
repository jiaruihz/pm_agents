import argparse
import asyncio
import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.agents.llm.client import LLMClient, extract_content


async def _run(prompt: str) -> str:
    client = LLMClient()
    try:
        resp = await client.chat([{"role": "user", "content": prompt}])
        return extract_content(resp)
    finally:
        await client.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Ping DeepSeek via OpenAI-compatible API.")
    parser.add_argument("--prompt", default="你是谁", help="Prompt to send")
    args = parser.parse_args()

    content = asyncio.run(_run(args.prompt))
    print("DEEPSEEK_OK")
    print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
