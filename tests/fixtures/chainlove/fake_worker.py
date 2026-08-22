#!/usr/bin/env python3
"""Deterministic fixture worker for offline runner E2E. Logs every invocation."""
import json, os, sys
from pathlib import Path

stage, task_path, output_path = sys.argv[1], sys.argv[2], sys.argv[3]
log = os.environ.get("CHAINLOVE_WORKER_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(stage + "\n")
task = json.loads(Path(task_path).read_text())
output = Path(output_path)
output.parent.mkdir(parents=True, exist_ok=True)

if stage in ("candidate_mcp", "candidate_services"):
    viable = []
    for slug in [s for s in os.environ.get("CHAINLOVE_FAKE_VIABLE", "").split(",") if s]:
        viable.append({"slug": slug, "networks": ["somnia"], "sources": ["fixture"]})
    output.write_text(json.dumps({
        "work_order_id": stage, "viable": viable,
        "excluded": [{"candidate": "fixture-excluded", "reason": "fixture provider bar"}],
        "gates": {"scan_coverage_verified": True, "collision_scan_complete": True,
                   "commercial_fields_supported": True},
        "coverage_note": "fixture coverage: full enumeration of frozen inputs",
    }, indent=2))
elif stage == "evidence_review":
    run_dir = output.parent.parent
    spec_env = os.environ.get("CHAINLOVE_FAKE_SPEC")
    if spec_env:
        spec = json.loads(Path(spec_env).read_text())
    else:
        spec = {"schema_version": "chainlove_approved_spec_v1", "candidates": [],
                 "rejected": [{"slug": "fixture-rejected", "reason": "fixture evidence insufficient"}],
                 "deferred": [{"slug": "fixture-deferred", "trigger": "PR #999 merged or closed"}],
                 "estimated": {"cells": 0, "images": 0, "modifier": 1.0, "nominal_usd": 0.0,
                                "estimate_status": "insufficient_sample"}}
    (run_dir / "approved_patch_spec.json").write_text(json.dumps(spec, indent=2))
    output.write_text(json.dumps({"work_order_id": "evidence_review",
                                   "approved_spec": str(run_dir / "approved_patch_spec.json"),
                                   "rejected": spec.get("rejected", []),
                                   "deferred": spec.get("deferred", [])}, indent=2))
elif stage == "adversarial_review":
    verdict = os.environ.get("CHAINLOVE_FAKE_ADVERSARIAL", "APPROVE")
    output.write_text(json.dumps({
        "work_order_id": "adversarial_review", "verdict": verdict,
        "reason": "fixture adversarial pass",
        "reviewed_commit_sha": task["reviewed_commit_sha"],
    }, indent=2))
output.with_suffix(".usage.json").write_text(json.dumps({
    "tokens": 1000, "tool_uses": 1, "duration_s": 0.1, "model": "fixture-worker"}))
