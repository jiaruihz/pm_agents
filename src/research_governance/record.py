"""Project-wide research record contract.

The record is deliberately a small metadata envelope. Domain artifacts keep
their own schemas; this contract only makes the question, denominator,
identity, output route, and durable knowledge handoff consistent.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Literal, Optional
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "pm_agents_research_record_v1"
IDENTIFIER_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
RECORD_ID_PATTERN = re.compile(
    r"research:[a-z0-9][a-z0-9._-]{0,127}:"
    r"[a-z0-9][a-z0-9._-]{0,127}:[a-z0-9][a-z0-9._-]{0,127}"
)
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
ALLOWED_LOCATOR_SCHEMES = {
    "artifact",
    "db",
    "https",
    "jrs",
    "production",
    "runtime",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LifecycleStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETE = "complete"
    BLOCKED = "blocked"
    SUPERSEDED_FOR_NOW = "superseded_for_now"


class MachineFormat(str, Enum):
    CSV = "csv"
    JSON = "json"
    JSONL = "jsonl"
    PARQUET = "parquet"
    SQLITE = "sqlite"
    NONE = "none"


def _identifier(value: str, field_name: str) -> str:
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(
            f"{field_name} must match {IDENTIFIER_PATTERN.pattern!r}: {value!r}"
        )
    return value


def _durable_locator(value: Optional[str], field_name: str) -> Optional[str]:
    if value is None:
        return None
    if CONTROL_CHARACTER_PATTERN.search(value):
        raise ValueError(f"{field_name} cannot contain control characters")
    parsed = urlparse(value)
    if parsed.scheme:
        if parsed.scheme not in ALLOWED_LOCATOR_SCHEMES:
            raise ValueError(
                f"{field_name} uses unsupported locator scheme {parsed.scheme!r}"
            )
        return value
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(
            f"{field_name} must be repository-relative or a durable URI: {value!r}"
        )
    return path.as_posix()


def _single_line(value: str, field_name: str) -> str:
    if CONTROL_CHARACTER_PATTERN.search(value):
        raise ValueError(f"{field_name} must be a single line without control characters")
    return value


def _single_line_list(values: List[str], field_name: str) -> List[str]:
    return [_single_line(value, f"{field_name}[]") for value in values]


def _require_utc(value: Optional[datetime], field_name: str) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field_name} must use UTC")
    return value


class ResearchQuestion(StrictModel):
    hypothesis: str = Field(min_length=1)
    decision_target: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    exclusions: List[str] = Field(min_length=1)

    @field_validator("hypothesis", "decision_target", "scope")
    @classmethod
    def validate_prompt_text(cls, value: str, info) -> str:
        return _single_line(value, f"question.{info.field_name}")

    @field_validator("exclusions")
    @classmethod
    def validate_exclusions(cls, value: List[str]) -> List[str]:
        return _single_line_list(value, "question.exclusions")


class ResearchMethod(StrictModel):
    grain: str = Field(min_length=1)
    denominator_scope: str = Field(min_length=1)
    evidence_layers: List[str] = Field(min_length=1)
    pit_or_asof_policy: str = Field(min_length=1)
    label_contract: str = Field(min_length=1)
    primary_metrics: List[str] = Field(min_length=1)
    baselines: List[str] = Field(min_length=1)
    forward_policy: str = Field(min_length=1)
    acceptance_gates: List[str] = Field(min_length=1)
    fee_and_execution_basis: str = Field(min_length=1)

    @field_validator(
        "grain",
        "denominator_scope",
        "pit_or_asof_policy",
        "label_contract",
        "forward_policy",
        "fee_and_execution_basis",
    )
    @classmethod
    def validate_prompt_text(cls, value: str, info) -> str:
        return _single_line(value, f"method.{info.field_name}")

    @field_validator(
        "evidence_layers", "primary_metrics", "baselines", "acceptance_gates"
    )
    @classmethod
    def validate_prompt_lists(cls, value: List[str], info) -> List[str]:
        return _single_line_list(value, f"method.{info.field_name}")


class EvidenceInput(StrictModel):
    input_id: str
    kind: str = Field(min_length=1)
    locator: str
    identity: str = Field(min_length=1)
    coverage: str = Field(min_length=1)
    observed_at_utc: Optional[datetime] = None

    @field_validator("input_id")
    @classmethod
    def validate_input_id(cls, value: str) -> str:
        return _identifier(value, "input_id")

    @field_validator("locator")
    @classmethod
    def validate_locator(cls, value: str) -> str:
        validated = _durable_locator(value, "inputs[].locator")
        assert validated is not None
        return validated

    @field_validator("observed_at_utc")
    @classmethod
    def validate_observed_at_utc(
        cls, value: Optional[datetime]
    ) -> Optional[datetime]:
        return _require_utc(value, "inputs[].observed_at_utc")


class ExecutionIdentity(StrictModel):
    producer: str = Field(min_length=1)
    code_identity: str = Field(min_length=1)
    config_locator: Optional[str] = None
    config_identity: str = Field(min_length=1)
    reproduce_command: str = Field(min_length=1)

    @field_validator("config_locator")
    @classmethod
    def validate_config_locator(cls, value: Optional[str]) -> Optional[str]:
        return _durable_locator(value, "execution.config_locator")


class OutputRouting(StrictModel):
    artifact_root_contract: str = Field(min_length=1)
    artifact_manifest: Optional[str] = None
    canonical_machine_format: MachineFormat
    compact_summary_locator: Optional[str] = None

    @field_validator(
        "artifact_root_contract", "artifact_manifest", "compact_summary_locator"
    )
    @classmethod
    def validate_output_locator(cls, value: Optional[str], info) -> Optional[str]:
        return _durable_locator(value, f"outputs.{info.field_name}")

    @model_validator(mode="after")
    def machine_output_requires_manifest(self) -> "OutputRouting":
        if (
            self.canonical_machine_format != MachineFormat.NONE
            and self.artifact_manifest is None
        ):
            raise ValueError(
                "artifact_manifest is required when canonical_machine_format is not none"
            )
        return self


class KnowledgeHandoff(StrictModel):
    family_living_doc: str
    registry_or_index: str
    dated_snapshot: Optional[str] = None
    durable_conclusion: Optional[str] = None
    action: Optional[str] = None
    superseded_record_ids: List[str] = Field(default_factory=list)

    @field_validator(
        "family_living_doc", "registry_or_index", "dated_snapshot"
    )
    @classmethod
    def validate_knowledge_locator(cls, value: Optional[str], info) -> Optional[str]:
        return _durable_locator(value, f"knowledge.{info.field_name}")

    @field_validator("superseded_record_ids")
    @classmethod
    def validate_superseded_record_ids(cls, value: List[str]) -> List[str]:
        invalid = [item for item in value if not RECORD_ID_PATTERN.fullmatch(item)]
        if invalid:
            raise ValueError(
                "knowledge.superseded_record_ids must contain research record IDs: "
                + ", ".join(invalid)
            )
        return value


class ResearchRecord(StrictModel):
    schema_version: Literal[SCHEMA_VERSION]
    record_id: str
    run_id: str
    domain: str
    family: str
    skill: str = Field(min_length=1)
    lifecycle_status: LifecycleStatus
    observed_at_utc: Optional[datetime] = None
    question: ResearchQuestion
    method: ResearchMethod
    inputs: List[EvidenceInput] = Field(default_factory=list)
    execution: ExecutionIdentity
    outputs: OutputRouting
    knowledge: KnowledgeHandoff

    @field_validator("domain", "family", "run_id")
    @classmethod
    def validate_identifiers(cls, value: str, info) -> str:
        return _identifier(value, info.field_name)

    @field_validator("skill")
    @classmethod
    def validate_skill(cls, value: str) -> str:
        normalized = value.removeprefix("$")
        return _identifier(normalized, "skill")

    @field_validator("observed_at_utc")
    @classmethod
    def validate_observed_at_utc(
        cls, value: Optional[datetime]
    ) -> Optional[datetime]:
        return _require_utc(value, "observed_at_utc")

    @model_validator(mode="after")
    def validate_identity_and_completion(self) -> "ResearchRecord":
        expected = f"research:{self.domain}:{self.family}:{self.run_id}"
        if self.record_id != expected:
            raise ValueError(f"record_id must be {expected!r}")
        terminal = {
            LifecycleStatus.COMPLETE,
            LifecycleStatus.BLOCKED,
            LifecycleStatus.SUPERSEDED_FOR_NOW,
        }
        if self.lifecycle_status in terminal:
            if self.observed_at_utc is None:
                raise ValueError("terminal research records require observed_at_utc")
            if not self.knowledge.durable_conclusion:
                raise ValueError(
                    "terminal research records require knowledge.durable_conclusion"
                )
            if not self.knowledge.action:
                raise ValueError("terminal research records require knowledge.action")
            serialized = self.model_dump(mode="json")
            pending = sorted(
                path
                for path, value in _walk_strings(serialized)
                if value.upper().startswith("TODO")
            )
            if pending:
                raise ValueError(
                    "terminal research records cannot contain TODO placeholders: "
                    + ", ".join(pending)
                )
        if self.lifecycle_status == LifecycleStatus.COMPLETE and not self.inputs:
            raise ValueError("complete research records require at least one evidence input")
        return self


def _walk_strings(value: object, path: str = ""):
    if isinstance(value, str):
        yield path or "$", value
    elif isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            yield from _walk_strings(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_strings(item, f"{path}[{index}]")


def load_record(path: Path) -> ResearchRecord:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix in {".yaml", ".yml"}:
        payload = yaml.safe_load(text)
    elif suffix == ".json":
        payload = json.loads(text)
    else:
        raise ValueError(f"research record must be JSON or YAML: {path}")
    if not isinstance(payload, dict):
        raise ValueError(f"research record must contain an object: {path}")
    return ResearchRecord.model_validate(payload)


def _repo_relative_target(repo_root: Path, locator: Optional[str]) -> Optional[Path]:
    if locator is None or urlparse(locator).scheme:
        return None
    return repo_root / locator


def validate_record_file(
    path: Path,
    *,
    repo_root: Optional[Path] = None,
    skill_names: Optional[set[str]] = None,
) -> ResearchRecord:
    record = load_record(path)
    if skill_names is not None and record.skill not in skill_names:
        raise ValueError(f"research record references unknown skill: {record.skill}")
    if repo_root is not None:
        required_locators = [
            ("knowledge.family_living_doc", record.knowledge.family_living_doc),
            ("knowledge.registry_or_index", record.knowledge.registry_or_index),
            ("execution.config_locator", record.execution.config_locator),
        ]
        required_locators.extend(
            (f"inputs[{index}].locator", item.locator)
            for index, item in enumerate(record.inputs)
        )
        if record.lifecycle_status in {
            LifecycleStatus.COMPLETE,
            LifecycleStatus.BLOCKED,
            LifecycleStatus.SUPERSEDED_FOR_NOW,
        }:
            required_locators.extend(
                [
                    ("outputs.artifact_manifest", record.outputs.artifact_manifest),
                    (
                        "outputs.compact_summary_locator",
                        record.outputs.compact_summary_locator,
                    ),
                    ("knowledge.dated_snapshot", record.knowledge.dated_snapshot),
                ]
            )
        for field_name, locator in required_locators:
            target = _repo_relative_target(repo_root, locator)
            if target is not None and not target.exists():
                raise ValueError(f"{field_name} does not exist: {locator}")
    return record


def build_prompt(record: ResearchRecord) -> str:
    """Render the bounded, non-duplicative prompt for one research turn."""
    lines = [
        f"Use ${record.skill}.",
        f"research_record={record.record_id}",
        f"hypothesis={record.question.hypothesis}",
        f"decision_target={record.question.decision_target}",
        f"scope={record.question.scope}",
        "exclusions=" + "; ".join(record.question.exclusions),
        f"grain={record.method.grain}",
        f"denominator_scope={record.method.denominator_scope}",
        "primary_metrics=" + ", ".join(record.method.primary_metrics),
        "baselines=" + ", ".join(record.method.baselines),
        "acceptance_gates=" + "; ".join(record.method.acceptance_gates),
        f"forward_policy={record.method.forward_policy}",
        f"output_manifest={record.outputs.artifact_manifest or 'none'}",
        f"knowledge_target={record.knowledge.family_living_doc}",
    ]
    return "\n".join(lines) + "\n"
