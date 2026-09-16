"""Regenerate instances.yaml from strategy_specs().

Run once to externalize the hardcoded list; safe to re-run after Task 2
(strategy_specs() then just reloads the yaml and we re-dump identical data).

Usage: .venv/bin/python -m src.strategies.runtime._generate_instances_yaml
"""

from __future__ import annotations

from dataclasses import asdict

import yaml

from scripts.ops.refresh_weather_strategy_runtime_registry import strategy_specs
from src.strategies.runtime.specs import INSTANCES_PATH


def main() -> None:
    rows = []
    for spec in strategy_specs():
        data = asdict(spec)
        data["artifact_files"] = [list(pair) for pair in data["artifact_files"]]
        rows.append(data)
    INSTANCES_PATH.write_text(
        yaml.safe_dump(rows, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"wrote {len(rows)} instances -> {INSTANCES_PATH}")


if __name__ == "__main__":
    main()
