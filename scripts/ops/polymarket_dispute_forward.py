#!/usr/bin/env python3
"""Run the read-only Polymarket dispute forward collector."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.rule_lawyer.dispute_forward import run_capture_once  # noqa: E402
from src.strategies.rule_lawyer.canonical import materialize  # noqa: E402
from src.strategies.rule_lawyer.canonical_audit import audit_canonical  # noqa: E402
from src.strategies.rule_lawyer.dispute_strategy import (  # noqa: E402
    score_forward_snapshots,
    update_shadow_ledger,
)
from src.strategies.rule_lawyer.trade_intents import (  # noqa: E402
    sync_zero_notional_trade_intents,
)
from src.strategies.rule_lawyer.paper_execution import sync_paper_execution  # noqa: E402
from src.strategies.rule_lawyer.capture_receipts import sync_capture_receipts  # noqa: E402
from src.platform.storage.jsonl import write_json_atomic  # noqa: E402


def clarification_worker_command(
    output_root: Path,
    *,
    model: str,
    reasoning_effort: str,
    max_new_cards: int,
) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "scripts/ops/polymarket_clarification_forward.py"),
        "--output-root",
        str(output_root),
        "--codex",
        "--model",
        model,
        "--reasoning-effort",
        reasoning_effort,
        "--max-new-cards",
        str(max_new_cards),
    ]


def run_clarification_worker(
    output_root: Path,
    *,
    model: str,
    reasoning_effort: str,
    max_new_cards: int,
) -> dict:
    proc = subprocess.run(
        clarification_worker_command(
            output_root,
            model=model,
            reasoning_effort=reasoning_effort,
            max_new_cards=max_new_cards,
        ),
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=900,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "clarification worker failed: "
            + ((proc.stderr or proc.stdout or "unknown error").strip())
        )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("clarification worker produced no summary")
    return json.loads(lines[-1])


def runtime_health_status(
    canonical_health: dict | None, capture_receipts: dict | None
) -> str:
    if canonical_health and canonical_health.get("status") == "error":
        return "error"
    if (
        (canonical_health and canonical_health.get("warnings"))
        or (
            capture_receipts
            and capture_receipts.get("demands", 0)
            > capture_receipts.get("successful_demands", 0)
        )
    ):
        return "warming"
    return "ok"


def verify_model_artifact(model_path: Path, expected_sha256: str | None) -> str:
    if not model_path.is_file():
        raise FileNotFoundError(f"dispute scoring model missing: {model_path}")
    actual = hashlib.sha256(model_path.read_bytes()).hexdigest()
    if expected_sha256 and actual != expected_sha256.lower():
        raise RuntimeError(
            f"dispute scoring model sha256 mismatch: expected={expected_sha256} actual={actual}"
        )
    return actual


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="runtime/dispute_repricing/forward_v1")
    parser.add_argument("--lookback-hours", type=float, default=72)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=30)
    parser.add_argument("--skip-source-evidence", action="store_true")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--max-bootstrap-reconciliations-per-run", type=int, default=8)
    parser.add_argument("--force-recapture", action="store_true")
    parser.add_argument(
        "--model",
        default="runtime/dispute_repricing/dispute_case_panel_v1/market_plus_rules_model.joblib",
    )
    parser.add_argument("--model-sha256")
    parser.add_argument("--skip-scoring", action="store_true")
    parser.add_argument("--clarification-court", action="store_true")
    parser.add_argument("--clarification-model", default="gpt-5.4")
    parser.add_argument("--clarification-reasoning-effort", default="medium")
    parser.add_argument("--clarification-max-new-cards", type=int, default=8)
    parser.add_argument(
        "--canonical-db",
        type=Path,
        default=ROOT / "runtime/dispute_repricing/dispute.db",
    )
    parser.add_argument("--skip-canonical", action="store_true")
    parser.add_argument("--subscription-epoch-root", type=Path)
    parser.add_argument("--health-path", type=Path)
    args = parser.parse_args()
    output_root = Path(args.output_root)
    model_path = Path(args.model)
    model_sha256 = None
    if not args.skip_scoring:
        model_sha256 = verify_model_artifact(model_path, args.model_sha256)
    while True:
        summary = run_capture_once(
            output_root,
            lookback_hours=args.lookback_hours,
            fetch_source_evidence=not args.skip_source_evidence,
            workers=args.workers,
            force_recapture=args.force_recapture,
            max_bootstrap_reconciliations_per_run=(
                args.max_bootstrap_reconciliations_per_run
            ),
        )
        scorecard = None
        shadow_ledger = None
        clarification = None
        canonical = None
        canonical_health = None
        trade_intents = None
        paper_execution = None
        capture_receipts = None
        if not args.skip_scoring:
            scorecard = score_forward_snapshots(
                output_root / "snapshots.jsonl",
                model_path,
                output_root / "signals.jsonl",
            )
            write_json_atomic(output_root / "scorecard.json", scorecard)
        if args.clarification_court:
            clarification = run_clarification_worker(
                output_root,
                model=args.clarification_model,
                reasoning_effort=args.clarification_reasoning_effort,
                max_new_cards=max(0, args.clarification_max_new_cards),
            )
        trade_intents = sync_zero_notional_trade_intents(output_root)
        paper_execution = sync_paper_execution(output_root)
        if paper_execution["blocked"]:
            raise RuntimeError(
                f"dispute paper execution blocked {len(paper_execution['blocked'])} intents"
            )
        shadow_ledger = update_shadow_ledger(output_root)
        write_json_atomic(output_root / "shadow_ledger.json", shadow_ledger)
        capture_receipts = sync_capture_receipts(
            output_root, args.subscription_epoch_root
        )
        if not args.skip_canonical:
            canonical = materialize(output_root, args.canonical_db)
            canonical_health = audit_canonical(args.canonical_db)
            write_json_atomic(output_root / "canonical_health.json", canonical_health)
        print(
            json.dumps(
                {
                    "capture": summary,
                    "scorecard": scorecard,
                    "shadow_ledger": shadow_ledger,
                    "clarification": clarification,
                    "canonical": canonical,
                    "canonical_health": canonical_health,
                    "trade_intents": trade_intents,
                    "paper_execution": paper_execution,
                    "capture_receipts": capture_receipts,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        health = {
            "schema_version": "dispute_repricing_runtime_health_v1",
            "status": runtime_health_status(canonical_health, capture_receipts),
            "updated_at_utc": summary["captured_at_utc"],
            "strategy_key": "rule_lawyer.dispute_repricing",
            "execution_mode": "zero_notional_shadow",
            "live_authority": False,
            "actual_notional": 0.0,
            "model_path": str(model_path) if not args.skip_scoring else None,
            "model_sha256": model_sha256,
            "capture": summary,
            "trade_intents": trade_intents,
            "paper_execution": paper_execution,
            "capture_receipts": capture_receipts,
            "canonical_health": canonical_health,
        }
        health_path = args.health_path or output_root / "health.json"
        write_json_atomic(health_path, health)
        if canonical_health and canonical_health.get("status") == "error":
            raise RuntimeError(
                "dispute canonical health failed: "
                + ", ".join(canonical_health.get("errors") or ["unknown error"])
            )
        if not args.loop:
            return 0
        args.force_recapture = False
        time.sleep(max(5, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
