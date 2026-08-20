"""Reusable Chain.Love bounty orchestration profile."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from ..contracts import AuthoritySpec, BudgetSpec, DomainSpec, RiskLevel, TaskSpec
from ..orchestration import (
    JsonAssertion,
    RequestProfile,
    RoleSpec,
    VerifierKind,
    VerifierSpec,
    WorkOrder,
)
from ..orchestration.contracts import AssertionOperator


DEFAULT_NETWORKS = ("algorand", "filecoin", "somnia")
DEFAULT_CATEGORIES = ("mcpservers", "security", "storages")


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
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--limit",
                "1000",
                "--json",
                "number,title,body,headRefName,updatedAt,files",
            ]
        )
    )
    captured_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": "chainlove_open_pr_snapshot_v1",
        "repository": repo,
        "captured_at_utc": captured_at,
        "open_pr_count": len(rows),
        "pull_requests": rows,
    }


def _assertions(evidence_ref: str, *paths: str) -> tuple[VerifierSpec, ...]:
    return tuple(
        VerifierSpec(
            acceptance_id=path.rsplit(".", 1)[-1],
            kind=VerifierKind.JSON_ASSERTIONS,
            evidence_ref=evidence_ref,
            assertions=(
                JsonAssertion(path=f"$.gates.{path.rsplit('.', 1)[-1]}", operator=AssertionOperator.TRUTHY),
            ),
        )
        for path in paths
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
    extended: bool = False,
    category_modifiers: dict[str, float] | None = None,
    deferred_queue_path: Path | None = None,
) -> dict[str, Any]:
    """Return validated task, routing, roles and bounded WorkOrders."""

    frozen = {
        "base_ref": "origin/main",
        "base_sha": base_sha,
        "open_pr_snapshot": str(snapshot_path.resolve()),
        "open_pr_snapshot_sha256": snapshot_sha256,
        "open_pr_snapshot_captured_at_utc": snapshot_captured_at_utc,
        "reward_address": reward_address,
    }
    task = TaskSpec(
        run_id=run_id,
        task_type="chainlove.bounty_batch",
        objective="Build, independently verify, and submit one source-backed Chain.Love bounty batch.",
        scope={
            "repo": repo,
            "repo_path": str(repo_path.resolve()),
            "eligible_networks": networks,
            "categories": categories,
            "target_pr": "new",
        },
        acceptance=(
            "candidate_set_verified",
            "repository_patch_validated",
            "independent_review_approved",
            "pull_request_submitted",
        ),
        authority=AuthoritySpec(
            auto_execute=(
                RiskLevel.READ_ONLY,
                RiskLevel.DERIVED_DATA_WRITE,
                RiskLevel.REPOSITORY_WRITE,
            )
        ),
        budgets=BudgetSpec(
            max_actions=24,
            max_failures=2,
            max_experiments=0,
            patience=2,
            max_parallelism=2,
        ),
        frozen_inputs=frozen,
        domain=DomainSpec(initial_phase="ORCHESTRATE", terminal_phase="DONE"),
    )
    profile = RequestProfile(
        objective=task.objective,
        risk=RiskLevel.REPOSITORY_WRITE,
        estimated_stages=4,
        independent_workstreams=2,
        needs_iteration=True,
        needs_resume=True,
        needs_independent_review=True,
        explicit_harness=True,
        explicit_multi_agent=True,
    )
    roles = (
        RoleSpec(
            name="luna_scanner",
            requested_model="gpt-5.6-luna",
            reasoning_effort="medium",
            sandbox="read-only",
            require_usage=True,
        ),
        RoleSpec(
            name="terra_reviewer",
            requested_model="gpt-5.6-terra",
            reasoning_effort="high",
            sandbox="read-only",
            require_usage=True,
        ),
        RoleSpec(
            name="luna_verifier",
            requested_model="gpt-5.6-luna",
            reasoning_effort="medium",
            sandbox="read-only",
            require_usage=True,
        ),
        RoleSpec(
            name="luna_checker",
            requested_model="gpt-5.6-luna",
            reasoning_effort="low",
            sandbox="read-only",
            require_usage=True,
        ),
    )
    common_scope = {
        "repository": repo,
        "repo_path": str(repo_path.resolve()),
        "base_sha": base_sha,
        "collision_snapshot_ref": str(snapshot_path.resolve()),
        "collision_snapshot_sha256": snapshot_sha256,
        "eligible_networks": networks,
        "source_policy": "current primary sources only",
    }
    if category_modifiers:
        priority_hint = {
            "category_modifier_hint": dict(category_modifiers),
            "category_priority_rule": (
                "Prefer categories whose current weekly modifier is >= 1.0; "
                "categories below 1.0 are discounted at compensation time."
            ),
        }
    else:
        priority_hint = {}
    scanner_usage_cap = (
        "Cap exploration at ~40 tool calls; early-exit once 8 viable candidates are "
        "verified or the seam is demonstrably swept."
    )
    candidate_mcp = WorkOrder(
        work_order_id="candidate_mcp",
        objective=(
            "Produce a bounded MCP candidate matrix. Read the frozen open-PR snapshot once; "
            "query only exact candidate names/slugs for post-snapshot deltas. "
            + scanner_usage_cap
        ),
        role="luna_scanner",
        parent_run_id=run_id,
        scope={**common_scope, **priority_hint, "categories": ("mcpservers",)},
        acceptance=("snapshot_bound", "collision_scan_complete", "weak_candidates_excluded"),
        verifiers=_assertions(
            "candidate_mcp.json",
            "gates.snapshot_bound",
            "gates.collision_scan_complete",
            "gates.weak_candidates_excluded",
        ),
        max_attempts=2,
        lease_timeout_seconds=900,
    )
    candidate_services = WorkOrder(
        work_order_id="candidate_services",
        objective=(
            "Produce a bounded security/storage candidate matrix with sourceable required fields. "
            "Reuse the frozen collision snapshot and reject unsupported commercial claims. "
            + scanner_usage_cap
        ),
        role="luna_scanner",
        parent_run_id=run_id,
        scope={**common_scope, **priority_hint, "categories": tuple(item for item in categories if item != "mcpservers")},
        acceptance=("snapshot_bound", "collision_scan_complete", "commercial_fields_supported"),
        verifiers=_assertions(
            "candidate_services.json",
            "gates.snapshot_bound",
            "gates.collision_scan_complete",
            "gates.commercial_fields_supported",
        ),
        max_attempts=2,
        lease_timeout_seconds=900,
    )
    evidence_review = WorkOrder(
        work_order_id="evidence_review",
        objective=(
            "Adjudicate only shortlisted candidates against primary sources, canonical schema, "
            "semantic duplication, and exact post-snapshot collision deltas."
        ),
        role="terra_reviewer",
        parent_run_id=run_id,
        depends_on=("candidate_mcp", "candidate_services"),
        scope={**common_scope, "review_input_refs": ("candidate_mcp.json", "candidate_services.json")},
        acceptance=("approved_candidate_set_written", "network_mapping_supported", "required_fields_supported"),
        verifiers=_assertions(
            "evidence_review.json",
            "gates.approved_candidate_set_written",
            "gates.network_mapping_supported",
            "gates.required_fields_supported",
        ),
        closes_acceptance=("candidate_set_verified",),
        max_attempts=1,
        lease_timeout_seconds=1200,
    )
    implementation_review = WorkOrder(
        work_order_id="implementation_review",
        objective=(
            "Mechanically review the staged CSV patch against approved evidence, schemas, hydration, "
            "sort order, URLs, and a targeted fresh collision delta."
        ),
        role="luna_verifier",
        parent_run_id=run_id,
        depends_on=("evidence_review",),
        scope={**common_scope, "approved_evidence_ref": "evidence_review.json"},
        acceptance=("schema_pipeline_passed", "hydration_verified", "evidence_matches_diff", "final_collision_scan_zero"),
        verifiers=_assertions(
            "implementation_review.json",
            "gates.schema_pipeline_passed",
            "gates.hydration_verified",
            "gates.evidence_matches_diff",
            "gates.final_collision_scan_zero",
        ),
        closes_acceptance=("repository_patch_validated", "independent_review_approved"),
        max_attempts=1,
        lease_timeout_seconds=900,
    )
    publish_scope = {
        **common_scope,
        "expected_reward_address": reward_address,
        "author_policy": "GitHub user identity only; do not label Codex as submitter or author",
    }
    publish_verification = WorkOrder(
        work_order_id="publish_verification",
        objective=(
            "Verify the draft PR is open on the intended base/head, matches the reviewed commit and "
            "scope, and uses the explicitly supplied reward address (or none if absent)."
        ),
        role="luna_checker",
        parent_run_id=run_id,
        depends_on=("implementation_review",),
        scope=publish_scope,
        acceptance=("pr_open", "head_matches_reviewed_commit", "reward_address_checked"),
        verifiers=_assertions(
            "publish_verification.json",
            "gates.pr_open",
            "gates.head_matches_reviewed_commit",
            "gates.reward_address_checked",
        ),
        closes_acceptance=("pull_request_submitted",),
        max_attempts=1,
        lease_timeout_seconds=600,
    )
    work_orders: tuple[WorkOrder, ...] = (
        candidate_mcp,
        candidate_services,
        evidence_review,
        implementation_review,
        publish_verification,
    )
    if extended:
        # Extended seams (2026-08-21, from the batch7/8 post-mortems): the new-offer
        # seam is mostly swept, so runs also need a verified stale-data inventory and
        # a deferred-candidate queue whose triggers are rechecked cheaply.
        evidence_review_inputs = list(evidence_review.scope["review_input_refs"])
        stale_sweep = WorkOrder(
            work_order_id="stale_sweep",
            objective=(
                "Build a verified stale-data inventory for providers referenced by eligible-network "
                "outputs: dead links, cross-domain redirects, defunct or pivoted providers, and "
                "changed pricing. Treat local curl 000 as unverified and re-check through an "
                "independent fetcher before proposing removals; classify 403/429 as bot-protected. "
                "Cap at ~40 tool calls."
            ),
            role="luna_scanner",
            parent_run_id=run_id,
            scope={**common_scope, "prior_scan_ref": None},
            acceptance=("sweep_snapshot_bound", "findings_source_verified", "collision_status_recorded"),
            verifiers=_assertions(
                "stale_inventory.json",
                "gates.sweep_snapshot_bound",
                "gates.findings_source_verified",
                "gates.collision_status_recorded",
            ),
            max_attempts=2,
            lease_timeout_seconds=1200,
        )
        deferred_recheck = WorkOrder(
            work_order_id="deferred_recheck",
            objective=(
                "Recheck each deferred candidate's trigger condition (e.g. 'PR #2663 merged') and "
                "emit the subset whose triggers now hold, ready for the next candidate batch. "
                "Low-effort mechanical checks only."
            ),
            role="luna_checker",
            parent_run_id=run_id,
            scope={
                **common_scope,
                "deferred_queue_read_path": str(deferred_queue_path.resolve())
                if deferred_queue_path
                else None,
            },
            acceptance=("triggers_rechecked", "ready_subset_written"),
            verifiers=_assertions(
                "deferred_ready.json",
                "gates.triggers_rechecked",
                "gates.ready_subset_written",
            ),
            max_attempts=1,
            lease_timeout_seconds=600,
        )
        work_orders = work_orders + (stale_sweep, deferred_recheck)
        evidence_review.scope = {
            **evidence_review.scope,
            "review_input_refs": (*evidence_review_inputs, "stale_inventory.json", "deferred_ready.json"),
            "deferred_queue_write_path": str(deferred_queue_path.resolve())
            if deferred_queue_path
            else None,
        }
    return {
        "task": task,
        "profile": profile,
        "roles": roles,
        "work_orders": work_orders,
    }


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
    work_order_dir = output_dir / "work_orders"
    work_order_dir.mkdir(parents=True, exist_ok=True)
    for item in bundle["work_orders"]:
        (work_order_dir / f"{item.work_order_id}.json").write_text(
            item.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )


def snapshot_sha256(snapshot: dict[str, Any]) -> str:
    raw = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = [
    "DEFAULT_CATEGORIES",
    "DEFAULT_NETWORKS",
    "build_bundle",
    "capture_collision_snapshot",
    "snapshot_sha256",
    "write_bundle",
]
