#!/usr/bin/env python3
"""Deterministic preflight + freeze_inputs for Chain.Love bounty runs.

Produces an immutable input manifest (open-PR snapshot, header-safe claimed-slug
index, context hashes) with fail-fast validation. No agent is involved; gates are
proven by chainlove_verify.py, not self-asserted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

FREEZE_SCHEMA_VERSION = "chainlove_freeze_manifest_v1"
CLAIMED_INDEX_SCHEMA_VERSION = "chainlove_claimed_index_v1"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DEFAULT_CATEGORIES = ("mcpservers", "security", "storages", "services")
RESERVED_SLUGS = {"slug"}  # CSV header cell, never a claim


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_payload(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    ).hexdigest()


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def append_decision(path: Path, entry: dict[str, Any]) -> None:
    """Append-only decision ledger entry with fsync durability."""
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"recorded_at_utc": utc_now(), **entry}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def run_git(repo_path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_path), *args], text=True, capture_output=True, check=False
    )
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def target_paths(networks: tuple[str, ...], categories: tuple[str, ...]) -> set[str]:
    paths: set[str] = {"references/providers/providers.csv"}
    listing_categories = set(categories) | {
        "apis", "explorers", "faucets", "sdks", "wallets", "analytics",
        "bridges", "oracles", "platforms", "ramps",
    }
    for network in networks:
        for category in listing_categories:
            paths.add(f"listings/specific-networks/{network}/{category}.csv")
    for category in categories:
        paths.add(f"listings/all-networks/{category}.csv")
        paths.add(f"references/offers/{category}.csv")
    return paths


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def preflight(
    *,
    repo_path: Path,
    expected_remote: str,
    runner: Callable[[list[str]], str] | None = None,
    github_user: str | None = None,
    wip_limit: int = 2,
) -> dict[str, Any]:
    runner = runner or (lambda argv: subprocess.run(
        argv, text=True, capture_output=True, check=True
    ).stdout)
    report: dict[str, Any] = {"checked_at_utc": utc_now(), "checks": {}}
    checks = report["checks"]

    if not (repo_path / ".git").exists():
        raise RuntimeError(f"repo path is not a git repository: {repo_path}")
    checks["repo_path_exists"] = True

    remote_url = run_git(repo_path, "remote", "get-url", "origin")
    if expected_remote.rstrip("/") not in remote_url and remote_url.rstrip("/") != expected_remote.rstrip("/"):
        raise RuntimeError(f"origin remote mismatch: {remote_url} != {expected_remote}")
    checks["origin_remote"] = remote_url

    status = run_git(repo_path, "status", "--short")
    checks["worktree_dirty"] = bool(status)
    report["dirty_paths"] = [line[3:] for line in status.splitlines() if line.strip()]

    fetch_error = ""
    fetch_variants = (
        ("fetch", "origin", "main"),
        ("fetch", "origin", "main"),
        ("-c", "http.version=HTTP/1.1", "fetch", "origin", "main"),  # local HTTP/2 flake
    )
    for attempt, variant in enumerate(fetch_variants, start=1):
        try:
            run_git(repo_path, *variant)
            fetch_error = ""
            break
        except RuntimeError as exc:
            fetch_error = str(exc)
            time.sleep(3 * attempt)
    if fetch_error:
        # git transport to github.com flaps locally while api.github.com is stable.
        # Integrity-preserving fallback: if the local origin/main SHA equals the
        # API-reported main SHA, the local ref IS current; otherwise fail closed.
        api_sha = _api_branch_sha(expected_remote)
        local_sha = run_git(repo_path, "rev-parse", "origin/main")
        if api_sha and api_sha == local_sha:
            checks["origin_main_fetched"] = True
            checks["origin_main_freshness"] = "verified_via_api_equivalence"
        else:
            raise RuntimeError(
                f"git fetch failed and local origin/main ({local_sha[:9]}) != API main "
                f"({api_sha[:9] if api_sha else 'unavailable'}); re-freeze required: "
                f"{fetch_error[:160]}"
            )
    else:
        checks["origin_main_fetched"] = True

    if github_user:
        own = _own_open_prs(expected_remote, github_user)
        changes_requested = [pr for pr in own if pr.get("active_changes_requested")]
        report["own_open_prs"] = [
            {"number": pr["number"], "title": pr.get("title", "")[:80],
             "review_decision": pr.get("review_decision")}
            for pr in own
        ]
        report["feedback_first_mode"] = bool(changes_requested)
        report["changes_requested_prs"] = [pr["number"] for pr in changes_requested]
        report["wip_unmerged_prs"] = len(own)
        report["wip_limit"] = wip_limit
        report["wip_exceeded"] = len(own) > wip_limit
    return report


def _own_open_prs(repo: str, github_user: str) -> list[dict[str, Any]]:
    """Own open PRs with review decisions; read-only, feeds feedback-first/WIP policy."""

    proc = subprocess.run(
        ["gh", "pr", "list", "--repo", repo, "--author", github_user,
         "--state", "open", "--json", "number,title,reviewDecision",
         "--limit", "30"],
        text=True, capture_output=True, check=False, timeout=60,
    )
    if proc.returncode or not (proc.stdout or "").strip():
        return []
    rows = json.loads(proc.stdout)
    own = []
    for row in rows:
        reviews: list[dict[str, Any]] = []
        view = subprocess.run(
            ["gh", "pr", "view", str(row["number"]), "--repo", repo,
             "--json", "reviews"],
            text=True, capture_output=True, check=False, timeout=60,
        )
        if view.returncode == 0:
            reviews = json.loads(view.stdout or "{}").get("reviews", [])
        own.append({
            "number": row["number"],
            "title": row.get("title", ""),
            "review_decision": row.get("reviewDecision"),
            "reviews": reviews,
            # only the LATEST substantive review counts; a resolved
            # CHANGES_REQUESTED in history is not active feedback
            "active_changes_requested": _latest_substantive_state(reviews)
            == "CHANGES_REQUESTED",
        })
    return own


def _latest_substantive_state(reviews: list[dict[str, Any]]) -> str | None:
    substantive = [
        r for r in reviews
        if r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")
    ]
    if not substantive:
        return None
    return max(substantive, key=lambda r: r.get("submittedAt", ""))["state"]


def _api_branch_sha(repo: str) -> str | None:
    owner, _, name = repo.partition("/")
    proc = subprocess.run(
        ["gh", "api", f"/repos/{owner}/{name}/branches/main"],
        text=True, capture_output=True, check=False, timeout=60,
    )
    if proc.returncode:
        return None
    try:
        return json.loads(proc.stdout)["commit"]["sha"]
    except (json.JSONDecodeError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Freeze inputs
# ---------------------------------------------------------------------------

def extract_claimed_slugs(
    diffs: dict[int, str], paths: set[str]
) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Parse per-PR unified diffs into path -> sorted claimed slugs.

    Excludes the CSV header cell ('slug'), empty cells and malformed rows;
    parse failures are counted, never silently dropped.
    """
    claimed: dict[str, set[str]] = {}
    parse_errors = 0
    for pr_diff in diffs.values():
        current: str | None = None
        for line in pr_diff.splitlines():
            match = re.match(r"^\+\+\+ b/(.+)$", line)
            if match:
                current = match.group(1) if match.group(1) in paths else None
                continue
            if current is None or not line.startswith("+") or line.startswith("+++"):
                continue
            slug = line[1:].split(",", 1)[0].strip()
            if slug in RESERVED_SLUGS or slug == "":
                continue
            if not SLUG_RE.fullmatch(slug):
                parse_errors += 1
                continue
            claimed.setdefault(current, set()).add(slug)
    return {path: sorted(slugs) for path, slugs in sorted(claimed.items())}, {"parse_errors": parse_errors}


def build_manifest(
    *,
    repo_path: Path,
    run_dir: Path,
    snapshot: dict[str, Any],
    claimed_index: dict[str, Any],
    context_refs: dict[str, Path],
    networks: tuple[str, ...],
    categories: tuple[str, ...],
    category_modifiers: dict[str, float] | None,
    generator_commit: str,
    base_sha_before_fetch: str | None = None,
) -> dict[str, Any]:
    base_sha = run_git(repo_path, "rev-parse", "origin/main")
    if base_sha_before_fetch and base_sha_before_fetch != base_sha:
        raise RuntimeError(
            "origin/main moved during freeze; re-freeze required "
            f"({base_sha_before_fetch} -> {base_sha})"
        )
    if not snapshot or not snapshot.get("pull_requests"):
        raise RuntimeError("open-PR snapshot is empty or malformed")
    if not claimed_index.get("paths") and claimed_index.get("parse_errors", 1) != 0:
        raise RuntimeError("claimed index is empty and parse status unknown")
    if claimed_index.get("parse_errors"):
        raise RuntimeError(
            f"claimed index has {claimed_index['parse_errors']} unparseable diff rows; refusing"
        )
    if not networks or not categories:
        raise RuntimeError("networks and categories must be non-empty")

    contexts = {}
    for name, path in context_refs.items():
        if not path.is_file():
            raise RuntimeError(f"context ref missing: {name} -> {path}")
        contexts[name] = {"path": str(path), "sha256": sha256_file(path)}

    manifest = {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "created_at_utc": utc_now(),
        "repo": {"path": str(repo_path), "base_sha": base_sha},
        "snapshot": {
            "captured_at_utc": snapshot.get("captured_at_utc"),
            "open_pr_count": snapshot.get("open_pr_count", len(snapshot.get("pull_requests", []))),
            "sha256": sha256_payload(snapshot),
        },
        "claimed_index": {
            "path": str(run_dir / "claimed_slugs.json"),
            "sha256": sha256_payload(claimed_index),
            "target_path_count": len(target_paths(networks, categories)),
            "parse_errors": 0,
        },
        "networks": list(networks),
        "categories": list(categories),
        "category_modifiers": category_modifiers or {},
        "context_refs": contexts,
        "generator_commit": generator_commit,
        "fail_fast": "passed",
    }
    atomic_write_json(run_dir / "claimed_slugs.json", claimed_index)
    atomic_write_json(run_dir / "freeze_manifest.json", manifest)
    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def gh_api_paginated(repo: str, path: str) -> Any:
    """One `gh api --paginate` call returns the full list; never re-paginate manually
    (a second full fetch loops forever once a page boundary is crossed)."""

    proc = subprocess.run(
        ["gh", "api", "--paginate", f"/repos/{repo}/{path}"],
        text=True, capture_output=True, check=False, timeout=600,
    )
    if proc.returncode:
        raise RuntimeError(f"gh api failed: {proc.stderr.strip()[:200]}")
    return json.loads(proc.stdout)


def capture_snapshot(repo: str) -> dict[str, Any]:
    prs = gh_api_paginated(repo, "pulls?state=open")
    snapshot = {
        "schema_version": "chainlove_open_pr_snapshot_v1",
        "repository": repo,
        "captured_at_utc": utc_now(),
        "open_pr_count": len(prs),
        "pull_requests": [
            {
                "number": pr["number"],
                "headRefName": pr["head"]["ref"],
                "updatedAt": pr["updated_at"],
                "files": [
                    {"path": f["filename"]}
                    for f in gh_api_paginated(repo, f"pulls/{pr['number']}/files")
                ],
            }
            for pr in prs
        ],
    }
    return snapshot


def capture_pr_diffs(repo: str, numbers: list[int], *, max_retries: int = 3) -> dict[int, str]:
    diffs: dict[int, str] = {}
    for number in numbers:
        last_error = ""
        for attempt in range(1, max_retries + 1):
            proc = subprocess.run(
                ["gh", "pr", "diff", str(number), "--repo", repo],
                text=True, capture_output=True, check=False, timeout=120,
            )
            if proc.returncode == 0:
                diffs[number] = proc.stdout
                last_error = ""
                break
            last_error = proc.stderr.strip()[:200]
            time.sleep(2 * attempt)  # transient HTTP/2 / rate-limit flakes
        if last_error:
            # Giant PRs kill `gh pr diff` streams; the REST diff endpoint on
            # api.github.com paginates cleanly — fall back to it.
            proc = subprocess.run(
                ["gh", "api", "-H", "Accept: application/vnd.diff",
                 f"/repos/{repo}/pulls/{number}"],
                text=True, capture_output=True, check=False, timeout=300,
            )
            if proc.returncode == 0 and proc.stdout.startswith("diff"):
                diffs[number] = proc.stdout
                continue
            raise RuntimeError(
                f"gh pr diff {number} failed after {max_retries} attempts "
                f"and REST fallback: {last_error} / {proc.stderr.strip()[:120]}"
            )
    return diffs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-path", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--repo", default="Chain-Love/chain-love")
    parser.add_argument("--github-user", default="jiaruihz")
    parser.add_argument("--wip-limit", type=int, default=2)
    parser.add_argument("--network", action="append", dest="networks",
                        default=["algorand", "filecoin", "somnia"])
    parser.add_argument("--category", action="append", dest="categories",
                        default=list(DEFAULT_CATEGORIES))
    parser.add_argument("--category-modifiers", help="JSON dict of frozen weekly modifiers")
    parser.add_argument("--context", action="append", default=[],
                        help="name=path context ref (precedents, deferred queue, stale inventory)")
    parser.add_argument("--snapshot-json", type=Path,
                        help="reuse an existing snapshot file instead of calling gh")
    args = parser.parse_args(argv)

    started = time.time()
    preflight_report = preflight(
        repo_path=args.repo_path, expected_remote=args.repo,
        github_user=args.github_user, wip_limit=args.wip_limit,
    )
    args.run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.run_dir / "preflight_report.json", preflight_report)

    paths = target_paths(tuple(args.networks), tuple(args.categories))
    if args.snapshot_json:
        snapshot = json.loads(args.snapshot_json.read_text(encoding="utf-8"))
    else:
        snapshot = capture_snapshot(args.repo)
        atomic_write_json(args.run_dir / "open_pr_snapshot.json", snapshot)

    touching = [
        pr["number"] for pr in snapshot["pull_requests"]
        if any(f["path"] in paths for f in pr["files"])
    ]
    diffs = capture_pr_diffs(args.repo, touching)
    claimed_paths, err = extract_claimed_slugs(diffs, paths)
    claimed_index = {
        "schema_version": CLAIMED_INDEX_SCHEMA_VERSION,
        "captured_at_utc": utc_now(),
        "touching_prs": touching,
        "parse_errors": err["parse_errors"],
        "paths": claimed_paths,
    }

    context_refs = {}
    for item in args.context:
        name, _, raw = item.partition("=")
        context_refs[name] = Path(raw)

    generator_commit = run_git(Path(__file__).resolve().parents[2], "rev-parse", "HEAD")
    manifest = build_manifest(
        repo_path=args.repo_path,
        run_dir=args.run_dir,
        snapshot=snapshot,
        claimed_index=claimed_index,
        context_refs=context_refs,
        networks=tuple(args.networks),
        categories=tuple(args.categories),
        category_modifiers=json.loads(args.category_modifiers) if args.category_modifiers else None,
        generator_commit=generator_commit,
    )
    elapsed = round(time.time() - started, 1)
    print(json.dumps({
        "manifest": str(args.run_dir / "freeze_manifest.json"),
        "base_sha": manifest["repo"]["base_sha"],
        "open_pr_count": manifest["snapshot"]["open_pr_count"],
        "touching_prs": len(touching),
        "claimed_paths": len(claimed_paths),
        "elapsed_seconds": elapsed,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
