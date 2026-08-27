from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts" / "ops").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "AGENTS.md", root / "AGENTS.md")
    shutil.copy2(
        REPO_ROOT / "scripts" / "ops" / "ai_task_ctl.py",
        root / "scripts" / "ops" / "ai_task_ctl.py",
    )
    for name in ("README.md", "PROTOCOL.md", "PROJECTS.md", "current_task.md"):
        destination = root / "tasks" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / "tasks" / name, destination)
    shutil.copytree(REPO_ROOT / "tasks" / "projects", root / "tasks" / "projects")
    shutil.copytree(REPO_ROOT / "tasks" / "templates", root / "tasks" / "templates")
    for name in ("queue", "active", "archive", "handoffs", "packets"):
        (root / "tasks" / name).mkdir(parents=True, exist_ok=True)
    return root


def _run(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "ops" / "ai_task_ctl.py"), *args],
        cwd=root,
        check=check,
        capture_output=True,
        text=True,
    )


def test_end_to_end_packet_and_attachment_bundle(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    created = _run(
        root,
        "new",
        "--project",
        "WCIR",
        "--workstream",
        "ORACLE",
        "--title",
        "packet smoke",
    )
    task = Path(created.stdout.strip())
    handoff = Path(_run(root, "handoff", "--task", str(task)).stdout.strip())
    evidence = root / "evidence.json"
    evidence.write_text('{"ok": true}\n', encoding="utf-8")

    packed = _run(
        root,
        "pack",
        "--role",
        "gptpro",
        "--task",
        str(task),
        "--handoff",
        str(handoff),
        "--attachment",
        str(evidence),
    ).stdout.splitlines()
    packet, bundle = map(Path, packed)

    assert packet.is_file()
    assert bundle.is_file()
    with zipfile.ZipFile(bundle) as archive:
        assert packet.name in archive.namelist()
        assert any(name.endswith("evidence.json") for name in archive.namelist())
    assert "PASS: 3 projects; 1 tasks" in _run(root, "check").stdout


def test_rejects_unsafe_paths_bad_workstream_and_duplicate_ids(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    bad = _run(
        root,
        "new",
        "--project",
        "WCIR",
        "--workstream",
        "../BAD",
        "--title",
        "bad",
        check=False,
    )
    assert bad.returncode != 0

    task = Path(
        _run(
            root,
            "new",
            "--project",
            "WCIR",
            "--workstream",
            "ORACLE",
            "--title",
            "safe",
        ).stdout.strip()
    )
    escaped = _run(
        root,
        "pack",
        "--role",
        "glm",
        "--task",
        str(task),
        "--output",
        "../outside.md",
        check=False,
    )
    assert escaped.returncode != 0

    shutil.copy2(task, root / "tasks" / "active" / task.name)
    duplicate = _run(root, "check", check=False)
    assert duplicate.returncode == 1
    assert "duplicate TASK_ID" in duplicate.stdout
