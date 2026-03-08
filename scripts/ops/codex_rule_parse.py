#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.rule_lawyer.services.market_resolver import resolve_market
from src.strategies.rule_lawyer.parser import parse_market_with_codex_cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse Polymarket rules with codex exec and write structured JSON.")
    parser.add_argument("--target-market", required=True, help="Polymarket market URL, slug, or condition id")
    parser.add_argument("--out-file", default="", help="Optional output JSON path")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    market = resolve_market(args.target_market)
    payload = {
        "market_id": market.market_id,
        "slug": market.slug,
        "question": market.question,
        "description": market.description,
        "rules": market.rules,
        "category": market.category,
        "end_at_utc": market.end_date,
    }
    parsed = parse_market_with_codex_cli(payload)
    if not parsed:
        raise SystemExit("codex rule parse failed")
    data = parsed.model_dump()
    if args.out_file:
        out_path = Path(args.out_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"output json: {out_path}")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
