#!/usr/bin/env python3
"""Manage Codex-only Allblue -> TAG fallback inside Clash Verge.

The macOS proxy remains on Clash Verge's existing mixed port.  Only OpenAI and
ChatGPT domains are routed through CODEX-STABLE; weather routing and TAG's own
node selector remain independently owned.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any

import yaml


SCHEMA_VERSION = "codex_proxy_failover_overlay_v1"
DEFAULT_CONFIG_ROOT = (
    Path.home()
    / "Library/Application Support/io.github.clash-verge-rev.clash-verge-rev"
)
DEFAULT_PROFILE_NAME = "Allblue 加速器"
DEFAULT_GROUP = "CODEX-STABLE"
DEFAULT_PRIMARY_GROUP = "Allblue 加速器"
DEFAULT_TAG_PROXY = "TAG-LOCAL"
DEFAULT_TAG_PORT = 7890
DEFAULT_SYSTEM_PROXY_PORT = 7897
DEFAULT_TEST_URL = "https://api.openai.com/v1/models"
CODEX_RULES = (
    "DOMAIN-SUFFIX,openai.com,CODEX-STABLE",
    "DOMAIN-SUFFIX,chatgpt.com,CODEX-STABLE",
    "DOMAIN-SUFFIX,oaistatic.com,CODEX-STABLE",
    "DOMAIN-SUFFIX,oaiusercontent.com,CODEX-STABLE",
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _profile(index: dict[str, Any], name: str) -> dict[str, Any]:
    for item in index.get("items") or []:
        if isinstance(item, dict) and item.get("type") == "remote" and item.get("name") == name:
            return item
    raise RuntimeError(f"Clash profile not found: {name}")


def _enhancement_paths(
    config_root: Path,
    profile: dict[str, Any],
) -> dict[str, Path]:
    option = profile.get("option") or {}
    identities = {
        name: str(option.get(name) or "") for name in ("proxies", "groups", "rules")
    }
    if not all(identities.values()):
        raise RuntimeError("Allblue profile is missing proxies/groups/rules enhancements")
    return {
        name: config_root / "profiles" / f"{uid}.yaml"
        for name, uid in identities.items()
    }


def build_overlay(
    *,
    config_root: Path = DEFAULT_CONFIG_ROOT,
    profile_name: str = DEFAULT_PROFILE_NAME,
) -> tuple[dict[str, Any], list[tuple[Path, bytes]]]:
    index_path = config_root / "profiles.yaml"
    index = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
    profile = _profile(index, profile_name)
    paths = _enhancement_paths(config_root, profile)
    payloads = {
        name: (yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        for name, path in paths.items()
    }

    proxies = payloads["proxies"]
    proxy_prepend = [
        row
        for row in (proxies.get("prepend") or [])
        if not isinstance(row, dict) or row.get("name") != DEFAULT_TAG_PROXY
    ]
    proxy_prepend.insert(
        0,
        {
            "name": DEFAULT_TAG_PROXY,
            "type": "http",
            "server": "127.0.0.1",
            "port": DEFAULT_TAG_PORT,
        },
    )
    proxies.update(
        {
            "prepend": proxy_prepend,
            "append": proxies.get("append") or [],
            "delete": proxies.get("delete") or [],
        }
    )

    groups = payloads["groups"]
    group_prepend = [
        row
        for row in (groups.get("prepend") or [])
        if not isinstance(row, dict) or row.get("name") != DEFAULT_GROUP
    ]
    group_prepend.insert(
        0,
        {
            "name": DEFAULT_GROUP,
            "type": "fallback",
            "proxies": [DEFAULT_PRIMARY_GROUP, DEFAULT_TAG_PROXY],
            "url": DEFAULT_TEST_URL,
            "interval": 30,
            "lazy": False,
        },
    )
    groups.update(
        {
            "prepend": group_prepend,
            "append": groups.get("append") or [],
            "delete": groups.get("delete") or [],
        }
    )

    rules = payloads["rules"]
    codex_rule_domains = {row.split(",", 2)[1] for row in CODEX_RULES}
    rule_prepend = [
        row
        for row in (rules.get("prepend") or [])
        if not (
            isinstance(row, str)
            and len(row.split(",", 2)) == 3
            and row.split(",", 2)[1] in codex_rule_domains
        )
    ]
    rules.update(
        {
            "prepend": [*CODEX_RULES, *rule_prepend],
            "append": rules.get("append") or [],
            "delete": rules.get("delete") or [],
        }
    )

    outputs = [
        (
            paths[name],
            yaml.safe_dump(payloads[name], allow_unicode=True, sort_keys=False).encode(
                "utf-8"
            ),
        )
        for name in ("proxies", "groups", "rules")
    ]
    plan = {
        "schema_version": SCHEMA_VERSION,
        "profile_uid": profile.get("uid"),
        "profile_name": profile.get("name"),
        "current_profile_uid": index.get("current"),
        "profile_active": index.get("current") == profile.get("uid"),
        "system_proxy_port": DEFAULT_SYSTEM_PROXY_PORT,
        "group": DEFAULT_GROUP,
        "primary": DEFAULT_PRIMARY_GROUP,
        "fallback": DEFAULT_TAG_PROXY,
        "test_url": DEFAULT_TEST_URL,
        "rules": list(CODEX_RULES),
        "files": [
            {
                "path": str(path),
                "before_sha256": sha256_bytes(path.read_bytes()),
                "after_sha256": sha256_bytes(value),
                "changed": path.read_bytes() != value,
            }
            for path, value in outputs
        ],
    }
    return plan, outputs


def apply_overlay(
    *,
    config_root: Path = DEFAULT_CONFIG_ROOT,
    profile_name: str = DEFAULT_PROFILE_NAME,
) -> dict[str, Any]:
    plan, outputs = build_overlay(config_root=config_root, profile_name=profile_name)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup_root = config_root / "pm_agents_backups" / "codex_proxy" / stamp
    for path, value in outputs:
        relative = path.relative_to(config_root)
        atomic_write(backup_root / relative, path.read_bytes())
        atomic_write(path, value)
    return {**plan, "applied": True, "backup_root": str(backup_root)}


def runtime_status(*, config_root: Path = DEFAULT_CONFIG_ROOT) -> dict[str, Any]:
    plan, _ = build_overlay(config_root=config_root)
    socket = config_root / "clash-verge.sock"
    configured_socket = Path("/tmp/verge/verge-mihomo.sock")
    socket = configured_socket if configured_socket.exists() else socket
    proxies: dict[str, Any] = {}
    if socket.exists():
        result = subprocess.run(
            [
                "/usr/bin/curl",
                "-sS",
                "--max-time",
                "3",
                "--unix-socket",
                str(socket),
                "http://localhost/proxies",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            proxies = (json.loads(result.stdout) or {}).get("proxies") or {}
    group = proxies.get(DEFAULT_GROUP) or {}
    return {
        **plan,
        "overlay_ready": not any(row["changed"] for row in plan["files"]),
        "runtime_group_present": bool(group),
        "runtime_group_type": group.get("type"),
        "runtime_current_proxy": group.get("now"),
        "runtime_candidates": group.get("all") or [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-root", type=Path, default=DEFAULT_CONFIG_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan")
    apply = sub.add_parser("apply")
    apply.add_argument("--confirm-network-change", action="store_true")
    sub.add_parser("status")
    args = parser.parse_args()

    if args.command == "plan":
        payload, _ = build_overlay(config_root=args.config_root)
    elif args.command == "apply":
        if not args.confirm_network_change:
            raise SystemExit("apply requires --confirm-network-change")
        payload = apply_overlay(config_root=args.config_root)
    else:
        payload = runtime_status(config_root=args.config_root)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
