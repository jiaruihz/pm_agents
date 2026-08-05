"""Command line entry for the offline D-1 market-residual shadow runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .evaluator import write_evaluation
from .runtime import (
    FrozenResidualArtifact,
    LinearMarketResidualModel,
    ShadowPolicy,
    ShadowRuntime,
    load_jsonl,
)


HERE = Path(__file__).resolve().parent


def _run(args: argparse.Namespace) -> None:
    policy_config = json.loads(Path(args.config).read_text()) if args.config else {}
    artifact = FrozenResidualArtifact.load(Path(args.artifact), expected_sha256=args.artifact_sha256)
    runtime = ShadowRuntime(
        LinearMarketResidualModel(artifact),
        ShadowPolicy.from_mapping(policy_config.get("policy")),
    )
    summary = runtime.run(load_jsonl(Path(args.checkpoints)), Path(args.output))
    print(json.dumps(summary, sort_keys=True))


def _evaluate(args: argparse.Namespace) -> None:
    report = write_evaluation(
        load_jsonl(Path(args.predictions)),
        load_jsonl(Path(args.settlements)),
        Path(args.output),
    )
    print(json.dumps(report, sort_keys=True))


def _demo(args: argparse.Namespace) -> None:
    output = Path(args.output)
    run_args = argparse.Namespace(
        artifact=str(HERE / "fixtures/demo_artifact.json"),
        artifact_sha256=None,
        checkpoints=str(HERE / "fixtures/demo_checkpoints.jsonl"),
        config=str(HERE / "fixtures/demo_config.json"),
        output=str(output / "shadow"),
    )
    _run(run_args)
    evaluation_args = argparse.Namespace(
        predictions=str(output / "shadow/predictions.jsonl"),
        settlements=str(HERE / "fixtures/demo_settlements.jsonl"),
        output=str(output / "evaluation"),
    )
    _evaluate(evaluation_args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--artifact", required=True)
    run_parser.add_argument("--artifact-sha256")
    run_parser.add_argument("--checkpoints", required=True)
    run_parser.add_argument("--config")
    run_parser.add_argument("--output", required=True)
    run_parser.set_defaults(handler=_run)
    eval_parser = subparsers.add_parser("evaluate")
    eval_parser.add_argument("--predictions", required=True)
    eval_parser.add_argument("--settlements", required=True)
    eval_parser.add_argument("--output", required=True)
    eval_parser.set_defaults(handler=_evaluate)
    demo_parser = subparsers.add_parser("demo")
    demo_parser.add_argument("--output", default="/tmp/weather_d1_market_residual_shadow_demo")
    demo_parser.set_defaults(handler=_demo)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
