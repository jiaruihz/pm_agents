#!/usr/bin/env python3
"""Safely create and pin an immutable weather production release.

This command deliberately owns only the small, auditable release transition:
an exact detached worktree, its declared runtime bindings, plus the matching two
fields in ``production.yaml``.  It never restarts a runtime and defaults to a
no-write dry run.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
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


def _runtime_binding_paths(
    *,
    spec: dict[str, Any],
    release: dict[str, Any],
    checkout: Path,
    operational_repo: Path,
) -> tuple[tuple[str, Path, Path], ...]:
    bindings_raw = release.get("runtime_bindings", ["operational_venv"])
    if not isinstance(bindings_raw, list) or not bindings_raw:
        raise ReleaseError("release runtime_bindings must be a non-empty list")
    bindings = tuple(str(binding) for binding in bindings_raw)
    allowed = {"operational_venv", "operational_env", "canonical_db"}
    unknown = sorted(set(bindings) - allowed)
    if unknown:
        raise ReleaseError("unknown release runtime bindings: " + ", ".join(unknown))
    if len(bindings) != len(set(bindings)):
        raise ReleaseError("duplicate release runtime bindings")
    if "operational_venv" not in bindings:
        raise ReleaseError("release runtime_bindings must include operational_venv")

    overrides_raw = release.get("runtime_binding_sources") or {}
    if not isinstance(overrides_raw, dict):
        raise ReleaseError("release runtime_binding_sources must be a mapping")
    undeclared_overrides = sorted(set(map(str, overrides_raw)) - set(bindings))
    if undeclared_overrides:
        raise ReleaseError(
            "release runtime_binding_sources reference undeclared bindings: "
            + ", ".join(undeclared_overrides)
        )

    canonical_db_raw = spec.get("canonical_db_path")
    defaults = {
        "operational_venv": (checkout / ".venv", operational_repo / ".venv"),
        "operational_env": (checkout / ".env", operational_repo / ".env"),
        "canonical_db": (
            checkout / "runtime/weather.db",
            Path(str(canonical_db_raw or "")),
        ),
    }
    rows: list[tuple[str, Path, Path]] = []
    for binding in bindings:
        target, default_source = defaults[binding]
        source = Path(str(overrides_raw.get(binding, default_source)))
        if not source.is_absolute():
            raise ReleaseError(f"runtime binding source must be absolute: {binding}: {source}")
        if not (source.exists() or source.is_symlink()):
            raise ReleaseError(f"runtime binding source is missing: {binding}: {source}")
        if binding == "operational_venv" and not source.is_dir():
            raise ReleaseError(f"runtime binding source must be a directory: {binding}: {source}")
        if binding in {"operational_env", "canonical_db"} and not source.is_file():
            raise ReleaseError(f"runtime binding source must be a file: {binding}: {source}")
        rows.append((binding, target, source))
    return tuple(rows)


def _validate_runtime_binding_target(*, checkout: Path, target: Path) -> None:
    try:
        relative = target.relative_to(checkout)
    except ValueError as exc:
        raise ReleaseError(f"runtime binding target escapes release checkout: {target}") from exc
    parent = checkout
    for part in relative.parts[:-1]:
        parent /= part
        if parent.is_symlink():
            raise ReleaseError(f"runtime binding parent must not be a symlink: {parent}")
        if parent.exists() and not parent.is_dir():
            raise ReleaseError(f"runtime binding parent must be a directory: {parent}")


def _inspect_runtime_bindings(
    *,
    checkout: Path,
    bindings: tuple[tuple[str, Path, Path], ...],
) -> dict[str, str]:
    checks: dict[str, str] = {}
    for binding, target, source in bindings:
        _validate_runtime_binding_target(checkout=checkout, target=target)
        if target.exists() or target.is_symlink():
            if not target.is_symlink() or target.resolve() != source.resolve():
                raise ReleaseError(
                    f"release runtime binding conflicts with required symlink: "
                    f"{binding}: {target}"
                )
            checks[binding] = "existing_valid_symlink"
        else:
            checks[binding] = "will_create_symlink"
    return checks


def _create_runtime_bindings(
    *,
    checkout: Path,
    bindings: tuple[tuple[str, Path, Path], ...],
) -> list[str]:
    created: list[str] = []
    for binding, target, source in bindings:
        _validate_runtime_binding_target(checkout=checkout, target=target)
        if target.exists() or target.is_symlink():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(source, target)
        created.append(binding)
    return created


def _git_common_dir(repo: Path) -> Path:
    common = Path(_git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = repo / common
    return common.resolve(strict=True)


@contextmanager
def _exclusive_release_lock(production_spec: Path):
    """Serialize validation, checkout creation, and pin replacement per repo."""

    try:
        spec_path = production_spec.resolve(strict=True)
        repo = Path(
            _git(spec_path.parent, "rev-parse", "--show-toplevel")
        ).resolve(strict=True)
        lock_path = _git_common_dir(repo) / "weather-production-release.lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except (OSError, ReleaseError) as exc:
        raise ReleaseError(f"cannot open production release lock: {exc}") from exc
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ReleaseError(
                "another production release operation holds the lock; retry"
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _resolve_release_root(path: Path) -> Path:
    if not path.is_absolute():
        raise ReleaseError("production_release_root must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ReleaseError(
            f"production_release_root does not resolve: {path}: {exc}"
        ) from exc
    if not resolved.is_dir():
        raise ReleaseError("production_release_root must be a directory")
    return resolved


def _resolve_release_namespace(
    declared_release_root: Path,
    resolved_release_root: Path,
    release_id: str,
) -> Path:
    namespace = declared_release_root / release_id
    if namespace.is_symlink():
        raise ReleaseError(f"release namespace must not be a symlink: {namespace}")
    if not namespace.is_dir():
        raise ReleaseError(
            f"release namespace must be an existing directory: {namespace}"
        )
    resolved = namespace.resolve(strict=True)
    if resolved.parent != resolved_release_root:
        raise ReleaseError(f"release namespace escapes production root: {namespace}")
    return resolved


def _verify_release_root_identity(
    declared_release_root: Path,
    resolved_release_root: Path,
    expected_identity: tuple[int, int],
) -> None:
    try:
        current = declared_release_root.resolve(strict=True)
        stat = current.stat()
    except OSError as exc:
        raise ReleaseError(
            "production_release_root changed or disappeared during release"
        ) from exc
    if current != resolved_release_root or (stat.st_dev, stat.st_ino) != expected_identity:
        raise ReleaseError(
            "production_release_root physical identity changed during release"
        )


def _verify_checkout(
    checkout: Path, expected_sha: str, expected_common_git_dir: Path
) -> dict[str, str]:
    if checkout.is_symlink() or not checkout.is_dir():
        raise ReleaseError(f"release checkout is not a directory: {checkout}")
    head = _git(checkout, "rev-parse", "HEAD").lower()
    if head != expected_sha:
        raise ReleaseError(f"existing release HEAD mismatch: expected {expected_sha}, observed {head}")
    status = _git(
        checkout,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    allowed_untracked: list[str] = []
    unsafe_status: list[str] = []
    for line in status.splitlines():
        code = line[:2]
        relative = line[3:] if len(line) > 3 else ""
        if code == "??" and (
            relative == ".venv" or relative.startswith("runtime/")
        ):
            allowed_untracked.append(relative)
        else:
            unsafe_status.append(line)
    if unsafe_status:
        raise ReleaseError(
            f"existing release has unsafe tracked/untracked changes: {checkout}: "
            + "; ".join(unsafe_status[:10])
        )
    common_git_dir = _git_common_dir(checkout)
    if common_git_dir != expected_common_git_dir:
        raise ReleaseError(
            "release checkout is not registered to the operational repository: "
            f"{checkout}"
        )
    return {
        "head": head,
        "tracked_clean": "true",
        "allowed_untracked": ",".join(allowed_untracked),
        "common_git_dir": str(common_git_dir),
    }


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


def _run_release_impl(*, release_id: str, source_sha: str, expected_old_sha: str, production_spec: Path, apply: bool, confirm_production: bool, reason: str, source_basis: str = "operational-head") -> dict[str, Any]:
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise ReleaseError("release_id may contain only letters, digits, _ and -")
    source_sha = _full_sha(source_sha, "source_sha")
    expected_old_sha = _full_sha(expected_old_sha, "expected_old_sha")
    if apply and (not confirm_production or not reason.strip()):
        raise ReleaseError("--apply requires --confirm-production and a non-empty --reason")

    try:
        production_spec = production_spec.resolve(strict=True)
    except OSError as exc:
        raise ReleaseError(f"production spec does not exist: {production_spec}") from exc
    spec, text = _load_spec(production_spec)
    release = _release_block(spec, release_id)
    old_sha = _full_sha(str(release["expected_repo_sha"]), "existing expected_repo_sha")
    if old_sha != expected_old_sha:
        raise ReleaseError(f"expected-old-sha mismatch: spec has {old_sha}, requested {expected_old_sha}")
    operational_repo = Path(str(spec.get("operational_repo_root") or ""))
    declared_release_root = Path(str(spec.get("production_release_root") or ""))
    if not operational_repo.is_absolute() or not operational_repo.is_dir():
        raise ReleaseError("operational_repo_root must be an existing absolute directory")
    operational_repo = operational_repo.resolve(strict=True)
    expected_spec_path = (
        operational_repo / "src/strategies/runtime/production.yaml"
    ).resolve(strict=False)
    if production_spec != expected_spec_path:
        raise ReleaseError(
            "production spec must be the operational repository production.yaml: "
            f"{expected_spec_path}"
        )
    resolved_release_root = _resolve_release_root(declared_release_root)
    release_root_stat = resolved_release_root.stat()
    release_root_identity = (release_root_stat.st_dev, release_root_stat.st_ino)
    old_root = Path(str(release["checkout_root"]))
    declared_namespace = declared_release_root / release_id
    resolved_namespace = _resolve_release_namespace(
        declared_release_root, resolved_release_root, release_id
    )
    expected_old_root = declared_namespace / old_sha
    if old_root != expected_old_root:
        raise ReleaseError("existing checkout_root does not match the immutable release layout")
    if old_root.resolve(strict=True).parent != resolved_namespace:
        raise ReleaseError("existing checkout_root escapes the physical release namespace")
    target = declared_namespace / source_sha
    physical_target = resolved_namespace / source_sha
    if (
        target != target.absolute()
        or target.parent.parent != declared_release_root
        or physical_target.parent != resolved_namespace
    ):
        raise ReleaseError("computed release target escapes production_release_root")

    resolved_source = _git(operational_repo, "rev-parse", f"{source_sha}^{{commit}}").lower()
    if resolved_source != source_sha:
        raise ReleaseError(f"source SHA does not resolve exactly: {source_sha} -> {resolved_source}")
    operational_head = _git(operational_repo, "rev-parse", "HEAD").lower()
    operational_common_git_dir = _git_common_dir(operational_repo)
    if source_basis not in {"operational-head", "pinned-release"}:
        raise ReleaseError("unsupported source basis")
    ancestry_base = old_sha if source_basis == "pinned-release" else operational_head
    ancestor = subprocess.run(
        ["git", "-C", str(operational_repo), "merge-base", "--is-ancestor", ancestry_base, source_sha],
        check=False,
    ).returncode == 0
    if not ancestor:
        raise ReleaseError(f"source SHA is not a descendant of the selected {source_basis} base {ancestry_base}")

    checks: dict[str, Any] = {
        "release_id_exists_once": True,
        "old_pin_matches": True,
        "source_resolves_exactly": True,
        "source_basis": source_basis,
        "ancestry_base": ancestry_base,
        "selected_base_is_ancestor": True,
        "operational_head_is_ancestor": subprocess.run(
            ["git", "-C", str(operational_repo), "merge-base", "--is-ancestor", operational_head, source_sha],
            check=False,
        ).returncode == 0,
        "target_layout": str(target),
        "physical_release_root": str(resolved_release_root),
        "physical_target": str(physical_target),
        "old_checkout": _verify_checkout(
            old_root, old_sha, operational_common_git_dir
        ),
    }
    existed = physical_target.exists() or physical_target.is_symlink()
    if existed:
        checks["existing_checkout"] = _verify_checkout(
            physical_target, source_sha, operational_common_git_dir
        )
    else:
        checks["existing_checkout"] = "will_create"

    runtime_bindings = _runtime_binding_paths(
        spec=spec,
        release=release,
        checkout=physical_target,
        operational_repo=operational_repo,
    )
    checks["runtime_bindings"] = _inspect_runtime_bindings(
        checkout=physical_target,
        bindings=runtime_bindings,
    )

    replacement = _replace_release_pin(text["raw_text"], release_id, old_root, old_sha, target, source_sha)
    relative_spec = production_spec.relative_to(operational_repo)
    _git(
        operational_repo,
        "ls-files",
        "--error-unmatch",
        "--",
        str(relative_spec),
    )
    spec_status = _git(
        operational_repo,
        "status",
        "--porcelain",
        "--",
        str(relative_spec),
    )
    if spec_status and replacement != text["raw_text"]:
        raise ReleaseError(
            "production spec has pre-existing changes; refusing to overwrite them"
        )
    checks["production_spec_status"] = (
        "already_pinned" if replacement == text["raw_text"] else "tracked_clean"
    )
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

    if _git(operational_repo, "rev-parse", "HEAD").lower() != operational_head:
        raise ReleaseError("operational HEAD changed after validation; retry dry-run")
    _verify_release_root_identity(
        declared_release_root, resolved_release_root, release_root_identity
    )
    if not existed:
        _git(
            operational_repo,
            "worktree",
            "add",
            "--detach",
            str(physical_target),
            source_sha,
        )
        report["worktree_created"] = True
    _verify_checkout(physical_target, source_sha, operational_common_git_dir)
    created_bindings = _create_runtime_bindings(
        checkout=physical_target,
        bindings=runtime_bindings,
    )
    report["runtime_bindings_created"] = created_bindings
    report["venv_symlink_created"] = "operational_venv" in created_bindings
    if _git(operational_repo, "rev-parse", "HEAD").lower() != operational_head:
        raise ReleaseError(
            "operational HEAD changed while creating release; pin remains unchanged"
        )
    _verify_release_root_identity(
        declared_release_root, resolved_release_root, release_root_identity
    )
    if production_spec.read_text(encoding="utf-8") != text["raw_text"]:
        raise ReleaseError(
            "production spec changed while creating release; pin remains unchanged"
        )
    _atomic_write(production_spec, replacement)
    report["pin_updated"] = True
    return report


def run_release(
    *,
    release_id: str,
    source_sha: str,
    expected_old_sha: str,
    production_spec: Path,
    apply: bool,
    confirm_production: bool,
    reason: str,
    source_basis: str = "operational-head",
) -> dict[str, Any]:
    if apply and (not confirm_production or not reason.strip()):
        raise ReleaseError(
            "--apply requires --confirm-production and a non-empty --reason"
        )
    if apply:
        with _exclusive_release_lock(production_spec):
            return _run_release_impl(
                release_id=release_id,
                source_sha=source_sha,
                expected_old_sha=expected_old_sha,
                production_spec=production_spec,
                apply=apply,
                confirm_production=confirm_production,
                reason=reason,
                source_basis=source_basis,
            )
    return _run_release_impl(
        release_id=release_id,
        source_sha=source_sha,
        expected_old_sha=expected_old_sha,
        production_spec=production_spec,
        apply=apply,
        confirm_production=confirm_production,
        reason=reason,
        source_basis=source_basis,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--expected-old-sha", required=True)
    parser.add_argument("--production-spec", type=Path, required=True)
    parser.add_argument("--source-basis", choices=("operational-head", "pinned-release"),
                        default="operational-head", help="Require ancestry from development HEAD or the exact current production pin")
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
            source_basis=args.source_basis,
        )
    except ReleaseError as exc:
        print(json.dumps({"schema_version": "weather_production_release_v1", "ok": False, "error": str(exc)}))
        return 2
    print(json.dumps({"ok": True, **report}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
