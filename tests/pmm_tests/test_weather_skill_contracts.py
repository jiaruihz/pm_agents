from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_all_weather_skills_are_routed_and_indexed() -> None:
    names = {path.parent.name for path in (ROOT / "skills").glob("weather-*/SKILL.md")}
    agents = _read("AGENTS.md")
    claude = _read("CLAUDE.md")
    index = _read("docs/WEATHER_DOCS_INDEX.md")

    assert "weather-jrs-runtime-failover" in names
    for name in names:
        assert (ROOT / "skills" / name / "agents/openai.yaml").is_file(), name
        assert name in agents, name
        assert name in claude, name
        assert name in index, name


def test_deploy_skill_uses_only_canonical_jrs_process_context() -> None:
    text = _read("skills/weather-strategy-deploy/SKILL.md")

    for forbidden in (
        "tmux list-sessions",
        "tmux -L weather-jrs",
        "screen -ls",
        "LaunchAgent/tmux/screen",
    ):
        assert forbidden not in text

    for required in (
        "weather_production_ctl.py health",
        "weather_production_ctl.py plan",
        "weather_production_manifest.py --strict",
        "weather-data-feed-jrs",
        "weather_jrs_tmux_env.sh",
    ):
        assert required in text


def test_wcir_skills_pin_decision_and_canonical_identity() -> None:
    lineage = _read("skills/weather-strategy-lineage/SKILL.md")
    performance = _read("skills/weather-strategy-performance/SKILL.md")
    research = _read("skills/weather-strategy-research/SKILL.md")

    for term in (
        "EventEnvelope",
        "DecisionContext",
        "ModelOutput",
        "SignalCandidate",
        "TradeIntent",
        "feature_book_snapshot_id",
        "execution_book_snapshot_id",
    ):
        assert term in lineage

    for text in (lineage, performance, research):
        assert "candidate_grain_version" in text
        assert "build_id" in text
        assert "observed_at_utc" in text


def test_fact_refresh_prefers_bounded_and_incremental_paths() -> None:
    text = _read("skills/weather-fact-rebuild/SKILL.md")

    assert "start_weather_canonical_refresh_tmux.sh" in text
    assert "bounded canonical refresh one-shot" in text
    assert "已审批的增量" in text
    assert "不无条件重算全部 candidate" in text
    assert "run_stack.sh --rebuild" in text


def test_system_and_analysis_contracts_use_wcir_main_lineage() -> None:
    paths = (
        "AGENTS.md",
        "CLAUDE.md",
        "docs/WEATHER_ANALYSIS_CONTRACT.md",
        "docs/WEATHER_SYSTEM_CONTRACT.md",
    )

    for path in paths:
        text = _read(path)
        for term in (
            "EventEnvelope",
            "DecisionContext",
            "ModelOutput",
            "SignalCandidate",
            "TradeIntent",
        ):
            assert term in text, (path, term)

    for path in paths[2:]:
        text = _read(path)
        assert "candidate_grain_version" in text
        assert "build_id" in text
        assert "observed_at_utc" in text
