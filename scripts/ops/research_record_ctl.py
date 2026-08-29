#!/usr/bin/env python3
"""Create, validate, summarize, and render project research records."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_governance.record import (  # noqa: E402
    SCHEMA_VERSION,
    ResearchRecord,
    build_prompt,
    validate_record_file,
)


SKILL_CATALOG = ROOT / "skills" / "catalog.yaml"


def skill_names(catalog_path: Path = SKILL_CATALOG) -> set[str]:
    payload = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    skills = payload.get("skills") if isinstance(payload, dict) else None
    if not isinstance(skills, dict):
        raise ValueError(f"invalid skill catalog: {catalog_path}")
    return set(skills)


def _encoded_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace ``path`` with canonical JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    target_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, target_mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(_encoded_json(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_schema(path: Path, payload: dict[str, Any], *, force: bool = False) -> bool:
    """Write a schema without silently replacing a different existing schema.

    Returns ``True`` when bytes were written and ``False`` when the existing
    file already represented the same JSON value.
    """
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            if not force:
                raise FileExistsError(
                    f"refusing to overwrite unreadable schema without --force: {path}"
                ) from exc
        else:
            if existing == payload:
                return False
            if not force:
                raise FileExistsError(
                    f"refusing to overwrite different schema without --force: {path}"
                )
    write_json(path, payload)
    return True


def initial_payload(args: argparse.Namespace) -> dict[str, Any]:
    record_id = f"research:{args.domain}:{args.family}:{args.run_id}"
    return {
        "schema_version": SCHEMA_VERSION,
        "record_id": record_id,
        "run_id": args.run_id,
        "domain": args.domain,
        "family": args.family,
        "skill": args.skill.removeprefix("$"),
        "lifecycle_status": "planned",
        "observed_at_utc": None,
        "question": {
            "hypothesis": "TODO: one falsifiable hypothesis",
            "decision_target": "TODO: the decision this evidence may change",
            "scope": "TODO: fixed universe, dates, cities, and sources",
            "exclusions": ["live behavior changes", "unregistered variants"],
        },
        "method": {
            "grain": "TODO",
            "denominator_scope": "TODO",
            "evidence_layers": ["TODO"],
            "pit_or_asof_policy": "TODO",
            "label_contract": "TODO",
            "primary_metrics": ["TODO"],
            "baselines": ["TODO"],
            "forward_policy": "TODO",
            "acceptance_gates": ["TODO"],
            "fee_and_execution_basis": "TODO or N/A with reason",
        },
        "inputs": [],
        "execution": {
            "producer": "TODO: stable runner path",
            "code_identity": "TODO: git SHA or immutable release identity",
            "config_locator": None,
            "config_identity": "TODO: config hash or N/A with reason",
            "reproduce_command": "TODO: exact command",
        },
        "outputs": {
            "artifact_root_contract": "production://research_artifact_root",
            "artifact_manifest": None,
            "canonical_machine_format": "none",
            "compact_summary_locator": None,
        },
        "knowledge": {
            "family_living_doc": args.living_doc,
            "registry_or_index": args.registry,
            "dated_snapshot": None,
            "durable_conclusion": None,
            "action": None,
            "superseded_record_ids": [],
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("record", type=Path)
    validate.add_argument("--repo-root", type=Path, default=ROOT)

    prompt = subparsers.add_parser("prompt")
    prompt.add_argument("record", type=Path)
    prompt.add_argument("--repo-root", type=Path, default=ROOT)

    summary = subparsers.add_parser("summary")
    summary.add_argument("record", type=Path)
    summary.add_argument("--repo-root", type=Path, default=ROOT)

    schema = subparsers.add_parser("schema")
    schema.add_argument("--out", type=Path)
    schema.add_argument("--force", action="store_true")

    init = subparsers.add_parser("init")
    init.add_argument("--domain", required=True)
    init.add_argument("--family", required=True)
    init.add_argument("--run-id", required=True)
    init.add_argument("--skill", required=True)
    init.add_argument("--living-doc", required=True)
    init.add_argument("--registry", required=True)
    init.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "schema":
            payload = ResearchRecord.model_json_schema()
            if args.out is None:
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                write_schema(args.out, payload, force=args.force)
            return 0
        if args.command == "init":
            if args.skill.removeprefix("$") not in skill_names():
                raise ValueError(f"unknown skill: {args.skill}")
            payload = initial_payload(args)
            ResearchRecord.model_validate(payload)
            if args.out.exists():
                raise FileExistsError(f"refusing to overwrite research record: {args.out}")
            write_json(args.out, payload)
            print(args.out)
            return 0

        record = validate_record_file(
            args.record,
            repo_root=args.repo_root,
            skill_names=skill_names(),
        )
        if args.command == "prompt":
            print(build_prompt(record), end="")
        elif args.command == "summary":
            print(
                json.dumps(
                    {
                        "record_id": record.record_id,
                        "lifecycle_status": record.lifecycle_status.value,
                        "skill": record.skill,
                        "denominator_scope": record.method.denominator_scope,
                        "artifact_manifest": record.outputs.artifact_manifest,
                        "family_living_doc": record.knowledge.family_living_doc,
                        "durable_conclusion": record.knowledge.durable_conclusion,
                        "action": record.knowledge.action,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(f"research record ok: {record.record_id}")
        return 0
    except (FileExistsError, OSError, ValidationError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
