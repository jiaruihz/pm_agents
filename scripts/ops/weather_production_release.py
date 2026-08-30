#!/usr/bin/env python3
"""Safely create and pin an immutable weather production release.

This command deliberately owns only the small, auditable release transition:
an exact detached worktree plus the matching two fields in ``production.yaml``.
It never restarts a runtime and defaults to a no-write dry run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml


SHA_RE = re.compile(r"[0-9a-f]{40}")
RELEASE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


class ReleaseError(RuntimeError):
    """A validation failure that must leave the existing pin unchanged."""


def _git(repo: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ReleaseError(f"git {' '.join(args)} failed: {detail}")
    return completed.stdout.strip()


def _full_sha(value: str, label: str) -> str:
    value = value.lower()
    if not SHA_RE.fullmatch(value):
        raise ReleaseError(f"{label} must be a full 40-character lowercase SHA")
    return value


def _load_spec(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        raw_text = path.read_text()
        loaded = yaml.safe_load(raw_text)
    except (OSError, yaml.YAMLError) as exc:
        raise ReleaseError(f"cannot read production spec {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ReleaseError("production spec must be a mapping")
    return loaded, {"raw_text": raw_text}


def _release_block(spec: dict[str, Any], release_id: str) -> dict[str, Any]:
    releases = spec.get("production_releases")
    if not isinstance(releases, list):
        raise ReleaseError("production_releases must be a list")
    matches = [item for item in releases if isinstance(item, dict) and item.get("release_id") == release_id]
    if len(matches) != 1:
        raise ReleaseError(f"release_id must occur exactly once: {release_id}")
    item = matches[0]
    if not isinstance(item.get("checkout_root"), str) or not isinstance(item.get("expected_repo_sha"), str):
        raise ReleaseError(f"release {release_id} lacks checkout_root or expected_repo_sha")
    return item


def _verify_checkout(checkout: Path, expected_sha: str) -> dict[str, str]:
    if not checkout.is_dir():
        raise ReleaseError(f"release checkout is not a directory: {checkout}")
    head = _git(checkout, "rev-parse", "HEAD").lower()
    if head != expected_sha:
        raise ReleaseError(f"existing release HEAD mismatch: expected {expected_sha}, observed {head}")
    tracked_status = _git(checkout, "status", "--porcelain", "--untracked-files=no")
    if tracked_status:
        raise ReleaseError(f"existing release has tracked changes: {checkout}")
    return {"head": head, "tracked_clean": "true"}


def _replace_release_pin(raw_text: str, release_id: str, old_root: Path, old_sha: str, new_root: Path, new_sha: str) -> str:
    """Replace exactly the two simple scalar lines inside one release list item."""
    item_re = re.compile(
        rf"(?ms)^(?P<indent>[ \t]*)- release_id: {re.escape(release_id)}[ \t]*\n"
        rf"(?P<body>.*?)(?=^(?P=indent)- release_id:|\Z)"
    )
    matches = list(item_re.finditer(raw_text))
    if len(matches) != 1:
        raise ReleaseError(f"textual release block must occur exactly once: {release_id}")
    match = matches[0]
    body = match.group("body")
    indent = match.group("indent")
    property_indent = indent + "  "
    old_root_line = f"{property_indent}checkout_root: {old_root}"
    old_sha_line = f"{property_indent}expected_repo_sha: {old_sha}"
    if body.count(old_root_line + "\n") != 1 or body.count(old_sha_line + "\n") != 1:
        raise ReleaseError("release block text does not exactly match expected old pin")
    if len(re.findall(rf"(?m)^{re.escape(property_indent)}checkout_root:", body)) != 1:
        raise ReleaseError("release block checkout_root is not unique")
    if len(re.findall(rf"(?m)^{re.escape(property_indent)}expected_repo_sha:", body)) != 1:
        raise ReleaseError("release block expected_repo_sha is not unique")
    new_body = body.replace(old_root_line + "\n", f"{property_indent}checkout_root: {new_root}\n")
    new_body = new_body.replace(old_sha_line + "\n", f"{property_indent}expected_repo_sha: {new_sha}\n")
    return raw_text[: match.start("body")] + new_body + raw_text[match.end("body") :]


def _atomic_write(path: Path, content: str) -> None:
    mode = path.stat().st_mode
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def run_release(*, release_id: str, source_sha: str, expected_old_sha: str, production_spec: Path, apply: bool, confirm_production: bool, reason: str) -> dict[str, Any]:
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise ReleaseError("release_id may contain only letters, digits, _ and -")
    source_sha = _full_sha(source_sha, "source_sha")
    expected_old_sha = _full_sha(expected_old_sha, "expected_old_sha")
    if apply and (not confirm_production or not reason.strip()):
        raise ReleaseError("--apply requires --confirm-production and a non-empty --reason")

    production_spec = production_spec.absolute()
    spec, text = _load_spec(production_spec)
    release = _release_block(spec, release_id)
    old_sha = _full_sha(str(release["expected_repo_sha"]), "existing expected_repo_sha")
    if old_sha != expected_old_sha:
        raise ReleaseError(f"expected-old-sha mismatch: spec has {old_sha}, requested {expected_old_sha}")
    operational_repo = Path(str(spec.get("operational_repo_root") or ""))
    release_root = Path(str(spec.get("production_release_root") or ""))
    if not operational_repo.is_absolute() or not operational_repo.is_dir():
        raise ReleaseError("operational_repo_root must be an existing absolute directory")
    if not release_root.is_absolute():
        raise ReleaseError("production_release_root must be absolute")
    old_root = Path(str(release["checkout_root"]))
    expected_old_root = release_root / release_id / old_sha
    if old_root != expected_old_root:
        raise ReleaseError("existing checkout_root does not match the immutable release layout")
    target = release_root / release_id / source_sha
    if target != target.absolute() or target.parent.parent != release_root:
        raise ReleaseError("computed release target escapes production_release_root")

    resolved_source = _git(operational_repo, "rev-parse", f"{source_sha}^{{commit}}").lower()
    if resolved_source != source_sha:
        raise ReleaseError(f"source SHA does not resolve exactly: {source_sha} -> {resolved_source}")
    operational_head = _git(operational_repo, "rev-parse", "HEAD").lower()
    ancestor = subprocess.run(
        ["git", "-C", str(operational_repo), "merge-base", "--is-ancestor", operational_head, source_sha],
        check=False,
    ).returncode == 0
    if not ancestor:
        raise ReleaseError("source SHA is not a descendant of the current operational HEAD")

    checks: dict[str, Any] = {
        "release_id_exists_once": True,
        "old_pin_matches": True,
        "source_resolves_exactly": True,
        "operational_head_is_ancestor": True,
        "target_layout": str(target),
    }
    existed = target.exists() or target.is_symlink()
    if existed:
        checks["existing_checkout"] = _verify_checkout(target, source_sha)
    else:
        checks["existing_checkout"] = "will_create"

    venv_target = operational_repo / ".venv"
    venv_link = target / ".venv"
    if venv_link.exists() or venv_link.is_symlink():
        if not venv_link.is_symlink() or venv_link.resolve() != venv_target.resolve():
            raise ReleaseError(f"release .venv conflicts with required operational symlink: {venv_link}")
        checks["venv"] = "existing_valid_symlink"
    elif not venv_target.is_dir():
        raise ReleaseError(f"operational .venv is missing: {venv_target}")
    else:
        checks["venv"] = "will_create_symlink"

    replacement = _replace_release_pin(text["raw_text"], release_id, old_root, old_sha, target, source_sha)
    checks["pin_text_replacement"] = True
    report: dict[str, Any] = {
        "schema_version": "weather_production_release_v1",
        "mode": "apply" if apply else "dry_run",
        "release_id": release_id,
        "reason": reason.strip() if apply else None,
        "old": {"checkout_root": str(old_root), "expected_repo_sha": old_sha},
        "new": {"checkout_root": str(target), "expected_repo_sha": source_sha},
        "operational_head": operational_head,
        "checks": checks,
        "pin_updated": False,
        "worktree_created": False,
    }
    if not apply:
        return report

    if not existed:
        target.parent.mkdir(parents=True, exist_ok=True)
        _git(operational_repo, "worktree", "add", "--detach", str(target), source_sha)
        report["worktree_created"] = True
    _verify_checkout(target, source_sha)
    if not (venv_link.exists() or venv_link.is_symlink()):
        os.symlink(venv_target, venv_link)
        report["venv_symlink_created"] = True
    _atomic_write(production_spec, replacement)
    report["pin_updated"] = True
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--expected-old-sha", required=True)
    parser.add_argument("--production-spec", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-production", action="store_true")
    parser.add_argument("--reason", default="")
    args = parser.parse_args(argv)
    try:
        report = run_release(
            release_id=args.release_id,
            source_sha=args.source_sha,
            expected_old_sha=args.expected_old_sha,
            production_spec=args.production_spec,
            apply=args.apply,
            confirm_production=args.confirm_production,
            reason=args.reason,
        )
    except ReleaseError as exc:
        print(json.dumps({"schema_version": "weather_production_release_v1", "ok": False, "error": str(exc)}))
        return 2
    print(json.dumps({"ok": True, **report}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
