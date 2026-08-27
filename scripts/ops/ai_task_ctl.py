#!/usr/bin/env python3
"""Create, validate and package PM Agents task contracts."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TASK_ROOT = ROOT / "tasks"
PROJECT_ROOT = TASK_ROOT / "projects"
TASK_DIRS = [TASK_ROOT / name for name in ("queue", "active", "archive")]
REQUIRED_FIELDS = {
    "TASK_ID",
    "PROJECT_ID",
    "WORKSTREAM",
    "STATUS",
    "ROLE",
    "REPO_ROOT",
    "BASE_COMMIT",
    "OWNER",
    "CREATED_AT",
    "HANDOFF_PATH",
}
VALID_STATUSES = {"DRAFT", "ACTIVE", "BLOCKED", "READY_FOR_REVIEW", "ACCEPTED", "PAUSED"}
VALID_ROLES = {"EXECUTOR", "REVIEWER", "COORDINATOR"}
TASK_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*-[A-Z][A-Z0-9]*-\d{2}$")
FIELD_RE = re.compile(r"^([A-Z][A-Z0-9_]*):\s*(.*?)\s*$", re.MULTILINE)


def parse_fields(text: str) -> dict[str, str]:
    pairs = FIELD_RE.findall(text)
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"duplicate fields: {', '.join(duplicates)}")
    return dict(pairs)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def resolve_from_root(value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else ROOT / path).resolve()


def require_within(path: Path, parent: Path, label: str) -> Path:
    resolved_parent = parent.resolve()
    if not path.is_relative_to(resolved_parent):
        raise SystemExit(f"{label} must stay within {resolved_parent}: {path}")
    return path


def resolve_task(value: str) -> Path:
    path = resolve_from_root(value).resolve()
    require_within(path, TASK_ROOT, "task")
    if path.parent not in {directory.resolve() for directory in TASK_DIRS}:
        raise SystemExit(f"task must be directly under queue/active/archive: {path}")
    if not path.is_file():
        raise SystemExit(f"task not found: {path}")
    return path


def project_path(project_id: str) -> Path:
    return PROJECT_ROOT / f"{project_id}.md"


def task_files() -> list[Path]:
    return sorted(path for directory in TASK_DIRS for path in directory.glob("*.md"))


def validate() -> list[str]:
    errors: list[str] = []
    required_files = (
        ROOT / "AGENTS.md",
        TASK_ROOT / "README.md",
        TASK_ROOT / "PROTOCOL.md",
        TASK_ROOT / "PROJECTS.md",
        TASK_ROOT / "current_task.md",
    )
    for path in required_files:
        if not path.is_file():
            errors.append(f"missing protocol file: {path.relative_to(ROOT)}")

    project_ids: set[str] = set()
    for path in sorted(PROJECT_ROOT.glob("*.md")):
        try:
            data = parse_fields(path.read_text(encoding="utf-8"))
        except ValueError as error:
            errors.append(f"{path.relative_to(ROOT)}: {error}")
            continue
        project_id = data.get("PROJECT_ID", "")
        if not project_id:
            errors.append(f"{path.relative_to(ROOT)}: missing PROJECT_ID")
            continue
        if project_id in project_ids:
            errors.append(f"duplicate PROJECT_ID: {project_id}")
        project_ids.add(project_id)
        repo = Path(data.get("REPO_ROOT", ""))
        if not repo.is_dir():
            errors.append(f"{path.relative_to(ROOT)}: REPO_ROOT missing: {repo}")
        else:
            try:
                git(repo, "rev-parse", "--show-toplevel")
            except (subprocess.CalledProcessError, FileNotFoundError):
                errors.append(f"{path.relative_to(ROOT)}: REPO_ROOT is not a Git checkout")

    active: dict[tuple[str, str], list[str]] = {}
    seen_task_ids: dict[str, Path] = {}
    for path in task_files():
        text = path.read_text(encoding="utf-8")
        try:
            data = parse_fields(text)
        except ValueError as error:
            errors.append(f"{path.relative_to(ROOT)}: {error}")
            continue
        missing = sorted(REQUIRED_FIELDS - data.keys())
        if missing:
            errors.append(f"{path.relative_to(ROOT)}: missing fields {', '.join(missing)}")
            continue
        task_id = data["TASK_ID"]
        if task_id in seen_task_ids:
            errors.append(
                f"duplicate TASK_ID {task_id}: "
                f"{seen_task_ids[task_id].relative_to(ROOT)}, {path.relative_to(ROOT)}"
            )
        else:
            seen_task_ids[task_id] = path
        if not TASK_ID_RE.fullmatch(task_id):
            errors.append(f"{path.relative_to(ROOT)}: invalid TASK_ID {task_id}")
        if path.stem != task_id:
            errors.append(f"{path.relative_to(ROOT)}: filename must match TASK_ID")
        expected_prefix = f"{data['PROJECT_ID']}-{data['WORKSTREAM']}-"
        if not task_id.startswith(expected_prefix):
            errors.append(f"{path.relative_to(ROOT)}: TASK_ID does not match project/workstream")
        if data["PROJECT_ID"] not in project_ids:
            errors.append(f"{path.relative_to(ROOT)}: unknown PROJECT_ID {data['PROJECT_ID']}")
        status = data["STATUS"]
        if status not in VALID_STATUSES:
            errors.append(f"{path.relative_to(ROOT)}: invalid STATUS {status}")
        if data["ROLE"] not in VALID_ROLES:
            errors.append(f"{path.relative_to(ROOT)}: invalid ROLE {data['ROLE']}")

        repo = Path(data["REPO_ROOT"])
        if repo.is_dir() and data["BASE_COMMIT"] and "<" not in data["BASE_COMMIT"]:
            try:
                git(repo, "cat-file", "-e", f"{data['BASE_COMMIT']}^{{commit}}")
            except (subprocess.CalledProcessError, FileNotFoundError):
                errors.append(f"{path.relative_to(ROOT)}: BASE_COMMIT not found")

        if status == "ACTIVE":
            key = (data["PROJECT_ID"], data["WORKSTREAM"])
            active.setdefault(key, []).append(task_id)
            if path.parent.name != "active":
                errors.append(f"{path.relative_to(ROOT)}: ACTIVE task must be in tasks/active")
        if status == "DRAFT" and path.parent.name != "queue":
            errors.append(f"{path.relative_to(ROOT)}: DRAFT task must be in tasks/queue")
        if status == "ACCEPTED" and path.parent.name != "archive":
            errors.append(f"{path.relative_to(ROOT)}: ACCEPTED task must be in tasks/archive")
        if status == "READY_FOR_REVIEW":
            handoff = resolve_from_root(data["HANDOFF_PATH"])
            try:
                require_within(handoff, TASK_ROOT / "handoffs", "handoff")
            except SystemExit as error:
                errors.append(f"{path.relative_to(ROOT)}: {error}")
                continue
            if not handoff.is_file():
                errors.append(f"{path.relative_to(ROOT)}: handoff missing: {handoff}")
            else:
                try:
                    handoff_data = parse_fields(handoff.read_text(encoding="utf-8"))
                except ValueError as error:
                    errors.append(f"{handoff.relative_to(ROOT)}: {error}")
                else:
                    for key in ("TASK_ID", "PROJECT_ID"):
                        if handoff_data.get(key) != data[key]:
                            errors.append(f"{handoff.relative_to(ROOT)}: {key} does not match task")
        if status != "DRAFT":
            for marker in ("<PROJECT>", "<WORKSTREAM>", "<FULL_COMMIT>", "一个主要目标"):
                if marker in text:
                    errors.append(f"{path.relative_to(ROOT)}: unresolved marker {marker}")

    for key, ids in active.items():
        if len(ids) > 1:
            errors.append(f"multiple ACTIVE tasks for {key[0]}/{key[1]}: {', '.join(ids)}")
    return errors


def next_task_id(project_id: str, workstream: str) -> str:
    prefix = f"{project_id}-{workstream}-"
    numbers = [
        int(path.stem[len(prefix) :])
        for path in task_files()
        if path.stem.startswith(prefix) and path.stem[len(prefix) :].isdigit()
    ]
    return f"{prefix}{max(numbers, default=0) + 1:02d}"


def command_check(_: argparse.Namespace) -> int:
    errors = validate()
    if errors:
        print(f"FAIL: {len(errors)} protocol error(s)")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"PASS: {len(list(PROJECT_ROOT.glob('*.md')))} projects; {len(task_files())} tasks")
    return 0


def command_new(args: argparse.Namespace) -> int:
    project_id = args.project.upper()
    workstream = args.workstream.upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9]*", workstream):
        raise SystemExit("workstream must contain only A-Z and 0-9 and start with a letter")
    project = project_path(project_id)
    if not project.is_file():
        raise SystemExit(f"unknown project: {project_id}")
    project_data = parse_fields(project.read_text(encoding="utf-8"))
    repo = Path(project_data["REPO_ROOT"])
    task_id = next_task_id(project_id, workstream)
    replacements = {
        "<TASK_ID>": task_id,
        "<TITLE>": args.title,
        "<PROJECT>-<WORKSTREAM>-<NN>": task_id,
        "<PROJECT>": project_id,
        "<WORKSTREAM>": workstream,
        "<FULL_COMMIT>": git(repo, "rev-parse", "HEAD"),
        "<ISO8601>": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    text = (TASK_ROOT / "templates" / "TASK.md").read_text(encoding="utf-8")
    for old, new in replacements.items():
        text = text.replace(old, new)
    destination = TASK_ROOT / "queue" / f"{task_id}.md"
    destination.write_text(text, encoding="utf-8")
    print(destination)
    return 0


def command_handoff(args: argparse.Namespace) -> int:
    task_path = resolve_task(args.task)
    task = parse_fields(task_path.read_text(encoding="utf-8"))
    destination = resolve_from_root(task["HANDOFF_PATH"])
    require_within(destination, TASK_ROOT / "handoffs", "handoff")
    if destination.exists() and not args.force:
        raise SystemExit(f"handoff already exists: {destination}; use --force to replace")
    replacements = {
        "<TASK_ID>": task["TASK_ID"],
        "<PROJECT>": task["PROJECT_ID"],
        "<FULL_COMMIT_OR_NONE>": "NONE",
        "<FULL_COMMIT>": task["BASE_COMMIT"],
        "<ISO8601>": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    text = (TASK_ROOT / "templates" / "HANDOFF.md").read_text(encoding="utf-8")
    for old, new in replacements.items():
        text = text.replace(old, new)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    print(destination)
    return 0


def role_contract(role: str) -> str:
    if role == "glm":
        return (
            "ROLE: EXECUTOR (GLM)\n\nExecute only the task below. Read the repository AGENTS.md, "
            "respect write scope and existing work, run validation, and produce the required handoff. "
            "Return READY_FOR_REVIEW or BLOCKED; do not self-accept, promote a stage, or enable live/external capability."
        )
    return (
        "ROLE: REVIEWER (GPT Pro)\n\nPerform a read-only independent review of the task, handoff, diff/tests and artifacts. "
        "Do not edit, broaden scope, invent evidence, update project state, or authorize outside the allowed dispositions. "
        "Return one allowed disposition and concise evidence-backed findings. Review is not an owner decision."
    )


def command_pack(args: argparse.Namespace) -> int:
    task_path = resolve_task(args.task)
    task_text = task_path.read_text(encoding="utf-8")
    task = parse_fields(task_text)
    project = project_path(task["PROJECT_ID"])
    handoff: Path | None = None
    if args.handoff:
        handoff = resolve_from_root(args.handoff)
    elif args.role == "gptpro":
        handoff = resolve_from_root(task["HANDOFF_PATH"])
    if handoff is not None:
        require_within(handoff, TASK_ROOT / "handoffs", "handoff")
    if args.role == "gptpro" and (handoff is None or not handoff.is_file()):
        raise SystemExit("gptpro packet requires an existing handoff")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{task['TASK_ID']}-{args.role.upper()}-{timestamp}"
    destination = resolve_from_root(args.output) if args.output else TASK_ROOT / "packets" / f"{run_id}.md"
    require_within(destination, TASK_ROOT / "packets", "packet output")
    if destination.exists():
        raise SystemExit(f"packet already exists: {destination}")
    attachments = [resolve_from_root(value) for value in args.attachment]
    for attachment in attachments:
        if not attachment.is_file():
            raise SystemExit(f"attachment not found or not a file: {attachment}")
    bundle = destination.with_suffix(".zip") if attachments else None
    if bundle is not None and bundle.exists():
        raise SystemExit(f"packet bundle already exists: {bundle}")
    sources = [ROOT / "AGENTS.md", TASK_ROOT / "PROTOCOL.md", project, task_path]
    if handoff is not None:
        sources.append(handoff)
    sources.extend(attachments)
    manifest = "\n".join(f"- `{path}` — SHA256 `{file_sha256(path)}`" for path in sources)
    sections = [
        f"# {run_id}\n\n{role_contract(args.role)}",
        f"\n\n## Source manifest\n\n{manifest}",
        "\n\n## Repository rules\n\n" + (ROOT / "AGENTS.md").read_text(encoding="utf-8"),
        "\n\n## Task protocol\n\n" + (TASK_ROOT / "PROTOCOL.md").read_text(encoding="utf-8"),
        "\n\n## Current project state\n\n" + project.read_text(encoding="utf-8"),
        "\n\n## Task\n\n" + task_text,
    ]
    if handoff is not None:
        sections.append("\n\n## Execution handoff\n\n" + handoff.read_text(encoding="utf-8"))
    attachment_note = (
        "Included in the companion ZIP:\n"
        + "\n".join(f"- `{path}`" for path in attachments)
        if attachments
        else "NONE. Provide every artifact listed under Material inputs or Artifacts and hashes separately."
    )
    sections.append(f"\n\n## Evidence attachments\n\n{attachment_note}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(sections) + "\n", encoding="utf-8")
    print(destination)
    if bundle is not None:
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(destination, arcname=destination.name)
            for index, attachment in enumerate(attachments, start=1):
                archive.write(attachment, arcname=f"attachments/{index:02d}-{attachment.name}")
        print(bundle)
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.set_defaults(func=command_check)
    new = commands.add_parser("new")
    new.add_argument("--project", required=True)
    new.add_argument("--workstream", required=True)
    new.add_argument("--title", required=True)
    new.set_defaults(func=command_new)
    handoff = commands.add_parser("handoff")
    handoff.add_argument("--task", required=True)
    handoff.add_argument("--force", action="store_true")
    handoff.set_defaults(func=command_handoff)
    pack = commands.add_parser("pack")
    pack.add_argument("--role", required=True, choices=("glm", "gptpro"))
    pack.add_argument("--task", required=True)
    pack.add_argument("--handoff")
    pack.add_argument("--output")
    pack.add_argument("--attachment", action="append", default=[])
    pack.set_defaults(func=command_pack)
    return root


def main() -> int:
    args = parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
