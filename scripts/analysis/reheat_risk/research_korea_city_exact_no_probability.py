#!/usr/bin/env python3
"""Train/evaluate Korea-city PIT probability heads."""

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
from weather_model_evaluation.korea_city_distribution import (  # noqa: E402
    DistributionExperimentConfig,
    run_distribution_experiment,
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
    parser.add_argument(
        "--experiment",
        choices=("exact_no", "remaining_heat_distribution"),
        default="exact_no",
    )
    parser.add_argument("--city", choices=("Seoul", "Busan"), required=True)
    parser.add_argument(
        "--checkpoint-root",
        type=Path,
        default=production.data_feed_runtime_root / "output/korea_first_seen_state_v1/checkpoints",
    )
    parser.add_argument("--db", type=Path, default=production.canonical_db_path)
    parser.add_argument(
        "--physical-states",
        type=Path,
        default=Path(
            "/Volumes/jrs/pm_agents/research/korea_iem_remaining_heat/v1/"
            "hourly_states.csv.gz"
        ),
    )
    parser.add_argument(
        "--market-batches-root",
        type=Path,
        default=production.data_feed_runtime_root / "market_books/batches",
    )
    parser.add_argument("--physical-train-end", default="2026-07-07")
    parser.add_argument("--physical-holdout-start", default="2026-07-08")
    parser.add_argument("--physical-holdout-end", default="2026-07-21")
    parser.add_argument("--residual-train-end", default="2026-08-01")
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

    family = (
        "korea_city_remaining_heat_distribution"
        if args.experiment == "remaining_heat_distribution"
        else "korea_city_exact_no"
    )
    output_dir = args.output_dir or (
        artifact_root
        / "weather_city_intraday_probability"
        / family
        / f"city={args.city.casefold()}"
        / f"run={args.run_id}"
    )
    code_identity = {
        **_git_identity(),
        "runner": str(Path(__file__).resolve()),
        "run_id": args.run_id,
        "experiment": args.experiment,
    }
    if args.experiment == "remaining_heat_distribution":
        summary = run_distribution_experiment(
            config=DistributionExperimentConfig(
                city=args.city,
                physical_train_end=args.physical_train_end,
                physical_holdout_start=args.physical_holdout_start,
                physical_holdout_end=args.physical_holdout_end,
                amos_start=args.start_date,
                residual_train_end=args.residual_train_end,
                development_end=args.development_end,
                holdout_start=args.holdout_start,
                end_date=args.end_date,
                bootstrap_draws=args.bootstrap_draws,
                seed=args.seed,
            ),
            physical_states_path=args.physical_states,
            checkpoint_root=args.checkpoint_root,
            market_batches_root=args.market_batches_root,
            db_path=args.db,
            output_dir=output_dir,
            code_identity=code_identity,
        )
    else:
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
            code_identity=code_identity,
        )
    print(json.dumps({
        "summary_path": summary["summary_path"],
        "summary_sha256": summary["summary_sha256"],
        "selected_weather_weight": summary.get(
            "selected_weather_weight",
            summary.get("selected_weather_likelihood_ratio_weight"),
        ),
        "decision": summary["decision"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
