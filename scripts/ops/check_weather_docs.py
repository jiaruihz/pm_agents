#!/usr/bin/env python3
"""Read-only consistency checks for current weather docs and skills.

Historical snapshots may keep old wording. This checker verifies that current
entrypoints route around those snapshots instead of treating old host/strategy
state as present truth.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def check_entrypoints(errors: list[str]) -> None:
    for path in ("AGENTS.md", "CLAUDE.md"):
        text = read(path)
        if "docs/WEATHER_DOCS_INDEX.md" not in text:
            fail(errors, f"{path}: missing docs index link")
        if "weather-strategy-research" not in text:
            fail(errors, f"{path}: missing research skill routing")
        if "weather-jrs-runtime-failover" not in text:
            fail(errors, f"{path}: missing JRS failover skill routing")
        if not re.search(r"短期生产(?:主机)? = Mac", text):
            fail(errors, f"{path}: missing Mac production boundary")
        for required in (
            "weather_dashboard_api",
            "com.pm-agents.weather-api",
            "canonical refresh 是唯一登记的 DB 刷新 one-shot",
            "db_route.status=healthy",
        ):
            if required not in text:
                fail(errors, f"{path}: missing controller contract {required!r}")
        for term in ("EventEnvelope", "SignalCandidate", "TradeIntent"):
            if term not in text:
                fail(errors, f"{path}: missing WCIR lineage term {term}")

    agents = read("AGENTS.md")
    claude = read("CLAUDE.md")
    if agents[agents.index("## 0.") :] != claude[claude.index("## 0.") :]:
        fail(errors, "AGENTS.md and CLAUDE.md core bodies differ")


def check_current_strategy_language(errors: list[str]) -> None:
    entrypoint = read("docs/WEATHER_STRATEGY_ENTRYPOINT.md")
    marker = "## Historical Production Posture"
    if marker not in entrypoint:
        fail(errors, "WEATHER_STRATEGY_ENTRYPOINT.md: missing historical boundary")
        current = entrypoint
    else:
        current = entrypoint.split(marker, 1)[0]

    for phrase in (
        "Production data truth is N100",
        "N100 should run exactly",
        "Current default live execution policy",
    ):
        if phrase in current:
            fail(errors, f"WEATHER_STRATEGY_ENTRYPOINT.md: stale current phrase {phrase!r}")

    for path in sorted((ROOT / "skills").glob("weather-*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        for phrase in ("wsl -d", "N100 最新真相", "/home/rui/projects"):
            if phrase in text:
                fail(errors, f"{path.relative_to(ROOT)}: stale host phrase {phrase!r}")


def check_historical_status(errors: list[str]) -> None:
    expectations = {
        "docs/WEATHER_CITY_POOL_DECISIONS.md": "不再是当前 live allowlist source of truth",
        "docs/WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md": "2026-06-06 blender/basket 接手快照",
        "docs/WEATHER_DATA_COLLECTION_INVENTORY.md": "本文固定为 2026-06-29/30",
    }
    for path, marker in expectations.items():
        text = read(path)
        if "Status: snapshot" not in text:
            fail(errors, f"{path}: must be a snapshot")
        if marker not in text:
            fail(errors, f"{path}: missing historical disclaimer")


def check_skill_surface(errors: list[str]) -> None:
    required = {
        "weather-strategy-research",
        "weather-strategy-performance",
        "weather-strategy-lineage",
        "weather-strategy-exposure",
        "weather-live-account-reconcile",
        "weather-fact-rebuild",
        "weather-strategy-deploy",
        "weather-jrs-runtime-failover",
    }
    for name in required:
        root = ROOT / "skills" / name
        for rel in ("SKILL.md", "agents/openai.yaml"):
            if not (root / rel).exists():
                fail(errors, f"skills/{name}: missing {rel}")

    index = read("docs/WEATHER_DOCS_INDEX.md")
    for name in required:
        if name not in index:
            fail(errors, f"WEATHER_DOCS_INDEX.md: missing skill {name}")


def check_operational_skill_contracts(errors: list[str]) -> None:
    deploy = read("skills/weather-strategy-deploy/SKILL.md")
    legacy_socket_command = "tmux -L " + "weather" + "-jrs"
    for forbidden in (
        "tmux list-sessions",
        legacy_socket_command,
        "screen -ls",
        "LaunchAgent/tmux/screen",
    ):
        if forbidden in deploy:
            fail(errors, f"weather-strategy-deploy: forbidden legacy process entry {forbidden!r}")
    for required in (
        "weather_production_ctl.py health",
        "weather_production_ctl.py plan",
        "weather_production_manifest.py --strict",
        "weather-data-feed-jrs",
    ):
        if required not in deploy:
            fail(errors, f"weather-strategy-deploy: missing canonical entry {required!r}")

    lineage = read("skills/weather-strategy-lineage/SKILL.md")
    performance = read("skills/weather-strategy-performance/SKILL.md")
    research = read("skills/weather-strategy-research/SKILL.md")
    for path, text in (
        ("weather-strategy-lineage", lineage),
        ("weather-strategy-performance", performance),
        ("weather-strategy-research", research),
    ):
        for term in ("candidate_grain_version", "build_id", "observed_at_utc"):
            if term not in text:
                fail(errors, f"{path}: missing canonical identity term {term}")
    for term in ("EventEnvelope", "DecisionContext", "ModelOutput", "TradeIntent"):
        if term not in lineage:
            fail(errors, f"weather-strategy-lineage: missing WCIR lineage term {term}")

    for path in ("docs/WEATHER_ANALYSIS_CONTRACT.md", "docs/WEATHER_SYSTEM_CONTRACT.md"):
        text = read(path)
        for term in (
            "EventEnvelope",
            "DecisionContext",
            "ModelOutput",
            "SignalCandidate",
            "TradeIntent",
            "candidate_grain_version",
            "build_id",
            "observed_at_utc",
        ):
            if term not in text:
                fail(errors, f"{path}: missing WCIR/canonical term {term}")


def check_index_links(errors: list[str]) -> None:
    index_path = ROOT / "docs" / "WEATHER_DOCS_INDEX.md"
    index = index_path.read_text(encoding="utf-8")
    for match in re.finditer(r"\[[^\]]+\]\(([^)#]+)(?:#[^)]+)?\)", index):
        target = match.group(1)
        if target.startswith(("http://", "https://")):
            continue
        resolved = (index_path.parent / target).resolve()
        if not resolved.exists():
            fail(errors, f"WEATHER_DOCS_INDEX.md: broken link {target}")


def main() -> int:
    errors: list[str] = []
    check_entrypoints(errors)
    check_current_strategy_language(errors)
    check_historical_status(errors)
    check_skill_surface(errors)
    check_operational_skill_contracts(errors)
    check_index_links(errors)
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("weather docs check ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
