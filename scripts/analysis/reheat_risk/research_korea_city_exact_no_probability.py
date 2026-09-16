#!/usr/bin/env python3
"""Train/evaluate Korea-city PIT probability heads."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


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
from weather_model_evaluation.korea_airport_forward import (  # noqa: E402
    KoreaAirportForwardConfig,
    run_korea_airport_forward,
)


def _git_identity() -> dict[str, Any]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()
    )
    code_paths = (
        Path(__file__).resolve(),
        ROOT / "weather_model_evaluation/korea_airport_forward.py",
        ROOT / "weather_model_evaluation/korea_city_exact_no.py",
    )
    module_sha256 = {}
    for path in code_paths:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        module_sha256[str(path)] = digest.hexdigest()
    return {
        "repo_root": str(ROOT),
        "git_sha": head,
        "dirty": dirty,
        "module_sha256": module_sha256,
    }


def main() -> None:
    production = load_production_spec()
    artifact_root = production.research_artifact_root
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--experiment",
        choices=(
            "exact_no",
            "remaining_heat_distribution",
            "airport_fast_source_forward",
        ),
        default="exact_no",
        help=(
            "airport_fast_source_forward is the replay-stable evaluator name "
            "for the Seoul/Busan CrossNO probability-model branch"
        ),
    )
    parser.add_argument("--city", choices=("Seoul", "Busan", "Both"), required=True)
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
    parser.add_argument("--development-end")
    parser.add_argument("--holdout-start")
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--warmup-dates", type=int, default=7)
    parser.add_argument("--bootstrap-draws", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=20260813)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--seoul-artifact",
        type=Path,
        default=Path(
            "/Volumes/jrs-archive/pm_agents/research/artifact_store/"
            "weather_city_intraday_probability/korea_city_remaining_heat_distribution/"
            "city=seoul/run=20260813_decision_ladder_residual_v3/model.joblib"
        ),
    )
    parser.add_argument(
        "--busan-artifact",
        type=Path,
        default=(
            production.data_feed_runtime_root
            / "model_artifacts/city_probability_runtime_v3/"
            "busan_online_market_prior_v1.joblib"
        ),
    )
    parser.add_argument(
        "--decision-bundles",
        type=Path,
        default=(
            production.data_feed_runtime_root
            / "output/city_probability_runtime_v3/decision_bundles.jsonl"
        ),
    )
    parser.add_argument("--minimum-forward-dates", type=int, default=30)
    parser.add_argument("--fresh-book-seconds", type=float, default=10.0)
    args = parser.parse_args()

    if args.experiment != "airport_fast_source_forward" and args.city == "Both":
        parser.error("--city Both is only valid for airport_fast_source_forward")
    if args.experiment != "airport_fast_source_forward" and (
        not args.development_end or not args.holdout_start
    ):
        parser.error(
            "--development-end and --holdout-start are required for training experiments"
        )
    if args.experiment == "airport_fast_source_forward":
        # This is an artifact/evaluator family, not a separate strategy family.
        # The emitted summary carries the parent CrossNO strategy lineage.
        family = "korea_airport_fast_source_forward"
        output_dir = args.output_dir or (
            artifact_root
            / "weather_city_intraday_probability"
            / family
            / f"run={args.run_id}"
        )
    else:
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
    if args.experiment == "airport_fast_source_forward":
        cities = ("Seoul", "Busan") if args.city == "Both" else (args.city,)
        summary = run_korea_airport_forward(
            config=KoreaAirportForwardConfig(
                cities=cities,
                start_date=args.start_date,
                end_date=args.end_date,
                bootstrap_draws=args.bootstrap_draws,
                seed=args.seed,
                minimum_forward_dates=args.minimum_forward_dates,
                fresh_book_seconds=args.fresh_book_seconds,
            ),
            checkpoint_root=args.checkpoint_root,
            market_batches_root=args.market_batches_root,
            db_path=args.db,
            seoul_artifact_path=args.seoul_artifact,
            seoul_physical_states_path=args.physical_states,
            busan_artifact_path=args.busan_artifact,
            decision_bundles_path=args.decision_bundles,
            output_dir=output_dir,
            code_identity=code_identity,
        )
    elif args.experiment == "remaining_heat_distribution":
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
        "decision": summary.get("decision", summary.get("combined_decision")),
        "artifact_manifest_path": summary.get("artifact_manifest_path"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
