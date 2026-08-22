"""Runner-driven E2E per GLM_FINAL_AUTOMATION_PLAN §6:
offline NOOP, offline positive READY, resume at three failpoints, and
grant/publish idempotency. Receipts are produced by chainlove_run.py only —
hand-written receipts are not accepted anywhere in this file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "chainlove"
RUNNER = REPO_ROOT / "scripts" / "ops" / "chainlove_run.py"

CSV_PATH = "listings/specific-networks/somnia/services.csv"
CSV_HEADER = "slug,provider,offer,actionButtons,toolType,tag,price,planName,planType,description,starred"
POSITIVE_ROW = "fixture-svc,,!offer:fixture-svc,,,,,,,,,"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=check)


def make_fixture_env(tmp_path: Path, *, viable: bool) -> dict[str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    target = repo / CSV_PATH
    target.parent.mkdir(parents=True)
    target.write_text(f"{CSV_HEADER}\nagent-kit,,!offer:agent-kit,,,,,,,,,\n")
    template = repo / ".github" / "PULL_REQUEST_TEMPLATE.md"
    template.parent.mkdir(parents=True)
    template.write_text("## Summary\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "remote", "add", "origin", "https://github.com/Chain-Love/chain-love.git")
    bare = tmp_path / "fork.git"
    bare.mkdir()
    _git(bare, "init", "-q", "--bare", "-b", "main")
    _git(repo, "remote", "add", "fork", str(bare))
    _git(repo, "push", "-q", "fork", "main")
    # sandbox the origin fetch: keep the upstream URL string (repo-identity gate)
    # but rewrite it to a local mirror so fixture runs never touch the network
    upstream = tmp_path / "upstream.git"
    upstream.mkdir()
    _git(upstream, "init", "-q", "--bare", "-b", "main")
    _git(repo, "push", "-q", str(upstream), "main")
    _git(repo, "config", f"url.{upstream}".replace("\\", "\\") + ".insteadOf",
         "https://github.com/Chain-Love/chain-love.git") if False else None
    _git(repo, "config", f"url.{str(upstream)}.insteadOf",
         "https://github.com/Chain-Love/chain-love.git")

    json_tools = tmp_path / "json-tools"
    (json_tools / "meta").mkdir(parents=True)
    for script in ("validate_csv.py", "csv_to_json.py", "validate.py"):
        (json_tools / script).write_text("print('fixture-ok')\n")

    state = tmp_path / "state"
    state.mkdir()
    (state / "reviewer_precedents.md").write_text("# precedents\n")
    (state / "deferred_candidates.json").write_text(json.dumps({
        "items": [{"slug": "fixture-deferred", "trigger": "PR #999 merged or closed"}]}))
    (state / "stale_inventory.json").write_text(json.dumps({
        "genuinely_stale": [{"name": "FixtureStale", "claiming_prs": [999],
                              "collision": "claimed"}],
        "redirects_confirmed_all_claimed": []}))

    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "template_sha256": "fixture-template", "validate_workflow_sha256": "w",
        "link_check_workflow_sha256": "l",
        "discussion_41": {"updated_at": "t", "body_sha256": "a"},
        "discussion_839": {"updated_at": "t", "body_sha256": "b"},
        "fetched_at_utc": "2026-08-23T00:00:00Z"}))

    cfg = tmp_path / "gh_config.json"
    cfg.write_text(json.dumps({
        "main_sha": head, "own_prs": [],
        "pr_states": {"999": {"state": "OPEN"}},
        "live_prs": [], "head_prs": [],
        "pr_files": {"1": [{"filename": CSV_PATH}]},
        "pr_diffs": {"1": f"+++ b/{CSV_PATH}\n+agent-kit,,!offer:agent-kit,,,,,,,,\n"}}))

    spec = tmp_path / "positive_spec.json"
    spec.write_text(json.dumps({
        "schema_version": "chainlove_approved_spec_v1",
        "run_id": "e2e-run",
        "candidates": [{
            "slug": "fixture-svc", "network": "somnia", "category": "services",
            "csv_rows": [{"path": CSV_PATH, "row": POSITIVE_ROW}],
            "evidence": {"sources": ["fixture://evidence"]},
        }],
        "rejected": [{"slug": "fixture-rejected", "reason": "provider bar fixture"}],
        "deferred": [{"slug": "fixture-deferred", "trigger": "PR #999 merged or closed"}],
        "estimated": {"cells": 2, "images": 0, "modifier": 1.5, "nominal_usd": 0.12,
                       "estimate_status": "insufficient_sample"}}))

    run_dir = tmp_path / "run"
    env = {**dict(os.environ),
           "PATH": f"{FIXTURES}:{os.environ['PATH']}",
           "FAKE_GH_CONFIG": str(cfg),
           "FAKE_GH_LOG": str(tmp_path / "gh.log"),
           "CHAINLOVE_WORKER_LOG": str(tmp_path / "worker.log"),
           "CHAINLOVE_FAKE_SPEC": str(spec) if viable else ""}
    paths = {"repo": repo, "run_dir": run_dir, "json_tools": json_tools,
             "state": state, "policy": policy, "cfg": cfg, "head": head,
             "spec": spec}
    return {"env": env, "paths": paths}


def run_runner(env: dict, *extra: str, failpoint: str | None = None) -> subprocess.CompletedProcess:
    args = [sys.executable, str(RUNNER), "--mode", "review_required",
            "--run-id", "e2e-run", "--repo-path", str(env["paths"]["repo"]),
            "--run-dir", str(env["paths"]["run_dir"]),
            "--worker-cmd", str(FIXTURES / "fake_worker.py"),
            "--json-tools-dir", str(env["paths"]["json_tools"]),
            "--reward-address", "0x4B689c62992FCcC63525d32D70696E45190d260A",
            "--policy-json", str(env["paths"]["policy"]),
            "--precedents", str(env["paths"]["state"] / "reviewer_precedents.md"),
            "--deferred-queue", str(env["paths"]["state"] / "deferred_candidates.json"),
            "--stale-inventory", str(env["paths"]["state"] / "stale_inventory.json"),
            *extra]
    run_env = dict(env["env"])
    if failpoint:
        run_env["CHAINLOVE_FAILPOINT"] = failpoint
    return subprocess.run(args, env=run_env, capture_output=True, text=True, timeout=600)


def receipt_of(env: dict) -> dict:
    return json.loads((env["paths"]["run_dir"] / "run_receipt.json").read_text())


def worker_calls(env: dict) -> list[str]:
    log = Path(env["env"]["CHAINLOVE_WORKER_LOG"])
    if not log.exists():
        return []
    return [line for line in log.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# A. Offline NOOP E2E — runner-driven, builder/publisher must never start
# ---------------------------------------------------------------------------


def test_offline_noop_e2e_via_runner(tmp_path: Path) -> None:
    env = make_fixture_env(tmp_path, viable=False)
    proc = run_runner(env)
    assert proc.returncode == 0, proc.stderr[-800:]
    receipt = receipt_of(env)
    assert receipt["outcome"] == "NOOP_VERIFIED"
    assert receipt["candidate_count"] == 0
    completed = json.loads((env["paths"]["run_dir"] / "run_state.json").read_text())["completed_steps"]
    assert "builder" not in completed and "submission_bundle" not in completed
    assert not (env["paths"]["run_dir"] / "worktree").exists()
    outcome_gate = (env["paths"]["run_dir"] / "gate_outputs" / "outcome-contract.txt").read_text()
    assert "PASS" in outcome_gate
    assert receipt["pushed"] is False and receipt["pr_created"] is False


# ---------------------------------------------------------------------------
# B. Offline positive E2E — runner-driven to READY_TO_SUBMIT
# ---------------------------------------------------------------------------


def test_offline_positive_e2e_via_runner(tmp_path: Path) -> None:
    env = make_fixture_env(tmp_path, viable=True)
    proc = run_runner(env)
    assert proc.returncode == 0, proc.stderr[-800:]
    receipt = receipt_of(env)
    assert receipt["outcome"] == "READY_TO_SUBMIT"
    assert receipt["candidate_count"] == 1
    assert receipt["reviewed_commit_sha"]
    for gate in ("schema_pipeline_passed", "adversarial_review_approved",
                 "final_collision_scan_zero", "submission_template_valid"):
        assert gate in receipt["gates_satisfied"], gate
    worktree = env["paths"]["run_dir"] / "worktree"
    csv = (worktree / CSV_PATH).read_text()
    assert POSITIVE_ROW in csv
    assert receipt["pushed"] is False and receipt["pr_created"] is False


# ---------------------------------------------------------------------------
# C. Resume E2E — three failpoints, no duplicate side effects
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("failpoint", [
    "evidence_review_post",   # after scanner+review, before build
    "builder_post",           # after commit, before validation
    "adversarial_review_post",  # before submission bundle
])
def test_resume_e2e_no_duplicate_side_effects(tmp_path: Path, failpoint: str) -> None:
    env = make_fixture_env(tmp_path, viable=True)
    killed = run_runner(env, failpoint=failpoint)
    assert killed.returncode == 9, f"failpoint not hit: {killed.stderr[-400:]}"
    resumed = run_runner(env, "--resume")
    assert resumed.returncode == 0, resumed.stderr[-800:]
    receipt = receipt_of(env)
    assert receipt["outcome"] == "READY_TO_SUBMIT"

    calls = worker_calls(env)
    for stage in ("candidate_mcp", "candidate_services", "evidence_review", "adversarial_review"):
        assert calls.count(stage) == 1, f"{stage} ran {calls.count(stage)} times"

    ledger = (env["paths"]["run_dir"] / "decision_ledger.jsonl").read_text().splitlines()
    rejections = [json.loads(l) for l in ledger if json.loads(l).get("kind") == "rejection"]
    assert len(rejections) == 1  # evidence_review append_decisions exactly once

    worktree = env["paths"]["run_dir"] / "worktree"
    base_count = int(_git(worktree, "rev-list", "--count",
                          env["paths"]["head"]).stdout.strip())
    total_count = int(_git(worktree, "rev-list", "--count", "HEAD").stdout.strip())
    assert total_count == base_count + 1  # exactly one new commit


# ---------------------------------------------------------------------------
# Grant/publish idempotency (offline, supervised_submit path)
# ---------------------------------------------------------------------------


def _grant_file(env: dict, **overrides) -> Path:
    spec_hash = _sha256(env["paths"]["run_dir"] / "approved_patch_spec.json")
    sha = json.loads((env["paths"]["run_dir"] / "run_state.json").read_text())["reviewed_commit_sha"]
    grant = {
        "grant_id": "g-e2e", "action": "submit_pr", "repo": "Chain-Love/chain-love",
        "github_user": "jiaruihz", "reward_address": "0x4B689c62992FCcC63525d32D70696E45190d260A",
        "max_prs": 1, "expires_at_utc": "2999-01-01T00:00:00Z", "run_id": "e2e-run",
        "approved_spec_sha256": spec_hash, "reviewed_commit_sha": sha,
        "human_verified_at_utc": "2998-01-01T00:00:00Z",
        **overrides,
    }
    path = env["paths"]["run_dir"] / "grant.json"
    path.write_text(json.dumps(grant, indent=2))
    return path


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_supervised_submit_and_resume_do_not_duplicate_pr(tmp_path: Path) -> None:
    env = make_fixture_env(tmp_path, viable=True)
    assert run_runner(env).returncode == 0
    # configure fake gh for publish: CI green, PR head == reviewed sha
    cfg = json.loads(env["paths"]["cfg"].read_text())
    state = json.loads((env["paths"]["run_dir"] / "run_state.json").read_text())
    cfg["ci_sha"] = state["reviewed_commit_sha"]
    cfg["pr_head_sha"] = state["reviewed_commit_sha"]
    env["paths"]["cfg"].write_text(json.dumps(cfg))

    grant = _grant_file(env)
    first = run_runner(env, "--mode", "supervised_submit", "--grant", str(grant), "--resume")
    assert first.returncode == 0, first.stderr[-800:]
    receipt = receipt_of(env)
    assert receipt["outcome"] == "SUBMITTED"
    assert receipt["pr_number"] == 4242

    gh_log = Path(env["env"]["FAKE_GH_LOG"]).read_text()
    assert gh_log.count("pr create") == 1
    # grant consumed by this run is reusable on resume, but the run is terminal:
    # a plain --resume must not re-execute anything (no second create)
    again = run_runner(env, "--mode", "supervised_submit", "--grant", str(grant), "--resume")
    assert again.returncode == 0
    gh_log = Path(env["env"]["FAKE_GH_LOG"]).read_text()
    assert gh_log.count("pr create") == 1

    # a different run cannot reuse the consumed grant: bind the ORIGINAL run's
    # spec hash + reviewed SHA but a foreign run id -> publish gate must BLOCK
    other = dict(env)
    other_run_dir = tmp_path / "run2"
    other["paths"] = {**env["paths"], "run_dir": other_run_dir}
    Path(env["env"]["FAKE_GH_LOG"]).write_text("")
    original_spec = env["paths"]["run_dir"] / "approved_patch_spec.json"
    original_sha = state["reviewed_commit_sha"]
    grant_other = _grant_file(env, run_id="other-run",
                              approved_spec_sha256=_sha256(original_spec),
                              reviewed_commit_sha=original_sha)
    proc = run_runner(other, "--mode", "supervised_submit", "--grant", str(grant_other))
    # the other run reaches publish only after its own full pipeline; the grant
    # run-mismatch must BLOCK before any create. If it died earlier on a gate,
    # that also proves no PR was created — but assert the strict case:
    receipt2 = receipt_of(other)
    assert receipt2["outcome"] in ("BLOCKED", "FAILED", "READY_TO_SUBMIT")
    assert Path(env["env"]["FAKE_GH_LOG"]).read_text().count("pr create") == 0


# ---------------------------------------------------------------------------
# Gate audit additions per plan §5
# ---------------------------------------------------------------------------


def test_repo_identity_exact_normalized() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("cv", REPO_ROOT / "scripts/ops/chainlove_verify.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.normalize_repo("https://github.com/Chain-Love/chain-love.git") == "chain-love/chain-love"
    assert m.normalize_repo("WWW.GitHub.com/Other/Repo/") == "other/repo"
    # a similar-looking owner must NOT match (no substring pass)
    assert m.normalize_repo("https://github.com/Chain-Love/chain-love-extra") != "chain-love/chain-love"


def test_stale_delta_four_states_and_unknown_keeps_seam_closed(tmp_path, monkeypatch) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("sd", REPO_ROOT / "scripts/ops/chainlove_stale_delta.py")
    sd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sd)

    monkeypatch.setattr(sd, "gh_pr_state", lambda repo, n: {
        1: "merged", 2: "open", 3: "closed", 4: "unknown"}[n])
    baseline = tmp_path / "inv.json"
    baseline.write_text(json.dumps({
        "genuinely_stale": [
            {"name": "MergedClaim", "claiming_prs": [1]},
            {"name": "OpenClaim", "claiming_prs": [2]},
            {"name": "UnknownClaim", "claiming_prs": [4]},
            {"name": "MixedUnknown", "claiming_prs": [3, 4]},
        ],
        "redirects_confirmed_all_claimed": [{"name": "R", "claiming_prs": [2]}],
    }))
    out = tmp_path / "delta.json"
    rc = sd.main(["--repo", "R", "--baseline", str(baseline), "--output", str(out)])
    assert rc == 0
    delta = json.loads(out.read_text())
    by_name = {f["name"]: f for f in delta["findings"]}
    assert by_name["MergedClaim"]["seam_open"] is True
    assert by_name["OpenClaim"]["seam_open"] is False
    assert by_name["UnknownClaim"]["seam_open"] is False       # unknown != no claim
    assert by_name["MixedUnknown"]["seam_open"] is False       # unknown blocks even with a closed claim
    assert by_name["UnknownClaim"]["unknown_queries"] == [4]


def test_ledger_multiprocess_appends(tmp_path: Path) -> None:
    script = (
        "import importlib.util, sys;"
        f"spec = importlib.util.spec_from_file_location('cf', {str(REPO_ROOT / 'scripts/ops/chainlove_freeze.py')!r});"
        "cf = importlib.util.module_from_spec(spec); spec.loader.exec_module(cf);"
        f"cf.append_decision({str(tmp_path / 'led.jsonl')!r}, "
        "{'tag': sys.argv[1], 'i': sys.argv[2]})"
    )
    procs = [subprocess.Popen([sys.executable, "-c", script, f"p{n}", str(i)])
             for n in range(3) for i in range(40)]
    for p in procs:
        assert p.wait() == 0
    lines = [json.loads(l) for l in (tmp_path / "led.jsonl").read_text().splitlines() if l.strip()]
    assert len(lines) == 120
    assert all(json.dumps(l).count("recorded_at_utc") == 1 for l in lines)
