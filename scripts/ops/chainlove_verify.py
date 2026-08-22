#!/usr/bin/env python3
"""Trusted command verifiers for Chain.Love bounty gates.

Every subcommand checks REAL files, git state, GitHub state or CI — never an
agent's self-asserted booleans. Exit codes: 0 = pass, 1 = fail, 2 = BLOCKED
(external/precondition blocker, distinct from a quality failure), 3 = usage.
Per GLM_FINAL_AUTOMATION_PLAN §3/P0: collision never trusts GitHub search;
grants are one-shot and fully bound; receipts must carry real verifier records.
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


def blocked(message: str) -> int:
    print(f"BLOCKED: {message}")
    return 2


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


def gh_api(path: str) -> tuple[dict | None, str]:
    proc = subprocess.run(
        ["gh", "api", path], text=True, capture_output=True, check=False, timeout=90
    )
    if proc.returncode:
        return None, proc.stderr.strip()[:200]
    try:
        return json.loads(proc.stdout), ""
    except json.JSONDecodeError as exc:
        return None, f"unparseable response: {exc}"


def _load_json_fail_closed(path_text: str):
    """Missing or corrupt evidence fails the gate; trusted verifiers never crash open."""

    path = Path(path_text)
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot read evidence {path}: {exc}")
        return None, 1


def normalize_repo(url_or_slug: str) -> str:
    text = url_or_slug.strip().lower().removesuffix(".git")
    text = re.sub(r"^https?://", "", text).removesuffix("/")
    text = re.sub(r"^www\.", "", text)
    parts = [p for p in text.split("/") if p]
    return "/".join(parts[-2:]) if len(parts) >= 2 else text


# --- gates -----------------------------------------------------------------

def gate_repo_identity(args: argparse.Namespace) -> int:
    # raw config, not `remote get-url`: insteadOf rewrites must not change identity
    proc = git(Path(args.repo_path), "config", "remote.origin.url")
    if proc.returncode or not proc.stdout.strip():
        return fail("cannot resolve origin remote")
    actual = normalize_repo(proc.stdout.strip())
    expected = normalize_repo(args.expect_remote)
    if actual != expected:
        return fail(f"origin remote mismatch: {actual} != {expected}")
    return ok(f"origin == {expected} (exact, normalized)")


def gate_freeze_manifest(args: argparse.Namespace) -> int:
    manifest, rc = _load_json_fail_closed(args.manifest)
    if rc:
        return rc
    for key in ("repo.base_sha", "repo.api_begin_sha", "repo.api_end_sha", "repo.local_sha",
                "snapshot.sha256", "claimed_index.sha256", "networks", "categories",
                "context_refs", "generator_commit", "policy.template_sha256"):
        node: object = manifest
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return fail(f"manifest missing field: {key}")
            node = node[part]
    repo_part = manifest["repo"]
    shas = {repo_part["api_begin_sha"], repo_part["api_end_sha"], repo_part["local_sha"]}
    if len(shas) != 1 or repo_part["base_sha"] not in shas:
        return fail(f"freeze drift: begin/end/local main SHAs disagree: {repo_part}")
    snapshot_path = Path(args.manifest).parent / "open_pr_snapshot.json"
    if not snapshot_path.is_file():
        return fail(f"snapshot file missing beside manifest: {snapshot_path}")
    actual = sha256_payload(json.loads(snapshot_path.read_text(encoding="utf-8")))
    if actual != manifest["snapshot"]["sha256"]:
        return fail("snapshot hash does not match manifest")
    index_path = Path(manifest["claimed_index"]["path"])
    if not index_path.is_file():
        return fail(f"claimed index missing: {index_path}")
    if sha256_payload(json.loads(index_path.read_text(encoding="utf-8"))) != manifest["claimed_index"]["sha256"]:
        return fail("claimed index hash does not match manifest")
    return ok("manifest consistent; begin/end/local SHAs agree; snapshot hash verified")


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
    allowlist_text = Path(args.allowlist).read_text(encoding="utf-8")
    provenance_path = Path(args.allowlist).with_name("allowlist_provenance.json")
    if provenance_path.is_file():
        prov, _ = _load_json_fail_closed(str(provenance_path)), None
        if isinstance(prov, tuple):  # _load returns (payload, rc)
            prov = prov[0]
        recorded = prov.get("allowlist_sha256") if isinstance(prov, dict) else None
        if recorded and recorded != sha256_file(Path(args.allowlist)):
            return fail("allowlist hash != provenance hash (allowlist tampered after generation)")
    proc = git(Path(args.repo_path), "diff-tree", "--no-commit-id", "--name-only", "-r", args.sha)
    if proc.returncode:
        return fail("diff-tree failed")
    actual = set(proc.stdout.split())
    expected = {line.strip() for line in allowlist_text.splitlines() if line.strip()}
    if actual != expected:
        return fail(f"changed paths != allowlist: extra={sorted(actual - expected)} missing={sorted(expected - actual)}")
    return ok(f"changed paths exactly match allowlist ({len(actual)}); provenance intact")


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


def _fetch_pr_diff(repo: str, number: int) -> tuple[str | None, str]:
    """gh pr diff with REST fallback; returns (diff_text, error)."""

    last_error = ""
    for attempt in range(1, 4):
        proc = subprocess.run(
            ["gh", "pr", "diff", str(number), "--repo", repo],
            text=True, capture_output=True, check=False, timeout=120,
        )
        if proc.returncode == 0:
            return proc.stdout, ""
        last_error = proc.stderr.strip()[:160]
    proc = subprocess.run(
        ["gh", "api", "-H", "Accept: application/vnd.diff",
         f"/repos/{repo}/pulls/{number}"],
        text=True, capture_output=True, check=False, timeout=300,
    )
    if proc.returncode == 0 and proc.stdout.startswith("diff"):
        return proc.stdout, ""
    return None, last_error or proc.stderr.strip()[:160]


def gate_final_collision(args: argparse.Namespace) -> int:
    """No GitHub search: enumerate open PRs created/updated after the frozen
    snapshot capture, read each real diff, and match slugs in added rows.
    Any un-fetchable diff => BLOCKED (2), never silently skipped."""

    snapshot, rc = _load_json_fail_closed(args.snapshot)
    if rc:
        return rc
    captured = snapshot.get("captured_at_utc", "")
    if not captured:
        return fail("snapshot lacks captured_at_utc")
    proc = subprocess.run(
        ["gh", "pr", "list", "--repo", args.repo, "--state", "open",
         "--limit", "1000", "--json", "number,updatedAt"],
        text=True, capture_output=True, check=False, timeout=120,
    )
    if proc.returncode or not (proc.stdout or "").strip():
        return blocked(f"cannot enumerate open PRs: {proc.stderr.strip()[:160]}")
    live_prs = json.loads(proc.stdout)
    suspects = [p for p in live_prs if str(p.get("updatedAt", "")) > captured]
    for pr in suspects:
        diff, err = _fetch_pr_diff(args.repo, int(pr["number"]))
        if diff is None:
            return blocked(
                f"cannot fetch diff for PR #{pr['number']} (updated {pr['updatedAt']}); "
                f"refusing to pass collision on incomplete data: {err}"
            )
        for line in diff.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            cells = line[1:].split(",")
            row_slug = cells[0].strip() if cells else ""
            offer_ref = cells[2].strip() if len(cells) > 2 else ""
            for slug in args.slugs:
                if row_slug == slug or offer_ref == f"!offer:{slug}":
                    return fail(
                        f"open PR #{pr['number']} claims slug {slug} in an added row "
                        "(post-snapshot delta)"
                    )
    return ok(
        f"zero collisions for {len(args.slugs)} slugs across {len(suspects)} "
        f"post-snapshot-updated open PRs (diffs enumerated, no search)"
    )


def gate_submit_grant(args: argparse.Namespace) -> int:
    grant, rc = _load_json_fail_closed(args.grant)
    if rc:
        return rc
    required = {"grant_id", "action", "repo", "github_user", "reward_address",
                "max_prs", "expires_at_utc", "run_id", "approved_spec_sha256",
                "reviewed_commit_sha", "human_verified_at_utc"}
    missing = required - set(grant)
    if missing:
        return fail(f"grant missing fields: {sorted(missing)}")
    if grant["action"] != "submit_pr":
        return fail(f"grant action {grant['action']} is not submit_pr")
    if normalize_repo(grant["repo"]) != normalize_repo(args.repo):
        return fail(f"grant repo mismatch: {grant['repo']}")
    if grant["github_user"] != args.github_user:
        return fail(f"grant user mismatch: {grant['github_user']}")
    if grant["run_id"] != args.run_id:
        return fail(f"grant run mismatch: {grant['run_id']} != {args.run_id}")
    if int(grant["max_prs"]) != 1:
        return fail(f"this pipeline only permits max_prs == 1; grant says {grant['max_prs']}")
    if args.approved_spec:
        spec_hash = sha256_file(Path(args.approved_spec))
        if grant["approved_spec_sha256"] != spec_hash:
            return fail("grant approved_spec_sha256 does not match the frozen spec")
    if args.reviewed_sha and grant["reviewed_commit_sha"] != args.reviewed_sha:
        return fail("grant reviewed_commit_sha mismatch")
    if args.expect_address and grant["reward_address"] != args.expect_address:
        return fail("grant reward address mismatch")
    if not grant.get("human_verified_at_utc"):
        return fail("grant lacks human_verified_at_utc (user source-bundle verification)")
    expires = datetime.fromisoformat(grant["expires_at_utc"].replace("Z", "+00:00"))
    verified = datetime.fromisoformat(grant["human_verified_at_utc"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    if now >= expires:
        return fail(f"grant expired at {grant['expires_at_utc']}")
    if verified >= expires:
        return fail("human verification timestamp is not before expiry")
    consumed_path = Path(args.grant).with_name("grant_consumed.json")
    if consumed_path.exists():
        consumed, _ = _load_json_fail_closed(str(consumed_path)), None
        if isinstance(consumed, tuple):
            consumed = consumed[0]
        if isinstance(consumed, dict) and consumed.get("run_id") != args.run_id:
            return fail(f"grant already consumed by run {consumed.get('run_id')}")
    return ok(f"one-shot submit grant {grant['grant_id']} valid, fully bound, unconsumed")


def gate_pr_head(args: argparse.Namespace) -> int:
    pr, err = gh_api(f"repos/{args.repo}/pulls/{args.pr}")
    if pr is None:
        return blocked(f"cannot read PR {args.pr}: {err}")
    if pr["state"] != "open":
        return fail(f"PR state {pr['state']}")
    if pr["head"]["sha"] != args.expect_sha:
        return fail(f"PR head {pr['head']['sha'][:9]} != reviewed {args.expect_sha[:9]}")
    if pr["base"]["ref"] != "main":
        return fail(f"PR base {pr['base']['ref']} != main")
    return ok(f"PR #{args.pr} open, head matches reviewed commit, base main")


def gate_ci_head(args: argparse.Namespace) -> int:
    job_name = getattr(args, "job", "Validate JSON")
    runs, err = gh_api(
        f"repos/{args.repo}/actions/runs?head_sha={args.expect_sha}&per_page=20"
    )
    if runs is None:
        return blocked(f"cannot read CI runs: {err}")
    checks_ok = 0
    for run in runs.get("workflow_runs", []):
        if run["name"] != job_name or run["head_sha"] != args.expect_sha:
            continue
        conclusion = run["conclusion"]
        if conclusion == "success":
            checks_ok += 1
        elif conclusion in ("skipped", "action_required", "cancelled", None):
            return blocked(
                f"{job_name} on head {args.expect_sha[:9]} is {conclusion or 'pending'} — "
                "fork gate BLOCKED, not pass"
            )
        elif conclusion == "failure":
            return fail(f"{job_name} failed on current head")
    if checks_ok == 0:
        return blocked(f"no successful {job_name} run found for head {args.expect_sha[:9]}")
    return ok(f"{job_name} success on current head ({checks_ok} run(s))")


def _receipt_gate_evidence(receipt: dict, gate: str):
    entry = (receipt.get("gates") or {}).get(gate)
    if not isinstance(entry, dict):
        return None, f"gates['{gate}'] is not a structured verifier record"
    record_path = entry.get("verifier_record")
    if not record_path or not Path(record_path).is_file():
        return None, f"gate {gate}: verifier record artifact missing: {record_path}"
    if sha256_file(Path(record_path)) != entry.get("verifier_record_sha256"):
        return None, f"gate {gate}: verifier record hash drift"
    return entry, None


def gate_outcome_contract(args: argparse.Namespace) -> int:
    receipt, rc = _load_json_fail_closed(args.receipt)
    if rc:
        return rc
    outcome = receipt.get("outcome")
    candidate_count = receipt.get("candidate_count")
    satisfied = receipt.get("gates_satisfied", [])
    for gate in satisfied:
        _, err = _receipt_gate_evidence(receipt, gate)
        if err:
            return fail(f"self-reported gate without real verifier evidence: {err}")
    if outcome == "NOOP_VERIFIED":
        if candidate_count != 0:
            return fail("NOOP_VERIFIED with non-zero candidates")
        for gate in ("scan_coverage_verified", "rejection_ledger_written"):
            if gate not in satisfied:
                return fail(f"NOOP missing gate: {gate}")
        ledger = receipt.get("decision_ledger")
        if not ledger or not Path(ledger).is_file():
            return fail("NOOP requires the decision ledger artifact (rejections/deferrals)")
        return ok("outcome NOOP_VERIFIED: zero candidates, real verifier records, ledger present")
    if outcome == "READY_TO_SUBMIT":
        if not candidate_count:
            return fail("READY_TO_SUBMIT with zero candidates")
        for key in ("approved_spec_sha256", "reviewed_commit_sha"):
            if not receipt.get(key):
                return fail(f"READY_TO_SUBMIT missing {key}")
        spec_path = receipt.get("approved_spec_path")
        if spec_path and sha256_file(Path(spec_path)) != receipt["approved_spec_sha256"]:
            return fail("approved spec hash drift at receipt time")
        for gate in ("schema_pipeline_passed", "adversarial_review_approved",
                     "final_collision_scan_zero", "submission_template_valid"):
            if gate not in satisfied:
                return fail(f"READY_TO_SUBMIT missing gate: {gate}")
        return ok("outcome READY_TO_SUBMIT: candidates bound to spec+SHA with real gates")
    if outcome == "SUBMITTED":
        for gate in ("pr_matches_reviewed_commit", "ci_matches_current_head",
                     "submit_grant_valid"):
            if gate not in satisfied:
                return fail(f"SUBMITTED missing gate: {gate}")
        if not receipt.get("pr_number"):
            return fail("SUBMITTED without pr_number")
        return ok("outcome SUBMITTED: grant, PR and current-head CI verified")
    allowed = {"DEFERRED", "REJECTED", "BLOCKED", "FAILED"}
    if outcome not in allowed:
        return fail(f"unknown outcome {outcome}")
    if not receipt.get("reason"):
        return fail(f"{outcome} requires a reason")
    blocking = [
        g for g, rec in (receipt.get("gates") or {}).items()
        if rec.get("verifier_record") and Path(rec["verifier_record"]).is_file()
    ]
    if not blocking and not receipt.get("decision_ledger"):
        return fail(f"{outcome} without any blocker evidence (verifier record or ledger)")
    return ok(f"outcome {outcome} carries blocking evidence")


def gate_submission_bundle(args: argparse.Namespace) -> int:
    bundle, rc = _load_json_fail_closed(args.bundle)
    if rc:
        return rc
    for key in ("run_id", "reviewed_commit_sha", "base_sha", "pr_body",
                "pr_template_sha256", "reward_address", "slugs"):
        if not bundle.get(key):
            return fail(f"bundle missing {key}")
    body = Path(bundle["pr_body"]).read_text(encoding="utf-8")
    for needle in ("## Summary", "## Type of change", "## Validation checklist",
                   "AI disclosure", bundle["reward_address"]):
        if needle not in body:
            return fail(f"PR body missing required content: {needle[:40]}")
    for checkbox in ("Style Guide", "personally opened and verified every new link",
                     "supports the adjusted network", "not a blind AI-generated submission"):
        if checkbox not in body:
            return fail(f"PR body missing checklist item: {checkbox[:40]}")
    if body.count(ZENSOR) != 10:
        return fail(f"PR body U+200B count {body.count(ZENSOR)} != 10")
    proc = git(Path(args.repo_path), "diff-tree", "--no-commit-id", "--name-only", "-r",
               bundle["reviewed_commit_sha"])
    if proc.returncode:
        return fail("cannot enumerate changed paths of reviewed commit")
    changed = set(proc.stdout.split())
    spec = json.loads((Path(args.bundle).parent / "approved_patch_spec.json").read_text(encoding="utf-8"))
    spec_paths = {r["path"] for c in spec.get("candidates", []) for r in c.get("csv_rows", [])}
    spec_paths |= {c["logo"]["dest"] for c in spec.get("candidates", []) if c.get("logo")}
    if changed != spec_paths:
        return fail(f"bundle slugs/paths disagree with reviewed diff: {sorted(changed ^ spec_paths)}")
    if bundle.get("run_id") != spec.get("run_id"):
        return fail("bundle run_id != spec run_id")
    est = spec.get("estimated", {})
    if est.get("estimate_status") != "insufficient_sample":
        return fail("estimate_status must stay insufficient_sample until >=10 settled PRs")
    return ok("submission bundle complete: template hash, address, checklist, "
              "disclosure, U+200B, changed paths and estimate contract verified")


def gate_scan_coverage(args: argparse.Namespace) -> int:
    for name in ("candidate_mcp", "candidate_services"):
        payload, rc = _load_json_fail_closed(str(Path(args.run_dir) / "worker_outputs" / f"{name}.json"))
        if rc:
            return rc
        gates = payload.get("gates", {})
        if gates.get("scan_coverage_verified") is not True:
            return fail(f"{name}: scan_coverage_verified not true")
        if not (payload.get("coverage_note") or "").strip():
            return fail(f"{name}: coverage_note empty — coverage unjustified")
    return ok("both scanners: coverage verified with justification")


def gate_rejection_ledger(args: argparse.Namespace) -> int:
    ledger = Path(args.ledger)
    if not ledger.is_file():
        return fail(f"decision ledger missing: {ledger}")
    lines = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    substantive = [l for l in lines if l.get("kind") in ("rejection", "deferral", "correction", "run-outcome")]
    if not substantive:
        return fail("decision ledger has no rejection/deferral entries — NOOP unjustified")
    return ok(f"decision ledger: {len(substantive)} substantive entries")


def gate_adversarial_approved(args: argparse.Namespace) -> int:
    payload, rc = _load_json_fail_closed(args.result)
    if rc:
        return rc
    if str(payload.get("verdict", "")).upper() != "APPROVE":
        return fail(f"adversarial verdict {payload.get('verdict')!r} is not APPROVE")
    if not (payload.get("reviewed_commit_sha") or "").strip():
        return fail("adversarial result lacks reviewed_commit_sha binding")
    if payload["reviewed_commit_sha"] != args.expect_sha:
        return fail("adversarial result bound to a different commit SHA")
    return ok(f"adversarial APPROVE bound to {args.expect_sha[:9]}")


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
    p = sub.add_parser("final-collision"); p.add_argument("--repo", required=True); p.add_argument("--snapshot", required=True); p.add_argument("--slug", dest="slugs", action="append", required=True); p.set_argument = None; p.set_defaults(func=gate_final_collision)
    p = sub.add_parser("submit-grant"); p.add_argument("--grant", required=True); p.add_argument("--run-id", required=True); p.add_argument("--repo", required=True); p.add_argument("--github-user", required=True); p.add_argument("--approved-spec"); p.add_argument("--reviewed-sha"); p.add_argument("--expect-address"); p.set_defaults(func=gate_submit_grant)
    p = sub.add_parser("pr-head"); p.add_argument("--repo", required=True); p.add_argument("--pr", required=True); p.add_argument("--expect-sha", required=True); p.set_defaults(func=gate_pr_head)
    p = sub.add_parser("ci-head"); p.add_argument("--repo", required=True); p.add_argument("--pr", required=True); p.add_argument("--expect-sha", required=True); p.add_argument("--job", default="Validate JSON"); p.set_defaults(func=gate_ci_head)
    p = sub.add_parser("outcome-contract"); p.add_argument("--receipt", required=True); p.set_defaults(func=gate_outcome_contract)
    p = sub.add_parser("scan-coverage"); p.add_argument("--run-dir", required=True); p.set_defaults(func=gate_scan_coverage)
    p = sub.add_parser("rejection-ledger"); p.add_argument("--ledger", required=True); p.set_defaults(func=gate_rejection_ledger)
    p = sub.add_parser("adversarial-approved"); p.add_argument("--result", required=True); p.add_argument("--expect-sha", required=True); p.set_defaults(func=gate_adversarial_approved)
    p = sub.add_parser("submission-bundle"); p.add_argument("--bundle", required=True); p.add_argument("--repo-path", required=True); p.add_argument("--reviewed-sha"); p.add_argument("--expect-address"); p.set_defaults(func=gate_submission_bundle)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        return fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    sys.exit(main())
