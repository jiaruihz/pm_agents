#!/usr/bin/env python3
"""Train/evaluate one Korea-city PIT exact-current-rung NO probability head."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec  # noqa: E402
from weather_model_evaluation.korea_city_exact_no import (  # noqa: E402
    ExperimentConfig,
    run_experiment,
)


def _git_identity() -> dict[str, str | bool]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()
    )
    return {"repo_root": str(ROOT), "git_sha": head, "dirty": dirty}


def main() -> None:
    production = load_production_spec()
    artifact_root = production.research_artifact_root
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", choices=("Seoul", "Busan"), required=True)
    parser.add_argument(
        "--checkpoint-root",
        type=Path,
        default=production.data_feed_runtime_root / "output/korea_first_seen_state_v1/checkpoints",
    )
    parser.add_argument("--db", type=Path, default=production.canonical_db_path)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--development-end", required=True)
    parser.add_argument("--holdout-start", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--warmup-dates", type=int, default=7)
    parser.add_argument("--bootstrap-draws", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=20260813)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    output_dir = args.output_dir or (
        artifact_root
        / "weather_city_intraday_probability"
        / "korea_city_exact_no"
        / f"city={args.city.casefold()}"
        / f"run={args.run_id}"
    )
    summary = run_experiment(
        config=ExperimentConfig(
            city=args.city,
            start_date=args.start_date,
            development_end=args.development_end,
            holdout_start=args.holdout_start,
            end_date=args.end_date,
            warmup_dates=args.warmup_dates,
            bootstrap_draws=args.bootstrap_draws,
            seed=args.seed,
        ),
        checkpoint_root=args.checkpoint_root,
        db_path=args.db,
        output_dir=output_dir,
        code_identity={**_git_identity(), "runner": str(Path(__file__).resolve()), "run_id": args.run_id},
    )
    print(json.dumps({
        "summary_path": summary["summary_path"],
        "summary_sha256": summary["summary_sha256"],
        "selected_weather_weight": summary["selected_weather_weight"],
        "decision": summary["decision"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
