#!/usr/bin/env python3
"""Install TAG route maintenance from the pinned immutable control-plane release."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec  # noqa: E402


LABEL = "com.pm-agents.tag-proxy-route-maintain"


def build_install_plan(production_spec: Path) -> dict[str, Any]:
    spec = load_production_spec(production_spec)
    release = spec.release("control_plane")
    checkout = release.checkout_root.resolve(strict=True)
    observed_sha = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if observed_sha != release.expected_repo_sha:
        raise RuntimeError(
            "control_plane release SHA mismatch: "
            f"expected={release.expected_repo_sha}:observed={observed_sha}"
        )
    python = checkout / ".venv/bin/python"
    controller = checkout / "scripts/ops/tag_proxy_route_ctl.py"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise RuntimeError(f"release Python is unavailable: {python}")
    if not controller.is_file():
        raise RuntimeError(f"release TAG controller is unavailable: {controller}")
    plist_path = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            str(python),
            str(controller),
            "maintain",
            "--apply",
            "--reason",
            "launchd bounded TAG route maintenance",
            "--quiet",
        ],
        "RunAtLoad": True,
        "StartInterval": 120,
        "ProcessType": "Background",
        "StandardOutPath": "/tmp/pm-agents-tag-proxy-route.out.log",
        "StandardErrorPath": "/tmp/pm-agents-tag-proxy-route.err.log",
    }
    return {
        "label": LABEL,
        "checkout_root": str(checkout),
        "expected_repo_sha": release.expected_repo_sha,
        "plist_path": str(plist_path),
        "plist": payload,
    }


def atomic_write_plist(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            plistlib.dump(payload, handle, fmt=plistlib.FMT_XML, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-spec", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-production", action="store_true")
    parser.add_argument("--reason", default="")
    args = parser.parse_args(argv)
    if args.apply and (not args.confirm_production or not args.reason.strip()):
        parser.error("--apply requires --confirm-production and --reason")
    plan = build_install_plan(args.production_spec.resolve(strict=True))
    plan["mode"] = "apply" if args.apply else "dry_run"
    if args.apply:
        plist_path = Path(plan["plist_path"])
        atomic_write_plist(plist_path, plan["plist"])
        domain = f"gui/{os.getuid()}"
        subprocess.run(
            ["/bin/launchctl", "bootout", domain, str(plist_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        subprocess.run(
            ["/bin/launchctl", "bootstrap", domain, str(plist_path)],
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["/bin/launchctl", "kickstart", f"{domain}/{LABEL}"],
            capture_output=True,
            text=True,
            check=True,
        )
    print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
