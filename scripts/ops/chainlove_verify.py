#!/usr/bin/env python3
"""Trusted command verifiers for Chain.Love bounty gates.

Every subcommand checks REAL files, git state, GitHub state or CI — never an
agent's self-asserted booleans. Exit 0 = pass, 1 = fail, 2 = usage error.
Output (stdout) is captured as evidence by TrustedVerifierRunner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ZENSOR = "\u200b"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def fail(message: str) -> int:
    print(f"FAIL: {message}")
    return 1


def ok(message: str) -> int:
    print(f"PASS: {message}")
    return 0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    ).hexdigest()


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True, check=False
    )


def gh_json(*args: str) -> dict:
    proc = subprocess.run(
        ["gh", *args, "--json"], text=True, capture_output=True, check=False
    ) if False else subprocess.run(
        ["gh", "api", *args], text=True, capture_output=True, check=False
    )
    if proc.returncode:
        raise RuntimeError(f"gh api failed: {proc.stderr.strip()[:200]}")
    return json.loads(proc.stdout)


def _load_json_fail_closed(path_text: str):
    """Missing or corrupt evidence fails the gate; trusted verifiers never crash open."""

    path = Path(path_text)
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot read evidence {path}: {exc}")
        return None, 1


# --- gates -----------------------------------------------------------------

def gate_repo_identity(args: argparse.Namespace) -> int:
    repo = Path(args.repo_path)
    proc = git(repo, "remote", "get-url", "origin")
    if proc.returncode:
        return fail("cannot resolve origin remote")
    url = proc.stdout.strip()
    if args.expect_remote.rstrip("/") not in url:
        return fail(f"origin remote mismatch: {url}")
    return ok(f"origin={url}")


def gate_freeze_manifest(args: argparse.Namespace) -> int:
    manifest, rc = _load_json_fail_closed(args.manifest)
    if rc:
        return rc
    required = ["schema_version", "repo.base_sha", "snapshot.sha256", "claimed_index.sha256",
                "networks", "categories", "context_refs", "generator_commit"]
    for key in required:
        node: object = manifest
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return fail(f"manifest missing field: {key}")
            node = node[part]
    snapshot_path = Path(args.manifest).parent / "open_pr_snapshot.json"
    if snapshot_path.is_file():
        actual = sha256_payload(json.loads(snapshot_path.read_text(encoding="utf-8")))
        if actual != manifest["snapshot"]["sha256"]:
            return fail("snapshot hash does not match manifest")
    index_path = Path(manifest["claimed_index"]["path"])
    if not index_path.is_file():
        return fail(f"claimed index missing: {index_path}")
    if sha256_payload(json.loads(index_path.read_text(encoding="utf-8"))) != manifest["claimed_index"]["sha256"]:
        return fail("claimed index hash does not match manifest")
    return ok("manifest internally consistent")


def gate_claimed_index(args: argparse.Namespace) -> int:
    index, rc = _load_json_fail_closed(args.index)
    if rc:
        return rc
    if index.get("parse_errors"):
        return fail(f"parse_errors={index['parse_errors']}")
    for path, slugs in index.get("paths", {}).items():
        if not path.startswith(("listings/", "references/")) or not path.endswith(".csv"):
            return fail(f"non-target path in index: {path}")
        if "slug" in slugs:
            return fail(f"header slug leaked into index for {path}")
        for slug in slugs:
            if not SLUG_RE.fullmatch(slug):
                return fail(f"malformed slug in index: {slug}")
    if not index.get("paths") and not index.get("touching_prs"):
        return fail("index is empty with zero touching PRs — snapshot likely wrong")
    return ok(f"{len(index.get('paths', {}))} paths indexed, 0 parse errors")


def gate_context_hashes(args: argparse.Namespace) -> int:
    manifest, rc = _load_json_fail_closed(args.manifest)
    if rc:
        return rc
    for name, ref in manifest.get("context_refs", {}).items():
        path = Path(ref["path"])
        if not path.is_file():
            return fail(f"context {name} missing: {path}")
        if sha256_file(path) != ref["sha256"]:
            return fail(f"context {name} hash drift: {path}")
    return ok(f"{len(manifest.get('context_refs', {}))} context refs verified")


def gate_base_current(args: argparse.Namespace) -> int:
    repo = Path(args.repo_path)
    proc = git(repo, "rev-parse", "origin/main")
    if proc.returncode:
        return fail("cannot resolve origin/main")
    if proc.stdout.strip() != args.base_sha:
        return fail(
            f"base drift: manifest {args.base_sha[:9]} != origin/main {proc.stdout.strip()[:9]}; "
            "re-freeze required"
        )
    return ok(f"base {args.base_sha[:9]} is current origin/main")


def gate_commit_identity(args: argparse.Namespace) -> int:
    proc = git(Path(args.repo_path), "show", "-s", "--format=%an%n%ae", args.sha)
    if proc.returncode:
        return fail(f"cannot read commit {args.sha}")
    name, email = (proc.stdout.strip().splitlines() + [""])[:2]
    if name != args.expect_name or email != args.expect_email:
        return fail(f"commit identity {name} <{email}> != expected {args.expect_name} <{args.expect_email}>")
    return ok(f"commit identity {name} <{email}>")


def gate_changed_paths(args: argparse.Namespace) -> int:
    proc = git(Path(args.repo_path), "diff-tree", "--no-commit-id", "--name-only", "-r", args.sha)
    if proc.returncode:
        return fail("diff-tree failed")
    actual = set(proc.stdout.split())
    expected = {line.strip() for line in Path(args.allowlist).read_text(encoding="utf-8").splitlines() if line.strip()}
    if actual != expected:
        return fail(f"changed paths != allowlist: extra={sorted(actual - expected)} missing={sorted(expected - actual)}")
    return ok(f"changed paths exactly match allowlist ({len(actual)})")


def gate_diff_minimal(args: argparse.Namespace) -> int:
    proc = git(Path(args.repo_path), "show", "--numstat", "--format=", args.sha)
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        add, delete, path = line.split("\t")
        if int(delete) > args.max_deletions:
            return fail(f"{path}: {delete} deletions exceed budget {args.max_deletions}")
        if int(add) > args.max_additions:
            return fail(f"{path}: {add} additions exceed budget {args.max_additions}")
    return ok(f"diff within budget (<= {args.max_additions}+/{args.max_deletions}- per file)")


def gate_schema_pipeline(args: argparse.Namespace) -> int:
    import shutil
    import tempfile

    repo = Path(args.repo_path)
    with tempfile.TemporaryDirectory(prefix="cl-verify-") as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(repo, work, symlinks=True)
        for script in ("validate_csv.py", "csv_to_json.py", "validate.py"):
            shutil.copy(Path(args.json_tools_dir) / script, work / script)
        shutil.copytree(Path(args.json_tools_dir) / "meta", work / "meta", dirs_exist_ok=True)
        env = {**dict(__import__("os").environ), "PYTHONPATH": ""}
        for script in ("validate_csv.py", "csv_to_json.py", "validate.py"):
            proc = subprocess.run(
                [sys.executable, str(work / script)], cwd=work, env=env,
                text=True, capture_output=True, timeout=600,
            )
            print(f"--- {script} exit={proc.returncode}")
            print((proc.stdout or "")[-500:])
            if proc.returncode:
                return fail(f"{script} exited {proc.returncode}")
    return ok("json-tools three-step pipeline passed on patched tree")


def gate_hydration(args: argparse.Namespace) -> int:
    # Expects triples network:slug:present|absent from generated json/ dir
    for item in args.checks:
        network, slug, expected = item.split(":")
        path = Path(args.json_dir) / f"{network}.json"
        if not path.is_file():
            return fail(f"generated network json missing: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        found = any(
            row.get("slug") == slug
            for rows in data.values() if isinstance(rows, list)
            for row in rows if isinstance(row, dict)
        )
        if found != (expected == "present"):
            return fail(f"{network}/{slug}: expected {expected}, found={found}")
    return ok(f"{len(args.checks)} hydration checks passed")


def gate_zwsp_count(args: argparse.Namespace) -> int:
    body = Path(args.file).read_text(encoding="utf-8")
    count = body.count(ZENSOR)
    if count != args.expect:
        return fail(f"U+200B count {count} != {args.expect}")
    return ok(f"U+200B count == {args.expect}")


def gate_final_collision(args: argparse.Namespace) -> int:
    # Live delta scan: a slug claim lives in the PR's added rows (slug cell or
    # !offer:<slug> reference), not in changed-file paths.
    for slug in args.slugs:
        proc = subprocess.run(
            ["gh", "pr", "list", "--repo", args.repo, "--state", "open",
             "--search", slug, "--json", "number,title"],
            text=True, capture_output=True, check=False,
        )
        if proc.returncode:
            return fail(f"gh pr list failed for {slug}: {proc.stderr.strip()[:120]}")
        for pr in json.loads(proc.stdout or "[]"):
            diff_proc = subprocess.run(
                ["gh", "pr", "diff", str(pr["number"]), "--repo", args.repo],
                text=True, capture_output=True, check=False,
            )
            for line in (diff_proc.stdout or "").splitlines():
                if not line.startswith("+") or line.startswith("+++"):
                    continue
                cells = line[1:].split(",")
                row_slug = cells[0].strip() if cells else ""
                offer_ref = cells[2].strip() if len(cells) > 2 else ""
                if row_slug == slug or offer_ref == f"!offer:{slug}":
                    return fail(
                        f"open PR #{pr['number']} claims slug {slug} in an added row"
                    )
    return ok(f"zero collisions for {len(args.slugs)} slugs")


def gate_submit_grant(args: argparse.Namespace) -> int:
    grant, rc = _load_json_fail_closed(args.grant)
    if rc:
        return rc
    required = {"grant_id", "action", "repo", "github_user", "reward_address",
                "max_prs", "expires_at_utc", "run_id"}
    missing = required - set(grant)
    if missing:
        return fail(f"grant missing fields: {sorted(missing)}")
    if grant["action"] != "submit_pr":
        return fail(f"grant action {grant['action']} is not submit_pr")
    if grant["repo"] != args.repo:
        return fail(f"grant repo mismatch: {grant['repo']}")
    if grant["github_user"] != args.github_user:
        return fail(f"grant user mismatch: {grant['github_user']}")
    if grant["run_id"] != args.run_id:
        return fail(f"grant run mismatch: {grant['run_id']} != {args.run_id}")
    expires = datetime.fromisoformat(grant["expires_at_utc"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) >= expires:
        return fail(f"grant expired at {grant['expires_at_utc']}")
    return ok(f"submit grant {grant['grant_id']} valid until {grant['expires_at_utc']}")


def gate_pr_head(args: argparse.Namespace) -> int:
    pr = gh_json(f"repos/{args.repo}/pulls/{args.pr}")
    if pr["state"] != "open":
        return fail(f"PR state {pr['state']}")
    if pr["head"]["sha"] != args.expect_sha:
        return fail(f"PR head {pr['head']['sha'][:9]} != reviewed {args.expect_sha[:9]}")
    if pr["base"]["ref"] != "main":
        return fail(f"PR base {pr['base']['ref']} != main")
    return ok(f"PR #{args.pr} open, head matches reviewed commit, base main")


def gate_ci_head(args: argparse.Namespace) -> int:
    runs = gh_json(f"repos/{args.repo}/actions/runs?head_sha={args.expect_sha}&per_page=20")
    job_name = getattr(args, "job", "Validate JSON")
    checks_ok = 0
    for run in runs.get("workflow_runs", []):
        if run["name"] != job_name:
            continue
        if run["head_sha"] != args.expect_sha:
            continue
        conclusion = run["conclusion"]
        if conclusion == "success":
            checks_ok += 1
        elif conclusion in ("skipped", "action_required", "cancelled", None):
            return fail(
                f"{job_name} on head {args.expect_sha[:9]} is {conclusion or 'pending'} — "
                "fork gate BLOCKED, not pass"
            )
        elif conclusion == "failure":
            return fail(f"{job_name} failed on current head")
    if checks_ok == 0:
        return fail(f"no successful {job_name} run found for head {args.expect_sha[:9]}")
    return ok(f"{job_name} success on current head ({checks_ok} run(s))")


def gate_outcome_contract(args: argparse.Namespace) -> int:
    receipt, rc = _load_json_fail_closed(args.receipt)
    if rc:
        return rc
    outcome = receipt.get("outcome")
    candidate_count = receipt.get("candidate_count")
    gates = receipt.get("gates_satisfied", [])
    if outcome == "NOOP_VERIFIED":
        if candidate_count != 0:
            return fail("NOOP_VERIFIED with non-zero candidates")
        for needed in (getattr(args, "noop_gate_1", "scan_coverage_verified"),
                       getattr(args, "noop_gate_2", "rejection_ledger_written")):
            if needed not in gates:
                return fail(f"NOOP missing gate: {needed}")
    elif outcome == "READY_TO_SUBMIT":
        if not candidate_count:
            return fail("READY_TO_SUBMIT with zero candidates")
        for needed in (getattr(args, "ready_gate_1", "schema_pipeline_passed"),
                       getattr(args, "ready_gate_2", "adversarial_review_approved"),
                       getattr(args, "ready_gate_3", "final_collision_scan_zero")):
            if needed not in gates:
                return fail(f"READY_TO_SUBMIT missing gate: {needed}")
        if "submit_grant_valid" in gates and receipt.get("submitted") is not True:
            return fail("grant present but PR not submitted — inconsistent")
    elif outcome == "SUBMITTED":
        for needed in (getattr(args, "submitted_gate_1", "pr_matches_reviewed_commit"),
                       getattr(args, "submitted_gate_2", "ci_matches_current_head")):
            if needed not in gates:
                return fail(f"SUBMITTED missing gate: {needed}")
    else:
        allowed = {"DEFERRED", "REJECTED", "BLOCKED", "FAILED"}
        if outcome not in allowed:
            return fail(f"unknown outcome {outcome}")
        if not receipt.get("reason"):
            return fail(f"{outcome} requires a reason")
    return ok(f"outcome {outcome} satisfies branch contract")


# --- CLI -------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="gate", required=True)

    p = sub.add_parser("repo-identity"); p.add_argument("--repo-path", required=True); p.add_argument("--expect-remote", required=True); p.set_defaults(func=gate_repo_identity)
    p = sub.add_parser("freeze-manifest"); p.add_argument("--manifest", required=True); p.set_defaults(func=gate_freeze_manifest)
    p = sub.add_parser("claimed-index"); p.add_argument("--index", required=True); p.set_defaults(func=gate_claimed_index)
    p = sub.add_parser("context-hashes"); p.add_argument("--manifest", required=True); p.set_defaults(func=gate_context_hashes)
    p = sub.add_parser("base-current"); p.add_argument("--repo-path", required=True); p.add_argument("--base-sha", required=True); p.set_defaults(func=gate_base_current)
    p = sub.add_parser("commit-identity"); p.add_argument("--repo-path", required=True); p.add_argument("--sha", required=True); p.add_argument("--expect-name", required=True); p.add_argument("--expect-email", required=True); p.set_defaults(func=gate_commit_identity)
    p = sub.add_parser("changed-paths"); p.add_argument("--repo-path", required=True); p.add_argument("--sha", required=True); p.add_argument("--allowlist", required=True); p.set_defaults(func=gate_changed_paths)
    p = sub.add_parser("diff-minimal"); p.add_argument("--repo-path", required=True); p.add_argument("--sha", required=True); p.add_argument("--max-additions", type=int, default=50); p.add_argument("--max-deletions", type=int, default=10); p.set_defaults(func=gate_diff_minimal)
    p = sub.add_parser("schema-pipeline"); p.add_argument("--repo-path", required=True); p.add_argument("--json-tools-dir", required=True); p.set_defaults(func=gate_schema_pipeline)
    p = sub.add_parser("hydration"); p.add_argument("--json-dir", required=True); p.add_argument("--check", dest="checks", action="append", required=True); p.set_defaults(func=gate_hydration)
    p = sub.add_parser("zwsp-count"); p.add_argument("--file", required=True); p.add_argument("--expect", type=int, default=10); p.set_defaults(func=gate_zwsp_count)
    p = sub.add_parser("final-collision"); p.add_argument("--repo", required=True); p.add_argument("--slug", dest="slugs", action="append", required=True); p.set_defaults(func=gate_final_collision)
    p = sub.add_parser("submit-grant"); p.add_argument("--grant", required=True); p.add_argument("--run-id", required=True); p.add_argument("--repo", required=True); p.add_argument("--github-user", required=True); p.set_defaults(func=gate_submit_grant)
    p = sub.add_parser("pr-head"); p.add_argument("--repo", required=True); p.add_argument("--pr", required=True); p.add_argument("--expect-sha", required=True); p.set_defaults(func=gate_pr_head)
    p = sub.add_parser("ci-head"); p.add_argument("--repo", required=True); p.add_argument("--pr", required=True); p.add_argument("--expect-sha", required=True); p.add_argument("--job", default="Validate JSON"); p.set_defaults(func=gate_ci_head)
    p = sub.add_parser("outcome-contract")
    p.add_argument("--receipt", required=True)
    p.add_argument("--noop-gate-1", default="scan_coverage_verified")
    p.add_argument("--noop-gate-2", default="rejection_ledger_written")
    p.add_argument("--ready-gate-1", default="schema_pipeline_passed")
    p.add_argument("--ready-gate-2", default="adversarial_review_approved")
    p.add_argument("--ready-gate-3", default="final_collision_scan_zero")
    p.add_argument("--submitted-gate-1", default="pr_matches_reviewed_commit")
    p.add_argument("--submitted-gate-2", default="ci_matches_current_head")
    p.set_defaults(func=gate_outcome_contract)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        return fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    sys.exit(main())
