#!/usr/bin/env python3
"""Run the common Phase 1 replay/report pipeline over frozen city fixtures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from weather_model_evaluation import (  # noqa: E402
    FixtureCheckpointBuilder,
    FixtureInputCatalog,
    ReplayRunner,
    build_evaluation_report,
    stable_json,
)


DEFAULT_FIXTURES = ROOT / "tests" / "fixtures" / "weather_city_intraday_phase0"


def validate_output_dir(path: Path) -> Path:
    """Keep Phase 1 artifacts away from production/canonical runtime paths."""

    resolved = path.resolve()
    allowed_roots = (
        Path(tempfile.gettempdir()).resolve(),
        Path("/tmp").resolve(),
        (ROOT / "runtime" / "research").resolve(),
        (ROOT / "research_outputs").resolve(),
    )
    if not any(resolved.is_relative_to(root) for root in allowed_roots):
        allowed = ", ".join(str(root) for root in allowed_roots)
        raise ValueError(
            f"output-dir must be research/temp only; allowed roots: {allowed}"
        )
    return resolved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = validate_output_dir(args.output_dir)
    paths = sorted(args.fixtures.glob("*.json"))
    if not paths:
        raise SystemExit(f"no fixtures found: {args.fixtures}")
    result = ReplayRunner(
        input_provider=FixtureInputCatalog(paths),
        checkpoint_builder=FixtureCheckpointBuilder(),
    ).run()
    report = build_evaluation_report(result.rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "prediction_table.jsonl").write_text(
        "".join(stable_json(row) + "\n" for row in result.rows),
        encoding="utf-8",
    )
    (output_dir / "replay_manifest.json").write_text(
        json.dumps(result.manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "evaluation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        stable_json(
            {"manifest": result.manifest, "report_hash": report["report_hash"]}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
