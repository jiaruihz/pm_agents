#!/usr/bin/env python3
"""Audit project structure, context entrypoints, skills, and artifact boundaries."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs" / "project_structure.yaml"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    message: str
    path: Optional[str] = None


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return payload


def _git_paths(repo_root: Path, *args: str) -> set[str]:
    output = subprocess.check_output(["git", *args], cwd=repo_root).decode(
        "utf-8", errors="surrogateescape"
    )
    return {item for item in output.split("\0") if item}


def tracked_files(repo_root: Path) -> set[str]:
    return _git_paths(repo_root, "ls-files", "-z")


def untracked_files(repo_root: Path, prefix: Optional[str] = None) -> set[str]:
    args = ["ls-files", "-z", "--others", "--exclude-standard"]
    if prefix:
        args.extend(["--", prefix])
    return _git_paths(repo_root, *args)


def is_ignored(repo_root: Path, relative: str) -> bool:
    completed = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", relative],
        cwd=repo_root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def _split_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("missing YAML frontmatter")
    try:
        raw = text.split("---\n", 2)[1]
    except IndexError as exc:
        raise ValueError("unterminated YAML frontmatter") from exc
    payload = yaml.safe_load(raw)
    if not isinstance(payload, dict):
        raise ValueError("frontmatter must be a mapping")
    return payload


def _top_level_tracked(tracked: Iterable[str]) -> tuple[set[str], set[str]]:
    directories: set[str] = set()
    files: set[str] = set()
    for relative in tracked:
        if "/" in relative:
            directories.add(relative.split("/", 1)[0])
        else:
            files.add(relative)
    return directories, files


def _audit_roots(
    repo_root: Path, config: dict[str, Any], tracked: set[str]
) -> tuple[list[Finding], dict[str, Any]]:
    findings: list[Finding] = []
    actual_dirs, actual_files = _top_level_tracked(tracked)
    expected_dirs = set(config["tracked_root_directories"])
    expected_files = set(config["tracked_root_files"])
    for value in sorted(actual_dirs - expected_dirs):
        findings.append(
            Finding(
                "error", "unknown_tracked_root", "tracked root is unclassified", value
            )
        )
    for value in sorted(expected_dirs - actual_dirs):
        findings.append(
            Finding(
                "error",
                "missing_tracked_root",
                "registered tracked root is absent",
                value,
            )
        )
    for value in sorted(actual_files - expected_files):
        findings.append(
            Finding(
                "error", "unknown_root_file", "tracked root file is unclassified", value
            )
        )
    for value in sorted(expected_files - actual_files):
        findings.append(
            Finding(
                "error", "missing_root_file", "registered root file is absent", value
            )
        )

    transient = tuple(f"{root}/" for root in config["transient_roots"])
    for relative in sorted(path for path in tracked if path.startswith(transient)):
        findings.append(
            Finding(
                "error",
                "tracked_transient_artifact",
                "transient workspace content must not be tracked",
                relative,
            )
        )

    classified_physical = (
        expected_dirs
        | set(config["workspace_only_roots"])
        | set(config["transient_roots"])
        | set(config["legacy_or_workspace_config_roots"])
    )
    physical_dirs = {
        path.name
        for path in repo_root.iterdir()
        if path.is_dir() and path.name != ".git"
    }
    for value in sorted(physical_dirs - classified_physical):
        findings.append(
            Finding(
                "warning",
                "unclassified_workspace_root",
                "untracked workspace root needs an owner before promotion",
                value,
            )
        )
    legacy_untracked_files = config.get("legacy_untracked_root_files") or {}
    if not isinstance(legacy_untracked_files, dict):
        raise ValueError("legacy_untracked_root_files must be a mapping")
    physical_files = {
        path.name
        for path in repo_root.iterdir()
        if path.name != ".git" and (path.is_file() or path.is_symlink())
    }
    for value in sorted(physical_files - actual_files):
        if is_ignored(repo_root, value):
            continue
        if value in legacy_untracked_files:
            findings.append(
                Finding(
                    "warning",
                    "legacy_untracked_root_file",
                    str(legacy_untracked_files[value]),
                    value,
                )
            )
            continue
        findings.append(
            Finding(
                "error",
                "unclassified_workspace_file",
                "untracked root file must be ignored, registered as legacy, or moved",
                value,
            )
        )
    return findings, {
        "tracked_files": len(tracked),
        "tracked_root_directories": len(actual_dirs),
        "tracked_root_files": len(actual_files),
        "physical_root_directories": len(physical_dirs),
        "physical_root_files": len(physical_files),
    }


def _audit_context(repo_root: Path, config: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    contract = config["context_contract"]
    for relative in contract["required_files"]:
        if not (repo_root / relative).is_file():
            findings.append(
                Finding(
                    "error",
                    "missing_context_file",
                    "context contract target is absent",
                    relative,
                )
            )
    max_lines = int(contract["max_entrypoint_lines"])
    target_lines = int(contract.get("target_entrypoint_lines", max_lines))
    for relative in ("AGENTS.md", "CLAUDE.md"):
        path = repo_root / relative
        if not path.is_file():
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > max_lines:
            findings.append(
                Finding(
                    "error",
                    "context_entrypoint_too_large",
                    f"entrypoint has {lines} lines; maximum is {max_lines}",
                    relative,
                )
            )
        elif lines > target_lines:
            findings.append(
                Finding(
                    "warning",
                    "context_entrypoint_above_target",
                    f"entrypoint has {lines} lines; target is {target_lines}",
                    relative,
                )
            )
    readme_path = repo_root / "README.md"
    if readme_path.is_file():
        readme = readme_path.read_text(encoding="utf-8")
        for marker in contract["readme_required_links"]:
            if marker not in readme:
                findings.append(
                    Finding(
                        "error",
                        "readme_missing_route",
                        f"README must route to {marker}",
                        "README.md",
                    )
                )
        for marker in contract["readme_forbidden_markers"]:
            if marker in readme:
                findings.append(
                    Finding(
                        "error",
                        "readme_stale_runtime",
                        f"stale runtime marker: {marker}",
                        "README.md",
                    )
                )
    return findings


def _audit_skills(
    repo_root: Path, catalog_path: Path
) -> tuple[list[Finding], dict[str, Any]]:
    findings: list[Finding] = []
    catalog = _load_yaml(catalog_path)
    entries = catalog.get("skills")
    if not isinstance(entries, dict):
        raise ValueError(f"skill catalog must contain a skills mapping: {catalog_path}")
    skill_paths = sorted((repo_root / "skills").glob("*/SKILL.md"))
    actual = {path.parent.name for path in skill_paths}
    declared = set(entries)
    for name in sorted(actual - declared):
        findings.append(
            Finding(
                "error",
                "uncataloged_skill",
                "skill is absent from catalog",
                f"skills/{name}",
            )
        )
    for name in sorted(declared - actual):
        findings.append(
            Finding(
                "error",
                "missing_skill",
                "cataloged skill is absent",
                f"skills/{name}",
            )
        )

    allowed_lifecycle = {"active", "dormant"}
    for path in skill_paths:
        name = path.parent.name
        try:
            frontmatter = _split_frontmatter(path)
        except ValueError as exc:
            findings.append(
                Finding(
                    "error",
                    "invalid_skill_frontmatter",
                    str(exc),
                    str(path.relative_to(repo_root)),
                )
            )
            continue
        if frontmatter.get("name") != name:
            findings.append(
                Finding(
                    "error",
                    "skill_name_mismatch",
                    "frontmatter name must match directory",
                    str(path.relative_to(repo_root)),
                )
            )
        if not str(frontmatter.get("description") or "").strip():
            findings.append(
                Finding(
                    "error",
                    "skill_missing_description",
                    "skill description is empty",
                    str(path.relative_to(repo_root)),
                )
            )
        agent = path.parent / "agents" / "openai.yaml"
        if not agent.is_file():
            findings.append(
                Finding(
                    "error",
                    "skill_missing_agent_metadata",
                    "agents/openai.yaml is required",
                    str(path.parent.relative_to(repo_root)),
                )
            )
        else:
            metadata = _load_yaml(agent)
            default_prompt = str(
                (metadata.get("interface") or {}).get("default_prompt") or ""
            )
            if f"${name}" not in default_prompt:
                findings.append(
                    Finding(
                        "error",
                        "skill_prompt_mismatch",
                        "default_prompt must invoke its own skill",
                        str(agent.relative_to(repo_root)),
                    )
                )
        entry = entries.get(name) or {}
        for field in ("domain", "lifecycle", "use_for", "not_for"):
            if not entry.get(field):
                findings.append(
                    Finding(
                        "error",
                        "skill_catalog_field_missing",
                        f"catalog field {field} is required",
                        f"skills/{name}",
                    )
                )
        lifecycle = entry.get("lifecycle")
        if lifecycle not in allowed_lifecycle:
            findings.append(
                Finding(
                    "error",
                    "skill_lifecycle_invalid",
                    f"unsupported lifecycle: {lifecycle}",
                    f"skills/{name}",
                )
            )
        if (
            lifecycle == "dormant"
            and "dormant" not in str(frontmatter.get("description", "")).lower()
        ):
            findings.append(
                Finding(
                    "error",
                    "dormant_skill_not_disclosed",
                    "dormant lifecycle must be explicit in discovery description",
                    str(path.relative_to(repo_root)),
                )
            )
        for dependency in entry.get("depends_on") or []:
            if dependency not in declared:
                findings.append(
                    Finding(
                        "error",
                        "skill_dependency_missing",
                        f"unknown dependency: {dependency}",
                        f"skills/{name}",
                    )
                )
    return findings, {
        "skill_count": len(actual),
        "active_skills": sum(
            (entries.get(name) or {}).get("lifecycle") == "active" for name in declared
        ),
        "dormant_skills": sum(
            (entries.get(name) or {}).get("lifecycle") == "dormant" for name in declared
        ),
    }


def _audit_reviews(
    repo_root: Path, config: dict[str, Any], tracked: set[str], *, deep: bool = False
) -> tuple[list[Finding], dict[str, Any]]:
    findings: list[Finding] = []
    policy = config["review_workspace"]
    review_root = str(policy["root"])
    prefix = f"{review_root}/"
    metadata = set(policy.get("metadata_allowlist") or [])
    legacy = sorted(
        relative
        for relative in tracked
        if relative.startswith(prefix) and relative not in metadata
    )
    legacy_bytes = sum(
        (repo_root / relative).stat().st_size
        for relative in legacy
        if (repo_root / relative).is_file()
    )
    if len(legacy) > int(policy["max_legacy_tracked_files"]):
        findings.append(
            Finding(
                "error",
                "tracked_review_debt_grew",
                f"tracked review files grew to {len(legacy)}",
                review_root,
            )
        )
    if legacy_bytes > int(policy["max_legacy_tracked_bytes"]):
        findings.append(
            Finding(
                "error",
                "tracked_review_bytes_grew",
                f"tracked review bytes grew to {legacy_bytes}",
                review_root,
            )
        )
    visible_untracked = sorted(untracked_files(repo_root, review_root) - metadata)
    if visible_untracked:
        findings.append(
            Finding(
                "error",
                "review_workspace_not_ignored",
                f"{len(visible_untracked)} review artifacts are visible to git",
                review_root,
            )
        )

    physical_count: Optional[int] = None
    physical_bytes: Optional[int] = None
    if deep:
        physical_count = 0
        physical_bytes = 0
        root = repo_root / review_root
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_file():
                    physical_count += 1
                    physical_bytes += path.stat().st_size
    return findings, {
        "legacy_tracked_files": len(legacy),
        "legacy_tracked_bytes": legacy_bytes,
        "visible_untracked_files": len(visible_untracked),
        "physical_files": physical_count,
        "physical_bytes": physical_bytes,
    }


def audit_project(
    repo_root: Path = ROOT, config_path: Path = DEFAULT_CONFIG, *, deep: bool = False
) -> dict[str, Any]:
    config = _load_yaml(config_path)
    tracked = tracked_files(repo_root)
    root_findings, root_metrics = _audit_roots(repo_root, config, tracked)
    skill_findings, skill_metrics = _audit_skills(
        repo_root, repo_root / config["skill_catalog"]
    )
    review_findings, review_metrics = _audit_reviews(
        repo_root, config, tracked, deep=deep
    )
    findings = (
        root_findings
        + _audit_context(repo_root, config)
        + skill_findings
        + review_findings
    )
    template = repo_root / config["research_record_template"]
    if not template.is_file():
        findings.append(
            Finding(
                "error",
                "missing_research_record_template",
                "research record template is absent",
                str(template.relative_to(repo_root)),
            )
        )
    else:
        try:
            from src.research_governance.record import (
                ResearchRecord,
                validate_record_file,
            )

            validate_record_file(
                template,
                repo_root=repo_root,
                skill_names=set(
                    _load_yaml(repo_root / config["skill_catalog"])["skills"]
                ),
            )
        except (OSError, ValueError) as exc:
            findings.append(
                Finding(
                    "error",
                    "invalid_research_record_template",
                    str(exc),
                    str(template.relative_to(repo_root)),
                )
            )
        else:
            schema_path = repo_root / config["research_record_schema"]
            if not schema_path.is_file():
                findings.append(
                    Finding(
                        "error",
                        "missing_research_record_schema",
                        "committed JSON schema is absent",
                        str(schema_path.relative_to(repo_root)),
                    )
                )
            else:
                try:
                    committed_schema = json.loads(
                        schema_path.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError) as exc:
                    findings.append(
                        Finding(
                            "error",
                            "invalid_research_record_schema",
                            str(exc),
                            str(schema_path.relative_to(repo_root)),
                        )
                    )
                else:
                    if committed_schema != ResearchRecord.model_json_schema():
                        findings.append(
                            Finding(
                                "error",
                                "research_record_schema_drift",
                                "committed JSON schema differs from the Pydantic contract",
                                str(schema_path.relative_to(repo_root)),
                            )
                        )
    counts = {
        "errors": sum(item.severity == "error" for item in findings),
        "warnings": sum(item.severity == "warning" for item in findings),
    }
    return {
        "schema_version": "pm_agents_project_structure_audit_v1",
        "counts": counts,
        "metrics": {
            "repository": root_metrics,
            "skills": skill_metrics,
            "reviews": review_metrics,
        },
        "findings": [asdict(item) for item in findings],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument(
        "--deep",
        action="store_true",
        help="include physical review-workspace file and byte counts",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = audit_project(ROOT, args.config, deep=args.deep)
    except (OSError, subprocess.CalledProcessError, ValueError, yaml.YAMLError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        metrics = report["metrics"]
        review_physical = metrics["reviews"]["physical_files"]
        review_physical_label = (
            str(review_physical) if review_physical is not None else "skipped"
        )
        print(
            "project structure: "
            f"tracked={metrics['repository']['tracked_files']} "
            f"skills={metrics['skills']['skill_count']} "
            f"review_physical={review_physical_label} "
            f"errors={report['counts']['errors']} "
            f"warnings={report['counts']['warnings']}"
        )
        for finding in report["findings"]:
            location = f" {finding['path']}:" if finding.get("path") else ""
            print(
                f"{finding['severity'].upper()} [{finding['code']}]"
                f"{location} {finding['message']}"
            )
    if args.strict and report["counts"]["errors"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
