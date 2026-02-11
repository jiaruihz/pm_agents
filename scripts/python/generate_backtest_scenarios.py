from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pmm.backtest.scenario_generator import generate_all_from_catalog


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="generate_backtest_scenarios",
        description="Generate PMM synthetic scenario files for backtesting",
    )
    p.add_argument(
        "--catalog",
        default="pmm/backtest/case_catalog.json",
        help="Path to case catalog json",
    )
    p.add_argument(
        "--out-dir",
        default="pmm/backtest/scenarios",
        help="Output directory for generated scenario files",
    )
    p.add_argument("--seed", type=int, default=42, help="Base random seed")
    p.add_argument(
        "--show-initial",
        action="store_true",
        help="Print initial_state of each generated scenario",
    )
    p.add_argument(
        "--show-sample",
        default="",
        help="Show first tick snapshot for one scenario_id",
    )
    return p


def main() -> None:
    args = _parser().parse_args()
    res = generate_all_from_catalog(args.catalog, args.out_dir, seed=args.seed)
    print(json.dumps(res, ensure_ascii=False, indent=2))

    files = [Path(x) for x in res.get("files", [])]
    if args.show_initial:
        for fp in files:
            data = json.loads(fp.read_text(encoding="utf-8"))
            print(
                json.dumps(
                    {
                        "scenario_id": data.get("scenario_id"),
                        "initial_state": data.get("initial_state", {}),
                        "token_ids": data.get("token_ids", []),
                    },
                    ensure_ascii=False,
                )
            )

    sample_id = args.show_sample.strip()
    if sample_id:
        target = None
        for fp in files:
            if fp.stem == sample_id:
                target = fp
                break
        if target is None:
            raise ValueError(f"scenario not found: {sample_id}")
        data = json.loads(target.read_text(encoding="utf-8"))
        ticks = data.get("ticks", [])
        first_tick = ticks[0] if ticks else {}
        print(
            json.dumps(
                {
                    "scenario_id": sample_id,
                    "first_tick": first_tick,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()

