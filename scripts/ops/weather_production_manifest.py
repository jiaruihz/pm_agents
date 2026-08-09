#!/usr/bin/env python3
"""Inspect desired versus observed Mac weather production topology.

The command is read-only unless ``--json-out`` is supplied.  It does not trust
the strategy registry as present-state proof: processes, the canonical JRS
tmux server, open SQLite handles, checkout roots, and runtime summaries are
inspected independently and then compared with the git-authored desired spec.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shlex
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import (  # noqa: E402
    WeatherProductionSpec,
    load_production_spec,
)
from src.strategies.runtime.specs import load_instance_specs  # noqa: E402


PROCESS_MARKERS = (
    "weather",
    "tmax",
    "low_price",
    "regime",
    "knmi",
    "aemet",
    "first_seen",
    "full_ladder",
)
DB_OPTIONS = {"--db", "--db-path", "--runtime-db", "--weather-db"}
OUTPUT_OPTIONS = {"--output-dir", "--out", "--runtime-dir"}
SECRET_OPTIONS = ("secret", "password", "private-key", "private_key", "token")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_command(
    args: Sequence[str],
    *,
    timeout: float = 10.0,
    stderr: int | None = subprocess.DEVNULL,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if stderr is None else stderr,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(list(args), 127, "", str(exc))


def parse_process_table(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pattern = re.compile(
        r"^\s*(?P<pid>\d+)\s+(?P<ppid>\d+)\s+"
        r"(?P<started>\S+\s+\S+\s+\d+\s+\d+:\d+:\d+\s+\d+)\s+"
        r"(?P<command>.+)$"
    )
    for line in text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        rows.append(
            {
                "pid": int(match.group("pid")),
                "ppid": int(match.group("ppid")),
                "started": match.group("started"),
                "command": match.group("command"),
            }
        )
    return rows


def weather_process(row: Mapping[str, Any]) -> bool:
    command = str(row.get("command") or "").lower()
    if "weather_production_manifest.py" in command:
        return False
    return any(marker in command for marker in PROCESS_MARKERS)


def command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def option_values(tokens: Sequence[str], options: set[str]) -> list[str]:
    values: list[str] = []
    for index, token in enumerate(tokens):
        if token in options and index + 1 < len(tokens):
            values.append(tokens[index + 1])
            continue
        for option in options:
            prefix = option + "="
            if token.startswith(prefix):
                values.append(token[len(prefix) :])
    return values


def redact_command(command: str) -> str:
    tokens = command_tokens(command)
    redacted: list[str] = []
    redact_next = False
    for token in tokens:
        lowered = token.lower()
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        if token.startswith("--") and any(marker in lowered for marker in SECRET_OPTIONS):
            if "=" in token:
                redacted.append(token.split("=", 1)[0] + "=<redacted>")
            else:
                redacted.append(token)
                redact_next = True
            continue
        redacted.append(token)
    return shlex.join(redacted)


def classify_execution_mode(command: str) -> str:
    tokens = command_tokens(command)
    lowered = command.lower()
    token_set = set(tokens)
    if "--live" in token_set and "--confirm-live" in token_set:
        return "live_confirmed"
    if "--live" in token_set:
        return "live_unconfirmed"
    if "shadow" in lowered or "--zero-notional" in token_set:
        return "zero_notional_shadow"
    if any(marker in lowered for marker in ("collector", "observation", "data_feed")):
        return "collector"
    if any(marker in lowered for marker in ("monitor", "patrol", "observer")):
        return "monitor"
    return "unknown"


def file_identity(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "is_symlink": path.is_symlink(),
        "resolved_path": str(path.resolve(strict=False)),
    }
    if not path.exists():
        return result
    stat = path.stat()
    result.update(
        {
            "device": stat.st_dev,
            "inode": stat.st_ino,
            "size_bytes": stat.st_size,
            "mtime_utc": datetime.fromtimestamp(
                stat.st_mtime, timezone.utc
            ).isoformat(timespec="seconds"),
        }
    )
    return result


def same_file(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return bool(
        left.get("exists")
        and right.get("exists")
        and (left.get("device"), left.get("inode"))
        == (right.get("device"), right.get("inode"))
    )


def probe_file_readable(path: Path) -> dict[str, Any]:
    """Read one byte so path metadata cannot masquerade as JRS access."""

    try:
        with path.open("rb") as handle:
            handle.read(1)
    except OSError as exc:
        return {
            "readable": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {"readable": True, "error": None}


def probe_file_readable_via_canonical_context(
    spec: WeatherProductionSpec, path: Path
) -> dict[str, Any]:
    """Retry a JRS read under the already-authorized canonical tmux parent."""

    helper = ROOT / "scripts/ops/weather_jrs_tmux_env.sh"
    inner = f"/usr/bin/head -c 1 {shlex.quote(str(path))} >/dev/null"
    command = (
        f"source {shlex.quote(str(helper))}; "
        "weather_jrs_tmux_exec_checked "
        f"{shlex.quote(spec.canonical_tmux_socket)} manifest_db_read "
        f"{shlex.quote(inner)}"
    )
    result = run_command(["/bin/bash", "-lc", command], timeout=20, stderr=None)
    if result.returncode == 0:
        return {
            "readable": True,
            "error": None,
            "read_context": "canonical_jrs_tmux",
        }
    detail = (result.stdout or result.stderr or "canonical read probe failed").strip()
    return {
        "readable": False,
        "error": detail[-1000:],
        "read_context": "canonical_jrs_tmux",
    }


def inspect_volume_identity(path: Path) -> dict[str, Any]:
    """Resolve an external volume by UUID, not its mutable mount label."""

    result = subprocess.run(
        ["/usr/sbin/diskutil", "info", "-plist", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        return {
            "path": str(path),
            "mounted": False,
            "volume_uuid": None,
            "error": result.stderr.decode("utf-8", errors="replace").strip(),
        }
    try:
        payload = plistlib.loads(result.stdout)
    except (plistlib.InvalidFileException, ValueError) as exc:
        return {
            "path": str(path),
            "mounted": False,
            "volume_uuid": None,
            "error": f"invalid_diskutil_plist:{exc}",
        }
    return {
        "path": str(path),
        "mounted": bool(payload.get("MountPoint")),
        "mount_point": payload.get("MountPoint"),
        "volume_name": payload.get("VolumeName"),
        "volume_uuid": str(payload.get("VolumeUUID") or "").upper() or None,
        "device_identifier": payload.get("DeviceIdentifier"),
        "protocol": payload.get("BusProtocol"),
        "solid_state": payload.get("SolidState"),
        "writable": payload.get("WritableVolume"),
        "smart_status": payload.get("SMARTStatus"),
        "total_bytes": payload.get("TotalSize"),
        "container_free_bytes": payload.get("APFSContainerFree"),
        "error": None,
    }


def inspect_db_route(
    spec: WeatherProductionSpec,
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    repo_root = repo_root or spec.operational_repo_root
    canonical = file_identity(spec.canonical_db_path)
    compatibility = [
        file_identity(path)
        for path in spec.resolved_compatibility_db_paths(repo_root)
    ]
    linked = [row for row in compatibility if same_file(canonical, row)]
    distinct_existing = [
        row
        for row in compatibility
        if row.get("exists") and not same_file(canonical, row)
    ]
    read_probe = (
        probe_file_readable(spec.canonical_db_path)
        if canonical.get("exists")
        else {"readable": False, "error": "canonical DB missing"}
    )
    if (
        canonical.get("exists")
        and not read_probe["readable"]
        and spec.canonical_db_path.is_relative_to(spec.production_storage_root)
    ):
        read_probe = probe_file_readable_via_canonical_context(
            spec, spec.canonical_db_path
        )
    if canonical.get("exists") and not read_probe["readable"]:
        status = "inaccessible"
    elif canonical.get("exists") and linked and not distinct_existing:
        status = "healthy"
    elif canonical.get("exists") and distinct_existing:
        status = "split"
    elif canonical.get("exists"):
        status = "jrs_unlinked"
    elif any(row.get("exists") for row in compatibility):
        status = "local_only"
    else:
        status = "missing"
    return {
        "status": status,
        "expected": canonical,
        "compatibility": compatibility,
        "linked_compatibility_paths": [row["path"] for row in linked],
        "distinct_existing_paths": [row["path"] for row in distinct_existing],
        "read_probe": read_probe,
    }


def parse_lsof_db_consumers(text: str) -> dict[int, dict[str, Any]]:
    consumers: dict[int, dict[str, Any]] = {}
    current_pid: int | None = None
    for raw in text.splitlines():
        if not raw:
            continue
        kind, value = raw[0], raw[1:]
        if kind == "p" and value.isdigit():
            current_pid = int(value)
            consumers.setdefault(current_pid, {"pid": current_pid, "command": "", "paths": []})
        elif current_pid is not None and kind == "c":
            consumers[current_pid]["command"] = value
        elif current_pid is not None and kind == "n":
            consumers[current_pid]["paths"].append(value)
    for row in consumers.values():
        row["paths"] = sorted(set(row["paths"]))
    return consumers


def inspect_db_consumers(paths: Iterable[Path]) -> dict[int, dict[str, Any]]:
    existing = [str(path) for path in paths if path.exists()]
    if not existing:
        return {}
    proc = run_command(["lsof", "-n", "-Fpcn", *existing], timeout=15.0, stderr=None)
    return parse_lsof_db_consumers(proc.stdout)


def process_cwd(pid: int) -> Path | None:
    proc = run_command(
        ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
        timeout=3.0,
        stderr=None,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("n/"):
            return Path(line[1:])
    return None


def git_root(path: Path | None, cache: dict[str, Path | None]) -> Path | None:
    if path is None:
        return None
    key = str(path)
    if key in cache:
        return cache[key]
    proc = run_command(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        timeout=3.0,
        stderr=None,
    )
    root = Path(proc.stdout.strip()) if proc.returncode == 0 and proc.stdout.strip() else None
    cache[key] = root
    return root


def git_metadata(root: Path, cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    key = str(root)
    if key in cache:
        return cache[key]
    head = run_command(["git", "-C", key, "rev-parse", "HEAD"], timeout=3.0, stderr=None)
    branch = run_command(
        ["git", "-C", key, "rev-parse", "--abbrev-ref", "HEAD"],
        timeout=3.0,
        stderr=None,
    )
    status = run_command(
        ["git", "-C", key, "status", "--porcelain", "--untracked-files=no"],
        timeout=5.0,
        stderr=None,
    )
    payload = {
        "root": key,
        "head": head.stdout.strip() if head.returncode == 0 else None,
        "branch": branch.stdout.strip() if branch.returncode == 0 else None,
        "dirty_tracked": bool(status.stdout.strip()) if status.returncode == 0 else None,
    }
    cache[key] = payload
    return payload


def inspect_persistent_worktrees(spec: WeatherProductionSpec) -> list[dict[str, Any]]:
    """Find top-level pm_agents worktrees not owned by the production contract."""
    proc = run_command(
        [
            "git",
            "-C",
            str(spec.operational_repo_root),
            "worktree",
            "list",
            "--porcelain",
        ],
        timeout=5.0,
        stderr=None,
    )
    if proc.returncode != 0:
        return []
    allowed = {spec.operational_repo_root.resolve()}
    if spec.canonical_refresh_checkout_root is not None:
        allowed.add(spec.canonical_refresh_checkout_root.resolve())
    allowed.update(
        runtime.checkout_root.resolve()
        for runtime in spec.managed_runtimes
        if runtime.checkout_root is not None
    )
    parent = spec.operational_repo_root.parent.resolve()
    prefix = spec.operational_repo_root.name
    rows: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        if not line.startswith("worktree "):
            continue
        root = Path(line.removeprefix("worktree ")).resolve()
        if root.parent != parent or not root.name.startswith(prefix):
            continue
        rows.append(
            {
                "root": str(root),
                "registered": root in allowed,
                "exists": root.exists(),
            }
        )
    return sorted(rows, key=lambda row: str(row["root"]))


def runtime_summary(tokens: Sequence[str]) -> dict[str, Any]:
    output_values = option_values(tokens, OUTPUT_OPTIONS)
    for value in output_values:
        summary_path = Path(value) / "latest_summary.json"
        if not summary_path.exists():
            continue
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        return {
            "path": str(summary_path),
            "generated_at_utc": payload.get("generated_at_utc"),
            "status": payload.get("status"),
            "live_enabled": payload.get("live_enabled"),
            "deployed_repo_sha": payload.get("deployed_repo_sha"),
            "source_checkout_root": payload.get("source_checkout_root"),
            "critical_source_dirty": payload.get("critical_source_dirty"),
        }
    return {}


def checkout_for_process(
    row: Mapping[str, Any],
    *,
    root_cache: dict[str, Path | None],
) -> Path | None:
    cwd = process_cwd(int(row["pid"]))
    root = git_root(cwd, root_cache)
    if root:
        return root
    for token in command_tokens(str(row.get("command") or "")):
        if not token.startswith("/"):
            continue
        candidate = Path(token)
        root = git_root(candidate if candidate.is_dir() else candidate.parent, root_cache)
        if root:
            return root
    return None


def descendants(processes: Sequence[Mapping[str, Any]], parent_pid: int) -> list[int]:
    children: dict[int, list[int]] = {}
    for row in processes:
        children.setdefault(int(row["ppid"]), []).append(int(row["pid"]))
    result: list[int] = []
    stack = list(children.get(parent_pid, []))
    while stack:
        pid = stack.pop()
        result.append(pid)
        stack.extend(children.get(pid, []))
    return result


def inspect_tmux(
    spec: WeatherProductionSpec,
    processes: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    tmux_bin = spec.canonical_tmux_binary
    if not tmux_bin.exists():
        fallback = run_command(["sh", "-lc", "command -v tmux"], stderr=None)
        if fallback.returncode != 0 or not fallback.stdout.strip():
            return []
        tmux_bin = Path(fallback.stdout.strip())
    proc = run_command(
        [
            str(tmux_bin),
            "-L",
            spec.canonical_tmux_socket,
            "list-panes",
            "-a",
            "-F",
            "#{session_name}\t#{pane_pid}\t#{pane_current_path}\t#{pane_start_command}",
        ],
        timeout=8.0,
        stderr=None,
    )
    sessions: dict[str, dict[str, Any]] = {}
    process_by_pid = {int(row["pid"]): row for row in processes}
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 3)
        if len(parts) != 4 or not parts[1].isdigit():
            continue
        pane_pid = int(parts[1])
        child_rows = [
            process_by_pid[pid]
            for pid in descendants(processes, pane_pid)
            if pid in process_by_pid and weather_process(process_by_pid[pid])
        ]
        session = sessions.setdefault(
            parts[0],
            {
                "session": parts[0],
                "panes": [],
                "process_pids": [],
            },
        )
        session["panes"].append(
            {
                "pane_pid": pane_pid,
                "pane_current_path": parts[2],
                "pane_start_command": redact_command(parts[3]),
            }
        )
        session["process_pids"].extend(int(row["pid"]) for row in child_rows)
    for row in sessions.values():
        row["process_pids"] = sorted(set(row["process_pids"]))
    return sorted(sessions.values(), key=lambda row: row["session"])


def inspect_launchctl() -> list[dict[str, Any]]:
    proc = run_command(["launchctl", "list"], timeout=8.0, stderr=None)
    rows: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        label = parts[-1]
        if "pm-agents" not in label.lower():
            continue
        rows.append(
            {
                "label": label,
                "pid": int(parts[0]) if parts[0].isdigit() else None,
                "last_exit_status": int(parts[1]) if re.fullmatch(r"-?\d+", parts[1]) else None,
            }
        )
    return sorted(rows, key=lambda row: row["label"])


def finding(severity: str, kind: str, message: str, detail: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "severity": severity,
        "kind": kind,
        "message": message,
        "detail": dict(detail),
    }


def sqlite_main_path(path: str) -> Path:
    for suffix in ("-wal", "-shm"):
        if path.endswith(suffix):
            return Path(path[: -len(suffix)])
    return Path(path)


def db_path_matches_canonical(path: str, canonical: Mapping[str, Any]) -> bool:
    observed = file_identity(sqlite_main_path(path))
    return same_file(canonical, observed)


def build_manifest(
    *,
    spec: WeatherProductionSpec,
    processes: Sequence[dict[str, Any]],
    tmux_rows: Sequence[dict[str, Any]],
    launchctl_rows: Sequence[dict[str, Any]],
    db_route: Mapping[str, Any],
    db_consumers: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    root_cache: dict[str, Path | None] = {}
    git_cache: dict[str, dict[str, Any]] = {}
    observed_processes: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    persistent_worktrees = inspect_persistent_worktrees(spec)
    unregistered_worktrees = [
        row for row in persistent_worktrees if not bool(row["registered"])
    ]
    if unregistered_worktrees:
        findings.append(
            finding(
                "warning",
                "unregistered_persistent_worktrees",
                "top-level pm_agents worktrees exist outside the production contract",
                {"worktrees": unregistered_worktrees},
            )
        )

    production_volume = inspect_volume_identity(spec.production_storage_root)
    archive_volume = inspect_volume_identity(spec.archive_storage_root)
    expected_production_uuid = spec.production_storage_volume_uuid
    if expected_production_uuid and (
        production_volume.get("volume_uuid") != expected_production_uuid
    ):
        findings.append(
            finding(
                "critical",
                "production_storage_volume_identity_mismatch",
                "production mount does not resolve to the pinned physical volume UUID",
                {
                    "root": str(spec.production_storage_root),
                    "expected_volume_uuid": expected_production_uuid,
                    "observed": production_volume,
                },
            )
        )
    if production_volume.get("mounted") and not production_volume.get("writable"):
        findings.append(
            finding(
                "critical",
                "production_storage_not_writable",
                "production volume is mounted but not writable",
                {"observed": production_volume},
            )
        )
    total_bytes = production_volume.get("total_bytes")
    free_bytes = production_volume.get("container_free_bytes")
    if isinstance(total_bytes, int) and isinstance(free_bytes, int) and total_bytes > 0:
        free_ratio = free_bytes / total_bytes
        if free_ratio < 0.10:
            findings.append(
                finding(
                    "critical" if free_ratio < 0.05 else "warning",
                    "production_storage_low_capacity",
                    "production volume free capacity is below the operating threshold",
                    {
                        "free_bytes": free_bytes,
                        "total_bytes": total_bytes,
                        "free_ratio": round(free_ratio, 6),
                    },
                )
            )
    expected_archive_uuid = spec.archive_storage_volume_uuid
    if archive_volume.get("mounted") and expected_archive_uuid and (
        archive_volume.get("volume_uuid") != expected_archive_uuid
    ):
        findings.append(
            finding(
                "critical",
                "archive_storage_volume_identity_mismatch",
                "archive mount does not resolve to the pinned physical volume UUID",
                {
                    "root": str(spec.archive_storage_root),
                    "expected_volume_uuid": expected_archive_uuid,
                    "observed": archive_volume,
                },
            )
        )

    if db_route["status"] != "healthy":
        findings.append(
            finding(
                "critical",
                f"canonical_db_{db_route['status']}",
                "weather canonical DB route does not match the JRS desired topology",
                {
                    "expected": db_route["expected"]["path"],
                    "distinct_existing_paths": db_route["distinct_existing_paths"],
                },
            )
        )

    noncanonical_consumers: list[dict[str, Any]] = []
    for consumer in db_consumers.values():
        bad_paths = [
            path
            for path in consumer.get("paths", [])
            if not db_path_matches_canonical(path, db_route["expected"])
        ]
        if bad_paths:
            noncanonical_consumers.append(
                {
                    "pid": consumer["pid"],
                    "command": consumer.get("command"),
                    "paths": bad_paths,
                }
            )
    if noncanonical_consumers:
        findings.append(
            finding(
                "critical",
                "db_consumer_outside_canonical",
                "running processes have SQLite handles on a DB other than the JRS canonical DB",
                {"consumers": noncanonical_consumers},
            )
        )

    for raw in processes:
        if not weather_process(raw):
            continue
        tokens = command_tokens(str(raw["command"]))
        checkout = checkout_for_process(raw, root_cache=root_cache)
        checkout_meta = git_metadata(checkout, git_cache) if checkout else None
        summary = runtime_summary(tokens)
        row = {
            "pid": raw["pid"],
            "ppid": raw["ppid"],
            "started": raw["started"],
            "execution_mode": classify_execution_mode(str(raw["command"])),
            "command": redact_command(str(raw["command"])),
            "checkout": checkout_meta,
            "declared_db_paths": option_values(tokens, DB_OPTIONS),
            "open_db_paths": list(db_consumers.get(int(raw["pid"]), {}).get("paths", [])),
            "runtime_summary": summary,
        }
        observed_processes.append(row)
        mode = row["execution_mode"]
        if mode == "live_unconfirmed":
            findings.append(
                finding(
                    "critical",
                    "live_without_confirm_live",
                    "live process is missing --confirm-live",
                    {"pid": row["pid"], "command": row["command"]},
                )
            )
        if checkout and str(checkout).startswith(("/private/tmp/", "/tmp/")):
            findings.append(
                finding(
                    "critical" if mode == "live_confirmed" else "warning",
                    (
                        "live_process_from_temporary_checkout"
                        if mode == "live_confirmed"
                        else "process_from_temporary_checkout"
                    ),
                    "weather process runs from a temporary checkout",
                    {
                        "pid": row["pid"],
                        "execution_mode": mode,
                        "checkout": str(checkout),
                    },
                )
            )
        deployed_sha = summary.get("deployed_repo_sha")
        checkout_head = checkout_meta.get("head") if checkout_meta else None
        if deployed_sha and checkout_head and deployed_sha != checkout_head:
            findings.append(
                finding(
                    "warning",
                    "process_checkout_head_drift",
                    "running process reports a different loaded SHA than the checkout HEAD",
                    {
                        "pid": row["pid"],
                        "deployed_repo_sha": deployed_sha,
                        "checkout_head": checkout_head,
                    },
                )
            )

    expected_sessions = {
        item.tmux_session
        for item in load_instance_specs()
        if item.tmux_session
    }
    expected_sessions.update(
        item.tmux_session for item in spec.managed_runtimes
    )
    for row in tmux_rows:
        row["registered_strategy_session"] = row["session"] in expected_sessions

    unregistered_sessions = [
        row["session"]
        for row in tmux_rows
        if not row["registered_strategy_session"]
        and row["session"] not in spec.allowed_unmanaged_sessions
    ]
    if unregistered_sessions:
        findings.append(
            finding(
                "warning",
                "tmux_sessions_missing_from_instance_registry",
                "canonical JRS tmux sessions exist outside the git-authored instance registry",
                {"sessions": unregistered_sessions},
            )
        )

    failed_launch_agents = [
        row
        for row in launchctl_rows
        if row.get("pid") is None
        and row.get("last_exit_status") not in (None, 0)
    ]
    if failed_launch_agents:
        findings.append(
            finding(
                "critical",
                "launch_agent_last_exit_nonzero",
                "pm-agents LaunchAgent jobs have a non-zero last exit status",
                {"jobs": failed_launch_agents},
            )
        )

    checkouts = sorted(git_cache.values(), key=lambda row: row["root"])
    severities = [row["severity"] for row in findings]
    status = (
        "critical"
        if "critical" in severities
        else "warning"
        if "warning" in severities
        else "healthy"
    )
    return {
        "manifest_version": spec.version,
        "generated_at_utc": utc_now(),
        "host": socket.gethostname(),
        "host_role": spec.host_role,
        "status": status,
        "desired": {
            "production_storage_root": str(spec.production_storage_root),
            "production_storage_volume_uuid": spec.production_storage_volume_uuid,
            "archive_storage_root": str(spec.archive_storage_root),
            "archive_storage_volume_uuid": spec.archive_storage_volume_uuid,
            "canonical_db_path": str(spec.canonical_db_path),
            "operational_repo_root": str(spec.operational_repo_root),
            "compatibility_db_paths": [
                str(path) for path in spec.resolved_compatibility_db_paths()
            ],
            "data_feed_runtime_root": str(spec.data_feed_runtime_root),
            "pm_runtime_root": str(spec.pm_runtime_root),
            "research_artifact_root": str(spec.research_artifact_root),
            "canonical_tmux_socket": spec.canonical_tmux_socket,
            "canonical_tmux_binary": str(spec.canonical_tmux_binary),
            "managed_runtime_sessions": [
                item.tmux_session for item in spec.managed_runtimes
            ],
            "allowed_unmanaged_sessions": list(spec.allowed_unmanaged_sessions),
        },
        "storage_volumes": {
            "production": production_volume,
            "archive": archive_volume,
        },
        "db_route": dict(db_route),
        "db_consumers": sorted(db_consumers.values(), key=lambda row: int(row["pid"])),
        "checkouts": checkouts,
        "persistent_worktrees": persistent_worktrees,
        "processes": sorted(observed_processes, key=lambda row: int(row["pid"])),
        "tmux_sessions": list(tmux_rows),
        "launch_agents": list(launchctl_rows),
        "findings": findings,
    }


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def compare_prechange_manifest(
    payload: dict[str, Any],
    baseline: Mapping[str, Any],
    *,
    allow_missing_sessions: Iterable[str] = (),
) -> dict[str, Any]:
    """Fail closed when a production change drops an existing JRS session.

    The baseline is an observed pre-change manifest, not desired-state metadata.
    This protects every process that was actually running before a tmux/server,
    deployment, or runtime change without treating historical registry entries
    as present-state truth.
    """

    baseline_sessions = {
        str(row.get("session"))
        for row in baseline.get("tmux_sessions", [])
        if isinstance(row, Mapping) and row.get("session")
    }
    current_sessions = {
        str(row.get("session"))
        for row in payload.get("tmux_sessions", [])
        if isinstance(row, Mapping) and row.get("session")
    }
    allowed = {str(item) for item in allow_missing_sessions}
    missing = sorted(baseline_sessions - current_sessions - allowed)
    payload["prechange_comparison"] = {
        "baseline_generated_at_utc": baseline.get("generated_at_utc"),
        "baseline_sessions": sorted(baseline_sessions),
        "current_sessions": sorted(current_sessions),
        "allowed_missing_sessions": sorted(allowed),
        "missing_sessions": missing,
    }
    if missing:
        payload.setdefault("findings", []).append(
            finding(
                "critical",
                "canonical_tmux_sessions_lost_since_prechange",
                "production change dropped canonical JRS sessions that were running before the change",
                {
                    "sessions": missing,
                    "baseline_generated_at_utc": baseline.get("generated_at_utc"),
                },
            )
        )
    severities = [row.get("severity") for row in payload.get("findings", [])]
    payload["status"] = (
        "critical"
        if "critical" in severities
        else "warning"
        if "warning" in severities
        else "healthy"
    )
    return payload


def collect_manifest(spec: WeatherProductionSpec) -> dict[str, Any]:
    """Collect the read-only observed production manifest for reuse by ctl tools."""

    ps = run_command(
        ["ps", "-axo", "pid=,ppid=,lstart=,command="],
        timeout=10.0,
        stderr=None,
    )
    processes = parse_process_table(ps.stdout)
    tmux_rows = inspect_tmux(spec, processes)
    launchctl_rows = inspect_launchctl()
    db_route = inspect_db_route(spec)
    db_paths = [
        spec.canonical_db_path,
        *spec.resolved_compatibility_db_paths(),
    ]
    db_consumers = inspect_db_consumers(db_paths)
    return build_manifest(
        spec=spec,
        processes=processes,
        tmux_rows=tmux_rows,
        launchctl_rows=launchctl_rows,
        db_route=db_route,
        db_consumers=db_consumers,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-spec", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument(
        "--compare-prechange",
        type=Path,
        help="compare current canonical JRS sessions with a pre-change manifest",
    )
    parser.add_argument(
        "--allow-missing-session",
        action="append",
        default=[],
        help="session intentionally stopped by this change; repeat as needed",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return non-zero when the observed topology has a critical finding",
    )
    args = parser.parse_args()

    spec = load_production_spec(args.production_spec)
    payload = collect_manifest(spec)
    if args.compare_prechange:
        baseline = json.loads(args.compare_prechange.read_text(encoding="utf-8"))
        if not isinstance(baseline, dict):
            raise ValueError("pre-change manifest must be a JSON object")
        payload = compare_prechange_manifest(
            payload,
            baseline,
            allow_missing_sessions=args.allow_missing_session,
        )
    if args.json_out:
        write_json_atomic(args.json_out, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if args.strict and payload["status"] == "critical" else 0


if __name__ == "__main__":
    raise SystemExit(main())
