import importlib.util
from pathlib import Path

import yaml

from scripts.ops.check_project_structure import _audit_skills
from scripts.ops.check_weather_docs import (
    entrypoint_semantic_errors,
    normalized_entrypoint_core,
)


ROOT = Path(__file__).resolve().parents[2]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compact_entrypoints_keep_semantic_contract_and_adapter_parity() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert entrypoint_semantic_errors("AGENTS.md", agents) == []
    assert entrypoint_semantic_errors("CLAUDE.md", claude) == []
    assert normalized_entrypoint_core(agents) == normalized_entrypoint_core(claude)
    assert "weather_dashboard_api" not in agents
    assert "com.pm-agents.weather-api" not in agents


def test_entrypoint_semantics_detect_missing_skill_route() -> None:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    errors = entrypoint_semantic_errors(
        "AGENTS.md", text.replace("weather-strategy-exposure", "missing-route")
    )

    assert any("weather-strategy-exposure" in error for error in errors)


def test_skill_size_and_default_prompt_contracts() -> None:
    config = yaml.safe_load(
        (ROOT / "configs/project_structure.yaml").read_text(encoding="utf-8")
    )
    findings, metrics = _audit_skills(
        ROOT,
        ROOT / config["skill_catalog"],
        config["context_contract"],
    )

    errors = [finding for finding in findings if finding.severity == "error"]
    assert errors == []
    assert metrics["skill_count"] == 15
    assert metrics["max_skill_lines"] <= config["context_contract"]["skill_max_lines"]
    assert (
        metrics["max_default_prompt_chars"]
        <= config["context_contract"]["skill_default_prompt_max_chars"]
    )


def test_weather_preflight_checks_manifest_before_controller_health() -> None:
    for path in sorted((ROOT / "skills").glob("weather-*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        manifest = "weather_production_manifest.py --strict"
        health = "weather_production_ctl.py health"
        if manifest in text and health in text:
            assert text.index(manifest) < text.index(health), path


def test_chatgpt_scaffold_ignores_login_state_and_records(tmp_path: Path) -> None:
    module = _load_module(
        "chatgpt_web_scaffold",
        ROOT
        / "skills/chatgpt-web-playwright/scripts/scaffold_chatgpt_web_bot.py",
    )
    target = tmp_path / "bot"

    module.scaffold(target)

    ignored = (target / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/profile-chatgpt/" in ignored
    assert "data/records/" in ignored


def test_pmm_snapshot_default_is_runtime_scoped(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_module(
        "pmm_market_data_fetcher",
        ROOT / "skills/pmm-market-data-fetcher/scripts/fetch_market_data.py",
    )
    monkeypatch.chdir(tmp_path)

    output = module._make_output_path("example")

    assert output.parent == Path("runtime/pmm/market_snapshots")
    assert output.name.startswith("market_snapshot_example_")
    assert output.parent.is_dir()
