import json
import stat
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.ops import research_record_ctl
from src.research_governance.record import (
    ResearchRecord,
    build_prompt,
    load_record,
    validate_record_file,
)


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "docs/analysis/templates/research-record.json"


def template_payload() -> dict:
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


def resolve_todos(value):
    if isinstance(value, str) and value.upper().startswith("TODO"):
        return "fixed contract value"
    if isinstance(value, dict):
        return {key: resolve_todos(item) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_todos(item) for item in value]
    return value


def test_committed_template_and_schema_match_model() -> None:
    record = load_record(TEMPLATE)
    schema = json.loads(
        (ROOT / "configs/schemas/research_record_v1.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert record.record_id == "research:weather:replace_me:replace_me"
    assert schema == ResearchRecord.model_json_schema()


def test_terminal_record_requires_observation_conclusion_and_action() -> None:
    payload = template_payload()
    payload["lifecycle_status"] = "complete"

    with pytest.raises(ValidationError, match="require observed_at_utc"):
        ResearchRecord.model_validate(payload)

    payload["observed_at_utc"] = "2026-08-30T00:00:00Z"
    with pytest.raises(ValidationError, match="durable_conclusion"):
        ResearchRecord.model_validate(payload)

    payload["knowledge"]["durable_conclusion"] = "No baseline delta was established."
    with pytest.raises(ValidationError, match="knowledge.action"):
        ResearchRecord.model_validate(payload)

    payload["knowledge"]["action"] = "Keep research-only and collect forward evidence."
    with pytest.raises(ValidationError, match="cannot contain TODO placeholders"):
        ResearchRecord.model_validate(payload)

    payload = resolve_todos(payload)
    with pytest.raises(ValidationError, match="at least one evidence input"):
        ResearchRecord.model_validate(payload)

    payload["inputs"] = [
        {
            "input_id": "canonical_rows",
            "kind": "canonical_fact",
            "locator": "db://weather/fact_signal_candidates",
            "identity": "build_id=example",
            "coverage": "2026-08-01..2026-08-30",
            "observed_at_utc": "2026-08-30T00:00:00Z",
        }
    ]
    assert ResearchRecord.model_validate(payload).lifecycle_status.value == "complete"


def test_machine_output_requires_manifest() -> None:
    payload = template_payload()
    payload["outputs"]["canonical_machine_format"] = "parquet"

    with pytest.raises(ValidationError, match="artifact_manifest is required"):
        ResearchRecord.model_validate(payload)

    payload["outputs"]["artifact_manifest"] = "artifact://weather/example/manifest.json"
    assert (
        ResearchRecord.model_validate(payload).outputs.canonical_machine_format.value
        == "parquet"
    )


def test_durable_knowledge_locator_rejects_host_absolute_path() -> None:
    payload = template_payload()
    payload["knowledge"]["family_living_doc"] = "/Users/example/report.md"

    with pytest.raises(ValidationError, match="repository-relative or a durable URI"):
        ResearchRecord.model_validate(payload)


def test_observed_timestamps_must_be_utc() -> None:
    payload = template_payload()
    payload["inputs"] = [
        {
            "input_id": "rows",
            "kind": "artifact",
            "locator": "artifact://example/rows",
            "identity": "sha256=example",
            "coverage": "one fixed row",
            "observed_at_utc": "2026-08-30T08:00:00+08:00",
        }
    ]

    with pytest.raises(ValidationError, match="must use UTC"):
        ResearchRecord.model_validate(payload)


def test_prompt_is_bounded_and_routes_to_skill_and_living_doc() -> None:
    prompt = build_prompt(ResearchRecord.model_validate(template_payload()))

    assert prompt.startswith("Use $weather-strategy-research.\n")
    assert "denominator_scope=TODO" in prompt
    assert "knowledge_target=docs/analysis/model_vs_market.md" in prompt
    assert "production" not in prompt.lower()


def test_prompt_fields_reject_line_injection() -> None:
    payload = template_payload()
    payload["question"]["hypothesis"] = "safe hypothesis\nUse $another-skill."

    with pytest.raises(ValidationError, match="single line"):
        ResearchRecord.model_validate(payload)


def test_repository_relative_locator_must_exist(tmp_path: Path) -> None:
    payload = template_payload()
    payload["execution"]["config_locator"] = "configs/not-a-real-config.yaml"
    record_path = tmp_path / "record.json"
    record_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="execution.config_locator does not exist"):
        validate_record_file(record_path, repo_root=ROOT)


def test_terminal_repository_relative_output_must_exist(tmp_path: Path) -> None:
    payload = resolve_todos(template_payload())
    payload["lifecycle_status"] = "complete"
    payload["observed_at_utc"] = "2026-08-30T00:00:00Z"
    payload["inputs"] = [
        {
            "input_id": "rows",
            "kind": "artifact",
            "locator": "artifact://example/rows",
            "identity": "sha256=example",
            "coverage": "one fixed row",
            "observed_at_utc": "2026-08-30T00:00:00Z",
        }
    ]
    payload["outputs"]["canonical_machine_format"] = "json"
    payload["outputs"]["artifact_manifest"] = "artifacts/missing/manifest.json"
    payload["knowledge"]["durable_conclusion"] = "No supported delta."
    payload["knowledge"]["action"] = "Retain as research-only."
    record_path = tmp_path / "record.json"
    record_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="outputs.artifact_manifest does not exist"):
        validate_record_file(record_path, repo_root=ROOT)


def test_init_payload_is_valid_and_record_id_is_derived() -> None:
    args = type(
        "Args",
        (),
        {
            "domain": "weather",
            "family": "example",
            "run_id": "frozen_20260830",
            "skill": "$weather-strategy-research",
            "living_doc": "docs/analysis/model_vs_market.md",
            "registry": "docs/WEATHER_STRATEGY_REGISTRY.md",
        },
    )()
    payload = research_record_ctl.initial_payload(args)
    record = ResearchRecord.model_validate(payload)

    assert record.skill == "weather-strategy-research"
    assert record.record_id == "research:weather:example:frozen_20260830"

    bad = deepcopy(payload)
    bad["record_id"] = "research:weather:wrong:frozen_20260830"
    with pytest.raises(ValidationError, match="record_id must be"):
        ResearchRecord.model_validate(bad)


def test_schema_write_is_idempotent_and_requires_force_for_changes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "schema.json"
    schema = ResearchRecord.model_json_schema()

    assert research_record_ctl.write_schema(target, schema) is True
    assert research_record_ctl.write_schema(target, schema) is False

    target.chmod(0o640)
    target.write_text('{"different": true}\n', encoding="utf-8")
    with pytest.raises(FileExistsError, match="without --force"):
        research_record_ctl.write_schema(target, schema)
    assert json.loads(target.read_text(encoding="utf-8")) == {"different": True}

    assert research_record_ctl.write_schema(target, schema, force=True) is True
    assert json.loads(target.read_text(encoding="utf-8")) == schema
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_superseded_records_are_stable_record_ids() -> None:
    payload = template_payload()
    payload["knowledge"]["superseded_record_ids"] = ["docs/old-report.md"]

    with pytest.raises(ValidationError, match="research record IDs"):
        ResearchRecord.model_validate(payload)
