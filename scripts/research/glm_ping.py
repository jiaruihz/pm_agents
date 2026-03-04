#!/usr/bin/env python3
import argparse
import os
import sys

from dotenv import load_dotenv

try:
    from zai import ZhipuAiClient
except ModuleNotFoundError:
    print("Missing dependency: zai", file=sys.stderr)
    print("Install with: pip install zai-sdk", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Ping GLM via Zhipu zai SDK.")
    parser.add_argument("--prompt", default="你好，请介绍一下自己。", help="Prompt to send")
    parser.add_argument("--system", default="你是一个有用的AI助手。", help="System prompt")
    parser.add_argument("--model", default=os.getenv("GLM_MODEL", "glm-4.6"), help="GLM model")
    parser.add_argument(
        "--temperature",
        type=float,
        default=float(os.getenv("GLM_TEMPERATURE", "0.6")),
        help="Sampling temperature",
    )
    args = parser.parse_args()

    api_key = os.getenv("GLM") or os.getenv("GLM_API_KEY")
    if not api_key:
        print("Missing API key. Set GLM (recommended) or GLM_API_KEY.", file=sys.stderr)
        return 1

    client = ZhipuAiClient(api_key=api_key)
    response = client.chat.completions.create(
        model=args.model,
        messages=[
            {"role": "system", "content": args.system},
            {"role": "user", "content": args.prompt},
        ],
        temperature=args.temperature,
    )
    print(response.choices[0].message.content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

