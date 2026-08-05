#!/usr/bin/env python3
"""Read-only consistency checks for current weather docs and skills.

Historical snapshots may keep old wording. This checker verifies that current
entrypoints route around those snapshots instead of treating old host/strategy
state as present truth.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
HYGIENE_CONFIG = ROOT / "configs" / "repo_hygiene.yaml"
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
ENTRYPOINT_PREFIXES = ("start_", "stop_", "restart_", "status_", "run_")
GENERATED_METADATA_MARKERS = (
    "summary",
    "manifest",
    "spec",
    "protocol",
    "model",
    "audit",
    "readme",
)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def git_tracked_files() -> set[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode("utf-8")
    return {item for item in output.split("\0") if item}


def hygiene_config() -> dict:
    payload = yaml.safe_load(HYGIENE_CONFIG.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"repo hygiene config must be a mapping: {HYGIENE_CONFIG}")
    return payload


def local_markdown_targets(path: Path) -> list[tuple[str, Path]]:
    targets: list[tuple[str, Path]] = []
    for match in MARKDOWN_LINK_RE.finditer(path.read_text(encoding="utf-8")):
        raw = match.group(1).split("#", 1)[0].strip().strip("<>")
        if not raw or raw.startswith(
            ("http://", "https://", "mailto:", "file:", "#")
        ):
            continue
        targets.append((raw, (path.parent / raw).resolve()))
    return targets


def repo_relative(path: Path) -> str | None:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return None


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


def check_index_links(errors: list[str], tracked: set[str]) -> None:
    index_path = ROOT / "docs" / "WEATHER_DOCS_INDEX.md"
    for target, resolved in local_markdown_targets(index_path):
        if not resolved.exists():
            fail(errors, f"WEATHER_DOCS_INDEX.md: broken link {target}")
            continue
        relative = repo_relative(resolved)
        if relative is not None and relative not in tracked:
            fail(
                errors,
                f"WEATHER_DOCS_INDEX.md: target exists locally but is not git tracked: "
                f"{relative}",
            )


def check_authoritative_links(errors: list[str], tracked: set[str]) -> None:
    for path in authoritative_doc_paths(tracked):
        for target, resolved in local_markdown_targets(path):
            relative = repo_relative(resolved)
            if relative is None:
                continue
            if not resolved.exists():
                fail(errors, f"{path.relative_to(ROOT)}: broken link {target}")
            elif relative not in tracked:
                fail(
                    errors,
                    f"{path.relative_to(ROOT)}: linked target is not git tracked: "
                    f"{relative}",
                )


def authoritative_doc_paths(tracked: set[str]) -> list[Path]:
    paths = [ROOT / "AGENTS.md", ROOT / "CLAUDE.md"]
    paths.extend(
        path
        for path in sorted((ROOT / "docs").glob("WEATHER_*.md"))
        if repo_relative(path) in tracked
    )
    return paths


def authoritative_link_targets(tracked: set[str]) -> set[str]:
    targets: set[str] = set()
    for path in authoritative_doc_paths(tracked):
        for target, resolved in local_markdown_targets(path):
            relative = repo_relative(resolved)
            if relative is not None:
                targets.add(relative)
    return targets


def active_code_corpus(tracked: set[str]) -> str:
    chunks: list[str] = []
    for relative in sorted(tracked):
        if not relative.startswith(("src/", "scripts/ops/", "configs/", "tests/")):
            continue
        path = ROOT / relative
        if path.suffix.lower() not in {".py", ".sh", ".yaml", ".yml", ".json"}:
            continue
        chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(chunks)


def generated_artifact_repo_eligible(
    relative: str,
    *,
    corpus: str,
    authoritative_targets: set[str],
) -> bool:
    path = Path(relative)
    metadata = path.suffix.lower() == ".md" or any(
        marker in path.name.lower() for marker in GENERATED_METADATA_MARKERS
    )
    return metadata or generated_artifact_required_in_worktree(
        relative,
        corpus=corpus,
        authoritative_targets=authoritative_targets,
    )


def generated_artifact_required_in_worktree(
    relative: str,
    *,
    corpus: str,
    authoritative_targets: set[str],
) -> bool:
    active_reference = relative in corpus
    casebook = "/generated/intraday_decision_casebook_v1/" in relative
    authoritative_evidence = relative in authoritative_targets
    return active_reference or casebook or authoritative_evidence


def check_generated_artifacts(errors: list[str], tracked: set[str]) -> None:
    config = hygiene_config()
    max_bytes = int(config["generated_artifact_max_bytes"])
    large_allowlist = set(config.get("large_generated_allowlist") or [])
    corpus = active_code_corpus(tracked)
    authoritative_targets = authoritative_link_targets(tracked)
    generated = sorted(
        relative
        for relative in tracked
        if relative.startswith("docs/analysis/") and "/generated/" in relative
    )
    for relative in generated:
        path = ROOT / relative
        if not path.exists():
            fail(errors, f"tracked generated artifact is missing: {relative}")
            continue
        if not generated_artifact_repo_eligible(
            relative,
            corpus=corpus,
            authoritative_targets=authoritative_targets,
        ):
            fail(
                errors,
                f"generated data must live in JRS research_artifact_root: {relative}",
            )
        if path.stat().st_size > max_bytes and relative not in large_allowlist:
            fail(
                errors,
                f"tracked generated artifact exceeds {max_bytes} bytes: {relative}",
            )
    for relative in sorted(large_allowlist):
        if relative not in tracked:
            fail(errors, f"stale large_generated_allowlist entry: {relative}")


def production_script_closure(tracked: set[str]) -> tuple[set[str], set[str]]:
    production = read("src/strategies/runtime/production.yaml")
    scripts = {
        relative
        for relative in tracked
        if relative.startswith("scripts/") and Path(relative).suffix in {".py", ".sh"}
    }
    direct = {
        relative
        for relative in scripts
        if relative in production or Path(relative).name in production
    }
    closure = set(direct)
    while True:
        corpus = "\n".join(
            (ROOT / relative).read_text(encoding="utf-8", errors="ignore")
            for relative in closure
        )
        additions = {
            relative
            for relative in scripts - closure
            if relative in corpus or Path(relative).name in corpus
        }
        if not additions:
            return closure, scripts
        closure.update(additions)


def check_production_entrypoints(errors: list[str], tracked: set[str]) -> None:
    config = hygiene_config()
    closure, scripts = production_script_closure(tracked)
    entrypoints = {
        relative
        for relative in scripts
        if relative.startswith("scripts/ops/")
        and Path(relative).name.startswith(ENTRYPOINT_PREFIXES)
    }
    outside = entrypoints - closure
    ceiling = int(config["max_entrypoints_outside_production_closure"])
    if len(outside) > ceiling:
        fail(
            errors,
            f"entrypoints outside production dependency closure grew from ceiling "
            f"{ceiling} to {len(outside)}",
        )


def main() -> int:
    errors: list[str] = []
    tracked = git_tracked_files()
    check_entrypoints(errors)
    check_current_strategy_language(errors)
    check_historical_status(errors)
    check_skill_surface(errors)
    check_operational_skill_contracts(errors)
    check_index_links(errors, tracked)
    check_authoritative_links(errors, tracked)
    check_generated_artifacts(errors, tracked)
    check_production_entrypoints(errors, tracked)
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("weather docs check ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
