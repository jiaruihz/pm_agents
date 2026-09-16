"""Reusable Chain.Love bounty orchestration profile (contract: GLM_AUTOMATION_IMPLEMENTATION_BRIEF.md).

DAG (extended=True):
    preflight -> freeze_inputs
      -> candidate_mcp / candidate_services / stale_delta / deferred_recheck
      -> evidence_review
         zero candidates -> noop_verification            (NOOP_VERIFIED)
         approved        -> implementation_worker
            -> deterministic_validation + adversarial_review (parallel) -> fix loop (<=2)
            -> submission_bundle                          (READY_TO_SUBMIT)
            -> publish [submit-grant gated] -> post_publish_verification -> ci_monitor
Terminal outcomes: NOOP_VERIFIED | READY_TO_SUBMIT | SUBMITTED | DEFERRED | REJECTED | BLOCKED | FAILED.
Key gates are COMMAND verifiers backed by scripts/ops/chainlove_verify.py — never agent self-assertion.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from ..contracts import AuthoritySpec, BudgetSpec, DomainSpec, RiskLevel, TaskSpec
from ..orchestration import (
    RequestProfile,
    RoleSpec,
    VerifierKind,
    VerifierSpec,
    WorkOrder,
)
from ..orchestration.contracts import AssertionOperator


DEFAULT_NETWORKS = ("algorand", "filecoin", "somnia")
DEFAULT_CATEGORIES = ("mcpservers", "security", "storages", "services")
VERIFY_SCRIPT = "scripts/ops/chainlove_verify.py"

TERMINAL_OUTCOMES = {
    "NOOP_VERIFIED": {
        "condition": "candidate_count == 0",
        "requires": ("scan_coverage_verified", "rejection_ledger_written"),
    },
    "READY_TO_SUBMIT": {
        "condition": "candidate_count > 0 and submit grant absent",
        "requires": (
            "schema_pipeline_passed",
            "adversarial_review_approved",
            "final_collision_scan_zero",
        ),
    },
    "SUBMITTED": {
        "condition": "candidate_count > 0 and valid submit grant",
        "requires": (
            "schema_pipeline_passed",
            "adversarial_review_approved",
            "final_collision_scan_zero",
            "submit_grant_valid",
            "pr_matches_reviewed_commit",
            "ci_matches_current_head",
        ),
    },
    "DEFERRED": {"condition": "blocked by open claim / unmerged provider / unverifiable source", "requires": ("deferred_ledger_written",)},
    "REJECTED": {"condition": "quality gate failed after fix loop", "requires": ("rejection_ledger_written",)},
    "BLOCKED": {"condition": "external blocker (credentials, fork CI approval, rate limit)", "requires": ("blocker_recorded",)},
    "FAILED": {"condition": "internal contract violation", "requires": ("failure_recorded",)},
}


def _run(argv: list[str], *, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(argv)}\n{proc.stderr}")
    return proc.stdout.strip()


def capture_collision_snapshot(repo: str) -> dict[str, Any]:
    """Capture one reusable open-PR metadata snapshot instead of rescanning per agent."""

    rows = json.loads(
        _run(
            [
                "gh",
                "api",
                "--paginate",
                f"/repos/{repo}/pulls?state=open&per_page=100",
            ]
        )
    )
    return {
        "schema_version": "chainlove_open_pr_snapshot_v1",
        "repository": repo,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "open_pr_count": len(rows),
        "pull_requests": rows,
    }


def snapshot_sha256(snapshot: dict[str, Any]) -> str:
    raw = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _verify(acceptance_id: str, *argv: str, expected_exit_code: int = 0) -> VerifierSpec:
    return VerifierSpec(
        acceptance_id=acceptance_id,
        kind=VerifierKind.COMMAND,
        argv=(VERIFY_SCRIPT, *argv),
        expected_exit_code=expected_exit_code,
        timeout_seconds=900,
    )


def _json_gate(acceptance_id: str, evidence_ref: str, path: str, expected: Any = True) -> VerifierSpec:
    """JSON-assertion gate for agent-produced artifacts (never a security-critical gate)."""

    from ..orchestration import JsonAssertion

    return VerifierSpec(
        acceptance_id=acceptance_id,
        kind=VerifierKind.JSON_ASSERTIONS,
        evidence_ref=evidence_ref,
        assertions=(
            JsonAssertion(path=path, operator=AssertionOperator.EQUALS, expected=expected),
        ),
    )


def build_bundle(
    *,
    run_id: str,
    repo: str,
    repo_path: Path,
    base_sha: str,
    snapshot_path: Path,
    snapshot_sha256: str,
    snapshot_captured_at_utc: str,
    networks: tuple[str, ...] = DEFAULT_NETWORKS,
    categories: tuple[str, ...] = DEFAULT_CATEGORIES,
    reward_address: str | None = None,
    extended: bool = True,
    run_dir: Path | None = None,
    worktree_path: Path | None = None,
    submission_mode: str = "review_required",
    grant_path: Path | None = None,
    github_user: str = "jiaruihz",
    json_tools_dir: Path | None = None,
    precedents_path: Path | None = None,
    deferred_queue_path: Path | None = None,
    stale_inventory_path: Path | None = None,
    decision_ledger_path: Path | None = None,
) -> dict[str, Any]:
    """Return validated task, routing, roles and bounded WorkOrders."""

    run_dir = run_dir or snapshot_path.parent
    manifest_path = run_dir / "freeze_manifest.json"
    index_path = run_dir / "claimed_slugs.json"
    receipt_path = run_dir / "run_receipt.json"
    worktree_path = worktree_path or (repo_path.parent / "worktrees" / run_id)
    json_tools_dir = json_tools_dir or (repo_path.parent / "json-tools")
    precedents_path = precedents_path or (repo_path.parent / "harness-runs" / "state" / "reviewer_precedents.md")
    deferred_queue_path = deferred_queue_path or (repo_path.parent / "harness-runs" / "state" / "deferred_candidates.json")
    stale_inventory_path = stale_inventory_path or (repo_path.parent / "harness-runs" / "state" / "stale_inventory.json")
    decision_ledger_path = decision_ledger_path or (run_dir / "decision_ledger.jsonl")

    frozen = {
        "base_ref": "origin/main",
        "base_sha": base_sha,
        "open_pr_snapshot": str(snapshot_path.resolve()),
        "open_pr_snapshot_sha256": snapshot_sha256,
        "open_pr_snapshot_captured_at_utc": snapshot_captured_at_utc,
        "freeze_manifest": str(manifest_path),
        "claimed_index": str(index_path),
        "reward_address": reward_address,
        "precedents": str(precedents_path),
        "deferred_queue": str(deferred_queue_path),
        "stale_inventory": str(stale_inventory_path),
        "decision_ledger": str(decision_ledger_path),
        "submission_mode": submission_mode,
        "submit_grant": str(grant_path) if grant_path else None,
    }

    task = TaskSpec(
        run_id=run_id,
        task_type="chainlove.bounty_batch",
        objective="Build, independently verify, and (only with an explicit submit grant) submit one source-backed Chain.Love bounty batch; zero-candidate runs terminate NOOP_VERIFIED.",
        scope={
            "repo": repo,
            "repo_path": str(repo_path.resolve()),
            "eligible_networks": list(networks),
            "categories": list(categories),
            "terminal_outcomes": TERMINAL_OUTCOMES,
        },
        acceptance=(
            "inputs_frozen",
            "scan_disposition_recorded",
            "terminal_outcome_certified",
        ),
        authority=AuthoritySpec(
            auto_execute=(
                RiskLevel.READ_ONLY,
                RiskLevel.DERIVED_DATA_WRITE,
                RiskLevel.REPOSITORY_WRITE,
            ),
            explicit_action_grants=("submit_pr",) if submission_mode == "auto" else (),
        ),
        budgets=BudgetSpec(
            max_actions=30,
            max_failures=3,
            max_experiments=0,
            patience=2,
            max_parallelism=3,
        ),
        frozen_inputs=frozen,
        domain=DomainSpec(initial_phase="ORCHESTRATE", terminal_phase="DONE"),
    )
    profile = RequestProfile(
        objective=task.objective,
        risk=RiskLevel.REPOSITORY_WRITE,
        estimated_stages=6,
        independent_workstreams=3,
        needs_iteration=True,
        needs_resume=True,
        needs_independent_review=True,
        explicit_harness=True,
        explicit_multi_agent=True,
    )
    roles = (
        RoleSpec(name="sol_preflight", requested_model="gpt-5.6-sol", reasoning_effort="low",
                 sandbox="read-only", network_access=True),
        RoleSpec(name="sol_freeze", requested_model="gpt-5.6-sol", reasoning_effort="low",
                 sandbox="read-only", network_access=True),
        RoleSpec(name="luna_scanner", requested_model="gpt-5.6-luna", reasoning_effort="medium",
                 sandbox="read-only", network_access=True),
        RoleSpec(name="sol_stale_delta", requested_model="gpt-5.6-sol", reasoning_effort="low",
                 sandbox="read-only", network_access=True),
        RoleSpec(name="sol_deferred_recheck", requested_model="gpt-5.6-sol", reasoning_effort="low",
                 sandbox="read-only", network_access=True),
        RoleSpec(name="terra_reviewer", requested_model="gpt-5.6-terra", reasoning_effort="high",
                 sandbox="read-only", network_access=True),
        RoleSpec(name="sol_noop_verifier", requested_model="gpt-5.6-sol", reasoning_effort="low",
                 sandbox="read-only", network_access=True),
        RoleSpec(
            name="sol_builder", requested_model="gpt-5.6-sol", reasoning_effort="high",
            sandbox="workspace-write", network_access=True,
            writable_roots=(str(worktree_path),),
        ),
        RoleSpec(name="terra_adversarial", requested_model="gpt-5.6-terra", reasoning_effort="high",
                 sandbox="read-only", network_access=True),
        RoleSpec(name="sol_submission", requested_model="gpt-5.6-sol", reasoning_effort="low",
                 sandbox="read-only", network_access=True),
        RoleSpec(name="sol_publisher", requested_model="gpt-5.6-sol", reasoning_effort="low",
                 sandbox="read-only", network_access=True),
        RoleSpec(name="sol_post_publish", requested_model="gpt-5.6-sol", reasoning_effort="low",
                 sandbox="read-only", network_access=True),
    )
    common_scope = {
        "repository": repo,
        "repo_path": str(repo_path.resolve()),
        "base_sha": base_sha,
        "freeze_manifest": str(manifest_path),
        "collision_snapshot_ref": str(snapshot_path.resolve()),
        "collision_snapshot_sha256": snapshot_sha256,
        "claimed_index_ref": str(index_path),
        "precedents_ref": str(precedents_path),
        "eligible_networks": list(networks),
        "source_policy": "current primary sources only",
    }
    scanner_usage_cap = (
        "40 tool calls is a SOFT budget, not an accuracy gate: if coverage is still "
        "incomplete at budget, emit INCOMPLETE — never disguise it as zero candidates."
    )

    preflight = WorkOrder(
        work_order_id="preflight",
        objective=(
            "Deterministic preflight: gh auth (no tokens in logs), repo identity, dirty-tree "
            "report, fetch origin/main, own open-PR review/CI state. Any CHANGES_REQUESTED or "
            "unaddressed maintainer comment switches the run to feedback-first mode (no new PR)."
        ),
        role="sol_preflight",
        parent_run_id=run_id,
        scope={**common_scope, "executor": "script",
               "script": "scripts/ops/chainlove_freeze.py::preflight",
               "feedback_first_gate": True, "github_user": github_user},
        acceptance=("repo_identity_valid", "worktree_state_recorded"),
        verifiers=(
            _verify("repo_identity_valid", "repo-identity", "--repo-path", str(repo_path), "--expect-remote", f"https://github.com/{repo}.git"),
        ),
        max_attempts=1,
        lease_timeout_seconds=300,
    )
    freeze_inputs = WorkOrder(
        work_order_id="freeze_inputs",
        objective=(
            "Deterministic freeze: fetch -> base SHA -> open-PR snapshot -> per-PR diffs -> "
            "header-safe claimed index -> context hashes -> manifest; fail-fast on drift."
        ),
        role="sol_freeze",
        parent_run_id=run_id,
        depends_on=("preflight",),
        scope={**common_scope, "executor": "script",
               "script": "scripts/ops/chainlove_freeze.py",
               "run_dir": str(run_dir)},
        acceptance=("base_snapshot_consistent", "claimed_index_valid", "context_hashes_valid", "base_current"),
        verifiers=(
            _verify("base_snapshot_consistent", "freeze-manifest", "--manifest", str(manifest_path)),
            _verify("claimed_index_valid", "claimed-index", "--index", str(index_path)),
            _verify("context_hashes_valid", "context-hashes", "--manifest", str(manifest_path)),
            _verify("base_current", "base-current", "--repo-path", str(repo_path), "--base-sha", base_sha),
        ),
        max_attempts=2,
        lease_timeout_seconds=1200,
    )
    candidate_mcp = WorkOrder(
        work_order_id="candidate_mcp",
        objective=(
            "Scan mcpservers for eligible networks from the frozen inputs. Output viable/"
            "rejected/deferred with per-cell primary sources and collision status. " + scanner_usage_cap
        ),
        role="luna_scanner",
        parent_run_id=run_id,
        depends_on=("freeze_inputs",),
        scope={**common_scope, "executor": "agent", "categories": ["mcpservers"],
               "output_ref": "candidate_mcp.json"},
        acceptance=("scan_coverage_verified", "collision_scan_complete"),
        max_attempts=2,
        lease_timeout_seconds=1800,
    )
    candidate_services = WorkOrder(
        work_order_id="candidate_services",
        objective=(
            "Scan security/storages/services for eligible networks from the frozen inputs. "
            "Reject unsupported commercial claims. " + scanner_usage_cap
        ),
        role="luna_scanner",
        parent_run_id=run_id,
        depends_on=("freeze_inputs",),
        scope={**common_scope, "executor": "agent",
               "categories": [c for c in categories if c != "mcpservers"],
               "output_ref": "candidate_services.json"},
        acceptance=("scan_coverage_verified", "collision_scan_complete"),
        max_attempts=2,
        lease_timeout_seconds=1800,
    )
    stale_delta = WorkOrder(
        work_order_id="stale_delta",
        objective=(
            "Deterministic stale-delta: link/redirect inventory diffed against the prior "
            "state/stale_inventory.json baseline; only mechanically unjudgeable anomalies go "
            "to agents. Local curl-000 is never evidence of death (DNS-poisoned egress)."
        ),
        role="sol_stale_delta",
        parent_run_id=run_id,
        depends_on=("freeze_inputs",),
        scope={**common_scope, "executor": "script-first",
               "baseline_ref": str(stale_inventory_path),
               "output_ref": "stale_delta.json"},
        acceptance=("sweep_snapshot_bound", "findings_source_verified"),
        max_attempts=2,
        lease_timeout_seconds=1200,
    )
    deferred_recheck = WorkOrder(
        work_order_id="deferred_recheck",
        objective=(
            "Deterministic deferred-queue trigger recheck (gh lookups only, no agent): emit "
            "deferred_ready.json with the subset whose triggers now hold; atomic update."
        ),
        role="sol_deferred_recheck",
        parent_run_id=run_id,
        depends_on=("freeze_inputs",),
        scope={**common_scope, "executor": "script",
               "deferred_queue_ref": str(deferred_queue_path),
               "output_ref": "deferred_ready.json"},
        acceptance=("triggers_rechecked",),
        max_attempts=1,
        lease_timeout_seconds=600,
    )
    evidence_review = WorkOrder(
        work_order_id="evidence_review",
        objective=(
            "Adjudicate ALL viable + deferred-ready candidates against primary sources, the "
            "precedents file, frozen base providers, current category modifiers and a live "
            "post-snapshot delta. Emit a machine-readable approved patch specification; "
            "implementers may not expand it. Rejections/deferrals append to the decision ledger."
        ),
        role="terra_reviewer",
        parent_run_id=run_id,
        depends_on=("candidate_mcp", "candidate_services", "stale_delta", "deferred_recheck"),
        scope={**common_scope, "executor": "agent",
               "review_input_refs": ("candidate_mcp.json", "candidate_services.json",
                                     "stale_delta.json", "deferred_ready.json"),
               "approved_spec_ref": "approved_patch_spec.json",
               "decision_ledger": str(decision_ledger_path)},
        acceptance=(
            "approved_candidate_set_written",
            "network_mapping_supported",
            "required_fields_supported",
        ),
        max_attempts=1,
        lease_timeout_seconds=1800,
    )
    noop_verification = WorkOrder(
        work_order_id="noop_verification",
        objective=(
            "Zero-candidate branch: verify scan coverage claims and the rejection ledger, then "
            "terminate NOOP_VERIFIED. Never proceed to the builder."
        ),
        role="sol_noop_verifier",
        parent_run_id=run_id,
        depends_on=("evidence_review",),
        scope={**common_scope, "executor": "script", "output_ref": "noop_receipt.json",
               "branch": "zero_candidates"},
        acceptance=("scan_coverage_verified", "rejection_ledger_written"),
        max_attempts=1,
        lease_timeout_seconds=300,
    )
    implementation = WorkOrder(
        work_order_id="implementation_worker",
        objective=(
            "Build ONLY the approved patch specification: fresh worktree from the frozen base "
            "(re-verify base; on drift re-run collision/provider checks, escalate back to "
            "evidence_review), minimal diff (no CRLF rewrites), commit as the pinned GitHub "
            "identity. No push."
        ),
        role="sol_builder",
        parent_run_id=run_id,
        depends_on=("evidence_review",),
        risk=RiskLevel.REPOSITORY_WRITE,
        write_owners=(run_id,),
        scope={**common_scope, "executor": "agent",
               "worktree": str(worktree_path),
               "approved_spec_ref": "approved_patch_spec.json",
               "changed_paths_allowlist": "changed_paths.allowlist",
               "commit_identity": {"name": github_user,
                                    "email": f"{github_user}@users.noreply.github.com"}},
        acceptance=("changed_paths_exact", "diff_is_minimal", "commit_identity_valid"),
        verifiers=(
            _verify("changed_paths_exact", "changed-paths", "--repo-path", str(worktree_path),
                    "--sha", "HEAD", "--allowlist", str(worktree_path / "changed_paths.allowlist")),
            _verify("diff_is_minimal", "diff-minimal", "--repo-path", str(worktree_path), "--sha", "HEAD"),
            _verify("commit_identity_valid", "commit-identity", "--repo-path", str(worktree_path),
                    "--sha", "HEAD", "--expect-name", github_user,
                    "--expect-email", f"{github_user}@users.noreply.github.com"),
        ),
        max_attempts=2,
        lease_timeout_seconds=1800,
    )
    deterministic_validation = WorkOrder(
        work_order_id="deterministic_validation",
        objective=(
            "Trusted script validation on the built commit: upstream json-tools three-step "
            "pipeline, hydration spot checks, CSV mechanics. Re-run for every new fix commit."
        ),
        role="sol_submission",
        parent_run_id=run_id,
        depends_on=("implementation_worker",),
        scope={**common_scope, "executor": "script", "json_tools_dir": str(json_tools_dir),
               "output_ref": "deterministic_validation.json"},
        acceptance=("schema_pipeline_passed", "hydration_verified"),
        max_attempts=2,
        lease_timeout_seconds=1200,
    )
    adversarial_review = WorkOrder(
        work_order_id="adversarial_review",
        objective=(
            "Independent adversarial audit of the final diff: read frozen evidence, approved "
            "spec, diff and precedents ONLY — not the implementer's narrative. Goal: reject. "
            "Neither evidence_review nor implementation_worker may hold this role."
        ),
        role="terra_adversarial",
        parent_run_id=run_id,
        depends_on=("implementation_worker",),
        scope={**common_scope, "executor": "agent",
               "diff_ref": "review.diff", "approved_spec_ref": "approved_patch_spec.json",
               "output_ref": "adversarial_review.json"},
        acceptance=("adversarial_review_approved",),
        closes_acceptance=("independent_review_approved",),
        max_attempts=1,
        lease_timeout_seconds=1800,
    )
    submission_bundle = WorkOrder(
        work_order_id="submission_bundle",
        objective=(
            "Assemble ready-to-submit artifact: reviewed commit SHA, base SHA, changed paths/"
            "cells, PR title/body (frozen upstream template), reward address, AI disclosure, "
            "U+200B == 10, validation hashes, fresh final collision scan (TTL 10 min), "
            "estimated eligible cells x modifier."
        ),
        role="sol_submission",
        parent_run_id=run_id,
        depends_on=("deterministic_validation", "adversarial_review"),
        scope={**common_scope, "executor": "script",
               "pr_body_ref": "pr_body.md", "submission_bundle_ref": "submission_bundle.json",
               "collision_ttl_minutes": 10},
        acceptance=("submission_template_valid", "final_collision_scan_zero"),
        verifiers=(
            _verify("submission_template_valid", "zwsp-count", "--file", str(run_dir / "pr_body.md"), "--expect", "10"),
            _verify("final_collision_scan_zero", "final-collision", "--repo", repo, "--slug", "<per-run slugs>"),
        ),
        max_attempts=2,
        lease_timeout_seconds=900,
    )
    publish = WorkOrder(
        work_order_id="publish",
        objective=(
            "Push the reviewed branch to the fork and open exactly one PR — ONLY when a valid, "
            "unexpired submit grant bound to this run/repo/user/address exists, no unaddressed "
            "reviewer feedback, WIP limit respected, final collision scan within TTL, and the "
            "pushed SHA equals the reviewed SHA. Never merge/close/label/fund anything."
        ),
        role="sol_publisher",
        parent_run_id=run_id,
        depends_on=("submission_bundle",),
        risk=RiskLevel.PRODUCTION_CHANGE,
        scope={**common_scope, "executor": "script",
               "submission_mode": submission_mode,
               "grant_path": str(grant_path) if grant_path else None,
               "reward_address": reward_address},
        acceptance=("submit_grant_valid", "pr_matches_reviewed_commit"),
        verifiers=(
            _verify("submit_grant_valid", "submit-grant", "--grant",
                    str(grant_path) if grant_path else "/nonexistent/grant.json",
                    "--run-id", run_id, "--repo", repo, "--github-user", github_user),
        ),
        max_attempts=1,
        lease_timeout_seconds=600,
    )
    post_publish = WorkOrder(
        work_order_id="post_publish_verification",
        objective=(
            "Verify the live PR: open, author, base/head, head SHA == reviewed SHA, body, reward "
            "address, AI disclosure, changed paths; CI must be green ON THE CURRENT HEAD — "
            "fork gate action_required/skipped counts as BLOCKED, not pass."
        ),
        role="sol_post_publish",
        parent_run_id=run_id,
        depends_on=("publish",),
        scope={**common_scope, "executor": "script", "output_ref": "post_publish.json"},
        acceptance=("pr_matches_reviewed_commit", "ci_matches_current_head"),
        max_attempts=2,
        lease_timeout_seconds=600,
    )
    ci_monitor = WorkOrder(
        work_order_id="ci_monitor",
        objective=(
            "Record the certified run receipt with branch-consistent terminal outcome; validate "
            "with the outcome-contract verifier."
        ),
        role="sol_post_publish",
        parent_run_id=run_id,
        depends_on=("post_publish_verification",),
        scope={**common_scope, "executor": "script", "receipt_ref": str(receipt_path)},
        acceptance=("terminal_outcome_certified",),
        verifiers=(
            _verify("terminal_outcome_certified", "outcome-contract", "--receipt", str(receipt_path)),
        ),
        max_attempts=1,
        lease_timeout_seconds=300,
    )

    work_orders: tuple[WorkOrder, ...] = (
        preflight,
        freeze_inputs,
        candidate_mcp,
        candidate_services,
        stale_delta,
        deferred_recheck,
        evidence_review,
        noop_verification,
        implementation,
        deterministic_validation,
        adversarial_review,
        submission_bundle,
        publish,
        post_publish,
        ci_monitor,
    ) if extended else (
        preflight,
        freeze_inputs,
        candidate_mcp,
        candidate_services,
        evidence_review,
        implementation,
        deterministic_validation,
        adversarial_review,
        submission_bundle,
    )
    return {
        "task": task,
        "profile": profile,
        "roles": roles,
        "work_orders": work_orders,
        "terminal_outcomes": TERMINAL_OUTCOMES,
    }


def tempfile_dir() -> str:
    import tempfile

    return tempfile.gettempdir()


def write_bundle(bundle: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "task.json").write_text(
        bundle["task"].model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "profile.json").write_text(
        bundle["profile"].model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "roles.json").write_text(
        json.dumps(
            [item.model_dump(mode="json") for item in bundle["roles"]],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "terminal_outcomes.json").write_text(
        json.dumps(bundle["terminal_outcomes"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    work_order_dir = output_dir / "work_orders"
    work_order_dir.mkdir(parents=True, exist_ok=True)
    for item in bundle["work_orders"]:
        (work_order_dir / f"{item.work_order_id}.json").write_text(
            item.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )


__all__ = [
    "DEFAULT_CATEGORIES",
    "DEFAULT_NETWORKS",
    "TERMINAL_OUTCOMES",
    "build_bundle",
    "capture_collision_snapshot",
    "snapshot_sha256",
    "write_bundle",
]
