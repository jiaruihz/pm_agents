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


def test_jrs_prompts_do_not_treat_identity_as_permission_or_overclaim_fix() -> None:
    for path in (
        "AGENTS.md",
        "CLAUDE.md",
        "skills/weather-strategy-deploy/SKILL.md",
        "skills/weather-jrs-runtime-failover/SKILL.md",
    ):
        text = _read(path)
        for required in (
            "不能证明",
            "TCC",
            "reboot/login",
        ):
            assert required in text, (path, required)

    agents = _read("AGENTS.md")
    claude = _read("CLAUDE.md")
    for text in (agents, claude):
        assert "历史复发优先于“已修好”叙述" in text
        assert "永久解决的验收标准" in text
        assert "unit test mock 通过不能替代" in text

    failover = _read("skills/weather-jrs-runtime-failover/SKILL.md")
    assert "prospective server" in failover
    assert "canonical server death/recreate" in failover
    assert "recovery improved" in failover


def test_jrs_prompts_and_mutation_skills_require_attach_only_controller_entry() -> None:
    paths = (
        "AGENTS.md",
        "CLAUDE.md",
        "skills/weather-strategy-deploy/SKILL.md",
        "skills/weather-jrs-runtime-failover/SKILL.md",
    )

    for path in paths:
        text = _read(path)
        for required in (
            "attach-only",
            "recover-jrs-context",
            "controller",
            "fail closed",
        ):
            assert required in text, (path, required)

    deploy = _read("skills/weather-strategy-deploy/SKILL.md")
    assert "bounded one-shot" in deploy
    assert "直接执行 start/stop" in deploy


def test_deploy_skill_defaults_to_mac_and_quarantines_n100_history() -> None:
    text = _read("skills/weather-strategy-deploy/SKILL.md")

    for required in (
        "当前生产主机是 Mac mini",
        "src/strategies/runtime/production.yaml",
        "Historical Production Posture",
        "用户明确提出“N100 灾备恢复/迁回”",
        "不得 SSH N100",
        "本 skill 不包含 N100 的部署或恢复命令",
        "独立恢复合同",
    ):
        assert required in text

    assert "先读：`AGENTS.md`、`WEATHER_REPO_BOUNDARY.md`、`WEATHER_STRATEGY_ENTRYPOINT.md`" not in text


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


def test_weather_research_instruction_layers_use_one_brief_template() -> None:
    for path in ("AGENTS.md", "CLAUDE.md"):
        text = _read(path)
        assert "研究指令分层" in text
        assert "docs/analysis/templates/research.md" in text
        assert "唯一动作" in text

    skill = _read("skills/weather-strategy-research/SKILL.md")
    for required in (
        "单轮研究 brief 与 readiness",
        "一个可证伪假设",
        "验收门槛",
        "唯一动作",
        "clean frozen-forward status",
    ):
        assert required in skill

    template = _read("docs/analysis/templates/research.md")
    for required in (
        "## 单轮 brief",
        "## Readiness",
        "acceptance gates",
        "PIT state + four clocks",
        "clean frozen-forward status",
    ):
        assert required in template


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
